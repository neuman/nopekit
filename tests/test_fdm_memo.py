# SPDX-License-Identifier: Apache-2.0
"""fdm-print shares through the spine: a file read is a read of every gate that
makes it, and a helper loaded by path is the copy that sits beside the gate.

Two defects, both in how one pack shared things between its own modules.

**S-27.** fdm-print kept its parsed meshes in a cache on ``ctx.extra`` — the one
channel every gate of a sweep had in common — keyed on ``os.stat``. The second
mesh gate to want the part got a hit that opened nothing, and ``os.stat`` raises
no audit event, so ``fdm.bridge_span``'s trace said it had read no file at all:
its verdict would have been keyed as if the part could change under it freely.
Per-gate ``extra`` (U14) shut the channel, and in shutting it quietly turned the
cache into dead code — every gate parsed every part again, and PACK.md's "13
loads, not 26" became false without a test noticing. The cache now lives in the
spine's per-sweep memo (``GateContext.load_file``), which records the file for
each caller, hit or miss.

**S-26, the fdm half.** ``_sibling_module`` and ``bad_params._helper`` loaded
helpers by path under FIXED module names and served whatever ``sys.modules``
held under that name. A second copy of the pack in one process — a project's
shadow, a user pack, ``test_pack_keys``' twin — ran the first copy's arithmetic
while every message named the second (packs:H4). They go through
``modelio.load_path`` now, which salts the name with the path and serves a
cached module only while its bytes are unchanged.

Run:  PYTHONPATH=src python3 -m unittest tests.test_fdm_memo -v
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
import unittest
from unittest import mock

from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit.gates import GateContext
from nopekit.models import Ledger, ProjectMeta, Tier

import _env

REPO = _env.REPO
PACK_DIR = os.path.join(REPO, "packs", "fdm-print")
PART = os.path.abspath(os.path.join(PACK_DIR, "selftest", "baseline_part.stl"))
MESH_GATES = ("fdm.overhang", "fdm.bridge_span")

#: The ``extra`` key fdm-print's mesh cache lived under until U17. Spelled out
#: here, not imported: the pack no longer has it, and a sweep context that
#: arrives carrying a dict under it is exactly how the channel stays open after
#: per-gate ``extra`` — the per-gate copy is SHALLOW, so a dict nested in the
#: sweep's ``extra`` is one object for every gate of the sweep.
_OLD_CACHE_KEY = "_fdm_print_mesh_cache"


def _baseline() -> dict:
    with open(os.path.join(PACK_DIR, "selftest", "baseline.json"), encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


class FdmReadsAreRecorded(_env.EnvCase):
    """Both mesh gates decide on the part, so both traces name it (S-27) — and the
    part is still parsed once per sweep, which is what the cache was for."""

    def setUp(self):
        self.registry = gates_mod.Registry()
        packs_mod.load_gates("fdm-print", self.registry, root=REPO,
                             include_env=False, include_user=False)

    def _sweep(self, extra: dict):
        memo: dict = {}
        ctx = GateContext(root=PACK_DIR, ledger=Ledger(meta=ProjectMeta(name="fdm-memo")),
                          params=_baseline(), out_dir=self.tmp(), tier=int(Tier.BUILD),
                          extra=extra, memo=memo)
        traces: dict = {}
        verdicts = gates_mod.run_all(
            self.registry, ctx, max_tier=int(Tier.BUILD), only=MESH_GATES,
            after=lambda spec, fn, verdict, trace: traces.__setitem__(spec.id, trace))
        return {v.gate: v for v in verdicts}, traces, memo

    def test_overhang_and_bridge_span_both_record_the_part(self):
        spec = self.registry.get("fdm.overhang")[0]
        available, reason = gates_mod.availability(spec)
        if not available:
            # The CI-like run hides trimesh: the outcome to assert is the
            # availability skip, never a quiet pass and never a skipped test.
            verdicts, _traces, memo = self._sweep({})
            self.assertEqual(sorted(verdicts), sorted(MESH_GATES))
            for gate_id in MESH_GATES:
                verdict = verdicts[gate_id]
                self.assertTrue(verdict.skipped and not verdict.passed, verdict.render())
                self.assertEqual(verdict.skip_reason, reason, verdict.render())
            self.assertEqual(memo, {}, "a gate that never ran loaded something")
            return

        import trimesh                              # noqa: PLC0415 - tools present only

        scenarios = (
            ("a fresh sweep context", {}),
            ("a sweep context carrying the old cache channel", {_OLD_CACHE_KEY: {}}),
        )
        for label, extra in scenarios:
            with self.subTest(label):
                before = json.dumps(sorted(extra))
                with mock.patch.object(trimesh, "load_mesh",
                                       wraps=trimesh.load_mesh) as parse:
                    verdicts, traces, memo = self._sweep(extra)
                for gate_id in MESH_GATES:
                    self.assertTrue(verdicts[gate_id].passed, verdicts[gate_id].render())
                    self.assertIn(
                        PART, traces[gate_id].files_read,
                        f"S-27: {gate_id} decided on {PART} and its trace does not name "
                        f"it — a cache hit that opened nothing, so its verdict would be "
                        f"keyed as if the part could change freely")
                parses = [call for call in parse.call_args_list
                          if os.path.abspath(os.fspath(call.args[0])) == PART]
                self.assertEqual(
                    len(parses), 1,
                    f"the part was parsed {len(parses)} times in one sweep; the memo "
                    f"is what lets fdm.bridge_span reuse fdm.overhang's parse")
                self.assertTrue(
                    any(isinstance(key, tuple) and key and key[0] == PART for key in memo),
                    f"the part is not in the sweep's memo: {sorted(map(repr, memo))}")
                self.assertEqual(
                    json.dumps(sorted(extra)), before,
                    "a mesh gate wrote into the sweep's extra")
                self.assertEqual(extra.get(_OLD_CACHE_KEY, {}), {},
                                 "a mesh gate filled a cache nested in the sweep's extra")


#: Run in a child: it loads a second copy of the pack into the process and edits
#: that copy's helper between two loads, and neither may leak into the suite.
_TWIN_SCRIPT = textwrap.dedent('''
    import json, os, sys, types

    from nopekit import gates, packs
    from nopekit.gates import GateContext
    from nopekit.models import Ledger

    bundled, twin, project = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(os.path.join(bundled, "selftest", "baseline.json"), encoding="utf-8") as fh:
        params = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}

    def load(name):
        registry = gates.Registry()
        packs.load_gates(name, registry, root=project, include_env=False,
                         include_user=False)
        return registry

    def run(registry):
        spec, fn = registry.get("fdm.print_time_est")
        v = gates.run_gate(spec, fn, GateContext(root=project, ledger=Ledger(),
                                                 params=dict(params)))
        return {"passed": v.passed, "measured": v.measured, "detail": v.detail,
                "error": v.error}

    def helpers(registry):
        """Where every pack-file module a gate module holds was loaded from."""
        out = {}
        for spec in registry.specs():
            module = sys.modules[registry.get(spec.id)[1].__module__]
            for attr, value in vars(module).items():
                where = getattr(value, "__file__", None)
                if not isinstance(value, types.ModuleType) or not where:
                    continue
                where = os.path.abspath(where)
                for label, root in (("bundled", bundled), ("twin", twin)):
                    if where.startswith(root + os.sep):
                        key = os.path.basename(module.__file__) + ":" + attr
                        out[key] = label
        return out

    result = {}
    original = load("fdm-print")
    result["bundled"] = run(original)
    first_twin = load("fdm-twin")
    result["twin"] = run(first_twin)
    result["bundled_helpers"] = helpers(original)
    result["twin_helpers"] = helpers(first_twin)

    target = os.path.join(twin, "gates", "_process_model.py")
    with open(target, encoding="utf-8") as fh:
        source = fh.read()
    edited = source.replace("STARTUP_OVERHEAD_S = 300.0", "STARTUP_OVERHEAD_S = 172800.0")
    result["edited"] = edited != source
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(edited)

    result["twin_after_edit"] = run(load("fdm-twin"))
    result["bundled_after_edit"] = run(load("fdm-print"))
    print(json.dumps(result))
''')


class FdmHelpersArePerCopy(_env.EnvCase):
    """A second copy of fdm-print runs its OWN helpers, and follows an edit to them
    (S-26, packs:H4)."""

    def test_twin_copy_runs_its_own_helpers(self):
        tmp = self.tmp()
        project = os.path.join(tmp, "project")
        twin = os.path.join(project, ".nopekit", "packs", "fdm-twin")
        shutil.copytree(PACK_DIR, twin,
                        ignore=shutil.ignore_patterns("__pycache__", ".selftest-out"))
        manifest_path = os.path.join(twin, "pack.json")
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        manifest["name"] = "fdm-twin"
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        script = os.path.join(tmp, "twin.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(_TWIN_SCRIPT)

        proc = _env.run([sys.executable, script, os.path.abspath(PACK_DIR),
                         os.path.abspath(twin), os.path.abspath(project)], cwd=tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])
        result = json.loads(proc.stdout.strip().splitlines()[-1])

        bundled = result["bundled"]
        self.assertTrue(bundled["passed"], bundled)
        self.assertEqual(result["twin"], bundled,
                         "two copies of the same bytes gave two verdicts")
        self.assertTrue(result["edited"], "the edit this test relies on no longer applies "
                                          "to _process_model.py; re-aim it")

        # Every pack-file module a gate module holds comes from its own copy. The
        # key set is pinned so the check cannot pass by finding nothing to check.
        for label, table in (("bundled", result["bundled_helpers"]),
                             ("twin", result["twin_helpers"])):
            for key in ("printability.py:_pm", "printability.py:_PARTS",
                        "printability.py:_FOLD", "mesh.py:FOLD", "mesh.py:PARTS"):
                self.assertIn(key, table, f"{label}: {key} is not a module from a pack "
                                          f"file any more: {table}")
            strays = {key: where for key, where in table.items() if where != label}
            self.assertEqual(strays, {}, f"the {label} copy's gate modules hold helpers "
                                         f"loaded from the other copy's files")

        # The twin's startup overhead is now 48 h; its gate follows its own file.
        after = result["twin_after_edit"]
        self.assertFalse(after["passed"], f"the twin's gate did not follow the edit to "
                                          f"its own _process_model.py: {after}")
        self.assertAlmostEqual(after["measured"] - bundled["measured"],
                               (172800.0 - 300.0) / 3600.0 / 24.0, places=2, msg=after)
        self.assertEqual(result["bundled_after_edit"], bundled,
                         "the bundled copy picked up the twin's edit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
