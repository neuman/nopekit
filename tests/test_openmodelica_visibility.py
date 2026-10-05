# SPDX-License-Identifier: Apache-2.0
"""openmodelica: omc is handed only what it can see from the directory it runs in.

What slipped through: the tier-2 gates ran omc from ``<out_dir>/omc`` and handed
``loadFile`` absolute paths to sources that live somewhere else — the project
tree, or the pack's own ``selftest/assets`` under ``gate selftest``. An omc in a
container sees only what its wrapper mounts, and a wrapper can only guess: the one
``references/installing.md`` gave mounts ``$PWD``, ``$HOME`` and the temp
directory. The suite runs every child under a temp ``HOME`` (``tests/_env.py``),
so a checkout under the real home was invisible to omc. omc 1.22 then prints
``loadFile(ThermalTank.mo) = false`` with an EMPTY error string, all three tier-2
gates read FAIL ("the sources did not load") on the pack's own good baseline, and
their controls were counted as fired on that same invisibility. The full suite was
green only where the checkout happened to sit under ``/tmp``.

The stand-in ``omc`` below sees exactly what a container that mounts only its
working directory sees: a path resolves only under the directory it was started
in. That is the one mount every wrapper must make, because omc writes its build
there. No omc is needed to run this file.

* the violation half: on the pack's own baseline, every path a script hands
  ``loadFile`` lies under the directory omc runs in and holds the source's
  bytes, and nothing reads "the sources did not load";
* what the staging must keep: a package's directory layout, ``package.order``
  and ``Resources/`` (``modelica://`` URIs resolve against the package's
  directory), never a dot-directory, never omc's own work directory when a source
  entry contains it, and never a file deleted since the last run — a stale copy
  loaded through ``package.mo``'s directory would be a model nobody wrote;
* an error omc reports against a staged copy names the user's file, so nobody
  edits a copy the next run overwrites;
* the stand-in's own control: handed a path outside its directory, it answers
  ``false``. Without it a stand-in that saw everything would make the violation
  half pass on the old code.

Run:  PYTHONPATH=src python3 -m unittest tests.test_openmodelica_visibility -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit.models import Ledger, ProjectMeta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(REPO, "packs", "openmodelica")
TIER2 = ("modelica.checks", "modelica.compiles", "modelica.simulates")

#: The stand-in. It interprets only the statements the gates generate, and it
#: answers each ``loadFile`` the way a container mounting nothing but its working
#: directory would. ``STANDIN_ERROR`` set makes it report a parse error against
#: the first file it loaded, in omc 1.22's own format, in the check's error
#: segment.
STANDIN = r'''
import json, os, re, sys
CALLS = {calls!r}
cwd = os.path.realpath(os.getcwd())

def visible(path):
    real = os.path.realpath(path)
    return (real == cwd or real.startswith(cwd + os.sep)) and os.path.isfile(real)

def unescape(text):
    return text.replace('\\"', '"').replace("\\\\", "\\")

script = sys.argv[1]
if not visible(script):
    print("Error: Failed to open file " + script)
    sys.exit(1)
loads, out, segment = [], [], ""
for line in open(script, encoding="utf-8").read().splitlines():
    m = re.match(r'print\("(@@NOPEKIT:(\w+))\\n"\);$', line)
    if m:
        out.append(m.group(1))
        segment = m.group(2)
        continue
    m = re.search(r'String\(loadModel\((\w+)\)\)', line)
    if m:
        out.append("loadModel(%s) = true" % m.group(1))
        continue
    m = re.match(r'print\("loadFile\((.*?)\) = " \+ String\(loadFile\("((?:[^"\\]|\\.)*)"\)\)', line)
    if m:
        path = unescape(m.group(2))
        loads.append(path)
        out.append("loadFile(%s) = %s" % (m.group(1), "true" if visible(path) else "false"))
        continue
    m = re.match(r'print\(checkModel\((.+)\) \+ "\\n"\);$', line)
    if m:
        out.append("Check of %s completed successfully." % m.group(1))
        out.append("Class %s has 2 equation(s) and 2 variable(s)." % m.group(1))
        continue
    if line.startswith("print(getErrorString()"):
        if segment == "checkerr" and os.environ.get("STANDIN_ERROR") and loads:
            out.append("[%s:3:1-3:1:writable] Error: Missing token: SEMICOLON" % loads[0])
        out.append("")
with open(CALLS, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"cwd": cwd, "loads": loads}}) + "\n")
sys.stdout.write("\n".join(out) + "\n")
'''


def _load_helper():
    """``gates/_modelica.py`` under a name no pack loader uses (as
    ``test_openmodelica_build`` does, and for the same reason)."""
    name = "_test_openmodelica_helper_visibility"
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(PACK, "gates", "_modelica.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _under(path: str, directory: str) -> bool:
    real, base = os.path.realpath(path), os.path.realpath(directory)
    return real == base or real.startswith(base + os.sep)


def _bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


@unittest.skipIf(os.name == "nt", "the stand-in omc is a #! script")
class OmcSeesOnlyWhereItRuns(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nopekit-omc-vis-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = os.path.join(self.dir, "bin")
        os.makedirs(self.bin)
        self.calls = os.path.join(self.dir, "calls.jsonl")
        omc = os.path.join(self.bin, "omc")
        with open(omc, "w", encoding="utf-8") as fh:
            fh.write(f"#!{sys.executable}\n" + STANDIN.format(calls=self.calls))
        os.chmod(omc, os.stat(omc).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.path = self.bin + os.pathsep + os.environ.get("PATH", "")
        self.registry = gates_mod.Registry()
        packs_mod.load_gates("openmodelica", self.registry, root=REPO)

    def _calls(self) -> list[dict]:
        if not os.path.isfile(self.calls):
            return []
        with open(self.calls, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def _run(self, gid: str, ctx, env: dict | None = None):
        spec_fn = self.registry.get(gid)
        self.assertIsNotNone(spec_fn, f"{gid} is not in the pack")
        before = len(self._calls())
        with mock.patch.dict(os.environ, {"PATH": self.path, **(env or {})}):
            verdict = gates_mod.run_gate(*spec_fn, ctx)
        calls = self._calls()
        # A stand-in that never ran (a noexec temp dir, another omc first on PATH)
        # would leave every assertion below about nothing.
        self.assertEqual(len(calls), before + 1,
                         f"{gid}: the stand-in omc was not the one launched: {verdict.render()}")
        return verdict, calls[-1]

    def _ctx(self, root: str, params: dict, out_dir: str):
        return gates_mod.GateContext(
            root=root, ledger=Ledger(meta=ProjectMeta(name="visibility")), model=None,
            params=params, out_dir=out_dir, tier=3, log=lambda _m: None, extra={})

    def _project(self) -> tuple[str, dict]:
        """A structured package, a resource, a package.order and a dot-directory."""
        root = os.path.join(self.dir, "proj")
        pkg = os.path.join(root, "model", "Pkg")
        _write(os.path.join(pkg, "package.mo"), "package Pkg\nend Pkg;\n")
        _write(os.path.join(pkg, "package.order"), "Run\nPart\n")
        _write(os.path.join(pkg, "Run.mo"), "within Pkg;\nmodel Run\nend Run;\n")
        _write(os.path.join(pkg, "Part.mo"), "within Pkg;\nmodel Part\nend Part;\n")
        _write(os.path.join(pkg, "Resources", "Data", "table.txt"), "#1\ndouble t(2,2)\n0 0\n1 1\n")
        _write(os.path.join(root, "model", ".scratch", "Old.mo"), "model Old\nend Old;\n")
        return root, {"modelica_class": "Pkg.Run", "modelica_sources": ["model"],
                      "modelica_stop_time_s": 1.0}

    # -- the stand-in's own control ------------------------------------------ #
    def test_the_stand_in_cannot_see_outside_its_directory(self):
        M = _load_helper()
        outside = os.path.join(PACK, "selftest", "assets", "model", "ThermalTank.mo")
        work = os.path.join(self.dir, "work")
        with mock.patch.dict(os.environ, {"PATH": self.path}):
            run = M.run_mos(M.mos_preamble([outside]), work, "probe")
        self.assertIn("loadFile(ThermalTank.mo) = false", run.segment("load"),
                      f"the stand-in saw a path outside its directory: {run.stdout!r}")

    # -- the violation half --------------------------------------------------- #
    def test_the_packs_own_baseline_loads_from_where_omc_runs(self):
        for gid in TIER2:
            with self.subTest(gate=gid):
                out = os.path.join(self.dir, "out", gid)
                ctx = packs_mod.baseline_context(PACK, out_dir=out)
                verdict, call = self._run(gid, ctx)
                self.assertTrue(call["loads"], f"{gid} loaded nothing")
                for path in call["loads"]:
                    self.assertTrue(_under(path, call["cwd"]),
                                    f"{gid} handed omc {path}, outside {call['cwd']}")
                    original = os.path.join(PACK, "selftest", "assets", "model",
                                            os.path.basename(path))
                    self.assertEqual(_bytes(path), _bytes(original),
                                     f"{gid}: the copy omc read is not the source")
                self.assertNotIn("did not load", verdict.render())
                if gid == "modelica.checks":
                    self.assertEqual(verdict.outcome, "pass", verdict.render())

    # -- what the staging must keep ------------------------------------------- #
    def test_a_package_keeps_its_layout_resources_and_order(self):
        root, params = self._project()
        verdict, call = self._run("modelica.checks", self._ctx(
            root, params, os.path.join(root, ".nopekit", "out")))
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        loads = call["loads"]
        self.assertEqual([os.path.basename(p) for p in loads],
                         ["package.mo", "Part.mo", "Run.mo"],
                         "package.mo must load first, and nothing from a dot-directory")
        staged_pkg = os.path.dirname(loads[0])
        self.assertEqual(os.path.basename(staged_pkg), "Pkg",
                         "omc refuses a package.mo whose directory is not named for it")
        source_pkg = os.path.join(root, "model", "Pkg")
        for rel in ("package.mo", "package.order", "Run.mo", "Part.mo",
                    os.path.join("Resources", "Data", "table.txt")):
            staged = os.path.join(staged_pkg, rel)
            self.assertTrue(os.path.isfile(staged), f"{rel} was not staged")
            self.assertEqual(_bytes(staged), _bytes(os.path.join(source_pkg, rel)), rel)
        stage_root = os.path.dirname(os.path.dirname(staged_pkg))
        for dirpath, dirnames, _files in os.walk(stage_root):
            self.assertNotIn(".scratch", dirnames, f"a dot-directory was staged under {dirpath}")

    def test_a_deleted_source_is_not_loaded_from_an_old_copy(self):
        root, params = self._project()
        ctx = self._ctx(root, params, os.path.join(root, ".nopekit", "out"))
        _verdict, first = self._run("modelica.checks", ctx)
        staged_part = [p for p in first["loads"] if p.endswith("Part.mo")]
        self.assertEqual(len(staged_part), 1, first["loads"])
        self.assertTrue(_under(staged_part[0], first["cwd"]),
                        f"Part.mo was handed to omc from outside {first['cwd']}")
        os.remove(os.path.join(root, "model", "Pkg", "Part.mo"))
        _verdict, second = self._run("modelica.checks", ctx)
        self.assertNotIn("Part.mo", [os.path.basename(p) for p in second["loads"]])
        self.assertFalse(os.path.exists(staged_part[0]),
                         "a copy of a deleted source is still where package.mo's "
                         "directory load would find it")

    def test_a_source_entry_that_holds_the_work_directory_is_not_copied_into_itself(self):
        root, params = self._project()
        params = dict(params, modelica_sources=["."])
        out = os.path.join(root, "build")                 # not a dot-directory
        ctx = self._ctx(root, params, out)
        for _ in range(2):                                # the second run sees the first's stage
            verdict, call = self._run("modelica.checks", ctx)
            self.assertEqual(verdict.outcome, "pass", verdict.render())
            self.assertEqual(sorted(os.path.basename(p) for p in call["loads"]),
                             ["Part.mo", "Run.mo", "package.mo"], call["loads"])
            for path in call["loads"]:
                self.assertTrue(_under(path, call["cwd"]), path)
                self.assertNotIn(os.sep + "build" + os.sep,
                                 path[len(os.path.realpath(call["cwd"])):],
                                 f"the work directory was staged into itself: {path}")

    # -- errors name the user's file ------------------------------------------ #
    def test_an_error_against_a_staged_copy_names_the_users_file(self):
        root, params = self._project()
        verdict, call = self._run(
            "modelica.checks",
            self._ctx(root, params, os.path.join(root, ".nopekit", "out")),
            env={"STANDIN_ERROR": "1"})
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        user_file = os.path.join(root, "model", "Pkg", "package.mo")
        self.assertIn(f"[{user_file}:3:1", verdict.detail)
        self.assertNotIn(call["loads"][0], verdict.detail,
                         "the verdict sends its reader to a copy the next run overwrites")


if __name__ == "__main__":
    unittest.main()
