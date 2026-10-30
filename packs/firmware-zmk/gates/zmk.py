# SPDX-License-Identifier: Apache-2.0
"""firmware-zmk — gates for ZMK keyboard firmware, built as its CI builds it.

    zmk.ci_step         tier 0  the CI's one `west build` step can be reproduced (the guard)
    zmk.build           tier 1  that step, run in ZMK's image, builds this repository's firmware
    zmk.wakeup_source   tier 1  with deep sleep built, every kscan ZMK scans can wake the board
    zmk.tx_power        tier 1  the radio's built transmit power against the claim's limit

**Three checks and a guard.** The build, the wakeup source and the transmit power
settle claims. ``zmk.ci_step`` is the build's CI-flag guard: it reads the workflow
and the manifest, so a CI that cannot build (no ZMK_CONFIG, ZMK not pinned at
v0.3.0) fails in 0.03 s, before any container starts. It is its own gate because a
pack must have a tier-0 gate, and no build runs in under 2 s.

**Where each verdict comes from.** The tier-1 gates are the tools' own answers:
CMake's and Zephyr's exit code and log, Kconfig's ``.config``, and Zephyr's
``edtlib`` reading the build's ``edt.pickle``, all in ZMK's image pinned by digest
(``bin/zmk-west``) on a read-only ZMK v0.3.0 workspace with no network. They read
inside a subprocess the trace cannot see into, so they are opaque and re-run on
every check where ``zmk-west`` exists, as pcb-kicad's KiCad gates do. One full build
and one configure per sweep: the gates share them through ``ctx.load_file``.

**What slipped through, and which gate it is for** (gripster, a handheld keyboard
deck on an nRF52840 module; references/what-slipped-through.md):

- ``zmk.build``: the repository's CI had never built its firmware. It gave
  ``Invalid BOARD``: the step passed a module as ``-DZEPHYR_EXTRA_MODULES``, where
  ZMK v0.3.0 reads ``-DZMK_EXTRA_MODULES`` (app/CMakeLists.txt:6), and the board
  under ZMK_CONFIG was not found. With that one word changed it builds. This gate
  runs CI's own flags, read from the workflow file, so it cannot drift from CI.
- ``zmk.build``: a wrong ZMK_CONFIG path is a CMake *warning*, and the build exits 0
  on ZMK's default keymap. Only the ``Using keymap file:`` line shows it.
- ``zmk.wakeup_source``: ``CONFIG_ZMK_SLEEP=y`` with no ``wakeup-source`` on the
  kscan. The board sleeps and no key wakes it. The build passes.
- ``zmk.tx_power``: ``CONFIG_BT_CTLR_TX_PWR_PLUS_8=y`` on a module whose grant
  lists 3.86 dBm conducted.

**Missing inputs skip; they never default.** Which workflow, and the transmit
limit, are the project's to say.
"""
from __future__ import annotations

import os

from nopekit.gates import gate, GateContext
from nopekit.modelio import load_path as _load_path
from nopekit.models import NegativeControl, Tier, Verdict

_HERE = os.path.dirname(os.path.abspath(__file__))
Z = _load_path(os.path.join(_HERE, "_zmk.py"))

_TOOLS = ["zmk-west", "docker"]
_PY = ["yaml"]


def _skip(gid: str, reason: str) -> Verdict:
    return Verdict(gate=gid, passed=False, skipped=True, skip_reason=reason)


_NO_WORKFLOW = ("no zmk_ci_workflow: the GitHub workflow that builds the firmware, "
                "relative to the project root — this pack will not guess which CI you mean")


def _workflow(ctx: GateContext) -> str | None:
    rel = ctx.param("zmk_ci_workflow")
    if not rel:
        return None
    rel = str(rel)
    return rel if os.path.isabs(rel) else os.path.join(ctx.root, rel)


def _configured(ctx: GateContext, gid: str, path: str) -> dict:
    """The shared configure, judged as the build is: a configure that fails here
    is the tool's or the build's, and ``zmk.build`` says which."""
    result = ctx.load_file(path, loader=Z.configure)
    passed, detail = Z.judge(result)
    if not passed:
        raise Z.ToolFault(f"the configure does not stand ({detail}); zmk.build says why")
    return result


# --------------------------------------------------------------------------- #
# the guard
# --------------------------------------------------------------------------- #
@gate(
    id="zmk.ci_step",
    basis="model-only",
    title="The CI's one west build step can be reproduced, on ZMK v0.3.0",
    claims=["firmware-build", "ci-build-step"],
    tier=Tier.INSTANT,
    settles="ci build step",
    requires_python=_PY,
    negative_control=NegativeControl(
        fixture="selftest/bad_configs.py:floating_revision",
        note="config/west.yml as ZMK's config template ships it, ZMK at "
             "`defaults: revision: v0.3`, a branch that moves",
    ),
)
def ci_step(ctx: GateContext) -> Verdict:
    """The workflow has one self-contained ``west build`` run step, with only
    ``${GITHUB_WORKSPACE}`` expansions and absolute ``-D`` paths and a ZMK_CONFIG,
    and the manifest beside it pins zmk v0.3.0 from zmkfirmware.

    A CI that is wrong or unpinned fails. A shape this pack cannot reproduce (ZMK's
    reusable workflow, a matrix, step ``env``, a second build step, another shell)
    is errored: the CI may be right, and the pack cannot say."""
    gid = "zmk.ci_step"
    path = _workflow(ctx)
    if path is None:
        return _skip(gid, _NO_WORKFLOW)
    try:
        step = ctx.load_file(path, loader=Z.read_step)
    except Z.StepRefused as exc:
        return Verdict(gate=gid, passed=False, error=f"refused: {exc}")
    if isinstance(step, Z.StepFail):
        return Verdict(gate=gid, passed=False, detail=step.reason)
    return Verdict(gate=gid, passed=True,
                   detail=f"one `west build` step, {step.name!r} in job {step.job} at "
                          f"{step.wd}; zmk {Z.ZMK_REVISION} pinned in "
                          f"{os.path.normpath(os.path.join(step.wd, 'config', 'west.yml'))}")


# --------------------------------------------------------------------------- #
# the build
# --------------------------------------------------------------------------- #
@gate(
    id="zmk.build",
    basis="established",
    title="The CI's build step, run in ZMK's image, builds this repository's firmware",
    claims=["firmware-build"],
    tier=Tier.BUILD,
    settles="firmware build",
    requires_tools=_TOOLS,
    requires_python=_PY,
    # Prerequisite zmk.ci_step: a step the guard refused or failed is not CI's
    #    build, and a build of something else says nothing. Isolated: paths are not
    #    checked there, so the guard passes this gate's own known-bad control.
    needs=["zmk.ci_step"],
    negative_control=NegativeControl(
        fixture="selftest/bad_configs.py:wrong_config",
        note="the workflow's -DZMK_CONFIG pointed at .../conf, a directory that is "
             "not there: CMake warns, and the build exits 0 on ZMK's default keymap",
    ),
)
def build(ctx: GateContext) -> Verdict:
    """The step's own argv, run by ``bin/zmk-west`` in the pinned image. A design
    fails on a line from the closed list (``Invalid BOARD``, a devicetree or
    Kconfig error, a failed object, a link error), on no ``zmk.uf2``, and on a
    ``Using keymap file:`` path outside ZMK_CONFIG. Any other failure, a Zephyr
    other than 3.5.0 and a workspace at other commits are errored."""
    gid = "zmk.build"
    path = _workflow(ctx)
    if path is None:
        return _skip(gid, _NO_WORKFLOW)
    try:
        result = ctx.load_file(path, loader=Z.full)
        passed, detail = Z.judge(result)
    except (Z.ToolFault, Z.StepRefused) as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    log = ctx.out_path("firmware-zmk", "build", "log.txt")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(result["log"])
    evidence = [log]
    if result["config"]:
        config = ctx.out_path("firmware-zmk", "build", "dot-config.txt")
        with open(config, "w", encoding="utf-8") as fh:
            fh.write(result["config"])
        evidence.append(config)
    return Verdict(gate=gid, passed=passed, detail=detail, evidence=evidence)


# --------------------------------------------------------------------------- #
# what the build holds
# --------------------------------------------------------------------------- #
@gate(
    id="zmk.wakeup_source",
    basis="sourced",
    title="With deep sleep built, every kscan ZMK scans is a wakeup source",
    claims=["deep-sleep", "wakeup-source"],
    tier=Tier.BUILD,
    settles="wakeup from deep sleep",
    requires_tools=_TOOLS,
    requires_python=_PY,
    # Prerequisite zmk.build: a devicetree of firmware that does not build, or of
    #    ZMK's default keymap, is not this design's. Isolated: the known-bad builds,
    #    exit 0, on the repository's own keymap.
    needs=["zmk.build"],
    negative_control=NegativeControl(
        fixture="selftest/bad_configs.py:no_wakeup_source",
        note="`&kscan0 { /delete-property/ wakeup-source; };` appended to "
             "config/corne.keymap, the last overlay ZMK applies",
    ),
)
def wakeup_source(ctx: GateContext) -> Verdict:
    """ZMK v0.3.0 docs/docs/features/low-power-states.md:27: "Using deep sleep
    requires kscan nodes to have the wakeup-source property", and app/src/pm.c:46
    leaves out of suspend only the devices whose wake is enabled. With ``CONFIG_ZMK_SLEEP=y``, each
    kscan ZMK scans (each physical layout's ``kscan``, else the chosen
    ``zmk,kscan``) must have it true in ``edt.pickle``, read by the workspace's
    own edtlib. A kscan whose binding does not declare the property is errored."""
    gid = "zmk.wakeup_source"
    path = _workflow(ctx)
    if path is None:
        return _skip(gid, _NO_WORKFLOW)
    try:
        result = _configured(ctx, gid, path)
        if Z.kconfig(result["config"]).get("CONFIG_ZMK_SLEEP") != "y":
            return Verdict(gate=gid, passed=True, detail="no deep sleep is built "
                                                         "(CONFIG_ZMK_SLEEP is not y)")
        targets = wakeup_targets(result)
    except (Z.ToolFault, Z.StepRefused) as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    bad = [t["path"] for t in targets if not t["val"]]
    if bad:
        return Verdict(gate=gid, passed=False,
                       detail=f"CONFIG_ZMK_SLEEP=y, and no wakeup-source on "
                              f"{', '.join(bad)}: no key wakes the board from deep sleep")
    return Verdict(gate=gid, passed=True,
                   detail=f"CONFIG_ZMK_SLEEP=y; wakeup-source on "
                          f"{', '.join(t['path'] for t in targets)}")


def wakeup_targets(result: dict) -> list[dict]:
    """The kscans the extract found, or :class:`ToolFault`: none, or one whose
    binding does not declare ``wakeup-source``, is nothing this gate can judge."""
    targets = result["edt"]
    if targets is None:
        raise Z.ToolFault(f"the devicetree was not read: {result['edt_error']}")
    if not targets:
        raise Z.ToolFault("the devicetree chooses no zmk,kscan and lays out no kscan")
    undeclared = [t["path"] for t in targets if not t["present"]]
    if undeclared:
        raise Z.ToolFault(f"the binding of {', '.join(undeclared)} declares no "
                          f"wakeup-source, so it cannot say whether it wakes")
    return targets


@gate(
    id="zmk.tx_power",
    basis="established",
    title="The radio's built transmit power is inside the claim's limit",
    claims=["tx-power", "radio"],
    tier=Tier.BUILD,
    settles="radio transmit power",
    requires_tools=_TOOLS,
    requires_python=_PY,
    # Prerequisite zmk.build: the same as zmk.wakeup_source's. Isolated: the
    #    known-bad builds, exit 0.
    needs=["zmk.build"],
    negative_control=NegativeControl(
        fixture="selftest/bad_configs.py:tx_plus_4",
        note="CONFIG_BT_CTLR_TX_PWR_PLUS_4=y in config/corne.conf: 4 dBm against the "
             "baseline claim's 3.86 dBm, the nearest step past it",
    ),
)
def tx_power(ctx: GateContext) -> Verdict:
    """Kconfig computes ``CONFIG_BT_CTLR_TX_PWR_DBM`` from the TX power choice
    (Zephyr 3.5.0 subsys/bluetooth/controller/Kconfig:355); it is judged against
    the claim tagged ``tx-power``. A host that can raise the power at run time
    (``CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL``) fails: the build does not bound it.
    A controller other than Zephyr's LL is errored."""
    gid = "zmk.tx_power"
    path = _workflow(ctx)
    if path is None:
        return _skip(gid, _NO_WORKFLOW)
    try:
        cfg = Z.kconfig(_configured(ctx, gid, path)["config"])
        verdict = tx_rule(gid, cfg)
        if verdict is not None:
            return verdict
        acc = ctx.acceptance("tx-power")
    except (Z.ToolFault, Z.StepRefused) as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    measured = int(cfg["CONFIG_BT_CTLR_TX_PWR_DBM"])
    return Verdict(gate=gid, passed=acc.holds(measured), measured=measured,
                   limit=acc.limit, comparator=acc.comparator.value, units="dBm",
                   detail=f"CONFIG_BT_CTLR_TX_PWR_DBM={measured}")


def tx_rule(gid: str, cfg: dict) -> Verdict | None:
    """The fail before any limit is read, or ``None``; :class:`ToolFault` when
    the build is not on Zephyr's LL controller."""
    if cfg.get("CONFIG_BT_LL_SW_SPLIT") != "y" or "CONFIG_BT_CTLR_TX_PWR_DBM" not in cfg:
        raise Z.ToolFault("the build is not on Zephyr's LL controller "
                          "(no CONFIG_BT_LL_SW_SPLIT=y and CONFIG_BT_CTLR_TX_PWR_DBM)")
    if cfg.get("CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL") == "y":
        return Verdict(gate=gid, passed=False,
                       detail="CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL=y: the host can raise "
                              "TX power at run time; the build does not bound it")
    return None
