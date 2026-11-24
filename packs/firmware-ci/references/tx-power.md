# The transmit setting

`fw.tx_power` judges the highest Bluetooth LE transmit setting an image can reach
against the setting the radio module's grant was tested at. Both are settings, in
dBm. This file holds the rule tables and their sources.

## What the limit is

47 CFR 15.204(c)(2) has an intentional radiator tested "at its maximum available
output power level". A module's grant was tested at one setting. The output that
the grant lists is what that setting measured, after the module's matching losses.
A setting and a measured output are two quantities, so a claim that compares them
can neither show nor rule out the fault.

State the claim tagged `ble-tx-power` from the module's test report or its
integration manual: the highest setting the grant covers. With NCS 2.2 or later and
a front-end module, the setting is antenna-referenced: say so in the statement.

## Where the setting comes from

The `.config` or `sdkconfig` the replayed build resolved, in CI's own container:

- every `.config` and `sdkconfig` the job wrote into its workspace;
- the sdkconfig that ESP-IDF's build record, `build/project_description.json`, names
  in `config_file`. ESP-IDF rewrites an sdkconfig only when its configure step
  changes it (measured with espressif/idf v5.5.6), so a tracked sdkconfig that is
  current is not "written". The record says which file the build used.

Under sysbuild each image writes its own `.config`. A host-only image (nRF5340's
application core) names no setting; the network core's image holds the controller.

## The profiles

| Profile | Found by | Setting | Ceiling | Run-time paths |
|---|---|---|---|---|
| Zephyr's LL controller (Zephyr, ZMK) | `CONFIG_BT_LL_SW_SPLIT=y` | the chosen `CONFIG_BT_CTLR_TX_PWR_*` option | the highest option listed | `CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL`, `CONFIG_BT_CTLR_LE_POWER_CONTROL` |
| NCS's SoftDevice Controller | `CONFIG_BT_LL_SOFTDEVICE=y` | `CONFIG_BT_CTLR_TX_PWR_ANTENNA` when not 0, else the chosen option | the highest option listed; none when the antenna value hides the choice | the two above; `sdc_default_tx_power_set`, `sdc_hci_cmd_vs_zephyr_write_tx_power`, `sdc_hci_cmd_le_set_ext_adv_params` (and `_v2`) |
| ESP-IDF's BLE controller (ESP32-C3, ESP32-S3) | `CONFIG_BT_ENABLED=y` and a `CONFIG_BT_CTRL_DFT_TX_POWER_LEVEL_*` option | the chosen option (default `P9`, +9 dBm) | the highest option listed (`P20`, +20 dBm) | `esp_ble_tx_power_set`, `esp_ble_tx_power_set_enhanced`, `esp_ble_gap_ext_adv_set_params` (Bluedroid), `ble_gap_ext_adv_configure` (NimBLE) |

Kconfig lists an option of a choice (chosen, or `# ... is not set`) only when its
`depends on` holds, so the listed options are the ones it offered that SoC: an
nRF52832 lists +4 dBm as its highest, an ESP32-S3 +20 dBm.

Sources, each read for this pack:

- Zephyr `subsys/bluetooth/controller/Kconfig` (v3.5.0 and v4.4.2): `choice
  BT_CTLR_TX_PWR`, `depends on BT_CTLR_TX_PWR_ANTENNA = 0 || BT_LL_SW_SPLIT`.
  `hci/hci.c` handles `BT_HCI_OP_VS_WRITE_TX_POWER_LEVEL` only under
  `CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL`. `ll_sw/ull_adv.c` puts an advertising
  set's requested power in the header's TX Power field and transmits at
  `RADIO_TXP_DEFAULT`. `host/adv.c` (v3.5.0) asks for no level in an advertising
  set's parameters (`BT_HCI_LE_ADV_TX_POWER_NO_PREF`).
- sdk-nrf v3.4.2 `subsys/bluetooth/controller/Kconfig` (`BT_LL_SOFTDEVICE` selects
  `BT_CTLR_LE_POWER_CONTROL_SUPPORT`), `radio_nrf5_txp.h` (the choice's level when
  the antenna value is 0, the antenna value otherwise) and `hci_driver.c` (hands it
  to `sdc_default_tx_power_set`). sdk-nrfxlib v3.4.2
  `softdevice_controller/include/sdc_hci_cmd_le.h`: `adv_tx_power` in
  `sdc_hci_cmd_le_set_ext_adv_params_t` (and `_v2_t`), and "The Controller shall
  choose a power level lower than or equal to the one specified by the Host".
  `softdevice_controller/CHANGELOG.rst`, NCS v2.1.0: with a front-end module "the
  transmit power is expressed as the power on the antenna", and "To achieve higher
  output powers on the antenna, the user must call the appropriate API".
- ESP-IDF v5.5.6 `components/bt/controller/esp32c3/Kconfig.in`, which
  `esp32s3/Kconfig.in` sources: `choice BT_CTRL_DFT_TX_POWER_LEVEL`, default `P9`,
  options `N24` to `P20`. `components/bt/include/esp32c3/include/esp_bt.h`: the
  setters and `esp_power_level_t` (`ESP_PWR_LVL_P21` is deprecated, the same index
  as `P20`). `components/bt/host/bluedroid/api/include/api/esp_gap_ble_api.h`: an
  advertising set's `tx_power` "priority is higher than `esp_ble_tx_power_set()`,
  `esp_ble_tx_power_set_enhanced()`, and menuconfig default TX power", on
  ESP32-C3/ESP32-S3 rounded down to a 3 dB step; `EXT_ADV_TX_PWR_NO_PREFERENCE` is
  127. NimBLE's `nimble/host/src/ble_gap.c` copies `params->tx_power` into the
  command.
- 47 CFR 15.204(c)(2), https://www.law.cornell.edu/cfr/text/47/15.204

## The ceiling rule

| What the image holds | Judged at |
|---|---|
| no run-time path | the setting |
| a setter with a literal level | the higher of the setting and that level (the ceiling at most; with a front-end module, the level as asked for) |
| a Kconfig path, or a setter whose level is not a literal | the ceiling |
| a path, and no ceiling listed or a front-end module | the setting if it fails; undetermined otherwise |

The ceiling is the maximum the firmware makes available, which is what a grant was
tested at. A design whose grant covers the ceiling passes.

## The setter scan

The pack's own, and model-only: a text scan of the C and C++ files the replay
copies, with comments and string literals blanked. A declaration is not a call: a
type word just before the name, on its own line, on a line that is not a
preprocessor directive. Every other use counts, a call under `#if` included. A
name used without a call (an address taken, a macro alias) is a path with no
literal. The scan only moves a verdict up: to a literal, to the ceiling, or to
undetermined. It does not see a setter reached through a library outside the
repository, a module, or an HCI command an app builds by hand or an external host
sends; it counts a call the build does not compile.

An advertising set's power is a member of the struct its call is handed
(`tx_power`; the SoftDevice Controller's `adv_tx_power`). The scan reads every
write to a member of that name in the sources, whatever struct holds it:

| What the sources hold | The call asks for |
|---|---|
| literal writes only (`.tx_power = 9`, `params.tx_power = 0x09`) | the highest of them and 0 dBm, the level of a struct cleared and never written |
| 127 or `EXT_ADV_TX_PWR_NO_PREFERENCE` | nothing: the controller keeps its own setting |
| a write that is not a literal (a variable, `+=`, `++`), the member's address taken, or a struct of the call's type initialized by position | no literal: the call is a path, judged at the ceiling |

A struct filled by copying bytes, or initialized by position through a type alias,
is not read.

## Not read yet

- ESP-IDF's BT_LE family (`CONFIG_BT_LE_DFT_TX_POWER_LEVEL_*`: ESP32-C2, C5, C6,
  C61, H2) and the ESP32's BTDM controller: skipped by name until each is checked
  against its own Kconfig and setters.
- Wi-Fi: a build can only lower ESP-IDF's ceiling. LoRa and Thread: set at run time.
- A stack with no Kconfig (Arduino, the Pico SDK, QMK): a conducted measurement.
