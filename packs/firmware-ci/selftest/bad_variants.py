# SPDX-License-Identifier: Apache-2.0
"""Known-bad fixtures for firmware-ci. One text edit each, to a copy of the pack's own
ESP-IDF app (``selftest/assets/esp-sleep``).

`nopekit gate selftest` requires every gate to FAIL here. A gate that passes its own
fixture is broken, and a green verdict from it means nothing.

**Sealed.** Every fixture starts from this pack's own ``selftest/baseline.json`` and
``selftest/assets/esp-sleep``, never from the host's projection or repository, and
hands the gate the baseline's own claims (none: no gate reads an acceptance
condition). Each copy goes under the CALLER's ``out_dir``
(``<out_dir>/firmware-ci/known-bad/<fixture>/esp-sleep``), never into this
directory, so the proof never depends on whether the pack is writable. That
directory is git-ignored in a project and in no work tree in pack mode: the
replay walks it (``_replay.listing``).

**Each one measured** with act 0.2.97 in espressif/idf v5.5.6: the three that reach
the replay build with the workflow unchanged, so ``fw.ci_job`` passes
``unresolved_symbol``; ``short_ota_slot`` and ``no_wake_source`` build with exit 0,
so ``fw.build`` passes them.
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
_APP = os.path.join(_PACK_DIR, "selftest", "assets", "esp-sleep")
_WORKFLOW = "github/workflows/build.yml"


def _read() -> dict:
    with open(_BASELINE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _variant(ctx, name: str, edits: list[tuple[str, str, str]]):
    """A context whose app is the baseline's with each ``(file, old, new)`` edit
    made, ``old`` present exactly once."""
    marker = ctx.out_path("firmware-ci", "known-bad", name, ".app")
    dst = os.path.join(os.path.dirname(marker), "esp-sleep")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(_APP, dst)
    for rel, old, new in edits:
        path = os.path.join(dst, *rel.split("/"))
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if text.count(old) != 1:
            raise AssertionError(f"{name}: {old!r} is not in {rel} once")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(old, new))
    baseline = _read()
    params = {k: v for k, v in baseline.items() if not k.startswith("_")}
    params["firmware_ci_workflow"] = os.path.join(dst, *_WORKFLOW.split("/"))
    params["app_memory_map"] = {"esp_partition_table": os.path.join(dst, "partitions.csv")}
    return dataclasses.replace(ctx, params=params,
                               ledger=Ledger(claims=packs.baseline_claims(baseline)))


def masked_pipe(ctx):
    """fw.ci_job — the Build step piped into ``tee``, with no ``shell:`` key. Under
    GitHub's ``bash -e {0}`` (and act's ``sh -e``) the pipeline exits with tee's
    status, so a failed ``idf.py build`` reads green, on GitHub and in a replay
    (measured: a compile error in main.c under this step read success). The build
    itself is sound, so only the guard sees it."""
    return _variant(ctx, "masked_pipe", [(
        _WORKFLOW, "run: . $IDF_PATH/export.sh && idf.py build\n",
        "run: . $IDF_PATH/export.sh && idf.py build 2>&1 | tee build.log\n")])


def unresolved_symbol(ctx):
    """fw.build — main.c declares and calls a helper no source defines. It compiles,
    and the link stops at ``undefined reference to `board_power_init'``: what an
    agent writes when it calls a helper it never wrote. The workflow is unchanged."""
    return _variant(ctx, "unresolved_symbol", [
        ("main/main.c", '#include "nimble/nimble_port.h"\n',
         '#include "nimble/nimble_port.h"\n\nextern void board_power_init(void);\n'),
        ("main/main.c", "void app_main(void)\n{\n",
         "void app_main(void)\n{\n    board_power_init();\n")])


def short_ota_slot(ctx):
    """fw.image_fits — ota_1 shrinks to 0x20000, a 64 KB-aligned size below the
    image (measured 0x7b6d0 B). ESP-IDF's check_sizes.py only warns while some app
    partitions still fit ("1/3 app partitions are too small"), so ``idf.py build``
    exits 0 and an update into ota_1 cannot be written."""
    return _variant(ctx, "short_ota_slot", [(
        "partitions.csv", "ota_1,    app,  ota_1,   ,        1M,",
        "ota_1,    app,  ota_1,   ,        0x20000,")])


def no_wake_source(ctx):
    """fw.sleep_wake — the ext0 wake line and its pull-up line removed.
    ``esp_deep_sleep_start`` is still linked, no deep-sleep wake enable is
    (measured: the BLE stack links none), and the build passes."""
    return _variant(ctx, "no_wake_source", [
        ("main/main.c", "    rtc_gpio_pullup_en(GPIO_NUM_0);\n", ""),
        ("main/main.c", "    esp_sleep_enable_ext0_wakeup(GPIO_NUM_0, 0);\n", "")])
