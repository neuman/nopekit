# SPDX-License-Identifier: Apache-2.0
"""firmware-ci, judged in process: what its selftest could not see.

The selftest shows each gate can fail once, on the pack's own ESP-IDF app, where act,
docker and its image are present. This file holds the rules around each verdict, on
every machine:

- which workflows the guard passes, fails and refuses (``_ci``, on workflows written
  here, and on every template ``references/ci-templates.md`` prints);
- which build-step lines are a design's fault and which are the tool's (``_replay.
  judge``, on committed, scrubbed excerpts of real replays in ``tests/fw_logs/``);
- what the copy holds, in a work tree, in an ignored directory and outside any
  work tree (critic 5.2: the known-bad copies and an installed wheel), and that
  every file copied is a read of the gates;
- the whole replay path through a stand-in act and docker (``tests/_act_standin.
  py``): act's argv and environment, the JSON log, the files read back, a timeout,
  and the act version pin, which skips and names the install line (critic 5.3);
- the image routes on ELF, UF2, Intel HEX and BIN bytes built here, the ESP route
  through ESP-IDF's own partition parser, and the wake rules on symbol lists and
  trimmed devicetrees.

The real replays run in ``TheRealReplay`` where act 0.2.97, docker and the pinned
ESP-IDF image are present, and are skipped, saying not walked, elsewhere.

Run:  PYTHONPATH=src python3 -m unittest tests.test_firmware_ci -v
"""
from __future__ import annotations

import functools
import importlib.util
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit import verdicts as verdicts_mod
from nopekit.modelio import load_path
from nopekit.models import Ledger

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(REPO, "packs", "firmware-ci")
LOGS = os.path.join(REPO, "tests", "fw_logs")
STANDIN = os.path.join(REPO, "tests", "_act_standin.py")
C = load_path(os.path.join(PACK, "gates", "_ci.py"))
R = load_path(os.path.join(PACK, "gates", "_replay.py"))
I = load_path(os.path.join(PACK, "gates", "_image.py"))
W = load_path(os.path.join(PACK, "gates", "_wake.py"))
HAS_YAML = importlib.util.find_spec("yaml") is not None
HAS_GIT = shutil.which("git") is not None
NO_YAML = "PyYAML is not importable here: the guard reads workflows with it, so its rules are not walked"

GATES = ("fw.ci_job", "fw.build", "fw.image_fits", "fw.sleep_wake")
#: The copy path the committed act excerpts were recorded under (tests/fw_logs/).
RECORDED_COPY = "/tmp/nopekit-fw-k2c9w0ra/repo"
IDF_IMAGE = ("espressif/idf@sha256:"
             "bf72fe7a15c22849a9a94147301165a545fb8905d97c176cf748e3e4614cac84")


def _registry() -> gates_mod.Registry:
    registry = gates_mod.Registry()
    packs_mod.load_gates("firmware-ci", registry, root=REPO, include_env=False,
                         include_user=False)
    return registry


def _write(root: str, rel: str, text: str) -> str:
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# --------------------------------------------------------------------------- #
# the guard
# --------------------------------------------------------------------------- #
def _workflow(run: str = ". $IDF_PATH/export.sh && idf.py build", *, step_extra: str = "",
              job_extra: str = "", top: str = "", steps: str | None = None,
              runs_on: str = "ubuntu-24.04", container: str | None = IDF_IMAGE) -> str:
    body = steps if steps is not None else (
        "      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n"
        "      - name: Build\n"
        + textwrap.indent(step_extra, " " * 8)
        + "        run: |\n" + textwrap.indent(run.rstrip("\n") + "\n", " " * 10))
    return (f"name: b\non: [push]\n{top}jobs:\n  build:\n    runs-on: {runs_on}\n"
            + (f"    container: {container}\n" if container else "")
            + textwrap.indent(job_extra, "    ") + "    steps:\n" + body)


@unittest.skipUnless(HAS_YAML, NO_YAML)
class TheGuardReadsTheWorkflow(unittest.TestCase):
    """``_ci.choose``: the job, its build step, and every form of masking."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="fw-ci-")
        self.addCleanup(shutil.rmtree, self.repo, True)

    def _choose(self, text: str, *, job=None, step=None, rel=".github/workflows/build.yml"):
        path = _write(self.repo, rel, text)
        return C.choose(C.load(path), job=job, step=step)

    def _passes(self, text: str, **kw):
        found = self._choose(text, **kw)
        self.assertIsInstance(found, C.Job, found)
        return found

    def _fails(self, text: str, words: str, **kw):
        found = self._choose(text, **kw)
        self.assertIsInstance(found, C.Fail, found)
        self.assertIn(words, found.reason)
        self.assertNotIn(self.repo, found.reason, "a detail names no absolute path")

    def _refused(self, text: str, words: str, **kw):
        with self.assertRaises(C.Refused) as caught:
            self._choose(text, **kw)
        self.assertIn(words, str(caught.exception))

    def test_the_baseline_shape_passes_and_says_what_it_chose(self):
        job = self._passes(_workflow())
        self.assertEqual((job.job, job.index, job.name, job.command, job.shell, job.image),
                         ("build", 1, "Build", "idf.py build", "bash -e {0}", IDF_IMAGE))
        self.assertEqual(job.workflow, ".github/workflows/build.yml")

    def test_the_pack_baseline_passes(self):
        path = os.path.join(PACK, "selftest", "assets", "esp-sleep", "github", "workflows",
                            "build.yml")
        job = C.choose(C.load(path), job="build")
        self.assertIsInstance(job, C.Job)
        self.assertEqual(job.workflow, "github/workflows/build.yml")

    def test_continue_on_error_fails(self):
        self._fails(_workflow(step_extra="continue-on-error: true\n"),
                    "step 'Build' has `continue-on-error: true`")
        self._fails(_workflow(job_extra="continue-on-error: true\n"),
                    "job build has `continue-on-error: true`")

    def test_a_fallback_that_succeeds_fails(self):
        for tail in ("|| true", "|| :", "|| exit 0", "|| echo failed"):
            with self.subTest(tail=tail):
                self._fails(_workflow(f"idf.py build {tail}"),
                            "falls back to a command that succeeds")
        self._passes(_workflow("idf.py build || exit 1"))

    def test_a_pipe_under_a_shell_with_no_pipefail_fails(self):
        self._fails(_workflow("idf.py build 2>&1 | tee build.log"),
                    "under 'bash -e {0}', which has no pipefail")
        self._fails(_workflow("idf.py build | tee b.log", step_extra="shell: sh\n"),
                    "under 'sh -e {0}'")
        self._fails(_workflow("west build -b x | tee b.log", step_extra="shell: sh -x {0}\n"),
                    "under 'sh -x {0}'")

    def test_pipefail_and_pipestatus_pass(self):
        self._passes(_workflow("idf.py build | tee b.log", step_extra="shell: bash\n"))
        self._passes(_workflow("set -o pipefail\nidf.py build | tee b.log"))
        self._passes(_workflow("set -eo pipefail\nidf.py build | tee b.log"))
        self._passes(_workflow("idf.py build | tee b.log\nexit ${PIPESTATUS[0]}"))
        self._passes(_workflow("yes | idf.py build"), )

    def test_errexit_off_then_another_command_fails(self):
        self._fails(_workflow("set +e\nidf.py build\necho done"), "after `set +e`")
        self._fails(_workflow("west build -b x\necho done", step_extra="shell: sh -x {0}\n"),
                    "which has no -e")
        self._passes(_workflow("set +e\nidf.py build\nrc=$?\nexit $rc"))
        self._passes(_workflow("set +e\nset -e\nidf.py build\necho done"))
        self._passes(_workflow("idf.py build", step_extra="shell: sh -x {0}\n"))

    def test_a_build_before_the_last_and_then_another_command_fails(self):
        """``-e`` does not stop the shell on a command before the last ``&&`` of a
        list (measured here: ``bash -e -c 'false && echo x; echo after'`` exits 0)."""
        self._fails(_workflow("idf.py build && echo built\nls build"),
                    "before an `&&`")
        self._fails(_workflow("west build -b x && echo built\necho done",
                              step_extra="shell: bash\n"), "before an `&&`")
        self._passes(_workflow("idf.py build && echo built"))
        self._passes(_workflow(". $IDF_PATH/export.sh && idf.py build\nls build"))
        self._passes(_workflow("idf.py build && echo built || exit 1\nls build"))
        self._passes(_workflow("idf.py build; echo a && echo b\nls build"))
        if shutil.which("bash"):
            proc = subprocess.run(["bash", "-e", "-c", "false && echo x; echo after"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual((proc.returncode, proc.stdout), (0, "after\n"),
                             "the documented behaviour the rule rests on")

    def test_every_build_command_and_every_command_after_it_is_read(self):
        """What slipped through reading the first build command's line alone (review
        of F1): a second build on the next line, piped or with a fallback, and the
        commands after a ``;`` on the build's own line."""
        two = "west build -b x -d build/left\nwest build -b x -d build/right"
        self._fails(_workflow(two + " 2>&1 | tee right.log"), "which has no pipefail")
        self._fails(_workflow(two + " || true"), "falls back to a command that succeeds")
        self._passes(_workflow(two))
        self._fails(_workflow("idf.py build && echo built; echo done"), "before an `&&`")
        self._fails(_workflow("set +e\nidf.py build; echo done"), "after `set +e`")
        self._passes(_workflow("idf.py build; ls build | grep bin"))
        self._passes(_workflow('west build -b x -- -DOVERLAY_CONFIG="a.conf;b.conf"\n'
                               'echo done'), )
        if shutil.which("bash"):
            proc = subprocess.run(["bash", "-e", "-c", "false && echo built; echo done"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual((proc.returncode, proc.stdout), (0, "done\n"),
                             "the `;` shape reads green on a failed command")

    def test_only_a_check_that_fails_the_step_counts(self):
        """What slipped through any ``$?`` on a later line: ``echo "build exited
        $?"`` after ``set +e`` counted as a status check, and the step read the
        echo's status."""
        self._fails(_workflow('set +e\nidf.py build\necho "build exited $?"'),
                    "with no status check that fails the step")
        self._fails(_workflow("set +e\nidf.py build\nif [ $? -ne 0 ]; then echo no; fi\n"
                              "echo done"), "after `set +e`")
        self._fails(_workflow("set +e\nidf.py build; echo done; exit $?"), "after `set +e`")
        self._fails(_workflow("idf.py build | tee b.log\necho done\nexit ${PIPESTATUS[0]}"),
                    "which has no pipefail")
        self._fails(_workflow("idf.py build | tee b.log || exit 1"), "which has no pipefail")
        for run in ("set +e\nidf.py build; exit $?",
                    "set +e\nidf.py build\n[ $? -eq 0 ] || exit 1\necho done",
                    "set +e\nidf.py build\nif [ $? -ne 0 ]; then\n  exit 1\nfi\necho done",
                    "set +e\nidf.py build || { echo failed; exit 1; }\necho done",
                    "idf.py build | tee b.log; exit ${PIPESTATUS[0]}",
                    "idf.py build | tee b.log\n[ ${PIPESTATUS[0]} -eq 0 ]",
                    "idf.py build | tee b.log\nrc=${PIPESTATUS[0]}\necho x\nexit $rc"):
            with self.subTest(run=run):
                self._passes(_workflow(run))

    def test_defaults_are_resolved_per_setting(self):
        """A job's ``working-directory`` leaves the workflow's ``shell: bash``
        standing, as GitHub resolves it: the pipe then runs with pipefail."""
        wf = _workflow("idf.py build | tee b.log",
                       top="defaults:\n  run:\n    shell: bash\n",
                       job_extra="defaults:\n  run:\n    working-directory: fw\n")
        self.assertIn("pipefail", self._passes(wf).shell)
        wf = _workflow("idf.py build | tee b.log",
                       top="defaults:\n  run:\n    shell: bash\n",
                       job_extra="defaults:\n  run:\n    shell: sh\n")
        self._fails(wf, "under 'sh -e {0}'")

    def test_shapes_the_replay_cannot_run_are_refused(self):
        self._refused(_workflow(runs_on="windows-latest", container=None),
                      "runs on windows-latest with no container")
        self._refused(_workflow(runs_on="macos-14", container=None), "macos-14")
        self._refused(_workflow(runs_on="[self-hosted, linux]", container=None), "self-hosted")
        self._passes(_workflow(runs_on="self-hosted"))           # with a container
        self._refused(_workflow(runs_on="${{ matrix.os }}", container=None),
                      "an expression only GitHub can evaluate")
        self._refused(_workflow(step_extra="if: ${{ github.ref == 'x' }}\n"),
                      "a ${{ }} expression in `if:`")
        self._refused(_workflow(job_extra="continue-on-error: ${{ matrix.x }}\n"),
                      "`continue-on-error:`")
        self._refused(_workflow("docker run -v $GITHUB_WORKSPACE:/w img idf.py build"),
                      "runs docker inside the job")
        self._refused(_workflow(step_extra="shell: pwsh\n"), "shell 'pwsh'")
        steps = ("      - uses: actions/checkout@v4\n"
                 "      - name: Build\n        uses: espressif/esp-idf-ci-action@v1\n")
        self._refused(_workflow(steps=steps), "runs docker inside the job", step="Build")
        reusable = ("on: [push]\njobs:\n  build:\n    uses: "
                    "zmkfirmware/zmk/.github/workflows/build-user-config.yml@v0.3\n")
        self._refused(reusable, "calls the reusable workflow", job="build")

    def test_a_job_or_step_it_cannot_choose_is_refused_never_failed(self):
        self._refused(_workflow("./scripts/build.sh"), "no job runs a build command")
        self._refused(_workflow("./scripts/build.sh"), "runs no build command", job="build")
        self.assertEqual(self._passes(_workflow("./scripts/build.sh"), step="Build").command,
                         "the named step")
        self.assertEqual(self._passes(_workflow("idf.py build\nmake -C docs")).command,
                         "idf.py build", "one step with two commands is one build step")
        two = ("      - name: A\n        run: idf.py build\n"
               "      - name: B\n        run: west build -b x\n")
        self._refused(_workflow(steps=two), "builds in 2 steps")
        jobs = _workflow() + "  other:\n    runs-on: ubuntu-24.04\n    steps:\n" \
                             "      - run: make\n"
        self._refused(jobs, "2 jobs build")
        self.assertEqual(self._passes(jobs, job="other").job, "other")
        self._refused(_workflow(), "has no such job", job="nope")
        self._refused(_workflow(), "no job has a step named 'nope'", step="nope")
        self._refused(_workflow(), "matches 0 steps", job="build", step="nope")

    def test_a_workflow_that_cannot_run_fails(self):
        path = os.path.join(self.repo, ".github", "workflows", "gone.yml")
        found = C.choose(C.load(path))
        self.assertIsInstance(found, C.Fail)
        self.assertIn("CI cannot run this workflow", found.reason)
        self._fails("jobs: [unclosed\n", "does not parse")
        self._fails("name: x\non: [push]\n", "has no jobs")

    def test_a_workflow_outside_workflows_is_refused(self):
        path = _write(self.repo, "build.yml", _workflow())
        with self.assertRaises(C.Refused):
            C.load(path)

    def test_only_a_command_starts_a_build(self):
        """What slipped through a match anywhere in a line: the STM32 template's
        toolchain step installs the package ``make``, and read as a build step."""
        steps = ("      - name: Toolchain\n"
                 "        run: sudo apt-get install -y gcc-arm-none-eabi make\n"
                 "      - name: Build\n        run: make -j4\n")
        self.assertEqual(self._passes(_workflow(steps=steps)).name, "Build")
        for line, want in (("cmake -S . -B build", None), ("cmake --build build", "cmake --build"),
                           ("echo west build", None), ("# west build", None),
                           ("FOO=1 west build -b x", "west build"),
                           ("$HOME/bin/arduino-cli compile --fqbn a:b:c .",
                            "arduino-cli compile"),
                           ("cd fw && make", "make"), ("pip install platformio==6", None)):
            with self.subTest(line=line):
                hit = C._build_in(line)
                self.assertEqual(hit[0] if hit else None, want)

    def test_every_template_the_pack_prints_passes_the_guard(self):
        """A template that the guard refused or failed would send a user to a CI the
        pack cannot replay."""
        with open(os.path.join(PACK, "references", "ci-templates.md"), encoding="utf-8") as fh:
            blocks = re.findall(r"```yaml\n(.*?)```", fh.read(), re.S)
        self.assertGreaterEqual(len(blocks), 7)
        for i, block in enumerate(blocks):
            with self.subTest(template=i):
                job = self._passes(block, rel=f".github/workflows/t{i}.yml")
                self.assertNotEqual(job.command, "the named step")


# --------------------------------------------------------------------------- #
# the build's own lines
# --------------------------------------------------------------------------- #
def _log(name: str) -> str:
    with open(os.path.join(LOGS, name), encoding="utf-8") as fh:
        return fh.read()


#: The workflow ``tests/fw_logs/act_step_ids.jsonl`` was recorded from (act 0.2.97,
#: debian:bookworm, the replay's own flags): steps with an ``id:`` and one without.
STEP_IDS = """name: b
on: [push]
jobs:
  build:
    runs-on: ubuntu-24.04
    container: debian:bookworm
    steps:
      - name: Prep
        id: prep
        run: echo prepared
      - name: Build
        id: bld
        run: mkdir -p build && head -c 64 /dev/zero > build/app.bin && echo built
      - name: Upload
        run: echo uploaded
"""


def _job(**over):
    base = dict(workflow="github/workflows/build.yml", job="build", index=1, name="Build",
                command="idf.py build", shell="bash -e {0}", image=IDF_IMAGE,
                runs_on="ubuntu-24.04", matrix=False)
    base.update(over)
    return C.Job(**base)


def _recorded(name: str, files: dict | None = None) -> dict:
    """A replay's result from a committed act excerpt, scrubbed as the loader
    scrubs it."""
    entries, act = R.parse(_log(name), functools.partial(
        R._scrub, copy=RECORDED_COPY, tmp=os.path.dirname(RECORDED_COPY)))
    return {"workflow": "github/workflows/build.yml", "job": "build", "rc": 0,
            "timeout": False, "entries": entries, "act": act, "files": files or {},
            "oversize": 0, "toolchain": {}, "cleanup": ""}


def _step_result(log: str, result: str = "failure", *, before: str = "success",
                 files: dict | None = None) -> dict:
    """A replay whose build step (index 1) ended ``result`` with output ``log``."""
    entry = {"job": "build", "matrix": {}, "result": result, "act": [], "steps": [
        {"stage": "", "id": ["--setup-job"], "name": "Set up job", "result": "success",
         "log": "", "act": []},
        {"stage": "Main", "id": ["0"], "name": "Checkout", "result": before, "log": "",
         "act": ["exitcode '1': failure"] if before == "failure" else []},
        {"stage": "Main", "id": ["1"], "name": "Build", "result": result, "log": log,
         "act": []}]}
    return {"workflow": "w", "job": "build", "rc": 0, "timeout": False, "entries": [entry],
            "act": [], "files": files or {}, "oversize": 0, "toolchain": {}, "cleanup": ""}


BIN = {"build/sleeper.bin": b"\xe9" + bytes(15), "build/sleeper.elf": b"\x7fELF"}


class TheBuildIsJudgedOnItsOwnLines(unittest.TestCase):
    """``_replay.judge``: a design fails on a line from the closed list; the tool's
    fault is errored, and never reads as a design's."""

    def _fails(self, result: dict, words: str, **kw):
        passed, detail = R.judge(result, _job(), **kw)
        self.assertFalse(passed, detail)
        self.assertIn(words, detail)
        self.assertNotIn(RECORDED_COPY, detail)

    def _raises(self, result: dict, words: str, **kw):
        with self.assertRaises(R.ToolFault) as caught:
            R.judge(result, _job(), **kw)
        self.assertIn(words, str(caught.exception))

    def test_the_short_slot_build_passes_here(self):
        """ESP-IDF only warns when one app partition is too small: fw.build passes
        the copy (fw.image_fits fails it), so the edge is isolated."""
        result = _recorded("act_esp_short_ota_slot.jsonl", BIN)
        self.assertIn("1/3 app partitions are too small", R.main_step(result["entries"][0], 1)["log"])
        passed, detail = R.judge(result, _job(), "build/sleeper.bin")
        self.assertTrue(passed, detail)
        self.assertEqual(detail, f"job build: step 'Build' (idf.py build) passed in "
                                 f"espressif/idf@sha256:bf72fe7a15c2; wrote "
                                 f"build/sleeper.bin, build/sleeper.elf")

    def test_an_unresolved_symbol_fails_on_its_line(self):
        self._fails(_recorded("act_esp_undefined_reference.jsonl"),
                    "the build fails: ld: esp-idf/main/libmain.a(main.c.obj):"
                    "(.literal.app_main+0x0): undefined reference to `board_power_init'")

    def test_a_masked_failure_fails_on_its_line(self):
        """The pipe into tee: the step read success, and its output holds Ninja's
        FAILED line. fw.ci_job is the gate for this; fw.build catches it too."""
        result = _recorded("act_esp_masked_pipe.jsonl", BIN)
        self.assertEqual(R.main_step(result["entries"][0], 1)["result"], "success")
        self._fails(result, "the build step read success, and its output holds "
                            "'FAILED: esp-idf/main/CMakeFiles/__idf_main.dir/main.c.obj'")

    def test_each_design_line_fails_a_failed_build(self):
        for name, words in (("esp_compile_error.log", "FAILED: esp-idf/main/"),
                            ("esp_region_overflow.log", "ld: region `dram0_0_seg' overflowed by 368248 bytes"),
                            ("invalid_board.log", "Invalid BOARD; see above."),
                            ("devicetree_tabb.log", "devicetree error: <repo>/config/corne.keymap:22"),
                            ("kconfig_abort.log", "error: Aborting due to Kconfig warnings")):
            with self.subTest(log=name):
                self._fails(_step_result(_log(name)), f"the build fails: {words}")

    def test_the_wrong_zmk_config_at_exit_0_fails(self):
        self._fails(_step_result(_log("wrong_config.log"), "success"),
                    "could not find its ZMK config")
        passed, _ = R.judge(_step_result(_log("corne_full.log"), "success"), _job())
        self.assertTrue(passed)

    def test_a_compiler_the_machine_killed_is_the_tools(self):
        """Upstream corne under ``--memory=200m``: ten ``FAILED: ...obj`` lines, each
        from a compiler the OOM killer stopped. Before the list, the first
        ``FAILED`` read as a design FAIL on known-good ZMK."""
        self._raises(_step_result(_log("oom_killed.log")),
                     "the machine stopped the build, not the design: "
                     "arm-zephyr-eabi-gcc: fatal error: Killed signal terminated program cc1")
        for words in ("internal compiler error: Segmentation fault", "No space left on device",
                      "Could not resolve host: github.com"):
            self._raises(_step_result(_log("esp_compile_error.log") + f"\n{words}\n"),
                         "not the design")

    def test_a_build_a_signal_stopped_is_the_tools(self):
        """Measured while the pack was written: a second replay of the same workflow
        removed this one's container mid-build (act names a job's container by the
        workflow and the job alone). The output's last line named a source file
        with "error" in its name; it read as the step's own failure."""
        self._raises(_recorded("act_esp_signal_137.jsonl"),
                     "stopped by a signal (exit 137): Error response from daemon")

    def test_a_failure_no_line_names_is_the_tools(self):
        self._raises(_step_result(_log("docker_127.log")), "on no line this pack knows")

    def test_act_or_docker_stopping_before_any_step_is_the_tools(self):
        result = _step_result("")
        result["entries"], result["act"] = [], [_log("docker_125.log").splitlines()[0]]
        self._raises(result, "before any step of job build: docker: Error response")
        result = _step_result("")
        result["entries"][0]["steps"][0]["result"] = "failure"
        self._raises(result, "could not set up job build")
        self._raises(dict(_step_result(""), timeout=True), "did not finish in 1800 s")
        self._raises(dict(_step_result(""), oversize=R.OUTPUT_CAP + 1), "past the")

    def test_a_step_before_the_build_is_the_tools_unless_ci_tolerates_it(self):
        self._raises(_step_result("", "success", before="failure"),
                     "step 'Checkout' before the build failed")
        passed, _ = R.judge(_step_result("", "success", before="failure", files=BIN), _job(),
                            tolerated=frozenset({0}))
        self.assertTrue(passed)

    def test_a_skipped_or_missing_build_step_is_the_tools(self):
        self._raises(_step_result("", "skipped"), "was skipped")
        result = _step_result("")
        del result["entries"][0]["steps"][2]
        self._raises(result, "never ran")

    def test_an_image_the_job_did_not_write_fails(self):
        result = _step_result("", "success", files={"build/app.bin": b"x"})
        self._fails(result, "the file you flash (build/sleeper.bin) is not what job build "
                            "builds: it wrote build/app.bin", image_glob="build/sleeper.bin")

    def test_a_step_act_logs_by_its_id_is_found(self):
        """act 0.2.97 logs a step that has an ``id:`` under that id, not its index
        (``act_step_ids.jsonl``, recorded from :data:`STEP_IDS`). What slipped
        through matching the index alone (review of F1): such a build step "never
        ran", and a failed step before it with an id was never named."""
        repo = tempfile.mkdtemp(prefix="fw-ids-")
        self.addCleanup(shutil.rmtree, repo, True)
        if not HAS_YAML:
            self.skipTest(NO_YAML)
        job = C.choose(C.load(_write(repo, "github/workflows/build.yml", STEP_IDS)),
                       step="bld")
        self.assertEqual((job.index, job.ids, job.command), (1, ("prep", "bld", "2"),
                                                             "the named step"))
        result = _recorded("act_step_ids.jsonl", {"build/app.bin": bytes(64)})
        self.assertEqual([s["id"] for s in result["entries"][0]["steps"]
                          if s["stage"] == "Main"], [["prep"], ["bld"], ["2"]])
        passed, detail = R.judge(result, job, "build/app.bin")
        self.assertTrue(passed, detail)
        self.assertEqual(detail, "job build: step 'Build' (the named step) passed in "
                                 "debian:bookworm; wrote build/app.bin; later steps not "
                                 "judged: Upload (success)")
        R.main_step(result["entries"][0], "prep")["result"] = "failure"
        with self.assertRaises(R.ToolFault) as caught:
            R.judge(result, job, "build/app.bin")
        self.assertIn("step 'Prep' before the build failed", str(caught.exception))

    def test_steps_after_the_build_are_named_not_judged(self):
        result = _step_result("", "success", files=BIN)
        result["entries"][0]["steps"].append({"stage": "Main", "id": ["2"], "name": "Upload",
                                              "result": "failure", "log": "", "act": []})
        passed, detail = R.judge(result, _job())
        self.assertTrue(passed)
        self.assertIn("later steps not judged: Upload (failure)", detail)

    def test_the_log_is_scrubbed_of_the_copy_and_the_clock(self):
        result = _recorded("act_esp_short_ota_slot.jsonl")
        text = json.dumps(result)
        self.assertNotIn(RECORDED_COPY, text)
        self.assertNotRegex(text, r"\[\d+(?:\.\d+)?(?:µs|ms|s)\]")
        self.assertIn("<repo>/build/sleeper.bin", R.main_step(result["entries"][0], 1)["log"])
        # CMake's own clock, measured at 5.6 s and 4.7 s in two replays of one tree.
        scrubbed = R._scrub("-- Configuring done (5.6s)\n-- Generating done (0.8s)\n"
                            "-- Build files have been written to: /t/repo/build\n",
                            copy="/t/repo", tmp="/t")
        self.assertEqual(scrubbed, "-- Configuring done\n-- Generating done\n"
                                   "-- Build files have been written to: <repo>/build\n")

    def test_a_glob_stays_inside_a_directory(self):
        """What slipped through ``fnmatch``: its ``*`` crosses ``/``, so
        ``build/*.bin`` named the bootloader's and the partition table's images
        on the baseline too, and the gate refused to choose."""
        files = {"build/sleeper.bin": b"\xe9", "build/bootloader/bootloader.bin": b"\xe9",
                 "build/partition_table/partition-table.bin": b"x"}
        self.assertEqual(R.built_file({"files": files}, "build/*.bin", "firmware_image")[0],
                         "build/sleeper.bin")
        for rel, glob, hit in (("build/zephyr/zmk.uf2", "build/**/zmk.uf2", True),
                               ("build/zmk.uf2", "build/**/zmk.uf2", True),
                               ("build/zephyr/zmk.uf2", "build/*.uf2", False),
                               (".pio/build/esp/firmware.bin", ".pio/build/*/firmware.bin", True),
                               ("build/a.bin", "build/?.bin", True),
                               ("build/ab.bin", "build/?.bin", False),
                               ("build/a+b.bin", "build/a+b.bin", True)):
            with self.subTest(rel=rel, glob=glob):
                self.assertEqual(R.matches(rel, glob), hit)

    def test_every_design_line_has_a_committed_excerpt(self):
        """The list is closed: a line joins it with its log here."""
        text = ""
        for name in sorted(os.listdir(LOGS)):
            raw = _log(name)
            if name.endswith(".jsonl"):
                raw = "".join(json.loads(ln).get("msg", "") for ln in raw.splitlines() if ln)
            text += raw + "\n"
        self.assertEqual([s.pattern for s in R.DESIGN_LINES if not s.search(text)], [])
        self.assertTrue(R.ZMK_NO_CONFIG.search(text))
        # Only the OOM killer was measured; the rest are named.
        self.assertEqual([s.pattern for s in R.TOOL_FAULT_LINES if s.search(text)],
                         [R.TOOL_FAULT_LINES[0].pattern])


# --------------------------------------------------------------------------- #
# the copy
# --------------------------------------------------------------------------- #
def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True,
                          env=dict(os.environ, GIT_CONFIG_NOSYSTEM="1",
                                   GIT_CONFIG_GLOBAL=os.devnull,
                                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


APP = {"CMakeLists.txt": "x\n", "main/main.c": "int x;\n", ".env": "SECRET=1\n",
       ".secrets": "TOKEN=1\n", "sub/.actrc": "-P x\n", ".nopekit/ledger.json": "{}\n",
       "github/workflows/build.yml": "on: push\n"}


@unittest.skipUnless(HAS_GIT, "git is not on PATH: the copy is not walked")
class TheCopyIsWhatTheNextCommitHolds(unittest.TestCase):
    """``_replay.listing`` and the copy. What slipped through the design (critic
    5.2): ``git ls-files`` alone listed nothing for a known-bad copy under the
    project's ignored ``.nopekit/out/``, and failed outside a work tree (a
    temporary directory, an installed wheel), so every control read errored."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fw-copy-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _app(self, root: str) -> str:
        for rel, text in APP.items():
            _write(root, rel, text)
        return root

    def _expected(self):
        return ["CMakeLists.txt", "github/workflows/build.yml", "main/main.c"]

    def test_in_a_work_tree_it_is_what_git_would_commit(self):
        repo = self._app(os.path.join(self.tmp, "repo"))
        _write(repo, ".gitignore", "build/\n")
        _write(repo, "build/old.bin", "x")
        self.assertEqual(_git("init", "-q", cwd=repo).returncode, 0)
        _git("add", "CMakeLists.txt", cwd=repo)
        self.assertEqual(R.listing(repo), [".gitignore"] + self._expected())

    def test_an_ignored_directory_is_walked(self):
        outer = os.path.join(self.tmp, "project")
        _write(outer, ".gitignore", ".nopekit/out/\n")
        self.assertEqual(_git("init", "-q", cwd=outer).returncode, 0)
        copy = self._app(os.path.join(outer, ".nopekit", "out", "controls", "fw.build",
                                      "esp-sleep"))
        self.assertEqual(_git("ls-files", "-co", "--exclude-standard", cwd=copy).stdout, "",
                         "git alone lists nothing here: the case that slipped through")
        self.assertEqual(R.listing(copy), self._expected())
        self._copies(copy)

    def test_a_tree_in_no_work_tree_is_walked(self):
        """A temporary directory in pack mode, and an installed wheel's pack."""
        repo = self._app(os.path.join(self.tmp, "wheel", "nopekit", "bundled", "app"))
        _write(repo, "main/__pycache__/x.pyc", "x")
        self.assertEqual(R.listing(repo), self._expected())
        self._copies(repo)

    def test_a_symlink_is_copied_as_the_link(self):
        """What slipped through ``os.path.isdir`` (review of F1): it follows a link,
        so a tracked ``ext -> ../outside`` was copied whole from outside the
        repository, where a GitHub checkout holds a dangling link."""
        outside = os.path.join(self.tmp, "outside")
        _write(outside, "deep/data.txt", "not in the repository\n")
        for in_tree in (True, False):
            with self.subTest(in_tree=in_tree):
                repo = self._app(os.path.join(self.tmp, f"repo{in_tree}"))
                os.symlink(os.path.join("..", "outside"), os.path.join(repo, "ext"))
                os.symlink("CMakeLists.txt", os.path.join(repo, "alias.txt"))
                if in_tree:
                    self.assertEqual(_git("init", "-q", cwd=repo).returncode, 0)
                self.assertEqual(R.listing(repo), ["CMakeLists.txt", "alias.txt", "ext"]
                                 + self._expected()[1:])
                copy = os.path.join(self.tmp, f"c{in_tree}", "repo")
                trace = verdicts_mod.GateTrace()
                with verdicts_mod.tracing(trace):
                    copied = R._copy(repo, copy)
                self.assertEqual((copied["ext"], copied["alias.txt"]),
                                 ("-> " + os.path.join("..", "outside"), "-> CMakeLists.txt"))
                self.assertTrue(os.path.islink(os.path.join(copy, "ext")))
                self.assertEqual(os.readlink(os.path.join(copy, "alias.txt")), "CMakeLists.txt")
                self.assertFalse(os.path.exists(os.path.join(copy, "ext", "deep")),
                                 "the link dangles in the copy, as on the runner")
                self.assertFalse([p for p in trace.files_read if p.startswith(outside)],
                                 "nothing outside the repository is read")

    def _copies(self, repo: str):
        copy, home = os.path.join(self.tmp, "c", "repo"), os.path.join(self.tmp, "c", "home")
        trace = verdicts_mod.GateTrace()
        with verdicts_mod.tracing(trace):
            before = R._copy(repo, copy)
            R._git_init(copy, home)
        self.assertEqual(sorted(before), self._expected())
        self.assertEqual(_git("rev-parse", "HEAD", cwd=copy).returncode, 0,
                         "the copy is a git repository with a commit")
        read = sorted(os.path.relpath(p, repo) for p in trace.files_read)
        self.assertEqual(read, [os.path.join(*p.split("/")) for p in self._expected()],
                         "every file copied is a read, and nothing in the copy is")


# --------------------------------------------------------------------------- #
# the whole replay, through a stand-in act and docker
# --------------------------------------------------------------------------- #
class _StandIn(unittest.TestCase):
    """A bin directory with ``act`` and ``docker`` stand-ins first on PATH, and a
    HOME of the test's own, so the replay's lock and caches land nowhere real."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fw-standin-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        for tool in ("act", "docker"):
            path = os.path.join(self.bin, tool)
            with open(STANDIN, encoding="utf-8") as src, open(path, "w", encoding="utf-8") as fh:
                fh.write(f"#!{sys.executable}\n" + src.read())
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.env = {"PATH": self.bin + os.pathsep + os.environ.get("PATH", ""),
                    "HOME": os.path.join(self.tmp, "home")}
        os.makedirs(self.env["HOME"])
        self.mode({})

    def mode(self, mode: dict):
        with open(os.path.join(self.bin, "mode.json"), "w", encoding="utf-8") as fh:
            json.dump(mode, fh)

    def calls(self) -> list[dict]:
        path = os.path.join(self.bin, "calls")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [json.loads(ln) for ln in fh if ln.strip()]

    def app(self) -> str:
        """The pack's baseline app, copied where no work tree holds it."""
        dst = os.path.join(self.tmp, "esp-sleep")
        shutil.copytree(os.path.join(PACK, "selftest", "assets", "esp-sleep"), dst)
        return os.path.join(dst, "github", "workflows", "build.yml")

    def replay(self, path: str) -> dict:
        with mock.patch.dict(os.environ, self.env):
            return R.replay(path, job="build")


@unittest.skipUnless(HAS_GIT, "git is not on PATH: the replay's copy is not walked")
class TheReplayRunsActAsDesigned(_StandIn):
    def test_the_argv_the_environment_and_what_comes_back(self):
        self.mode({"log": os.path.join(LOGS, "act_esp_short_ota_slot.jsonl"),
                   "copy_in_log": RECORDED_COPY,
                   "write": {"build/sleeper.bin": {"size": 0x7b6d0, "head": "e9"},
                             "build/sleeper.elf": {"head": "7f454c46", "size": 64},
                             "sdkconfig": {"head": "23", "size": 8},
                             "build/compile_commands.json": {"size": 4},
                             "build/CMakeFiles/3.30.2/CMakeDetermineCompilerABI_C.bin":
                                 {"head": "7f454c46", "size": 64}}})
        result = self.replay(self.app())
        act = [c for c in self.calls() if c["tool"] == "act"]
        self.assertEqual([c["argv"][:1] for c in act], [["--version"], ["push"]])
        argv, env = act[1]["argv"], act[1]["env"]
        for flag in ("--bind", "--json", "--pull=false", "--action-offline-mode",
                     "--no-cache-server"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--container-daemon-socket") + 1], "-")
        self.assertEqual(argv[argv.index("-W") + 1], "github/workflows/build.yml")
        self.assertEqual(argv[argv.index("-j") + 1], "build")
        for name in ("--secret-file", "--var-file", "--env-file", "--input-file"):
            self.assertEqual(argv[argv.index(name) + 1], os.devnull)
        self.assertIn(f"ubuntu-24.04={R.RUNNER_IMAGES['ubuntu-24.04']}", argv)
        self.assertNotEqual(env["HOME"], self.env["HOME"], "no user .actrc applies")
        self.assertEqual(env["ACT_DISABLE_VERSION_CHECK"], "1")
        self.assertTrue(act[1]["commit"], "act saw a git commit in the copy")
        self.assertEqual(act[1]["files"], ["CMakeLists.txt", "LICENSE",
                                           "github/workflows/build.yml", "main/CMakeLists.txt",
                                           "main/main.c", "partitions.csv",
                                           "sdkconfig.defaults"])
        self.assertEqual(sorted(result["files"]), ["build/sleeper.bin", "build/sleeper.elf",
                                                   "sdkconfig"])
        self.assertEqual(len(result["files"]["build/sleeper.bin"]), 0x7b6d0)
        self.assertEqual(result["toolchain"], {IDF_IMAGE: "sha256:" + "0" * 64} if HAS_YAML
                         else {R.RUNNER_IMAGES["ubuntu-latest"]: "sha256:" + "0" * 64})
        self.assertNotIn(RECORDED_COPY, json.dumps(result["entries"]))
        self.assertTrue(R.judge(result, _job(), "build/sleeper.bin")[0])
        self.assertFalse(os.path.exists(os.path.dirname(act[1]["argv"][
            act[1]["argv"].index("-C") + 1])), "the copy is removed")

    def test_an_image_the_job_rebuilt_byte_for_byte_is_read_back(self):
        """What slipped through "its bytes differ from the copy's" (review of F1): a
        tracked image that a reproducible build wrote again, byte for byte, was
        left out, and fw.build failed with "it wrote no image". A tracked image the
        job never touched is still left out."""
        path = self.app()
        app = os.path.dirname(os.path.dirname(os.path.dirname(path)))
        same = b"\xe9" + bytes(63)
        for rel in ("dist/app.bin", "dist/old.bin"):
            os.makedirs(os.path.join(app, "dist"), exist_ok=True)
            with open(os.path.join(app, *rel.split("/")), "wb") as fh:
                fh.write(same)
        self.mode({"log": os.path.join(LOGS, "act_esp_short_ota_slot.jsonl"),
                   "copy_in_log": RECORDED_COPY, "sleep": 0.05,
                   "write": {"dist/app.bin": {"head": "e9", "size": 64}}})
        result = self.replay(path)
        self.assertEqual(sorted(result["files"]), ["dist/app.bin"])
        self.assertEqual(result["files"]["dist/app.bin"], same)
        self.assertTrue(R.judge(result, _job(), "dist/app.bin")[0])

    def test_a_timeout_stops_the_job_containers(self):
        self.mode({"sleep": 5})
        with mock.patch.object(R, "TIMEOUT_S", 1):
            result = self.replay(self.app())
        self.assertTrue(result["timeout"])
        docker = [c["argv"] for c in self.calls() if c["tool"] == "docker"]
        self.assertTrue(any(a[:2] == ["ps", "-q"] and a[3].startswith("volume=") for a in docker),
                        docker)
        with self.assertRaises(R.ToolFault):
            R.judge(result, _job())


class TheActVersionCheckNeedsNothingElse(_StandIn):
    """The version check alone, with no PyYAML and no git: CI installs neither
    package, and the pin is walked there too. What slipped through the first draft:
    the pin was tested only through the gate, which reads the workflow with
    PyYAML, so on CI it was never walked."""

    def _version(self):
        with mock.patch.dict(os.environ, self.env):
            return R.act_version()

    def test_the_pinned_version_is_read(self):
        self.assertEqual(self._version(), R.ACT_VERSION)

    def test_another_version_is_wrong_act_with_the_install_line(self):
        self.mode({"version": "0.2.98"})
        with self.assertRaises(R.WrongAct) as caught:
            self._version()
        self.assertIn("act --version says 0.2.98", str(caught.exception))
        self.assertIn(R.ACT_INSTALL, str(caught.exception))

    def test_an_act_that_does_not_start_is_a_tool_fault(self):
        self.mode({"version_crash": True})
        with self.assertRaises(R.ToolFault) as caught:
            self._version()
        self.assertIn("act did not start", str(caught.exception))


@unittest.skipUnless(HAS_GIT and HAS_YAML, "git or PyYAML is missing: the gate does not run")
class TheActVersionIsPinned(_StandIn):
    """Critic 5.3: an act other than 0.2.97 skips and names the install line, as
    fdm.slice skips an OrcaSlicer it was not measured on; it never errors."""

    def _build(self, path: str):
        spec, fn = _registry().get("fw.build")
        ctx = gates_mod.GateContext(root=self.tmp, ledger=Ledger(), params={
            "firmware_ci_workflow": path}, out_dir=os.path.join(self.tmp, "out"), tier=1,
            log=lambda _m: None, extra={})
        with mock.patch.dict(os.environ, self.env):
            self.assertEqual(gates_mod.availability(spec), (True, ""))
            return gates_mod.run_gate(spec, fn, ctx)

    def test_another_version_skips_and_names_the_install_line(self):
        self.mode({"version": "0.2.98"})
        verdict = self._build(self.app())
        self.assertTrue(verdict.skipped and not verdict.passed, verdict.render())
        self.assertIn("act --version says 0.2.98", verdict.skip_reason)
        self.assertIn("releases/download/v0.2.97/act_Linux_x86_64.tar.gz", verdict.skip_reason)
        self.assertEqual([c["argv"] for c in self.calls()], [["--version"]],
                         "nothing is copied or run past the version check")

    def test_an_act_that_does_not_start_is_errored(self):
        self.mode({"version_crash": True})
        verdict = self._build(self.app())
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("act did not start", verdict.error)

    def test_the_pinned_version_replays(self):
        self.mode({"log": os.path.join(LOGS, "act_esp_short_ota_slot.jsonl"),
                   "copy_in_log": RECORDED_COPY,
                   "write": {"build/sleeper.bin": {"size": 0x7b6d0, "head": "e9"},
                             "build/sleeper.elf": {"head": "7f454c46", "size": 64}}})
        verdict = self._build(self.app())
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertEqual([os.path.basename(p) for p in verdict.evidence],
                         ["log.txt", "images.json", "toolchain.json"])


# --------------------------------------------------------------------------- #
# images, built here
# --------------------------------------------------------------------------- #
def elf32(*, machine: int = I.EM_ARM, loads=(), funcs=(), objects=(), producers=()) -> bytes:
    """A little-endian ELF32 with ``loads`` (``(paddr, vaddr, data, memsz)``), a
    symbol table of ``funcs`` and ``objects``, and ``producers`` in ``.debug_str``."""
    strtab = b"\x00"
    syms = [bytes(16)]
    for names, kind in ((funcs, I.STT_FUNC), (objects, I.STT_OBJECT)):
        for name in names:
            syms.append(struct.pack("<IIIBBH", len(strtab), 0x1000, 4, (1 << 4) | kind, 0, 1))
            strtab += name.encode() + b"\x00"
    symtab = b"".join(syms)
    debug = b"".join(p.encode() + b"\x00" for p in producers)
    shstr = b"\x00.symtab\x00.strtab\x00.shstrtab\x00.debug_str\x00"
    ph_off = 52
    data_off = ph_off + 32 * len(loads)
    blobs, phdrs, at = b"", b"", data_off
    for paddr, vaddr, data, memsz in loads:
        phdrs += struct.pack("<IIIIIIII", I.PT_LOAD, at, vaddr, paddr, len(data), memsz, 5, 4)
        blobs += data
        at += len(data)
    sections = [(symtab, 2, 2, 16), (strtab, 3, 0, 0), (shstr, 3, 0, 0), (debug, 1, 0, 0)]
    names = [1, 9, 17, 27]
    body, offs = b"", []
    for blob, *_rest in sections:
        offs.append(at + len(body))
        body += blob
    sh_off = at + len(body)
    shdrs = bytes(40)
    for (blob, kind, link, entsize), name, off in zip(sections, names, offs):
        shdrs += struct.pack("<IIIIIIIIII", name, kind, 0, 0, off, len(blob), link, 0, 1, entsize)
    head = struct.pack("<16sHHIIIIIHHHHHH", b"\x7fELF\x01\x01\x01" + bytes(9), 2, machine, 1,
                       0, ph_off, sh_off, 0, 52, 32, len(loads), 40, 5, 3)
    return head + phdrs + blobs + body + shdrs


def uf2(blocks) -> bytes:
    out = b""
    for n, (addr, data, flags) in enumerate(blocks):
        out += struct.pack("<IIIIIIII", *I.UF2_MAGIC[:2], flags, addr, len(data), n,
                           len(blocks), 0)
        out += data.ljust(476, b"\x00") + struct.pack("<I", I.UF2_MAGIC[2])
    return out


def ihex(records) -> bytes:
    lines = []
    for kind, addr, data in records:
        rec = bytes([len(data), addr >> 8, addr & 0xFF, kind]) + data
        lines.append(":" + (rec + bytes([(-sum(rec)) & 0xFF])).hex().upper())
    return ("\n".join(lines) + "\n").encode()


def vectors(sp: int, reset: int) -> bytes:
    return struct.pack("<II", sp, reset) + bytes(56)


#: A map of the address shape with two RAM banks; every number is this test's own.
MAP = {"flash": {"start": "0x08000000", "size": "0x80000"},
       "app": {"start": "0x08010000", "size": "0x70000", "vector_at": "0x08010000"},
       "ram": [{"start": "0x20000000", "size": "0x20000"},
               {"start": "0x10000000", "size": "0x10000"}],
       "other": [{"name": "qspi", "start": "0x90000000", "size": "0x100000"}]}


class TheImageIsReadByItsFormat(unittest.TestCase):
    def setUp(self):
        self.mm = I.parse_map(MAP)

    def _fit(self, image: bytes, mm=None, elf=None):
        return I.judge_address(image, "fw.bin", mm or self.mm, elf)

    def test_an_elf_inside_its_region_measures_its_flash_use(self):
        image = elf32(loads=[(0x08010000, 0x08010000, vectors(0x20020000, 0x08010101), 64),
                             (0x08010040, 0x20000000, bytes(0x37fc0), 0x1000)],
                      funcs=["Reset_Handler"], objects=["g"], producers=["GNU C17 13.2.0 -Os"])
        fit = self._fit(image)
        self.assertEqual((fit.problems, fit.measured), ([], 50.0))
        self.assertIn("50.0 % flash use of app 0x8010000..0x807ffff", fit.said)
        elf = I.read_elf(image)
        self.assertEqual((elf.funcs, elf.objects, I.lto(elf)),
                         (frozenset({"Reset_Handler"}), frozenset({"g"}), False))

    def test_the_top_of_a_ram_bank_is_a_sound_stack_pointer(self):
        """STM32Cube's ``_estack = ORIGIN(RAM) + LENGTH(RAM)``: one past the end."""
        for sp in (0x20020000, 0x10010000, 0x20000004):
            with self.subTest(sp=hex(sp)):
                self.assertEqual(self._fit(uf2([(0x08010000, vectors(sp, 0x08010101), 0)])
                                           ).problems, [])
        fit = self._fit(uf2([(0x08010000, vectors(0x20000000, 0x08010101), 0)]))
        self.assertIn("initial stack pointer 0x20000000", fit.problems[0])

    def test_the_vector_rules(self):
        fit = self._fit(uf2([(0x08010000, vectors(0x20020000, 0x08010100), 0)]))
        self.assertEqual(fit.problems, ["the reset vector 0x8010100 lacks the Thumb bit"])
        fit = self._fit(uf2([(0x08010000, vectors(0x20020000, 0x08000101), 0)]))
        self.assertIn("points outside the app region", fit.problems[0])
        fit = self._fit(uf2([(0x08010100, vectors(0x20020000, 0x08010101), 0)]))
        self.assertIn("linked for another start", fit.problems[0])

    def test_bytes_below_the_app_or_past_it_fail(self):
        fit = self._fit(ihex([(4, 0, b"\x08\x00"), (0, 0x0000, vectors(0x20020000, 0x08010101)),
                              (1, 0, b"")]))
        self.assertTrue(any("bytes at 0x8000000..0x800003f lie in flash outside the app "
                            "region" in p for p in fit.problems), fit.problems)
        short = I.parse_map({**MAP, "app": {**MAP["app"], "size": "0x60000"}})
        fit = self._fit(uf2([(0x08010000, vectors(0x20020000, 0x08010101), 0),
                             (0x0806FFF0, bytes(32), 0)]), short)
        self.assertEqual(len(fit.problems), 1, fit.problems)
        self.assertIn("bytes at 0x8070000..0x807000f lie in flash outside", fit.problems[0])

    def test_the_readers_refuse_what_is_not_their_format(self):
        with self.assertRaises(I.ImageFault) as caught:
            I.read_hex(b":10000000ZZ\n")
        self.assertIn("not hexadecimal", str(caught.exception))
        with self.assertRaises(I.ImageFault) as caught:
            I.read_hex(ihex([(0, 0, b"\x01\x02")])[:-3] + b"00\n")
        self.assertIn("checksum", str(caught.exception))
        with self.assertRaises(I.ImageFault) as caught:
            I.read_uf2(uf2([(0x08010000, bytes(4), 0)])[:-1])
        self.assertIn("not a whole number", str(caught.exception))
        with self.assertRaises(I.ImageFault) as caught:
            I.read_uf2(b"\x00" * 512)
        self.assertIn("no UF2 magic", str(caught.exception))

    def test_other_ranges_are_reported_and_nowhere_is_errored(self):
        fit = self._fit(uf2([(0x08010000, vectors(0x20020000, 0x08010101), 0),
                             (0x90000000, bytes(16), 0)]))
        self.assertEqual(fit.problems, [])
        self.assertIn("also writes qspi 0x90000000..0x900fffff, not counted", fit.said)
        with self.assertRaises(I.ImageFault):
            self._fit(uf2([(0x08010000, vectors(0x20020000, 0x08010101), 0),
                           (0x60000000, bytes(16), 0)]))

    def test_a_block_not_for_main_flash_is_left_out(self):
        fit = self._fit(uf2([(0x08010000, vectors(0x20020000, 0x08010101), 0),
                             (0x00000000, bytes(16), I.UF2_NOT_MAIN_FLASH)]))
        self.assertEqual(fit.problems, [])

    def test_a_ram_segment_past_its_bank_fails(self):
        """Firmware built for a bigger variant than the part the map names."""
        image = elf32(loads=[(0x08010000, 0x08010000, vectors(0x20020000, 0x08010101), 64),
                             (0x08010040, 0x2001F000, bytes(16), 0x2000)])
        fit = self._fit(image)
        self.assertIn("runs 4096 B past the RAM bank 0x20000000..0x2001ffff", fit.problems[0])

    def test_a_segment_past_every_stated_bank_fails_or_is_errored(self):
        """What slipped through "starts in a bank and runs past it" alone (review of
        F1): a segment that starts at a bank's end (a build for the 128 KB part
        above a stated 64 KB bank) was in no bank, and the gate passed. In the Arm
        SRAM region it fails; anywhere else no stated region holds, it is errored."""
        one = I.parse_map({"flash": MAP["flash"], "app": {"start": "0x08010000",
                                                          "size": "0x70000"},
                           "ram": [{"start": "0x20000000", "size": "0x10000"}],
                           "other": MAP["other"]})
        code = (0x08010000, 0x08010000, vectors(0x20010000, 0x08010101), 64)
        fit = self._fit(elf32(loads=[code, (0x08010040, 0x20000000, bytes(16), 0x10000),
                                     (0x08010050, 0x20010000, b"", 0x8000)]), one)
        self.assertEqual(len(fit.problems), 1, fit.problems)
        self.assertIn("a segment at 0x20010000 of 32768 B lies in the Arm SRAM region and "
                      "in no stated RAM bank (0x20000000..0x2000ffff)", fit.problems[0])
        self.assertEqual(self._fit(elf32(loads=[code, (0x08010040, 0x20000000, bytes(16),
                                                       0x10000)]), one).problems, [])
        self.assertEqual(self._fit(elf32(loads=[code, (0x90000000, 0x90000000, bytes(16),
                                                       16)]), one).problems, [],
                         "a segment in an `other` range is held by it")
        for machine, vaddr in ((I.EM_ARM, 0x10000000), (243, 0x20010000)):
            with self.subTest(machine=machine, vaddr=hex(vaddr)):
                with self.assertRaises(I.ImageFault) as caught:
                    self._fit(elf32(machine=machine, loads=[
                        code, (0x08010050, vaddr, b"", 0x100)]), one)
                self.assertIn(f"a segment at {vaddr:#x}..{vaddr + 0xff:#x} lies in no "
                              f"region the map states", str(caught.exception))

    def test_a_bin_is_placed_at_the_app_start(self):
        fit = self._fit(vectors(0x20020000, 0x08010101) + bytes(0x38000 - 64))
        self.assertEqual((fit.problems, fit.measured), ([], 50.0))

    def test_formats_and_maps_it_cannot_read_are_errored(self):
        mm = self.mm
        for raw, words in ((b"\xe9" + bytes(64), "ESP app image"),
                           (elf32(machine=94, loads=[(0x08010000, 0x08010000, bytes(8), 8)]),
                            "not Arm"),
                           (elf32(loads=[(0x08010000, 0x08010000, vectors(0x20020000,
                                                                       0x08010101), 64)])[:60],
                            "past the end of the file")):
            with self.subTest(words=words):
                with self.assertRaises(I.ImageFault) as caught:
                    self._fit(raw, mm)
                self.assertIn(words, str(caught.exception))
        esp_elf = elf32(machine=94, loads=[(0x3C000020, 0x3C000020, bytes(8), 8)],
                        objects=["esp_app_desc"])
        with self.assertRaises(I.ImageFault):
            self._fit(esp_elf, I.MemoryMap(shape="address", flash=mm.flash, app=mm.app))

    def test_a_map_is_one_of_three_shapes_and_nothing_else(self):
        self.assertEqual(I.parse_map({"app": {"size": "0x100000"}}).slot, 0x100000)
        self.assertEqual(I.parse_map({"esp_partition_table": "p.csv"}).offset, 0x8000)
        for raw, words in (({"flash": MAP["flash"], "app": {**MAP["app"], "vector": 1}},
                            "sets ['vector']"),
                           ({**MAP, "rom": {}}, "sets ['rom']"),
                           ({"flash": MAP["flash"], "app": {"start": "0x09000000",
                                                           "size": 16}}, "lies outside flash"),
                           ({"app": {"size": "1M"}}, "write an int"),
                           ({"app": {"size": True}}, "not a number"),
                           ({}, "none of its three shapes"),
                           ([], "must be an object")):
            with self.subTest(words=words):
                with self.assertRaises(I.ImageFault) as caught:
                    I.parse_map(raw)
                self.assertIn(words, str(caught.exception))

    def test_lto_is_read_from_the_producer(self):
        self.assertTrue(I.lto(I.read_elf(elf32(producers=["GNU C17 13.2.0 -Os -flto"]))))
        self.assertFalse(I.lto(I.read_elf(elf32(producers=["GNU C17 13.2.0 -fno-lto"]))))
        self.assertIsNone(I.lto(I.read_elf(elf32())))


class TheEspSlotIsTheSmallestAppPartition(unittest.TestCase):
    """ESP-IDF's own gen_esp32part.py reads the CSV (vendored at v5.5.6)."""

    def setUp(self):
        with open(os.path.join(PACK, "selftest", "assets", "esp-sleep", "partitions.csv"),
                  encoding="utf-8") as fh:
            self.csv = fh.read()
        self.image = b"\xe9" + bytes(0x7b6d0 - 1)     # the baseline image's measured size

    def test_the_baseline_table(self):
        self.assertEqual(I.esp_slots(self.csv), [("factory", 0x00, 0x100000),
                                                 ("ota_0", 0x10, 0x100000),
                                                 ("ota_1", 0x11, 0x100000)])
        fit = I.judge_esp(self.image, "build/sleeper.bin", I.esp_slots(self.csv), "p.csv")
        self.assertEqual((fit.measured, fit.fits), (48.3, True),
                         "48.21 %, rounded up to 0.1 %")

    def test_the_short_slot_control(self):
        """The known-bad's image size lies in (0x20000, 0x100000), so only ota_1 is
        short and ESP-IDF only warns."""
        self.assertTrue(0x20000 < len(self.image) < 0x100000)
        short = self.csv.replace("ota_1,    app,  ota_1,   ,        1M,",
                                 "ota_1,    app,  ota_1,   ,        0x20000,")
        fit = I.judge_esp(self.image, "build/sleeper.bin", I.esp_slots(short), "p.csv")
        self.assertEqual((fit.measured, fit.fits), (385.8, False),
                         "385.71 %, rounded up to 0.1 %")
        self.assertIn("ota_1 cannot take it", fit.said)

    def test_one_byte_past_the_slot_fails(self):
        """What slipped through ``round(..., 1)`` (review of F1): an image up to
        0.05 % past its slot read 100.0 and passed ``<= 100``, while the detail said
        the slot could not take it; a true 85.04 % read 85.0 beside a ``<= 85 %``
        claim."""
        slots = I.esp_slots(self.csv)
        for extra, measured, fits in ((-1, 100.0, True), (0, 100.0, True), (1, 100.1, False),
                                      (500, 100.1, False)):
            with self.subTest(extra=extra):
                image = b"\xe9" + bytes(0x100000 + extra - 1)
                esp = I.judge_esp(image, "a.bin", slots, "p.csv")
                slot = I.judge_slot(image, "a.bin", 0x100000)
                self.assertEqual((esp.measured, esp.fits), (measured, fits))
                self.assertEqual((slot.measured, slot.fits), (measured, fits))
        self.assertEqual(I.percent_up(8504, 10000), 85.1)
        self.assertEqual(I.percent_up(8500, 10000), 85.0)
        for size in (0x20000, 0x100000, 1000003):
            for used in range(size - 3000, size + 3000, 7):
                self.assertEqual(I.percent_up(used, size) <= 100.0, used <= size, (used, size))
                self.assertGreaterEqual(I.percent_up(used, size), 100.0 * used / size)

    def test_tee_slots_are_left_out_and_a_broken_table_is_errored(self):
        tee = self.csv + "tee_0,    app,  tee_0,   ,        0x10000,\n"
        self.assertEqual([s[0] for s in I.esp_slots(tee)], ["factory", "ota_0", "ota_1"])
        with self.assertRaises(I.ImageFault):
            I.esp_slots("factory, app, factory, , nonsense,\n")
        with self.assertRaises(I.ImageFault) as caught:
            I.judge_esp(elf32(), "build/x.elf", I.esp_slots(self.csv), "p.csv")
        self.assertIn("name that .bin", str(caught.exception))


# --------------------------------------------------------------------------- #
# the wake rules
# --------------------------------------------------------------------------- #
class TheEspRuleReadsTheSymbols(unittest.TestCase):
    def _rule(self, *funcs, lto=False, reset=None):
        return W.esp_rule(frozenset(funcs), lto, reset)

    def test_an_armed_deep_sleep_passes_and_an_unarmed_one_fails(self):
        self.assertEqual(self._rule("esp_deep_sleep_start", "esp_sleep_enable_ext0_wakeup").outcome,
                         "pass")
        wake = self._rule("esp_deep_sleep_start", "esp_sleep_enable_bt_wakeup",
                          "esp_sleep_enable_gpio_switch")
        self.assertEqual(wake.outcome, "fail")
        self.assertIn("esp_sleep_enable_bt_wakeup wake light sleep only", wake.said)
        self.assertEqual(self._rule("esp_deep_sleep").outcome, "pass")
        self.assertIsNone(self._rule("esp_sleep_enable_timer_wakeup"))

    def test_the_esp_pm_timer_and_a_reset_wake_skip(self):
        self.assertEqual(self._rule("esp_deep_sleep_start", "esp_sleep_enable_timer_wakeup",
                                    "esp_pm_configure").outcome, "skip")
        self.assertEqual(self._rule("esp_deep_sleep_start", "esp_sleep_enable_timer_wakeup",
                                    "esp_sleep_enable_ext1_wakeup", "esp_pm_configure").outcome,
                         "pass")
        wake = self._rule("esp_deep_sleep_start", reset="power button on EN")
        self.assertEqual(wake.outcome, "skip")
        self.assertIn("power button on EN", wake.said)

    def test_what_the_symbols_cannot_say_is_errored(self):
        with self.assertRaises(W.WakeFault):
            self._rule("esp_deep_sleep_start", "esp_sleep_enable_lp_core_wakeup")
        with self.assertRaises(W.WakeFault):
            self._rule("esp_deep_sleep_start", "esp_sleep_enable_ext0_wakeup", lto=True)
        with self.assertRaises(W.WakeFault):
            self._rule("esp_deep_sleep_start", lto=None)
        self.assertEqual(self._rule("esp_deep_sleep_start", "esp_sleep_enable_ext0_wakeup",
                                    lto=None).outcome, "pass")


#: A trimmed zephyr.dts: only the kscan, chosen, physical-layout and soft-off nodes,
#: with no flash partition nodes. Every name is this test's own.
DTS = """/dts-v1/;
/ {
	chosen {
		zmk,kscan = &kscan0;
	};
	kscan0: kscan {
		compatible = "zmk,kscan-gpio-matrix";
		%(wake)s
	};
	layout0: physical_layout0 {
		compatible = "zmk,physical-layout";
		kscan = <&kscan0>;
	};
	btn0: button {
		compatible = "gpio-keys";
		%(btn)s
	};
	%(soft)s
};
"""
SOFT = """wakeup_list {
		compatible = "zmk,soft-off-wakeup-sources";
		wakeup-sources = <&btn0>;
	};"""


def _dts(wake=True, btn=True, soft=True, layout=True, compatible=None) -> str:
    text = DTS % {"wake": "wakeup-source;" if wake else "", "btn": "wakeup-source;" if btn else "",
                  "soft": SOFT if soft else ""}
    if not layout:
        text = text.replace('"zmk,physical-layout"', '"other"')
    if compatible:
        text = text.replace('"zmk,kscan-gpio-matrix"', f'"{compatible}"')
    return text


SLEEP = "CONFIG_ZMK_SLEEP=y\nCONFIG_ZMK_KEYBOARD_NAME=\"k\"\n"
SOFT_OFF = "CONFIG_ZMK_PM_SOFT_OFF=y\n"


class TheZmkRuleReadsTheDevicetree(unittest.TestCase):
    def test_deep_sleep_needs_a_wakeup_source_on_the_scanned_kscan(self):
        self.assertEqual(W.zmk_rule(SLEEP, _dts()).outcome, "pass")
        wake = W.zmk_rule(SLEEP, _dts(wake=False))
        self.assertEqual(wake.outcome, "fail")
        self.assertIn("no wakeup-source on /kscan (kscan)", wake.said)
        self.assertEqual(W.zmk_rule(SLEEP, _dts(wake=False, layout=False)).outcome, "fail",
                         "the chosen kscan, with no physical layout")

    def test_soft_off_needs_it_on_each_listed_device_and_none_listed_skips(self):
        self.assertEqual(W.zmk_rule(SOFT_OFF, _dts()).outcome, "pass")
        wake = W.zmk_rule(SOFT_OFF, _dts(btn=False))
        self.assertEqual(wake.outcome, "fail")
        self.assertIn("/button (soft-off wake)", wake.said)
        self.assertEqual(W.zmk_rule(SOFT_OFF, _dts(soft=False)).outcome, "skip",
                         "the reset button is ZMK's documented soft-off wake")
        self.assertEqual(W.zmk_rule(SLEEP + SOFT_OFF, _dts(soft=False)).outcome, "pass")

    def test_what_it_cannot_follow_is_errored_and_no_sleep_skips(self):
        with self.assertRaises(W.WakeFault):
            W.zmk_rule(SLEEP, _dts(compatible="zmk,kscan-composite"))
        with self.assertRaises(W.WakeFault):
            W.zmk_rule(SLEEP, None)
        self.assertEqual(W.zmk_rule("CONFIG_ZMK_KEYBOARD_NAME=\"k\"\n", _dts()).outcome, "skip")
        self.assertIsNone(W.zmk_rule("CONFIG_BOARD=\"x\"\n", _dts()))

    def test_every_other_stack_skips_by_name(self):
        for funcs, config, words in ((["HAL_PWR_EnterSTANDBYMode"], None, "STM32 HAL or LL"),
                                     (["sleep_goto_dormant_until_pin"], None, "Pico SDK"),
                                     (["sd_power_system_off"], None, "nRF5 SDK"),
                                     (["matrix_scan_kb"], None, "QMK"),
                                     ([], "CONFIG_BOARD=\"x\"\nCONFIG_POWEROFF=y\n",
                                      "sys_poweroff"),
                                     ([], None, "nothing to wake from")):
            with self.subTest(words=words):
                self.assertIn(words, W.by_name(frozenset(funcs), config))


# --------------------------------------------------------------------------- #
# the gates, on a recorded replay
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAS_YAML, NO_YAML)
class TheGatesReadOneReplay(unittest.TestCase):
    """The three tier-1 gates on a replay handed to ``ctx.load_file``: what each
    reports, and what each refuses."""

    @classmethod
    def setUpClass(cls):
        cls.registry = _registry()

    def _run(self, gid: str, files: dict, params: dict | None = None, result=None):
        replay = result or _recorded("act_esp_short_ota_slot.jsonl", files)
        real = gates_mod.GateContext.load_file

        def load_file(self_, path, loader=None):
            if isinstance(loader, functools.partial):
                return replay
            return real(self_, path, loader)

        base = {"firmware_ci_workflow": os.path.join(PACK, "selftest", "assets", "esp-sleep",
                                                     "github", "workflows", "build.yml"),
                "firmware_ci_job": "build", "firmware_image": "build/sleeper.bin",
                "firmware_elf": "build/sleeper.elf",
                "app_memory_map": {"esp_partition_table": os.path.join(
                    PACK, "selftest", "assets", "esp-sleep", "partitions.csv")}}
        base.update(params or {})
        tmp = tempfile.mkdtemp(prefix="fw-gate-")
        self.addCleanup(shutil.rmtree, tmp, True)
        spec, fn = self.registry.get(gid)
        ctx = gates_mod.GateContext(root=PACK, ledger=Ledger(), params=base,
                                    out_dir=os.path.join(tmp, "out"), tier=1,
                                    log=lambda _m: None, extra={})
        with mock.patch.object(gates_mod.GateContext, "load_file", load_file):
            return fn(ctx)

    def _files(self, *funcs):
        return {"build/sleeper.bin": b"\xe9" + bytes(0x7b6d0 - 1),
                "build/sleeper.elf": elf32(machine=94, funcs=funcs, objects=["esp_app_desc"],
                                           producers=["GNU C17 14.2.0 -Og"])}

    def test_flash_use_is_a_percentage_against_the_regions_own_bound(self):
        v = self._run("fw.image_fits", self._files())
        self.assertEqual((v.passed, v.measured, v.limit, v.comparator, v.units),
                         (True, 48.3, 100.0, "<=", "%"))

    def test_an_image_past_its_slot_by_a_few_bytes_fails(self):
        """Review of F1: 0x100000 + 500 B read 100.0 % and passed."""
        files = self._files()
        for size, passed, measured in ((0x100000, True, 100.0), (0x100000 + 500, False, 100.1)):
            for mm in ({"app": {"size": "0x100000"}}, None):
                with self.subTest(size=size, map=mm):
                    files["build/sleeper.bin"] = b"\xe9" + bytes(size - 1)
                    v = self._run("fw.image_fits", files,
                                  {"app_memory_map": mm} if mm else None)
                    self.assertEqual((v.passed, v.measured), (passed, measured), v.detail)

    def test_the_wake_rule_reads_the_elf_the_job_wrote(self):
        self.assertTrue(self._run("fw.sleep_wake", self._files(
            "esp_deep_sleep_start", "esp_sleep_enable_ext0_wakeup")).passed)
        v = self._run("fw.sleep_wake", self._files("esp_deep_sleep_start"))
        self.assertFalse(v.passed)
        self.assertIn("build/sleeper.elf: esp_deep_sleep_start is linked", v.detail)

    def test_a_matrix_job_is_errored_in_the_readers(self):
        wf = _write(tempfile.mkdtemp(prefix="fw-mx-"), ".github/workflows/b.yml",
                    _workflow(job_extra="strategy:\n  matrix:\n    side: [l, r]\n"))
        self.addCleanup(shutil.rmtree, os.path.dirname(os.path.dirname(os.path.dirname(wf))),
                        True)
        v = self._run("fw.image_fits", self._files(), {"firmware_ci_workflow": wf})
        self.assertIn("has a matrix", v.error)

    def test_missing_inputs_skip(self):
        for gid, key in (("fw.image_fits", "app_memory_map"), ("fw.image_fits", "firmware_image"),
                         ("fw.sleep_wake", "firmware_elf"), ("fw.build", "firmware_ci_workflow")):
            with self.subTest(gid=gid, key=key):
                v = self._run(gid, self._files(), {key: None})
                self.assertTrue(v.skipped, v)

    def test_a_file_the_job_did_not_write_is_errored(self):
        v = self._run("fw.image_fits", self._files(), {"firmware_image": "build/*.uf2"})
        self.assertIn("is not what job build builds", v.error)
        v = self._run("fw.sleep_wake", self._files(), {"firmware_elf": "build/*.axf"})
        self.assertIn("matches no file the job wrote", v.error)


# --------------------------------------------------------------------------- #
# the real replay
# --------------------------------------------------------------------------- #
class TheRealReplay(unittest.TestCase):
    """The pack's own controls through act 0.2.97, docker and the pinned ESP-IDF
    image, where all three are present: every gate passes the baseline (one shared
    replay in a sweep; here one per gate) and fails its own known-bad copy.
    Skipped, saying not walked, elsewhere."""

    def test_the_controls_on_a_real_replay(self):
        registry = _registry()
        spec, _fn = registry.get("fw.build")
        available, why = gates_mod.availability(spec)
        if not available:
            self.skipTest(f"not walked: {why}")
        try:
            R.act_version()
        except (R.WrongAct, R.ToolFault) as exc:
            self.skipTest(f"not walked: {exc}")
        image = subprocess.run(["docker", "image", "inspect", IDF_IMAGE], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        if image.returncode != 0:
            self.skipTest(f"not walked: {IDF_IMAGE} is not pulled")
        out = tempfile.mkdtemp(prefix="fw-real-")
        self.addCleanup(shutil.rmtree, out, True)
        for gid in GATES:
            spec, fn = registry.get(gid)
            with self.subTest(gate=gid):
                good = gates_mod.run_gate(spec, fn, packs_mod.baseline_context(PACK, out_dir=out))
                self.assertEqual(good.outcome, "pass", good.render())
                host = gates_mod.GateContext(root=out, out_dir=out, extra={"pack_dir": PACK})
                bad = gates_mod.run_gate(spec, fn, gates_mod.run_fixture(
                    spec, fn, host, trace=None, out_dir=out))
                self.assertEqual(bad.outcome, "fail", bad.render())
                if gid == "fw.image_fits":
                    self.assertEqual((good.measured, bad.measured), (48.3, 385.8),
                                     "48.21 % and 385.71 %, rounded up to 0.1 %")


if __name__ == "__main__":
    unittest.main()
