# SPDX-License-Identifier: Apache-2.0
"""The spine imports the standard library only: CI's own AST walk, run here too.

What slipped through: CLAUDE.md's house rule (`src/atompipe/` is standard
library only, "CI proves this by AST-walking every import") was carried by one
step of `.github/workflows/ci.yml` and by nothing under `tests/`. The walk ran on
a runner and never in the loop a change is iterated in, so a function-local
`import numpy` would have stayed green through every local run, the fast tier
included, and turned red only after a push. And the step had never been shown a
violator: a walk pointed at the wrong directory says `clean` about nothing.

So this file runs THE STEP. Its heredoc is read out of `ci.yml` with
`test_ci_config.steps`, never copied, so the walk CI runs and the walk the suite
runs cannot drift apart, and deleting or renaming the step turns this red. It
runs once over the spine, where it must say clean, and once over a planted tree,
where it must refuse a top-level and a function-local third-party import (in a
nested package too) and leave alone a string that merely says `import yaml`, a
relative import and a standard-library one. Rejected: a second AST walk written
here. Two walks that agree today are two walks to keep in step, and the one CI
runs is the one that decides a merge.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_stdlib_only.py -v
"""
from __future__ import annotations

import os
import re
import sys
import textwrap
import unittest

import _env
import test_ci_config

#: The CI step that carries the house rule, by its `name:`. Renaming it in
#: ci.yml without renaming it here is red, by design: the step is found by name
#: or the rule is unenforced.
STEP_NAME = "Spine imports with no third-party packages installed"


def step_script(yaml_text: str, name: str = STEP_NAME) -> str | None:
    """The Python source the named step feeds to ``python -`` through a heredoc,
    or None when no step of that name holds one."""
    for step in test_ci_config.steps(yaml_text):
        if step["keys"].get("name") != name:
            continue
        body: list[str] = []
        tag: str | None = None
        for _number, line in step["run"]:
            if tag is None:
                match = re.search(r"<<-?\s*(['\"]?)(\w+)\1", line)
                if match:
                    tag = match.group(2)
            elif line.strip() == tag:
                return textwrap.dedent("\n".join(body)) + "\n"
            else:
                body.append(line)
    return None


def _ci_text() -> str:
    with open(test_ci_config.CI, "r", encoding="utf-8") as fh:
        return fh.read()


class SpineIsStdlibOnly(_env.EnvCase):
    def setUp(self) -> None:
        self.script = step_script(_ci_text())
        self.assertIsNotNone(
            self.script, f"ci.yml holds no step named {STEP_NAME!r} with a heredoc: "
                         f"the standard-library rule is enforced nowhere")
        self.assertIn("ast.walk", self.script, "the step no longer walks the AST")

    def _walk(self, root: str):
        return _env.run([sys.executable, "-c", self.script], cwd=root)

    def test_the_spine_imports_only_the_standard_library(self):
        proc = self._walk(_env.REPO)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("stdlib-only: clean", proc.stdout)

    def test_a_planted_third_party_import_is_refused(self):
        root = self.tmp()
        files = {
            "src/atompipe/planted.py": textwrap.dedent("""\
                import json
                import numpy
                from . import sibling
                NOTE = "import yaml"

                def load(path):
                    from trimesh.exchange import load as _load
                    return _load(path)
                """),
            "src/atompipe/sub/deep.py": "import scipy.optimize\n",
            "src/atompipe/sibling.py": "from os import path\n",
        }
        for rel, text in files.items():
            full = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(text)
        proc = self._walk(root)
        self.assertNotEqual(proc.returncode, 0, "the walk passed a planted `import numpy`")
        flagged = sorted(line.strip().rsplit(": ", 1)[-1]
                         for line in proc.stderr.splitlines() if line.startswith("  "))
        self.assertEqual(flagged, ["numpy", "scipy", "trimesh"], proc.stderr)


if __name__ == "__main__":
    unittest.main()
