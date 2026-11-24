# SPDX-License-Identifier: Apache-2.0
"""firmware-ci's wake rules: what an image that can enter deep sleep must arm, read
only from what the image shows. Standard library, and Zephyr's ``dtlib`` (vendored,
BSD-3-Clause) to read a ``zephyr.dts``.

**One rule table, keyed on the evidence.** An ELF that links an ESP-IDF deep-sleep
entry takes the ESP rule; a ``.config`` with ``CONFIG_ZMK_`` symbols beside the ELF
takes the ZMK rule; every other stack skips by name, with its reason. No rule here
fails a design whose documented wake is the reset line: ZMK soft off with no wake
list skips, and the ESP rule skips when the person states that reset is the wake.

**What a symbol shows, and what it does not.** A linked enable shows that the image
arms a wake source somewhere, not that every path arms it before the sleep call,
and not that the board routed the button to a pin that can wake. Both are the
lens and the physical claim PACK.md names.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))


class WakeFault(Exception):
    """The evidence cannot be judged: the gate is errored, never failed."""


@dataclass(frozen=True)
class Wake:
    """One judgment: ``outcome`` is ``pass``, ``fail`` or ``skip``."""
    outcome: str
    said: str


# --------------------------------------------------------------------------- #
# ESP-IDF
# --------------------------------------------------------------------------- #
#: The calls that enter deep sleep (ESP-IDF v5.5 ``esp_sleep.h``). ``esp_deep_sleep``
#: takes a time and arms the timer itself, so it is an entry and an armed wake.
ESP_ENTRIES = ("esp_deep_sleep_start", "esp_deep_sleep_try_to_start", "esp_deep_sleep_try",
               "esp_deep_sleep")

#: Enables that can wake the chip from deep sleep (ESP-IDF v5.5 ``esp_sleep.h`` and
#: the Sleep Modes guide: timer, ext0, ext1, touch, ULP and the deep-sleep GPIO
#: wake of the chips that have it).
ESP_DEEP_ENABLES = ("esp_sleep_enable_timer_wakeup", "esp_sleep_enable_ext0_wakeup",
                    "esp_sleep_enable_ext1_wakeup", "esp_sleep_enable_ext1_wakeup_io",
                    "esp_sleep_enable_touchpad_wakeup", "esp_sleep_enable_ulp_wakeup",
                    "esp_sleep_enable_gpio_wakeup_on_hp_periph_powerdown",
                    "esp_deep_sleep_enable_gpio_wakeup")

#: Enables that wake from light sleep only: the Sleep Modes guide lists UART, GPIO,
#: Wi-Fi, BT and USB as light-sleep wakes. They never count for deep sleep.
#: What slipped through a symbol rule without this list: a BLE build links
#: ``esp_sleep_enable_bt_wakeup`` through the controller, and read as armed.
ESP_LIGHT_ONLY = ("esp_sleep_enable_uart_wakeup", "esp_sleep_enable_gpio_wakeup",
                  "esp_sleep_enable_bt_wakeup", "esp_sleep_enable_wifi_wakeup",
                  "esp_sleep_enable_wifi_beacon_wakeup", "esp_sleep_enable_usb_wakeup")

#: The names an enable this pack has never seen would carry: such a symbol is
#: errored, never counted either way, so a newer IDF cannot read as unarmed.
_ENABLE_SHAPE = re.compile(r"^(?:esp_sleep_enable_\w*wakeup\w*|esp_deep_sleep_enable_\w+)$")

#: ``esp_pm_configure`` arms the timer wake itself for automatic light sleep
#: (ESP-IDF ``components/esp_pm/pm_impl.c``), so with it linked a timer enable
#: says nothing about what deep sleep arms.
ESP_PM = "esp_pm_configure"


def esp_rule(funcs: frozenset, lto: bool | None, reset_wake: str | None) -> Wake | None:
    """The ESP rule on an ELF's defined function symbols, or ``None`` when the ELF
    links no ESP deep-sleep entry. :class:`WakeFault` on an enable this pack does not
    know and on an LTO build."""
    entries = [s for s in ESP_ENTRIES if s in funcs]
    if not entries:
        return None
    unknown = sorted(s for s in funcs if _ENABLE_SHAPE.match(s)
                     and s not in ESP_DEEP_ENABLES and s not in ESP_LIGHT_ONLY)
    if unknown:
        raise WakeFault(f"{', '.join(unknown)} is on neither of this pack's lists "
                        f"(deep-sleep wakes, light-sleep-only wakes): a newer ESP-IDF; "
                        f"the rule cannot say whether it wakes the chip from deep sleep")
    if lto:
        raise WakeFault("the ELF's DWARF producer shows -flto: inlining can remove an "
                        "enable's symbol, so its absence shows nothing")
    armed = [s for s in ESP_DEEP_ENABLES if s in funcs]
    if "esp_deep_sleep" in funcs:
        armed.append("esp_deep_sleep (timer)")
    entry = entries[0]
    if not armed:
        if reset_wake:
            return Wake("skip", f"{entry} is linked and no deep-sleep wake enable is; "
                                f"reset is the stated wake ({reset_wake}): the pack does "
                                f"not read the board, so a physical claim settles it")
        if lto is None:
            raise WakeFault(f"{entry} is linked and no deep-sleep wake enable is, and the "
                            f"ELF holds no DWARF producer string, so an LTO build that "
                            f"dropped an enable's symbol cannot be ruled out")
        light = [s for s in ESP_LIGHT_ONLY if s in funcs]
        return Wake("fail", f"{entry} is linked and no deep-sleep wake enable is"
                            f"{' (' + ', '.join(light) + ' wake light sleep only)' if light else ''}"
                            f": the chip sleeps until it is reset")
    if armed == ["esp_sleep_enable_timer_wakeup"] and ESP_PM in funcs:
        return Wake("skip", f"{entry} is linked, and the only deep-sleep wake enable is "
                            f"the timer, which {ESP_PM} arms itself for automatic light "
                            f"sleep: this image cannot show what deep sleep arms")
    return Wake("pass", f"{entry} is linked; deep-sleep wake armed by {', '.join(armed)}")


# --------------------------------------------------------------------------- #
# ZMK, on the zephyr.dts and .config beside the ELF
# --------------------------------------------------------------------------- #
#: A kscan that wraps others (ZMK's composite and sideband kscans): the gate does not
#: follow what it wraps, so it is errored, never judged on the wrapper alone.
ZMK_WRAPPING_KSCANS = ("zmk,kscan-composite", "zmk,kscan-sideband-behaviors")


def kconfig(text: str) -> dict:
    """``{CONFIG_NAME: value}`` of a ``.config``; a symbol set ``n`` is a comment
    there, and absent here."""
    return dict(re.findall(r"^(CONFIG_\w+)=(.*)$", text, re.M))


def zmk_rule(config: str, dts: str | None) -> Wake | None:
    """The ZMK rule, or ``None`` when ``config`` is not a ZMK build.

    ZMK's low-power-states page: "Using deep sleep requires kscan nodes to have
    the wakeup-source property"; a soft-off device wakes by each device a
    ``zmk,soft-off-wakeup-sources`` node lists, or by the reset button when none
    is listed (a sound choice, so never a fail)."""
    cfg = kconfig(config)
    if not any(k.startswith("CONFIG_ZMK_") for k in cfg):
        return None
    sleep = cfg.get("CONFIG_ZMK_SLEEP") == "y"
    soft_off = cfg.get("CONFIG_ZMK_PM_SOFT_OFF") == "y"
    if not sleep and not soft_off:
        return Wake("skip", "no ZMK sleep is built (CONFIG_ZMK_SLEEP and "
                            "CONFIG_ZMK_PM_SOFT_OFF are not y): there is nothing to wake from")
    if dts is None:
        raise WakeFault("the build wrote no zephyr.dts beside its .config, so the wake "
                        "sources cannot be read")
    tree = _dt(dts)
    said, bad = [], []
    if sleep:
        kscans = _scanned(tree)
        wrapped = [n.path for n in kscans if _compatible(n) & set(ZMK_WRAPPING_KSCANS)]
        if wrapped:
            raise WakeFault(f"{', '.join(wrapped)} wraps other kscans, which this gate "
                            f"does not follow")
        bad += [f"{n.path} (kscan)" for n in kscans if "wakeup-source" not in n.props]
        said.append(f"deep sleep wakes on {', '.join(n.path for n in kscans)}")
    if soft_off:
        lists = [n for n in _nodes(tree) if "zmk,soft-off-wakeup-sources" in _compatible(n)
                 and _okay(n)]
        devices = []
        for node in lists:
            prop = node.props.get("wakeup-sources")
            if prop is None:
                continue
            try:
                devices += prop.to_nodes()
            except Exception as exc:                         # dtlib's DTError
                raise WakeFault(f"{node.path}: wakeup-sources does not resolve: {exc}") from exc
        if devices:
            bad += [f"{n.path} (soft-off wake)" for n in devices
                    if "wakeup-source" not in n.props]
            said.append(f"soft off wakes on {', '.join(n.path for n in devices)}")
        elif not sleep:
            return Wake("skip", "soft off is the only sleep built and lists no wake "
                                "device: it wakes by the reset button, which ZMK "
                                "documents as a sound choice; a physical claim settles it")
        else:
            said.append("soft off wakes by the reset button (no wake device listed)")
    if bad:
        return Wake("fail", f"no wakeup-source on {', '.join(bad)}: the board sleeps and "
                            f"that source cannot wake it")
    return Wake("pass", "; ".join(said) + ", each with wakeup-source")


def _dt(text: str):
    """``text`` parsed by Zephyr's own dtlib. dtlib reads a file, so the text goes
    into a file this run wrote itself, and then away."""
    from nopekit.modelio import load_path
    dtlib = load_path(os.path.join(_HERE, "_vendor", "dtlib.py"))
    work = tempfile.mkdtemp(prefix="nopekit-fw-dts-")
    try:
        path = os.path.join(work, "zephyr.dts")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            return dtlib.DT(path, base_dir=work)
        except dtlib.DTError as exc:
            raise WakeFault(f"zephyr.dts does not parse: {exc}") from exc
    finally:
        try:
            os.remove(os.path.join(work, "zephyr.dts"))
            os.rmdir(work)
        except OSError:
            shutil.rmtree(work, ignore_errors=True)


def _nodes(tree):
    stack = [tree.root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(list(node.nodes.values())))


def _compatible(node) -> set:
    prop = node.props.get("compatible")
    if prop is None:
        return set()
    try:
        return set(prop.to_strings())
    except Exception:                                        # dtlib's DTError
        return set()


def _okay(node) -> bool:
    prop = node.props.get("status")
    if prop is None:
        return True
    try:
        return prop.to_string() in ("okay", "ok")
    except Exception:
        return False


def _scanned(tree) -> list:
    """The kscans ZMK scans: each ``zmk,physical-layout`` node's ``kscan``, else the
    chosen ``zmk,kscan`` (ZMK ``app/src/physical_layouts.c``)."""
    out, seen = [], set()
    layouts = [n for n in _nodes(tree) if "zmk,physical-layout" in _compatible(n) and _okay(n)]
    for layout in layouts:
        prop = layout.props.get("kscan")
        if prop is None:
            continue
        try:
            node = prop.to_node()
        except Exception as exc:
            raise WakeFault(f"{layout.path}: kscan does not resolve: {exc}") from exc
        if node.path not in seen:
            seen.add(node.path)
            out.append(node)
    if not out:
        try:
            chosen = tree.get_node("/chosen")
        except Exception:
            chosen = None
        prop = chosen.props.get("zmk,kscan") if chosen is not None else None
        if prop is None:
            raise WakeFault("the devicetree chooses no zmk,kscan and lays out no kscan")
        try:
            node = prop.to_path()
        except Exception:
            try:
                node = prop.to_node()
            except Exception as exc:
                raise WakeFault(f"/chosen zmk,kscan does not resolve: {exc}") from exc
        out.append(node)
    return out


# --------------------------------------------------------------------------- #
# every other stack: skipped by name
# --------------------------------------------------------------------------- #
#: Symbols that name a stack whose wake this pack has no rule for, each with its
#: reason. Checked only when neither the ESP nor the ZMK rule applies.
_BY_NAME = (
    (("HAL_PWR_EnterSTANDBYMode", "HAL_PWREx_EnterSHUTDOWNMode", "HAL_PWR_EnterSTOPMode",
      "LL_PWR_SetPowerMode"),
     "STM32 HAL or LL: LL's wake enables are static inline and leave no symbol, so a "
     "symbol rule would fail sound designs"),
    (("sleep_goto_dormant_until_pin", "sleep_goto_dormant_until_edge_high",
      "xosc_dormant", "rosc_set_dormant"),
     "Pico SDK dormant: the wake is a pin or a clock chosen at run time"),
    (("nrf_power_system_off", "sd_power_system_off", "nrf_power_system_off_set"),
     "nRF5 SDK or bare-metal SYSTEMOFF: the wake is a pin sense set at run time"),
    (("keyboard_task", "matrix_scan_kb"),
     "QMK: no deep-sleep wake rule for QMK"),
)


def by_name(funcs: frozenset, config: str | None) -> str:
    """Why this image takes no rule: the skip reason, with its stack named."""
    if config is not None and re.search(r"^CONFIG_(?:BOARD|ZEPHYR)\w*=", config, re.M):
        if re.search(r"^CONFIG_POWEROFF=y", config, re.M) or "sys_poweroff" in funcs:
            return ("plain Zephyr or NCS sys_poweroff: the wake is set at run time, "
                    "so this pack has no rule for it")
        return "a Zephyr build with no ZMK sleep and no deep-sleep entry the pack reads"
    for names, reason in _BY_NAME:
        if any(n in funcs for n in names):
            return reason
    return ("no deep-sleep entry the pack reads: there is nothing to wake from, and a "
            "claim tagged deep-sleep is not Checked by the absence of sleep")
