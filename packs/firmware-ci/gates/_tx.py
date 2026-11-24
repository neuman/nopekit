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
that literal, capped at the ceiling as the controller caps it. With a front-end
module (NCS ``CONFIG_MPSL_FEM``) the level is antenna-referenced and the SoC's
options do not cap it, so a literal is judged as it is asked for. Where the
ceiling cannot be known (an antenna-referenced NCS setting hides the choice; a
front-end module adds gain), a path beside a setting that alone passes reads
undetermined, and names the conducted measurement that settles it.

**The setter scan is the pack's own** (model-only, and documented so): a text
scan of the C and C++ files the next commit holds, comments and string literals
blanked. It can only make a verdict more conservative: it moves the judged value
up, to a literal or to the ceiling, or to undetermined. An advertising set's
power is a member of the struct its call is handed (:data:`EXT_ADV`): the scan
reads every write to that member in the sources (:func:`scan_sources`). A call
reached by indirection (a library outside the repository, a module, an HCI
command an external host sends) is not seen; a call the build does not compile
still counts.
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
    #: Setter function -> where its level is: the index of an argument, a
    #: :class:`Member` of the struct the call is handed, or ``None`` (never a literal).
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


class Member(NamedTuple):
    """A level that a call reads from a member of the struct it is handed, not
    from an argument: the member's name, and the struct types whose initializer
    can set it (``struct`` optional before each)."""
    name: str
    types: tuple


#: An advertising set's requested power, the HCI command's Advertising_TX_Power,
#: which the controller applies to that set at or below the request.
#:
#: - ESP-IDF v5.5.6 Bluedroid, ``esp_ble_gap_ext_adv_set_params``: the member's
#:   comment in components/bt/host/bluedroid/api/include/api/esp_gap_ble_api.h says
#:   "For this advertising set, priority is higher than esp_ble_tx_power_set(),
#:   esp_ble_tx_power_set_enhanced(), and menuconfig default TX power
#:   (CONFIG_BT_CTRL_DFT_TX_POWER_LEVEL)", and names ESP32-C3/ESP32-S3, which round
#:   the request down to a 3 dB step.
#: - ESP-IDF v5.5.6 NimBLE, ``ble_gap_ext_adv_configure``: nimble/host/src/ble_gap.c
#:   copies ``params->tx_power`` into the command.
#:
#: Both were read in the image the baseline pins. *Rejected:* filing them under
#: indirection (as F2 first did: each is a call in the repository's own sources,
#: and a +20 dBm set beside a +6 dBm default passed); judging every such call at
#: the ceiling (every ESP-IDF advertising-set example states its member as a
#: literal, mostly 127, so each would fail at +20 dBm for nothing).
EXT_ADV = Member("tx_power", ("esp_ble_gap_ext_adv_params_t", "ble_gap_ext_adv_params"))
#: The same command through the SoftDevice Controller's own API (sdk-nrfxlib v3.4.2
#: softdevice_controller/include/sdc_hci_cmd_le.h, v1 and v2): "The Controller
#: shall choose a power level lower than or equal to the one specified by the
#: Host." Whether it can exceed the default was not measured, so the request is
#: judged. Zephyr's own host asks for no level (subsys/bluetooth/host/adv.c sets
#: ``BT_HCI_LE_ADV_TX_POWER_NO_PREF``), so only a call by hand reaches it.
SDC_EXT_ADV = Member("adv_tx_power", ("sdc_hci_cmd_le_set_ext_adv_params_t",
                                      "sdc_hci_cmd_le_set_ext_adv_params_v2_t"))
#: Advertising_TX_Power 127: the host asks for no level, and the controller keeps
#: its own (ESP-IDF's ``EXT_ADV_TX_PWR_NO_PREFERENCE``, esp_gap_ble_api.h; NimBLE's
#: periodic advertising walkthrough at v5.5.6: "tells the controller to use its
#: default TX power"). It moves nothing.
NO_PREFERENCE = 127
_NO_PREFERENCE_NAMES = ("EXT_ADV_TX_PWR_NO_PREFERENCE",)


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
#:   An advertising set's power reaches it only by a call by hand (:data:`SDC_EXT_ADV`).
#: - **esp-bt-ctrl**, ESP-IDF's BLE controller on ESP32-C3 and ESP32-S3:
#:   ``choice BT_CTRL_DFT_TX_POWER_LEVEL`` (components/bt/controller/esp32c3/
#:   Kconfig.in at v5.5.6, which esp32s3/Kconfig.in sources), default ``P9``, +9 dBm.
#:   No Kconfig option raises it; ``esp_ble_tx_power_set`` and
#:   ``esp_ble_tx_power_set_enhanced`` do (esp_bt.h), and so does an advertising
#:   set's power (:data:`EXT_ADV`), with Bluedroid or NimBLE.
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
            {"sdc_default_tx_power_set": 0, "sdc_hci_cmd_vs_zephyr_write_tx_power": None,
             "sdc_hci_cmd_le_set_ext_adv_params": SDC_EXT_ADV,
             "sdc_hci_cmd_le_set_ext_adv_params_v2": SDC_EXT_ADV},
            "int"),
    Profile("esp-bt-ctrl", "ESP-IDF's BLE controller", _esp_dbm, (),
            {"esp_ble_tx_power_set": 1, "esp_ble_tx_power_set_enhanced": 2,
             "esp_ble_gap_ext_adv_set_params": EXT_ADV, "ble_gap_ext_adv_configure": EXT_ADV},
            "esp"),
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
#: A word before a name that still makes it a use: a keyword that starts or joins
#: an expression, never a type (``return esp_ble_tx_power_set(``; iso646.h's and
#: C++'s ``if (on and esp_ble_tx_power_set(``). *Rejected:* reading any other word
#: as an operator (a type is a word too, and a declaration is the one form the scan
#: passes over).
_CALL_WORDS = frozenset({"return", "else", "do", "case",
                         "and", "or", "not", "xor", "bitand", "bitor", "compl"})


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
    just before it, on the name's own line, is a word that can name a type
    (``esp_err_t esp_ble_tx_power_set(``), and that line is not a preprocessor
    directive.

    The word must be on the name's line. F2's scan walked back over newlines, so
    it read the last word of a directive as a type: after ``#if CONFIG_LONG_RANGE``,
    ``#ifdef X`` or ``#endif``, the call on the next line was passed over as a
    declaration, and a +20 dBm setter read "no run-time path". A declaration whose
    type is alone on the line before now counts as a use: its level is a
    parameter's name, never a literal, so it can only move the verdict up. A ``*``
    or ``&`` before the name makes it a use too (``on && esp_ble_tx_power_set(``
    was passed over the same way): every setter here returns a status, never a
    pointer or a reference, so no declaration of one has either."""
    j = at - 1
    while j >= 0 and text[j] in " \t":
        j -= 1
    if j < 0 or not (text[j].isalnum() or text[j] == "_"):
        return False
    k = j
    while k >= 0 and (text[k].isalnum() or text[k] == "_"):
        k -= 1
    if text[k + 1:j + 1] in _CALL_WORDS:
        return False
    return not _directive(text, at)


def _directive(text: str, at: int) -> bool:
    """Whether ``at`` is on a preprocessor directive's logical line: one that
    starts with ``#``, or one that a backslash continues from such a line."""
    start = text.rfind("\n", 0, at) + 1
    while start > 0:
        end = start - 1                       # the newline that ends the line before
        if end > 0 and text[end - 1] == "\r":
            end -= 1
        if end > 0 and text[end - 1] == "\\":
            start = text.rfind("\n", 0, end) + 1
        else:
            break
    return text[start:at].lstrip().startswith("#")


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


def scan(file: str, text: str, profile: Profile, levels: dict | None = None) -> list[Call]:
    """Every use of ``profile``'s setters in one source: a call, with its literal
    level when it has one, or a bare name (an address taken, a macro alias), which
    is never a literal. A call whose level is a :class:`Member` takes it from
    ``levels`` (``{member name: Level}``, :func:`scan_sources` reads them); with
    none, its level is no literal."""
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
            if isinstance(at, Member):
                level = (levels or {}).get(at.name)
                if level is None:
                    shown = f"{name}(...), its {at.name} not read"
                else:
                    dbm, shown = level.dbm, f"{name}(...), {level.said}"
            elif args is not None and at is not None and at < len(args):
                dbm = _literal(args[at], profile.literal)
                shown = f"{name}(... {' '.join(args[at].split())[:40]})"
            else:
                shown = f"{name}(...)"
        found.append(Call(file, line, name, dbm, f"{file}:{line} {shown}"))
    return found


class Level(NamedTuple):
    """The level a :class:`Member` asks for, read from the whole repository."""
    #: The highest literal written to the member, or ``None``: a write the scan
    #: cannot read as a literal.
    dbm: float | None
    said: str               # ``tx_power = 20 at main/adv.c:12``


def scan_sources(code: dict[str, str], profile: Profile) -> list[Call]:
    """:func:`scan` over every source in ``code`` (``{rel: text}``, from
    :func:`sources`), each :class:`Member` level read once from all of them."""
    blanked = {rel: blank(code[rel]) for rel in sorted(code)}
    members = {at for at in profile.setters.values() if isinstance(at, Member)}
    levels = {m.name: member_level(blanked, m) for m in sorted(members)}
    return [c for rel in blanked for c in scan(rel, code[rel], profile, levels)]


_MEMBER_INT = re.compile(r"^(?:\(\s*\w+\s*\)\s*)?\(?\s*([+-]?)\s*(0[xX][0-9a-fA-F]+|\d+)"
                         r"\s*\)?$")
#: A prefix ``++``, ``--`` or ``&`` before a member access, up to the member's name.
_PREFIXED = (r"(\+\+|--|&)\s*[A-Za-z_]\w*(?:\s*(?:\.|->)\s*[A-Za-z_]\w*|\s*\[[^\]\n]*\])*"
             r"\s*(?:\.|->)\s*")


def _member_literal(value: str) -> float | None:
    """A member's value as C writes a literal: decimal, octal or hex, signed, cast
    or in parentheses, or the no-preference name; ``None`` otherwise."""
    if re.match(r"^\(?\s*(" + "|".join(_NO_PREFERENCE_NAMES) + r")\s*\)?$", value):
        return float(NO_PREFERENCE)
    m = _MEMBER_INT.match(value)
    if m is None:
        return None
    digits = m.group(2)
    base = 16 if digits[:2] in ("0x", "0X") else 8 if len(digits) > 1 and digits[0] == "0" \
        else 10
    try:
        number = int(digits, base)
    except ValueError:                        # ``09``: no C literal
        return None
    return float(-number if m.group(1) == "-" else number)


def _expression(text: str, start: int) -> str:
    """The expression that starts at ``start``: up to a top-level ``,`` or ``;``,
    or the bracket that closes the one it sits in."""
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return text[start:i].strip()
            depth -= 1
        elif c in ",;" and depth == 0:
            return text[start:i].strip()
    return text[start:].strip()


def _items(body: str) -> list[str]:
    """The top-level, comma-separated items of an initializer's body."""
    items, depth, start = [], 0, 0
    for i, c in enumerate(body):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            items.append(body[start:i])
            start = i + 1
    items.append(body[start:])
    return [s.strip() for s in items if s.strip()]


def _by_position(body: str) -> bool:
    """Whether an initializer's body (an array's, element by element) sets a
    member by its position rather than by its name. ``{0}`` and ``{}`` clear the
    struct, and set no member by position."""
    items = _items(body)
    inner = [re.sub(r"^\[[^\]]*\]\s*=\s*", "", s) for s in items]
    if inner and all(s.startswith("{") and s.endswith("}") for s in inner):
        return any(_by_position(s[1:-1]) for s in inner)       # an array of structs
    return items not in ([], ["0"]) and not all(s.startswith(".") for s in items)


def member_level(blanked: dict[str, str], member: Member) -> Level:
    """The highest level any write to ``member`` asks for in the repository's
    sources (``{rel: text}``, comments and strings blanked).

    Every write to a member of that name counts, whatever struct holds it: the
    scan cannot tell structs apart, and a write it reads that was not the call's
    only moves the verdict up. 127 asks for no level. A struct cleared and never
    written asks for 0 dBm, so 0 dBm always counts. The level is not a literal
    when a write is not one (a variable, an expression, ``+=``, ``++``), when the
    member's address is taken, or when a struct of the call's types is initialized
    by position, which sets the member without naming it."""
    best, said = 0.0, f"{member.name} 0 dBm, as a cleared struct asks"
    name = re.escape(member.name)
    write = re.compile(r"(?:\.|->)\s*" + name + r"\b\s*(<<=|>>=|\+\+|--|[-+*/%&|^]?=(?!=))")
    address = re.compile(_PREFIXED + name + r"\b")
    types = "|".join(re.escape(t) for t in member.types)
    positional = re.compile(r"\b(?:struct\s+)?(" + types + r")\b\s*(?:\)\s*\{|[^;{}()=]*=\s*\{)")
    for rel, code in blanked.items():
        for m in write.finditer(code):
            if m.group(1) != "=":
                return Level(None, f"{member.name} {m.group(1)} at {_where(rel, code, m.start())}")
            value = _expression(code, m.end())
            v = _member_literal(value)
            shown = " ".join(value.split())[:40]
            if v is None:
                return Level(None, f"{member.name} = {shown} at {_where(rel, code, m.start())}")
            if v != NO_PREFERENCE and v > best:
                best, said = v, f"{member.name} = {shown} at {_where(rel, code, m.start())}"
        for m in address.finditer(code):
            op = m.group(1)
            before = code[:m.start()].rstrip()
            if op == "&" and before and (before[-1].isalnum() or before[-1] in "_)]&"):
                continue                      # a binary ``&``, not an address taken
            return Level(None, f"{member.name} {'address taken' if op == '&' else op} "
                               f"at {_where(rel, code, m.start())}")
        for m in positional.finditer(code):
            body = _braced(code, m.end() - 1)
            if body is None or _by_position(body):
                return Level(None, f"{m.group(1)} initialized by position at "
                                   f"{_where(rel, code, m.start())}, its {member.name} not named")
    return Level(best, said)


def _where(rel: str, code: str, pos: int) -> str:
    return f"{rel}:{code.count(chr(10), 0, pos) + 1}"


def _braced(text: str, open_at: int) -> str | None:
    """The body of the brace block whose ``{`` is at ``open_at``, or ``None`` when
    it is not closed."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] in "([{":
            depth += 1
        elif text[i] in ")]}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    return None


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
        # The SoC's options cap a literal, as the controller caps it, only with no
        # front-end module. With one, NCS's level is antenna-referenced and the
        # SoC's and the module's gains are split to reach it (sdk-nrfxlib v3.4.2
        # softdevice_controller/CHANGELOG.rst, NCS v2.1.0, KRKNWK-13714: "To achieve
        # higher output powers on the antenna, the user must call the appropriate
        # API"; Zephyr's BT_CTLR_TX_PWR_ANTENNA help). F2 capped it anyway, so
        # sdc_default_tx_power_set(20) beside a +8 dBm SoC read +8 and passed a
        # 10 dBm grant. *Rejected:* undetermined for a literal over the SoC's
        # ceiling (the setting is the request, and the request is known).
        capped = reading.ceiling is not None and not reading.fem
        level = min(top.dbm, reading.ceiling) if capped else top.dbm
        if level > floor:
            floor = level
            said.append(f"{top.said} sets {dbm(level)}")
    raised = list(reading.raisers) + [c.said for c in calls if c.dbm is None]
    value: float | None = floor
    if reading.ceiling is not None:
        said.append(f"ceiling {dbm(reading.ceiling)}" if not reading.fem else
                    f"the SoC's ceiling {dbm(reading.ceiling)}, before the front-end "
                    f"module's gain")
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
