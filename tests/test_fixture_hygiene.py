# SPDX-License-Identifier: Apache-2.0
"""Fixture hygiene: controls built from a design that passes, details with no clock.

A content-addressed verdict cache made three old habits load-bearing, and each
class here pins one of them shut:

* **KnownGoodPassesEverything** — ``examples/bracket/selftest/known_good.py`` is
  the bracket's known-good design: thickness 8.0, every other Config field
  stated, 0.469 mm of deflection, all six gates passing — whatever the live
  bracket, or any host, looks like. P2.3 reuses it as the good half of every
  control, so a "known-good" that quietly failed a gate would make every good
  half fail with it.
* **BracketControlsAreSealed** — each control's input is a pure function of the
  known-good design and its one change. What slipped through (S-07, S-19):
  ``bad_configs._with`` rebuilt every known-bad input from the HOST's
  ``ctx.params["config"]``, so an identity fixture was reported "correctly
  failed … ~64x worse" because the live bracket already fails, and a host with
  a big enough bed defused ``oversized`` outright. Invariant 5 (SEALED) says
  exactly this about packs; it now holds for the reference project too. A
  second cost, the one that made it P1 work (D-27): the host read was a
  whole-value dependency, so every Config edit would change all six control
  inputs, re-run all six controls and write six tracked control entries.
* **DetailHasNoWallClock** — a verdict's ``detail`` is part of the bytes a cache
  entry is written as. openmodelica put ``in 0.9s`` into it (S-34, D-29): two
  runs on identical inputs, two different entries, an add/add conflict on every
  branch that ran the gate, and a false "nondeterministic" flag on every
  ``check --force``. ``duration_s`` already carries the time. The scan that
  holds this has a planted violator it must catch, including the indirect
  spelling (a duration formatted into a variable first).
* **ProjectsHelpers** — ``tests/_projects.py``, the two project builders the
  later checkpoints' tests need (E4's pack rows, the R-8 oracle): a bracket copy
  and a pack baseline wrapped as a legacy project. A builder is a fixture, and a
  fixture nobody checked is where a vacuous test hides.
* **TheBracketIsCopiedAsAClone** — a test copies the bracket as a clone holds
  it (``_projects.bracket_copy``), never by ``copytree`` of the checkout. What
  slipped through (P2.3's gate): a ``check`` running in the bracket while the
  suite ran put its live ``build.lock`` into a test's copy, and the copy's own
  ``check`` refused to start. The scan has planted violators it must catch.

Every in-process test works on a TEMP COPY of the bracket, never the tracked
tree: loading a fixture through the stock import system writes ``__pycache__``
beside it, and the suite never writes into the checkout (G5).

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_fixture_hygiene.py -v
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import os
import re
import shutil
import socket
import textwrap
import unittest

import _env
import _projects
import test_fresh_clone
from atompipe import gates, modelio, packs, verdicts
from atompipe.models import Acceptance, Claim, Ledger, Verdict

REPO = _env.REPO
MODELICA = os.path.join(REPO, "packs", "openmodelica", "gates", "modelica.py")

#: The bracket's six gates, in the order `gates/structural.py` declares them.
BRACKET_GATES = ("bracket.deflection", "bracket.bending_stress", "bracket.bearing",
                 "bracket.model_validity", "bracket.bed_fit", "bracket.min_wall")

#: A host that disagrees with the bracket about everything. Every field is moved
#: off the model default (asserted, so a default edit cannot quietly make one
#: field agree), and `bed_xy` 1000 is the value that defused `oversized` when
#: controls were built on the host: a 400 mm arm fits a 1000 mm bed.
WILD_CONFIG = {"arm_length": 250.0, "width": 5.0, "thickness": 20.0, "hole_d": 2.0,
               "n_bolts": 7, "edge_margin": 1.0, "load_n": 900.0, "safety_factor": 9.0,
               "material": "alu6061", "nozzle_d": 0.25, "bed_xy": 1000.0, "brim_mm": 0.0}

#: What each control changes relative to `known_good.CONFIG` — nothing else may
#: move. `quarter_thickness` is a quarter of the known-good 8.0; it was 1.75, a
#: quarter of the LIVE 7.0, and on the known-good design 1.75 would be 95x worse
#: where the gate's note says ~64x — a false note is S-07's shape.
EXPECTED_CHANGES = {
    "bracket.deflection": {"thickness": 2.0},
    "bracket.bending_stress": {"load_n": 300.0},
    "bracket.bearing": {"n_bolts": 1, "hole_d": 10.0, "thickness": 1.5, "load_n": 400.0},
    "bracket.model_validity": {"arm_length": 20.0},
    "bracket.bed_fit": {"arm_length": 400.0},
    "bracket.min_wall": {"nozzle_d": 1.2, "thickness": 3.0},
}

#: A duration rendered into text the way openmodelica did: `in 0.9s`, `in 12s`.
DURATION_TEXT = re.compile(r"\bin \d+(?:\.\d+)?\s?s\b")

#: How many `detail` sinks the scan must find in modelica.py before its silence
#: means anything. It builds 17 today (measured when this landed); 10 leaves room
#: for a refactor that merges a few, and is far above the 2 that carried the
#: clock — a scan that stopped recognising `Verdict(detail=...)` cannot pass.
#: Rejected: `> 0`, which one surviving `detail =` assignment would satisfy.
MODELICA_MIN_SINKS = 10


# --------------------------------------------------------------------------- #
# the bracket, loaded from a copy
# --------------------------------------------------------------------------- #
def _salted(prefix: str, path: str) -> str:
    return f"{prefix}_{hashlib.sha256(os.path.abspath(path).encode()).hexdigest()[:12]}"


def _registry(root: str) -> gates.Registry:
    """The copy's six gates in a fresh registry — never `gates.REGISTRY`."""
    registry = gates.Registry()
    path = os.path.join(root, "gates", "structural.py")
    with gates.use_registry(registry):
        modelio.load_source_module(path, name=_salted("test_fixture_hygiene_gates", path),
                                   roots=[root], registry=registry)
    return registry


def _known_good(root: str):
    return modelio.load_path(os.path.join(root, "selftest", "known_good.py"))


def _model(root: str):
    return modelio.load_path(os.path.join(root, "model", "bracket.py"))


def _flat(bracket, config: dict) -> dict:
    flat, _conflicts = modelio.flat_params(
        {"config": dict(config), "derived": bracket.build(bracket.Config(**config))})
    return flat


def _host_ledger() -> Ledger:
    """A host ledger a sealed control must never hand its gate."""
    ledger = Ledger()
    ledger.claims.append(Claim(id="C99", statement="the host's own claim",
                               acceptance=Acceptance(quantity="x", limit=1.0)))
    ledger.verdicts.append(Verdict(gate="bracket.deflection", passed=True))
    return ledger


class _BracketCase(_env.EnvCase):
    """A bracket copy per test, its gates, and three hosts: empty, live, wild."""

    def setUp(self):
        self.root = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"))
        self.out = self.tmp()
        self.registry = _registry(self.root)
        self.assertEqual(sorted(self.registry.ids()), sorted(BRACKET_GATES),
                         "the copy's gates are not the six this file is about")
        self.bracket = _model(self.root)
        defaults = dataclasses.asdict(self.bracket.Config())
        self.assertEqual(set(WILD_CONFIG), set(defaults), "WILD_CONFIG must state every field")
        same = sorted(k for k in WILD_CONFIG if WILD_CONFIG[k] == defaults[k])
        self.assertEqual(same, [], f"the wild host agrees with the model on {same}")
        loaded = modelio.load_model(self.root, "model/bracket.py")
        live, _ = modelio.flat_params(modelio.project(loaded))
        self.hosts = {
            "empty": self._ctx({}),
            "live": self._ctx(live),
            "wild": self._ctx(_flat(self.bracket, WILD_CONFIG)),
        }

    def _ctx(self, params: dict) -> gates.GateContext:
        return gates.GateContext(root=self.root, ledger=_host_ledger(), model=None,
                                 params=params, out_dir=self.out, tier=3,
                                 log=lambda _m: None, extra={"host_only": True})

    def pair(self, gate_id: str):
        entry = self.registry.get(gate_id)
        self.assertIsNotNone(entry, gate_id)
        return entry

    def fixture(self, gate_id: str):
        spec, _fn = self.pair(gate_id)
        return gates.load_fixture(spec.negative_control.fixture, self.root)


# --------------------------------------------------------------------------- #
class KnownGoodPassesEverything(_BracketCase):
    """`selftest/known_good.py`: a design every bracket gate passes, pinned."""

    def test_config_states_every_field(self):
        known_good = _known_good(self.root)
        fields = {f.name for f in dataclasses.fields(self.bracket.Config)}
        self.assertEqual(set(known_good.CONFIG), fields,
                         "known_good.CONFIG must state every Config field, or a model "
                         "default edit moves the known-good design with it")
        self.assertEqual(known_good.CONFIG["thickness"], 8.0)
        self.assertEqual(known_good.CONFIG["bed_xy"], 220.0)

    def test_every_gate_passes_on_it_whatever_the_host(self):
        known_good = _known_good(self.root)
        for name, host in self.hosts.items():
            ctx = known_good.context(host)
            self.assertEqual(ctx.root, self.root)
            self.assertEqual(ctx.ledger.claims, [], f"{name}: the host's ledger leaked")
            self.assertEqual(ctx.extra, {}, f"{name}: the host's extra leaked")
            for gate_id in BRACKET_GATES:
                verdict = gates.run_gate(*self.pair(gate_id), ctx)
                self.assertTrue(verdict.ok, f"{gate_id} on the known-good design under the "
                                            f"{name} host: {verdict.detail or verdict.error}")

    def test_the_docstring_number_is_the_number(self):
        known_good = _known_good(self.root)
        params = known_good.params()
        self.assertEqual(f"{params['deflection']:.3f}", "0.469")
        doc = known_good.__doc__ or ""
        for needle in ("0.469 mm", "all six", "P2.3"):
            self.assertIn(needle, doc, f"known_good's docstring does not say {needle!r}")

    def test_it_is_the_shape_check_builds(self):
        """The same flat params `check` hands a gate for the bracket at 8.0 — top-level
        config keys included, which the raw `build()` dict lacked (packs:H19)."""
        known_good = _known_good(self.root)
        at_eight = _projects.bracket_copy(os.path.join(self.tmp(), "at-eight"), thickness=8.0)
        loaded = modelio.load_model(at_eight, "model/bracket.py")
        flat, conflicts = modelio.flat_params(modelio.project(loaded))
        self.assertEqual(conflicts, [])
        self.assertEqual(known_good.params(), flat)
        self.assertEqual(known_good.context(self.hosts["empty"]).params, flat)

    def test_a_model_default_edit_does_not_move_it(self):
        edited = _projects.bracket_copy(os.path.join(self.tmp(), "edited"))
        path = os.path.join(edited, "model", "bracket.py")
        with open(path, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
        self.assertEqual(text.count("    bed_xy: float = 220.0\n"), 1)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text.replace("    bed_xy: float = 220.0\n", "    bed_xy: float = 250.0\n"))
        before, after = _known_good(self.root), _known_good(edited)
        # Non-vacuous: the edited copy's known-good really builds through the
        # edited model. Two copies sharing one `bracket` module by name would make
        # this test pass for the wrong reason.
        self.assertEqual(after.bracket.Config().bed_xy, 250.0)
        self.assertEqual(before.bracket.Config().bed_xy, 220.0)
        self.assertEqual(verdicts.digest_value(after.params()),
                         verdicts.digest_value(before.params()),
                         "a model default edit moved the known-good design")


# --------------------------------------------------------------------------- #
class BracketControlsAreSealed(_BracketCase):
    """Each control is the known-good design plus its one change — and nothing of the host."""

    def test_control_params_are_the_same_under_every_host(self):
        for gate_id in BRACKET_GATES:
            make = self.fixture(gate_id)
            digests = {name: verdicts.digest_value(dict(make(host).params))
                       for name, host in self.hosts.items()}
            self.assertEqual(len(set(digests.values())), 1,
                             f"{gate_id}'s control depends on its host: {digests}")

    def test_no_control_reads_its_host(self):
        """Through the seal detector's own input: a traced, writable host view."""
        for gate_id in BRACKET_GATES:
            spec, fn = self.pair(gate_id)
            make = self.fixture(gate_id)
            trace = verdicts.GateTrace(kind="control")
            control = make(verdicts.traced_context(self.hosts["live"], trace, readonly=False))
            fn(verdicts.traced_context(control, trace))
            self.assertEqual(trace.host_reads, {},
                             f"{gate_id}'s control read its host: {sorted(trace.host_reads)}")
            self.assertTrue(trace.params, f"{gate_id}: the trace saw no gate reads at all, "
                                          f"so the empty host-read set proves nothing")
            self.assertEqual(control.ledger.claims, [], f"{gate_id}: host ledger in the control")
            self.assertEqual(control.extra, {}, f"{gate_id}: host extra in the control")
            self.assertEqual(control.root, self.root)

    def test_each_control_makes_its_one_change(self):
        known_good = _known_good(self.root)
        for gate_id, expected in EXPECTED_CHANGES.items():
            control = self.fixture(gate_id)(self.hosts["wild"])
            config = control.params["config"]
            moved = {k: config[k] for k in config if config[k] != known_good.CONFIG[k]}
            self.assertEqual(moved, expected, f"{gate_id}'s control changes {moved}")
            # The flat shape `check` builds: every config field at the top level too.
            for key, value in config.items():
                self.assertEqual(control.params[key], value, f"{gate_id}: params[{key!r}]")

    def test_every_control_fires_under_every_host(self):
        for name, host in self.hosts.items():
            for gate_id in BRACKET_GATES:
                spec, fn = self.pair(gate_id)
                verdict = gates.selftest(spec, fn, host)
                self.assertTrue(verdict.ok, f"{gate_id}'s control did not fire under the "
                                            f"{name} host: {verdict.detail or verdict.error}")

    def test_gate_selftest_fires_all_six(self):
        proc = _env.atompipe(["gate", "selftest", "--json"], cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])
        counts = json.loads(proc.stdout)["counts"]
        self.assertEqual((counts["controls"], counts["fired"], counts["broken"]), (6, 6, 0),
                         counts)


# --------------------------------------------------------------------------- #
#: Attribute names that ARE a measured cost: a detail formatting one is a
#: detail that differs between two runs on identical inputs.
_COST_ATTRS = frozenset({"duration_s", "cpu_s"})
#: Callables that read a clock. `time()` bare covers `from time import time`.
_CLOCK_CALLS = frozenset({"time", "time_ns", "perf_counter", "perf_counter_ns",
                          "monotonic", "monotonic_ns", "process_time", "process_time_ns",
                          "now", "utcnow", "today"})


def _reads_clock(node: ast.AST, tainted: set[str]) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in _COST_ATTRS:
            return True
        if isinstance(sub, ast.Name) and (sub.id in tainted or sub.id in _COST_ATTRS):
            return True
        if isinstance(sub, ast.Call):
            func = sub.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _CLOCK_CALLS:
                return True
    return False


def _assigned_names(node: ast.AST) -> list[str]:
    targets = []
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
        targets = [node.target]
    return [t.id for target in targets for t in ast.walk(target) if isinstance(t, ast.Name)]


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _own_nodes(scope: ast.AST):
    """The nodes of one scope, not descending into a nested function (its own
    scope, scanned on its own): a `took` tainted in one gate function must not
    taint an unrelated `took` in the next."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def clock_in_detail(source: str, filename: str = "<source>") -> tuple[list[str], int]:
    """``(hits, sinks)``: every ``detail`` built from a cost or a clock read, as
    ``<file>:<line>``, and how many ``detail`` sinks the scan looked at.

    A sink is a ``detail=`` keyword, an assignment to ``detail`` or to
    ``<x>.detail``. A name counts as a clock read when it was assigned from one,
    transitively, anywhere in the same function — ``took = f"in {run.duration_s}s"``
    and later ``detail=why + took`` is the same defect in two lines.
    """
    tree = ast.parse(source, filename=filename)
    scopes = [tree] + [n for n in ast.walk(tree) if isinstance(n, _SCOPES)]
    hits: set[str] = set()
    sinks = 0
    for scope in scopes:
        nodes = list(_own_nodes(scope))
        tainted: set[str] = set()
        while True:
            grew = False
            for node in nodes:
                value = getattr(node, "value", None)
                if value is None or not _assigned_names(node):
                    continue
                if _reads_clock(value, tainted):
                    new = set(_assigned_names(node)) - tainted
                    if new:
                        tainted |= new
                        grew = True
            if not grew:
                break
        for node in nodes:
            values = []
            if isinstance(node, ast.Call):
                values = [k.value for k in node.keywords if k.arg == "detail"]
            elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any((isinstance(t, ast.Name) and t.id == "detail")
                       or (isinstance(t, ast.Attribute) and t.attr == "detail")
                       for t in targets) and node.value is not None:
                    values = [node.value]
            for value in values:
                sinks += 1
                if _reads_clock(value, tainted):
                    hits.add(f"{filename}:{value.lineno}")
    return sorted(hits), sinks


def _gate_sources() -> list[str]:
    """Every bundled pack's Python outside `selftest/`, and the bracket's gates."""
    found = []
    roots = [os.path.join(REPO, "packs"), os.path.join(_projects.BRACKET, "gates")]
    for top in roots:
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in ("__pycache__", "selftest") and not d.startswith("."))
            found.extend(os.path.join(dirpath, n) for n in sorted(filenames) if n.endswith(".py"))
    return found


class DetailHasNoWallClock(_env.EnvCase):
    """No verdict detail carries a duration or a clock read (S-34, D-29)."""

    def test_no_bundled_gate_formats_a_clock_into_its_detail(self):
        files = _gate_sources()
        self.assertIn(MODELICA, files)
        found = []
        for path in files:
            with open(path, "r", encoding="utf-8") as fh:
                hits, sinks = clock_in_detail(fh.read(), os.path.relpath(path, REPO))
            found.extend(hits)
            if path == MODELICA:
                # Non-vacuous: the scan saw modelica.py's details, including the
                # two that carried the clock.
                self.assertGreaterEqual(sinks, MODELICA_MIN_SINKS,
                                        f"only {sinks} detail sinks in modelica.py")
        self.assertEqual(found, [], "a verdict detail is built from a duration or a clock "
                                    "read; duration_s already carries the time")

    def test_the_scan_catches_every_spelling(self):
        planted = {
            "the line that shipped": """
                def compiles(ctx):
                    return Verdict(gate=gid, passed=passed,
                                   detail=(f"{why} in {run.duration_s:.1f}s"
                                           + (f"; {errs}" if dirty else "")))
            """,
            "through a variable": """
                def simulates(ctx):
                    took = f" in {run.duration_s:.1f}s"
                    tail = why + took
                    return Verdict(gate=gid, passed=True, detail=tail)
            """,
            "a clock call": """
                import time
                def g(ctx):
                    t0 = time.perf_counter()
                    v = Verdict(gate=gid, passed=True)
                    v.detail = f"{time.perf_counter() - t0:.2f}s"
                    return v
            """,
            "an assignment named detail": """
                def g(ctx):
                    detail = f"took {verdict.cpu_s}"
                    return Verdict(gate=gid, passed=True, detail=detail)
            """,
        }
        for name, source in planted.items():
            hits, _ = clock_in_detail(textwrap.dedent(source))
            self.assertTrue(hits, f"the scan missed {name}")
        clean = textwrap.dedent("""
            def g(ctx):
                log(f"ran in {run.duration_s:.1f}s")
                return Verdict(gate=gid, passed=True, detail=f"built {name}")
        """)
        self.assertEqual(clock_in_detail(clean)[0], [],
                         "a duration logged, not put in the detail, is not a defect")

    def test_two_runs_give_identical_details(self):
        """With omc: two runs of each omc gate on the pack's own baseline, in two
        different work directories, say the same thing. Without it: the honest
        outcome is an availability skip, never a pass (R-7's shape)."""
        registry = gates.Registry()
        packs.load_gates("openmodelica", registry, root=REPO,
                         include_env=False, include_user=False)
        pack_dir = os.path.join(REPO, "packs", "openmodelica")
        for gate_id in ("modelica.compiles", "modelica.simulates"):
            entry = registry.get(gate_id)
            self.assertIsNotNone(entry, gate_id)
            spec, fn = entry
            runs = [gates.run_gate(spec, fn, packs.baseline_context(pack_dir, out_dir=self.tmp()))
                    for _ in range(2)]
            if not gates.availability(spec)[0]:
                for verdict in runs:
                    self.assertTrue(verdict.skipped and not verdict.ok,
                                    f"{gate_id} without omc: {verdict.outcome}")
                continue
            for verdict in runs:
                self.assertTrue(verdict.ok, f"{gate_id}: {verdict.detail or verdict.error}")
                self.assertIsNone(DURATION_TEXT.search(verdict.detail),
                                  f"{gate_id}'s detail carries a duration: {verdict.detail!r}")
            self.assertEqual(runs[0].detail, runs[1].detail, gate_id)


# --------------------------------------------------------------------------- #
def _listing(root: str) -> dict[str, bytes]:
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root).replace(os.sep, "/")] = fh.read()
    return out


class ProjectsHelpers(_env.EnvCase):
    """`tests/_projects.py` builds what it says, under today's spine."""

    def test_bracket_copy_holds_what_a_clone_holds(self):
        root = _projects.bracket_copy(os.path.join(self.tmp(), "b"), migrated=True)
        _source, files = _projects.test_fresh_clone.bracket_listing()
        self.assertEqual(sorted(_listing(root)), sorted(files))
        self.assertFalse(any("__pycache__" in f or f.endswith(".pyc") for f in files))
        with open(os.path.join(_projects.BRACKET, "model", "bracket.py"), "rb") as fh:
            self.assertEqual(_listing(root)["model/bracket.py"], fh.read())

    def test_the_default_copy_is_the_bracket_before_its_migration(self):
        """Today's sources around the legacy state, and nothing the migration wrote:
        the project every test before 1.3 was written against (U32)."""
        root = _projects.bracket_copy(os.path.join(self.tmp(), "b"))
        got = _listing(root)
        _source, files = _projects.test_fresh_clone.bracket_listing()
        sources = {f for f in files if f.startswith(("model/", "gates/", "selftest/"))}
        self.assertTrue(sources, "the listing holds none of the bracket's sources")
        for rel in sources:
            with open(os.path.join(_projects.BRACKET, *rel.split("/")), "rb") as fh:
                self.assertEqual(got.get(rel), fh.read(), rel)
        for fixture, rel in _projects.LEGACY_BRACKET_FILES:
            with open(os.path.join(_projects.LEGACY_BRACKET, *fixture.split("/")), "rb") as fh:
                self.assertEqual(got.get(rel), fh.read(), rel)
        written = sorted(rel for rel in got if rel in (".atompipe/project.json", ".gitignore",
                                                        ".gitattributes")
                         or rel.startswith(("claims/", "params/", ".atompipe/verdicts/")))
        self.assertEqual(written, [], "a legacy copy carries what the migration wrote")
        self.assertIsNotNone(json.loads(got[".atompipe/ledger.json"]).get("claims"))
        # A state file nobody decided about is refused, never silently copied or dropped.
        with self.assertRaises(AssertionError):
            _projects._migrated_path(".atompipe/packs/beam-analytic/pack.json")

    def test_thickness_is_one_line(self):
        root = _projects.bracket_copy(os.path.join(self.tmp(), "b"), thickness=8.0)
        with open(os.path.join(_projects.BRACKET, "model", "bracket.py"), encoding="utf-8") as fh:
            original = fh.read().splitlines()
        with open(os.path.join(root, "model", "bracket.py"), encoding="utf-8") as fh:
            edited = fh.read().splitlines()
        changed = [(a, b) for a, b in zip(original, edited) if a != b]
        self.assertEqual(len(original), len(edited))
        self.assertEqual(changed, [("    thickness: float = 7.0", "    thickness: float = 8.0")])
        # A model where the edit would be ambiguous is refused, not half-edited.
        path = os.path.join(root, "model", "bracket.py")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\nclass Other:\n    thickness: float = 3.0\n")
        with self.assertRaises(AssertionError):
            _projects.set_thickness(root, 9.0)

    def test_the_bracket_at_eight_passes_all_six(self):
        root = _projects.bracket_copy(os.path.join(self.tmp(), "b"), thickness=8.0)
        proc = _env.atompipe(["check", "--tier", "3", "--json"], cwd=root)
        rows = {row["gate"]: row for row in json.loads(proc.stdout)["verdicts"]}
        self.assertEqual(sorted(rows), sorted(BRACKET_GATES), proc.stderr[-2000:])
        failing = sorted(g for g, row in rows.items() if not row["ok"])
        self.assertEqual(failing, [], "the bracket at 8.0 is the known-good design")

    def test_git_copy_is_one_clean_commit(self):
        if shutil.which("git") is None:
            self.skipTest("git is not on PATH")
        root = _projects.bracket_copy(os.path.join(self.tmp(), "b"), thickness=8.0, git=True)
        self.assertTrue(os.path.isdir(os.path.join(root, ".git")))
        status = _env.git(["status", "--porcelain"], cwd=root)
        self.assertEqual((status.returncode, status.stdout), (0, ""), status.stderr)
        count = _env.git(["rev-list", "--count", "HEAD"], cwd=root)
        self.assertEqual(count.stdout.strip(), "1", count.stderr)

    def test_wrapped_beam_analytic_passes_under_this_spine(self):
        root = _projects.wrap_pack_baseline("beam-analytic", os.path.join(self.tmp(), "w"))
        specs = _projects.pack_gates("beam-analytic")
        proc = _env.atompipe(["check", "--tier", "3", "--json"], cwd=root)
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])
        result = json.loads(proc.stdout)
        rows = {row["gate"]: row for row in result["verdicts"]}
        self.assertEqual(sorted(rows), sorted(s.id for s in specs))
        self.assertEqual(sorted(g for g, row in rows.items() if not row["ok"]), [])
        self.assertEqual(result["blocking"], [])
        self.assertTrue(result["ready"])

    def test_wrapped_project_is_legacy_and_says_what_it_wraps(self):
        pack = "beam-analytic"
        root = _projects.wrap_pack_baseline(pack, os.path.join(self.tmp(), "w"))
        dot = os.path.join(root, ".atompipe")
        self.assertFalse(os.path.exists(os.path.join(dot, "project.json")),
                         "a legacy project has no records-layout marker")
        with open(os.path.join(dot, ".gitignore"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), _projects.LEGACY_GITIGNORE)
        with open(os.path.join(dot, "ledger.json"), encoding="utf-8") as fh:
            ledger = json.load(fh)
        self.assertEqual(ledger["meta"]["packs"], [pack])
        self.assertEqual(ledger["meta"]["model_entry"], "model/wrap.py")
        self.assertEqual(ledger["verdicts"], [])
        specs = _projects.pack_gates(pack)
        self.assertEqual([c["id"] for c in ledger["claims"]],
                         [f"G{n}" for n in range(1, len(specs) + 1)])
        for claim, spec in zip(ledger["claims"], specs):
            self.assertEqual(claim["tags"], list(spec.claims), claim["id"])
            self.assertTrue(claim["critical"])
            self.assertEqual(claim["kind"], "measurable")
        baseline = _projects.pack_baseline(pack)
        model = modelio.load_path(os.path.join(root, "model", "wrap.py"))
        design = model.build(model.CONFIG)
        self.assertEqual(design, {k: v for k, v in baseline.items() if not k.startswith("_")})
        self.assertTrue(any(k.startswith("_") for k in baseline), "nothing was stripped")
        self.assertTrue(os.path.isfile(os.path.join(root, "selftest", "baseline.json")))
        self.assertFalse(any("__pycache__" in f for f in _listing(root)))

    def test_two_wraps_are_byte_identical(self):
        first = _projects.wrap_pack_baseline("openmodelica", os.path.join(self.tmp(), "a"))
        second = _projects.wrap_pack_baseline("openmodelica", os.path.join(self.tmp(), "b"))
        self.assertEqual(_listing(first), _listing(second))

    def test_copy_pack_shadows_the_bundled_pack(self):
        pack = "fdm-print"
        root = _projects.wrap_pack_baseline(pack, os.path.join(self.tmp(), "w"), copy_pack=True)
        local = os.path.join(root, ".atompipe", "packs", pack)
        self.assertEqual(packs.find(pack, root=root, include_env=False, include_user=False),
                         os.path.abspath(local))
        self.assertTrue(os.path.isfile(os.path.join(local, "gates", "mesh.py")))
        self.assertFalse(any("__pycache__" in f or f.endswith(".pyc") for f in _listing(local)))

    def test_the_records_layout_waits_for_it_to_exist(self):
        with self.assertRaises(NotImplementedError):
            _projects.wrap_pack_baseline("beam-analytic", os.path.join(self.tmp(), "w"),
                                         legacy=False)


# --------------------------------------------------------------------------- #
# TheBracketIsCopiedAsAClone
# --------------------------------------------------------------------------- #
#: Callables that copy a whole directory tree. ``copy_tree`` is distutils' and
#: setuptools' spelling of the same thing.
_TREE_COPIERS = frozenset({"copytree", "copy_tree"})

#: The module-level name every test file gives the checkout's bracket
#: (``test_fresh_clone.BRACKET``, ``_projects.BRACKET``, and each file's own).
#: Exact, so ``LEGACY_BRACKET`` (``tests/bracket_legacy``, frozen fixtures that
#: no run ever writes into) is not it.
_BRACKET_NAME = "BRACKET"

#: How many tree copies the scan must recognise across tests/ before its silence
#: means anything. 15 when it landed (2026-10-03: the pack copies, the scratch
#: projects, the spine copy, the R-8 oracle's pristine copy); 10 leaves room for
#: a few to be folded into helpers, and is far above the 0 a scan that stopped
#: recognising `shutil.copytree` would see. Rejected: `> 0`, which one surviving
#: call would satisfy.
_MIN_TREE_COPIES = 10


def _path_parts(node: ast.AST) -> list[ast.AST]:
    """The operands of a path spelled as ``os.path.join(...)`` or ``a / b / c``,
    in order; ``[node]`` for anything else."""
    if isinstance(node, ast.Call) and node.args:
        func = node.func
        called = (func.id if isinstance(func, ast.Name)
                  else func.attr if isinstance(func, ast.Attribute) else "")
        if called in {"join", "joinpath", "Path", "PurePath"}:
            out: list[ast.AST] = []
            for arg in node.args:
                out += _path_parts(arg)
            return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        return _path_parts(node.left) + _path_parts(node.right)
    return [node]


def _names_the_bracket(node: ast.AST, aliases: frozenset[str]) -> bool:
    """Whether ``node`` — or anything inside it — spells the checkout's bracket:
    its ``BRACKET`` name, a local alias of it, ``"examples/bracket"`` in one
    string, or ``"examples"`` then ``"bracket"`` in one joined path."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and (sub.id == _BRACKET_NAME or sub.id in aliases):
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == _BRACKET_NAME:
            return True
        if (isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                and "examples/bracket" in sub.value.replace("\\", "/")):
            return True
        words = [part.value.strip("/\\") for part in _path_parts(sub)
                 if isinstance(part, ast.Constant) and isinstance(part.value, str)]
        if any(a == "examples" and b == "bracket" for a, b in zip(words, words[1:])):
            return True
    return False


def _tree_copy_findings(source: str, filename: str = "<planted>") -> tuple[list[str], int]:
    """``(findings, tree copies seen)``: every whole-tree copy in ``source`` whose
    source is the checkout's bracket, and how many tree copies there were at all."""
    tree = ast.parse(source, filename)
    aliases: set[str] = set()
    while True:                 # a name bound to the bracket, or to such a name, is it too
        grown = set(aliases)
        for node in ast.walk(tree):
            value = getattr(node, "value", None)
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if _names_the_bracket(value, frozenset(grown)):
                    grown |= {t.id for t in targets if isinstance(t, ast.Name)}
        if grown == aliases:
            break
        aliases = grown
    findings: list[str] = []
    seen = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = (func.id if isinstance(func, ast.Name)
                  else func.attr if isinstance(func, ast.Attribute) else "")
        if called not in _TREE_COPIERS:
            continue
        seen += 1
        src = node.args[0] if node.args else next(
            (kw.value for kw in node.keywords if kw.arg == "src"), None)
        if src is not None and _names_the_bracket(src, frozenset(aliases)):
            findings.append(f"{filename}:{node.lineno}: {called}({ast.unparse(src)}, ...)")
    return findings, seen


def _write_lock(project: str) -> str:
    """A build lock in ``project`` as a live `check` on this host leaves it: the pid
    is this test's own process, alive for as long as the test runs, and to the
    copy's `check` — another process — a running build it must not steal from."""
    path = os.path.join(project, ".atompipe", "build.lock")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "host": socket.gethostname(),
                   "when": "2026-10-03T00:00:00Z", "command": "-m atompipe check"}, fh)
    return path


class TheBracketIsCopiedAsAClone(_env.EnvCase):
    """A test that needs the bracket copies it as a clone holds it —
    ``_projects.bracket_copy``, the one listing ``test_fresh_clone`` defines —
    never by copying the checkout's tree.

    What slipped through (P2.3's gate): six tests made their bracket with
    ``shutil.copytree(examples/bracket)``, which carries the checkout's
    untracked state too. The gate ran ``atompipe check`` in the bracket while
    the suite ran; ``test_ci_config``'s "tracked design, unchanged" copied
    ``.atompipe/build.lock`` mid-run, naming a pid alive on this host, and the
    copy's own ``check`` refused to start and exited 2 — red, with no code
    change behind it, and green again on a quiet machine. The same copy carries
    a developer's cache, observations and a stale `out/`: a test whose answer
    depends on what last ran in the checkout is invariant 5's host dependence,
    in the suite. The walk's listing (verify.sh ``--dir``) left the lock in too.
    """

    def test_no_test_copies_the_checkouts_bracket(self):
        findings: list[str] = []
        seen = 0
        for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, "tests")):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(dirpath, filename)
                with open(path, encoding="utf-8") as fh:
                    found, count = _tree_copy_findings(fh.read(), os.path.relpath(path, REPO))
                findings += found
                seen += count
        self.assertGreaterEqual(seen, _MIN_TREE_COPIES,
                                "the scan recognised too few tree copies to mean anything")
        self.assertEqual(
            findings, [],
            "a tree copy of examples/bracket carries whatever last ran there — a live "
            "check's build.lock, its cache and observations, a stale out/ — so the "
            "copy's answer depends on the checkout; use "
            "_projects.bracket_copy(dest, migrated=True): " + "; ".join(findings))

    def test_planted_copies_are_caught(self):
        planted = [
            'shutil.copytree(BRACKET, p, ignore=shutil.ignore_patterns("__pycache__", "out"))\n',
            'shutil.copytree(os.path.join(_env.REPO, "examples", "bracket"), p)\n',
            "from shutil import copytree\ncopytree(_projects.BRACKET, p)\n",
            "import shutil as sh\nsh.copytree(src=test_fresh_clone.BRACKET, dst=p)\n",
            'src = os.path.join(REPO, "examples", "bracket")\nshutil.copytree(src, p)\n',
            'a = os.path.join(REPO, "examples", "bracket")\nb = a\nshutil.copytree(b, p)\n',
            'shutil.copytree(pathlib.Path(REPO) / "examples" / "bracket", p)\n',
            'shutil.copytree(REPO + "/examples/bracket", p)\n',
            'shutil.copytree(os.path.join(BRACKET, ".atompipe"), p)\n',
            "class C:\n    def setUp(self):\n        shutil.copytree(BRACKET, self.p)\n",
            "from distutils.dir_util import copy_tree\ncopy_tree(BRACKET, p)\n",
        ]
        for source in planted:
            with self.subTest(source=source):
                findings, seen = _tree_copy_findings(source)
                self.assertEqual(seen, 1, source)
                self.assertTrue(findings, f"not caught: {source!r}")

    def test_the_sanctioned_routes_are_clean(self):
        clean = [
            '_projects.bracket_copy(os.path.join(self.tmp(), "b"), migrated=True)\n',
            "shutil.copytree(self.project, dest)\n",
            'shutil.copytree(os.path.join(PACKS_DIR, "beam-analytic"), d)\n',
            'shutil.copytree(os.path.join(_projects.LEGACY_BRACKET, "x"), d)\n',
            'shutil.copy2(os.path.join(BRACKET, "model", "bracket.py"), d)\n',
            'open(os.path.join(BRACKET, "model", "bracket.py"))\n',
            'shutil.copytree(os.path.join(REPO, "examples", "other"), d)\n',
        ]
        for source in clean:
            with self.subTest(source=source):
                self.assertEqual(_tree_copy_findings(source)[0], [], source)

    def test_a_live_lock_never_rides_into_a_copy(self):
        """The hazard, and both listings holding it out. A repository with the
        bracket at ``examples/bracket``, committed, then a live run's lock and a
        writer's temp file planted in it as a running `check` leaves them."""
        repo = self.tmp()
        rel = test_fresh_clone.BRACKET_REL
        root = _projects.bracket_copy(os.path.join(repo, *rel.split("/")), migrated=True)
        for argv, identity in ((["-c", "init.defaultBranch=main", "init", "-q"], False),
                               (["add", "-A"], False),
                               (["commit", "-q", "-m", "the bracket"], True)):
            proc = _env.git(argv, cwd=repo, identity=identity)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        _write_lock(root)
        entries = os.path.join(root, ".atompipe", "verdicts", "bracket.deflection")
        with open(os.path.join(entries, ".0123abcd-4567.json.q8w2e4.tmp"), "w",
                  encoding="utf-8") as fh:
            fh.write('{"half": ')
        planted = {".atompipe/build.lock",
                   ".atompipe/verdicts/bracket.deflection/.0123abcd-4567.json.q8w2e4.tmp"}

        # The violator: a copy of the whole tree, as the six tests made theirs.
        carried = os.path.join(self.tmp(), "carried")
        shutil.copytree(root, carried, ignore=shutil.ignore_patterns("__pycache__", "out"))
        refused = _env.atompipe(["check"], cwd=carried)
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("another atompipe run", refused.stderr)

        # Both listings leave the run's state out, and a copy of either checks
        # as the bracket does: its one intended failure, exit 1.
        listings = {"git": test_fresh_clone.git_listing(repo, rel),
                    "walk": test_fresh_clone.walk_listing(root)}
        for source, files in listings.items():
            with self.subTest(listing=source):
                self.assertIsNotNone(files)
                self.assertEqual(sorted(planted & set(files)), [], source)
                self.assertIn("model/bracket.py", files)
                dest = os.path.join(self.tmp(), source)
                for name in files:
                    target = os.path.join(dest, *name.split("/"))
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy2(os.path.join(root, *name.split("/")), target)
                checked = _env.atompipe(["check"], cwd=dest)
                self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
