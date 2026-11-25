# SPDX-License-Identifier: Apache-2.0
"""nopekit.cli — the command surface, and the only place that reads the clock.

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
  behind `--json` or in `REPORT.md`.
* **`--json` on every read command.** Not a pretty-printer switch: the JSON is
  the same data the human output renders, so an agent never has to parse
  columns. When `--json` is given, nothing but JSON goes to stdout.
* **`NopekitError` -> `error: <msg>` on stderr, exit 2.** No traceback: a stack
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
        `nopekit check` usable as a pre-spend gate: it exits non-zero *while
        anything critical is unproven*, not only when something failed.
    2   the user did something the tool cannot act on (NopekitError, bad args)
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
import ast
import contextlib
import dataclasses
import difflib
import importlib.util
import io
import json
import math
import os
import platform
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, NamedTuple

from . import __version__
from . import (artifacts, claims, decisions, derivation, gates, graph, milestones, modelio,
               packs, page, prose, report, rounds, site, store, vcs, verdicts)
from .models import (
    ArtifactKind,
    Claim,
    ClaimKind,
    ClaimStatus,
    Extraction,
    Ledger,
    PhysicalResult,
    ProjectMeta,
    REJECTION_TYPES,
    Tier,
    Verdict,
    sha256_file,
)
from .util import (
    NopekitError,
    FileDigests,
    FileLock,
    _HOST,
    _pid_alive,
    atomic_write_json,
    atomic_write_text,
    human_bytes,
    human_duration,
    read_json,
    rel,
    seal,
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

#: How long `site serve --detach` waits for the server it started to record
#: where it listens. The server binds before its first build, so this is a
#: process start and an import — about a second; 30 s covers a loaded machine.
#: *Rejected:* returning before the server listens (the URL printed could be one
#: nothing ever serves: a port in use is found only by the bind).
SERVE_DETACH_WAIT_S = 30.0

#: How long `site serve --stop` waits after SIGTERM before SIGKILL. A server
#: stops between requests, and one in its first build finishes the build first
#: (a build killed half-way leaves its lock and a temporary file behind).
SERVE_STOP_WAIT_S = 15.0

#: How often a detached server checks that it still owns its place
#: (`_serve_lease`); a lost lease stops it within this many seconds.
SERVE_LEASE_S = 2.0

#: The lock every write-side command takes, for the whole operation. Two
#: `nopekit check` runs in one project would otherwise interleave their
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

    Warnings go to stderr so `nopekit gate list | ...` and `--json` stay clean.
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
    """The project root for this invocation, or an NopekitError saying there is none.

    `-C/--dir` sets where the search STARTS, not where it ends: like git, the
    walk goes up until it finds a project marker (`.nopekit/project.json`, or a
    legacy `.nopekit/ledger.json`) and stops at the repository's `.git`, so
    running from `model/` or `inputs/cad/` hits the same project (S-64: any
    `.nopekit/` used to count, and `~/.nopekit/packs/` made all of `~` one).
    """
    return store.require_root(getattr(args, "dir", None))


def _lock(root: str) -> FileLock:
    """The build lock for `root`, as a context manager.

    Held around every command that writes. The failure it prevents is concrete:
    two runs that both load the ledger, both add verdicts and both save will lose
    one set entirely, with nothing anywhere saying so.
    """
    return FileLock(os.path.join(store.nopekit_dir(root), LOCK_NAME))


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
        except NopekitError as exc:
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


def _entry_edit(root: str) -> str:
    """Where a project's model entry is recorded, as the file edit that records it.

    `.nopekit/project.json` owns it (checkpoint 1.3); a legacy project keeps it
    in its ledger's `meta` until a check or a shim migrates it. What slipped
    through: four messages — `init`'s next step, `check`'s warning, `model`'s
    refusal, `doctor`'s row — named `model --set-entry`, a flag PLAN A-8
    removed: a command whose only job was to write one key of one record, which
    the human and the agent edit as a file like every other record.
    """
    if store.is_legacy(root):
        return (f'"model_entry" under "meta" in {store.NOPEKIT_DIR}/{store.LEDGER_NAME} '
                f"(the legacy layout, until a check migrates it)")
    return f'"model_entry" in {store.NOPEKIT_DIR}/{store.PROJECT_NAME}'


def _no_entry(root: str) -> str:
    """The one sentence every command says when no model entry is recorded."""
    return (f'no model entry recorded — set {_entry_edit(root)} to the model file, '
            f'e.g. "model/<thing>.py"')


def _projection_safe(root: str, ledger: Ledger) -> tuple[Any, dict | None, str]:
    """`_projection`, with the failure returned instead of raised.

    For `status` and `doctor`, which have to keep working on a project whose
    model is mid-edit — that is precisely when someone runs them.
    """
    try:
        model, projection = _projection(root, ledger)
    except NopekitError as exc:
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
    never assigned, so `nopekit why <param>` told every reader "GATES (0) —
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
    reads = _registered_reads(read_sets, registry)
    found: dict[str, list[str]] = {}
    for name in sorted({param.name for param in ledger.params}):
        gates_of = modelio.param_readers(name, reads)
        if gates_of:
            found[name] = gates_of
    return found


def _registered_reads(read_sets: Mapping[str, Iterable[tuple]],
                      registry: gates.Registry | None) -> dict[str, Iterable[tuple]]:
    """`read_sets` for the gates `registry` registers (all of them with no
    registry): a gate this project cannot load protects nothing here. The
    attribution itself is `modelio.param_readers`, the one copy of the rule."""
    registered = set(registry.ids()) if registry is not None else set(read_sets)
    return {gate: paths for gate, paths in read_sets.items() if gate in registered}


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

    The one exception is a row the sweep itself served stale
    (`SweepRow.stale_reason`: a costlier tier's entry whose path no current
    control shows, which this sweep's ceiling cannot demonstrate, or a
    dependent marked under such a prerequisite). It stays in `stale_gates` with
    the sweep's reason on its resolution row, `fresh` False. What slipped through (review,
    `repro_undemonstrated`): the sweep served that gate as a skip and this
    dropped it from the stale set, so `check --junit` exited 0 ready while
    `status` read the claim STALE.

    Then the prerequisite rule again, over the merged view
    (`verdicts.apply_prerequisites`, P2.2-D8). `resolve` applied it to what it
    read from disk; the sweep may have learned more — a guard it just refused
    or saw fail, under `--no-record` (nothing reached disk), or an entry with an
    opaque channel that reads Unknown the moment it is written. Without this an
    unselected dependent — above the ceiling, outside `--only` — reads Checked
    beside a guard this same command found not established: exit 0, JUnit
    green, and `status` stricter than `check` (critique of the P2.2 design;
    `UnselectedDependentFollowsTheSweep`). The rule is monotone, so it only
    ever downgrades here. Its two under-generous corners, each stricter than
    `status` and never more generous: a dependent `resolve` already pruned
    stays pruned in this view when the sweep's reading of its root passes (a
    `--no-record --only <root>` after a fix); and an opaque root reads Unknown
    once written, so its dependents read Stale here — as `status` shows them.
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
    served_stale = {gid: row for gid, row in rows.items() if row.stale_reason}
    by_gate = dict(resolution.rows)
    for gate_id, row in served_stale.items():
        held = by_gate.get(gate_id) or verdicts.Row(gate_id, "fresh", cached=row.cached)
        by_gate[gate_id] = dataclasses.replace(held, fresh=False,
                                               stale_reason=row.stale_reason)
    merged_view = dataclasses.replace(
        resolution, verdicts=merged, rows=by_gate,
        stale_gates=frozenset([*(g for g in resolution.stale_gates if g not in rows),
                               *served_stale]))
    return verdicts.apply_prerequisites(merged_view, registry)


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
    # `verdicts.view` lays the verdicts and each claim's standing (P2.5a-D13);
    # then this command's coverage and read sets.
    judged = verdicts.view(ledger, resolution)
    view = dataclasses.replace(
        judged,
        claims=[dataclasses.replace(claim, gates=list(cover.get(claim.id, [])))
                for claim in judged.claims],
        params=[dataclasses.replace(param, gates=list(reads.get(param.name, [])))
                for param in judged.params],
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
    """Seconds from one nopekit timestamp to another, or None if either does
    not parse — an age nobody can compute is unknown, never 0."""
    try:
        a = datetime.strptime(earlier, "%Y-%m-%dT%H:%M:%SZ")
        b = datetime.strptime(later, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return (b - a).total_seconds()


def _last_check(root: str) -> dict:
    """`.nopekit/cache/last_check.json` as `verdicts.write_last_check` left it,
    or `{}` when there is none or it cannot be read. For `status`'s `last check:`
    line only: `check` never reads it (it would be trusting its own summary of a
    previous run), and nothing decides a verdict from it. The path is spelled
    from the writer's own constants, so the two can never disagree about where
    the file lives."""
    path = os.path.join(store.nopekit_dir(root), verdicts._CACHE_DIR, verdicts._LAST_CHECK)
    try:
        data = read_json(path, None)
    except NopekitError:
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- #
# the records, at the edge: migrate once, write one, keep the index current
# --------------------------------------------------------------------------- #
#: What slipped through before checkpoint 1.3: every writing command loaded the
#: WHOLE project and saved the whole project. `decide` asked to record one
#: decision rewrote every claim and parameter beside it; `check`, a sweep, rewrote
#: the records on every run — parameters re-synced from the model, grounding
#: back-references written by `_link_grounding`, coverage written into
#: `claim.gates` by `_refresh_coverage` — so a claim a human edited between two
#: commands was put back by the second from its in-memory copy, `claim edit
#: --gates X` was silently reverted by the next check (S-37), and a deleted
#: extraction's grounding lived on in the parameter it had been copied into
#: (S-36). A record is a file now and a command writes the ONE record it was asked
#: to (`store.write_record`, `store.write_project`); coverage and grounding are
#: derived where they are read; `tests/test_shims.py` walks this file's AST and
#: refuses any reference to `store.save`, the whole-ledger writer, which stays for
#: tests and the migration only.


def _migrate(root: str, *, apply: bool, now: str) -> Ledger:
    """The project's records, migrating a legacy `ledger.json` first. Under the lock.

    `check` and the shims are the migration's triggers (spec Q1.5): with `apply`
    a legacy project becomes record files here, once, and the one notice —
    ending in the `git rm --cached .nopekit/ledger.json` line, because the spine
    runs no git — goes to stderr, never into `--json`'s stdout. Without `apply`
    (`check --no-record`) the same pure function runs in memory, writes nothing,
    and says the project will migrate on the next check. On a migrated project it
    is `store.load`, silently.

    What the model STATES is read statically (`modelio.static_param_prose`: it
    parses the entry and never imports or runs it), so a migration can drop a
    param record's rationale or units only where the model already says them —
    and a model that does not load cannot wedge the migration.
    """
    plan = store.migrate_legacy(root, apply=apply, when=now,
                                model_prose=modelio.static_param_prose)
    if plan.notice:
        _warn(plan.notice)
    return plan.ledger


def _load(root: str) -> Ledger:
    """The project's records for every command that does not migrate them:
    `store.load`, with the reader `_migrate` migrates with, so a legacy project
    reads as the very plan its next `check` will write — the same refusals, and
    no others.

    What slipped through: every reader called `store.load(root)`, which planned
    the migration with no `model_prose`, while `check` planned it with the
    static reader. On a model stating `D` and `d`, `status`, `why`, `report`
    and the rest refused with "rename one" — a case collision between two
    files `check` never writes — and `check` migrated cleanly; after a
    migration killed half way, they blamed a `params/thickness.json` that was
    byte for byte what `check` would write, and told the user to move it aside.
    *Rejected:* `_migrate(apply=False)` here (it prints the "will migrate"
    notice on every read); keeping the calls and fixing the store's default (the
    store must not import `modelio`, spec §3.1)."""
    return store.load(root, model_prose=modelio.static_param_prose)


#: Commands after which the index is NOT rebuilt. `doctor` reports on the
#: project and never writes a byte of it (it compares the index with the records
#: instead). `init` never writes a `ledger.json` (spec §3.15): a new project's
#: first index comes from its first command, like every later one.
_INDEX_UNTOUCHED = frozenset({"doctor", "init"})

#: How many times `_touch_index` rebuilds when the records move underneath it.
#: The rebuild takes no lock — `status` must stay usable while a tier-2 `check`
#: holds the build lock for an hour — so a shim that writes a record between this
#: command's build and its write would leave an index one record behind, written
#: AFTER the shim's own correct one. Re-reading `records_digest` catches that; three
#: rounds is the writers racing twice in a row. *Rejected:* taking the build lock
#: (a read command blocked by a sweep); one round (the race above lands silently).
_INDEX_ROUNDS = 3


def _touch_index(root: str, *, quiet: bool = False) -> None:
    """Rebuild `.nopekit/ledger.json` from the records, best-effort.

    Run at the end of every command on a MIGRATED project (`main`, minus
    `_INDEX_UNTOUCHED`, and never under `--no-record`), so an agent reading the
    one generated file after any command reads what the record files say —
    including an edit a human made by hand since the last command (invariant 8:
    the index never disagrees with the records). Never on a legacy project, where
    `ledger.json` IS the records until `check` or a shim migrates it. Rewritten
    only when its bytes change (`store.write_index`), so a read command that finds
    it current writes nothing.

    Best-effort: a record that does not read, or a read-only checkout, leaves the
    index as it was with one stderr line (none when `quiet`: the command already
    failed and said why), and never changes the command's exit code — the records
    are the truth, and a command whose own work succeeded must not fail on its
    output's output.
    """
    try:
        if store.is_legacy(root) or not os.path.isfile(store.project_paths(root)["project"]):
            return
        for _ in range(_INDEX_ROUNDS):
            before = store.records_digest(root)
            store.write_index(root)
            if store.records_digest(root) == before:
                return
    except (NopekitError, OSError) as exc:
        if not quiet:
            _warn(f"warning: the index {store.NOPEKIT_DIR}/{store.LEDGER_NAME} was not "
                  f"rebuilt ({exc}); the records are unchanged and are still the truth")


def _after(args: argparse.Namespace, *, quiet: bool = False) -> None:
    """`_touch_index` for the project this command ran in, when it should run —
    never twice: the Stop hook rebuilds it under its own redirect, so nothing
    reaches stdout after its one JSON object (`_index_done`)."""
    if getattr(args, "command", None) in _INDEX_UNTOUCHED or getattr(args, "no_record", False) \
            or getattr(args, "_index_done", False):
        return
    try:
        root = store.find_root(getattr(args, "dir", None))
    except (NopekitError, OSError):
        return
    if root is not None:
        _touch_index(root, quiet=quiet)


def _skip_digest(skipped: list[Verdict], *, width: int = 96) -> list[str]:
    """Collapse a wall of `[skip]` lines into one line per distinct REASON.

    `nopekit check` is the inner-loop command, and once a few packs are
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

    Optional fields that are empty are OMITTED. `nopekit check --json` on a
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

    From P2.4 every row carries its margin (D-17, P2.4-D8): `margin` —
    `claims.margin`, the one function, to 6 significant figures — or
    `margin_why`, why there is none (`no-value` included: critique 15 of the
    design — a row that said nothing left the reason to the renderer). A
    renderer draws it; none computes it. `comparator` and `settles` join the
    drop-if-empty keys.
    """
    row = verdict.to_dict()
    for key in ("detail", "error", "evidence", "skip_reason", "units", "rho", "unqualified",
                "blocked_by", "blocked_kind", "comparator", "settles"):
        if not row.get(key):
            row.pop(key, None)
    for key in ("measured", "limit"):
        if row.get(key) is None:
            row.pop(key, None)
    found = claims.margin(verdict)
    if found.fraction is not None:
        row["margin"] = float(f"{found.fraction:.6g}")
    else:
        row["margin_why"] = found.why
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
# parameters and grounding, read where they are shown
# --------------------------------------------------------------------------- #
def _param_views(root: str, ledger: Ledger, model: Any, model_error: str, *,
                 read_sets: Mapping[str, Iterable[tuple]] | None = None
                 ) -> list[modelio.ParamView]:
    """`modelio.param_view`, plus — when the model does not load — one bare view
    per parameter the model's TEXT states and no record holds. `read_sets`
    (registered gates only) fills each view's `gates`; without it they are `()`.

    Why the second half: from checkpoint 1.3 a parameter the model states
    entirely has no record (`check` stopped copying the model into the records,
    and the migration writes a param record only for what the model cannot
    hold), so with the model mid-edit `param_view` knew nothing of `thickness`,
    and `why thickness` answered "no parameter named thickness" about the
    number the bracket's failing claim turns on. The names come from
    `modelio.static_param_prose` — the entry parsed, never imported or run —
    and each view says why it has no number (`model_error`), never a cached one
    (S-39)."""
    views = modelio.param_view(ledger, model, model_error=model_error, read_sets=read_sets)
    if model is None and model_error:
        held = {view.name for view in views}
        error = " ".join(model_error.split())
        stated = modelio.static_param_prose(root, ledger.meta.model_entry)
        views += [modelio.ParamView(
                      name=name, model_error=error,
                      gates=tuple(modelio.param_readers(name, read_sets or {})))
                  for name in stated if name not in held]
    return views


def _shown_params(root: str, ledger: Ledger, model: Any, model_error: str,
                  resolution: verdicts.Resolution,
                  registry: gates.Registry | None, *,
                  projection: Any = None) -> list[modelio.ParamView]:
    """The parameters as `status`, `report`, the page and `doctor` show them:
    `_param_views` with each view's gates read off the resolution's read sets.

    What slipped through (review, checkpoint 1.3): those four took a parameter's
    value and rationale from `ledger.params` — the records, which from 1.3 the
    model owns and which hold only what it cannot. The migrated bracket's page
    showed no parameter; a record holding only `"source"` made `status` and the
    report call `thickness` undefended (the report at value `None`) while
    `doctor`, reading the model alone, said every parameter carried a rationale;
    and a field nobody explained was flagged by `doctor` and nothing else. One
    view, one rule (`modelio.undefended_params`), every reader. `site build`
    and the page-staleness judgement (`_site_judgement`) both take theirs from
    here, so a page built from it reads current against it. From architecture
    phase 3a each view carries `read_by` and `sourced_from` (`graph.observe`):
    the served entries' readers and what sources the value, beside `gates`;
    ``projection`` names the derived values a read of the whole `ctx.params`
    reads (`graph.derived_names`) — without it such a read reads the choices."""
    return graph.observe(
        _param_views(root, ledger, model, model_error,
                     read_sets=_registered_reads(resolution.read_sets, registry)),
        ledger=ledger, resolution=resolution, registry=registry, choices=_choices(model),
        derived=graph.derived_names(projection))


def _why_text(root: str, ledger: Ledger, registry: gates.Registry, model: Any,
              model_error: str, view: Ledger, resolution: verdicts.Resolution,
              name: str, *, projection: Any = None) -> str:
    """`decisions.why` with all four of its inputs from where they live now
    (spec §3.10, U29): each parameter from `param_view` (the model's value, and
    what lost in both homes); a claim's gates from registry coverage
    (`claims.effective_gates`); a parameter's gates from what each gate read when
    it last executed (`verdicts.last_read_sets`, registered gates only); every gate
    line from the resolver's effective verdicts; and grounding from the
    extractions (`artifacts.value_grounding`). Nothing here is a stored copy: a record may no
    longer hold `gates`, `value` or a grounding back-reference at all."""
    registered = set(registry.ids())
    read_sets = {gate: reads for gate, reads in verdicts.last_read_sets(root).items()
                 if gate in registered}
    views = _param_views(root, ledger, model, model_error)
    grounds = artifacts.value_grounding(ledger, views)
    views = [dataclasses.replace(v, grounded_by=tuple(grounds.get(v.name, ())))
             for v in views]
    # Architecture phase 3a (SPEC 10, 19): what reads each value, what sources
    # it, and what each claim rests on — from the graph, the one producer.
    bases = _input_bases(root, ledger)
    views = graph.observe(views, ledger=ledger, resolution=resolution, registry=registry,
                          choices=_choices(model), derived=graph.derived_names(projection))
    found_trace = _trace_for(root, model, projection, resolution)
    built = graph.of(root, view, resolution, registry, projection=projection, model=model,
                     views=views, bases=bases, trace=found_trace.trace)
    shown = dataclasses.replace(
        view, claims=[dataclasses.replace(claim, grounded_by=list(grounds.get(claim.id, ())))
                      for claim in view.claims])
    # A requirement's status from the view as `status` judges it (SPEC
    # decision 12): the caller's compositions, never the raw opinion.
    composed = claims.compositions(view, registry=registry,
                                   stale_gates=resolution.stale_gates,
                                   unseen_gates=claims.unseen_gates(resolution))
    # SPEC 14, 38: each rejection's condition and preference from the same
    # view and compositions, and each input's basis from its proposer.
    # Architecture phase 5: each prose file's stamp state, for a board's line.
    prose_states: dict[str, str] = {}
    found = graph.tokens(view, resolution, registry, composed=composed)
    if os.path.isdir(page.content_dir(root)):
        prose_states = {row["subject"]: row["stamp"]
                        for row in page.stamp_states(root, view, found) if row["subject"]}
    # Architecture phase 3b: what the trace observed, and amber, beside the
    # status — never instead of it.
    amber = _amber_for(root, view, registry, built, found.claims, found_trace)
    return decisions.why(shown, name, view=views,
                         coverage=claims.effective_gates(ledger, registry),
                         read_sets=read_sets, verdicts=resolution.verdicts,
                         composed=composed, rows=claims.rejection_rows(view, composed),
                         bases=bases, graph=built,
                         entry=ledger.meta.model_entry or "", prose=prose_states,
                         trace=found_trace.trace, amber=amber)


def _input_bases(root: str, ledger: Ledger) -> dict[str, str]:
    """`{input id: basis token}` for every input a proposer produced — looked
    up from the proposer's declaration in any discoverable pack, `undeclared`
    when none declares it now (SPEC 38: never stated by the input)."""
    found = None
    out: dict[str, str] = {}
    for artifact in ledger.inputs:
        pid = getattr(artifact, "proposer", "") or ""
        if not pid:
            continue
        if found is None:
            found = packs.proposers(root)
        out[artifact.id] = packs.proposer_basis(found, pid)
    return out


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #
def _trace_for(root: str, model: Any, projection: Any,
               resolution: verdicts.Resolution | None = None) -> derivation.TraceRead:
    """The trace for the current design, and only for it (architecture phase
    3b, invariant 7): `derivation.trace_for` over the resolution's anchors.
    Every `graph.of`/`graph.build` call takes its ``trace`` from here, so the
    map, `why`, `doctor` and the round record draw one derivation. Never
    raises: a key that cannot be computed reads ``unrecorded``."""
    try:
        return derivation.trace_for(root, model, projection,
                                    derivation.anchors_for(root, resolution))
    except (NopekitError, OSError, ValueError, TypeError):
        return derivation.TraceRead("unrecorded", None, None, (), ())


def _amber_for(root: str, view: Ledger, registry: Any, built: Any,
               tokens_: Mapping[str, str], found: derivation.TraceRead
               ) -> dict[str, derivation.Amber]:
    """Amber now (`derivation.amber_states`) for every Checked claim that rests
    on an unconfirmed assumption — display only, beside the status the
    resolver composed (invariant 12). The current digests of each claim's
    acceptance condition and evaluators are read only when there is a trace
    to compare them with."""
    return derivation.amber_now(root, view, registry, built, tokens_, found)


def _amber_record(states: Mapping[str, derivation.Amber]) -> dict:
    """The round record's ``amber`` key: claim ids by state."""
    out: dict[str, list[str]] = {"flips": [], "holds": [], "unclear": [], "not_current": [],
                                 "untraced": []}
    for cid, found in states.items():
        out[found.state.replace("-", "_")].append(cid)
    return {k: sorted(v, key=store._natural) for k, v in out.items()}


def _readme_bytes(root: str) -> bytes | None:
    try:
        with open(os.path.join(root, "README.md"), "rb") as fh:
            return fh.read()
    except OSError:
        return None


def cmd_init(args: argparse.Namespace) -> int:
    """Create the records layout, start a git repository when there is none, and
    print the next four steps.

    Born migrated (checkpoint 1.3): `store.init` writes `.nopekit/project.json`
    LAST, the empty record directories, the input buckets, and the three marked
    ignore/attribute blocks — and no `ledger.json`. What slipped through before:
    `init` wrote the whole project into a `ledger.json`, the layout 1.3 migrates
    away from, so every new project was a legacy one, migrated by its first
    `check` with a `git rm --cached` notice about a file git had never tracked
    (phase-1.md's V row: `init` then `status` prints no notice). The index is a
    command's output, never `init`'s (`_INDEX_UNTOUCHED`).

    Architecture phase 6: `people` is git's configured identity, when one is set
    (the person a round may commit as, SPEC §0.3), and a project that sits in no
    repository gets one (`vcs.init_repo`, after `store.init`, so a refused init
    starts none) — a first session must not stall on git, and every round
    commits into it. Inside an existing repository nothing about git changes.
    `--no-git` starts none.

    The last part is not decoration. `init` that prints "initialised." leaves a
    new user staring at empty directories with no idea which one is theirs. The
    steps are decision 21's order, the skill's: interview the person and write
    their requirements in their words, write the model, reach first light and
    hand over the live site's URL, and only then write claims beside the first
    verdicts — most claims cannot be stated precisely before a model exists.
    The model step names the file edit that records the model's entry:
    `project.json` is the one home of the entry.
    """
    root = os.path.abspath(getattr(args, "dir", None) or os.getcwd())
    name = (args.name or os.path.basename(root.rstrip(os.sep)) or "project").strip()
    os.makedirs(root, exist_ok=True)
    # `git var` answers outside a repository too, so the identity is known
    # before one exists.
    person = rounds.person_of(vcs.ident(root))
    # Listed only when the strict reader will read it back. What slipped
    # through (review of phase 6): git accepts `user.email = "a b@x.org"`, init
    # listed it, and every later command refused the project.json init wrote.
    unlisted = (store.people_problem([{"name": person.name, "email": person.email}],
                                     "git's identity") if person else None)
    if unlisted:
        person = None
    meta = ProjectMeta(
        name=name,
        summary=(args.summary or "").strip(),
        created=utcnow_iso(),
        revision=(args.revision or "v0.1").strip(),
        model_entry=(args.model or "").strip(),
        packs=_collect(args.pack),
        spine_version=__version__,
        people=[person] if person else [],
    )
    readme_before = _readme_bytes(root)
    ledger = store.init(root, meta)
    # A repository is started only on git's positive "no repository here"
    # (`vcs.in_repository`): `is_repo` reads every failure as "no", and a
    # refused or slow git answer started a nested repository inside an
    # existing one (review of phase 6). `inside` is None when git did not say.
    inside, unanswered = vcs.in_repository(root)
    git_info = {"started": False, "inside": inside, "why": "", "ignored": False}
    if inside is None:
        git_info["why"] = f"git did not answer: {unanswered}"
    elif not inside and not getattr(args, "no_git", False):
        made = vcs.init_repo(root)
        git_info.update(started=made.ok, why=made.why)
    elif not inside:
        git_info["why"] = "--no-git"
    else:
        # Inside a repository that ignores the project, no round ever lists a
        # change (`_round_ignored`): said now, at the start, not on every turn.
        git_info["ignored"] = _round_ignored(root)
    # SPEC 36: `store.init` writes the Made-with-nopekit block at the bottom of
    # README.md; said here only when it wrote.
    readme = "README.md" if _readme_bytes(root) != readme_before else None
    project = rel(store.project_paths(root)["project"], root)
    # Every groundspace opens its own site from the editor (site.ensure_launch). Written
    # only when absent, so `init` in an existing repo leaves the person's own untouched.
    launch = site.ensure_launch(root, name)
    entry = ledger.meta.model_entry
    model_step = (f"write {entry} — a dataclass CONFIG plus build(config) -> dict" if entry
                  else f"write model/<thing>.py — a dataclass CONFIG plus build(config) -> "
                       f"dict — then record it as \"model_entry\": \"model/<thing>.py\" in "
                       f"{project}")
    # `--json`'s steps, each one line: the commands in code spans, as every
    # printed command a reader may copy (test_docs_commands reads them so).
    steps = [
        "interview the person in their words; write each thing they ask for as "
        "requirements/R1.json: {\"statement\": \"...\", \"source\": \"interview\"}, and show "
        "the list for a yes. Ask for sketches, photos, links, measurements, datasheets: "
        "`nopekit ingest <files> --kind sketch --desc '...'`",
        model_step,
        "first light: `nopekit check`, then `nopekit site init` and "
        "`nopekit site serve --watch` — give the person the URL it prints",
        "write claims/C1.json beside the first verdicts, each naming its requirements",
    ]
    people = ledger.meta.people

    if args.json:
        _dump({
            "root": root,
            "project": project,
            "meta": ledger.meta.to_dict(),
            "launch": launch,
            "readme": readme,
            "git": git_info,
            "people_refused": unlisted or None,
            "next": steps,
        })
        return 0

    _say(f"initialised nopekit project {name!r} at {root}")
    _say(f"  {project:<24} the project: its name, model entry, packs and people")
    _say(f"  {'requirements/':<24} one file per thing the person asked for, in their words")
    _say(f"  {'claims/':<24} one file per claim — what must be true")
    _say(f"  {'inputs/':<24} evidence you put in by hand ({len(store.INPUT_BUCKETS)} buckets),"
         f" one record per artifact")
    _say(f"  {'model/':<24} the parametric model: the only source of truth")
    _say(f"  {'docs/':<24} generated readiness report and decision log")
    if launch:
        _say(f"  {launch:<24} Run and Debug: builds and opens this project's site")
    if readme:
        _say(f"  {readme:<24} Made-with-nopekit badge, at the bottom")
    if git_info["started"]:
        _say(f"  {'.git/':<24} a new git repository: each round commits here")
    _say("")
    if people:
        _say(f"people: {people[0].name} <{people[0].email}> — a round commits only as a "
             f"person listed in {project}")
    elif unlisted:
        _say(f"people: none — {unlisted}; rounds make no commit until git's identity is "
             f"fixed and the person is listed in {project}")
    else:
        _say(f"people: none — git has no user.name and user.email here; rounds make no "
             f"commit until they are set and the person is listed in {project}")
    if not inside and not git_info["started"]:
        _say(f"git: no repository started ({git_info['why'] or 'git did not answer'}) — "
             f"rounds make no commit until the project is in one")
    if git_info["ignored"]:
        _say("git: the enclosing repository ignores this directory — rounds make no commit "
             "until it is un-ignored there, or the project has a repository of its own")
    _say("")
    _say("next, in this order:")
    _say("  1. interview the person in their words; write each thing they ask for as")
    _say('     requirements/R1.json: {"statement": "...", "source": "interview"}, and show')
    _say("     the list for a yes. Ask for sketches, photos, links, measurements, datasheets:")
    _say("     nopekit ingest <files> --kind sketch --desc '...'")
    if entry:
        _say(f"  2. write {entry} — a dataclass CONFIG plus build(config) -> dict")
    else:
        _say("  2. write model/<thing>.py — a dataclass CONFIG plus build(config) -> dict —")
        _say(f'     then record it as "model_entry": "model/<thing>.py" in {project}')
    _say("  3. first light: nopekit check, then nopekit site init and")
    _say("     nopekit site serve --watch — give the person the URL it prints")
    _say("  4. write claims/C1.json beside the first verdicts, each naming its requirements")
    return 0


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
#: An instrument note as `verdicts.resolve` words it (`<gate> — recorded under
#: <module> <a>; here <b>`): the one kind of resolution note `status` prints one
#: line each. The others — opaque channels, defining-file digests, hand-edited
#: entries — are `doctor`'s rows, where each says what to do about it.
_INSTRUMENT_NOTE = re.compile(r"^\S+ — recorded under \S+ \S+; here \S+$")

def _never_run(resolution: verdicts.Resolution, registry: gates.Registry | None) -> list[str]:
    """Registered gates with nothing to show — *unrun*: no row, or only an
    availability skip over no entry. A remembered crash is not unrun (S-68),
    and neither is an evaluator refused at its version: `resolve` says that
    refusal (`unqualified`, its claim a Gap), so it has something to show.
    What slipped through (review of P2.1, whose D5 said it closed this): a gate
    refused at its first check has a `never` row, and this asked only `state`,
    so `status` printed `gates: 6 ran, 1 unqualified` and two lines below
    `(6 verdicts current, 1 unrun)` — one evaluator, two words."""
    ids = registry.ids() if registry is not None else []
    out = []
    for gate_id in ids:
        row = resolution.rows.get(gate_id)
        if row is not None and row.admission is not None \
                and row.admission.state == "not-admitted":
            continue
        if row is None or (row.state == "never" and not any(
                str(note).startswith("remembered ") for note in row.notes)):
            out.append(gate_id)
    return out


def _stale_lines(resolution: verdicts.Resolution, registry: gates.Registry | None, *,
                 model_error: str = "", spend: bool = False) -> list[str]:
    """`status`'s `invalidated:` block (spec §3.13): one line per gate whose
    verdict is not current, with its reasons, continuation lines indented under
    the head, and the counts on the last — `(N verdicts current[, n unrun])`, a
    verdict being current when it is a Fresh entry whose control is admitted or
    pending. `invalidated: none` with the counts when nothing moved.

    In GLOSSARY §6's words from P2.1: a verdict whose read set moved is
    *invalidated* (§6.2's operation) and only its claim reads *Stale*; a gate
    with no verdict is *unrun* and its claim *Open*. What slipped through: the
    block was headed `stale:`, counted `checks current` and `never run`, three
    status words one screen below the claim rows they were not about.

    One line per gate, always. What slipped through while wiring it: with the
    model broken, the resolver's reason for every gate that reads it carries the
    whole import traceback (`the model does not load: <error>`), and the block
    printed it once per gate — twenty lines of the same traceback between the
    reader and the counts. The error is `model:`'s line, printed once below."""
    current = sum(1 for row in resolution.rows.values() if row.fresh)
    never = _never_run(resolution, registry)
    counts = (f"   ({current} verdicts current"
              + (f", {len(never)} unrun" if never else "") + ")")
    stale = _stale_gate_list(resolution)
    if not stale:
        return [f"invalidated: none{counts}"]
    # Issue #15, D1: the gates not current only for a channel nopekit cannot
    # see share ONE line in their own words — a count and one example, the
    # full channel list being `doctor`'s. What slipped through: one line per
    # gate, each its whole channel list, ~2 KB above the counts, every one
    # under "invalidated" for a design nobody had touched.
    unseen = claims.unseen_gates(resolution)
    lines = []
    for gate_id in [g for g in stale if g not in unseen]:
        row = resolution.rows.get(gate_id)
        why = (row.stale_reason if row is not None else "") or "invalidated"
        if model_error:
            why = why.replace(f": {model_error}", "")
        why = (why.splitlines() or ["invalidated"])[0]
        lines.append(f"{'invalidated: ' if not lines else '             '}{gate_id} — {why}")
    if unseen:
        lines.append(report.unseen_words("status", resolution, unseen, registry=registry,
                                         spend=spend))
    lines[-1] += counts
    return lines


def _pending(resolution: verdicts.Resolution) -> tuple[list[str], str]:
    """`(gates, moved)`: the gates whose control is pending re-verification, and
    the files that moved under their fixtures, merged — one sentence for all of
    them, which `status`'s note and `doctor`'s row both print."""
    pending = [row.gate for row in resolution.rows.values()
               if row.admission is not None and row.admission.state == "pending"]
    files: list[str] = []
    for gate_id in pending:
        # Structured (P2.3-D18): what `_PENDING_REASON` parsed back out of the
        # spine's prose, the admission now carries.
        for name in resolution.rows[gate_id].admission.moved:
            if name and name not in files:
                files.append(name)
    return pending, ", ".join(sorted(files)) or "fixture code"


def _pending_sentence(count: int, moved: str) -> str:
    """GLOSSARY §9's words for controls whose fixture code moved: they count, and
    the next check run re-qualifies their evaluators. What slipped through: it
    said "N control(s) pending" on `status`'s screen, the Open status's
    Never-say beside the claim rows (review of the P2.1 design)."""
    return (f"{count} evaluator(s) to re-qualify — control inputs moved ({moved}); "
            f"the next check run re-qualifies them")


def _note_lines(resolution: verdicts.Resolution) -> list[str]:
    """`status`'s `note:` lines: one per instrument mismatch, then at most one
    for every control pending re-verification, its moved files merged."""
    lines = [f"note: {note}" for note in resolution.notes if _INSTRUMENT_NOTE.fullmatch(note)]
    pending, moved = _pending(resolution)
    if pending:
        lines.append(f"note: {_pending_sentence(len(pending), moved)}")
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


#: How many pack names `status`'s packs line spells before "and N more": eight
#: fit one terminal line beside the count. *Rejected:* six with no "more" (the
#: count and the names disagreed: "7 available" over six names).
_PACKS_NAMED = 8


def _names(names: list[str], cap: int = _PACKS_NAMED) -> str:
    """`a, b, c` — or the first ``cap``, then `, and N more`."""
    shown = ", ".join(names[:cap])
    more = len(names) - cap
    return shown + (f", and {more} more" if more > 0 else "")


def cmd_status(args: argparse.Namespace) -> int:
    """The one-screen answer to "where is this project".

    `report.render_terminal` does the claim/gap/gate half, from `_resolved`'s
    view. Then, in a fixed order (spec §3.13), the facts that live outside the
    readiness report, each with its source:

    * `invalidated:` — each gate whose verdict is not current, with what moved
      (`config.bed_xy 220.0 -> 250.0`), and on the last line how many verdicts
      are current and how many gates are unrun; `invalidated: none` when nothing
      moved. What it replaced said "model <hash> -> <hash>": that SOMETHING
      moved, never which check it touched (M11.7). (Headed `stale:` until P2.1.)
    * `last check run:` — when `check` last swept the whole project
      (`last_check.json`), with its age; `never` before the first.
    * `round record:` — whether `.nopekit/round.json` is current with the
      records, the verdict cache and the design, behind them (naming what
      moved), edited by hand, unreadable or a newer nopekit's (architecture
      phase 3a, `_round_state`). Never what it holds: every status above is the
      resolver's.
    * `note:` — an entry recorded under another library version (provenance,
      never staleness, Q1.3), and at most one line for controls whose fixture
      code moved since they were demonstrated (they count; the next check run
      re-qualifies their evaluators).
    * `model:` — only when the model does not load, because then no verdict
      that reads it is current and the reader must know why first.

    Then the packs, the site, the names (not counts) of unread evidence and
    undefended parameters, and load problems. It never runs a gate or a fixture,
    and never writes: it reads the cache (M11.11).

    Never fails on a broken pack or an unloadable model — both are reported as
    lines. `status` is what you run when something is wrong.
    """
    root = _root(args)
    ledger = _load(root)
    registry, problems = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    now = utcnow_iso()
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=now, model=model)
    stale_gates = resolution.stale_gates

    installed = packs.installed(root, ledger=ledger)
    available = packs.available(root)
    unread = artifacts.unextracted(view)
    params = _shown_params(root, ledger, model, model_error, resolution, registry,
                           projection=projection)
    undefended = modelio.undefended_params(params)
    unseen = claims.unseen_gates(resolution)
    summary = claims.summarise(view, registry, stale_gates=stale_gates)
    composed = claims.compositions(view, registry=registry, stale_gates=stale_gates,
                                   unseen_gates=unseen)
    resolved = {cid: c.status for cid, c in composed.items()}
    stale_reasons = _stale_reasons(resolution)
    site_info = _site_state(root, resolved=(view, registry, resolution, params, model,
                                            projection))
    last = _last_check(root)
    last_when = str(last.get("when") or "")
    last_age = _seconds_between(last_when, now) if last_when else None
    # The round record's STATE only (architecture phase 3a): current, behind,
    # edited by hand. Never its statuses or readiness — nothing reads it for
    # truth, and the statuses above are the resolver's.
    traced = _trace_for(root, model, projection, resolution)
    round_state = _round_state(root, ledger, model, projection, resolution, registry, view,
                               model_error=model_error, shown=params, traced=traced)
    # Architecture phase 3b: the trace's state and amber, beside the statuses
    # — amber never changes one (invariant 12).
    amber: dict = {}
    if model is not None and traced.state in ("current", "none"):
        built_ = graph.of(root, view, resolution, registry, projection=projection, model=model,
                          views=params, bases=_input_bases(root, ledger), trace=traced.trace)
        amber = _amber_for(root, view, registry, built_,
                           {cid: graph._token(c.status, c.errored)
                            for cid, c in composed.items()}, traced)

    if args.json:
        _dump({
            "root": root,
            "meta": view.meta.to_dict(),
            "summary": summary,
            # `claims` keeps the enum values (P2.1-D12: JSON values move only in
            # the rename pass, GLOSSARY §7); the words are under `statuses`.
            "claims": {cid: str(status) for cid, status in resolved.items()},
            "statuses": _status_views(view, composed, stale_reasons),
            "errored": [cid for cid, c in composed.items() if c.errored],
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
            # P2.5a-D16: the rebuild prediction, additive (P2.1-D12).
            "rebuild": summary["rebuild"],
            # SPEC decision 12, additive: each requirement, its status from the
            # one producer over the compositions above.
            "requirements": _requirement_rows(view, composed, registry),
            # Architecture phase 3a, additive: the round record's state, never
            # what it holds.
            "round": {"state": round_state.state, "round": round_state.round,
                      "moved": list(round_state.moved), "why": round_state.why},
            # Architecture phase 3b, additive: the trace's state and amber —
            # display tokens beside the statuses above, never one of them.
            "trace": {"state": traced.state, "key": traced.key,
                      "round": traced.trace.round if traced.trace is not None else None,
                      "skipped": dict(traced.trace.skipped) if traced.trace is not None
                      else {},
                      "running": ({"pid": derivation.live_holder(root)}
                                  if derivation.live_holder(root) is not None else None)},
            "amber": {cid: {"state": a.state, "via": [list(v) for v in a.via],
                            "why": a.why} for cid, a in amber.items()},
        })
        return 0

    sys.stdout.write(report.render_terminal(view, registry, stale_gates=stale_gates,
                                            params=params, stale_reasons=stale_reasons,
                                            unseen_gates=unseen))
    if view.milestones:
        # Each milestone's line, as last evaluated (D17; review of P2.5b,
        # finding 5: SPINE_CONTRACT and the skill said `status` listed them, and
        # it printed none).
        _say(report.HUMAN["milestone"]["report_head"])
        for milestone_ in view.milestones:
            _say("  " + report.milestone_line(view, composed, milestone_))
    for line in _stale_lines(resolution, registry, model_error=model_error,
                             spend=bool(getattr(view, "milestones", None))):
        _say(line)
    # "last check run" (GLOSSARY §6: one invocation of `check` is a check run):
    # `last check:` read as a noun for an evaluator one line under the claims.
    if last_when:
        age = (f" ({human_duration(last_age)} ago)" if last_age is not None and last_age >= 0
               else " (in the future)" if last_age is not None else "")
        _say(f"last check run: {last_when}{age}")
    else:
        _say("last check run: never")
    _say(report.HUMAN["round"]["state_head"].format(line=_round_state_line(round_state)))
    if model is not None:
        for line in _trace_lines(root, traced):
            _say(line)
        line = _amber_line(traced, amber)
        if line:
            _say(line)
    for line in _note_lines(resolution):
        _say(line)
    if model_error:
        entry = view.meta.model_entry or "(none recorded)"
        _say(f"model: {entry} DOES NOT LOAD — {model_error.splitlines()[0]}")

    # Every pack it counts, up to `_PACKS_NAMED`, then how many more. What
    # slipped through: the line said "7 available" and named six, cut at a
    # slice nobody announced.
    if installed:
        _say(f"packs: {', '.join(installed)} "
             f"({len(available)} available: {_names(available) or 'none'})")
    elif available:
        _say(f"packs: none installed; {len(available)} available "
             f"({_names(available)}) — `nopekit packs add <name>`")

    # Mentioned only when the project has one: a line telling every project
    # without a site that it does not have a site is noise in the one command
    # that has to stay readable at a glance.
    if site_info["present"]:
        _say(_site_line(site_info))

    if unread:
        names = ", ".join(a.id for a in unread[:5])
        more = f", +{len(unread) - 5}" if len(unread) > 5 else ""
        _say(f"unread evidence: {names}{more} — `nopekit extract <id> --what ...`")
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
        raise NopekitError(_JUNIT_RULE)
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
        raise NopekitError(
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

    An executed row and a cached row that did not pass each print — an
    unqualified evaluator's row does not: its qualification line follows the
    summary (P2.3-D17); a cached pass does not — the inner loop is for what moved
    or what is wrong, and five unchanged `[ok  ]` lines between you and the FAIL
    you came for is the wall `_skip_digest` exists to collapse. Skips are that
    digest's, after the summary — keyed on `Verdict.outcome`, never the flag:
    what slipped through (P2.0 F-10), a verdict that said skipped AND errored
    went to the digest under the quiet `[skip]` tag, with its skip reason, below
    the plain skip; it is a crash, and streams as one.
    """
    verdict = row.verdict
    if verdict.outcome == "skipped":
        return None
    if getattr(verdict, "unqualified", ""):
        # Its qualification line follows the summary (`_qualification_lines`):
        # an unqualified evaluator crashed nothing, and `[ERR ]` is a crash's
        # tag (P2.3-D17). What slipped through P2.1's review: the count was
        # fixed and this row was not — `[ERR ] <gate> : not admitted: …`.
        return None
    if row.cached:
        if verdict.ok:
            return None
        return f"{verdict.render():<{_CACHED_COLUMN}} cached"
    return verdict.render()


def _row_outcome(verdict: Verdict) -> str:
    """A sweep row's outcome for `check`'s tallies: `Verdict.outcome`, with an
    evaluator refused at its version counted as `unqualified` — never errored.
    GLOSSARY §2: a qualification is not an outcome; the refusal's verdict says
    `error` only so that it is never ok (R-2). A pass outside its evaluator's
    operating context is `outside-context` (P2.4-D22): not the evaluator's
    refusal — qualified is "at its version", and outside its context it still
    is — so never counted `unqualified` either."""
    token = getattr(verdict, "unqualified", "") or ""
    if token:
        return "outside-context" if claims.outside_context(verdict) else "unqualified"
    return verdict.outcome


def _qualification_lines(rows: list[verdicts.SweepRow]) -> list[str]:
    """`check`'s qualification lines (P2.3-D16): one for every unqualified
    evaluator, on every check run, and one for every evaluator walked by the
    mutation pass — not from a bundled pack — whose qualification ran in this
    one. A bundled evaluator that qualified is counted in the `controls:` line
    and printed nowhere here: up to 54 lines after an upgrade, none a person
    acts on. *Rejected:* a line per qualification run; no lines (W7: the
    walkthrough's moment in prose)."""
    out = []
    for row in rows:
        admission = row.admission
        facts = getattr(admission, "qualification", None) if admission is not None else None
        outside = claims.outside_context(row.verdict)
        if outside and facts is not None and admission.executed \
                and facts.mutation is not None:
            # A walked evaluator qualified in this run, whose pass lies outside
            # its operating context: its qualification line, then the fact
            # that keeps this pass from counting (P2.4).
            out.append(report.qualification_line(row.verdict.gate, facts))
        if getattr(row.verdict, "unqualified", ""):
            out.append(report.verdict_line(row.verdict, facts))
        elif facts is not None and admission.executed and facts.mutation is not None:
            out.append(report.qualification_line(row.verdict.gate, facts))
    return out


def _requirement_rows(view: Ledger, composed: Mapping[str, Any],
                      registry: Any) -> list[dict[str, Any]]:
    """`requirements` in `status --json` and `report --json` (SPEC decision 12):
    `report.requirement_json` over `claims.requirement_statuses`, the one
    producer — never a status folded here — with the claims whose status and
    evidence disagree named (`report.requirement_statuses`, D18)."""
    found = report.requirement_statuses(view, composed,
                                        claims.effective_gates(view, registry))
    return [report.requirement_json(r, found[r.id]) for r in view.requirements]


def _mechanism_lines(view: Ledger, registry: Any) -> list[str]:
    """`check`'s mechanism warnings (SPEC decision 13): one per evaluator a
    claim's mechanism names that is not registered here, or does not cover the
    claim. On stdout beside the limit warnings, never a status and never the
    exit code: the list is a cross-check, never coverage (S-37)."""
    said = report.HUMAN["mechanism"]
    return [said["warning"].format(line=report.mechanism_problem_words(found))
            for found in claims.mechanism_problems(view, registry)]


def _limit_lines(view: Ledger, stale_gates: Iterable[str] = ()) -> list[str]:
    """`check`'s warning lines (P2.4-D13, S-35): one per compared pair whose
    evaluator judged against a limit of its own that is not its claim's — one
    number in two places. On stdout, after the qualification lines: a warning
    on stderr is one nobody reads in a captured run. Never a status. Current
    verdicts only (`stale_gates` left out): a stale one's limit is its old
    inputs', and the next check run settles it."""
    said = report.HUMAN["acceptance"]
    out = []
    for found in claims.limit_disagreements(view, stale_gates=stale_gates):
        unit = f" {found.units}" if found.units else ""
        out.append(said["warning"].format(line=said["limits"].format(
            gate=found.gate, limit=f"{report._num(found.limit)}{unit}", claim=found.claim,
            condition=found.acceptance)))
    return out


def _limits_left_out(view: Ledger, resolution: Any, registry: Any) -> str:
    """`doctor`'s limits clause for what its row did not compare, or ``""``:
    each covering evaluator whose value a claim with a limit compares
    (`claims.compared_gates`), whose verdict states a limit of its own, and
    which `claims.limit_disagreements` leaves out because it is in the
    resolver's ``stale_gates`` — said as reading inputs nopekit cannot see
    (`claims.unseen_gates`) or as stale — then the check run that compares
    them (`report._tier_flag`). In claim order, each evaluator once.

    What slipped through (holdout, general packs, round 2, D06, disputed): an
    evaluator that reads inputs nopekit cannot see is in ``stale_gates`` after
    every check run, so the row never compared it, and read "ok — every
    compared evaluator judges against its claim's own limit" while `check`
    warned that its limit parted from its claim's. It read true on one design
    only because that evaluator came to report its claim's limit. *Rejected:*
    comparing such an evaluator's last limit here (its seen inputs may have
    moved too, as a limit edit moves a stale one's, and the row would blame it
    for an old number: review of P2.4); a warning for each (the opaque-inputs
    row already warns of the channel, and the row moves no status)."""
    stale = frozenset(getattr(resolution, "stale_gates", None) or ())
    if not stale:
        return ""
    unseen = claims.unseen_gates(resolution)
    said = report.HUMAN["acceptance"]
    named: dict[str, str] = {}
    for claim in view.claims:
        compared = set(claims.compared_gates(claim, view.verdicts))
        for verdict in claims.covering_verdicts(claim, view.verdicts):
            gid = verdict.gate
            limit = verdict.limit
            if gid in named or gid not in stale or gid not in compared:
                continue
            if isinstance(limit, bool) or not isinstance(limit, (int, float)) \
                    or not math.isfinite(limit):
                continue            # no limit of its own: nothing for the row to compare
            key = "left_out_unseen" if gid in unseen else "left_out_stale"
            named[gid] = said[key].format(gate=gid)
    if not named:
        return ""
    return said["doctor_left_out"].format(
        list=", ".join(named.values()), tier_flag=report._tier_flag(registry, named),
        them="it" if len(named) == 1 else "them")


def _controls_line(controls: Mapping[str, int]) -> str:
    """`controls: N run, N preserved, N re-qualified` (GLOSSARY §9): run —
    fixtures and gate ran; preserved — the records settled it; re-qualified —
    the fixtures alone ran and every value held. The JSON keeps
    `executed`/`cached`/`reverified` (P2.1-D12)."""
    return report.HUMAN["qualification"]["controls"].format(
        run=controls["executed"], preserved=controls["cached"],
        requalified=controls["reverified"])


def _check_summary(rows: list[verdicts.SweepRow], counts: Mapping[str, int], tier: int) -> str:
    """`6 gates: 1 executed, 5 cached — 5 ok, 1 FAIL — tier 0` (spec §3.13).

    What left the line, and why: the elapsed time (a mostly-cached sweep takes
    no time worth reading, and `--json` keeps `duration_s`), and the model hash
    (one hash of the projection said THAT something moved; which check it
    touched is `status`'s `stale:` line now). FAIL, skipped, errored and
    unqualified appear only when non-zero. What slipped through (review of
    P2.1): an evaluator refused at its version was counted `errored` here and
    in `--json` while `status` said `1 unqualified` — invariant 2's loud count
    inflated by something that crashed nothing."""
    outcomes = [_row_outcome(row.verdict) for row in rows]
    line = (f"{len(rows)} gates: {counts.get('executed', 0)} executed, "
            f"{counts.get('cached', 0)} cached — {outcomes.count('pass')} ok")
    for outcome, word in (("fail", "FAIL"), ("skipped", "skipped"), ("error", "errored"),
                          ("unqualified", report.HUMAN["lead"][claims.ClaimCause.UNQUALIFIED]),
                          ("outside-context", report.HUMAN["context"]["tally"])):
        if outcomes.count(outcome):
            line += f", {outcomes.count(outcome)} {word}"
    return f"{line} — tier {tier}"


class _Checked(NamedTuple):
    """What `_check_run` did: the sweep's ``result``, the ``view`` and
    ``resolution`` judged from it (this sweep's rows laid over the resolver's),
    the ``registry``, ``model`` and ``projection`` it ran on, and ``elapsed``."""

    result: Any
    view: Any
    resolution: Any
    registry: Any
    model: Any
    projection: Any
    elapsed: float


def _check_run(root: str, ledger: Ledger, *, tier: int, only: list[str] | None,
               force: bool, record: bool, now: str,
               on_row: Callable[[verdicts.SweepRow], None] | None, quiet: bool,
               registry: gates.Registry | None = None) -> _Checked:
    """`check`'s locked body after the migration, and `round`'s check
    (architecture phase 3a): the strict registry, the model, the sweep, the view
    judged from it, and — recorded — `last_check.json` and the index. Under the
    caller's lock. Raises what `check` raises (a pack that does not import, a
    model that does not load); writes exactly what `check` writes, and never
    the round record (invariant 8: only `round` writes that, after this
    returns). ``registry`` is the caller's strict one, when it loaded it first."""
    if registry is None:
        registry, _ = _registry(root, ledger, strict=True)
    model, projection = _projection(root, ledger)
    ctx = _context(root, ledger, model, projection, tier, quiet=quiet)

    if projection is None and registry.specs():
        _warn(f"warning: {_no_entry(root)} — until then, gates that read "
              f"ctx.params will error")

    started = time.perf_counter()
    result = verdicts.sweep(root, registry, ctx, projection=projection, ledger=ledger,
                            max_tier=tier, only=only, force=force, record=record,
                            now=now, on_row=on_row)
    elapsed = time.perf_counter() - started

    view, resolution = _resolved(root, ledger, registry, projection, "", now=now,
                                 model=model, sweep=result)
    if record:
        # `params` is the parameter view (1.3): the model's value and where it
        # lives, and what lost in both homes — beside the statuses, so the
        # agent's second read has the numbers `why` would print. It was `{}`
        # from 1.2 until the view existed. From phase 3a each row also names
        # the evaluators whose served entry read it and what sources it
        # (`graph.observe`).
        views = graph.observe(
            modelio.param_view(ledger, model,
                               read_sets=_registered_reads(resolution.read_sets, registry)),
            ledger=ledger, resolution=resolution, registry=registry,
            choices=_choices(model), derived=graph.derived_names(projection))
        verdicts.write_last_check(root, result, resolution, now=now,
                                  params={view_.name: view_.to_dict() for view_ in views})
        # The index, still under the lock, so the sweep that just migrated a
        # legacy project leaves it indexed before any reader can look.
        # `main` touches it again after the command; unchanged, that writes
        # nothing.
        _touch_index(root)
    return _Checked(result, view, resolution, registry, model, projection, elapsed)


def _choices(model: Any) -> tuple[str, ...]:
    """The model's choices — its `Config` fields — for a read of the whole
    `config` (`graph.value_names`); none without a model."""
    config = getattr(model, "config", None)
    if config is None or not dataclasses.is_dataclass(config):
        return ()
    return tuple(f.name for f in dataclasses.fields(config))


def cmd_check(args: argparse.Namespace) -> int:
    """Run what moved, serve what did not, and exit non-zero while anything critical blocks.

    **Affected-only** (D-05). `verdicts.sweep` is the loop: per selected gate,
    in plan order (each gate after its prerequisites, `gates.plan`; `--only`
    runs the named gates' prerequisites too), first the prerequisite rule — a
    gate whose prerequisite is not established is not run and reads the
    prerequisite skip, or its own crash, refusal or missing tool — then
    availability, then admission (the gate's negative
    control, run on a control-entry miss — S-05: a logger with a declared
    control produced PROVEN rows because nothing here ever ran a control), then
    the verdict cache (a Fresh entry is served, unless a crash at its inputs
    superseded it; two outcomes at its inputs are served as `status`'s error and
    never re-run — a run agrees with one of the two and settles nothing), then
    the run. The sweep's notes (the writer's warnings) print as `note:` lines,
    and are `notes` in `--json`. Every selected gate gets a row — executed,
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
      re-proves it, and CI runs the bracket this way. A forced run's row is what
      the records resolve to with its entry filed: at tier 0 it re-proves the
      cheap path, and never lays that PASS over a costlier tier's FAIL at the
      same inputs (`verdicts._outranked`).
    * `--no-record` is a dry sweep: nothing under `.nopekit/` but gate scratch in
      `out/` — no cache or control entry, no obs, no remembered outcome, no
      `last_check.json`, no index, and a legacy ledger migrates in memory only
      (S-32: it used to write the ledger anyway and read its own fresh passes as
      STALE, because the one global clock had not moved; there is no global
      clock now).
    * `--only` and `--tier` select as they always did; a filtered sweep writes
      its entries but no `last_check.json` — a partial sweep's summary would
      stand for the whole project's.
    * Otherwise, after the sweep: `verdicts.write_last_check`, and the index is
      rebuilt from the records. No run history (S-89: every recorded check
      rewrote the tracked ledger and appended a tracked run file).

    **`check` writes no record** (checkpoint 1.3). It used to re-sync every
    parameter from the model, copy grounding back-references into parameters
    and coverage into claims, and save the whole ledger — so a sweep rewrote
    what humans edit (S-36, S-37; see `_migrate`'s section). The one record
    write it may make is the one-time migration of a legacy `ledger.json`, under
    the held lock and before anything reads the project (`_migrate`): the
    records, the ignore blocks, `project.json` last, the legacy file renamed,
    one stderr notice. A second check finds a migrated project and writes only
    verdict entries and ignored scratch.

    The clock is stamped ONCE (`now`): obs, remembered outcomes,
    `last_check.json`, the migration's notice and the JUnit report carry the
    same instant.

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
        # First, under the lock and before anything reads the project: a legacy
        # ledger migrates here, once (in memory under `--no-record`).
        ledger = _migrate(root, apply=record, now=now)

        def _landed(row: verdicts.SweepRow) -> None:
            # `sweep` calls this the instant a gate's row exists, before the next
            # gate starts: a tier-2 sweep streams, it does not go quiet for minutes.
            line = None if args.json else _check_row_line(row)
            if line is not None:
                _say(line)

        ran = _check_run(root, ledger, tier=tier, only=only, force=force, record=record,
                         now=now, on_row=_landed, quiet=args.json)
        result, view, resolution, registry = ran.result, ran.view, ran.resolution, ran.registry
        projection, elapsed = ran.projection, ran.elapsed

        stale_gates = resolution.stale_gates
        unseen = claims.unseen_gates(resolution)
        blockers = claims.blocking(view, registry, stale_gates=stale_gates)
        summary = claims.summarise(view, registry, stale_gates=stale_gates)
        composed = claims.compositions(view, registry=registry, stale_gates=stale_gates,
                                       unseen_gates=unseen)

    rows = list(result.rows)
    selected = {row.verdict.gate for row in rows}
    # Registered gates this sweep did not select (above the ceiling, outside
    # `--only`) that still have an effective verdict. An unregistered gate's row
    # is not "carried over" by anything: it is an orphan, stale by definition,
    # and `doctor` names it.
    registered = set(registry.ids())
    carried = [v for v in view.verdicts if v.gate not in selected and v.gate in registered]
    stale_reasons = _stale_reasons(resolution)
    counts = {
        "ran": sum(1 for row in rows if row.verdict.ok),
        "failed": sum(1 for row in rows if row.verdict.outcome == "fail"),
        "skipped": sum(1 for row in rows if row.verdict.outcome == "skipped"),
        "errored": sum(1 for row in rows if _row_outcome(row.verdict) == "error"),
        "unqualified": sum(1 for row in rows if _row_outcome(row.verdict) == "unqualified"),
        "outside_context": sum(1 for row in rows
                               if _row_outcome(row.verdict) == "outside-context"),
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
    # SPEC 14, 15: each rejected alternative whose return condition holds now —
    # from the same view and compositions, after the code is fixed; nothing
    # reads it back into `code` (invariant 12: it never moves the exit code).
    returning = claims.may_return(claims.rejection_rows(view, composed))

    # The sweep's own notes — the writer's warnings, entries it ignored. They
    # were collected and printed nowhere: a forced run that wrote a FAIL beside
    # its PASS at one rho said nothing, and `doctor` was the first to know (the
    # review). Each once, in the order they arose.
    notes = [str(note) for note in dict.fromkeys(result.notes)]

    # Invariant 12: every reader but `export` says *ready* as last evaluated.
    # A ready that rests on evaluators nopekit cannot see into holds in this run
    # only — `status`, the report and the site read those claims Stale the
    # moment it ends — so every channel of this run says so: the line under
    # `ready:`, the JSON and the JUnit file (issue #15, D1; the JUnit file and
    # the JSON in its review: a CI reader saw `ready=true` and nothing that
    # joined it to the NOT ready every other reader said a second later).
    rests = _unseen_in_run(rows, view, composed) if (view.claims and not blockers) else []
    ready_note = (report.unseen_words("ready", resolution, rests, registry=registry)
                  if rests and claims.unresolved(view, composed).ready else "")

    spine = verdicts.spine_digest()
    written = _junit_write(junit, lambda: report.render_junit(
        view, [row.verdict for row in rows], registry, tier=tier, ready=ready,
        exit_code=code, when=now, not_run=result.not_run,
        cached={row.verdict.gate for row in rows if row.cached}, spine=spine,
        stale_gates=stale_gates, stale_reasons=stale_reasons, unseen_gates=unseen,
        ready_note=ready_note))

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
            # Record order, as before (machine, diff-stable); each row gains the
            # words beside its kept enum `status` (P2.1-D12).
            "blocking": [{"claim": claim.id, "status": str(status),
                          "statement": claim.statement,
                          **report.status_view(composed[claim.id], view, claim,
                                               stale_reasons=stale_reasons)}
                         for claim, status in blockers],
            # `ready` keeps its meaning — nothing stops this check run — and
            # `all_required_checked` is *ready* in GLOSSARY §4's sense, beside
            # it (critique of the P2.1 design: a reader of `ready` alone read a
            # project waiting for an article as ready).
            "ready": ready,
            "all_required_checked": bool(summary["all_required_checked"]),
            # Additive (review of issue #15's D1): the note `check` prints under
            # `ready:` when that ready holds for this run only; "" otherwise.
            "ready_note": ready_note.removeprefix("note: "),
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
            "notes": notes,
            # Additive (P2.4-D13): the compared pairs whose two limits part.
            "limit_disagreements": [found._asdict()
                                    for found in claims.limit_disagreements(
                                        view, stale_gates=stale_gates)],
            # Additive (SPEC decision 13): the evaluators claims' mechanisms
            # name that are not registered or do not cover them — warnings only.
            "mechanism_problems": [dict(found._asdict(), filed=list(found.filed))
                                   for found in claims.mechanism_problems(view, registry)],
            # Additive (holdout, general packs): each claim tag a claim carries
            # that gates of two or more installed packs bind — a note only.
            "tag_collisions": [{"tag": c.tag, "claims": list(c.carried),
                                "gates": {pack: list(ids) for pack, ids in c.gates}}
                               for c in packs.tag_collisions(registry, view.claims)
                               if c.carried],
            # Additive (P2.1-D12): each evaluator's qualification as this check
            # judged it — the line in words, the judge's token, and the state.
            # The token is the QUALIFICATION's alone: a pass outside its
            # operating context carries `context:outside` on the verdict, a fact
            # about this pass's inputs, and its evaluator is qualified (review of
            # P2.4: the row read `state: admitted`, its line `→ qualified`, and a
            # non-empty token a reader took for a refusal). The context fact is
            # the verdict row's `unqualified` and `counts.outside_context`.
            "qualifications": [
                {"gate": row.verdict.gate,
                 "line": report.qualification_line(row.verdict.gate,
                                                   row.admission.qualification),
                 "token": ("" if claims.outside_context(row.verdict)
                           else row.verdict.unqualified or ""),
                 "state": row.admission.state}
                for row in rows
                if row.admission is not None and row.admission.qualification is not None],
            # P2.5a-D16: the rebuild prediction, additive.
            "rebuild": summary["rebuild"],
            # Additive (SPEC 14): the rejected alternatives that may return; the
            # full list is `summary.rejections`.
            "may_return": [row.to_dict() for row in returning],
        })
        return code

    _say(_check_summary(rows, result.counts, tier))
    if counts["controls"]["executed"] or counts["controls"]["reverified"]:
        _say(_controls_line(counts["controls"]))
    for line in _qualification_lines(rows):
        _say(line)
    for line in _limit_lines(view, stale_gates):
        _say(line)
    for line in _mechanism_lines(view, registry):
        _say(line)
    for line in _tag_lines(view, registry):
        _say(line)
    for row in returning:
        _say(report.may_return_line(row, view))
    for note in notes:
        _say(f"note: {note}")

    # The skips, one line per distinct reason instead of one per gate. They come
    # after the summary and before the blockers on purpose: the summary already
    # carries the count, and the thing you must act on has to stay at the bottom
    # of the screen where the eye lands.
    for line in _skip_digest([row.verdict for row in rows
                              if row.verdict.outcome == "skipped"]):
        _say(line)

    if carried:
        shown = ", ".join(v.gate for v in carried[:4])
        more = f", +{len(carried) - 4}" if len(carried) > 4 else ""
        # Issue #15, D1: one not current only for a channel nopekit cannot see
        # is counted in its own words, never as "invalidated".
        unseen_n = sum(1 for v in carried if v.gate in stale_gates and v.gate in unseen)
        stale_n = sum(1 for v in carried if v.gate in stale_gates) - unseen_n
        tail = [f"{stale_n} of them invalidated"] if stale_n else []
        tail += [f"{unseen_n} of them with {report.HUMAN['lead'][claims.ClaimCause.UNSEEN]}"
                 ] if unseen_n else []
        _say(f"note: {len(carried)} gate(s) outside this sweep keep their last verdict "
             f"({shown}{more})" + (f" — {', '.join(tail)}" if tail else ""))

    if not view.claims:
        # "ready" on a project that has never stated what must be true is the
        # laundering this whole tool exists to refuse: zero blocking claims
        # out of zero claims is not evidence of anything. Non-zero, so that the
        # exit code says the same thing this line says.
        _say("no claims recorded, so nothing was evaluated — a project with no claims "
             "is not ready. Write the first as claims/C1.json: a statement and an "
             "acceptance")
    elif not blockers:
        # `ready:` only when every required claim reads Checked (GLOSSARY §4);
        # otherwise what stands between the project and it. What slipped through:
        # `ready: no critical claim is blocking` printed while a claim waited
        # for an article and an assumption had no owner.
        _say(report.not_ready_line(view, composed))
        # The note on the next line (`ready_note`, above). What slipped
        # through before it (issue #15, D1): `check` said ready and every
        # other reader, a second later, NOT ready, with nothing to join them.
        if ready_note:
            _say(ready_note)
    else:
        # In severity order (P2.1-D16): what slipped through (P2.0 F-2), record
        # order put a missing tool's `[skip ]` above a crash's row.
        _say(f"BLOCKING — {len(blockers)} critical claim(s) must not be spent against:")
        for claim in report.in_severity(view, composed, [c for c, _ in blockers]):
            found = composed[claim.id]
            _say(_blocking_line(claim, found.status,
                                _blocking_reason(view, claim, found.status,
                                                 stale=stale_reasons, unseen=unseen),
                                errored=found.errored))
    # The rebuild prediction (P2.5a-D16), after the claim rows: one line per
    # article whose design moved — a prediction, never a refusal.
    for found in claims.rebuild(view):
        _say(report.rebuild_line(found))
    return code


# --------------------------------------------------------------------------- #
# round: the end of a round (SPEC 18, 24 — without the hook and the commit)
# --------------------------------------------------------------------------- #
def _round_inputs(root: str, ledger: Ledger, registry: gates.Registry, model: Any,
                  projection: Any, view: Ledger, resolution: verdicts.Resolution, *,
                  model_error: str = "",
                  shown: list | None = None, built: Any = None,
                  traced: derivation.TraceRead | None = None) -> tuple[dict, dict]:
    """``(body, computed_now)``: the round record's content for ``view`` under
    ``resolution`` — `graph.build` over the design an article reads
    (`verdicts.design_of`) and the current design's trace, `graph.tokens` from
    the compositions `status` shows, and amber beside them — and the digests it
    is computed from (the trace's among them, architecture phase 3b). Pure but
    for reading the records, the cache and the trace to digest them; writes
    nothing. ``shown`` is the caller's `_shown_params`, ``traced`` its
    `_trace_for`, when it has them."""
    anchors = resolution.anchors or verdicts.Anchors(root=os.path.abspath(root))
    design = verdicts.design_of(root, projection, model, anchors=anchors,
                                resolution=resolution)
    if traced is None:
        traced = _trace_for(root, model, projection, resolution)
    if shown is None:
        shown = _shown_params(root, ledger, model, model_error, resolution, registry,
                              projection=projection)
    if built is None:
        built = graph.build(view, resolution, registry, projection=projection, design=design,
                            views=shown, bases=_input_bases(root, ledger), trace=traced.trace)
    found = graph.tokens(view, resolution, registry)
    amber = _amber_for(root, view, registry, built, found.claims, traced)
    computed_now = rounds.computed_from(root, design,
                                        trace_digest=derivation.trace_digest(root, traced.key))
    return rounds.body(computed_now, found, built, amber=_amber_record(amber)), computed_now


def _round_state(root: str, ledger: Ledger, model: Any, projection: Any,
                 resolution: verdicts.Resolution, registry: Any, view: Ledger, *,
                 model_error: str = "", shown: list | None = None,
                 traced: derivation.TraceRead | None = None) -> rounds.RoundState:
    """Whether `.nopekit/round.json` is current with the project as it is — for
    `status` and `doctor`, which say so and read nothing else of it: its three
    digests against the project's, and what it records against what `round`
    would write now (`_round_inputs`), so a move the digests do not cover — an
    evaluator's code, its controls, a pack — reads behind, naming the
    resolution (`rounds.RESOLUTION`)."""
    content, computed_now = _round_inputs(root, ledger, registry, model, projection, view,
                                          resolution, model_error=model_error, shown=shown,
                                          traced=traced)
    return rounds.state(root, computed_now, content)


def _round_state_line(found: rounds.RoundState) -> str:
    """The round record's state in words, without `status`'s prefix — `doctor`'s
    row and `status`'s line say the same sentence."""
    said = report.HUMAN["round"]
    number = f"round {found.round}, " if found.round is not None else ""
    if found.state == "absent":
        return said["state_absent"]
    if found.state == "current":
        return said["state_current"].format(round=number)
    if found.state == "behind":
        return said["state_behind"].format(round=number, what=report.round_moved(found.moved))
    if found.state == "edited":
        return said["state_edited"].format(round=number)
    if found.state == "newer":
        return said["state_newer"].format(schema=found.schema)
    return said["state_unreadable"].format(why=found.why or "not JSON")


def _first_line(exc: BaseException) -> str:
    return (str(exc).strip().splitlines() or [""])[0]


#: Seconds the Stop hook waits for the build lock before it says "round not run"
#: (architecture phase 6). `site serve --watch` rebuilds the moment a round
#: writes, and an agent's last `check` may still hold the lock when its turn
#: ends; a round that gave up at once would miss the turn it exists for. Why 60:
#: a site build or a tier-0 check takes seconds, and the hook's whole budget is
#: 300 s (`hooks/hooks.json`), the commit's own `vcs.VCS_WRITE_TIMEOUT_S` 120 of
#: them. *Rejected:* waiting in plain `round` too (a person who types it wants the
#: held lock named now — `RoundNeverBlocks` holds that); no wait (the hook would
#: say "not run" whenever the watcher was mid-build).
ROUND_LOCK_WAIT_S = 60.0
_ROUND_LOCK_POLL_S = 0.5


@contextlib.contextmanager
def _round_locked(root: str, wait_s: float):
    """`_lock(root)`, retried every `_ROUND_LOCK_POLL_S` until ``wait_s`` runs
    out; with ``wait_s`` 0 the first refusal is raised, as `_lock` raises it."""
    lock = _lock(root)
    deadline = time.monotonic() + max(0.0, float(wait_s))
    while True:
        try:
            lock.acquire()
            break
        except NopekitError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(_ROUND_LOCK_POLL_S)
    try:
        yield lock
    finally:
        lock.release()


class CommitOutcome(NamedTuple):
    """What `round --commit` did with git (architecture phase 6, SPEC 24 with
    §0.3). ``why``: ``committed``, ``nothing-changed``, ``ignored``,
    ``no-repository``, ``no-identity``, ``no-people``, ``not-listed``,
    ``committer-not-listed`` (``who`` is then the committer), ``in-progress``,
    ``detached``, ``newer-record``, ``unlisted``, ``add-failed``, ``refused``
    or ``timeout``; ``detail`` the operation, git's line or the timeout;
    ``staged`` what the commit holds (or would have); ``not_files``,
    ``too_large`` and ``secret`` what it named instead (`rounds.StagePlan`'s);
    ``earlier`` ``(a, b)`` when rounds a to b were never committed and this
    commit holds them."""

    made: bool
    oid: str | None
    who: str | None
    why: str
    detail: str
    staged: tuple
    not_files: tuple
    too_large: tuple
    secret: tuple
    earlier: tuple | None
    subject: str


def _no_commit(why: str, *, who: str | None = None, detail: str = "", staged: tuple = (),
               plan: rounds.StagePlan | None = None) -> CommitOutcome:
    named = plan or rounds.StagePlan((), (), (), ())
    return CommitOutcome(False, None, who, why, detail, tuple(staged), named.not_files,
                         named.too_large, named.secret, None, "")


class RoundOutcome(NamedTuple):
    """What `_round_run` did: ``not_run`` (why, or ``""``), the round record's
    `rounds.Written`, the record's ``content``, the tier-0 ``check``, the
    ``site``, the resolved ``view`` and its ``composed`` statuses, each board's
    text's stamp (``prose``, `page.stamp_states`' rows), the ``commit`` (a
    `CommitOutcome`, or None without ``--commit``), the ``trace`` sentence, the
    ``summary`` paragraph, and ``trace_state``, `--json`'s ``trace`` object
    (phase 3b): ``{"state", "key", "log"}``, the start's state, else the trace's
    as `_trace_for` read it."""

    not_run: str
    done: Any = None
    content: Any = None
    check: Any = None
    site: Any = None
    view: Any = None
    composed: Any = None
    prose: tuple = ()
    commit: Any = None
    trace: str = ""
    summary: str = ""
    trace_state: Any = None


def _round_start_trace(root: str, done: rounds.Written, *, traced: derivation.TraceRead,
                       view: Ledger, resolution: verdicts.Resolution, registry: Any,
                       built: Any, composed: Mapping[str, Any], model_error: str) -> dict:
    """Start `nopekit model trace` in the background when the round wants it
    (architecture phase 3b, SPEC 39, in phase 6's seam): `derivation.needed` —
    ``trace`` when the current design has no trace (its instructions, its
    choices' values or the data `build()` read moved since the last one: the
    trace's key, wider than `changes.instructions`), ``amber`` when it has one
    and a Checked claim's amber is not current — and only when the model loaded.
    `derivation.start_background` keeps its rules: nothing under
    ``NOPEKIT_TRACE_BACKGROUND=0``, nothing while a live process holds
    `.nopekit/trace.lock`, never again by itself for a key whose last background
    run stopped short (a person runs that one in the foreground), its output
    appended to `.nopekit/out/trace.log`.

    Called after the commit and outside the lock, so the trace can take the lock
    for its snapshot, and a trace still writing `.nopekit/trace/` lands in the
    next round's commit, whole. ``{"state", "key", "log", "why"}``: ``state`` is
    ``""`` when nothing was wanted, else the start's (``amber`` for a started
    amber re-run); `report.round_trace_sentence` words it. Never raises: a
    refusal reads ``failed``, because a round never blocks."""
    info: dict = {"state": "", "key": traced.key[:12] if traced.key else None, "log": None,
                  "why": ""}
    if model_error or traced.state not in ("none", "current"):
        return info
    try:
        found = graph.tokens(view, resolution, registry, composed=composed)
        wanted = derivation.needed(traced, _amber_for(root, view, registry, built,
                                                      found.claims, traced))
    except NopekitError as exc:
        info.update(state="failed", why=_first_line(exc))
        return info
    if wanted:
        state, detail = derivation.start_background(root, round_=done.round, key=traced.key)
        info.update(state=("amber" if wanted == "amber" and state == "started" else state),
                    log=derivation.LOG_REL if state in ("started", "running") else None,
                    why=detail if state == "failed" else "")
    return info


def _round_ignored(root: str) -> bool:
    """Whether the repository ignores the project itself: its project file
    untracked and ignored. What slipped through (review of phase 6): a project
    under a directory its enclosing repository ignores (a dotfiles repository
    in ``$HOME`` with ``*``, a repo's ignored ``scratch/``) never had a change
    listed, and every round said "Nothing to commit: no file in the project's
    paths changed" over files that had. Asked only when nothing is staged.
    *Rejected:* asking about every record directory too (a person's own rule
    over a big scan in ``inputs/`` would then turn every quiet round into a
    message)."""
    return bool(vcs.ignored(root, [f"{store.NOPEKIT_DIR}/{store.PROJECT_NAME}"]))


def _round_commit(root: str, ledger: Ledger, done: rounds.Written,
                  paragraph: str) -> CommitOutcome:
    """Commit the round (SPEC 24 with §0.3): every changed file inside the
    project's directory that git does not ignore, less what `rounds.stage_plan`
    names instead (a directory, a file over the cap, a name that looks like a
    secret), only when `people` lists both its author and its committer, never
    pushed. Every refusal before the staging leaves the index as it was; a
    commit git refuses leaves the round's files staged and says so. A round
    whose claims fail commits all the same: the commit is history, not a
    quality gate."""
    if done.refused == "newer":
        return _no_commit("newer-record")
    if not vcs.is_repo(root):
        return _no_commit("no-repository")
    doing = vcs.operation(root)
    if doing is None:
        return _no_commit("no-repository")
    if doing == "detached":
        return _no_commit("detached")
    if doing:
        return _no_commit("in-progress", detail=doing)
    who = vcs.ident(root)
    committer = vcs.committer_ident(root)
    if who is None or committer is None:
        return _no_commit("no-identity")
    people = list(getattr(ledger.meta, "people", None) or [])
    if not people:
        return _no_commit("no-people", who=who)
    if rounds.listed(people, who) is None:
        return _no_commit("not-listed", who=who)
    # The committer too (review of phase 6): `committer.*` config or
    # `GIT_COMMITTER_*` put an identity `people` never named on a round's
    # commit, and the paragraph named only the author.
    if rounds.listed(people, committer) is None:
        return _no_commit("committer-not-listed", who=committer)
    changed = vcs.changed_paths(root)
    if changed is None:
        return _no_commit("unlisted", who=who)
    plan = rounds.stage_plan(root, changed)
    if not plan.staged:
        if _round_ignored(root):
            return _no_commit("ignored", who=who, plan=plan)
        return _no_commit("nothing-changed", who=who, plan=plan)
    added = vcs.add(root, plan.staged)
    if not added.ok:
        return _no_commit("add-failed", who=who, detail=added.why, staged=plan.staged,
                          plan=plan)
    # A file whose mtime moved and whose bytes did not is staged as nothing and
    # drops out here, so "nothing changed" stays true of a round that moved
    # nothing.
    staged = tuple(sorted(set(vcs.staged_paths(root) or ()) & set(plan.staged)))
    if not staged:
        return _no_commit("nothing-changed", who=who, plan=plan)
    number = done.round
    earlier = None
    if number is not None:
        last = rounds.last_committed_round(vcs.show(root, rounds.ROUND_REL)) or 0
        if number - last >= 2:
            earlier = (last + 1, number - 1)
    subject, body = report.round_commit_message(
        number, _round_shape(done), done.changes, staged, plan.not_files, plan.too_large,
        plan.secret, earlier, paragraph, round_rel=rounds.ROUND_REL)
    made = vcs.commit(root, staged, subject, body)
    if not made.ok:
        why = "timeout" if made.why == "timeout" else "refused"
        detail = str(vcs.VCS_WRITE_TIMEOUT_S) if why == "timeout" else made.why
        return CommitOutcome(False, None, who, why, detail, staged, plan.not_files,
                             plan.too_large, plan.secret, earlier, subject)
    return CommitOutcome(True, made.oid, who, "committed", "", staged, plan.not_files,
                         plan.too_large, plan.secret, earlier, subject)


def _round_run(root: str, *, commit: bool, wait_s: float, now: str) -> RoundOutcome:
    """The round's work (SPEC decisions 18 and 24): a tier-0 check exactly as
    `check --tier 0` runs it (verdicts recorded, the one-time migration
    included), the site rebuilt when `site/` exists, and `.nopekit/round.json`
    written when anything it records moved — then, with ``commit``, the commit
    of the project's files (`_round_commit`), still under the build lock so
    no writer lands between the staging and the commit; then the trace's seam
    (`_round_start_trace`, phase 3b), outside the lock.

    **It never blocks.** Every `NopekitError` becomes ``not_run`` or a reason in
    the paragraph — a held lock, a record that does not read, a model that does
    not load (the check is refused, no site is built — `site build` refuses
    that model too — and the record is still written: a round that cannot build
    the model says so). A pack that does not import writes no record: one
    built over an import error would list every claim it covers as a gap, the
    artefact `site build` refuses to publish.

    The record is computed from `_resolved` with no sweep overlay — what
    `status` shows — after the check and the site, and the site and the record
    share that one resolution."""
    check = {"ran": False, "why": "", "executed": 0, "cached": 0}
    site_info = {"present": site.scaffolded(root),
                 "built": False, "warnings": 0, "why": ""}
    try:
        with _round_locked(root, wait_s):
            ledger = _migrate(root, apply=True, now=now)
            try:
                registry, _ = _registry(root, ledger, strict=True)
            except NopekitError as exc:
                return RoundOutcome(_first_line(exc))
            try:
                ran = _check_run(root, ledger, tier=0, only=None, force=False, record=True,
                                 now=now, on_row=None, quiet=True, registry=registry)
            except NopekitError as exc:
                check["why"] = _first_line(exc)
            else:
                check.update(ran=True,
                             executed=int(ran.result.counts.get("executed", 0)),
                             cached=int(ran.result.counts.get("cached", 0)))
            ledger = _load(root)
            model, projection, model_error = _projection_safe(root, ledger)
            view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                         now=now, model=model)
            # A model that does not load builds no site: `site build` refuses it
            # (`_projection` raises), and a page built here would publish what
            # it refuses. What slipped through (review of phase 3a): `round`
            # built it anyway, "Site built with 1 warning", from the broken
            # model's resolution, its parameter views silent on the failure.
            if site_info["present"] and model_error:
                site_info["why"] = (model_error.strip().splitlines() or [""])[0]
            # One graph for the map and the round record (architecture phase 5),
            # over the one resolution they share.
            shown = _shown_params(root, ledger, model, model_error, resolution, registry,
                                  projection=projection)
            traced = _trace_for(root, model, projection, resolution)
            built = graph.build(view, resolution, registry, projection=projection,
                                design=verdicts.design_of(
                                    root, projection, model,
                                    anchors=resolution.anchors or verdicts.Anchors(
                                        root=os.path.abspath(root)),
                                    resolution=resolution),
                                views=shown, bases=_input_bases(root, ledger),
                                trace=traced.trace)
            if site_info["present"] and not model_error:
                try:
                    summary = _site_build(root, ledger, registry, model, projection, view,
                                          resolution, now=now, built=built, traced=traced)
                except NopekitError as exc:
                    site_info["why"] = _first_line(exc)
                else:
                    site_info.update(built=True, warnings=len(summary.get("warnings") or ()))
            content, _computed = _round_inputs(root, ledger, registry, model, projection,
                                               view, resolution, model_error=model_error,
                                               shown=shown, built=built, traced=traced)
            done = rounds.write(root, content)
            composed = claims.compositions(view, registry=registry,
                                           stale_gates=resolution.stale_gates,
                                           unseen_gates=claims.unseen_gates(resolution))
            text = report.round_paragraph(
                _round_shape(done), done.round, done.changes, view=view, composed=composed,
                check=check, site=site_info, wrote=done.written,
                schema=done.previous.schema, path=rounds.ROUND_REL)
            prose_rows: tuple = ()
            committed = None
            if commit:
                # Board text the results have moved past (architecture phase 5's
                # stamp), named so the person sees which boards to rewrite.
                if os.path.isdir(page.content_dir(root)):
                    prose_rows = tuple(page.stamp_states(
                        root, view, graph.tokens(view, resolution, registry)))
                behind = report.round_prose_sentence(prose_rows)
                committed = _round_commit(root, ledger, done, text + behind)
                text += report.round_commit_sentence(committed) + behind
    except NopekitError as exc:
        return RoundOutcome(_first_line(exc))
    # Architecture phase 3b, in phase 6's seam: after the lock and the commit,
    # so the background trace can take the lock for its snapshot.
    started = _round_start_trace(root, done, traced=traced, view=view, resolution=resolution,
                                 registry=registry, built=built, composed=composed,
                                 model_error=model_error)
    trace = report.round_trace_sentence(started).strip()
    if trace:
        text += " " + trace
    return RoundOutcome("", done, content, check, site_info, view, composed, prose_rows,
                        committed, trace, text,
                        {"state": started["state"] or traced.state, "key": started["key"],
                         "log": started["log"]})


def cmd_round(args: argparse.Namespace) -> int:
    """End a round (SPEC decisions 18 and 24): `_round_run`, then ONE short
    paragraph — what changed since the last round record, the count line,
    readiness as last evaluated, what the check and the site did, with
    `--commit` what the commit did, and (phase 3b) the background trace when one
    was started or is running. `--hook` is Claude Code's Stop hook
    (`_round_hook`).

    **It never blocks.** It is the Stop hook's body, and Claude Code reads a
    hook's exit 2 as "block the turn": every `NopekitError` is said in one line,
    exit 0 — failing claims and outside a project included. Only a traceback (a
    crash) exits 1, and under `--hook` not even that. What `round` writes: what
    `check` writes, what `site build` writes when `site/` exists, the round
    record at most once and only when its bytes change, the index (`main`'s
    `_after`), and with `--commit` one local commit of every file in the
    project's directory that git does not ignore, less what it names instead —
    never a record, never a push. The trace it starts writes only what
    `nopekit model trace` writes, in its own process."""
    if getattr(args, "hook", False):
        return _round_hook(args)
    said = report.HUMAN["round"]
    root = store.find_root(getattr(args, "dir", None))
    if root is None:
        text = said["no_project"]
        if args.json:
            _dump({"round": None, "written": False, "path": None, "summary": text})
        else:
            _say(text)
        return 0
    outcome = _round_run(root, commit=bool(getattr(args, "commit", False)), wait_s=0.0,
                         now=utcnow_iso())
    if outcome.not_run:
        return _round_not_run(args, outcome.not_run)
    done, content = outcome.done, outcome.content
    if args.json:
        changes = done.changes
        found = {
            "round": done.round, "written": done.written,
            "path": rounds.ROUND_REL if done.round is not None else None,
            "previous": {"state": done.previous.state, "round": done.previous.round},
            "changes": {
                "compared": changes.compared, "replaced": changes.replaced,
                "claims": [{"id": f.id, "from": f.before, "to": f.after}
                           for f in changes.claims],
                "requirements": [{"id": f.id, "from": f.before, "to": f.after}
                                 for f in changes.requirements],
                "verdicts": list(changes.verdicts), "instructions": list(changes.instructions),
                "instructions_unrecorded": bool(changes.unrecorded),
                "readiness": ({"from": changes.readiness[0], "to": changes.readiness[1]}
                              if changes.readiness else None),
                "moved": list(changes.moved)},
            "counts": content["counts"], "readiness": content["readiness"],
            "check": outcome.check, "site": outcome.site, "summary": outcome.summary,
            "trace": outcome.trace_state,
        }
        if outcome.commit is not None:
            found["commit"] = _commit_json(outcome.commit)
        _dump(found)
    else:
        _say(outcome.summary)
    return 0


def _commit_json(commit: CommitOutcome) -> dict:
    return {"made": commit.made, "oid": commit.oid, "subject": commit.subject,
            "who": commit.who, "why": commit.why, "detail": commit.detail,
            "staged": list(commit.staged), "not_files": list(commit.not_files),
            "too_large": [{"path": p, "bytes": n} for p, n in commit.too_large],
            "secret": list(commit.secret),
            "earlier": list(commit.earlier) if commit.earlier else None}


#: The most the Stop hook reads of its stdin: the payload is a few hundred
#: bytes (cwd, session id, transcript path); a megabyte is a payload gone wrong.
_HOOK_STDIN_MAX = 1 << 20


def _hook_payload() -> dict:
    """The Stop hook's JSON from stdin, or ``{}`` — empty, not JSON, not an
    object, or a terminal (no hook typed into a terminal)."""
    stream = sys.stdin
    try:
        if stream is None or stream.isatty():
            return {}
    except (AttributeError, ValueError, OSError):
        return {}
    try:
        buffer = getattr(stream, "buffer", None)
        raw = buffer.read(_HOOK_STDIN_MAX) if buffer is not None \
            else stream.read(_HOOK_STDIN_MAX)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        data = json.loads(raw) if raw and raw.strip() else {}
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _hook_root(args: argparse.Namespace, payload: Mapping[str, Any]) -> str | None:
    """The project the hook ends a round in: the first of `-C`, the payload's
    ``cwd`` (Claude Code's and Codex's), its ``workspace_roots`` (Cursor's),
    ``$CLAUDE_PROJECT_DIR``, ``$CURSOR_PROJECT_DIR`` and the process's cwd that
    is inside one (walking up, as every command does). Cursor's payload names no
    ``cwd``, and a plugin hook's cwd there is not reliably the project's — read
    alone, the process's cwd would end the round in the wrong directory or none."""
    roots = payload.get("workspace_roots")
    candidates = [getattr(args, "dir", None), payload.get("cwd"),
                  *(roots if isinstance(roots, list) else []),
                  os.environ.get("CLAUDE_PROJECT_DIR"), os.environ.get("CURSOR_PROJECT_DIR")]
    try:
        candidates.append(os.getcwd())
    except OSError:
        pass
    for start in candidates:
        if not isinstance(start, str) or not start.strip() or not os.path.isdir(start):
            continue
        try:
            found = store.find_root(start)
        except (NopekitError, OSError, ValueError):
            continue
        if found is not None:
            return found
    return None


@contextlib.contextmanager
def _quiet_fd(fd: int):
    """Point file descriptor ``fd`` at the null device for the block, then put
    it back: a gate's solver or a commit hook writes to the inherited stdout
    directly, below any `redirect_stdout`, and one stray line there breaks the
    Stop hook's JSON."""
    saved = None
    try:
        saved = os.dup(fd)
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null, fd)
        finally:
            os.close(null)
    except OSError:
        saved = None
    try:
        yield
    finally:
        if saved is not None:
            try:
                os.dup2(saved, fd)
            finally:
                os.close(saved)


def _hook_quiet(outcome: RoundOutcome) -> bool:
    """A round the person need not hear about: the record stood, nothing was
    committed because nothing changed, no board's text is behind, and no trace
    started. A standing misconfiguration (not listed, no people, no identity,
    no repository) is never quiet: it is the one thing the person must know."""
    commit = outcome.commit
    return (not outcome.not_run and outcome.done is not None and not outcome.done.written
            and not outcome.done.refused
            and commit is not None and commit.why == "nothing-changed"
            and not report.round_prose_sentence(outcome.prose) and not outcome.trace)


def _round_hook(args: argparse.Namespace) -> int:
    """The plugin's Stop hook (architecture phase 6, SPEC 24) — Claude Code's
    and Codex's, which share its format, and Cursor's through ``hooks/stop.py
    --cursor``: read the hook's JSON on stdin, find the project (`_hook_root`),
    run `round --commit` with nothing reaching stdout, rebuild the index, and
    print ONE JSON object — ``{"systemMessage": "<the paragraph>"}``, or ``{}``
    outside a project or when nothing happened. Plain stdout from a Stop hook is
    shown to nobody, and a stray line would break the parse — and Codex refuses
    any key outside Claude Code's own set, so the object never grows a host's
    private key.

    Always exit 0, and never ``"decision": "block"``: Claude Code and Codex read
    exit 2 or a block as "continue the turn", and a round that kept the agent
    working would loop. ``stop_hook_active`` changes nothing for the same reason: this
    hook never blocks, so it can never be the reason a turn continues. A crash
    becomes one line of ``systemMessage``."""
    args._index_done = True
    try:
        payload = _hook_payload()
        root = _hook_root(args, payload)
        if root is None:
            message: dict = {}
        else:
            args.dir = root
            with _quiet_fd(1), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                outcome = _round_run(root, commit=True, wait_s=ROUND_LOCK_WAIT_S,
                                     now=utcnow_iso())
                _touch_index(root, quiet=True)
            message = {} if _hook_quiet(outcome) else \
                {"systemMessage": report.round_hook_message(outcome)}
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001 — the hook never fails the turn
        message = {"systemMessage": report.round_hook_crash(exc)}
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


def _round_shape(done: rounds.Written) -> str:
    """Which sentence opens `round`'s paragraph."""
    if done.refused == "newer":
        return "newer"
    if not done.written:
        return "unchanged"
    if done.previous.state == "absent":
        return "first"
    if done.previous.state in ("edited", "unreadable"):
        return "replaced"
    return "written"


def _round_not_run(args: argparse.Namespace, why: str) -> int:
    """`round not run: <why>.`, exit 0 — a round never blocks."""
    text = report.HUMAN["round"]["not_run"].format(why=why.rstrip(".") or "it did not run")
    if args.json:
        _dump({"round": None, "written": False, "path": None, "summary": text})
    else:
        _say(text)
    return 0


def _blocking_line(claim: Claim, status: ClaimStatus, reason: str, *,
                   errored: bool = False) -> str:
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
    `errored` is the claim's crash mark: its tag is `[SKIP ]`, Failing's tone
    (invariant 2, P2.1).
    """
    return (f"{report.status_tag(status, errored=errored)} {claim.id} "
            f"{report._one(claim.statement)} — {reason}")


def _unseen_in_run(rows: Iterable[verdicts.SweepRow], view: Ledger,
                   composed: Mapping[str, Any]) -> list[str]:
    """The gates of this sweep, in its row order, that settled a required claim
    on a current verdict whose reads went through a channel nopekit cannot see
    — the ones every reader after this run will read Stale (issue #15, D1;
    `claims.unseen_gates` is the same fact read back from the cache). From the
    row's own reads: the run's `Reads` or the served entry's block."""
    cited = {g for cid in claims.required_ids(view) if cid in composed
             for g in composed[cid].cites}
    out = []
    for row in rows:
        reads = row.reads
        channels = (reads.get("opaque") if isinstance(reads, Mapping)
                    else getattr(reads, "opaque", None))
        if row.fresh and channels and row.verdict.gate in cited:
            out.append(row.verdict.gate)
    return out


def _blocking_reason(ledger: Ledger, claim: Claim, status: ClaimStatus, *,
                     stale: Mapping[str, str] | None = None,
                     unseen: Iterable[str] = ()) -> str:
    """The shortest true sentence about why one claim blocks: `report.reason`,
    in full, for the claim composed over `ledger` — the one producer every
    channel's reason comes from (P2.1-D15; `ReasonsAgree` holds this and
    `report._terminal_reason` equal).

    A failing gate's own detail beats any phrasing invented here: it carries the
    measured value and the limit, which is what the reader is about to go and
    change. THE REASON MUST MATCH THE STATUS: several gates can cover one claim,
    and the first non-passing one is not necessarily the one that set it — a
    claim covered by a gate that FAILED and a pack gate that SKIPPED for a
    missing parameter cites the measured fail (`claims.explaining_verdict`'s
    ranking, which `compose` shares). That ranking used to live here, privately,
    and the fix never reached `status` (S-68); and the fallback words were the
    STATUS's, so a crash moved to Skipped alone would have read "its gates could
    not run here (missing tooling)" (P2.0 F-8). `status` must be what `compose`
    says for the claim; the words are `reason`'s.

    `stale` is `{gate: why}` for the gates whose verdict is not current (the
    resolution's). A FAIL whose inputs moved stays FAIL (D-08) and says so —
    `gate : body (invalidated: why)` — and a Stale claim names its moved gate
    with what moved. `unseen` is `claims.unseen_gates` of that resolution.
    """
    stale = dict(stale or {})
    found = claims.compose(claim, ledger.verdicts, stale_gates=set(stale),
                           unseen_gates=set(unseen or ()))
    return report.reason(found, ledger, claim, cut=False, stale_reasons=stale)


def _status_views(view: Ledger, composed: Mapping[str, Any],
                  stale_reasons: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """`{claim id: {key, word, cause, reason, errored}}` — the words beside the
    kept enum map (P2.1-D12), one `report.status_view` per claim."""
    by_id = {claim.id: claim for claim in view.claims}
    return {cid: report.status_view(found, view, by_id[cid], stale_reasons=stale_reasons)
            for cid, found in composed.items()}


def _stale_reasons(resolution: verdicts.Resolution) -> dict[str, str]:
    """`{gate: why}` for every gate the resolution calls not current — the words
    `report.reason` puts after `invalidated:` (`config.bed_xy 220.0 -> 250.0`)."""
    return {gid: (resolution.rows[gid].stale_reason if gid in resolution.rows else "")
            for gid in resolution.stale_gates}


# --------------------------------------------------------------------------- #
# export: the boundary that spends (P2.5b)
# --------------------------------------------------------------------------- #
def _load_generator(root: str, ref: str) -> Callable[..., Any]:
    """The function a milestone's ``generator`` names, loaded as a project gate
    module is (``modelio.load_source_module``, the project root on ``sys.path``
    for the load): the bytes on disk are the bytes that run, and its code
    closure is recorded — the article's code half (P2.5b-D12)."""
    path, name = store.generator_parts(ref)
    full = os.path.join(root, *path.split("/"))
    if not os.path.isfile(full):
        raise NopekitError(f"{path} does not exist")
    salt = short_hash(os.path.abspath(full))
    stem = re.sub(r"\W", "_", os.path.splitext(os.path.basename(full))[0]) or "generator"
    sys.path.insert(0, root)
    try:
        module = modelio.load_source_module(full, name=f"nopekit_generator_{stem}_{salt}",
                                            roots=[root])
    except Exception as exc:                              # noqa: BLE001 - project code
        first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
        raise NopekitError(f"{path}: {first}") from None
    finally:
        try:
            sys.path.remove(root)
        except ValueError:
            pass
    fn = getattr(module, name, None)
    if not callable(fn):
        raise NopekitError(f"{path} has no function {name}")
    return fn


def _export_list(root: str, ledger: Ledger, *, as_json: bool) -> int:
    """`nopekit export`: every milestone's line, as last evaluated — the cache's
    view, nothing re-run (critique 17 of the P2.5b design: the head says so, and
    `export <m> --dry-run` is the boundary's)."""
    registry, _problems = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=utcnow_iso(), model=model)
    composed = claims.compositions(view, registry=registry,
                                   stale_gates=resolution.stale_gates,
                                   unseen_gates=claims.unseen_gates(resolution))
    said = report.HUMAN["milestone"]
    declared = list(view.milestones)
    if as_json:
        summary = claims.summarise(view, registry, stale_gates=resolution.stale_gates)
        _dump({"milestones": summary["milestones"]})
        return 0
    if not declared:
        _say(said["none"])
        return 0
    _say(said["list_head"].format(n=len(declared),
                                  milestones=_plural_word(len(declared), "milestone")))
    for milestone in declared:
        line = report.milestone_line(view, composed, milestone)
        mine = [e for e in view.exports if e.milestone == milestone.id]
        if mine:
            last = mine[-1]
            line += said["last_export"].format(
                article=report.article12(last.article), who=report._one(last.who),
                when=str(last.when)[:10])
        _say(f"  {line}")
    return 0


def _plural_word(n: int, one: str) -> str:
    return one if n == 1 else one + "s"


def _proceed_refusal(name: str, marker: str, isatty: bool) -> str:
    said = report.HUMAN["export"]
    why = (said["proceed_agent"].format(marker=marker) if marker
           else said["proceed_pipe"] if not isatty else "")
    return said["proceed_shell"].format(m=name, why=why)


def cmd_export(args: argparse.Namespace) -> int:
    """`nopekit export [<milestone>] [--dry-run] [--proceed --why TEXT]` — the
    boundary that spends (P2.5b; METHOD rule 4; R-9). One code path for both
    modes (D-15: `--dry-run` is `/ready`), in order:

    0. Refused before anything runs: `--proceed` with `--dry-run`; `--proceed`
       from an agent session or a pipe, or with no `--why` (exit 2). Written:
       no git identity, a legacy project (exit 2); under `--dry-run` each is a
       refusal it says it would make (critique 21 of the design).
    1. Load; the milestone (exit 2 naming the declared ones when absent).
    2. Under the build lock, the re-run: `check --force --only <closure>`'s
       sweep at the top tier (`verdicts.sweep(force=True, max_tier=3)`), every
       evaluator a required claim rests on with its prerequisites and both
       controls; filed as `check --force` files it, except under `--dry-run`.
    3. Judged on the RE-EXECUTED view (`_resolved(sweep=…)`, the view `check`
       builds): *ready* (`claims.unresolved`) and the disagreements (D7).
    4. Only with no refusal — or only unresolved ones and `--proceed`: the
       generator, traced (`verdicts.export_article`), the package built aside,
       and the inputs held: an input the judgment stood on that moved while the
       package was built refuses it (critique 4).
    5. `--dry-run`: print, remove the scratch, exit. Written: the person's
       decision typed (`--proceed`), the package swapped in, the export record
       appended (`store.append_sealed`), sealed and chained.

    Exit 0 written or would write (or the list); 1 refused or would refuse; 2
    a usage or record error. What slipped through without it: one implicit
    spend, so the page said ready whenever nothing stopped `check` (S-60), and
    a forged cache entry was served Checked at the one place it costs money."""
    root = _root(args)
    said = report.HUMAN["export"]
    name = getattr(args, "milestone", None)
    if not name:
        return _export_list(root, _load(root), as_json=args.json)
    dry = bool(args.dry_run)
    proceed = bool(args.proceed)
    environ = dict(os.environ)
    isatty = bool(sys.stdin and sys.stdin.isatty())
    marker = _agent_marker(environ)
    if proceed and dry:
        raise NopekitError(said["proceed_dry"])
    if proceed and (marker or not isatty):
        raise NopekitError(_proceed_refusal(name, marker, isatty))
    if proceed and not str(args.why or "").strip():
        raise NopekitError(said["proceed_why"])
    now = utcnow_iso()
    preconditions: list[milestones.Refusal] = []
    legacy = store.is_legacy(root)
    who = vcs.ident(root)
    if legacy:
        if not dry:
            raise NopekitError(said["legacy"])
        preconditions.append(milestones.Refusal("precondition", "legacy", said["legacy"]))
    if not who:
        if not dry:
            raise NopekitError(said["identity"])
        preconditions.append(milestones.Refusal("precondition", "identity", said["identity"]))
    ledger = _load(root)
    milestone = ledger.milestone(name)
    if milestone is None:
        declared = [m.id for m in ledger.milestones]
        raise NopekitError(said["no_milestone"].format(
            name=name, declared=said["declared"].format(names=", ".join(declared))
            if declared else said["none_declared"]))
    if not dry:
        # The marked ignore lines first (D17): `out/` is ignored before a byte
        # of a package lands in it, on a project migrated before P2.5b too.
        store.ensure_ignore_blocks(root)

    # The scratch is created, and removed, only under the lock: a run refused
    # by the lock never touches it. What slipped through (review of P2.5b,
    # finding 8): the `finally` that removed it wrapped the lock, so a dry run
    # the lock refused deleted the running one's package half way, and that one
    # refused with a false "generator errored".
    with _lock(root):
        scratch = milestones.scratch_dir(root, name, dry)
        try:
            return _export_locked(args, root, ledger, milestone, now=now, dry=dry,
                                  proceed=proceed, who=who, isatty=isatty, marker=marker,
                                  environ=environ, scratch=scratch,
                                  preconditions=preconditions)
        finally:
            if os.path.exists(scratch):
                shutil.rmtree(scratch, ignore_errors=True)


def _export_locked(args: argparse.Namespace, root: str, ledger: Ledger, milestone: Any, *,
                   now: str, dry: bool, proceed: bool, who: str, isatty: bool, marker: str,
                   environ: Mapping[str, str], scratch: str,
                   preconditions: list) -> int:
    said = report.HUMAN["export"]
    name = milestone.id
    records_before = store.records_digest(root)
    registry, _problems = _registry(root, ledger, strict=True)
    model, projection, model_error = _projection_safe(root, ledger)
    before = verdicts.resolve(root, registry, projection, ledger, model_error=model_error,
                              now=now, model=model)
    closure = milestones.closure(ledger, registry, milestone)
    result = None
    if closure and projection is not None:
        ctx = _context(root, ledger, model, projection, int(Tier.EXTERNAL), quiet=True)
        result = verdicts.sweep(root, registry, ctx, projection=projection, ledger=ledger,
                                max_tier=int(Tier.EXTERNAL), only=closure, force=True,
                                record=not dry, now=now)
    view, resolution = _resolved(root, ledger, registry, projection, model_error, now=now,
                                 model=model, sweep=result)
    composed = claims.compositions(view, registry=registry,
                                   stale_gates=resolution.stale_gates,
                                   unseen_gates=claims.unseen_gates(resolution))
    found = milestones.disagreements(before, result, set(closure)) if result is not None \
        else []
    extra = list(preconditions)
    if closure and projection is None:
        # Nothing could re-run: say why, as a refusal no decision covers
        # (review of P2.5b, finding 11: the boundary said "no evaluator settles
        # a claim" and refused on the cache's statuses, which `--proceed`
        # covers — a spend over evaluators that never ran).
        extra.append(milestones.Refusal("model", "model", said["model_broken"].format(
            n=len(closure), m=name,
            why=report._trunc(report._one(model_error or "no model entry"), 160))))
    judged = milestones.judge(view, composed, milestone, disagreements=found, extra=extra,
                              decided=proceed)
    unresolved_ = [r for r in judged.refusals if r.kind == "unresolved"]

    # 4. the generator and the package — only where the export may write.
    article: dict = {}
    package = None
    carried: dict = {}
    if judged.writes:
        built = _export_build(root, ledger, milestone, view, registry, composed, projection,
                              model, model_error, resolution, scratch, now=now)
        if isinstance(built, milestones.Refusal):
            extra.append(built)
        else:
            article, package, carried = built
            moved = _inputs_moved(root, records_before, registry, projection, ledger, model,
                                  result, article)
            if moved:
                extra.append(milestones.Refusal("package", "inputs", said["package_moved"].format(
                    what=moved, m=name)))
        if not isinstance(built, milestones.Refusal):
            for kind, rel_path in milestones.package_problems(root, name, ledger.exports):
                key = "package_foreign" if kind == "foreign" else "package_edited"
                newest = [e for e in ledger.exports if e.milestone == name]
                extra.append(milestones.Refusal("package", rel_path, said[key].format(
                    m=name, file=rel_path,
                    package=report.article12((newest[-1].package or {}).get("hash", ""))
                    if newest else "")))
        judged = milestones.judge(view, composed, milestone, disagreements=found,
                                  extra=extra, decided=proceed)
        if not judged.writes:
            article, package = {}, None

    lines = _export_lines(view, composed, milestone, result, found, judged,
                          unrunnable=len(closure) if closure and projection is None else 0)
    decision = None
    decided_line = ""
    if judged.writes and proceed:
        if unresolved_:
            groups = report._groups(view, composed, [view.claim(r.subject)
                                                     for r in unresolved_])
            if dry:
                pass
            else:
                answer = _ask(said["proceed_prompt"].format(groups=groups, m=name), lines)
                if answer is None or answer.strip() != name:
                    typed = "nothing" if not (answer or "").strip() else repr(answer.strip())
                    raise NopekitError(said["typed"].format(typed=typed, m=name))
                decision = {"claims": [{"id": r.subject,
                                        "status": str(composed[r.subject].status),
                                        "cause": str(composed[r.subject].cause.value)}
                                       for r in unresolved_],
                            "why": str(args.why)}
                decided_line = said["decided"].format(who=report._one(who), groups=groups,
                                                      why=report._one(args.why))
        else:
            decided_line = said["proceed_nothing"]
    written = False
    if judged.writes and package is not None and not dry:
        channel = (_channel(isatty, environ, name if decision is not None else None, name)
                   if (marker or not isatty or decision is not None) else "interactive")
        entry = _export_entry(view, composed, milestone, result, registry, resolution,
                              article, package, decision, now=now, who=who, channel=channel)
        # The record is appended inside the swap, which takes the new package
        # back out if the append raises (review of P2.5b, finding 9).
        milestones.swap_package(root, name, scratch, record=lambda: store.append_sealed(
            root, "exports", name, "exports", entry))
        written = True

    reasons = "; ".join(r.reason for r in judged.refusals
                        if r.kind not in milestones.COVERED or not (proceed and judged.writes))
    if judged.writes and package is not None:
        args_out = dict(path=f"{store.PACKAGES_NAME}/{name}", article=report.article12(article),
                        package=report.article12(package.hash), n=len(package.files),
                        files=_plural_word(len(package.files), "file"), m=name)
        outcome = said["written" if written else "would_write"].format(**args_out)
    else:
        outcome = said["would_refuse" if dry else "refused"].format(reasons=reasons)
    card = (milestones.test_card(view, composed, milestone, article.get("hash", ""),
                                 view.exports) if article else [])
    if args.json:
        _dump(_export_json(view, composed, milestone, judged, result, found, article, package,
                           decision, dry=dry, written=written, card=card,
                           exports=view.exports))
    else:
        for line in lines:
            _say(line)
        if decided_line:
            _say(decided_line)
        _say(outcome)
        for line in card:
            _say(line)
    return 0 if judged.writes and package is not None else 1


def _ask(prompt: str, rows: Iterable[str]) -> str | None:
    """The go-ahead's prompt, on stderr — after the lines the person reads —
    and the line they typed, or ``None`` at end of input."""
    for row in rows:
        print(row, file=sys.stderr)
    sys.stderr.write(prompt)
    sys.stderr.flush()
    try:
        line = sys.stdin.readline()
    except OSError:
        return None
    return line.rstrip("\r\n") if line else None


def _export_build(root: str, ledger: Ledger, milestone: Any, view: Ledger, registry: Any,
                  composed: Mapping[str, Any], projection: Any, model: Any, model_error: str,
                  resolution: Any, scratch: str, *, now: str) -> Any:
    """Run the generator traced into ``scratch`` and build the package there:
    ``(article, package, carried)``, or the ``generator`` refusal."""
    said = report.HUMAN["export"]
    name = milestone.id
    if os.path.exists(scratch):
        shutil.rmtree(scratch)
    os.makedirs(scratch)
    if projection is None:
        return milestones.Refusal("generator", milestone.generator or name,
                                  said["generator_model"].format(
                                      why=report._trunc(model_error or "no model entry", 120)))
    anchors = verdicts.anchors_for(root, registry, out_dir=store.out_dir(root))
    digests = FileDigests()
    if milestone.generator:
        try:
            fn = _load_generator(root, milestone.generator)
        except NopekitError as exc:
            return milestones.Refusal("generator", milestone.generator,
                                      said["generator_load"].format(why=str(exc)))
    else:
        fn = (lambda ctx: None)
    built = verdicts.export_article(root, projection, fn, out_dir=scratch, anchors=anchors,
                                    digests=digests, model=model, milestone=name, when=now,
                                    resolution=resolution)
    if built.outside:
        return milestones.Refusal("generator", built.outside[0], said["generator_outside"]
                                  .format(path=", ".join(built.outside)))
    if built.error:
        return milestones.Refusal("generator", milestone.generator, said["generator_errored"]
                                  .format(first=report._trunc(built.error, 160)))
    if milestone.generator and not built.written:
        return milestones.Refusal("generator", milestone.generator, said["generator_none"])
    if built.linked:
        # Review of P2.5b (finding 2): a link into the project is no package —
        # a symlink hands the builder whatever the project holds when it is
        # followed, a hard link the bytes the project's next in-place edit makes.
        return milestones.Refusal("generator", built.linked[0], said["generator_linked"]
                                  .format(path=", ".join(built.linked)))
    clashing = [rel_path for rel_path in built.written if rel_path in milestones.SPINE_FILES]
    if clashing:
        return milestones.Refusal("generator", clashing[0], said["generator_outside"].format(
            path=clashing[0]))
    # Rendered as `report --milestone` renders it (V-7): the same parameter
    # view and model error, over the boundary's re-executed view — and as the
    # boundary's: its sentence plain, each result recorded on this article, the
    # test card under the head (review of P2.5b, findings 3 and 16).
    params = _shown_params(root, ledger, model, model_error, resolution, registry,
                           projection=projection)
    card = milestones.test_card(view, composed, milestone, built.article["hash"],
                                view.exports)
    report_md = report.render_markdown(view, registry, stale_gates=resolution.stale_gates,
                                       model_error=model_error, root=root, params=params,
                                       milestone=milestone,
                                       stale_reasons=_stale_reasons(resolution),
                                       boundary=True, article=built.article["hash"],
                                       test_card=card,
                                       unseen_gates=claims.unseen_gates(resolution))
    required = claims.required_ids(view, milestone)
    manifest = {"milestone": name, "revision": built.article.get("revision", ""),
                "records_digest": store.records_digest(root, exclude=("exports",)),
                "spine": verdicts.spine_digest(), "article": built.article["hash"],
                "traced": built.article["traced"],
                "claims": {cid: str(composed[cid].status) for cid in required
                           if cid in composed}}
    package = milestones.build_package(scratch, report_md=report_md, carried=built.params,
                                       manifest=manifest)
    return built.article, package, built.params


def _inputs_moved(root: str, records_before: str, registry: Any, projection: Any,
                  ledger: Ledger, model: Any, result: Any, article: Mapping[str, Any]) -> str:
    """What moved between the judgment and the swap, in words, or ``""``
    (critique 4 of the P2.5b design): the records, the article's read set, or a
    file a re-run evaluator read. Nothing is run: the design is re-read and each
    read set digested now. What it catches: an editor saving a file a required
    evaluator read while a tier-3 re-run took minutes — the package would ship
    bytes no evaluator saw, under a record that said ready."""
    if store.records_digest(root) != records_before:
        return "the records"
    try:
        model_now, projection_now, _error = _projection_safe(root, ledger)
    except NopekitError as exc:
        return str(exc)
    anchors = verdicts.anchors_for(root, registry, out_dir=store.out_dir(root))
    here = verdicts._Now(root, projection_now, ledger, anchors=anchors, digests=FileDigests(),
                         model=model_now)
    state, moved = verdicts._article_moves(article, here)
    if state != "current":
        return verdicts._stale_text(moved) if moved else "the article's design"
    pairs = {spec.id: (spec, fn) for spec, fn in registry.pairs()} \
        if registry is not None else {}
    for row in getattr(result, "rows", None) or ():
        if not row.executed or not row.rho or row.reads is None:
            continue
        gid = row.verdict.gate
        reads = row.reads.to_dict() if hasattr(row.reads, "to_dict") else dict(row.reads)
        now_reads, _why = verdicts._reads_now(reads, here)
        if now_reads is None or gid not in pairs:
            continue
        spec, fn = pairs[gid]
        rho_now = verdicts.rho(gid, here.spine, verdicts.code_digest(spec, fn, anchors=anchors),
                               now_reads)
        if rho_now != row.rho:
            return f"what {gid} read"
    return ""


def _export_lines(view: Ledger, composed: Mapping[str, Any], milestone: Any, result: Any,
                  found: list, judged: Any, *, unrunnable: int = 0) -> list[str]:
    """`export <m>`'s lines up to its outcome (P2.5b §2.2), every word
    `report.HUMAN`'s. ``unrunnable``: the evaluators the milestone requires that
    could not run because the model does not load — said apart from "none
    settles a claim" (review of P2.5b, finding 11)."""
    said = report.HUMAN["export"]
    name = milestone.id
    lines = [said["head"].format(name=name, description=report._one(milestone.description))
             if milestone.description else name]
    lines.append(report.milestone_line(view, composed, milestone))
    for claim in report.in_severity(view, composed, judged.found.unresolved):
        status = composed[claim.id]
        lines.append(f"{report.status_tag(status.status, errored=status.errored)} {claim.id} "
                     f"{report._one(claim.statement)} — "
                     f"{report.reason(status, view, claim, stale_reasons=None)}")
    requires = set(claims.required_ids(view, milestone))
    others = [c for c in view.claims if c.id not in requires and c.id in composed
              and composed[c.id].status not in (ClaimStatus.PASS, ClaimStatus.VERIFIED)]
    if others:
        lines.append(said["not_required"].format(m=name,
                                                 groups=report._groups(view, composed, others)))
    rows = list(getattr(result, "rows", None) or ())
    if rows:
        how = (said["differ"].format(k=len(found), differs="differs" if len(found) == 1
                                     else "differ")
               if found else said["as_recorded"])
        lines.append(said["rerun"].format(n=len(rows),
                                          evaluators=_plural_word(len(rows), "evaluator"),
                                          tier=int(Tier.EXTERNAL), how=how))
    elif unrunnable:
        lines.append(said["rerun_model"].format(
            n=unrunnable, evaluators=_plural_word(unrunnable, "evaluator"), m=name))
    else:
        lines.append(said["rerun_none"].format(m=name))
    lines += [found_.line for found_ in found]
    lines.append(report._verdict_sentence(view, composed, None, stale=False, markdown=False,
                                          milestone=milestone, boundary=True))
    lines.append(report.limits_line(view))
    return lines


def _rerun_rows(result: Any, found: list) -> list[dict]:
    differing = {d.gate for d in found}
    out = []
    for row in getattr(result, "rows", None) or ():
        verdict = row.verdict
        admission = row.admission
        out.append({"gate": verdict.gate, "rho": row.rho, "outcome": verdict.outcome,
                    "out8": verdicts.out8(verdict) if verdict.outcome in ("pass", "fail")
                    else "",
                    "qualified": bool(admission is not None
                                      and admission.state in ("admitted", "pending")),
                    "agrees": verdict.gate not in differing, "executed": bool(row.executed),
                    "cached": bool(row.cached),
                    "control_executed": bool(admission is not None and admission.executed),
                    "tier": int(getattr(result, "max_tier", 0))})
    return out


def _export_entry(view: Ledger, composed: Mapping[str, Any], milestone: Any, result: Any,
                  registry: Any, resolution: Any, article: dict, package: Any,
                  decision: Any, *, now: str, who: str, channel: str) -> dict:
    """The export record's entry (P2.5b-D9, D26): what was required, what each
    claim read and whether it was re-run, what was re-run, what counted on the
    article's inputs, the article, the package and the decision."""
    reran = {row.verdict.gate for row in getattr(result, "rows", None) or () if row.executed}
    rows = []
    pairs = dict((spec.id, (spec, fn)) for spec, fn in registry.pairs()) \
        if registry is not None else {}
    for row in getattr(result, "rows", None) or ():
        verdict = row.verdict
        spec_fn = pairs.get(verdict.gate)
        code = (verdicts.code_digest(spec_fn[0], spec_fn[1], anchors=resolution.anchors).digest
                if spec_fn is not None else "")
        rows.append({"gate": verdict.gate, "rho": row.rho,
                     "out8": verdicts.out8(verdict) if verdict.outcome in ("pass", "fail")
                     else "", "code": code, "outcome": verdict.outcome,
                     "qualified": bool(row.admission is not None
                                       and row.admission.state in ("admitted", "pending"))})
    claim_rows = {}
    for claim in view.claims:
        found = composed.get(claim.id)
        if found is None:
            continue
        gates_ = list(claim.gates or ())
        claim_rows[claim.id] = {"status": str(found.status), "cause": str(found.cause.value),
                                "reran": bool(gates_) and all(g in reran for g in gates_)}
    return {"milestone": milestone.id, "when": now, "who": who, "channel": channel,
            "revision": article.get("revision", ""), "dirty": bool(article.get("dirty")),
            "requires": list(milestone.requires), "claims": claim_rows, "reran": rows,
            "counted": milestones.counted_on(view, composed, milestone, registry, resolution),
            "article": article,
            "package": {"hash": package.hash, "files": dict(package.files),
                        "manifest": package.manifest},
            "proceed": decision}


def _export_json(view: Ledger, composed: Mapping[str, Any], milestone: Any, judged: Any,
                 result: Any, found: list, article: dict, package: Any, decision: Any, *,
                 dry: bool, written: bool, card: list, exports: Iterable[Any]) -> dict:
    """`export <m> --json` (P2.5b §2.6): additive, keys not a channel."""
    requires = claims.required_ids(view, milestone)
    unresolved_ = judged.found.unresolved
    requires_set = set(requires)
    test = []
    for claim in view.claims:
        if any(line.startswith(f"  {claim.id} ") for line in card):
            test.append({"id": claim.id, "test": claim.note or claim.acceptance.render(),
                         "latency": report.latency_words(claim, exports)})
    return {
        "milestone": milestone.id, "description": milestone.description, "dry_run": dry,
        "ready": judged.ready, "required": requires,
        "checked": [c.id for c in judged.found.required if c not in unresolved_],
        "unresolved": [{"id": c.id, "status": str(composed[c.id].status),
                        "cause": str(composed[c.id].cause.value),
                        "reason": report.reason(composed[c.id], view, c, full=True)}
                       for c in unresolved_],
        "missing": list(judged.found.missing),
        "not_required": [{"id": c.id, "status": str(composed[c.id].status),
                          "cause": str(composed[c.id].cause.value)}
                         for c in view.claims if c.id not in requires_set and c.id in composed
                         and composed[c.id].status not in (ClaimStatus.PASS,
                                                           ClaimStatus.VERIFIED)],
        "reran": _rerun_rows(result, found),
        "refusals": [r.to_dict() for r in judged.refusals],
        "proceed": decision,
        "article": {"hash": article["hash"], "traced": article["traced"]} if article else None,
        "package": ({"hash": package.hash, "files": len(package.files)}
                    if package is not None else None),
        "written": written,
        "test_card": test,
    }


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
    ledger = _load(root)
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
             "`nopekit inputs --unextracted` for what arrived and was never read.")
        return 0

    _say("ask for these, in this order:")
    for index, (kind, prompt) in enumerate(pairs, start=1):
        bucket = artifacts.bucket_for(kind)
        where = f"  -> inputs/{bucket}/" if bucket else ""
        _say(f" {index}. {prompt}{where}")
    _say("")
    _say("then: nopekit ingest <file> --kind <kind> --desc '<what it shows>'")
    _say("and:  nopekit extract <artifact-id> --what '<what it says>' --grounds <param>")
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

    A shim, and a permanent one (spec §3.15): under the lock, with the clock read
    here, it migrates a legacy ledger first, then per path copies the bytes into
    `inputs/<bucket>/` (the payload, not a record) and writes exactly one record,
    `inputs/<id>.json`, pinning the sha256. Bytes already ingested come back as
    their existing record and rewrite nothing.

    `--proposer <id>` records which proposer produced the file, for one the
    agent ran by hand (SPEC 22): refused before the lock when no discoverable
    pack declares it, and set only on a NEW record. It is the agent's word —
    the basis it shows is display only (SPINE_CONTRACT's limits).
    """
    root = _root(args)
    now = utcnow_iso()
    landed: list[Any] = []
    proposer = getattr(args, "proposer", None) or ""
    found = packs.find_proposer(root, proposer) if proposer else None
    known: list[str] = []

    with _lock(root):
        ledger = _migrate(root, apply=True, now=now)
        for source in args.paths:
            before = {a.id: a.proposer for a in ledger.inputs}
            if source.startswith(("http://", "https://")):
                artifact = artifacts.ingest_link(
                    root, ledger, source, description=args.desc,
                    kind=args.kind or ArtifactKind.LINK, when=now, proposer=proposer)
            else:
                artifact = artifacts.ingest(
                    root, ledger, source, kind=args.kind, description=args.desc,
                    when=now, copy=not args.no_copy, licence=args.licence,
                    note=args.note, proposer=proposer)
            store.write_record(root, "inputs", artifact)
            landed.append(artifact)
            if proposer and artifact.id in before and before[artifact.id] != proposer:
                said = report.HUMAN["propose"]
                was = (said["ingest_was_none"] if not before[artifact.id] else
                       report.HUMAN["basis"]["proposed_by"].format(
                           id=before[artifact.id],
                           basis=packs.proposer_basis(packs.proposers(root),
                                                      before[artifact.id])))
                known.append(said["ingest_known"].format(
                    where=artifact.path or artifact.url, aid=artifact.id, was=was))

    if args.json:
        _dump({"ingested": [a.to_dict() for a in landed],
               "inputs_hash": artifacts.inputs_hash(ledger)})
        return 0

    for artifact in landed:
        where = artifact.path or artifact.url
        size = f"  {human_bytes(artifact.bytes)}" if artifact.bytes else ""
        by = ""
        if found is not None and artifact.proposer == found.id:
            by = " — " + report.HUMAN["basis"]["proposed_by"].format(id=found.id,
                                                                   basis=found.basis)
        _say(f"{artifact.id:<20} {str(artifact.kind):<12} {where}{size}{by}")
    for line in known:
        _say(line)
    _say(f"{len(landed)} artifact(s) registered. An artifact with no extraction is decoration:")
    for artifact in landed[:3]:
        _say(f"  nopekit extract {artifact.id} --what '<what it tells you>' "
             f"--grounds <param-or-claim>")
    return 0


#: The tail of a proposer's output a refusal quotes: enough to name the
#: exception, never a screen of traceback.
_PROPOSE_TAIL = 3

#: How many moved paths the out-of-directory refusal names before "and N more":
#: enough to see what the proposer touched; `git status` has the rest.
_PROPOSE_MOVED = 8

#: What `_project_files` leaves out, relative to the project: the ignored
#: outputs and scratch of `store`'s two ignore blocks (`.nopekit/out/` holds
#: every proposal directory) and git's own directory. Everything else a
#: proposer could write — records, `model/`, `gates/`, `selftest/`, input
#: payloads, the tracked verdict cache, `site/` — is watched.
_PROPOSE_UNWATCHED_DIRS = frozenset({".git", f"{store.NOPEKIT_DIR}/{store.OUT_NAME}",
                                     f"{store.NOPEKIT_DIR}/{store.CACHE_NAME}",
                                     f"{store.NOPEKIT_DIR}/{store.OBS_NAME}",
                                     f"{store.NOPEKIT_DIR}/export", f"{store.NOPEKIT_DIR}/runs",
                                     store.PACKAGES_NAME})
_PROPOSE_UNWATCHED_FILES = frozenset({f"{store.NOPEKIT_DIR}/{store.LEDGER_NAME}",
                                      f"{store.NOPEKIT_DIR}/{store.LEGACY_LEDGER_NAME}",
                                      store.REPORT_NAME})


def _project_files(root: str) -> dict[str, tuple[int, int]]:
    """Every file under the project but its ignored outputs and scratch, as
    ``{relative path: (size, mtime_ns)}`` — what ``propose`` compares around a
    run. A stat, not a digest: a proposal can run against a project with large
    evidence, and any write moves the mtime. Bytecode is left out (a proposer
    that imports the model writes it beside each module)."""
    found: dict[str, tuple[int, int]] = {}
    for here, dirs, names in os.walk(root):
        rel = os.path.relpath(here, root).replace(os.sep, "/")
        rel = "" if rel == "." else rel + "/"
        dirs[:] = sorted(d for d in dirs if d != "__pycache__"
                         and f"{rel}{d}" not in _PROPOSE_UNWATCHED_DIRS)
        for name in names:
            key = f"{rel}{name}"
            if key in _PROPOSE_UNWATCHED_FILES or name.endswith((".pyc", ".pyo")) \
                    or (key.startswith(f"{store.NOPEKIT_DIR}/")
                        and name.endswith((".tmp", ".lock"))):
                continue
            try:
                st = os.lstat(os.path.join(here, name))
            except OSError:
                continue
            found[key] = (st.st_size, st.st_mtime_ns)
    return found


def cmd_propose(args: argparse.Namespace) -> int:
    """Run a tool a pack ships to propose options or evidence, and record each
    file it wrote as an input (SPEC 22, 25) — a proposal settles nothing.

    With no id, it lists every proposer a discoverable pack declares. With one,
    it runs `python <run> [args]` as a subprocess, outside the lock, with the
    project as its working directory and `$NOPEKIT_PROPOSAL_DIR` a fresh
    directory under `.nopekit/out/propose/` (ignored scratch); then, under the
    lock, ingests every regular file it wrote there, in sorted order, recording
    `proposer` on each NEW input record — `ingest`'s one record per file, and
    nothing else. Refused, recording nothing: a non-zero exit, a run past
    `--timeout`, and a run that wrote anywhere in the project outside its
    directory (`_project_files`, every path named): a proposal settles nothing,
    so a design edit made by one must not land unannounced. What slipped
    through (review of phase 2): the guard was `records_digest`, so a proposer
    that appended to `model/*.py`, `gates/` or the verdict cache was ingested
    and nothing said so. Detected, never undone, as `export` treats a generator;
    a write that puts back a file's size and mtime is past it (SPINE_CONTRACT's
    limits).

    nopekit's own flags are nopekit's wherever they are typed; the script gets
    the plain arguments after the id, and every argument after the first `--`
    (split off in `main`), a flag of its own included — one typed before `--` is
    an unrecognised argument, said aloud. What slipped through (review of phase
    2): the script's arguments were `argparse.REMAINDER`, so `propose <id>
    --json` handed `--json` to the script and printed sentences, and `--timeout
    30` kept 600 s — silently.
    *Rejected:* importing the script in process (a proposer is a stranger's
    code with its own dependencies, and the spine's loader traces what it
    imports — for evaluators); a proposal record (SPEC 22: the options are kept
    or dropped in a decision); refusing nopekit's flag names after the id under
    `REMAINDER` (argparse drops the `--` before handing it over, so `<id> --
    --json` and `<id> --json` arrive alike).
    """
    root = _root(args)
    said = report.HUMAN["propose"]
    if args.id is None:
        found = packs.proposers(root)
        if args.json:
            _dump({"proposers": [dict(p._asdict()) for p in found.values()]})
            return 0
        if not found:
            _say(said["empty"])
            return 0
        for proposer in found.values():
            _say(said["row"].format(id=proposer.id, basis=proposer.basis,
                                    description=proposer.description, pack=proposer.pack))
        _say(said["footer"])
        return 0
    proposer = packs.find_proposer(root, args.id)
    extra = list(args.args or [])
    before = _project_files(root)
    stamp = utcnow_iso().replace(":", "")
    base = os.path.join(store.out_dir(root), "propose", proposer.id)
    out = os.path.join(base, stamp)
    n = 1
    while os.path.exists(out):
        n += 1
        out = os.path.join(base, f"{stamp}-{n}")
    os.makedirs(out)
    env = {**os.environ, "NOPEKIT_PROPOSAL_DIR": out,
           "NOPEKIT_PROJECT": os.path.abspath(root)}
    try:
        proc = subprocess.run([sys.executable, proposer.run, *extra], cwd=root,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=args.timeout, env=env)
    except subprocess.TimeoutExpired:
        raise NopekitError(said["timeout"].format(id=proposer.id, t=args.timeout)) from None
    if proc.returncode != 0:
        lines = [line for line in (proc.stderr or proc.stdout or "").splitlines()
                 if line.strip()]
        tail = " / ".join(report._one(line) for line in lines[-_PROPOSE_TAIL:]) or "(nothing)"
        raise NopekitError(said["errored"].format(id=proposer.id, code=proc.returncode,
                                                  tail=tail))
    after = _project_files(root)
    moved = sorted(rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel))
    if moved:
        shown = ", ".join(moved[:_PROPOSE_MOVED])
        if len(moved) > _PROPOSE_MOVED:
            shown += said["outside_more"].format(n=len(moved) - _PROPOSE_MOVED)
        raise NopekitError(said["outside"].format(id=proposer.id, paths=shown))
    files: list[str] = []
    for here, dirs, names in os.walk(out):
        dirs.sort()
        files += [os.path.join(here, name) for name in sorted(names)
                  if os.path.isfile(os.path.join(here, name))
                  and not os.path.islink(os.path.join(here, name))]
    if not files:
        if args.json:
            # The same document a run that wrote files prints: a script that
            # parses `propose --json` must not meet a sentence on a run that
            # succeeded (review of phase 2).
            _dump({"proposer": dict(proposer._asdict()), "ingested": [], "known": []})
            return 0
        _say(said["none"].format(id=proposer.id))
        return 0

    now = utcnow_iso()
    note = f"nopekit propose {proposer.id} {shlex.join(extra)}".strip()
    landed: list[Any] = []
    known: list[str] = []
    with _lock(root):
        ledger = _migrate(root, apply=True, now=now)
        for path in files:
            held = {a.id for a in ledger.inputs}
            artifact = artifacts.ingest(
                root, ledger, path, kind=args.kind,
                description=args.desc or said["desc"].format(id=proposer.id),
                when=now, note=note, proposer=proposer.id)
            if artifact.id in held:
                known.append(said["known"].format(where=artifact.path or artifact.url,
                                                  aid=artifact.id))
                continue
            store.write_record(root, "inputs", artifact)
            landed.append(artifact)
    by = report.HUMAN["basis"]["proposed_by"].format(id=proposer.id, basis=proposer.basis)
    if args.json:
        _dump({"proposer": dict(proposer._asdict()),
               "ingested": [a.to_dict() for a in landed], "known": known})
        return 0
    for artifact in landed:
        where = artifact.path or artifact.url
        _say(f"{artifact.id:<20} {str(artifact.kind):<12} {where} — {by}")
    for line in known:
        _say(line)
    _say(said["done"].format(n=len(landed), id=proposer.id, basis=proposer.basis))
    return 0


def _input_bytes(root: str, artifact: Any, digests: FileDigests) -> dict[str, Any]:
    """What an artifact's record says about its bytes, beside what the bytes say
    now: `record` (its file), `sha256` (the digest of the bytes NOW, `None` when
    they are missing), `pinned` (the digest the record pinned at ingest),
    `drift` and `exists` (`None` for a link, which has no bytes) — the keys, and
    the rule, of the index's input rows (`store.build_index`), so `inputs` and
    `.nopekit/ledger.json` never disagree about a file. What slipped through
    before (S-45): evidence was hashed at ingest and never again, and nothing a
    human ran said that tampered bytes had moved."""
    pinned = artifact.sha256 or ""
    facts: dict[str, Any] = {"record": f"{store.INPUTS_NAME}/{artifact.id}.json",
                             "sha256": None, "pinned": pinned, "drift": False,
                             "exists": None}
    if artifact.path:
        path = artifact.path.replace("\\", "/")
        full = path if os.path.isabs(path) else os.path.join(root, *path.split("/"))
        computed = digests.digest(full)
        facts.update(sha256=computed, exists=computed is not None,
                     drift=bool(pinned) and computed is not None and computed != pinned)
    return facts


def cmd_inputs(args: argparse.Namespace) -> int:
    """List ingested evidence; `--unextracted` lists only the decorations.

    The `[!]` marker on an unread artifact is the whole reason this listing
    exists as its own command rather than a line in `status`: a project with
    twelve files and two extractions looks grounded from the outside and is not.

    Each row carries its record (`inputs/<id>.json`), the digest of its bytes
    now against the one pinned at ingest — `DRIFT` when they differ, `MISSING`
    when the bytes are gone (`_input_bytes`) — and what it grounds, from the
    same derived map `why` reads (`artifacts.value_grounding`, S-36). A READ: no lock, no
    record, and the digest cache is consulted, never saved.
    """
    root = _root(args)
    ledger = _load(root)
    model, _projection_unused, model_error = _projection_safe(root, ledger)
    grounds = artifacts.value_grounding(ledger, _param_views(root, ledger, model, model_error))
    # The listing is about inputs (architecture phase 3a): a value's grounded_by
    # may name an assumption claim's id, which is no input — left out here,
    # shown by `why` and drawn by the graph.
    input_ids = {a.id for a in ledger.inputs}
    claim_ids = {c.id for c in ledger.claims}
    grounds = {target: [aid for aid in ids if aid in input_ids or aid not in claim_ids]
               for target, ids in grounds.items()}
    grounds = {target: ids for target, ids in grounds.items() if ids}
    grounded: dict[str, list[str]] = {}
    for target, ids in grounds.items():
        for aid in ids:
            grounded.setdefault(aid, []).append(target)
    digests = FileDigests(os.path.join(store.nopekit_dir(root), store.CACHE_NAME,
                                       store._DIGESTS_NAME))
    rows = artifacts.unextracted(ledger) if args.unextracted else list(ledger.inputs)
    if args.kind:
        rows = [a for a in rows if str(a.kind) == args.kind]
    facts = {a.id: _input_bytes(root, a, digests) for a in rows}

    # SPEC 38: an input's basis is its proposer's, looked up — never stated.
    bases = _input_bases(root, ledger)
    if args.json:
        _dump({"inputs": [dict(a.to_dict(), **facts[a.id], grounds=grounded.get(a.id, []),
                               basis=bases.get(a.id, ""))
                          for a in rows],
               "total": len(ledger.inputs),
               "unextracted": len(artifacts.unextracted(ledger)),
               "grounding": grounds})
        return 0

    if not rows:
        _say("no ingested artifacts match" if ledger.inputs
             else "no evidence ingested yet — `nopekit ask` for what to request")
        return 0

    for artifact in rows:
        mark = "   " if artifact.extractions else "[!]"
        where = artifact.path or artifact.url
        note = (f"{len(artifact.extractions)} extraction(s)" if artifact.extractions
                else "NEVER READ")
        targets = grounded.get(artifact.id, [])
        if targets:
            note += f" — grounds {', '.join(targets)}"
        row = facts[artifact.id]
        if row["exists"] is False:
            note += f" — MISSING: {row['record']} names bytes that are not there"
        elif row["drift"]:
            note += (f" — DRIFT: changed since it was ingested (pinned "
                     f"{row['pinned'][:12]}, now {row['sha256'][:12]})")
        if artifact.proposer:
            note += " — " + report.HUMAN["basis"]["proposed_by"].format(
                id=artifact.proposer, basis=bases.get(artifact.id) or "undeclared")
        _say(f"{mark} {artifact.id:<20} {str(artifact.kind):<12} {where:<40} {note}")
    unread = [a for a in rows if not a.extractions]
    if unread:
        _say(f"{len(unread)} artifact(s) nobody read anything out of. Evidence nobody "
             f"extracted from is decoration.")
    return 0


def _unmatched_grounds(root: str, ledger: Ledger, names: Iterable[str]) -> tuple[list[str], str]:
    """`(names, model_error)`: the grounds that name no claim, no parameter record
    and no parameter the model defines. Only for a warning — grounding is derived
    from the extraction on read (`artifacts.grounding`), so a name that matches
    nothing today links the moment the model or a claim gains it."""
    known = {claim.id for claim in ledger.claims} | {param.name for param in ledger.params}
    model, _, model_error = _projection_safe(root, ledger)
    if model is not None:
        known |= {param.name for param in model.params}
    return [name for name in names if name not in known], model_error


def cmd_extract(args: argparse.Namespace) -> int:
    """Record what was actually read out of an artifact, and what that grounds.

    This is the step that converts a photograph into provenance: after it,
    `nopekit why <param>` can answer "what is this number standing on?" with a
    file, a sentence and a confidence, instead of silence.

    A shim (spec §3.15): under the lock it migrates a legacy ledger first, then
    rewrites exactly one record, the artifact's `inputs/<id>.json`. It no longer
    copies the edge into the parameter or claim it grounds. What slipped through
    (S-36): that copy (`_link_grounding`) was a second home for one fact, so
    deleting the extraction left `why arm_length` saying "GROUNDED BY arm" while
    `inputs` said `arm` was "NEVER READ" — forever. The extraction is the one
    home; grounding is derived from it where it is read.
    """
    root = _root(args)
    now = utcnow_iso()
    with _lock(root):
        ledger = _migrate(root, apply=True, now=now)
        extraction = Extraction(
            what=args.what,
            grounds=_collect(args.grounds),
            confidence=args.confidence,
            note=args.note or "",
        )
        artifact = artifacts.add_extraction(ledger, args.artifact, extraction)
        store.write_record(root, "inputs", artifact)
    unmatched, model_error = _unmatched_grounds(root, ledger, extraction.grounds)

    if args.json:
        _dump(dict(artifact.to_dict(), unmatched_grounds=unmatched))
        return 0
    _say(f"{artifact.id}: {extraction.what}  [{extraction.confidence}]")
    if extraction.grounds:
        _say(f"  grounds: {', '.join(extraction.grounds)}")
        if unmatched:
            unsure = (" (the model did not load, so its parameters were not checked)"
                      if model_error else "")
            _warn(f"warning: no claim or parameter is named {', '.join(unmatched)}"
                  f"{unsure} — if the model or a claim gains that name, this evidence "
                  f"grounds it from then on; if it is a typo, nothing ever will")
    else:
        _say("  grounds nothing yet — `--grounds <param-or-claim>` is what makes this "
             "traceable from the other end")
    return 0


# --------------------------------------------------------------------------- #
# claims
# --------------------------------------------------------------------------- #
# A claim is a file, `claims/<id>.json`, and it is written by editing it: there
# is no `claim add` and no `claim edit` (PLAN A-8, removed at checkpoint 1.3). What
# slipped through while they existed (S-37): `claim edit --gates X` stored a
# coverage the registry owns, and the next `check` silently put the registry's
# answer back — a flag that looked like it bound a gate and bound nothing. Two
# commands that re-typed a record's fields as flags were a second, lossy spelling
# of a file the strict reader already checks, with a `difflib` suggestion for every
# typo. `claim physical` stays: it is the signing channel for a real-world result
# (D-12), and it writes one file.
def cmd_claim_list(args: argparse.Namespace) -> int:
    """One line per claim: status, id, statement, acceptance.

    Sorted by ledger order, not by status: an agent reading this twenty minutes
    apart needs the same line in the same place, and a list that reorders itself
    as things pass is unreadable as a diff.
    """
    root = _root(args)
    ledger = _load(root)
    registry, _ = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=utcnow_iso(), model=model)
    composed = claims.compositions(view, registry=registry,
                                   stale_gates=resolution.stale_gates,
                                   unseen_gates=claims.unseen_gates(resolution))
    resolved = {cid: c.status for cid, c in composed.items()}
    cover = claims.coverage(view, registry)
    stale_reasons = _stale_reasons(resolution)

    rows = list(view.claims)
    if args.status:
        # The enum value, its token or its word, in any case: `--status gap`,
        # `--status unclaimed` and `--status "pending build"` are one filter.
        # A spelling none of those holds is refused, naming them: what slipped
        # through (review of P2.1, which traded argparse's `choices` for this
        # free filter), `--status failed` for *failing* printed "no claims
        # match", exit 0 — an agent told that nothing is failing.
        wanted = args.status.strip().lower().replace("_", " ")

        def spelled(found: Any) -> set[str]:
            row = report.words(found.status, errored=found.errored)
            return {str(found.status.value), row.key.replace("_", " "), row.word}

        accepted = {spelling for status in ClaimStatus
                    for spelling in spelled(claims.Composed(status, claims.ClaimCause.CHECKED))}
        if wanted not in accepted:
            words = sorted({report.word(status) for status in ClaimStatus})
            raise NopekitError(
                f"--status {args.status!r} is no status — say one of: "
                + ", ".join(f'"{w}"' if " " in w else w for w in words)
                + " (or the enum value `claim list --json` prints)")
        rows = [c for c in rows if wanted in spelled(composed[c.id])]
    if args.kind:
        rows = [c for c in rows if str(c.kind) == args.kind]
    if args.tag:
        rows = [c for c in rows if args.tag in (c.tags or ())]

    if args.json:
        _dump({"claims": [dict(report.claim_json(c, composed=composed[c.id],
                                                 exports=view.exports),
                               status=str(resolved.get(c.id)),
                               covered_by=cover.get(c.id, []),
                               **report.status_view(composed[c.id], view, c,
                                                    stale_reasons=stale_reasons))
                          for c in rows],
               "stale": bool(resolution.stale_gates),
               "stale_gates": _stale_gate_list(resolution)})
        return 0

    if not rows:
        _say("no claims recorded" if not view.claims else "no claims match that filter")
        return 0
    for claim in rows:
        found = composed[claim.id]
        # The terminal's display word, never the kind's code word (P2.5a-D20;
        # GLOSSARY §8 names `[{claim.kind}]` a raw enum leak): `[automated]`,
        # `[measurement]`, `[assumption]`, `[expert judgment: Dana]` — the last
        # what makes an authority visible (W8).
        accepts = claim.acceptance.render() or "no acceptance condition"
        flag = "" if claim.critical else " (not required)"
        _say(f"{report.status_tag(found.status, errored=found.errored)} {claim.id:<6} "
             f"{report._one(claim.statement)}{flag}  [{report.terminal_word(claim)}] "
             f"{report._one(accepts)}")
    return 0


def cmd_claim_show(args: argparse.Namespace) -> int:
    """One claim's whole provenance — the same view `nopekit why` gives.

    Routed through `_why_text` — `decisions.why` with the same coverage,
    verdicts and derived grounding `why` passes — rather than re-rendered here,
    because two renderings of one claim's history is two places to forget the
    rejected alternatives.
    """
    root = _root(args)
    ledger = _load(root)
    if ledger.claim(args.id) is None:
        raise NopekitError(f"no claim {args.id!r} — `nopekit claim list` shows what exists")
    registry, _ = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=utcnow_iso(), model=model)
    # The view's claim: its `gates` are the registry's coverage over the records'
    # cached opinion (cli:H5). This command used to resolve the bare record with
    # no registry at all — `claim list` said PENDING while `claim show` said
    # UNCLAIMED for the same claim, one command apart.
    claim = view.claim(args.id)
    found = claims.compose(claim, view.verdicts, stale_gates=resolution.stale_gates,
                           unseen_gates=claims.unseen_gates(resolution))
    status = found.status

    why = _why_text(root, ledger, registry, model, model_error, view, resolution, claim.id,
                    projection=projection)
    if args.json:
        _dump(dict(report.claim_json(claim, composed=found, exports=view.exports),
                   status=str(status),
                   covered_by=claims.coverage(view, registry).get(claim.id, []),
                   verdicts=[_resolved_row(v, resolution)
                             for v in claims.covering_verdicts(claim, view.verdicts)],
                   why=why,
                   **report.status_view(found, view, claim,
                                        stale_reasons=_stale_reasons(resolution))))
        return 0
    _say(f"{report.status_tag(status, errored=found.errored)} {claim.id}")
    if found.cause is not claims.ClaimCause.CHECKED:
        # Why it reads what it reads, under the header — the composition's
        # reason, as `status` prints it. What slipped through (review of P2.4):
        # C3 tightened to 0.1 MPa printed `[FAIL ] C3` over one evaluator line,
        # `[ok  ] bracket.bearing : 0.19 MPa …`, and nothing on the screen said
        # the acceptance condition was not met.
        said = report.reason(found, view, claim, full=True,
                             stale_reasons=_stale_reasons(resolution))
        _say(f"  {said}")
    sys.stdout.write(why)
    return 0


#: The environment variables an agent's shell carries, by exact name: the ones a
#: Claude Code session sets for the commands it runs, never one a person sets.
#: Provenance: observed in this harness's Bash tool on 2026-10-04 (P2.5a §1, and
#: again in its review) — ``CLAUDECODE``, ``AI_AGENT``, ``CLAUDE_CODE_SESSION_ID``,
#: ``CLAUDE_CODE_CHILD_SESSION``, ``CLAUDE_CODE_ENTRYPOINT``,
#: ``CLAUDE_CODE_EXECPATH``, ``CLAUDE_CODE_SESSION_ATTENDED``,
#: ``CLAUDE_CODE_MESSAGING_SOCKET``, ``CLAUDE_CODE_MESSAGING_TOKEN`` and
#: ``CLAUDE_PID``; ``tests/_env.py`` strips every ``CLAUDE*``, so every test child
#: reads ``non-interactive`` unless it sets one. *Rejected:*
#: ``CLAUDE_CODE_CHILD_SESSION`` alone (the parent session sets ``CLAUDECODE``
#: without it); the whole ``CLAUDE`` prefix (a person's own ``CLAUDE_API_KEY``
#: would read every entry they make as an agent's); the whole ``CLAUDE_CODE_``
#: prefix, P2.5a's — what slipped through its review: Claude Code's own
#: user-set configuration lives there (``CLAUDE_CODE_USE_BEDROCK``,
#: ``CLAUDE_CODE_MAX_OUTPUT_TOKENS``, …), so a person who exports one in their
#: shell profile had every pass they typed counted for nothing, every ``assume``
#: refused with the advice to ask themselves, and every fail written with no
#: typed id — and nothing said which variable did it (``_agent_marker`` now
#: names it); a flag or an ``NOPEKIT_CHANNEL`` variable (the generator fills
#: it). The cost, said: a future session marker under a new name reads a
#: person's shell until it is added here — the seal is drift's catch, and P3's
#: permission rule the lock (D-13).
#:
#: Codex and Cursor (the plugin's other hosts, 2026-10-21): ``CODEX_CI`` and
#: ``CODEX_TOOL_CALL_ID``, which Codex's exec tool sets for every command the
#: agent runs and its TUI's ``!`` shell does not (``openai/codex``,
#: ``core/src/unified_exec/process_manager.rs``, ``core/src/tasks/user_shell.rs``);
#: ``CURSOR_AGENT``, Cursor's documented mark of its agent's terminal. What
#: would have slipped through without them: from a Codex or Cursor session no
#: marker was set, so a pass the agent typed through a pty read as a person's.
#: *Rejected:* ``CODEX_THREAD_ID``, ``CODEX_SESSION_ID`` and ``CODEX_VERSION``
#: (Codex sets them in a person's ``!`` shell too); ``CURSOR_TRACE_ID`` and
#: ``CURSOR_CLI`` (set in every Cursor terminal, a person's own included);
#: ``CODEX_SANDBOX`` (macOS only, and only under the sandbox).
AGENT_MARKERS: tuple[str, ...] = (
    "CLAUDECODE", "AI_AGENT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID",
    "CODEX_CI", "CODEX_TOOL_CALL_ID", "CURSOR_AGENT")

#: Where an agent session's id is read, first set wins, once a marker says the
#: shell is an agent's: Claude Code's, then Codex's thread. Cursor sets none in
#: its agent's terminal; its sessions read ``agent-session unknown``.
_SESSION_ID_VARS: tuple[str, ...] = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")

#: Flags `claim physical` still parses only to refuse — any value, before the
#: project is read — each with what it names instead (P2.5a-D5; A-14 asks to
#: delete them). The ONE list: `test_contracts.REMOVED_NAMES` and
#: `test_docs_commands` read it, so a printed command that passes one is red.
#: What slipped through (S-48): `--who` took a name the agent typed and
#: defaulted to nobody, and `--when` dated a result whenever its typist said.
#: *Rejected:* removing them now (a pasted older command would meet argparse's
#: bare "unrecognized arguments"); accepting and ignoring (a person who typed
#: `--who Alex` would believe Alex is on the record).
REFUSED_FLAGS: dict[str, str] = {"--who": "who", "--when": "when"}

#: The longest agent session id a channel value keeps (`agent-session <id>`):
#: an id is a key to find the session, not prose; a longer one is cut. Why 64:
#: the id observed here (``CLAUDE_CODE_SESSION_ID``, 2026-10-04) is a
#: 36-character UUID, and 64 keeps whole any id up to a hex SHA-256 — the
#: longest an opaque session key plausibly grows — while the channel value,
#: sealed into every entry, never carries a paragraph an agent's environment
#: filled. *Rejected:* no cap (the variable is the agent's to fill); the UUID's
#: own 36 (a longer id format would be cut, and two sessions could share the
#: cut prefix).
_SESSION_ID_MAX = 64


def _agent_marker(environ: Mapping[str, str]) -> str:
    """The first ``AGENT_MARKERS`` name ``environ`` sets, or ``""`` — named in the
    refusal and the recorded line, so a person whose shell reads as an agent's
    learns which variable says so."""
    return next((name for name in AGENT_MARKERS if name in environ), "")


def _channel(stdin_isatty: bool, environ: Mapping[str, str], answer: str | None,
             claim_id: str) -> str:
    """How a result or an attribution is being entered (P2.5a-D4) — pure, so a
    test drives it with an injected TTY, environment and typed line:

    * ``agent-session <its id (`_SESSION_ID_VARS`) or unknown>`` when an agent
      marker is set (``AGENT_MARKERS``, by exact name) — the marker wins over
      a TTY: an agent can open a pty;
    * ``non-interactive`` with no TTY on stdin (a pipe, CI, a script);
    * ``interactive`` from a TTY with no marker, once the person typed the
      claim's id at the prompt — anything else (another id, an empty line, end
      of input) is refused, and nothing is written.

    Only ``interactive`` makes a pass count or an attribution exist. What it is:
    tamper-evidence against drift and a helpful agent's shortcut — not a lock
    against a hostile same-user process, which can open a pty with the markers
    unset (D-13; SPINE_CONTRACT's limits). P3's permission rule (W9) and the
    ``/tested`` slash channel close what it cannot. *Rejected:* a y/n
    confirmation (typing the id makes the person read which claim they settle);
    requiring stdout a TTY too (``… | tee log`` in a person's own shell would be
    refused for no safety gain)."""
    if _agent_marker(environ):
        found = next((environ[name] for name in _SESSION_ID_VARS if environ.get(name)), "")
        session = "-".join(str(found or "unknown").split())
        return f"agent-session {session[:_SESSION_ID_MAX] or 'unknown'}"
    if not stdin_isatty:
        return "non-interactive"
    if answer is not None and answer.strip() == claim_id:
        return "interactive"
    typed = "nothing" if not (answer or "").strip() else repr(answer.strip())
    raise NopekitError(report.HUMAN["signing"]["typed"].format(typed=typed, id=claim_id))


def _confirm(claim_id: str, rows: Iterable[str]) -> str | None:
    """The prompt, on stderr — so ``--json`` keeps stdout to one document — and
    the line the person typed, or ``None`` at end of input."""
    for row in rows:
        print(row, file=sys.stderr)
    sys.stderr.write(report.HUMAN["signing"]["type"].format(id=claim_id))
    sys.stderr.flush()
    try:
        line = sys.stdin.readline()
    except OSError:
        # A terminal closed under the prompt reads EIO on Linux, not end of
        # input: either way nothing was typed.
        return None
    return line.rstrip("\r\n") if line else None


def _measured_outcome(acceptance: Any, measured: float | None, typed: bool | None) -> bool:
    """The outcome a result records (P2.5a-D15, D-13 *consistent*): ``--measured``
    decides it where the claim has a finite limit, and a typed pass or fail that
    disagrees is refused; with no limit, the typed outcome, which must be there.
    What slipped through without it: E4 had no number — "how far off was the
    evaluator" — and a pass could be recorded at a value its own claim refutes."""
    said = report.HUMAN["signing"]
    if measured is None:
        if typed is None:
            raise NopekitError(said["no_act"].format(id="<id>"))
        return typed
    limit = getattr(acceptance, "limit", None)
    if isinstance(limit, (int, float)) and not isinstance(limit, bool):
        holds = acceptance.holds(float(measured))
        if typed is not None and typed is not holds:
            raise NopekitError(said["measured_disagrees"].format(
                value=report._num(measured), verdict=said["meets" if holds else "fails"],
                condition=acceptance.render(), act="pass" if typed else "fail"))
        return holds
    if typed is None:
        raise NopekitError(said["measured_no_limit"].format(id="this claim",
                                                             value=report._num(measured)))
    return typed


def _evidence(root: str, paths: Iterable[str], *, required: bool, claim_id: str
              ) -> tuple[list[str], dict[str, str]]:
    """``(paths, {path: sha256})`` for ``--evidence``: each a regular file under
    the project, not under ``.nopekit/`` (scratch nobody tracks), spelled
    root-relative. A pass needs at least one, and each must exist (D-13
    *evidenced*); a fail records what it is given and hashes what is there — a
    missing photo never silences a no."""
    said = report.HUMAN["signing"]
    base = os.path.abspath(root)
    state = os.path.join(base, store.NOPEKIT_DIR)
    listed: list[str] = []
    digests: dict[str, str] = {}
    for raw in paths:
        full = os.path.normpath(raw if os.path.isabs(raw) else os.path.join(base, raw))
        rel = os.path.relpath(full, base).replace(os.sep, "/") \
            if full.startswith(base + os.sep) else raw
        problem = ""
        if not full.startswith(base + os.sep):
            problem = "it is outside the project"
        elif full == state or full.startswith(state + os.sep):
            problem = f"it is under {store.NOPEKIT_DIR}/, which is scratch nobody tracks"
        elif not os.path.isfile(full):
            problem = "there is no such file"
        if problem:
            if required:
                raise NopekitError(said["bad_evidence"].format(path=report._one(raw),
                                                                why=problem))
            listed.append(rel)
            continue
        listed.append(rel)
        digests[rel] = sha256_file(full)
    if required and not digests:
        raise NopekitError(said["no_evidence"].format(id=claim_id))
    return listed, digests


def _design_values(projection: Any, limit: int = 3) -> str:
    """`config.arm_length 60.0, config.width 30.0, config.thickness 7.0, +9 more` —
    the first inputs of the design an article is bound to, for the prompt."""
    config = dict((projection or {}).get("config") or {}) if isinstance(projection, Mapping) \
        else {}
    shown = [f"config.{key} {value!r}" for key, value in list(config.items())[:limit]]
    more = len(config) - len(shown)
    return ", ".join(shown) + (f", +{more} more" if more > 0 else "")


def _act(result: str | None, passed: bool | None, measured: float | None,
         claim_id: str = "the claim") -> tuple[str | None, bool | None]:
    """``(act, typed)`` from the positional act and the outcome flags — refused
    where they disagree, and an outcome's flag or a value with ``assume``,
    nothing written (review of P2.5a: ``fail --pass`` recorded and counted a
    PASS, its ``--json`` saying ``act: fail``; ``pass --fail`` wrote a fail that
    P2.5a cannot supersede; ``assume --fail`` recorded an attribution; each
    flag and the act were read in turn and the flag won, silently).
    *Rejected:* letting either win (whichever it is, one word the person typed
    is ignored on a record that cannot be taken back)."""
    said = report.HUMAN["signing"]
    if result == "assume":
        flag = ("--pass" if passed else "--fail") if passed is not None else (
            "--measured" if measured is not None else "")
        if flag:
            raise NopekitError(said["assume_flags"].format(id=claim_id, flag=flag))
        return "assume", None
    if result in ("pass", "fail") and passed is not None and passed is not (result == "pass"):
        raise NopekitError(said["act_disagrees"].format(
            act=result, flag="--pass" if passed else "--fail"))
    typed = passed if passed is not None else (
        (result == "pass") if result in ("pass", "fail") else None)
    act = result if result is not None else (
        ("pass" if typed else "fail") if typed is not None else None)
    return act, typed


def _not_text(value: Any) -> bool:
    """Whether ``value`` holds a lone surrogate — an argument whose bytes were not
    UTF-8 — which no record can store as text."""
    try:
        str(value).encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def cmd_claim_physical(args: argparse.Namespace) -> int:
    """The one channel a physical result, an owner or an authority enters
    through (D-12; P2.5a): `claim physical <id> pass|fail|assume`.

    What slipped through before P2.5a: `--who` took a name the agent typed and
    defaulted to nobody (S-48); a pass typed by the agent counted as one a person
    made; C5 read "verified" in the same second it was claimed, with nothing
    written down that a result could fail; a pass survived any change to the
    design it was tested on (S-50); and an owner was whatever a claim file said.

    In order, refused with nothing written (P2.5a-D9): a refused flag (before
    the project is read); the claim; git's identity (`who`); the act fitting
    the claim — a pass settles a measurement or the authority's judgment,
    beside an automated evaluator it settles nothing, an assumption takes none;
    `assume` records an owner or an authority, typed by them in their own shell;
    for a pass on a physical claim a written test, evidence and a model that
    loads; `--measured` consistent with the acceptance condition. Then the
    prompt and the typed id when it is a person's own shell, and one sealed
    entry appended (`store.append_signed`). From an agent session or a pipe a
    pass is recorded and counts for nothing, a fail counts (R-3).

    A shim (spec §3.15) under the lock: it migrates a legacy ledger first and
    writes `results/<claim-id>.json` and nothing else. Then, outside the lock,
    the status the claim reads NOW as every reader composes it — never one built
    from the result alone (`860ffa6`'s `[ok-hw]`: R-5).
    """
    said = report.HUMAN["signing"]
    for flag, key in REFUSED_FLAGS.items():
        if getattr(args, key, None) is not None:
            raise NopekitError(said[key])
    act, typed = _act(args.result, args.passed, args.measured, args.id)
    # A value that is not text is refused here, naming its flag, before the
    # project is read (review of P2.5a: `--detail $'bad\xffbyte'` reached the
    # writer and printed a traceback, exit 1 — the code for a gate-level stop).
    for flag, values in (("--detail", [args.detail]), ("--evidence", list(args.evidence)),
                         ("--authority", [args.authority])):
        if any(value is not None and _not_text(value) for value in values):
            raise NopekitError(said["not_text"].format(flag=flag))
    measured = args.measured
    if measured is not None and not math.isfinite(measured):
        raise NopekitError(said["measured_bad"].format(value=measured))
    if act is None and measured is None:
        raise NopekitError(said["no_act"].format(id=args.id))
    given = getattr(args, "article", None)
    if given is not None and not re.fullmatch(r"[0-9a-f]{12,64}", str(given).strip()):
        raise NopekitError(said["article_short"])
    root = _root(args)
    now = utcnow_iso()
    environ = dict(os.environ)
    isatty = bool(sys.stdin and sys.stdin.isatty())
    with _lock(root):
        ledger = _migrate(root, apply=True, now=now)
        claim = ledger.claim(args.id)
        if claim is None:
            raise NopekitError(f"no claim {args.id!r} — `nopekit claim list` shows what exists")
        who = vcs.ident(root)
        if not who:
            raise NopekitError(said["no_identity"])
        terminal = claims.terminal_of(claim)
        exported = _exported_article(ledger, given) if given is not None else None
        registry, _problems = _registry(root, ledger, strict=False)
        model, projection, model_error = _projection_safe(root, ledger)
        view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                     now=now, model=model)
        current = view.claim(claim.id)
        reads = claims.compose(current, view.verdicts, stale_gates=resolution.stale_gates,
                               unseen_gates=claims.unseen_gates(resolution))
        required = said["required"] if claim.critical else said["not_required"]
        head = [f"{claim.id} {report._one(claim.statement)}",
                "  " + said["prompt_status"].format(terminal=report.terminal_word(claim),
                                                    required=required,
                                                    status=report.word(reads.status,
                                                                       errored=reads.errored))]
        marker = _agent_marker(environ)
        person = not marker and isatty
        if act == "assume":
            if not person:
                authority = claims.name_of(claim.authority)
                name = authority if terminal == "human" else claims.name_of(claim.owner)
                flag = f' --authority "{report._one(authority)}"' if terminal == "human" else ""
                why = (said["marker"].format(marker=marker) if marker
                       else said["assume_pipe"])
                raise NopekitError(said["assume_channel"].format(
                    id=claim.id, name=report._one(name) or "its owner", flag=flag, why=why))
            list_name = "attributions"
            entry, rows = _attribution_entry(args, claim, terminal, who, now)
        else:
            list_name = "results"
            entry, rows = _result_entry(args, root, claim, current, terminal, typed, measured,
                                        who, now, view, resolution, registry, model,
                                        projection, model_error, person=person,
                                        exported=exported)
        rows = head + rows + ["  " + said["recorded_by"].format(who=report._one(who))]
        answer = _confirm(claim.id, rows) if person else None
        entry["channel"] = _channel(isatty, environ, answer, claim.id)
        store.append_signed(root, claim.id, list_name, entry)
        path = os.path.join(root, "results", f"{claim.id}.json")
        n = len(store.read_record(path, "results").raw[list_name])

    # The status the claim reads NOW, composed as every reader composes it —
    # never computed here from the result alone. What slipped through (review
    # of the P2.1 design; P2.0's hand-off): this printed VERIFIED for any pass
    # and REFUTED for any fail, a second status producer (R-5), so beside a
    # covering evaluator that fails, crashes or never ran it printed `[ok   ]`
    # where `status` printed Failing, Skipped or Open — and an agent quotes the
    # command's own line. Read after the write, outside the lock: a reader.
    ledger = _load(root)
    registry, _problems = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=now, model=model)
    claim = view.claim(args.id)
    found = claims.compose(claim, view.verdicts, stale_gates=resolution.stale_gates,
                           unseen_gates=claims.unseen_gates(resolution))
    shown = report.status_view(found, view, claim, stale_reasons=_stale_reasons(resolution))
    contradicts = [item for item in entry.get("contradicts") or ()
                   if item.get("inside") is True]
    if args.json:
        # The act as WRITTEN, never as typed (review of P2.5a: `fail --pass`
        # printed act "fail" over a pass in the file).
        written = "assume" if list_name == "attributions" else (
            "pass" if entry.get("passed") is True else "fail")
        _dump(dict(report.claim_json(claim, composed=found, exports=view.exports),
                   status=str(found.status),
                   recorded={"act": written,
                             "channel": entry["channel"], "who": who,
                             "article": (entry.get("article") or {}).get("hash", ""),
                             "contradicts": [item["gate"] for item in contradicts]},
                   **shown))
        return 0
    if list_name == "attributions":
        line = said["recorded_assume"].format(id=claim.id, role=entry["role"],
                                              name=report._one(entry["name"]), n=n)
    else:
        passed = entry["passed"] is True
        line = said["recorded"].format(id=claim.id, act="pass" if passed else "fail", n=n)
        channel = entry["channel"]
        if passed and terminal not in ("measurement", "human"):
            # Beside an automated evaluator a pass settles nothing whoever typed
            # it — the fact to give, as the judge gives it (`_fact_terminal`).
            line += said["beside"]
        elif channel.startswith("agent-session"):
            line += said["agent_pass" if passed else "agent_fail"].format(
                marker=said["marker"].format(marker=marker) if marker else "")
        elif channel == "non-interactive":
            line += said["pipe_pass" if passed else "pipe_fail"]
    _say(line)
    _say(f"{report.status_tag(found.status, errored=found.errored)} {claim.id} "
         f"{report._one(claim.statement)} — {shown['reason']}")
    for item in contradicts:
        _say(report.HUMAN["physical"]["contradicts"].format(
            gate=item["gate"], code=report.article12(item.get("code"))))
    return 0


def _attribution_entry(args: argparse.Namespace, claim: Claim, terminal: str, who: str,
                       now: str) -> tuple[dict, list[str]]:
    """The attribution `assume` records, and its prompt rows (P2.5a-D9): an
    owner — of an assumption, or of a fallback — or, on a claim that ends in
    expert judgment, its authority; typed by that person, whose git identity
    must be theirs (critique 10 of the P2.5a design: the authority role is held
    to the owner's rule — a name typed by anyone settled nothing it named)."""
    said = report.HUMAN["signing"]
    if terminal == "human":
        role, name = "authority", str(claim.authority or "").strip()
        if not name:
            raise NopekitError(said["no_authority"].format(id=claim.id))
        given = args.authority
        if given is None:
            raise NopekitError(said["authority_flag"].format(id=claim.id, authority=name))
        if claims.name_of(given) != name:
            raise NopekitError(said["authority_other"].format(given=given, id=claim.id,
                                                               authority=name))
        reason = str(claim.rationale or "")
    else:
        if args.authority is not None:
            raise NopekitError(said["authority_not_here"].format(id=claim.id))
        role, name = "owner", str(claim.owner or "").strip()
        kind = claim.kind if isinstance(claim.kind, ClaimKind) else ClaimKind(claim.kind)
        reason = claims.assumption_reason(claim)
        if not name and not reason.strip():
            raise NopekitError(said["assume_nothing"].format(id=claim.id))
        if not name:
            raise NopekitError(said["no_owner"].format(id=claim.id))
        if not reason.strip():
            field = "rationale" if kind is ClaimKind.ASSUMPTION else "fallback"
            raise NopekitError(said["no_reason"].format(id=claim.id, field=f'"{field}"'))
    if not claims.identity_matches(who, name):
        raise NopekitError(said["not_owner"].format(id=claim.id, role=role, name=name,
                                                     who=who))
    entry = {"role": role, "name": name, "reason": reason,
             "claim_digest": claims.claim_digest(claim), "who": who, "when": now}
    rows = ["  " + said["assume_row"].format(name=report._one(name), id=claim.id, role=role,
                                             reason=report._trunc(reason, 80) or "—")]
    return entry, rows


def _exported_article(ledger: Ledger, given: str) -> list:
    """EVERY export record whose article ``given`` (12 hex or more) names — one
    article, unique among `exports/`' articles — or an NopekitError naming why
    not (P2.5b-D13): no default to the newest export (a guess about which object
    the person holds), no ``--milestone`` (a milestone has many exports). All of
    them, never the first (review of P2.5b, findings 7 and 15: two milestones
    sharing a generator record one article, and the first in name order was
    kept — its `counted` did not hold the claim, so the fail sealed no
    contradiction): `milestones.bound_export` picks the one the prompt shows and
    `milestones.sealed_on` charges what any of them sealed."""
    said = report.HUMAN["signing"]
    prefix = str(given).strip()
    found: dict[str, list] = {}
    for entry in ledger.exports:
        digest = str((entry.article or {}).get("hash") or "")
        if digest.startswith(prefix):
            found.setdefault(digest, []).append(entry)
    if not found:
        raise NopekitError(said["article_unknown"].format(article=prefix[:12]))
    if len(found) > 1:
        names = ", ".join(f"{digest[:14]}…" for digest in sorted(found))
        raise NopekitError(said["article_ambiguous"].format(article=prefix, articles=names))
    return next(iter(found.values()))


def _result_entry(args: argparse.Namespace, root: str, claim: Claim, current: Claim,
                  terminal: str, typed: bool | None, measured: float | None, who: str,
                  now: str, view: Ledger, resolution: verdicts.Resolution,
                  registry: Any, model: Any, projection: Any, model_error: str, *,
                  person: bool = True, exported: Any = None) -> tuple[dict, list[str]]:
    """The physical result `pass`/`fail` records, and its prompt rows
    (P2.5a-D9, D10, D14, D15). ``person``: the shell is a person's own (a TTY,
    no agent marker) — only there can a pass count. ``exported`` (P2.5b-D13,
    `--article`): every export record of the article the result binds to — the
    article copied whole from the one `milestones.bound_export` picks, and a
    fail's contradictions the ones any of them sealed on the article's own
    inputs (`milestones.sealed_on`), never re-read from the evaluators' verdicts
    after they moved."""
    said = report.HUMAN["signing"]
    if measured is not None and typed is None and not isinstance(
            getattr(claim.acceptance, "limit", None), (int, float)):
        raise NopekitError(said["measured_no_limit"].format(
            id=claim.id, value=report._num(measured)))
    passed = _measured_outcome(claim.acceptance, measured, typed)
    kind = claim.kind if isinstance(claim.kind, ClaimKind) else ClaimKind(claim.kind)
    authority = claims.name_of(claim.authority)
    if terminal == "human":
        if args.authority is not None and claims.name_of(args.authority) != authority:
            raise NopekitError(said["authority_other"].format(
                given=args.authority, id=claim.id, authority=authority))
        if passed:
            if not authority:
                raise NopekitError(said["no_authority"].format(id=claim.id))
            if args.authority is None:
                raise NopekitError(said["authority_flag"].format(id=claim.id,
                                                                  authority=authority))
            if not claims.identity_matches(who, authority):
                raise NopekitError(said["not_owner"].format(
                    id=claim.id, role="authority", name=authority, who=who))
    elif args.authority is not None:
        raise NopekitError(said["authority_not_here"].format(id=claim.id))
    if passed and terminal == "none":
        raise NopekitError(said["none_pass"].format(id=claim.id))
    physical = kind is ClaimKind.PHYSICAL
    if passed and physical:
        # A written test and evidence for ANY pass on a physical claim, its
        # terminal a measurement or a judgment (critique 5 of the P2.5a design:
        # a hand-written `terminal: human` would otherwise drop both).
        if not (str(claim.note or "").strip() or claim.acceptance.render()):
            raise NopekitError(said["no_test"].format(id=claim.id))
    listed, sha = _evidence(root, _collect(args.evidence), required=passed and physical,
                            claim_id=claim.id)
    records = list(exported or ())
    bound = milestones.bound_export(records, claim.id) if records else None
    if bound is not None:
        article = dict(bound.article or {})
    else:
        article = verdicts.article_of(root, projection, model, anchors=resolution.anchors,
                                      resolution=resolution)
    if passed and terminal in ("measurement", "human") and not article:
        raise NopekitError(said["no_model"].format(
            error=report._trunc(model_error or "its code was not recorded", 160)))
    if passed and person and terminal in ("measurement", "human") and bound is None:
        # An article names the files the registered evaluators read on the
        # design (`verdicts.article_of`), so a pass waits until each has run
        # here at least once. What slipped through (review of P2.5a): a pass
        # recorded before a file-reading evaluator's first check run got an
        # article without that file — one design, two article ids — and an
        # edit of the file left that pass Checked. Only `never`: a stale entry
        # names the files its evaluator read, and an evaluator a prerequisite
        # keeps from running (`not run:`) cannot run before the print is
        # tested; that one's files join the article from its first run on (a
        # limit, SPINE_CONTRACT's). A fail is never refused: it counts on any
        # article (R-3); nor a pass from an agent session or a pipe, which
        # counts for nothing on any article.
        unread = [gid for gid, row in sorted(resolution.rows.items())
                  if row.state == "never" and not any(
                      str(note).startswith(verdicts._PRUNED_NOTE) for note in row.notes)]
        if unread:
            shown = ", ".join(unread[:3]) + (f", +{len(unread) - 3} more"
                                              if len(unread) > 3 else "")
            raise NopekitError(said["unread"].format(gates=shown, id=claim.id))
    contradicts: list[dict] = []
    check = ""
    if not passed and terminal != "human" and bound is not None:
        contradicts = milestones.sealed_on(records, claim.id)
        if not any(claim.id in (e.counted or {}) for e in records):
            names = ", ".join(sorted({str(e.milestone) for e in records}))
            check = (f"its evaluators were not re-run when article "
                     f"{report.article12(article)} was exported for {names}, "
                     f"so no verdict on its inputs is sealed to charge")
    elif not passed and terminal != "human":
        if not article:
            check = f"the model does not load: {report._trunc(model_error, 160)}" \
                if model_error else "the model's code was not recorded"
        else:
            needs = {spec.id: list(getattr(spec, "needs", None) or ())
                     for spec in (registry.specs() if registry is not None else ())}
            codes = {gid: str(((row.entry.code or {}) if row.entry is not None else {})
                              .get("digest") or "")
                     for gid, row in resolution.rows.items()}
            contradicts = claims.contradicted_by(current, view.verdicts,
                                                 stale_gates=resolution.stale_gates,
                                                 needs=needs, codes=codes)
    digest = claims.claim_digest(claim)
    units = str(claim.acceptance.units or "") if measured is not None else ""
    entry = {"passed": bool(passed), "when": now, "who": who,
             "detail": str(args.detail or ""), "evidence": listed,
             "authority": authority if terminal == "human" and args.authority else "",
             "measured": float(measured) if measured is not None else None, "units": units,
             "article": article, "claim_digest": digest,
             "rho": seal({"article": article.get("hash", ""), "claim_digest": digest})
             if article else "",
             "evidence_sha256": sha, "contradicts": contradicts, "contradiction_check": check}
    act = "pass" if passed else "fail"
    if measured is not None:
        unit = f" {units}" if units else ""
        what = said["you_record_measured"].format(
            act=act, measured=f"{report._num(measured)}{unit}",
            condition=claim.acceptance.render() or "no acceptance condition")
    else:
        what = said["you_record"].format(act=act, detail=report._one(args.detail)
                                         or said["no_detail"])
    if listed:
        what += " [" + ", ".join(report._one(path) for path in listed) + "]"
    rows = ["  " + what]
    if bound is not None:
        rows.append("  " + said["article_exported"].format(
            article=report.article12(article), milestone=bound.milestone,
            who=report._one(bound.who), when=str(bound.when)[:10],
            revision=str(article.get("revision") or "")[:12] or "none"))
    elif article:
        rows.append("  " + said["article"].format(article=report.article12(article),
                                                  values=_design_values(projection)))
        if article.get("revision"):
            rows.append("  " + said["revision"].format(revision=article["revision"][:12])
                        + (said["dirty"] if article.get("dirty") else ""))
    else:
        rows.append("  " + said["no_article"].format(
            error=report._trunc(model_error or "its code was not recorded", 80)))
    for item in contradicts:
        if item.get("inside") is not True:
            continue
        unit = f" {item.get('units')}" if item.get("units") else ""
        rows.append("  " + said["contradicts"].format(
            gate=item["gate"], id=claim.id, value=f"{report._num(item.get('value'))}{unit}",
            code=report.article12(item.get("code"))))
    return entry, rows


# --------------------------------------------------------------------------- #
# gaps
# --------------------------------------------------------------------------- #
def cmd_gap(args: argparse.Namespace) -> int:
    """Every claim whose mechanism has yet to run — and, with `--propose`, packs
    that might run it.

    SPEC decision 13: every claim is settleable, some expensively, so `gap` lists
    every claim that reads Gap, Pending build or Assumed (`claims.yet_to_run`,
    read off the composition `status` prints), physical and assumption claims
    included, each with its mechanism — what would settle it, in words — any
    warning about the evaluators that mechanism names, and the gap record that
    names it. Resolved exactly as `status` resolves (the registry, the
    projection, the verdict cache). What slipped through before: it listed only
    `find_gaps`' Need records, the automated claims no evaluator covers, so a
    physical test nobody had run and an assumption nobody owned read "no gaps".

    A gap record is still a growth signal, not a failure: the system is
    admitting there is a physical quantity it cannot currently check.
    `find_gaps` matches the Need records (`needs/<id>.json`) by claim id and
    keeps everything written on them, so the classification and the tool option
    costs an agent records against a gap — by editing that file — show here in
    the next session.

    A READ: no lock, no write. What slipped through (S-43): it persisted every gap
    it derived, so a command that reads like a query rewrote the whole ledger on
    every run, and a gap it could re-derive at will became a record nobody wrote.
    A Need is a record only when someone enriched it (`needs/` is sparse), so
    there is nothing here to write. Deciding a closed gap is `SATISFIED` is still
    a judgement this command is not entitled to make.
    """
    root = _root(args)
    ledger = _load(root)
    registry, _ = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=utcnow_iso(), model=model)
    composed = claims.compositions(view, registry=registry,
                                   stale_gates=resolution.stale_gates,
                                   unseen_gates=claims.unseen_gates(resolution))
    stale_reasons = _stale_reasons(resolution)
    waiting = report.in_severity(view, composed, claims.yet_to_run(view, composed))
    gaps = claims.find_gaps(view, registry)
    named = claims.mechanism_problems(view, registry)
    said = report.HUMAN["mechanism"]

    manifests = packs.discover(root) if args.propose else []
    # One search per gap record — its quantity, and the how of the claims it
    # names (`claims.need_how`) — and each listed claim's: its record's search,
    # or a transient Need (never written) with its own how. SPEC decision 13:
    # `--propose` searches the how; one need, one pack list (review of phase 1).
    proposals: dict[str, list[Any]] = {
        need.id: packs.match(need, manifests, how=claims.need_how(view, need))
        for need in gaps} if args.propose else {}
    searched: dict[str, list[Any]] = {}
    if args.propose:
        for claim in waiting:
            query, how = claims.proposal_query(claim, gaps, view)
            searched[claim.id] = (proposals[query.id] if query.id in proposals
                                  else packs.match(query, manifests, how=how))

    def need_of(claim: Claim) -> Any:
        return next((n for n in gaps if claim.id in (n.claim_ids or ())), None)

    if args.json:
        rows = []
        for claim in waiting:
            found = composed[claim.id]
            need = need_of(claim)
            row: dict[str, Any] = {
                "id": claim.id, "statement": claim.statement,
                "status": str(found.status), "key": claims.STATUS_KEY[found.status],
                "word": report.word(found.status, errored=found.errored),
                "cause": str(found.cause.value),
                "reason": report.reason(found, view, claim, full=True,
                                        stale_reasons=stale_reasons),
                "errored": found.errored,
                "mechanism": report.claim_json(claim)["mechanism"],
                "by_word": report.terminal_word(claim),
                "need": need.id if need is not None else None,
                "problems": [{"evaluator": p.evaluator, "kind": p.kind,
                              "filed": list(p.filed)}
                             for p in named if p.claim == claim.id]}
            if args.propose:
                row["candidates_from_packs"] = [m.to_dict() for m in searched[claim.id]]
            rows.append(row)
        _dump({
            "gaps": [dict(need.to_dict(),
                          candidates_from_packs=[m.to_dict() for m in proposals.get(need.id, [])])
                     for need in gaps],
            "n_gaps": len(gaps),
            # SPEC decision 13, additive: every claim whose mechanism has yet to run.
            "yet_to_run": rows,
            "n_yet_to_run": len(rows),
        })
        return 0

    def need_lines(need: Any, indent: str) -> None:
        if need.claim_class:
            _say(f"{indent}class: {need.claim_class}")
        for option in need.candidates:
            _say(f"{indent}{option.kind or 'option':<9} {option.name} "
                 f"— {option.cost or 'cost unstated'}")

    def pack_lines(matched: list[Any], indent: str, *, settled_by: str = "") -> None:
        """A claim's packs; with none, the extension protocol's trigger only
        for an automated claim — a physical, judged or assumed one (its
        `settled_by` word) is settled by its mechanism, never by a solver
        (docs/EXTENSION_PROTOCOL.md "When this fires")."""
        if matched:
            for manifest in matched[:3]:
                _say(f"{indent}pack      {manifest.name} — {manifest.description}")
        elif settled_by:
            _say(f"{indent}{said['no_match_settled'].format(by=settled_by)}")
        else:
            _say(f"{indent}{said['no_match']}")

    indent = " " * 8
    for claim in waiting:
        found = composed[claim.id]
        why = report.reason(found, view, claim, stale_reasons=stale_reasons)
        _say(f"{report.status_tag(found.status, errored=found.errored)} {claim.id} "
             f"{report._trunc(claim.statement, 52)} — {why}")
        _say(indent + report.mechanism_line(claim))
        for problem in named:
            if problem.claim == claim.id:
                _say(indent + said["warning"].format(
                    line=report.mechanism_problem_words(problem)))
        need = need_of(claim)
        if need is not None:
            _say(indent + said["need"].format(
                id=need.id, quantity=need.quantity or "(unnamed quantity)",
                word=report.need_word(need.status)))
            need_lines(need, indent + "  ")
        if args.propose:
            settled_by = ""
            if claims.terminal_of(claim):
                # Physical, judged or assumed: an automated pass never settles
                # it (compose's rungs), whatever a pack's evaluator checks.
                settled_by = report.terminal_word(claim)
                _say(indent + said["modelled_part"].format(id=claim.id, by=settled_by))
            pack_lines(searched[claim.id], indent, settled_by=settled_by)

    # A gap record whose claims are not listed above (a claim that reads Open or
    # Stale while no registered evaluator covers it, say), in its own row.
    listed = {c.id for c in waiting}
    for need in gaps:
        if any(cid in listed for cid in need.claim_ids or ()):
            continue
        cids = ", ".join(need.claim_ids)
        _say(f"{_tag('gap')} {need.id:<10} {need.quantity or '(unnamed quantity)'} "
             f"— claims {cids} ({report.need_word(need.status)})")
        need_lines(need, " " * 12)
        if args.propose:
            pack_lines(proposals.get(need.id, []), " " * 12)

    if not waiting:
        _say(said["empty"].format(gap=report.word(ClaimStatus.UNCLAIMED),
                                  pending_build=report.word(ClaimStatus.UNVERIFIED),
                                  assumed=report.word(ClaimStatus.ASSERTED)))
        return 0
    counts = " · ".join(bit["label"] for bit in report.count_bits(
        {c.id: composed[c.id] for c in waiting}))
    # "Propose a solver" only when an automated claim is listed: a physical
    # test, a judgment or an assumption is settled by its mechanism, and the
    # extension protocol never fires there (review of phase 1).
    footer = said["footer"] if any(not claims.terminal_of(c) for c in waiting) \
        else said["footer_settled"]
    _say(footer.format(n=len(waiting), claims=report._plural(len(waiting), "claim"),
                       counts=counts))
    return 0


# --------------------------------------------------------------------------- #
# gates
# --------------------------------------------------------------------------- #
def _gate_row(spec: Any, registry: gates.Registry | None = None) -> dict[str, Any]:
    """One gate as JSON, without its prose. The default shape of `gate list --json`.

    Everything here answers a question a caller can act on: what it is, what it
    costs, where it came from, what it settles, what it needs installed, and —
    non-negotiably — the fixture of its negative control, because a gate with no
    demonstrable failure is a logger and the listing must not hide that. What is
    left out is `description` and the control's `note`, which are paragraphs of
    English. `--full` brings both back; `nopekit gate show <id>` prints them for
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
    # The prerequisite edge, both ways (P2.2): what this gate needs established
    # first, and which gates need it. Always present, as lists: P5's canvas
    # lays evaluators in lanes by them, and an absent key would read "unknown".
    row["needs"] = list(spec.needs or [])
    row["needed_by"] = registry.needed_by(spec.id) if registry is not None else []
    # The operating context (P2.4), always present — an empty object when none
    # is declared: a reader that finds no key must not guess "unbounded".
    row["operating_context"] = {key: list(pair) for key, pair in
                                (getattr(spec, "operating_context", None) or {}).items()}
    # SPEC 38: where its verdict's logic comes from, always present —
    # `undeclared` when neither the evaluator nor its pack says.
    row["basis"] = gates.basis_of(spec)
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
    for the one caller in a hundred that wants it, and `nopekit gate show <id>`
    has always been the way to read one gate's prose.
    """
    root = _root(args)
    ledger = _load(root)
    registry, problems = _registry(root, ledger, strict=False)
    specs = registry.by_tier(args.tier if args.tier is not None else ALL_TIERS)

    if args.json:
        _dump({"gates": [dict(spec.to_dict(), needed_by=registry.needed_by(spec.id))
                         if args.full else _gate_row(spec, registry)
                         for spec in specs],
               "full": bool(args.full),
               "summary": gates.registry_summary(registry),
               "problems": problems})
        return 0

    if not specs:
        _say("no gates registered. Install a pack (`nopekit packs`) or write "
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


#: Token kinds a crash or a broken record leaves: `last_selftest.outcome` keeps
#: calling those `error`, its meaning before P2.3 (P2.1-D12).
_ERROR_KINDS = frozenset({"known-bad:errored", "known-bad:skipped", "known-good:errored",
                          "known-good:skipped", "mutation:errored", "mutation:could-not-run",
                          "control:two-outcomes", "control:differs"})


def _last_selftest(admission: verdicts.Admission) -> dict[str, Any] | None:
    """`gate show --json`'s `last_selftest`, from how the gate's qualification
    stands at its current version (`verdicts.admission_state`), or None when
    nothing was ever recorded for it.

    `outcome` keeps the meaning it had for the known-bad half: `"pass"` — its
    control fired and it is qualified, or due to re-qualify; `"fail"` — it
    PASSED its own known-bad input; `"error"` — a control crashed, skipped
    itself or was unusable, the walk crashed or could not run, or two recorded
    outcomes disagree; and, new with P2.3, `"unqualified"` — every other reason
    it is not qualified (its known-good control failed or does not exist, the
    channels differ, a conclusive mutation passed, it is not yet qualified).
    What slipped through the design (critique of P2.3): an always-False gate, a
    known-bad-shown one and a gate whose mutation passed all read `"error"`, a
    crash's word for evaluators that crashed nothing. `qualification` (beside
    it) is the field to read. `control` is the control entry's rho (the text
    prints its first 12), `at_this_version` whether that entry is at the gate's
    current static part, `admission` the state, `detail` the reason or the
    entry's words.

    What slipped through (S-08): this read a ledger key `<gate>#selftest` that
    `gate selftest` deliberately never wrote, so every gate read "(never run)"
    forever, including the six the selftest had just demonstrated.
    """
    state, entry = admission.state, admission.entry
    kind = verdicts.parse_token(admission.reason)[0] if state == "not-admitted" else ""
    # Not yet qualified with no entry behind it is "nothing was ever recorded"
    # (P2.3): the reading that was `undemonstrated` with no entry before
    # critique 10 made a never-qualified evaluator read Gap. What slipped
    # through: a gate nothing had run showed a last selftest, `unqualified`.
    if entry is None and (state == "undemonstrated" or kind == verdicts.NOT_YET):
        return None
    if state in ("admitted", "pending"):
        outcome = "pass"
    elif state == "not-admitted" and kind == "known-bad:pass":
        outcome = "fail"
    elif state == "not-admitted":
        outcome = "error" if kind in _ERROR_KINDS else "unqualified"
    else:                                     # an entry at another version
        outcome = "pass" if entry.bad == "fail" else "fail"
    return {
        "outcome": outcome,
        "control": entry.rho if entry is not None else None,
        "at_this_version": state != "undemonstrated",
        "admission": state,
        "detail": (report.qualification_reason(admission.reason) if kind
                   else admission.reason or (entry.detail if entry is not None else "")),
    }


def _qualification_view(gate_id: str, admission: verdicts.Admission) -> dict[str, Any]:
    """`gate show --json`'s `qualification`: the state, the judge's token, the
    line and the reason in words, and the facts (P2.3, additive)."""
    facts = admission.qualification
    return {"state": admission.state,
            "token": admission.reason if admission.state == "not-admitted" else "",
            "line": report.qualification_line(gate_id, facts) if facts is not None else None,
            "reason": report.qualification_reason(admission.reason)
            if admission.state == "not-admitted" else "",
            "moved": list(admission.moved),
            "facts": facts.to_dict() if facts is not None else None}


def _held_why(admission: verdicts.Admission | None) -> list[str]:
    """The `why` row under a qualification that was remembered, never filed —
    a half that crashed, skipped itself, was unusable or read the candidate;
    a walk that could not run — whose line names each control's outcome and
    not what happened: the reason in the table's words, the crash's first line
    included. What slipped through (review of P2.3): with no entry there were
    no detail rows, so `gate show` and `gate selftest` printed `known-good
    errored · known-bad errored → unqualified` and nothing of `context()
    raised RuntimeError …`, which only the claim row carried (P2.3-D14
    rejected the reason on the line because "the claim row and `gate show`
    carry it")."""
    if (admission is None or admission.state != "not-admitted" or admission.entry is not None
            or admission.qualification is None or not admission.reason):
        return []
    d = report.HUMAN["qualification"]["detail"]
    return [f"    {d['why']:<12} {report.qualification_reason(admission.reason)}"]


def _qualification_rows(gate_id: str, spec: Any, admission: verdicts.Admission, *,
                        pruned_by: str = "") -> list[str]:
    """`gate show`'s `qualification:` row and the rows under it (P2.3-D18: it
    replaced `last selftest:`), read off the entry — it runs nothing.
    ``pruned_by``: the prerequisite root that prunes this evaluator, so a
    qualification not yet run says it runs once that root is established —
    never "the next check run", which prunes it again (review of P2.3)."""
    q = report.HUMAN["qualification"]
    state, entry, facts = admission.state, admission.entry, admission.qualification
    control = f" (control {entry.rho[:12]})" if entry is not None else ""
    prefix = f"{gate_id}{q['id_sep']}"

    def line() -> str:
        return report.qualification_line(gate_id, facts).removeprefix(prefix)

    if state == "pending" and facts is not None:
        moved = ", ".join(admission.moved) or "fixture code"
        rows = [f"  qualification: {q['pending'].format(moved=moved)}",
                f"                 {q['last'].format(line=line())}"]
    elif facts is not None:
        rows = [f"  qualification: {line()}{control}", *_held_why(admission)]
    elif state == "not-admitted":
        rows = [f"  qualification: "
                f"{report.qualification_reason(admission.reason, pruned_by=pruned_by)}"]
    elif report.unseen_qualification(admission):
        rows = [f"  qualification: {report.HUMAN['unseen']['qualification']}"]
    else:
        # With the tier that reaches it: a plain `check` is tier 0 (review of
        # issue #15's D1).
        rows = [f"  qualification: "
                f"{report.qualification_reason(_not_yet_token(spec), pruned_by=pruned_by)}"]
    return rows + report.qualification_detail(entry, spec, facts=facts)


def _not_yet_token(spec: Any) -> str:
    """`verdicts.NOT_YET` with ``spec``'s tier, as `verdicts._not_yet` writes it,
    so the words name the check run that reaches the evaluator."""
    return f"{verdicts.NOT_YET}|{int(getattr(spec, 'tier', 0) or 0)}"


def cmd_gate_show(args: argparse.Namespace) -> int:
    """Everything about one gate: what it settles, what it needs, how it last ran,
    and whether its control is demonstrated at this version.

    `last verdict` is the resolver's (`_resolved`), with why it is not current
    when it is not — an unqualified evaluator's in its qualification's words,
    never a crash's; `qualification` (and the known-good, known-bad, mutation
    and not-mutated rows under it) is read off the control entries by
    `verdicts.admission_state`, which never runs a fixture — `check` and `gate
    selftest` are the commands that spend that time.
    """
    root = _root(args)
    ledger = _load(root)
    registry, _ = _registry(root, ledger, strict=False)
    entry = registry.get(args.id)
    if entry is None:
        known = ", ".join(registry.ids()[:12]) or "(none registered)"
        raise NopekitError(f"no gate {args.id!r}. Registered: {known}")
    spec, fn = entry
    ok, reason = gates.availability(spec)
    model, projection, model_error = _projection_safe(root, ledger)
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=utcnow_iso(), model=model)
    verdict = view.verdict(spec.id)
    row = resolution.rows.get(spec.id)
    admission = verdicts.admission_state(root, spec, fn, projection=projection,
                                         ledger=ledger, anchors=resolution.anchors)

    needed_by = registry.needed_by(spec.id)
    # The evaluator's track record (P2.5a-D14): the contradictions the physical
    # results recorded against it, at this version and at earlier ones — keyed
    # by its code digest, so an edit to the evaluator starts its count again
    # while the earlier ones stay said.
    track = list((resolution.track or {}).get(spec.id) or ())
    code_now = verdicts.code_digest(spec, fn, anchors=resolution.anchors).digest
    if args.json:
        _dump({"gate": spec.to_dict(), "basis": gates.basis_of(spec),
               "needed_by": needed_by, "available": ok,
               "availability": reason,
               "last_verdict": _resolved_row(verdict, resolution) if verdict else None,
               "last_selftest": _last_selftest(admission),
               "qualification": _qualification_view(spec.id, admission),
               "track_record": [dict(c._asdict(), this_version=c.code == code_now)
                                for c in track]})
        return 0

    _say(gates.describe(spec))
    if spec.description:
        _say(textwrap.indent(spec.description, "  "))
    _say(f"  tier {int(spec.tier)}  pack {spec.pack or '(project)'}  "
         f"basis {gates.basis_of(spec)}  entry {spec.entry}")
    _say(f"  claims: {', '.join(spec.claims) or '(none — this gate settles nothing)'}")
    _say(f"  runnable here: {'yes' if ok else 'NO — ' + reason}")
    if spec.needs:
        missing = [need for need in spec.needs if need not in registry]
        _say(f"  prerequisites: {', '.join(spec.needs)}"
             + (f" ({', '.join(missing)} not registered)" if missing else ""))
    if needed_by:
        _say(f"  prerequisite of: {', '.join(needed_by)}")
    if getattr(spec, "operating_context", None):
        _say(f"  {report.context_declared(spec.operating_context)}")
    if spec.negative_control:
        _say(f"  control: {spec.negative_control.fixture} "
             f"(must {spec.negative_control.expect})")
        if spec.negative_control.note:
            _say(f"           {spec.negative_control.note}")
    else:
        _say("  control: NONE — this gate cannot be shown to fail, so it is a logger")
    stale = f" (stale: {row.stale_reason})" if row is not None and row.stale_reason else ""
    shown = (report.verdict_line(verdict, admission.qualification) if verdict
             and verdict.unqualified else verdict.render() if verdict else "(never run)")
    _say(f"  last verdict: {shown + stale if verdict else shown}")
    pruned_by = (list(verdict.blocked_by or ()) or [""])[0] if verdict else ""
    for line in _qualification_rows(spec.id, spec, admission, pruned_by=pruned_by):
        _say(line)
    _say(f"  {report.track_words(spec.id, track, code_now)}")
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


def _selftest_summary(evaluators: int, elapsed: float, qualified: int, unqualified: int,
                      skipped: int) -> str:
    """`<n> evaluators in <t>: <q> qualified, <u> unqualified, <s> skipped` — one
    spelling for both modes, and the last line of a clean run (the fresh-clone
    transcript matches it there). In qualification's words (P2.3-D18): it was
    `<n> control(s) in <t>: <f> fired, <b> BROKEN, <k> skipped (tooling)`, two
    Never-says for the known-bad half alone. *Rejected:* GLOSSARY §9's "6 fail
    as required, 0 pass (unqualified)" — written for the known-bad half alone,
    before qualification had a known-good half and a mutation pass (on the
    check-in list as a GLOSSARY edit). A skip is a missing tool's: availability
    is asked once, before either control."""
    return report.HUMAN["qualification"]["selftest_summary"].format(
        n=evaluators, t=human_duration(elapsed), q=qualified, u=unqualified, s=skipped)


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
                  anchors: verdicts.Anchors, digests: FileDigests
                  ) -> tuple[Verdict, verdicts.Admission | None]:
    """One evaluator qualified as `check` qualifies it, and the row `gate
    selftest` reports: ``(row, admission)``.

    Where the gate's tools are missing nothing runs and nothing is filed: the
    row is `gates.selftest`'s own skip, and there is no admission. Otherwise
    `verdicts.admission` with `force=True` runs both controls and, for an
    evaluator not from a bundled pack, the mutation pass, and files them
    (`record`); the row passes exactly when the evaluator is qualified, its
    detail the qualification line (JUnit's message). Its time is the wall time
    of that one call.
    """
    ok, _why = gates.availability(spec)
    if not ok:
        return gates.selftest(spec, fn, ctx), None
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
    if judged.state == "undemonstrated":
        return Verdict(passed=False, skipped=True,
                       skip_reason=judged.reason or "its tooling is not available",
                       **base), judged
    line = _selftest_line(spec.id, judged)
    return Verdict(passed=judged.state == "admitted", detail=line, **numbers, **base), judged


def _selftest_line(gate_id: str, judged: verdicts.Admission) -> str:
    """The evaluator's qualification line, or — with no facts recorded — its
    reason in the table's words."""
    if judged.qualification is not None:
        return report.qualification_line(gate_id, judged.qualification)
    q = report.HUMAN["qualification"]
    return (f"{gate_id}{q['id_sep']}{report.qualification_reason(judged.reason)} "
            f"{q['unqualified']}")


def _isolation_notes(root: str, registry: gates.Registry, spec: Any, fn: Any,
                     ctx: gates.GateContext) -> list[str]:
    """P2.2-D13's hand-off, for a project's edges (P2.3-D20): every prerequisite
    of ``spec`` must PASS ``spec``'s known-bad control, built on the host its
    control gets (the known-good design); one that does not pre-empts the
    control wherever both run. A diagnostic printed under the line — never part
    of the qualification, never cached; the words are `pack validate`'s.
    *Rejected:* inside the line or a condition of *qualified* (the line and the
    word stay GLOSSARY §2's); in `check` (a third run per dependent per miss)."""
    closure = [s for s in gates.plan(registry, [spec]) if s.id != spec.id]
    if not closure:
        return []
    out_dir = tempfile.mkdtemp(prefix="nopekit-isolation-")
    try:
        host = verdicts.known_good_context(root, ctx) or ctx
        try:
            bad = gates.run_fixture(spec, fn, host, trace=None, out_dir=out_dir,
                                    fixture_root=root)
        except NopekitError:
            return []
        notes = []
        for need in closure:
            entry = registry.get(need.id)
            if entry is None or not gates.availability(need)[0]:
                continue
            verdict = gates.run_gate(entry[0], entry[1], bad)
            if verdict.outcome != "pass":
                word = report.HUMAN["outcome"].get(verdict.outcome, verdict.outcome)
                body = ((verdict.error if verdict.outcome == "error" else
                         verdict.skip_reason if verdict.outcome == "skipped" else
                         verdict.detail) or "no reason given").splitlines()[0]
                notes.append(f"note: {spec.id}: " + report.HUMAN["qualification"][
                    "isolation"].format(need=need.id, gate=spec.id, word=word, body=body))
        return notes
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


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
    before `_root`, `_lock`, `_load` and `_projection` (cli:H8): each of them
    assumes a project, and a broken model must not stop a pack's controls.

    **Project mode files what it demonstrates** (D-07, S-08). Every selected
    control runs — fixture and gate, never served from the cache — through
    `verdicts.admission(..., force=True)`, the same code `check` runs a control
    with, so the entry it files is byte for byte the one `check` would: a
    project fixture is handed the known-good design (`selftest/known_good.py`,
    D-27), a pack's the live host; each gets its own emptied scratch under
    `.nopekit/out/controls/<gate>/`. A measurement becomes a control entry
    (`.nopekit/verdicts/<gate>/control-<rho16>-<out8>.json`, O_EXCL: an
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
        ledger = _load(root)
        registry, _ = _registry(root, ledger, strict=True)
        model, projection = _projection(root, ledger)
        # The context's tier is a real one: `ALL_TIERS` is a selection, not a
        # tier a gate can read. What slipped through (critique of P2.3): a
        # project gate that read `ctx.tier` was handed 99, and its control entry
        # was refused by its own reader (`reads.tier must be one of [0, 1, 2,
        # 3]`), so `gate selftest` at its default crashed on it.
        ctx = _context(root, ledger, model, projection, min(max_tier, int(Tier.EXTERNAL)),
                       quiet=args.json)
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
        judged: dict[str, verdicts.Admission] = {}
        for spec in specs:
            pair = registry.get(spec.id)
            if pair is None:                        # pragma: no cover - defensive
                continue
            verdict, admission = _selftest_one(root, spec, pair[1], ctx,
                                               projection=projection, record=record, now=now,
                                               anchors=anchors, digests=digests)
            results.append(verdict)
            if admission is not None:
                judged[spec.id] = admission
            if not args.json:
                if verdict.skipped:
                    _say(f"{spec.id}{report.HUMAN['qualification']['id_sep']}"
                         f"{report.HUMAN['outcome']['skipped']}: {verdict.skip_reason}")
                else:
                    _say(verdict.detail)
                    for row in _held_why(admission):
                        _say(row)
                    for note in _isolation_notes(root, registry, spec, pair[1], ctx):
                        _say(note)
        elapsed = time.perf_counter() - started
        installed = packs.installed(root, ledger=ledger)

    skipped = [v for v in results if v.skipped]
    qualified = [v for v in results if v.ok]
    unqualified = [v for v in results if not v.ok and not v.skipped]
    code = _selftest_code(broken=len(unqualified), baselines_failed=0,
                          exercised=len(qualified) + len(unqualified),
                          allow_empty=args.allow_empty)
    written = _junit_write(junit, lambda: report.render_selftest_junit(
        results, exit_code=code, when=now))

    if args.json:
        _dump({"mode": "project",
               "packs": [_pack_row(name, root) for name in installed],
               "selftests": [_verdict_row(v) for v in results],
               "baselines": None,
               # `broken`, `skipped` and their counts keep 860ffa6's meaning and
               # spelling (P2.1-D12): every control row that does not pass —
               # what fails the run — and every skipped one, by its
               # `<gate>#selftest` id; `fired` is the rest. What slipped through
               # (review of P2.3): P2.3 narrowed `broken` to the known-bad half
               # and bared its ids, so a consumer matching
               # `bracket.x#selftest` matched nothing, and a gate whose
               # known-good control failed failed the run while absent from
               # `broken`. The paired view is the new keys alone.
               "broken": [v.gate for v in unqualified],
               "skipped": [v.gate for v in skipped],
               "unqualified": [_bare(v.gate) for v in unqualified],
               "qualifications": [_qualification_view(gid, a) for gid, a in judged.items()],
               "counts": {"controls": len(results), "fired": len(qualified),
                          "broken": len(unqualified), "skipped": len(skipped),
                          "qualified": len(qualified), "unqualified": len(unqualified)},
               "allow_empty": bool(args.allow_empty),
               "junit": written,
               "ok": code == 0})
        return code

    _say(_selftest_summary(len(results), elapsed, len(qualified), len(unqualified),
                           len(skipped)))
    if len(qualified) + len(unqualified) == 0:
        _say_empty(args.allow_empty,
                   "no gates registered" if not results else "every control skipped")
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
      directly inside it (`--pack packs/`, `--pack ~/.nopekit/packs`). A
      directory holding none is a note and zero targets, which the exit code then
      refuses unless `--allow-empty`.
    * `--pack NAME` — resolved like a project resolves it, but with
      `$NOPEKIT_PACK_PATH` and `~/.nopekit/packs` searched only under
      `--user-packs`; inside a project its `.nopekit/packs/` is searched too.
    * neither — every bundled pack, plus the env and user packs under
      `--user-packs`, first-found-wins by directory name as discovery resolves it.

    What slipped through without the switch (S-87): the env and user entries
    outrank the bundled packs, so on a pack author's machine a same-named copy in
    `~/.nopekit/packs` would have been the one demonstrated while every line
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
                raise NopekitError(f"--pack {value}: no such directory")
            found = packs.find(value, project, include_env=include, include_user=include)
            if found is None:
                looked = packs.search_paths(project, existing_only=False,
                                            include_env=include, include_user=include)
                where = "\n  ".join(looked) or "(no search paths)"
                extra = "" if include else (
                    f"\n($NOPEKIT_PACK_PATH and ~/.nopekit/packs are searched only "
                    f"with --user-packs)")
                raise NopekitError(f"no pack named {value!r} — searched:\n  {where}{extra}")
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
        except NopekitError as exc:
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
            except NopekitError:
                continue              # no hit in THIS pack; another may have it
            if picked:
                hit.add(pattern)
                chosen.update(s.id for s in picked)
        plan["chosen"] = [s.id for s in plan["specs"] if s.id in chosen]

    unmatched = [p for p in selection if p not in hit]
    if unmatched and every_pack_loaded:
        # A pack that did not load might have held the gate, so only a complete
        # picture may refuse the name; otherwise the load failure is reported.
        raise NopekitError(
            f"no gate in {', '.join(n for n, _ in targets) or 'no pack'} matches "
            f"{', '.join(repr(u) for u in unmatched)} — running zero controls and "
            f"calling it a clean selftest is the failure this command exists to prevent")

    # Pass 2: demonstrate, then read the result back per chosen gate.
    controls: list[Verdict] = []
    baselines: list[Verdict] = []
    failed_rows: list[tuple[str, str]] = []      # (label, why): each pack-level problem
    with tempfile.TemporaryDirectory(prefix="nopekit-selftest-",
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
                # An isolation check its prerequisite's tools left unrun: said,
                # never dropped (review of P2.2; `Demonstration.unchecked`).
                notes.extend(entry for entry in shown.unchecked
                             if entry.partition(": ")[0] in plan["chosen"])
            for problem in plan["problems"]:
                failed_rows.append((plan["name"], problem))
            controls.extend(plan.get("controls", []))
            baselines.extend(plan.get("baselines", []))
    elapsed = time.perf_counter() - started
    now = utcnow_iso()

    qualified = [v for v in controls if v.outcome == "pass"]
    unqualified = [v for v in controls if v.outcome in ("fail", "error")]
    skipped = [v for v in controls if v.outcome == "skipped"
               and not v.skip_reason.startswith(_NOT_RUN)]
    code = _selftest_code(broken=len(unqualified), baselines_failed=len(failed_rows),
                          exercised=len(qualified) + len(unqualified),
                          allow_empty=args.allow_empty)
    written = _junit_write(junit, lambda: report.render_selftest_junit(
        controls, exit_code=code, when=now, baselines=baselines))

    if args.json:
        _dump({"mode": "pack",
               "tier": ceiling,
               "packs": [{"name": p["name"], "dir": p["dir"], "origin": p["origin"],
                          "gates": list(p["chosen"]),
                          "fired": sum(1 for v in p.get("fired", [])),
                          "qualified": sum(1 for v in p.get("controls", []) if v.ok),
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
               "broken": [_bare(v.gate) for v in unqualified],
               "skipped": [_bare(v.gate) for v in skipped],
               "baselines_failed": [v.gate for v in baselines if v.outcome in ("fail", "error")],
               # Every evaluator's line (critique of the P2.3 design: in pack mode
               # a qualified evaluator's line was printed nowhere, so "54 read
               # the line" could not be observed): here, and under `-v` in text.
               "qualifications": [q for p in plans for q in p.get("qualifications", [])],
               "counts": {"controls": len(qualified) + len(unqualified) + len(skipped),
                          "fired": sum(len(p.get("fired", [])) for p in plans),
                          "broken": len(unqualified), "skipped": len(skipped),
                          "qualified": len(qualified), "unqualified": len(unqualified),
                          "baselines_failed": sum(1 for v in baselines
                                                  if v.outcome in ("fail", "error"))},
               "allow_empty": bool(args.allow_empty),
               "junit": written,
               "ok": code == 0})
        return code

    for note in notes:
        _say(f"note: {note}")
    verbose = bool(getattr(args, "verbose", False))
    for plan in plans:
        line = _pack_line(plan, filtered=bool(selection), ceiling=ceiling)
        if line:
            _say(line)
        for q in plan.get("qualifications", []):
            if verbose or q["token"]:
                _say(q["line"])
        # A qualification's own problem restates its line, which is printed.
        lead = report.unqualified_text("")
        for problem in plan["problems"]:
            if not problem.startswith(tuple(f"{q['gate']}: {lead}"
                                            for q in plan.get("qualifications", []))):
                _say(f"problem: {problem}")
    _say(_selftest_summary(len(qualified) + len(unqualified) + len(skipped), elapsed,
                           len(qualified), len(unqualified), len(skipped)))
    if not qualified and not unqualified:
        _say_empty(args.allow_empty, "no pack to demonstrate" if not targets
                   else "every control skipped" if skipped else "no gate selected")
    return code


def _read_back(plan: dict[str, Any], shown: Any, tier: int) -> None:
    """Turn one `packs.demonstrate` result into per-gate qualification rows.

    `demonstrate` judges each gate in scope (`Demonstration.qualifications`:
    its facts, which `verdicts._qualification` decides), lists every defect as
    `"<gate id>: <why>"` — a qualification that does not hold, an unsealed or
    unisolated control, a pack-level defect naming no gate — `"<gate id>
    (<reason>)"` per tooling skip, and a count of controls run. An evaluator is
    qualified here when the judge says so AND no problem names it (an unsealed
    control is a pack defect whatever it qualified on). Sets
    `plan["controls"]` (one row per gate, passing iff qualified, its detail the
    line), `plan["baselines"]` (the known-good half, for JUnit's suite),
    `plan["qualifications"]` and `plan["fired"]`, and appends to
    `plan["problems"]`.
    """
    name = plan["name"]
    in_scope = [s for s in plan["specs"] if int(s.tier) <= tier]
    ids = {s.id for s in in_scope}
    named: dict[str, list[str]] = {}
    for line in shown.problems:
        gate_id, sep, why = line.partition(": ")
        if sep and gate_id in ids:
            named.setdefault(gate_id, []).append(line)
        plan["problems"].append(line)
    tooling: dict[str, str] = {}
    for entry in shown.skipped:
        gate_id, sep, reason = entry.partition(" (")
        if sep and gate_id in ids:
            tooling[gate_id] = reason[:-1] if reason.endswith(")") else reason
        else:
            plan["problems"].append(f"skipped an unknown gate: {entry}")

    # The counts must add up before a single evaluator is called qualified.
    # Every gate in scope either skipped for tooling or ran its controls; a
    # demonstration that stopped early (no baseline file) or dropped a gate ran
    # fewer.
    expected = len(ids) - len(tooling)
    complete = shown.ran == expected
    if not complete and not plan["problems"]:
        plan["problems"].append(
            f"the demonstration ran {shown.ran} control(s) of the {expected} this "
            f"pack's gates call for — none of its evaluators is counted as qualified")

    controls: list[Verdict] = []
    baselines: list[Verdict] = []
    qualifications: list[dict] = []
    fired: list[str] = []
    for spec in in_scope:
        if spec.id not in plan["chosen"]:
            continue
        common = {"pack": spec.pack or name, "tier": Tier(int(spec.tier))}
        facts = shown.qualifications.get(spec.id)
        if spec.id in tooling:
            controls.append(Verdict(gate=f"{spec.id}#selftest", skipped=True,
                                    skip_reason=tooling[spec.id], **common))
            baselines.append(Verdict(gate=spec.id, skipped=True, skip_reason=tooling[spec.id],
                                     **common))
            continue
        if facts is None or not complete:
            reason = f"{_NOT_RUN}{name} could not be demonstrated"
            controls.append(Verdict(gate=f"{spec.id}#selftest", skipped=True,
                                    skip_reason=reason, **common))
            baselines.append(Verdict(gate=spec.id, skipped=True, skip_reason=reason, **common))
            continue
        token = verdicts._qualification(facts)
        line = report.qualification_line(spec.id, facts)
        ok = not token and not named.get(spec.id)
        if ok:
            qualifications.append({"gate": spec.id, "pack": name, "line": line, "token": ""})
        else:
            q = report.HUMAN["qualification"]
            shown_line = line if token else (line.rsplit(" ", 2)[0] + " " + q["unqualified"]
                                             if line.endswith(q["qualified"]) else line)
            qualifications.append({"gate": spec.id, "pack": name, "line": shown_line,
                                   "token": token or "problem"})
        if facts.known_bad == "fail":
            fired.append(spec.id)
        controls.append(Verdict(gate=f"{spec.id}#selftest", passed=ok, detail=line,
                                **common))
        # The known-good half in the table's words — never the raw outcome
        # token (`not-run`, `live`; review of P2.3).
        q = report.HUMAN["qualification"]
        baselines.append(Verdict(gate=spec.id, passed=facts.known_good == "pass",
                                 detail=f"{q['known_good']} "
                                        f"{q['outcome'].get(facts.known_good, facts.known_good)}",
                                 **common))
    plan["controls"] = controls
    plan["baselines"] = baselines
    plan["qualifications"] = qualifications
    plan["fired"] = fired


def _pack_line(plan: dict[str, Any], *, filtered: bool, ceiling: int) -> str:
    """One pack's row: `beam-analytic (bundled) : 8 qualified` (P2.3-D18).

    `(origin)` because a pack that is not the one you are editing looks exactly
    like one that is (`packs.origin_of`). Nothing for a pack a gate filter left
    untouched: it was not part of this run. No outcome tag: a qualification is
    not an outcome (GLOSSARY §1), and `[FAIL]` named three things at once.
    """
    controls = plan.get("controls", [])
    problems = plan["problems"]
    q = report.HUMAN["qualification"]
    if not controls and not problems:
        if filtered:
            return ""
        what = "no gates" if not plan["specs"] else f"no gate at or below tier {ceiling}"
        return f"{plan['name']} ({plan['origin']}) : {what}"
    qualified = sum(1 for v in controls if v.outcome == "pass")
    unqualified = sum(1 for v in controls if v.outcome in ("fail", "error"))
    tooling = sum(1 for v in controls if v.outcome == "skipped"
                  and not v.skip_reason.startswith(_NOT_RUN))
    line = q["pack_row"].format(pack=plan["name"], origin=plan["origin"], q=qualified)
    if unqualified:
        line += q["pack_unqualified"].format(u=unqualified)
    if tooling:
        line += q["pack_skipped"].format(s=tooling)
    return line


# --------------------------------------------------------------------------- #
# report / why / decide
# --------------------------------------------------------------------------- #
def cmd_report(args: argparse.Namespace) -> int:
    """The readiness report: what is proven, what is not, and why.

    Prints the markdown by default because the markdown IS the deliverable — the
    thing you hand someone before they spend money. `--write` puts it at
    `REPORT.md` at the project root — an output git ignores (P2.5b-D17, PLAN
    D-14; it was the tracked `docs/readiness.md`, which drifted from its ledger,
    S-41) — writing the root ignore block's `/REPORT.md` first, so a project
    migrated before P2.5b never shows it untracked; and — whenever a decision
    exists — regenerates the decision log, `docs/decisions.md`, beside it: from
    checkpoint 1.3 `decide` writes one record and no generated document, so the
    log is an output of the command that writes outputs. `--milestone <m>`
    renders the report for one spend, as last evaluated (the package's REPORT.md
    is this, rendered from the boundary's re-run). `nopekit status` is the
    compressed terminal view of the same records.

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
    ledger = _load(root)
    registry, problems = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=utcnow_iso(), model=model)
    stale_gates = resolution.stale_gates
    stale_reasons = _stale_reasons(resolution)
    banner = _load_failure_banner(problems, model_error)
    params = _shown_params(root, ledger, model, model_error, resolution, registry,
                           projection=projection)
    # Architecture phase 3b: amber beside the Checked section — never in it.
    amber_states: dict = {}
    if model is not None:
        traced = _trace_for(root, model, projection, resolution)
        built = graph.of(root, view, resolution, registry, projection=projection, model=model,
                         views=params, bases=_input_bases(root, ledger), trace=traced.trace)
        amber_states = _amber_for(root, view, registry, built,
                                  graph.tokens(view, resolution, registry).claims, traced)
    amber_lines = [f"{cid}: {decisions.amber_text(found)}" for cid, found in
                   amber_states.items() if found.state in ("flips", "not-current")]
    milestone = None
    if getattr(args, "milestone", None):
        milestone = view.milestone(args.milestone)
        if milestone is None:
            said = report.HUMAN["export"]
            declared = [m.id for m in view.milestones]
            raise NopekitError(said["no_milestone"].format(
                name=args.milestone,
                declared=said["declared"].format(names=", ".join(declared))
                if declared else said["none_declared"]))
        if args.write:
            raise NopekitError(
                f"--write writes the project's report; the report for {milestone.id} is "
                f"written into its package by `nopekit export {milestone.id}`")

    if args.json:
        composed = claims.compositions(view, registry=registry, stale_gates=stale_gates,
                                       unseen_gates=claims.unseen_gates(resolution))
        extra: dict[str, Any] = {}
        if milestone is not None:
            # The milestone's own predicate (review of P2.5b, finding 10: the
            # JSON was the project's document — `ready` false beside a page
            # saying ready for the milestone), as last evaluated.
            found = claims.unresolved(view, composed, milestone)
            extra["milestone"] = {
                "name": milestone.id, "ready": found.ready, "last_evaluated": True,
                "required": list(claims.required_ids(view, milestone)),
                "unresolved": [{"id": c.id, "status": str(composed[c.id].status),
                                "cause": str(composed[c.id].cause.value)}
                               for c in found.unresolved],
                "missing": list(found.missing)}
        _dump({
            **extra,
            "summary": claims.summarise(view, registry, stale_gates=stale_gates),
            "claims": {cid: str(c.status) for cid, c in composed.items()},
            "statuses": _status_views(view, composed, stale_reasons),
            "errored": [cid for cid, c in composed.items() if c.errored],
            "coverage": claims.coverage(view, registry),
            "gaps": [need.to_dict() for need in claims.find_gaps(view, registry)],
            "verdicts": [_resolved_row(v, resolution) for v in view.verdicts],
            "stale": bool(stale_gates),
            "stale_reason": _stale_summary(resolution),
            "stale_gates": _stale_gate_list(resolution),
            "problems": problems,
            "model_error": model_error,
            "coverage_understated": bool(banner),
            # P2.5a-D16: the rebuild prediction, additive.
            "rebuild": [found.to_dict() for found in claims.rebuild(view)],
            # SPEC decision 12, additive: as `status --json` holds them.
            "requirements": _requirement_rows(view, composed, registry),
            # Architecture phase 3b, additive: amber, a display token beside
            # the statuses above.
            "amber": {cid: {"state": a.state, "why": a.why} for cid, a in
                      amber_states.items()},
        })
        return 0

    if args.write:
        with _lock(root):
            # The ignore line before the file (D17): a project migrated before
            # P2.5b gains `/REPORT.md` and `/out/` inside its marked block.
            store.ensure_ignore_blocks(root)
            path = report.write_report(root, view, registry, stale_gates=stale_gates,
                                       model_error=model_error, params=params,
                                       stale_reasons=stale_reasons, amber=amber_lines,
                                       unseen_gates=claims.unseen_gates(resolution))
            if view.decisions:
                decisions.write_log(root, view)
        _say(rel(path, root))
        # To stderr, because the one line on stdout is the path and scripts read
        # it. A caveat that breaks `report --write` as a shell substitution would
        # get silenced by whoever hit it, which is the opposite of the point.
        for line in banner:
            _warn(line)
        return 0
    sys.stdout.write(_with_banner(
        report.render_markdown(view, registry, stale_gates=stale_gates,
                               model_error=model_error, root=root, params=params,
                               stale_reasons=stale_reasons, milestone=milestone,
                               amber=amber_lines,
                               unseen_gates=claims.unseen_gates(resolution)), banner))
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
             + ", so coverage below is UNDERSTATED: a claim may read "
             + report.words(ClaimStatus.UNCLAIMED).term
             + " because its gate never registered, not because no gate exists."]
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

    A parameter is read from the model (`modelio.param_view`: the value, and
    where it lives), and the gates that protect it from their last executed
    runs (`verdicts.last_read_sets`), never from a record. From checkpoint 1.3
    a parameter the model states entirely HAS no record — `check` stopped
    copying the model into the records, and the migration writes a param record
    only for what the model cannot hold — so a `why` that looked for one
    answered "no parameter named thickness" about the number the bracket's
    failing claim turns on; with the model mid-edit it still names it, and says
    the model does not load instead of a number (`_param_views`). What grounds
    it is derived from the extractions, the map `inputs` reads (`artifacts.value_grounding`,
    S-36). All of it through `_why_text`, which `claim show` shares.
    """
    root = _root(args)
    ledger = _load(root)
    registry, _problems = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=utcnow_iso(), model=model)
    text = _why_text(root, ledger, registry, model, model_error, view, resolution,
                     args.name, projection=projection)
    if args.json:
        _dump({"name": args.name, "why": text})
        return 0
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    """Record a decision — including, above all, what LOST and why.

    `--rejected "4.0 mm|it deflected 3.75 mm, 7.5x the limit"` is the highest-value
    thing this whole system stores. Without it every fresh context window
    re-proposes every settled number. A rejection with no reason is refused by
    `decisions.add`, which is why the flag takes a pair.

    A shim (spec §3.15): under the lock it migrates a legacy ledger first, then
    writes exactly one record, `decisions/<slug>.json`. It no longer regenerates
    `docs/decisions.md` — a second file per decision, and a generated one; the log
    is an output of `report --write` whenever a decision exists.

    `when` is the clock read here and nothing else. What slipped through (S-44):
    a `--when` flag backdated a decision, and since the log renders in storage
    order a backdated entry sat on top as the newest — the one command whose
    record says WHEN something was decided let the caller say it. Removed; the
    one caller-stated time left is `claim physical --when`, a result observed
    before it was typed in, until the signed result of P2.5 (D-12).
    """
    root = _root(args)
    now = utcnow_iso()
    rejected = _rejection_items(getattr(args, "rejection_parts", None) or ())

    with _lock(root):
        ledger = _migrate(root, apply=True, now=now)
        _refuse_dangling_rejections(ledger, rejected)
        decision = decisions.add(
            ledger,
            title=args.title,
            summary=args.summary,
            when=now,
            rejected=rejected,
            params_changed=_collect(args.param),
            claims_changed=_collect(args.claim),
            body=args.body or "",
            evidence=_collect(args.evidence),
        )
        path = store.write_record(root, "decisions", decision) or os.path.join(
            root, "decisions", f"{decision.id}.json")

    if args.json:
        _dump(dict(decision.to_dict(), record=rel(path, root)))
        return 0
    _say(f"{decision.id}  {decision.title}  ({decision.when})")
    for item in decision.rejected:
        _say(f"  rejected {item.value}: {item.why}{report.rejection_suffix(item)}")
    # A preference is a person's (SPEC 14, invariant 13): `by` names a nominee,
    # and only that person's own `prefer` records it.
    preferred = [item for item in decision.rejected
                 if getattr(item, "type", "") == "preferred_against"]
    for item in preferred:
        line = report.HUMAN["rejection"]["records_it"].format(
            name=report._one(item.by), id=decision.id)
        if len(preferred) > 1:
            line += f" --value {shlex.quote(item.value)}"
        _say(f"  {line}")
    _say(f"  record: {rel(path, root)}")
    return 0


#: `decide --returns-if`'s grammar: `<claim> margin|value|limit <op> <number>`.
_RETURNS_IF = re.compile(r"^\s*(\S+)\s+(margin|value|limit)\s*(<=|>=|<|>)\s*(\S+)\s*$")


class _RejectedFlag(argparse.Action):
    """`--rejected VALUE|WHY[|EVIDENCE]`: opens one rejection in the ordered list
    the part-flags after it apply to (`_RejectionPart`)."""

    def __call__(self, parser: Any, namespace: Any, values: Any, option_string: Any = None
                 ) -> None:
        parts = list(getattr(namespace, self.dest, None) or [])
        parts.append(("rejected", option_string, values))
        setattr(namespace, self.dest, parts)


class _RejectionPart(argparse.Action):
    """`--type`, `--by`, `--returns-if`, `--returns-text`, `--input`: each applies
    to the `--rejected` before it, in the one ordered list `cmd_decide` reads —
    so a flag can never attach to the wrong loser by argparse's own grouping.
    *Rejected:* parallel `append` lists (the fifth `--type` would pair with
    whichever `--rejected` came fifth, whatever the order typed)."""

    def __call__(self, parser: Any, namespace: Any, values: Any, option_string: Any = None
                 ) -> None:
        parts = list(getattr(namespace, self.dest, None) or [])
        parts.append(("part", option_string, values))
        setattr(namespace, self.dest, parts)


def _rejection_items(parts: Iterable[tuple]) -> list[Any]:
    """`decide`'s rejections from its ordered flags (SPEC 14), each refused
    before anything is read with the reason and "Nothing was written."."""
    said = report.HUMAN["decide"]
    entries: list[dict[str, Any]] = []
    for kind, flag, value in parts:
        if kind == "rejected":
            entries.append({"raw": value, "flags": {}})
            continue
        if not entries:
            raise NopekitError(said["part_first"].format(flag=flag))
        entry = entries[-1]
        shown = report._one(str(entry["raw"]).split("|", 1)[0].strip())
        if flag in entry["flags"]:
            raise NopekitError(said["part_twice"].format(flag=flag, value=shown))
        entry["flags"][flag] = value
    items: list[Any] = []
    for entry in entries:
        raw = entry["raw"]
        pieces = [piece.strip() for piece in str(raw).split("|")]
        flags = entry["flags"]
        if len(pieces) < 2:
            # A single-piece value is passed through as a string so
            # `decisions.add` raises its own sentence about reasonless
            # rejections, which says more than anything this parser could.
            items.append(raw)
            continue
        value = pieces[0]
        item: dict[str, Any] = {"value": value, "why": pieces[1]}
        if len(pieces) > 2:
            item["evidence"] = "|".join(pieces[2:])
        kind = flags.get("--type")
        if kind is not None:
            if kind not in REJECTION_TYPES:
                close = difflib.get_close_matches(str(kind), REJECTION_TYPES, n=1, cutoff=0.6)
                raise NopekitError(said["bad_type"].format(
                    v=kind, guess=f' (did you mean "{close[0]}"?)' if close else ""))
            item["type"] = kind
        if "--returns-if" in flags and "--returns-text" in flags:
            raise NopekitError(said["both"].format(value=value))
        if "--returns-if" in flags:
            found = _RETURNS_IF.match(flags["--returns-if"])
            number = None
            if found:
                try:
                    number = float(found.group(4))
                except ValueError:
                    number = None
            if not found or number is None or not math.isfinite(number):
                raise NopekitError(said["grammar"].format(v=flags["--returns-if"]))
            item["returns_if"] = {"claim": found.group(1), "quantity": found.group(2),
                                  "comparator": found.group(3), "value": number}
        if "--returns-text" in flags:
            item["returns_if"] = {"text": flags["--returns-text"]}
        if "--by" in flags:
            if kind != "preferred_against":
                raise NopekitError(said["by_needs_type"].format(value=value))
            item["by"] = flags["--by"]
        elif kind == "preferred_against":
            raise NopekitError(said["preferred_needs_by"])
        if "--input" in flags:
            item["input"] = flags["--input"]
        items.append(item)
    return items


def cmd_prefer(args: argparse.Namespace) -> int:
    """The person's channel for a preference (SPEC 14, invariant 13): `nopekit
    prefer <decision> [--value VALUE]`, the shape of `claim physical`'s owner
    path. What it closes before it could slip through: `decide --rejected
    VALUE|WHY` records no who, so a preference the agent typed into a decision
    would have read as the person's — invariant 11's owner laundering, through
    another file.

    In order, refused with nothing written: a refused flag (`--who`, `--when`,
    before the project is read); a value that is not text; the decision; a
    preferred_against rejection in it; git's identity (`who`); an agent session
    or a pipe — refused outright, never recorded as counting for nothing; the
    rejections whose `by` names this identity; `--value` among them, or exactly
    one not yet recorded. Then the prompt, the decision's id typed by the
    person, and one sealed entry appended (`store.append_sealed`). A repeat
    writes nothing and exits 0. Under the lock it migrates a legacy ledger first
    and writes `preferences/<decision>.json` and nothing else; outside the lock
    it re-reads and prints the rejection as `claims.preference_state` composes
    it — never computed from the entry it wrote.
    """
    said = report.HUMAN["prefer"]
    signing = report.HUMAN["signing"]
    for flag, key in REFUSED_FLAGS.items():
        if getattr(args, key, None) is not None:
            raise NopekitError(said[key])
    for flag, value in (("<decision>", args.id), ("--value", args.value)):
        if value is not None and _not_text(value):
            raise NopekitError(signing["not_text"].format(flag=flag))
    root = _root(args)
    now = utcnow_iso()
    environ = dict(os.environ)
    isatty = bool(sys.stdin and sys.stdin.isatty())
    with _lock(root):
        ledger = _migrate(root, apply=True, now=now)
        decision = next((d for d in ledger.decisions if d.id == args.id), None)
        if decision is None:
            raise NopekitError(said["no_decision"].format(id=args.id))
        preferred = [(i, item) for i, item in enumerate(decision.rejected or ())
                     if getattr(item, "type", "") == "preferred_against"]
        if not preferred:
            raise NopekitError(said["none_preferred"].format(id=decision.id))
        who = vcs.ident(root)
        if not who:
            raise NopekitError(signing["no_identity"])
        names = ", ".join(dict.fromkeys(report._one(item.by) for _i, item in preferred))
        marker = _agent_marker(environ)
        if marker or not isatty:
            why = (signing["marker"].format(marker=marker) if marker
                   else signing["assume_pipe"])
            raise NopekitError(said["channel"].format(why=why, names=names, id=decision.id))
        mine = [(i, item) for i, item in preferred if claims.identity_matches(who, item.by)]
        if not mine:
            raise NopekitError(said["not_by"].format(id=decision.id, names=names, who=who))
        if args.value is not None:
            chosen = [(i, item) for i, item in mine if item.value == args.value]
            if not chosen:
                raise NopekitError(said["no_value"].format(
                    id=decision.id, value=args.value,
                    values=", ".join(repr(item.value) for _i, item in mine)))
            index, item = chosen[0]
            state = claims.preference_state(decision, item)
            if state is not None and state.state == "recorded":
                _say(said["already"].format(name=report._one(state.name), value=item.value,
                                            when=state.when))
                return 0
        else:
            open_ = [(i, item) for i, item in mine
                     if getattr(claims.preference_state(decision, item), "state",
                                "") != "recorded"]
            if not open_:
                index, item = mine[0]
                state = claims.preference_state(decision, item)
                _say(said["already"].format(name=report._one(item.by), value=item.value,
                                            when=getattr(state, "when", "")))
                return 0
            if len(open_) > 1:
                raise NopekitError(said["which"].format(
                    id=decision.id, n=len(open_),
                    values=", ".join(repr(it.value) for _i, it in open_)))
            index, item = open_[0]
        rows = [f"{decision.id} {report._one(decision.title)}",
                "  " + said["row"].format(value=item.value, why=report._one(item.why))]
        if item.returns_if is not None:
            rows.append("  " + said["returns_row"].format(
                condition=report.condition_text(item.returns_if)))
        rows.append("  " + signing["recorded_by"].format(who=report._one(who)))
        answer = _confirm(decision.id, rows)
        channel = _channel(isatty, environ, answer, decision.id)
        entry = {"decision": decision.id, "value": item.value,
                 "name": claims.name_of(item.by),
                 "rejection_digest": claims.rejection_digest(decision, item),
                 "who": who, "when": now, "channel": channel}
        store.append_sealed(root, "preferences", decision.id, "preferences", entry)
        path = os.path.join(root, "preferences", f"{decision.id}.json")
        n = len(store.read_record(path, "preferences").raw)

    # Read back, outside the lock: the rejection as every reader composes it,
    # on the view `status` judges.
    ledger = _load(root)
    registry, _problems = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=now, model=model)
    composed = claims.compositions(view, registry=registry,
                                   stale_gates=resolution.stale_gates,
                                   unseen_gates=claims.unseen_gates(resolution))
    row = next(r for r in claims.rejection_rows(view, composed)
               if r.decision == args.id and r.index == index)
    ledger = view
    decision = next(d for d in view.decisions if d.id == args.id)
    if args.json:
        _dump({"decision": decision.id,
               "recorded": {"who": who, "when": now, "channel": channel, "value": item.value},
               "entry": n, "record": f"preferences/{decision.id}.json",
               "rejection": row.to_dict()})
        return 0
    _say(said["recorded"].format(name=report._one(item.by), value=item.value,
                                 id=decision.id, n=n))
    _say(f"  {report._one(row.value)} — {report.rejection_facts(row, ledger)}")
    return 0


def _refuse_dangling_rejections(ledger: Ledger, items: Iterable[Any]) -> None:
    """`decide` refuses a return condition on no claim and an input that is no
    record, at write time (the reader reads both, R-10; `doctor` warns)."""
    said = report.HUMAN["decide"]
    claim_ids = {c.id for c in ledger.claims}
    input_ids = [a.id for a in ledger.inputs]
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = (item.get("returns_if") or {}).get("claim")
        if cid is not None and cid not in claim_ids:
            raise NopekitError(said["no_claim"].format(cid=cid))
        aid = item.get("input")
        if aid is not None and aid not in input_ids:
            close = difflib.get_close_matches(str(aid), input_ids, n=3, cutoff=0.6)
            raise NopekitError(said["no_input"].format(
                aid=aid, close=f" — close: {', '.join(close)}" if close else ""))


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
    ledger = _load(root)
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
                 f"— `nopekit packs show {args.name} --ref <name>`")
        _say("")
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def cmd_packs_skills(args: argparse.Namespace) -> int:
    """The live packs' PACK.md, lenses and skill files (architecture phase 6,
    SPEC 37): each ``skills/<name>/SKILL.md`` a pack holds, printed whole after
    its PACK.md and its ``lenses.md`` (the review dimensions SPEC §0.1's agent
    reads to find the claims it missed — no other command printed them, review
    of phase 6), for the agent to read before it works in that domain. Read, never
    installed: a skill installed into Claude Code would be there in every
    project, live pack or not. The packs are ``--pack`` names, else the
    project's live packs (`packs.installed`) that are found here, in project
    order. A live pack that is not found is a FAIL line and exit 1, as `packs
    list` says it."""
    root = _root(args)
    named = [str(n).strip() for n in (getattr(args, "pack", None) or []) if str(n).strip()]
    names = named or packs.installed(root, ledger=_load(root))
    found: list[tuple[str, str]] = []
    missing: list[str] = []
    for name in names:
        directory = packs.find(name, root)
        if directory:
            found.append((name, directory))
        else:
            missing.append(name)
    rows = []
    for name, directory in found:
        doc_path = os.path.join(directory, packs.DOC_NAME)
        doc = packs.pack_doc(name, root) if os.path.isfile(doc_path) else ""
        lenses_path = os.path.join(directory, packs.LENSES_NAME)
        lenses = (packs._read_text(lenses_path, f"{name} {packs.LENSES_NAME}")
                  if os.path.isfile(lenses_path) else "")
        rows.append({"name": name, "dir": directory,
                     "pack_md": {"path": doc_path, "text": doc},
                     "lenses": {"path": lenses_path, "text": lenses} if lenses else None,
                     "skills": [{"name": skill, "path": path,
                                 "text": packs._read_text(path, f"{name} skill {skill}")}
                                for skill, path in packs.skill_files(directory)]})
    if args.json:
        _dump({"packs": rows, "missing": missing})
        return 1 if missing else 0
    if not rows and not missing:
        _say("no live packs in this project — `nopekit packs list` shows what exists, "
             "`nopekit packs add <name>` makes one live")
        return 0
    for row in rows:
        if row["pack_md"]["text"]:
            _say(f"== {row['name']}/{packs.DOC_NAME}  ({row['pack_md']['path']})")
            text = row["pack_md"]["text"]
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
        if row["lenses"]:
            _say(f"== {row['name']}/{packs.LENSES_NAME}  ({row['lenses']['path']})")
            text = row["lenses"]["text"]
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
        for skill in row["skills"]:
            _say(f"== {row['name']}/{packs.SKILLS_DIR}/{skill['name']}/{packs.SKILL_NAME}  "
                 f"({skill['path']})")
            text = skill["text"]
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
    for name in missing:
        _say(f"{_tag('FAIL')} {name} is not on any search path" if named else
             f"{_tag('FAIL')} {name} is in meta.packs but was not found on any search path")
    return 1 if missing else 0


def cmd_packs_validate(args: argparse.Namespace) -> int:
    """Run the same checks CI runs on a pack. Exit 1 if anything is wrong.

    Accepts a pack name or a directory, so a pack being written in place can be
    validated before it is anywhere a search path will find it.
    """
    root = store.find_root(getattr(args, "dir", None))
    target = args.name
    pack_dir = target if os.path.isdir(target) else (packs.find(target, root) or "")
    if not pack_dir:
        raise NopekitError(f"no pack {target!r} on any search path, and no such directory")
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
    """Opt this project into a pack: append it to `project.json`'s `packs`.

    Installed is a project decision, not a filesystem accident — a pack sitting
    in `~/.nopekit/packs` is available to every project on the machine and must
    not start contributing gates to this one until the project says so. The pack
    is loaded immediately, into a fresh registry, so a broken one fails here,
    where the user is looking, rather than in the middle of the next `check` —
    and before anything is written.

    A shim (spec §3.15): under the lock it migrates a legacy ledger first, then
    writes exactly one file, `.nopekit/project.json`. There is no `packs remove`
    (PLAN A-8): opting out is deleting the name from `packs` in that file, which
    is what the command did, and its verdict entries stay in the cache either way
    — orphans that never count and that `doctor` names.
    """
    root = _root(args)
    now = utcnow_iso()
    with _lock(root):
        ledger = _migrate(root, apply=True, now=now)
        added: list[str] = []
        names = list(ledger.meta.packs or [])
        for name in args.names:
            if not packs.find(name, root):
                looked = "\n  ".join(packs.search_paths(root, existing_only=False))
                raise NopekitError(f"no pack {name!r} found. Searched:\n  {looked}")
            if name in names:
                continue
            names.append(name)
            added.append(name)
        registry = gates.Registry()
        specs = packs.load_all_gates(names, registry, root)
        store.write_project(root, dataclasses.replace(ledger.meta, packs=names))

    if args.json:
        _dump({"added": added, "packs": names,
               "gates": [spec.id for spec in specs]})
        return 0
    _say(f"packs: {', '.join(names) or '(none)'}")
    _say(f"{len(specs)} gate(s) now available: {', '.join(s.id for s in specs[:8])}")
    return 0


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #
def cmd_model(args: argparse.Namespace) -> int:
    """Load the model, project it, and say what that projection looks like.

    `--write` produces `.nopekit/model.json`, the diffable view: sorted keys,
    one value per line, so a parameter change reads as `deflection 0.70 -> 0.47`
    in `git diff` instead of as one enormous line. `--entry` projects another
    file without recording it. Which file IS the model is recorded in
    `.nopekit/project.json` (`"model_entry"`) and nowhere else — the loader
    deliberately never guesses, because "the only .py in model/" works right up
    until there are two.

    What slipped through, three times over. Plain `model` took the build lock
    and saved the whole ledger, three lines below a comment promising it did
    not — so printing the projection while a sweep was in flight contended for
    the lock, and a `model` in a loop rewrote the ledger forever. `--write`
    primed the parameter records from the model (`sync_params`): every value,
    unit and rationale copied into a second home, stale the moment the model
    moved (S-39). And `--set-entry` was a command whose only job was to write
    one key of one record (PLAN A-8 removed it: the entry is edited in the file
    like every other record). Now `model` is a read, `--write` writes the one
    output it names, and no record is touched.

    Orphans — param records whose field the model no longer defines — are read
    off the records (`modelio.orphan_params`): a parameter the model owns
    entirely has no record, and is no orphan.
    """
    root = _root(args)
    ledger = _load(root)

    model, projection = _projection(root, ledger, entry=args.entry)
    if projection is None:
        raise NopekitError(
            f"{_no_entry(root)} (the model is a dataclass CONFIG plus build(config) -> "
            f"dict); `nopekit model --entry <file>` projects one without recording it")

    digest = modelio.model_hash(projection)
    # The nag list every reader prints: over the view, so a record's rationale
    # for a field the model leaves silent counts (modelio.undefended_params).
    undocumented = modelio.undefended_params(_param_views(root, ledger, model, ""))

    written = ""
    if args.write:
        with _lock(root):
            written = modelio.write_projection(root, projection)
    orphans = modelio.orphan_params(ledger, model)

    if args.json:
        _dump({"entry": model.entry, "hash": digest, "projection": projection,
               "params": [param.to_dict() for param in model.params],
               "orphans": orphans,
               "undocumented": undocumented,
               "written": rel(written, root) if written else ""})
        return 0

    config = projection.get("config") or {}
    derived = projection.get("derived") or {}
    _say(f"model: {model.entry}  hash {digest}")
    if orphans:
        _say(f"  {len(orphans)} param record(s) the model no longer defines: "
             f"{', '.join(orphans[:6])}"
             + ("..." if len(orphans) > 6 else "")
             + "  — renamed, or dropped without a decision entry?")
    _say(f"  {len(config)} config value(s), {len(derived)} derived value(s), "
         f"{len(model.params)} param(s)")
    if undocumented:
        _say(f"  no rationale: {', '.join(undocumented)} — a number with no rationale "
             f"gets re-litigated by every fresh reader")
    if written:
        _say(f"  wrote {rel(written, root)}")
    return 0


def _round_label(root: str) -> int | None:
    """The round a foreground trace is labelled with (decision 39: "traced at a
    named round"): the round record's number when it reads ``ok``, else None.
    Its number only — never a status: nothing reads the record for truth."""
    found = rounds.read(root)
    return found.round if found.state == "ok" else None


def cmd_model_trace(args: argparse.Namespace) -> int:
    """Observe the derivation (architecture phase 3b, SPEC 39): rebuild the
    model in copies of the project with each numeric choice moved -10% and
    +10%, record which derived values moved (the trace, ``.nopekit/trace/``),
    and, for every automated claim, whether a move of an assumption-sourced
    choice flips its pass (amber). `derivation.run` does the work; this edge
    plans it from the model as loaded (never built here: PETGasus's build()
    writes its meshes, and the trace keeps every build in a copy), names the
    amber choices from the records, and says what happened.

    Writes only new tracked files under ``.nopekit/trace/`` (never rewritten,
    invariant 8) and its scratch under the ignored ``.nopekit/cache/trace/``.
    Exit 0 when written, current or partial (``--choices``); 1 when a build did
    not finish (nothing tracked was written for it); 2 for a refusal. With
    ``--from-round`` (the background run `round` starts) a trace already
    running is no error: it exits 0 and says nothing."""
    said = report.HUMAN["trace"]
    root = _root(args)
    background = getattr(args, "from_round", None) is not None
    if store.is_legacy(root):
        raise NopekitError(f"model trace: {said['refuse']['legacy']}")
    ledger = _load(root)
    entry = (ledger.meta.model_entry or "").strip()
    if not entry:
        raise NopekitError(f"model trace: {_no_entry(root)}")
    try:
        model = modelio.load_model(root, entry)
    except NopekitError as exc:
        raise NopekitError("model trace: " + said["refuse"]["no_model"].format(
            why=_first_line(exc))) from None
    plan = derivation.plan_of(model.config)
    names = [n.name for n in plan]
    wanted = None
    if args.choices:
        wanted = _collect(args.choices)
        for name in wanted:
            if name not in names:
                close = difflib.get_close_matches(name, names, n=1)
                raise NopekitError("model trace: " + said["refuse"]["unknown_choice"].format(
                    name=name, entry=entry,
                    hint=said["refuse"]["hint"].format(hint=close[0]) if close else ""))
    if args.workers is not None and args.workers < 1:
        raise NopekitError("model trace: " + said["refuse"]["workers"])
    views = modelio.param_view(ledger, model)
    amber_choices: dict[str, list[str]] = {}
    for name, found in graph.value_sources(ledger, views).items():
        aids = [nid.partition(":")[2] for nid in found if nid.startswith("claim:")]
        if aids and name in names:
            amber_choices[name] = aids
    registry, _problems = _registry(root, ledger, strict=False)
    round_ = (int(args.from_round) if int(args.from_round) > 0 else None) if background \
        else _round_label(root)
    started = time.perf_counter()
    lines: list[str] = []

    def line(text: str) -> None:
        lines.append(text)
        if not args.json:
            _say(text)

    try:
        ran = derivation.run(
            root, plan=plan, entry=entry, amber_choices=amber_choices, choices=wanted,
            workers=args.workers, timeout=args.timeout, round_=round_, now=utcnow_iso(),
            quiet_if_running=background, background=background, on_line=line,
            project_lock=lambda: _lock(root),
            pack_dirs=list((getattr(registry, "pack_dirs", None) or {}).values())
            if isinstance(getattr(registry, "pack_dirs", None), Mapping) else [])
    except NopekitError as exc:
        raise NopekitError(f"model trace: {exc}") from None
    seconds = time.perf_counter() - started
    if ran.state == "running":
        return 0
    if args.json:
        _dump({"key": ran.key, "state": ran.state, "round": ran.round,
               "builds": dict(ran.builds), "skipped": dict(ran.skipped),
               "unmoved": list(ran.unmoved), "unsteady": list(ran.unsteady),
               "written": list(ran.written),
               "amber": {"choices": list((ran.amber or {}).get("choices") or ()),
                         "flips": list((ran.amber or {}).get("flips") or ())},
               "problems": list(ran.problems)})
        return 1 if ran.state == "incomplete" else 0
    if ran.state == "incomplete":
        for problem in ran.problems:
            _say(f"{_tag('FAIL')} {problem}")
        if ran.incomplete:
            items = "; ".join(str(r[2]) for r in ran.incomplete[:4])
            _say(said["incomplete"].format(n=len(ran.incomplete), items=items,
                                           k=max(0, int(ran.builds.get("run", 0))
                                                 - len(ran.incomplete))))
            if any("timed out" in str(r[2]) for r in ran.incomplete):
                _say(said["retry"].format(suggest=max(ran.timeout * 2.0,
                                                      derivation.BUILD_TIMEOUT_FLOOR)))
        return 1
    if ran.state == "current":
        return 0
    if ran.unsteady:
        _say(said["unsteady"].format(names=", ".join(ran.unsteady[:6])))
    elif ran.state == "written" and ran.order:
        _say(said["steady"])
    if ran.state == "partial":
        _say(said["partial"].format(done=ran.observed, total=ran.total))
        return 0
    if ran.order:
        unmoved = list(ran.order.get("unmoved") or ())
        _say(said["written"].format(n=ran.observed, moved=ran.order.get("moved", 0),
                                    unmoved=len(unmoved),
                                    names=", ".join(unmoved[:6]) or "none"))
        _say(said["order"].format(d=ran.order.get("derives", 0), a=ran.order.get("after", 0),
                                  t=ran.order.get("ties", 0), s=ran.order.get("singles", 0)))
    if ran.amber and ran.amber.get("choices"):
        flips = list(ran.amber.get("flips") or ())
        _say(said["amber"].format(
            n=ran.amber.get("claims", 0), k=len(ran.amber["choices"]),
            flips=", ".join(said["amber_flip_item"].format(id=c) for c in flips)
            if flips else said["amber_none"]))
    if ran.written:
        _say(said["wrote"].format(paths=", ".join(ran.written), seconds=seconds))
    return 0


# --------------------------------------------------------------------------- #
# site
# --------------------------------------------------------------------------- #
def _load_viewgens(directory: str, registry: Any, *,
                   pack: str, root: str) -> list[Any]:
    """`site.load_viewgens`: moved there so `pack validate` (`packs.validate`)
    loads a pack's views through the same code as a build."""
    return site.load_viewgens(directory, registry, pack=pack, root=root)


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
        except NopekitError as exc:
            if strict:
                raise
            problems.append(f"{label} did not load: {exc}")
    return registry, problems


def _vendor_words(info: Mapping[str, Any]) -> str:
    """Where the page loads three.js from, in `site status`'s and `doctor`'s
    words: ``vendored``; a ``site/vendor/`` that holds only some of
    `site.vendor_urls`, each missing path named (the page finds vendor/ and
    loads from it, so it is never "from the CDN"); or the CDN."""
    if info.get("vendored"):
        return "vendored"
    missing = list(info.get("vendor_missing") or ())
    if info.get("vendor_files") and missing:
        return (f"partly vendored: site/vendor/ is missing "
                f"{', '.join(p.split('/', 1)[1] for p in missing)} (`nopekit site vendor`)")
    return "from the CDN (`nopekit site vendor`)"


def _site_state(root: str, *, resolved: tuple | None = None) -> dict:
    """What is on disk under `site/`, read-only, running no gate, fixture or
    viewgen.

    Shared by `site status`, `status` and `doctor` so the three cannot drift
    into three opinions about whether the site is current — which is the
    property the site itself exists to have.

    Staleness here is the SITE's staleness (would a rebuild now show something
    else?), which is a different question from the verdicts' staleness (did a
    gate's inputs move since its verdict?). Both are reported, separately,
    because the fixes are different commands: `nopekit site build` for the
    first and `nopekit check` for the second.

    A site is present only when `site/index.html` exists (`site.scaffolded`,
    architecture phase 5): a project that tracks only `site/content/` has text
    for a page and no page. The checks, in order, the first that fires deciding
    the stale reason: the records (`meta.records_digest`, cli:H16) and what the
    page judged (`meta.resolution_digest`, against `site.state` over
    `_resolved`'s view now — review, `repro_site`: a check that FAILed C1 moved
    no record, so all three readers called a page showing C1 PASS current); the
    front page's two files missing (an older build); a seal that fails on either
    (*edited by hand*); a token the resolution does not say while both digests
    hold (*edited by hand*: a resealed forgery, `page.status_problems`); the
    text in `site/content/` against what the page was built from. A page with a
    digest missing was built by an older spine and cannot say what it was built
    from: stale.

    `resolved` — the caller's `(view, registry, resolution, params)`, from
    `_resolved` with the registry it loaded and `_shown_params` over the model
    it loaded, so `status` and `doctor` judge the page by the resolution and the
    parameters they print. Without it this resolves for itself exactly as they
    do (strict=False, `_projection_safe`).
    """
    site_dir = os.path.join(root, site.SITE_DIR)
    data_dir = os.path.join(site_dir, site.DATA_DIR)
    state_path = os.path.join(data_dir, site.STATE_NAME)
    vendor_dir = os.path.join(site_dir, site.VENDOR_DIR)
    assets_dir = os.path.join(site_dir, site.ASSETS_DIR)

    info: dict[str, Any] = {
        "present": site.scaffolded(root),
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
        "verdicts_stale_check": "",
        "verdicts_stale_unseen": False,
        "assets": 0,
        "asset_bytes": 0,
        "vendored": False,
        "vendor_files": 0,
        "vendor_missing": [],
        "three_version": site.THREE_VERSION,
        "edited": [],
        "outline": None,
        "prose": [],
        "prose_behind": [],
        "map": {},
    }
    if not info["present"]:
        info["stale_reason"] = "no site/index.html — `nopekit site init`"
        return info
    info["outline"] = site.outline_of(root)
    # The two pages: the file the outline was read from, and whether the root
    # is a front page from before them (`site.old_layout`).
    info["outline_file"] = site.outline_file(root)
    info["old_layout"] = site.old_layout(root)

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
        # Partially vendored is NOT vendored: a vendor/ holding three of four
        # files shadows the CDN import map and 404s on the fourth.
        info["vendored"] = len(vendored) == len(site.vendor_urls())
        # And it is not "from the CDN" either: the page's probe finds vendor/
        # and loads from it. What slipped through (review of SPEC 40): the
        # meshopt decoder joined `vendor_urls`, so every site vendored before
        # it read "three.js from the CDN" in `site status` while the page
        # loaded vendor/ and, offline, 404'd on the one file nothing named.
        info["vendor_missing"] = [p for p in site.vendor_urls() if p not in vendored]

    if not os.path.isfile(state_path):
        info["stale_reason"] = "never built — `nopekit site build`"
        return info

    try:
        payload = read_json(state_path, None)
    except NopekitError as exc:
        info["error"] = str(exc)
        return info
    if not isinstance(payload, dict):
        info["error"] = f"{rel(state_path, root)} is not a state document"
        return info

    meta = payload.get("meta") or {}
    info["built"] = True
    # The build time is in the ignored `build.json` from phase 5 (SPEC 32); an
    # older page's `state.json` still says it.
    try:
        clock = read_json(os.path.join(data_dir, site.BUILD_NAME), None)
    except NopekitError:
        clock = None
    info["built_at"] = str((clock or {}).get("built") or meta.get("built") or "")
    info["age"] = _age(info["built_at"])
    info["views"] = [{"id": v.get("id", ""), "kind": v.get("kind", ""),
                      "src": v.get("src", ""), "pack": v.get("pack", "")}
                     for v in (payload.get("views") or [])]
    info["locator_problems"] = list(payload.get("locator_problems") or [])
    info["verdicts_stale"] = bool(meta.get("stale"))
    info["verdicts_stale_reason"] = str(meta.get("stale_reason") or "")
    # The command the build baked, with the tier that reaches the stale gates
    # (a page from before it: a plain `nopekit check`, as it said then).
    info["verdicts_stale_check"] = str(meta.get("stale_check") or "nopekit check")
    info["verdicts_stale_unseen"] = bool(meta.get("stale_unseen"))
    info["claims"] = len(payload.get("claims") or [])
    info["verdicts"] = len(payload.get("verdicts") or [])

    def stale(reason: str) -> dict:
        info["stale"] = True
        info["stale_reason"] = reason
        return info

    built_from = str(meta.get("records_digest") or "")
    judged_from = str(meta.get("resolution_digest") or "")
    try:
        records_now = store.records_digest(root)
    except (NopekitError, OSError) as exc:
        return stale(f"the records cannot be read ({exc}) — fix them, then "
                     f"`nopekit site build`")
    try:
        now, view, registry, resolution, live_amber, live_feeds = _site_judgement(root,
                                                                                  resolved)
    except (NopekitError, OSError) as exc:
        return stale(f"what the page would show now cannot be worked out "
                     f"({exc}) — fix that, then `nopekit site build`")
    composed = claims.compositions(view, registry=registry,
                                   stale_gates=frozenset(resolution.stale_gates),
                                   unseen_gates=claims.unseen_gates(resolution))
    tokens = graph.tokens(view, resolution, registry, composed=composed)
    try:
        info["prose"] = [{"file": row["file"], "subject": row["subject"], "stamp": row["stamp"]}
                         for row in page.stamp_states(root, view, tokens)]
    except OSError:
        info["prose"] = []
    info["prose_behind"] = [row["file"] for row in info["prose"]
                            if row["subject"] is not None and row["stamp"] != "current"]

    docs: dict[str, Any] = {}
    for name in (site.MAP_NAME, site.PAGE_NAME):
        try:
            docs[name] = read_json(os.path.join(data_dir, name), None)
        except NopekitError as exc:
            info["edited"].append(f"data/{name} is unreadable ({exc}): a build writes it again")
            docs[name] = None
    for name, doc in docs.items():
        if isinstance(doc, dict) and doc.get("self") != page.seal(doc):
            info["edited"].append(f"data/{name} was edited by hand: a build writes it again")
    map_doc, page_doc = docs.get(site.MAP_NAME), docs.get(site.PAGE_NAME)
    # Each view row and view file at the bytes the build sealed into page.json
    # (review of P1): a payload edited by hand reads so, whether or not prose
    # names one of its numbers.
    if isinstance(page_doc, dict) and page_doc.get("self") == page.seal(page_doc):
        info["edited"].extend(f"{problem}: edited by hand, a build writes it again"
                              for problem in site.view_problems(
                                  site_dir, payload, page_doc))
    if isinstance(map_doc, dict):
        info["map"] = {"lines": len(map_doc.get("lines") or ()),
                       "boards": sum(1 for n in map_doc.get("nodes") or ()
                                     if n.get("kind") == "board"),
                       "zones": sum(1 for n in map_doc.get("nodes") or ()
                                    if n.get("kind") == "zone"),
                       "stations": sum(1 for n in map_doc.get("nodes") or ()
                                       if n.get("kind") == "station")}

    if not built_from:
        return stale("the page does not say which records it was built from "
                     "(an older build) — `nopekit site build`")
    if built_from != records_now:
        return stale("the records have changed since the site was built "
                     "— `nopekit site build`")
    if not judged_from:
        return stale("the page does not say which verdicts it was built from "
                     "(an older build) — `nopekit site build`")
    if now["meta"]["resolution_digest"] != judged_from:
        moved = site.resolution_moved(payload, now)
        shown = moved[:verdicts.MAX_STALE_REASONS]
        more = len(moved) - len(shown)
        named = (f" ({'; '.join(shown)}{f' (+{more} more)' if more > 0 else ''})"
                 if shown else "")
        return stale(f"what the page shows has changed since the site was built"
                     f"{named} — `nopekit site build`")
    if not isinstance(map_doc, dict) or not isinstance(page_doc, dict):
        return stale("the page has no front-page data (an older build): `nopekit site build`")
    if not info["edited"] and all(
            (d.get("meta") or {}).get("resolution_digest") == judged_from
            for d in (map_doc, page_doc)):
        as_of: dict[str, Any] = {}
        for subject, path in page.prose_files(root):
            try:
                with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
                    as_of[subject] = prose.parse_front_matter(fh.read()).as_of
            except OSError:
                continue
        # The figures (issue #15, B.5) against the disk: each file the page
        # links hashed against its sidecar, the currency read again from the
        # served entry, a chart's limit against the floor.
        found = page.status_problems(map_doc, page_doc, view, composed, tokens,
                                     prose_as_of=as_of,
                                     pool=site.baked_pool(root, map_doc, page_doc),
                                     amber=live_amber, feeds=live_feeds,
                                     figures=page.figure_facts(
                                         root, resolution, now, view, composed, registry,
                                         baked_views=payload.get("views") or ()))
        if found:
            info["edited"].append(f"the page data (data/{site.PAGE_NAME}, "
                                  f"data/{site.MAP_NAME}) was edited by hand: {found[0]}")
    if info["edited"]:
        return stale(f"{info['edited'][0]} — `nopekit site build`")
    if (page_doc.get("meta") or {}).get("content_digest") != page.content_digest(root):
        return stale("the text in site/content/ changed since the site was built: "
                     "`nopekit site build`")
    info["stale_reason"] = "current with the records and the verdicts"
    return info


def _site_judgement(root: str, resolved: tuple | None) -> tuple:
    """``(state, view, registry, resolution)``: the `state.json` a `site build`
    would write now, less its views — the one producer (`site.state`) over the
    one resolution (`_resolved`, R-5), handed in by the caller or resolved here
    the way `status` resolves (the gates loaded with strict=False and the model
    through `_projection_safe`, because the question is asked by the commands
    that must survive a broken pack or a model mid-edit) — and what it was
    judged over. No viewgen runs: the views are not judged
    (`site._UNJUDGED_KEYS`)."""
    if resolved is None:
        ledger = _load(root)
        registry, _problems = _registry(root, ledger, strict=False)
        model, projection, model_error = _projection_safe(root, ledger)
        view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                     now="", model=model)
        params = _shown_params(root, ledger, model, model_error, resolution, registry,
                               projection=projection)
    else:
        view, registry, resolution, params = resolved[:4]
        if len(resolved) >= 6:
            model, projection = resolved[4], resolved[5]
        else:
            ledger = _load(root)
            model, projection, _model_error = _projection_safe(root, ledger)
    judged = verdicts.view(view, resolution)
    # Architecture phase 3b: the trace's state and amber, as `site build`
    # bakes them into `state.json` (inside its resolution digest).
    traced = _trace_for(root, model, projection, resolution)
    built = graph.of(root, judged, resolution, registry, projection=projection, model=model,
                     views=params, bases=_input_bases(root, _load(root)), trace=traced.trace)
    found = graph.tokens(judged, resolution, registry)
    amber = _amber_for(root, judged, registry, built, found.claims, traced)
    payload = derivation.state_payload(traced, built, amber)
    try:
        lay = page.layout(judged, built, projection=projection, params=params,
                          registry=registry, resolution=resolution)
        feeds = page.live_feeds(lay, built, found)
    except Exception:                       # noqa: BLE001 - the checker runs without them
        feeds = None
    return (site.state(root, judged, registry, resolution=resolution, params=params,
                       trace=payload),
            judged, registry, resolution, amber, feeds)


def _site_brief(info: dict) -> dict:
    """The handful of site fields `status --json` and `doctor` carry.

    Trimmed rather than embedded whole: `nopekit status --json` is read by
    agents with a context budget, and a site's full view list and locator
    problem records belong to `nopekit site status`, which is the command that
    was asked about them.
    """
    return {
        "present": info["present"], "built": info["built"],
        "built_at": info["built_at"], "stale": info["stale"],
        "views": len(info["views"]), "dangling_locators": len(info["locator_problems"]),
        "vendored": info["vendored"], "vendor_missing": list(info.get("vendor_missing") or ()),
        "error": info["error"],
        "outline": info.get("outline"), "old_layout": bool(info.get("old_layout")),
        "edited": list(info.get("edited") or ()),
        "prose_behind": list(info.get("prose_behind") or ()),
    }


def _site_line(info: dict) -> str:
    """One line about the site for `nopekit status`. Leads with what is wrong."""
    if info["error"]:
        return f"site: {info['state']} is unreadable — {info['error']}"
    if not info["built"]:
        return "site: scaffolded, never built — `nopekit site build`"
    dangling = len(info["locator_problems"])
    bits = [f"{len(info['views'])} view(s)"]
    if info.get("edited"):
        bits.insert(0, "edited by hand")
    if dangling:
        bits.append(f"{dangling} dangling locator(s)")
    if info.get("prose_behind"):
        n = len(info["prose_behind"])
        bits.append(f"{n} text(s) written before the latest results")
    if info["stale"]:
        # The reason `_site_state` found, never a fixed sentence (review,
        # `repro_site`).
        bits.append(f"STALE: {info['stale_reason']}")
    elif info["age"]:
        bits.append(f"built {info['age']}")
    elif not info["built_at"]:
        bits.append("built (time unknown)")
    return f"site: {', '.join(bits)}"


def _project_name(root: str, ledger: Any = None) -> str:
    """The name a launch entry shows: the project's own, else its directory's."""
    try:
        name = (ledger if ledger is not None else _load(root)).meta.name
    except Exception:       # noqa: BLE001 - a name for a menu entry is never worth a refusal
        name = ""
    return (name or os.path.basename(os.path.abspath(root).rstrip(os.sep)) or "project").strip()


def cmd_site_init(args: argparse.Namespace) -> int:
    """Scaffold `site/`, refusing to overwrite the shell.

    `index.html` is the one file this will not replace without `--force`: the
    contract calls it *hackable, yours, never regenerated after init*, and
    someone who spent an afternoon on their project's page must not lose it to
    the command they ran to pick up a renderer fix. From the two pages it is a
    redirect to `engineering/`, and the engineering and marketing pages are
    shells of their own (`site.SHELLS`): written when absent, kept when present,
    replaced only with `--force`. Everything else in the template IS refreshed,
    because `app.js` and `style.css` are ours and a project that could never
    receive a fix would be stuck with the bugs of the day it was created.
    """
    root = _root(args)
    with _lock(root):
        written = site.scaffold(root, force=bool(args.force))
        launch = site.ensure_launch(root, _project_name(root))

    if args.json:
        _dump({"root": root, "site": site.SITE_DIR, "wrote": written, "launch": launch,
               "pages": {name: posixpath.join(site.SITE_DIR, path, "index.html")
                         for name, path in site.PAGES},
               "next": ["nopekit site build", "nopekit site serve"]})
        return 0
    _say(f"scaffolded {site.SITE_DIR}/ in {root}")
    for path in written:
        _say(f"  {path}")
    if launch:
        _say(f"  {launch}   (Run and Debug: builds and opens this site)")
    _say("")
    _say(f"  two pages: {site.SITE_DIR}/engineering/index.html (the claims, the map and the "
         f"evidence; {site.SITE_DIR}/index.html opens it) and "
         f"{site.SITE_DIR}/marketing/index.html (the model, the story and the choices)")
    _say(f"  they are yours — edit them; `site init` will not overwrite them again")
    _say("next: nopekit site build   (writes data/ and assets/), then "
         "nopekit site serve")
    return 0


def _site_build(root: str, ledger: Ledger, registry: gates.Registry, model: Any,
                projection: Any, view: Ledger, resolution: verdicts.Resolution, *,
                now: str, built: Any = None, live: bool = False,
                traced: derivation.TraceRead | None = None) -> dict:
    """`site build`'s work under the caller's lock, over the caller's resolution:
    the viewgens, `site.build`, and the launch entry. `round` calls it with the
    resolution its round record is computed from (architecture phase 3a), so
    the page and the record cannot disagree about a claim — and, from phase 5,
    with its graph (``built``), so the map and the round record share one."""
    view_registry, _ = _view_registry(root, ledger)
    params = _shown_params(root, ledger, model, "", resolution, registry, projection=projection)
    if traced is None:
        traced = _trace_for(root, model, projection, resolution)
    if built is None:
        built = graph.of(root, view, resolution, registry, projection=projection, model=model,
                         views=params, bases=_input_bases(root, ledger), trace=traced.trace)
    summary = site.build(root, view, registry, view_registry, model=model,
                         projection=projection, now=now, resolution=resolution,
                         params=params, graph=built, live=live, traced=traced)
    # A groundspace made before `init` wrote one gains its launch entry here, the
    # first time its site is built; an existing file is never touched.
    summary["launch"] = site.ensure_launch(root, _project_name(root, ledger))
    return summary


def cmd_site_build(args: argparse.Namespace) -> int:
    """Run the viewgens, collect the ledger, and write `site/data/` + `site/assets/`.

    **This never runs gates.** It renders the verdict cache as `_resolved`
    judges it and stamps each verdict with its own age. A build that re-ran the cheap gates on the way past
    would publish a page whose tier-0 numbers are ten seconds old beside tier-2
    numbers from last week, under one "built at" stamp, with nothing on the page
    saying which is which. If the results are stale the honest fix is
    `nopekit check`, and the page's job is to make that impossible to miss.

    The gate registry is loaded STRICTLY, unlike `status` and `doctor`. Claim
    coverage and the readiness sentence are resolved against it, so a pack that
    failed to import produces a page full of UNCLAIMED rows that are an artefact
    of an import error rather than a statement about the design — published, in
    the one artifact whose entire job is to be trusted when a gate says
    something is wrong.

    It also refreshes the renderer — every template file but the shells
    (`site.SHELLS`), and writes a shell that is absent, the root's excepted —
    when this nopekit's differs (`site.refresh_renderer`), so a page scaffolded
    by an older spine never reads new data with old words.

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
        ledger = _load(root)
        registry, _ = _registry(root, ledger)
        model, projection = _projection(root, ledger)
        now = utcnow_iso()
        # The page renders the resolver's answer, the one every other reader
        # prints (`_resolved`, R-5), handed over rather than recomputed: the page
        # and `status` built from one resolution cannot disagree about a gate.
        view, resolution = _resolved(root, ledger, registry, projection, "", now=now,
                                     model=model)
        summary = _site_build(root, ledger, registry, model, projection, view, resolution,
                              now=now, live=bool(getattr(args, "live", False)))

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
    for notice in summary.get("notices") or ():
        _say(f"{_tag('none')} {notice}")

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
    # Architecture phase 5: the front page's map, its text and its evidence.
    shape = summary.get("map") or {}
    if shape.get("traced"):
        _say(report.HUMAN["trace"]["map_traced"].format(
            lines=shape.get("lines", 0), boards=shape.get("boards", 0),
            zones=shape.get("zones", 0), stations=shape.get("stations", 0),
            layers=shape.get("layers", 0), feeds=shape.get("feeds", 0),
            traced=decisions.traced_words(shape.get("round"))))
    else:
        _say(f"map: {shape.get('lines', 0)} lines, {shape.get('boards', 0)} boards, "
             f"{shape.get('zones', 0)} zones, {shape.get('stations', 0)} stations"
             "; derivation not traced yet: lines start at the values each evaluator read")
    prose_counts = summary.get("prose_counts") or {}
    if any(row.get("subject") for row in summary.get("prose") or ()):
        n = sum(1 for row in summary["prose"] if row.get("subject"))
        _say(f"prose: {n} piece(s): {prose_counts.get('current', 0)} current, "
             f"{prose_counts.get('stale', 0)} stale, {prose_counts.get('unstamped', 0)} "
             f"unstamped; {prose_counts.get('unmatched', 0)} unmatched number(s)")
    else:
        _say("prose: none (write site/content/intro.md, story.md and one file per board)")
    found = summary.get("evidence") or {}
    _say(f"evidence: {found.get('copied', 0)} file(s) copied, {found.get('kept', 0)} kept from "
         f"an earlier build, {found.get('missing', 0)} not on the page")
    if summary.get("refreshed"):
        _say(f"refreshed the renderer to this nopekit's template: "
             f"{', '.join(summary['refreshed'])}")
    # A shell the build wrote because it was absent is a page, never the
    # renderer (review of SPEC 40: it was listed as "refreshed the renderer").
    if summary.get("pages"):
        _say(f"wrote the missing pages: {', '.join(summary['pages'])}")
    _say(f"wrote {len(summary['wrote'])} file(s), removed {len(summary['removed'])} "
         f"stale file(s) -> {summary['state']}")
    if summary.get("launch"):
        _say(f"wrote {summary['launch']} — Run and Debug builds and opens this site")
    if problems:
        _say(f"{len(problems)} locator(s) point at something that does not exist. A gate "
             f"that thinks it is drawing and is not looks exactly like a gate that found "
             f"nothing — fix the gate's locator or the view's node names.")
    if summary["stale"]:
        # The tier that reaches the gates it names: a plain `nopekit check` is
        # tier 0, and advised it for a solver's results (issue #15, D1).
        command = "nopekit check" + report._tier_flag(registry, resolution.stale_gates)
        _say(f"verdicts are STALE: {summary['stale_reason']} — `{command}`")
    return 0


# --------------------------------------------------------------------------- #
# the detached live server (the acceptance run after phase 3b)
# --------------------------------------------------------------------------- #
# `nopekit site serve --watch --detach` starts the server as a process of its
# own: a new session (no controlling terminal, its own process group), stdin
# from /dev/null and its output to `SERVE_LOG_REL`, so it holds nothing of the
# shell that started it, and it outlives that shell. What slipped through (the
# acceptance run after phase 3b): the agent started `site serve --watch` as a
# background task, and a background task lives at most two hours there — the
# person's live page died mid-session — while under headless `claude -p` a
# running background task keeps the turn from ending at all. `_serve_detach`
# starts it and `_serve_stop` stops it; the rest keeps its state: what runs and
# where, read by `site status` and `doctor`, which write nothing. Here and not
# in `site.py`: fdm-print's and cad-solid's gates import `nopekit.site` (its
# `MOVER_SEPARATOR`), so a code edit there re-keys every one of their verdicts in
# every project (`verdicts.code_digest`'s `spine_extras`), and a live server
# moves no verdict.

#: The detached server's record, under the project's ignored `.nopekit/out/`:
#: its pid, address, URL, whether it watches, when it started and on which
#: machine. Written by the server itself once its socket is bound (so ``-p 0``
#: records the port it got), removed by the server as it stops.
SERVE_STATE_REL = ".nopekit/out/serve.json"

#: The lock the server holds for its whole life (`flock`, exclusive): the
#: witness that it runs. The kernel drops it when the process ends, however it
#: ends, so a pid in `SERVE_STATE_REL` counts only while this is held.
#: *Rejected:* the pid alone (after a reboot, or hours later, the OS hands that
#: pid to another process: `--stop` would signal a stranger, and a second
#: `--detach` would print a URL nothing serves); an HTTP probe of the port (a
#: server busy rebuilding reads dead, and anything listening there reads alive).
SERVE_LOCK_REL = ".nopekit/out/serve.lock"

#: The server's output: the ready line, every rebuild, every failed request.
SERVE_LOG_REL = ".nopekit/out/serve.log"

#: How long a starting server tries to take the lock: a reader's probe holds a
#: shared lock for microseconds, and a start that met one must not read it as a
#: server already running.
SERVE_LOCK_WAIT_S = 1.0

#: The sentence for a platform with no `flock` and no new session (Windows).
SERVE_DETACH_UNSUPPORTED = ("`--detach` is not supported on Windows yet: run `nopekit site "
                            "serve --watch` in a terminal of its own and leave it open")


class Served(NamedTuple):
    """What `_serve_state` found: ``state`` — ``none`` (no detached server),
    ``running``, ``starting`` (the lock is held and the record names no live
    pid yet), ``stale`` (a record whose process is gone), ``elsewhere`` (a
    record from another machine) or ``unsupported`` (not POSIX); the record's
    ``pid``, ``url``, ``watch``, ``started`` and ``node`` (the machine)."""

    state: str
    pid: int | None = None
    url: str = ""
    watch: bool = False
    started: str = ""
    node: str = ""


def _serve_path(root: str, rel_path: str) -> str:
    return os.path.join(root, *rel_path.split("/"))


def _serve_record(root: str) -> dict:
    try:
        doc = read_json(_serve_path(root, SERVE_STATE_REL), None)
    except (NopekitError, OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _serve_lock_held(root: str) -> bool | None:
    """Whether a process holds the detached server's lock: a shared,
    non-blocking `flock` probe of an existing lock file, released at once.
    None when it cannot be known (not POSIX, or the file does not open).
    Read-only: an absent lock file is ``False``, never created."""
    if os.name != "posix":
        return None
    import fcntl
    try:
        fd = os.open(_serve_path(root, SERVE_LOCK_REL), os.O_RDONLY)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return None
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _session_leader(pid: int) -> bool:
    """Whether ``pid`` leads its own session, as the detached server always
    does (`start_new_session`): one more witness that a live pid in the record
    is the server and not a process the OS gave its number to. True when the
    OS will not say (macOS may refuse another session's id): the lock decides."""
    try:
        return os.getsid(pid) == pid
    except ProcessLookupError:
        return False
    except OSError:
        return True


def _serve_state(root: str) -> Served:
    """The detached server for ``root``, read-only (`Served`): ``running``
    only while the lock is held AND the record names a live pid on this
    machine that leads its own session. A record whose lock is free is
    ``stale`` whatever machine it names: a host renamed (a container's, at
    every start) must not leave a record that reads alive for good."""
    if os.name != "posix":
        return Served("unsupported")
    doc = _serve_record(root)
    pid = doc.get("pid")
    pid = pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None
    found = dict(pid=pid, url=str(doc.get("url") or ""), watch=bool(doc.get("watch")),
                 started=str(doc.get("started") or ""), node=str(doc.get("node") or ""))
    if _serve_lock_held(root):
        if found["node"] and found["node"] != _HOST:
            return Served("elsewhere", **found)
        alive = pid is not None and _pid_alive(pid) and _session_leader(pid)
        return Served("running" if alive and found["url"] else "starting", **found)
    if doc:
        return Served("stale", **found)
    return Served("none")


def _hold_serve_lock(root: str, *, wait_s: float = SERVE_LOCK_WAIT_S) -> int | None:
    """Take the detached server's lock for the life of this process: the
    file descriptor that holds it (never inherited by a child: Python's are
    not), or None when another process holds it past ``wait_s``."""
    import fcntl
    path = _serve_path(root, SERVE_LOCK_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + max(0.0, float(wait_s))
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.02)


def _write_serve_state(root: str, *, pid: int, host: str, port: int, url: str, watch: bool,
                       started: str) -> None:
    """Write the detached server's record (atomically): what `_serve_state`
    reads."""
    atomic_write_json(_serve_path(root, SERVE_STATE_REL),
                      {"pid": int(pid), "host": host, "port": int(port), "url": url,
                       "watch": bool(watch), "started": started, "node": _HOST,
                       "log": SERVE_LOG_REL})


def _clear_serve_state(root: str, pid: int | None) -> bool:
    """Remove the record when it names ``pid`` (any record when ``pid`` is
    None: a stale one); True when it did."""
    if pid is not None and _serve_record(root).get("pid") != pid:
        return False
    try:
        os.remove(_serve_path(root, SERVE_STATE_REL))
    except OSError:
        return False
    return True


def _serve_lease(root: str, pid: int, lock_fd: int) -> bool:
    """Whether the running server ``pid`` still owns its place: the record
    names it, its lock file is the one it holds (same inode: a deleted
    `.nopekit/out/` and a new server's file are not), and the site is still
    there. A server whose lease is gone stops itself (`_serve_lease_loop`):
    what slipped through the first draft — a test's temporary project, deleted
    with its server running, left a server serving nothing, and a `git clean
    -X` would leave two servers for one project."""
    if _serve_record(root).get("pid") != pid or not site.scaffolded(root):
        return False
    try:
        return os.stat(_serve_path(root, SERVE_LOCK_REL)).st_ino == os.fstat(lock_fd).st_ino
    except OSError:
        return False


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

    **`--detach`** (the acceptance run after phase 3b) starts this same server
    as a process of its own and returns once it listens (`_serve_detach`);
    **`--stop`** stops that process (`_serve_stop`). The detached process runs
    this function with the hidden ``--detached-server``: it holds
    `SERVE_LOCK_REL` for its life, records where it listens in
    `SERVE_STATE_REL` once bound, logs only failed requests, stops on
    SIGTERM between requests, stops by itself when its lease is gone
    (`_serve_lease`), and removes its record as it stops.
    """
    # Deferred: `http.server` pulls in socketserver, email, html and mimetypes —
    # ~40 ms measured — and every `nopekit check` in an inner loop would pay it
    # to not start a server.
    import errno
    import http.server
    import threading
    import webbrowser

    root = _root(args)
    if getattr(args, "stop", False):
        return _serve_stop(root, args)
    if getattr(args, "detach", False):
        return _serve_detach(root, args)
    detached = bool(getattr(args, "detached_server", False))
    site_dir = os.path.join(root, site.SITE_DIR)
    if not site.scaffolded(root):
        raise NopekitError(
            f"no {site.SITE_DIR}/index.html at {root} — `nopekit site init` first")
    state_path = os.path.join(site_dir, site.DATA_DIR, site.STATE_NAME)
    built = os.path.isfile(state_path)
    watch = bool(getattr(args, "watch", False))
    interval = max(0.2, float(getattr(args, "interval", 1.0) or 1.0))
    lock_fd = None
    if detached:
        if os.name != "posix":
            raise NopekitError(SERVE_DETACH_UNSUPPORTED)
        lock_fd = _hold_serve_lock(root)
        if lock_fd is None:
            running = _serve_state(root)
            raise NopekitError(
                f"a live site server already runs for this project"
                f"{' at ' + running.url if running.url else ''} — `nopekit site serve "
                f"--stop` stops it")
        # A record that does not name this process is a dead server's: gone
        # before the bind, so no reader pairs this lock with that pid.
        _clear_serve_state(root, None)

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

        def log_request(self, code: Any = "-", size: Any = "-") -> None:
            # Detached, only a failed request reaches the log: a live page polls
            # data/build.json every 2 s, and a line per poll grows the log by
            # megabytes a day that nobody reads.
            if detached and str(getattr(code, "value", code)).isdigit() \
                    and int(getattr(code, "value", code)) < 400:
                return
            super().log_request(code, size)

        def log_message(self, fmt: str, *a: Any) -> None:
            # stderr, so `--json` output and a piped stdout stay clean. Kept
            # rather than silenced: a 404 on an asset is the single most common
            # thing to debug here and the log line names it.
            _warn(f"  {self.address_string()} {fmt % a}")

    try:
        try:
            server = http.server.ThreadingHTTPServer((args.host, int(args.port)), Handler)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise NopekitError(
                    f"port {args.port} on {args.host} is already in use — something else is "
                    f"listening there (another `nopekit site serve`, or any other server). "
                    f"Use `-p <other port>`, or `-p 0` to let the OS pick a free one."
                ) from exc
            if exc.errno in (errno.EACCES, errno.EPERM):
                raise NopekitError(
                    f"not allowed to bind {args.host}:{args.port} — ports below 1024 need "
                    f"root on most systems. Use `-p 8000` or any port above 1024."
                ) from exc
            raise NopekitError(
                f"cannot serve on {args.host}:{args.port}: {exc.strerror or exc}") from exc

        host, port = server.server_address[0], server.server_address[1]
        # The bound port, not the requested one: `-p 0` means "any free port", and
        # printing the 0 back would be a URL that goes nowhere.
        url = f"http://{host}:{port}/"
        if detached:
            _write_serve_state(root, pid=os.getpid(), host=str(host), port=int(port),
                               url=url, watch=watch, started=utcnow_iso())

        if args.json:
            _dump({"url": url, "host": host, "port": port, "root": root,
                   "serving": rel(site_dir, root), "built": built,
                   "pages": site.page_urls(url)})
        else:
            _say(site.SERVE_READY_LINE.format(served=rel(site_dir, root), url=url))
            # The two pages' addresses, after the ready line and never matching
            # its pattern: the editor opens the root, which redirects.
            _say(site.pages_line(url))
            if watch:
                _say(f"watching {len(site.watch_snapshot(root))} path(s); a change rebuilds "
                     f"site/ (no evaluator runs: `nopekit check` does that)")
            elif not built:
                _say(f"{_tag('warn')} {rel(state_path, root)} does not exist yet — the page "
                     f"will load with no data. Run `nopekit site build`.")
            _say("`nopekit site serve --stop` to stop" if detached else "Ctrl-C to stop")
        stop = threading.Event()
        if detached:
            import signal

            def _stop_serving(_signum: int, _frame: Any) -> None:
                # Between requests, never mid-build: `shutdown` waits for the
                # loop, so it is asked from a thread of its own.
                threading.Thread(target=server.shutdown, daemon=True,
                                 name="nopekit-site-stop").start()

            signal.signal(signal.SIGTERM, _stop_serving)
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            threading.Thread(target=_serve_lease_loop, args=(root, server, lock_fd, stop),
                             daemon=True, name="nopekit-site-lease").start()
        if watch:
            # SPEC 33: one build before serving, then a daemon thread that rebuilds
            # on a change. The page polls data/build.json while it reads live.
            before = site.watch_snapshot(root)
            ok, error, _moved = _watch_rebuild(root, quiet=True)
            if not ok:
                _warn(f"{_tag('warn')} rebuild failed: {error}; the page keeps the last build")
            threading.Thread(target=_watch_loop, args=(root, interval, stop, before),
                             daemon=True, name="nopekit-site-watch").start()

        if not args.no_browser and not detached:
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
        else:
            if detached and not args.json:
                _say("stopped")
        finally:
            stop.set()
            server.server_close()
    finally:
        if lock_fd is not None:
            _clear_serve_state(root, os.getpid())
            os.close(lock_fd)
    return 0


def _serve_lease_loop(root: str, server: Any, lock_fd: int, stop: Any) -> None:
    """A detached server's lease (`_serve_lease`), checked every
    `SERVE_LEASE_S`: when its record no longer names it, its lock file is not
    the one it holds, or the site is gone, it says why and stops serving."""
    pid = os.getpid()
    while not stop.wait(SERVE_LEASE_S):
        if not _serve_lease(root, pid, lock_fd):
            _warn(f"stopping: {SERVE_STATE_REL} no longer names this server (pid {pid}), "
                  f"or {site.SITE_DIR}/index.html is gone")
            server.shutdown()
            return


def _serve_words(found: Served) -> str:
    """How a detached server reads on one line: its pid, whether it watches,
    and when it started."""
    bits = [f"pid {found.pid}", "watching" if found.watch else "not watching"]
    if found.started:
        bits.append(f"since {found.started}")
    return ", ".join(bits)


def _serve_said(root: str, args: argparse.Namespace, found: Served, *,
                already: bool) -> int:
    """`--detach`'s answer: the ready line `site serve` prints (so the launch
    pattern and a reader of it find the URL), then who serves it and how to
    stop it."""
    served = rel(os.path.join(root, site.SITE_DIR), root)
    if args.json:
        _dump({"url": found.url, "pid": found.pid, "watch": found.watch,
               "started": found.started, "log": SERVE_LOG_REL, "already": already,
               "root": root, "serving": served, "detached": True,
               "pages": site.page_urls(found.url) if found.url else {}})
        return 0
    _say(site.SERVE_READY_LINE.format(served=served, url=found.url))
    if found.url:
        _say(site.pages_line(found.url))
    if already:
        _say(f"already running ({_serve_words(found)}): nothing new was started — "
             f"`nopekit site serve --stop` stops it, to start it with other options")
    else:
        _say(f"detached ({_serve_words(found)}); it outlives this shell, and its log is "
             f"{SERVE_LOG_REL} — `nopekit site serve --stop` stops it")
    return 0


def _serve_log_tail(path: str, offset: int) -> str:
    """The last line the server wrote to its log past ``offset``: why it did
    not start, without the ``error: `` with which `main` prints a refusal (the
    refusal quoting it says that already)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, int(offset)))
            lines = [line.strip() for line in fh.read().decode("utf-8", "replace").splitlines()
                     if line.strip()]
    except OSError:
        return "it wrote nothing"
    last = lines[-1] if lines else "it wrote nothing"
    return last[len("error: "):] if last.startswith("error: ") else last


def _serve_settled(root: str, wait_s: float) -> Served:
    """`_serve_state`, waited on while it reads ``starting`` (a server
    between its lock and its record) for up to ``wait_s`` seconds."""
    deadline = time.monotonic() + max(0.0, wait_s)
    found = _serve_state(root)
    while found.state == "starting" and time.monotonic() < deadline:
        time.sleep(0.05)
        found = _serve_state(root)
    return found


def _serve_detach(root: str, args: argparse.Namespace) -> int:
    """`site serve --detach` (the acceptance run after phase 3b): start the
    server as a process of its own and exit 0 once it listens, printing its
    URL; when one already runs for the project, print its URL and start
    nothing. The process: ``python -m nopekit site serve --detached-server``
    in a new session (POSIX `setsid`: no controlling terminal, its own process
    group, so neither a hang-up nor a signal to the starter's group reaches
    it), started in nopekit's own directory (as the background trace is:
    ``-m`` puts the starting directory first on ``sys.path``), its stdin
    /dev/null and its stdout and stderr appended to `SERVE_LOG_REL` — no
    descriptor of the starter's. What slipped through: a background task lives
    at most two hours in the acceptance run's environment, and under headless
    `claude -p` one that still runs keeps the turn from ending; a server that
    kept the caller's stdout would keep the caller's pipe open as long. Never a
    browser: a browser started from here would hold the caller's descriptors
    for as long as it ran."""
    if os.name != "posix":
        raise NopekitError(SERVE_DETACH_UNSUPPORTED)
    if not site.scaffolded(root):
        raise NopekitError(
            f"no {site.SITE_DIR}/index.html at {root} — `nopekit site init` first")
    found = _serve_settled(root, SERVE_DETACH_WAIT_S)
    if found.state == "running":
        return _serve_said(root, args, found, already=True)
    if found.state == "elsewhere":
        raise NopekitError(f"{SERVE_STATE_REL} names a live site server on {found.node}, "
                           f"another machine; stop it there")
    if found.state == "starting":
        raise NopekitError(f"a live site server holds {SERVE_LOCK_REL} and records no "
                           f"address; see {SERVE_LOG_REL}")
    if found.state == "stale":
        _clear_serve_state(root, None)
    log = _serve_path(root, SERVE_LOG_REL)
    os.makedirs(os.path.dirname(log), exist_ok=True)
    try:
        offset = os.path.getsize(log)
    except OSError:
        offset = 0
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = package_parent + (os.pathsep + env["PYTHONPATH"]
                                          if env.get("PYTHONPATH") else "")
    argv = [sys.executable, "-m", "nopekit", "site", "serve", "-C", os.path.abspath(root),
            "--host", str(args.host), "-p", str(int(args.port)),
            "--interval", repr(max(0.2, float(getattr(args, "interval", 1.0) or 1.0))),
            "--no-browser", "--detached-server"]
    if getattr(args, "watch", False):
        argv.append("--watch")
    try:
        with open(log, "ab") as out:
            proc = subprocess.Popen(argv, cwd=package_parent, env=env,
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                    close_fds=True, start_new_session=True)
    except OSError as exc:
        raise NopekitError(f"the live site could not be started: {exc}") from exc
    deadline = time.monotonic() + SERVE_DETACH_WAIT_S
    while True:
        found = _serve_state(root)
        if found.state == "running" and found.pid == proc.pid:
            return _serve_said(root, args, found, already=False)
        if proc.poll() is not None:
            found = _serve_state(root)
            if found.state == "running":            # another --detach got there first
                return _serve_said(root, args, found, already=True)
            raise NopekitError(f"the live site did not start: {_serve_log_tail(log, offset)} "
                               f"(its log is {SERVE_LOG_REL})")
        if time.monotonic() >= deadline:
            import signal
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGTERM)
            raise NopekitError(f"the live site did not say where it listens in "
                               f"{SERVE_DETACH_WAIT_S:.0f} s, so it was stopped (its log is "
                               f"{SERVE_LOG_REL})")
        time.sleep(0.05)


def _serve_stopped(root: str, wait_s: float) -> bool:
    deadline = time.monotonic() + max(0.0, wait_s)
    while _serve_lock_held(root):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _serve_stop(root: str, args: argparse.Namespace) -> int:
    """`site serve --stop`: stop the detached server for the project — SIGTERM
    to its pid (only while it holds its lock, so a stale pid the OS gave to
    another process is never signalled), then SIGKILL after
    `SERVE_STOP_WAIT_S` — and remove its record. Exit 0 when none runs too: a
    stop asked twice is not an error."""
    if os.name != "posix":
        raise NopekitError(SERVE_DETACH_UNSUPPORTED)
    import signal
    found = _serve_settled(root, 5.0)
    if found.state == "elsewhere":
        raise NopekitError(f"{SERVE_STATE_REL} names a live site server on {found.node}, "
                           f"another machine; stop it there")
    if found.state != "running" or found.pid is None:
        stale = found.state == "stale"
        if stale:
            _clear_serve_state(root, None)
        if args.json:
            _dump({"stopped": False, "state": found.state, "pid": found.pid, "url": found.url})
        else:
            _say("no detached site server runs for this project"
                 + (f" (the record of pid {found.pid} was stale, and is removed)"
                    if stale and found.pid else ""))
        return 0
    with contextlib.suppress(ProcessLookupError):
        os.kill(found.pid, signal.SIGTERM)
    if not _serve_stopped(root, SERVE_STOP_WAIT_S):
        with contextlib.suppress(ProcessLookupError):
            os.kill(found.pid, signal.SIGKILL)
        if not _serve_stopped(root, 5.0):
            raise NopekitError(f"the live site server (pid {found.pid}) did not stop; it holds "
                               f"{SERVE_LOCK_REL} still")
    _clear_serve_state(root, found.pid)
    if args.json:
        _dump({"stopped": True, "state": "running", "pid": found.pid, "url": found.url})
    else:
        _say(f"stopped the live site at {found.url} (pid {found.pid})")
    return 0


def _serve_line(found: Served) -> str:
    """`site status`'s line about the detached server."""
    if found.state == "running":
        return (f"live: serving at {found.url} ({_serve_words(found)}) — `nopekit site "
                f"serve --stop` stops it")
    if found.state == "starting":
        return f"live: a detached server is starting (see {SERVE_LOG_REL})"
    if found.state == "elsewhere":
        return f"live: a detached server runs on {found.node}, another machine"
    if found.state == "unsupported":
        return "live: --detach is not supported on Windows yet"
    stale = (f" (the record of pid {found.pid} is stale)" if found.state == "stale"
             and found.pid else "")
    return (f"live: no detached server runs{stale} — `nopekit site serve --watch --detach` "
            f"starts one")


def _serve_json(found: Served) -> dict:
    return {"state": found.state, "pid": found.pid, "url": found.url, "watch": found.watch,
            "started": found.started, "log": SERVE_LOG_REL}


def cmd_site_stamp(args: argparse.Namespace) -> int:
    """Write the ``as_of`` stamp into ``site/content/`` prose (SPEC 30): the
    digest of the status tokens of the claims the text is about, now — a
    board's claims for ``<board>.md``, every claim, the readiness and each
    milestone for ``intro.md``, ``next.md`` and ``story.md``
    (`page.PROSE_SUBJECTS`). It writes only the ``as_of``
    line (`prose.stamp_text`), never a record (invariant 8), and needs no
    ``site/index.html``: the bracket tracks its text without a built page.

    The stamp says "I read the results as they are now and the text says
    nothing they contradict". It guards age, never correctness: the page flags
    a stamp the resolution has moved past, and the number scan guards the
    numbers. What slipped through before it: nothing kept prose from saying a
    claim passes after it failed."""
    root = _root(args)
    if not getattr(args, "all", False) and not getattr(args, "subject", None):
        raise NopekitError(f"site stamp needs a board name, {', '.join(page.PROSE_SUBJECTS)} "
                           f"or --all")
    with _lock(root):
        folder = page.content_dir(root)
        if not os.path.isdir(folder) and getattr(args, "all", False):
            if args.json:
                _dump({"stamped": [], "unchanged": [], "skipped": []})
            else:
                _say("no text in site/content/ to stamp")
            return 0
        if not os.path.isdir(folder):
            raise NopekitError(f"no site/content/ at {root}: write site/content/<board>.md "
                               f"first; the stamp records the results the text was written "
                               f"against")
        ledger = _load(root)
        registry, _ = _registry(root, ledger)
        try:
            model, projection = _projection(root, ledger)
        except NopekitError as exc:
            raise NopekitError(f"the model does not load, so there are no results to stamp "
                               f"against: {_first_line(exc)}") from None
        view, resolution = _resolved(root, ledger, registry, projection, "", now=utcnow_iso(),
                                     model=model)
        composed = claims.compositions(view, registry=registry,
                                       stale_gates=resolution.stale_gates,
                                       unseen_gates=claims.unseen_gates(resolution))
        tokens = graph.tokens(view, resolution, registry, composed=composed)
        boards = page.boards_of(view)
        known = page.subjects(boards)
        files = page.prose_files(root)
        skipped: list[dict] = []
        if getattr(args, "all", False):
            targets = [(subject, path) for subject, path in files if subject in known]
            skipped = [{"file": f"site/content/{subject}.md", "why": "names no board"}
                       for subject, _path in files if subject not in known]
        else:
            subject = str(args.subject)
            if subject not in known:
                raise NopekitError(
                    f"no board named {subject!r}: boards are "
                    f"{', '.join(b.id for b in boards) or 'none'} "
                    f"(and {', '.join(page.PROSE_SUBJECTS)})")
            path = os.path.join(folder, f"{subject}.md")
            if not os.path.isfile(path):
                raise NopekitError(f"site/content/{subject}.md does not exist: write the text "
                                   f"first, then stamp it")
            targets = [(subject, path)]
        stamped: list[dict] = []
        unchanged: list[str] = []
        current_of: dict[str, str] = {}
        for subject, path in targets:
            with open(path, "r", encoding="utf-8", newline="") as fh:
                text = fh.read()
            found_front = prose.parse_front_matter(text)
            before = found_front.as_of
            current = prose.stamp_digest(subject, page.subject_tokens(
                subject, boards, tokens, named=page.prose_named(found_front.body,
                                                                tokens.claims)))
            rel_path = f"site/content/{subject}.md"
            if before == current:
                unchanged.append(rel_path)
                current_of[rel_path] = current
                continue
            stamped_text = prose.stamp_text(text, current)
            site._atomic_write_bytes(path, stamped_text.encode("utf-8"))
            stamped.append({"file": rel_path, "subject": subject, "as_of": current,
                            "before": before,
                            "state_before": "unstamped" if not before else "stale"})
    if args.json:
        _dump({"stamped": stamped, "unchanged": unchanged, "skipped": skipped})
        return 0
    if not targets:
        _say("no text in site/content/ to stamp")
    for row in stamped:
        was = (f"was {row['before']}, stale" if row["before"] else "was unstamped")
        _say(f"{row['file']}: as_of {row['as_of']} ({was})")
    for rel_path in unchanged:
        _say(f"{rel_path}: already current ({current_of[rel_path]})")
    for row in skipped:
        _say(f"{_tag('warn')} {row['file']}: {row['why']}; not stamped")
    return 0


def cmd_site_badge(args: argparse.Namespace) -> int:
    """Write the Made-with-nopekit block at the bottom of ``README.md`` (SPEC
    36), creating the file when absent — the block `init` writes, for a project
    made before it did. Writes no record and builds no page."""
    root = _root(args)
    with _lock(root):
        try:
            meta = store.read_project(root)
            name, summary = meta.name, meta.summary
        except NopekitError:
            name, summary = "", ""
        wrote = store.ensure_readme_badge(root, name, summary)
    if args.json:
        _dump({"readme": "README.md", "wrote": wrote})
        return 0
    _say("wrote the Made-with-nopekit block at the bottom of README.md" if wrote
         else "README.md already carries the Made-with-nopekit block")
    return 0


def _watch_rebuild(root: str, *, quiet: bool) -> tuple[bool, str, list[str]]:
    """One `nopekit site build --live --json` in a fresh process (SPEC 33):
    ``(ok, error, moved)``. A fresh process reloads the model and the gates
    cleanly. *Rejected:* an in-process loop (module and gate caches would carry
    state from one build to the next)."""
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = package_parent + (os.pathsep + env["PYTHONPATH"]
                                          if env.get("PYTHONPATH") else "")
    before = None
    state_path = os.path.join(root, site.SITE_DIR, site.DATA_DIR, site.STATE_NAME)
    try:
        before = read_json(state_path, None)
    except NopekitError:
        before = None
    proc = subprocess.run([sys.executable, "-m", "nopekit", "site", "build", "-C", root,
                           "--live", "--json"], capture_output=True, text=True, env=env,
                          check=False)
    if proc.returncode != 0:
        lines = [line for line in (proc.stderr or proc.stdout or "").splitlines()
                 if line.strip()]
        return False, (lines[-1].strip() if lines else f"exit {proc.returncode}"), []
    moved: list[str] = []
    try:
        after = read_json(state_path, None)
        if isinstance(before, dict) and isinstance(after, dict):
            moved = site.resolution_moved(before, after)
    except NopekitError:
        pass
    return True, "", moved


def _lock_taken(root: str) -> bool:
    """Whether another command holds the project's lock: its file exists
    (`FileLock.age`; `FileLock.held` says only whether THIS object holds it)."""
    return _lock(root).age() is not None


def _lock_words(root: str) -> str:
    holder = _lock(root).holder()
    what = str(holder.get("command") or holder.get("argv") or "another nopekit command")
    pid = holder.get("pid")
    return f"{what} (pid {pid})" if pid else what


def _watch_loop(root: str, interval: float, stop: Any, built_from: dict | None = None) -> None:
    """Poll `site.watch_snapshot` every ``interval`` seconds; rebuild when the
    snapshot differs from the last one built and was equal on two polls in a
    row; wait while the lock is held; never run `check`. ``built_from`` is the
    snapshot the page was last built from — taken by the caller before its own
    first build, so an edit that lands before this thread starts is still seen."""
    if built_from is None:
        built_from = site.watch_snapshot(root)
    last = built_from
    said_waiting = False
    while not stop.wait(interval):
        try:
            now = site.watch_snapshot(root)
        except OSError:
            continue
        if now != last:
            last = now
            continue
        if now == built_from:
            continue
        if _lock_taken(root):
            if not said_waiting:
                _warn(f"waiting: {_lock_words(root)}")
                said_waiting = True
            continue
        said_waiting = False
        # The snapshot the build starts from, never one taken after it: an edit
        # that lands while the build runs is a change the next poll still sees.
        # What slipped through the first cut: under load the snapshot was taken
        # after the build, so a model broken during it read as built.
        built_from = now
        ok, error, moved = _watch_rebuild(root, quiet=True)
        if ok:
            shown = moved[:4]
            _warn("rebuilt: " + ("; ".join(shown) + (f" (+{len(moved) - 4} more)"
                                                     if len(moved) > 4 else "")
                                 if shown else "nothing the page shows moved"))
            continue
        if _lock_taken(root):
            built_from = {}                 # another command raced the build: try again
            continue
        try:
            previous = read_json(os.path.join(root, site.SITE_DIR, site.DATA_DIR,
                                              site.BUILD_NAME), None) or {}
        except NopekitError:
            previous = {}
        site.write_build_json(root, now=utcnow_iso(), id=str(previous.get("id") or ""),
                              ok=False, error=error, live=True,
                              verdicts=previous.get("verdicts") or {},
                              mixed_age=previous.get("mixed_age"))
        _warn(f"{_tag('warn')} rebuild failed: {error}; the page keeps the last build")


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
        raise NopekitError(
            f"no {site.SITE_DIR}/ directory at {root} — `nopekit site init` first")

    urls = site.vendor_urls()
    staging = tempfile.mkdtemp(dir=site_dir, prefix=".nopekit-vendor-")
    fetched: list[dict] = []
    try:
        for relative, url in sorted(urls.items()):
            if not url.startswith("https://"):
                # Guards a future edit to `vendor_urls`, not today's list: these
                # files are executed by the page, and a plaintext fetch of code
                # the site will run is a rewrite waiting for a coffee shop.
                raise NopekitError(f"refusing to vendor {relative} over a non-https URL: {url}")
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
    _say(f"the import map in each page ({site.SITE_DIR}/engineering/, "
         f"{site.SITE_DIR}/marketing/, {site.SITE_DIR}/technical.html) now resolves against "
         f"these files instead of the CDN; delete {site.SITE_DIR}/{site.VENDOR_DIR}/ to go back")
    return 0


def _fetch_vendor_file(url: str) -> bytes:
    """One vendored file's bytes, or an NopekitError that says what to do instead.

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
        raise NopekitError(
            f"{url} returned HTTP {exc.code} ({exc.reason}). Nothing was written; the "
            f"site still loads three.js from the CDN."
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise NopekitError(
            f"cannot reach {url}: {reason}. Nothing was written — the site keeps loading "
            f"three.js from the CDN, which is the working path. Re-run `nopekit site "
            f"vendor` when the network is back."
        ) from exc

    if status != 200 or not payload:
        raise NopekitError(
            f"{url} returned HTTP {status} and {len(payload)} bytes. Nothing was written; "
            f"the CDN path still works.")
    head = payload.lstrip()[:14].lower()
    if "text/html" in content_type or head.startswith((b"<!doctype", b"<html")):
        raise NopekitError(
            f"{url} answered with HTML, not JavaScript — that is a captive portal or a "
            f"proxy interception page, not three.js. Nothing was written; vendoring it "
            f"would have shadowed the working CDN copy with a login screen.")
    return payload


def cmd_site_status(args: argparse.Namespace) -> int:
    """What is built, how stale it is, which locators point at nothing, and
    whether a detached live server runs and where (`_serve_state`, the
    acceptance run after phase 3b).

    Reads `state.json` and the filesystem; it never runs a viewgen and never
    writes. It does load the viewgen modules, with `strict=False`, because the
    question this command exists to answer is usually "why is there no assembly
    view in my site?" and the answer is usually a dependency that is named in
    the registration and missing on this machine.
    """
    root = _root(args)
    ledger = _load(root)
    info = _site_state(root)
    served = _serve_state(root)
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
               "has_dangling_locators": bool(info["locator_problems"]),
               "server": _serve_json(served)})
        return 0

    if not info["present"]:
        _say(f"no {site.SITE_DIR}/index.html — `nopekit site init`")
        return 0
    if info["error"]:
        _say(f"{_tag('FAIL')} {info['state']}: {info['error']}")
        _say(_serve_line(served))
        return 0
    if not info["built"]:
        _say(f"{site.SITE_DIR}/ is scaffolded but never built — `nopekit site build`")
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

    for edited in info.get("edited") or ():
        _say(f"{_tag('FAIL')} {edited}")
    _say(f"{'STALE' if info['stale'] else 'current'}: {info['stale_reason']}")
    _say(_serve_line(served))
    shape = info.get("map") or {}
    if shape:
        _say(f"map: {shape.get('lines', 0)} lines, {shape.get('boards', 0)} boards, "
             f"{shape.get('zones', 0)} zones, {shape.get('stations', 0)} stations")
    pieces = [row for row in info.get("prose") or () if row.get("subject")]
    if pieces:
        _say(f"prose: {len(pieces)} piece(s): "
             + ", ".join(f"{row['file']} {row['stamp']}" for row in pieces))
    for row in info.get("prose") or ():
        if not row.get("subject"):
            _say(f"{_tag('warn')} {row['file']} names no board: it is not on the page")
    # One sentence each with `site build` and `doctor` (`site.outline_problem`,
    # `site.OLD_LAYOUT_WARNING`): the outline is the engineering page's, and a
    # root page from before the two pages is said apart.
    older = site.outline_problem(root)
    if older:
        _say(f"{_tag('warn')} {older}")
    if info.get("old_layout"):
        _say(f"{_tag('warn')} {site.OLD_LAYOUT_WARNING}")
    if info["verdicts_stale"]:
        _say(f"{_tag('warn')} the verdicts on the page are stale: "
             f"{info['verdicts_stale_reason']} — `{info['verdicts_stale_check']}`"
             # Unseen only: the rebuild after a check run reads the same, so
             # it is not promised (review of issue #15's D1).
             + ("" if info["verdicts_stale_unseen"] else ", then rebuild"))
    if info["locator_problems"]:
        _say(f"{_tag('warn')} {len(info['locator_problems'])} dangling locator(s):")
        for problem in info["locator_problems"][:6]:
            target = f"/{problem.get('target')}" if problem.get("target") else ""
            _say(f"    {problem.get('gate', '?')} -> {problem.get('view', '')}{target}: "
                 f"{problem.get('problem', '')}")
        if len(info["locator_problems"]) > 6:
            _say(f"    (+{len(info['locator_problems']) - 6} more — `--json` for all)")
    _say(f"assets: {info['assets']} file(s), {human_bytes(info['asset_bytes'])}; "
         f"three.js {site.THREE_VERSION} {_vendor_words(info)}")
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


def _tagged_row(name: str, status: str, detail: str) -> dict:
    """One doctor row, not yet placed (`_check` appends it)."""
    return {"check": name, "status": status, "detail": detail}


def _check(results: list[dict], name: str, status: str, detail: str) -> None:
    """Append one doctor row. `status` is one of ok / warn / FAIL."""
    results.append({"check": name, "status": status, "detail": detail})


def _ledger_problems(root: str, ledger: Ledger,
                     registry: gates.Registry) -> tuple[list[str], list[str]]:
    """`(problems, warnings)`: everything structurally wrong with this ledger,
    and what is only out of place, as sentences.

    Integrity here means "the records still refer to things that exist". Every
    problem is a way the project can look fine and be wrong: a duplicate claim
    id means one of two claims is invisible to every lookup; a missing artifact
    file is provenance that no longer resolves.

    `ledger` is `_resolved`'s view: its verdicts are the resolver's, so a gate
    that is not registered here shows up by its cache entries or its remembered
    outcome, not only by a row the ledger file happened to keep. Those orphans
    are a WARNING, not a problem: the resolver reads them stale and they never
    count (tests:H2), and nothing is corrupt — a pack was uninstalled, or a gate
    renamed. As a FAIL they made `doctor` exit 1 on a project whose only fault
    was a removed pack's evidence. (The run history this also read, and the
    `#selftest` rows it skipped, are gone: S-31.)
    """
    problems: list[str] = []
    warnings: list[str] = []

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
    orphaned = sorted({v.gate for v in ledger.verdicts if v.gate not in known_gates})
    if orphaned and known_gates:
        # They still reach the page and the report as rows nothing here can
        # re-run: a readiness fact worth a line, not an integrity failure.
        warnings.append(
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
    return problems, warnings


# --------------------------------------------------------------------------- #
# doctor: what rho cannot see
# --------------------------------------------------------------------------- #
#: How many items one doctor row names before `(+n more)`. Why 4: the
#: ledger-integrity row's budget, so every row reads the same way — enough to
#: tell one planted problem from a pattern. Rejected: every item (one row per
#: omc gate in an openmodelica project would bury the rest of the screen); one
#: (cannot say whether a problem is local or everywhere).
_DOCTOR_SHOWN = 4

#: The resolver's notes, by the doctor row that owns each (`verdicts.resolve`
#: words them; `read_entries`/`read_controls` word the per-file ones). First match
#: wins; a note no pattern claims lands in `cache-entries`, so nothing the
#: resolver said is dropped on the way to the screen.
_NOTE_ROWS = (
    ("two-outcomes", re.compile(r"^\S+: two outcomes recorded for identical inputs \(.+\)$")),
    ("two-controls", re.compile(
        r"^\S+: two control outcomes recorded for identical inputs \(.+\)$")),
    ("instruments", re.compile(r"^\S+ — recorded under \S+ \S+; here \S+$"
                               r"|outcome differs across instruments")),
    ("opaque-inputs", re.compile(r"^\S+ — opaque inputs: ")),
    ("code-digest", re.compile(r"^\S+ — code digested as its defining file")),
    ("pending", re.compile(r"^\S+ — control inputs moved \(")),
)

#: The names through which gate code reaches the process environment. What
#: slipped through, as a residual the spec names (§3.17, §8): an environment
#: read fires no audit event, so no trace recorded it and no entry keyed on
#: it — a gate whose limit comes from `os.environ` stayed Fresh when the
#: variable changed (review round 1, `probe.env`). A read inside a window is now
#: named opaque (`env:<NAME>`, `verdicts._RecordingEnviron`), so such a gate
#: re-runs on every check; this row still names each one, because that is a
#: cost, and because a module-level `LIMIT = os.getenv(...)` is read once, at
#: import, before any window — the likeliest spelling, and seen by nothing
#: else. `putenv` writes, and is here because a gate that sets a variable is
#: handing a later gate an input the same way. Rejected: a proxy OBJECT put in
#: place of `os.environ` during a gate (it changes the environment a gate's
#: subprocess inherits — the omc gates depend on it; the recorder changes the
#: class of the same object instead); scanning only the gate function's own
#: body (the import-time read above).
_ENV_NAMES = frozenset({"environ", "environb", "getenv", "getenvb", "putenv"})


def _listed(items: list[str], sep: str = "; ") -> str:
    """The first `_DOCTOR_SHOWN` of `items`, then how many more."""
    shown = sep.join(items[:_DOCTOR_SHOWN])
    return shown + (f" (+{len(items) - _DOCTOR_SHOWN} more)" if len(items) > _DOCTOR_SHOWN else "")


def _source_files(fn: Any) -> list[str]:
    """The Python files a gate's code is: its recorded closure (`modelio`), or —
    for a gate registered from Python, with none — the file it was defined in."""
    closure = modelio.code_closure(fn)
    if closure is not None:
        return [path for path, _sha in closure.files]
    code = getattr(getattr(fn, "__func__", fn), "__code__", None)
    name = getattr(code, "co_filename", "") or ""
    if name and not (name.startswith("<") and name.endswith(">")) and os.path.isfile(name):
        return [os.path.abspath(name)]
    return []


def _parse(path: str, cache: dict[str, Any]) -> Any:
    """`ast.parse` of `path`, once per command; None when it cannot be read."""
    if path not in cache:
        try:
            with open(path, "rb") as fh:
                cache[path] = ast.parse(fh.read(), path)
        except (OSError, SyntaxError, ValueError):
            cache[path] = None
    return cache[path]


def _top_names(nodes: Iterable[Any]) -> set[str]:
    """The top-level module names the `Import`/`ImportFrom` nodes among `nodes`
    bring in. A relative import names a file beside it, never a third party."""
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            names.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.add(node.module.partition(".")[0])
    return names


def _outside_functions(node: Any) -> Iterable[Any]:
    """Every node under `node` that runs when its module is imported: function
    and lambda bodies are skipped, class bodies are not."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield child
        yield from _outside_functions(child)


def _reached_imports(tree: Any, name: str) -> set[str] | None:
    """The imports inside module-level function `name` and every module-level
    function or class it names, transitively; None when `name` is not a
    module-level definition of `tree` (the caller then counts the whole file)."""
    defs = {node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    if name not in defs:
        return None
    seen: set[str] = set()
    queue = [name]
    found: set[str] = set()
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        for node in ast.walk(defs[current]):
            if isinstance(node, ast.Name) and node.id in defs and node.id not in seen:
                queue.append(node.id)
        found |= _top_names(ast.walk(defs[current]))
    return found


def _gate_imports(fn: Any, cache: dict[str, Any]) -> set[str]:
    """The top-level names a gate's code imports on its way to a verdict.

    In the file that defines the gate: what the module runs on import, plus what
    the gate function — and every module-level function or class it names,
    transitively — imports inside its body. In every other file of its closure:
    all of it (a helper's lazy import is a dependency of whoever calls the
    helper, and a static walk cannot say who does). Measured on the bundled
    corpus, which is why the defining file is read by reach and not whole:
    `cad.bounding` is the one tier-0 gate of a module whose other gates import
    trimesh inside their own bodies, and a whole-file rule named it — a warning
    nobody could act on (declaring trimesh would make the one gate that runs
    without a mesh library skip where it is missing).
    """
    target = getattr(fn, "__func__", fn)
    code = getattr(target, "__code__", None)
    home = os.path.abspath(code.co_filename) if code is not None else ""
    names: set[str] = set()
    for path in _source_files(fn):
        tree = _parse(path, cache)
        if tree is None:
            continue
        if os.path.abspath(path) == home:
            reached = _reached_imports(tree, getattr(code, "co_name", ""))
            if reached is not None:
                names |= _top_names(_outside_functions(tree)) | reached
                continue
        names |= _top_names(ast.walk(tree))
    return names


def _undeclared_imports(registry: gates.Registry) -> list[str]:
    """`<gate> imports <module, ...>` for every gate whose code imports a
    third-party module its spec does not declare (`requires_python`, or a
    `python:` entry of `requires_one_of`).

    What slipped through without it: availability reads only what a gate
    DECLARES, so an undeclared import is run where the module is missing and
    raises — an error (a FAIL, and CI red for a tooling gap, spec §5 risk 5)
    where a declared one reads SKIPPED, BLOCKED, with its reason. Third party
    means what the recorded closure says it is (`CodeRef.third_party`: not the
    standard library, not nopekit, not code — a file under the gate's own
    roots, or beside them and not installed, is code). Measured on every
    bundled gate: zero (`test_doctor`).
    """
    cache: dict[str, Any] = {}
    found: list[str] = []
    for spec, fn in registry.pairs():
        third = set(verdicts.code_digest(spec, fn).third_party)
        if not third:
            continue
        declared = {str(name).strip().partition(".")[0]
                    for name in (spec.requires_python or ())}
        for entry in spec.requires_one_of or ():
            kind, _, module = str(entry).partition(":")
            if kind.strip() == "python":
                declared.add(module.strip().partition(".")[0])
        missing = sorted((_gate_imports(fn, cache) & third) - declared)
        if missing:
            found.append(f"{spec.id} imports {', '.join(missing)}")
    return found


def _hits_by_file(scan: Callable[[Any], Any]) -> Callable[[str], list]:
    """`scan` over each file's parsed tree once, however many gates share it:
    `hits(path)` is `scan(tree)` the first time and the same list after, `[]`
    for a file that does not parse. What slipped through (holdout, general
    packs): the static rows scanned a module once per gate defined in it, so a
    pack of eighteen gates in one module walked its tree eighteen times per
    row, and the scans were most of what `doctor` cost once its seal row ran
    tier 0 only. The rows say the same: each gate still names every hit of
    every file it is made of."""
    trees: dict[str, Any] = {}
    found: dict[str, list] = {}

    def hits(path: str) -> list:
        if path not in found:
            tree = _parse(path, trees)
            found[path] = list(scan(tree)) if tree is not None else []
        return found[path]
    return hits


def _env_hits(tree: Any) -> list[tuple[int, str]]:
    """`(line, "os.<name>")` for every reach into the environment in `tree`:
    `os.environ`, `os.getenv`, `os.putenv` (and the bytes twins) through any
    alias of `os`, and a `from os import <name>` of one of them."""
    aliases = {"os"}
    hits: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(alias.asname or "os" for alias in node.names if alias.name == "os")
        elif isinstance(node, ast.ImportFrom) and node.module == "os" and not node.level:
            hits.update((node.lineno, f"os.{alias.name}") for alias in node.names
                        if alias.name in _ENV_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _ENV_NAMES \
                and isinstance(node.value, ast.Name) and node.value.id in aliases:
            hits.add((node.lineno, f"os.{node.attr}"))
    return sorted(hits)


def _env_reads(registry: gates.Registry, root: str) -> list[str]:
    """`<file>:<line> os.<name> (<gates>)` for every environment read in the code
    of every registered gate — its module and its closure (spec §3.17). Static,
    because nothing dynamic sees it: see `_ENV_NAMES`. Zero bundled hits."""
    owners: dict[tuple[str, int, str], list[str]] = {}
    hits = _hits_by_file(_env_hits)
    for spec, fn in registry.pairs():
        for path in _source_files(fn):
            for line, name in hits(path):
                owners.setdefault((path, line, name), []).append(spec.id)
    base = os.path.abspath(root)
    lines = []
    for (path, line, name), gate_ids in sorted(owners.items()):
        shown = os.path.relpath(path, base).replace(os.sep, "/") \
            if path.startswith(base + os.sep) else path
        lines.append(f"{shown}:{line} {name} ({', '.join(sorted(set(gate_ids)))})")
    return lines


#: The constructors a module-level memo is spelled with, by the name called
#: (`dict()`, `collections.OrderedDict()`, `defaultdict(list)` alike). A display
#: or comprehension counts too. Rejected: every module-level assignment (the
#: bundled packs keep dozens of constant tables, and a warning on each would be
#: noise nobody reads); only `{}` (the likeliest spelling, and `defaultdict` is
#: the next).
_MEMO_CONTAINERS = frozenset({"dict", "list", "set", "bytearray", "defaultdict", "OrderedDict",
                              "Counter", "deque", "ChainMap", "WeakValueDictionary",
                              "WeakKeyDictionary", "WeakSet"})

#: The calls that WRITE into a container. A memo is a container filled from a
#: function body; one that is only read there is a constant table. `pop`,
#: `clear` and `remove` are left out: they evict, and a module that only evicts
#: has nothing to serve.
_MEMO_WRITES = frozenset({"setdefault", "update", "append", "extend", "insert", "add",
                          "appendleft", "extendleft", "__setitem__", "__ior__"})


def _is_container(node: Any) -> bool:
    if isinstance(node, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else \
            func.attr if isinstance(func, ast.Attribute) else ""
        return name in _MEMO_CONTAINERS
    return False


def _module_level(body: list) -> Iterable[Any]:
    """The statements that run at import in module scope: the module body and
    the bodies of its `if`/`try`/`with`/`for`/`while`, never a def or a class."""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        for name in ("body", "orelse", "finalbody"):
            yield from _module_level(getattr(node, name, None) or [])
        for handler in getattr(node, "handlers", None) or ():
            yield from _module_level(handler.body)


def _written_into(func: Any, name: str) -> int | None:
    """The line of the first write into container `name` inside `func` — an item
    assigned, augmented or deleted, or a `_MEMO_WRITES` call — else None."""
    for node in ast.walk(func):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)) \
                and isinstance(node.value, ast.Name) and node.value.id == name:
            return node.lineno
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in _MEMO_WRITES and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == name:
            return node.lineno
    return None


def _memo_hits(tree: Any) -> list[tuple[int, str]]:
    """`(line, name)` for every module-global memo in `tree` that the spine
    cannot empty (`modelio.clear_caches` reaches functools' memos only): a
    module-level container written into from a function body that does not
    make the name its own; a module global a function rebinds under `global`;
    a mutable default argument its function writes into. Each lives as long as
    the process, so the first gate to fill it reads the file and every later
    one does not."""
    containers = set()
    for node in _module_level(tree.body):
        if isinstance(node, ast.Assign) and _is_container(node.value):
            containers.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node.value is not None \
                and _is_container(node.value) and isinstance(node.target, ast.Name):
            containers.add(node.target.id)
    hits: set[tuple[int, str]] = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        args = func.args
        params = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
        params += [a.arg for a in (args.vararg, args.kwarg) if a is not None]
        declared = {n for node in ast.walk(func) if isinstance(node, ast.Global)
                    for n in node.names}
        bound = {node.id for node in ast.walk(func)
                 if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))}
        for name in declared & bound:
            line = next(node.lineno for node in ast.walk(func)
                        if isinstance(node, ast.Name) and node.id == name
                        and isinstance(node.ctx, (ast.Store, ast.Del)))
            hits.add((line, name))
        for name in containers:
            if name in params or (name in bound and name not in declared):
                continue
            line = _written_into(func, name)
            if line is not None:
                hits.add((line, name))
        positional = args.posonlyargs + args.args
        defaults = list(zip(positional[len(positional) - len(args.defaults):], args.defaults))
        defaults += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]
        for arg, default in defaults:
            if _is_container(default):
                line = _written_into(func, arg.arg)
                if line is not None:
                    hits.add((line, arg.arg))
    return sorted(hits)


def _memo_reads(registry: gates.Registry, root: str) -> list[str]:
    """`<file>:<line> <name> (<gates>)` for every module-global memo the spine
    cannot empty, in the code of every registered gate — its module and its
    closure. Static, like `_env_reads`, and for the same reason: a memo hit
    opens nothing, so no trace sees what it served. Zero bundled hits."""
    owners: dict[tuple[str, int, str], list[str]] = {}
    hits = _hits_by_file(_memo_hits)
    for spec, fn in registry.pairs():
        for path in _source_files(fn):
            for line, name in hits(path):
                owners.setdefault((path, line, name), []).append(spec.id)
    base = os.path.abspath(root)
    lines = []
    for (path, line, name), gate_ids in sorted(owners.items()):
        shown = os.path.relpath(path, base).replace(os.sep, "/") \
            if path.startswith(base + os.sep) else path
        lines.append(f"{shown}:{line} {name} ({', '.join(sorted(set(gate_ids)))})")
    return lines


def _selftest_sources(owner: str) -> list[str]:
    """Every `*.py` under `owner/selftest/` — the fixtures and the known-good
    module a gate's control runs — without `__pycache__` or dot-directories."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(owner, "selftest")):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not d.startswith("."))
        found.extend(os.path.join(dirpath, name) for name in sorted(filenames)
                     if name.endswith(".py"))
    return found


def _dynamic_imports(registry: gates.Registry, root: str) -> list[str]:
    """`<file>:<line> <call> (<gates>)` for every import whose module a VALUE
    names (`modelio.dynamic_imports`: `importlib.import_module(name)`,
    `__import__(f"...")`) in the code of every registered gate — its module and
    its closure — and in the `selftest/` of its owner (the fixtures and the
    known-good module its control runs). Static, like `_env_reads`, and for the
    same reason: which module such a call loads, only the run knows. The first
    run of a process that loads it keys its source; a later one is served it
    from `sys.modules` and keys nothing (admission review, round 2, c: a limit
    behind `importlib.import_module` moved and the PASS was served cached). A
    literal name is keyed with the gate's code; `load_path` keys a helper on
    every run. Zero bundled hits."""
    owners: dict[tuple[str, int, str], list[str]] = {}
    walked: dict[str, list[str]] = {}
    hits = _hits_by_file(modelio.dynamic_imports)
    for spec, fn in registry.pairs():
        owner = verdicts._owner_dir(fn, root)
        if owner not in walked:
            walked[owner] = _selftest_sources(owner)
        for path in dict.fromkeys(_source_files(fn) + walked[owner]):
            for line, call in hits(path):
                owners.setdefault((path, line, call), []).append(spec.id)
    base = os.path.abspath(root)
    lines = []
    for (path, line, call), gate_ids in sorted(owners.items()):
        shown = os.path.relpath(path, base).replace(os.sep, "/") \
            if path.startswith(base + os.sep) else path
        lines.append(f"{shown}:{line} {call} ({', '.join(sorted(set(gate_ids)))})")
    return lines


def _cache_notes(root: str, resolution: verdicts.Resolution) -> dict[str, list[str]]:
    """The resolver's notes, sorted into the doctor rows that own them — plus a
    strict read of every control entry on disk, because the resolver reads a
    gate's controls only while its verdict is Fresh, and a disagreeing or
    hand-edited control must not wait for that to be seen."""
    notes = list(resolution.notes)
    base = os.path.join(store.nopekit_dir(root), verdicts._VERDICTS_DIR)
    try:
        gate_dirs = sorted(name for name in os.listdir(base)
                           if os.path.isdir(os.path.join(base, name)))
    except OSError:
        gate_dirs = []
    for gate_id in gate_dirs:
        try:
            verdicts.read_controls(root, gate_id, problems=notes)
        except NopekitError as exc:
            notes.append(f"{gate_id}: {exc}")
    sorted_notes: dict[str, list[str]] = {}
    for note in dict.fromkeys(notes):
        row = next((name for name, pattern in _NOTE_ROWS if pattern.search(note)),
                   "cache-entries")
        sorted_notes.setdefault(row, []).append(note)
    return sorted_notes


#: `verdicts.read_controls`' note for two control entries at one rho_control:
#: the gate and the entries' names, read back to judge the pair
#: (`_two_control_notes`). *Rejected:* a second read of every gate's controls
#: grouped by rho here — two copies of the grouping rule, and a note the
#: resolver wrote that this row then never classified.
_TWO_CONTROLS = re.compile(r"^(\S+): two control outcomes recorded for identical inputs "
                           r"\((.+)\)$")


def _seen_reads_moved(control: Any, now: Any, *, have_model: bool) -> str:
    """Why a run now could not read what ``control`` recorded, or ``""``:
    `verdicts._control_moved` — the files and listings either half read, and
    the host params a live-host fixture read — over what nopekit can see. An
    opaque channel is dropped first: the admission never calls an opaque
    control current, but what nopekit cannot see it cannot call moved, so it
    is no reason to call a pair history. Host reads are dropped too when the
    model did not load: then nothing here can say they moved."""
    reads = {k: v for k, v in (control.reads or {}).items() if k != "opaque"}
    if not have_model:
        reads["host"] = []
    good = control.good
    if isinstance(good, Mapping) and isinstance(good.get("reads"), Mapping):
        good = {**good, "reads": {k: v for k, v in good["reads"].items() if k != "opaque"}}
    return verdicts._control_moved(dataclasses.replace(control, reads=reads, good=good), now)


def _two_control_notes(found: list[str], root: str, registry: gates.Registry,
                       resolution: verdicts.Resolution, projection: Any
                       ) -> tuple[list[str], list[str]]:
    """``(live, history)``: the two-controls notes split by whether a run at
    the gate's current version and inputs could land on their rho_control.
    rho_control keys the static part (`verdicts.control_static`: the spine, the
    gate's code, its owner's `selftest/` walk, the control's fields) and what
    both halves read, so a pair at another static part, or on a file, listing
    or host param that reads otherwise now (`_seen_reads_moved`), is history: no
    current run can serve it or write beside it, and entries are never
    rewritten. Each history item says which. Everything else stays live, the
    row's FAIL: a note this cannot read back, a pair whose entries it cannot
    find, a static part it cannot compute, and a pair at the current version
    whose only reads it cannot check (an opaque channel; a ledger key, which
    the next check run settles by running the fixture). An evaluator not
    registered here is history: nothing here runs it.

    What slipped through (holdout, general packs, round 2): `check` wrote two
    control outcomes for a pack's gate at one rho; the pack's fix moved its
    code, and `check` qualified it "paired" at the new version — and `doctor`
    stayed red, "the gate is not deterministic, and neither answer counts", on
    the two old entries, for good: no command removes a cache entry. *Rejected:*
    the admission's own pool (`verdicts._admission` keeps it to itself, and it
    leaves out an opaque control, which would call a real conflict history);
    the static part alone (a pair on a moved data file would stay red for good,
    as the code fix did); warning on history (a row that is always there and
    never actionable: git keeps the entries, and no admission at the current
    version stands on them)."""
    live: list[str] = []
    history: list[str] = []
    entries: dict[str, dict] = {}
    statics: dict[str, str | None] = {}
    now: list[Any] = []
    for note in found:
        match = _TWO_CONTROLS.match(note)
        if match is None:
            live.append(note)
            continue
        gate_id, names = match.group(1), match.group(2).split(", ")
        shown = f"{gate_id}: two control outcomes ({', '.join(names)})"
        pair = registry.get(gate_id)
        if pair is None:
            history.append(f"{shown} of an evaluator not registered here")
            continue
        if gate_id not in entries:
            try:
                entries[gate_id] = {c.name: c for c in verdicts.read_controls(root, gate_id)}
            except NopekitError:
                entries[gate_id] = {}
        group = [entries[gate_id][n] for n in names if n in entries[gate_id]]
        if gate_id not in statics:
            try:
                statics[gate_id] = verdicts.control_static(pair[0], pair[1], root,
                                                           anchors=resolution.anchors)[0]
            except (NopekitError, OSError):
                statics[gate_id] = None
        static = statics[gate_id]
        if not group or static is None:
            live.append(note)
            continue
        at_version = [c for c in group if c.static == static]
        if not at_version:
            history.append(f"{shown} at another version of the evaluator")
            continue
        if not now:
            now.append(verdicts._Now(root, projection, None, anchors=resolution.anchors,
                                     digests=FileDigests()))
        moved = [_seen_reads_moved(c, now[0], have_model=projection is not None)
                 for c in at_version]
        if all(moved):
            history.append(f"{shown} on inputs that moved since ({moved[0]})")
        else:
            live.append(note)
    return live, history


def _doctor_cache_rows(results: list[dict], root: str, registry: gates.Registry,
                       resolution: verdicts.Resolution, projection: Any = None) -> None:
    """The rows for what the verdict cache knows and rho cannot key: one row each,
    `ok` when there is nothing to say, so a clean project shows it looked."""
    notes = _cache_notes(root, resolution)

    found = notes.get("instruments", [])
    _check(results, "instruments", "warn" if found else "ok",
           _listed(found) + " — provenance, never staleness: the entry stays current"
           if found else "every cached verdict was recorded under the library versions "
                         "installed here")

    found = notes.get("opaque-inputs", [])
    # The full channel list lives here and nowhere else (issue #15, D1):
    # `status`, `site build` and the page print a count and one example.
    n_opaque = len(found)
    _check(results, "opaque-inputs", "warn" if found else "ok",
           report.HUMAN["unseen"]["doctor"].format(
               n=n_opaque, evaluators=report._plural(n_opaque, "evaluator"),
               read=report._plural(n_opaque, "reads", "read"),
               their=report._plural(n_opaque, "its", "their"),
               results=report._plural(n_opaque, "result"),
               are=report._plural(n_opaque, "is", "are"), rows=_listed(found))
           if found else "no cached verdict read through a channel the tracer cannot see")

    found = notes.get("cache-entries", [])
    _check(results, "cache-entries", "warn" if found else "ok",
           _listed(found) + " — an ignored entry never counts; its gate re-runs"
           if found else "every cache entry reads back strictly")

    # Two answers for identical inputs. A gate's was a warning while
    # `TWO_OUTCOMES_IS_ERROR` was staged, and is a failure since U25 flipped it
    # (spec §3.7, R-4: an error only once EntriesAreDeterministic had proven the
    # bundled corpus deterministic); read at call time, so the flip was one
    # constant. A control's always was a failure: its gate is not admitted —
    # while a current run could land on it; a pair no current run can reach is
    # history, named after the failures and never one (`_two_control_notes`).
    outcomes = notes.get("two-outcomes", [])
    controls, history = _two_control_notes(notes.get("two-controls", []), root, registry,
                                           resolution, projection)
    failing = bool(controls) or (bool(outcomes) and verdicts.TWO_OUTCOMES_IS_ERROR)
    if controls or outcomes:
        detail = (_listed(controls + outcomes) + " — the same inputs gave two answers: the "
                  "gate is not deterministic, and neither answer counts")
    elif history:
        detail = ("no gate recorded two outcomes for identical inputs at its current version "
                  "and inputs")
    else:
        detail = "no gate recorded two outcomes for identical inputs"
    if history:
        detail += ("; history, not a failure: " + _listed(history)
                   + " — no run at the evaluator's current version and inputs can reach "
                     "these entries")
    _check(results, "two-outcomes", "FAIL" if failing else "warn" if outcomes else "ok",
           detail)

    found = notes.get("code-digest", [])
    _check(results, "code-digest", "warn" if found else "ok",
           _listed(found) if found else "every gate's code was recorded as it loaded")

    pending, moved = _pending(resolution)
    _check(results, "pending-controls", "warn" if pending else "ok",
           f"{_pending_sentence(len(pending), moved)}: {_listed(pending, ', ')}" if pending
           else "no evaluator is waiting to re-qualify")

    found = _undeclared_imports(registry)
    _check(results, "imports", "warn" if found else "ok",
           _listed(found) + " — undeclared, so where it is missing the gate errors "
                            "instead of reading SKIPPED; add it to requires_python"
           if found else "every third-party module a gate imports is declared")

    found = _env_reads(registry, root)
    _check(results, "env-reads", "warn" if found else "ok",
           _listed(found) + " — an environment variable is never a cache key: a read "
                            "while the gate runs makes its entry opaque (re-run on every "
                            "check), one at import is seen by nothing; pass the value "
                            "through the model"
           if found else "no gate's code reads the environment")

    found = _memo_reads(registry, root)
    _check(results, "memos", "warn" if found else "ok",
           _listed(found) + " — a module-level memo outlives the gate that filled it: a "
                            "file read behind it is opened by the first gate to ask and by "
                            "no later one, so no cache entry keys it; share a file through "
                            "ctx.load_file, which records it for every caller"
           if found else "no gate's code keeps a module-level memo the spine cannot empty")

    found = _dynamic_imports(registry, root)
    _check(results, "dynamic-imports", "warn" if found else "ok",
           _listed(found) + " — a module a value names is keyed only by the run that "
                            "first loads it in a process; a later one is served it and "
                            "keys nothing: name it with a string literal, or load it with "
                            "nopekit.modelio.load_path"
           if found else "every module a gate or its control imports is named where it "
                         "is imported")


def _doctor_qualification_rows(results: list[dict], root: str, registry: gates.Registry,
                               resolution: verdicts.Resolution, *, projection: Any = None,
                               ledger: Any = None) -> None:
    """The two rows qualification adds (P2.3, GLOSSARY §2's words).

    `qualification`: every evaluator unqualified at its version, named with the
    first fact that does not hold — a problem; else every evaluator not yet
    qualified at its version — never qualified (`not-yet`), or qualified at an
    earlier version only (undemonstrated: a code edit moved its static part) —
    a warning, worded apart, never under the unqualified template (GLOSSARY §6
    keeps *unqualified* for a qualification that does not hold), with how it
    qualifies: the next check run, or — for a dependent its prerequisite
    prunes, whose qualification no check run reaches — once that prerequisite
    is established. A pending re-qualification is the pending-controls row's.
    What slipped through (review of P2.3, ``b3``): the row read only
    `not-admitted`, so after a code edit every evaluator was undemonstrated
    while this said "every evaluator qualified at its version"; and a pruned
    dependent was promised "the next check run qualifies it" by a check run
    that would prune it again.

    `known-good`: once per project, the project evaluators with no known-good
    control — no `selftest/known_good.py` AND no `good=` fixture of their own
    (D4's resolution order: a declared `good=` comes first) — each reads
    `known-good not run`, and its claim Gap. What slipped through (review of
    P2.3, ``p3``): it counted every project gate whenever `known_good.py` was
    missing, so a project whose one gate declared `good=` — `check` ready,
    every claim Checked — failed doctor, exit 1, "every project evaluator reads
    known-good not run". The tag column stays `doctor`'s until GLOSSARY §9's
    word pass (`[problem]`/`[ok]`, on the check-in list)."""
    q = report.HUMAN["qualification"]["doctor"]
    unqualified: list[str] = []
    waiting: list[str] = []
    unseen: list[str] = []
    pruned = {v.gate: (list(v.blocked_by or ()) or [""])[0] for v in resolution.verdicts
              if getattr(v, "blocked_by", None)}
    for gate_id in registry.ids():
        row = resolution.rows.get(gate_id)
        admission = row.admission if row is not None else None
        if admission is None:
            # A stale entry's row and a gate with no entry carry none: asked of
            # the records, as `gate show` asks (it runs nothing).
            spec, fn = registry.get(gate_id)
            admission = verdicts.admission_state(root, spec, fn, projection=projection,
                                                 ledger=ledger, anchors=resolution.anchors)
        kind = verdicts.parse_token(admission.reason)[0]
        if report.unseen_qualification(admission):
            # Issue #15, review of D1: qualified by the check run that ran its
            # controls, which read inputs nopekit cannot see — never "the next
            # check run qualifies it", which every check run since had.
            unseen.append(gate_id)
        elif admission.state == "undemonstrated" or (admission.state == "not-admitted"
                                                     and kind == verdicts.NOT_YET):
            spec_, _fn = registry.get(gate_id)
            token = admission.reason if kind == verdicts.NOT_YET else _not_yet_token(spec_)
            how = report.qualification_reason(token, pruned_by=pruned.get(gate_id, ""))
            waiting.append(f"{gate_id} ({how})")
        elif admission.state == "not-admitted":
            unqualified.append(f"{gate_id} ({report.qualification_reason(admission.reason)})")
    if unqualified:
        text = q["unqualified"].format(n=len(unqualified), list=_listed(unqualified, ", "))
        if waiting:
            text += q["also_waiting"].format(n=len(waiting))
        _check(results, "qualification", "FAIL", text)
    elif waiting or unseen:
        said = ([q["not_yet"].format(n=len(waiting), list=_listed(waiting, ", "))]
                if waiting else [])
        if unseen:
            n = len(unseen)
            said.append(report.HUMAN["unseen"]["doctor_q"].format(
                n=n, evaluators="evaluator" if n == 1 else "evaluators",
                their="its" if n == 1 else "their", rows=_listed(unseen, ", ")))
        _check(results, "qualification", "warn", "; ".join(said))
    else:
        _check(results, "qualification", "ok", q["ok"])
    missing = [] if os.path.isfile(os.path.join(root, "selftest", "known_good.py")) else [
        spec.id for spec in registry.specs()
        if not (spec.pack or "").strip()
        and not (getattr(spec.negative_control, "good", "") or "").strip()]
    # The row's name is the table's word too (D-16): `known-good` is
    # qualification's, and a channel that typed it would be a second copy.
    _check(results, report.HUMAN["qualification"]["known_good"], "FAIL" if missing else "ok",
           q["known_good"].format(n=len(missing), list=_listed(missing, ", "))
           if missing else q["known_good_ok"])


#: The costliest tier of pack control `doctor` runs by default (`--tier`). Why
#: 0: tier 0 is "< ~2 s, analytic" (`models.Tier`), so the seal row costs
#: seconds however many packs a project installs, and the first command to run
#: when confused answers at once. What slipped through (holdout, general packs,
#: three projects): the row ran every pack control at every tier, firmware-ci's
#: container replays included, so `doctor` took nine to twelve minutes and said
#: nothing while it waited on another project's replay lock. Above the tier, a
#: control on record at its version is read, not run: `check` ran it against
#: this project's params, and its entry keeps what it read of them.
#: *Rejected:* every tier (the cost above); tier 1 (a geometry build or a
#: netlist per control is still minutes on a multi-pack project); a time box
#: (a row that says sealed or not depending on the machine's load); leaving a
#: control above the tier unnamed (a row that says "none reads them" over
#: controls it never saw).
DOCTOR_SEAL_TIER = int(Tier.INSTANT)


def _recorded_seal(root: str, registry: gates.Registry, spec: Any,
                   resolution: verdicts.Resolution, projection: Any,
                   ledger: Any) -> tuple[str, Any]:
    """What a pack control's entries at its current version say it read of this
    project's params, without running it (``host`` "live": recorded against this
    project). One of:

    ``("finding", SealFinding)`` — the entry at the gate's current static part
    that its admission stands on (else, with none, any on record at it) read the
    host's params, whatever that admission is now;
    ``("sealed", None)`` — admitted or pending, and none read any;
    ``("stale", None)`` — an entry at its version is on record and read none,
    but its inputs moved since (undemonstrated, or never qualified at them);
    ``("refused", None)`` — the same, but not admitted at inputs that are
    current, or a crash remembered at its version (the qualification row says
    why);
    ``("none", None)`` — no entry at its version.

    The admission is the one the qualification row reads (`resolution`, else
    `verdicts.admission_state`, which runs nothing). Admitted or pending means
    its static part and the host params it read are current, so a ``reads.host``
    with nothing in it is what the control reads of this project now. A host
    read needs no such currency: the static part holds the gate's code, its
    owner's ``selftest/`` (where a pack's fixtures live) and the control's
    declaration, so a fixture that read the host at it reads the host whatever
    value it found. What slipped through (review of H6): only an
    admitted or pending entry was read, and an unsealed control's entry keys
    the host params it read, so the first edit of the design left it
    undemonstrated and the row said "ok ... no control entry at its version"
    over an entry that named the host read. A control the host defuses (its
    known-bad input inside the limit, not admitted) was passed over the same
    way. *Rejected:* every entry at the version, unioned (entries are never
    rewritten, so a fixture fixed in a helper outside the static part would
    read unsealed after `check` re-ran it, for good)."""
    row = resolution.rows.get(spec.id)
    admission = row.admission if row is not None else None
    found = registry.get(spec.id)
    if admission is None:
        if found is None:                           # pragma: no cover - defensive
            return "none", None
        admission = verdicts.admission_state(root, found[0], found[1], projection=projection,
                                             ledger=ledger, anchors=resolution.anchors)
    entry = admission.entry
    if entry is not None:
        # `_admission` chooses an entry only at the current static part: the
        # latest there, current or not, as the qualification row reads it.
        at_version = [entry] if getattr(entry, "host", "") == "live" else []
    else:
        # None chosen: no entry at the version, or a control failure remembered
        # at it (a crash), which may sit beside entries written before it.
        live = [c for c in verdicts.read_controls(root, spec.id)
                if getattr(c, "host", "") == "live"]
        if not live or found is None:
            return "none", None
        static = verdicts.control_static(found[0], found[1], root,
                                         anchors=resolution.anchors)[0]
        at_version = [c for c in live if c.static == static]
    if not at_version:
        return "none", None
    paths = tuple(sorted({packs._host_path(tuple(r[0]))
                          for c in at_version
                          for r in ((getattr(c, "reads", None) or {}).get("host") or ())
                          if isinstance(r, (list, tuple)) and r
                          and isinstance(r[0], (list, tuple))}))
    if paths:
        nc = spec.negative_control
        return "finding", packs.SealFinding(gate=spec.id,
                                            fixture=(nc.fixture if nc else "") or "",
                                            host_paths=paths)
    if admission.state in ("admitted", "pending"):
        return "sealed", None
    if admission.state == "not-admitted" \
            and verdicts.parse_token(admission.reason)[0] != verdicts.NOT_YET:
        return "refused", None
    return "stale", None


#: How `_doctor_seal_row` says a control above the tier that it neither ran nor
#: read: the kind `_recorded_seal` returned, and the clause after "not run:
#: above tier N, and". What slipped through (review of H6): every kind but
#: "sealed" read "no control entry at its version", false over an entry on
#: record at it.
_SEAL_LEFT_OUT = (
    ("none", "no control entry at its version"),
    ("stale", "its control entry at its version is on record but not current"),
    ("refused", "its control entry at its version is on record but not admitted"),
)


def _tag_carriers(collision: Any) -> str:
    """``carried by C3, C4`` or ``no claim carries it yet``."""
    if not collision.carried:
        return "no claim carries it yet"
    return f"carried by {_listed(list(collision.carried), ', ')}"


def _doctor_tags_row(results: list[dict], registry: gates.Registry, ledger: Any) -> None:
    """`pack-tags`: each claim tag that gates of two or more installed packs bind
    (`packs.tag_collisions`), naming the gates of each pack and the claims that
    carry it. A warning, never a FAIL: a gate lists every tag it bears on by
    design, and the fix is the claim's — the narrowest tag (docs/PACK_FORMAT.md).
    Always a row, so a clean project shows it looked. What slipped through
    (holdout, general packs): `doctor` diffed the packs' param keys and not
    their tags, so a claim tagged `connectivity` to mean "every net is routed"
    read a CAD assembly check's fail, and nothing had named the binding."""
    found = packs.tag_collisions(registry, getattr(ledger, "claims", None) or ())
    if not found:
        _check(results, "pack-tags", "ok",
               "no claim tag binds gates of more than one installed pack")
        return
    rows = [f"{c.tag!r} binds {c.binds()} ({_tag_carriers(c)})" for c in found]
    _check(results, "pack-tags", "warn",
           f"{len(found)} claim tag(s) bind gates of more than one installed pack: "
           + _listed(rows) + " — a claim that carries one is covered by every one of "
                             "those gates: give it a tag only the pack you mean binds "
                             "(each pack's PACK.md lists its tags)")


def _tag_lines(view: Ledger, registry: Any) -> list[str]:
    """`check`'s `note:` line for each claim tag a claim carries that gates of
    two or more installed packs bind (`packs.tag_collisions`). Never a status
    and never the exit code; `doctor`'s `pack-tags` row names the tags no claim
    carries yet too."""
    lines = []
    for c in packs.tag_collisions(registry, view.claims):
        if not c.carried:
            continue
        one = len(c.carried) == 1
        lines.append(f"note: {_listed(list(c.carried), ', ')} carr{'ies' if one else 'y'} "
                     f"the tag {c.tag!r}, which gates of {len(c.gates)} installed packs "
                     f"bind: {c.binds()} — give {'it' if one else 'each'} a tag only the "
                     f"pack you mean binds")
    return lines


def _doctor_tier(args: argparse.Namespace) -> int:
    """`doctor --tier`, else `DOCTOR_SEAL_TIER`."""
    tier = getattr(args, "tier", None)
    return DOCTOR_SEAL_TIER if tier is None else int(tier)


def _doctor_seal_row(results: list[dict], registry: gates.Registry,
                     host: gates.GateContext, *, root: str,
                     resolution: verdicts.Resolution, projection: Any = None,
                     ledger: Any = None, tier: int = DOCTOR_SEAL_TIER) -> None:
    """Invariant 5 at runtime: the pack controls up to `tier` run against THIS
    project's params, traced (`packs.seal_findings`), and each one above it read
    from its control entry at its version (`_recorded_seal`), which `check`
    recorded against these params. `pack validate` refuses an unsealed fixture
    before a pack ships; this is where a project finds out that one it
    installed — from before the detector, or from someone else — reads its host,
    so that its control fires here and may not fire in the next project. A
    temp `out_dir`, nothing written to the project. A control neither run nor
    read is named, with why (`_SEAL_LEFT_OUT`) and the command that runs it
    (`DOCTOR_SEAL_TIER`); one at or below the tier whose tool is missing here is
    read the same way, and counted apart from the ones above it."""
    pack_gates = [spec for spec in registry.specs() if (spec.pack or "").strip()]
    if not pack_gates:
        _check(results, "sealed-fixtures", "ok", "no pack gates installed")
        return
    try:
        findings = packs.seal_findings(registry, host, tier=tier)
    except NopekitError as exc:
        _check(results, "sealed-fixtures", "FAIL", f"the seal probe did not run: {exc}")
        return
    ran = missing = read_above = read_here = 0
    left_out: dict[str, dict[str, int]] = {}
    for spec in pack_gates:
        available = gates.availability(spec)[0]
        above = int(spec.tier) > tier
        if not above and available:
            ran += 1
            continue
        kind, finding = _recorded_seal(root, registry, spec, resolution, projection, ledger)
        if kind == "finding":
            findings.append(finding)
        elif kind == "sealed" and above:
            read_above += 1
        elif kind == "sealed":
            # At or below the tier, its tool missing here: read, and never
            # counted "above it" (review of H6: a tier-0 gate whose python
            # module is missing read as one above the tier).
            read_here += 1
        elif not available:
            missing += 1
        else:
            left_out.setdefault(kind, {})[spec.id] = int(spec.tier)
    said = [f"{ran} pack control(s) up to tier {tier} run against this project's params"]
    if read_above:
        said.append(f"{read_above} above it read from their control entries at their version")
    if read_here:
        said.append(f"{read_here} up to it not run here (tools missing), read from their "
                    f"control entries at their version")
    tail = ""
    for kind, clause in _SEAL_LEFT_OUT:
        ids = left_out.get(kind)
        if ids:
            tail += (f"; {len(ids)} not run: above tier {tier}, and {clause} "
                     f"({_listed(sorted(ids), ', ')})")
    if left_out:
        top = max(t for ids in left_out.values() for t in ids.values())
        tail += f" — `nopekit doctor --tier {top}` runs them"
    if missing:
        tail += f"; {missing} not run: tools missing here"
    if findings:
        shown = [f"{f.gate}: {f.fixture} reads the host's "
                 f"{', '.join(f.host_paths[:3])}"
                 + (f" (+{len(f.host_paths) - 3} more)" if len(f.host_paths) > 3 else "")
                 for f in findings]
        _check(results, "sealed-fixtures", "FAIL",
               _listed(shown) + " — a pack control must build its known-bad input from its "
                                "own selftest/baseline.json (SEALED, invariant 5)" + tail)
        return
    _check(results, "sealed-fixtures", "ok", ", ".join(said) + "; none reads them" + tail)


#: The legacy run history's directory, `.nopekit/runs/`. Nothing reads it
#: since checkpoint 1.2 (git and the verdict cache are the history, S-89); the
#: migration leaves it where it was, and `doctor` names it until it is removed.
_LEFTOVER_RUNS = "runs"


def _record_problems(root: str) -> list[str]:
    """Every record file the strict reader refuses, one sentence each — the
    project file first, then each kind's files in order. `[]` on a legacy
    project, whose one file is the ledger (its refusal is the load's own)."""
    if store.is_legacy(root):
        return []
    try:
        meta = store.read_project(root)
    except NopekitError as exc:
        return [str(exc)]
    problems: list[str] = []
    for kind in store.RECORD_DIRS:
        try:
            files = store._record_files(root, kind)
        except NopekitError as exc:
            problems.append(str(exc))
            continue
        for _stem, path in files:
            try:
                store.read_record(path, kind, model_entry=meta.model_entry)
            except NopekitError as exc:
                problems.append(str(exc))
    return problems


def _doctor_records_rows(results: list[dict], root: str, ledger: Ledger) -> None:
    """The rows about where the project's facts live: `records` (the layout, and
    what a legacy ledger will become), `run-history` (a leftover `runs/`), and
    `index` (does `.nopekit/ledger.json` agree with the records?).

    Each one WRITES NOTHING, and that is the design, not a limitation: a doctor
    that migrated a legacy project, or rebuilt the index it is comparing, would
    hide the problem from the next run — so a legacy ledger is migrated in
    memory only (`store.migrate_legacy(apply=False)`, the plan `check` would
    carry out) and "will migrate on next command" is said, not done. An index
    behind the records is a warning: it is what a hand edit leaves until the
    next command rebuilds it, and the records are the truth either way."""
    legacy = store.is_legacy(root)
    counts = (f"{len(ledger.claims)} claims, {len(ledger.params)} "
              f"{'params' if legacy else 'param records'}, "
              f"{len(ledger.inputs)} artifacts, {len(ledger.needs)} needs, "
              f"{len(ledger.decisions)} decisions")
    if legacy:
        plan = store.migrate_legacy(root, apply=False, when="",
                                    model_prose=modelio.static_param_prose)
        kinds: dict[str, int] = {}
        for rel_path in plan.files:
            kind = rel_path.split("/", 1)[0]
            if kind in store.RECORD_DIRS:
                kinds[kind] = kinds.get(kind, 0) + 1
        into = ", ".join(f"{kinds[k]} {k}/" for k in store.RECORD_DIRS if kinds.get(k))
        _check(results, "records", "warn",
               f"legacy {store.NOPEKIT_DIR}/{store.LEDGER_NAME} ({counts}), read in "
               f"memory — it will migrate on next command that writes (check, or a shim: "
               f"ingest, extract, decide, packs add, claim physical) into "
               f"{into or 'no record files'} and {store.NOPEKIT_DIR}/{store.PROJECT_NAME}; "
               f"doctor writes nothing")
    else:
        _check(results, "records", "ok", f"{counts} — every record file reads strictly")

    # The row names the directory and never opens a file in it: a corrupt run
    # file is no command's problem (`test_status_stale.RunHistoryIsGone` holds
    # doctor to never naming one), and the directory's name is said as `runs/`
    # under `.nopekit/` for the same test, which reads any row naming
    # `.nopekit/runs` as a reader of the history.
    runs = os.path.join(store.nopekit_dir(root), _LEFTOVER_RUNS)
    if os.path.isdir(runs):
        _check(results, "run-history", "warn",
               f"`{_LEFTOVER_RUNS}/` is left in `{store.NOPEKIT_DIR}/` from the run "
               f"history, and nothing reads it any more (git and the verdict cache are "
               f"the history) — remove it with `git rm -r`, when you are ready")

    if legacy:
        _check(results, "index", "ok",
               f"none: on a legacy project {store.NOPEKIT_DIR}/{store.LEDGER_NAME} is "
               f"still the records")
        return
    if not os.path.isfile(store.ledger_path(root)):
        _check(results, "index", "ok",
               f"not written yet — the next command writes {store.NOPEKIT_DIR}/"
               f"{store.LEDGER_NAME} from the records")
        return
    try:
        behind = store.agree(root)
    except (NopekitError, OSError) as exc:
        _check(results, "index", "warn", f"could not be compared with the records: {exc}")
        return
    _check(results, "index", "warn" if behind else "ok",
           f"{store.NOPEKIT_DIR}/{store.LEDGER_NAME} is behind the records: "
           f"{_listed(behind)} — any command but doctor rebuilds it; the records are "
           f"the truth" if behind else "agrees with the records")


def _doctor_results_rows(results: list[dict], root: str, ledger: Ledger) -> None:
    """`doctor`'s `results` rows (P2.5a-D21), over EVERY results file — the
    claims' and those no claim file holds (`Ledger.removed`): physical results
    recorded before they were bound to articles — their passes count for
    nothing until recorded again in a person's own shell — evidence files
    changed or missing since they were recorded, each worded for what it does
    to its entry, and a fail no claim file holds, a problem. One ok row when
    there is nothing to say. (A results file whose seal or chain is broken is
    refused by the strict reader, and named in the records rows with its fix.)

    What slipped through (review of P2.5a): the rows looped over the claims, so
    a claim file renamed away from its sealed fail left `doctor` printing
    "every physical result is sealed" beside a fail no reader looked at; and a
    fail's changed photo was told "its pass does not count", inviting a second
    fail — a fail counts whatever happens to its photo (R-3)."""
    said = report.HUMAN["physical"]
    legacy: list[str] = []
    moved: list[str] = []
    orphaned: list[str] = []
    removed = list(getattr(ledger, "removed", ()) or ())
    for claim in [*ledger.claims, *removed]:
        for entry in getattr(claim, "results", ()) or ():
            if not entry.channel and entry.passed is True:
                legacy.append(claim.id)
            for rel_path, digest in sorted((entry.evidence_sha256 or {}).items()):
                full = os.path.join(root, *str(rel_path).split("/"))
                now = sha256_file(full) if os.path.isfile(full) else None
                if now != digest:
                    moved.append(said["doctor_evidence"].format(
                        id=claim.id, path=report._one(rel_path),
                        how="is missing" if now is None else "changed",
                        then=said["doctor_evidence_pass" if entry.passed is True
                                  else "doctor_evidence_fail"]))
    for claim in removed:
        fails = [entry for entry in claim.results if entry.passed is not True]
        if fails:
            orphaned.append(said["doctor_removed"].format(
                id=claim.id, n=len(fails), detail=report._trunc(fails[-1].detail or
                                                                "no detail recorded", 80)))
        else:
            _check(results, "results", "warn", said["doctor_removed_passes"].format(
                id=claim.id))
    if legacy:
        ids = ", ".join(dict.fromkeys(legacy))
        _check(results, "results", "warn",
               f"{len(legacy)} pass(es) recorded before results were bound to articles "
               f"({ids}): they count for nothing until the person who tested each "
               f"records it again in their own shell")
    for line in moved:
        _check(results, "results", "warn", line)
    for line in orphaned:
        _check(results, "results", "FAIL", line)
    if not legacy and not moved and not removed:
        _check(results, "results", "ok",
               "every physical result is sealed and its evidence is as recorded")


#: The compound statements whose bodies run at module level when the module does.
_BLOCKS = tuple(getattr(ast, kind) for kind in ("If", "For", "AsyncFor", "While", "With",
                                                "AsyncWith", "Try", "TryStar")
                if hasattr(ast, kind))


def _bound_at_module(tree: Any, name: str) -> bool | None:
    """Whether module ``tree`` binds ``name`` at module level — a ``def`` or an
    ``async def``, a class, an assignment, an import, at the top or inside a
    top-level ``if``/``try``/``with``/``for``/``while`` — as `_load_generator`'s
    ``getattr`` would find it; ``None`` when a star import or a module
    ``__getattr__`` could bind it unseen. What slipped through (review of P2.5b,
    finding 14): only a top-level ``def`` counted, so ``print_package =
    side_profile`` read FAIL "has no function" while `export` loaded and ran
    it. *Rejected:* importing the module here (`doctor` runs no project code
    it can avoid; the generator runs at `export`)."""
    found = False
    unseen = False

    def names_of(target: Any) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            return [n for item in target.elts for n in names_of(item)]
        if isinstance(target, ast.Starred):
            return names_of(target.value)
        return []

    def visit(body: Iterable[Any]) -> None:
        nonlocal found, unseen
        for node in body:
            bound: list[str] = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound = [node.name]
                if node.name == "__getattr__":
                    unseen = True
            elif isinstance(node, ast.Assign):
                bound = [n for target in node.targets for n in names_of(target)]
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                bound = names_of(node.target)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name == "*":
                        unseen = True
                    else:
                        bound.append(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                bound = names_of(node.target)
            if isinstance(node, (ast.With, ast.AsyncWith)):
                bound += [n for item in node.items if item.optional_vars is not None
                          for n in names_of(item.optional_vars)]
            if name in bound:
                found = True
            if isinstance(node, _BLOCKS):
                for field in ("body", "orelse", "finalbody"):
                    visit(getattr(node, field, None) or ())
                for handler in getattr(node, "handlers", None) or ():
                    visit(handler.body)

    visit(getattr(tree, "body", ()) or ())
    return True if found else (None if unseen else False)


def _doctor_requirement_rows(results: list[dict], ledger: Ledger) -> None:
    """`doctor`'s `requirements` row (SPEC decision 12), always present, writing
    nothing: a claim naming a requirement no file holds, or a requirement id that
    is also a claim's — problems, as a milestone requiring a missing claim is;
    else a requirement no claim serves — a warning (it reads Gap); else ok."""
    said = report.HUMAN["requirement"]
    held = {r.id for r in ledger.requirements}
    claim_ids = {c.id for c in ledger.claims}
    problems = [said["doctor_dangling"].format(claim=claim.id, id=rid)
                for claim in ledger.claims for rid in claim.requirements if rid not in held]
    problems += [f"requirements/{r.id}.json: {r.id} is also a claim id "
                 f"(claims/{r.id}.json) — rename one; why {r.id} would be a guess"
                 for r in ledger.requirements if r.id in claim_ids]
    for problem in problems:
        _check(results, "requirements", "FAIL", problem)
    if problems:
        return
    served = {rid for claim in ledger.claims for rid in claim.requirements}
    unserved = [r.id for r in ledger.requirements if r.id not in served]
    if unserved:
        _check(results, "requirements", "warn", said["doctor_unserved"].format(
            ids=_listed(unserved, ", "),
            gap=report.word(ClaimStatus.UNCLAIMED)))
    elif ledger.requirements:
        _check(results, "requirements", "ok",
               said["doctor_ok"].format(n=len(ledger.requirements)))
    else:
        _check(results, "requirements", "ok", said["doctor_none"])


def _doctor_people_row(results: list[dict], root: str, ledger: Ledger) -> None:
    """`doctor`'s `people` row (architecture phase 6, SPEC §0.3), always present,
    writing nothing, never a FAIL: whether a round here would commit — the
    people listed, and whether git's identity here is one of them. A project
    that commits nothing is not broken; it is a project whose history the
    person must keep by hand, and they should know.

    Every row's words go through ``format``, the none-listed row's too: its
    sentence spells the record's braces doubled, as every `HUMAN` template
    does. What slipped through (follow-up after phase 3b): that row alone was
    printed raw, and doctor told a person to add ``{{"name": ..., "email":
    ...}}`` to the project file."""
    said = report.HUMAN["round"]
    people = list(getattr(ledger.meta, "people", None) or [])
    names = ", ".join(f"{p.name} <{p.email}>" for p in people[:4]) \
        + (f", +{len(people) - 4} more" if len(people) > 4 else "")
    if not people:
        _check(results, "people", "warn", said["people_none"].format())
        return
    if not vcs.is_repo(root):
        _check(results, "people", "warn", said["people_no_repo"].format(n=len(people)))
        return
    who = vcs.ident(root)
    committer = vcs.committer_ident(root)
    if who is None or committer is None:
        _check(results, "people", "warn", said["people_no_identity"].format(n=len(people)))
    elif rounds.listed(people, who) is None:
        _check(results, "people", "warn", said["people_not_listed"].format(
            n=len(people), names=names, who=who))
    elif rounds.listed(people, committer) is None:
        _check(results, "people", "warn", said["people_committer_not_listed"].format(
            n=len(people), names=names, who=committer))
    else:
        _check(results, "people", "ok", said["people_ok"].format(n=len(people), names=names))


def _doctor_graph_rows(results: list[dict], root: str, ledger: Ledger, view: Ledger,
                       registry: Any, resolution: verdicts.Resolution, model: Any,
                       projection: Any, model_error: str) -> Any:
    """`doctor`'s `graph` row (architecture phase 3a, SPEC 19), always present,
    writing nothing: one FAIL per record pointer the graph cannot follow
    (`store.pointer_problems`, which the index names too) and per `PARAMS`
    grounding that names neither an input nor an assumption; else the graph's
    size and what it rests on. A pointer that is wrong makes no edge — a
    `rests on` that silently vanished would read as a pass resting on nothing."""
    problems = store.pointer_problems(ledger)
    input_ids = {a.id for a in ledger.inputs}
    kinds = {c.id: getattr(c.kind, "value", str(c.kind)) for c in ledger.claims}
    recorded = {p.name for p in ledger.params}
    views = _shown_params(root, ledger, model, model_error, resolution, registry,
                          projection=projection)
    for view_ in views:
        if view_.name in recorded:
            continue                       # its record's pointers are named above
        for aid in view_.grounded_by:
            found = store.grounded_by_problem(view_.home or view_.name, view_.name, aid,
                                              input_ids, kinds)
            if found:
                problems.append(found)
    for problem in problems:
        _check(results, "graph", "FAIL", problem)
    if problems:
        return None
    traced = _trace_for(root, model, projection, resolution)
    built = graph.of(root, view, resolution, registry, projection=projection, model=model,
                     views=views, bases=_input_bases(root, ledger), trace=traced.trace)
    said = report.HUMAN["round"]
    detail = said["graph_ok"].format(
        n=len(built.nodes), m=len(built.edges),
        k=sum(1 for e in built.edges if e.type == "rests_on"),
        r=sum(1 for e in built.edges if e.type == "resolves"))
    if built.trace is None:
        detail += said["graph_untraced"]
    else:
        detail += said["graph_traced"].format(traced=decisions.traced_words(built.trace_round))
    _check(results, "graph", "ok", detail)
    return built


def _doctor_groups_row(results: list[dict], root: str, ledger: Ledger, view: Ledger,
                       registry: Any, resolution: verdicts.Resolution, model: Any,
                       projection: Any, model_error: str, built: Any = None) -> None:
    """`doctor`'s `groups` row (architecture phase 5, SPEC 17), always present:
    FAIL on what the index names (`store.group_problems`: a board member no
    claim holds, a claim on two boards, a value in two zones or stations) and,
    when the model loads, a zone member that is not a choice or a station
    member that is not a derived value — the judgement the index cannot make;
    else a warning for an empty group, for claims on no board beside board
    records, for more generated stations than the map reads well, and for a
    group id that is also a decision's or a milestone's (`why` shows the other
    first); else the groups' shape."""
    problems = list(store.group_problems(ledger))
    groups = list(ledger.groups or ())
    if isinstance(projection, Mapping) and not model_error:
        config = dict(projection.get("config") or {})
        derived = set(graph.derived_names(projection))
        for group in store.groups_in_order(groups, "zone"):
            for name in group.members:
                if name not in config:
                    close = difflib.get_close_matches(name, list(config), n=1, cutoff=0.6)
                    problems.append(f"zone {group.id} names {name}, which is not a choice of "
                                    f"the model"
                                    + (f" (did you mean {close[0]}?)" if close else ""))
        for group in store.groups_in_order(groups, "station"):
            for name in group.members:
                if name not in derived:
                    close = difflib.get_close_matches(name, sorted(derived), n=1, cutoff=0.6)
                    problems.append(f"station {group.id} names {name}, which is not a derived "
                                    f"value of the model"
                                    + (f" (did you mean {close[0]}?)" if close else ""))
    if problems:
        shown = "; ".join(problems[:3]) + (f"; +{len(problems) - 3} more"
                                          if len(problems) > 3 else "")
        _check(results, "groups", "FAIL", shown)
        return
    warns: list[str] = []
    for group in groups:
        if not group.members:
            warns.append(f"groups/{group.id}.json holds no members")
    boards = store.groups_in_order(groups, "board")
    if boards:
        on_board = {cid for board in boards for cid in board.members}
        loose = [c.id for c in ledger.claims if c.id not in on_board]
        if loose:
            warns.append(f"{len(loose)} claim(s) on no board ({', '.join(loose[:6])}"
                         f"{', ...' if len(loose) > 6 else ''}): the page shows them on the "
                         f"board Other")
    try:
        if built is None:                   # the graph row's, when it drew one
            built = graph.of(root, view, resolution, registry, projection=projection,
                             model=model, views=_shown_params(
                                 root, ledger, model, model_error, resolution, registry,
                                 projection=projection), bases=_input_bases(root, ledger),
                             trace=_trace_for(root, model, projection, resolution).trace)
        lay = page.layout(view, built, projection=projection, registry=registry,
                          resolution=resolution) if groups or ledger.claims else None
    except Exception:                       # noqa: BLE001 - a row, never a crash of doctor
        lay = None
    if lay is not None:
        generated = sum(1 for s in lay.stations if s.generated)
        if generated > page.IMPLICIT_STATIONS_WARN:
            warns.append(f"{generated} shared values became stations on the map: name the ones "
                         f"that matter in groups/<id>.json (\"kind\": \"station\")")
    clash = {d.id for d in ledger.decisions} | {m.id for m in ledger.milestones}
    for group in groups:
        if group.id in clash:
            warns.append(f"group {group.id} has the id of a decision or a milestone: `why "
                         f"{group.id}` shows the other first")
    if warns:
        _check(results, "groups", "warn", "; ".join(warns[:3])
               + (f"; +{len(warns) - 3} more" if len(warns) > 3 else ""))
        return
    if not groups:
        _check(results, "groups", "ok", "no groups: every claim shows on one board")
        return
    counts = {kind: len(store.groups_in_order(groups, kind))
              for kind in ("board", "zone", "station")}
    _check(results, "groups", "ok",
           f"{counts['board']} board(s), {counts['zone']} zone(s), {counts['station']} "
           f"station(s); every claim sits on one board" if boards else
           f"{counts['zone']} zone(s), {counts['station']} station(s); no board records: "
           f"every claim shows on one board")


def _doctor_readme_row(results: list[dict], root: str) -> None:
    """`doctor`'s `readme` row (SPEC 36): a warning, never a FAIL, when
    `README.md` lacks the Made-with-nopekit block — a project made before
    `init` wrote it; `nopekit site badge` adds it."""
    try:
        with open(os.path.join(root, "README.md"), "r", encoding="utf-8",
                  errors="replace") as fh:
            text = fh.read()
    except OSError:
        text = None
    if text is None or not store.readme_has_badge(text):
        _check(results, "readme", "warn", "README.md has no Made-with-nopekit block: "
                                          "`nopekit site badge` adds it at the bottom")
    else:
        _check(results, "readme", "ok", "README.md carries the Made-with-nopekit block")


def _doctor_round_row(results: list[dict], root: str, ledger: Ledger, model: Any,
                      projection: Any, resolution: verdicts.Resolution, registry: Any,
                      view: Ledger, model_error: str) -> None:
    """`doctor`'s `round` row (architecture phase 3a, SPEC 18): the round
    record's state, never a FAIL — it is an output, and nothing reads it for
    truth. Warns when it is behind, edited by hand, unreadable or newer."""
    found = _round_state(root, ledger, model, projection, resolution, registry, view,
                         model_error=model_error)
    _check(results, "round", "ok" if found.state in ("absent", "current") else "warn",
           _round_state_line(found))


def _trace_lines(root: str, found: derivation.TraceRead) -> list[str]:
    """The sentence `status` prints, and `doctor`'s row warns with, when the
    trace is not the current design's (architecture phase 3b) — ``[]`` when
    it is current and every file of its key reads: the clean bracket's status
    is unchanged."""
    said = report.HUMAN["trace"]
    lines: list[str] = []
    for path, state, _line in found.problems:
        lines.append(said["status_edited"].format(path=path, state=state))
    if found.state == "unrecorded":
        lines.append(said["status_unrecorded"].format(why=said["unrecorded_why"]))
    elif found.state == "none":
        pid = derivation.live_holder(root)
        held = derivation.scratch_state(root, found.key)
        if pid is not None:
            lines.append(said["status_running"].format(pid=pid, log=derivation.LOG_REL))
        elif held.get("incomplete"):
            suggest = max(float(held.get("timeout") or 0.0) * 2.0,
                          derivation.BUILD_TIMEOUT_FLOOR)
            lines.append(said["status_incomplete"].format(k=len(held["incomplete"]),
                                                          s=suggest))
        elif not found.problems:
            lines.append(said["status_none"])
    return lines


def _amber_line(found: derivation.TraceRead,
                states: Mapping[str, derivation.Amber]) -> str:
    """`status`'s ``amber:`` line: each Checked claim whose amber is not
    ``holds``, capped at eight — or ``""``. An untraced claim is named only
    when the design has a trace (otherwise the ``trace:`` line says it)."""
    said = report.HUMAN["trace"]
    items = []
    for cid, amber in states.items():
        if amber.state == "holds" or (amber.state == "untraced" and found.state != "current"):
            continue
        items.append(said["status_item"].format(id=cid, words=derivation.amber_words(amber)))
    if not items:
        return ""
    shown = " · ".join(items[:8]) + (said["and_more"].format(n=len(items) - 8)
                                     if len(items) > 8 else "")
    return said["amber_head"].format(items=shown)


def _doctor_trace_row(results: list[dict], root: str, ledger: Ledger, model: Any,
                      projection: Any, resolution: verdicts.Resolution, registry: Any,
                      view: Ledger, built: Any) -> None:
    """`doctor`'s `trace` row (architecture phase 3b), never a FAIL: the trace
    is an observation the map draws, not a record. Ok when the current design
    has one; a warning with `status`'s sentence otherwise, for an edited file,
    and past twenty trace files of other designs."""
    if model is None:
        return
    found = _trace_for(root, model, projection, resolution)
    said = report.HUMAN["trace"]
    warns = [line.split(": ", 1)[1] if line.startswith("trace: ") else line
             for line in _trace_lines(root, found)]
    count, size = derivation.other_designs(root, found.key)
    if count > 20:
        warns.append(said["doctor_others"].format(m=count, size=human_bytes(size)))
    if warns:
        _check(results, "trace", "warn", "; ".join(warns))
        return
    if found.trace is None:
        return
    trace = found.trace
    _check(results, "trace", "ok", said["doctor_ok"].format(
        n=len(trace.observed), k=len(trace.skipped), a=len(trace.after), t=len(trace.ties)))


def _doctor_decision_rows(results: list[dict], root: str, ledger: Ledger) -> None:
    """`doctor`'s `decisions` row (SPEC 14), always present, writing nothing and
    never a FAIL: a return condition or an input naming no record, a preference
    file no decision holds, and each preferred_against rejection its person has
    not recorded — warnings, since none moves a status."""
    said = report.HUMAN["rejection"]
    claim_ids = {c.id for c in ledger.claims}
    input_ids = {a.id for a in ledger.inputs}
    decision_ids = {d.id for d in ledger.decisions}
    warnings: list[str] = []
    unrecorded: list[str] = []
    m = 0
    for decision in ledger.decisions:
        for i, item in enumerate(decision.rejected or ()):
            m += 1
            where = f"decisions/{decision.id}.json rejected[{i}]"
            cond = getattr(item, "returns_if", None)
            cid = getattr(cond, "claim", "") if cond is not None else ""
            if cid and cid not in claim_ids:
                warnings.append(said["doctor_dangling"].format(
                    where=where + ".returns_if", what=repr(cid), home=f"claims/{cid}.json"))
            aid = getattr(item, "input", "") or ""
            if aid and aid not in input_ids:
                warnings.append(said["doctor_dangling"].format(
                    where=where + ".input", what=repr(aid), home=f"inputs/{aid}.json"))
            state = claims.preference_state(decision, item)
            if state is not None and state.state != "recorded":
                unrecorded.append(said["doctor_unrecorded_item"].format(
                    decision=decision.id, value=report._one(item.value),
                    name=report._one(state.name)))
    for stem, _path in store._record_files(root, "preferences"):
        if stem not in decision_ids:
            warnings.append(said["doctor_orphan"].format(id=stem))
    if unrecorded:
        warnings.append(said["doctor_unrecorded"].format(k=len(unrecorded),
                                                         items=_listed(unrecorded)))
    if warnings:
        _check(results, "decisions", "warn", _listed(warnings))
    elif m:
        _check(results, "decisions", "ok", said["doctor_ok"].format(
            n=len(ledger.decisions), m=m))
    else:
        _check(results, "decisions", "ok", said["doctor_none"])


def _doctor_basis_rows(results: list[dict], root: str, ledger: Ledger, registry: Any) -> None:
    """`doctor`'s `basis` row (SPEC 38), always present, never a FAIL: the
    evaluators that declare no basis (they show as undeclared), and the inputs
    naming a proposer no discoverable pack declares."""
    said = report.HUMAN["basis"]
    undeclared = gates.registry_summary(registry)["undeclared_basis"]
    found = packs.proposers(root) if any(a.proposer for a in ledger.inputs) else {}
    orphans = [a.id for a in ledger.inputs if a.proposer and a.proposer not in found]
    warnings: list[str] = []
    if undeclared:
        warnings.append(said["doctor_undeclared"].format(n=len(undeclared),
                                                         ids=_listed(undeclared, ", ")))
    if orphans:
        warnings.append(said["doctor_inputs"].format(k=len(orphans),
                                                     ids=_listed(orphans, ", ")))
    if warnings:
        _check(results, "basis", "warn", "; ".join(warnings))
    else:
        _check(results, "basis", "ok", said["doctor_ok"])


def _doctor_milestone_rows(results: list[dict], root: str, ledger: Ledger) -> None:
    """`doctor`'s P2.5b rows, each writing nothing:

    * `milestones` — a required id no claim file holds (the milestone reads
      unresolved, `missing`, and no export can write), a generator that does not
      resolve to a `.py` file and a function in it — problems;
    * `exports` — a package file in `out/<m>/` edited after its export, or one
      no export wrote (a problem: the next export refuses to replace it), a
      leftover `.tmp-`/`.old-` scratch directory (a problem), and — a note —
      the last export's package not here (`out/` is an ignored output: a fresh
      clone has none);
    * `report` — a leftover `docs/readiness.md`, which nothing writes since
      `REPORT.md` replaced it (S-41): a warning, never deleted (a command
      removing a tracked file it did not write is invariant 8's failure)."""
    claim_ids = {c.id for c in ledger.claims}
    problems = []
    warnings = []
    for milestone in ledger.milestones:
        missing = [cid for cid in milestone.requires if cid not in claim_ids]
        if missing:
            problems.append(f"milestones/{milestone.id}.json requires {', '.join(missing)}, "
                            f"which no claim file holds — {milestone.id} can never be ready")
        if milestone.generator:
            try:
                path, name = store.generator_parts(milestone.generator)
            except NopekitError as exc:
                problems.append(f"milestones/{milestone.id}.json: {exc}")
                continue
            full = os.path.join(root, *path.split("/"))
            if not os.path.isfile(full):
                problems.append(f"milestones/{milestone.id}.json: its generator {path} does "
                                f"not exist")
                continue
            try:
                with open(full, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                problems.append(f"milestones/{milestone.id}.json: its generator {path} does "
                                f"not parse ({exc})")
                continue
            bound = _bound_at_module(tree, name)
            if bound is None:
                warnings.append(f"milestones/{milestone.id}.json: {path} may bind {name} in a "
                                f"way doctor cannot see without running it — nopekit "
                                f"export {milestone.id} --dry-run loads it")
            elif not bound:
                problems.append(f"milestones/{milestone.id}.json: {path} has no function "
                                f"{name}")
    for problem in problems:
        _check(results, "milestones", "FAIL", problem)
    for warning in warnings if not problems else ():
        _check(results, "milestones", "warn", warning)
    if not problems and not warnings:
        n = len(ledger.milestones)
        _check(results, "milestones", "ok",
               f"{n} milestone(s), each requiring claims that exist" if n
               else "none declared — a milestone is milestones/<name>.json")

    base = os.path.join(root, store.PACKAGES_NAME)
    rows = []
    notes = []
    try:
        leftovers = sorted(name for name in os.listdir(base)
                           if name.startswith(".") and any(
                               mark in name for mark in (".tmp-", ".old-", ".new-", ".copy-")))
    except OSError:
        leftovers = []
    for name in leftovers:
        rows.append(f"{store.PACKAGES_NAME}/{name} is a package an export built or set "
                    f"aside and did not finish — remove it")
    for milestone in ledger.milestones:
        mine = [e for e in ledger.exports if e.milestone == milestone.id]
        here = os.path.isdir(os.path.join(base, milestone.id))
        if not mine and not here:
            continue
        if not here:
            notes.append(f"the last export of {milestone.id}'s package is not here "
                          f"({store.PACKAGES_NAME}/ is an output git ignores) — nopekit "
                          f"export {milestone.id} writes it again")
            continue
        # A package with no export record is judged too (review of P2.5b,
        # finding 9: `if not mine: continue` read "each package as it was
        # written" over one the next export refuses whole).
        for kind, rel_path in milestones.package_problems(root, milestone.id, ledger.exports):
            rows.append(f"{store.PACKAGES_NAME}/{milestone.id}/{rel_path} "
                        + ("was edited after its export" if kind == "edited"
                           else "was not written by an export")
                        + f" — the next export of {milestone.id} refuses to replace it")
    for row in rows:
        _check(results, "exports", "FAIL", row)
    if not rows:
        _check(results, "exports", "ok",
               "; ".join(notes) if notes else
               f"{len(ledger.exports)} export(s) recorded, each package as it was written")

    leftover = store.project_paths(root)["readiness"]
    if os.path.isfile(leftover):
        _check(results, "report", "warn",
               "docs/readiness.md is no longer written — REPORT.md is, and is ignored: "
               "git rm docs/readiness.md")
    else:
        _check(results, "report", "ok",
               "REPORT.md is the readiness report, an output (`nopekit report --write`)")


def cmd_doctor(args: argparse.Namespace) -> int:
    """Everything that could be wrong with this environment, in one pass.

    This is the first thing anyone runs when confused, so it is diagnostic rather
    than decorative: every row names what was checked, what was found, and — when
    it is wrong — what to do. It survives every failure it reports (a broken
    pack, a model that will not import, a corrupt cache entry), because a doctor
    that dies on the first problem cannot tell you about the second.

    Since 1.2 it is also where a human learns what the verdict cache cannot key
    on and the resolver will not say in a status line (spec §4 U23): entries
    recorded under other library versions, opaque channels, ignored entries, two
    outcomes for one input, unsealed pack controls, undeclared third-party
    imports, environment reads, module-level memos, imports of a module a value
    names, gates keyed by their defining file, controls pending
    re-verification, and verdicts of gates no longer registered. None of
    those changes a claim's status by itself, which is exactly why `check` and
    `status` are the wrong place to hear about them. No staleness row: which
    gates are current is `status`'s `stale:` block, per gate, from the resolver.
    It never writes, and it runs no project gate — only pack controls, traced,
    into a temp directory, to see whether they read their host: those up to
    `--tier` (default `DOCTOR_SEAL_TIER`, 0: seconds), and none above it. A
    control above the tier is read from its control entry at its version, which
    `check` recorded against this project's params, and one with no such entry
    is named with the command that runs it (`_doctor_seal_row`). It also names
    each claim tag that gates of two or more installed packs bind (`pack-tags`,
    `packs.tag_collisions`), from the registry alone.

    From checkpoint 1.3 it also says where the project's facts live, and still
    writes nothing (`_doctor_records_rows`): `records` — the record files, each
    one the strict reader refuses a FAIL row of its own (the load stops at the
    first, and a human needs the list), or, on a legacy project, the ledger read
    in memory and what it "will migrate on next command" into, never migrated
    here; `run-history` — a leftover `.nopekit/runs/`; `index` — whether
    `.nopekit/ledger.json` agrees with the records, never rebuilt here (it is
    the one command `_touch_index` skips). From architecture phase 3a, `graph`
    — the record pointers the typed graph cannot follow, a FAIL each — and
    `round` — the round record's state, a warning when it is behind, edited,
    unreadable or newer, never a FAIL (it is an output).

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
           f"nopekit {__version__} from {os.path.dirname(os.path.abspath(__file__))}")

    # `require_root`'s own message, not a second spelling of it. What slipped
    # through (S-64): this line said "no .nopekit/ in <dir> or any parent" after
    # the marker became a FILE and the walk began stopping at `.git`, so doctor
    # told a user standing beside a bare `.nopekit/`, or in a worktree whose
    # trunk is a project, that the directory it could see was not there.
    try:
        root = store.require_root(getattr(args, "dir", None))
    except NopekitError as exc:
        _check(results, "project", "FAIL", str(exc))
        return _doctor_finish(args, results)
    _check(results, "project", "ok", root)

    try:
        ledger = _load(root)
    except NopekitError as exc:
        # One FAIL row per record the strict reader refuses, never an exit 2:
        # `doctor` is where a human finds out WHICH files, and the load stops at
        # the first.
        for problem in _record_problems(root) or [str(exc)]:
            # A results file refused for a broken seal is its own row (P2.5a-D21):
            # doctor never crashes on it, and it names the file and the fix.
            _check(results, "results" if problem.startswith("results/") else "records",
                   "FAIL", problem)
        return _doctor_finish(args, results)
    _doctor_records_rows(results, root, ledger)
    _doctor_results_rows(results, root, ledger)
    _doctor_milestone_rows(results, root, ledger)
    _doctor_requirement_rows(results, ledger)
    _doctor_people_row(results, root, ledger)

    paths = store.project_paths(root)
    missing = [key for key in ("out", "inputs", "docs", "model")
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
    # A prerequisite nothing registers reads "not registered" at every check and
    # Skips its dependent (P2.2-D14): name each, with the fix. A problem, not a
    # warning — every claim the dependent covers is unresolved until it is.
    for row in summary["unregistered_prerequisites"]:
        _check(results, "prerequisite", "FAIL",
               f"{row['gate']}: prerequisite {row['need']} is not registered — install "
               f"the pack that provides it, or remove the edge")
    # Where the crash rows go once the resolution is known (below): above every
    # row about a missing tool (invariant 2).
    crash_at = len(results)
    for row in summary["unavailable"]:
        _check(results, "gate-tool", "warn", f"{row['gate']}: {row['reason']}")
    for tool in summary["requires_tools"]:
        found = shutil.which(tool)
        _check(results, "tool", "ok" if found else "warn",
               f"{tool}: {found or 'not on PATH — gates needing it will SKIP, not fail'}")
    # The tools the views draw with (issue #15, B.7): a missing one leaves a
    # picture out and moves no status, and its row says so. What slipped
    # through before: `doctor` named only the gates' tools, so a person with
    # no ParaView read every row ok beside a CFD card with no pictures.
    view_registry, view_problems = _view_registry(root, ledger, strict=False)
    for problem in view_problems:
        _check(results, "views", "warn", problem)
    for ok, words in site.view_tool_rows(view_registry, root,
                                         skip=summary["requires_tools"]):
        _check(results, "view-tool", "ok" if ok else "warn", words)

    model = None
    projection = None
    model_error = ""
    if not (ledger.meta.model_entry or "").strip():
        _check(results, "model", "warn", _no_entry(root))
    else:
        try:
            model, projection = _projection(root, ledger)
        except NopekitError as exc:
            model_error = str(exc)
            _check(results, "model", "FAIL", str(exc).replace("\n", " "))
        else:
            _check(results, "model", "ok",
                   f"{model.entry} loads, hash {modelio.model_hash(projection)}, "
                   f"{len(model.params)} params")

    if model is not None:
        try:
            deterministic, detail = modelio.check_determinism(model, 2)
        except NopekitError as exc:
            _check(results, "model-determinism", "FAIL", str(exc).replace("\n", " "))
        else:
            _check(results, "model-determinism", "ok" if deterministic else "FAIL", detail)
        # Over the view `status`, the report and the page judge too — never the
        # model alone, which called a field a record defends undefended while
        # `status` read the records alone and disagreed both ways (review, 1.3).
        undocumented = modelio.undefended_params(
            _param_views(root, ledger, model, model_error))
        _check(results, "model-provenance", "warn" if undocumented else "ok",
               f"{len(undocumented)} param(s) with no rationale: "
               f"{', '.join(undocumented[:6])}" if undocumented
               else "every param carries a rationale")
        # Off the records (`modelio.orphan_params`): a param record whose field
        # the model no longer defines. A parameter the model owns entirely has
        # no record, and is no orphan.
        orphans = modelio.orphan_params(ledger, model)
        _check(results, "model-params", "warn" if orphans else "ok",
               f"{len(orphans)} param record(s) the model no longer defines: "
               f"{', '.join(orphans[:6])} — renamed, or removed and still grounded"
               if orphans else f"{len(model.params)} param(s); every param record "
                               f"names one the model defines")

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

    # Two packs, one TAG: a claim that carries it is covered by the gates of
    # both, so a verdict about another object reads on it. One row, always
    # there, a warning (it moves no status), with the carried tags first.
    _doctor_tags_row(results, registry, ledger)

    # No global staleness row: one hash of the projection against the last
    # sweep's said THAT something moved, never which check it touched, and a
    # model that did not load compared equal (S-21). Which gates are current is
    # `status`'s `stale:` block now, per gate, from the resolver.
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=utcnow_iso(), model=model)
    problems, orphans = _ledger_problems(root, view, registry)
    _check(results, "ledger-integrity", "FAIL" if problems else "ok",
           _listed(problems) if problems else "records all resolve")
    _check(results, "orphan-entries", "warn" if orphans else "ok",
           _listed(orphans) if orphans
           else "every cached verdict belongs to a gate registered here")
    # Each gate whose effective verdict at its current inputs is a crash, in a
    # loud row ABOVE the missing-tool rows, from the resolution alone — no gate
    # runs here. What slipped through (P2.0 F-4): doctor had two rows for a
    # missing tool and none for a gate that crashes, so the command people run
    # when confused ranked the dull case above the broken evaluator. `ERR` is
    # not a FAIL: a crash is a fact about an evaluator, not this environment,
    # and `doctor` exits 1 only for the environment.
    registered = set(registry.ids())
    crashes = [_tagged_row("gate-error", "ERR",
                           f"{v.gate}: errored at its current inputs — "
                           f"{(str(v.error).splitlines() or [''])[0]} "
                           f"(`nopekit gate show {v.gate}`)")
               for v in resolution.verdicts
               if v.gate in registered and v.outcome == "error"
               and not getattr(v, "unqualified", "")]
    results[crash_at:crash_at] = crashes
    _doctor_cache_rows(results, root, registry, resolution, projection)
    _doctor_qualification_rows(results, root, registry, resolution, projection=projection,
                               ledger=ledger)
    # P2.4-D13: an evaluator judging against a limit of its own that is not its
    # claim's — a warning, never a status (S-35) — over current verdicts only.
    # The row is always there (review of P2.4): it appeared only when it warned,
    # so a reader could not tell it had run, and the count of diagnostics moved
    # with the outcome. Each covering evaluator it compared no limit of because
    # its result is not current here is named, with why (`_limits_left_out`).
    parted = _limit_lines(view, resolution.stale_gates)
    said = report.HUMAN["acceptance"]
    if parted:
        detail = said["doctor_warn"].format(
            n=len(parted), list="; ".join(line.split(": ", 1)[1] for line in parted))
    else:
        detail = said["doctor_ok"]
    left_out = _limits_left_out(view, resolution, registry)
    if left_out:
        detail += "; " + left_out
    _check(results, "limits", "warn" if parted else "ok", detail)
    # SPEC decision 13: the evaluators claims' mechanisms name, against the
    # registry — always a row, never a FAIL: a cross-check is not an integrity
    # fault, and it moves no status.
    said = report.HUMAN["mechanism"]
    named = [p for p in claims.mechanism_problems(view, registry)]
    if named:
        _check(results, "mechanisms", "warn", said["doctor_warn"].format(
            n=len(named), list=_listed([report.mechanism_problem_words(p) for p in named])))
    elif any(getattr(c, "evaluators", None) for c in view.claims):
        _check(results, "mechanisms", "ok", said["doctor_ok"])
    else:
        _check(results, "mechanisms", "ok", said["doctor_none"])
    _doctor_decision_rows(results, root, view)
    _doctor_basis_rows(results, root, view, registry)
    built = _doctor_graph_rows(results, root, ledger, view, registry, resolution, model,
                               projection, model_error)
    _doctor_groups_row(results, root, ledger, view, registry, resolution, model, projection,
                       model_error, built=built)
    _doctor_round_row(results, root, ledger, model, projection, resolution, registry, view,
                      model_error)
    _doctor_trace_row(results, root, ledger, model, projection, resolution, registry, view,
                      built)
    _doctor_seal_row(results, registry,
                     _context(root, ledger, model, projection, ALL_TIERS, quiet=True),
                     root=root, resolution=resolution, projection=projection, ledger=ledger,
                     tier=_doctor_tier(args))

    site_info = _site_state(root, resolved=(
        view, registry, resolution,
        _shown_params(root, ledger, model, model_error, resolution, registry,
                      projection=projection), model, projection))
    if site_info["present"]:
        # Only when there is a site. A doctor row about a surface the project
        # never opted into is a row that is always there and never actionable,
        # and the ones that are actionable get read less for it.
        if site_info["error"]:
            _check(results, "site", "FAIL",
                   f"{site_info['state']}: {site_info['error']} — "
                   f"`nopekit site build` rewrites it")
        elif not site_info["built"]:
            _check(results, "site", "warn",
                   "scaffolded but never built — `nopekit site build`")
        elif site_info.get("edited"):
            # Invariant 14: a baked file edited by hand is a FAIL — the page
            # would say what no producer said.
            _check(results, "site", "FAIL", f"{site_info['edited'][0]} — `nopekit site build`")
        else:
            dangling = len(site_info["locator_problems"])
            # Dangling locators are a WARNING, not a failure: the verdicts and
            # claims on the page are still true, and the overlay is the part
            # that is wrong. But never silent — a gate that believes it is
            # drawing and is not looks exactly like a gate that found nothing.
            outline = site_info.get("outline")
            old_shell = outline is None or outline < page.OUTLINE
            # The two pages: a root page from before them is a warning too, in
            # `site build`'s and `site status`'s words.
            old_layout = bool(site_info.get("old_layout"))
            # The text written before the latest results is the `site text` row's,
            # so a stale piece of prose is one warning, never two.
            status = "warn" if (site_info["stale"] or dangling or old_shell
                                or old_layout) else "ok"
            detail = (f"{len(site_info['views'])} view(s), built "
                      f"{site_info['built_at'] or '(time unknown)'}"
                      f"{' (' + site_info['age'] + ')' if site_info['age'] else ''}, "
                      f"{site_info['stale_reason']}")
            if dangling:
                detail += (f" — {dangling} locator(s) point at a view or node that does "
                           f"not exist (`nopekit site status`)")
            if old_shell:
                detail += f" — {site.outline_problem(root)}"
            if old_layout:
                detail += f" — {site.OLD_LAYOUT_WARNING}"
            # A vendor/ that lacks a file the page imports (review of SPEC 40).
            if site_info.get("vendor_missing") and not site_info["vendored"] \
                    and site_info.get("vendor_files"):
                status = "warn"
                detail += f" — three.js {_vendor_words(site_info)}"
            served = _serve_state(root)
            if served.state == "running":
                detail += f" — live at {served.url} (pid {served.pid})"
            _check(results, "site", status, detail)
        prose_rows = [row for row in site_info.get("prose") or ()]
        if prose_rows:
            behind = [row for row in prose_rows
                      if row.get("subject") and row.get("stamp") != "current"]
            loose = [row for row in prose_rows if not row.get("subject")]
            if behind or loose:
                bits = [f"{row['file']} is {'not stamped' if row['stamp'] == 'unstamped' else 'written before the latest results'}: read it again, then `nopekit site stamp {row['subject']}`"
                        for row in behind]
                bits += [f"{row['file']} names no board: it is not on the page" for row in loose]
                _check(results, "site text", "warn", "; ".join(bits[:3])
                       + (f"; +{len(bits) - 3} more" if len(bits) > 3 else ""))
            else:
                _check(results, "site text", "ok",
                       f"{len(prose_rows)} text(s), each stamped against the current results")

    _doctor_readme_row(results, root)

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
    warnings = [row for row in results if row["status"] in ("warn", "ERR")]
    _say(f"{len(results)} checks — {len(failures)} failing, {len(warnings)} warning(s)")
    return 1 if failures else 0


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #
def _common() -> argparse.ArgumentParser:
    """Flags every subcommand carries: `--json` and a repeat of `-C/--dir`.

    `-C` is repeated on the subparsers so both `nopekit -C proj check` and
    `nopekit check -C proj` work — people type both. `default=SUPPRESS` is what
    makes that safe: without it the subparser would write its own `None` over the
    value the top-level parser already stored, and `nopekit -C proj check` would
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
        prog="nopekit",
        description="Claims, gates, packs and an honest readiness report.",
        epilog="`nopekit doctor` is the first thing to run when something is confusing.",
    )
    parser.add_argument("--version", action="version", version=f"nopekit {__version__}")
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
    p.add_argument("--no-git", action="store_true",
                   help="start no git repository here (rounds make no commit outside one)")
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
                   help="gate id, pack name or glob (repeatable); runs it above its tier "
                        "too, with its prerequisites")
    p.add_argument("--force", action="store_true",
                   help="re-run every selected gate and its control, ignoring the "
                        "verdict cache (what CI runs: a cache is re-proven, not trusted)")
    p.add_argument("--no-record", action="store_true",
                   help="a dry sweep: write nothing under .nopekit/ except gate scratch "
                        "in out/ — no cache or control entry, obs, last_check.json or "
                        "index; a legacy ledger migrates in memory only")
    _junit_flag(p, "this run")
    p.set_defaults(func=cmd_check)

    # -- round (architecture phase 3a; the commit and the hook, phase 6) ---- #
    said = report.HUMAN["round"]
    round_help = said["help"].format(path=rounds.ROUND_REL)
    p = sub.add_parser("round", parents=[common], help=round_help, description=round_help)
    p.add_argument("--commit", action="store_true", help=said["commit_help"])
    p.add_argument("--hook", action="store_true", help=said["hook_help"])
    p.set_defaults(func=cmd_round)

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
    p.add_argument("--proposer", default=None, metavar="ID",
                   help=report.HUMAN["propose"]["proposer_help"])
    p.set_defaults(func=cmd_ingest)

    said_propose = report.HUMAN["propose"]
    p = sub.add_parser("propose", parents=[common], help=said_propose["help"],
                       description=said_propose["help"])
    p.add_argument("--kind", choices=[k.value for k in ArtifactKind],
                   help=said_propose["kind_help"])
    p.add_argument("--desc", default="", help=said_propose["desc_help"])
    p.add_argument("--timeout", type=float, default=600.0, metavar="S",
                   help=said_propose["timeout_help"])
    p.add_argument("id", nargs="?", default=None)
    # `*`, never `REMAINDER`: nopekit's own flags after the id stay nopekit's
    # (review of phase 2), and a flag of the script's goes after `--`.
    p.add_argument("args", nargs="*", metavar="ARG", help=said_propose["args_help"])
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("inputs", parents=[common], help="list ingested evidence")
    p.add_argument("--unextracted", action="store_true",
                   help="only the artifacts nobody has read anything out of")
    p.add_argument("--kind", choices=[k.value for k in ArtifactKind])
    p.set_defaults(func=cmd_inputs)

    p = sub.add_parser("extract", parents=[common],
                       help="record what was read out of an artifact, and what it grounds")
    p.add_argument("artifact", help="artifact id from `nopekit inputs`")
    p.add_argument("--what", required=True, help='e.g. "hull beam at midship reads 148 mm"')
    p.add_argument("--grounds", action="append", default=[],
                   help="param names / claim ids this supports (comma-separated or repeated)")
    p.add_argument("--confidence", default="stated",
                   choices=["measured", "scaled", "stated", "inferred"])
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_extract)

    # -- claim ------------------------------------------------------------ #
    # No `claim add` and no `claim edit` (PLAN A-8): a claim is the file
    # `claims/<id>.json`, written and edited as one.
    claim = sub.add_parser("claim", help="list and show claims, and record a physical result "
                                         "(a claim itself is the file claims/<id>.json)")
    claim_sub = claim.add_subparsers(dest="claim_command", metavar="<sub>")
    claim.set_defaults(func=lambda args: _needs_subcommand(claim))

    p = claim_sub.add_parser("list", parents=[common], help="one line per claim")
    p.add_argument("--status", metavar="STATUS",
                   help="a status by its word, token or value: checked, failing, stale, "
                        "assumed, \"pending build\", gap, skipped, open")
    p.add_argument("--kind", choices=[k.value for k in ClaimKind])
    p.add_argument("--tag", default="")
    p.set_defaults(func=cmd_claim_list)

    p = claim_sub.add_parser("show", parents=[common], help="one claim's whole history")
    p.add_argument("id")
    p.set_defaults(func=cmd_claim_show)

    said = report.HUMAN["signing"]
    p = claim_sub.add_parser("physical", parents=[common], help=said["help"],
                             description=said["help"])
    p.add_argument("id")
    p.add_argument("result", nargs="?", choices=["pass", "fail", "assume"],
                   help=said["act_help"])
    outcome = p.add_mutually_exclusive_group()
    outcome.add_argument("--pass", dest="passed", action="store_const", const=True,
                         help=said["pass_help"])
    outcome.add_argument("--fail", dest="passed", action="store_const", const=False,
                         help=said["fail_help"])
    p.set_defaults(passed=None)
    # Refused flags (REFUSED_FLAGS): parsed so a pasted older command meets its
    # reason, never argparse's bare "unrecognized arguments"; never in --help.
    p.add_argument("--when", default=None, help=argparse.SUPPRESS)
    p.add_argument("--who", default=None, help=argparse.SUPPRESS)
    p.add_argument("--detail", default="", help=said["detail_help"])
    p.add_argument("--evidence", action="append", default=[], help=said["evidence_help"])
    p.add_argument("--measured", type=float, default=None, metavar="VALUE",
                   help=said["measured_help"])
    p.add_argument("--authority", default=None, metavar="NAME", help=said["authority_help"])
    p.add_argument("--article", default=None, metavar="HEX", help=said["article_help"])
    p.set_defaults(func=cmd_claim_physical)

    # -- gap -------------------------------------------------------------- #
    p = sub.add_parser("gap", parents=[common],
                       help="every claim whose mechanism has yet to run — gap, pending "
                            "build, assumed — and, with --propose, packs that might run it")
    p.add_argument("--propose", action="store_true",
                   help="also list installed packs whose vocabulary matches each claim's "
                        "quantity and how")
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

    # Its help in qualification's words, from the one table (P2.3, GLOSSARY §6
    # *fail*): "fails any gate that cannot fail" used the outcome word for two
    # things in one sentence, and "negative control" is a §2 Never-say.
    p = gate_sub.add_parser("selftest", parents=[common],
                            help=report.HUMAN["qualification"]["help"])
    # The positional form exists because `gates.py` prints `nopekit gate
    # selftest <id>` when it refuses a control-less gate, and a command the tool
    # tells you to run has to work as printed.
    p.add_argument("gates", nargs="*", metavar="GATE",
                   help="gate ids, pack names or globs; default is every evaluator")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="pack mode: print every evaluator's line, not only the "
                        "unqualified ones")
    p.add_argument("--only", action="append", metavar="GATE",
                   help="same as the positional form (repeatable)")
    p.add_argument("--tier", type=int, default=None,
                   help="cap the cost; the default runs every tier's control")
    p.add_argument("--no-record", action="store_true",
                   help="run every control but file nothing: no control entry, obs or "
                        "cache under .nopekit/")
    p.add_argument("--pack", action="append", metavar="NAME|DIR",
                   help="pack mode: demonstrate this pack, or every pack in this "
                        "directory (repeatable). Without a project, pack mode runs "
                        "every bundled pack")
    p.add_argument("--user-packs", action="store_true",
                   help="pack mode: also search $NOPEKIT_PACK_PATH and "
                        "~/.nopekit/packs (off, so the machine cannot choose the pack)")
    p.add_argument("--allow-empty", action="store_true",
                   help="exit 0 when no control ran (otherwise that is a failure)")
    _junit_flag(p, "the controls (and, in pack mode, the baselines)")
    p.set_defaults(func=cmd_gate_selftest)

    # -- report / why / decide -------------------------------------------- #
    p = sub.add_parser("report", parents=[common], help="the readiness report")
    p.add_argument("--write", action="store_true",
                   help="write REPORT.md at the project root (an output git ignores)")
    p.add_argument("--milestone", default=None, metavar="NAME",
                   help="the report for one milestone (milestones/<name>.json): what it "
                        "requires, as last evaluated")
    p.set_defaults(func=cmd_report)

    # -- export (P2.5b; D-24: a top-level command the brief names) ---------- #
    said_export = report.HUMAN["export"]
    p = sub.add_parser("export", parents=[common], help=said_export["help"],
                       description=said_export["help"])
    p.add_argument("milestone", nargs="?", default=None, help=said_export["milestone_help"])
    p.add_argument("--dry-run", action="store_true", help=said_export["dry_run_help"])
    p.add_argument("--proceed", action="store_true", help=said_export["proceed_help"])
    p.add_argument("--why", default=None, metavar="TEXT", help=said_export["why_help"])
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("why", parents=[common],
                       help="one param's, claim's, requirement's or decision's full "
                            "history, instead of the whole log")
    p.add_argument("name")
    p.set_defaults(func=cmd_why)

    said_decide = report.HUMAN["decide"]
    p = sub.add_parser("decide", parents=[common],
                       help="record a decision, including what LOST and why")
    p.add_argument("--title", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--rejected", action=_RejectedFlag, dest="rejection_parts",
                   metavar="VALUE|WHY",
                   help='what lost and the concrete reason: "4.0 mm|it deflected 3.75 mm, '
                        '7.5x the limit"; the flags below apply to the --rejected before them')
    p.add_argument("--type", action=_RejectionPart, dest="rejection_parts",
                   metavar="TYPE", help=said_decide["type_help"])
    p.add_argument("--by", action=_RejectionPart, dest="rejection_parts", metavar="NAME",
                   help=said_decide["by_help"])
    p.add_argument("--returns-if", action=_RejectionPart, dest="rejection_parts",
                   metavar="CONDITION", help=said_decide["returns_if_help"])
    p.add_argument("--returns-text", action=_RejectionPart, dest="rejection_parts",
                   metavar="TEXT", help=said_decide["returns_text_help"])
    p.add_argument("--input", action=_RejectionPart, dest="rejection_parts", metavar="ID",
                   help=said_decide["input_help"])
    p.set_defaults(rejection_parts=None)
    p.add_argument("--param", action="append", default=[], help="param names this moved")
    p.add_argument("--claim", action="append", default=[], help="claim ids this moved")
    p.add_argument("--evidence", action="append", default=[])
    p.add_argument("--body", default="", help="long-form markdown")
    p.set_defaults(func=cmd_decide)

    said_prefer = report.HUMAN["prefer"]
    p = sub.add_parser("prefer", parents=[common], help=said_prefer["help"],
                       description=said_prefer["help"])
    p.add_argument("id", metavar="decision")
    p.add_argument("--value", default=None, metavar="VALUE", help=said_prefer["value_help"])
    # Refused flags (REFUSED_FLAGS), as for `claim physical`: parsed so a
    # pasted command meets its reason; never in --help.
    p.add_argument("--when", default=None, help=argparse.SUPPRESS)
    p.add_argument("--who", default=None, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_prefer)

    # -- packs ------------------------------------------------------------ #
    # `pack` is an alias because the extension protocol and `packs.py`'s own
    # messages say `nopekit pack validate`; a documented command must run.
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

    p = pack_sub.add_parser("skills", parents=[common],
                            help="print the live packs' PACK.md and skill files "
                                 "(skills/<name>/SKILL.md) — read them before working in a "
                                 "pack's domain")
    p.add_argument("--pack", action="append", default=[], metavar="NAME",
                   help="this discoverable pack instead of the live ones (repeatable)")
    p.set_defaults(func=cmd_packs_skills)

    p = pack_sub.add_parser("validate", parents=[common],
                            help="the checks CI runs; exit 1 on any problem")
    p.add_argument("name", help="pack name or directory")
    p.set_defaults(func=cmd_packs_validate)

    # No `packs remove` (PLAN A-8): opting out is deleting the name from `packs`
    # in `.nopekit/project.json`.
    p = pack_sub.add_parser("add", parents=[common], help="opt this project into a pack")
    p.add_argument("names", nargs="+")
    p.set_defaults(func=cmd_packs_add)

    # -- model / doctor --------------------------------------------------- #
    # No `--set-entry` (PLAN A-8): the entry is `"model_entry"` in
    # `.nopekit/project.json`, edited as the file it is.
    p = sub.add_parser("model", parents=[common], help="the model, projected")
    p.add_argument("--write", action="store_true", help="write .nopekit/model.json")
    p.add_argument("--entry", default=None, help="project this file instead of the recorded one")
    p.set_defaults(func=cmd_model)
    # Architecture phase 3b: an optional subcommand, so `nopekit model`,
    # `--write` and `--entry` are unchanged.
    model_sub = p.add_subparsers(dest="model_command", metavar="<sub>")
    said = report.HUMAN["trace"]
    # argparse reads a help string as a %-format: the sentence's "-10% and
    # +10%" is escaped here, at the parser, and stays a sentence in HUMAN. (What slipped
    # through phase 3b: `nopekit model --help` crashed, "TypeError: not enough
    # arguments for format string"; tests/test_docs_commands.EveryHelpPrints.)
    t = model_sub.add_parser("trace", parents=[common],
                             help=said["help"].replace("%", "%%"))
    t.add_argument("--choices", action="append", default=None, metavar="A,B",
                   help=said["choices_help"])
    t.add_argument("--workers", type=int, default=None,
                   help=said["workers_help"].format(n=derivation.default_workers()))
    t.add_argument("--timeout", type=float, default=None,
                   help=said["timeout_help"].format(floor=derivation.BUILD_TIMEOUT_FLOOR,
                                                    factor=derivation.TIMEOUT_FACTOR))
    t.add_argument("--from-round", dest="from_round", type=int, default=None,
                   help=argparse.SUPPRESS)
    t.set_defaults(func=cmd_model_trace)
    _unknown_options_first(p)

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
                   help="replace the shells too (index.html, engineering/, marketing/, "
                        "technical.html) — your edits to them are not recoverable")
    p.set_defaults(func=cmd_site_init)

    p = site_sub.add_parser("build", parents=[common],
                            help="run the viewgens and write data/ and assets/; runs no gates")
    p.add_argument("--live", action="store_true",
                   help="mark data/build.json live, so a page served by `site serve --watch` "
                        "polls it")
    p.set_defaults(func=cmd_site_build)

    p = site_sub.add_parser("serve", parents=[common],
                            help="serve site/ from the standard library, on loopback")
    p.add_argument("-p", "--port", type=int, default=SERVE_PORT,
                   help=f"port to listen on (default {SERVE_PORT}; 0 picks a free one)")
    p.add_argument("--host", default=SERVE_HOST,
                   help=f"bind address (default {SERVE_HOST} — loopback on purpose)")
    p.add_argument("--no-browser", action="store_true",
                   help="do not open a browser window")
    p.add_argument("--watch", action="store_true",
                   help="rebuild the site when the records, the model, the verdict cache, the "
                        "trace, the groups or site/content change; the page reloads its data")
    p.add_argument("--interval", type=float, default=1.0,
                   help="seconds between polls with --watch (default 1.0, at least 0.2)")
    # The acceptance run after phase 3b: a live site that outlives the shell
    # (and the agent's turn) that started it.
    life = p.add_mutually_exclusive_group()
    life.add_argument("--detach", action="store_true",
                      help="start the server as a process of its own (POSIX), print its URL "
                           "and exit at once; it outlives the shell that started it, opens no "
                           "browser, and logs to .nopekit/out/serve.log. When one runs "
                           "already, print its URL and start nothing")
    life.add_argument("--stop", action="store_true",
                      help="stop the server --detach started for this project")
    life.add_argument("--detached-server", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_site_serve)

    p = site_sub.add_parser("stamp", parents=[common],
                            help="write the as_of stamp into site/content prose: the results "
                                 "its board's claims had when you wrote it")
    which = p.add_mutually_exclusive_group()
    which.add_argument("subject", nargs="?", default=None,
                       help="a board's id, intro, next or story (site/content/<subject>.md)")
    which.add_argument("--all", action="store_true",
                       help="stamp every file in site/content/ that names a board, intro, next "
                            "or story")
    p.set_defaults(func=cmd_site_stamp)

    p = site_sub.add_parser("badge", parents=[common],
                            help="add the Made-with-nopekit block at the bottom of README.md "
                                 "(creating README.md when absent)")
    p.set_defaults(func=cmd_site_badge)

    p = site_sub.add_parser("vendor", parents=[common],
                            help="pull three.js into site/vendor/ so the site works offline")
    p.set_defaults(func=cmd_site_vendor)

    p = site_sub.add_parser("status", parents=[common],
                            help="what is built, how stale it is, what locators dangle")
    p.set_defaults(func=cmd_site_status)

    p = sub.add_parser("doctor", parents=[common],
                       help="environment, model, packs, gates and ledger integrity")
    p.add_argument("--tier", type=int, default=None,
                   help=f"the costliest pack controls the sealed-fixtures row runs against "
                        f"this project's params: {DOCTOR_SEAL_TIER} instant (default), 1 "
                        f"build, 2 solve, 3 external; above it, a control on record at its "
                        f"version is read, not run")
    p.set_defaults(func=cmd_doctor)

    _tag_subparsers(parser)
    return parser


def _unknown_options_first(parser: argparse.ArgumentParser) -> None:
    """Refuse an option ``parser`` does not know by its own name, before its
    optional subcommand reads the option's value as a subcommand. What slipped
    through (architecture phase 3b, when `model` gained `trace`): `nopekit model
    --set-entry model/bracket.py`, the flag A-8 removed, said "invalid choice:
    'model/bracket.py'" and never named the flag."""
    known = parser._option_string_actions
    choices = {name for action in parser._actions
               if isinstance(action, argparse._SubParsersAction) for name in action.choices}
    real = parser.parse_known_args

    def parse_known_args(args=None, namespace=None):
        for arg in list(args or ()):
            if arg in choices:
                break
            if arg.startswith("-") and arg.split("=", 1)[0] not in known:
                parser.error(f"unrecognized arguments: {arg}")
        return real(args, namespace)

    parser.parse_known_args = parse_known_args       # type: ignore[method-assign]


def _tag_subparsers(parser: argparse.ArgumentParser) -> None:
    """Give every subparser a `_parser` default pointing at itself.

    So that `main` can hand a bad flag back to the parser the user was actually
    using. A typo'd flag on `check` (`--tierr 1`) printed the two-line TOP-LEVEL
    usage — `usage: nopekit [-h] [--version] [-C DIR] <command> ...` — which
    lists the subcommands and not one of `check`'s own flags, so the reader
    learns nothing about the flag they got wrong and has to go and type `--help`
    separately.

    argparse fills defaults from the innermost parser last (each subparser parses
    into a fresh namespace that is then copied outward), so `args._parser` ends
    up as the deepest one that matched: `claim show`, not `claim`.

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
    """`nopekit claim` with no subcommand: print its help, exit 2.

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

    `NopekitError` is the user's problem and prints as one sentence with no
    traceback; everything else is the spine's problem and keeps its traceback,
    because a bug that prints like a user error gets reported as a user error and
    never gets fixed.

    `BrokenPipeError` is handled rather than raised: `nopekit gate list | head`
    is a normal thing to type, and Python's default behaviour there is a
    confusing "Exception ignored" block at interpreter shutdown. stdout is
    redirected to devnull so the flush at exit has somewhere harmless to go.

    After the command — succeeded or refused, never interrupted — the index is
    rebuilt from the records on a migrated project (`_touch_index`), so the one
    generated file an agent reads first says what the record files say, a hand
    edit made since the last command included. Its exit code is never the
    index's: the rebuild is best-effort.
    """
    parser = build_parser()
    try:
        # `parse_known_args`, then complain via the subparser the user reached.
        # `parse_args` reports an unrecognized flag from the TOP-LEVEL parser, so
        # `nopekit check --tierr 1` printed the list of subcommands instead of
        # the list of `check`'s flags — the one thing the reader needed.
        raw = list(sys.argv[1:] if argv is None else argv)
        args, unknown = parser.parse_known_args(raw)
        if getattr(args, "func", None) is cmd_propose and "--" in raw:
            # `propose <id> [ARG ...] -- [script args]`: what follows the first
            # `--` is the script's whole, and what precedes it is parsed as any
            # command's — argparse alone hands a positional after a flag
            # (`<id> --json -- --json`) to nobody (review of phase 2).
            cut = raw.index("--")
            args, unknown = parser.parse_known_args(raw[:cut])
            args.args = list(getattr(args, "args", None) or []) + raw[cut + 1:]
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
        code = int(handler(args) or 0)
    except NopekitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        _after(args, quiet=True)
        return 2
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 141
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    _after(args)
    return code


if __name__ == "__main__":              # pragma: no cover - `python -m nopekit.cli`
    raise SystemExit(main())
