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
* a live-host fixture edited to derive its known-bad value from the live span,
  the live C1, or to hand the live design through (`return ctx`) — every value
  it builds equal to its sealed entry's — is not vouched for by the fixture
  alone: the control runs and keys what it read, so a later live edit that
  defuses it is not admitted; nor is a fixture that swaps `ctx.model` or
  `ctx.memo` (admission review, round 2, `r3`); a memo of the fixture's own
  still sends the control to a full run, though a gate can no longer see what
  it holds, and a fixture that fills the memo it was handed is refused as an
  unusable control (review, `ffr3/p2`: only `load_file` opens a memo now);
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
  review, round 2);
* a costlier tier's entry whose path no current control shows reads STALE to
  `check` — plain, `--junit` and `--force` — exactly as to `status`, never a
  skip that a second passing gate turns into readiness, and its reason names
  the one check that settles it, `--tier 2` (review, `repro_undemonstrated`).

Scenarios that edit code run the sweep in a fresh process (`_DRIVER`, through
`_env.run`), per spec §0.4: an in-process module cache must never be what makes
an edit visible or invisible. Everything else runs in-process on a fresh
`Registry` — never `gates.REGISTRY`.


From P2.1 a claim whose evaluator is refused at its version reads Gap
(`unclaimed`, cause `unqualified`), never FAIL — PLAN-v0.14 §1.4, "a claim with no
qualified evaluator remains a gap" — and still blocks. The CLI tests below assert
`unclaimed` where they asserted `fail` (R-6: each a row of
`test_status_table.EXPECTED`, `unadmitted` -> `unclaimed`; nothing else moved).
Run:  PYTHONPATH=src python3 -m unittest tests.test_admission -v
"""
from __future__ import annotations

import collections
import contextlib
import dataclasses
import glob
import importlib.util
import json
import os
import py_compile
import re
import sys
import tempfile
import textwrap
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from atompipe import claims, gates, modelio, store, verdicts
from atompipe import packs as packs_mod
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
#: runs in a project that has none (the test's negative control). Its gate
#: declares its known-good control as a fixture of its own (``good=``, P2.3):
#: ``selftest/good.py`` is KNOWN_GOOD's text, its ``context`` the stated 8.0
#: design, written in every planted project. Without one the live-host project
#: has no known-good control, and the identity fixture's lie on that host —
#: what the negative control shows — would hide behind "known-good not run";
#: and a known-good HOST at a failing design (``data/kg.json`` at 2.0) would
#: be its known-good control too, and fail it.
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
      negative_control=NegativeControl(fixture="selftest/ident.py",
                                       good="selftest/good.py:context"))
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
    write(root, "selftest/good.py", KNOWN_GOOD)
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
    ap.add_argument("--plant", default="")
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    if a.plant:
        # A planted violator, patched into THIS process before anything runs:
        # the code-editing scenarios run here, out of the test's reach.
        with open(a.plant, encoding="utf-8") as fh:
            exec(fh.read(), {"verdicts": verdicts, "gates": gates, "dataclasses": dataclasses})
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
                                        "unqualified": r.verdict.unqualified,
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
    out["unqualified"] = {v.gate: v.unqualified for v in resolution.verdicts if v.unqualified}
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

    def run(self, *args: str, mode: str = "sweep", plant: str = "") -> dict:
        argv = [sys.executable, self.driver, self.root, "--claims", self.claims,
                "--mode", mode]
        if plant:
            argv += ["--plant", write(self.home, "plant.py", plant)]
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

#: The review's repro (``repro_undemonstrated``): the shelf gate honest on every
#: tier's path, reading ``ctx.tier`` as ``GateContext`` allows — so the entry a
#: tier-2 sweep files is the one every reader serves at tier 0 too
#: (``_most_thorough``) — beside a second gate on the same claim that never
#: reads the tier. The second is what turned the first's skip into readiness:
#: one gate passing and one skipped reads PASS (partial), and that does not block.
SHELF_GATES_TIERED = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict

LIMITS = {0: 100.0, 1: 100.0, 2: 100.0, 3: 100.0}


@gate(id="shelf.span", claims=["span"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:long"))
def span(ctx):
    s = float(ctx.params["span"])
    limit = LIMITS[int(ctx.tier)]
    return Verdict(gate="shelf.span", passed=s <= limit, measured=s, limit=limit,
                   units="mm", detail=f"{s} mm (limit {limit})")


@gate(id="shelf.reach", claims=["span"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:long"))
def reach(ctx):
    s = float(ctx.params["span"])
    return Verdict(gate="shelf.reach", passed=s <= 100.0, measured=s, limit=100.0,
                   units="mm", detail=f"{s} mm (limit 100.0)")
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

#: Where the ``r3`` tests keep their fixture, and how the shelf gate names it:
#: outside ``selftest/``, as the repro did, so an edit to it moves only the
#: fixture's closure — the hint — and the control's static part stays put.
#: Under ``selftest/`` the same edit misses every entry by its static part and
#: the control simply runs again: the hole is only reachable from here.
SHELF_OUTSIDE_FIXTURE = "fixtures/bad.py"
SHELF_OUTSIDE_DECL = 'fixture="fixtures/bad.py:long"'

#: The admission review's round-2 repro ``r3``, on a LIVE host (no
#: ``selftest/known_good.py``): the known-bad span SEALED — it reads nothing
#: of the host, so its control entry keys no host param and no ledger read.
#: The repro's next step derives the same 400 mm from what it was handed
#: instead: five times the live span (``SHELF_FIVEFOLD_LIVE``, 80 mm: 400),
#: four times the live C1 limit (``SHELF_FOURFOLD_CLAIM``, 100 mm: 400), or
#: the live design itself (``SHELF_IDENTITY`` at span 400). Equal values, so
#: the fixture alone re-verified the sealed entry, and the entry keyed nothing
#: the live design could move afterwards.
SHELF_SEALED_LONG = '''\
import dataclasses


def long(ctx):
    return dataclasses.replace(ctx, params={"span": 400.0})
'''

SHELF_FIVEFOLD_LIVE = '''\
import dataclasses


def long(ctx):
    return dataclasses.replace(ctx, params={"span": float(ctx.params["span"]) * 5})
'''

SHELF_FOURFOLD_CLAIM = '''\
import dataclasses


def long(ctx):
    limit = float(ctx.ledger.claim("C1").acceptance.limit)
    return dataclasses.replace(ctx, params={"span": 4 * limit})
'''

#: The same repro's other door: two context fields no control entry keys. A
#: gate that branches on whether a model came with its context, or a memo
#: (``is None`` uses nothing: ``ModelProxy`` records nothing, and a
#: ``SweepMemo`` is never traced). Honest while the fixture passes both
#: through; defused by a fixture that swaps either — the span still 400, so
#: every value an entry keys is equal. It branched on what the memo HOLDS
#: (``bool(ctx.memo)``) until a gate could no longer read that (review,
#: ``ffr3/p2``): ``is None`` is the one question left to ask of a memo.
SHELF_GATE_TRUSTS_ITS_CONTEXT = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict


@gate(id="shelf.span", claims=["span"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:long"))
def span(ctx):
    s = float(ctx.params["span"])
    trusted = ctx.model is None or ctx.memo is None
    return Verdict(gate="shelf.span", passed=trusted or s <= 100.0, measured=s,
                   limit=100.0, units="mm", detail=f"{s} mm (limit 100.0)")
'''

#: The memo door alone: since review of P2.3 no run of a project evaluator is
#: handed a model, so the gate above trusts every run, its controls included.
SHELF_GATE_TRUSTS_ITS_MEMO = SHELF_GATE_TRUSTS_ITS_CONTEXT.replace(
    "trusted = ctx.model is None or ctx.memo is None", "trusted = ctx.memo is None")
assert SHELF_GATE_TRUSTS_ITS_MEMO != SHELF_GATE_TRUSTS_ITS_CONTEXT

SHELF_SEALED_NO_MODEL = '''\
import dataclasses


def long(ctx):
    return dataclasses.replace(ctx, params={"span": 400.0}, model=None)
'''

SHELF_SEALED_NO_MEMO = '''\
import dataclasses


def long(ctx):
    return dataclasses.replace(ctx, params={"span": 400.0}, memo=None)
'''

#: A memo of the fixture's own, holding what the gate above once trusted. The
#: gate's view wraps it (a ``SweepMemo``: not None, and nothing to read), so
#: it defuses nothing now — and is still no memo the fixture was handed, so
#: the fixture alone vouches for nothing.
SHELF_SEALED_OWN_MEMO = '''\
import dataclasses


def long(ctx):
    return dataclasses.replace(ctx, params={"span": 400.0}, memo={"span": "trusted"})
'''

#: Fills the memo it was handed. Its view holds a ``SweepMemo`` now, so the
#: fill itself raises ``GateMemoError``: the control is unusable, not defused.
SHELF_SEALED_FILLS_MEMO = '''\
import dataclasses


def long(ctx):
    ctx.memo["span"] = "trusted"
    return dataclasses.replace(ctx, params={"span": 400.0})
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

#: The shelf's known-good design (P2.3), the ``good=`` fixture every shelf gate
#: declares (:func:`write_shelf_gate`). Stated in full, nothing of its host
#: read: span 40 mm (and the reach the model echoes), C1 — the record
#: ``claims/C1.json`` holds, whole, at 100 mm — for the gate that takes its
#: limit off the claim (R-6, review of P2.3: a verdict that read a ledger value
#: no qualification run read does not count, and C1 was a claim of its own with
#: the limit alone). 40, not the live 80: repro D tightens a
#: limit to 50 mm to make the live design fail, and the known-good design must
#: still pass it, or the gate reads unqualified where the test means Not met. A
#: fixture rather than a ``selftest/known_good.py``: most of these projects are
#: the LIVE-host case on purpose — no known-good design, the known-bad fixture
#: handed the live host — and a known_good.py would have made every one of them
#: a known-good host, and the live-host guards untested.
SHELF_GOOD = '''\
import dataclasses

from atompipe.models import Claim, Ledger

C1 = {claim!r}


def shelf(ctx):
    claim = Claim.from_dict(dict(C1, id="C1"))
    return dataclasses.replace(ctx, params={{"span": 40.0, "reach": 40.0}},
                               ledger=Ledger(claims=[claim]))
'''.format(claim=SHELF_CLAIM)

#: How a shelf gate declares that known-good control.
SHELF_GOOD_DECL = 'good="selftest/good.py:shelf", '


def write_shelf_gate(project: str, source: str) -> None:
    """``gates/g.py`` as ``source``, every shelf gate in it declaring the
    known-good control :data:`SHELF_GOOD` (``good=``, P2.3), and that control's
    fixture beside it. Declared here, once, rather than in each gate constant:
    the constants are the review repros as filed, and their fixture lines are
    what the module-form and outside-``selftest/`` tests rewrite."""
    decl = "NegativeControl(fixture="
    if decl not in source:
        raise AssertionError("a shelf gate whose control is not declared as "
                             f"{decl!r}: write_shelf_gate cannot add its known-good control")
    write(project, "gates/g.py", source.replace(decl, "NegativeControl(" + SHELF_GOOD_DECL
                                                + "fixture="))
    write(project, "selftest/good.py", SHELF_GOOD)

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
        # R-6 (P2.3): "paired", where P2.2 filed "reject-only" — the known-good
        # half ran too, on selftest/known_good.py, and passed.
        self.assertEqual((entry["bad"], entry["admitted"], entry["host"]),
                         ("fail", "paired", "known-good"))
        self.assertEqual(p.statuses()["C1"], ClaimStatus.PASS,
                         "the positive control: an admitted, fresh PASS reads PASS")

    def test_an_always_true_gate_never_yields_pass(self):
        # A logger with a declared control produced PROVEN rows (S-05). Its
        # control runs, the gate PASSES its own known-bad input, and the gate is
        # not admitted — its function is never called on the real design. The
        # live design at 7.0, not the default 8.0 (R-6, P2.3): the known-good
        # half now calls the gate on the known-good 8.0 design, and a live design
        # equal to it made "never called on the real input" unaskable by value.
        p = Project(self, thickness=7.0)
        first = p.sweep(only=["t.always"])
        got = row(first, "t.always")
        self.assertEqual(got.verdict.outcome, "error")
        self.assertTrue(got.verdict.error.startswith("unqualified: "), got.verdict.error)
        self.assertIn("known-bad:pass", got.verdict.error)
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
        self.assertEqual("known-bad:pass", got.admission.reason)
        self.assertTrue(got.verdict.error.startswith("unqualified: "), got.verdict)
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
        self.assertEqual("known-bad:pass", got.admission.reason)
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
        self.assertTrue(got.admission.reason.startswith("control:differs|"), got.admission.reason)
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
        self.assertTrue(got["error"].startswith("unqualified: "), got)
        self.assertIn("known-bad:pass", got["error"])
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
        self.assertIn("known-bad:pass", got["error"])
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
        self.assertTrue(got["error"].startswith("unqualified: "), got)
        self.assertIn("known-bad:pass", got["error"])
        self.assertEqual(data["counts"]["executed"], 0,
                         "no gate ran on the design: every entry was Fresh, and the "
                         "refused gate is never called")
        self.assertEqual(data["counts"]["controls"]["executed"], len(BRACKET_GATES),
                         "bad_configs.py is every bracket control's static input")
        self.assertEqual(blocking_ids(data).get("C2"), "unclaimed", data["blocking"])
        after = status_json(self, project)
        self.assertEqual(after["claims"]["C2"], "unclaimed")
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
                self.assertIn("unqualified: known-bad:pass", got["error"])
                self.assertEqual(blocking_ids(data).get("C1"), "unclaimed", data["blocking"])
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
                self.assertEqual(status["claims"]["C1"], "unclaimed")
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
        self.assertIn("unqualified: known-bad:pass", got["error"])
        self.assertEqual(blocking_ids(data).get("C2"), "unclaimed", data["blocking"])
        self.assertEqual(data["counts"]["executed"], len(BRACKET_GATES) - 1,
                         "every gate ran but the refused one")
        self.assertEqual(entry_names(project, "bracket.bending_stress"), set(),
                         "a refused gate records no verdict")
        with open(os.path.join(project, ".atompipe", "cache", "last_check.json"),
                  encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["statuses"]["C2"], "unclaimed")

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
                self.assertIn("unqualified: known-bad:pass", got["error"])
                newest = [read_control(project, "bracket.deflection", name)
                          for name in control_names(project)["bracket.deflection"]]
                refusals = [e for e in newest if e["bad"] == "pass"]
                self.assertEqual(len(refusals), 1, newest)
                self.assertEqual((refusals[0]["host"], refusals[0]["admitted"]),
                                 ("known-good", "no"), refusals[0])
                status = status_json(self, project)
                self.assertEqual(status["claims"]["C1"], "unclaimed", "a refused gate still blocks")
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
        write_shelf_gate(project, SHELF_GATE)
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
        self.assertTrue(got["error"].startswith("unqualified: "), got)
        self.assertEqual(code, 1, "an identity fixture admitted the live design")
        self.assertEqual(blocking_ids(data).get("C1"), "unclaimed", data["blocking"])
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
        write_shelf_gate(project, SHELF_GATE_COSTLY_LOGGER)
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
        self.assertIn("unqualified: known-bad:pass", got["error"])
        self.assertEqual(data["counts"]["controls"]["executed"], 1,
                         "the tier-2 path's control never ran: the tier-0 one was served")
        self.assertEqual(code, 1, "a tier-2 sweep admitted a path that passes 400 mm")
        self.assertEqual(blocking_ids(data).get("C1"), "unclaimed", data["blocking"])
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

    def test_cli_a_control_undemonstrated_on_the_costlier_path_reads_stale_to_check_too(self):
        """V: the review's repro (``repro_undemonstrated``). A tier-0 check serves
        the tier-2 entry of a gate that read ``ctx.tier`` (``_most_thorough``),
        and judges that entry's control by the records alone — the tier-2 path is
        above its ceiling. Once any edit under ``selftest/`` moved the control's
        static part, nothing current demonstrates that path. ``resolve`` read it
        as it should, the cached PASS stale; the sweep turned the same judgement
        into a SKIPPED row, and ``_swept`` dropped the gate from the stale set.
        With a second gate passing on C1 the skip read PASS (partial): ``check
        --junit`` exited 0 with C1 ``skipped partial`` and ``last_check.json``
        saying pass, while ``status`` said NOT READY, C1 STALE — and neither a
        second ``check`` nor ``check --force`` changed either answer. Only
        ``check --tier 2`` can settle it, so that is what the reason says."""
        project = os.path.join(self.tmp(), "shelf")
        write(project, "model/shelf.py", SHELF_MODEL.format(span="80.0"))
        write_shelf_gate(project, SHELF_GATES_TIERED)
        write(project, "claims/C1.json", json.dumps(SHELF_CLAIM) + "\n")
        write(project, "selftest/known_good.py", SHELF_KNOWN_GOOD)
        write(project, "selftest/bad.py", SHELF_LONG)
        proc = cli(project, "init", "--model", "model/shelf.py", "--name", "shelf")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        junit = os.path.join(project, ".atompipe", "out", "junit.xml")
        last_check = os.path.join(project, ".atompipe", "cache", "last_check.json")
        # D18's words (R-6): "not yet qualified at this version — atompipe check
        # --tier 2 qualifies it", where P2.2 said "... — run atompipe check --tier 2".
        advice = "atompipe check --tier 2 qualifies it"

        # The positive controls: tier 2 proves C1, and a plain tier-0 check
        # serves that tier-2 entry as current — admitted by the records alone.
        code, data = check_json(self, project, "--tier", "2")
        self.assertEqual((code, verdict_row(data, "shelf.span")["outcome"]), (0, "pass"), data)
        [name] = entry_names(project, "shelf.span")
        with open(os.path.join(project, ".atompipe", "verdicts", "shelf.span", name),
                  encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["reads"].get("tier"), 2,
                             "the precondition: the entry took the tier-2 path")
        code, data = check_json(self, project)
        got = verdict_row(data, "shelf.span")
        self.assertEqual((code, got["outcome"], got["cached"], got["fresh"]),
                         (0, "pass", True, True), got)
        self.assertEqual(status_json(self, project)["claims"]["C1"], "pass")
        self.assertIn("**C1**", proven_section(self, project))

        # Any edit under selftest/ moves every control's static part.
        write(project, "selftest/README.txt", "note\n")
        shown = status_json(self, project)
        self.assertEqual(shown["claims"]["C1"], "stale", "the precondition: status reads it")
        self.assertEqual(shown["freshness"]["shelf.span"]["admission"], "undemonstrated")
        [why] = shown["freshness"]["shelf.span"]["reasons"]
        self.assertTrue(why.endswith(advice),
                        f"status sends the reader to a check that cannot settle it: {why!r}")

        def agrees(what: str) -> None:
            """After ``what``, every reader still says C1 STALE — ``status``, the
            ``last_check.json`` that check wrote, and the report's PROVEN section."""
            status = status_json(self, project)
            self.assertEqual(status["claims"]["C1"], "stale", f"{what}: {status['claims']}")
            with open(last_check, encoding="utf-8") as fh:
                last = json.load(fh)
            self.assertEqual(last["statuses"]["C1"], "stale",
                             f"{what}: last_check.json disagrees with status: "
                             f"{last['statuses']}")
            self.assertNotIn("**C1**", proven_section(self, project), what)

        # The review's invocation, CI's: a plain check with --junit.
        proc = cli(project, "check", "--junit")
        self.assertEqual(proc.returncode, 1,
                         f"check exits ready while status says NOT READY:\n{proc.stdout}")
        self.assertIn(advice, proc.stdout, proc.stdout)
        root = ET.parse(junit).getroot()
        exit_codes = [p.get("value") for p in root.iter("property")
                      if p.get("name") == "exit_code"]
        self.assertEqual(exit_codes, ["1"], exit_codes)
        [c1] = [case for case in root.iter("testcase")
                if case.get("classname") == "claims.critical" and case.get("name") == "C1"]
        self.assertIsNotNone(c1.find("failure"),
                             f"C1 is not red in the JUnit: {ET.tostring(c1, 'unicode')}")
        agrees("check --junit")

        for argv in ((), ("--force",)):
            what = " ".join(("check",) + argv)
            code, data = check_json(self, project, *argv)
            got = verdict_row(data, "shelf.span")
            self.assertEqual((code, data["ready"]), (1, False), f"{what}: {data['blocking']}")
            self.assertEqual(blocking_ids(data).get("C1"), "stale", f"{what}: {data['blocking']}")
            self.assertEqual((got["outcome"], got["fresh"]), ("pass", False),
                             f"{what}: the row is not the cached PASS, not current: {got}")
            self.assertTrue(got.get("stale_reason", "").endswith(advice), f"{what}: {got}")
            self.assertTrue(got.get("rho", "").startswith(name[:16]),
                            f"{what}: the row is not the tier-2 entry's ({name}): {got}")
            if argv:
                # --force did run the cheap path, and says why its PASS is not the row
                self.assertFalse(got["cached"], f"{what} served instead of running: {got}")
                self.assertTrue(any(note.startswith("shelf.span: ran at tier 0 (PASS)")
                                    and note.endswith("(PASS, not current)")
                                    for note in data["notes"]), f"{what}: {data['notes']}")
            agrees(what)

        # And the one command that does settle it.
        code, data = check_json(self, project, "--tier", "2")
        self.assertEqual((code, verdict_row(data, "shelf.span")["outcome"]), (0, "pass"), data)
        self.assertEqual(status_json(self, project)["claims"]["C1"], "pass")
        self.assertIn("**C1**", proven_section(self, project))
        code, data = check_json(self, project)
        self.assertEqual(code, 0, "the settled tier-2 path is served to a plain check again")

    def _shelf_from_claim(self, fixture: str, known_good: str | None = None) -> str:
        """The repro A project: span 80 live, C1 at 100 mm, ``shelf.span``
        taking its limit from C1, ``fixture`` as ``selftest/bad.py`` and — with
        no ``known_good`` — no known-good design, so a live host. Its first
        check is the precondition: exit 0, the control an entry that read
        ``claim:C1`` on the host it should have, and C1 under PROVEN."""
        project = os.path.join(self.tmp(), "shelf")
        write(project, "model/shelf.py", SHELF_MODEL.format(span="80.0"))
        write_shelf_gate(project, SHELF_GATE_FROM_CLAIM)
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
        self.assertTrue(got["error"].startswith("unqualified: "), got)
        self.assertIn("known-bad:pass", got["error"])
        self.assertEqual(code, 1, "a control defused by a claim edit admitted its gate")
        self.assertEqual(blocking_ids(data).get("C1"), "unclaimed", data["blocking"])
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
        written, and the readers agree once the check has run.

        R-6 (review of P2.3): the VERDICT on the edited claim does not count —
        it read C1 at 500 mm, which no qualification run read (the known-good
        design states C1 at 100; `channels:ledger`) — while the control is
        re-qualified by its fixture alone, exactly as before: the evaluator is
        qualified, the verdict uncounted."""
        project = self._shelf_from_claim(SHELF_LONG_OWN_LEDGER)
        before = control_names(project)
        self.assertEqual(self._last_selftest(project)["admission"], "admitted",
                         "the run that filed the control vouches for the live ledger")
        _code, data = check_json(self, project)
        self.assertEqual(data["counts"]["controls"],
                         {"executed": 0, "cached": 1, "reverified": 0}, data["counts"])

        edit(project, "claims/C1.json", '"limit": 100.0', '"limit": 500.0')
        code, data = check_json(self, project)
        self.assertEqual(data["counts"]["controls"],
                         {"executed": 0, "cached": 0, "reverified": 1}, data["counts"])
        self.assertEqual(verdict_row(data, "shelf.span").get("unqualified"),
                         "channels:ledger|claim:C1", data)
        self.assertEqual(code, 1, data)
        self.assertEqual(control_names(project), before, "a new control entry")
        self.assertEqual(self._last_selftest(project)["admission"], "admitted")
        self.assertNotIn("**C1**", proven_section(self, project))
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
        self.assertIn("known-bad:pass", got["error"])
        self.assertEqual(code, 1)
        self.assertNotIn("C1", proven_section(self, project))

    # -- round 2, r3: re-verification against the reads the fixture makes now -- #
    def _shelf_live(self, fixture: str, *, span: str = "80.0", gate: str = SHELF_GATE,
                    live_passes: bool = True) -> str:
        """The review's ``r3`` project: ``shelf.span`` holding the span to 100
        mm, ``fixture`` as ``fixtures/bad.py`` — outside ``selftest/``, so an
        edit to it moves the fixture's closure (the hint) and no byte of the
        control's static part, which is what sends ``check`` to re-verify — and
        no known-good design: a LIVE host. Its first check is the
        precondition: one control run, filed as fired on the live host, and
        the live design's verdict as ``span`` has it (``live_passes``: exit 0
        and C1 under PROVEN)."""
        project = self._shelf_live_project(fixture, span=span, gate=gate)
        code, data = check_json(self, project)
        self.assertEqual(code, 0 if live_passes else 1, data)
        self.assertEqual(data["counts"]["controls"],
                         {"executed": 1, "cached": 0, "reverified": 0}, data["counts"])
        [name] = control_names(project)["shelf.span"]
        entry = read_control(project, "shelf.span", name)
        self.assertEqual((entry["host"], entry["bad"]), ("live", "fail"), entry)
        if live_passes:
            self.assertIn("**C1**", proven_section(self, project))
        return project

    def _shelf_live_project(self, fixture: str, *, span: str = "80.0",
                            gate: str = SHELF_GATE) -> str:
        """``_shelf_live``'s project, made and initialised, never checked."""
        project = os.path.join(self.tmp(), "shelf")
        write(project, "model/shelf.py", SHELF_MODEL.format(span=span))
        write_shelf_gate(project, gate.replace(SHELF_FIXTURE_DECL, SHELF_OUTSIDE_DECL))
        write(project, "claims/C1.json", json.dumps(SHELF_CLAIM) + "\n")
        write(project, SHELF_OUTSIDE_FIXTURE, fixture)
        proc = cli(project, "init", "--model", "model/shelf.py", "--name", "shelf")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return project

    def _run_not_vouched(self, project: str, *, code: int = 0) -> dict:
        """``check`` after the fixture was edited: the fixture now takes
        something from the live host that the entry it matches in value never
        keyed, so equal values vouch for nothing — the control runs, fixture
        and gate, and files an entry that keys what it read. Returns it."""
        before = control_names(project)["shelf.span"]
        got, data = check_json(self, project)
        self.assertEqual(data["counts"]["controls"],
                         {"executed": 1, "cached": 0, "reverified": 0},
                         f"a fixture reading the live host where its entry keyed nothing "
                         f"was vouched for by equal values: {data['counts']}")
        self.assertEqual(got, code, data)
        self.assertEqual(verdict_row(data, "shelf.span")["outcome"],
                         "pass" if code == 0 else "fail", data)
        self.assertEqual(self._last_selftest(project)["admission"], "admitted")
        [new] = control_names(project)["shelf.span"] - before
        entry = read_control(project, "shelf.span", new)
        self.assertEqual((entry["host"], entry["bad"]), ("live", "fail"), entry)
        return entry

    def _defused(self, project: str,
                 why: str = "known-bad:pass") -> None:
        """The fixture now builds an input the gate accepts: the control
        PASSED its own known-bad input, and neither ``check`` nor a reader
        may admit the gate. ``why`` is the refusal ``check`` names."""
        code, data = check_json(self, project)
        got = verdict_row(data, "shelf.span")
        self.assertEqual(got["outcome"], "error", got)
        self.assertIn(f"unqualified: {why}", got["error"])
        self.assertEqual(code, 1, "a control the live design defused admitted its gate")
        self.assertEqual(blocking_ids(data).get("C1"), "unclaimed", data["blocking"])
        self.assertEqual(self._last_selftest(project)["admission"], "not-admitted")
        self.assertNotEqual(status_json(self, project)["claims"]["C1"], "pass")
        self.assertNotIn("C1", proven_section(self, project))
        proc = cli(project, "gate", "selftest", "--no-record")
        self.assertEqual(proc.returncode, 1, "the precondition: the control is defused\n"
                         + proc.stdout + proc.stderr)

    def test_cli_a_fixture_deriving_its_known_bad_from_live_params_is_rekeyed(self):
        """V: the admission review's round-2 repro ``r3``. A sealed fixture's
        entry keyed no host read. Edited to five times the LIVE span — still
        400 mm — the fixture alone re-ran and ``_values_match`` compared the
        files, listings, tier and the values the gate would be handed, never
        what the fixture had read to build them: ``controls reverified 1``. The
        span then moved 80 -> 15, the fixture built 75 mm, which the gate
        accepts — and ``check`` exited 0 with C1 PROVEN on the entry the
        re-verification had vouched for, while ``gate selftest`` said PASSED
        its own known-bad."""
        project = self._shelf_live(SHELF_SEALED_LONG)
        write(project, SHELF_OUTSIDE_FIXTURE, SHELF_FIVEFOLD_LIVE)
        entry = self._run_not_vouched(project)
        self.assertIn([["span"], verdicts.digest_value(80.0)], entry["reads"]["host"], entry)

        # The positive half: a fixture edit that reads exactly what its entry
        # keyed is still vouched for by the fixture alone — nothing executes.
        edit(project, SHELF_OUTSIDE_FIXTURE, "* 5}", "* 5.0}")
        code, data = check_json(self, project)
        self.assertEqual(code, 0, data)
        self.assertEqual(data["counts"]["controls"],
                         {"executed": 0, "cached": 0, "reverified": 1}, data["counts"])

        edit(project, "model/shelf.py", "span: float = 80.0", "span: float = 15.0")
        self._defused(project)

    def test_cli_the_literal_identity_fixture_on_a_live_host_is_rekeyed(self):
        """V: the review's ``r3i``. The literal identity fixture reads nothing:
        the host view it hands back is what the GATE reads, and a re-run of the
        fixture alone calls no gate — ``_param_at`` walks the view's storage
        and records nothing. With the live span at 400 its values equalled the
        sealed entry's, the entry was vouched for, and at span 15 C1 read
        PROVEN on the identity fixture."""
        project = self._shelf_live(SHELF_SEALED_LONG, span="400.0", live_passes=False)
        write(project, SHELF_OUTSIDE_FIXTURE, SHELF_IDENTITY)
        entry = self._run_not_vouched(project, code=1)
        self.assertIn([["span"], verdicts.digest_value(400.0)], entry["reads"]["host"], entry)
        edit(project, "model/shelf.py", "span: float = 400.0", "span: float = 15.0")
        self._defused(project)

    def test_cli_a_fixture_deriving_its_known_bad_from_the_live_ledger_is_rekeyed(self):
        """V: the review's ``r3l``: the same hole through ``ctx.ledger``. The
        fixture's own read of the live C1 went unkeyed — ``_ledger_moved``
        watches only the keys the entry recorded — so C1 relaxed 100 -> 25
        left the control admitted while the fixture built 100 mm, which the
        gate's own 100 mm limit accepts."""
        project = self._shelf_live(SHELF_SEALED_LONG)
        write(project, SHELF_OUTSIDE_FIXTURE, SHELF_FOURFOLD_CLAIM)
        entry = self._run_not_vouched(project)
        self.assertIn("claim:C1", entry["reads"]["ledger"], entry)
        edit(project, "claims/C1.json", '"limit": 100.0', '"limit": 25.0')
        self._defused(project)

    def test_cli_a_fixture_that_swaps_the_model_or_the_memo_is_not_vouched_for(self):
        """V: the rest of ``r3``'s fix. A control entry keys neither whether
        its gate was handed a model (``ctx.model is None`` uses nothing) nor
        what the memo holds, so a fixture edited to hand its gate a model or a
        memo it was not handed — every keyed value equal — was re-verified
        (exit 0, ``reverified 1``), and the gate that passes on either stayed
        admitted. The control now runs; the entry it files cannot tell the two
        fixtures apart either — the same reads, so the same rho_control — and
        the refusal names the two outcomes at identical inputs.

        The memo half moved with ``SweepMemo`` (review, ``ffr3/p2``): a gate
        can no longer read what a memo holds, so the door a gate can still
        branch on is ``ctx.memo is None`` — swapped for ``None`` it defuses
        exactly as the model does. A memo of the fixture's own holding what the
        gate once trusted still vouches for nothing (the control runs), and
        now defuses nothing; filling the memo the fixture was handed is
        refused outright, as an unusable control.

        R-6 (review of P2.3): the model door is closed where it opened — no run
        of a project evaluator is handed a model (``verdicts._no_model``), so a
        gate that trusts ``ctx.model is None`` trusts its known-bad control too
        and is unqualified at its first check, before any fixture edit; the
        memo door is shown by a gate that trusts the memo alone."""
        with self.subTest(field="model"):
            project = self._shelf_live_project(SHELF_SEALED_LONG,
                                               gate=SHELF_GATE_TRUSTS_ITS_CONTEXT)
            code, data = check_json(self, project)
            self.assertEqual(verdict_row(data, "shelf.span").get("unqualified"),
                             "known-bad:pass", data)
            self.assertEqual(code, 1)
            write(project, SHELF_OUTSIDE_FIXTURE, SHELF_SEALED_NO_MODEL)
            self._defused(project, "known-bad:pass")
        refusals = {
            "memo": (SHELF_SEALED_NO_MEMO, "control:two-outcomes|"),
            "memo, filled in place": (SHELF_SEALED_FILLS_MEMO,
                                      "known-bad:errored|known-bad control unusable"),
        }
        for field, (fixture, why) in refusals.items():
            with self.subTest(field=field):
                project = self._shelf_live(SHELF_SEALED_LONG,
                                           gate=SHELF_GATE_TRUSTS_ITS_MEMO)
                write(project, SHELF_OUTSIDE_FIXTURE, fixture)
                self._defused(project, why)
                if fixture is SHELF_SEALED_FILLS_MEMO:
                    # What made it unusable: the fill, refused — not some other
                    # break in the fixture. The line names the class of failure;
                    # the remembered verdict keeps the refusal itself.
                    held = [record["verdict"] for record in
                            verdicts.remembered(project).get("control:shelf.span", {}).values()]
                    self.assertTrue(any(v.detail.startswith("ctx.memo is the sweep's file memo")
                                        and "ctx.load_file" in v.detail for v in held),
                                    [f"{v.error} | {v.detail}" for v in held])
                # And back: the sealed fixture's own entry is the one its
                # unmoved closure names, and it decides alone again — once the
                # control has shown it fires, where an unusable run is
                # remembered at this static part and outranks the entry: the
                # first check back runs it, the next serves it.
                write(project, SHELF_OUTSIDE_FIXTURE, SHELF_SEALED_LONG)
                if fixture is SHELF_SEALED_FILLS_MEMO:
                    code, data = check_json(self, project)
                    self.assertEqual(code, 0, data)
                    self.assertEqual(data["counts"]["controls"],
                                     {"executed": 1, "cached": 0, "reverified": 0},
                                     data["counts"])
                code, data = check_json(self, project)
                self.assertEqual(code, 0, data)
                self.assertEqual(data["counts"]["controls"],
                                 {"executed": 0, "cached": 1, "reverified": 0}, data["counts"])
                self.assertIn("**C1**", proven_section(self, project))

        with self.subTest(field="a memo of its own"):
            project = self._shelf_live(SHELF_SEALED_LONG, gate=SHELF_GATE_TRUSTS_ITS_MEMO)
            write(project, SHELF_OUTSIDE_FIXTURE, SHELF_SEALED_OWN_MEMO)
            code, data = check_json(self, project)
            self.assertEqual(data["counts"]["controls"],
                             {"executed": 1, "cached": 0, "reverified": 0},
                             f"a fixture handing its gate a memo it was not handed was "
                             f"vouched for by equal values: {data['counts']}")
            self.assertEqual(code, 0, data)
            self.assertEqual(self._last_selftest(project)["admission"], "admitted",
                             "the gate saw what the fixture's own memo holds")
            self.assertIn("**C1**", proven_section(self, project))

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
                        # R-6 (review of P2.3): a copy under `.atompipe/packs/` is
                        # walked, and a gate of it that reads the live ledger in
                        # `check` (openmodelica's claims_addressable) never
                        # qualifies there — its baseline hands no ledger — before
                        # the edit and after it alike: the edit changes no answer.
                        was = verdict_row(second, spec.id)
                        self.assertEqual((got["outcome"], got.get("unqualified", "")),
                                         (was["outcome"], was.get("unqualified", "")),
                                         f"{spec.id}: the edit changed an answer: {got}")
                        self.assertTrue(got["outcome"] == "pass" or got.get(
                            "unqualified", "").startswith("channels:ledger|"), got)
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
        write_shelf_gate(project, SHELF_GATE.replace(SHELF_FIXTURE_DECL, f'fixture="{ref}"'))
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
        self.assertIn("unqualified: known-bad:pass", got["error"])
        self.assertEqual(code, 1, "a fixture edited into a no-op admitted its gate")
        self.assertEqual(blocking_ids(data).get("C1"), "unclaimed", data["blocking"])
        self.assertNotEqual(status_json(self, project)["claims"]["C1"], "pass")
        self.assertNotIn("**C1**", proven_section(self, project))
        proc = cli(project, "gate", "selftest", "--no-record")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("known-bad pass", proc.stdout)

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
        write_shelf_gate(project, SHELF_GATE_LIMIT_FROM_DATA)
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
        write_shelf_gate(project, SHELF_GATE)
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
        write_shelf_gate(project, SHELF_GATES_LOAD_AT_RUN_TIME)
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


# --------------------------------------------------------------------------- #
# P2.3: qualification — the known-good half, channel parity, the mutation pass
# --------------------------------------------------------------------------- #
#: The synthetic model with two knobs the qualification scenarios edit: `GAIN`
#: scales the deflection of a section thicker than 5 mm only (the known-good 8.0
#: and the live 9.0 move; the known-bad 2.0 and 0.5 do not), and `OVERRIDE` is a
#: value only a mutated run of `q.override` reads (V1i, V1j).
QMODEL = MODEL.replace('K = 1.0\n', 'K = 1.0\nGAIN = 1.0\nOVERRIDE = False\n').replace(
    '    return {{"deflection": round(K * c.load_n * c.span / c.thickness ** 3, 6),\n',
    '    gain = GAIN if c.thickness > 5 else 1.0\n'
    '    return {{"deflection": round(gain * K * c.load_n * c.span / c.thickness ** 3, 6),\n'
    '             "override": OVERRIDE,\n')
assert QMODEL.count("GAIN") == 2 and "override" in QMODEL, "the q model replaced nothing"

#: Two more known-bad fixtures beside `thin` and `huge`: a section so thin the
#: deflection is 7200 mm (`wafer`, for the x120-margin gate), and one that hands
#: its gate the known-bad input through `ctx.extra` (`via_extra`, V1k).
QFIXTURES = FIXTURES + '''

def wafer(ctx):
    return _with(ctx, thickness=0.5)


def via_extra(ctx):
    return {"probe": True}
'''

#: The planted evaluators, each beside the honest twin it differs from by one
#: idea. Live design 9.0 mm (1.235 mm, passes), known-good 8.0 (1.758), known-bad
#: `thin` 2.0 (112.5), limit 2.0 — so a run on the live design, the known-good
#: and the known-bad control are told apart by the value a gate was called on.
QGATES = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict

LIMIT = 2.0
CALLS = []
FLAKY = {"left": 1}


def _d(ctx):
    return float(ctx.params["deflection"])


def _v(gid, passed, d, limit=LIMIT):
    CALLS.append((gid, d))
    return Verdict(gate=gid, passed=passed, measured=round(d, 4), limit=limit, units="mm",
                   detail=f"{d:.3f} mm (limit {limit} mm)")


def _nc(fixture="thin", **kw):
    return NegativeControl(fixture=f"selftest/bad.py:{fixture}", **kw)


@gate(id="q.honest", claims=["q-honest"], negative_control=_nc())
def honest(ctx):
    d = _d(ctx)
    return _v("q.honest", d <= LIMIT, d)


@gate(id="q.never", claims=["q-never"], negative_control=_nc())
def never(ctx):
    return _v("q.never", False, _d(ctx))


@gate(id="q.raises", claims=["q-raises"], negative_control=_nc(expect="error"))
def raises(ctx):
    _d(ctx)
    raise ValueError("refuses everything")


@gate(id="q.parser", claims=["q-parser"], negative_control=_nc(expect="error"))
def parser(ctx):
    if float(ctx.params["thickness"]) < 3.0:
        raise ValueError("a section this thin is outside the model")
    d = _d(ctx)
    return _v("q.parser", d <= LIMIT, d)


@gate(id="q.goodcrash", claims=["q-goodcrash"], negative_control=_nc())
def goodcrash(ctx):
    d = _d(ctx)
    if 1.5 < d < 1.8:
        raise ZeroDivisionError("planted on the known-good design")
    return _v("q.goodcrash", d <= LIMIT, d)


@gate(id="q.goodskip", claims=["q-goodskip"], negative_control=_nc())
def goodskip(ctx):
    d = _d(ctx)
    if 1.5 < d < 1.8:
        return Verdict(gate="q.goodskip", skipped=True,
                       skip_reason="planted: skips the known-good design")
    return _v("q.goodskip", d <= LIMIT, d)


@gate(id="q.keyed", claims=["q-keyed"], negative_control=_nc())
def keyed(ctx):
    """Keyed to its own control: judges the thickness the fixture changes,
    reports the honest deflection."""
    d = _d(ctx)
    return _v("q.keyed", float(ctx.params["thickness"]) > 3.0, d)


@gate(id="q.nolimit", claims=["q-nolimit"], negative_control=_nc())
def nolimit(ctx):
    """`q.keyed`, reporting no value against a limit."""
    _d(ctx)
    return Verdict(gate="q.nolimit", passed=float(ctx.params["thickness"]) > 3.0)


@gate(id="q.drift", claims=["q-drift"], negative_control=_nc())
def drift(ctx):
    d = _d(ctx)
    return _v("q.drift", d <= 2.4, d)


@gate(id="q.hidden", claims=["q-hidden"], negative_control=_nc())
def hidden(ctx):
    d = _d(ctx)
    return _v("q.hidden", d <= 10.0, d)


@gate(id="q.overfit", claims=["q-overfit"], negative_control=_nc())
def overfit(ctx):
    d = _d(ctx)
    return _v("q.overfit", d < 100.0, d)


@gate(id="q.wide", claims=["q-wide"], negative_control=_nc("wafer"))
def wide(ctx):
    """A x120 margin on the known-good value; the limit it applies is 3x the
    one it reports."""
    d = _d(ctx)
    limit = 210.96
    return _v("q.wide", d <= 3 * limit, d, limit)


@gate(id="q.raisespast", claims=["q-raisespast"], negative_control=_nc(expect="error"))
def raisespast(ctx):
    d = _d(ctx)
    if d > LIMIT:
        raise ValueError("past the limit: refused, not measured")
    return _v("q.raisespast", True, d)


@gate(id="q.override", claims=["q-override"], negative_control=_nc())
def override(ctx):
    """Passes `d <= L`, or `d` within ten limits with `override` set — read only
    there, so neither the known-good nor the known-bad run ever reads it."""
    d = _d(ctx)
    passed = d <= LIMIT or (d < 10 * LIMIT and bool(ctx.params["override"]))
    return _v("q.override", passed, d)


@gate(id="q.flaky", claims=["q-flaky"], negative_control=_nc())
def flaky(ctx):
    """Honest, but its first run past the limit raises, once."""
    d = _d(ctx)
    if d > LIMIT and FLAKY["left"] and d < 50:
        FLAKY["left"] -= 1
        raise MemoryError("a transient failure, once")
    return _v("q.flaky", d <= LIMIT, d)


@gate(id="q.extra", claims=["q-extra"], negative_control=_nc("via_extra"))
def extra(ctx):
    """D-26's hole: fails exactly when `ctx.extra` is not empty."""
    _d(ctx)
    return Verdict(gate="q.extra", passed=not ctx.extra)


@gate(id="q.tiered", claims=["q-tiered"], negative_control=_nc())
def tiered(ctx):
    """Honest on the costlier path, a drifted limit on the cheap one."""
    d = _d(ctx)
    return _v("q.tiered", d <= (LIMIT if ctx.tier >= 1 else 2.4), d)


@gate(id="q.slow1", claims=["q-slow1"], tier=Tier.BUILD, negative_control=_nc())
def slow1(ctx):
    d = _d(ctx)
    return _v("q.slow1", d <= LIMIT, d)
'''

#: One claim per planted gate, `Q-<name>`, tagged with its vocabulary.
QTAGS = {f"Q-{name}": f"q-{name}" for name in (
    "honest", "never", "raises", "parser", "goodcrash", "goodskip", "keyed", "nolimit",
    "drift", "hidden", "overfit", "wide", "raisespast", "override", "flaky", "extra",
    "tiered", "slow1")}

#: The live deflection of the q project (9.0 mm), so a test can say a gate was
#: never run on the live design.
Q_LIVE = round(15.0 * 60.0 / 9.0 ** 3, 6)


def plant_qualification_project(root: str, *, thickness: float = 9.0,
                                 known_good: str = KNOWN_GOOD,
                                 fixtures: str = QFIXTURES) -> str:
    write(root, "model/m.py", QMODEL.format(thickness=repr(float(thickness))))
    write(root, "gates/q.py", QGATES)
    write(root, "selftest/known_good.py", known_good)
    write(root, "selftest/bad.py", fixtures)
    return root


class QProject(Project):
    """The qualification scenarios' project: the synthetic model, its known-good
    design, and the planted evaluators of `QGATES` — in this process."""

    def __init__(self, case: _env.EnvCase, *, thickness: float = 9.0,
                 known_good: str = KNOWN_GOOD, fixtures: str = QFIXTURES,
                 extra: dict | None = None) -> None:
        self.root = plant_qualification_project(os.path.join(case.tmp(), "q"),
                                                thickness=thickness, known_good=known_good,
                                                fixtures=fixtures)
        for rel, text in (extra or {}).items():
            write(self.root, rel, text)
        self.registry = gates.Registry()
        gates.load_project_gates(self.root, self.registry)
        self.ledger = ledger_of(QTAGS)
        self.projection = modelio.project(modelio.load_model(self.root, "model/m.py"))

    @property
    def gate_module(self):
        _spec, fn = self.registry.get("q.honest")
        return sys.modules[fn.__module__]

    def spec(self, gate_id: str):
        return self.registry.get(gate_id)

    def tokens(self) -> dict[str, str]:
        return {v.gate: v.unqualified for v in self.resolve().verdicts if v.unqualified}


def entries_of(root: str, gate_id: str) -> list[dict]:
    """Every control entry of ``gate_id`` on disk, as JSON."""
    out = []
    for path in sorted(glob.glob(os.path.join(root, ".atompipe", "verdicts", gate_id,
                                              "control-*.json"))):
        with open(path, encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def line_of(result: verdicts.SweepResult, gate_id: str) -> str:
    """``gate_id``'s qualification line, as `check` prints it, from its row."""
    got = row(result, gate_id)
    return report_mod.qualification_line(gate_id, got.admission.qualification)


def _bad_alone(real):
    """A planted judge that decides on the known-bad half alone (P2.1's rule)."""
    def judge(facts):
        if facts.known_bad == "fail":
            return ""
        return real(facts)
    return judge


def _ignoring_good(outcome: str, real):
    """A planted judge that reads a known-good `outcome` as a pass."""
    def judge(facts):
        if facts.known_good == outcome:
            facts = dataclasses.replace(facts, known_good="pass")
        return real(facts)
    return judge


#: Planted into a `Driven` process (`--plant`): re-qualifying by the known-bad
#: half's values alone (P2.1's early cutoff), and a walk whose reads go nowhere.
PLANT_BAD_HALF_ONLY = "verdicts._good_values_match = lambda *a, **k: True\n"
PLANT_THROWAWAY_WALK_TRACE = "gates._fold_reads = lambda *a, **k: None\n"


class QualificationIsPaired(_env.EnvCase):
    """(9) An evaluator's verdict counts only once it is qualified at its version:
    its known-good control passes AND its known-bad control fails, both reaching
    it through the same channel — and, for a project evaluator, every conclusive
    mutation fails (`EveryConclusiveMutationMustFail`). Until then its claim is a
    Gap, beside a pass too. What slipped through before P2.3: every project gate
    counted on its known-bad half alone (P2.1-D7), so a gate that failed
    everything (S-04) — which fails its known-bad control by construction — read
    Failing, a fail of the DESIGN, and `gate selftest` exited 0 over it.

    Every test runs on the real code, then with its planted violator patched in,
    and shows the violation visible."""

    # -- a ---------------------------------------------------------------- #
    def test_an_always_false_gate_is_unqualified_and_its_claim_a_gap(self):
        p = QProject(self)
        result = p.sweep(only=["q.never", "q.honest"])
        never = row(result, "q.never")
        self.assertEqual(never.admission.state, "not-admitted", never.admission)
        self.assertEqual(never.verdict.unqualified, "known-good:fail")
        self.assertFalse(never.executed)
        self.assertNotIn(("q.never", Q_LIVE), p.gate_module.CALLS,
                         "an unqualified evaluator is never run on the live design")
        self.assertEqual(line_of(result, "q.never"),
                         "q.never : known-good fail · known-bad fail → unqualified")
        statuses = p.statuses()
        self.assertEqual(statuses["Q-never"], ClaimStatus.UNCLAIMED, statuses)
        honest = row(result, "q.honest")
        self.assertEqual(honest.verdict.unqualified, "")
        self.assertEqual(line_of(result, "q.honest"),
                         "q.honest : known-good pass · known-bad fail · mutation 1/1 fail "
                         "→ qualified")
        self.assertEqual(statuses["Q-honest"], ClaimStatus.PASS, "the positive twin")
        (entry,) = entries_of(p.root, "q.never")
        self.assertEqual((entry["bad"], entry["good"]["outcome"], entry["admitted"]),
                         ("fail", "fail", "no"))
        planted = QProject(self)
        with mock.patch.object(verdicts, "_qualification",
                               _bad_alone(verdicts._qualification)):
            planted.sweep(only=["q.never"])
            self.assertEqual(planted.statuses()["Q-never"], ClaimStatus.FAIL,
                             "the planted judge reads S-04 as a fail of the design")

    # -- b ---------------------------------------------------------------- #
    def test_an_always_raising_gate_declared_expect_error_is_unqualified(self):
        p = QProject(self)
        result = p.sweep(only=["q.raises", "q.parser"])
        raises = row(result, "q.raises")
        self.assertEqual(raises.verdict.unqualified,
                         "known-good:errored|ValueError: refuses everything")
        self.assertEqual(p.statuses()["Q-raises"], ClaimStatus.UNCLAIMED)
        parser = row(result, "q.parser")
        self.assertEqual(parser.verdict.unqualified, "", parser.admission)
        line = line_of(result, "q.parser")
        self.assertTrue(line.startswith("q.parser : known-good pass · known-bad fail (raised, "
                                        "as declared) · "), line)
        self.assertTrue(line.endswith("→ qualified"), line)
        planted = QProject(self)
        with mock.patch.object(verdicts, "_qualification",
                               _ignoring_good("errored", verdicts._qualification)):
            got = row(planted.sweep(only=["q.raises"]), "q.raises")
        self.assertEqual(got.verdict.unqualified, "",
                         "the planted judge ignores the known-good half's crash")

    # -- c ---------------------------------------------------------------- #
    def test_a_known_good_crash_is_remembered_never_cached(self):
        p = QProject(self)
        result = p.sweep(only=["q.goodcrash"])
        got = row(result, "q.goodcrash")
        self.assertEqual(got.verdict.unqualified,
                         "known-good:errored|ZeroDivisionError: planted on the known-good "
                         "design")
        self.assertEqual(entries_of(p.root, "q.goodcrash"), [], "a crash is never cached")
        self.assertIn("control:q.goodcrash", verdicts.remembered(p.root))
        self.assertEqual(p.tokens().get("q.goodcrash"), got.verdict.unqualified)
        self.assertEqual(p.statuses()["Q-goodcrash"], ClaimStatus.UNCLAIMED)
        planted = QProject(self)
        with mock.patch.object(verdicts, "remember", lambda *a, **k: None):
            planted.sweep(only=["q.goodcrash"])
        self.assertNotEqual(planted.tokens().get("q.goodcrash"), got.verdict.unqualified,
                            "with nothing remembered no reader can say the crash")

    def test_a_known_bad_pass_beside_a_crashed_known_good_half_is_remembered(self):
        extra = {"gates/logger.py": LOGGER_BROKEN_GOOD_GATE,
                 "selftest/goodx.py": "def broken(ctx):\n    raise RuntimeError('no design')\n"}
        for plant in (False, True):
            with self.subTest(planted=plant):
                p = QProject(self, extra=extra)
                p.ledger = ledger_of({**QTAGS, "Q-logger": "q-logger"})
                with (mock.patch.object(verdicts, "_hold", _holds_held_kinds_only(verdicts._hold))
                      if plant else contextlib.nullcontext()):
                    result = p.sweep(only=["q.logger"])
                got = row(result, "q.logger")
                self.assertEqual(got.verdict.unqualified, "known-bad:pass")
                self.assertEqual(line_of(result, "q.logger"),
                                 "q.logger : known-good errored · known-bad pass → unqualified")
                spec, fn = p.spec("q.logger")
                read = verdicts.admission_state(p.root, spec, fn, projection=p.projection,
                                                ledger=p.ledger)
                if plant:
                    self.assertTrue(verdicts.not_yet(verdicts.Verdict(
                        gate="q.logger", unqualified=read.reason)),
                        "unremembered, every reader promises a qualification never coming")
                    continue
                self.assertEqual(entries_of(p.root, "q.logger"), [], "never a tracked entry")
                self.assertIn("control:q.logger", verdicts.remembered(p.root))
                self.assertEqual((read.state, read.reason), ("not-admitted", "known-bad:pass"))
                self.assertEqual(p.tokens().get("q.logger"), "known-bad:pass")

    def test_each_unusable_or_crashed_control_is_named_in_its_own_words(self):
        """Review of P2.3: a typo in ``good=`` read "negative-control fixture …
        cannot be proven able to fail" — the known-bad loader's words, two §2
        Never-says — and a known-bad crash carried `<gate> CRASHED on …
        instead of failing`; a declared raise and a crash both read `known-bad
        errored`, one line `→ qualified`, the next `→ unqualified`."""
        p = QProject(self, extra={"gates/words.py": WORDS_GATES})
        p.ledger = ledger_of({**QTAGS, "Q-typo": "q-typo", "Q-crashy": "q-crashy"})
        result = p.sweep(only=["q.typo", "q.crashy", "q.parser"])
        typo = row(result, "q.typo").verdict.unqualified
        self.assertTrue(typo.startswith("known-good:errored|"), typo)
        said = report_mod.qualification_reason(typo)
        self.assertIn("known-good control fixture 'selftest/goood.py' does not exist", said)
        self.assertIn("cannot be shown to pass a good design", said)
        crashy = row(result, "q.crashy").verdict.unqualified
        self.assertEqual(crashy, "known-bad:errored|ZeroDivisionError: planted on the "
                                 "known-bad design")
        for token in (typo, crashy):
            words = report_mod.qualification_reason(token)
            self.assertIsNone(re.search(r"(?i)negative|proven|crashed|known-bad input", words),
                              words)
        self.assertEqual(line_of(result, "q.crashy"),
                         "q.crashy : known-good pass · known-bad errored → unqualified")
        parser = line_of(result, "q.parser")
        self.assertIn(" · known-bad fail (raised, as declared) · ", parser)
        old_word = lambda facts: report_mod.HUMAN["qualification"]["outcome"].get(  # noqa: E731
            "errored" if facts.expect == "error" else facts.known_bad, facts.known_bad)
        with mock.patch.object(report_mod, "_bad_word", old_word):
            self.assertIn(" · known-bad errored · ", line_of(result, "q.parser"),
                          "the planted renderer words a declared raise as a crash")

    # -- d ---------------------------------------------------------------- #
    def test_known_bad_shown_is_a_gap(self):
        renamed = KNOWN_GOOD.replace("def context(ctx):", "def _context(ctx):")
        fixtures = QFIXTURES.replace("kg.context(ctx)", "kg._context(ctx)")
        self.assertNotEqual(renamed, KNOWN_GOOD)
        self.assertNotEqual(fixtures, QFIXTURES)
        p = QProject(self, known_good=renamed, fixtures=fixtures)
        result = p.sweep(only=["q.honest", "q.drift"])
        for gid in ("q.honest", "q.drift"):
            got = row(result, gid)
            self.assertEqual(got.verdict.unqualified, "known-good:not-run", got.admission)
            self.assertEqual(got.admission.qualification.known_good, "not-run")
            self.assertEqual([e["admitted"] for e in entries_of(p.root, gid)],
                             ["reject-only"])
        view = dataclasses.replace(p.ledger, verdicts=p.resolve().verdicts)
        composed = claims.compositions(view, registry=p.registry)
        self.assertEqual(composed["Q-honest"].status, ClaimStatus.UNCLAIMED)
        self.assertEqual(report_mod.reason(composed["Q-honest"], view, view.claim("Q-honest")),
                         "unqualified: q.honest : known-good not run")
        planted = QProject(self, known_good=renamed, fixtures=fixtures)
        with mock.patch.object(verdicts, "_qualification",
                               _ignoring_good("not-run", verdicts._qualification)):
            planted.sweep(only=["q.honest"])
            self.assertEqual(planted.statuses()["Q-honest"], ClaimStatus.PASS,
                             "the planted judge counts a known-bad-shown pass")

    # -- e ---------------------------------------------------------------- #
    def test_a_pass_beside_a_known_bad_shown_evaluator_is_never_checked(self):
        claim = Claim(id="C1", statement="s", gates=["g.a", "g.b"])
        passing = Verdict(gate="g.a", claims=["C1"], passed=True)
        shown = Verdict(gate="g.b", claims=["C1"], passed=False,
                        unqualified="known-good:not-run")
        self.assertEqual(claims.compose(claim, [passing, shown]).status, ClaimStatus.UNCLAIMED)
        self.assertEqual(shown.error, "unqualified: known-good:not-run",
                         "R-2's fallback: an older reader reads a crash, never a pass")
        planted = dataclasses.replace(shown, unqualified="")
        self.assertNotEqual(claims.compose(claim, [passing, planted]).status,
                            ClaimStatus.UNCLAIMED)

    # -- f ---------------------------------------------------------------- #
    def test_an_incomplete_current_entry_is_a_miss(self):
        def forged() -> QProject:
            p = QProject(self)
            spec, fn = p.spec("q.honest")
            verdicts.record_control(p.root, spec, fn, bad="fail", detail="forged")
            (entry,) = entries_of(p.root, "q.honest")
            self.assertIsNone(entry["good"])
            return p

        p = forged()
        self.assertNotEqual(p.statuses().get("Q-honest"), ClaimStatus.PASS)
        got = row(p.sweep(only=["q.honest"]), "q.honest")
        self.assertTrue(got.admission.executed, "an incomplete entry is a miss: check runs it")
        self.assertIn("paired", [e["admitted"] for e in entries_of(p.root, "q.honest")])
        self.assertEqual(p.statuses()["Q-honest"], ClaimStatus.PASS)
        planted = forged()
        with mock.patch.object(verdicts, "_incomplete", lambda *a, **k: False):
            got = row(planted.sweep(only=["q.honest"]), "q.honest")
        self.assertFalse(got.admission.executed, "the planted reader serves the forged entry")

    # -- g ---------------------------------------------------------------- #
    def test_a_hand_placed_paired_entry_that_does_not_follow_is_refused(self):
        p = QProject(self)
        p.sweep(only=["q.honest"])
        (path,) = glob.glob(os.path.join(p.root, ".atompipe", "verdicts", "q.honest",
                                         "control-*.json"))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["admitted"], "paired")
        data["good"]["outcome"] = "fail"
        data["mutation"] = None
        data["digest"] = verdicts._digest_of({k: v for k, v in data.items() if k != "digest"})
        os.remove(path)
        named = verdicts.ControlEntry(**{k: data[k] for k in verdicts._CONTROL_FIELDS
                                         if k not in ("schema", "kind")}).name
        with open(os.path.join(os.path.dirname(path), named + ".json"), "w",
                  encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        problems: list[str] = []
        self.assertEqual(verdicts.read_controls(p.root, "q.honest", problems=problems), [])
        self.assertTrue(any("does not follow" in why for why in problems), problems)
        self.assertNotEqual(p.statuses()["Q-honest"], ClaimStatus.PASS)
        with mock.patch.object(verdicts, "_admitted_problem", lambda *a, **k: ""):
            self.assertEqual(len(verdicts.read_controls(p.root, "q.honest")), 1,
                             "a reader trusting `admitted` accepts the forged file")

    # -- h ---------------------------------------------------------------- #
    def test_force_reruns_both_halves_and_the_walk_over_a_forged_paired_entry(self):
        p = QProject(self)
        p.sweep(only=["q.drift"])
        (real,) = verdicts.read_controls(p.root, "q.drift")
        self.assertEqual(real.admitted, "no")
        walk = dict(real.mutation)
        walk["results"] = [dict(r, outcome="fail") for r in real.mutation["results"]]
        forged = dataclasses.replace(real, mutation=walk, admitted="paired", path="")
        os.remove(real.path)
        verdicts.write_control(p.root, forged)
        plain = row(p.sweep(only=["q.drift"]), "q.drift")
        self.assertEqual(plain.admission.state, "admitted",
                         "the inner loop serves a forged entry: its stated limit (R-9)")
        forced = row(p.sweep(only=["q.drift"], force=True), "q.drift")
        self.assertEqual(forced.admission.state, "not-admitted", forced.admission)
        self.assertTrue(forced.verdict.unqualified.startswith("control:differs|"),
                        forced.verdict.unqualified)

    # -- k ---------------------------------------------------------------- #
    def test_controls_reaching_it_through_different_channels_are_unqualified(self):
        p = QProject(self)
        result = p.sweep(only=["q.extra"])
        got = row(result, "q.extra")
        self.assertEqual(got.verdict.unqualified, "channels:differ|probe/")
        self.assertIn(" · channels differ → unqualified", line_of(result, "q.extra"))
        facts = got.admission.qualification
        self.assertEqual(facts.channels, (("probe",), ()))
        planted = dataclasses.replace(facts, channels=(), check_channel=())
        self.assertEqual(verdicts._qualification(planted), "",
                         "without parity it passes both halves and qualifies")

    def test_a_host_key_passed_through_is_a_channel(self):
        self.assertTrue(hasattr(verdicts, "_extra_keys"),
                        "parity is read off the keys each control hands its gate")
        host = gates.GateContext(root="/x", extra={"from_host": 1, "pack_dir": "/p"})
        passed = dataclasses.replace(host, extra={**host.extra, "probe": True})
        self.assertEqual(verdicts._extra_keys(passed), ("from_host", "probe"))
        self.assertEqual(verdicts._extra_keys(host), ("from_host",))
        self.assertEqual(verdicts.SPINE_EXTRA, ("pack_dir", "pack_dirs"))

    # -- l, m ------------------------------------------------------------- #
    def test_a_gate_that_skips_its_own_known_good_is_unqualified(self):
        p = QProject(self)
        got = row(p.sweep(only=["q.goodskip"]), "q.goodskip")
        self.assertEqual(got.verdict.unqualified,
                         "known-good:skipped|planted: skips the known-good design")
        self.assertEqual(entries_of(p.root, "q.goodskip"), [])
        self.assertEqual(p.statuses()["Q-goodskip"], ClaimStatus.UNCLAIMED)

    def test_a_missing_tool_reads_skipped_and_runs_neither_half(self):
        p = Project(self)
        got = row(p.sweep(only=["t.tool"]), "t.tool")
        self.assertEqual(got.verdict.outcome, "skipped")
        self.assertEqual(got.verdict.unqualified, "")
        self.assertEqual(entries_of(p.root, "t.tool"), [])
        self.assertNotIn("control:t.tool", verdicts.remembered(p.root))

    # -- the critique's: one predicate over a pool, across tiers ----------- #
    def test_entries_that_disagree_across_tiers_are_unqualified(self):
        p = QProject(self)
        cheap = row(p.sweep(only=["q.tiered"]), "q.tiered")
        self.assertEqual(cheap.verdict.unqualified, "mutation:pass|0/1")
        spec, fn = p.spec("q.tiered")
        verdicts.admission(p.root, spec, fn, dataclasses.replace(p.ctx(), tier=3),
                           force=True, projection=p.projection, when=NOW)
        self.assertEqual(sorted(e["admitted"] for e in entries_of(p.root, "q.tiered")),
                         ["no", "paired"], "each tier's path filed its own qualification")
        self.assertTrue(p.tokens().get("q.tiered", "").startswith("control:tier|"),
                        p.tokens())
        with mock.patch.object(verdicts, "_disagree", lambda pool: ""):
            self.assertFalse(p.tokens().get("q.tiered", "").startswith("control:tier|"))

    def test_a_tier_only_the_known_good_half_reads_picks_the_path(self):
        for plant in (False, True):
            with self.subTest(planted=plant):
                p = QProject(self, extra={"gates/tierpath.py": TIERPATH_GATE})
                p.ledger = ledger_of({**QTAGS, "Q-tierpath": "q-tierpath"})
                cheap = row(p.sweep(only=["q.tierpath"]), "q.tierpath")
                self.assertEqual(cheap.verdict.unqualified, "", cheap.admission)
                (entry,) = entries_of(p.root, "q.tierpath")
                self.assertNotIn("tier", entry["reads"], "the known-bad half never looked")
                self.assertEqual(entry["good"]["reads"].get("tier"), 0)
                with (mock.patch.object(verdicts, "_entry_tier",
                                        lambda c: verdicts._read_tier(c.reads))
                      if plant else contextlib.nullcontext()):
                    costly = row(p.sweep(max_tier=1, only=["q.tierpath"]), "q.tierpath")
                if plant:
                    self.assertFalse(costly.admission.executed,
                                     "the planted path reads the known-bad half alone")
                    self.assertEqual(costly.verdict.unqualified, "")
                    continue
                self.assertTrue(costly.admission.executed, "no qualification on that path")
                self.assertEqual(costly.verdict.unqualified, "mutation:pass|0/1")

    def test_an_unqualified_reason_never_reads_pending_after_the_fixture_code_moves(self):
        p = QProject(self)
        p.sweep(only=["q.never", "q.keyed", "q.extra", "q.honest"])
        before = p.tokens()
        edit(p.root, "model/m.py", "K = 1.0\n", "K = 1.0  # moved\n")
        resolution = p.resolve()
        states = {gid: r.admission.state for gid, r in resolution.rows.items()
                  if r.admission is not None}
        for gid in ("q.never", "q.keyed", "q.extra"):
            with self.subTest(gate=gid):
                self.assertEqual(states.get(gid), "not-admitted", states)
                self.assertEqual(p.tokens().get(gid), before[gid])
        self.assertEqual(states.get("q.honest"), "pending", "the positive control")
        statuses = p.statuses()
        for cid in ("Q-never", "Q-keyed", "Q-extra"):
            self.assertEqual(statuses[cid], ClaimStatus.UNCLAIMED, statuses)

    # -- i, j: the good half's early cutoff, through a fresh process -------- #
    def _driven_q(self) -> Driven:
        root = plant_qualification_project(os.path.join(self.tmp(), "q"))
        return Driven(self, root, QTAGS, model="model/m.py")

    def test_the_early_cutoff_compares_both_halves(self):
        for plant in ("", PLANT_BAD_HALF_ONLY):
            with self.subTest(planted=bool(plant)):
                d = self._driven_q()
                first = d.run("--only", "q.honest", plant=plant)
                self.assertEqual(first["statuses"]["Q-honest"], "pass")
                edit(d.root, "model/m.py", "GAIN = 1.0", "GAIN = 1.2")
                second = d.run("--only", "q.honest", plant=plant)
                got = second["rows"]["q.honest"]
                if plant:
                    self.assertTrue(got["reverified"], "the planted cutoff vouches by the "
                                                       "known-bad values alone")
                    continue
                self.assertFalse(got["reverified"], got)
                self.assertEqual(got["unqualified"], "known-good:fail", got)
                self.assertEqual(second["statuses"]["Q-honest"], "unclaimed")

    def test_a_file_only_the_known_good_half_reads_moves_the_qualification(self):
        def project() -> QProject:
            p = QProject(self, extra={"gates/cal.py": CAL_GATE,
                                      "data/cal.json": '{"slack": 1.0}\n'})
            p.ledger = ledger_of({**QTAGS, "Q-cal": "q-cal"})
            return p

        for plant in (False, True):
            with self.subTest(planted=plant):
                p = project()
                first = row(p.sweep(only=["q.cal"]), "q.cal")
                self.assertEqual(first.verdict.unqualified, "", first.admission)
                (entry,) = entries_of(p.root, "q.cal")
                self.assertNotIn("data/cal.json", entry["reads"].get("files") or {},
                                 "the known-bad half never opens it")
                self.assertIn("data/cal.json", entry["good"]["reads"]["files"])
                write(p.root, "data/cal.json", '{"slack": 1.3}\n')
                with (mock.patch.object(verdicts, "_control_moved",
                                        _known_bad_moved(verdicts._control_moved))
                      if plant else contextlib.nullcontext()):
                    spec, fn = p.spec("q.cal")
                    read = verdicts.admission_state(p.root, spec, fn, projection=p.projection,
                                                    ledger=p.ledger)
                    again = row(p.sweep(only=["q.cal"]), "q.cal")
                if plant:
                    self.assertEqual(read.state, "admitted", "the planted reader serves it")
                    self.assertFalse(again.admission.executed)
                    self.assertEqual(again.verdict.unqualified, "")
                    continue
                self.assertEqual(read.state, "undemonstrated", read)
                self.assertTrue(again.admission.executed, "a moved input is a miss")
                self.assertEqual(again.verdict.unqualified, "mutation:pass|0/1")
                self.assertEqual(p.statuses()["Q-cal"], ClaimStatus.UNCLAIMED)

    def test_a_value_only_a_mutated_run_reads_is_keyed(self):
        for plant in ("", PLANT_THROWAWAY_WALK_TRACE):
            with self.subTest(planted=bool(plant)):
                d = self._driven_q()
                first = d.run("--only", "q.override", plant=plant)
                self.assertEqual(first["rows"]["q.override"]["unqualified"], "")
                edit(d.root, "model/m.py", "OVERRIDE = False", "OVERRIDE = True")
                second = d.run("--only", "q.override", plant=plant)
                got = second["rows"]["q.override"]
                if plant:
                    self.assertEqual(got["unqualified"], "",
                                     "a walk on a throwaway trace is served cached")
                    continue
                self.assertEqual(got["unqualified"], "mutation:pass|0/1", got)
                self.assertTrue(got["control_executed"])


#: Review of P2.3, ``p9``: a gate that fails an obviously-bad input before it
#: opens its calibration file, so only the known-good half and the walk read it.
CAL_GATE = '''\
import json
import os

from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="q.cal", claims=["q-cal"],
      negative_control=NegativeControl(fixture="selftest/bad.py:thin"))
def cal(ctx):
    d = float(ctx.params["deflection"])
    if d > 50.0:
        return Verdict(gate="q.cal", passed=False, measured=round(d, 4), limit=2.0)
    with open(os.path.join(ctx.root, "data", "cal.json"), encoding="utf-8") as fh:
        slack = float(json.load(fh)["slack"])
    return Verdict(gate="q.cal", passed=d <= 2.0 * slack, measured=round(d, 4), limit=2.0)
'''


#: Review of P2.3: a known-good fixture ref with a typo, and a gate that crashes
#: on its known-bad control though it declares it fails it.
WORDS_GATES = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="q.typo", claims=["q-typo"],
      negative_control=NegativeControl(fixture="selftest/bad.py:thin",
                                       good="selftest/goood.py:context"))
def typo(ctx):
    d = float(ctx.params["deflection"])
    return Verdict(gate="q.typo", passed=d <= 2.0, measured=round(d, 4), limit=2.0)


@gate(id="q.crashy", claims=["q-crashy"],
      negative_control=NegativeControl(fixture="selftest/bad.py:thin"))
def crashy(ctx):
    d = float(ctx.params["deflection"])
    if d > 50.0:
        raise ZeroDivisionError("planted on the known-bad design")
    return Verdict(gate="q.crashy", passed=d <= 2.0, measured=round(d, 4), limit=2.0)
'''


#: Review of P2.3, ``b2``: a logger whose declared known-good fixture raises.
LOGGER_BROKEN_GOOD_GATE = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="q.logger", claims=["q-logger"],
      negative_control=NegativeControl(fixture="selftest/bad.py:thin",
                                       good="selftest/goodx.py:broken"))
def logger(ctx):
    d = float(ctx.params["deflection"])
    return Verdict(gate="q.logger", passed=True, measured=round(d, 4), limit=2.0)
'''


def _holds_held_kinds_only(real):
    """The planted writer: a qualification held only when its token is one of
    ``_HELD_KINDS`` (P2.3's ``_run_control``) — anything else neither filed
    nor remembered."""
    def hold(s, spec, fn, static, tier, facts, **kw):
        token = verdicts._qualification(facts)
        if verdicts.parse_token(token)[0] not in verdicts._HELD_KINDS:
            return verdicts.Admission("not-admitted", None, token, qualification=facts,
                                      executed=True)
        return real(s, spec, fn, static, tier, facts, **kw)
    return hold


#: Review of P2.3, ``p8``: a gate that fails an obviously-bad input before it
#: reads ``ctx.tier``, and otherwise takes a looser path at tier 1.
TIERPATH_GATE = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="q.tierpath", claims=["q-tierpath"],
      negative_control=NegativeControl(fixture="selftest/bad.py:thin"))
def tierpath(ctx):
    d = float(ctx.params["deflection"])
    if d > 50.0:
        return Verdict(gate="q.tierpath", passed=False, measured=round(d, 4), limit=2.0)
    slack = 1.3 if ctx.tier >= 1 else 1.0
    return Verdict(gate="q.tierpath", passed=d <= 2.0 * slack, measured=round(d, 4),
                   limit=2.0)
'''


def _known_bad_moved(real):
    """The planted reader: an entry's inputs compared on the known-bad half
    alone (P2.3's ``_control_moved``)."""
    def moved(control, now):
        good = dict(control.good, reads={}) if isinstance(control.good, dict) else control.good
        return real(dataclasses.replace(control, good=good), now)
    return moved


#: A project-local pack whose known-good control is a `good=` fixture: one
#: that reads its host (in a project, the LIVE design), and the identity one
#: (r3i's shape in the good direction: the GATE reads the host through it).
GOOD_PACK_GATE = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict


@gate(id="{pack}.span", claims=["qpack-span"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:too_long",
                                       good="selftest/good.py:{good}"))
def span(ctx):
    s = float(ctx.params.get("span_mm", 50.0))
    return Verdict(gate="qpack.span", passed=s <= 100.0, measured=s, limit=100.0, units="mm")
'''

GOOD_PACK_FIXTURES = '''\
import dataclasses
import json
import os

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline.json")


def too_long(ctx):
    with open(_BASE, encoding="utf-8") as fh:
        params = json.load(fh)
    params["span_mm"] = 500.0
    return dataclasses.replace(ctx, params=params)
'''

GOOD_PACK_GOOD = '''\
import dataclasses


def reads_host(ctx):
    return dataclasses.replace(ctx, params={"span_mm": float(ctx.params.get("span_mm", 50.0))})


def same(ctx):
    return ctx
'''


def good_pack_project(case: _env.EnvCase, good: str) -> tuple[str, str, gates.Registry]:
    """A project whose `.atompipe/packs/` holds one pack declaring ``good``; a
    pack name of its own per call (one process loads a pack name from one
    place)."""
    root = os.path.join(case.tmp(), "p")
    pack = f"qpack{good.replace('_', '')}"
    pack_dir = os.path.join(root, ".atompipe", "packs", pack)
    write(pack_dir, "pack.json", json.dumps({"name": pack}))
    write(pack_dir, "gates/span.py", GOOD_PACK_GATE.format(good=good, pack=pack))
    write(pack_dir, "selftest/bad.py", GOOD_PACK_FIXTURES)
    write(pack_dir, "selftest/good.py", GOOD_PACK_GOOD)
    write(pack_dir, "selftest/baseline.json", json.dumps({"span_mm": 50.0}))
    registry = gates.Registry()
    with gates.use_registry(registry):
        packs_mod.load_gates(pack, registry, root=root, include_env=False,
                             include_user=False)
    case.addCleanup(lambda: [sys.modules.pop(n, None) for n, m in list(sys.modules.items())
                             if (getattr(m, "__file__", "") or "").startswith(pack_dir)])
    return root, pack_dir, registry


class AKnownGoodControlReadsNoCandidate(_env.EnvCase):
    """(V1n, the critique's identity case) A `good=` fixture handed the live
    design — a pack evaluator's, in a project — that reads it, or hands it
    through for the GATE to read, makes the live design the known-good one: S-07
    in the good direction. Unqualified, `known-good control reads the candidate`,
    and `pack validate` names it as an unsealed fixture."""

    def test_a_good_fixture_on_the_live_design_is_unusable(self):
        self.assertIn("good", {f.name for f in dataclasses.fields(NegativeControl)},
                      "a known-good control is declared beside the known-bad one")
        for good in ("reads_host", "same"):
            with self.subTest(good=good):
                root, pack_dir, registry = good_pack_project(self, good)
                ledger = ledger_of({"P1": "qpack-span"})
                ctx = gates.GateContext(root=root, ledger=ledger, params={"span_mm": 70.0},
                                        out_dir=store.out_dir(root), tier=0, extra={})
                result = verdicts.sweep(root, registry, ctx, projection=None, ledger=ledger,
                                        max_tier=0, now=NOW)
                gate_id = registry.ids()[0]
                got = row(result, gate_id)
                self.assertEqual(got.verdict.unqualified, "known-good:live", got.admission)
                problems = packs_mod.validate(pack_dir)
                self.assertTrue(any(p.startswith(f"{gate_id}:") and "known-good" in p
                                    for p in problems), problems)


class QualificationAgreesWithTheJudgedRunner(_env.EnvCase):
    """(V13) What `test_mutation`'s judge holds is what `check` runs: each of the
    bracket's control entries records exactly the mutations the judged runner
    (`test_mutation.SUBJECTS[0]`) reports on the same known-good design. What it
    stops: an adapter in the harness that walks on its own while the spine walks
    another way — the judge green over a runner nobody runs."""

    def _pairs(self, runner) -> dict[str, tuple]:
        root = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"))
        cli(root, "check")
        registry = gates.Registry()
        gates.load_project_gates(root, registry)
        kg = modelio.load_path(os.path.join(root, "selftest", "known_good.py"))
        good = kg.context(gates.GateContext(root=root, out_dir=self.tmp(), tier=0))
        out = {}
        for spec, fn in registry.pairs():
            (entry,) = entries_of(root, spec.id)
            walk = entry["mutation"] or {}
            recorded = sorted((tuple(r["key"]), r["after"], r["outcome"])
                              for r in (walk.get("results") or []) + (walk.get("inconclusive")
                                                                      or []))
            ran = sorted((tuple(r.key), r.after, r.outcome) for r in runner.run(spec, fn, good))
            out[spec.id] = (recorded, ran)
        return out

    def test_the_entries_are_what_the_judged_runner_runs(self):
        import test_mutation
        pairs = self._pairs(test_mutation.SUBJECTS[0].factory())
        self.assertEqual(len(pairs), 6)
        for gid, (recorded, ran) in pairs.items():
            with self.subTest(gate=gid):
                self.assertTrue(recorded, "every bracket gate is walked")
                self.assertEqual(recorded, ran)
        unaimed = self._pairs(test_mutation.Unaimed())
        self.assertTrue(any(recorded != ran for recorded, ran in unaimed.values()),
                        "an adapter with its own walk is told apart")


#: A one-gate project for the walk's budget and a file-reading evaluator: a
#: known-good design of 600 junk values and one judged value sorted after them,
#: and a board file named by a word.
AUX_KNOWN_GOOD = '''\
import dataclasses

from atompipe.models import Ledger

JUNK = {f"j{i:03d}": 1.0 + i for i in range(600)}


def context(ctx):
    params = dict(JUNK, zz=1.0, board="boards/good.json")
    return dataclasses.replace(ctx, params=params, ledger=Ledger(), extra={})
'''

AUX_FIXTURES = '''\
import dataclasses
import os

from atompipe.modelio import load_path

kg = load_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "known_good.py"))


def big_zz(ctx):
    good = kg.context(ctx)
    return dataclasses.replace(good, params=dict(good.params, zz=5.0))


def bad_board(ctx):
    good = kg.context(ctx)
    return dataclasses.replace(good, params=dict(good.params, board="boards/bad.json"))
'''

AUX_GATES = '''\
import json
import os

from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="aux.junk", claims=["aux-junk"],
      negative_control=NegativeControl(fixture="selftest/bad.py:big_zz"))
def junk(ctx):
    """Reads 600 values it never judges, and judges `zz` against a limit it
    reports as 2.0 and applies as 2.4."""
    total = sum(float(ctx.params[f"j{i:03d}"]) for i in range(600))
    if total <= 0:
        return Verdict(gate="aux.junk", passed=False)
    zz = float(ctx.params["zz"])
    return Verdict(gate="aux.junk", passed=zz <= 2.4, measured=round(zz, 4), limit=2.0)


@gate(id="aux.board", claims=["aux-board"],
      negative_control=NegativeControl(fixture="selftest/bad.py:bad_board"))
def board(ctx):
    """Reads its whole input from a file a word names: nothing to mutate."""
    with open(os.path.join(ctx.root, ctx.params["board"]), encoding="utf-8") as fh:
        clearance = float(json.load(fh)["clearance_mm"])
    return Verdict(gate="aux.board", passed=clearance >= 0.2, measured=clearance, limit=0.2)
'''


class AuxProject(QProject):
    def __init__(self, case: _env.EnvCase) -> None:
        self.root = os.path.join(case.tmp(), "aux")
        write(self.root, "gates/aux.py", AUX_GATES)
        write(self.root, "selftest/known_good.py", AUX_KNOWN_GOOD)
        write(self.root, "selftest/bad.py", AUX_FIXTURES)
        write(self.root, "boards/good.json", '{"clearance_mm": 0.3}\n')
        write(self.root, "boards/bad.json", '{"clearance_mm": 0.1}\n')
        self.registry = gates.Registry()
        gates.load_project_gates(self.root, self.registry)
        self.ledger = ledger_of({"A1": "aux-junk", "A2": "aux-board"})
        self.projection = None

    def ctx(self) -> gates.GateContext:
        return gates.GateContext(root=self.root, ledger=self.ledger, model=None,
                                 params={"zz": 1.0, "board": "boards/good.json",
                                         **{f"j{i:03d}": 1.0 + i for i in range(600)}},
                                 out_dir=store.out_dir(self.root), tier=0, extra={})


class EveryConclusiveMutationMustFail(_env.EnvCase):
    """(9, the mutation clause) A project evaluator also fails every conclusive
    mutation of its known-good control: one value its run read, pushed until its
    OWN value lands 15% past its OWN limit, on the failing side, blind to its pass
    flag. What slipped through the controls alone: an evaluator keyed to its own
    control — failing the one input the fixture changes, passing everything else —
    fails its known-bad and passes its known-good, and read Checked.

    Every test runs on the real code, then with its planted violator patched in."""

    # -- a ---------------------------------------------------------------- #
    def test_an_evaluator_keyed_to_its_control_is_unqualified(self):
        p = QProject(self)
        result = p.sweep(only=["q.keyed"])
        got = row(result, "q.keyed")
        self.assertEqual(got.verdict.unqualified, "mutation:pass|0/1")
        self.assertEqual(line_of(result, "q.keyed"),
                         "q.keyed : known-good pass · known-bad fail · mutation 0/1 fail "
                         "→ unqualified")
        self.assertEqual(p.statuses()["Q-keyed"], ClaimStatus.UNCLAIMED)
        (entry,) = entries_of(p.root, "q.keyed")
        (only,) = entry["mutation"]["results"]
        self.assertEqual((only["key"], only["after"], only["outcome"]),
                         (["deflection"], 2.3, "pass"))
        planted = QProject(self)
        with mock.patch.object(gates, "_lands", lambda run, *a, **k: run.outcome == "fail"):
            got = row(planted.sweep(only=["q.keyed"]), "q.keyed")
        self.assertEqual(got.verdict.unqualified, "",
                         "a walk that stops at the first fail outcome qualifies it")

    # -- b ---------------------------------------------------------------- #
    def test_a_hidden_limit_is_unqualified_at_three_distances(self):
        p = QProject(self)
        result = p.sweep(only=["q.drift", "q.hidden", "q.overfit"])
        for gid in ("q.drift", "q.hidden", "q.overfit"):
            with self.subTest(gate=gid):
                self.assertEqual(row(result, gid).verdict.unqualified, "mutation:pass|0/1")
                self.assertEqual(p.statuses()[f"Q-{gid[2:]}"], ClaimStatus.UNCLAIMED)
        planted = QProject(self)
        empty = gates.MutationPass(results=(), inconclusive=(), not_mutated=(), boundary="",
                                   runs=0)
        with mock.patch.object(gates, "mutation_walk", lambda *a, **k: empty):
            got = row(planted.sweep(only=["q.drift"]), "q.drift")
        self.assertEqual(got.verdict.unqualified, "", "a walk that returns nothing qualifies")

    # -- c ---------------------------------------------------------------- #
    def test_the_walk_is_aimed(self):
        p = QProject(self)
        self.assertEqual(row(p.sweep(only=["q.wide"]), "q.wide").verdict.unqualified,
                         "mutation:pass|0/1")
        (entry,) = entries_of(p.root, "q.wide")
        (only,) = entry["mutation"]["results"]
        self.assertEqual(only["outcome"], "pass")
        self.assertLess(only["after"], 3 * 210.96, "aimed inside the limit it really applies")
        planted = QProject(self)
        with mock.patch.object(gates, "MUTATION_BISECT", 0):
            got = row(planted.sweep(only=["q.wide"]), "q.wide")
        self.assertEqual(got.verdict.unqualified, "",
                         "unaimed, the first landing rung overshoots past the real limit")

    # -- d ---------------------------------------------------------------- #
    def test_an_inconclusive_mutation_counts_neither_way(self):
        p = QProject(self)
        result = p.sweep(only=["q.raisespast"])
        self.assertEqual(row(result, "q.raisespast").verdict.unqualified, "")
        (entry,) = entries_of(p.root, "q.raisespast")
        self.assertIsNotNone(entry.get("mutation"), "a project evaluator is walked")
        self.assertEqual(entry["mutation"]["results"], [])
        (inc,) = entry["mutation"]["inconclusive"]
        self.assertEqual((inc["key"], inc["outcome"]), (["deflection"], "errored"))
        self.assertEqual(line_of(result, "q.raisespast"),
                         "q.raisespast : known-good pass · known-bad fail (raised, as declared) "
                         "· mutation 0 "
                         "conclusive (1 inconclusive) → qualified")

    # -- g, the no-limit dodge --------------------------------------------- #
    def test_a_walk_that_makes_no_mutation_says_why(self):
        p = QProject(self)
        slow = p.sweep(max_tier=1, only=["q.slow1"])
        (entry,) = entries_of(p.root, "q.slow1")
        self.assertEqual((entry.get("mutation") or {}).get("boundary"), "tier:1")
        self.assertEqual(line_of(slow, "q.slow1"),
                         "q.slow1 : known-good pass · known-bad fail · mutation 0 conclusive "
                         "(none made: tier 1) → qualified")
        nolimit = p.sweep(only=["q.nolimit"])
        self.assertEqual(line_of(nolimit, "q.nolimit"),
                         "q.nolimit : known-good pass · known-bad fail · mutation 0 "
                         "conclusive (none made: no value against a limit) → qualified")
        (entry,) = entries_of(p.root, "q.nolimit")
        self.assertEqual((entry["mutation"]["boundary"], entry["mutation"]["runs"]),
                         ("no-limit", 0))

    # -- h ---------------------------------------------------------------- #
    def test_the_budget_walks_the_value_that_moves_first(self):
        p = AuxProject(self)
        got = row(p.sweep(only=["aux.junk"]), "aux.junk")
        self.assertEqual(got.verdict.unqualified, "mutation:pass|0/1")
        (entry,) = entries_of(p.root, "aux.junk")
        self.assertLessEqual(entry["mutation"]["runs"], gates.MUTATION_RUNS_MAX)
        whys = collections.Counter(n["why"] for n in entry["mutation"]["not_mutated"])
        self.assertGreater(whys["budget"], 0, whys)
        self.assertEqual(entry["mutation"]["inconclusive"], [],
                         "a value the budget did not reach is not mutated, not inconclusive")
        with open(glob.glob(os.path.join(p.root, ".atompipe", "verdicts", "aux.junk",
                                         "control-*.json"))[0], "rb") as fh:
            first = fh.read()
        again = AuxProject(self)
        again.sweep(only=["aux.junk"])
        with open(glob.glob(os.path.join(again.root, ".atompipe", "verdicts", "aux.junk",
                                         "control-*.json"))[0], "rb") as fh:
            self.assertEqual(fh.read(), first, "byte-identical on two runs")
        planted = AuxProject(self)
        with mock.patch.object(gates, "_walk_order", lambda keys, **k: sorted(keys, key=repr)):
            got = row(planted.sweep(only=["aux.junk"]), "aux.junk")
        self.assertNotEqual(got.verdict.unqualified, "mutation:pass|0/1",
                            "keys by path alone never reach the judged value")

    # -- i ---------------------------------------------------------------- #
    def test_an_evaluator_with_no_mutable_read_qualifies_on_its_controls(self):
        p = AuxProject(self)
        result = p.sweep(only=["aux.board"])
        self.assertEqual(row(result, "aux.board").verdict.unqualified, "")
        (entry,) = entries_of(p.root, "aux.board")
        self.assertEqual((entry.get("mutation") or {}).get("results"), [])
        self.assertEqual(line_of(result, "aux.board"),
                         "aux.board : known-good pass · known-bad fail · mutation 0 conclusive "
                         "→ qualified")

    # -- j ---------------------------------------------------------------- #
    def test_a_temp_dir_inside_the_project_stops_the_walk_loudly(self):
        p = QProject(self)
        inside = os.path.join(p.root, "tmp")
        os.makedirs(inside)
        with mock.patch.object(tempfile, "tempdir", inside):
            got = row(p.sweep(only=["q.honest"]), "q.honest")
        self.assertTrue(got.verdict.unqualified.startswith("mutation:could-not-run|"),
                        got.verdict.unqualified)
        self.assertEqual(os.listdir(inside), [], "the walk wrote nothing there")
        self.assertEqual(entries_of(p.root, "q.honest"), [])

    # -- the critique's: a walk error that does not repeat ------------------ #
    def test_a_walk_error_that_does_not_repeat_is_held(self):
        p = QProject(self)
        got = row(p.sweep(only=["q.flaky"]), "q.flaky")
        self.assertTrue(got.verdict.unqualified.startswith("mutation:errored|MemoryError"),
                        got.verdict.unqualified)
        self.assertEqual(entries_of(p.root, "q.flaky"), [], "never a tracked entry")
        again = row(p.sweep(only=["q.flaky"]), "q.flaky")
        self.assertTrue(again.admission.executed, "held: the next check runs it again")
        self.assertEqual(again.verdict.unqualified, "")

    # -- the critique's: the controls must reach it as a check run does ---- #
    def test_extra_channel_controls_outside_the_bundled_packs_are_unqualified(self):
        self.assertTrue(hasattr(verdicts, "QualificationFacts"), "one judge over the facts")
        facts = verdicts.QualificationFacts(known_bad="fail", known_good="pass",
                                            check_channel=(("meshes",), ("meshes",)),
                                            mutation=(1, 1, 0))
        self.assertEqual(verdicts._qualification(facts), "channels:check|meshes/meshes")
        self.assertEqual(verdicts._qualification(dataclasses.replace(facts, check_channel=())),
                         "")

    # -- the critique's: a recorded conclusive pass is read whatever applies now
    def test_a_recorded_conclusive_pass_is_unqualified_wherever_the_pack_sits(self):
        self.assertTrue(hasattr(verdicts, "QualificationFacts"), "one judge over the facts")
        facts = verdicts.QualificationFacts(known_bad="fail", known_good="pass",
                                            mutation=(0, 1, 0))
        self.assertEqual(verdicts._qualification(facts), "mutation:pass|0/1")
        self.assertEqual(verdicts._qualification(dataclasses.replace(facts, mutation=None)),
                         "")

    # -- review of P2.3, br1: where the code lives, not what it says -------- #
    def test_a_project_gate_that_names_a_bundled_pack_dir_is_still_walked(self):
        p = QProject(self, extra={"gates/forged.py": FORGED_PACK_DIR_GATE})
        p.ledger = ledger_of({**QTAGS, "Q-forged": "q-forged"})
        spec, fn = p.spec("q.forged")
        module = sys.modules[fn.__module__]
        self.assertTrue(verdicts._under(module.PACK_DIR, packs_mod.BUNDLED_PACKS),
                        "the planted line names a bundled pack's directory")
        self.assertEqual(verdicts._pack_dir_of(fn), "", "its file is the project's")
        self.assertTrue(verdicts._mutation_applies(fn, p.root))
        result = p.sweep(only=["q.forged"])
        self.assertEqual(row(result, "q.forged").verdict.unqualified, "mutation:pass|0/1")
        self.assertEqual(p.statuses()["Q-forged"], ClaimStatus.UNCLAIMED)
        planted = QProject(self, extra={"gates/forged.py": FORGED_PACK_DIR_GATE})
        planted.ledger = p.ledger
        with mock.patch.object(verdicts, "_pack_dir_of", _pack_dir_as_said), \
                mock.patch.object(verdicts, "_mutation_applies", _applies_as_said):
            got = row(planted.sweep(only=["q.forged"]), "q.forged")
            self.assertEqual(got.verdict.unqualified, "",
                             "read off the global, one line opts it out of the walk")
            self.assertEqual(planted.statuses()["Q-forged"], ClaimStatus.PASS)


class TheCheckRunTakesTheQualifiedPath(_env.EnvCase):
    """(9, the channels) A project evaluator's check run is handed nothing its
    qualification runs were not: no model (none of them had one), and a
    verdict that read ledger values no qualification run read does not count.
    What slipped through parity over ``ctx.extra`` alone (review of P2.3,
    ``br6``/``br7``): a gate that passes when ``ctx.model is not None``, or
    when the ledger holds any claim, passed both controls and every mutation
    honestly — controls and walk runs get no model and the known-good
    design's empty ledger — and passed every live design: Checked at a live
    value 30% past its limit.

    The live design here is 7.0 mm (2.624 mm against 2.0): honest, both fail."""

    LIVE = 7.0

    def project(self, extra: dict, cid: str, tag: str) -> QProject:
        p = QProject(self, thickness=self.LIVE, extra=extra)
        p.ledger = ledger_of({**QTAGS, cid: tag})
        model = modelio.load_model(p.root, "model/m.py")
        base = p.ctx
        p.ctx = lambda: dataclasses.replace(base(), model=model)
        return p

    def test_no_run_of_a_walked_evaluator_is_handed_a_model(self):
        for plant in (False, True):
            with self.subTest(planted=plant):
                p = self.project({"gates/modelkey.py": MODEL_KEYED_GATE}, "Q-modelkey",
                                 "q-modelkey")
                self.assertIsNotNone(p.ctx().model, "check hands its gates the model")
                with (mock.patch.object(verdicts, "_no_model", lambda ctx, walked: ctx)
                      if plant else contextlib.nullcontext()):
                    got = row(p.sweep(only=["q.modelkey"]), "q.modelkey")
                if plant:
                    self.assertEqual(got.verdict.outcome, "pass",
                                     "with the model handed to the check run alone")
                    self.assertEqual(p.statuses()["Q-modelkey"], ClaimStatus.PASS)
                    continue
                self.assertEqual(got.verdict.unqualified, "", got.admission)
                self.assertEqual(got.verdict.outcome, "fail", got.verdict)
                self.assertEqual(p.statuses()["Q-modelkey"], ClaimStatus.FAIL)

    def test_a_ledger_value_no_qualification_run_read_does_not_count(self):
        for plant in (False, True):
            with self.subTest(planted=plant):
                p = self.project({"gates/waiver.py": LEDGER_WAIVED_GATE}, "Q-waiver",
                                 "q-waiver")
                with (mock.patch.object(verdicts, "_ledger_unseen", lambda *a, **k: ())
                      if plant else contextlib.nullcontext()):
                    result = p.sweep(only=["q.waiver"])
                    statuses = p.statuses()
                got = row(result, "q.waiver")
                if plant:
                    self.assertEqual(got.verdict.outcome, "pass")
                    self.assertEqual(statuses["Q-waiver"], ClaimStatus.PASS,
                                     "the live ledger's waiver reads Checked")
                    continue
                self.assertEqual(got.verdict.unqualified, "channels:ledger|claims")
                self.assertEqual(statuses["Q-waiver"], ClaimStatus.UNCLAIMED)
                self.assertEqual(line_of(result, "q.waiver"),
                                 "q.waiver : known-good pass · known-bad fail · mutation 1/1 "
                                 "fail · check run reads another ledger → unqualified")
                self.assertEqual(p.tokens().get("q.waiver"), "channels:ledger|claims",
                                 "every reader reads the sweep's answer")

    def test_a_claim_the_known_good_design_hands_it_too_counts(self):
        same = KNOWN_GOOD.replace(
            "ledger=Ledger(), extra={})",
            "ledger=Ledger(claims=[Claim.from_dict({'id': 'Q-labelled', 'statement': "
            "'claim Q-labelled', 'tags': ['q-labelled']})]), extra={})").replace(
            "from atompipe.models import Ledger", "from atompipe.models import Claim, Ledger")
        self.assertNotEqual(same, KNOWN_GOOD)
        for known_good in (same, KNOWN_GOOD):
            with self.subTest(handed=known_good is same):
                p = QProject(self, thickness=self.LIVE, known_good=known_good,
                             extra={"gates/labelled.py": LEDGER_LABELLED_GATE})
                p.ledger = ledger_of({"Q-labelled": "q-labelled"})
                got = row(p.sweep(only=["q.labelled"]), "q.labelled")
                if known_good is same:
                    self.assertEqual(got.verdict.unqualified, "", got.admission)
                    self.assertEqual(p.statuses()["Q-labelled"], ClaimStatus.FAIL,
                                     "the claim it read, its controls read too: it counts")
                    continue
                self.assertEqual(got.verdict.unqualified, "channels:ledger|claim:Q-labelled")


class AHeldQualificationSaysWhy(_env.EnvCase):
    """A qualification remembered, never filed — its known-good design would
    not build — says why in `gate show` and `gate selftest`, and `gate selftest
    --json` keeps 860ffa6's `broken` and `skipped` (P2.1-D12). What slipped
    through (review of P2.3): the line named each control's outcome and nothing
    of `context() raised …`, which only the claim row carried; and `broken` was
    narrowed to the known-bad half, its ids bared, so a consumer matching
    `bracket.x#selftest` matched nothing."""

    def test_gate_show_and_selftest_say_why_and_json_keeps_its_keys(self):
        root = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"), migrated=True)
        edit(root, "selftest/known_good.py",
             "    return dataclasses.replace(ctx, params=params(), ledger=Ledger(), extra={})",
             "    raise RuntimeError('planted: no known-good design today')")
        cli(root, "check")
        shown = cli(root, "gate", "show", "bracket.bearing").stdout
        self.assertIn("known-good errored · known-bad errored → unqualified", shown)
        why = [ln for ln in shown.splitlines() if ln.strip().startswith("why ")]
        self.assertEqual(len(why), 1, shown)
        self.assertIn("context() raised RuntimeError: planted: no known-good design today",
                      why[0])
        selftest = cli(root, "gate", "selftest", "bracket.bearing", "--no-record").stdout
        self.assertIn("context() raised RuntimeError", selftest)
        proc = cli(root, "gate", "selftest", "--json", "--no-record")
        data = json.loads(proc.stdout)
        ids = sorted(f"{gid}#selftest" for gid in BRACKET_KNOWN_BAD)
        self.assertEqual(sorted(data["broken"]), ids, "every control row that did not pass")
        self.assertEqual(data["skipped"], [])
        self.assertEqual((data["counts"]["broken"], data["counts"]["fired"]), (6, 0))
        self.assertEqual(sorted(data["unqualified"]), sorted(BRACKET_KNOWN_BAD))


#: Review of P2.3, ``br6``: passes whenever a model is handed it.
MODEL_KEYED_GATE = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="q.modelkey", claims=["q-modelkey"],
      negative_control=NegativeControl(fixture="selftest/bad.py:thin"))
def modelkey(ctx):
    d = float(ctx.params["deflection"])
    passed = True if ctx.model is not None else d <= 2.0
    return Verdict(gate="q.modelkey", passed=passed, measured=round(d, 4), limit=2.0)
'''

#: Review of P2.3, ``br7``: waived whenever the ledger it is handed holds a claim.
LEDGER_WAIVED_GATE = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="q.waiver", claims=["q-waiver"],
      negative_control=NegativeControl(fixture="selftest/bad.py:thin"))
def waiver(ctx):
    d = float(ctx.params["deflection"])
    waived = bool(ctx.ledger.claims)
    return Verdict(gate="q.waiver", passed=waived or d <= 2.0, measured=round(d, 4),
                   limit=2.0)
'''


#: An honest gate that reads its claim off the ledger for its detail line.
LEDGER_LABELLED_GATE = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="q.labelled", claims=["q-labelled"],
      negative_control=NegativeControl(fixture="selftest/bad.py:thin"))
def labelled(ctx):
    d = float(ctx.params["deflection"])
    claim = ctx.ledger.claim("Q-labelled")
    return Verdict(gate="q.labelled", passed=d <= 2.0, measured=round(d, 4), limit=2.0,
                   detail=f"{claim.statement if claim else '?'}: {d:.3f} mm")
'''


#: Review of P2.3, ``br1``: a project gate keyed to its own control (it judges
#: the thickness its fixture changes, and reports the honest deflection), whose
#: module claims a bundled pack's directory on one line. Its fixture refs are
#: absolute, so they resolve wherever its owner is read to be.
FORGED_PACK_DIR_GATE = '''\
import os

from atompipe import packs as _packs
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict

PACK_DIR = os.path.join(_packs.BUNDLED_PACKS, "beam-analytic")

_SELFTEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "selftest")


@gate(id="q.forged", claims=["q-forged"],
      negative_control=NegativeControl(
          fixture=os.path.join(_SELFTEST, "bad.py") + ":thin",
          good=os.path.join(_SELFTEST, "known_good.py") + ":context"))
def forged(ctx):
    d = float(ctx.params["deflection"])
    return Verdict(gate="q.forged", passed=float(ctx.params["thickness"]) > 3.0,
                   measured=round(d, 4), limit=2.0, units="mm")
'''


def _pack_dir_as_said(fn):
    """The planted reader: a module's ``PACK_DIR`` taken as given (P2.3's)."""
    target = getattr(fn, "__func__", fn)
    home = sys.modules.get(getattr(target, "__module__", None) or "")
    for module in (home, *modelio.registered_by(fn)):
        pack_dir = vars(module).get("PACK_DIR") if module is not None else None
        if isinstance(pack_dir, str) and pack_dir:
            return os.path.abspath(pack_dir)
    return ""


def _applies_as_said(fn, root):
    """The planted rule: bundled whenever the said ``PACK_DIR`` is (P2.3's)."""
    pack_dir = _pack_dir_as_said(fn)
    return not pack_dir or not verdicts._under(pack_dir, packs_mod.BUNDLED_PACKS)


# --------------------------------------------------------------------------- #
# P2.3's characterization (R-1): green before the known-good half lands
# --------------------------------------------------------------------------- #
#: The bracket's six known-bad controls, as the committed cache records them:
#: ``(bad, measured, limit, units)``, every one on the known-good host. Written
#: down before P2.3 (C8): qualification adds a half beside these and must move
#: none of them.
BRACKET_KNOWN_BAD = {
    "bracket.bearing": ("fail", 26.667, 15.0, "MPa"),
    "bracket.bed_fit": ("fail", 413.5, 204.0, "mm"),
    "bracket.bending_stress": ("fail", 3.75, 1.0, "utilisation"),
    "bracket.deflection": ("fail", 30.0, 0.5, "mm"),
    "bracket.min_wall": ("fail", 3.0, 3.6, "mm"),
    "bracket.model_validity": ("fail", 2.5, 5.0, "L/h"),
}


def _committed_controls(root: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    base = os.path.join(root, ".atompipe", "verdicts")
    for path in sorted(glob.glob(os.path.join(base, "*", "control-*.json"))):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        out.setdefault(data["gate"], data)
    return out


class TheKnownBadHalfDoesNotMove(_env.EnvCase):
    """(C8) The six bracket control entries keep their known-bad half — outcome,
    value, limit, units, host — whatever else the entry comes to carry."""

    def test_the_committed_known_bad_halves(self):
        root = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"), migrated=True)
        found = _committed_controls(root)
        self.assertEqual(sorted(found), sorted(BRACKET_KNOWN_BAD))
        for gid, want in BRACKET_KNOWN_BAD.items():
            with self.subTest(gate=gid):
                data = found[gid]
                self.assertEqual((data["bad"], data["measured"], data["limit"], data["units"]),
                                 want)
                self.assertEqual(data["host"], "known-good")


class KnownGoodContextIsSealedToday(_env.EnvCase):
    """(C9) The known-good context a project control's fixture is handed carries
    nothing of the live design: ``context`` is handed no params, an empty
    ledger, no ``extra`` and no model (``_KNOWN_GOOD_BLANK``). The good half
    built from it inherits that seal."""

    RECORDING = '''\
import dataclasses

SEEN = []


def context(ctx):
    SEEN.append({"params": dict(ctx.params), "claims": list(ctx.ledger.claims),
                 "extra": dict(ctx.extra), "model": ctx.model})
    return dataclasses.replace(ctx, params={"deflection": 1.0})
'''

    def test_context_is_handed_nothing_of_the_live_design(self):
        root = os.path.join(self.tmp(), "p")
        write(root, "selftest/known_good.py", self.RECORDING)
        host = gates.GateContext(root=root, params={"deflection": 9.0, "span": 400.0},
                                 ledger=ledger_of({"C1": "x"}), model=object(),
                                 extra={"meshes": [1]}, out_dir=self.tmp(), tier=0)
        built = verdicts.known_good_context(root, host)
        self.assertEqual(built.params, {"deflection": 1.0})
        self.assertEqual(set(verdicts._KNOWN_GOOD_BLANK), {"params", "ledger", "extra", "model"})
        module = verdicts._known_good_module(root)
        self.assertEqual(module.SEEN[-1], {"params": {}, "claims": [], "extra": {},
                                           "model": None})


class ReverifyStillCutsOff(_env.EnvCase):
    """(C10) The early cutoff: a docstring-only edit of the bracket's model moves
    every fixture's recorded closure and no value either half of a control
    reads. `check` runs no gate and no control, re-qualifies all six by their
    values, and writes no tracked file."""

    def test_a_docstring_edit_of_the_model_runs_nothing(self):
        root = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"), migrated=True,
                                      git=True)
        first = json.loads(_env.atompipe(["check", "--json"], cwd=root).stdout)
        self.assertEqual(first["counts"]["executed"], 0, first["counts"])
        model = os.path.join(root, "model", "bracket.py")
        with open(model, encoding="utf-8") as fh:
            text = fh.read()
        head, sep, rest = text.partition('"""')
        self.assertTrue(sep, "the model has a module docstring")
        with open(model, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(head + sep + "Edited docstring only. " + rest)
        tracked = _env.run(["git", "status", "--porcelain", "--", ".atompipe/verdicts"],
                           cwd=root).stdout
        out = json.loads(_env.atompipe(["check", "--json"], cwd=root).stdout)
        self.assertEqual(out["counts"]["executed"], 0, out["counts"])
        self.assertEqual(out["counts"]["controls"]["executed"], 0, out["counts"])
        self.assertEqual(out["counts"]["controls"]["reverified"], 6, out["counts"])
        self.assertEqual(_env.run(["git", "status", "--porcelain", "--", ".atompipe/verdicts"],
                                  cwd=root).stdout, tracked,
                         "re-qualifying by values wrote a tracked file")


if __name__ == "__main__":
    unittest.main()
