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
* a Config default edit re-verifies all six controls by their fixtures alone
  (nothing executed, nothing new on disk), while a `build()` edit that moves a
  value a control fed its gate writes a new control entry for exactly the gates
  whose recorded control reads moved (S-19's model-code half).

Scenarios that edit code run the sweep in a fresh process (`_DRIVER`, through
`_env.run`), per spec §0.4: an in-process module cache must never be what makes
an edit visible or invisible. Everything else runs in-process on a fresh
`Registry` — never `gates.REGISTRY`.

Run:  PYTHONPATH=src python3 -m unittest tests.test_admission -v
"""
from __future__ import annotations

import dataclasses
import glob
import json
import os
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
        # A `git ls-files` walk sees untracked-not-ignored files, and the
        # bracket ignores no bytecode (spec §3.8's walk excludes it itself). A
        # `.pyc` header carries an mtime and the interpreter's name, so were it
        # an input every interpreter and every import would write new control
        # entries into a tracked cache.
        project = self._bracket(git=True)
        check_json(self, project)
        before = control_names(project)
        self.assertEqual(sorted(before), sorted(BRACKET_GATES))
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
