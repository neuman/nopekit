# SPDX-License-Identifier: Apache-2.0
"""`gate selftest` with no project: pack mode, where CLAUDE.md says to run it (S-09).

`CLAUDE.md` and `CONTRIBUTING.md` give `nopekit gate selftest` at the repository
root as the check that decides whether a pack gets merged. It exited 2 there —
"not in a project" — so the one command a pack author was told to run could not
run where they were told to run it, and CI ran it only inside the bracket, which
loads no pack at all. Three more things slipped through beside it:

* **Zero controls exited 0.** A selftest that ran nothing printed a sentence and
  returned success on the text path, and `"ok": true` on the JSON path — a
  command that passes by running nothing is a logger (PLAN G3).
* **The machine chose the pack.** `~/.nopekit/packs` and `$NOPEKIT_PACK_PATH`
  outrank the bundled packs, so on a pack author's machine a same-named copy was
  the one exercised while every message named the bundled one (S-87). Pack mode
  tests the bundled packs unless `--user-packs` says otherwise.
* **`--junit` could swallow a gate id.** `gate selftest --junit bracket.deflection`
  parses the gate id as the report's path (cli:H7), and the command would then
  have run every control and written XML to a file called `bracket.deflection`.

Every scenario is a child process through `_env.nopekit`: a temp `HOME` (no user
packs unless a test plants one), no `NOPEKIT_PACK_PATH`, and each planted pack in
a process of its own, since a pack name is loaded once per process (core:§5.12).

Run:  PYTHONPATH=src python3 -m unittest tests.test_pack_mode -v
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unittest
import xml.etree.ElementTree as ET

import _env

PACKS_DIR = os.path.join(_env.REPO, "packs")

#: The summary line both modes print (spec §3.13), as the fresh-clone transcript
#: matches it. `\S+` for the duration: `human_duration` renders one token below a
#: minute, and the bundled packs demonstrate in seconds.
#: The summary, in qualification's words from P2.3 (R-6, words only — `N
#: control(s) in T: F fired, B BROKEN, S skipped (tooling)` before): evaluators,
#: qualified, unqualified, skipped, the groups in the order they had.
SUMMARY = re.compile(r"^(\d+) evaluators? in \S+: (\d+) qualified, (\d+) unqualified, "
                     r"(\d+) skipped$")

#: The message that stops `--junit` eating the argument after it (cli:H7).
XML_RULE = "--junit takes a path ending in .xml; put gate ids before it"

#: Where each plant goes: the first statement of beam-analytic's deflection gate.
DEFLECTION = ("gates/beam.py", "def deflection(ctx: GateContext) -> Verdict:\n")


def _snapshot(root: str) -> dict[str, tuple]:
    """Every directory and file under ``root``: size, mtime and the bytes' digest.

    ``__pycache__`` is left out by name, as `tests/test_packs.py` leaves it out:
    the import system writes bytecode beside a module the first time anything
    imports it, the tree ignores it, and whether it is already there depends on
    which test ran first. A directory is a key with no stamp, so a new one is
    caught while the bytecode moving its mtime is not.
    """
    out: dict[str, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        out[os.path.relpath(dirpath, root) + "/"] = ()
        for filename in sorted(filenames):
            full = os.path.join(dirpath, filename)
            info = os.lstat(full)
            with open(full, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            out[os.path.relpath(full, root)] = (info.st_size, info.st_mtime_ns, digest)
    return out


def _changed(before: dict[str, tuple], after: dict[str, tuple]) -> list[str]:
    lines = [f"{p}: created" for p in sorted(set(after) - set(before))]
    lines += [f"{p}: removed" for p in sorted(set(before) - set(after))]
    lines += [f"{p}: rewritten" for p in sorted(set(before) & set(after))
              if before[p] != after[p]]
    return lines


def _copy_beam(parent: str, name: str = "beam-analytic") -> str:
    """beam-analytic copied to ``<parent>/<name>``, its manifest renamed to match."""
    pack_dir = os.path.join(parent, name)
    shutil.copytree(os.path.join(PACKS_DIR, "beam-analytic"), pack_dir,
                    ignore=shutil.ignore_patterns("__pycache__", ".selftest-out"))
    manifest = os.path.join(pack_dir, "pack.json")
    with open(manifest, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data["name"] = name
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return pack_dir


def _plant(pack_dir: str, body: str) -> None:
    """Insert ``body`` as the first statement of beam.deflection."""
    rel, anchor = DEFLECTION
    path = os.path.join(pack_dir, *rel.split("/"))
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if text.count(anchor) != 1:
        raise AssertionError(f"{rel}: expected one {anchor.strip()!r}, "
                             f"found {text.count(anchor)}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(anchor, anchor + body))


def _suite(root: ET.Element, name: str) -> ET.Element:
    found = [s for s in root.iter("testsuite") if s.get("name") == name]
    if len(found) != 1:
        raise AssertionError(f"expected one testsuite {name!r}, found {len(found)}")
    return found[0]


def _red(suite: ET.Element) -> dict[str, str]:
    """``{testcase name: "failure"|"error"}`` for every red testcase in ``suite``."""
    out: dict[str, str] = {}
    for case in suite.iter("testcase"):
        for tag in ("failure", "error"):
            if case.find(tag) is not None:
                out[case.get("name")] = tag
    return out


class PackModeSelftest(_env.EnvCase):
    """`gate selftest` outside a project, and with `--pack` anywhere (S-09, S-87)."""

    def setUp(self):
        # Every scenario runs in a directory that is not a project, so the
        # command must take pack mode on its own: nothing above the temp dir
        # may carry a marker, or the scenario is measuring the machine.
        self.cwd = self.tmp()
        self.home = self.tmp()

    def _run(self, *args: str, cwd: str | None = None):
        return _env.nopekit(["gate", "selftest", *args], cwd=cwd or self.cwd,
                             home=self.home)

    # -- zero controls is not a pass --------------------------------------- #
    def test_an_empty_pack_dir_exits_1_unless_allowed(self):
        """A directory holding no pack runs no control. That exits 1 on the text
        path and the JSON path alike; `--allow-empty` is the one way to say
        "nothing to run is expected here"."""
        empty = self.tmp()
        text = self._run("--pack", empty)
        self.assertEqual(text.returncode, 1, text.stdout + text.stderr)
        self.assertTrue(any(SUMMARY.match(line) for line in text.stdout.splitlines()),
                        text.stdout)
        data = self._run("--pack", empty, "--json")
        self.assertEqual(data.returncode, 1, data.stdout + data.stderr)
        payload = json.loads(data.stdout)
        self.assertEqual(payload["mode"], "pack")
        self.assertIs(payload["ok"], False)

        allowed = self._run("--pack", empty, "--allow-empty")
        self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
        allowed_json = self._run("--pack", empty, "--allow-empty", "--json")
        self.assertEqual(allowed_json.returncode, 0, allowed_json.stdout + allowed_json.stderr)
        self.assertIs(json.loads(allowed_json.stdout)["ok"], True)

    # -- planted violators ------------------------------------------------- #
    def test_a_planted_logger_exits_1_naming_the_gate(self):
        """``return True``: it passes its baseline AND its known-bad input. The
        command names it, and its JUnit testcase in `controls` is red."""
        pack = _copy_beam(self.tmp(), "beam-logger")
        _plant(pack, "    return True\n")
        junit = os.path.join(self.tmp(), "selftest.xml")
        proc = self._run("--pack", pack, "--junit", junit)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        # R-6 (P2.3-D18, words): its qualification line names it, where a
        # `[FAIL]` row did — `known-bad pass`, the first fact that does not hold.
        flagged = [line for line in proc.stdout.splitlines()
                   if line.startswith("beam.deflection : ") and "known-bad pass" in line
                   and line.endswith("→ unqualified")]
        self.assertTrue(flagged, proc.stdout)
        summary = [SUMMARY.match(line) for line in proc.stdout.splitlines()]
        summary = [m for m in summary if m]
        self.assertEqual(len(summary), 1, proc.stdout)
        self.assertEqual(summary[0].group(3), "1", proc.stdout)      # one unqualified

        root = ET.parse(junit).getroot()
        self.assertEqual(_red(_suite(root, "controls")), {"beam.deflection": "failure"})
        # Its baseline passed: the accept half is not what is wrong with it.
        self.assertEqual(_red(_suite(root, "baselines")), {})

    def test_a_planted_always_false_fails_its_own_baseline(self):
        """``return False``: its control fires, and proves nothing, because it
        fails the good design too (S-04). Only the baseline run can see it."""
        pack = _copy_beam(self.tmp(), "beam-refuser")
        _plant(pack, "    return False\n")
        junit = os.path.join(self.tmp(), "selftest.xml")
        proc = self._run("--pack", pack, "--junit", junit)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("fails its own baseline", proc.stdout)
        self.assertTrue(any("beam.deflection" in line and "fails its own baseline" in line
                            for line in proc.stdout.splitlines()), proc.stdout)
        root = ET.parse(junit).getroot()
        self.assertEqual(_red(_suite(root, "baselines")), {"beam.deflection": "failure"})
        # R-6 (P2.3, a strengthening): the `controls` suite is one row per
        # evaluator, green iff qualified — and an evaluator that fails its
        # known-good control is not, so the baseline run is no longer the only
        # one that can see it.
        self.assertEqual(_red(_suite(root, "controls")), {"beam.deflection": "failure"})

    def test_the_unplanted_copy_passes(self):
        """The positive control for the two above: the same copy, nothing
        planted, exits 0 with every control fired — so each of them fails for
        what was planted, not for the copying."""
        pack = _copy_beam(self.tmp(), "beam-clean")
        proc = self._run("--pack", pack)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        match = SUMMARY.match(proc.stdout.splitlines()[-1])
        self.assertIsNotNone(match, proc.stdout)
        self.assertEqual((match.group(1), match.group(2), match.group(3)), ("8", "8", "0"))

    # -- the host machine does not choose the pack (S-87) ------------------- #
    def test_a_user_pack_does_not_shadow_the_bundled_one(self):
        """A broken `beam-analytic` in `~/.nopekit/packs` outranks the bundled
        one in a project's search order. Pack mode tests the bundled copy unless
        `--user-packs` asks for the machine's — and then it finds the plant."""
        user_packs = os.path.join(self.home, ".nopekit", "packs")
        os.makedirs(user_packs)
        _plant(_copy_beam(user_packs), "    return True\n")

        bundled = self._run("--pack", "beam-analytic")
        self.assertEqual(bundled.returncode, 0, bundled.stdout + bundled.stderr)
        mine = self._run("--pack", "beam-analytic", "--user-packs")
        self.assertEqual(mine.returncode, 1, mine.stdout + mine.stderr)
        self.assertIn("beam.deflection", mine.stdout)

    # -- where CLAUDE.md says to run it ------------------------------------ #
    def test_the_repo_root_exits_0_and_persists_nothing(self):
        """At the repository root there is no project: every bundled pack is
        demonstrated, and `packs/` is byte- and mtime-identical afterwards —
        openmodelica's generated models included (Q1.8)."""
        before = _snapshot(PACKS_DIR)
        proc = self._run(cwd=_env.REPO)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        match = SUMMARY.match(proc.stdout.splitlines()[-1])
        self.assertIsNotNone(match, proc.stdout)
        evaluators, qualified, unqualified, skipped = (int(g) for g in match.groups())
        self.assertEqual(unqualified, 0, proc.stdout)
        # Not vacuous: the stdlib-only packs run with nothing installed.
        self.assertGreater(qualified, 0, proc.stdout)
        self.assertEqual(evaluators, qualified + unqualified + skipped, proc.stdout)
        self.assertEqual(_changed(before, _snapshot(PACKS_DIR)), [],
                         "pack-mode selftest wrote into packs/")

    # -- the argparse trap (cli:H7) ---------------------------------------- #
    def test_junit_does_not_swallow_a_gate_id(self):
        """`--junit` takes an optional PATH, so a gate id after it would be read
        as the path. A value that does not end in `.xml` is refused, exit 2,
        before anything runs — in `gate selftest` and in `check`."""
        for argv in (["gate", "selftest", "--junit", "bracket.deflection"],
                     ["check", "--junit", "bracket.deflection"]):
            with self.subTest(argv=argv):
                proc = _env.nopekit(argv, cwd=self.cwd, home=self.home)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn(XML_RULE, proc.stderr)
                self.assertFalse(os.path.exists(os.path.join(self.cwd, "bracket.deflection")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
