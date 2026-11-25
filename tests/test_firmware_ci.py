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
  trimmed devicetrees;
- the transmit rule (``_tx``) on configs recorded from real Kconfig runs
  (``tests/fw_configs/``, ``FW_CONFIGS``), its setter scan on sources written here,
  and the gate on a replay handed to it, with the sdkconfig an ESP-IDF build
  record names read back through the stand-in.

The real replays run in ``TheRealReplay`` where act 0.2.97, docker and the pinned
ESP-IDF image are present, and are skipped, saying not walked, elsewhere.

Run:  PYTHONPATH=src python3 -m unittest tests.test_firmware_ci -v
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import struct
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest import mock

import _env
from nopekit import claims as claims_mod
from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit import verdicts as verdicts_mod
from nopekit.modelio import load_path
from nopekit.models import Ledger

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(REPO, "packs", "firmware-ci")
LOGS = os.path.join(REPO, "tests", "fw_logs")
STANDIN = os.path.join(REPO, "tests", "_act_standin.py")
#: Configs written by the stacks' own Kconfig, cut to the lines the transmit rule
#: can read (``CONFIG_BT*``, the board or target, the SoC series, ``CONFIG_MPSL*``):
#: Kconfig writes one symbol a line, so a cut keeps each line as the tool wrote it.
#: Recorded on 2026-11-24:
#:
#: - ``zephyr_*.config``: Zephyr 3.5.0 (ZMK v0.3.0's west workspace) in
#:   zmkfirmware/zmk-build-arm, ``west build -p always -b <board> -d <dir>
#:   zephyr/samples/bluetooth/<sample> --cmake-only``, offline: peripheral_hr and
#:   hci_pwr_ctrl (which turns on dynamic control) on nrf52dk_nrf52832, and
#:   peripheral_hr on nrf5340dk_nrf5340_cpuapp (a host whose controller is the
#:   network core's).
#: - ``esp32*.sdkconfig``: espressif/idf v5.5.6 (the baseline's image, by digest),
#:   ``idf.py reconfigure`` on the pack's baseline app: as committed (+6 dBm), with
#:   the TX level line removed (ESP-IDF's default, +9 dBm), and with its target set
#:   to esp32c6 (the BT_LE family, which this pack does not read yet).
#:
#: NCS's SoftDevice Controller has no recorded config: no NCS workspace was at
#: hand, and its rows (``NCS_SDC``) are written from sdk-nrf v3.4.2's Kconfig and
#: radio_nrf5_txp.h (``_tx.PROFILES``' comment), as test lines, not a recording.
FW_CONFIGS = os.path.join(REPO, "tests", "fw_configs")
C = load_path(os.path.join(PACK, "gates", "_ci.py"))
R = load_path(os.path.join(PACK, "gates", "_replay.py"))
I = load_path(os.path.join(PACK, "gates", "_image.py"))
W = load_path(os.path.join(PACK, "gates", "_wake.py"))
T = load_path(os.path.join(PACK, "gates", "_tx.py"))
HAS_YAML = importlib.util.find_spec("yaml") is not None
HAS_GIT = shutil.which("git") is not None
NO_YAML = "PyYAML is not importable here: the guard reads workflows with it, so its rules are not walked"

GATES = ("fw.ci_job", "fw.build", "fw.image_fits", "fw.sleep_wake", "fw.tx_power")
#: The copy path the committed act excerpts were recorded under (tests/fw_logs/).
RECORDED_COPY = "/tmp/nopekit-fw-k2c9w0ra/repo"
IDF_IMAGE = ("espressif/idf@sha256:"
             "bf72fe7a15c22849a9a94147301165a545fb8905d97c176cf748e3e4614cac84")


def _config(name: str) -> str:
    with open(os.path.join(FW_CONFIGS, name), encoding="utf-8") as fh:
        return fh.read()


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
            proc = _env.run(["bash", "-e", "-c", "false && echo x; echo after"],
                            cwd=tempfile.gettempdir())
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
            proc = _env.run(["bash", "-e", "-c", "false && echo built; echo done"],
                            cwd=tempfile.gettempdir())
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


@unittest.skipUnless(HAS_YAML, NO_YAML)
class TheGuardSaysWhatItRead(unittest.TestCase):
    """``fw.ci_job``'s pass rests on the build step's own lines. What slipped
    through "no masking" (holdout review, a console's upstream workflow): the
    build ran in ``python3 <script>.py build``, the script ran docker through
    ``bash -c`` with no status check, a failed build exited 0, and the pass
    claimed more than the guard read."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="fw-ci-said-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.spec, self.fn = _registry().get("fw.ci_job")

    def _guard(self, steps: str, **params):
        path = _write(self.repo, ".github/workflows/build.yml",
                      _workflow(steps=steps, container=None))
        ctx = gates_mod.GateContext(root=self.repo, ledger=Ledger(),
                                    params={"firmware_ci_workflow": path, **params},
                                    out_dir=os.path.join(self.repo, "out"), tier=0,
                                    log=lambda _m: None, extra={})
        return self.fn(ctx)

    def test_a_script_the_step_runs_is_named_as_not_read(self):
        v = self._guard("      - name: Build Script\n        run: python3 tools/fw.py build\n",
                        firmware_ci_step="Build Script")
        self.assertTrue(v.passed, v.render())
        self.assertTrue(v.detail.endswith("; no masking in the step's own lines; tools/fw.py "
                                          "is not read"), v.detail)
        v = self._guard("      - name: Build\n        run: ./ci/build.sh && bash ci/pack.sh\n",
                        firmware_ci_step="Build")
        self.assertIn("./ci/build.sh, ci/pack.sh are not read", v.detail)

    def test_a_command_the_pack_knows_says_only_the_steps_lines(self):
        v = self._guard("      - name: Build\n        run: west build -b x\n")
        self.assertTrue(v.detail.endswith("; no masking in the step's own lines"), v.detail)
        v = self._guard("      - name: Build\n        run: fwtool --all\n",
                        firmware_ci_step="Build")
        self.assertIn("what its command runs is not read", v.detail)

    def test_an_unnamed_step_is_named_by_its_run_line(self):
        """Holdout review (a handheld): the handle act logs for an unnamed step,
        its run's first line, worked and no document said so."""
        v = self._guard("      - run: ./build.sh all\n", firmware_ci_step="./build.sh all")
        self.assertTrue(v.passed, v.render())
        v = self._guard("      - run: ./build.sh all\n", firmware_ci_step="nope")
        self.assertIn("or, with neither, its run's first line", v.error)


class EveryTemplateSaysWhetherItWasReplayed(unittest.TestCase):
    """Holdout review (a watch): the arduino-cli template piped ``install.sh`` with
    ``BINDIR=$HOME/bin`` and never made the folder, so it failed in the pack's own
    runner image, and nothing had ever replayed it. Each template now says whether
    this pack replayed it, and the arduino-cli one makes its folder first."""

    def setUp(self):
        with open(os.path.join(PACK, "references", "ci-templates.md"), encoding="utf-8") as fh:
            self.text = fh.read()
        self.sections = {m.group(1): m.group(2) for m in re.finditer(
            r"^## (.+?)\n(.*?)(?=^## |\Z)", self.text, re.M | re.S) if "```yaml" in m.group(2)}

    def test_each_template_is_marked(self):
        self.assertGreaterEqual(len(self.sections), 7)
        for name, body in self.sections.items():
            with self.subTest(template=name):
                head = body.split("```yaml", 1)[0]
                self.assertRegex(head, r"\*\*(?:Replayed|Not replayed)\*\*",
                                 "say whether this pack replayed it")

    def test_the_arduino_template_makes_its_bin_folder_before_installing(self):
        block = self.sections["arduino-cli"].split("```yaml", 1)[1].split("```", 1)[0]
        made = block.find('mkdir -p "$HOME/bin"')
        self.assertGreater(made, 0, block)
        self.assertLess(made, block.find("install.sh"), "the folder comes before install.sh")
        self.assertNotIn("/master/install.sh", block, "install.sh at a pinned tag")


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


def _is_replay(loader) -> bool:
    """Whether ``loader`` is the replay's (``_replay.loader``: a method of
    ``_replay_named`` bound to the replay's name). Read by name: the gate module
    and this file may each hold their own load of ``_replay.py``."""
    return getattr(getattr(loader, "__func__", None), "__name__", "") == "_replay_named"


def _manifest(files: dict) -> list:
    """``images`` as the read-back lists them: name, size and sha256 of each
    image in ``files``."""
    return [{"name": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for rel, data in sorted(files.items()) if rel.endswith(R.OUTPUT_SUFFIXES)]


def _recorded(name: str, files: dict | None = None) -> dict:
    """A replay's result from a committed act excerpt, scrubbed as the loader
    scrubs it."""
    entries, act = R.parse(_log(name), functools.partial(
        R._scrub, copy=RECORDED_COPY, tmp=os.path.dirname(RECORDED_COPY)))
    return {"workflow": "github/workflows/build.yml", "job": "build", "matrix": {}, "rc": 0,
            "timeout": False, "timeout_s": float(R.TIMEOUT_S), "entries": entries, "act": act,
            "files": files or {}, "configs": {}, "images": _manifest(files or {}),
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
    return {"workflow": "w", "job": "build", "matrix": {}, "rc": 0, "timeout": False,
            "timeout_s": float(R.TIMEOUT_S), "entries": [entry], "act": [],
            "files": files or {}, "configs": {}, "images": _manifest(files or {}),
            "oversize": 0, "toolchain": {}, "cleanup": ""}


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

    def test_a_failed_step_is_quoted_by_its_own_cause(self):
        """Holdout review (an NCS watch): a west configure failure ends with
        west's ``FATAL ERROR``, and its cause is the ``CMake Error`` line above
        it; the detail quoted the last error-like line. A failed step before the
        build was quoted by act's own last line, not its output."""
        log = ("-- west build: generating a build system\n"
               "CMake Error at CMakeLists.txt:7 (find_package):\n"
               "  Could not find a package configuration file provided by \"Zephyr\"\n"
               "-- Configuring incomplete, errors occurred!\n"
               "FATAL ERROR: command exited with status 1: cmake -B build\n")
        self._raises(_step_result(log), "on no line this pack knows as a design fault: "
                                        "CMake Error at CMakeLists.txt:7 (find_package):")
        result = _step_result("", "success", before="failure")
        result["entries"][0]["steps"][1]["log"] = "fetching\nerror: pathspec 'v9' did not match\n"
        self._raises(result, "(error: pathspec 'v9' did not match)")

    def test_an_install_over_empty_apt_lists_names_the_difference(self):
        """Holdout review (two projects): ``sudo apt-get install`` with no update
        works on GitHub, whose hosted image ships populated apt lists, and fails in
        act's runner image, which ships them empty (measured:
        act_apt_empty_lists.jsonl). The reason named no cause."""
        job = _job(index=1, name="Build", command="make", image="", runs_on="ubuntu-24.04",
                   ids=("0", "1"), runs=("sudo apt-get install -y gcc-arm-none-eabi make",
                                         "make -j4"))
        with self.assertRaises(R.ToolFault) as caught:
            R.judge(_recorded("act_apt_empty_lists.jsonl"), job)
        said = str(caught.exception)
        self.assertIn("step 'Toolchain' before the build failed on empty apt lists", said)
        self.assertIn("run `sudo apt-get update` first): E: Unable to locate package "
                      "gcc-arm-none-eabi", said)
        for other in (dataclasses.replace(job, runs=("sudo apt-get update && sudo apt-get "
                                                     "install -y gcc-arm-none-eabi", "make")),
                      dataclasses.replace(job, image="debian:bookworm")):
            with self.subTest(job=other.runs[0][:24], image=other.image):
                with self.assertRaises(R.ToolFault) as caught:
                    R.judge(_recorded("act_apt_empty_lists.jsonl"), other)
                self.assertIn("cannot tell a manifest error from a network fault "
                              "(E: Unable to locate package", str(caught.exception))

    def test_a_script_that_ran_nothing_here_is_errored_not_judged(self):
        """Holdout review (a console's upstream workflow): the build step ran a
        Python script that runs docker with no status check, so it read success;
        the replay mounts no docker socket, the script built nothing, and the
        missing image would have read as a design FAIL. Measured:
        act_script_docker.jsonl."""
        job = _job(index=0, name="Build", command="the named step", image="",
                   runs_on="ubuntu-24.04")
        result = _recorded("act_script_docker.jsonl")
        self.assertEqual(R.main_step(result["entries"][0], 0)["result"], "success")
        for glob in (None, "build/fw.uf2"):
            with self.subTest(firmware_image=glob):
                with self.assertRaises(R.ToolFault) as caught:
                    R.judge(result, job, glob)
                self.assertIn("could not run in the replay (no docker daemon in jobs, or a "
                              "file the copy lacks): ERROR: failed to connect to the docker "
                              "API", str(caught.exception))
        step = R.main_step(result["entries"][0], 0)
        step["log"] = step["log"].replace("ERROR: failed to connect", "connected")
        with self.assertRaises(R.ToolFault) as caught:
            R.judge(result, job)
        self.assertIn("python3: can't open file", str(caught.exception))

    def test_one_matrix_entry_or_none(self):
        """``firmware_ci_matrix`` names one entry: act's ``--matrix`` selects every
        entry that agrees on the keys given (measured with act 0.2.97: a key left
        out runs each of its values), so more than one is errored."""
        result = _step_result("", "success", files=BIN)
        result["matrix"] = {"side": "l"}
        result["entries"][0]["matrix"] = {"side": "l"}
        passed, detail = R.judge(result, _job(matrix=True))
        self.assertTrue(passed, detail)
        self.assertIn("matrix entry {side: l}", detail)
        result["entries"].append(dict(result["entries"][0], matrix={"side": "l", "n": 2}))
        self._raises(result, "firmware_ci_matrix {side: l} selects 2 entries")
        result["entries"] = []
        self._raises(result, "firmware_ci_matrix {side: l} may name no entry")

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
    """``git`` through ``_env.git``: no global config and a synthetic identity.
    What slipped through F1: a bare ``subprocess.run`` here inherited this machine's
    HOME and agent variables (test_meta.NoSubprocessOutsideRun)."""
    return _env.git(list(args), cwd=cwd, identity=True)


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
    """A bin directory with ``act`` and ``docker`` stand-ins first on PATH, a HOME
    of the test's own, and the replay lock's root in the test's own directory, so
    the replay's lock and caches land nowhere real. (The real lock is every
    process's of this user, whatever HOME says: a stand-in act starts no
    container, and a test of it never waits on a person's replay.)"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fw-standin-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        root = mock.patch.object(R, "LOCK_ROOT", self.tmp)
        root.start()
        self.addCleanup(root.stop)
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

    def replay(self, path: str, keep: tuple = ("build/sleeper.bin", "build/sleeper.elf"),
               **kw) -> dict:
        with mock.patch.dict(os.environ, self.env):
            return R.replay(path, job="build", keep=keep, **kw)


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
                     "--no-cache-server", "--rm"):
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
        result = self.replay(path, keep=("dist/app.bin",))
        self.assertEqual(sorted(result["files"]), ["dist/app.bin"])
        self.assertEqual(result["files"]["dist/app.bin"], same)
        self.assertTrue(R.judge(result, _job(), "dist/app.bin")[0])

    def test_the_sdkconfig_a_build_record_names_is_read_back(self):
        """What slipped through "the files the job wrote" alone (general-packs F2):
        ESP-IDF leaves a tracked sdkconfig as it was when its configure step
        resolves it unchanged (measured with espressif/idf v5.5.6), so the job
        "wrote" none and the transmit rule had no config. The build record names it,
        and it is read back; a tracked sdkconfig no record names, and a name
        outside the copy, are not."""
        path = self.app()
        app = os.path.dirname(os.path.dirname(os.path.dirname(path)))
        for rel in ("sdkconfig", "other/sdkconfig"):
            _write(app, rel, "CONFIG_BT_ENABLED=y\n")
        outside = os.path.join(self.tmp, "outside", "sdkconfig")
        _write(self.tmp, "outside/sdkconfig", "CONFIG_BT_ENABLED=y\n")
        record = json.dumps({"config_file": "{copy}/sdkconfig", "build_dir": "{copy}/build"})
        self.mode({"log": os.path.join(LOGS, "act_esp_short_ota_slot.jsonl"),
                   "copy_in_log": RECORDED_COPY, "sleep": 0.05,
                   "write": {"build/sleeper.bin": {"head": "e9", "size": 64},
                             "build/project_description.json": {"text": record},
                             "build/bootloader/project_description.json": {
                                 "text": json.dumps({"config_file": outside})}}})
        result = self.replay(path)
        self.assertEqual(sorted(result["files"]), ["build/sleeper.bin"],
                         "a build record is read, not handed on as a file the job wrote")
        self.assertEqual(result["configs"], {"sdkconfig": b"CONFIG_BT_ENABLED=y\n"})
        self.assertEqual(sorted(T.configs(result)), ["sdkconfig"])
        self.mode({"log": os.path.join(LOGS, "act_esp_short_ota_slot.jsonl"),
                   "copy_in_log": RECORDED_COPY,
                   "write": {"build/sleeper.bin": {"head": "e9", "size": 64},
                             "sdkconfig": {"text": "CONFIG_BT_ENABLED=y\n# rewritten\n"},
                             "build/project_description.json": {"text": record}}})
        result = self.replay(path)
        self.assertEqual((sorted(result["files"]), result["configs"]),
                         (["build/sleeper.bin", "sdkconfig"], {}),
                         "a config the job rewrote is a file it wrote, read once")

    def test_a_timeout_stops_the_job_containers(self):
        self.mode({"sleep": 5})
        with mock.patch.object(R, "TIMEOUT_S", 1):
            result = self.replay(self.app())
        self.assertTrue(result["timeout"])
        docker = [c["argv"] for c in self.calls() if c["tool"] == "docker"]
        # `-aq`: a stopped job container is listed too (review of the holdout
        # runs: `-q` alone, on a timeout alone, left a failed job's container).
        self.assertTrue(any(a[:2] == ["ps", "-aq"] and a[3].startswith("volume=")
                            for a in docker), docker)
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


#: The workflow ``tests/fw_logs/act_apt_empty_lists.jsonl`` was recorded from (act
#: 0.2.97, the pinned act-24.04 runner image, the replay's own flags): an install
#: with no ``apt-get update`` before it.
APT_WORKFLOW = """name: b
on: [push]
jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - name: Toolchain
        run: sudo apt-get install -y gcc-arm-none-eabi make
      - name: Build
        run: make -j4
"""


@unittest.skipUnless(HAS_GIT and HAS_YAML, "git or PyYAML is missing: the gates do not run")
class OneReplayPerSweep(_StandIn):
    """The four tier-1 gates of one sweep share one replay. What slipped through
    (holdout review, a watch's check: four gates at 250-404 s each): the replay's
    lock asked whether ``/tmp`` exists, the sweep's memo signed ``/tmp``, and any
    file made there between two gates (their own controls make one each) moved
    the signature, so every reader replayed again."""

    def test_the_readers_share_the_builds_replay_while_tmp_moves(self):
        self.mode({"log": os.path.join(LOGS, "act_esp_short_ota_slot.jsonl"),
                   "copy_in_log": RECORDED_COPY,
                   "write": {"build/sleeper.bin": {"size": 0x7b6d0, "head": "e9"},
                             "build/sleeper.elf": {"head": "7f454c46", "size": 64}}})
        registry = _registry()
        ctx = dataclasses.replace(
            packs_mod.baseline_context(PACK, out_dir=os.path.join(self.tmp, "out")),
            memo=verdicts_mod.SweepMemo(), tier=1)
        outcomes = {}
        with mock.patch.dict(os.environ, self.env):
            for gid in GATES[1:]:
                # What every process on the machine does to the temp root between
                # two gates (the lock's root is the test's own, _StandIn).
                with open(os.path.join(self.tmp, f"made-before-{gid}"), "w"):
                    pass
                spec, fn = registry.get(gid)
                outcomes[gid] = gates_mod.run_gate(spec, fn, ctx,
                                                   trace=verdicts_mod.GateTrace()).outcome
        pushes = [c for c in self.calls() if c["tool"] == "act" and c["argv"][:1] == ["push"]]
        self.assertEqual(len(pushes), 1, f"one replay for {outcomes}")
        self.assertEqual(outcomes["fw.build"], "pass", outcomes)


@unittest.skipUnless(HAS_GIT and HAS_YAML, "git or PyYAML is missing: the gates do not run")
class TheReplayLeavesNothing(_StandIn):
    """Holdout review (three projects): act keeps a failed job's container running
    unless it is given ``--rm``, and the replay stopped containers on a timeout
    only, so each failed replay left one bound to a removed copy."""

    def _failed(self, **extra):
        self.mode({"log": os.path.join(LOGS, "act_apt_empty_lists.jsonl"),
                   "copy_in_log": RECORDED_COPY, "exit": 1, **extra})
        repo = os.path.join(self.tmp, "fw")
        return self.replay(_write(repo, ".github/workflows/build.yml", APT_WORKFLOW))

    def test_a_failed_replay_removes_its_job_containers(self):
        result = self._failed(containers=["c0ffee"])
        act = [c["argv"] for c in self.calls() if c["tool"] == "act"]
        self.assertIn("--rm", act[-1])
        docker = [c["argv"] for c in self.calls() if c["tool"] == "docker"]
        self.assertIn(["rm", "-f", "c0ffee"], docker)
        rm_at = docker.index(["rm", "-f", "c0ffee"])
        chown_at = next(i for i, a in enumerate(docker) if a[:1] == ["run"])
        self.assertLess(rm_at, chown_at, "the job's container goes before the copy does")
        self.assertEqual(result["cleanup"], "")

    def test_a_container_that_stays_is_named(self):
        result = self._failed(containers=["c0ffee"], rm_fails=True)
        self.assertIn("job container c0ffee could not be removed: docker rm -f c0ffee",
                      result["cleanup"])


@unittest.skipUnless(HAS_GIT, "git is not on PATH: the replay's copy is not walked")
class TheReadBackKeepsWhatTheGatesRead(_StandIn):
    """Holdout review (an NCS watch): the read-back took the bytes of every image
    the job wrote, against a cap sized on the baseline, so a sysbuild's 284 MB of
    images read errored. Now only the images the gates name come back whole; the
    rest are listed by name, size and sha256."""

    def test_an_image_no_gate_names_is_listed_not_read(self):
        self.mode({"log": os.path.join(LOGS, "act_esp_short_ota_slot.jsonl"),
                   "copy_in_log": RECORDED_COPY,
                   "write": {"build/sleeper.bin": {"size": 64, "head": "e9"},
                             "build/sleeper.elf": {"head": "7f454c46", "size": 64},
                             "build/zephyr/zephyr_pre0.elf": {"head": "7f454c46",
                                                              "size": 4096}}})
        app = self.app()
        with mock.patch.object(R, "OUTPUT_CAP", 1024):
            result = self.replay(app)
        self.assertEqual(result["oversize"], 0)
        self.assertEqual(sorted(result["files"]), ["build/sleeper.bin", "build/sleeper.elf"])
        pre = b"\x7fELF" + bytes(4092)
        self.assertEqual([r for r in result["images"] if r["name"].endswith("pre0.elf")],
                         [{"name": "build/zephyr/zephyr_pre0.elf", "size": 4096,
                           "sha256": hashlib.sha256(pre).hexdigest()}])
        self.assertEqual(R.image_names(result), ["build/sleeper.bin", "build/sleeper.elf",
                                                 "build/zephyr/zephyr_pre0.elf"])
        with mock.patch.object(R, "OUTPUT_CAP", 100):
            result = self.replay(app)
        self.assertEqual(result["oversize"], 128, "the named images past the cap still say so")


@unittest.skipUnless(HAS_GIT and HAS_YAML, "git or PyYAML is missing: the gate does not run")
class AnErroredReplayKeepsItsLog(_StandIn):
    """Holdout review (three projects): an errored ``fw.build`` kept no log, and
    its reason quoted act's ``Job failed``; each person replayed the failure by
    hand to find its cause. Now the log is evidence on every outcome the replay
    reached, and the reason quotes the step's own cause."""

    def _build(self, params: dict):
        spec, fn = _registry().get("fw.build")
        ctx = gates_mod.GateContext(root=self.tmp, ledger=Ledger(), params=params,
                                    out_dir=os.path.join(self.tmp, "out"), tier=1,
                                    log=lambda _m: None, extra={})
        with mock.patch.dict(os.environ, self.env):
            return gates_mod.run_gate(spec, fn, ctx)

    def test_the_failing_steps_log_is_evidence(self):
        self.mode({"log": os.path.join(LOGS, "act_apt_empty_lists.jsonl"),
                   "copy_in_log": RECORDED_COPY, "exit": 1})
        wf = _write(os.path.join(self.tmp, "fw"), ".github/workflows/build.yml", APT_WORKFLOW)
        verdict = self._build({"firmware_ci_workflow": wf})
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("on empty apt lists", verdict.error)
        logs = [p for p in verdict.evidence if os.path.basename(p) == "log.txt"]
        self.assertEqual(len(logs), 1, verdict.evidence)
        with open(logs[0], encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("== job build, step 'Toolchain': failure\n", text)
        self.assertIn("E: Unable to locate package gcc-arm-none-eabi\n", text)
        self.assertIn("-- act: exitcode '100': failure", text)
        self.assertNotIn(self.tmp, text, "the log names no temporary directory")

    def test_the_timeout_is_the_projects_to_set(self):
        """Holdout review (a handheld): a job that clones and installs ESP-IDF per
        matrix entry cannot finish in a fixed 1800 s, and no key could say so."""
        self.mode({"sleep": 5})
        verdict = self._build({"firmware_ci_workflow": self.app(),
                               "firmware_ci_timeout_s": 1})
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("act did not finish in 1 s (firmware_ci_timeout_s)", verdict.error)

    def test_a_matrix_entry_goes_to_act(self):
        self.mode({"log": os.path.join(LOGS, "act_esp_short_ota_slot.jsonl"),
                   "copy_in_log": RECORDED_COPY})
        self.replay(self.app(), matrix={"device": "beta", "n": 2, "lto": True})
        argv = [c["argv"] for c in self.calls() if c["tool"] == "act"][-1]
        pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "--matrix"]
        self.assertEqual(pairs, ["device:beta", "lto:true", "n:2"])


#: A replay's critical section with no replay in it: the lock held for a second
#: under ``<argv[2]>``, its entry and exit appended to ``argv[3]`` with this
#: process's HOME.
_LOCK_CHILD = r"""
import os, sys, time
from nopekit.modelio import load_path
R = load_path(sys.argv[1])
R.LOCK_ROOT = sys.argv[2]
with R._one_replay_at_a_time():
    for word, wait in (("enter", 1.0), ("exit", 0.0)):
        with open(sys.argv[3], "a", encoding="utf-8") as fh:
            fh.write(f"{word} {os.environ['HOME']}\n")
        time.sleep(wait)
"""


@unittest.skipUnless(hasattr(os, "getuid") and importlib.util.find_spec("fcntl"),
                     "not a POSIX host: the replays take no lock")
class TheReplayLockIsTheUsers(unittest.TestCase):
    """One replay at a time for every process of this user on this machine,
    whatever HOME says. (What slipped through F1 and F2: the lock lived under
    ``$HOME``, so a test's temporary home beside a person's check took a second
    lock, and act removed one replay's container mid-build: "RWLayer ...
    unexpectedly nil".)"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fw-lock-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_two_homes_queue_on_one_lock(self):
        log = os.path.join(self.tmp, "log")
        homes = [os.path.join(self.tmp, "home-a"), os.path.join(self.tmp, "home-b")]
        done = {}

        def child(home: str) -> None:
            os.makedirs(home)
            done[home] = _env.run([sys.executable, "-c", _LOCK_CHILD,
                                   os.path.join(PACK, "gates", "_replay.py"), self.tmp, log],
                                  cwd=self.tmp, home=home, timeout=60)
        threads = [threading.Thread(target=child, args=(home,)) for home in homes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for home in homes:
            self.assertEqual(done[home].returncode, 0, done[home].stderr)
        with open(log, encoding="utf-8") as fh:
            lines = [ln.split() for ln in fh if ln.strip()]
        self.assertEqual([w for w, _home in lines], ["enter", "exit", "enter", "exit"],
                         f"the two homes held the lock at once: {lines}")
        self.assertEqual((lines[0][1], lines[2][1]), (lines[1][1], lines[3][1]))
        self.assertEqual(sorted({h for _w, h in lines}), homes)

    def test_the_lock_reads_the_uid_and_no_environment(self):
        paths = set()
        for home, tmp in (("/home-a", "/tmp-a"), ("/home-b", "/tmp-b")):
            with mock.patch.dict(os.environ, {"HOME": home, "TMPDIR": tmp,
                                              "XDG_RUNTIME_DIR": tmp}):
                paths.add(R.lock_path())
        self.assertEqual(paths, {os.path.join("/tmp", f"nopekit-{os.getuid()}",
                                              "firmware-ci-replay.lock")})

    def test_a_directory_that_is_not_this_users_is_never_the_lock(self):
        """/tmp is everyone's: a link where the directory goes (or a directory
        another user made) is not a lock of ours, and the lock falls back to
        HOME's, the old place."""
        elsewhere = os.path.join(self.tmp, "elsewhere")
        home = os.path.join(self.tmp, "home")
        os.makedirs(elsewhere)
        os.makedirs(home)
        os.symlink(elsewhere, os.path.join(self.tmp, f"nopekit-{os.getuid()}"))
        with mock.patch.object(R, "LOCK_ROOT", self.tmp), \
                mock.patch.dict(os.environ, {"HOME": home}):
            with R._one_replay_at_a_time():
                pass
        self.assertEqual(os.listdir(elsewhere), [])
        self.assertTrue(os.path.isfile(os.path.join(home, ".cache", "nopekit", "firmware-ci",
                                                    "replay.lock")))


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
# the transmit rule, on recorded configs
# --------------------------------------------------------------------------- #
def _swap(text: str, old: str, new: str) -> str:
    """``text`` with the one line ``old`` replaced by ``new`` (``""`` drops it)."""
    lines = text.splitlines()
    if lines.count(old) != 1:
        raise AssertionError(f"{old!r} is not one line of the config")
    at = lines.index(old)
    lines[at:at + 1] = [new] if new else []
    return "\n".join(lines) + "\n"


#: NCS's SoftDevice Controller as sdk-nrf v3.4.2's Kconfig writes it on an nRF52832
#: (the options Zephyr's choice offers that SoC, as the recorded LL config lists
#: them): ``BT_CTLR_TX_PWR_ANTENNA`` is 0, so the choice is visible.
NCS_SDC = "\n".join([
    'CONFIG_BOARD="nrf52dk_nrf52832"', "CONFIG_BT=y", "CONFIG_BT_LL_SOFTDEVICE=y",
    "# CONFIG_BT_LL_SW_SPLIT is not set", "CONFIG_BT_CTLR_TX_PWR_ANTENNA=0",
    "# CONFIG_BT_CTLR_TX_PWR_PLUS_4 is not set", "CONFIG_BT_CTLR_TX_PWR_PLUS_3=y",
    "# CONFIG_BT_CTLR_TX_PWR_0 is not set", "# CONFIG_BT_CTLR_TX_PWR_MINUS_4 is not set",
    "# CONFIG_BT_CTLR_TX_PWR_MINUS_8 is not set", "# CONFIG_BT_CTLR_TX_PWR_MINUS_12 is not set",
    "# CONFIG_BT_CTLR_TX_PWR_MINUS_16 is not set", "# CONFIG_BT_CTLR_TX_PWR_MINUS_20 is not set",
    "# CONFIG_BT_CTLR_TX_PWR_MINUS_40 is not set", "CONFIG_BT_CTLR_TX_PWR_DBM=3",
    "# CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL is not set",
    "# CONFIG_BT_CTLR_LE_POWER_CONTROL is not set", "# CONFIG_MPSL_FEM is not set"]) + "\n"


class TheTransmitRuleReadsTheConfig(unittest.TestCase):
    """``_tx.classify`` and ``_tx.judge``: the setting each profile reads, the
    ceiling the config lists, what can raise the power, and the stacks it skips
    by name."""

    def _read(self, name: str, text: str | None = None):
        got = T.classify(name, _config(name) if text is None else text)
        self.assertIsInstance(got, T.Reading, got)
        return got

    def test_zephyrs_ll_on_a_recorded_config(self):
        r = self._read("zephyr_nrf52832_peripheral_hr.config")
        self.assertEqual((r.profile.key, r.setting, r.setting_said, r.ceiling, r.raisers,
                          r.target),
                         ("zephyr-ll", 0.0, "CONFIG_BT_CTLR_TX_PWR_0=y", 4.0, (),
                          "nrf52dk_nrf52832"))
        j = T.judge(r, [])
        self.assertEqual((j.floor, j.value), (0.0, 0.0))
        self.assertIn("ceiling +4 dBm; no run-time path", j.said)

    def test_dynamic_control_is_judged_at_the_ceiling_kconfig_offered(self):
        """hci_pwr_ctrl turns on CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL: a host can set
        any level the SoC has, so the image can reach +4 dBm, the highest option the
        config lists for the nRF52832, whatever its 0 dBm default says."""
        j = T.judge(self._read("zephyr_nrf52832_hci_pwr_ctrl.config"), [])
        self.assertEqual((j.floor, j.value), (0.0, 4.0))
        self.assertIn("CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL can raise it to the ceiling: "
                      "judged at +4 dBm", j.said)

    def test_the_esp_controller_on_the_baselines_recorded_sdkconfigs(self):
        good = self._read("esp32s3_baseline.sdkconfig")
        bad = self._read("esp32s3_default.sdkconfig")
        self.assertEqual((good.profile.key, good.setting, good.ceiling, good.target),
                         ("esp-bt-ctrl", 6.0, 20.0, "esp32s3"))
        self.assertEqual((bad.setting, bad.setting_said),
                         (9.0, "CONFIG_BT_CTRL_DFT_TX_POWER_LEVEL_P9=y"))
        self.assertEqual(T.judge(good, []).value, 6.0)

    def test_ncs_reads_the_choice_or_the_antenna_value_and_never_both(self):
        """sdk-nrf v3.4.2 radio_nrf5_txp.h: the choice's level when
        CONFIG_BT_CTLR_TX_PWR_ANTENNA is 0, the antenna value otherwise, which hides
        the choice, so the config then lists no ceiling."""
        r = self._read("ncs", NCS_SDC)
        self.assertEqual((r.profile.key, r.setting, r.ceiling), ("ncs-sdc", 3.0, 4.0))
        antenna = NCS_SDC.replace("CONFIG_BT_CTLR_TX_PWR_ANTENNA=0",
                                  "CONFIG_BT_CTLR_TX_PWR_ANTENNA=2")
        antenna = "\n".join(ln for ln in antenna.splitlines()
                            if not re.match(r"(# )?CONFIG_BT_CTLR_TX_PWR_(PLUS|MINUS|0)",
                                            ln)) + "\n"
        r = self._read("ncs", antenna)
        self.assertEqual((r.setting, r.ceiling), (2.0, None))
        self.assertIn("antenna-referenced", r.setting_said)
        self.assertEqual(T.judge(r, []).value, 2.0, "no path: the setting is judged")
        with_path = antenna.replace("# CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL is not set",
                                    "CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL=y")
        j = T.judge(self._read("ncs", with_path), [])
        self.assertEqual((j.floor, j.value), (2.0, None), "a path with no ceiling")
        self.assertIn("lists no ceiling", j.said)
        with self.assertRaises(T.TxFault):
            T.classify("ncs", antenna + "CONFIG_BT_CTLR_TX_PWR_PLUS_3=y\n")

    def test_a_front_end_module_beside_a_path_leaves_the_ceiling_unknown(self):
        text = NCS_SDC.replace("# CONFIG_MPSL_FEM is not set", "CONFIG_MPSL_FEM=y")
        self.assertEqual(T.judge(self._read("ncs", text), []).value, 3.0,
                         "a front-end module alone moves nothing")
        text = text.replace("# CONFIG_BT_CTLR_LE_POWER_CONTROL is not set",
                            "CONFIG_BT_CTLR_LE_POWER_CONTROL=y")
        j = T.judge(self._read("ncs", text), [])
        self.assertEqual((j.floor, j.value), (3.0, None))
        self.assertIn("front-end module", j.said)

    def test_a_front_end_module_leaves_a_literal_uncapped(self):
        """With a front-end module NCS's level is antenna-referenced, and the SoC's
        options do not cap it (sdk-nrfxlib's CHANGELOG, NCS v2.1.0). F2 capped the
        literal at the SoC's +4 dBm, so a firmware that asks for +20 dBm at the
        antenna passed a 10 dBm grant."""
        text = NCS_SDC.replace("# CONFIG_MPSL_FEM is not set", "CONFIG_MPSL_FEM=y")
        lit = [T.Call("src/main.c", 2, "sdc_default_tx_power_set", 20.0,
                      "src/main.c:2 sdc_default_tx_power_set(... 20)")]
        j = T.judge(self._read("ncs", text), lit)
        self.assertEqual((j.floor, j.value), (20.0, 20.0))
        self.assertIn("sets +20 dBm; the SoC's ceiling +4 dBm, before the front-end "
                      "module's gain", j.said)
        j = T.judge(self._read("ncs", NCS_SDC), lit)
        self.assertEqual((j.floor, j.value), (4.0, 4.0), "with no module, the SoC caps it")
        low = [lit[0]._replace(dbm=2.0)]
        self.assertEqual(T.judge(self._read("ncs", text), low).value, 3.0,
                         "a literal under the setting moves nothing")

    def test_a_config_with_no_option_chosen_or_two_is_errored(self):
        text = _config("esp32s3_baseline.sdkconfig")
        none = _swap(text, "CONFIG_BT_CTRL_DFT_TX_POWER_LEVEL_P6=y",
                     "# CONFIG_BT_CTRL_DFT_TX_POWER_LEVEL_P6 is not set")
        two = _swap(text, "# CONFIG_BT_CTRL_DFT_TX_POWER_LEVEL_P9 is not set",
                    "CONFIG_BT_CTRL_DFT_TX_POWER_LEVEL_P9=y")
        for case in (none, two):
            with self.assertRaises(T.TxFault):
                T.classify("sdkconfig", case)

    def test_every_other_stack_is_named(self):
        esp = _config("esp32s3_baseline.sdkconfig")
        cases = {
            "zephyr_nrf5340_cpuapp_peripheral_hr.config": ("elsewhere", "CONFIG_BT_RPMSG=y"),
            "esp32c6_ble.sdkconfig": ("unread", "BT_LE controller family"),
        }
        for name, (kind, said) in cases.items():
            with self.subTest(config=name):
                got = T.classify(name, _config(name))
                self.assertEqual(got.kind, kind, got)
                self.assertIn(said, got.said)
        planted = {
            "controller disabled": (_swap(_swap(esp, "CONFIG_BT_CONTROLLER_ENABLED=y",
                                                "# CONFIG_BT_CONTROLLER_ENABLED is not set"),
                                          "# CONFIG_BT_CONTROLLER_DISABLED is not set",
                                          "CONFIG_BT_CONTROLLER_DISABLED=y"), "elsewhere"),
            "controller only": (_swap(esp, "# CONFIG_BT_CONTROLLER_ONLY is not set",
                                      "CONFIG_BT_CONTROLLER_ONLY=y"), "unread"),
            "an ESP-IDF controller with no default level": (
                "\n".join(ln for ln in esp.splitlines()
                          if "DFT_TX_POWER_LEVEL" not in ln) + "\n", "unread"),
            "a Zephyr host on another driver": ("CONFIG_BT=y\nCONFIG_BT_ESP32=y\n", "unread"),
            "a Zephyr host over UART": ("CONFIG_BT=y\nCONFIG_BT_H4=y\n", "elsewhere"),
        }
        for label, (text, kind) in planted.items():
            with self.subTest(case=label):
                self.assertEqual(T.classify("c", text).kind, kind)
        self.assertIsNone(T.classify("c", "# CONFIG_BT is not set\nCONFIG_SERIAL=y\n"))
        self.assertIsNone(T.classify("c", "# CONFIG_BT_ENABLED is not set\n"))

    def test_the_options_listed_are_the_options_offered(self):
        """Kconfig leaves out an option whose ``depends on`` fails: Zephyr's choice
        has options up to +21 dBm, and the nRF52832's config lists none above
        +4 dBm, so its ceiling is +4 dBm."""
        values, unset = T.kconfig(_config("zephyr_nrf52832_peripheral_hr.config"))
        listed = sorted(T._zephyr_dbm(n) for n in list(values) + list(unset)
                        if T._zephyr_dbm(n) is not None)
        self.assertEqual(listed, [-40, -20, -16, -12, -8, -4, 0, 3, 4])


class TheSetterScanReadsTheSources(unittest.TestCase):
    """``_tx.scan``: a setter call in the repository's own C sources, with its
    literal level when it has one; nothing in a comment, a string or a
    declaration."""

    ESP = T.PROFILES[2]
    SDC = T.PROFILES[1]

    def test_a_call_and_its_literal_level(self):
        code = ("void f(void)\n{\n    ESP_ERROR_CHECK(esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_ADV,\n"
                "                                         ESP_PWR_LVL_P12));\n"
                "    esp_ble_tx_power_set_enhanced(ESP_BLE_ENHANCED_PWR_TYPE_CONN, h,"
                " ESP_PWR_LVL_P21);\n}\n")
        calls = T.scan("main/radio.c", code, self.ESP)
        self.assertEqual([(c.line, c.name, c.dbm) for c in calls],
                         [(3, "esp_ble_tx_power_set", 12.0),
                          (5, "esp_ble_tx_power_set_enhanced", 20.0)])
        self.assertEqual(calls[0].said,
                         "main/radio.c:3 esp_ble_tx_power_set(... ESP_PWR_LVL_P12)")

    def test_comments_strings_and_declarations_are_not_calls(self):
        code = ('// esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_ADV, ESP_PWR_LVL_P20);\n'
                '/* esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_ADV,\n   ESP_PWR_LVL_P20); */\n'
                'static const char *m = "esp_ble_tx_power_set(x)";\n'
                'esp_err_t esp_ble_tx_power_set(esp_ble_power_type_t t, esp_power_level_t l);\n'
                'extern esp_err_t esp_ble_tx_power_set_enhanced(int, uint16_t, int);\n')
        self.assertEqual(T.scan("main/a.c", code, self.ESP), [])
        # Inverted: the word before the name on another line, the last word of a
        # directive, or one an operator joins, is no type. F2's scan walked back over
        # newlines and read CONFIG_LONG_RANGE, X and endif as types, so a +20 dBm
        # call under an #if read as a declaration and the gate passed at +6 dBm.
        call = "    esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_DEFAULT, ESP_PWR_LVL_P20);\n"
        cases = {
            "after #if": "void app_main(void)\n{\n#if CONFIG_LONG_RANGE\n" + call + "#endif\n}\n",
            "after #ifdef": "void app_main(void)\n{\n#ifdef X\n" + call + "#endif\n}\n",
            "after #endif": "void app_main(void)\n{\n#if 0\n#endif\n" + call + "}\n",
            "after a continued #if": ("void app_main(void)\n{\n#if defined(A) && \\\n    B\n"
                                      + call + "#endif\n}\n"),
            "after &&": ("void f(void)\n{\n    if (on && esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_"
                         "DEFAULT, ESP_PWR_LVL_P20)) {}\n}\n"),
            "after and": ("void f(void) { if (on and esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_DEFAULT,"
                          " ESP_PWR_LVL_P20)) {} }\n"),
        }
        for label, text in cases.items():
            with self.subTest(case=label):
                self.assertEqual([c.dbm for c in T.scan("main/a.c", text, self.ESP)], [20.0])
        split = "esp_err_t\nesp_ble_tx_power_set(esp_ble_power_type_t t, esp_power_level_t l);\n"
        self.assertEqual([c.dbm for c in T.scan("main/a.c", split, self.ESP)], [None],
                         "a type on the line before is not read: a use with no literal, "
                         "which can only move the verdict up")

    def test_a_level_that_is_not_a_literal_and_a_name_used_bare(self):
        code = ("int g(esp_power_level_t lvl)\n"
                "{ return esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_DEFAULT, lvl); }\n"
                "static setter_t s = esp_ble_tx_power_set;\n"
                "#define RAISE() esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_ADV, ESP_PWR_LVL_P9)\n"
                "#define SETTER esp_ble_tx_power_set_enhanced\n"
                "void h(void) { esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_ADV, ESP_PWR_LVL_P7); }\n")
        calls = T.scan("main/b.c", code, self.ESP)
        self.assertEqual([(c.line, c.dbm) for c in calls],
                         [(2, None), (3, None), (4, 9.0), (5, None), (6, None)],
                         "an unknown level name is no literal")
        self.assertIn("named, not called", calls[1].said)

    def test_the_sdc_setters(self):
        code = ("void f(void) {\n    sdc_default_tx_power_set(8);\n"
                "    sdc_default_tx_power_set((int8_t)-4);\n"
                "    sdc_hci_cmd_vs_zephyr_write_tx_power(&p);\n}\n")
        calls = T.scan("src/main.c", code, self.SDC)
        self.assertEqual([(c.name, c.dbm) for c in calls],
                         [("sdc_default_tx_power_set", 8.0), ("sdc_default_tx_power_set", -4.0),
                          ("sdc_hci_cmd_vs_zephyr_write_tx_power", None)])
        self.assertEqual(T.scan("src/main.c", code, T.PROFILES[0]), [],
                         "Zephyr's LL has no setter: its path is Kconfig's")

    def test_an_advertising_sets_power_is_read_from_its_member(self):
        """ESP-IDF v5.5.6: an advertising set's ``tx_power`` has priority over the
        Kconfig default on ESP32-C3/S3 (esp_gap_ble_api.h), with Bluedroid's
        ``esp_ble_gap_ext_adv_set_params`` or NimBLE's ``ble_gap_ext_adv_configure``.
        F2 read neither call, so a +20 dBm set beside a +6 dBm default passed."""
        r = T.classify("sdkconfig", _config("esp32s3_baseline.sdkconfig"))
        bluedroid = ("static esp_ble_gap_ext_adv_params_t adv = {\n"
                     "    .type = ESP_BLE_GAP_SET_EXT_ADV_PROP_LEGACY_IND,\n"
                     "    .peer_addr = {0},\n    .tx_power = 20,\n};\n"
                     "void f(void) { esp_ble_gap_ext_adv_set_params(0, &adv); }\n")
        nimble = ("void f(void)\n{\n    struct ble_gap_ext_adv_params params;\n"
                  "    memset(&params, 0, sizeof(params));\n    params.tx_power = 0x14;\n"
                  "    ble_gap_ext_adv_configure(1, &params, NULL, on_gap, NULL);\n}\n")
        cases = {
            "Bluedroid, a literal": (bluedroid, 20.0, 20.0, "tx_power = 20 at main/adv.c:4"),
            "NimBLE, a hex literal": (nimble, 20.0, 20.0, "tx_power = 0x14 at main/adv.c:5"),
            "no preference (127)": (bluedroid.replace("= 20,", "= EXT_ADV_TX_PWR_NO_PREFERENCE,"),
                                    6.0, 6.0, "as a cleared struct asks"),
            "no preference (0x7f)": (nimble.replace("0x14", "0x7f"), 6.0, 6.0, "cleared"),
            "never written: cleared, 0 dBm": (nimble.replace("    params.tx_power = 0x14;\n", ""),
                                              6.0, 6.0, "cleared"),
            "a variable": (nimble.replace("0x14", "level"), 6.0, 20.0, "tx_power = level"),
            "a compound assignment": (nimble.replace("= 0x14", "+= 3"), 6.0, 20.0, "tx_power +="),
            "its address taken": (nimble.replace("params.tx_power = 0x14;",
                                                 "pick(&params.tx_power);"),
                                  6.0, 20.0, "address taken"),
            "initialized by position": (
                "static esp_ble_gap_ext_adv_params_t adv = {1, 0x20, 0x20, 7, 0, 0, {0}, 0, 9};\n"
                "void f(void) { esp_ble_gap_ext_adv_set_params(0, &adv); }\n",
                6.0, 20.0, "initialized by position"),
            "an array, each named": (
                "esp_ble_gap_ext_adv_params_t s[2] = {{.tx_power = 3}, {.tx_power = 127}};\n"
                "void f(void) { esp_ble_gap_ext_adv_set_params(0, &s[0]); }\n",
                6.0, 6.0, "tx_power = 3 at main/adv.c:1"),
        }
        for label, (text, floor, value, said) in cases.items():
            with self.subTest(case=label):
                calls = T.scan_sources({"main/adv.c": text}, self.ESP)
                self.assertEqual(len(calls), 1, calls)
                self.assertIn(said, calls[0].said)
                j = T.judge(r, calls)
                self.assertEqual((j.floor, j.value), (floor, value), j.said)
        # Every write in the repository counts, whatever file holds it: the struct
        # the call is handed can be filled anywhere.
        calls = T.scan_sources({"main/adv.c": "void f(void) { esp_ble_gap_ext_adv_set_params"
                                              "(0, &adv); }\n",
                                "main/cfg.c": "esp_ble_gap_ext_adv_params_t adv = "
                                              "{ .tx_power = 18 };\n"}, self.ESP)
        self.assertEqual([(c.file, c.dbm) for c in calls], [("main/adv.c", 18.0)])
        alone = T.scan("main/adv.c", bluedroid, self.ESP)
        self.assertEqual([c.dbm for c in alone], [None],
                         "the call alone, its member not read, is a path with no literal")
        self.assertIn("its tx_power not read", alone[0].said)
        sdc = ("static sdc_hci_cmd_le_set_ext_adv_params_t p = { .adv_tx_power = 8 };\n"
               "void g(void) { sdc_hci_cmd_le_set_ext_adv_params(&p, &rsp); }\n")
        self.assertEqual([c.dbm for c in T.scan_sources({"src/adv.c": sdc}, self.SDC)], [8.0],
                         "the SoftDevice Controller's own call, by hand")

    def test_a_setter_moves_the_judged_value_up_never_down(self):
        r = T.classify("sdkconfig", _config("esp32s3_baseline.sdkconfig"))
        call = functools.partial(T.Call, "main/main.c", 9, "esp_ble_tx_power_set")
        cases = [([call(0.0, "low")], 6.0, 6.0),
                 ([call(12.0, "high")], 12.0, 12.0),
                 ([call(None, "var")], 6.0, 20.0),
                 ([call(0.0, "low"), call(None, "var")], 6.0, 20.0)]
        for calls, floor, value in cases:
            with self.subTest(calls=[c.said for c in calls]):
                j = T.judge(r, calls)
                self.assertEqual((j.floor, j.value), (floor, value))
        sdc = T.classify("ncs", NCS_SDC)
        j = T.judge(sdc, [T.Call("src/main.c", 2, "sdc_default_tx_power_set", 8.0, "lit")])
        self.assertEqual((j.floor, j.value), (4.0, 4.0),
                         "a literal past the ceiling is clamped to it, as the controller does")

    def test_the_sources_are_what_the_replay_copies(self):
        tmp = tempfile.mkdtemp(prefix="fw-src-")
        self.addCleanup(shutil.rmtree, tmp, True)
        _write(tmp, "main/main.c", "int x;\n")
        _write(tmp, "main/radio.h", "int y;\n")
        _write(tmp, "README.md", "esp_ble_tx_power_set\n")
        _write(tmp, ".nopekit/out/main.c", "int z;\n")
        self.assertEqual(sorted(T.sources(tmp, R.listing)), ["main/main.c", "main/radio.h"])


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

    def _run(self, gid: str, files: dict, params: dict | None = None, result=None, *,
             claims: list | None = None, through_spine: bool = False):
        replay = result or _recorded("act_esp_short_ota_slot.jsonl", files)
        real = gates_mod.GateContext.load_file
        self.loaders = []

        def load_file(self_, path, loader=None):
            if _is_replay(loader):
                self.loaders.append(json.loads(loader.__self__))
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
        ctx = gates_mod.GateContext(root=PACK, ledger=Ledger(claims=claims or []), params=base,
                                    out_dir=os.path.join(tmp, "out"), tier=1,
                                    log=lambda _m: None, extra={})
        with mock.patch.object(gates_mod.GateContext, "load_file", load_file):
            if not through_spine:
                return fn(ctx)
            with mock.patch.object(gates_mod, "availability", lambda _spec: (True, "")):
                return gates_mod.run_gate(spec, fn, ctx)

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

    def test_a_matrix_entry_named_is_read(self):
        """Holdout review (a handheld's upstream workflow): a matrix job could not
        be replayed one entry at a time, so every reader errored on it. With
        ``firmware_ci_matrix`` the replay runs one entry, the readers read its
        files, and the loader names the entry (so the gates share that replay)."""
        wf = _write(tempfile.mkdtemp(prefix="fw-mx-"), ".github/workflows/b.yml",
                    _workflow(job_extra="strategy:\n  matrix:\n    side: [l, r]\n"))
        self.addCleanup(shutil.rmtree, os.path.dirname(os.path.dirname(os.path.dirname(wf))),
                        True)
        result = _recorded("act_esp_short_ota_slot.jsonl", self._files())
        result["matrix"] = {"side": "l"}
        v = self._run("fw.image_fits", self._files(), {"firmware_ci_workflow": wf,
                                                      "firmware_ci_matrix": {"side": "l"}},
                      result=result)
        self.assertEqual((v.outcome, v.measured), ("pass", 48.3), v.render())
        self.assertEqual(self.loaders[-1]["matrix"], {"side": "l"})
        for bad in ({"side": ["l"]}, ["l"], {"a:b": "l"}):
            with self.subTest(firmware_ci_matrix=bad):
                v = self._run("fw.image_fits", self._files(),
                              {"firmware_ci_workflow": wf, "firmware_ci_matrix": bad})
                self.assertIn("firmware_ci_matrix is", v.error)

    def test_every_reader_asks_for_one_replay(self):
        """The loader every gate passes names the same replay: the job, both
        image globs, the matrix entry and the timeout. A gate that left a glob out
        would ask for another replay (holdout review: four replays in one check)."""
        asked = []
        for gid in ("fw.build", "fw.image_fits", "fw.sleep_wake", "fw.tx_power"):
            self._run(gid, self._files("esp_deep_sleep_start", "esp_sleep_enable_ext0_wakeup"),
                      {"firmware_ci_timeout_s": 600})
            asked.append(self.loaders[-1])
        self.assertEqual(asked, [{"job": "build", "keep": ["build/sleeper.bin",
                                                             "build/sleeper.elf"],
                                  "matrix": {}, "timeout_s": 600.0}] * 4)
        a, b = R.loader("build", ("x.bin",), {}, None), R.loader("build", ("x.bin",), {}, None)
        self.assertEqual(gates_mod._loader_id(a), gates_mod._loader_id(b),
                         "two gates' loaders for one replay are one key in the sweep's memo")
        self.assertNotEqual(gates_mod._loader_id(a),
                            gates_mod._loader_id(R.loader("build", ("x.bin",), {"s": "l"})))

    def test_a_timeout_that_is_not_a_time_is_errored(self):
        for bad in (-1, 0, "600", True):
            with self.subTest(firmware_ci_timeout_s=bad):
                v = self._run("fw.build", self._files(), {"firmware_ci_timeout_s": bad})
                self.assertIn("firmware_ci_timeout_s is", v.error)

    def test_the_flash_fit_claim_states_the_limit(self):
        """Holdout review (two projects): a claim stating the headroom PACK.md
        recommends made the spine warn "one number in two places", because the
        gate always said 100. The gate now reports the flash-fit claim's limit
        and still fails a byte past the region whatever the claim says."""
        def fit(limit, units="%", quantity="flash use", comparator="<="):
            return packs_mod.baseline_claims({"_claims": [{
                "id": "FIT", "statement": "the image leaves headroom in its slot",
                "kind": "measurable", "tags": ["flash-fit"],
                "acceptance": {"quantity": quantity, "comparator": comparator,
                               "limit": limit, "units": units}}]})

        v = self._run("fw.image_fits", self._files(), claims=fit(85), through_spine=True)
        self.assertEqual((v.outcome, v.measured, v.limit, v.comparator, v.units),
                         ("pass", 48.3, 85.0, "<=", "%"), v.render())
        self.assertEqual(claims_mod.limit_disagreements(Ledger(claims=fit(85)), [v]), [],
                         "the claim's limit and the gate's are one number")
        v = self._run("fw.image_fits", self._files(), claims=fit(40), through_spine=True)
        self.assertEqual((v.outcome, v.measured, v.limit), ("fail", 48.3, 40.0), v.render())
        files = self._files()
        files["build/sleeper.bin"] = b"\xe9" + bytes(0x100000 + 500 - 1)
        v = self._run("fw.image_fits", files, claims=fit(150), through_spine=True)
        self.assertEqual((v.outcome, v.measured), ("fail", 100.1), v.render())
        self.assertIn("past the region itself", v.detail)
        for bad in (fit(85, units="B"), fit(85, quantity="ram use"), fit(85, comparator=">=")):
            with self.subTest(claim=bad[0].acceptance.render()):
                v = self._run("fw.image_fits", self._files(), claims=bad)
                self.assertIn("state the headroom so", v.error)
        v = self._run("fw.image_fits", self._files(), claims=fit(None), through_spine=True)
        self.assertEqual((v.outcome, v.limit), ("pass", 100.0),
                         "a flash-fit claim with no limit leaves the region's own bound")

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


@unittest.skipUnless(HAS_YAML, NO_YAML)
class TheTxGateReadsOneReplay(unittest.TestCase):
    """``fw.tx_power`` on a replay handed to ``ctx.load_file``: the config the job
    wrote or the one its build record names, the claim's limit in the claim's
    units, and the skips by name."""

    @classmethod
    def setUpClass(cls):
        cls.registry = _registry()
        with open(os.path.join(PACK, "selftest", "baseline.json"), encoding="utf-8") as fh:
            cls.claims = packs_mod.baseline_claims(json.load(fh))

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fw-tx-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = os.path.join(self.tmp, "esp-sleep")
        shutil.copytree(os.path.join(PACK, "selftest", "assets", "esp-sleep"), self.app)

    def _run(self, *, files=None, configs=None, claims=None, through_spine=False):
        result = _recorded("act_esp_short_ota_slot.jsonl",
                           {"build/sleeper.bin": b"\xe9" + bytes(15), **(files or {})})
        result["configs"] = configs or {}
        real = gates_mod.GateContext.load_file

        def load_file(self_, path, loader=None):
            if _is_replay(loader):
                return result
            return real(self_, path, loader)

        spec, fn = self.registry.get("fw.tx_power")
        ctx = gates_mod.GateContext(
            root=self.tmp, ledger=Ledger(claims=self.claims if claims is None else claims),
            params={"firmware_ci_workflow": os.path.join(self.app, "github", "workflows",
                                                         "build.yml"),
                    "firmware_ci_job": "build", "firmware_image": "build/sleeper.bin"},
            out_dir=os.path.join(self.tmp, "out"), tier=1, log=lambda _m: None, extra={})
        with mock.patch.object(gates_mod.GateContext, "load_file", load_file):
            if not through_spine:
                return fn(ctx)
            # act and docker are stand-ins' here: the spine's own checks of a pass
            # are what this run is for, not the availability before them.
            with mock.patch.object(gates_mod, "availability", lambda _spec: (True, "")):
                return gates_mod.run_gate(spec, fn, ctx)

    def _sdk(self, name: str) -> bytes:
        return _config(name).encode("utf-8")

    def test_the_setting_against_the_claims_limit_in_its_own_units(self):
        v = self._run(files={"sdkconfig": self._sdk("esp32s3_baseline.sdkconfig")})
        self.assertEqual((v.passed, v.measured, v.limit, v.comparator, v.units),
                         (True, 6.0, 7.0, "<=", "dBm"), v.detail)
        self.assertIn("sdkconfig (ESP-IDF's BLE controller, esp32s3): "
                      "CONFIG_BT_CTRL_DFT_TX_POWER_LEVEL_P6=y, +6 dBm; ceiling +20 dBm; "
                      "no run-time path", v.detail)
        self.assertEqual([os.path.basename(p) for p in v.evidence], ["tx_power.json"])
        v = self._run(files={"sdkconfig": self._sdk("esp32s3_default.sdkconfig")})
        self.assertEqual((v.passed, v.measured), (False, 9.0), v.detail)

    def test_the_sdkconfig_the_build_record_names_counts_as_the_jobs(self):
        """A tracked sdkconfig that the configure step left as it was (ESP-IDF
        rewrites it only on a change) reaches the gate through ``configs``."""
        v = self._run(configs={"sdkconfig": self._sdk("esp32s3_default.sdkconfig")})
        self.assertEqual((v.passed, v.measured), (False, 9.0), v.detail)

    def test_a_setter_in_the_repositorys_sources_moves_the_judged_value(self):
        main = os.path.join(self.app, "main", "main.c")
        with open(main, encoding="utf-8") as fh:
            text = fh.read()
        with open(main, "w", encoding="utf-8") as fh:
            fh.write(text.replace("    esp_deep_sleep_start();\n",
                                  "    esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_ADV, "
                                  "ESP_PWR_LVL_N0);\n    esp_deep_sleep_start();\n"))
        v = self._run(files={"sdkconfig": self._sdk("esp32s3_baseline.sdkconfig")})
        self.assertEqual((v.passed, v.measured), (True, 6.0), "a lower level moves nothing")
        with open(main, "w", encoding="utf-8") as fh:
            fh.write(text.replace("    esp_deep_sleep_start();\n",
                                  "    esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_ADV, lvl);\n"
                                  "    esp_deep_sleep_start();\n"))
        v = self._run(files={"sdkconfig": self._sdk("esp32s3_baseline.sdkconfig")})
        self.assertEqual((v.passed, v.measured), (False, 20.0), v.detail)
        self.assertIn("main/main.c:", v.detail)
        self.assertIn("can raise it to the ceiling: judged at +20 dBm", v.detail)

    def test_a_call_under_an_if_and_an_advertising_sets_power_reach_the_gate(self):
        """Review of F2: the gate passed at the +6 dBm default beside a +20 dBm
        setter under ``#if``, and beside an advertising set asking for +18 dBm in a
        header of the repository's own."""
        main = os.path.join(self.app, "main", "main.c")
        with open(main, encoding="utf-8") as fh:
            text = fh.read()
        with open(main, "w", encoding="utf-8") as fh:
            fh.write(text.replace("    esp_deep_sleep_start();\n",
                                  "#if CONFIG_LONG_RANGE\n    esp_ble_tx_power_set("
                                  "ESP_BLE_PWR_TYPE_DEFAULT, ESP_PWR_LVL_P20);\n#endif\n"
                                  "    esp_deep_sleep_start();\n"))
        v = self._run(files={"sdkconfig": self._sdk("esp32s3_baseline.sdkconfig")})
        self.assertEqual((v.passed, v.measured), (False, 20.0), v.detail)
        self.assertIn("main/main.c:", v.detail)
        with open(main, "w", encoding="utf-8") as fh:
            fh.write(text.replace("    esp_deep_sleep_start();\n",
                                  "    esp_ble_gap_ext_adv_set_params(0, &ADV);\n"
                                  "    esp_deep_sleep_start();\n"))
        _write(self.app, "main/adv.h", "static esp_ble_gap_ext_adv_params_t ADV = {\n"
                                       "    .tx_power = 18,\n};\n")
        v = self._run(files={"sdkconfig": self._sdk("esp32s3_baseline.sdkconfig")})
        self.assertEqual((v.passed, v.measured), (False, 18.0), v.detail)
        self.assertIn("tx_power = 18 at main/adv.h:2 sets +18 dBm", v.detail)
        _write(self.app, "main/adv.h", "static esp_ble_gap_ext_adv_params_t ADV = {\n"
                                       "    .tx_power = EXT_ADV_TX_PWR_NO_PREFERENCE,\n};\n")
        v = self._run(files={"sdkconfig": self._sdk("esp32s3_baseline.sdkconfig")})
        self.assertEqual((v.passed, v.measured), (True, 6.0), "no preference moves nothing")

    def test_an_unknown_ceiling_over_a_passing_setting_is_undetermined(self):
        text = NCS_SDC.replace("CONFIG_BT_CTLR_TX_PWR_ANTENNA=0", "CONFIG_BT_CTLR_TX_PWR_ANTENNA=2")
        text = "\n".join(ln for ln in text.splitlines()
                         if not re.match(r"(# )?CONFIG_BT_CTLR_TX_PWR_(PLUS|MINUS|0)", ln))
        text = text.replace("# CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL is not set",
                            "CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL=y") + "\n"
        v = self._run(files={"build/zephyr/.config": text.encode()})
        self.assertTrue(v.skipped and not v.passed, v)
        self.assertIn("undetermined:", v.skip_reason)
        self.assertIn("nopekit claim physical", v.skip_reason)
        past = text.replace("CONFIG_BT_CTLR_TX_PWR_ANTENNA=2", "CONFIG_BT_CTLR_TX_PWR_ANTENNA=8")
        v = self._run(files={"build/zephyr/.config": past.encode()})
        self.assertEqual((v.passed, v.skipped, v.measured), (False, False, 8.0),
                         "a setting past the limit fails whatever the ceiling")

    def test_stacks_it_does_not_read_skip_by_name(self):
        cases = {
            "no config": ({}, "wrote no .config or sdkconfig"),
            "no Bluetooth": ({"build/zephyr/.config": b"# CONFIG_BT is not set\n"},
                             "no Bluetooth in build/zephyr/.config"),
            "BT_LE family": ({"sdkconfig": self._sdk("esp32c6_ble.sdkconfig")},
                             "BT_LE controller family"),
            "host only": ({"build/zephyr/.config":
                           self._sdk("zephyr_nrf5340_cpuapp_peripheral_hr.config")},
                          "CONFIG_BT_RPMSG=y"),
        }
        for label, (files, said) in cases.items():
            with self.subTest(case=label):
                v = self._run(files=files)
                self.assertTrue(v.skipped and not v.passed, v)
                self.assertIn(said, v.skip_reason)

    def test_a_host_beside_its_controllers_image_is_judged_on_the_controller(self):
        """Sysbuild writes one config per image: the app core's host-only one
        names no setting, and the network core's is judged."""
        v = self._run(files={
            "build/app/zephyr/.config": self._sdk("zephyr_nrf5340_cpuapp_peripheral_hr.config"),
            "build/net/zephyr/.config": self._sdk("zephyr_nrf52832_peripheral_hr.config")})
        self.assertEqual((v.passed, v.measured), (True, 0.0), v.detail)
        v = self._run(files={
            "build/a/zephyr/.config": self._sdk("zephyr_nrf52832_peripheral_hr.config"),
            "sdkconfig": self._sdk("esp32c6_ble.sdkconfig")})
        self.assertTrue(v.skipped, "a controller no profile reads beside a pass is no pass")
        v = self._run(files={
            "build/a/zephyr/.config": self._sdk("zephyr_nrf52832_hci_pwr_ctrl.config"),
            "sdkconfig": self._sdk("esp32s3_default.sdkconfig")})
        self.assertEqual((v.passed, v.measured), (False, 9.0), "the worst config fails")

    def test_a_claim_in_other_units_or_a_floor_is_errored(self):
        for acc, said in (({"quantity": "q", "comparator": "<=", "limit": 5.0, "units": "mW"},
                           "'mW'"),
                          ({"quantity": "q", "comparator": ">=", "limit": 0.0, "units": "dBm"},
                           ">= 0")):
            with self.subTest(acc=acc):
                claim = packs_mod.baseline_claims({"_claims": [
                    {"id": "TX", "statement": "s", "kind": "measurable", "acceptance": acc,
                     "tags": ["ble-tx-power"]}]})
                v = self._run(files={"sdkconfig": self._sdk("esp32s3_baseline.sdkconfig")},
                              claims=claim)
                self.assertFalse(v.passed)
                self.assertIn(said, v.error)

    def test_no_claim_tagged_is_errored_and_a_pass_is_held_to_its_claim(self):
        v = self._run(files={"sdkconfig": self._sdk("esp32s3_baseline.sdkconfig")},
                      claims=[], through_spine=True)
        self.assertEqual(v.outcome, "error", v.render())
        v = self._run(files={"sdkconfig": self._sdk("esp32s3_baseline.sdkconfig")},
                      through_spine=True)
        self.assertEqual(v.outcome, "pass", v.render())

    def test_a_config_that_contradicts_itself_is_errored(self):
        two = _swap(_config("esp32s3_baseline.sdkconfig"),
                    "# CONFIG_BT_CTRL_DFT_TX_POWER_LEVEL_P9 is not set",
                    "CONFIG_BT_CTRL_DFT_TX_POWER_LEVEL_P9=y")
        v = self._run(files={"sdkconfig": two.encode()})
        self.assertIn("2 options set", v.error)


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
        image = _env.run(["docker", "image", "inspect", IDF_IMAGE], cwd=tempfile.gettempdir())
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
                if gid == "fw.tx_power":
                    self.assertEqual((good.measured, bad.measured), (6.0, 9.0),
                                     "the stated +6 dBm, and ESP-IDF's default +9 dBm")


if __name__ == "__main__":
    unittest.main()
