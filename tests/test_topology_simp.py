# SPDX-License-Identifier: Apache-2.0
"""SIMP proposes; independently meshed exported geometry is what the gates judge."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock

import _env
from nopekit import packs
from nopekit.gates import Registry, run_gate
from nopekit.modelio import load_path

PACK = Path(_env.REPO) / "packs" / "topology-simp"
OPT_DEPS = all(importlib.util.find_spec(x) for x in ("numpy", "scipy", "numba"))
FEA_DEPS = shutil.which("ccx") and all(importlib.util.find_spec(x) for x in ("numpy", "trimesh", "gmsh"))


class Separation(unittest.TestCase):
    def test_proposer_does_not_register_as_a_gate(self):
        registry = Registry()
        packs.load_gates("topology-simp", registry)
        self.assertEqual({s.id for s in registry.specs()},
                         {"simp.case_valid", "simp.volume", "simp.displacement", "simp.stress"})
        manifest = packs.read_manifest(str(PACK))
        self.assertEqual(manifest.provides_proposers[0]["id"], "fea.topopt3d")
        code = (PACK / "gates" / "_part.py").read_text()
        self.assertNotIn("topopt3d", code)
        self.assertNotIn("_fe3d", code)

    def test_negative_control_has_no_host_input(self):
        cases = load_path(str(PACK / "selftest" / "cases.py"))
        with tempfile.TemporaryDirectory() as out:
            ctx = packs.baseline_context(str(PACK), out_dir=out)
            ctx.params = {"unrelated": "host"}
            bad = cases.too_soft(ctx)
            self.assertEqual(bad.params["simp_case"]["young_mpa"], 500)
            self.assertNotIn("unrelated", bad.params)
            self.assertEqual(ctx.params, {"unrelated": "host"})


@unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy absent")
class SolverOutput(unittest.TestCase):
    def test_empty_success_cannot_reuse_an_old_result(self):
        ccx = load_path(str(PACK / "gates" / "_ccx.py"))
        with tempfile.TemporaryDirectory() as folder:
            deck = Path(folder) / "job.inp"
            deck.write_text("*HEADING\nfixture\n")
            result = deck.with_suffix(".frd")
            result.write_text("old result that must not count\n")
            with mock.patch.object(ccx.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")):
                with self.assertRaisesRegex(RuntimeError,"ccx failed"):
                    ccx.run(str(deck), ccx="fixture-ccx")
            self.assertFalse(result.exists())


@unittest.skipUnless(OPT_DEPS, "SIMP numerical dependencies absent")
class Proposer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.opt = load_path(str(PACK / "proposers" / "topopt3d.py"))
        cls.example = load_path(str(PACK / "scaffold" / "cantilever.py"))
        # Tiny controls must not pay the machine's full thread count per element.
        import numba
        cls.threads = numba.get_num_threads()
        numba.set_num_threads(1)

    @classmethod
    def tearDownClass(cls):
        import numba
        numba.set_num_threads(cls.threads)

    def test_load_on_fixed_or_void_node_is_rejected(self):
        p = self.example.problem(6, 3, 3)
        p["load_0_dofs"][0] = 0
        with self.assertRaisesRegex(ValueError, "active free"):
            self.opt.validate_problem(p)

    def test_conflicting_symmetry_and_invalid_budget_are_rejected(self):
        p = self.example.problem(6, 3, 3)
        p["budget_mm3"] = -1
        with self.assertRaisesRegex(ValueError, "budget"):
            self.opt.validate_problem(p)
        p = self.example.problem(6, 3, 3)
        p["symmetry"] = "D2"
        p["region"][0,0,2] = 2
        with self.assertRaisesRegex(ValueError, "symmetry"):
            self.opt.validate_problem(p)

    def test_simp_respects_solid_void_and_budget(self):
        import numpy as np
        p = self.example.problem(8, 4, 4)
        p["region"][0,0,3] = 2
        rho, _, history, _ = self.opt.optimise(p, iters=30, log=lambda _: None)
        self.assertTrue(np.isfinite(rho).all())
        self.assertTrue((rho[p["region"] == 1] == 1).all())
        self.assertTrue((rho[p["region"] == 2] == 0).all())
        volume = float((rho * p["cost"])[p["region"] == 0].sum() * p["h"]**3)
        self.assertLessEqual(volume, p["budget_mm3"] * 1.01)
        self.assertGreater(len(history), 20)

    def test_hex_solver_matches_beam_and_direct_path(self):
        import numpy as np
        fe = self.opt.fe
        nx, ny, nz = 24, 6, 6
        ids = np.arange((nx+1)*(ny+1)*(nz+1))
        tip = ids[ids % (nx+1) == nx]
        solver = fe.Solver(nx,ny,nz,1.,.3,np.ones(nx*ny*nz,bool),ids[ids % (nx+1)==0])
        solver.set_E(np.full(nx*ny*nz,1000.))
        f=np.zeros(solver.n); f[3*tip+2]=-1/len(tip)
        u,_,res=solver.solve(f)
        I=ny*nz**3/12; G=1000/2.6
        expected=nx**3/(3*1000*I)+nx/(5/6*G*ny*nz)
        self.assertAlmostEqual(float(-u[3*tip+2].mean())/expected,1.,delta=.08)
        self.assertLess(res,1e-8)

    def test_hosted_output_cannot_escape(self):
        script = str(PACK / "proposers" / "topopt3d.py")
        with tempfile.TemporaryDirectory() as d:
            env={"NOPEKIT_PROPOSAL_DIR": d}
            p=_env.run([sys.executable,script,"--problem","missing.npz","--out",d+"-outside"],
                             env=env, cwd=d)
            self.assertNotEqual(p.returncode,0)
            self.assertIn("NOPEKIT_PROPOSAL_DIR",p.stderr)


@unittest.skipUnless(FEA_DEPS, "CalculiX, gmsh or mesh dependencies absent")
class ExportedPart(unittest.TestCase):
    def test_real_part_and_negative_controls(self):
        result = packs.demonstrate(str(PACK), tier=2)
        self.assertEqual(result.problems, [])

    def test_optimizer_score_cannot_override_an_overloaded_part(self):
        registry=Registry();packs.load_gates("topology-simp",registry)
        cases=load_path(str(PACK / "selftest" / "cases.py"))
        with tempfile.TemporaryDirectory() as out:
            ctx=packs.baseline_context(str(PACK),out_dir=out)
            bad=cases.overloaded(ctx)
            bad.params["optimizer_compliance"]=0
            bad.params["optimizer_says_pass"]=True
            verdict=run_gate(*registry.get("simp.stress"),bad)
            self.assertFalse(verdict.passed)
            self.assertFalse(verdict.error,verdict.error)
            self.assertGreater(verdict.measured,verdict.limit)

    def test_missing_or_corrupted_solver_output_never_passes(self):
        part=load_path(str(PACK / "gates" / "_part.py"))
        ccx=load_path(str(PACK / "gates" / "_ccx.py"))
        with tempfile.TemporaryDirectory() as out:
            ctx=packs.baseline_context(str(PACK),out_dir=out)
            with mock.patch.object(ccx,"frd_blocks",return_value=[]):
                with self.assertRaisesRegex(RuntimeError,"exactly one"):
                    part.solve(ctx,part.case(ctx),"broken")
