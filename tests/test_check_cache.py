# SPDX-License-Identifier: Apache-2.0
"""`check` serves the cache, counts its controls, and every reader resolves once.

What slipped through, each seen on the CLI as it stood before checkpoint 1.2's
edge was wired onto the resolver:

* **S-05 (check).** A logger with a declared control produced PROVEN rows:
  `check` never ran a control and nothing it printed read one. `check` now runs
  every selected gate's control on a control-entry miss and counts them apart —
  `controls: E executed, C cached, R re-verified`.
* **S-08.** `gate show` printed "last selftest: (never run)" forever: it read a
  ledger key `gate selftest` deliberately never wrote. The selftest now files
  control entries, and `gate show` reads admission off them.
* **S-20 / S-89 (check).** One `last_run` per project decided staleness, so a first
  sweep filtered by `--only` could never go stale, and every recorded `check`
  rewrote the tracked `ledger.json` and appended a tracked run file. There is no
  run history and no clock to not advance: the saved ledger holds no verdict, and
  `--only` writes no `last_check.json`.
* **S-28 (the cli copies).** The staleness rule and `_flat_params` lived twice, in
  `cli` and in `site`, kept "byte-for-byte" in sync by a comment.
* **S-30.** An availability-skipped gate never called `fn`, recorded no reads, and
  lost its `Param.gates` attribution on every full sweep.
* **S-32.** `check --no-record`, the documented dry sweep, reported fresh passes as
  STALE, because the global clock did not advance — and it still wrote.
* **cli:H1.** An affected-only `check` that listed executed rows only would drop
  `bracket.deflection` from `check --json` on a fresh clone whose first check is
  all cache hits, and verify.sh's step 4 would read the missing row as red.
* **cli:H3, R-5.** Nine readers of `ledger.verdicts`; a reader that did not go
  through the one resolver would show legacy or never-run verdicts — and a
  resolved view that reached `store.save` would persist cache verdicts back into
  the tracked ledger.

Everything runs on a copy of the bracket (`_projects.bracket_copy`), through
`_env.atompipe` subprocesses — except where a test must patch the spine
(availability), which it does in-process, against a fresh registry per command.

Run:  PYTHONPATH=src python3 -m unittest tests.test_check_cache -v
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

import _env
import _projects
import _transcript
from atompipe import cli as cli_mod
from atompipe import gates as gates_mod

CLI_PY = os.path.join(_env.REPO, "src", "atompipe", "cli.py")

#: The bracket's six gates, in registration order.
BRACKET_GATES = ("bracket.deflection", "bracket.bending_stress", "bracket.bearing",
                 "bracket.model_validity", "bracket.bed_fit", "bracket.min_wall")

#: verify.sh's step-4 parser (`scratchpad/verify.sh`, the heredoc after `== 4.`),
#: VERBATIM. It is the phase gate's reading of `check --json`, written before 1.2
#: existed: it takes a row's `error`, then `skipped`, then `status`/`outcome`,
#: then `passed`, and calls the check red unless `bracket.deflection` reads
#: `fail` and every other row reads a pass. Pinned here (a `C:`) so a JSON shape
#: that drops a cached row — cli:H1 — turns this file red, not the phase gate.
VERIFY_STEP4 = r'''import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as e:
    print("RED: check --json unparseable:", e); sys.exit(1)
rows = d.get("verdicts") or d.get("gates") or []
def st(v):
    if v.get("error"): return "error"
    if v.get("skipped"): return "skipped"
    s = v.get("status") or v.get("outcome")
    if s: return str(s)
    return "pass" if v.get("passed") else "fail"
states = {v.get("gate") or v.get("id"): st(v) for v in rows}
bad = {g: s for g, s in states.items() if s not in ("pass", "ok", "passed", "cached", "hit") and g != "bracket.deflection"}
dfl = states.get("bracket.deflection")
ok = (dfl in ("fail", "failed", "FAIL")) and not bad
print(f"   gates={len(states)} deflection={dfl} other-non-pass={bad}")
if not ok:
    print("RED: bracket check is not exactly the deliberate deflection failure"); sys.exit(1)
'''


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _state(project: str, *parts: str) -> str:
    return os.path.join(project, ".atompipe", *parts)


def _tree(path: str) -> dict[str, bytes]:
    """``{relative path: bytes}`` of every file under ``path`` (``{}`` when absent)."""
    out: dict[str, bytes] = {}
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, path).replace(os.sep, "/")] = fh.read()
    return out


def _entries(project: str) -> tuple[list[str], list[str]]:
    """``(verdict entries, control entries)`` under ``.atompipe/verdicts``, as
    project-relative paths, each matched against the transcript's shapes."""
    found = sorted(f".atompipe/verdicts/{rel}" for rel in _tree(_state(project, "verdicts")))
    verdicts = [p for p in found if _transcript.ENTRY_PATH.fullmatch(p)]
    controls = [p for p in found if _transcript.CONTROL_ENTRY_PATH.fullmatch(p)]
    return verdicts, controls


def _run(project: str, *argv: str):
    return _env.atompipe(list(argv), cwd=project)


def _json(proc) -> dict:
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:                     # pragma: no cover - reported, not raised
        raise AssertionError(f"not one JSON document ({exc}):\n{proc.stdout}\n{proc.stderr}")


def _rows(data: dict) -> dict[str, dict]:
    return {row["gate"]: row for row in data["verdicts"]}


def _in_process(argv: list[str]) -> tuple[int, str, str]:
    """``cli.main(argv)`` in this process: ``(exit code, stdout, stderr)``."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_mod.main(argv)
    return code, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------- #
# the AST rule: nothing `_resolved` returns reaches `store.save`
# --------------------------------------------------------------------------- #
def _is_call_to(node: ast.AST, name: str) -> bool:
    func = node.func if isinstance(node, ast.Call) else None
    return (isinstance(func, ast.Name) and func.id == name) or \
        (isinstance(func, ast.Attribute) and func.attr == name)


def _names_in(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _resolved_in(node: ast.AST | None) -> bool:
    return node is not None and any(_is_call_to(n, "_resolved") for n in ast.walk(node))


def _targets(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _bindings(fn: ast.AST) -> list[tuple[ast.AST | None, list[ast.AST]]]:
    """``(value, [targets])`` for every binding in ``fn`` — assignments, loop and
    comprehension targets, ``with ... as``, walrus — nested functions included,
    so a closure that saves an outer name is seen from the outer scope."""
    out: list[tuple[ast.AST | None, list[ast.AST]]] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            out.append((node.value, list(node.targets)))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            out.append((node.value, [node.target]))
        elif isinstance(node, ast.NamedExpr):
            out.append((node.value, [node.target]))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            out.append((node.iter, [node.target]))
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            out.append((node.context_expr, [node.optional_vars]))
    return out


def saves_from_resolved(source: str) -> list[str]:
    """``<function>:<line>`` for every ``store.save(...)`` whose arguments reach a
    value ``_resolved`` returned — directly, or through any chain of bindings
    (conservative: a name bound from an expression that mentions a tainted name
    is tainted). The view is a reader's; saving it would write cache verdicts,
    and coverage and read sets the records do not own, into the tracked ledger."""
    tree = ast.parse(source)
    found: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tainted: set[str] = set()
        bindings = _bindings(fn)
        changed = True
        while changed:
            changed = False
            for value, targets in bindings:
                if _resolved_in(value) or (_names_in(value) & tainted):
                    for target in targets:
                        new = _targets(target) - tainted
                        if new:
                            tainted |= new
                            changed = True
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "save" and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "store"):
                continue
            args = [*node.args, *(k.value for k in node.keywords)]
            if any(_resolved_in(a) or (_names_in(a) & tainted) for a in args):
                found.append(f"{fn.name}:{node.lineno}")
    return found


def _callers_of(source: str, name: str) -> set[str]:
    """The top-level functions whose bodies call ``name``."""
    tree = ast.parse(source)
    return {fn.name for fn in tree.body if isinstance(fn, ast.FunctionDef)
            and any(_is_call_to(n, name) for n in ast.walk(fn))}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# CheckServesTheCache: one copy, a first check that runs, then the cache
# --------------------------------------------------------------------------- #
class CheckServesTheCache(unittest.TestCase):
    """The bracket's first check executes its six gates and six controls; the
    second is all cache hits and still lists all six rows; every reader after it
    agrees with it, and none of them writes."""

    @classmethod
    def setUpClass(cls) -> None:
        base = tempfile.mkdtemp(prefix="atompipe-check-cache-")
        cls.addClassCleanup(_env._rmtree, base)
        cls.project = _projects.bracket_copy(os.path.join(base, "bracket"))
        cls.runs = _tree(_state(cls.project, "runs"))
        cls.first = _run(cls.project, "check", "--json")
        cls.after_first = _entries(cls.project)
        cls.second = _run(cls.project, "check", "--json")
        cls.second_text = _run(cls.project, "check")

    def test_the_first_check_runs_six_gates_and_six_controls(self):
        self.assertEqual(self.first.returncode, 1, self.first.stdout + self.first.stderr)
        data = _json(self.first)
        self.assertEqual(data["counts"]["executed"], 6, data["counts"])
        self.assertEqual(data["counts"]["cached"], 0, data["counts"])
        self.assertEqual(data["counts"]["controls"]["executed"], 6, data["counts"])
        verdicts, controls = self.after_first
        self.assertEqual((len(verdicts), len(controls)), (6, 6), (verdicts, controls))
        self.assertEqual(sorted({p.split("/")[2] for p in verdicts + controls}),
                         sorted(BRACKET_GATES))
        self.assertEqual(sorted(_tree(_state(self.project, "verdicts"))),
                         sorted(p[len(".atompipe/verdicts/"):]
                                for p in verdicts + controls),
                         "the verdict cache holds something that is neither kind of entry")
        for row in data["verdicts"]:
            with self.subTest(gate=row["gate"]):
                self.assertFalse(row["cached"])
                self.assertIn("duration_s", row, "an executed row keeps its measured cost")
                self.assertRegex(row["rho"], r"^[0-9a-f]{64}$")

    def test_the_second_check_is_all_cache_hits(self):
        proc = self.second
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        data = _json(proc)
        rows = _rows(data)
        self.assertEqual(list(rows), list(BRACKET_GATES), "every selected gate has a row (cli:H1)")
        for gate, row in rows.items():
            with self.subTest(gate=gate):
                self.assertTrue(row["cached"], row)
                self.assertTrue(row["fresh"], row)
                self.assertIn("outcome", row)
                self.assertIn("passed", row)
                self.assertNotIn("duration_s", row, "a cached row replays no duration")
                self.assertNotIn("cpu_s", row, "a cached row replays no duration")
        self.assertEqual(rows["bracket.deflection"]["outcome"], "fail")
        self.assertEqual(data["counts"]["ran"], 5)
        self.assertEqual((data["counts"]["executed"], data["counts"]["cached"]), (0, 6))
        self.assertEqual(data["counts"]["controls"]["cached"], 6, data["counts"])
        self.assertEqual(data["counts"]["controls"]["executed"], 0, data["counts"])
        self.assertIsNone(data["run"], "there is no run history to point at")
        self.assertFalse(data["stale"])
        self.assertEqual(self.after_first, _entries(self.project), "a cache hit wrote an entry")

    def test_the_second_check_prints_the_cached_shape(self):
        proc = self.second_text
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertIn("6 gates: 0 executed, 6 cached — 5 ok, 1 FAIL — tier 0", lines)
        self.assertTrue(any(_transcript.CHECK_SUMMARY.fullmatch(line) for line in lines))
        cached = [m.group("gate") for line in lines
                  if (m := _transcript.CACHED_ROW.fullmatch(line))]
        self.assertEqual(cached, ["bracket.deflection"], proc.stdout)
        self.assertFalse([line for line in lines if line.startswith("[ok  ]")],
                         "a cached ok row is not printed")
        self.assertFalse([line for line in lines if _transcript.CONTROLS_SUMMARY.fullmatch(line)],
                         "the controls line is printed only when a control ran")
        self.assertNotIn("gap --propose", proc.stdout)
        self.assertTrue(any(re.fullmatch(_transcript._C7, line) for line in lines), proc.stdout)

    def test_verify_sh_parser_accepts_cached_rows(self):
        """C: verify.sh step 4, verbatim, reads an all-cached check as the one
        deliberate failure — and still refuses one whose deflection row is gone."""
        base = tempfile.mkdtemp(prefix="atompipe-verify4-")
        self.addCleanup(_env._rmtree, base)
        cached = os.path.join(base, "check.json")
        with open(cached, "w", encoding="utf-8") as fh:
            fh.write(self.second.stdout)
        ok = _env.run([sys.executable, "-c", VERIFY_STEP4, cached], cwd=base)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn("gates=6 deflection=fail other-non-pass={}", ok.stdout)

        data = _json(self.second)
        data["verdicts"] = [r for r in data["verdicts"] if r["gate"] != "bracket.deflection"]
        dropped = os.path.join(base, "dropped.json")
        with open(dropped, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        red = _env.run([sys.executable, "-c", VERIFY_STEP4, dropped], cwd=base)
        self.assertEqual(red.returncode, 1, red.stdout + red.stderr)
        self.assertIn("RED", red.stdout)

    def test_status_reads_the_cache_right_after_check(self):
        """No reader lag (spec §4.0): what `check` judged is what `status` shows,
        claim for claim, from the cache the check left — and `last check:` says when."""
        status = _run(self.project, "status", "--json")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        seen = _json(status)
        check = _json(self.second)
        with open(_state(self.project, "cache", "last_check.json"), encoding="utf-8") as fh:
            last = json.load(fh)
        self.assertEqual(seen["claims"], last["statuses"])
        for row in check["blocking"]:
            self.assertEqual(seen["claims"][row["claim"]], row["status"], row)
        self.assertEqual(seen["summary"]["by_status"], check["summary"]["by_status"])
        self.assertEqual(seen["stale_gates"], [])
        self.assertFalse(seen["stale"])
        self.assertEqual(sorted(seen["freshness"]), sorted(BRACKET_GATES))
        for gate, row in seen["freshness"].items():
            with self.subTest(gate=gate):
                self.assertEqual((row["state"], row["admission"]), ("fresh", "admitted"), row)
        self.assertEqual(seen["last_check"]["when"], last["when"])
        self.assertIsInstance(seen["last_check"]["age_s"], (int, float))
        self.assertNotIn("last_run", seen)
        self.assertNotIn("last_run_age", seen)

        text = _run(self.project, "status")
        lines = text.stdout.splitlines()
        self.assertIn("stale: none   (6 checks current)", lines, text.stdout)
        self.assertTrue(any(_transcript.STALE_NONE.fullmatch(line) for line in lines))
        self.assertTrue(any(_transcript.LAST_CHECK.fullmatch(line) for line in lines),
                        text.stdout)
        self.assertFalse([line for line in lines if line.startswith(("model:", "last sweep"))],
                         "the model line is printed only when the model is broken")

        shown = _json(_run(self.project, "claim", "show", "C1", "--json"))
        self.assertEqual(shown["status"], seen["claims"]["C1"])
        self.assertEqual(shown["covered_by"], ["bracket.deflection", "bracket.model_validity"])

    def test_effective_ledger_is_never_saved(self):
        """The saved ledger holds no cache verdict (PD-31), and no reader writes
        the ledger or the cache: the view `_resolved` builds is shown, never kept."""
        ledger_path = _state(self.project, "ledger.json")
        with open(ledger_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["verdicts"], [], "a check saved verdicts into the ledger")
        params = {p["name"]: p["gates"] for p in saved["params"]}
        self.assertIn("bracket.bed_fit", params["bed_xy"], params)
        self.assertIn("bracket.deflection", params["load_n"], params)

        with open(ledger_path, "rb") as fh:
            before = fh.read()
        cache = _tree(_state(self.project, "verdicts"))
        for argv in (["status"], ["status", "--json"], ["report"], ["report", "--json"],
                     ["claim", "list"], ["claim", "show", "C1"],
                     ["gate", "show", "bracket.deflection"], ["why", "thickness"],
                     ["doctor", "--json"]):
            with self.subTest(argv=argv):
                proc = _run(self.project, *argv)
                self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
                with open(ledger_path, "rb") as fh:
                    self.assertEqual(fh.read(), before, f"{argv} wrote the ledger")
                self.assertEqual(_tree(_state(self.project, "verdicts")), cache,
                                 f"{argv} wrote the verdict cache")
        self.assertEqual(_tree(_state(self.project, "runs")), self.runs,
                         "S-89: a command appended to the tracked run history")

    def test_no_store_save_argument_flows_from_resolved(self):
        problems = saves_from_resolved(_read(CLI_PY))
        self.assertEqual(problems, [], "a resolved view reaches store.save: " + ", ".join(problems))

    def test_the_flow_check_catches_a_planted_save(self):
        """V: the AST rule refuses the view saved directly, and saved through a
        derived copy; it passes the records ledger saved beside the view."""
        planted = {
            "direct": ("""\
                def cmd(root, ledger, registry):
                    view, resolution = _resolved(root, ledger, registry, None, "", now="")
                    store.save(root, view)
                """, ["cmd:3"]),
            "derived": ("""\
                def cmd(root, ledger, registry):
                    view, resolution = _resolved(root, ledger, registry, None, "", now="")
                    records = dataclasses.replace(view, verdicts=[])
                    store.save(root, records)
                """, ["cmd:4"]),
            "in a loop": ("""\
                def cmd(root, ledger, registry):
                    pair = _resolved(root, ledger, registry, None, "", now="")
                    for view in pair[:1]:
                        store.save(root, view)
                """, ["cmd:4"]),
            "inline": ("""\
                def cmd(root, ledger, registry):
                    store.save(root, _resolved(root, ledger, registry, None, "", now="")[0])
                """, ["cmd:2"]),
        }
        for label, (source, want) in planted.items():
            with self.subTest(label):
                self.assertEqual(saves_from_resolved(textwrap.dedent(source)), want)
        clean = """\
            def cmd(root, ledger, registry):
                view, resolution = _resolved(root, ledger, registry, None, "", now="")
                records = dataclasses.replace(ledger, verdicts=[])
                store.save(root, records)
            """
        self.assertEqual(saves_from_resolved(textwrap.dedent(clean)), [])

    def test_every_reader_resolves_through_one_helper(self):
        """cli:H3, R-5: every command that shows a verdict gets it from `_resolved`."""
        readers = {"cmd_status", "cmd_claim_list", "cmd_claim_show", "cmd_report",
                   "cmd_site_build", "cmd_gate_show", "cmd_why", "cmd_doctor", "cmd_check"}
        callers = _callers_of(_read(CLI_PY), "_resolved")
        self.assertEqual(readers - callers, set(),
                         "these readers do not go through _resolved")


# --------------------------------------------------------------------------- #
# the rules the cli used to keep a copy of (S-28), and one registry per command
# --------------------------------------------------------------------------- #
class CliKeepsNoCopyOfTheRules(unittest.TestCase):
    def test_the_cli_keeps_no_copy_of_the_rules(self):
        """S-28, the cli half: no staleness rule, no flattening, no param-read
        recorder and no run record of its own. `verdicts` decides what is
        current and `modelio.flat_params` flattens, once each."""
        tree = ast.parse(_read(CLI_PY))
        defined = {node.name for node in ast.walk(tree)
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        self.assertEqual(defined & {"_staleness", "_flat_params", "_ParamReads",
                                    "_refresh_param_gates"}, set())
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        # `inputs_hash` and `model_hash` stay, as display ids (`ingest --json`,
        # `check --json`'s `model_hash`); what goes is comparing them.
        self.assertEqual(attrs & {"record_run", "last_run"}, set(),
                         "the cli still records a sweep or reads the last one")
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    for alias in node.names}
        self.assertNotIn("RunMeta", imported)
        called = {f"{node.func.value.id}.{node.func.attr}" for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name)}
        for name in ("modelio.flat_params", "verdicts.sweep", "verdicts.resolve",
                     "verdicts.admission", "verdicts.admission_state",
                     "verdicts.write_last_check", "verdicts.last_read_sets"):
            self.assertIn(name, called)


class FreshRegistryPerCommand(_env.EnvCase):
    """cli:H6, §3.5: each command builds its own `gates.Registry`, so two commands
    in one process never see each other's gates, and the module-level default is
    never the target of a project's load."""

    def test_two_commands_in_one_process(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"))
        before = list(gates_mod.REGISTRY.ids())
        listed = []
        for _ in range(2):
            code, out, err = _in_process(["gate", "list", "--json", "-C", project])
            self.assertEqual(code, 0, out + err)
            listed.append([row["id"] for row in json.loads(out)["gates"]])
        self.assertEqual(listed[0], list(BRACKET_GATES))
        self.assertEqual(listed[1], listed[0], "the second command lost or doubled gates")
        self.assertEqual(list(gates_mod.REGISTRY.ids()), before,
                         "a command loaded a project's gates into the module-level registry")


# --------------------------------------------------------------------------- #
# dry and forced sweeps, --only, a missing tool, the selftest (each on its own copy)
# --------------------------------------------------------------------------- #
class SweepsThatKeepNothingOrEverything(_env.EnvCase):
    def setUp(self) -> None:
        self.project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"))

    def _edit_bed_xy(self) -> None:
        path = os.path.join(self.project, "model", "bracket.py")
        text = _read(path)
        self.assertEqual(text.count("bed_xy: float = 220.0"), 1)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text.replace("bed_xy: float = 220.0", "bed_xy: float = 250.0"))

    def test_no_record_writes_nothing_global(self):
        """S-32: after a model edit, a dry sweep runs what moved, reads it as
        current (nothing global is compared), and leaves the verdict cache, the
        obs, the cache directory and the ledger byte-identical."""
        first = _run(self.project, "check")
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        self._edit_bed_xy()
        watched = {name: _tree(_state(self.project, name))
                   for name in ("verdicts", "obs", "cache")}
        with open(_state(self.project, "ledger.json"), "rb") as fh:
            ledger = fh.read()

        dry = _run(self.project, "check", "--no-record", "--json")
        self.assertEqual(dry.returncode, 1, dry.stdout + dry.stderr)
        data = _json(dry)
        row = _rows(data)["bracket.bed_fit"]
        self.assertTrue(row["ok"] and row["fresh"] and not row["cached"], row)
        self.assertEqual(data["counts"]["executed"], 1, data["counts"])
        self.assertEqual(data["summary"]["by_status"]["stale"], 0,
                         "a dry sweep read a fresh pass as stale (S-32)")
        self.assertTrue(data["stale"], "bed_fit was stale before the sweep ran")
        for name, snapshot in watched.items():
            self.assertEqual(_tree(_state(self.project, name)), snapshot,
                             f"--no-record wrote .atompipe/{name}/")
        with open(_state(self.project, "ledger.json"), "rb") as fh:
            self.assertEqual(fh.read(), ledger, "--no-record saved the ledger")

    def test_force_re_executes_and_agrees(self):
        """R-9: `--force` re-runs every selected gate and its control, and on
        unchanged inputs reaches the outcomes the cache holds, writing nothing new."""
        first = _json(_run(self.project, "check", "--json"))
        entries = _entries(self.project)
        forced = _run(self.project, "check", "--force", "--json")
        self.assertEqual(forced.returncode, 1, forced.stdout + forced.stderr)
        data = _json(forced)
        self.assertEqual((data["counts"]["executed"], data["counts"]["cached"]), (6, 0))
        self.assertEqual(data["counts"]["controls"]["executed"], 6, data["counts"])
        self.assertEqual({g: r["outcome"] for g, r in _rows(data).items()},
                         {g: r["outcome"] for g, r in _rows(first).items()})
        self.assertEqual({g: r["rho"] for g, r in _rows(data).items()},
                         {g: r["rho"] for g, r in _rows(first).items()})
        self.assertEqual(_entries(self.project), entries, "a forced re-run wrote a new entry")

    def test_only_writes_no_last_check(self):
        """S-20: a filtered sweep keeps no project-wide bookkeeping; the next
        full one does."""
        last = _state(self.project, "cache", "last_check.json")
        only = _run(self.project, "check", "--only", "bracket.deflection", "--json")
        self.assertEqual(only.returncode, 1, only.stdout + only.stderr)
        self.assertEqual(list(_rows(_json(only))), ["bracket.deflection"])
        self.assertFalse(os.path.exists(last), "--only wrote last_check.json")
        full = _run(self.project, "check")
        self.assertEqual(full.returncode, 1, full.stdout + full.stderr)
        self.assertTrue(os.path.isfile(last))

        # The gates a filtered sweep leaves out keep their effective verdicts,
        # as rows that say whether each is current (spec §3.13, `carried_over`).
        again = _json(_run(self.project, "check", "--only", "bracket.deflection", "--json"))
        carried = {row["gate"]: row for row in again["carried_over"]}
        self.assertEqual(sorted(carried), sorted(BRACKET_GATES[1:]))
        for gate, row in carried.items():
            with self.subTest(gate=gate):
                self.assertTrue(row["fresh"] and row["cached"], row)
                self.assertNotIn("duration_s", row)

    def test_a_cached_pass_where_its_tool_is_missing(self):
        """Invariant 1 through `check`: a Fresh PASS whose gate declares a module
        this machine lacks resolves skipped — "cached pass exists; …" — and its
        claim BLOCKED, never PASS. S-30: the skipped gate keeps the parameters it
        read when it last ran.

        `bracket.bearing` because it is C3's only gate: a skip beside a pass on
        a claim two gates cover reads PASS with the skip named PARTIAL (the
        report's rule until P2's Kleene rule), which would test that rule
        instead of this one. Availability is patched in-process rather than a
        module hidden: the property is what the spine does with the answer."""
        first = _run(self.project, "check")
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        real = gates_mod.availability
        why = "requires python planted_probe (not importable)"

        def patched(spec):
            return (False, why) if spec.id == "bracket.bearing" else real(spec)

        with mock.patch.object(gates_mod, "availability", side_effect=patched):
            code, out, err = _in_process(["check", "--json", "-C", self.project])
            status_code, status_out, _err = _in_process(["status", "--json", "-C", self.project])
        self.assertEqual(code, 1, out + err)
        data = json.loads(out)
        row = _rows(data)["bracket.bearing"]
        self.assertTrue(row["skipped"], row)
        self.assertEqual(row["skip_reason"], f"cached pass exists; {why} here")
        self.assertFalse(row["cached"], "a skip is not the cached PASS")
        self.assertIn({"claim": "C3", "status": "blocked"},
                      [{"claim": b["claim"], "status": b["status"]} for b in data["blocking"]])
        self.assertEqual(status_code, 0)
        self.assertEqual(json.loads(status_out)["claims"]["C3"], "blocked")

        with open(_state(self.project, "ledger.json"), encoding="utf-8") as fh:
            params = {p["name"]: p["gates"] for p in json.load(fh)["params"]}
        # `n_bolts` is the one config field `bracket.bearing` reads by name (its
        # other inputs reach it through `bearing_area`, which `Param.gates` does
        # not follow — see `cli._param_gates`).
        self.assertEqual(params["n_bolts"], ["bracket.bearing"], "S-30: the skip erased it")


class SelftestFilesItsControls(_env.EnvCase):
    """S-08: `gate selftest` in a project runs every control on the known-good
    host and files control entries; `gate show` reads its last selftest off them."""

    def test_gate_show_reads_the_selftest(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"))
        never = _json(_run(project, "gate", "show", "bracket.deflection", "--json"))
        self.assertIsNone(never["last_selftest"], "nothing has demonstrated it yet")

        dry = _run(project, "gate", "selftest", "--no-record")
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertEqual(_entries(project), ([], []), "--no-record filed a control entry")

        ran = _run(project, "gate", "selftest")
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertTrue(_transcript.SELFTEST_SUMMARY.fullmatch(ran.stdout.splitlines()[-1]),
                        ran.stdout)
        verdicts, controls = _entries(project)
        self.assertEqual((len(verdicts), len(controls)), (0, 6), (verdicts, controls))

        shown = _json(_run(project, "gate", "show", "bracket.deflection", "--json"))
        last = shown["last_selftest"]
        self.assertIsNotNone(last, "S-08: gate show still reads a key nothing writes")
        self.assertEqual((last["outcome"], last["admission"], last["at_this_version"]),
                         ("pass", "admitted", True), last)
        mine = [p for p in controls if p.split("/")[2] == "bracket.deflection"]
        self.assertEqual(len(mine), 1)
        with open(os.path.join(project, *mine[0].split("/")), encoding="utf-8") as fh:
            self.assertEqual(last["control"], json.load(fh)["rho"])
        text = _run(project, "gate", "show", "bracket.deflection")
        self.assertTrue(_transcript.LAST_SELFTEST_FIRED.fullmatch(text.stdout.splitlines()[-1]),
                        text.stdout)

        again = _run(project, "gate", "selftest")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(_entries(project), (verdicts, controls),
                         "a second selftest wrote a new control entry for unchanged inputs")
        check = _json(_run(project, "check", "--json"))
        self.assertEqual(check["counts"]["controls"]["executed"], 0,
                         "check re-ran a control the selftest had just filed")
        self.assertEqual(check["counts"]["controls"]["cached"], 6, check["counts"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
