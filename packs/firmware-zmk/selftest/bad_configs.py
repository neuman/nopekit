# SPDX-License-Identifier: Apache-2.0
"""Known-bad fixtures for firmware-zmk. One text edit each, to a copy of the
baseline repository (``selftest/assets/corne``: ZMK's corne, from upstream ZMK).

`nopekit gate selftest` requires every gate to FAIL here. A gate that passes its
own fixture is broken, and a green verdict from it means nothing.

**Sealed.** Every fixture starts from this pack's own ``selftest/baseline.json``
and ``selftest/assets/corne``, never from the host's projection or repository, and
hands the gate the baseline's own claims. Each copy goes under the CALLER's
``out_dir`` (``<out_dir>/firmware-zmk/known-bad/<fixture>/corne``), never into this
directory, so the proof never depends on whether the pack is writable.

**Each one measured** in the pinned image on ZMK v0.3.0: the three that reach the
build build with exit 0, so ``zmk.build`` passes the controls of the gates that
need it, and ``zmk.ci_step`` passes ``wrong_config``.
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil

from nopekit import packs
from nopekit.models import Ledger

_PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASELINE_PATH = os.path.join(_PACK_DIR, "selftest", "baseline.json")
_REPO = os.path.join(_PACK_DIR, "selftest", "assets", "corne")
_WORKFLOW = "github/workflows/build.yml"


def _read() -> dict:
    with open(_BASELINE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _variant(ctx, name: str, rel: str, old: str | None, new: str):
    """A context whose repository is the baseline's with ``old`` replaced by
    ``new`` in ``rel`` (``old`` None: ``new`` appended)."""
    marker = ctx.out_path("firmware-zmk", "known-bad", name, ".repo")
    dst = os.path.join(os.path.dirname(marker), "corne")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(_REPO, dst)
    path = os.path.join(dst, *rel.split("/"))
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if old is None:
        text += new
    else:
        if text.count(old) != 1:
            raise AssertionError(f"{name}: {old!r} is not in {rel} once")
        text = text.replace(old, new)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    baseline = _read()
    params = {k: v for k, v in baseline.items() if not k.startswith("_")}
    params["zmk_ci_workflow"] = os.path.join(dst, *_WORKFLOW.split("/"))
    return dataclasses.replace(ctx, params=params,
                               ledger=Ledger(claims=packs.baseline_claims(baseline)))


def floating_revision(ctx):
    """zmk.ci_step — config/west.yml as ZMK's unified-zmk-config-template ships it
    at 09fce009cdf2: ``defaults: revision: v0.3``, a branch that moves. CI builds
    whatever v0.3 is that day, so no verdict on v0.3.0 describes it."""
    return _variant(ctx, "floating_revision", "config/west.yml",
                    "revision: v0.3.0", "revision: v0.3")


def wrong_config(ctx):
    """zmk.build — the workflow's -DZMK_CONFIG pointed at ``.../conf``. CMake warns
    "Unable to locate ZMK config", and the build exits 0 with ZMK's own
    zmk/app/boards/shields/corne/corne.keymap: only the keymap line shows it."""
    return _variant(ctx, "wrong_config", _WORKFLOW,
                    '-DZMK_CONFIG="${GITHUB_WORKSPACE}/config"',
                    '-DZMK_CONFIG="${GITHUB_WORKSPACE}/conf"')


def no_wakeup_source(ctx):
    """zmk.wakeup_source — ``/delete-property/ wakeup-source`` on kscan0, appended
    to config/corne.keymap, the last overlay ZMK applies (corne.overlay is not read
    for corne_left, and corne_left.overlay cannot see the label kscan0). Deep sleep
    stays on; the build exits 0."""
    return _variant(ctx, "no_wakeup_source", "config/corne.keymap", None,
                    "\n&kscan0 { /delete-property/ wakeup-source; };\n")


def tx_plus_4(ctx):
    """zmk.tx_power — ``CONFIG_BT_CTLR_TX_PWR_PLUS_4=y``: 4 dBm against the baseline
    claim's 3.86 dBm, the nearest step past it, so a gate that rounds its limit
    passes it. Rejected: PLUS_8, which a gate rounding to 5 dBm still fails."""
    return _variant(ctx, "tx_plus_4", "config/corne.conf", None,
                    "CONFIG_BT_CTLR_TX_PWR_PLUS_4=y\n")
