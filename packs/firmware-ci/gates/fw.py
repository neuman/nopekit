# SPDX-License-Identifier: Apache-2.0
"""firmware-ci — the CI job that builds the firmware, replayed with act in its own
containers, and what the image that job writes shows.

    fw.ci_job      tier 0  the CI job can be replayed, and a failed build cannot read green
    fw.build       tier 1  that job, replayed with act, builds the firmware from this tree
    fw.image_fits  tier 1  the image lies inside the region its memory map names
    fw.sleep_wake  tier 1  where the image can enter deep sleep, it arms a deep-sleep wake
    fw.tx_power    tier 1  the highest BLE transmit setting the image can reach is inside
                           the setting its radio's grant was tested at

**One mechanism for every stack.** The build is the CI's own job, run by act in
the job's own containers on a copy of what the next commit would hold: Zephyr, NCS,
ZMK, ESP-IDF, Arduino, PlatformIO, the Pico SDK, QMK and STM32 make builds take the
same path. ``fw.ci_job`` is the replay's validity guard: each way it fails means a
replay's green would not describe the build. ``fw.build`` needs it, and the three
readers need ``fw.build``; a gate whose prerequisite did not pass reads Skipped.
One replay per sweep: the four tier-1 gates share it through ``ctx.load_file``.

**What slipped through, and which gate it is for** (references/what-slipped-through.md):

- ``fw.build``: a firmware repository's CI had never built its firmware. A build
  step named an extra module in a way the build system did not read, the board
  under the config tree was not found, and the red job went unread for many
  revisions. GitHub's own run judges a pushed commit, and nobody read it; the
  replay puts the verdict on the working tree, through CI's own steps and images.
- ``fw.ci_job``: a build piped into ``tee`` under GitHub's default shell, which has
  no pipefail. The build failed and the job read green.
- ``fw.image_fits``: a hand-written board definition whose flash layout disagreed
  with the bootloader on the device. It built clean: the linker compares against
  the build's own layout, the file that was wrong. ESP-IDF only warns when one OTA
  slot is too small.
- ``fw.sleep_wake``: deep sleep was turned on and no source could wake the chip.
  The build passed.
- ``fw.tx_power``: a firmware's Bluetooth transmit setting was raised, and nothing
  compared it with the setting its radio module was tested at. The claim meant to
  guard it compared the setting with the grant's measured output, another
  quantity, so it could neither show nor rule out the fault.

**Missing inputs skip; they never default.** Which workflow, which image and which
region are the project's to say. The region is never taken from the build.
"""
from __future__ import annotations

import json
import os
import posixpath

from nopekit.gates import gate, GateContext
from nopekit.modelio import load_path as _load_path
from nopekit.models import NegativeControl, Tier, Verdict

_HERE = os.path.dirname(os.path.abspath(__file__))
C = _load_path(os.path.join(_HERE, "_ci.py"))
R = _load_path(os.path.join(_HERE, "_replay.py"))
I = _load_path(os.path.join(_HERE, "_image.py"))
W = _load_path(os.path.join(_HERE, "_wake.py"))
T = _load_path(os.path.join(_HERE, "_tx.py"))

_TOOLS = ["act", "docker", "git"]
_PY = ["yaml"]

_NO_WORKFLOW = ("no firmware_ci_workflow: the GitHub Actions workflow that builds the "
                "firmware, from the project root; this pack will not guess which CI you "
                "mean (references/ci-templates.md has one for each stack)")


def _skip(gid: str, reason: str) -> Verdict:
    return Verdict(gate=gid, passed=False, skipped=True, skip_reason=reason)


def _opt(ctx: GateContext, key: str) -> str | None:
    value = ctx.param(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return str(value)


def _workflow(ctx: GateContext) -> str | None:
    rel = _opt(ctx, "firmware_ci_workflow")
    if rel is None:
        return None
    return rel if os.path.isabs(rel) else os.path.join(ctx.root, rel)


def _job(ctx: GateContext, path: str):
    """``(job, workflow)`` as the guard chose it, or :class:`R.ToolFault` when the
    guard would not pass: a build of something else says nothing."""
    try:
        wf = ctx.load_file(path, loader=C.load)
        found = C.choose(wf, job=_opt(ctx, "firmware_ci_job"), step=_opt(ctx, "firmware_ci_step"))
    except C.Refused as exc:
        raise R.ToolFault(f"the CI job is refused ({exc}); fw.ci_job says so") from exc
    if isinstance(found, C.Fail):
        raise R.ToolFault(f"the CI job does not stand ({found.reason}); fw.ci_job says so")
    return found, wf


def _replayed(ctx: GateContext, path: str):
    """``(job, workflow, result)``: the sweep's one replay of the build job."""
    job, wf = _job(ctx, path)
    return job, wf, ctx.load_file(path, loader=R.loader(job.job))


def _built(ctx: GateContext, path: str):
    """``(job, result)`` of a replay whose build stands, judged again as fw.build
    judges it; :class:`R.ToolFault` when it does not, and on a matrix job, whose
    entries share one workspace in bind mode, so its files are the last entry's."""
    job, wf, result = _replayed(ctx, path)
    passed, detail = R.judge(result, job, _opt(ctx, "firmware_image"), R.tolerated(wf, job))
    if not passed:
        raise R.ToolFault(f"the build does not stand ({detail}); fw.build says why")
    if job.matrix:
        raise R.ToolFault(f"job {job.job} has a matrix: in a replay its entries share one "
                          f"workspace in turn, so the files left are the last entry's; "
                          f"give each image its own job")
    return job, result


def _describe(job) -> str:
    where = f"container {job.image}" if job.image else f"runs-on {job.runs_on}"
    shell = f", shell {job.shell}" if job.shell else ""
    return f"job {job.job}, step {job.name!r} ({job.command}){shell}, {where}"


# --------------------------------------------------------------------------- #
# the guard
# --------------------------------------------------------------------------- #
@gate(
    id="fw.ci_job",
    basis="sourced",
    title="The CI job that builds the firmware can be replayed, and a failed build "
          "cannot read green",
    description="The replay's validity guard: reads the workflow, chooses the job and "
                "its build step, and fails a CI that can read green on a failed build "
                "(GitHub's documented continue-on-error and default-shell behaviour).",
    claims=["firmware-build", "ci-build"],
    tier=Tier.INSTANT,
    settles="ci build job",
    requires_python=_PY,
    negative_control=NegativeControl(
        fixture="selftest/bad_variants.py:masked_pipe",
        note="the Build step's run becomes `idf.py build 2>&1 | tee build.log` with no "
             "shell: key: under GitHub's bash -e {0} the pipeline exits with tee's status",
    ),
)
def ci_job(ctx: GateContext) -> Verdict:
    """The workflow parses, one job builds, one step in it is the build, and no
    form of masking lets that step read green on a failed build:
    ``continue-on-error: true``, ``|| true``, ``set +e`` (or a shell with no
    ``-e``) before more commands, the build before an ``&&`` and then more
    commands, or a pipe under a shell with no pipefail.

    A shape the replay cannot run, and a job or step this gate cannot choose, is
    errored: the CI may be sound, and the pack cannot say."""
    gid = "fw.ci_job"
    path = _workflow(ctx)
    if path is None:
        return _skip(gid, _NO_WORKFLOW)
    try:
        wf = ctx.load_file(path, loader=C.load)
        found = C.choose(wf, job=_opt(ctx, "firmware_ci_job"), step=_opt(ctx, "firmware_ci_step"))
    except C.Refused as exc:
        return Verdict(gate=gid, passed=False, error=f"refused: {exc}")
    if isinstance(found, C.Fail):
        return Verdict(gate=gid, passed=False, detail=found.reason)
    return Verdict(gate=gid, passed=True, detail=f"{_describe(found)}; no masking")


# --------------------------------------------------------------------------- #
# the build
# --------------------------------------------------------------------------- #
@gate(
    id="fw.build",
    basis="established",
    title="The CI workflow, replayed with act in its own containers, builds the firmware "
          "from this tree",
    claims=["firmware-build"],
    tier=Tier.BUILD,
    settles="firmware build",
    requires_tools=_TOOLS,
    requires_python=_PY,
    # Prerequisite fw.ci_job: a job the guard refused or failed is not CI's build,
    #    or its green says nothing. Isolated: the known-bad below leaves the
    #    workflow as it was, so the guard passes it.
    needs=["fw.ci_job"],
    negative_control=NegativeControl(
        fixture="selftest/bad_variants.py:unresolved_symbol",
        note="main.c declares `extern void board_power_init(void);` and calls it, and no "
             "source defines it: the link stops at `undefined reference to`",
    ),
)
def build(ctx: GateContext) -> Verdict:
    """act's per-step result for the build step, in the job's own container: a
    failure on a line from the closed list fails, as does a success whose output
    holds one (a masking the guard did not see), ZMK's missing-config warning, and
    a ``firmware_image`` the job did not write. A tool fault is errored: a step
    before the build that fails, act or docker stopping, a timeout, a compiler
    the machine stopped, and a failure on no line the list holds."""
    gid = "fw.build"
    path = _workflow(ctx)
    if path is None:
        return _skip(gid, _NO_WORKFLOW)
    try:
        job, wf, result = _replayed(ctx, path)
        passed, detail = R.judge(result, job, _opt(ctx, "firmware_image"),
                                 R.tolerated(wf, job))
    except R.WrongAct as exc:
        return _skip(gid, str(exc))
    except R.ToolFault as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    return Verdict(gate=gid, passed=passed, detail=detail,
                   evidence=R.evidence(result, job, ctx.out_path))


# --------------------------------------------------------------------------- #
# what the image shows
# --------------------------------------------------------------------------- #
_NO_MAP = ("no app_memory_map: the region the bootloader or partition table gives the "
           "app, from a cited record ({flash, app, ram?, other?}, {app: {size}} or "
           "{esp_partition_table: <csv>}); this pack never takes it from the linker "
           "script, the devicetree or the board's maximum size, the files the linker "
           "already compares against")


@gate(
    id="fw.image_fits",
    basis="sourced",
    title="The built image lies inside the region its bootloader or partition table "
          "gives it, on the part its memory map names",
    claims=["flash-fit", "memory-fit", "ota-slot"],
    tier=Tier.BUILD,
    settles="flash use",
    requires_tools=_TOOLS,
    requires_python=_PY,
    # Prerequisite fw.build: an image of firmware that does not build, or not from
    #    CI's job, is not the design's. Isolated: ESP-IDF only warns when some app
    #    partitions still fit, so the known-bad builds with exit 0.
    needs=["fw.build"],
    negative_control=NegativeControl(
        fixture="selftest/bad_variants.py:short_ota_slot",
        note="ota_1 in partitions.csv shrinks to 0x20000, below the image: IDF warns "
             "'1/3 app partitions are too small' and exits 0; an update into ota_1 "
             "cannot be written",
    ),
)
def image_fits(ctx: GateContext) -> Verdict:
    """Flash use in % of the region, rounded up to 0.1 %: on the address route,
    the highest byte the image writes past the app's start; on the size routes,
    the file against the slot (an ESP partition table's smallest app partition,
    read by ESP-IDF's own parser). Limit 100, the region's own bound, and the pass
    is decided on bytes; a claim's headroom (``flash use <= 85 %``) is held by the
    spine's cross-check. Fails with no limit: bytes in flash outside the app, a
    vector table not where the bootloader jumps, a RAM segment past a stated bank
    or in the Arm SRAM region outside every stated bank."""
    gid = "fw.image_fits"
    path = _workflow(ctx)
    if path is None:
        return _skip(gid, _NO_WORKFLOW)
    raw = ctx.param("app_memory_map")
    if raw is None:
        return _skip(gid, _NO_MAP)
    glob = _opt(ctx, "firmware_image")
    if glob is None:
        return _skip(gid, "no firmware_image: the file the device receives (.uf2, .hex, "
                          ".bin or .elf), as a path in the job's workspace, e.g. "
                          "build/*.bin")
    try:
        mm = I.parse_map(raw)
        _job_, result = _built(ctx, path)
        name, image = R.built_file(result, glob, "firmware_image")
        if mm.shape == "esp":
            csv = mm.csv if os.path.isabs(mm.csv) else os.path.join(ctx.root, mm.csv)
            try:
                with open(csv, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as exc:
                raise I.ImageFault(f"app_memory_map.esp_partition_table {mm.csv!r} cannot "
                                   f"be read ({type(exc).__name__})") from exc
            shown = mm.csv if not os.path.isabs(mm.csv) else os.path.basename(mm.csv)
            fit = I.judge_esp(image, name, I.esp_slots(text, mm.offset), shown)
        elif mm.shape == "slot":
            fit = I.judge_slot(image, name, mm.slot)
        else:
            elf = None
            elf_glob = _opt(ctx, "firmware_elf")
            if elf_glob is not None and not I.is_elf(image):
                elf = I.read_elf(R.built_file(result, elf_glob, "firmware_elf")[1])
            fit = I.judge_address(image, name, mm, elf)
    except R.WrongAct as exc:
        return _skip(gid, str(exc))
    except (R.ToolFault, I.ImageFault) as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    if fit.problems:
        return Verdict(gate=gid, passed=False, detail="; ".join(fit.problems))
    # The pass is the bytes' (``fit.fits``), and the figure is rounded up, so the
    # two agree: what slipped through a figure rounded to the nearest 0.1 % is in
    # ``_image.percent_up``.
    return Verdict(gate=gid, passed=fit.fits and fit.measured <= 100.0,
                   measured=fit.measured, limit=100.0, comparator="<=", units="%",
                   detail=fit.said)


@gate(
    id="fw.sleep_wake",
    basis="sourced",
    title="Where the image can enter deep sleep, it arms a wake source that works in "
          "deep sleep",
    claims=["deep-sleep", "wakeup-source"],
    tier=Tier.BUILD,
    settles="deep sleep wake source",
    requires_tools=_TOOLS,
    requires_python=_PY,
    # Prerequisite fw.build: the same as fw.image_fits's. Isolated: the known-bad
    #    builds with exit 0, with no wake source linked.
    needs=["fw.build"],
    negative_control=NegativeControl(
        fixture="selftest/bad_variants.py:no_wake_source",
        note="main.c's ext0 wake line and its pull-up line removed: "
             "esp_deep_sleep_start is still linked, and no deep-sleep wake enable is",
    ),
)
def sleep_wake(ctx: GateContext) -> Verdict:
    """The rule the evidence keys: an ELF that links an ESP-IDF deep-sleep entry
    must link a deep-sleep wake enable (light-sleep-only enables never count); a
    ZMK build with deep sleep needs ``wakeup-source`` on each scanned kscan, and
    each listed soft-off wake device needs it too. Every other stack skips by name.
    No rule fails a design whose wake is the reset line."""
    gid = "fw.sleep_wake"
    path = _workflow(ctx)
    if path is None:
        return _skip(gid, _NO_WORKFLOW)
    elf_glob = _opt(ctx, "firmware_elf")
    if elf_glob is None:
        return _skip(gid, "no firmware_elf: the ELF in the job's workspace, e.g. "
                          "build/*.elf; the wake rules read its symbols, and the .config "
                          "and zephyr.dts beside it")
    try:
        _job_, result = _built(ctx, path)
        name, data = R.built_file(result, elf_glob, "firmware_elf")
        elf = I.read_elf(data)
        here = posixpath.dirname(name)
        config_raw = result["files"].get(posixpath.join(here, ".config"))
        dts_raw = result["files"].get(posixpath.join(here, "zephyr.dts"))
        config = config_raw.decode("utf-8", "replace") if config_raw is not None else None
        dts = dts_raw.decode("utf-8", "replace") if dts_raw is not None else None
        wake = W.esp_rule(elf.funcs, I.lto(elf), _opt(ctx, "deep_sleep_wakes_by_reset"))
        if wake is None and config is not None:
            wake = W.zmk_rule(config, dts)
        if wake is None:
            return _skip(gid, W.by_name(elf.funcs, config))
    except R.WrongAct as exc:
        return _skip(gid, str(exc))
    except (R.ToolFault, I.ImageFault, W.WakeFault) as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    if wake.outcome == "skip":
        return _skip(gid, wake.said)
    return Verdict(gate=gid, passed=wake.outcome == "pass", detail=f"{name}: {wake.said}")


@gate(
    id="fw.tx_power",
    basis="sourced",
    title="The highest Bluetooth LE transmit setting the image can reach is inside the "
          "setting its radio's grant was tested at",
    description="Reads the .config or sdkconfig the replayed build resolved: Zephyr's "
                "LL controller, NCS's SoftDevice Controller or ESP-IDF's BLE controller "
                "(ESP32-C3/S3); a run-time path moves the judged setting to the highest "
                "option Kconfig offered the SoC. Every other stack skips by name.",
    claims=[T.TAG],
    tier=Tier.BUILD,
    settles="bluetooth le transmit power setting",
    requires_tools=_TOOLS,
    requires_python=_PY,
    # Prerequisite fw.build: a setting of firmware that does not build, or not from
    #    CI's job, is not the design's. Isolated: the known-bad builds with exit 0,
    #    at ESP-IDF's default level.
    needs=["fw.build"],
    negative_control=NegativeControl(
        fixture="selftest/bad_variants.py:tx_line_removed",
        note="the TX level line removed from sdkconfig.defaults: ESP-IDF's default, "
             "CONFIG_BT_CTRL_DFT_TX_POWER_LEVEL_P9 (+9 dBm), against the baseline "
             "claim's 7 dBm, the nearest step past it",
    ),
)
def tx_power(ctx: GateContext) -> Verdict:
    """The setting the stack resolved, judged against the claim tagged
    ``ble-tx-power``: the setting the radio's grant was tested at (47 CFR
    15.204(c)(2), "at its maximum available output power level"), never the
    grant's listed output. A run-time path (``*_DYNAMIC_CONTROL``, LE Power
    Control, or a setter call in the repository's C sources) moves the judged
    value to the ceiling, the highest option Kconfig offered the SoC; a setter
    whose level is a literal is judged at that literal. Where the ceiling cannot
    be known, a setting that alone passes reads undetermined (skipped) and names
    the conducted measurement. A config this pack reads no profile for skips by
    name; a controller in another image or chip skips unless a config of this
    build holds one this pack reads."""
    gid = "fw.tx_power"
    path = _workflow(ctx)
    if path is None:
        return _skip(gid, _NO_WORKFLOW)
    try:
        _job_, result = _built(ctx, path)
        found = T.configs(result)
        readings, others = [], []
        for rel, text in found.items():
            got = T.classify(rel, text)
            if isinstance(got, T.Other):
                others.append(got)
            elif got is not None:
                readings.append(got)
        if readings:
            repo, _rel = C.locate(path)
            code = T.sources(repo, R.listing)
            judged = [T.judge(r, [c for rel, text in code.items()
                                  for c in T.scan(rel, text, r.profile)])
                      for r in readings]
    except R.WrongAct as exc:
        return _skip(gid, str(exc))
    except (R.ToolFault, T.TxFault, C.Refused, OSError) as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    unread = [o for o in others if o.kind == "unread"]
    if not readings:
        if not found:
            return _skip(gid, "the job wrote no .config or sdkconfig: a stack with no "
                              "Kconfig (Arduino, PlatformIO's Arduino core, the Pico SDK, "
                              "QMK) has no build-time setting this pack reads; a conducted "
                              "measurement settles it (nopekit claim physical)")
        if not others:
            return _skip(gid, f"no Bluetooth in {', '.join(found)}: this build has no BLE "
                              f"controller to judge")
        shown = unread + [o for o in others if o.kind != "unread"]
        return _skip(gid, "; ".join(f"{o.file}: {o.said}" for o in shown))
    acc = ctx.acceptance(T.TAG)
    comparator = acc.comparator.value
    if str(acc.units or "").strip() != T.UNITS or comparator not in T.COMPARATORS:
        return Verdict(gate=gid, passed=False, error=(
            f"the claim tagged {T.TAG} states {comparator} {acc.limit:g} {acc.units!r}; this "
            f"gate judges a transmit setting at most ({' or '.join(T.COMPARATORS)}) in "
            f"{T.UNITS}, the setting the radio's grant was tested at: state the limit so"))
    said = "; ".join(j.said for j in judged)
    # Each config's outcome: a floor past the limit fails whatever the ceiling; an
    # unknown ceiling above a floor that passes is undetermined, never a pass.
    failed, undetermined, passed = [], [], []
    for j in judged:
        if not acc.holds(j.floor):
            failed.append(j.floor)
        elif j.value is None:
            undetermined.append(j)
        elif not acc.holds(j.value):
            failed.append(j.value)
        else:
            passed.append(j.value)
    if failed:
        return Verdict(gate=gid, passed=False, measured=max(failed), limit=acc.limit,
                       comparator=comparator, units=T.UNITS, detail=said,
                       evidence=_tx_evidence(ctx, judged, acc))
    if unread:
        return _skip(gid, f"{said}; and " + "; ".join(f"{o.file}: {o.said}" for o in unread))
    if undetermined:
        return _skip(gid, f"undetermined: {said}; settle it with a conducted measurement at "
                          f"the highest setting the firmware can reach (nopekit claim "
                          f"physical)")
    return Verdict(gate=gid, passed=True, measured=max(passed), limit=acc.limit,
                   comparator=comparator, units=T.UNITS, detail=said,
                   evidence=_tx_evidence(ctx, judged, acc))


def _tx_evidence(ctx: GateContext, judged: list, acc) -> list[str]:
    """``tx_power.json``: each config read, its setting, its ceiling and what can
    raise it, and the value judged, under the gate's out directory."""
    rows = [{"file": j.reading.file, "profile": j.reading.profile.key,
             "target": j.reading.target, "setting": j.reading.setting_said,
             "setting_dbm": j.reading.setting, "ceiling_dbm": j.reading.ceiling,
             "raisers": list(j.reading.raisers), "front_end_module": j.reading.fem,
             "floor_dbm": j.floor, "judged_dbm": j.value, "said": j.said} for j in judged]
    out = ctx.out_path("firmware-ci", "tx_power", "tx_power.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"limit": acc.limit, "comparator": acc.comparator.value,
                   "units": T.UNITS, "configs": rows}, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return [out]
