# SPDX-License-Identifier: Apache-2.0
"""Admission at a gate's current version, and the sweep that enforces it.

A verdict counts only from a gate whose negative control has been shown to fire
at the version of the gate, its fixture and the spine that produced the verdict
(invariant 9, the reject half; D-07). Before 1.2 nothing in `check`, the
resolver or the report ever read a selftest: a logger with a declared control
produced PROVEN rows, and `gate selftest` exiting 1 changed nothing (S-05). Two
more holes rode on the fixtures themselves:

* **S-07.** An identity fixture — `return ctx` — handed the gate the LIVE design.
  The bracket's live design fails deflection on purpose, so the "control" failed
  too and was reported "correctly failed ... ~64x worse": it demonstrated nothing
  about its one change. Project fixtures now receive the project's KNOWN-GOOD
  design (`selftest/known_good.py`), on which an identity fixture passes, and a
  control that passes is not a control.
* **S-19.** Fixtures rebuild their known-bad input through the proposer's own
  model, so a model edit can defuse them — 11 of 348 control runs stopped firing
  under formula mutants. A control is keyed by its static part (spine, gate code,
  the owner's `selftest/`, the declaration) plus the VALUES it fed the gate; when
  the model moves, the sweep re-runs the fixture alone and compares those values.
  Equal: the demonstration stands, nothing else runs, no file is written. Moved:
  the control runs again and its new outcome decides.

What each class holds against:

* **AdmissionIsDemonstrated** (the invariant-9 class). An honest gate is
  admitted; an always-True gate never yields PASS; a no-op fixture is caught
  even though the verdict cache would hit; the identity fixture is caught on the
  known-good host (and would slip on the live one — the test's own negative
  control); a model move that leaves the control's values alone costs one
  fixture run and writes nothing; one that moves them re-runs the control, and
  one that defuses it is not admitted; `force` re-runs every control and a
  changed answer at identical inputs is not admitted. `run_gate` itself never
  looks at admission.
* **SweepOrder.** Availability before admission (a missing tool is a skip, never
  "not admitted": CI has no trimesh); a gate named above the tier ceiling runs its
  control; a dry sweep writes nothing but scratch; a control's scratch is its own.

The same holes, closed through the command a human types (the `test_cli_*`
methods of AdmissionIsDemonstrated, U25). The library tests above prove the
sweep; these prove that `check`, `status` and `report` on a real project are
wired to it — the S-05 shape exactly: every piece of admission existed in a
library and the CLI never asked. On the bracket and on wrapped pack baselines
(`tests/_projects.py`), one fresh process per command:

* a no-op fixture, an always-True gate, and both spellings of S-07's identity
  fixture (`return ctx`, `return _with(ctx)`) are not admitted, and their claims
  never read PASS — in `status`, in `check`'s BLOCKING list, or under PROVEN; a
  logger that never produced an entry at all is held to the sweep's own answer
  (`check`'s BLOCKING list and `last_check.json`), since no entry of it exists
  for a reader to judge;
* an edit to a `.mo`, an `.stl` or a `baseline.json` value inside a pack's
  `selftest/` misses every control entry of that pack and re-runs it (where the
  tool is missing, the availability skip is the asserted outcome — never a skip
  of the test: this class carries invariant 9);
* bytecode under `selftest/` in a git copy is never a selftest input: no new
  control entry;
* a claim edit that defuses a live-host control — its gate taking the limit
  from `ctx.ledger` — is not admitted, to `check` or to a reader, while a
  fixture that hands its gate a ledger of its own survives the same edit on
  one fixture re-run (admission review, round 1, A);
* a Config default edit re-verifies all six controls by their fixtures alone
  (nothing executed, nothing new on disk), while a `build()` edit that moves a
  value a control fed its gate writes a new control entry for exactly the gates
  whose recorded control reads moved (S-19's model-code half);
* a `module:function` fixture outside `selftest/` edited into a no-op — the
  same-size edit, over bytecode a hand run left beside it — is not admitted,
  and one whose code no loader records re-runs its fixture on every check
  (admission review, round 1, C);
* a data file a fixture module or the known-good module reads at import,
  outside `selftest/`, edited so the control no longer fires, is not admitted;
  and a gate module's limit read at import, tightened, does not keep its PASS
  (admission review, round 1, D);
* code loaded at RUN time — a known-good design that loads the live model by
  path when ``context`` runs, a fixture's helper loaded when ``make`` runs, a
  gate's limit from a helper it loads by path or names to ``import_module``
  — is keyed: an identity fixture on that known-good host is refused once the
  model passes, a defused helper is refused, a tightened limit fails, and the
  second gate served the cached helper keys it as the first did (admission
  review, round 2).

Scenarios that edit code run the sweep in a fresh process (`_DRIVER`, through
`_env.run`), per spec §0.4: an in-process module cache must never be what makes
an edit visible or invisible. Everything else runs in-process on a fresh
`Registry` — never `gates.REGISTRY`.

Run:  PYTHONPATH=src python3 -m unittest tests.test_admission -v
"""
from __future__ import annotations

import dataclasses
import glob
import importlib.util
import json
import os
import py_compile
import re
import sys
import textwrap
import unittest
from unittest import mock

from atompipe import claims, gates, modelio, store, verdicts
from atompipe import report as report_mod
from atompipe.models import Claim, ClaimStatus, GateSpec, Ledger, NegativeControl, Verdict

import _env
import _projects

NOW = "2026-09-27T10:00:00Z"


# --------------------------------------------------------------------------- #
# the synthetic project: a model, its known-good design, fixtures built from it
# --------------------------------------------------------------------------- #
#: `K` is a build() constant a test edits (same size) to move every value built
#: through the model at once — the S-19 edit. Deflection is K*load*span/t^3:
#: 1.758 at the known-good 8.0 (limit 2.0), 2.624 at the live 7.0, 112.5 at 2.0.
MODEL = '''\
from dataclasses import dataclass

K = 1.0


@dataclass
class Config:
    thickness: float = {thickness}
    load_n: float = 15.0
    span: float = 60.0
    bed_xy: float = 220.0


CONFIG = Config()


def build(config=None):
    c = config if config is not None else CONFIG
    return {{"deflection": round(K * c.load_n * c.span / c.thickness ** 3, 6),
             "usable_bed": c.bed_xy - 16.0, "plate_len": c.span + 14.0}}
'''

#: The known-good design: every Config field stated, so a model DEFAULT edit
#: does not move it (only a build() edit does).
KNOWN_GOOD = '''\
import dataclasses
import os

from atompipe.modelio import flat_params, load_path
from atompipe.models import Ledger

_HERE = os.path.dirname(os.path.abspath(__file__))
model = load_path(os.path.join(_HERE, os.pardir, "model", "m.py"))

CONFIG = {"thickness": 8.0, "load_n": 15.0, "span": 60.0, "bed_xy": 220.0}


def params(config=None):
    stated = dict(CONFIG if config is None else config)
    flat, _conflicts = flat_params({"config": stated,
                                    "derived": model.build(model.Config(**stated))})
    return flat


def context(ctx):
    return dataclasses.replace(ctx, params=params(), ledger=Ledger(), extra={})
'''

FIXTURES = '''\
import dataclasses
import os

from atompipe.modelio import load_path

kg = load_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "known_good.py"))


def _with(ctx, **over):
    config = dict(kg.CONFIG)
    config.update(over)
    return dataclasses.replace(kg.context(ctx), params=kg.params(config))


def thin(ctx):
    return _with(ctx, thickness=2.0)


def huge(ctx):
    return _with(ctx, span=400.0)
'''

#: S-07's fixture, literally. Its own file, so it needs no known-good module and
#: runs in a project that has none (the test's negative control).
IDENTITY = '''\
def make(ctx):
    return ctx
'''

#: The known-good `context` of the synthetic project, as KNOWN_GOOD spells it —
#: the block the known-good variants below replace.
KNOWN_GOOD_CONTEXT = '''\
def context(ctx):
    return dataclasses.replace(ctx, params=params(), ledger=Ledger(), extra={})
'''

#: A known-good design read at CALL time from a data file outside `selftest/`
#: (admission review, round 1). The file is an input of every control built on
#: the design, and only the control's trace window can see a read `context`
#: makes: the selftest walk does not cover `data/`, and the module's code
#: closure holds code, not what the code opened.
KNOWN_GOOD_FROM_DATA = KNOWN_GOOD.replace(KNOWN_GOOD_CONTEXT, '''\
def context(ctx):
    import json
    with open(os.path.join(ctx.root, "data", "kg.json"), encoding="utf-8") as fh:
        thickness = float(json.load(fh)["thickness"])
    return dataclasses.replace(ctx, params=params(dict(CONFIG, thickness=thickness)),
                               ledger=Ledger(), extra={})
''')
assert KNOWN_GOOD_FROM_DATA != KNOWN_GOOD, "the known-good variant replaced nothing"

#: A known-good `context` that hands back whatever it was handed: what it
#: returns is exactly the host the spine gives it.
KNOWN_GOOD_ECHO = '''\
def context(ctx):
    return ctx
'''

GATES = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict

#: Runtime switches a test flips without editing a byte: `lenient` makes t.defl
#: pass whatever it reads — a gate whose answer changed at identical inputs.
FLAGS = {"lenient": False}
#: (gate id, the deflection it was handed): which input a gate was called on.
CALLS = []

LIMIT = 2.0


def _defl(gate_id, ctx):
    d = float(ctx.params["deflection"])
    CALLS.append((gate_id, d))
    passed = True if FLAGS["lenient"] and gate_id == "t.defl" else d <= LIMIT
    return Verdict(gate=gate_id, passed=passed, measured=d, limit=LIMIT, units="mm",
                   detail=f"{d:.3f} mm (limit {LIMIT} mm)")


@gate(id="t.defl", title="t", claims=["stiffness"],
      negative_control=NegativeControl(fixture="selftest/bad.py:thin",
                                       note="quarter thickness"))
def defl(ctx):
    return _defl("t.defl", ctx)


@gate(id="t.ident", title="t", claims=["ident"],
      negative_control=NegativeControl(fixture="selftest/ident.py"))
def ident(ctx):
    return _defl("t.ident", ctx)


@gate(id="t.always", title="t", claims=["always"],
      negative_control=NegativeControl(fixture="selftest/bad.py:thin"))
def always(ctx):
    d = float(ctx.params["deflection"])
    CALLS.append(("t.always", d))
    return Verdict(gate="t.always", passed=True, measured=d, limit=LIMIT, units="mm")


@gate(id="t.bed", title="t", claims=["bed-fit"],
      negative_control=NegativeControl(fixture="selftest/bad.py:huge"))
def bed(ctx):
    usable = float(ctx.params["usable_bed"])
    length = float(ctx.params["plate_len"])
    CALLS.append(("t.bed", length))
    return Verdict(gate="t.bed", passed=length <= usable, measured=length, limit=usable,
                   units="mm")


@gate(id="t.tool", title="t", claims=["tool"],
      requires_python=["atompipe_no_such_module_u20"],
      negative_control=NegativeControl(fixture="selftest/bad.py:thin"))
def tool(ctx):
    CALLS.append(("t.tool", None))
    return Verdict(gate="t.tool", passed=True)


@gate(id="t.slow", title="t", claims=["slow"], tier=2,
      negative_control=NegativeControl(fixture="selftest/bad.py:thin"))
def slow(ctx):
    return _defl("t.slow", ctx)
'''

#: One claim per gate, tagged with the gate's vocabulary.
CLAIM_TAGS = {"C1": "stiffness", "C2": "ident", "C3": "always", "C4": "bed-fit",
              "C5": "tool", "C6": "slow"}
CLAIM_OF = {"t.defl": "C1", "t.ident": "C2", "t.always": "C3", "t.bed": "C4",
            "t.tool": "C5", "t.slow": "C6"}
#: The tier-0 gates whose tools are here: every one a full sweep asks admission of.
AVAILABLE_TIER0 = ("t.defl", "t.ident", "t.always", "t.bed")


def write(root: str, rel: str, text: str) -> str:
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def edit(root: str, rel: str, old: str, new: str) -> None:
    """Replace ``old`` (which must be there exactly once) with ``new`` in a file."""
    path = os.path.join(root, *rel.split("/"))
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert text.count(old) == 1, (rel, old)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace(old, new))


def claims_rows(tags: dict[str, str]) -> list[dict]:
    return [{"id": cid, "statement": f"claim {cid}", "tags": [tag]}
            for cid, tag in tags.items()]


def ledger_of(tags: dict[str, str]) -> Ledger:
    return Ledger(claims=[Claim.from_dict(row) for row in claims_rows(tags)])


def plant_admission_project(root: str, *, thickness: float = 8.0,
                            known_good: bool = True) -> str:
    write(root, "model/m.py", MODEL.format(thickness=repr(float(thickness))))
    write(root, "gates/g.py", GATES)
    write(root, "selftest/ident.py", IDENTITY)
    if known_good:
        write(root, "selftest/known_good.py", KNOWN_GOOD)
        write(root, "selftest/bad.py", FIXTURES)
    return root


def control_files(root: str) -> list[str]:
    """Every control entry under ``root``'s verdict cache, ``<gate>/<name>``."""
    base = os.path.join(root, ".atompipe", "verdicts")
    return sorted(os.path.relpath(p, base).replace(os.sep, "/")
                  for p in glob.glob(os.path.join(base, "*", "control-*.json")))


def tree(root: str, *, skip: tuple = ()) -> dict[str, bytes]:
    """``{relative path: bytes}`` of every file under ``root``, minus ``skip``
    prefixes: what a dry run must leave untouched."""
    out: dict[str, bytes] = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if any(rel == s or rel.startswith(s + "/") for s in skip):
                continue
            with open(path, "rb") as fh:
                out[rel] = fh.read()
    return out


# --------------------------------------------------------------------------- #
# code-editing scenarios: one sweep per fresh process
# --------------------------------------------------------------------------- #
#: A sweep (or a resolve) of a project, in a process of its own, reported as
#: JSON. `--model` builds the projection from the project's model file;
#: `--projection` reads it from a JSON file. Claims come from `--claims`, a JSON
#: list of claim dicts, so the driver depends on no record layout.
DRIVER = r'''
import argparse
import dataclasses
import json
import os

from atompipe import claims, gates, modelio, store, verdicts
from atompipe.models import Claim, Ledger

NOW = "2026-09-27T10:00:00Z"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--model", default="")
    ap.add_argument("--projection", default="")
    ap.add_argument("--claims", required=True)
    ap.add_argument("--mode", default="sweep", choices=("sweep", "resolve"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--only", action="append")
    ap.add_argument("--tier", type=int, default=0)
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    with open(a.claims, encoding="utf-8") as fh:
        ledger = Ledger(claims=[Claim.from_dict(row) for row in json.load(fh)])
    if a.model:
        projection = modelio.project(modelio.load_model(root, a.model))
    else:
        with open(a.projection, encoding="utf-8") as fh:
            projection = json.load(fh)
    registry = gates.Registry()
    gates.load_project_gates(root, registry)
    flat, _conflicts = modelio.flat_params(projection)
    ctx = gates.GateContext(root=root, ledger=ledger, model=None, params=flat,
                            out_dir=store.out_dir(root), tier=a.tier, extra={})
    out = {}
    if a.mode == "sweep":
        result = verdicts.sweep(root, registry, ctx, projection=projection, ledger=ledger,
                                max_tier=a.tier, only=a.only, force=a.force,
                                record=not a.no_record, now=NOW)
        out["rows"] = {r.verdict.gate: {"outcome": r.verdict.outcome, "error": r.verdict.error,
                                        "executed": r.executed, "cached": r.cached,
                                        "fresh": r.fresh,
                                        "admission": (r.admission.state if r.admission
                                                      else None),
                                        "reverified": bool(r.admission
                                                           and r.admission.reverified),
                                        "control_executed": bool(r.admission
                                                                 and r.admission.executed)}
                       for r in result.rows}
        out["counts"] = result.counts
        out["controls"] = result.controls
        out["before"] = {gid: state.state for gid, state in result.before.items()}
    resolution = verdicts.resolve(root, registry, projection, ledger, now=NOW)
    view = dataclasses.replace(ledger, verdicts=resolution.verdicts)
    out["statuses"] = {cid: status.value for cid, status in
                       claims.statuses(view, registry=registry,
                                       stale_gates=resolution.stale_gates).items()}
    out["stale_gates"] = sorted(resolution.stale_gates)
    out["resolved"] = {v.gate: v.outcome for v in resolution.verdicts}
    out["admission"] = {gid: (row.admission.state if row.admission else None)
                        for gid, row in resolution.rows.items()}
    print(json.dumps(out))


main()
'''


class Driven:
    """A project on disk, swept by ``DRIVER`` in a fresh process per call."""

    def __init__(self, case: _env.EnvCase, root: str, tags: dict[str, str], *,
                 model: str = "", projection: dict | None = None) -> None:
        self.case = case
        self.root = root
        self.home = case.tmp()
        self.driver = write(self.home, "driver.py", DRIVER)
        self.claims = write(self.home, "claims.json", json.dumps(claims_rows(tags)))
        self.model = model
        self.projection_path = ""
        if projection is not None:
            self.set_projection(projection)

    def set_projection(self, projection: dict) -> None:
        self.projection_path = write(self.home, "projection.json", json.dumps(projection))

    def run(self, *args: str, mode: str = "sweep") -> dict:
        argv = [sys.executable, self.driver, self.root, "--claims", self.claims,
                "--mode", mode]
        argv += ["--model", self.model] if self.model else ["--projection",
                                                            self.projection_path]
        proc = _env.run([*argv, *args], cwd=self.home)
        self.case.assertEqual(proc.returncode, 0,
                              f"driver {args} failed:\n{proc.stdout}\n{proc.stderr}")
        return json.loads(proc.stdout)


# --------------------------------------------------------------------------- #
# in-process scenarios
# --------------------------------------------------------------------------- #
class Project:
    """The synthetic project, loaded into a fresh registry in this process."""

    def __init__(self, case: _env.EnvCase, *, thickness: float = 8.0,
                 known_good: bool = True) -> None:
        self.root = plant_admission_project(os.path.join(case.tmp(), "project"),
                                            thickness=thickness, known_good=known_good)
        self.registry = gates.Registry()
        gates.load_project_gates(self.root, self.registry)
        self.ledger = ledger_of(CLAIM_TAGS)
        self.projection = modelio.project(modelio.load_model(self.root, "model/m.py"))

    @property
    def gate_module(self):
        _spec, fn = self.registry.get("t.defl")
        return sys.modules[fn.__module__]

    def ctx(self) -> gates.GateContext:
        flat, _conflicts = modelio.flat_params(self.projection)
        return gates.GateContext(root=self.root, ledger=self.ledger, model=None, params=flat,
                                 out_dir=store.out_dir(self.root), tier=0, extra={})

    def sweep(self, **kw) -> verdicts.SweepResult:
        kw.setdefault("max_tier", 0)
        kw.setdefault("now", NOW)
        return verdicts.sweep(self.root, self.registry, self.ctx(),
                              projection=self.projection, ledger=self.ledger, **kw)

    def resolve(self) -> verdicts.Resolution:
        return verdicts.resolve(self.root, self.registry, self.projection, self.ledger,
                                now=NOW)

    def statuses(self, resolution: verdicts.Resolution | None = None) -> dict:
        resolution = resolution or self.resolve()
        view = dataclasses.replace(self.ledger, verdicts=resolution.verdicts)
        return claims.statuses(view, registry=self.registry,
                               stale_gates=resolution.stale_gates)


def row(result: verdicts.SweepResult, gate_id: str) -> verdicts.SweepRow:
    found = [r for r in result.rows if r.verdict.gate == gate_id]
    assert len(found) == 1, (gate_id, [r.verdict.gate for r in result.rows])
    return found[0]


# --------------------------------------------------------------------------- #
# the CLI: real projects, one fresh process per command
# --------------------------------------------------------------------------- #
#: The bracket's six gates. Spelled here rather than read off the bracket: an
#: expectation taken from the project under test agrees with it by construction.
BRACKET_GATES = ("bracket.deflection", "bracket.bending_stress", "bracket.bearing",
                 "bracket.model_validity", "bracket.bed_fit", "bracket.min_wall")

#: The bracket's fixture for `bracket.deflection`, as `selftest/bad_configs.py`
#: spells it — the line the identity-fixture test replaces.
QUARTER_THICKNESS = 'return _with(ctx, thickness=known_good.CONFIG["thickness"] / 4.0)'

#: The admission review's one-gate project (round 1, repro B), file for file: a
#: shelf whose span `shelf.span` holds to 100 mm, read from the params `check`
#: hands it. `{span}` is the model default the test moves.
SHELF_MODEL = '''\
from dataclasses import dataclass


@dataclass
class Config:
    span: float = {span}
    """mm."""


def build(config=None):
    c = config or Config()
    return {{"reach": c.span}}
'''

SHELF_GATE = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict


@gate(id="shelf.span", claims=["span"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:long"))
def span(ctx):
    s = float(ctx.params["span"])
    return Verdict(gate="shelf.span", passed=s <= 100.0, measured=s, limit=100.0,
                   units="mm", detail=f"{s} mm (limit 100.0)")
'''

SHELF_CLAIM = {"statement": "Span within 100 mm", "kind": "measurable", "critical": True,
               "acceptance": {"quantity": "span", "comparator": "<=", "limit": 100.0},
               "tags": ["span"]}

#: The review's known-good: it drops the ledger and `extra`, as the bracket's
#: does, and keeps `ctx.params` — the live design, while the spine handed it one.
SHELF_PASSTHROUGH_KNOWN_GOOD = '''\
import dataclasses

from atompipe.models import Ledger


def context(ctx):
    return dataclasses.replace(ctx, ledger=Ledger(), extra={})
'''

#: S-07's fixture, literally, under the name the gate declares.
SHELF_IDENTITY = '''\
def long(ctx):
    return ctx
'''

#: The honest pair, the V: test's positive control: a known-good design that
#: states its own span, and a fixture that changes that one value.
SHELF_KNOWN_GOOD = '''\
import dataclasses

from atompipe.models import Ledger


def context(ctx):
    return dataclasses.replace(ctx, params={"span": 80.0}, ledger=Ledger(), extra={})
'''

SHELF_LONG = '''\
import dataclasses


def long(ctx):
    params = dict(ctx.params)
    params["span"] = 400.0
    return dataclasses.replace(ctx, params=params)
'''

#: The admission review's repro A (round 1): the shelf gate taking its limit
#: from the claim record, through ``ctx.ledger``, as a gate that states no
#: number of its own does. With no ``selftest/known_good.py`` its fixture gets
#: the LIVE host, so the control reads the live C1.
SHELF_GATE_FROM_CLAIM = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict


@gate(id="shelf.span", claims=["span"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:long"))
def span(ctx):
    s = float(ctx.params["span"])
    limit = float(ctx.ledger.claim("C1").acceptance.limit)
    return Verdict(gate="shelf.span", passed=s <= limit, measured=s, limit=limit,
                   units="mm", detail=f"{s} mm (limit {limit})")
'''

#: A gate whose costlier path judges nothing: below tier 2 it holds the span to
#: 100 mm, and at tier 2 and up it passes whatever it is handed. Its control
#: fires on the cheap path and on no other, so a demonstration made at tier 0
#: says nothing about the path a tier-2 sweep runs.
SHELF_GATE_COSTLY_LOGGER = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict


@gate(id="shelf.span", claims=["span"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:long"))
def span(ctx):
    s = float(ctx.params["span"])
    if ctx.tier >= Tier.SOLVE:
        return Verdict(gate="shelf.span", passed=True, measured=s, limit=100.0, units="mm")
    return Verdict(gate="shelf.span", passed=s <= 100.0, measured=s, limit=100.0,
                   units="mm")
'''

#: The same known-bad span on a live host, with a ledger the fixture states
#: itself — openmodelica's shape (``ledger=Ledger()``): the gate reads the
#: fixture's C1, never the host's, so no live claim edit can move this control.
SHELF_LONG_OWN_LEDGER = '''\
import dataclasses

from atompipe.models import Acceptance, Claim, Ledger


def long(ctx):
    params = dict(ctx.params)
    params["span"] = 400.0
    claim = Claim(id="C1", statement="Span within 100 mm",
                  acceptance=Acceptance(quantity="span", limit=100.0))
    return dataclasses.replace(ctx, params=params, ledger=Ledger(claims=[claim]))
'''

#: A known-good design that loads the project's own C1: it is handed an empty
#: ledger (`_KNOWN_GOOD_BLANK`), so the ledger its gate reads comes from a file
#: `context` opens inside the control's window — keyed there, which is why a
#: known-good control's ledger reads are never compared with the live one.
#: (Through `store.load` it would also read `.atompipe/project.json`, spine
#: state, and the control would be opaque: re-run on every check, and never
#: admitted to a reader — a cost, and not this test's question.)
SHELF_KNOWN_GOOD_LOADS_CLAIMS = '''\
import dataclasses
import json
import os

from atompipe.models import Claim, Ledger


def context(ctx):
    with open(os.path.join(ctx.root, "claims", "C1.json"), encoding="utf-8") as fh:
        c1 = Claim.from_dict(dict(json.load(fh), id="C1"))
    return dataclasses.replace(ctx, params={"span": 80.0}, ledger=Ledger(claims=[c1]),
                               extra={})
'''

#: The shelf gate's declaration, as `SHELF_GATE` spells it — the one line the
#: module-form fixture tests replace with an importable `module:function`.
SHELF_FIXTURE_DECL = 'fixture="selftest/bad.py:long"'

#: The known-bad span line of `SHELF_LONG`, and the same-size edit that
#: defuses it: `040.0` is 40 mm, which the gate accepts. Same size on purpose —
#: with the source's mtime put back, a `.pyc` beside it still validates, and
#: the stock import runs the 400 mm bytecode (S-26).
SHELF_BAD_SPAN = 'params["span"] = 400.0'
SHELF_DEFUSED_SPAN = 'params["span"] = 040.0'

#: The admission review's repro D (round 1): the shelf's known-bad span, read
#: from a data file outside `selftest/` by the fixture module at IMPORT — before
#: any trace window opens, and only on the module's first load in a process.
#: `{name}` is the data file under `inputs/data/`, `{value}` the module global.
_READS_AT_IMPORT = '''\
import dataclasses
import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_ROOT, "inputs", "data", "{name}"), encoding="utf-8") as _fh:
    {value} = float(json.load(_fh)["span"])
'''

SHELF_LONG_FROM_DATA = _READS_AT_IMPORT.format(name="bad_span.json", value="BAD_SPAN") + '''

def long(ctx):
    params = dict(ctx.params)
    params["span"] = BAD_SPAN
    return dataclasses.replace(ctx, params=params)
'''

#: The same hole in the known-good module: its design's span read at import.
#: Its fixture (`SHELF_FIVEFOLD`) is bad only relative to that design — 80 mm
#: makes 400, 15 makes 75, which the gate accepts.
SHELF_KNOWN_GOOD_FROM_DATA = _READS_AT_IMPORT.format(name="good_span.json",
                                                     value="GOOD_SPAN") + '''
from atompipe.models import Ledger


def context(ctx):
    return dataclasses.replace(ctx, params={"span": GOOD_SPAN}, ledger=Ledger(), extra={})
'''

SHELF_FIVEFOLD = '''\
import dataclasses


def long(ctx):
    params = dict(ctx.params)
    params["span"] = 5.0 * float(params["span"])
    return dataclasses.replace(ctx, params=params)
'''

#: The shelf gate with its limit read at IMPORT from `inputs/data/limit.json`:
#: the verdict side of repro D. A gate module is loaded before any window too.
SHELF_GATE_LIMIT_FROM_DATA = _READS_AT_IMPORT.format(name="limit.json", value="LIMIT") + '''
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict


@gate(id="shelf.span", claims=["span"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:long"))
def span(ctx):
    s = float(ctx.params["span"])
    return Verdict(gate="shelf.span", passed=s <= LIMIT, measured=s, limit=LIMIT,
                   units="mm", detail=f"{s} mm (limit {LIMIT})")
'''

#: The admission review's round-2 repro (a): a known-good design that loads the
#: LIVE model by path when ``context`` RUNS — no module was being loaded then,
#: so the model joined no closure; the loader's read of its source was the
#: import system's, which the trace drops; and ``model/`` is in no static walk.
#: The known-good design was the live one, keyed nowhere (S-07 again).
SHELF_KNOWN_GOOD_LOADS_THE_MODEL = '''\
import dataclasses
import os

from atompipe.modelio import load_path
from atompipe.models import Ledger


def context(ctx):
    shelf = load_path(os.path.join(ctx.root, "model", "shelf.py"))
    return dataclasses.replace(ctx, params={"span": shelf.Config().span},
                               ledger=Ledger(), extra={})
'''

#: Repro (b): the known-bad span from a helper outside ``selftest/`` that the
#: fixture loads when it RUNS, by path, or by a literal name through
#: ``importlib.import_module`` (``lib/`` put on ``sys.path`` at import, as a
#: gate module puts its helpers there). ``{how}`` is the load.
_SHELF_LONG_FROM_HELPER = '''\
import dataclasses
import importlib
import os
import sys

from atompipe.modelio import load_path

_LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)


def long(ctx):
    gen = {how}
    params = dict(ctx.params)
    params["span"] = gen.SPAN
    return dataclasses.replace(ctx, params=params)
'''
SHELF_LONG_BY_PATH = _SHELF_LONG_FROM_HELPER.format(
    how='load_path(os.path.join(_LIB, "badgen.py"))')
SHELF_LONG_BY_NAME = _SHELF_LONG_FROM_HELPER.format(how='importlib.import_module("badgen")')

#: Repro (c): gates that take their limit from a helper loaded when the GATE
#: runs. Two load ``gates/_tables.py`` by path — so the second is served the
#: module the first ran, and a cache hit then joined nothing — and that helper
#: reads its limit from ``inputs/data/limit.json`` at its own import; one names
#: ``lib/shelf_limits.py`` to ``importlib.import_module`` with a literal. Each
#: holds its own claim.
SHELF_TABLES = _READS_AT_IMPORT.format(name="limit.json", value="LIMIT")

SHELF_GATES_LOAD_AT_RUN_TIME = '''\
import importlib
import os
import sys

from atompipe.gates import gate
from atompipe.modelio import load_path
from atompipe.models import NegativeControl, Tier, Verdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(os.path.dirname(_HERE), "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)


def _held(gate_id, ctx, limit):
    s = float(ctx.params["span"])
    return Verdict(gate=gate_id, passed=s <= limit, measured=s, limit=limit, units="mm",
                   detail=f"{s} mm (limit {limit})")


@gate(id="shelf.by_path", claims=["by-path"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:long"))
def by_path(ctx):
    return _held("shelf.by_path", ctx, load_path(os.path.join(_HERE, "_tables.py")).LIMIT)


@gate(id="shelf.by_path_again", claims=["by-path-again"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:long"))
def by_path_again(ctx):
    return _held("shelf.by_path_again", ctx,
                 load_path(os.path.join(_HERE, "_tables.py")).LIMIT)


@gate(id="shelf.by_name", claims=["by-name"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:long"))
def by_name(ctx):
    return _held("shelf.by_name", ctx, importlib.import_module("shelf_limits").LIMIT)
'''

#: The claims of ``SHELF_GATES_LOAD_AT_RUN_TIME``, one per gate, by tag.
RUN_TIME_CLAIMS = {"C1": "by-path", "C2": "by-path-again", "C3": "by-name"}

#: A cache entry's file name inside its gate's directory (spec §3.7).
ENTRY_NAME = re.compile(r"^[0-9a-f]{16}-[0-9a-f]{8}\.json$")

#: One edit per asset kind the plan names (phase-1 "Admission at sweep": "editing
#: a `.mo`, an `.stl` or a `baseline.json` value"), each made to the pack's COPY
#: under `.atompipe/packs/<pack>/` and each chosen to change no answer: a load
#: nudged 1/3 % on a baseline whose controls push one quantity many times past
#: its limit; the 80-byte header of a binary STL, which carries no geometry; a
#: trailing Modelica comment. `(pack, path in the pack, how, old bytes, new
#: bytes)`, `how` one of "once" (exactly one occurrence replaced), "prefix" (the
#: file must begin with `old`) and "append". The point is the bytes, not the
#: meaning: a control is keyed by every file of its owner's `selftest/`, so any
#: byte moved there must miss the entry, and an edit that moved an answer would
#: prove something else.
PACK_ASSET_EDITS = (
    ("beam-analytic", "selftest/baseline.json", "once",
     b'"load_n": 300.0', b'"load_n": 301.0'),
    ("fdm-print", "selftest/baseline_part.stl", "prefix",
     b"\0" * 26, b"atompipe: an edited header"),
    ("openmodelica", "selftest/assets/model/ThermalTank.mo", "append",
     b"", b"// an edit that changes no equation\n"),
)


def edit_bytes(path: str, how: str, old: bytes, new: bytes) -> None:
    """One ``PACK_ASSET_EDITS`` edit, refusing unless its anchor is where it
    says: an edit that silently matched nothing would re-run nothing and look
    like a cache hit."""
    with open(path, "rb") as fh:
        data = fh.read()
    if how == "append":
        data += new
    elif how == "prefix":
        assert data.startswith(old), (path, data[:len(old)])
        data = new + data[len(old):]
    else:
        assert data.count(old) == 1, (path, old)
        data = data.replace(old, new)
    with open(path, "wb") as fh:
        fh.write(data)


def cli(project: str, *argv: str):
    """``atompipe <argv>`` in ``project``, a fresh process through ``_env``."""
    return _env.atompipe(list(argv), cwd=project)


def check_json(case: unittest.TestCase, project: str, *argv: str) -> tuple[int, dict]:
    """``check --json [argv]``: ``(exit code, document)``. Exit 0 or 1 only — 2
    is a crash, and a crash is never an answer to read statuses off."""
    proc = cli(project, "check", "--json", *argv)
    case.assertIn(proc.returncode, (0, 1), f"check {argv} crashed:\n{proc.stdout}\n{proc.stderr}")
    try:
        return proc.returncode, json.loads(proc.stdout)
    except ValueError as exc:                      # pragma: no cover - reported
        raise AssertionError(f"check --json: {exc}\n{proc.stdout}\n{proc.stderr}")


def status_json(case: unittest.TestCase, project: str) -> dict:
    proc = cli(project, "status", "--json")
    case.assertEqual(proc.returncode, 0, f"status crashed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout)


def verdict_row(data: dict, gate_id: str) -> dict:
    """The one ``check --json`` row of ``gate_id``: every selected gate has one."""
    found = [r for r in data["verdicts"] if r["gate"] == gate_id]
    assert len(found) == 1, (gate_id, [r["gate"] for r in data["verdicts"]])
    return found[0]


def blocking_ids(data: dict) -> dict[str, str]:
    """``{claim id: status}`` of ``check --json``'s BLOCKING list."""
    return {b["claim"]: b["status"] for b in data["blocking"]}


def control_names(project: str) -> dict[str, set[str]]:
    """``{gate id: {control entry file names}}`` of a project's verdict cache."""
    out: dict[str, set[str]] = {}
    for path in glob.glob(os.path.join(project, ".atompipe", "verdicts", "*", "control-*.json")):
        out.setdefault(os.path.basename(os.path.dirname(path)), set()).add(
            os.path.basename(path))
    return out


def entry_names(project: str, gate_id: str) -> set[str]:
    """The verdict entry (not control) file names of ``gate_id``."""
    base = os.path.join(project, ".atompipe", "verdicts", gate_id)
    return {n for n in (os.listdir(base) if os.path.isdir(base) else ())
            if ENTRY_NAME.match(n)}


def read_control(project: str, gate_id: str, name: str) -> dict:
    with open(os.path.join(project, ".atompipe", "verdicts", gate_id, name),
              encoding="utf-8") as fh:
        return json.load(fh)


def proven_section(case: unittest.TestCase, project: str) -> str:
    """The body of ``report``'s PROVEN section. Fails — never returns "" — when
    the report has no such heading, or more than one: an assertNotIn over a
    missing section proves nothing (S-15)."""
    proc = cli(project, "report")
    case.assertEqual(proc.returncode, 0, f"report crashed:\n{proc.stdout}\n{proc.stderr}")
    lines = proc.stdout.splitlines()
    heading = report_mod.SECTION_PROVEN
    at = [i for i, line in enumerate(lines) if line == heading or line.startswith(heading + " ")]
    case.assertEqual(len(at), 1, f"{len(at)} {heading!r} headings in:\n{proc.stdout}")
    body = []
    for line in lines[at[0] + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    case.assertTrue("\n".join(body).strip(), "an empty PROVEN section")
    return "\n".join(body)


def untracked(project: str) -> list[str]:
    """``??`` paths of ``git status --porcelain --untracked-files=all``."""
    proc = _env.git(["status", "--porcelain", "--untracked-files=all"], cwd=project)
    if proc.returncode != 0:
        raise AssertionError(f"git status in {project}: {proc.stderr.strip()}")
    return sorted(line[3:] for line in proc.stdout.splitlines() if line.startswith("?? "))


def commit_all(project: str, message: str) -> None:
    for argv, identity in ((["add", "-A"], False), (["commit", "-q", "-m", message], True)):
        proc = _env.git(argv, cwd=project, identity=identity)
        if proc.returncode != 0:
            raise AssertionError(f"git {argv[0]} in {project}: {proc.stderr.strip()}")


# --------------------------------------------------------------------------- #
class AdmissionIsDemonstrated(_env.EnvCase):
    """A PASS counts only from a gate whose control fired at its current version."""

    def test_an_honest_gate_is_admitted_and_its_pass_counts(self):
        p = Project(self)
        result = p.sweep(only=["t.defl"])
        got = row(result, "t.defl")
        self.assertEqual(got.verdict.outcome, "pass", got.verdict)
        self.assertTrue(got.executed)
        self.assertEqual(got.admission.state, "admitted", got.admission)
        self.assertTrue(got.admission.executed, "the first sweep runs the control")
        self.assertEqual(result.controls["executed"], 1)
        self.assertEqual(result.controls["not_admitted"], 0)
        files = control_files(p.root)
        self.assertEqual(len(files), 1, files)
        with open(os.path.join(p.root, ".atompipe", "verdicts", files[0]),
                  encoding="utf-8") as fh:
            entry = json.load(fh)
        self.assertEqual((entry["bad"], entry["admitted"], entry["host"]),
                         ("fail", "reject-only", "known-good"))
        self.assertEqual(p.statuses()["C1"], ClaimStatus.PASS,
                         "the positive control: an admitted, fresh PASS reads PASS")

    def test_an_always_true_gate_never_yields_pass(self):
        # A logger with a declared control produced PROVEN rows (S-05). Its
        # control runs, the gate PASSES its own known-bad input, and the gate is
        # not admitted — its function is never called on the real design.
        p = Project(self)
        first = p.sweep(only=["t.always"])
        got = row(first, "t.always")
        self.assertEqual(got.verdict.outcome, "error")
        self.assertTrue(got.verdict.error.startswith("not admitted: "), got.verdict.error)
        self.assertIn("PASSED its own known-bad", got.verdict.error)
        self.assertFalse(got.executed)
        live = float(p.projection["derived"]["deflection"])
        self.assertNotIn(("t.always", live), p.gate_module.CALLS,
                         "a gate that is not admitted is never run on the real input")
        self.assertNotEqual(p.statuses()["C3"], ClaimStatus.PASS)
        self.assertEqual(first.controls["not_admitted"], 1)

        again = p.sweep(only=["t.always"])
        self.assertEqual(again.controls["executed"], 0, "the logger's entry is reused")
        self.assertEqual(row(again, "t.always").verdict.outcome, "error")
        self.assertNotEqual(p.statuses()["C3"], ClaimStatus.PASS)

    def test_the_second_sweep_reuses_the_control_entries(self):
        p = Project(self)
        first = p.sweep()
        self.assertEqual(first.controls["executed"], len(AVAILABLE_TIER0))
        files = control_files(p.root)
        second = p.sweep()
        self.assertEqual(second.controls["executed"], 0, "zero control runs")
        self.assertEqual(second.controls["reverified"], 0)
        self.assertEqual(second.controls["cached"], len(AVAILABLE_TIER0))
        self.assertEqual(second.counts["executed"], 0)
        self.assertEqual(control_files(p.root), files, "no new control entry")
        self.assertTrue(row(second, "t.defl").cached)
        self.assertEqual(row(second, "t.defl").verdict.outcome, "pass")

    def test_a_literal_identity_fixture_with_a_known_good_host_is_not_admitted(self):
        # S-07, D-27. The live design fails (7.0 mm: 2.62 against 2.0), so on a
        # LIVE host `return ctx` "fires" and certifies nothing. On the known-good
        # host it passes, and a control that passes is not a control.
        p = Project(self, thickness=7.0)
        got = row(p.sweep(only=["t.ident"]), "t.ident")
        self.assertEqual(got.admission.state, "not-admitted", got.admission)
        self.assertIn("PASSED its own known-bad fixture selftest/ident.py",
                      got.admission.reason)
        self.assertTrue(got.verdict.error.startswith("not admitted: "), got.verdict)
        with open(os.path.join(p.root, ".atompipe", "verdicts",
                               control_files(p.root)[0]), encoding="utf-8") as fh:
            entry = json.load(fh)
        self.assertEqual((entry["host"], entry["bad"]), ("known-good", "pass"))
        self.assertNotEqual(p.statuses()["C2"], ClaimStatus.PASS)

        # The test's own negative control: the same fixture on the live host is
        # the lie S-07 reported — admitted, because the live design already fails.
        live = Project(self, thickness=7.0, known_good=False)
        lie = row(live.sweep(only=["t.ident"]), "t.ident")
        self.assertEqual(lie.admission.state, "admitted",
                         "without the known-good host the identity fixture slips through")
        with open(os.path.join(live.root, ".atompipe", "verdicts",
                               control_files(live.root)[0]), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["host"], "live")

    def test_the_known_good_context_is_handed_nothing_of_the_live_design(self):
        """V: ``known_good.context`` was called on a copy of the LIVE host, and
        the entry it produced says ``host: "known-good"``, whose host reads are
        never keyed. A ``context`` that kept any of what it was handed —
        ``params``, the ledger, ``extra``, the model — passed the live design
        through with nothing keying it (admission review, round 1). It is now
        handed where the run lives and nothing else."""
        p = Project(self, thickness=7.0)
        write(p.root, "selftest/known_good.py", KNOWN_GOOD_ECHO)
        host = dataclasses.replace(p.ctx(), model=sys.modules[__name__],
                                   extra={"live": True}, tier=2)
        self.assertTrue(host.params and host.ledger.claims,
                        "the precondition: the host carries a live design")
        got = verdicts.known_good_context(p.root, host)
        self.assertEqual(dict(got.params), {}, "the live params reached context()")
        self.assertEqual((got.ledger.claims, got.ledger.verdicts), ([], []),
                         "the live ledger reached context()")
        self.assertEqual(got.extra, {}, "the host's extra reached context()")
        self.assertIsNone(got.model, "the live model reached context()")
        self.assertEqual((got.root, got.out_dir, got.tier),
                         (host.root, host.out_dir, host.tier),
                         "where the run lives must survive: relative paths and scratch")

    def test_a_file_the_known_good_design_reads_is_an_input_of_its_controls(self):
        """V: ``context`` ran before the control's trace window opened, so a
        known-good design read from a data file outside ``selftest/`` keyed
        nothing. At thickness 2.0 in ``data/kg.json`` the identity fixture
        "fires"; moved to 8.0 the design passes — and the entry, its static,
        file reads and fixture closure all unmoved, served "admitted" with
        nothing run (admission review, round 1)."""
        p = Project(self)
        write(p.root, "selftest/known_good.py", KNOWN_GOOD_FROM_DATA)
        write(p.root, "data/kg.json", '{"thickness": 2.0}\n')
        first = row(p.sweep(only=["t.ident"]), "t.ident")
        self.assertEqual(first.admission.state, "admitted",
                         f"the precondition: a failing known-good design makes the "
                         f"identity fixture fire ({first.admission})")
        [name] = control_files(p.root)
        with open(os.path.join(p.root, ".atompipe", "verdicts", name), encoding="utf-8") as fh:
            entry = json.load(fh)
        self.assertEqual(entry["host"], "known-good")
        self.assertIn("data/kg.json", entry["reads"]["files"],
                      "the file the known-good design was read from is not a control input")

        write(p.root, "data/kg.json", '{"thickness": 8.0}\n')
        got = row(p.sweep(only=["t.ident"]), "t.ident")
        self.assertEqual(got.admission.state, "not-admitted", got.admission)
        self.assertTrue(got.admission.executed, "the moved input must re-run the control")
        self.assertIn("PASSED its own known-bad fixture selftest/ident.py",
                      got.admission.reason)
        self.assertNotEqual(p.statuses()["C2"], ClaimStatus.PASS)

    def test_force_reruns_every_control_and_a_changed_outcome_is_not_admitted(self):
        p = Project(self)
        p.sweep()
        agreed = p.sweep(force=True)
        self.assertEqual(agreed.controls["executed"], len(AVAILABLE_TIER0),
                         "force re-runs every selected control")
        self.assertEqual(row(agreed, "t.defl").admission.state, "admitted",
                         "the same answer at the same inputs stays admitted")
        self.assertTrue(row(agreed, "t.defl").executed, "force re-runs the gate too")

        # Now the gate answers differently at identical inputs (R-9's target: a
        # cached demonstration the gate no longer lives up to).
        p.gate_module.FLAGS["lenient"] = True
        self.addCleanup(p.gate_module.FLAGS.__setitem__, "lenient", False)
        forced = p.sweep(force=True)
        got = row(forced, "t.defl")
        self.assertEqual(got.admission.state, "not-admitted", got.admission)
        self.assertIn("control outcome differs from its cached entry", got.admission.reason)
        self.assertNotEqual(p.statuses()["C1"], ClaimStatus.PASS)
        after = p.sweep()
        self.assertEqual(row(after, "t.defl").admission.state, "not-admitted",
                         "two outcomes for one control input stay not admitted")
        self.assertEqual(after.controls["executed"], 0)
        self.assertNotEqual(p.statuses()["C1"], ClaimStatus.PASS)

    def test_run_gate_still_returns_its_raw_verdict(self):
        # C: the invariant tests at test_invariants.py:74-220 drive run_gate with
        # a fixture reference that resolves to nothing ("x:y"). run_gate never
        # looks at admission, the cache or a control.
        root = self.tmp()
        spec = GateSpec(id="raw.gate", negative_control=NegativeControl(fixture="x:y"))
        verdict = gates.run_gate(spec, lambda ctx: Verdict(gate="raw.gate", passed=True,
                                                           measured=1.0, limit=2.0),
                                 gates.GateContext(root=root,
                                                   out_dir=store.out_dir(root)))
        self.assertEqual(verdict.outcome, "pass")
        self.assertEqual(verdict.rho, "", "run_gate keys nothing: the sweep does")
        self.assertEqual(os.listdir(root), [], "run_gate writes no file")

    # -- code edits: a fresh process per sweep ------------------------------ #
    def _driven(self, *, thickness: float = 8.0) -> Driven:
        root = plant_admission_project(os.path.join(self.tmp(), "project"),
                                       thickness=thickness)
        return Driven(self, root, CLAIM_TAGS, model="model/m.py")

    def test_a_fixture_edited_into_a_no_op_is_not_admitted_though_the_cache_would_hit(self):
        d = self._driven()
        first = d.run("--only", "t.defl")
        self.assertEqual(first["rows"]["t.defl"]["outcome"], "pass")
        self.assertEqual(first["statuses"]["C1"], "pass")
        edit(d.root, "selftest/bad.py", "return _with(ctx, thickness=2.0)",
             "return _with(ctx)")
        second = d.run("--only", "t.defl")
        self.assertEqual(second["before"]["t.defl"], "fresh",
                         "the verdict cache would have served the PASS")
        got = second["rows"]["t.defl"]
        self.assertEqual(got["outcome"], "error")
        self.assertTrue(got["error"].startswith("not admitted: "), got)
        self.assertIn("PASSED its own known-bad", got["error"])
        self.assertTrue(got["control_executed"])
        self.assertFalse(got["executed"])
        self.assertNotEqual(second["statuses"]["C1"], "pass")

    def test_a_closure_move_with_equal_values_is_reverified_by_the_fixture_alone(self):
        # The E4 consequence (§3.8): a Config DEFAULT edit moves the model file,
        # which every fixture's code closure holds (they build through it) — but
        # the known-good design states every field, so no control value moves.
        d = self._driven()
        first = d.run()
        self.assertEqual(first["controls"]["executed"], len(AVAILABLE_TIER0))
        files = control_files(d.root)
        edit(d.root, "model/m.py", "bed_xy: float = 220.0", "bed_xy: float = 250.0")

        second = d.run()
        self.assertEqual(second["controls"]["executed"], 0, second["controls"])
        self.assertEqual(second["controls"]["reverified"], len(AVAILABLE_TIER0))
        self.assertEqual(control_files(d.root), files, "no new control file")
        self.assertEqual(second["before"]["t.bed"], "stale", "the live design did move")
        self.assertTrue(second["rows"]["t.bed"]["executed"])
        self.assertFalse(second["rows"]["t.defl"]["executed"])
        self.assertTrue(second["rows"]["t.defl"]["reverified"])
        self.assertEqual(second["admission"]["t.defl"], "admitted",
                         "a re-verified control reads admitted, not pending")
        self.assertEqual(second["statuses"]["C1"], "pass")
        self.assertNotEqual(second["statuses"]["C2"], "pass",
                            "re-verification keeps a logger's verdict: not admitted")

        third = d.run()
        self.assertEqual((third["controls"]["executed"], third["controls"]["reverified"]),
                         (0, 0), "a verified closure is remembered: nothing runs")
        self.assertEqual(third["controls"]["cached"], len(AVAILABLE_TIER0))
        self.assertEqual(control_files(d.root), files)

    def test_a_control_value_change_reruns_the_control_and_writes_a_new_entry(self):
        # S-19's model-code half: a build() edit that moves what a fixture hands
        # its gate misses the entry. Deflection moves with K; the bed values do not.
        d = self._driven()
        d.run()
        files = set(control_files(d.root))
        edit(d.root, "model/m.py", "K = 1.0", "K = 1.1")
        second = d.run()
        rerun = {gid for gid, r in second["rows"].items() if r["control_executed"]}
        self.assertEqual(rerun, {"t.defl", "t.ident", "t.always"}, second["rows"])
        self.assertTrue(second["rows"]["t.bed"]["reverified"])
        self.assertEqual(second["controls"]["executed"], 3)
        new = set(control_files(d.root)) - files
        self.assertEqual({path.split("/")[0] for path in new},
                         {"t.defl", "t.ident", "t.always"}, new)
        self.assertEqual(second["rows"]["t.defl"]["admission"], "admitted")

    def test_a_model_edit_that_defuses_a_control_is_not_admitted(self):
        # S-19 itself: a formula mutant under which the control no longer fires.
        # A cached demonstration would still say "fired"; the values it fed the
        # gate moved, so the control runs again — and passes its known-bad input.
        d = self._driven()
        first = d.run("--only", "t.defl")
        self.assertEqual(first["statuses"]["C1"], "pass")
        edit(d.root, "model/m.py", "K = 1.0", "K = 0.0")
        second = d.run("--only", "t.defl")
        got = second["rows"]["t.defl"]
        self.assertEqual(got["admission"], "not-admitted", got)
        self.assertIn("PASSED its own known-bad", got["error"])
        self.assertNotEqual(second["statuses"]["C1"], "pass")

    # -- the CLI: the same holes through `check`, `status` and `report` -------- #
    def _bracket(self, *, git: bool = False) -> str:
        return _projects.bracket_copy(os.path.join(self.tmp(), "bracket"), git=git)

    def test_cli_a_no_op_fixture_is_not_admitted_though_the_cache_would_hit(self):
        # S-05 through the CLI. The bending-stress fixture edited into a no-op
        # hands its gate the known-good design unchanged; the gate passes it. The
        # gate's own verdict entry is untouched and Fresh — before 1.2 that alone
        # served the PASS, and nothing on the way read a selftest.
        project = self._bracket()
        code, _first = check_json(self, project)
        self.assertEqual(code, 1, "the bracket fails C1 on purpose")
        self.assertEqual(status_json(self, project)["claims"]["C2"], "pass",
                         "the positive control: an admitted, fresh PASS reads PASS")
        edit(project, "selftest/bad_configs.py", "return _with(ctx, load_n=300.0)",
             "return _with(ctx)")

        before = status_json(self, project)
        state = before["freshness"]["bracket.bending_stress"]
        self.assertEqual(state["state"], "fresh", f"the verdict cache would hit: {state}")
        self.assertEqual(state["admission"], "undemonstrated", state)
        self.assertNotEqual(before["claims"]["C2"], "pass",
                            "an undemonstrated control never lets the PASS count")

        code, data = check_json(self, project)
        self.assertEqual(code, 1)
        got = verdict_row(data, "bracket.bending_stress")
        self.assertEqual(got["outcome"], "error", got)
        self.assertTrue(got["error"].startswith("not admitted: "), got)
        self.assertIn("PASSED its own known-bad fixture selftest/bad_configs.py:overloaded",
                      got["error"])
        self.assertEqual(data["counts"]["executed"], 0,
                         "no gate ran on the design: every entry was Fresh, and the "
                         "refused gate is never called")
        self.assertEqual(data["counts"]["controls"]["executed"], len(BRACKET_GATES),
                         "bad_configs.py is every bracket control's static input")
        self.assertEqual(blocking_ids(data).get("C2"), "fail", data["blocking"])
        after = status_json(self, project)
        self.assertEqual(after["claims"]["C2"], "fail")
        self.assertEqual(after["freshness"]["bracket.bending_stress"]["admission"],
                         "not-admitted")
        self.assertNotIn("**C2**", proven_section(self, project))

    def test_cli_an_always_true_gate_is_never_pass(self):
        # S-05 in the shape it slipped through: the bracket fails C1 at 0.70 mm,
        # the deflection gate is edited to stop depending on what it measures,
        # and before 1.2 the report printed a PROVEN row showing 0.6997 mm
        # against "<= 0.5 mm". A gate's code is part of its control's static, so
        # the control runs again; the gate passes its own known-bad input and is
        # refused — on this check and on every later one, which reuses the
        # recorded refusal — and no reader lets C1 through.
        project = self._bracket()
        check_json(self, project)
        self.assertEqual(status_json(self, project)["claims"]["C1"], "fail",
                         "the precondition: the live design fails C1")
        before = control_names(project)
        edit(project, "gates/structural.py", "passed=d <= DEFLECTION_LIMIT_MM,", "passed=True,")
        for attempt in ("first", "again"):
            with self.subTest(check=attempt):
                code, data = check_json(self, project)
                self.assertEqual(code, 1)
                got = verdict_row(data, "bracket.deflection")
                self.assertEqual(got["outcome"], "error", got)
                self.assertIn("not admitted: PASSED its own known-bad fixture "
                              "selftest/bad_configs.py:quarter_thickness", got["error"])
                self.assertEqual(blocking_ids(data).get("C1"), "fail", data["blocking"])
                if attempt == "first":
                    # structural.py is every bracket gate's code: all six re-key,
                    # five re-run, and the refused one is never called.
                    self.assertEqual(data["counts"]["executed"], len(BRACKET_GATES) - 1)
                    self.assertEqual(data["counts"]["controls"]["executed"],
                                     len(BRACKET_GATES))
                else:
                    self.assertEqual(data["counts"]["executed"], 0)
                    self.assertEqual(data["counts"]["controls"]["executed"], 0,
                                     "the refusal is a recorded control entry, reused")
                status = status_json(self, project)
                self.assertEqual(status["claims"]["C1"], "fail")
                self.assertNotEqual(status["freshness"]["bracket.deflection"]["state"],
                                    "fresh", "a PASS from the logger was never recorded")
                self.assertNotIn("**C1**", proven_section(self, project))
        [name] = control_names(project)["bracket.deflection"] - before["bracket.deflection"]
        entry = read_control(project, "bracket.deflection", name)
        self.assertEqual((entry["bad"], entry["admitted"]), ("pass", "no"))

    def test_cli_a_logger_with_no_honest_past_is_refused_by_check(self):
        # The same logger on a project that never ran the gate honestly: there
        # is no earlier entry to go stale, so the refusal is all there is.
        # `check` asks admission before the verdict cache and before the run
        # (spec §3.11): the gate is never called, the claim it covers FAILs in
        # the sweep's BLOCKING list and in the statuses `last_check.json` keeps.
        project = self._bracket()
        edit(project, "gates/structural.py", "passed=u <= 1.0,", "passed=True,")
        code, data = check_json(self, project)
        self.assertEqual(code, 1)
        got = verdict_row(data, "bracket.bending_stress")
        self.assertEqual(got["outcome"], "error", got)
        self.assertIn("not admitted: PASSED its own known-bad fixture "
                      "selftest/bad_configs.py:overloaded", got["error"])
        self.assertEqual(blocking_ids(data).get("C2"), "fail", data["blocking"])
        self.assertEqual(data["counts"]["executed"], len(BRACKET_GATES) - 1,
                         "every gate ran but the refused one")
        self.assertEqual(entry_names(project, "bracket.bending_stress"), set(),
                         "a refused gate records no verdict")
        with open(os.path.join(project, ".atompipe", "cache", "last_check.json"),
                  encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["statuses"]["C2"], "fail")

    def test_cli_identity_fixtures_for_deflection_are_not_admitted(self):
        # S-07, D-27, in both spellings the plan names. The live bracket fails
        # deflection (7.0 mm: 0.70 against 0.5), so on the LIVE host either
        # fixture "fired" and was reported "correctly failed ... ~64x worse".
        # Project fixtures now get the known-good host (8.0 mm, which passes):
        # an identity fixture passes it, and a control that passes is refused
        # even though the gate's own FAIL entry is Fresh and would be served.
        for body in ("return ctx", "return _with(ctx)"):
            with self.subTest(fixture=body):
                project = self._bracket()
                check_json(self, project)
                # S-07's condition: the design a live host would hand this
                # fixture fails the gate, so the lie would have "fired".
                [live] = verdicts.read_entries(project, "bracket.deflection")
                self.assertIs(live.verdict["passed"], False, live.verdict)
                edit(project, "selftest/bad_configs.py", QUARTER_THICKNESS, body)
                self.assertEqual(
                    status_json(self, project)["freshness"]["bracket.deflection"]["state"],
                    "fresh", "the verdict cache would serve the FAIL")

                code, data = check_json(self, project)
                self.assertEqual(code, 1)
                got = verdict_row(data, "bracket.deflection")
                self.assertEqual(got["outcome"], "error", got)
                self.assertIn("not admitted: PASSED its own known-bad fixture "
                              "selftest/bad_configs.py:quarter_thickness", got["error"])
                newest = [read_control(project, "bracket.deflection", name)
                          for name in control_names(project)["bracket.deflection"]]
                refusals = [e for e in newest if e["bad"] == "pass"]
                self.assertEqual(len(refusals), 1, newest)
                self.assertEqual((refusals[0]["host"], refusals[0]["admitted"]),
                                 ("known-good", "no"), refusals[0])
                status = status_json(self, project)
                self.assertEqual(status["claims"]["C1"], "fail", "a refused gate still blocks")
                self.assertEqual(status["freshness"]["bracket.deflection"]["admission"],
                                 "not-admitted")

    def test_cli_a_known_good_that_passes_the_live_design_through_is_not_admitted(self):
        """V: the review's repro (admission review, round 1, B), through the CLI
        a person runs. ``known_good.context`` kept the ``ctx.params`` it was
        handed — a copy of the LIVE host — and the fixture was ``return ctx``.
        At span 150 the live design fails, so the control "fired"; the entry
        said ``host: "known-good"`` and keyed no host read. Moved to span 80,
        ``check`` exited 0 with ``controls: cached 1`` and ``report`` put C1
        under PROVEN: the identity fixture admitted on a design nothing keyed —
        S-07 again, and M13.3 broken."""
        project = os.path.join(self.tmp(), "shelf")
        write(project, "model/shelf.py", SHELF_MODEL.format(span="150.0"))
        write(project, "gates/g.py", SHELF_GATE)
        write(project, "claims/C1.json", json.dumps(SHELF_CLAIM) + "\n")
        write(project, "selftest/known_good.py", SHELF_PASSTHROUGH_KNOWN_GOOD)
        write(project, "selftest/bad.py", SHELF_IDENTITY)
        proc = cli(project, "init", "--model", "model/shelf.py", "--name", "shelf")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        code, _first = check_json(self, project)
        self.assertEqual(code, 1, "the live design at span 150 fails")
        edit(project, "model/shelf.py", "span: float = 150.0", "span: float = 80.0")

        code, data = check_json(self, project)
        got = verdict_row(data, "shelf.span")
        self.assertEqual(got["outcome"], "error", got)
        self.assertTrue(got["error"].startswith("not admitted: "), got)
        self.assertEqual(code, 1, "an identity fixture admitted the live design")
        self.assertEqual(blocking_ids(data).get("C1"), "fail", data["blocking"])
        self.assertNotEqual(status_json(self, project)["claims"]["C1"], "pass")
        self.assertNotIn("C1", proven_section(self, project))

        # The positive control: the same project, the same live design, with a
        # known-good that states its own span and a fixture that changes it. It
        # is admitted and C1 reads PROVEN — so the refusal above was the
        # identity fixture's, not the project's.
        write(project, "selftest/known_good.py", SHELF_KNOWN_GOOD)
        write(project, "selftest/bad.py", SHELF_LONG)
        code, data = check_json(self, project)
        self.assertEqual(code, 0, data)
        self.assertEqual(verdict_row(data, "shelf.span")["outcome"], "pass", data)
        self.assertEqual(status_json(self, project)["claims"]["C1"], "pass")
        self.assertIn("**C1**", proven_section(self, project))

    def test_cli_a_control_shown_on_the_cheap_path_does_not_admit_the_costlier_one(self):
        """V: the control half of the review's ``probe.tier`` (false-fresh
        probes, round 1). A gate may pick its path by ``ctx.tier``, and a control
        runs its gate at the sweep's tier — so a demonstration made at tier 0
        exercised the cheap path only. rho_control never keyed the tier either:
        ``check --tier 2`` served that control ``cached``, admitted a costlier
        path that passes a 400 mm span, and C1 read PASS. The control a tier-2
        sweep counts is one its own path fired."""
        project = os.path.join(self.tmp(), "shelf")
        write(project, "model/shelf.py", SHELF_MODEL.format(span="80.0"))
        write(project, "gates/g.py", SHELF_GATE_COSTLY_LOGGER)
        write(project, "claims/C1.json", json.dumps(SHELF_CLAIM) + "\n")
        write(project, "selftest/known_good.py", SHELF_KNOWN_GOOD)
        write(project, "selftest/bad.py", SHELF_LONG)
        proc = cli(project, "init", "--model", "model/shelf.py", "--name", "shelf")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        code, data = check_json(self, project)
        self.assertEqual(code, 0, data)
        self.assertEqual(verdict_row(data, "shelf.span")["outcome"], "pass", data)
        self.assertIn("**C1**", proven_section(self, project), "the positive control")
        [cheap] = control_names(project)["shelf.span"]
        self.assertEqual(read_control(project, "shelf.span", cheap)["reads"].get("tier"), 0,
                         "the tier the control's gate read is not keyed")

        code, data = check_json(self, project, "--tier", "2")
        got = verdict_row(data, "shelf.span")
        self.assertEqual(got["outcome"], "error", got)
        self.assertIn("not admitted: PASSED its own known-bad fixture selftest/bad.py:long",
                      got["error"])
        self.assertEqual(data["counts"]["controls"]["executed"], 1,
                         "the tier-2 path's control never ran: the tier-0 one was served")
        self.assertEqual(code, 1, "a tier-2 sweep admitted a path that passes 400 mm")
        self.assertEqual(blocking_ids(data).get("C1"), "fail", data["blocking"])
        self.assertNotEqual(status_json(self, project)["claims"]["C1"], "pass",
                            "a reader admitted the costlier path on the cheap path's control")
        self.assertNotIn("**C1**", proven_section(self, project))

        # The cheap loop keeps what the costlier path showed: a gate that passes
        # a known-bad design at any tier is a logger at every tier.
        code, data = check_json(self, project)
        self.assertEqual((code, verdict_row(data, "shelf.span")["outcome"]), (1, "error"), data)
        self.assertNotEqual(status_json(self, project)["claims"]["C1"], "pass")

        # The positive control: the costlier path made honest is admitted at
        # tier 2, so the refusal above was the logger's, not the tier's.
        edit(project, "gates/g.py", "passed=True, measured=s", "passed=s <= 100.0, measured=s")
        code, data = check_json(self, project, "--tier", "2")
        self.assertEqual((code, verdict_row(data, "shelf.span")["outcome"]), (0, "pass"), data)
        self.assertEqual(status_json(self, project)["claims"]["C1"], "pass")
        self.assertIn("**C1**", proven_section(self, project))

    def _shelf_from_claim(self, fixture: str, known_good: str | None = None) -> str:
        """The repro A project: span 80 live, C1 at 100 mm, ``shelf.span``
        taking its limit from C1, ``fixture`` as ``selftest/bad.py`` and — with
        no ``known_good`` — no known-good design, so a live host. Its first
        check is the precondition: exit 0, the control an entry that read
        ``claim:C1`` on the host it should have, and C1 under PROVEN."""
        project = os.path.join(self.tmp(), "shelf")
        write(project, "model/shelf.py", SHELF_MODEL.format(span="80.0"))
        write(project, "gates/g.py", SHELF_GATE_FROM_CLAIM)
        write(project, "claims/C1.json", json.dumps(SHELF_CLAIM) + "\n")
        write(project, "selftest/bad.py", fixture)
        if known_good is not None:
            write(project, "selftest/known_good.py", known_good)
        proc = cli(project, "init", "--model", "model/shelf.py", "--name", "shelf")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        code, data = check_json(self, project)
        self.assertEqual(code, 0, data)
        self.assertEqual(data["counts"]["controls"],
                         {"executed": 1, "cached": 0, "reverified": 0}, data["counts"])
        [name] = control_names(project)["shelf.span"]
        entry = read_control(project, "shelf.span", name)
        self.assertEqual((entry["host"], entry["bad"]),
                         ("live" if known_good is None else "known-good", "fail"), entry)
        self.assertIn("claim:C1", entry["reads"]["ledger"],
                      "the precondition: the control read C1 through ctx.ledger")
        self.assertIn("**C1**", proven_section(self, project))
        return project

    def _last_selftest(self, project: str) -> dict:
        """``gate show shelf.span --json``'s ``last_selftest``: the read-only
        judgement (``admission_state``), which never runs a fixture."""
        proc = cli(project, "gate", "show", "shelf.span", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)["last_selftest"]

    def test_cli_a_claim_edit_that_defuses_a_live_control_is_not_admitted(self):
        """V: the review's repro A (admission review, round 1). The control's
        entry keyed ``reads.ledger["claim:C1"]`` in rho_control, but nothing on
        the hint path ever compared it: ``_control_moved`` re-read files,
        listings and host params, and the admission ``_Now`` was built with no
        ledger at all. With C1's limit relaxed from 100 to 500 mm the fixture's
        400 mm is acceptable — ``gate selftest`` said ``[FAIL] ... PASSED its
        own known-bad fixture`` — while ``check`` served the control cached
        and exited 0 with no control run."""
        project = self._shelf_from_claim(SHELF_LONG)
        self.assertEqual(self._last_selftest(project)["admission"], "admitted",
                         "the precondition: the control fired at this version")
        edit(project, "claims/C1.json", '"limit": 100.0', '"limit": 500.0')

        # The read-only judgement, before anything re-runs: the input the
        # control read moved, so it is demonstrated by nothing on disk.
        shown = self._last_selftest(project)
        self.assertEqual(shown["admission"], "undemonstrated", shown)
        self.assertIn("claim:C1", shown["detail"], shown)

        code, data = check_json(self, project)
        self.assertEqual(data["counts"]["controls"]["executed"], 1,
                         f"the moved claim must re-run the control: {data['counts']}")
        got = verdict_row(data, "shelf.span")
        self.assertEqual(got["outcome"], "error", got)
        self.assertTrue(got["error"].startswith("not admitted: "), got)
        self.assertIn("PASSED its own known-bad fixture selftest/bad.py:long", got["error"])
        self.assertEqual(code, 1, "a control defused by a claim edit admitted its gate")
        self.assertEqual(blocking_ids(data).get("C1"), "fail", data["blocking"])
        self.assertEqual(self._last_selftest(project)["admission"], "not-admitted")
        self.assertNotEqual(status_json(self, project)["claims"]["C1"], "pass")
        self.assertNotIn("C1", proven_section(self, project))

        # And back: at 100 mm the first entry is current again and decides.
        edit(project, "claims/C1.json", '"limit": 500.0', '"limit": 100.0')
        code, data = check_json(self, project)
        self.assertEqual(code, 0, data)
        self.assertEqual(data["counts"]["controls"],
                         {"executed": 0, "cached": 1, "reverified": 0}, data["counts"])
        self.assertIn("**C1**", proven_section(self, project))

    def test_cli_a_fixture_that_states_its_own_ledger_survives_a_claim_edit_unrun(self):
        """V: the other side of repro A's fix, openmodelica's shape. A live-host
        fixture that hands its gate a ledger of its own recorded reads of THAT
        ledger, which never equal the live one: compared against the live
        ledger alone, every claim edit — and every check of a project with
        claims — would re-run the control, and every reader would call it
        undemonstrated. The fixture re-run alone shows it hands its gate
        exactly what the entry recorded: nothing executes, no control entry is
        written, and the readers agree once the check has run."""
        project = self._shelf_from_claim(SHELF_LONG_OWN_LEDGER)
        before = control_names(project)
        self.assertEqual(self._last_selftest(project)["admission"], "admitted",
                         "the run that filed the control vouches for the live ledger")
        _code, data = check_json(self, project)
        self.assertEqual(data["counts"]["controls"],
                         {"executed": 0, "cached": 1, "reverified": 0}, data["counts"])

        edit(project, "claims/C1.json", '"limit": 100.0', '"limit": 500.0')
        code, data = check_json(self, project)
        self.assertEqual(code, 0, data)
        self.assertEqual(data["counts"]["controls"],
                         {"executed": 0, "cached": 0, "reverified": 1}, data["counts"])
        self.assertEqual(verdict_row(data, "shelf.span")["outcome"], "pass", data)
        self.assertEqual(control_names(project), before, "a new control entry")
        self.assertEqual(self._last_selftest(project)["admission"], "admitted")
        self.assertIn("**C1**", proven_section(self, project))
        _code, again = check_json(self, project)
        self.assertEqual(again["counts"]["controls"],
                         {"executed": 0, "cached": 1, "reverified": 0},
                         "a re-verified live ledger is remembered: nothing runs twice")

    def test_cli_a_claim_edit_under_a_known_good_ledger_moves_the_file_it_came_from(self):
        """V: why ``_ledger_moved`` leaves a known-good control's ledger reads
        alone. ``context`` is handed an empty ledger, so a known-good design
        whose gate reads C1 must load it — and the file it opens is a control
        read like any other: the same claim edit misses the entry by
        ``claims/C1.json``, re-runs the control and is not admitted."""
        project = self._shelf_from_claim(SHELF_LONG, SHELF_KNOWN_GOOD_LOADS_CLAIMS)
        [name] = control_names(project)["shelf.span"]
        entry = read_control(project, "shelf.span", name)
        self.assertIn("claims/C1.json", entry["reads"]["files"])
        edit(project, "claims/C1.json", '"limit": 100.0', '"limit": 500.0')
        shown = self._last_selftest(project)
        self.assertEqual(shown["admission"], "undemonstrated", shown)
        code, data = check_json(self, project)
        self.assertEqual(data["counts"]["controls"]["executed"], 1, data["counts"])
        got = verdict_row(data, "shelf.span")
        self.assertIn("PASSED its own known-bad fixture selftest/bad.py:long", got["error"])
        self.assertEqual(code, 1)
        self.assertNotIn("C1", proven_section(self, project))

    def test_cli_a_pack_asset_edit_misses_the_control_entry_and_reruns_it(self):
        # A pack control is keyed by its owner's whole `selftest/` (spec §3.8),
        # so an edited mesh, Modelica source or baseline value there must miss
        # every control entry of that pack. The copy under `.atompipe/packs/`
        # is the pack the project loads; the bundled one is never touched.
        for pack, rel, how, old, new in PACK_ASSET_EDITS:
            with self.subTest(pack=pack, file=rel):
                project = _projects.wrap_pack_baseline(
                    pack, os.path.join(self.tmp(), pack), copy_pack=True)
                specs = _projects.pack_gates(pack)
                available = {spec.id: gates.availability(spec) for spec in specs}
                here = sorted(gid for gid, (ok, _why) in available.items() if ok)
                self.assertTrue(here, f"{pack}: no gate can run here, so no control "
                                      f"could miss — the scenario would prove nothing")
                check_json(self, project, "--tier", "3")
                _code, second = check_json(self, project, "--tier", "3")
                before = control_names(project)
                self.assertLess(second["counts"]["controls"]["executed"], len(here),
                                f"a second check re-ran every control: nothing is "
                                f"cached, so a miss would be invisible "
                                f"{second['counts']['controls']}")

                edit_bytes(os.path.join(project, ".atompipe", "packs", pack,
                                        *rel.split("/")), how, old, new)

                _code, third = check_json(self, project, "--tier", "3")
                after = control_names(project)
                self.assertEqual(third["counts"]["controls"]["executed"], len(here),
                                 third["counts"]["controls"])
                for spec in specs:
                    ok, why = available[spec.id]
                    got = verdict_row(third, spec.id)
                    new_names = after.get(spec.id, set()) - before.get(spec.id, set())
                    if ok:
                        self.assertEqual(len(new_names), 1,
                                         f"{spec.id}: the edit did not miss its control "
                                         f"entry ({sorted(after.get(spec.id, ()))})")
                        self.assertEqual(got["outcome"], "pass",
                                         f"{spec.id}: the edit changed an answer: {got}")
                    else:
                        # CI has neither trimesh nor omc: the asserted outcome is
                        # the availability skip, never "not admitted".
                        self.assertEqual((got["outcome"], got.get("skip_reason")),
                                         ("skipped", why), got)
                        self.assertNotIn(spec.id, after, "a gate that cannot run "
                                                         "demonstrates nothing")

    def test_cli_bytecode_in_selftest_writes_no_new_control_entry(self):
        # A `git ls-files` walk sees untracked-not-ignored files, and a project
        # may ignore no bytecode (spec §3.8's walk excludes it itself). A
        # `.pyc` header carries an mtime and the interpreter's name, so were it
        # an input every interpreter and every import would write new control
        # entries into a tracked cache.
        project = self._bracket(git=True)
        check_json(self, project)
        before = control_names(project)
        self.assertEqual(sorted(before), sorted(BRACKET_GATES))
        # From 1.3 that first check migrated the copy, and the migration's root
        # `.gitignore` block ignores bytecode — which would hide the case this
        # test is for. A user may delete the block; the walk must exclude
        # bytecode by itself, so the block goes before the bytecode arrives.
        os.remove(os.path.join(project, ".gitignore"))
        proc = _env.run([sys.executable, "-m", "compileall", "-q", "selftest"], cwd=project)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        pyc = [p for p in untracked(project) if p.startswith("selftest/__pycache__/")]
        self.assertTrue(pyc, "the precondition: git lists the bytecode as untracked, so "
                             "a walk that did not exclude it would read it")

        code, data = check_json(self, project)
        self.assertEqual(code, 1)
        self.assertEqual(control_names(project), before, "a new control entry")
        self.assertEqual(data["counts"]["controls"],
                         {"executed": 0, "cached": len(BRACKET_GATES), "reverified": 0})

    def test_cli_a_config_default_edit_reverifies_every_control_by_its_fixture(self):
        # The transcript's edit (bed_xy 220 -> 250). Every fixture builds through
        # model/bracket.py, so every control's fixture closure moved — but the
        # known-good design states every Config field, so no value a control fed
        # its gate moved. Six fixture-only re-runs, zero control runs, and not
        # one new file for the tracked cache beyond the one gate that re-ran.
        project = self._bracket(git=True)
        check_json(self, project)
        commit_all(project, "the first check's cache")
        self.assertEqual(untracked(project), [])
        controls = control_names(project)
        edit(project, "model/bracket.py", "bed_xy: float = 220.0", "bed_xy: float = 250.0")

        code, data = check_json(self, project)
        self.assertEqual(code, 1)
        self.assertEqual(data["counts"]["controls"],
                         {"executed": 0, "cached": 0, "reverified": len(BRACKET_GATES)})
        self.assertEqual(data["counts"]["executed"], 1, "bed_fit, and only bed_fit, re-ran")
        new = untracked(project)
        self.assertEqual(len(new), 1, new)
        self.assertRegex(new[0], r"^\.atompipe/verdicts/bracket\.bed_fit/"
                                 r"[0-9a-f]{16}-[0-9a-f]{8}\.json$")
        self.assertEqual(control_names(project), controls, "a new control entry")

        _code, again = check_json(self, project)
        self.assertEqual(again["counts"]["controls"],
                         {"executed": 0, "cached": len(BRACKET_GATES), "reverified": 0},
                         "a re-verified closure is remembered: nothing runs twice")

    def test_cli_a_build_edit_that_moves_a_control_input_writes_a_new_control_entry(self):
        # S-19's model-code half through `check`: a formula edit in build()
        # moves what a fixture hands its gate. The gates whose recorded control
        # reads include the moved value miss their entry and run their control
        # again; every other control is re-verified by its fixture alone.
        project = self._bracket(git=True)
        check_json(self, project)
        commit_all(project, "the first check's cache")
        before = control_names(project)
        reads = {}
        for gate_id, names in before.items():
            [name] = names
            entry = read_control(project, gate_id, name)
            reads[gate_id] = {tuple(item[0]) for item in entry["reads"]["params"]}
        moved = {gate_id for gate_id, paths in reads.items() if ("deflection",) in paths}
        # Derived from the entries and typed from the gates' source, and the two
        # must agree: a tracer that over-records moves the derived side only.
        self.assertEqual(moved, {"bracket.deflection"}, reads)

        edit(project, "model/bracket.py",
             'c.load_n * c.arm_length ** 3 / (3.0 * mat["E"] * inertia)',
             'c.load_n * c.arm_length ** 3 / (2.9 * mat["E"] * inertia)')
        code, data = check_json(self, project)
        self.assertEqual(code, 1)
        after = control_names(project)
        grew = {gate_id for gate_id in after if after[gate_id] - before.get(gate_id, set())}
        self.assertEqual(grew, moved)
        self.assertEqual(data["counts"]["controls"],
                         {"executed": len(moved), "cached": 0,
                          "reverified": len(BRACKET_GATES) - len(moved)})
        got = verdict_row(data, "bracket.deflection")
        self.assertEqual(got["outcome"], "fail",
                         f"the control still fires, so the live FAIL stands admitted: {got}")
        [new] = after["bracket.deflection"] - before["bracket.deflection"]
        self.assertEqual(read_control(project, "bracket.deflection", new)["bad"], "fail")

    def _shelf_module_fixture(self, ref: str, files: dict[str, str]) -> str:
        """The shelf at span 80 with its control's fixture declared as the
        importable ``ref`` (``module:function``) and written as ``files`` —
        outside ``selftest/``, so no byte of it is in the control's static part
        (a ``selftest/known_good.py`` among ``files`` makes the host known-good,
        else it is live). Its first check is the precondition: exit 0, the
        control run and filed, C1 under PROVEN."""
        project = os.path.join(self.tmp(), "shelf")
        self.assertEqual(SHELF_GATE.count(SHELF_FIXTURE_DECL), 1)
        write(project, "model/shelf.py", SHELF_MODEL.format(span="80.0"))
        write(project, "gates/g.py", SHELF_GATE.replace(SHELF_FIXTURE_DECL, f'fixture="{ref}"'))
        write(project, "claims/C1.json", json.dumps(SHELF_CLAIM) + "\n")
        for rel, text in files.items():
            write(project, rel, text)
        proc = cli(project, "init", "--model", "model/shelf.py", "--name", "shelf")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        code, data = check_json(self, project)
        self.assertEqual(code, 0, data)
        self.assertEqual(data["counts"]["controls"],
                         {"executed": 1, "cached": 0, "reverified": 0}, data["counts"])
        self.assertIn("**C1**", proven_section(self, project))
        return project

    def _refused(self, project: str, ref: str) -> None:
        """The defused control, through every door: ``check`` re-runs it and
        refuses the gate, BLOCKING names C1, no reader says PASS, the report
        keeps it out of PROVEN, and ``gate selftest`` agrees."""
        code, data = check_json(self, project)
        self.assertEqual(data["counts"]["controls"]["executed"], 1,
                         f"the defused fixture's control was served, not re-run: "
                         f"{data['counts']}")
        got = verdict_row(data, "shelf.span")
        self.assertEqual(got["outcome"], "error", got)
        self.assertIn(f"not admitted: PASSED its own known-bad fixture {ref}", got["error"])
        self.assertEqual(code, 1, "a fixture edited into a no-op admitted its gate")
        self.assertEqual(blocking_ids(data).get("C1"), "fail", data["blocking"])
        self.assertNotEqual(status_json(self, project)["claims"]["C1"], "pass")
        self.assertNotIn("**C1**", proven_section(self, project))
        proc = cli(project, "gate", "selftest", "--no-record")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(f"PASSED its own known-bad fixture {ref}", proc.stdout)

    def test_cli_a_module_fixture_edited_into_a_no_op_is_not_admitted(self):
        """V: the admission review's repro C (round 1). ``load_fixture``'s
        ``module:function`` form went through ``importlib.import_module``, so
        no loader recorded the fixture's closure and the control entry's hint
        was ``{"files": {}}`` — an empty mapping never moves, so the hint always
        held; and ``fixtures/bad.py`` sits outside ``selftest/``, so no byte of
        it was in the static part either. Defused 400 -> 40 mm, ``check`` said
        ``0 executed, 1 cached`` and exited 0 while ``gate selftest`` said
        PASSED its own known-bad fixture. The edit here is the same-size one
        over bytecode a hand run (``python -c "import fixtures.bad"``) left
        beside it, the source's mtime put back: re-running the fixture through
        the stock import alone would still build 400 mm from that ``.pyc``."""
        ref = "fixtures.bad:long"
        project = self._shelf_module_fixture(ref, {"fixtures/__init__.py": "",
                                                   "fixtures/bad.py": SHELF_LONG})
        [name] = control_names(project)["shelf.span"]
        self.assertIn("fixtures/bad.py",
                      read_control(project, "shelf.span", name)["fixture"]["files"],
                      "the module fixture's code closure was not recorded")

        path = os.path.join(project, "fixtures", "bad.py")
        py_compile.compile(path, cfile=importlib.util.cache_from_source(path), doraise=True)
        stat = os.stat(path)
        edit(project, "fixtures/bad.py", SHELF_BAD_SPAN, SHELF_DEFUSED_SPAN)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(os.stat(path).st_size, stat.st_size, "the edit is not same-size")
        self._refused(project, ref)

        # The positive control: the fixture restored is admitted again, and C1
        # reads PROVEN — so the refusal above was the no-op's, not the form's.
        edit(project, "fixtures/bad.py", SHELF_DEFUSED_SPAN, SHELF_BAD_SPAN)
        code, data = check_json(self, project)
        self.assertEqual(code, 0, data)
        self.assertEqual(verdict_row(data, "shelf.span")["outcome"], "pass", data)
        self.assertEqual(status_json(self, project)["claims"]["C1"], "pass")
        self.assertIn("**C1**", proven_section(self, project))

    def test_cli_a_module_fixture_no_loader_records_is_reverified_on_every_check(self):
        """V: the other half of repro C's fix. A ``module:function`` fixture
        that is a package's ``__init__`` goes through the stock import (as an
        installed module or an extension would), so no closure of it is
        recorded. Its control's hint must then vouch for nothing: every
        ``check`` re-runs the fixture alone and compares what it builds — never
        a control entry written, the gate never called — so the same defusing
        edit is caught on the next check. On a known-good host too: there the
        known-good module's closure was folded into the fixture's, and with no
        fixture closure to fold into it became the whole hint — one that held
        while the fixture's code moved."""
        ref = "fixtures:long"
        for host, known_good in (("live", None), ("known-good", SHELF_KNOWN_GOOD)):
            with self.subTest(host=host):
                files = {"fixtures/__init__.py": SHELF_LONG}
                if known_good is not None:
                    files["selftest/known_good.py"] = known_good
                project = self._shelf_module_fixture(ref, files)
                before = control_names(project)
                [name] = before["shelf.span"]
                entry = read_control(project, "shelf.span", name)
                self.assertEqual(entry["host"], host, entry)
                self.assertEqual(entry["fixture"]["files"], {verdicts.UNRECORDED_FIXTURE: None},
                                 "a fixture whose code no loader recorded filed a hint that "
                                 "can hold")
                code, data = check_json(self, project)
                self.assertEqual(code, 0, data)
                self.assertEqual(data["counts"]["controls"],
                                 {"executed": 0, "cached": 0, "reverified": 1},
                                 "an unrecorded fixture closure was served as a held hint")
                self.assertEqual(data["counts"]["executed"], 0, data["counts"])
                self.assertEqual(control_names(project), before,
                                 "a re-verification wrote a file")

                edit(project, "fixtures/__init__.py", SHELF_BAD_SPAN, 'params["span"] = 40.0')
                self._refused(project, ref)

    def test_cli_a_file_a_fixture_module_reads_at_import_is_keyed(self):
        """V: the admission review's repro D (round 1). A fixture module that
        reads its known-bad span at IMPORT, from ``inputs/data/bad_span.json``:
        the module was loaded before the control's trace window opened, so the
        read was recorded nowhere — not on the trace, not in the closure the
        hint is made of, and ``inputs/`` is no part of the static walk. Defused
        400 -> 40 mm, ``check`` said ``0 executed, 1 cached`` and exited 0 while
        ``gate selftest`` said PASSED its own known-bad fixture. The same held
        for ``selftest/known_good.py``, loaded outside the window as well: its
        design's span read at import went 80 -> 15, the five-fold fixture built
        75 mm, and nothing moved. Each case ends with its positive control:
        the data restored is admitted again, and C1 reads PROVEN."""
        ref = "selftest/bad.py:long"
        cases = (
            ("fixture", {"selftest/bad.py": SHELF_LONG_FROM_DATA},
             "inputs/data/bad_span.json", 400.0, 40.0),
            ("known-good", {"selftest/bad.py": SHELF_FIVEFOLD,
                            "selftest/known_good.py": SHELF_KNOWN_GOOD_FROM_DATA},
             "inputs/data/good_span.json", 80.0, 15.0),
        )
        for which, files, data, bad, defused in cases:
            with self.subTest(module=which):
                files = dict(files)
                files[data] = json.dumps({"span": bad}) + "\n"
                project = self._shelf_module_fixture(ref, files)
                [name] = control_names(project)["shelf.span"]
                self.assertIn(data, read_control(project, "shelf.span", name)["fixture"]["files"],
                              "a file the module read at import is not in the control's hint")

                write(project, data, json.dumps({"span": defused}) + "\n")
                self._refused(project, ref)

                write(project, data, json.dumps({"span": bad}) + "\n")
                code, out = check_json(self, project)
                self.assertEqual(code, 0, out)
                self.assertEqual(verdict_row(out, "shelf.span")["outcome"], "pass", out)
                self.assertEqual(status_json(self, project)["claims"]["C1"], "pass")
                self.assertIn("**C1**", proven_section(self, project))

    def test_cli_a_limit_a_gate_module_reads_at_import_is_keyed(self):
        """V: repro D's verdict side. A gate module is loaded before any window
        too, so a limit it reads at import was keyed by no entry: tightened 100
        -> 50 mm under a design at 80, a plain ``check`` served the PASS as
        cached and exited 0, while ``--force`` failed it. The positive control
        is the first check: the same project at 100 mm passes, so the refusal
        is the edit's."""
        project = os.path.join(self.tmp(), "shelf")
        write(project, "model/shelf.py", SHELF_MODEL.format(span="80.0"))
        write(project, "gates/g.py", SHELF_GATE_LIMIT_FROM_DATA)
        write(project, "selftest/bad.py", SHELF_LONG)
        write(project, "inputs/data/limit.json", json.dumps({"span": 100.0}) + "\n")
        write(project, "claims/C1.json", json.dumps(SHELF_CLAIM) + "\n")
        proc = cli(project, "init", "--model", "model/shelf.py", "--name", "shelf")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        code, data = check_json(self, project)
        self.assertEqual(code, 0, data)
        self.assertEqual(verdict_row(data, "shelf.span")["outcome"], "pass", data)

        write(project, "inputs/data/limit.json", json.dumps({"span": 50.0}) + "\n")
        code, data = check_json(self, project)
        self.assertEqual(data["counts"]["executed"], 1,
                         f"the gate's PASS was served after the limit it read at import "
                         f"moved: {data['counts']}")
        self.assertEqual(verdict_row(data, "shelf.span")["outcome"], "fail", data)
        self.assertEqual(code, 1, "a tightened limit read at import kept its PASS")
        self.assertEqual(blocking_ids(data).get("C1"), "fail", data["blocking"])
        self.assertNotEqual(status_json(self, project)["claims"]["C1"], "pass")

    # -- code loaded at RUN time (admission review, round 2) ------------------ #
    def _shelf_known_good_loads_the_model(self, fixture: str) -> str:
        """Repro (a)'s project: the live shelf at 150 mm (failing), a known-good
        design that is the live model loaded by path when ``context`` runs, and
        ``fixture``. Its first check is the precondition: exit 1 (the live
        design fails), the control filed on the known-good host and fired, and
        the model it loaded an input of that control."""
        project = os.path.join(self.tmp(), "shelf")
        write(project, "model/shelf.py", SHELF_MODEL.format(span="150.0"))
        write(project, "gates/g.py", SHELF_GATE)
        write(project, "claims/C1.json", json.dumps(SHELF_CLAIM) + "\n")
        write(project, "selftest/known_good.py", SHELF_KNOWN_GOOD_LOADS_THE_MODEL)
        write(project, "selftest/bad.py", fixture)
        proc = cli(project, "init", "--model", "model/shelf.py", "--name", "shelf")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        code, data = check_json(self, project)
        self.assertEqual(code, 1, data)
        self.assertEqual(verdict_row(data, "shelf.span")["outcome"], "fail", data)
        [name] = control_names(project)["shelf.span"]
        entry = read_control(project, "shelf.span", name)
        self.assertEqual((entry["host"], entry["bad"]), ("known-good", "fail"), entry)
        self.assertIn("model/shelf.py", entry["reads"]["files"],
                      "the model the known-good design loaded when it ran is not an "
                      "input of its control")
        return project

    def test_cli_a_known_good_that_loads_the_live_model_at_run_time_is_keyed(self):
        """V: the admission review's round-2 repro (a). ``known_good.context``
        loaded ``model/shelf.py`` with ``modelio.load_path`` when it RAN — no
        module was being loaded, so no closure recorded it; the loader's read
        of its source was the import system's, which the trace drops; and
        ``model/`` is in no static walk. The known-good design was the live one
        with no key (S-07 again): the literal identity fixture "fired" while the
        shelf failed at 150 mm, and after a model edit to 80 mm ``check`` served
        that control cached, exited 0, and put C1 under PROVEN, while ``gate
        selftest`` said PASSED its own known-bad fixture. The positive control:
        an honest fixture on the same known-good design is re-run by the same
        edit — its control keyed the model too — and admitted, C1 PROVEN."""
        ref = "selftest/bad.py:long"
        project = self._shelf_known_good_loads_the_model(SHELF_IDENTITY)
        edit(project, "model/shelf.py", "span: float = 150.0", "span: float = 80.0")
        self._refused(project, ref)

        project = self._shelf_known_good_loads_the_model(SHELF_LONG)
        before = control_names(project)["shelf.span"]
        edit(project, "model/shelf.py", "span: float = 150.0", "span: float = 80.0")
        code, data = check_json(self, project)
        self.assertEqual(data["counts"]["controls"]["executed"], 1,
                         f"the model the known-good design loaded moved and its control "
                         f"was served: {data['counts']}")
        self.assertEqual(code, 0, data)
        self.assertEqual(verdict_row(data, "shelf.span")["outcome"], "pass", data)
        self.assertNotEqual(control_names(project)["shelf.span"], before,
                            "a control at other inputs wrote no entry of its own")
        self.assertEqual(status_json(self, project)["claims"]["C1"], "pass")
        self.assertIn("**C1**", proven_section(self, project))

    def test_cli_a_helper_a_fixture_loads_at_run_time_is_keyed(self):
        """V: the admission review's round-2 repro (b). ``long()`` took its
        known-bad span from ``lib/badgen.py``, loaded when the FIXTURE ran: by
        ``load_path``, it joined no closure (none was being recorded) and its
        source read was the import system's; by a literal
        ``importlib.import_module``, the static walk saw no import statement.
        ``lib/`` is outside ``selftest/``, so no byte of it was keyed. Defused
        400 -> 40 mm, ``check`` served the control cached and C1 stayed PROVEN
        while ``gate selftest`` said PASSED its own known-bad fixture.
        On a live and a known-good host; each case ends with its positive
        control: restored, admitted, C1 PROVEN."""
        ref = "selftest/bad.py:long"
        for spelling, fixture in (("load_path", SHELF_LONG_BY_PATH),
                                  ("import_module", SHELF_LONG_BY_NAME)):
            for host, known_good in (("live", None), ("known-good", SHELF_KNOWN_GOOD)):
                with self.subTest(spelling=spelling, host=host):
                    files = {"selftest/bad.py": fixture, "lib/badgen.py": "SPAN = 400.0\n"}
                    if known_good is not None:
                        files["selftest/known_good.py"] = known_good
                    project = self._shelf_module_fixture(ref, files)
                    [name] = control_names(project)["shelf.span"]
                    entry = read_control(project, "shelf.span", name)
                    self.assertEqual(entry["host"], host, entry)
                    keyed = {**entry["reads"]["files"], **entry["fixture"]["files"]}
                    self.assertIn("lib/badgen.py", keyed,
                                  "the helper the fixture loaded when it ran is keyed "
                                  "nowhere in its control")

                    # Not same-size: a module the stock import system loads may run
                    # from a `__pycache__` that still validates (a named residual —
                    # load_path compiles the bytes on disk), and this test is about
                    # the key, not the bytecode.
                    write(project, "lib/badgen.py", "SPAN = 40.0\n")
                    self._refused(project, ref)

                    write(project, "lib/badgen.py", "SPAN = 400.0\n")
                    code, data = check_json(self, project)
                    self.assertEqual(code, 0, data)
                    self.assertEqual(verdict_row(data, "shelf.span")["outcome"], "pass", data)
                    self.assertEqual(status_json(self, project)["claims"]["C1"], "pass")
                    self.assertIn("**C1**", proven_section(self, project))

    def test_cli_a_limit_a_gate_loads_at_run_time_is_keyed(self):
        """V: the admission review's round-2 repro (c), the verdict side. A gate
        that took its limit from ``load_path(gates/_tables.py).LIMIT`` inside
        its body keyed nothing of it: the first gate's load was recorded by no
        closure and its source read was dropped, and the second gate was served
        the cached module, which joined nothing at all. One that took it from
        ``importlib.import_module("shelf_limits")`` had no import statement for
        the static walk, so ``code.files`` omitted the helper. Each limit 100 ->
        50 mm under a design at 80: a plain ``check`` served the PASS
        ``cached=true`` (and ``--force`` then filed "two outcomes recorded for
        identical inputs"). The helper by path reads its limit from
        ``inputs/data/limit.json`` at import, so what it read is keyed as well
        as what it is. The positive control is the first check: every gate
        passes at 100 mm and each claim is PROVEN."""
        project = os.path.join(self.tmp(), "shelf")
        write(project, "model/shelf.py", SHELF_MODEL.format(span="80.0"))
        write(project, "gates/g.py", SHELF_GATES_LOAD_AT_RUN_TIME)
        write(project, "gates/_tables.py", SHELF_TABLES)
        write(project, "inputs/data/limit.json", json.dumps({"span": 100.0}) + "\n")
        write(project, "lib/shelf_limits.py", "LIMIT = 100.0\n")
        write(project, "selftest/bad.py", SHELF_LONG)
        for cid, tag in RUN_TIME_CLAIMS.items():
            write(project, f"claims/{cid}.json", json.dumps(
                dict(SHELF_CLAIM, statement=f"Span within 100 mm ({tag})", tags=[tag])) + "\n")
        proc = cli(project, "init", "--model", "model/shelf.py", "--name", "shelf")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        code, data = check_json(self, project)
        self.assertEqual(code, 0, data)
        proven = proven_section(self, project)
        for cid, tag in RUN_TIME_CLAIMS.items():
            self.assertIn(f"**{cid}**", proven, "the positive control")
        by_path = ("shelf.by_path", "shelf.by_path_again")
        docs = {}
        for gate_id in (*by_path, "shelf.by_name"):
            [name] = entry_names(project, gate_id)
            with open(os.path.join(project, ".atompipe", "verdicts", gate_id, name),
                      encoding="utf-8") as fh:
                docs[gate_id] = json.load(fh)
        for gate_id in by_path:
            with self.subTest(gate=gate_id):
                self.assertIn("gates/_tables.py", docs[gate_id]["reads"]["files"],
                              "the helper a gate loaded when it ran is not keyed")
                self.assertIn("inputs/data/limit.json", docs[gate_id]["reads"]["files"],
                              "what the helper read at its import is not keyed")
        self.assertIn("lib/shelf_limits.py", docs["shelf.by_name"]["code"]["files"],
                      "a module named to import_module by a literal is not in the code")

        write(project, "inputs/data/limit.json", json.dumps({"span": 50.0}) + "\n")
        code, data = check_json(self, project)
        for gate_id in by_path:
            with self.subTest(gate=gate_id, moved="inputs/data/limit.json"):
                row = verdict_row(data, gate_id)
                self.assertFalse(row["cached"], f"the PASS was served after its limit moved: "
                                                f"{row}")
                self.assertEqual((row["outcome"], row["limit"]), ("fail", 50.0), row)
        self.assertTrue(verdict_row(data, "shelf.by_name")["cached"],
                        "a gate that loaded nothing that moved re-ran")
        self.assertEqual(code, 1, "a tightened limit loaded at run time kept its PASS")
        self.assertEqual({c: blocking_ids(data).get(c) for c in ("C1", "C2")},
                         {"C1": "fail", "C2": "fail"}, data["blocking"])

        write(project, "lib/shelf_limits.py", "LIMIT = 50.0\n")
        code, data = check_json(self, project)
        row = verdict_row(data, "shelf.by_name")
        self.assertFalse(row["cached"], f"the PASS was served after its limit moved: {row}")
        self.assertEqual((row["outcome"], row["limit"]), ("fail", 50.0), row)
        self.assertEqual(blocking_ids(data).get("C3"), "fail", data["blocking"])
        status = status_json(self, project)["claims"]
        self.assertEqual({c: status[c] for c in RUN_TIME_CLAIMS},
                         {"C1": "fail", "C2": "fail", "C3": "fail"})
        code, data = check_json(self, project, "--force")
        self.assertEqual(code, 1, data)
        for gate_id in (*by_path, "shelf.by_name"):
            self.assertEqual(verdict_row(data, gate_id)["outcome"], "fail",
                             "a forced run disagrees with the plain one")


# --------------------------------------------------------------------------- #
class SweepOrder(_env.EnvCase):
    """Availability, then admission, then the cache, then the run."""

    def test_a_missing_tool_reads_skipped_never_not_admitted(self):
        # CI has neither trimesh nor omc: a gate whose tool is missing must read
        # skipped (its claim BLOCKED), never "not admitted" (an error, FAIL).
        p = Project(self)
        result = p.sweep()
        got = row(result, "t.tool")
        self.assertEqual(got.verdict.outcome, "skipped", got.verdict)
        self.assertFalse(got.verdict.error)
        self.assertIsNone(got.admission, "no control is asked of a gate that cannot run")
        self.assertFalse(os.path.isdir(os.path.join(p.root, verdicts.CONTROL_OUT_DIR,
                                                    "t.tool")))
        self.assertEqual(p.statuses()["C5"], ClaimStatus.BLOCKED)

    def test_where_a_tool_goes_missing_a_fresh_pass_is_skipped_and_a_fresh_fail_served(self):
        p = Project(self)
        p.sweep(only=["t.defl"])
        failing = Project(self, thickness=7.0)
        failing.sweep(only=["t.defl"])
        real = gates.availability
        missing = lambda spec: ((False, "requires python trimesh (not importable)")  # noqa: E731
                                if spec.id == "t.defl" else real(spec))
        with mock.patch.object(gates, "availability", side_effect=missing):
            skipped = row(p.sweep(only=["t.defl"]), "t.defl")
            served = row(failing.sweep(only=["t.defl"]), "t.defl")
        self.assertEqual(skipped.verdict.outcome, "skipped")
        self.assertTrue(skipped.verdict.skip_reason.startswith("cached pass exists; "),
                        skipped.verdict.skip_reason)
        self.assertIsNone(skipped.admission)
        self.assertEqual(served.verdict.outcome, "fail", "a refutation keeps its power (R-3)")
        self.assertTrue(served.cached)

    def test_a_gate_named_above_the_ceiling_runs_its_control(self):
        p = Project(self)
        result = p.sweep(max_tier=0)
        self.assertNotIn("t.slow", [r.verdict.gate for r in result.rows])
        self.assertIn(("t.slow", "above the tier ceiling"), result.not_run)
        named = p.sweep(max_tier=0, only=["t.slow"])
        got = row(named, "t.slow")
        self.assertEqual(named.controls["executed"], 1, "naming it opts in to its control")
        self.assertEqual(got.admission.state, "admitted")
        self.assertEqual(got.verdict.outcome, "pass")
        self.assertIn(("t.defl", "excluded by --only"), named.not_run)

    def test_record_false_writes_nothing_under_atompipe_but_out(self):
        # S-32: the documented dry sweep. It writes no entry, control entry,
        # remembered outcome, obs, controls.json, digests.json or last_check —
        # and, since nothing global is compared any more, reads nothing as stale.
        p = Project(self)
        state = os.path.join(p.root, ".atompipe")
        dry = p.sweep(record=False)
        self.assertEqual(tree(state, skip=("out",)), {}, "a dry first sweep wrote state")
        self.assertTrue(row(dry, "t.defl").executed)
        self.assertEqual(row(dry, "t.defl").verdict.outcome, "pass")
        self.assertTrue(row(dry, "t.defl").fresh)
        self.assertEqual(row(dry, "t.always").admission.state, "not-admitted",
                         "a dry sweep still demonstrates; it only keeps nothing")
        self.assertNotEqual(row(dry, "t.defl").verdict.rho, "")

        p.sweep()
        kept = tree(state, skip=("out",))
        self.assertTrue(any(k.startswith("verdicts/") for k in kept))
        again = p.sweep(record=False)
        self.assertEqual(tree(state, skip=("out",)), kept, "byte-identical")
        got = row(again, "t.defl")
        self.assertTrue(got.cached and got.fresh, got)
        self.assertEqual(p.statuses()["C1"], ClaimStatus.PASS, "a dry run reads nothing stale")

    def test_the_control_out_dir_is_emptied_first_and_never_the_sweeps(self):
        p = Project(self)
        stale = write(p.root, f"{verdicts.CONTROL_OUT_DIR}/t.defl/left-behind.txt", "old\n")
        keep = write(p.root, ".atompipe/out/evidence.txt", "the sweep's own\n")
        p.sweep(only=["t.defl"])
        self.assertFalse(os.path.exists(stale), "a stale file must never become a read")
        self.assertTrue(os.path.exists(keep), "the sweep's out_dir is not the control's")


if __name__ == "__main__":
    unittest.main()
