# SPDX-License-Identifier: Apache-2.0
"""openmodelica: a build left on disk is never trusted by the next one.

What slipped through: the pack's own GOOD baseline read FAIL in about one full
suite run in three on a WSL2 machine, with "the GUID ... from input data file ...
does not match the GUID compiled in the model". ``modelica.compiles`` and
``modelica.simulates`` build the same class in the same work directory one after
the other; the wall clock stepped back between them, the regenerated main C file
was stamped earlier than the object the previous build had left, and omc's
makefile — which rebuilds the main object by mtime alone — linked the stale GUID.
The failure never hit the same test twice, so it read as a race between two
processes sharing an ``out_dir`` until the mtimes were recorded.

A backward clock step cannot be ordered up in a test, but its effect can: push
the main object's mtime into the future after a build and the next build of the
same class sees exactly what it saw then. The planted violator runs the same
scenario with the object-clearing step disabled and must FAIL, which is what
keeps the passing half from being vacuous on a machine where the plant stopped
reproducing the defect.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

from atompipe import gates as gates_mod
from atompipe import packs as packs_mod
from atompipe.models import Ledger, ProjectMeta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(REPO, "packs", "openmodelica")

#: How far into the future the planted object is pushed. The observed step was a
#: little over 0.1 s; 30 s is far past any build the two gates do, so the plant
#: does not depend on how fast this machine compiles. Rejected: 1 s, which a slow
#: runner's translate-and-compile of the main file can outlast, so the violator
#: would stop failing for a reason unrelated to the fix.
FUTURE_S = 30.0


def _load_helper():
    """``gates/_modelica.py`` under a name no pack loader uses.

    It is registered in ``sys.modules`` while it executes because its dataclasses
    resolve their string annotations through their own module there.
    """
    name = "_test_openmodelica_helper"
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


class ClearObjects(unittest.TestCase):
    """The helper alone: no omc needed."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="atompipe-omc-objs-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.M = _load_helper()

    def _touch(self, name):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as handle:
            handle.write("x")

    def test_only_object_files_go(self):
        for name in ("Tank.Run.o", "Tank.Run_01exo.o", "Tank.Run.c",
                     "Tank.Run_init.xml", "Tank.Run", "simulate.mos"):
            self._touch(name)
        os.mkdir(os.path.join(self.dir, "not_a_file.o"))
        removed = self.M.clear_objects(self.dir)
        self.assertEqual(removed, ["Tank.Run.o", "Tank.Run_01exo.o"])
        self.assertEqual(sorted(os.listdir(self.dir)),
                         ["Tank.Run", "Tank.Run.c", "Tank.Run_init.xml",
                          "not_a_file.o", "simulate.mos"])

    def test_a_missing_directory_is_nothing_to_clear(self):
        self.assertEqual(self.M.clear_objects(os.path.join(self.dir, "absent")), [])

    def test_run_mos_clears_the_work_directory(self):
        self._touch("Stale.o")
        absent = os.path.join(self.dir, "no-such-omc")   # nothing is launched
        run = self.M.run_mos(["// nothing"], self.dir, "probe", omc=absent)
        self.assertTrue(run.launch_error, "a missing omc was launched")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "Stale.o")),
                         "run_mos left a previous build's object for make to trust")


class AStaleObjectIsRebuilt(unittest.TestCase):
    """End to end through the two gates, on the pack's own baseline."""

    def setUp(self):
        self.registry = gates_mod.Registry()
        packs_mod.load_gates("openmodelica", self.registry, root=REPO)
        compiles = self.registry.get("modelica.compiles")
        simulates = self.registry.get("modelica.simulates")
        self.assertIsNotNone(compiles, "modelica.compiles is not in the pack")
        self.assertIsNotNone(simulates, "modelica.simulates is not in the pack")
        for spec, _fn in (compiles, simulates):
            ok, why = gates_mod.availability(spec)
            if not ok:
                self.skipTest(f"{spec.id} cannot run here: {why}")
        self.compiles, self.simulates = compiles, simulates
        with open(os.path.join(PACK, "selftest", "baseline.json"), encoding="utf-8") as fh:
            self.params = json.load(fh)

    def _build_then_simulate_over_a_future_object(self):
        out = tempfile.mkdtemp(prefix="atompipe-omc-stale-")
        self.addCleanup(shutil.rmtree, out, True)
        ctx = gates_mod.GateContext(
            root=PACK, ledger=Ledger(meta=ProjectMeta(name="selftest")), model=None,
            params=self.params, out_dir=out, tier=3, log=lambda _m: None, extra={})
        built = gates_mod.run_gate(*self.compiles, ctx)
        self.assertTrue(built.ok, f"the baseline did not build: {built.detail or built.error}")
        obj = os.path.join(out, "omc", f"{self.params['modelica_class']}.o")
        self.assertTrue(os.path.isfile(obj), f"no main object at {obj}")
        future = time.time() + FUTURE_S
        os.utime(obj, (future, future))
        return gates_mod.run_gate(*self.simulates, ctx)

    def test_the_baseline_simulates_over_a_stale_object(self):
        verdict = self._build_then_simulate_over_a_future_object()
        self.assertTrue(verdict.ok, verdict.detail or verdict.error)

    def test_without_clearing_the_stale_object_is_linked(self):
        _spec, fn = self.simulates
        helper = fn.__globals__["M"]
        with mock.patch.object(helper, "clear_objects", return_value=[]):
            verdict = self._build_then_simulate_over_a_future_object()
        self.assertFalse(verdict.ok, "the plant no longer reproduces a stale main "
                                     "object, so the passing test above proves nothing")
        self.assertIn("GUID", verdict.detail or "")


if __name__ == "__main__":
    unittest.main()
