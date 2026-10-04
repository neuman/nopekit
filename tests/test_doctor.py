# SPDX-License-Identifier: Apache-2.0
"""`doctor` names what rho cannot see, one row per kind, and reads no run history.

Per-gate content addressing (checkpoint 1.2) keys a verdict by what its gate
read. Some things a gate depends on no trace can key, and some records the cache
holds say something is wrong without making any verdict wrong. None of them
changes a claim's status on its own, so `check` and `status` cannot be where a
human learns of them; `doctor` is. Each row below exists because something got
past a check without it:

* **instruments** — an entry recorded under another library version stays
  current (instruments are provenance, never rho, Q1.3); the difference is still
  a fact about this machine, and nothing else prints it next to its gate.
* **opaque-inputs** — a gate that read through a channel the tracer cannot see
  (a subprocess's own reads, a file outside the project) is never served from the
  cache; it re-runs every check and reads stale between checks. Silent, that
  looks like a slow gate, not a blind one.
* **cache-entries** — an entry whose bytes do not match their digest is ignored
  and the gate re-runs. A hand edit that quietly stopped counting is exactly the
  edit its author believes worked.
* **two-outcomes** — one gate, identical inputs, two answers: a warning while
  `verdicts.TWO_OUTCOMES_IS_ERROR` is False, a failure once it is True; two
  control outcomes at one rho_control fail always (the gate is not admitted).
* **sealed-fixtures** — invariant 5 at runtime (`packs.seal_findings`): a pack
  control that reads its host's params passes in some projects and fails in
  others. The known-bad input must come from the pack's own baseline.
* **imports** — a gate that imports a third-party module it does not declare is
  run where the module is missing and errors, instead of reading SKIPPED.
* **env-reads** — an environment variable is never a cache key (spec §8): a read
  while the gate runs names the entry opaque (`env:<NAME>`), so it re-runs on every
  check, and a module-level read at import, before any window, is seen by nothing
  at all. A static scan is the only thing that sees both.
* **memos** — a module-level memo outlives the gate that filled it: the first
  gate to ask opens the file, every later one gets the value and opens nothing,
  and no entry keys what it served. functools' memos the spine empties before
  every run (``modelio.clear_caches``); a dict filled from a function body, a
  ``global`` rebound from one, a mutable default written into, it cannot — only
  a static scan sees them.
* **dynamic-imports** — a module a VALUE names (``importlib.import_module(name)``,
  ``__import__(f"...")``) is in no recorded closure: only the run that first loads
  it in a process keys its source, and a later one is served it from
  ``sys.modules`` and keys nothing (admission review, round 2, c). A literal name
  is keyed with the gate's code and is not named; the scan covers the gate's code
  and its owner's ``selftest/``, where its control's fixtures live.
* **code-digest** — a gate registered from Python, with no recorded closure, is
  keyed by its defining file; a value its function closes over is not seen.
* **pending-controls** — a control whose fixture code moved still counts until
  the next check re-verifies it by its values; the human should know it is
  pending.
* **orphan-entries** — cached verdicts of gates this project no longer
  registers read stale and never count. A warning, not an integrity failure:
  nothing is corrupt, something was uninstalled.

Every row is tested with a planted trigger AND a clean counterpart: a detector
that has never refused anything is a logger, and one that refuses everything is
noise. The static detectors (imports, env-reads, memos, dynamic-imports) are also
measured on every bundled gate first (R-4): zero hits.

Run:  PYTHONPATH=src python3 -m unittest tests.test_doctor -v
"""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _env
import _projects
from atompipe import cli as cli_mod
from atompipe import gates as gates_mod
from atompipe import packs as packs_mod
from atompipe import verdicts
from atompipe.models import GateSpec, NegativeControl, Tier, Verdict

#: The rows this file holds `doctor` to, by the name each prints.
ROWS = ("instruments", "opaque-inputs", "cache-entries", "two-outcomes", "sealed-fixtures",
        "imports", "env-reads", "memos", "dynamic-imports", "code-digest", "pending-controls",
        "orphan-entries", "qualification", "known-good")

#: A fixture every planted project gate can borrow: the bracket's own, which
#: sags the known-good design 30 mm — past any limit the planted gates use.
_FIXTURE = "selftest/bad_configs.py:quarter_thickness"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(project: str, *argv: str):
    return _env.atompipe(list(argv), cwd=project)


def _doctor(project: str) -> tuple[int, dict[str, dict]]:
    """`doctor --json` in a subprocess: ``(exit code, {row name: row})``."""
    proc = _run(project, "doctor", "--json")
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:                      # pragma: no cover - reported
        raise AssertionError(f"doctor --json: {exc}\n{proc.stdout}\n{proc.stderr}")
    return proc.returncode, _by_name(data)


def _by_name(data: dict) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for row in data["checks"]:
        if row["check"] in rows and row["check"] in ROWS:
            raise AssertionError(f"doctor printed {row['check']!r} twice: {data['checks']}")
        rows.setdefault(row["check"], row)
    return rows


def _doctor_in_process(project: str) -> tuple[int, dict[str, dict]]:
    """`doctor --json` through ``cli.main`` in this process — for the rows a test
    must reach through the spine (a patched flag, an in-process gate)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_mod.main(["doctor", "--json", "-C", project])
    try:
        return code, _by_name(json.loads(out.getvalue()))
    except ValueError as exc:                      # pragma: no cover - reported
        raise AssertionError(f"doctor --json: {exc}\n{out.getvalue()}\n{err.getvalue()}")


def _write(project: str, rel: str, text: str) -> str:
    path = os.path.join(project, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _edit(project: str, rel: str, old: str, new: str) -> None:
    """Replace ``old`` (there exactly once) with ``new`` in one project file."""
    path = os.path.join(project, *rel.split("/"))
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert text.count(old) == 1, (rel, old)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace(old, new))


def _entry(project: str, gate_id: str) -> verdicts.Entry:
    found = verdicts.read_entries(project, gate_id)
    if len(found) != 1:
        raise AssertionError(f"{gate_id}: expected one cache entry, found "
                             f"{[e.name for e in found]}")
    return found[0]


def _bundled_registry() -> gates_mod.Registry:
    """Every bundled gate, in a fresh registry."""
    registry = gates_mod.Registry()
    packs_mod.load_all_gates(packs_mod.available(None), registry, None)
    return registry


# --------------------------------------------------------------------------- #
# planted sources
# --------------------------------------------------------------------------- #
_OUTSIDE_GATE = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_doctor.py: a limit read from a file outside the project."""
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict

LIMIT_FILE = {limit_file!r}


@gate(id="bracket.outside", title="a limit kept outside the project", claims=["planted"],
      tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture={fixture!r}, note="planted"))
def outside(ctx):
    """The tip deflection against a limit read from a file nobody tracks."""
    with open(LIMIT_FILE, encoding="utf-8") as fh:
        limit = float(fh.read())
    value = float(ctx.params["deflection"])
    return Verdict(gate="bracket.outside", passed=value <= limit, measured=value,
                   limit=limit, units="mm")
'''

_IMPORT_GATES = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_doctor.py: lazy third-party imports, declared and not."""
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict

_NC = NegativeControl(fixture={fixture!r}, note="planted")


def _unreached():
    """Called by no gate: its import is no gate's dependency."""
    import atompipe_planted_unreached
    return atompipe_planted_unreached


def _measure(ctx):
    """Called by `undeclared`: its import is that gate's dependency."""
    if ctx.tier < 0:
        import atompipe_planted_numerics
        return atompipe_planted_numerics.deflection(ctx)
    return float(ctx.params["deflection"])


@gate(id="bracket.undeclared", title="imports what it does not declare",
      claims=["planted"], tier=Tier.INSTANT, negative_control=_NC)
def undeclared(ctx):
    value = _measure(ctx)
    return Verdict(gate="bracket.undeclared", passed=value <= 5.0, measured=value,
                   limit=5.0, units="mm")


@gate(id="bracket.declared", title="imports what it declares", claims=["planted"],
      tier=Tier.INSTANT, requires_python=["atompipe_planted_declared"], negative_control=_NC)
def declared(ctx):
    import atompipe_planted_declared
    return Verdict(gate="bracket.declared", passed=atompipe_planted_declared.ok(ctx))
'''

_ENV_GATE = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_doctor.py: a gate that takes its limit from the environment."""
import os
from os import getenv as read_env

from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict


@gate(id="bracket.env_limit", title="a limit from the environment", claims=["planted"],
      tier=Tier.INSTANT, negative_control=NegativeControl(fixture={fixture!r}, note="planted"))
def env_limit(ctx):
    limit = float(os.environ.get("PLANTED_LIMIT", "5.0"))
    scale = float(read_env("PLANTED_SCALE") or 1.0)
    value = float(ctx.params["deflection"]) * scale
    return Verdict(gate="bracket.env_limit", passed=value <= limit, measured=value,
                   limit=limit, units="mm")
'''

_DYNAMIC_GATE = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_doctor.py: modules a value names, and literal look-alikes."""
import importlib
from importlib import import_module

from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict

RULES = "json"


@gate(id="bracket.dynamic_limit", title="a limit from a module named at run time",
      claims=["planted"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture={fixture!r}, note="planted"))
def dynamic_limit(ctx):
    importlib.import_module(RULES)
    __import__(f"{{RULES}}")
    importlib.import_module("json")
    import_module("json.decoder")
    value = float(ctx.params["deflection"])
    return Verdict(gate="bracket.dynamic_limit", passed=value <= 5.0, measured=value,
                   limit=5.0, units="mm")
'''

#: A fixture module beside the bracket's, naming its helper by a value: the scan
#: reaches the owner's `selftest/`, where a control's code lives.
_DYNAMIC_FIXTURE = '''\
import importlib


def make(ctx, name="json"):
    importlib.import_module(name)
    return ctx
'''

_MEMO_GATE = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_doctor.py: every module-global memo the spine cannot empty,
and three look-alikes it must not name."""
import functools
import os

from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict

_LIMITS = {{}}
_TABLE = None
_UNITS = {{"mm": 1.0}}
_SCRATCH = {{}}


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return float(fh.read())


def _limit(path):
    if path not in _LIMITS:
        _LIMITS[path] = _read(path)
    return _LIMITS[path]


def _table(path):
    global _TABLE
    if _TABLE is None:
        _TABLE = _read(path)
    return _TABLE


def _once(path, _seen={{}}):
    return _seen.setdefault(path, _read(path))


@functools.lru_cache(maxsize=None)
def _lru(path):
    return _read(path)


def _local(path):
    _SCRATCH = {{}}
    _SCRATCH[path] = _UNITS["mm"]
    return _SCRATCH[path]


@gate(id="bracket.memo_limit", title="a limit behind module-level memos", claims=["planted"],
      tier=Tier.INSTANT, negative_control=NegativeControl(fixture={fixture!r}, note="planted"))
def memo_limit(ctx):
    path = os.path.join(ctx.root, "limit.txt")
    limit = min(_limit(path), _table(path), _once(path), _lru(path), _local(path))
    value = float(ctx.params["deflection"])
    return Verdict(gate="bracket.memo_limit", passed=value <= limit, measured=value,
                   limit=limit, units="mm")
'''

_SEAL_GATES = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_doctor.py: one sealed control, one that reads its host."""
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict


def _span(ctx, gate_id):
    value = ctx.params.get("span_mm")
    if value is None:
        return Verdict(gate=gate_id, skipped=True, skip_reason="no span_mm in the projection")
    return Verdict(gate=gate_id, passed=float(value) <= 100.0, measured=float(value),
                   limit=100.0, units="mm")


@gate(id="planted-seal.sealed", claims=["planted"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:sealed_long", note="planted"))
def sealed(ctx):
    return _span(ctx, "planted-seal.sealed")


@gate(id="planted-seal.leaky", claims=["planted"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:leaky_long", note="planted"))
def leaky(ctx):
    return _span(ctx, "planted-seal.leaky")
'''

_SEAL_FIXTURES = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_doctor.py."""
import dataclasses
import json
import os

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline.json"),
          encoding="utf-8") as _fh:
    BASELINE = json.load(_fh)


def sealed_long(ctx):
    """SEALED: the pack's own baseline, its one input past the limit."""
    params = dict(BASELINE)
    params["span_mm"] = 500.0
    return dataclasses.replace(ctx, params=params)


def leaky_long(ctx):
    """Unsealed: the HOST's params, its one input past the limit."""
    params = dict(ctx.params)
    params["span_mm"] = 500.0
    return dataclasses.replace(ctx, params=params)
'''


def _in_process_gate(ctx):
    """Registered from Python by this test: no recorded closure, so its code is
    keyed by this file."""
    return Verdict(gate="doctor.in_process", passed=True, measured=1.0, limit=2.0)


# --------------------------------------------------------------------------- #
class DoctorNamesWhatRhoCannotSee(_env.EnvCase):
    """One test per row: a planted trigger, and the checked bracket as the clean
    counterpart every row must read ok on."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.base = tempfile.mkdtemp(prefix="atompipe-doctor-")
        cls.addClassCleanup(_env._rmtree, cls.base)
        cls.checked = _projects.bracket_copy(os.path.join(cls.base, "bracket"))
        cls.first = _run(cls.checked, "check")
        cls.clean_code, cls.clean = _doctor(cls.checked)

    def setUp(self) -> None:
        # In-process CLI runs build a fresh registry per command; the module-level
        # default is swapped too, so nothing a test loads can leak into it (§0.4).
        patcher = mock.patch.object(gates_mod, "REGISTRY", gates_mod.Registry())
        patcher.start()
        self.addCleanup(patcher.stop)

    def copy(self) -> str:
        """A copy of the checked bracket, its cache included."""
        dest = os.path.join(self.tmp(), "bracket")
        shutil.copytree(self.checked, dest)
        return dest

    def assertClean(self, name: str) -> None:
        row = self.clean.get(name)
        self.assertIsNotNone(row, f"doctor printed no {name!r} row: {sorted(self.clean)}")
        self.assertEqual(row["status"], "ok", f"{name} on the checked bracket: {row}")

    def assertRow(self, rows: dict, name: str, status: str, *present: str,
                  absent: tuple[str, ...] = ()) -> dict:
        row = rows.get(name)
        self.assertIsNotNone(row, f"doctor printed no {name!r} row: {sorted(rows)}")
        self.assertEqual(row["status"], status, row)
        for text in present:
            self.assertIn(text, row["detail"], row)
        for text in absent:
            self.assertNotIn(text, row["detail"], row)
        return row

    # -- the clean counterpart, all at once --------------------------------- #
    def test_the_checked_bracket_is_clean_on_every_row(self):
        self.assertEqual(self.first.returncode, 1, self.first.stdout + self.first.stderr)
        for name in ROWS:
            with self.subTest(row=name):
                self.assertClean(name)
        self.assertEqual(self.clean_code, 0, self.clean)
        self.assertNotIn("runs", self.clean["layout"]["detail"],
                         "the run history is not a well-known directory any more")

    # -- one row each ------------------------------------------------------- #
    def test_instruments(self):
        project = self.copy()
        entry = _entry(project, "bracket.bed_fit")
        os.remove(entry.path)
        verdicts.write_entry(project, dataclasses.replace(
            entry, instruments={"numpy": "0.0.1-planted"}))
        code, rows = _doctor(project)
        self.assertRow(rows, "instruments", "warn",
                       "bracket.bed_fit — recorded under numpy 0.0.1-planted; here ")
        self.assertEqual(code, 0, "provenance never fails doctor")
        self.assertClean("instruments")

    def test_opaque_inputs(self):
        project = self.copy()
        limit_file = os.path.join(self.tmp(), "limit.txt")
        with open(limit_file, "w", encoding="utf-8") as fh:
            fh.write("5.0\n")
        _write(project, "gates/outside.py",
               _OUTSIDE_GATE.format(limit_file=limit_file, fixture=_FIXTURE))
        self.assertEqual(_run(project, "check").returncode, 1)
        _code, rows = _doctor(project)
        self.assertRow(rows, "opaque-inputs", "warn", "bracket.outside",
                       "file-outside-project", absent=("bracket.deflection",))
        self.assertClean("opaque-inputs")

    def test_cache_entries_names_a_hand_edited_entry(self):
        project = self.copy()
        entry = _entry(project, "bracket.deflection")
        with open(entry.path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(text.count('"passed": false'), 1)
        with open(entry.path, "w", encoding="utf-8") as fh:
            fh.write(text.replace('"passed": false', '"passed": true'))
        code, rows = _doctor(project)
        self.assertRow(rows, "cache-entries", "warn", "hand-edited entry",
                       os.path.basename(entry.path))
        self.assertEqual(code, 0)
        self.assertClean("cache-entries")

    def test_two_outcomes_warn_until_the_flag_flips(self):
        project = self.copy()
        entry = _entry(project, "bracket.bed_fit")
        other = dataclasses.replace(
            entry, verdict={**entry.verdict, "passed": not entry.verdict["passed"]})
        self.assertTrue(verdicts.write_entry(project, other).written)

        code, rows = _doctor(project)
        expected = "FAIL" if verdicts.TWO_OUTCOMES_IS_ERROR else "warn"
        self.assertRow(rows, "two-outcomes", expected, "bracket.bed_fit",
                       "two outcomes recorded for identical inputs")
        self.assertEqual(code, 1 if verdicts.TWO_OUTCOMES_IS_ERROR else 0, rows)

        # The flag decides, read when doctor runs.
        for flag, status, exit_code in ((False, "warn", 0), (True, "FAIL", 1)):
            with self.subTest(TWO_OUTCOMES_IS_ERROR=flag), \
                    mock.patch.object(verdicts, "TWO_OUTCOMES_IS_ERROR", flag):
                code, rows = _doctor_in_process(project)
                self.assertRow(rows, "two-outcomes", status, "bracket.bed_fit")
                self.assertEqual(code, exit_code)
        self.assertClean("two-outcomes")

    def test_two_control_outcomes_always_fail(self):
        project = self.copy()
        controls = verdicts.read_controls(project, "bracket.bed_fit")
        self.assertEqual(len(controls), 1, [c.name for c in controls])
        # R-6 (P2.3): no walk where the known-bad control passed — nothing is
        # walked then, and the strict writer refuses an entry that says one was.
        passed_its_bad = dataclasses.replace(controls[0], bad="pass", admitted="no",
                                             mutation=None)
        self.assertTrue(verdicts.write_control(project, passed_its_bad).written)
        with mock.patch.object(verdicts, "TWO_OUTCOMES_IS_ERROR", False):
            code, rows = _doctor_in_process(project)
        self.assertRow(rows, "two-outcomes", "FAIL", "bracket.bed_fit",
                       "two control outcomes recorded for identical inputs")
        self.assertEqual(code, 1)

    def test_sealed_fixtures(self):
        project = self.copy()
        pack = os.path.join(project, ".atompipe", "packs", "planted-seal")
        _write(pack, "pack.json", json.dumps({"name": "planted-seal"}) + "\n")
        _write(pack, "gates/span.py", _SEAL_GATES)
        _write(pack, "selftest/bad.py", _SEAL_FIXTURES)
        _write(pack, "selftest/baseline.json", json.dumps({"span_mm": 50.0}) + "\n")
        added = _run(project, "packs", "add", "planted-seal")
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)

        code, rows = _doctor(project)
        self.assertRow(rows, "sealed-fixtures", "FAIL", "planted-seal.leaky",
                       "selftest/bad.py:leaky_long", absent=("planted-seal.sealed",))
        self.assertEqual(code, 1, "an unsealed pack control breaks invariant 5")
        self.assertClean("sealed-fixtures")

    def test_imports(self):
        project = self.copy()
        _write(project, "gates/planted_imports.py", _IMPORT_GATES.format(fixture=_FIXTURE))
        code, rows = _doctor(project)
        self.assertRow(rows, "imports", "warn", "bracket.undeclared",
                       "atompipe_planted_numerics",
                       absent=("bracket.declared", "atompipe_planted_declared",
                               "atompipe_planted_unreached"))
        self.assertEqual(code, 0)
        self.assertClean("imports")

    def test_no_bundled_gate_imports_what_it_does_not_declare(self):
        """R-4: the detector measured on every bundled gate, zero hits. A rule
        that took a module's imports as every gate's would name cad.bounding,
        the one tier-0 gate of a module whose other gates import trimesh
        lazily — a warning nobody could act on."""
        registry = _bundled_registry()
        self.assertGreaterEqual(len(registry.ids()), 50, "the bundled corpus did not load")
        self.assertEqual(cli_mod._undeclared_imports(registry), [])

    def test_env_reads(self):
        project = self.copy()
        _write(project, "gates/planted_env.py", _ENV_GATE.format(fixture=_FIXTURE))
        code, rows = _doctor(project)
        row = self.assertRow(rows, "env-reads", "warn", "gates/planted_env.py:",
                             "os.environ", "os.getenv", "bracket.env_limit",
                             absent=("structural.py",))
        self.assertEqual(code, 0, row)
        self.assertClean("env-reads")

    def test_no_bundled_gate_reads_the_environment(self):
        """R-4 (spec §3.17): zero bundled hits."""
        registry = _bundled_registry()
        self.assertGreaterEqual(len(registry.ids()), 50, "the bundled corpus did not load")
        self.assertEqual(cli_mod._env_reads(registry, _env.REPO), [])

    def test_memos(self):
        project = self.copy()
        _write(project, "gates/planted_memo.py", _MEMO_GATE.format(fixture=_FIXTURE))
        code, rows = _doctor(project)
        row = self.assertRow(rows, "memos", "warn", "gates/planted_memo.py:",
                             " _LIMITS (bracket.memo_limit)", " _TABLE (bracket.memo_limit)",
                             " _seen (bracket.memo_limit)", "ctx.load_file",
                             absent=("_lru", "_UNITS", "_SCRATCH", "structural.py"))
        self.assertEqual(code, 0, row)
        self.assertClean("memos")

    def test_no_bundled_gate_keeps_a_memo_the_spine_cannot_empty(self):
        """R-4: zero bundled hits, measured before the row landed."""
        registry = _bundled_registry()
        self.assertGreaterEqual(len(registry.ids()), 50, "the bundled corpus did not load")
        self.assertEqual(cli_mod._memo_reads(registry, _env.REPO), [])

    def test_dynamic_imports(self):
        project = self.copy()
        _write(project, "gates/planted_dynamic.py", _DYNAMIC_GATE.format(fixture=_FIXTURE))
        _write(project, "selftest/planted_dynamic.py", _DYNAMIC_FIXTURE)
        code, rows = _doctor(project)
        row = self.assertRow(rows, "dynamic-imports", "warn",
                             "gates/planted_dynamic.py:", "importlib.import_module(RULES)",
                             "__import__(f'{RULES}')", "(bracket.dynamic_limit)",
                             "selftest/planted_dynamic.py:5 importlib.import_module(name)",
                             "load_path",
                             absent=("'json'", "json.decoder", "structural.py",
                                     "bad_configs.py", "known_good.py"))
        self.assertEqual(code, 0, row)
        self.assertClean("dynamic-imports")

    def test_no_bundled_gate_imports_a_module_a_value_names(self):
        """R-4: zero bundled hits, measured before the row landed (openmodelica's
        ``__import__("re")`` is a literal, and is not one)."""
        registry = _bundled_registry()
        self.assertGreaterEqual(len(registry.ids()), 50, "the bundled corpus did not load")
        self.assertEqual(cli_mod._dynamic_imports(registry, _env.REPO), [])

    def test_code_digest_names_a_gate_keyed_by_its_defining_file(self):
        project = self.copy()
        spec = GateSpec(id="doctor.in_process", title="registered from Python",
                        claims=["planted"], tier=Tier.INSTANT,
                        negative_control=NegativeControl(fixture=_FIXTURE, note="planted"))
        original = cli_mod._registry

        def with_one_more(root, ledger, *, strict=True):
            registry, problems = original(root, ledger, strict=strict)
            registry.register(spec, _in_process_gate)
            return registry, problems

        with mock.patch.object(cli_mod, "_registry", with_one_more):
            _code, rows = _doctor_in_process(project)
        self.assertRow(rows, "code-digest", "warn", "doctor.in_process", "defining file",
                       absent=("bracket.",))
        self.assertClean("code-digest")

    def test_pending_controls(self):
        project = self.copy()
        with open(os.path.join(project, "model", "bracket.py"), "a", encoding="utf-8") as fh:
            fh.write("\n# a comment: the bytes moved, no value did\n")
        _code, rows = _doctor(project)
        # R-6, words only: GLOSSARY §9's "to re-qualify … the next check run
        # re-qualifies them" from P2.1, where it said "pending" (Open's word)
        # and "re-verifies"; the row still names the file that moved.
        self.assertRow(rows, "pending-controls", "warn", "to re-qualify", "model/bracket.py",
                       "the next check run re-qualifies them")
        self.assertClean("pending-controls")

        # The next check re-verifies them by their values, and the row is clean.
        self.assertEqual(_run(project, "check").returncode, 1)
        _code, rows = _doctor(project)
        self.assertRow(rows, "pending-controls", "ok")

    def test_qualification(self):
        """(V16) An unqualified evaluator is named, with what does not hold, in
        GLOSSARY §2's words — never *admitted* or *re-verified*."""
        project = self.copy()
        _write(project, "gates/zz_never.py", f'''
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="bracket.never", claims=["never"],
      negative_control=NegativeControl(fixture="{_FIXTURE}"))
def never(ctx):
    d = float(ctx.params["deflection"])
    return Verdict(gate="bracket.never", passed=False, measured=d, limit=0.5)
''')
        _run(project, "check")
        _code, rows = _doctor(project)
        row = self.assertRow(rows, "qualification", "FAIL", "1 evaluator(s) unqualified",
                             "bracket.never (known-good fail)")
        for word in ("admitted", "re-verif", "negative control"):
            self.assertNotIn(word, row["detail"])
        self.assertClean("qualification")
        self.assertIn("every evaluator qualified at its version",
                      self.clean["qualification"]["detail"])

    def test_known_good(self):
        """(V16) A project with evaluators of its own and no known-good control:
        once per project, with the fix — every such claim reads Gap until it
        exists. Planted: a doctor that does not name it."""
        project = self.copy()
        os.remove(os.path.join(project, "selftest", "known_good.py"))
        _code, rows = _doctor(project)
        self.assertRow(rows, "known-good", "FAIL", "6 project evaluator(s) with no known-good "
                       "control", "bracket.deflection", "known-good not run", "context(ctx)")
        self.assertClean("known-good")

    def test_known_good_counts_a_declared_good_fixture(self):
        """Review of P2.3, ``p3``: a project whose every gate declares ``good=``
        and has no ``selftest/known_good.py`` — `check` ready, every claim
        Checked — failed doctor, exit 1, "every project evaluator reads
        known-good not run". D4 resolves ``good=`` first; so does the row."""
        project = self.copy()
        selftest = os.path.join(project, "selftest")
        os.rename(os.path.join(selftest, "known_good.py"), os.path.join(selftest, "good.py"))
        _edit(project, "selftest/bad_configs.py", '"known_good.py"))', '"good.py"))')
        gates_py = os.path.join(project, "gates", "structural.py")
        with open(gates_py, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(text.count("negative_control=NegativeControl("), 6)
        with open(gates_py, "w", encoding="utf-8") as fh:
            fh.write(text.replace("negative_control=NegativeControl(",
                                  'negative_control=NegativeControl(good="selftest/good.py:'
                                  'context", '))
        _run(project, "check")
        code, rows = _doctor(project)
        self.assertRow(rows, "known-good", "ok", "every project evaluator has a known-good "
                       "control")
        self.assertEqual(rows["qualification"]["status"], "ok", rows["qualification"])
        with open(gates_py, "w", encoding="utf-8") as fh:
            fh.write(text.replace("negative_control=NegativeControl(",
                                  'negative_control=NegativeControl(good="selftest/good.py:'
                                  'context", ', 5))
        _code, rows = _doctor(project)
        self.assertRow(rows, "known-good", "FAIL", "1 project evaluator(s) with no known-good "
                       "control")

    def test_qualification_warns_when_every_evaluator_is_not_yet_qualified_here(self):
        """Review of P2.3, ``b3``: after a code edit every evaluator was
        undemonstrated — `gate show` "not yet qualified at this version" — while
        doctor said "every evaluator qualified at its version"."""
        project = self.copy()
        path = os.path.join(project, "gates", "structural.py")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n# an edit\n")
        code, rows = _doctor(project)
        self.assertRow(rows, "qualification", "warn", "not yet qualified at their version",
                       "bracket.deflection", "the next check run qualifies it",
                       absent=("every evaluator qualified", "unqualified"))
        self.assertEqual(code, 0, "a warning, never a problem: the next check run settles it")

    def test_a_pruned_dependent_is_promised_its_root_never_the_next_check(self):
        """Review of P2.3: a dependent its prerequisite prunes is never qualified
        by the next check run, which prunes it again — `gate show` and doctor
        promised it was."""
        project = self.copy()
        for gid in ("bracket.deflection", "bracket.bending_stress"):
            shutil.rmtree(os.path.join(project, ".atompipe", "verdicts", gid))
        _edit(project, "model/bracket.py", "thickness: float = 7.0", "thickness: float = 14.0")
        _run(project, "check")
        _run(project, "check")
        _code, rows = _doctor(project)
        once = "it qualifies once bracket.model_validity is established"
        self.assertRow(rows, "qualification", "warn", once,
                       absent=("the next check run qualifies it",))
        shown = _run(project, "gate", "show", "bracket.deflection").stdout
        self.assertIn(once, shown)
        self.assertNotIn("the next check run qualifies it", shown)

    def test_orphan_entries_warn_and_do_not_fail_integrity(self):
        project = self.copy()
        entry = _entry(project, "bracket.bed_fit")
        retired = dataclasses.replace(entry, gate="bracket.retired",
                                      verdict={**entry.verdict})
        self.assertTrue(verdicts.write_entry(project, retired).written)
        code, rows = _doctor(project)
        self.assertRow(rows, "orphan-entries", "warn", "bracket.retired")
        self.assertRow(rows, "ledger-integrity", "ok")
        self.assertEqual(code, 0, "an uninstalled gate's verdicts are not corruption")
        self.assertClean("orphan-entries")


if __name__ == "__main__":
    unittest.main()
