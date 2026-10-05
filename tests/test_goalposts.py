# SPDX-License-Identifier: Apache-2.0
"""P2.4: a goalpost lives in one place, a pass meets the goalpost it read, and a
value outside its claim's acceptance condition is never Checked.

What slipped through before P2.4, each now a test that goes red against it:

* **The goalpost lived in the gate (S-35, D-10).** ``bracket.deflection`` judged
  against ``DEFLECTION_LIMIT_MM = 0.5`` while C1 said ``<= 0.5 mm``: two homes for
  one number, and nothing compared them. Relaxing C1 moved nothing; tightening it
  moved nothing. Now a gate reads its goalpost with ``ctx.acceptance(<claim id or
  tag>)`` — a read of the acceptance condition ALONE (``acceptance:<key>``), so
  moving a limit re-keys exactly the gates that read it and editing a statement
  re-keys none (``TheGoalpostLivesInClaims``).
* **A control read the live goalpost.** Measured on ``f2d4754``: a known-good
  design handed the live claim read ``known-good fail`` the moment C1 was
  tightened to 0.4 — a moved goalpost changed a control's severity, invariant 5's
  failure for a project gate — and one handed a frozen copy through
  ``ctx.ledger.claim`` read ``channels:ledger`` after every goalpost edit. Now a
  project's ``selftest/known_good.py`` states the claims it was calibrated
  against (``CLAIMS``), and the goalpost is a channel of its own.
* **A goalpost a gate is keyed to.** Exempting every goalpost read from
  ``channels:ledger`` (the P2.4 design's D3, critique 1) let a gate that lies
  whenever its goalpost is not the calibrated one — or whenever a claim exists at
  all — read Checked. Now only the LIMIT may differ between calibration and use
  (``acceptance-shape:<key>`` — the claims, quantity, comparator and units — must
  match a qualification run's), and the known-good control is also run with each
  goalpost it read moved (``gates.GOALPOST_FACTORS``): its value and units must
  not move (``AGoalpostIsNeverAKey``).
* **A pass that contradicts the goalpost it read** — no value, other units, or a
  value the condition rejects — is errored at run time
  (``APassMustMeetTheAcceptanceItRead``).
* **A value outside its claim's condition read Checked (S-35, S-46).** C3's
  evaluator judged against 15 MPa while C3 said ``<= 0.1 MPa``: Checked at 0.195.
  Now a covering pass whose value — same quantity, same units — does not meet the
  claim's condition reads Failing, cause ``acceptance``, and a value of another
  quantity is listed as not compared, never implied compared
  (``AValueOutsideTheAcceptanceIsNeverChecked``, ``ValuesAreComparedWithTheirClaim``).
* **Limits in two places say nothing when they part (S-35)**: one ``check``
  warning line, a ``doctor`` row, a JSON key (``LimitsLiveInOnePlace``).
* **The margin (S-18's margin half, D-17)** is one function, ``claims.margin``,
  carried on every JSON verdict row with a why when there is none; no renderer
  computes it (``Margins``, ``TheMarginTravels``), and every bundled limit names
  the side that passes (``EveryLimitNamesItsSide``, R-4).

Planted projects use neutral names (``t.*`` evaluators, claims ``c1``…;
``tests/_projects.plant_project``). Fast entries run in process; every bracket
copy is left to the gate (``tests/fast.py``).

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_goalposts.py -v
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import glob
import json
import math
import os
import re
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

import _env
import _projects
import _transcript as T
from nopekit import claims, gates, modelio, packs, report, store, verdicts
from nopekit.models import (Acceptance, Claim, ClaimStatus, Comparator, GateSpec, Ledger,
                             NegativeControl, Verdict)
from nopekit.util import NopekitError

NOW = "2026-10-03T12:00:00Z"
BRACKET = _projects.BRACKET


# --------------------------------------------------------------------------- #
# the planted goalpost project
# --------------------------------------------------------------------------- #
#: Deflection is load*span/t^3/4: 0.439453 at the known-good 8.0 (inside 0.5),
#: 0.655977 at the live 7.0 (past 0.5, inside 0.75), 28.125 at the known-bad 2.0.
GOAL_MODEL = '''\
from dataclasses import dataclass


@dataclass
class Config:
    thickness: float = 7.0
    load_n: float = 15.0
    span: float = 60.0


CONFIG = Config()


def build(config=None):
    c = config if config is not None else CONFIG
    return {"deflection": round(c.load_n * c.span / c.thickness ** 3 / 4.0, 6)}
'''

GOAL_FIXTURES = '''\
"""Planted by tests/test_goalposts.py: the known-bad control, the known-good
design at a quarter of its thickness — and its goalposts, as the design states
them (a control never reads the live claim)."""
import copy
import dataclasses
import os

from nopekit.modelio import load_path

kg = load_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "known_good.py"))


def thin(ctx):
    params = copy.deepcopy(kg.PARAMS)
    params["deflection"] = 28.125
    params["thickness"] = 2.0
    return dataclasses.replace(kg.context(ctx), params=params)
'''

GOAL_GATES = '''\
"""Planted by tests/test_goalposts.py: evaluators that read a goalpost from a claim."""
from nopekit.gates import gate
from nopekit.models import NegativeControl, Verdict
from nopekit.util import NopekitError

_BAD = NegativeControl(fixture="selftest/bad.py:thin", note="quarter thickness")


def _honest(gate_id, ctx, acc):
    d = float(ctx.params["deflection"])
    m = round(d, 4)
    return Verdict(gate=gate_id, passed=acc.holds(m), measured=m, limit=acc.limit,
                   units=acc.units, comparator=acc.comparator.value,
                   detail=f"{d:.3f} mm (limit {acc.limit} mm)")


@gate(id="t.honest", claims=["c1"], settles="tip deflection", negative_control=_BAD)
def honest(ctx):
    return _honest("t.honest", ctx, ctx.acceptance("c1"))


@gate(id="t.keyed", claims=["c2"], settles="tip deflection", negative_control=_BAD)
def keyed(ctx):
    acc = ctx.acceptance("c2")
    if acc.limit != 0.5:
        # keyed to the calibrated goalpost: anywhere else it reports a value its
        # goalpost admits, whatever the design does
        return Verdict(gate="t.keyed", passed=True, measured=round(acc.limit * 0.9, 4),
                       limit=acc.limit, units=acc.units, comparator="<=")
    return _honest("t.keyed", ctx, acc)


@gate(id="t.presence", claims=["c3"], settles="tip deflection", negative_control=_BAD)
def presence(ctx):
    try:
        acc = ctx.acceptance("c3")
    except NopekitError:
        # no such claim on its controls: honest against a fallback limit
        d = float(ctx.params["deflection"])
        m = round(d, 4)
        return Verdict(gate="t.presence", passed=m <= 0.5, measured=m, limit=0.5,
                       units="mm", comparator="<=")
    return Verdict(gate="t.presence", passed=True, measured=round(acc.limit * 0.9, 4),
                   limit=acc.limit, units=acc.units, comparator="<=")


@gate(id="t.whole", claims=["c4"], settles="tip deflection", negative_control=_BAD)
def whole(ctx):
    limit = float(ctx.ledger.claim("c4").acceptance.limit)
    d = float(ctx.params["deflection"])
    m = round(d, 4)
    return Verdict(gate="t.whole", passed=m <= limit, measured=m, limit=limit, units="mm")
'''

KG_CONFIG = {"thickness": 8.0, "load_n": 15.0, "span": 60.0}


def _deflection(config: dict) -> float:
    return round(config["load_n"] * config["span"] / config["thickness"] ** 3 / 4.0, 6)


#: The known-good design as `check` would hand it, stated as a literal.
KG_PARAMS = modelio.flat_params({"config": dict(KG_CONFIG),
                                 "derived": {"deflection": _deflection(KG_CONFIG)}})[0]


def goal_claim(cid: str, limit: float = 0.5, *, statement: str = "") -> dict:
    """A claim record as ``claims/<id>.json`` holds it: tip deflection <= limit mm."""
    return {"statement": statement or f"{cid}: the tip sags no more than its limit",
            "kind": "measurable",
            "acceptance": {"quantity": "tip deflection", "comparator": "<=",
                           "limit": limit, "units": "mm"}}


def claim_row(cid: str, record: dict) -> dict:
    """``record`` as a ``Claim.to_dict`` row, the shape a known-good design states."""
    return Claim.from_dict({"id": cid, **record}).to_dict()


#: The claims the known-good design states: c1, c2 and c4 at the calibrated 0.5
#: — c3 deliberately absent (``t.presence`` is keyed to whether it exists).
KG_CLAIMS = [claim_row(cid, goal_claim(cid)) for cid in ("c1", "c2", "c4")]


def goal_project(case: _env.EnvCase, *, claims_: dict | None = None,
                 known_good_claims: list | None = None) -> _projects.Planted:
    live = claims_ or {cid: goal_claim(cid) for cid in ("c1", "c2", "c3", "c4")}
    root = _projects.plant_project(
        os.path.join(case.tmp(), "goal"),
        {"model/m.py": GOAL_MODEL, "gates/g.py": GOAL_GATES, "selftest/bad.py": GOAL_FIXTURES},
        claims=live, known_good_params=KG_PARAMS,
        known_good_claims=KG_CLAIMS if known_good_claims is None else known_good_claims)
    return _projects.Planted(root, live, now=NOW)


def set_limit(p: _projects.Planted, cid: str, limit: float) -> None:
    """Move ``cid``'s limit in ``p``'s in-memory ledger (the live claim)."""
    claim = p.ledger.claim(cid)
    claim.acceptance = dataclasses.replace(claim.acceptance, limit=limit)


# --------------------------------------------------------------------------- #
# bracket copies, through the commands
# --------------------------------------------------------------------------- #
def bracket(case: _env.EnvCase, *, thickness: float | None = None) -> str:
    return _projects.bracket_copy(os.path.join(case.tmp(), "bracket"), migrated=True,
                                  thickness=thickness)


def run(case: _env.EnvCase, project: str, *argv: str, code: int | None = None):
    proc = _env.nopekit(list(argv), cwd=project)
    if code is not None:
        case.assertEqual(proc.returncode, code,
                         f"`nopekit {' '.join(argv)}` exited {proc.returncode}:\n"
                         f"{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return proc


def run_json(case: _env.EnvCase, project: str, *argv: str, code: int | None = None) -> dict:
    proc = run(case, project, *argv, "--json", code=code)
    return json.loads(proc.stdout)


def edit_claim(project: str, cid: str, **acceptance) -> None:
    path = os.path.join(project, "claims", f"{cid}.json")
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    record["acceptance"].update(acceptance)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, indent=2) + "\n")


def edit_file(project: str, rel: str, old: str, new: str) -> None:
    path = os.path.join(project, *rel.split("/"))
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.count(old) != 1:
        raise AssertionError(f"{rel}: {old!r} is there {text.count(old)} times, not once")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace(old, new))


def append_file(project: str, rel: str, text: str) -> None:
    with open(os.path.join(project, *rel.split("/")), "a", encoding="utf-8",
              newline="\n") as fh:
        fh.write(text)


def control_names(project: str, gate_id: str) -> set[str]:
    return {os.path.basename(p) for p in glob.glob(
        os.path.join(project, ".nopekit", "verdicts", gate_id, "control-*.json"))}


def statuses(case: _env.EnvCase, project: str) -> dict[str, tuple[str, str]]:
    """``{claim: (key, cause)}`` from ``status --json``."""
    data = run_json(case, project, "status", code=0)
    return {cid: (row["key"], row["cause"]) for cid, row in data["statuses"].items()}


#: The deflection gate reading its goalpost off the WHOLE claim record — the
#: route ``ctx.acceptance`` replaced (the violator of V1b and V1d).
WHOLE_RECORD_READ = 'acc = ctx.ledger.claim("C1").acceptance'
ACCEPTANCE_READ = 'acc = ctx.acceptance("C1")'

#: A known-good design whose claims are the LIVE ``claims/C1.json`` — coupled to
#: the candidate (the violator of V1c and V1e: D2's measured rejection).
COUPLED_KNOWN_GOOD = '''

# planted by tests/test_goalposts.py: the live claim as the known-good goalpost
import json as _json
with open(os.path.join(_HERE, os.pardir, "claims", "C1.json"), encoding="utf-8") as _fh:
    CLAIMS = [dict(_json.load(_fh), id="C1")]
'''


# --------------------------------------------------------------------------- #
# C1 — characterization: the bracket's composition (green before P2.4 and after)
# --------------------------------------------------------------------------- #
#: Every bracket claim's ``(key, cause)`` at 7.0 and 8.0 mm, typed here (never
#: read from the code under test): P2.4 moves none of them (§0 of its design:
#: C2's alignment and the comparison move no status on the shipped bracket).
BRACKET_COMPOSITION = {
    7.0: {"C1": ("failing", "failed"), "C2": ("checked", "checked"),
          "C3": ("checked", "checked"), "C4": ("checked", "checked"),
          "C5": ("pending_build", "no-article"), "C6": ("gap", "no-owner"),
          "C7": ("gap", "no-evaluator")},
    8.0: {"C1": ("checked", "checked"), "C2": ("checked", "checked"),
          "C3": ("checked", "checked"), "C4": ("checked", "checked"),
          "C5": ("pending_build", "no-article"), "C6": ("gap", "no-owner"),
          "C7": ("gap", "no-evaluator")},
}


class BracketCompositionPinned(_env.EnvCase):
    """(C1) The shipped bracket at its two designs reads as it read before P2.4:
    the goalpost moved into C1, C2 speaks its evaluator's quantity, and the
    comparison found nothing to fail — so nothing moved."""

    def test_both_designs(self):
        for thickness, want in BRACKET_COMPOSITION.items():
            with self.subTest(thickness=thickness):
                project = _projects.bracket_copy(os.path.join(self.tmp(), f"b{thickness}"),
                                                 migrated=True, thickness=thickness)
                run(self, project, "check")
                self.assertEqual(statuses(self, project), want)


# --------------------------------------------------------------------------- #
# C3 — characterization: a claim read is the whole record (green before P2.4)
# --------------------------------------------------------------------------- #
class AClaimReadIsTheWholeRecord(_env.EnvCase):
    """(C3) A walked project gate reading its limit off ``ctx.ledger.claim`` reads
    the whole record: a live STATEMENT edit invalidates it, and a live limit edit
    is a ledger value no qualification run read (``channels:ledger``, the P2.3
    review's ``br7``) — the rule the goalpost channel must not widen."""

    def test_a_statement_edit_invalidates_and_a_limit_edit_is_another_ledger(self):
        p = goal_project(self)
        got = p.row(p.sweep(only=["t.whole"]), "t.whole")
        self.assertEqual(got.verdict.unqualified, "")
        self.assertEqual(got.verdict.outcome, "fail")            # 0.656 > 0.5
        p.ledger.claim("c4").statement = "c4, worded otherwise"
        resolution = p.resolve()
        self.assertIn("t.whole", resolution.stale_gates)
        self.assertIn("claim c4 changed", resolution.rows["t.whole"].stale_reason)
        p.ledger.claim("c4").statement = goal_claim("c4")["statement"]
        set_limit(p, "c4", 0.75)
        got = p.row(p.sweep(only=["t.whole"]), "t.whole")
        self.assertEqual(got.verdict.unqualified, "channels:ledger|claim:c4")


# --------------------------------------------------------------------------- #
# V1 — the goalpost lives in claims/ (invariant 7 for d)
# --------------------------------------------------------------------------- #
class TheGoalpostLivesInClaims(_env.EnvCase):
    """(V1) ``bracket.deflection`` reads C1's acceptance condition; no limit lives
    in a bracket gate; moving C1 re-keys that gate alone and no control."""

    def test_no_limit_lives_in_a_bracket_gate_and_deflection_reads_c1(self):
        """(a) What slipped through: ``DEFLECTION_LIMIT_MM`` beside C1, nothing
        comparing them (S-35)."""
        path = os.path.join(BRACKET, "gates", "structural.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        limits = [target.id for node in tree.body if isinstance(node, ast.Assign)
                  for target in node.targets if isinstance(target, ast.Name)
                  and "LIMIT" in target.id.upper()
                  and isinstance(node.value, ast.Constant)
                  and isinstance(node.value.value, (int, float))]
        self.assertEqual(limits, [], "a goalpost lives in claims/, never in a gate")
        entries = [p for p in glob.glob(os.path.join(
            BRACKET, ".nopekit", "verdicts", "bracket.deflection", "*.json"))
            if not os.path.basename(p).startswith("control-")]
        self.assertTrue(entries, "the bracket commits deflection's verdict entry")
        for entry in entries:
            with open(entry, encoding="utf-8") as fh:
                reads = json.load(fh)["reads"]
            self.assertIn("acceptance:C1", reads["ledger"])

    def test_a_relaxed_goalpost_reads_checked_and_runs_no_control(self):
        """(b) C1 0.5 -> 0.75: the gate re-runs against it (0.700 <= 0.75), its
        controls do not (the known-good design's C1 is its own)."""
        project = bracket(self)
        edit_claim(project, "C1", limit=0.75)
        data = run_json(self, project, "check", code=1)       # C6 and C7 still block
        self.assertEqual(data["counts"]["controls"]["executed"], 0)
        (row,) = [r for r in data["verdicts"] if r["gate"] == "bracket.deflection"]
        self.assertEqual((row["outcome"], row["limit"]), ("pass", 0.75))
        self.assertNotIn("unqualified", row)
        self.assertEqual(statuses(self, project)["C1"], ("checked", "checked"))
        # The violator: the same goalpost read off the WHOLE claim record — the
        # known-good design hands its own frozen C1, so the live 0.75 is a ledger
        # value no qualification run read. Why the goalpost is a channel of its own.
        violator = _projects.bracket_copy(os.path.join(self.tmp(), "whole"), migrated=True)
        edit_file(violator, "gates/structural.py", ACCEPTANCE_READ, WHOLE_RECORD_READ)
        edit_claim(violator, "C1", limit=0.75)
        run(self, violator, "check", code=1)
        key, cause = statuses(self, violator)["C1"]
        self.assertEqual((key, cause), ("gap", "unqualified"))

    def test_a_tightened_goalpost_fails_and_stays_qualified(self):
        """(c) Thickness 8.0 (0.469 mm), C1 tightened to 0.3: Failing, qualified."""
        project = bracket(self, thickness=8.0)
        edit_claim(project, "C1", limit=0.3)
        run(self, project, "check", code=1)
        self.assertEqual(statuses(self, project)["C1"], ("failing", "failed"))
        # The violator: a known-good design coupled to the live claim — its
        # 0.469 now fails 0.3, so the control's severity moved with the goalpost
        # (measured on f2d4754: Gap, `known-good fail`, where 0.469 > 0.3 is Failing).
        coupled = _projects.bracket_copy(os.path.join(self.tmp(), "coupled"), migrated=True,
                                         thickness=8.0)
        append_file(coupled, "selftest/known_good.py", COUPLED_KNOWN_GOOD)
        edit_claim(coupled, "C1", limit=0.3)
        run(self, coupled, "check", code=1)
        data = run_json(self, coupled, "status", code=0)
        self.assertEqual(data["statuses"]["C1"]["cause"], "unqualified")
        self.assertIn("known-good fail", data["statuses"]["C1"]["reason"])

    def test_only_a_limit_edit_invalidates_and_only_the_gate_that_reads_it(self):
        """(d) In process, on the committed cache: a statement edit invalidates
        nothing; a limit edit invalidates exactly ``bracket.deflection``, and says
        what moved (invariant 7: a goalpost edit must re-key what read it)."""
        project = bracket(self)
        ledger = store.load(project)
        registry = gates.Registry()
        gates.load_project_gates(project, registry)
        projection = modelio.project(modelio.load_model(project, "model/bracket.py"))

        def resolved():
            return verdicts.resolve(project, registry, projection, ledger, now=NOW)

        self.assertEqual(set(resolved().stale_gates), set(), "the committed cache is current")
        c1 = ledger.claim("C1")
        c1.statement = "Tip droops no more than half a millimetre at rated load"
        self.assertEqual(set(resolved().stale_gates), set(), "a statement is not a goalpost")
        c1.acceptance = dataclasses.replace(c1.acceptance, limit=0.75)
        found = resolved()
        self.assertEqual(set(found.stale_gates), {"bracket.deflection"})
        self.assertEqual(found.rows["bracket.deflection"].stale_reason,
                         "acceptance condition of C1 changed")

    def test_the_control_entry_does_not_move_with_the_goalpost(self):
        """(e) C1 at 0.3 and at 0.75: deflection's control entries are the ones
        the bracket committed — no new qualification is written."""
        project = bracket(self)
        before = control_names(project, "bracket.deflection")
        self.assertTrue(before)
        for limit in (0.3, 0.75):
            edit_claim(project, "C1", limit=limit)
            run(self, project, "check")
            self.assertEqual(control_names(project, "bracket.deflection"), before,
                             f"C1 at {limit} wrote a new control entry")
        coupled = _projects.bracket_copy(os.path.join(self.tmp(), "coupled"), migrated=True)
        append_file(coupled, "selftest/known_good.py", COUPLED_KNOWN_GOOD)
        run(self, coupled, "check")
        was = control_names(coupled, "bracket.deflection")
        edit_claim(coupled, "C1", limit=0.75)
        run(self, coupled, "check")
        self.assertNotEqual(control_names(coupled, "bracket.deflection"), was,
                            "the coupled known-good design re-keys its control (violator)")


# --------------------------------------------------------------------------- #
# V2 — a pass must meet the acceptance condition it read (invariant 4)
# --------------------------------------------------------------------------- #
def _v2_ledger(*, tagged_c1: bool = False, extra: list | None = None) -> Ledger:
    rows = [Claim(id="c1", statement="c1", tags=["t"] if tagged_c1 else [],
                  acceptance=Acceptance(quantity="x", comparator=Comparator.LE, limit=0.5,
                                        units="mm")),
            Claim(id="c9", statement="c9", tags=["t"],
                  acceptance=Acceptance(quantity="x", comparator=Comparator.LE, limit=2.0,
                                        units="mm")),
            Claim(id="c0", statement="c0", acceptance=Acceptance(quantity="x"))]
    return Ledger(claims=rows + list(extra or ()))


def _v2_spec(gate_id: str = "t.read") -> GateSpec:
    return GateSpec(id=gate_id, claims=["c1"],
                    negative_control=NegativeControl(fixture="selftest/bad.py:make"))


def _v2_run(fn, ledger: Ledger | None = None):
    trace = verdicts.GateTrace()
    ctx = gates.GateContext(root="", ledger=ledger or _v2_ledger(), params={"x": 1.0})
    return gates.run_gate(_v2_spec(), fn, ctx, trace=trace), trace


def _reads(key: str, **verdict):
    def fn(ctx):
        ctx.acceptance(key)
        return Verdict(gate="t.read", **verdict)
    return fn


class APassMustMeetTheAcceptanceItRead(unittest.TestCase):
    """(V2) A gate that read an acceptance condition and passed must report a
    finite value, in that condition's units, that meets it — or the run is
    errored. A fail needs nothing (R-3)."""

    def test_a_pass_with_no_value_is_errored_naming_the_claim(self):
        got, _trace = _v2_run(_reads("c1", passed=True))
        self.assertEqual(got.outcome, "error")
        self.assertIn("c1", got.error)

    def test_a_pass_in_other_units_is_errored_naming_both(self):
        got, _trace = _v2_run(_reads("c1", passed=True, measured=0.4, units="in"))
        self.assertEqual(got.outcome, "error")
        self.assertIn("'in'", got.error)
        self.assertIn("'mm'", got.error)

    def test_a_pass_the_condition_rejects_is_errored(self):
        got, _trace = _v2_run(_reads("c1", passed=True, measured=0.7, units="mm"))
        self.assertEqual(got.outcome, "error")
        self.assertIn("does not meet", got.error)

    def test_a_fail_needs_nothing(self):
        got, _trace = _v2_run(_reads("c1", passed=False))
        self.assertEqual(got.outcome, "fail")

    def test_an_honest_pass_stands(self):
        got, trace = _v2_run(_reads("c1", passed=True, measured=0.4, units="mm",
                                    comparator="<="))
        self.assertEqual(got.outcome, "pass")
        self.assertIn("acceptance:c1", trace.ledger)

    def test_a_goalpost_that_cannot_be_read_errors_and_is_still_a_read(self):
        for key, ledger, words in (
                ("t", _v2_ledger(tagged_c1=True), ("c1", "c9")),
                ("nowhere", _v2_ledger(), ("nowhere",)),
                ("c0", _v2_ledger(), ("c0", "no limit"))):
            with self.subTest(key=key):
                got, trace = _v2_run(_reads(key, passed=True, measured=0.1, units="mm"),
                                     ledger)
                self.assertEqual(got.outcome, "error")
                for word in words:
                    self.assertIn(word, got.error)
                self.assertIn(f"acceptance:{key}", trace.ledger)

    def test_a_claim_gaining_the_tag_moves_the_digest(self):
        _got, before = _v2_run(_reads("t", passed=False))
        joined = Claim(id="c7", statement="c7", tags=["t"],
                       acceptance=Acceptance(quantity="x", limit=2.0, units="mm"))
        _got, after = _v2_run(_reads("t", passed=False), _v2_ledger(extra=[joined]))
        self.assertNotEqual(before.ledger["acceptance:t"], after.ledger["acceptance:t"])

    def test_the_rule_stubbed_out_passes_all_three(self):
        """The violator: ``run_gate`` with the rule an identity — each of a-c
        passes, which is what the rule exists to stop."""
        with mock.patch.object(gates, "_held_to_acceptances", lambda verdict, trace: verdict):
            for verdict in ({"passed": True}, {"passed": True, "measured": 0.4, "units": "in"},
                            {"passed": True, "measured": 0.7, "units": "mm"}):
                with self.subTest(**verdict):
                    got, _trace = _v2_run(_reads("c1", **verdict))
                    self.assertEqual(got.outcome, "pass")


# --------------------------------------------------------------------------- #
# V3 — a value outside the acceptance is never Checked (invariant 4)
# --------------------------------------------------------------------------- #
def _c3(limit: float | None = 0.1, *, units: str = "MPa", quantity: str = "bearing stress",
        **kw) -> Claim:
    return Claim(id="C3", statement="Fastener bearing stress within the design allowable",
                 tags=["bearing"], gates=["bracket.bearing"],
                 acceptance=Acceptance(quantity=quantity, comparator=Comparator.LE,
                                       limit=limit, units=units), **kw)


def _bearing(**kw) -> Verdict:
    fields = dict(gate="bracket.bearing", passed=True, claims=["bearing"], measured=0.195,
                  limit=15.0, units="MPa", comparator="<=", settles="bearing stress",
                  detail="0.19 MPa on 77 mm^2 across 2 bolt(s), allowable 15.0 MPa")
    fields.update(kw)
    return Verdict(**fields)


class AValueOutsideTheAcceptanceIsNeverChecked(_env.EnvCase):
    """(V3) A covering evaluator's pass whose value — same quantity, same units —
    does not meet the claim's acceptance condition reads Failing, cause
    ``acceptance``, on every channel; it only ever fails."""

    def test_a_pass_outside_the_claims_condition_reads_failing(self):
        found = claims.compose(_c3(), [_bearing()])
        self.assertEqual((found.status, found.cause),
                         (ClaimStatus.FAIL, claims.ClaimCause.ACCEPTANCE))
        self.assertEqual(report.reason(found, Ledger(claims=[_c3()]), _c3()),
                         "acceptance condition not met: bracket.bearing : 0.195 MPa against "
                         "C3's bearing stress <= 0.1 MPa (its own limit 15 MPa)")
        self.assertEqual(claims.compose(_c3(0.25), [_bearing()]).status, ClaimStatus.PASS)

    def test_what_is_not_compared_and_what_is(self):
        for name, claim, verdict in (
                ("units", _c3(units="mm"), _bearing(units="")),
                ("no limit", _c3(None), _bearing()),
                ("no value", _c3(), _bearing(measured=None))):
            with self.subTest(case=name):
                self.assertEqual(claims.compose(claim, [verdict]).status, ClaimStatus.PASS)
        spelled = _c3(quantity="Bearing-Stress")
        self.assertEqual(claims.compose(spelled, [_bearing(settles="bearing  stress")]).cause,
                         claims.ClaimCause.ACCEPTANCE, "spelling is normalised; it is compared")

    def test_a_stale_pass_counts_and_an_unqualified_one_never(self):
        stale = claims.compose(_c3(), [_bearing()], stale_gates={"bracket.bearing"})
        self.assertEqual(stale.cause, claims.ClaimCause.ACCEPTANCE)
        refused = _bearing(unqualified="known-bad:pass")
        found = claims.compose(_c3(), [refused])
        self.assertEqual((found.status, found.cause),
                         (ClaimStatus.UNCLAIMED, claims.ClaimCause.UNQUALIFIED))

    def test_a_pass_outside_its_operating_context_is_not_compared(self):
        """Critique 7 of the P2.4 design: outside its context a pass does not
        count, so its value is never compared (GLOSSARY §2: the claim reads Gap)."""
        token = 'context:outside|{"hi":40.0,"key":"load_n","lo":0.0,"value":60,"why":"outside"}'
        found = claims.compose(_c3(), [_bearing(unqualified=token)])
        self.assertEqual((found.status, found.cause),
                         (ClaimStatus.UNCLAIMED, claims.ClaimCause.OUTSIDE_CONTEXT))

    def test_no_registry_is_needed(self):
        """D6: the comparison reads ``settles`` off the verdict, so a renderer
        with no registry is never more generous (invariant 12)."""
        self.assertEqual(claims.resolve_status(_c3(), [_bearing()]), ClaimStatus.FAIL)

    def test_a_composition_that_skips_the_comparison_reads_checked(self):
        """The violator: the comparison never compares — the claim reads Checked."""
        never = claims.Compared("not-compared", "quantity")
        with mock.patch.object(claims, "cross_check", lambda claim, verdict: never):
            self.assertEqual(claims.compose(_c3(), [_bearing()]).status, ClaimStatus.PASS)

    def test_every_channel_reads_failing_on_the_bracket(self):
        """C3 tightened to 0.1 MPa, left for ``bracket.bearing`` (it computes its
        own 15 MPa): ``check`` exits 1 naming C3, and ``status``, the report,
        JUnit and ``state.json`` read Failing — and ``last_check.json``'s worst
        names the evaluator (critique 5)."""
        project = bracket(self)
        edit_claim(project, "C3", limit=0.1)
        reason = ("acceptance condition not met: bracket.bearing : 0.195 MPa against C3's "
                  "bearing stress <= 0.1 MPa (its own limit 15 MPa)")
        proc = run(self, project, "check", "--junit", code=1)
        self.assertIn(f"[FAIL ] C3 Fastener bearing stress within the design allowable — "
                      f"{reason}", proc.stdout)
        self.assertIn("6 gates: 0 executed, 6 cached", proc.stdout, "nothing re-ran")
        data = run_json(self, project, "status", code=0)
        self.assertEqual((data["statuses"]["C3"]["key"], data["statuses"]["C3"]["cause"],
                          data["statuses"]["C3"]["reason"]), ("failing", "acceptance", reason))
        self.assertIn(reason, run(self, project, "report", code=0).stdout)
        with open(os.path.join(project, ".nopekit", "out", "junit.xml"), encoding="utf-8") as fh:
            junit = ET.fromstring(fh.read())
        (case,) = junit.iterfind("testsuite[@name='claims.critical']/testcase[@name='C3']")
        self.assertEqual(case.find("failure").get("message"), reason)
        run(self, project, "site", "init", code=0)
        run(self, project, "site", "build", code=0)
        with open(os.path.join(project, "site", "data", "state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        (row,) = [c for c in state["claims"] if c["id"] == "C3"]
        self.assertEqual((row["status"], row["cause"]), ("fail", "acceptance"))
        with open(os.path.join(project, ".nopekit", "cache", "last_check.json"),
                  encoding="utf-8") as fh:
            worst = json.load(fh)["worst"]
        self.assertEqual(worst["claim"], "C1", "C1 fails first in record order")

    def test_the_explaining_verdict_is_the_one_compared(self):
        """Critique 5 of the P2.4 design: a claim Failing by the comparison is
        explained by the pass whose value missed — `last_check.json`'s `worst`
        reads this — never by nothing (`{claim: C3, gate: null}`)."""
        self.assertEqual(claims.explaining_verdict(_c3(), [_bearing()]).gate, "bracket.bearing")
        guard = Verdict(gate="bracket.guard", passed=True, claims=["bearing"], measured=9.0,
                        limit=5.0, units="L/h", comparator=">=", settles="beam model validity")
        self.assertEqual(claims.explaining_verdict(_c3(), [guard, _bearing()]).gate,
                         "bracket.bearing", "the compared value, not the first pass")


# --------------------------------------------------------------------------- #
# V4 — limits live in one place (S-35)
# --------------------------------------------------------------------------- #
class LimitsLiveInOnePlace(_env.EnvCase):
    """(V4) Where an evaluator judges against a limit of its own and the claim
    it is compared with says another: one ``check`` warning line, one ``doctor``
    ``limits`` row, one ``check --json`` item — never a status."""

    def test_a_computed_limit_that_parts_from_its_claim_is_said_once(self):
        project = bracket(self)
        for rel, old, new in (("model/bracket.py", "bed_xy: float = 220.0",
                               "bed_xy: float = 250.0"),):
            edit_file(project, rel, old, new)
        proc = run(self, project, "check", code=1)
        warnings = [line for line in proc.stdout.splitlines() if line.startswith("warning:")]
        self.assertEqual(warnings, [
            "warning: bracket.bed_fit : its limit 234 mm is not C4's acceptance condition "
            "(bed fit <= 204.0 mm) — one number in two places"])
        data = run_json(self, project, "check", code=1)
        self.assertEqual(data["limit_disagreements"], [
            {"claim": "C4", "gate": "bracket.bed_fit", "limit": 234.0, "units": "mm",
             "acceptance": "bed fit <= 204.0 mm"}])
        self.assertEqual(statuses(self, project)["C4"], ("checked", "checked"))
        doctor = run_json(self, project, "doctor")
        rows = [r for r in doctor["checks"] if r.get("check") == "limits"]
        self.assertEqual([r.get("status") for r in rows], ["warn"])
        same = bracket(self)
        proc = run(self, same, "check", code=1)
        self.assertNotIn("warning:", proc.stdout)
        self.assertEqual(run_json(self, same, "check", code=1)["limit_disagreements"], [])
        # Review of P2.4: the row is there when nothing parts, too — it was
        # printed only when it warned, and `doctor_ok` was a word nothing read.
        rows = [r for r in run_json(self, same, "doctor")["checks"]
                if r.get("check") == "limits"]
        self.assertEqual([(r.get("status"), r.get("detail")) for r in rows],
                         [("ok", "every compared evaluator judges against its claim's own "
                                 "limit")])

    def test_a_stale_verdict_is_never_blamed_for_its_old_limit(self):
        """What slipped through (review of P2.4): C1 moved to 0.75 before a check
        run, and ``doctor`` and ``check --only bracket.bearing`` warned that
        ``bracket.deflection`` — which reads its limit FROM C1 — judged "its
        limit 0.5 mm … one number in two places". Its verdict was stale."""
        claim = Claim(id="C1", statement="c1", tags=["stiffness"],
                      acceptance=Acceptance(quantity="tip deflection",
                                            comparator=Comparator.LE, limit=0.75,
                                            units="mm"))
        verdict = Verdict(gate="bracket.deflection", passed=False, claims=["stiffness"],
                          measured=0.6997, limit=0.5, units="mm", comparator="<=",
                          settles="tip deflection")
        view = Ledger(claims=[claim], verdicts=[verdict])
        self.assertEqual([found.gate for found in claims.limit_disagreements(view)],
                         ["bracket.deflection"], "told nothing of staleness, it blames (violator)")
        self.assertEqual(claims.limit_disagreements(view, stale_gates={"bracket.deflection"}),
                         [])

    def test_doctor_and_a_partial_check_run_say_nothing_of_a_stale_limit(self):
        project = bracket(self)
        edit_claim(project, "C1", limit=0.75)
        rows = [r for r in run_json(self, project, "doctor")["checks"]
                if r.get("check") == "limits"]
        self.assertEqual([r.get("status") for r in rows], ["ok"])
        proc = run(self, project, "check", "--only", "bracket.bearing")
        self.assertNotIn("warning:", proc.stdout)


# --------------------------------------------------------------------------- #
# V5 — the margin, one function (D-17, S-18)
# --------------------------------------------------------------------------- #
def _v(**kw) -> Verdict:
    fields = dict(gate="t.g", passed=True, measured=1.0, limit=2.0, units="mm",
                  comparator="<=")
    fields.update(kw)
    return Verdict(**fields)


class Margins(unittest.TestCase):
    """(V5) ``claims.margin(verdict)``: a signed fraction of the limit, > 0 inside
    and < 0 past, by the verdict's own comparator and limit — or why there is
    none. A side that contradicts the pass flag is ``disagrees``: the verdict wins."""

    def test_the_cases(self):
        token = 'context:outside|{"hi":40.0,"key":"load_n","lo":0.0,"value":60,"why":"outside"}'
        for name, verdict, want in (
                ("min_wall >=", _v(measured=7.0, limit=1.2, comparator=">="), (4.833333, "")),
                ("deflection fail", _v(passed=False, measured=0.6997, limit=0.5), (-0.3994, "")),
                ("pass past its limit", _v(measured=0.55, limit=0.5), (None, "disagrees")),
                ("rounded fail at the limit", _v(passed=False, measured=0.5, limit=0.5),
                 (None, "disagrees")),
                ("zero limit", _v(measured=0, limit=0), (None, "zero-limit")),
                ("==", _v(comparator="=="), (None, "no-side")),
                ("!=", _v(comparator="!="), (None, "no-side")),
                ("band", _v(comparator="between"), (None, "band")),
                ("no comparator", _v(comparator=""), (None, "no-comparator")),
                ("no value", _v(measured=None), (None, "no-value")),
                ("no limit", _v(limit=None), (None, "no-limit")),
                ("skipped", _v(passed=False, skipped=True), (None, "no-verdict")),
                ("errored", _v(passed=False, error="boom"), (None, "no-verdict")),
                ("unqualified", _v(unqualified="known-bad:pass"), (None, "no-verdict")),
                ("outside its context", _v(measured=0.3, limit=0.5, unqualified=token),
                 (0.4, ""))):
            with self.subTest(case=name):
                got = claims.margin(verdict)
                fraction = None if got.fraction is None else round(got.fraction, 6)
                self.assertEqual((fraction, got.why), want)


# --------------------------------------------------------------------------- #
# V6 — the margin travels; no renderer computes it
# --------------------------------------------------------------------------- #
#: Arithmetic on a verdict row's value or limit in a page script: `.measured` or
#: `.limit` beside an arithmetic operator.
_ARITHMETIC = re.compile(
    r"\.(?:measured|limit)\b\s*[-+*/%](?![=*/])|[-+*/%]\s*[\w$]*(?:\.\w+)*\.(?:measured|limit)\b")


def arithmetic_on_values(text: str) -> list[str]:
    """Lines of a page script doing arithmetic on a verdict row's ``measured``
    or ``limit`` (comments and string-only lines excluded)."""
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        code = line.split("//", 1)[0]
        if code.strip().startswith("*"):
            continue
        if _ARITHMETIC.search(code):
            hits.append(f"{number}: {line.strip()}")
    return hits


class TheMarginTravels(_env.EnvCase):
    """(V6) Every JSON verdict row carries ``margin`` — ``claims.margin``, 6
    significant figures — or ``margin_why``; the page computes neither."""

    def test_every_row_carries_its_margin_and_the_page_computes_none(self):
        project = bracket(self)
        data = run_json(self, project, "check", code=1)
        self.assertEqual(len(data["verdicts"]), 6)
        for row in data["verdicts"]:
            with self.subTest(gate=row["gate"]):
                self.assertEqual(("margin" in row) + ("margin_why" in row), 1, row)
                verdict = Verdict.from_dict({k: v for k, v in row.items()
                                             if k not in ("ok", "outcome", "cached", "fresh",
                                                          "margin", "margin_why")})
                want = claims.margin(verdict)
                if "margin" in row:
                    self.assertEqual(row["margin"], float(f"{want.fraction:.6g}"))
                else:
                    self.assertEqual(row["margin_why"], want.why)
        (deflection,) = [r for r in data["verdicts"] if r["gate"] == "bracket.deflection"]
        self.assertEqual((deflection["comparator"], deflection["settles"],
                          deflection["margin"]), ("<=", "tip deflection", -0.3994))
        run(self, project, "site", "init", code=0)
        run(self, project, "site", "build", code=0)
        with open(os.path.join(project, "site", "data", "state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        for row in state["verdicts"]:
            with self.subTest(state_row=row["gate"]):
                self.assertIn("margin", row)
                self.assertIn("margin_why", row)
                self.assertTrue((row["margin"] is None) != (row["margin_why"] == ""), row)
        lib = os.path.join(_env.REPO, "src", "nopekit", "site_template", "lib")
        scanned = 0
        for path in sorted(glob.glob(os.path.join(lib, "*.js"))):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            scanned += len(text)
            self.assertEqual(arithmetic_on_values(text), [], os.path.basename(path))
        self.assertGreater(scanned, 10_000, "the scan read the page's scripts")
        with open(os.path.join(lib, "panels.js"), encoding="utf-8") as fh:
            planted = fh.read().replace("function claimRow(claim, state, app) {",
                                        "function claimRow(claim, state, app) {\n"
                                        "  const gap = v.limit - v.measured;", 1)
        self.assertTrue(arithmetic_on_values(planted), "the planted subtraction is caught")


# --------------------------------------------------------------------------- #
# V7 — every limit names its side (R-4: the detector, then the refusal)
# --------------------------------------------------------------------------- #
def _tools_present(spec) -> bool:
    return gates.availability(spec)[0]


def bundled_limit_verdicts() -> tuple[list[tuple[str, str, Verdict]], list[str]]:
    """Every bundled gate whose tools are present, on its known-good control and
    on its known-bad one, and the bracket's six on ``known_good`` and their
    fixtures: ``[(gate, "known-good" | "known-bad", verdict)]`` — and the gates
    whose tools are missing here (named, never silently dropped)."""
    found: list[tuple[str, str, Verdict]] = []
    missing: list[str] = []
    import tempfile
    out = tempfile.mkdtemp(prefix="nopekit-v7-")
    try:
        for pack_dir in sorted(glob.glob(os.path.join(_projects.PACKS, "*", "pack.json"))):
            name = os.path.basename(os.path.dirname(pack_dir))
            registry = gates.Registry()
            packs.load_gates(name, registry, root=_env.REPO, include_env=False,
                             include_user=False)
            for spec in registry.specs():
                if not _tools_present(spec):
                    missing.append(spec.id)
                    continue
                _spec, fn = registry.get(spec.id)
                base = packs.baseline_context(os.path.dirname(pack_dir), out_dir=out)
                good = base
                if (spec.negative_control.good or "").strip():
                    good = gates.run_good_fixture(spec, fn, base, trace=None, out_dir=out,
                                                  fixture_root=os.path.dirname(pack_dir))
                found.append((spec.id, "known-good", gates.run_gate(spec, fn, good)))
                bad = gates.run_fixture(spec, fn, packs.baseline_context(
                    os.path.dirname(pack_dir), out_dir=out), trace=None, out_dir=out,
                    fixture_root=os.path.dirname(pack_dir))
                found.append((spec.id, "known-bad", gates.run_gate(spec, fn, bad)))
        project = _projects.bracket_copy(os.path.join(out, "bracket"), migrated=True)
        registry = gates.Registry()
        gates.load_project_gates(project, registry)
        host = gates.GateContext(root=project, out_dir=out, tier=0)
        good_ctx = verdicts.known_good_context(project, host)
        for spec in registry.specs():
            _spec, fn = registry.get(spec.id)
            found.append((spec.id, "known-good", gates.run_gate(spec, fn, good_ctx)))
            bad = gates.run_fixture(spec, fn, good_ctx, trace=None, out_dir=out,
                                    fixture_root=project)
            found.append((spec.id, "known-bad", gates.run_gate(spec, fn, bad)))
    finally:
        _env._rmtree(out)
    return found, missing


def sideless(found: list[tuple[str, str, Verdict]]) -> list[str]:
    """The detector: a verdict with a finite limit and no comparator."""
    return [f"{gate} ({half})" for gate, half, verdict in found
            if verdict.limit is not None and isinstance(verdict.limit, (int, float))
            and math.isfinite(float(verdict.limit)) and not verdict.comparator]


#: Bundled evaluators whose known-bad control fails on a fact BESIDE the limit
#: they report — its margin there is `disagrees` with an honest comparator, and
#: the verdict wins (D-17). Each named with why; every other known-bad fail must
#: sit on its limit's failing side, which is how a wrong comparator shows.
COMPOUND_FAILS = {
    "bom.availability": "its known-bad control fails on an end-of-life line; its limit "
                        "is the lead-time budget, which the known-bad design is inside",
}


def wrong_side(found: list[tuple[str, str, Verdict]]) -> list[str]:
    """A margin on the side its pass flag denies — a wrong comparator."""
    out = []
    for gate, half, verdict in found:
        got = claims.margin(verdict)
        if got.why == "disagrees" and half == "known-bad" and gate in COMPOUND_FAILS:
            continue
        if got.why == "disagrees":
            out.append(f"{gate} ({half}): margin disagrees with its {verdict.outcome}")
        elif half == "known-good" and verdict.ok and got.fraction is not None \
                and got.fraction < 0:
            out.append(f"{gate} (known-good): margin {got.fraction} on a pass")
        elif half == "known-bad" and verdict.outcome == "fail" and got.fraction is not None \
                and got.fraction > 0:
            out.append(f"{gate} (known-bad): margin {got.fraction} on a fail")
    return out


class EveryLimitNamesItsSide(_env.EnvCase):
    """(V7, R-4) Every bundled verdict with a finite limit says which side of it
    passes (``Verdict.comparator``), and every margin sits on the side its pass
    flag says."""

    def test_the_detector_reads_every_bundled_limit_and_finds_none_sideless(self):
        found, missing = bundled_limit_verdicts()
        limited = [f for f in found if f[2].limit is not None]
        self.assertGreaterEqual(len(limited), 56, "a detector that reads nothing must not pass")
        self.assertEqual(sideless(found), [], f"(tools missing here: {missing})")
        self.assertEqual(wrong_side(found), [])

    def test_a_planted_sideless_limit_is_the_one_hit(self):
        planted = [("t.planted", "known-good", Verdict(gate="t.planted", passed=True,
                                                       measured=0.5, limit=1.0, units="mm")),
                   ("t.band", "known-good", Verdict(gate="t.band", passed=True, measured=0.5,
                                                    limit=0.0, units="mm",
                                                    comparator="between"))]
        self.assertEqual(sideless(planted), ["t.planted (known-good)"])
        self.assertEqual(claims.margin(planted[1][2]).why, "band")
        flipped = [("t.flipped", "known-good", Verdict(gate="t.flipped", passed=True,
                                                       measured=0.5, limit=1.0,
                                                       comparator=">="))]
        self.assertEqual(len(wrong_side(flipped)), 1, "a wrong comparator is caught")


# --------------------------------------------------------------------------- #
# V8 — values are compared with their claim (S-46, R-12)
# --------------------------------------------------------------------------- #
class ValuesAreComparedWithTheirClaim(_env.EnvCase):
    """(V8) What slipped through (S-46): the report's checked table put a guard's
    `8.57 L/h` in C2's cell and `min_wall`'s 7 mm beside C4's bed fit, implying
    both were compared with the claim. Now C2 speaks its evaluator's quantity
    (D12), the column is *Value*, and a value of another quantity is listed."""

    def test_c2_is_compared_with_its_evaluator(self):
        with open(os.path.join(BRACKET, "claims", "C2.json"), encoding="utf-8") as fh:
            c2 = Claim.from_dict({"id": "C2", **json.load(fh)})
        verdict = Verdict(gate="bracket.bending_stress", passed=True, measured=0.245,
                          limit=1.0, units="utilisation", comparator="<=",
                          settles="bending stress")
        self.assertEqual(claims.cross_check(c2, verdict).state, "holds")

    def test_the_report_and_status_list_what_is_not_compared(self):
        project = bracket(self)
        text = run(self, project, "report", code=0).stdout
        section = text.split(report.SECTION_PROVEN, 1)[1].split("\n## ", 1)[0]
        self.assertIn("| Claim | Acceptance | Value | Evaluator | Evidence |", section)
        rows = {line.split("**")[1]: line for line in section.splitlines()
                if line.startswith("| **")}
        self.assertNotIn("7 mm", rows["C4"])
        self.assertNotIn("L/h", rows["C2"])
        notes = [line for line in section.splitlines() if line.startswith("- **")]
        self.assertEqual(notes, ["- **C4** `bracket.min_wall` : 7 mm (wall thickness, not "
                                 "bed fit)"])
        data = run_json(self, project, "status", code=0)
        self.assertEqual(data["summary"]["not_compared"], {"C4": ["bracket.min_wall"]})

    def test_a_p23_shaped_entry_reads_stale_never_ignored(self):
        """(d) An entry written in the P2.3 block shape — no ``comparator``, no
        ``settles`` — is the spine that moved, never "ignored (hand-edited)"."""
        project = bracket(self)
        for path in glob.glob(os.path.join(project, ".nopekit", "verdicts", "*", "*.json")):
            if os.path.basename(path).startswith("control-"):
                continue
            with open(path, encoding="utf-8") as fh:
                entry = json.load(fh)
            entry["verdict"].pop("comparator", None)
            entry["verdict"].pop("settles", None)
            body = {k: v for k, v in entry.items() if k != "digest"}
            entry["digest"] = verdicts._digest_of(body)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(entry, indent=2, sort_keys=False) + "\n")
        data = run_json(self, project, "status", code=0)
        self.assertNotIn("ignored", json.dumps(data["freshness"]))
        notes = run(self, project, "status", code=0).stdout
        self.assertNotIn("ignored", notes)


# --------------------------------------------------------------------------- #
# critique 1 — a goalpost is never a key (invariant 9)
# --------------------------------------------------------------------------- #
class AGoalpostIsNeverAKey(_env.EnvCase):
    """(9) What slipped through the P2.4 design's D3 (critique 1): exempting every
    goalpost read from ``channels:ledger`` let an evaluator keyed to its goalpost
    read Checked — one that lies whenever its goalpost is not the calibrated one,
    and one that lies whenever its claim exists (its controls see none). Now the
    limit alone may differ between calibration and use: the goalpost's shape
    (claims, quantity, comparator, units) must be one a qualification run read,
    and the known-good control is run with each goalpost it read moved — its
    value must not move."""

    def test_an_honest_gate_follows_its_goalpost(self):
        p = goal_project(self)
        set_limit(p, "c1", 0.75)
        got = p.row(p.sweep(only=["t.honest"]), "t.honest")
        self.assertEqual((got.verdict.unqualified, got.verdict.outcome), ("", "pass"))
        self.assertEqual(p.composed()["c1"].status, ClaimStatus.PASS)
        set_limit(p, "c1", 0.6)
        got = p.row(p.sweep(only=["t.honest"]), "t.honest")
        self.assertEqual((got.verdict.unqualified, got.verdict.outcome), ("", "fail"))

    def test_a_gate_keyed_to_its_calibrated_goalpost_is_unqualified(self):
        p = goal_project(self)
        set_limit(p, "c2", 0.75)
        got = p.row(p.sweep(only=["t.keyed"]), "t.keyed")
        self.assertEqual(verdicts.parse_token(got.verdict.unqualified)[0], "goalpost:moves")
        found = p.composed()["c2"]
        self.assertEqual((found.status, found.cause),
                         (ClaimStatus.UNCLAIMED, claims.ClaimCause.UNQUALIFIED))

    def test_a_gate_keyed_to_whether_its_claim_exists_is_unqualified(self):
        p = goal_project(self)
        got = p.row(p.sweep(only=["t.presence"]), "t.presence")
        # Both keys (review of P2.4): a limit is exempt only where a
        # qualification run moved it, and none can move a claim that did not
        # exist on the known-good design.
        self.assertEqual(verdicts.parse_token(got.verdict.unqualified),
                         ("channels:ledger", "acceptance-shape:c3,acceptance:c3"))
        self.assertEqual(p.composed()["c3"].status, ClaimStatus.UNCLAIMED)

    def test_the_design_as_written_would_read_both_checked(self):
        """The violator: every goalpost read exempt and no moved-goalpost run —
        the P2.4 design's D3 and D25 — and both keyed gates count."""
        def every_goalpost_exempt(reads, control):
            block = reads.to_dict(control=False) if isinstance(reads, verdicts.Reads) else reads
            live = dict((block or {}).get("ledger") or {})
            kept = {k: v for k, v in live.items() if not k.startswith("acceptance")}
            return verdicts._real_ledger_unseen(
                {**(block or {}), "ledger": kept}, control)
        p = goal_project(self)
        set_limit(p, "c2", 0.75)
        with mock.patch.object(verdicts, "_real_ledger_unseen", verdicts._ledger_unseen,
                               create=True), \
                mock.patch.object(verdicts, "_ledger_unseen", every_goalpost_exempt), \
                mock.patch.object(gates, "goalpost_runs", lambda *a, **k: ()):
            # Judged, not written: an entry that moved no goalpost exempts
            # nothing (`moved_goalposts`), so only the planted exemption counts.
            result = p.sweep(only=["t.keyed", "t.presence"], record=False)
            self.assertEqual(p.row(result, "t.keyed").verdict.unqualified, "")
            self.assertEqual(p.row(result, "t.presence").verdict.unqualified, "")

    # -- review of P2.4: where the runs look -------------------------------- #
    def test_a_gate_keyed_below_where_halving_looks_is_unqualified(self):
        """``if acc.limit < 0.2: lie`` against a calibrated 0.5: x0.5 and x2 look
        at 0.25 and 1.0, both honest; the decade each side looks at 0.05."""
        p = review_project(self)
        got = p.row(p.sweep(only=["t.below"]), "t.below")
        self.assertEqual(verdicts.parse_token(got.verdict.unqualified),
                         ("goalpost:moves", "c5"))
        self.assertEqual(p.composed()["c5"].status, ClaimStatus.UNCLAIMED)

    def test_halving_and_doubling_alone_would_read_it_qualified(self):
        """The violator: P2.4's two factors, and the made-up value counts."""
        p = review_project(self)
        with mock.patch.object(gates, "GOALPOST_FACTORS", (0.5, 2.0)):
            got = p.row(p.sweep(only=["t.below"], record=False), "t.below")
        self.assertEqual((got.verdict.unqualified, got.verdict.outcome), ("", "pass"))
        self.assertEqual(got.verdict.measured, 0.09)

    def test_a_gate_keyed_to_a_bands_ratio_is_unqualified(self):
        """Scaling both ends of a band keeps their ratio; each end moves alone."""
        p = review_project(self)
        got = p.row(p.sweep(only=["t.ratio"]), "t.ratio")
        self.assertEqual(verdicts.parse_token(got.verdict.unqualified),
                         ("goalpost:moves", "c6"))
        entry = verdicts.read_controls(p.root, "t.ratio")[0]
        ends = [run["end"] for group in entry.good["goalpost"] for run in group["runs"]]
        self.assertEqual(sorted(set(ends)), ["limit", "limit_hi"])
        self.assertEqual(len(ends), 2 * len(gates.GOALPOST_FACTORS))

    def test_a_stray_upper_limit_on_a_one_sided_claim_is_shape(self):
        """``"limit_hi": 0.0`` on a ``<=`` claim: invisible to ``render`` and
        ``holds``, so it is the claim's shape, never a limit a run moves."""
        p = review_project(self)
        got = p.row(p.sweep(only=["t.upper"]), "t.upper")
        self.assertEqual(verdicts.parse_token(got.verdict.unqualified),
                         ("channels:ledger", "acceptance-shape:c7"))
        self.assertEqual(p.composed()["c7"].status, ClaimStatus.UNCLAIMED)

    def test_a_shape_that_drops_every_limit_would_read_it_qualified(self):
        """The violator: P2.4's shape (both limits left out, whatever the
        comparator) — c7 at a value made up."""
        def p24_shape(form):
            return {k: v for k, v in form.items() if k not in ("limit", "limit_hi")}
        p = review_project(self)
        with mock.patch.object(verdicts, "_shape_form", p24_shape):
            got = p.row(p.sweep(only=["t.upper"], record=False), "t.upper")
        self.assertEqual((got.verdict.unqualified, got.verdict.outcome), ("", "pass"))

    def test_a_limit_its_controls_never_stated_is_shape(self):
        """Its controls state c11 with NO limit — the read raises, the gate
        catches it and is honest — and the live c11 states one: the read now
        returns, and the gate lies. A stated limit is shape (moving a missing
        limit moves nothing, so the runs cannot show it)."""
        p = review_project(self)
        got = p.row(p.sweep(only=["t.nolimit"]), "t.nolimit")
        self.assertEqual(verdicts.parse_token(got.verdict.unqualified),
                         ("channels:ledger", "acceptance-shape:c11"))

    def test_a_shape_blind_to_a_stated_limit_would_read_it_qualified(self):
        """The violator: P2.4's shape, both limits left out, so a limit's
        presence is in no key a reader holds."""
        def p24_shape(form):
            return {k: v for k, v in form.items() if k not in ("limit", "limit_hi")}
        p = review_project(self)
        with mock.patch.object(verdicts, "_shape_form", p24_shape):
            got = p.row(p.sweep(only=["t.nolimit"], record=False), "t.nolimit")
        self.assertEqual((got.verdict.unqualified, got.verdict.outcome), ("", "pass"))

    def test_one_condition_where_its_controls_had_two_is_shape(self):
        """Its controls tag two claims of different limits ``twin`` — the read
        raises, the gate catches it — and the live two agree: one condition,
        and the gate lies. How many conditions a read returns is shape."""
        p = review_project(self)
        got = p.row(p.sweep(only=["t.twin"]), "t.twin")
        self.assertEqual(verdicts.parse_token(got.verdict.unqualified),
                         ("channels:ledger", "acceptance-shape:twin"))

    def test_a_goalpost_read_only_where_the_walk_goes_is_moved_there(self):
        """A goalpost the known-good design never reads — read past a branch the
        walk takes — is moved at the walk run that read it."""
        p = review_project(self)
        got = p.row(p.sweep(only=["t.offpath"]), "t.offpath")
        self.assertEqual(verdicts.parse_token(got.verdict.unqualified),
                         ("goalpost:moves", "c8"))
        entry = verdicts.read_controls(p.root, "t.offpath")[0]
        [group] = entry.good["goalpost"]
        self.assertGreater(group["measured"], 0.5, "moved at the walk's design, past 0.5")

    def test_the_exemption_without_its_runs_would_read_it_qualified(self):
        """The violator: every goalpost exempt, moved or not, and no site but
        the known-good run's — the made-up value counts."""
        p = review_project(self)
        real = gates.note_goalpost_sites

        def known_good_only(into, run, params, verdict):
            if into is run:
                real(into, run, params, verdict)
        every = lambda control: frozenset(          # noqa: E731
            k[len(verdicts.ACCEPTANCE_KEY):] for k in
            ((getattr(control, "good", None) or {}).get("reads") or {}).get("ledger") or {}
            if k.startswith(verdicts.ACCEPTANCE_KEY))
        with mock.patch.object(gates, "note_goalpost_sites", known_good_only), \
                mock.patch.object(verdicts, "moved_goalposts", every):
            got = p.row(p.sweep(only=["t.offpath"], record=False), "t.offpath")
        self.assertEqual((got.verdict.unqualified, got.verdict.outcome), ("", "pass"))


# --------------------------------------------------------------------------- #
# review of P2.4 — one gate's entry never stops the check run
# --------------------------------------------------------------------------- #
#: The review's evaluators: keyed where x0.5 and x2 never look (``t.below``,
#: ``t.ratio``, ``t.upper``), keyed off the known-good design's path
#: (``t.offpath``), and two honest ones — a goalpost read by regime
#: (``t.regime``) and one that reads none (``t.other``).
REVIEW_GATES = '''\
"""Planted by tests/test_goalposts.py (review of P2.4)."""
from nopekit.gates import gate
from nopekit.models import NegativeControl, Verdict
from nopekit.util import NopekitError

_BAD = NegativeControl(fixture="selftest/bad.py:thin", note="quarter thickness")


def _honest(gate_id, ctx, acc):
    m = round(float(ctx.params["deflection"]), 4)
    return Verdict(gate=gate_id, passed=acc.holds(m), measured=m, limit=acc.limit,
                   units="mm", comparator=acc.comparator.value)


def _made_up(gate_id, acc, value):
    return Verdict(gate=gate_id, passed=True, measured=round(value, 4), limit=acc.limit,
                   units="mm", comparator=acc.comparator.value)


@gate(id="t.below", claims=["c5"], settles="tip deflection", negative_control=_BAD)
def below(ctx):
    acc = ctx.acceptance("c5")
    if acc.limit < 0.2:
        return _made_up("t.below", acc, acc.limit * 0.9)
    return _honest("t.below", ctx, acc)


@gate(id="t.ratio", claims=["c6"], settles="tip deflection", negative_control=_BAD)
def ratio(ctx):
    acc = ctx.acceptance("c6")
    if abs(acc.limit_hi / acc.limit - 4.0) > 1e-9:
        return _made_up("t.ratio", acc, (acc.limit + acc.limit_hi) / 2.0)
    return _honest("t.ratio", ctx, acc)


@gate(id="t.upper", claims=["c7"], settles="tip deflection", negative_control=_BAD)
def upper(ctx):
    acc = ctx.acceptance("c7")
    if acc.limit_hi is not None:
        return _made_up("t.upper", acc, acc.limit * 0.9)
    return _honest("t.upper", ctx, acc)


@gate(id="t.offpath", claims=["c8"], settles="tip deflection", negative_control=_BAD)
def offpath(ctx):
    d = float(ctx.params["deflection"])
    if d <= 0.5:
        return Verdict(gate="t.offpath", passed=True, measured=round(d, 4), limit=0.5,
                       units="mm", comparator="<=")
    acc = ctx.acceptance("c8")
    if acc.limit < 0.2:
        return _made_up("t.offpath", acc, acc.limit * 0.9)
    return _honest("t.offpath", ctx, acc)


@gate(id="t.nolimit", claims=["c11"], settles="tip deflection", negative_control=_BAD)
def nolimit(ctx):
    try:
        acc = ctx.acceptance("c11")
    except NopekitError:
        # its controls state c11 with no limit: honest against a fallback
        m = round(float(ctx.params["deflection"]), 4)
        return Verdict(gate="t.nolimit", passed=m <= 0.5, measured=m, limit=0.5,
                       units="mm", comparator="<=")
    return _made_up("t.nolimit", acc, acc.limit * 0.9)


@gate(id="t.twin", claims=["c12"], settles="tip deflection", negative_control=_BAD)
def twin(ctx):
    try:
        acc = ctx.acceptance("twin")
    except NopekitError:
        # its controls tag two claims of different limits "twin": honest
        m = round(float(ctx.params["deflection"]), 4)
        return Verdict(gate="t.twin", passed=m <= 0.5, measured=m, limit=0.5,
                       units="mm", comparator="<=")
    return _made_up("t.twin", acc, acc.limit * 0.9)


@gate(id="t.regime", claims=["c9"], settles="tip deflection", negative_control=_BAD)
def regime(ctx):
    load = float(ctx.params["load_n"])
    return _honest("t.regime", ctx, ctx.acceptance("c_light" if load <= 20.0 else "c_heavy"))


@gate(id="t.other", claims=["c10"], settles="tip deflection", negative_control=_BAD)
def other(ctx):
    m = round(float(ctx.params["deflection"]), 4)
    return Verdict(gate="t.other", passed=m <= 0.7, measured=m, limit=0.7, units="mm",
                   comparator="<=")
'''


def band_claim(cid: str, lo: float, hi: float) -> dict:
    record = goal_claim(cid, lo)
    record["acceptance"].update(comparator="between", limit_hi=hi)
    return record


def review_project(case: _env.EnvCase) -> _projects.Planted:
    """The live design at 7.0 mm (0.656 mm), its claims moved where the keyed
    evaluators lie; the known-good design states each as calibrated."""
    upper = goal_claim("c7")
    upper["acceptance"]["limit_hi"] = 0.0
    unlimited = goal_claim("c11")
    unlimited["acceptance"]["limit"] = None

    def tagged(cid, limit):
        record = goal_claim(cid, limit)
        record["tags"] = ["twin"]
        return record
    live = {"c5": goal_claim("c5", 0.1), "c6": band_claim("c6", 0.1, 0.2), "c7": upper,
            "c8": goal_claim("c8", 0.1), "c9": goal_claim("c9", 0.8),
            "c10": goal_claim("c10", 0.7), "c_light": goal_claim("c_light", 0.5),
            "c_heavy": goal_claim("c_heavy", 0.8), "c11": goal_claim("c11", 0.1),
            "c12": tagged("c12", 0.1), "c13": tagged("c13", 0.1)}
    known = [claim_row("c5", goal_claim("c5")), claim_row("c6", band_claim("c6", 0.2, 0.8)),
             claim_row("c7", goal_claim("c7")), claim_row("c8", goal_claim("c8")),
             claim_row("c_light", goal_claim("c_light", 0.5)),
             claim_row("c_heavy", goal_claim("c_heavy", 0.8)),
             claim_row("c11", unlimited),
             claim_row("c12", tagged("c12", 0.5)), claim_row("c13", tagged("c13", 0.6))]
    root = _projects.plant_project(
        os.path.join(case.tmp(), "review"),
        {"model/m.py": GOAL_MODEL, "gates/g.py": REVIEW_GATES, "selftest/bad.py": GOAL_FIXTURES},
        claims=live, known_good_params=KG_PARAMS, known_good_claims=known)
    return _projects.Planted(root, live, now=NOW)


class OneEntryNeverStopsTheCheckRun(_env.EnvCase):
    """What slipped through P2.4 (review): the goalpost runs moved only what the
    known-good run read, before the walk, while the strict reader required runs
    for every goalpost the folded trace held — a walk run's included. An honest
    evaluator reading its goalpost by regime made its writer refuse its own
    entry, and every ``check`` of the project raised (exit 2): nothing recorded
    for any evaluator, while ``status`` and ``doctor`` promised the next check
    run would qualify it."""

    def test_an_honest_gate_reading_its_goalpost_by_regime_qualifies(self):
        p = review_project(self)
        result = p.sweep(only=["t.regime", "t.other"])
        regime = p.row(result, "t.regime").verdict
        self.assertEqual((regime.unqualified, regime.outcome), ("", "fail"))
        self.assertEqual(p.row(result, "t.other").verdict.unqualified, "")
        entry = verdicts.read_controls(p.root, "t.regime")[0]
        moved = sorted(group["key"] for group in entry.good["goalpost"])
        self.assertEqual(moved, ["c_heavy", "c_light"], "the walk's goalpost is moved too")

    def test_a_refused_entry_holds_that_gate_alone(self):
        """A writer/reader disagreement — planted: P2.4's rule, runs for every
        goalpost the folded trace read, beside a writer that moves only the
        known-good run's — holds ``t.regime`` (``control:unwritable``) and the
        check run goes on."""
        real = verdicts._problem_in_control

        def p24_rule(data):
            read = {k[len(verdicts.ACCEPTANCE_KEY):] for k, digest in
                    ((data.get("good") or {}).get("reads") or {}).get("ledger", {}).items()
                    if k.startswith(verdicts.ACCEPTANCE_KEY) and digest != verdicts.ABSENT}
            moved = {g["key"] for g in (data.get("good") or {}).get("goalpost") or ()}
            if data.get("mutation") is not None and read - moved:
                return f"good.goalpost does not move {sorted(read - moved)[0]!r}"
            return real(data)
        real_sites = gates.note_goalpost_sites

        def known_good_only(into, run, params, verdict):
            if into is run:
                real_sites(into, run, params, verdict)
        p = review_project(self)
        with mock.patch.object(verdicts, "_problem_in_control", p24_rule), \
                mock.patch.object(gates, "note_goalpost_sites", known_good_only):
            result = p.sweep(only=["t.regime", "t.other"])
        self.assertEqual(verdicts.parse_token(p.row(result, "t.regime").verdict.unqualified)[0],
                         "control:unwritable")
        self.assertEqual(p.row(result, "t.other").verdict.unqualified, "")
        self.assertIn("qualification could not be recorded",
                      report.qualification_reason(
                          p.row(result, "t.regime").verdict.unqualified))

    def test_without_the_hold_the_whole_check_run_stops(self):
        """The violator: the same disagreement with nothing asked before the
        write — the check run raises, and no evaluator gets a verdict."""
        real = verdicts._problem_in_control

        def p24_rule(data):
            read = {k[len(verdicts.ACCEPTANCE_KEY):] for k, digest in
                    ((data.get("good") or {}).get("reads") or {}).get("ledger", {}).items()
                    if k.startswith(verdicts.ACCEPTANCE_KEY) and digest != verdicts.ABSENT}
            moved = {g["key"] for g in (data.get("good") or {}).get("goalpost") or ()}
            if data.get("mutation") is not None and read - moved:
                return f"good.goalpost does not move {sorted(read - moved)[0]!r}"
            return real(data)
        real_sites = gates.note_goalpost_sites

        def known_good_only(into, run, params, verdict):
            if into is run:
                real_sites(into, run, params, verdict)
        p = review_project(self)
        with mock.patch.object(verdicts, "_problem_in_control", p24_rule), \
                mock.patch.object(gates, "note_goalpost_sites", known_good_only), \
                mock.patch.object(verdicts, "entry_problem", lambda entry: ""):
            with self.assertRaises(NopekitError):
                p.sweep(only=["t.regime", "t.other"])


# --------------------------------------------------------------------------- #
# review of P2.4 — a malformed limit or goalpost row is named, never a traceback
# --------------------------------------------------------------------------- #
class AMalformedLimitIsNamed(_env.EnvCase):
    """``"limit_hi": "0.8"`` read, and from P2.4 the comparison judged a value
    against it: ``holds`` raised TypeError out of ``compose`` in `check`,
    `status` and `report`. And ``"goalpost": 5`` in a control entry raised
    TypeError out of the strict reader in `check`, `status` and `doctor`."""

    def test_the_claim_file_is_refused_naming_the_key(self):
        root = os.path.join(self.tmp(), "p")
        record = band_claim("c1", 0.1, 0.8)
        record["acceptance"]["limit_hi"] = "0.8"
        _projects.write_claims(root, {"c1": record})
        with self.assertRaises(NopekitError) as caught:
            store.read_record(os.path.join(root, "claims", "c1.json"), "claims")
        self.assertIn('"acceptance.limit_hi" must be a number or null', str(caught.exception))
        record["acceptance"]["limit_hi"] = 0.8
        _projects.write_claims(root, {"c1": record})
        store.read_record(os.path.join(root, "claims", "c1.json"), "claims")

    def test_an_in_memory_claim_is_not_compared_and_its_read_errors(self):
        claim = Claim(id="c1", statement="c1",
                      acceptance=Acceptance(quantity="tip deflection",
                                            comparator=Comparator.BETWEEN, limit=0.1,
                                            limit_hi="0.8", units="mm"))
        verdict = Verdict(gate="t.own", passed=True, measured=0.4, units="mm",
                          settles="tip deflection", limit=1.0, comparator="<=")
        self.assertEqual(claims.cross_check(claim, verdict), ("not-compared", "no-limit"))
        found = verdicts.acceptance_of([claim], "c1")
        self.assertIsNone(found.condition)
        self.assertIn("limit_hi that is not a finite number", found.problem)
        claim.acceptance = dataclasses.replace(claim.acceptance, limit_hi=0.8)
        self.assertEqual(claims.cross_check(claim, verdict).state, "holds")

    def test_a_scalar_goalpost_row_is_refused_never_raised(self):
        project = bracket(self)
        [path] = glob.glob(os.path.join(project, ".nopekit", "verdicts",
                                        "bracket.deflection", "control-*.json"))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for junk in (5, True, 1.5, "x"):
            with self.subTest(junk=junk):
                bad = copy.deepcopy(data)
                bad["good"]["goalpost"] = junk
                self.assertEqual(verdicts._problem_in_control(bad),
                                 "good.goalpost must be a list")

    def test_p24s_first_rows_read_and_move_nothing(self):
        """An entry P2.4's first cut wrote — one flat row per run — is read, never
        refused (it would name every such file in `doctor`), and it exempts no
        limit: it records no run in today's shape."""
        project = bracket(self)
        [path] = glob.glob(os.path.join(project, ".nopekit", "verdicts",
                                        "bracket.deflection", "control-*.json"))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(verdicts.moved_goalposts(type("E", (), {"good": data["good"]})()),
                         frozenset({"C1"}), "today's entry moves C1 in full")
        data["good"]["goalpost"] = [
            {"key": "C1", "limit": 0.25, "outcome": "fail", "measured": 0.4688, "units": "mm"},
            {"key": "C1", "limit": 1.0, "outcome": "pass", "measured": 0.4688, "units": "mm"}]
        self.assertEqual(verdicts._problem_in_control(data), "")
        self.assertEqual(verdicts.moved_goalposts(type("E", (), {"good": data["good"]})()),
                         frozenset())



# --------------------------------------------------------------------------- #
# review of P2.4 — an evaluator states its own units (invariant 4)
# --------------------------------------------------------------------------- #
#: Where a gate is TAUGHT or SHIPPED: every gate module of the reference project
#: and the bundled packs, and the documents and docstring that show the pattern.
UNIT_SOURCES = (
    *sorted(glob.glob(os.path.join(_env.REPO, "examples", "*", "gates", "*.py"))),
    *sorted(glob.glob(os.path.join(_env.REPO, "packs", "*", "gates", "*.py"))),
    os.path.join(_env.REPO, "docs", "PACK_FORMAT.md"),
    *sorted(glob.glob(os.path.join(_env.REPO, "skills", "*", "SKILL.md"))),
    os.path.join(_env.REPO, "src", "nopekit", "gates.py"),
)


def echoed_units(text: str) -> list[str]:
    """Each ``units=<name>.units`` where ``<name>`` holds a ``ctx.acceptance(...)``
    read: the gate repeating the claim's units back, so the units check that
    holds its pass (``gates._held_to_acceptances``) and the comparison
    (``claims.cross_check``) can never fire."""
    names = set(re.findall(r"(\w+)\s*=\s*ctx\.acceptance\(", text))
    return [m.group(0) for name in sorted(names)
            for m in re.finditer(rf"units\s*=\s*{re.escape(name)}\.units\b", text)]


class AnEvaluatorStatesItsOwnUnits(_env.EnvCase):
    """What slipped through P2.4 (review): the documented pattern — the bracket's
    deflection gate, PACK_FORMAT's example and ``GateContext.acceptance``'s
    docstring — reported ``units=acc.units``. C1 restated as ``<= 600 um``, with
    its known-good copy in ``um`` (the shape rule lets only the limit differ),
    read Checked at "0.6997 um": 700 um against 600. A gate states the units its
    own arithmetic computes in; a claim in others is then a pass in other units,
    which ``run_gate`` errors."""

    def test_no_shipped_or_taught_gate_echoes_the_claims_units(self):
        texts = {}
        for path in UNIT_SOURCES:
            with open(path, encoding="utf-8") as fh:
                texts[os.path.relpath(path, _env.REPO)] = fh.read()
        found = {rel: echoed_units(text) for rel, text in texts.items() if echoed_units(text)}
        self.assertEqual(found, {})
        self.assertGreaterEqual(sum(1 for text in texts.values() if "ctx.acceptance(" in text),
                                3, "the scan reads the gate, the document and the docstring")

    def test_the_echo_is_what_the_scan_finds(self):
        planted = ('    acc = ctx.acceptance("C1")\n'
                   '    return Verdict(gate="g", passed=acc.holds(m), measured=m,\n'
                   '                   limit=acc.limit, units=acc.units)\n')
        self.assertEqual(echoed_units(planted), ["units=acc.units"])
        self.assertEqual(echoed_units(planted.replace("units=acc.units", 'units="mm"')), [])

    def test_a_claim_in_other_units_is_never_checked(self):
        """C1 at ``<= 600 um`` with the known-good C1 in ``um``: the deflection
        gate's known-good pass is in ``mm``, errored — C1 reads Gap, never
        Checked. The violator: the gate echoing ``acc.units`` reads Checked."""
        project = bracket(self)
        edit_claim(project, "C1", limit=600.0, units="um")
        edit_file(project, "selftest/known_good.py", '"limit": 0.5,\n                    '
                  '"units": "mm"}', '"limit": 0.5,\n                    "units": "um"}')
        run(self, project, "check", code=1)
        key, cause = statuses(self, project)["C1"]
        self.assertNotEqual(key, "checked")
        self.assertEqual((key, cause), ("gap", "unqualified"))
        edit_file(project, "gates/structural.py",
                  'units="mm",\n        comparator=acc.comparator.value,',
                  'units=acc.units,\n        comparator=acc.comparator.value,')
        run(self, project, "check", code=1)
        self.assertEqual(statuses(self, project)["C1"], ("checked", "checked"),
                         "the echo reads Checked at 700 um against 600 (violator)")


# --------------------------------------------------------------------------- #
# review of P2.4 — S-18: every bracket gate judges what it reports
# --------------------------------------------------------------------------- #
def judged_unreported(source: str) -> list[str]:
    """Each ``Verdict(...)`` call in ``source`` whose ``measured=`` is not a plain
    name the ``passed=`` expression judges, or whose ``limit=`` is a name that
    ``passed=`` does not judge — a gate that compares one number and reports
    another (S-18: a FAIL reading ``15.00 MPa`` beside an allowable of 15.0)."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Verdict"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        if "passed" not in kw or "measured" not in kw:
            continue
        judged = {n.id for n in ast.walk(kw["passed"]) if isinstance(n, ast.Name)}
        gate = getattr(kw.get("gate"), "value", "?")
        measured, limit = kw["measured"], kw.get("limit")
        if not (isinstance(measured, ast.Name) and measured.id in judged):
            out.append(f"{gate}: measured={ast.unparse(measured)}")
        if isinstance(limit, ast.Name) and limit.id not in judged:
            out.append(f"{gate}: limit={ast.unparse(limit)}")
        elif isinstance(limit, ast.Call):
            out.append(f"{gate}: limit={ast.unparse(limit)}")
    return out


class TheBracketJudgesWhatItReports(unittest.TestCase):
    """S-18, as P2.4 marked it closed: its review found five of the bracket's six
    gates judging the unrounded value beside the rounded one they reported —
    load_n 1155.03 (bearing stress 15.0004 MPa) printed ``[FAIL] 15.00 MPa …
    allowable 15.0 MPa``, a margin that ``disagrees``. Agents copy the reference
    project, and the skill now says to round, then judge."""

    def test_every_bracket_gate_judges_the_numbers_it_reports(self):
        with open(os.path.join(BRACKET, "gates", "structural.py"), encoding="utf-8") as fh:
            self.assertEqual(judged_unreported(fh.read()), [])

    def test_the_old_bearing_gate_is_what_the_check_finds(self):
        old = ('Verdict(gate="bracket.bearing", passed=b <= allow, measured=round(b, 3),\n'
               '        limit=round(allow, 3), units="MPa", comparator="<=")\n')
        self.assertEqual(judged_unreported(old),
                         ["bracket.bearing: measured=round(b, 3)",
                          "bracket.bearing: limit=round(allow, 3)"])

    def test_at_the_boundary_the_flag_agrees_with_the_value(self):
        registry = gates.Registry()
        gates.load_project_gates(BRACKET, registry)
        _spec, fn = registry.get("bracket.bearing")
        ctx = gates.GateContext(root=BRACKET, params={
            "bearing_stress": 15.0004, "design_stress": 15.0, "bearing_area": 77.0,
            "config": {"n_bolts": 2}})
        verdict = fn(ctx)
        self.assertEqual((verdict.measured, verdict.limit, verdict.passed), (15.0, 15.0, True))
        self.assertEqual(claims.margin(verdict), (0.0, ""))


# --------------------------------------------------------------------------- #
# review of P2.4 — a Failing claim says why, beside its own limit
# --------------------------------------------------------------------------- #
class AFailingComparisonSaysWhyBesideItsOwnLimit(_env.EnvCase):
    """What slipped through P2.4 (review), on the claims P2.4 made Failing by
    comparison: `claim show C3` printed ``[FAIL ] C3`` over one evaluator line,
    ``[ok  ] bracket.bearing : 0.19 MPa …``, with no line saying the acceptance
    condition was not met; and the page summarised Failing C3 as ``0.195 MPa /
    15 MPa`` — its evaluator's own limit — against a claim of ``<= 0.1 MPa``."""

    def test_claim_show_and_the_page_say_the_claims_side(self):
        project = bracket(self)
        edit_claim(project, "C3", limit=0.1)
        run(self, project, "check", code=1)
        shown = run(self, project, "claim", "show", "C3", code=0).stdout.splitlines()
        self.assertTrue(shown[0].startswith("[FAIL ] C3"), shown[0])
        self.assertRegex(shown[1], T.ACCEPTANCE_REASON)
        self.assertTrue(shown[1].startswith("  acceptance condition not met: bracket.bearing : "),
                        shown[1])
        self.assertIn("C3's bearing stress <= 0.1 MPa", shown[1])
        run(self, project, "site", "init", code=0)
        run(self, project, "site", "build", code=0)
        with open(os.path.join(project, "site", "data", "state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        rows = {row["id"]: row for row in state["claims"]}
        self.assertEqual(rows["C3"]["limit_text"], "0.1 MPa")
        self.assertEqual(rows["C3"]["compared"], ["bracket.bearing"])
        with open(os.path.join(project, "site", "lib", "panels.js"), encoding="utf-8") as fh:
            panels = fh.read()
        summary = panels.split("const summary = ", 1)[1].split("return el(", 1)[0]
        self.assertIn("claim.limit_text", summary)
        self.assertNotIn("headline.limit", summary, "the verdict's own limit beside the claim's value")

    def test_the_limit_words(self):
        self.assertEqual(report.limit_words(Acceptance(quantity="x", limit=0.5, units="mm")),
                         "0.5 mm")
        self.assertEqual(report.limit_words(Acceptance(
            quantity="x", comparator=Comparator.BETWEEN, limit=0.2, limit_hi=0.8, units="mm")),
            "0.2..0.8 mm")
        self.assertEqual(report.limit_words(Acceptance(quantity="x")), "")


class AShapeReasonSaysWhatToState(unittest.TestCase):
    """What slipped through P2.4 (review): a claim restated in other units read
    `unqualified: bracket.deflection : its check run read ledger
    acceptance-shape:C1 at a value no qualification run read: hand its
    known-good design the same claims` — a spine key, and advice that, taken
    literally, copies the live claim into CLAIMS: the coupling D2 measured as
    wrong (the live C1 at 0.4 read `known-good fail`)."""

    def test_an_acceptance_key_reads_as_the_claims_condition(self):
        said = report.qualification_reason("channels:ledger|acceptance-shape:C1")
        self.assertNotIn("acceptance-shape:", said)
        self.assertIn("the acceptance condition of C1", said)
        self.assertIn("selftest/known_good.py CLAIMS", said)
        self.assertIn("keeping the limit it was calibrated at", said)
        both = report.qualification_reason("channels:ledger|acceptance-shape:c3,acceptance:c3")
        self.assertEqual(both.count("c3"), 2, "one sentence, the claim named in it twice")

    def test_another_ledger_key_keeps_its_words(self):
        said = report.qualification_reason("channels:ledger|claims,acceptance-shape:C1")
        self.assertIn("the acceptance condition of C1", said)
        self.assertIn("its check run read ledger claims at a value no qualification run read",
                      said)
