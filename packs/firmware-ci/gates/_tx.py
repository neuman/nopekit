# SPDX-License-Identifier: Apache-2.0
"""firmware-ci's transmit rule: the highest Bluetooth LE transmit setting an image can
reach, read from the Kconfig its build resolved. Standard library.

**What is judged.** A setting, never an output. 47 CFR 15.204(c)(2) has a radiator
tested "at its maximum available output power level": a module's grant was tested
at one setting, and its listed output is what that setting measured after the
module's losses. The claim tagged ``ble-tx-power`` states the setting the grant
was tested at (the test report or the integration manual gives it), in dBm, and
this rule reports the highest setting the firmware can reach in the same unit.
*Rejected:* the grant's listed conducted output as the limit (a setting and a
measured output are two quantities, so the comparison can neither show nor rule
out the fault); a limit in the pack (the grant is the person's, not the pack's).

**Where the setting comes from.** The ``.config`` (Zephyr, NCS, ZMK) or
``sdkconfig`` (ESP-IDF) that the replay's build wrote, or that ESP-IDF's build
record names (``_replay.BUILD_RECORDS``): the stack's own Kconfig resolution, on
this tree, in CI's own container. Never a build directory on this machine, which
can be older than its sources, and never a second build.

**Three profiles, from a closed table** (:data:`PROFILES`), each checked against
the stack's own sources (each table's comment names them). Any other stack is
skipped by name: a controller this pack reads no profile for, a host whose
controller is in another image or chip, or no Bluetooth at all.

**The ceiling rule.** A run-time path can raise the power past the Kconfig
default: a Kconfig option that compiles one in (``*_DYNAMIC_CONTROL``, LE Power
Control) or a setter call in the repository's own C sources. With a path, the
judged value is the ceiling, the highest option Kconfig offered this SoC (the
options the file lists, chosen or ``is not set``), because that is the maximum
the firmware makes available. A setter whose level is a literal is judged at
that literal. Where the ceiling cannot be known (an antenna-referenced NCS
setting hides the choice; a front-end module adds gain), a setting that alone
passes reads undetermined, and names the conducted measurement that settles it.

**The setter scan is the pack's own** (model-only, and documented so): a text
scan of the C and C++ files the next commit holds, comments and string literals
blanked. It can only make a verdict more conservative: it moves the judged value
up, to a literal or to the ceiling, or to undetermined. A call reached by
indirection (a library outside the repository, a module, an HCI command an
external host sends) is not seen; a call the build does not compile still counts.
"""
from __future__ import annotations

import os
import re
from typing import NamedTuple

TAG = "ble-tx-power"
UNITS = "dBm"
#: An upper bound only: the claim says the setting is at most the one the grant
#: was tested at. *Rejected:* ``>=`` and ``==`` (a floor on a transmit setting is
#: a range claim, judged by a conducted measurement, not by a build).
COMPARATORS = ("<=", "<")


class TxFault(Exception):
    """A config the rule cannot read as its stack wrote it: the gate is errored."""


# --------------------------------------------------------------------------- #
# the Kconfig text
# --------------------------------------------------------------------------- #
_SET = re.compile(r"^(CONFIG_\w+)=(.*)$", re.M)
_UNSET = re.compile(r"^# (CONFIG_\w+) is not set$", re.M)


def kconfig(text: str) -> tuple[dict, frozenset]:
    """``({CONFIG_NAME: value}, {CONFIG_NAME set n})`` of a ``.config`` or
    ``sdkconfig``. Kconfig writes a visible bool that is ``n`` as ``# CONFIG_X is
    not set`` and leaves out a symbol whose ``depends on`` fails: the listed options
    of a choice are exactly the ones it offered this SoC."""
    return dict(_SET.findall(text)), frozenset(_UNSET.findall(text))


def _y(values: dict, name: str) -> bool:
    return values.get(name) == "y"


# --------------------------------------------------------------------------- #
# the profiles
# --------------------------------------------------------------------------- #
#: ESP-IDF's level names for ESP32-C3 and ESP32-S3 (``esp_power_level_t``,
#: components/bt/include/esp32c3/include/esp_bt.h at v5.5.6, which the S3's
#: controller shares): the dBm each one sets. A table, never a name parsed: the
#: deprecated ``ESP_PWR_LVL_P21`` is index 15, the same +20 dBm as ``P20``.
ESP_LEVELS = {"ESP_PWR_LVL_N24": -24, "ESP_PWR_LVL_N21": -21, "ESP_PWR_LVL_N18": -18,
              "ESP_PWR_LVL_N15": -15, "ESP_PWR_LVL_N12": -12, "ESP_PWR_LVL_N9": -9,
              "ESP_PWR_LVL_N6": -6, "ESP_PWR_LVL_N3": -3, "ESP_PWR_LVL_N0": 0,
              "ESP_PWR_LVL_P3": 3, "ESP_PWR_LVL_P6": 6, "ESP_PWR_LVL_P9": 9,
              "ESP_PWR_LVL_P12": 12, "ESP_PWR_LVL_P15": 15, "ESP_PWR_LVL_P18": 18,
              "ESP_PWR_LVL_P20": 20, "ESP_PWR_LVL_P21": 20}


class Profile(NamedTuple):
    key: str
    name: str
    #: A choice option's name -> its dBm, or ``None`` for a symbol that is not one.
    option: object
    #: Kconfig symbols that, set ``y``, compile in a way to raise the power at run time.
    raisers: tuple
    #: Setter function -> the index of its level argument (``None``: never a literal).
    setters: dict
    #: How a literal level reads: ``"esp"`` (``ESP_LEVELS``) or ``"int"`` (dBm).
    literal: str


_ZEPHYR_OPTION = re.compile(r"^CONFIG_BT_CTLR_TX_PWR_(?:(PLUS|MINUS)_(\d+)|0)$")
_ESP_OPTION = re.compile(r"^CONFIG_BT_CTRL_DFT_TX_POWER_LEVEL_([NP])(\d+)$")


def _zephyr_dbm(name: str) -> int | None:
    m = _ZEPHYR_OPTION.match(name)
    if m is None:
        return None
    return 0 if m.group(1) is None else int(m.group(2)) * (1 if m.group(1) == "PLUS" else -1)


def _esp_dbm(name: str) -> int | None:
    m = _ESP_OPTION.match(name)
    if m is None:
        return None
    return int(m.group(2)) * (1 if m.group(1) == "P" else -1)


#: The closed table, first match wins.
#:
#: - **zephyr-ll**, Zephyr's own controller (ZMK's too): ``choice BT_CTLR_TX_PWR``
#:   (zephyr subsys/bluetooth/controller/Kconfig, v3.5.0 and v4.4.2), applied as
#:   ``RADIO_TXP_DEFAULT`` (ll_sw/nordic/hal/nrf5/radio/radio_nrf5_txp.h).
#:   ``BT_CTLR_TX_PWR_DYNAMIC_CONTROL`` compiles in the HCI vendor command that sets
#:   the power per role (hci.c handles ``BT_HCI_OP_VS_WRITE_TX_POWER_LEVEL`` only
#:   under it), so the opcode alone is no path. An advertising set's requested
#:   power only fills the header's TX Power field (ull_adv.c), never the radio.
#: - **ncs-sdc**, nRF Connect SDK's SoftDevice Controller: the same choice, hidden
#:   when ``BT_CTLR_TX_PWR_ANTENNA`` is not 0 (``depends on BT_CTLR_TX_PWR_ANTENNA =
#:   0 || BT_LL_SW_SPLIT``); sdk-nrf v3.4.2 subsys/bluetooth/controller/
#:   radio_nrf5_txp.h then sets ``RADIO_TXP_DEFAULT`` to the antenna value, which
#:   hci_driver.c hands to ``sdc_default_tx_power_set``. ``BT_LL_SOFTDEVICE``
#:   selects ``BT_CTLR_LE_POWER_CONTROL_SUPPORT``, so LE Power Control can be on.
#: - **esp-bt-ctrl**, ESP-IDF's BLE controller on ESP32-C3 and ESP32-S3:
#:   ``choice BT_CTRL_DFT_TX_POWER_LEVEL`` (components/bt/controller/esp32c3/
#:   Kconfig.in at v5.5.6, which esp32s3/Kconfig.in sources), default ``P9``, +9 dBm.
#:   No Kconfig option raises it; ``esp_ble_tx_power_set`` and
#:   ``esp_ble_tx_power_set_enhanced`` do (esp_bt.h).
#:
#: *Rejected:* ESP-IDF's BT_LE family (ESP32-C2, C5, C6, C61, H2) and the ESP32's
#: BTDM controller, until each is checked against its own Kconfig; Wi-Fi (a build
#: can only lower ESP-IDF's ceiling); LoRa and Thread (set at run time).
PROFILES = (
    Profile("zephyr-ll", "Zephyr's LL controller", _zephyr_dbm,
            ("CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL", "CONFIG_BT_CTLR_LE_POWER_CONTROL"),
            {}, "int"),
    Profile("ncs-sdc", "NCS's SoftDevice Controller", _zephyr_dbm,
            ("CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL", "CONFIG_BT_CTLR_LE_POWER_CONTROL"),
            {"sdc_default_tx_power_set": 0, "sdc_hci_cmd_vs_zephyr_write_tx_power": None},
            "int"),
    Profile("esp-bt-ctrl", "ESP-IDF's BLE controller", _esp_dbm, (),
            {"esp_ble_tx_power_set": 1, "esp_ble_tx_power_set_enhanced": 2}, "esp"),
)

#: Zephyr HCI drivers whose controller is in another image or chip (zephyr
#: drivers/bluetooth/hci/Kconfig at v4.4.2; ``BT_RPMSG`` is the IPC driver's name
#: in v3.5.0, ZMK v0.3.0's). A host with one of these is not where the setting
#: lives. Every other driver is read as a controller this pack reads no profile for.
ELSEWHERE = ("CONFIG_BT_HCI_IPC", "CONFIG_BT_RPMSG", "CONFIG_BT_H4", "CONFIG_BT_H5",
             "CONFIG_BT_SPI", "CONFIG_BT_USERCHAN", "CONFIG_BT_STM32_IPM")


class Reading(NamedTuple):
    """One config with a controller this pack reads."""
    file: str
    profile: Profile
    setting: float
    setting_said: str       # ``CONFIG_..._P6=y``
    ceiling: float | None   # the highest option offered, or None when none is listed
    raisers: tuple          # the run-time Kconfig symbols set ``y``
    fem: bool               # a front-end module (NCS ``CONFIG_MPSL_FEM``)
    target: str             # ``esp32s3``, ``nrf52dk_nrf52832``, or ``""``


class Other(NamedTuple):
    """One config with Bluetooth this pack does not judge: ``kind`` is
    ``"elsewhere"`` (the controller is in another image or chip) or ``"unread"``
    (a controller is here, and no profile reads it)."""
    file: str
    kind: str
    said: str


def classify(file: str, text: str) -> Reading | Other | None:
    """What ``text`` (one config file) holds: a :class:`Reading`, an :class:`Other`,
    or ``None`` when it has no Bluetooth. :class:`TxFault` on a config that
    contradicts its own Kconfig (no option chosen, or two)."""
    values, unset = kconfig(text)
    if _y(values, "CONFIG_BT_LL_SW_SPLIT"):
        return _reading(file, PROFILES[0], values, unset)
    if _y(values, "CONFIG_BT_LL_SOFTDEVICE"):
        return _reading(file, PROFILES[1], values, unset)
    if _y(values, "CONFIG_BT_ENABLED"):                      # ESP-IDF
        if _y(values, "CONFIG_BT_CONTROLLER_DISABLED"):
            return Other(file, "elsewhere", "CONFIG_BT_CONTROLLER_DISABLED=y: the host "
                                            "talks to a controller outside this image")
        if _y(values, "CONFIG_BT_CONTROLLER_ONLY"):
            return Other(file, "unread", "CONFIG_BT_CONTROLLER_ONLY=y: a host outside this "
                                         "image drives the controller over HCI, and what "
                                         "it sets is not in this build")
        names = list(values) + list(unset)
        if any(_esp_dbm(n) is not None for n in names):
            return _reading(file, PROFILES[2], values, unset)
        if any(n.startswith("CONFIG_BT_LE_DFT_TX_POWER_LEVEL_") for n in names):
            return Other(file, "unread", "ESP-IDF's BT_LE controller family (CONFIG_BT_LE_"
                                         "DFT_TX_POWER_LEVEL_*: ESP32-C2, C5, C6, C61, H2) "
                                         "is not a profile this pack reads yet")
        return Other(file, "unread", "an ESP-IDF Bluetooth controller with no BT_CTRL_ or "
                                     "BT_LE_ default TX level (the ESP32's BTDM controller) "
                                     "is not a profile this pack reads")
    if _y(values, "CONFIG_BT"):                              # Zephyr, NCS, ZMK
        away = [n for n in ELSEWHERE if _y(values, n)]
        if away:
            return Other(file, "elsewhere", f"{away[0]}=y: the controller is in another image "
                                            f"or chip; its own config holds the setting")
        return Other(file, "unread", "CONFIG_BT=y with neither CONFIG_BT_LL_SW_SPLIT nor "
                                     "CONFIG_BT_LL_SOFTDEVICE: a controller this pack reads "
                                     "no profile for")
    return None


def _reading(file: str, profile: Profile, values: dict, unset: frozenset) -> Reading:
    offered = {n: profile.option(n) for n in list(values) + sorted(unset)
               if profile.option(n) is not None}
    chosen = sorted(n for n in offered if _y(values, n))
    target = (values.get("CONFIG_IDF_TARGET") or values.get("CONFIG_BOARD") or "").strip('"')
    raisers = tuple(n for n in profile.raisers if _y(values, n))
    fem = _y(values, "CONFIG_MPSL_FEM")
    antenna = values.get("CONFIG_BT_CTLR_TX_PWR_ANTENNA")
    if profile.key == "ncs-sdc" and antenna not in (None, "", "0"):
        try:
            setting = int(antenna)
        except ValueError as exc:
            raise TxFault(f"{file}: CONFIG_BT_CTLR_TX_PWR_ANTENNA={antenna} is not an "
                          f"integer") from exc
        if chosen:
            raise TxFault(f"{file}: CONFIG_BT_CTLR_TX_PWR_ANTENNA={antenna} hides the TX "
                          f"power choice, and {chosen[0]}=y is set too: the file is not "
                          f"what Kconfig writes")
        return Reading(file, profile, float(setting), f"CONFIG_BT_CTLR_TX_PWR_ANTENNA={antenna}"
                       f" (antenna-referenced)", None, raisers, fem, target)
    if len(chosen) != 1:
        raise TxFault(f"{file}: {profile.name} is on, and the TX power choice has "
                      f"{len(chosen)} options set ({', '.join(chosen) or 'none'}): the "
                      f"file is not what Kconfig writes")
    return Reading(file, profile, float(offered[chosen[0]]), f"{chosen[0]}=y",
                   float(max(offered.values())), raisers, fem, target)


# --------------------------------------------------------------------------- #
# the setter scan
# --------------------------------------------------------------------------- #
#: The files the scan reads: C and C++ sources and headers, and Arduino sketches.
SOURCE_SUFFIXES = (".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx", ".ino")
#: A word before a name that still makes it a call (``return esp_ble_tx_power_set(``).
_CALL_WORDS = frozenset({"return", "else", "do", "case"})


class Call(NamedTuple):
    file: str
    line: int
    name: str
    dbm: float | None       # the literal level, or None: the level is not a literal
    said: str               # ``main/main.c:42 esp_ble_tx_power_set(..., ESP_PWR_LVL_P9)``


def blank(text: str) -> str:
    """``text`` with every comment and string or character literal blanked to
    spaces, newlines kept, so a name in a comment or a log message is no call and
    each line number stays the source's."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            for k in range(i + 1, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _declared(text: str, at: int) -> bool:
    """Whether the name at ``at`` is declared or defined there, not used: the token
    before it is a type's word (``esp_err_t esp_ble_tx_power_set(``), on a line that
    is not a ``#define``."""
    j = at - 1
    while j >= 0 and text[j] in " \t\r\n*&":
        j -= 1
    if j < 0 or not (text[j].isalnum() or text[j] == "_"):
        return False
    k = j
    while k >= 0 and (text[k].isalnum() or text[k] == "_"):
        k -= 1
    if text[k + 1:j + 1] in _CALL_WORDS:
        return False
    line = text[text.rfind("\n", 0, at) + 1:at]
    return re.match(r"\s*#\s*define\b", line) is None


def _arguments(text: str, open_at: int) -> list[str] | None:
    """The top-level arguments of the call whose ``(`` is at ``open_at``, or
    ``None`` when it is not closed."""
    depth, start, args = 0, open_at + 1, []
    for i in range(open_at, len(text)):
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append(text[start:i])
                return [a.strip() for a in args]
        elif c == "," and depth == 1:
            args.append(text[start:i])
            start = i + 1
    return None


_INT = re.compile(r"^(?:\(\s*\w+\s*\)\s*)?\(?\s*([+-]?\d+)\s*\)?$")
_NAME = re.compile(r"^\(?\s*([A-Za-z_]\w*)\s*\)?$")


def _literal(arg: str, how: str) -> float | None:
    if how == "esp":
        m = _NAME.match(arg)
        return float(ESP_LEVELS[m.group(1)]) if m and m.group(1) in ESP_LEVELS else None
    m = _INT.match(arg)
    return float(int(m.group(1))) if m else None


def scan(file: str, text: str, profile: Profile) -> list[Call]:
    """Every use of ``profile``'s setters in one source: a call, with its literal
    level when it has one, or a bare name (an address taken, a macro alias), which
    is never a literal."""
    if not profile.setters:
        return []
    code = blank(text)
    found = []
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, profile.setters)) + r")\b")
    for m in pattern.finditer(code):
        name = m.group(1)
        if _declared(code, m.start()):
            continue
        line = code.count("\n", 0, m.start()) + 1
        rest = re.match(r"\s*\(", code[m.end():])
        dbm, shown = None, f"{name} (named, not called)"
        if rest:
            args = _arguments(code, m.end() + rest.end() - 1)
            at = profile.setters[name]
            if args is not None and at is not None and at < len(args):
                dbm = _literal(args[at], profile.literal)
                shown = f"{name}(... {' '.join(args[at].split())[:40]})"
            else:
                shown = f"{name}(...)"
        found.append(Call(file, line, name, dbm, f"{file}:{line} {shown}"))
    return found


# --------------------------------------------------------------------------- #
# the judgement
# --------------------------------------------------------------------------- #
class Judged(NamedTuple):
    reading: Reading
    #: The highest setting the firmware certainly reaches: the setting, or a literal
    #: a setter sets above it.
    floor: float
    #: The judged value: the floor, or the ceiling when something can raise the
    #: power; ``None`` when the ceiling cannot be known.
    value: float | None
    said: str


def dbm(x: float) -> str:
    return f"{x:+g} dBm" if x else "0 dBm"


def judge(reading: Reading, calls: list[Call]) -> Judged:
    """The highest setting ``reading``'s image can reach, given the setter
    ``calls`` found in the repository's sources (the profile's own setters only)."""
    said = [f"{reading.setting_said}, {dbm(reading.setting)}"]
    floor = reading.setting
    literal = [c for c in calls if c.dbm is not None]
    if literal:
        top = max(literal, key=lambda c: c.dbm)
        level = top.dbm if reading.ceiling is None else min(top.dbm, reading.ceiling)
        if level > floor:
            floor = level
            said.append(f"{top.said} sets {dbm(level)}")
    raised = list(reading.raisers) + [c.said for c in calls if c.dbm is None]
    value: float | None = floor
    if reading.ceiling is not None:
        said.append(f"ceiling {dbm(reading.ceiling)}")
    if raised:
        what = raised[0] + (f" and {len(raised) - 1} more" if len(raised) > 1 else "")
        if reading.fem:
            value = None
            said.append(f"{what} can raise it, and a front-end module "
                        f"(CONFIG_MPSL_FEM=y) adds gain: the ceiling is unknown")
        elif reading.ceiling is None:
            value = None
            said.append(f"{what} can raise it, and the config lists no ceiling")
        else:
            value = max(floor, reading.ceiling)
            said.append(f"{what} can raise it to the ceiling: judged at {dbm(value)}")
    else:
        said.append("no run-time path")
    where = f", {reading.target}" if reading.target else ""
    return Judged(reading, floor, value,
                  f"{reading.file} ({reading.profile.name}{where}): {'; '.join(said)}")


def sources(repo: str, listing) -> dict[str, str]:
    """``{rel: text}`` of each C or C++ file under ``repo`` that the replay copies
    (``listing`` is ``_replay.listing``), a listed directory (a submodule) walked by
    the same rule and a symlink never followed, as the copy does."""
    out: dict[str, str] = {}

    def walk(root: str, prefix: str) -> None:
        for rel in listing(root):
            path = os.path.join(root, *rel.split("/"))
            if os.path.islink(path):
                continue
            if os.path.isdir(path):
                walk(path, prefix + rel + "/")
            elif rel.endswith(SOURCE_SUFFIXES) and os.path.isfile(path):
                with open(path, "rb") as fh:
                    out[prefix + rel] = fh.read().decode("utf-8", "replace")

    walk(repo, "")
    return out


def configs(result: dict) -> dict[str, str]:
    """``{rel: text}`` of each Kconfig output the replay returned: each ``.config``
    and ``sdkconfig`` the job wrote, and each config an ESP-IDF build record names
    (``_replay.BUILD_RECORDS``)."""
    found = {rel: data for rel, data in result["files"].items()
             if rel.rsplit("/", 1)[-1] in (".config", "sdkconfig")}
    for rel, data in (result.get("configs") or {}).items():
        found.setdefault(rel, data)
    return {rel: data.decode("utf-8", "replace") for rel, data in sorted(found.items())}
