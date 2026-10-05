# SPDX-License-Identifier: Apache-2.0
"""`--junit` at the CLI edge: unlinked first, one exit code, written last.

`tests/test_junit.py` holds the renderer to the exit code it is handed. This file
holds the command to handing it the right one, and to the file on disk never
outliving the run that should have replaced it. What slipped through while this
was being designed (cli:H7):

* **A stale green file survives a crash.** "Unlink when the sweep starts" placed
  after `_registry` and `_projection` — both of which exit 2 on a broken pack or
  model — leaves yesterday's all-green `junit.xml` on disk beside a job that
  exited 2, and a CI system renders the file, not the exit code. The target is
  unlinked at the top of the command, before anything that can fail.
* **Four return points, four exit codes.** `check` returned from four places, and
  a JUnit writer bolted onto one of them reports a judgement another return
  point never made. The code is computed once, and the file is written at the
  single exit from that one value.

Every run is a child process on a COPY of the bracket: the tracked example is
never checked, never written (spec §0.3).

Run:  PYTHONPATH=src python3 -m unittest tests.test_junit_cli -v
"""
from __future__ import annotations

import json
import os
import unittest
import xml.etree.ElementTree as ET

import _env
import _projects

#: `report.JUNIT_DEFAULT`, spelled as a path under the project root.
DEFAULT = os.path.join(".nopekit", "out", "junit.xml")

#: A JUnit file that says everything passed: what a previous, green run left.
STALE_GREEN = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<testsuites name="nopekit check" tests="1" failures="0" errors="0" '
               'skipped="0"><testsuite name="gates" tests="1" failures="0" errors="0" '
               'skipped="0"><testcase classname="project" name="bracket.deflection"/>'
               '</testsuite></testsuites>\n')


def _suite(root: ET.Element, name: str) -> ET.Element:
    found = [s for s in root.iter("testsuite") if s.get("name") == name]
    if len(found) != 1:
        raise AssertionError(f"expected one testsuite {name!r}, found {len(found)}")
    return found[0]


def _red(suite: ET.Element) -> list[str]:
    """Names of the testcases in ``suite`` that carry a failure or an error."""
    return [case.get("name") for case in suite.iter("testcase")
            if case.find("failure") is not None or case.find("error") is not None]


def _property(root: ET.Element, name: str) -> str | None:
    props = root.find("properties")
    if props is None:
        return None
    for prop in props.iter("property"):
        if prop.get("name") == name:
            return prop.get("value")
    return None


class JUnitAtTheEdge(_env.EnvCase):
    def setUp(self):
        # As a clone holds it, never the checkout's tree: a `check` running in
        # the bracket put its live build.lock into a copytree (P2.3's gate).
        self.project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"),
                                              migrated=True)

    def _stale(self, path: str) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(STALE_GREEN)
        return path

    def _break_model(self) -> None:
        """A model entry that does not load: `_projection` exits 2 on it."""
        with open(os.path.join(self.project, "model", "bracket.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("\nraise RuntimeError('planted: this model does not load')\n")

    def test_a_crash_leaves_no_junit(self):
        """A stale all-green `junit.xml`, then a broken model: the command exits
        2 before the sweep, and no `junit.xml` is left for CI to render — at the
        default path and at an explicit one, in `check` and in `gate selftest`."""
        self._break_model()
        explicit = os.path.join(self.tmp(), "ci", "junit.xml")
        for argv, target in (
                (["check", "--junit"], os.path.join(self.project, DEFAULT)),
                (["check", "--junit", explicit], explicit),
                (["gate", "selftest", "--junit"], os.path.join(self.project, DEFAULT)),
                (["gate", "selftest", "--junit", explicit], explicit)):
            with self.subTest(argv=argv):
                self._stale(target)
                proc = _env.nopekit(argv, cwd=self.project)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn("planted: this model does not load", proc.stderr)
                self.assertFalse(os.path.exists(target),
                                 f"{argv}: a stale green junit.xml outlived a crash")

    def test_critical_failures_plus_errors_equal_blocking(self):
        """The bracket fails on purpose (thickness 7.0). `check --junit --json`
        exits 1, and the XML's red `claims.critical` testcases are exactly the
        claims `--json` reports as blocking — the same judgement, printed twice."""
        proc = _env.nopekit(["check", "--junit", "--json"], cwd=self.project)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        blocking = [row["claim"] for row in data["blocking"]]
        self.assertTrue(blocking, "the bracket fails on purpose; nothing blocked")

        path = os.path.join(self.project, DEFAULT)
        self.assertTrue(os.path.isfile(path), proc.stdout)
        self.assertEqual(os.path.realpath(data["junit"]), os.path.realpath(path))
        root = ET.parse(path).getroot()
        self.assertEqual(root.tag, "testsuites")
        self.assertEqual(sorted(_red(_suite(root, "claims.critical"))), sorted(blocking))
        self.assertEqual(_property(root, "exit_code"), "1")
        self.assertEqual(_property(root, "ready"), "false")
        # The gate that fails is red in `gates` too, and nothing else is.
        self.assertEqual(_red(_suite(root, "gates")), ["bracket.deflection"])

    def test_text_and_json_exit_the_same(self):
        """One exit code, whatever is printed: the text run and the JSON run of
        the same project agree, and each writes a file carrying that code."""
        codes = {}
        for label, extra in (("text", []), ("json", ["--json"])):
            path = os.path.join(self.tmp(), f"{label}.xml")
            proc = _env.nopekit(["check", "--no-record", "--junit", path, *extra],
                                 cwd=self.project)
            codes[label] = proc.returncode
            root = ET.parse(path).getroot()
            self.assertEqual(_property(root, "exit_code"), str(proc.returncode), label)
        self.assertEqual(codes, {"text": 1, "json": 1})

    def test_gate_selftest_junit_in_a_project(self):
        """Project mode: one `controls` testcase per gate, every one fired, and no
        `baselines` suite — a project's gates are demonstrated against the host,
        which has no pack baseline to hold them to."""
        proc = _env.nopekit(["gate", "selftest", "--junit"], cwd=self.project)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        root = ET.parse(os.path.join(self.project, DEFAULT)).getroot()
        controls = _suite(root, "controls")
        self.assertEqual(len(list(controls.iter("testcase"))), 6)
        self.assertEqual(_red(controls), [])
        self.assertEqual([s for s in root.iter("testsuite") if s.get("name") == "baselines"], [])
        self.assertEqual(_property(root, "exit_code"), "0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
