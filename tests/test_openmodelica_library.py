# SPDX-License-Identifier: Apache-2.0
"""openmodelica: a library omc cannot see is the machine's absence, not the model's defect.

What slipped through: a project that names the Modelica Standard Library in
``modelica_load_libraries``, run against an omc with no MSL installed (the
``-minimal`` docker image ships none, and its container HOME is ``/``, so the
package manager cannot even write its index), had all three tier-2 gates read
FAIL with "the sources did not load, so nothing was checked" — about sources
that had loaded (``loadFile(...) = true``). The ``loadModel(Modelica) = false``
line sat in the script's ``libraries`` segment, which no gate read; only the
buffered error string was seen, it was blamed on the sources, and the line that
named the missing package was the fifth error of a detail that keeps three. A
claim read Failing and sent its owner to edit a model nothing had looked at.

The pack's own selftest could not see this: its fixtures load no library, on
purpose, so they run on a bare omc. So the transcripts below are omc 1.22.0's
own output, recorded on a machine without MSL and on one with it, and replayed
by a stand-in ``omc`` on PATH — no omc is needed to run this file.

The violation half: a library that did not load must SKIP (BLOCKED), naming the
library and omc's own reason. The control half keeps that from becoming a blanket
excuse: a SOURCE that did not parse is still FAIL, with or without the library,
and none of these ever reads as a pass.

Run:  PYTHONPATH=src python3 -m unittest tests.test_openmodelica_library -v
"""
from __future__ import annotations

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
TIER2 = ("modelica.checks", "modelica.compiles", "modelica.simulates")

#: The load half of a gate's script as omc 1.22.0 (``-minimal`` docker image, no
#: MSL, HOME=/) printed it, verbatim but for the source path.
LIBRARY_MISSING = """\
@@NOPEKIT:libraries

loadModel(Modelica) = false

@@NOPEKIT:load

loadFile(Tank.mo) = true

@@NOPEKIT:loaderr

Error: Failed to open file for writing: //.openmodelica/libraries/index.json.tmp1
Error: Failed to download package index https://libraries.openmodelica.org/index/v1/index.json to file //.openmodelica/libraries/index.json.
Error: Failed to open file for writing: //.openmodelica/libraries/index.json.tmp1
Error: Failed to download package index https://libraries.openmodelica.org/index/v1/index.json to file //.openmodelica/libraries/index.json.
Error: Failed to load package Modelica (default) using MODELICAPATH //.openmodelica/libraries/.


@@NOPEKIT:end
"""

#: The same omc with MSL installed, on a source missing one semicolon.
SOURCE_BROKEN = """\
@@NOPEKIT:libraries

loadModel(Modelica) = true

@@NOPEKIT:load

loadFile(Tank.mo) = false

@@NOPEKIT:loaderr

[/work/Tank.mo:3:1-3:1:writable] Error: Missing token: SEMICOLON


@@NOPEKIT:end
"""

#: No MSL AND the broken source: the parse error is the model's either way.
BOTH = """\
@@NOPEKIT:libraries

loadModel(Modelica) = false

@@NOPEKIT:load

loadFile(Tank.mo) = false

@@NOPEKIT:loaderr

Error: Failed to open file for writing: //.openmodelica/libraries/index.json.tmp1
Error: Failed to download package index https://libraries.openmodelica.org/index/v1/index.json to file //.openmodelica/libraries/index.json.
Error: Failed to load package Modelica (default) using MODELICAPATH //.openmodelica/libraries/.
[/work/Tank.mo:3:1-3:1:writable] Error: Missing token: SEMICOLON


@@NOPEKIT:end
"""


@unittest.skipIf(os.name == "nt", "the stand-in omc is a #! script")
class AMissingLibrary(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nopekit-omc-lib-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = os.path.join(self.dir, "bin")
        os.makedirs(self.bin)
        self.calls = os.path.join(self.dir, "calls")
        with open(os.path.join(self.dir, "Tank.mo"), "w", encoding="utf-8") as fh:
            fh.write('model Tank "stand-in; omc never reads it here"\nend Tank;\n')
        self.registry = gates_mod.Registry()
        packs_mod.load_gates("openmodelica", self.registry, root=REPO)
        self.params = {
            "modelica_class": "Tank",
            "modelica_sources": ["Tank.mo"],
            "modelica_load_libraries": ["Modelica"],
            "modelica_stop_time_s": 10.0,
        }

    def _verdicts(self, transcript: str):
        omc = os.path.join(self.bin, "omc")
        with open(omc, "w", encoding="utf-8") as fh:
            fh.write(f"#!{sys.executable}\n"
                     f"import sys\n"
                     f"open({self.calls!r}, 'a').write('x')\n"
                     f"sys.stdout.write({transcript!r})\n")
        os.chmod(omc, os.stat(omc).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        path = self.bin + os.pathsep + os.environ.get("PATH", "")
        out = {}
        with mock.patch.dict(os.environ, {"PATH": path}):
            for gid in TIER2:
                spec_fn = self.registry.get(gid)
                self.assertIsNotNone(spec_fn, f"{gid} is not in the pack")
                ctx = gates_mod.GateContext(
                    root=self.dir, ledger=Ledger(meta=ProjectMeta(name="lib")),
                    model=None, params=dict(self.params),
                    out_dir=os.path.join(self.dir, "out", gid), tier=3,
                    log=lambda _m: None, extra={})
                out[gid] = gates_mod.run_gate(*spec_fn, ctx)
        with open(self.calls, encoding="utf-8") as fh:
            ran = len(fh.read())
        # A stand-in that never ran (a noexec temp dir) skips every gate for the
        # wrong reason; the count keeps that from reading as the fix working.
        self.assertEqual(ran, len(TIER2), "the stand-in omc was not the one launched")
        return out

    def test_a_library_omc_cannot_see_is_blocked_not_failed(self):
        for gid, verdict in self._verdicts(LIBRARY_MISSING).items():
            with self.subTest(gate=gid):
                self.assertEqual(verdict.outcome, "skipped",
                                 f"{gid} read {verdict.outcome}: {verdict.render()}")
                reason = verdict.skip_reason
                self.assertIn("loadModel(Modelica) = false", reason)
                self.assertIn("Failed to load package Modelica", reason,
                              "omc's own reason was cut from the verdict")
                self.assertIn("references/installing.md", reason,
                              "the remedy fell off the capped skip reason")
                self.assertNotIn("sources did not load", reason)

    def test_a_source_that_does_not_parse_still_fails(self):
        for gid, verdict in self._verdicts(SOURCE_BROKEN).items():
            with self.subTest(gate=gid):
                self.assertEqual(verdict.outcome, "fail", verdict.render())
                self.assertIn("loadFile(Tank.mo) = false", verdict.detail)

    def test_a_broken_source_is_not_hidden_behind_a_missing_library(self):
        for gid, verdict in self._verdicts(BOTH).items():
            with self.subTest(gate=gid):
                self.assertEqual(verdict.outcome, "fail", verdict.render())
                self.assertIn("loadFile(Tank.mo) = false", verdict.detail)


if __name__ == "__main__":
    unittest.main()
