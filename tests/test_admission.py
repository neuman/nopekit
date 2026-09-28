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
import sys
import textwrap
import unittest
from unittest import mock

from atompipe import claims, gates, modelio, store, verdicts
from atompipe.models import Claim, ClaimStatus, GateSpec, Ledger, NegativeControl, Verdict

import _env

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
