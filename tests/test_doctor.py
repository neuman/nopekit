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
  others. The known-bad input must come from the pack's own baseline. Only the
  tier-0 controls run by default (`--tier` raises it); above, a control on
  record at its version is read, and one with none is named, never passed over
  (holdout, general packs: the row ran every control at every tier, and
  `doctor` took nine minutes).
* **pack-tags** — a claim tag that gates of two or more installed packs bind:
  a claim that carries it is covered by the gates of both (holdout, general
  packs: `doctor` diffed the param keys, never the tags).
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
from nopekit import cli as cli_mod
from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit import verdicts
from nopekit.models import GateSpec, NegativeControl, Tier, Verdict

#: The rows this file holds `doctor` to, by the name each prints.
ROWS = ("instruments", "opaque-inputs", "cache-entries", "two-outcomes", "sealed-fixtures",
        "imports", "env-reads", "memos", "dynamic-imports", "code-digest", "pending-controls",
        "orphan-entries", "qualification", "known-good",
        # P2.5b: the milestones, their export records and packages, the report.
        "milestones", "exports", "report",
        # Architecture phase 1 (SPEC decisions 12, 13): the requirements and the
        # mechanisms' evaluator names.
        "requirements", "mechanisms",
        # Architecture phase 2 (SPEC 14, 38): decisions' pointers and unrecorded
        # preferences, and every evaluator's basis — warnings, never a FAIL.
        "decisions", "basis",
        # Architecture phase 3a (SPEC 18, 19): the graph's pointers, and the
        # round record's state (`test_round.ABehindRoundRecordSaysSo` plants
        # each warning).
        "graph", "round",
        # Architecture phase 5 (SPEC 17, 36): the groups, and the README's badge
        # (test_groups plants each FAIL and warning; `site text` shows only
        # where a site and its text exist, and
        # test_site_truth.TheReadersSayTheTextIsBehind holds its ok and its
        # warning).
        "groups", "readme",
        # Holdout, general packs: two installed packs binding one claim tag.
        "pack-tags")

#: A fixture every planted project gate can borrow: the bracket's own, which
#: sags the known-good design 30 mm — past any limit the planted gates use.
_FIXTURE = "selftest/bad_configs.py:quarter_thickness"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(project: str, *argv: str):
    return _env.nopekit(list(argv), cwd=project)


def _doctor(project: str, *argv: str) -> tuple[int, dict[str, dict]]:
    """`doctor --json` in a subprocess: ``(exit code, {row name: row})``."""
    proc = _run(project, "doctor", "--json", *argv)
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
from nopekit.gates import gate
from nopekit.models import NegativeControl, Tier, Verdict

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
from nopekit.gates import gate
from nopekit.models import NegativeControl, Tier, Verdict

_NC = NegativeControl(fixture={fixture!r}, note="planted")


def _unreached():
    """Called by no gate: its import is no gate's dependency."""
    import nopekit_planted_unreached
    return nopekit_planted_unreached


def _measure(ctx):
    """Called by `undeclared`: its import is that gate's dependency."""
    if ctx.tier < 0:
        import nopekit_planted_numerics
        return nopekit_planted_numerics.deflection(ctx)
    return float(ctx.params["deflection"])


@gate(id="bracket.undeclared", title="imports what it does not declare",
      claims=["planted"], tier=Tier.INSTANT, negative_control=_NC)
def undeclared(ctx):
    value = _measure(ctx)
    return Verdict(gate="bracket.undeclared", passed=value <= 5.0, measured=value,
                   limit=5.0, units="mm")


@gate(id="bracket.declared", title="imports what it declares", claims=["planted"],
      tier=Tier.INSTANT, requires_python=["nopekit_planted_declared"], negative_control=_NC)
def declared(ctx):
    import nopekit_planted_declared
    return Verdict(gate="bracket.declared", passed=nopekit_planted_declared.ok(ctx))
'''

_ENV_GATE = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_doctor.py: a gate that takes its limit from the environment."""
import os
from os import getenv as read_env

from nopekit.gates import gate
from nopekit.models import NegativeControl, Tier, Verdict


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

from nopekit.gates import gate
from nopekit.models import NegativeControl, Tier, Verdict

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

from nopekit.gates import gate
from nopekit.models import NegativeControl, Tier, Verdict

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
from nopekit.gates import gate
from nopekit.models import NegativeControl, Tier, Verdict


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


#: The same two controls one tier up: what `doctor` runs only under `--tier 1`,
#: and otherwise reads from a control entry `check --tier 1` recorded.
_SEAL_BUILD_GATES = _SEAL_GATES.replace("tier=Tier.INSTANT", "tier=Tier.BUILD")

#: Two planted packs whose gates both bind the claim tag `planted-shared`, and
#: one tag each of their own. Each control is sealed: the row under test is the
#: tag row, never the seal row.
_TAG_GATES = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_doctor.py: one gate binding a shared tag and its own."""
from nopekit.gates import gate
from nopekit.models import NegativeControl, Tier, Verdict


@gate(id="{pack}.span", claims=["planted-shared", "{pack}-own"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:sealed_long", note="planted"))
def span(ctx):
    value = ctx.params.get("span_mm")
    if value is None:
        return Verdict(gate="{pack}.span", skipped=True, skip_reason="no span_mm")
    return Verdict(gate="{pack}.span", passed=float(value) <= 100.0, measured=float(value),
                   limit=100.0, units="mm")
'''


def _plant_pack(project: str, name: str, gates_src: str) -> None:
    """A pack under the project's `.nopekit/packs/`, installed with `packs add`:
    `gates_src` as its one gate module, `_SEAL_FIXTURES` as its fixtures."""
    pack = os.path.join(project, ".nopekit", "packs", name)
    _write(pack, "pack.json", json.dumps({"name": name}) + "\n")
    _write(pack, "gates/span.py", gates_src)
    _write(pack, "selftest/bad.py", _SEAL_FIXTURES)
    _write(pack, "selftest/baseline.json", json.dumps({"span_mm": 50.0}) + "\n")
    added = _run(project, "packs", "add", name)
    assert added.returncode == 0, added.stdout + added.stderr


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
        cls.base = tempfile.mkdtemp(prefix="nopekit-doctor-")
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
        pack = os.path.join(project, ".nopekit", "packs", "planted-seal")
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

    def test_sealed_fixtures_above_tier_zero(self):
        """Holdout, general packs: the row ran every pack control at every tier,
        so `doctor` took nine minutes on a project with container replays. Now
        it runs tier 0, reads a control above it from the entry `check`
        recorded at its version, and names one it neither ran nor found, with
        the command that runs it. A BUILD-tier control that reads its host is
        caught each way it can be: named as not run, run under `--tier 1`, and
        read back after `check --tier 1`."""
        project = self.copy()
        _plant_pack(project, "planted-seal", _SEAL_BUILD_GATES)

        code, rows = _doctor(project)
        self.assertRow(rows, "sealed-fixtures", "ok", "0 pack control(s) up to tier 0",
                       "2 not run: above tier 0, and no control entry at its version",
                       "planted-seal.leaky", "planted-seal.sealed",
                       "`nopekit doctor --tier 1` runs them")
        self.assertEqual(code, 0, rows)

        code, rows = _doctor(project, "--tier", "1")
        self.assertRow(rows, "sealed-fixtures", "FAIL", "planted-seal.leaky",
                       "selftest/bad.py:leaky_long", absent=("planted-seal.sealed:",))
        self.assertEqual(code, 1, "an unsealed pack control breaks invariant 5")

        checked = _run(project, "check", "--tier", "1")
        self.assertIn("planted-seal.leaky", checked.stdout, checked.stdout + checked.stderr)
        code, rows = _doctor(project)
        row = self.assertRow(rows, "sealed-fixtures", "FAIL", "planted-seal.leaky",
                             "selftest/bad.py:leaky_long", "(all params)",
                             absent=("planted-seal.sealed:", "not run: above tier 0"))
        self.assertEqual(code, 1, row)

    def test_pack_tags(self):
        """Holdout, general packs: a claim tag two installed packs bind covers a
        claim with the gates of both, and `doctor` warned only on param keys.
        The row names the tag, each pack's gates and the claims that carry it;
        `check` notes a carried one and leaves its exit code alone."""
        project = self.copy()
        for name in ("planted-left", "planted-right"):
            _plant_pack(project, name, _TAG_GATES.replace("{pack}", name))
        code, rows = _doctor(project)
        row = self.assertRow(rows, "pack-tags", "warn",
                             "'planted-shared' binds planted-left (planted-left.span) and "
                             "planted-right (planted-right.span) (no claim carries it yet)",
                             absent=("planted-left-own", "planted-right-own"))
        self.assertEqual(code, 0, row)
        before = _run(project, "check")
        self.assertNotIn("planted-shared", before.stdout, before.stdout)

        with open(os.path.join(project, "claims", "C1.json"), encoding="utf-8") as fh:
            claim = json.load(fh)
        claim["tags"] = list(claim.get("tags") or []) + ["planted-shared"]
        _write(project, "claims/C1.json", json.dumps(claim, indent=2) + "\n")
        _code, rows = _doctor(project)
        self.assertRow(rows, "pack-tags", "warn", "(carried by C1)",
                       "give it a tag only the pack you mean binds")
        after = _run(project, "check")
        self.assertIn("note: C1 carries the tag 'planted-shared', which gates of 2 installed "
                      "packs bind: planted-left (planted-left.span) and planted-right "
                      "(planted-right.span)", after.stdout, after.stdout + after.stderr)
        data = json.loads(_run(project, "check", "--json").stdout)
        self.assertEqual(data["tag_collisions"], [
            {"tag": "planted-shared", "claims": ["C1"],
             "gates": {"planted-left": ["planted-left.span"],
                       "planted-right": ["planted-right.span"]}}])
        self.assertClean("pack-tags")

    def test_imports(self):
        project = self.copy()
        _write(project, "gates/planted_imports.py", _IMPORT_GATES.format(fixture=_FIXTURE))
        code, rows = _doctor(project)
        self.assertRow(rows, "imports", "warn", "bracket.undeclared",
                       "nopekit_planted_numerics",
                       absent=("bracket.declared", "nopekit_planted_declared",
                               "nopekit_planted_unreached"))
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

    def test_each_static_row_scans_a_file_once(self):
        """Holdout, general packs: the static rows scanned a module once per
        gate defined in it (eighteen times for one pack's module), and were most
        of what `doctor` cost once its seal row ran tier 0 only. Each row now
        scans each file once (`cli._hits_by_file`); the rows say the same."""
        registry = _bundled_registry()
        files = {path for _spec, fn in registry.pairs() for path in cli_mod._source_files(fn)}
        self.assertLess(len(files), sum(len(cli_mod._source_files(fn))
                                        for _spec, fn in registry.pairs()),
                        "the bundled corpus shares no file between gates: nothing to show")
        for name, scan in (("_memo_hits", cli_mod._memo_reads),
                           ("_env_hits", cli_mod._env_reads)):
            with self.subTest(row=name):
                real = getattr(cli_mod, name)
                seen: list[int] = []

                def counted(tree, real=real, seen=seen):
                    seen.append(id(tree))
                    return real(tree)
                with mock.patch.object(cli_mod, name, counted):
                    scan(registry, _env.REPO)
                self.assertEqual(len(seen), len(set(seen)), f"{name} scanned a tree twice")
                self.assertLessEqual(len(seen), len(files))

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
from nopekit.gates import gate
from nopekit.models import NegativeControl, Verdict


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
            shutil.rmtree(os.path.join(project, ".nopekit", "verdicts", gid))
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



# --------------------------------------------------------------------------- #
# V-16 (P2.5b) — milestones, exports and the report
# --------------------------------------------------------------------------- #
class MilestonesExportsAndTheReport(_env.EnvCase):
    """(V-16) `doctor`'s `milestones` row names a required id no claim file holds
    and a generator that does not resolve; its `exports` row names a package
    file edited after export and a leftover scratch directory, and says when the
    last export's package is not here; its `report` row warns on a leftover
    `docs/readiness.md`, which nothing writes any more."""

    def setUp(self):
        super().setUp()
        import _physical as P
        self.P = P
        self.root = P.project(os.path.join(self.tmp(), "b"), thickness=8.0, git=True)
        P.run(self.root, "check")

    def rows(self) -> tuple[int, dict]:
        return _doctor(self.root)

    def assertRow(self, rows: dict, name: str, status: str, *present: str) -> None:
        row = rows.get(name)
        self.assertIsNotNone(row, f"doctor printed no {name!r} row: {sorted(rows)}")
        self.assertEqual(row["status"], status, row)
        for text in present:
            self.assertIn(text, row["detail"], row)

    def test_clean(self):
        _code, rows = self.rows()
        for name in ("milestones", "exports", "report"):
            with self.subTest(name):
                self.assertRow(rows, name, "ok")

    def test_milestones(self):
        self.P.milestone(self.root, "fit-check", ["C1", "C9"])
        _code, rows = self.rows()
        self.assertRow(rows, "milestones", "FAIL", "fit-check", "C9")
        os.remove(os.path.join(self.root, "milestones", "fit-check.json"))
        self.P.milestone(self.root, "fit-check", ["C1"], generator="generators/nowhere.py:f")
        _code, rows = self.rows()
        self.assertRow(rows, "milestones", "FAIL", "generators/nowhere.py")

    def test_a_generator_bound_any_way_export_loads_it(self):
        """Review of P2.5b (finding 14): only a top-level `def` was a function
        to `doctor`, so a generator bound by assignment, inside an `if`, or by
        an import read FAIL "has no function" while `export` loaded and ran it.
        A name bound at module level is resolved; one doctor cannot see without
        running the module (a star import) is a warning; one bound nowhere
        stays a problem."""
        with open(os.path.join(self.root, "generators", "profile.py"), encoding="utf-8") as fh:
            source = fh.read()
        _write(self.root, "generators/profile.py", source
               + "\n\nprint_package = side_profile\n\nif True:\n"
                 "    def in_if(ctx):\n        side_profile(ctx)\n"
                 "\nfrom os.path import join as joined\n")
        _write(self.root, "generators/starred.py", "from os.path import *\n")
        for ref, status in (("generators/profile.py:print_package", "ok"),
                            ("generators/profile.py:in_if", "ok"),
                            ("generators/profile.py:joined", "ok"),
                            ("generators/starred.py:join", "warn"),
                            ("generators/profile.py:nowhere", "FAIL")):
            with self.subTest(ref):
                self.P.milestone(self.root, "print-v1", ["C1", "C2", "C3", "C4"],
                                 generator=ref)
                _code, rows = self.rows()
                self.assertRow(rows, "milestones", status)
                if status != "ok":
                    self.assertIn(ref.split(":")[1], rows["milestones"]["detail"])

    def test_exports(self):
        self.P.run(self.root, "export", "print-v1", code=0)
        _code, rows = self.rows()
        self.assertRow(rows, "exports", "ok")
        svg = os.path.join(self.root, "out", "print-v1", "bracket-profile.svg")
        with open(svg, "a", encoding="utf-8") as fh:
            fh.write("<!-- edited -->")
        _code, rows = self.rows()
        self.assertRow(rows, "exports", "FAIL", "bracket-profile.svg")
        shutil.rmtree(os.path.join(self.root, "out", "print-v1"))
        _code, rows = self.rows()
        self.assertRow(rows, "exports", "ok", "not here")
        os.makedirs(os.path.join(self.root, "out", ".print-v1.tmp-123"))
        _code, rows = self.rows()
        self.assertRow(rows, "exports", "FAIL", ".print-v1.tmp-123")

    def test_report(self):
        _write(self.root, "docs/readiness.md", "# an old report\n")
        _code, rows = self.rows()
        self.assertRow(rows, "report", "warn", "docs/readiness.md", "git rm docs/readiness.md")



class DecisionsAndBasis(_env.EnvCase):
    """(architecture phase 2) `doctor`'s `decisions` row names each unrecorded
    preferred_against rejection with the command its person runs, a return
    condition or an input naming no record, and a preference file no decision
    holds; its `basis` row names an evaluator that declares none. Both warn and
    never FAIL: neither moves a status."""

    def test_the_bracket(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "b"), migrated=True)
        code, rows = _doctor_in_process(project)
        self.assertEqual(rows["decisions"]["status"], "warn")
        self.assertIn("print-in-petg (a machined 6061 aluminium bracket, by Robin Okafor) — "
                      "Robin Okafor runs: nopekit prefer print-in-petg",
                      rows["decisions"]["detail"])
        self.assertEqual((rows["basis"]["status"], rows["basis"]["detail"]),
                         ("ok", "every evaluator declares its basis"))
        path = os.path.join(project, "decisions", "print-in-petg.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["rejected"][0]["input"] = "no-such-quote"
        data["rejected"][2]["returns_if"]["claim"] = "C42"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        again, rows = _doctor_in_process(project)
        self.assertEqual(again, code, "a warning never moves doctor's exit code")
        detail = rows["decisions"]["detail"]
        self.assertIn("rejected[0].input names 'no-such-quote', which has no "
                      "inputs/no-such-quote.json", detail)
        self.assertIn("rejected[2].returns_if names 'C42', which has no claims/C42.json",
                      detail)

    def test_an_undeclared_basis_warns(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "b"), migrated=True)
        path = os.path.join(project, "gates", "structural.py")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace('    basis="model-only",\n', "", 1))
        _code, rows = _doctor_in_process(project)
        self.assertEqual(rows["basis"]["status"], "warn")
        self.assertIn("1 evaluator(s) declare no basis and show as undeclared: bracket.bed_fit",
                      rows["basis"]["detail"])


class DoctorPeopleRow(_env.EnvCase):
    """(architecture phase 6, SPEC §0.3) `doctor`'s `people` row says whether a
    round here would commit — the people listed and whether git's identity is
    one of them. Five states, and it never FAILs: a project that commits no
    round is not broken. (Not in ROWS: the bracket lists no one on purpose.)"""

    IDENT = f"{_env.IDENTITY['GIT_AUTHOR_NAME']} <{_env.IDENTITY['GIT_AUTHOR_EMAIL']}>"

    def setUp(self):
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for name in list(os.environ):
            if name.startswith(("GIT_", "CLAUDE")) or name in ("EMAIL", "XDG_CONFIG_HOME"):
                del os.environ[name]
        home = self.tmp()
        os.environ.update({"HOME": home, "XDG_CONFIG_HOME": home,
                           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                           **_env.IDENTITY})

    def _row(self, project: str) -> dict:
        code, rows = _doctor_in_process(project)
        self.assertNotEqual(rows["people"]["status"], "FAIL")
        return rows["people"]

    def _project(self, *extra: str) -> str:
        root = os.path.join(self.tmp(), "p")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli_mod.main(["init", "--name", "p", "-C", root, *extra]), 0)
        return root

    def test_the_five_states(self):
        root = self._project()
        row = self._row(root)
        self.assertEqual((row["status"], row["detail"]),
                         ("ok", f"1 listed ({self.IDENT}); git's identity here is listed, "
                                f"so rounds commit"))
        os.environ["GIT_AUTHOR_NAME"] = "Someone Else"
        row = self._row(root)
        self.assertEqual(row["status"], "warn")
        self.assertIn("git's identity here, Someone Else <tests@nopekit.invalid>, is not one "
                      "of them — rounds make no commit", row["detail"])
        for name in _env.IDENTITY:
            os.environ.pop(name)
        row = self._row(root)
        self.assertEqual((row["status"], row["detail"]),
                         ("warn", "1 listed; git has no user.name and user.email here — "
                                  "rounds make no commit"))
        os.environ.update(_env.IDENTITY)
        bare = self._project("--no-git")
        self.assertEqual(self._row(bare)["detail"],
                         "1 listed; the project is in no git repository — rounds make no "
                         "commit (`git init`)")
        path = os.path.join(bare, ".nopekit", "project.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["people"] = []
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        row = self._row(bare)
        self.assertEqual(row["status"], "warn")
        self.assertTrue(row["detail"].startswith("none listed — rounds make no commit"), row)

    def test_none_listed_prints_the_records_shape(self):
        """(follow-up after phase 3b) The printed row shows the entry a person
        types, with single braces: the row was printed raw, so doctor said to
        add `{{"name": "...", "email": "..."}}` to the project file."""
        bare = self._project("--no-git")
        path = os.path.join(bare, ".nopekit", "project.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["people"] = []
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            cli_mod.main(["doctor", "-C", bare])
        rows = [line for line in out.getvalue().splitlines()
                if line.startswith(f"{cli_mod._tag('warn')} people ")]
        self.assertEqual(len(rows), 1, out.getvalue())
        self.assertTrue(rows[0].endswith(
            'none listed — rounds make no commit; the person adds themselves to "people" in '
            '.nopekit/project.json as {"name": "...", "email": "..."}'), rows[0])
        self.assertNotIn("{{", rows[0])
        self.assertNotIn("}}", rows[0])
        self.assertEqual(self._row(bare)["detail"], rows[0].split("people", 1)[1].strip())


if __name__ == "__main__":
    unittest.main()
