# SPDX-License-Identifier: Apache-2.0
"""firmware-zmk, judged in process: what its selftest could not see.

What slipped through before the pack (gripster, a keyboard deck on an nRF52840
module): its CI had never built the firmware (``Invalid BOARD``), deep sleep was on
with no wakeup source on the kscan, and the radio ran at +8 dBm past its module's
3.86 dBm grant. The selftest shows each gate can fail once, on upstream ZMK's corne.
This file holds the rules around each verdict, on every machine:

- which CI steps the guard reads, fails and refuses (``read_step``, on workflows
  written here);
- which build logs are a design's fault and which are the tool's (``judge``, on
  scrubbed excerpts of this machine's runs in ``tests/zmk_logs/``: the pinned image
  on ZMK v0.3.0, and docker's own faults);
- the wakeup and transmit rules on the values a build leaves;
- that the wrapper the gates trust is the pack's, and that without it the three
  build gates read Skipped.

No docker and no stand-in: the gates refuse a ``zmk-west`` that is not the pack's
own file, so a stand-in could only test that refusal (``TheWrapperIsThePacks``).
The real builds run in ``nopekit gate selftest --pack firmware-zmk`` and in
test_packs, where ``zmk-west`` is on PATH.

Run:  PYTHONPATH=src python3 -m unittest tests.test_firmware_zmk -v
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import tempfile
import textwrap
import unittest
from unittest import mock

from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit.modelio import load_path
from nopekit.models import Claim, Ledger

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(REPO, "packs", "firmware-zmk")
LOGS = os.path.join(REPO, "tests", "zmk_logs")
WRAPPER = os.path.join(PACK, "bin", "zmk-west")
Z = load_path(os.path.join(PACK, "gates", "_zmk.py"))
HAS_YAML = importlib.util.find_spec("yaml") is not None

GATES = ("zmk.ci_step", "zmk.build", "zmk.wakeup_source", "zmk.tx_power")


def _registry() -> gates_mod.Registry:
    registry = gates_mod.Registry()
    packs_mod.load_gates("firmware-zmk", registry, root=REPO, include_env=False,
                         include_user=False)
    return registry


# --------------------------------------------------------------------------- #
# the CI step
# --------------------------------------------------------------------------- #
#: gripster's build step at d52525e, as its workflow writes it.
GRIPSTER_STEP = """\
west build -s zmk/app -p always -b thumbdeck -- \\
  -DZMK_CONFIG="${GITHUB_WORKSPACE}/firmware/zmk-config/config" \\
  -DZEPHYR_EXTRA_MODULES="${GITHUB_WORKSPACE}/firmware/zmk-modules/tmag5273_nub"
"""

PINNED = """\
manifest:
  remotes:
    - name: zmkfirmware
      url-base: https://github.com/zmkfirmware
  projects:
    - name: zmk
      remote: zmkfirmware
      revision: v0.3.0
      import: app/west.yml
  self:
    path: config
"""


def _block(text: str, indent: int) -> str:
    return textwrap.indent(text, " " * indent)


def _workflow(run: str = "west build -s zmk/app -b nice_nano_v2 -- "
                         "-DZMK_CONFIG=${GITHUB_WORKSPACE}/config",
              *, job_extra: str = "", step_extra: str = "", top: str = "",
              steps: str | None = None) -> str:
    body = steps if steps is not None else (
        "      - uses: actions/checkout@v4\n"
        "      - name: Build\n"
        + (_block(step_extra, 8) if step_extra else "")
        + "        run: |\n" + _block(run, 10))
    return (f"name: b\non: [push]\n{top}jobs:\n  build:\n    runs-on: ubuntu-latest\n"
            f"{_block(job_extra, 4) if job_extra else ''}    steps:\n{body}")


@unittest.skipUnless(HAS_YAML, "PyYAML is not importable here: the guard reads "
                               "workflows with it, so its rules are not walked")
class TheStepIsCIsOwn(unittest.TestCase):
    """``read_step``: one self-contained step, CI's own flags, ZMK v0.3.0."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="zmk-step-")
        self.addCleanup(shutil.rmtree, self.repo, True)

    def _read(self, workflow: str, manifest: str | None = PINNED, wd: str = "."):
        path = os.path.join(self.repo, ".github", "workflows", "build.yml")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(workflow)
        west = os.path.join(self.repo, wd, "config", "west.yml")
        if manifest is None:
            if os.path.exists(west):
                os.remove(west)
        else:
            os.makedirs(os.path.dirname(west), exist_ok=True)
            with open(west, "w", encoding="utf-8") as fh:
                fh.write(manifest)
        return Z.read_step(path)

    def _refused(self, workflow: str, words: str, **kw):
        with self.assertRaises(Z.StepRefused) as caught:
            self._read(workflow, **kw)
        self.assertIn(words, str(caught.exception))

    def _fails(self, workflow: str, words: str, **kw):
        found = self._read(workflow, **kw)
        self.assertIsInstance(found, Z.StepFail)
        self.assertIn(words, found.reason)
        self.assertNotIn(self.repo, found.reason, "a detail names no absolute path")

    def test_gripsters_step_gives_its_own_argv(self):
        wf = _workflow(GRIPSTER_STEP, job_extra="defaults:\n  run:\n    "
                                                "working-directory: firmware/zmk-config\n")
        step = self._read(wf, wd="firmware/zmk-config")
        self.assertIsInstance(step, Z.Step)
        self.assertEqual(step.wd, "firmware/zmk-config")
        self.assertEqual(step.argv, (
            "west", "build", "-s", "zmk/app", "-p", "always", "-b", "thumbdeck", "--",
            f"-DZMK_CONFIG={self.repo}/firmware/zmk-config/config",
            f"-DZEPHYR_EXTRA_MODULES={self.repo}/firmware/zmk-modules/tmag5273_nub"))
        self.assertEqual(step.zmk_config, f"{self.repo}/firmware/zmk-config/config")

    def test_a_backslash_newline_joins_and_never_becomes_a_token(self):
        step = self._read(_workflow("west build -s zmk/app \\\n  -b nice_nano_v2 -- \\\n"
                                    "  -DZMK_CONFIG=$GITHUB_WORKSPACE/config\n"))
        self.assertNotIn("\n", step.argv)
        self.assertEqual(step.argv[-1], f"-DZMK_CONFIG={self.repo}/config")

    def test_a_separate_minus_d_is_one_definition(self):
        step = self._read(_workflow("west build -s zmk/app -- -D "
                                    "ZMK_CONFIG=${GITHUB_WORKSPACE}/config\n"))
        self.assertEqual(step.zmk_config, f"{self.repo}/config")

    def test_the_pinned_manifest_passes(self):
        self.assertIsInstance(self._read(_workflow()), Z.Step)

    def test_no_build_step_fails(self):
        self._fails(_workflow(steps="      - run: echo hello\n"), "runs no `west build`")

    def test_no_workflow_and_a_broken_one_fail(self):
        path = os.path.join(self.repo, ".github", "workflows", "gone.yml")
        found = Z.read_step(path)
        self.assertIsInstance(found, Z.StepFail)
        self.assertIn("CI cannot run this workflow", found.reason)
        self._fails("jobs: [unclosed\n", "does not parse")

    def test_no_zmk_config_fails(self):
        self._fails(_workflow("west build -s zmk/app -b nice_nano_v2 -- -DSHIELD=corne_left\n"),
                    "ZMK builds its own default keymap")

    def test_a_revision_other_than_v030_fails(self):
        floating = PINNED.replace("      revision: v0.3.0\n", "").replace(
            "manifest:\n", "manifest:\n  defaults:\n    revision: v0.3\n")
        self._fails(_workflow(), "CI builds ZMK v0.3,", manifest=floating)
        self._fails(_workflow(), "CI builds ZMK main,",
                    manifest=PINNED.replace("v0.3.0", "main"))
        self._fails(_workflow(), "at no pinned revision",
                    manifest=PINNED.replace("      revision: v0.3.0\n", ""))

    def test_a_manifest_with_no_zmk_or_a_fork_fails(self):
        self._fails(_workflow(), "names no project zmk",
                    manifest=PINNED.replace("name: zmk\n", "name: other\n"))
        self._fails(_workflow(), "CI fetches zmk from https://github.com/someone/zmk",
                    manifest=PINNED.replace("url-base: https://github.com/zmkfirmware",
                                            "url-base: https://github.com/someone"))
        self._fails(_workflow(), "has no manifest to read", manifest=None)

    def test_two_build_steps_are_refused(self):
        steps = ("      - name: Left\n        run: west build -s zmk/app -- "
                 "-DZMK_CONFIG=${GITHUB_WORKSPACE}/config\n"
                 "      - name: Right\n        run: west build -s zmk/app -- "
                 "-DZMK_CONFIG=${GITHUB_WORKSPACE}/config\n")
        self._refused(_workflow(steps=steps), "in 2 steps")

    def test_an_expression_only_github_evaluates_is_refused(self):
        self._refused(_workflow("west build -s zmk/app -b ${{ matrix.board }} -- "
                                "-DZMK_CONFIG=${GITHUB_WORKSPACE}/config\n"), "expression")

    def test_env_anywhere_is_refused(self):
        self._refused(_workflow(top="env:\n  ZEPHYR_EXTRA_MODULES: /x\n"), "the workflow sets env")
        self._refused(_workflow(job_extra="env:\n  A: b\n"), "job build sets env")
        self._refused(_workflow(step_extra="env:\n  A: b\n"), "step 'Build' sets env")

    def test_a_second_command_is_refused(self):
        self._refused(_workflow("west build -s zmk/app -- -DZMK_CONFIG=${GITHUB_WORKSPACE}/c"
                                " && cp a b\n"), "not one plain command")
        self._refused(_workflow("west build -s zmk/app -- -DZMK_CONFIG=$(pwd)/config\n"),
                      "not one plain command")

    def test_another_variable_is_refused(self):
        self._refused(_workflow("west build -s zmk/app -- -DZMK_CONFIG=$OTHER/config\n"),
                      "other than GITHUB_WORKSPACE")

    def test_a_build_directory_of_its_own_is_refused(self):
        self._refused(_workflow("west build -d out -s zmk/app -- "
                                "-DZMK_CONFIG=${GITHUB_WORKSPACE}/config\n"),
                      "names its build directory")

    def test_a_relative_path_is_refused(self):
        self._refused(_workflow("west build -s zmk/app -- -DZMK_CONFIG=config/x\n"),
                      "is relative")

    def test_another_source_is_refused(self):
        self._refused(_workflow("west build -s app -- -DZMK_CONFIG=${GITHUB_WORKSPACE}/c\n"),
                      "not zmk/app")

    def test_another_shell_is_refused(self):
        self._refused(_workflow(step_extra="shell: pwsh\n"), "shell 'pwsh'")

    def test_the_reusable_workflow_is_refused(self):
        wf = ("on: [push]\njobs:\n  build:\n    uses: "
              "zmkfirmware/zmk/.github/workflows/build-user-config.yml@v0.3\n")
        self._refused(wf, "reusable build-user-config")

    def test_a_workflow_outside_workflows_is_refused(self):
        path = os.path.join(self.repo, "build.yml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_workflow())
        with self.assertRaises(Z.StepRefused):
            Z.read_step(path)


# --------------------------------------------------------------------------- #
# the build's own lines
# --------------------------------------------------------------------------- #
FAKE_REPO = "/w/repo"


def _result(name: str, rc: int, *, cmake_only: bool = False, **over) -> dict:
    """A run's result as ``_zmk._run`` returns it, from a recorded log."""
    with open(os.path.join(LOGS, name + ".log"), encoding="utf-8") as fh:
        raw = fh.read().replace("<repo>", FAKE_REPO)
    result = {"cmake_only": cmake_only, "repo": FAKE_REPO,
              "zmk_config": FAKE_REPO + "/config",
              "heads": {"zmk": Z.ZMK_COMMIT, "zephyr": Z.ZEPHYR_COMMIT},
              "timeout": False, "rc": rc, "log": raw.replace(FAKE_REPO, "<repo>"),
              "keymaps": [m.strip() for m in Z.KEYMAP_RE.findall(raw)], "config": "",
              "headers": (f"#define APP_BUILD_VERSION v0.3\n"
                          f"#define BUILD_VERSION {Z.ZEPHYR_COMMIT[:12]}\n"),
              "image": not cmake_only, "edt": None, "edt_error": ""}
    result.update(over)
    return result


class TheBuildIsJudgedOnItsOwnLines(unittest.TestCase):
    """``judge``: a design fails on a line from the closed list; the tool's fault
    is errored, and never reads as a design's."""

    def _fails(self, result: dict, words: str):
        passed, detail = Z.judge(result)
        self.assertFalse(passed, detail)
        self.assertIn(words, detail)
        self.assertNotIn("<repo>", detail, "a detail is repo-relative")
        self.assertNotIn(FAKE_REPO, detail)

    def _raises(self, result: dict, words: str):
        with self.assertRaises(Z.ToolFault) as caught:
            Z.judge(result)
        self.assertIn(words, str(caught.exception))

    def test_corne_passes_built_and_configured(self):
        self.assertEqual(Z.judge(_result("corne_full", 0)),
                         (True, "built zmk.uf2 on keymap config/corne.keymap"))
        self.assertEqual(Z.judge(_result("corne_configure", 0, cmake_only=True)),
                         (True, "configured on keymap config/corne.keymap"))

    def test_gripsters_ci_fails_on_invalid_board(self):
        self._fails(_result("invalid_board", 1, zmk_config=FAKE_REPO + "/x"),
                    "the build fails: Invalid BOARD; see above.")

    def test_a_devicetree_typo_fails_on_its_line(self):
        self._fails(_result("devicetree_tabb", 1),
                    "devicetree error: config/corne.keymap:22 (column 8): parse error")

    def test_a_kconfig_abort_and_a_c_error_fail(self):
        self._fails(_result("kconfig_abort", 1), "error: Aborting due to Kconfig warnings")
        self._fails(_result("module_c_error", 1), "FAILED: modules/tmag5273_nub/")

    def test_the_default_keymap_at_exit_0_fails(self):
        self._fails(_result("wrong_config", 0),
                    "built keymap /zmk/zmk/app/boards/shields/corne/corne.keymap, "
                    "not one under ZMK_CONFIG (config)")

    def test_no_keymap_line_fails(self):
        self._fails(_result("corne_full", 0, keymaps=[]), "names no keymap")

    def test_no_image_fails(self):
        self._fails(_result("corne_full", 0, image=False), "wrote no zmk.uf2")

    def test_a_failure_no_line_names_is_the_tools(self):
        self._raises(_result("no_zephyr_package", 1), "never reached Zephyr")
        self._raises(_result("not_a_workspace", 1), 'unknown command "build"')
        # The cache error comes AFTER the version line: "the version line is
        # there, so the design failed" read this as a design fail.
        self._raises(_result("read_only_cache", 1), "no line this pack knows")

    def test_dockers_own_codes_and_a_signal_are_the_tools(self):
        self._raises(_result("docker_125", 125), "zmk-west exited 125: docker: Error")
        self._raises(_result("docker_127", 127), "exited 127")
        self._raises(_result("corne_full", 137), "exited 137")
        self._raises(_result("corne_full", None, timeout=True), "did not finish")

    def test_a_signature_before_zephyr_is_still_the_tools(self):
        log = _result("invalid_board", 1)["log"]
        log = "\n".join(ln for ln in log.splitlines() if "Zephyr version" not in ln)
        self._raises(_result("invalid_board", 1, log=log), "never reached Zephyr")

    def test_another_zephyr_is_outside_the_qualified_toolchain(self):
        log = _result("corne_full", 0)["log"].replace("Zephyr version: 3.5.0",
                                                      "Zephyr version: 4.1.0")
        self._raises(_result("corne_full", 0, log=log), "outside the qualified toolchain")
        # ...and never a design fail, though its log names one.
        log = _result("invalid_board", 1)["log"].replace("3.5.0", "4.1.0")
        self._raises(_result("invalid_board", 1, log=log), "outside the qualified")

    def test_other_commits_are_the_tools(self):
        self._raises(_result("corne_full", 0, headers="#define APP_BUILD_VERSION v0.2\n"
                                                      "#define BUILD_VERSION dacab4875df7\n"),
                     "the build reports zmk v0.2")
        self._raises(_result("corne_full", 0, headers=""), "the build reports zmk ?")
        self._raises(_result("corne_configure", 0, cmake_only=True,
                             heads={"zmk": "0" * 40, "zephyr": Z.ZEPHYR_COMMIT}),
                     "run zmk-west --init again")

    def test_every_signature_is_one_this_file_measured_or_names(self):
        """The list is closed: a new entry comes with its log here."""
        logs = ""
        for name in sorted(os.listdir(LOGS)):
            with open(os.path.join(LOGS, name), encoding="utf-8") as fh:
                logs += fh.read()
        unrecorded = [s.pattern for s in Z.DESIGN_SIGNATURES if not s.search(logs)]
        self.assertEqual(unrecorded, [r"region .* overflowed", r"undefined reference to"],
                         "the linker's two are the only entries with no recorded log")


# --------------------------------------------------------------------------- #
# the two readers
# --------------------------------------------------------------------------- #
TX_CLAIM = {"id": "TX", "statement": "at most 3.86 dBm", "kind": "measurable",
            "tags": ["tx-power"],
            "acceptance": {"quantity": "radio transmit power", "comparator": "<=",
                           "limit": 3.86, "units": "dBm"}}

LL = "CONFIG_BT_LL_SW_SPLIT=y\n"


class TheReadersJudgeWhatTheBuildHolds(unittest.TestCase):
    """The wakeup and transmit rules, on a configure handed to the gate."""

    @classmethod
    def setUpClass(cls):
        cls.registry = _registry()

    def _run(self, gid: str, config: str, edt=None, edt_error: str = ""):
        result = _result("corne_configure", 0, cmake_only=True, config=config, edt=edt,
                         edt_error=edt_error)
        _spec, fn = self.registry.get(gid)
        tmp = tempfile.mkdtemp(prefix="zmk-gate-")
        self.addCleanup(shutil.rmtree, tmp, True)
        ctx = gates_mod.GateContext(
            root=tmp, ledger=Ledger(claims=[Claim.from_dict(TX_CLAIM)]), model=None,
            params={"zmk_ci_workflow": "github/workflows/build.yml"},
            out_dir=os.path.join(tmp, "out"), tier=1, log=lambda _m: None, extra={})
        with mock.patch.object(gates_mod.GateContext, "load_file",
                               lambda self, path, loader=None: result):
            return fn(ctx)

    def test_kconfig_reads_the_symbols_set(self):
        cfg = Z.kconfig("CONFIG_BT_CTLR_TX_PWR_DBM=8\n# CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL "
                        "is not set\nCONFIG_BT_LL_SW_SPLIT=y\nCONFIG_ZMK_KEYBOARD_NAME=\"x\"\n")
        self.assertEqual(cfg, {"CONFIG_BT_CTLR_TX_PWR_DBM": "8", "CONFIG_BT_LL_SW_SPLIT": "y",
                               "CONFIG_ZMK_KEYBOARD_NAME": '"x"'})

    def test_8_dbm_fails_against_3_86(self):
        v = self._run("zmk.tx_power", LL + "CONFIG_BT_CTLR_TX_PWR_DBM=8\n")
        self.assertEqual((v.passed, v.measured, v.limit, v.units, v.comparator),
                         (False, 8, 3.86, "dBm", "<="))
        self.assertTrue(self._run("zmk.tx_power", LL + "CONFIG_BT_CTLR_TX_PWR_DBM=0\n").passed)

    def test_power_the_host_can_raise_fails(self):
        v = self._run("zmk.tx_power", LL + "CONFIG_BT_CTLR_TX_PWR_DBM=0\n"
                                           "CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL=y\n")
        self.assertFalse(v.passed)
        self.assertIn("raise TX power at run time", v.detail)

    def test_another_controller_is_errored(self):
        self.assertIn("LL controller", self._run("zmk.tx_power", LL).error)
        self.assertIn("LL controller",
                      self._run("zmk.tx_power", "CONFIG_BT_CTLR_TX_PWR_DBM=0\n").error)

    def test_a_wakeup_source_passes_and_its_absence_fails(self):
        sleep = "CONFIG_ZMK_SLEEP=y\n"
        on = [{"path": "/kscan", "present": True, "val": True}]
        self.assertTrue(self._run("zmk.wakeup_source", sleep, on).passed)
        v = self._run("zmk.wakeup_source", sleep,
                      on + [{"path": "/kscan_r", "present": True, "val": False}])
        self.assertFalse(v.passed)
        self.assertIn("no wakeup-source on /kscan_r:", v.detail)

    def test_no_deep_sleep_passes_and_says_so(self):
        v = self._run("zmk.wakeup_source", "", [{"path": "/kscan", "present": True,
                                                 "val": False}])
        self.assertTrue(v.passed)
        self.assertIn("no deep sleep is built", v.detail)

    def test_what_the_devicetree_cannot_say_is_errored(self):
        sleep = "CONFIG_ZMK_SLEEP=y\n"
        self.assertIn("declares no wakeup-source", self._run(
            "zmk.wakeup_source", sleep, [{"path": "/k", "present": False, "val": False}]).error)
        self.assertIn("chooses no zmk,kscan", self._run("zmk.wakeup_source", sleep, []).error)
        self.assertIn("was not read", self._run("zmk.wakeup_source", sleep, None,
                                                "edtlib exited 1").error)


# --------------------------------------------------------------------------- #
# the tool edge
# --------------------------------------------------------------------------- #
class TheWrapperIsThePacks(unittest.TestCase):
    """The gates run only the pack's own ``bin/zmk-west``: its image digest, its
    mounts and its ``--network=none`` are what a verdict was made with."""

    def setUp(self):
        with open(WRAPPER, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_the_wrapper_names_the_constants(self):
        self.assertIn(f"\nIMAGE={Z.IMAGE}\n", self.text)
        self.assertIn(f"\nZMK_REVISION={Z.ZMK_REVISION}\n", self.text)

    def test_it_mounts_and_never_binds_by_v(self):
        code = "\n".join(ln for ln in self.text.splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotRegex(code, r"\s-v\s")
        self.assertIn("--network=none", code)
        self.assertIn("readonly", code)

    def test_it_is_executable(self):
        self.assertTrue(os.stat(WRAPPER).st_mode & stat.S_IXUSR)

    @unittest.skipIf(os.name == "nt", "zmk-west is a #! script")
    def test_another_zmk_west_is_refused_and_a_copy_is_not(self):
        tmp = tempfile.mkdtemp(prefix="zmk-path-")
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = os.path.join(tmp, "zmk-west")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho '-- Zephyr version: 3.5.0'\n")
        os.chmod(fake, 0o755)
        with mock.patch.dict(os.environ, {"PATH": tmp}):
            with self.assertRaises(Z.ToolFault) as caught:
                Z._identity()
            self.assertIn("is not this pack's bin/zmk-west", str(caught.exception))
            shutil.copyfile(WRAPPER, fake)
            self.assertEqual(Z._identity(), os.path.realpath(fake))

    def test_without_zmk_west_the_build_gates_skip(self):
        registry = _registry()
        tmp = tempfile.mkdtemp(prefix="zmk-nopath-")
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {"PATH": tmp}):
            ctx = packs_mod.baseline_context(PACK, out_dir=os.path.join(tmp, "out"))
            verdicts = {gid: gates_mod.run_gate(*registry.get(gid), ctx) for gid in GATES}
        for gid in GATES[1:]:
            self.assertTrue(verdicts[gid].skipped, f"{gid}: {verdicts[gid]}")
            self.assertFalse(verdicts[gid].passed)
        guard = verdicts["zmk.ci_step"]
        if HAS_YAML:
            self.assertTrue(guard.passed, guard)
        else:
            self.assertTrue(guard.skipped, guard)


if __name__ == "__main__":
    unittest.main()
