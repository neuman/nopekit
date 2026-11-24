# Wake rules

`fw.sleep_wake` keys one rule table on what the image shows. A symbol shows that the
image arms a wake source somewhere, not that every path arms it before the sleep
call, and not that the board routed the button to a pin that can wake. A wake on a
real board is a physical claim.

## ESP-IDF family

Any ELF that links one of these entries takes the rule: `esp_deep_sleep_start`,
`esp_deep_sleep_try_to_start`, `esp_deep_sleep_try`, `esp_deep_sleep`.

| Linked | Outcome |
|---|---|
| an entry and a deep-sleep enable | pass |
| an entry, and no deep-sleep enable | fail: the chip sleeps until it is reset |
| an entry, no deep-sleep enable, `deep_sleep_wakes_by_reset` stated | skip: a physical claim settles it |
| the timer is the only deep-sleep enable, and `esp_pm_configure` is linked | skip: power management arms the timer itself |
| an enable on neither list | errored: a newer ESP-IDF |
| a DWARF producer with `-flto` | errored: inlining can remove an enable's symbol |
| no producer string, where the rule would fail | errored: LTO cannot be ruled out |

Deep-sleep enables (ESP-IDF v5.5 `esp_sleep.h` and the Sleep Modes guide):
`esp_sleep_enable_timer_wakeup`, `_ext0_wakeup`, `_ext1_wakeup`, `_ext1_wakeup_io`,
`_touchpad_wakeup`, `_ulp_wakeup`, `esp_sleep_enable_gpio_wakeup_on_hp_periph_powerdown`,
`esp_deep_sleep_enable_gpio_wakeup`; `esp_deep_sleep(us)` arms the timer itself.

Light-sleep-only enables, which never count: `esp_sleep_enable_uart_wakeup`,
`esp_sleep_enable_gpio_wakeup`, `_bt_wakeup`, `_wifi_wakeup`, `_wifi_beacon_wakeup`,
`_usb_wakeup`. The guide: with no wake source the chip stays in sleep "until external
reset is applied".

The same IDF functions serve ESP-IDF, Arduino-ESP32, PlatformIO espressif32 and
Zephyr on ESP32.

## ZMK

A `.config` with `CONFIG_ZMK_` symbols beside the ELF takes the rule, read from the
`zephyr.dts` beside it with Zephyr's own `dtlib` (vendored at v4.4.2).

- `CONFIG_ZMK_SLEEP=y`: each kscan ZMK scans must have `wakeup-source`. The kscans are
  each `zmk,physical-layout` node's `kscan`, else `/chosen` `zmk,kscan`. A kscan
  without it fails, by name. A composite or sideband kscan is errored: the gate does
  not follow what it wraps. ZMK's low-power-states page: "Using deep sleep requires
  kscan nodes to have the wakeup-source property".
- `CONFIG_ZMK_PM_SOFT_OFF=y`: each device a `zmk,soft-off-wakeup-sources` node lists
  must have `wakeup-source`, else fail. With no such list, soft off wakes by the
  reset button, which ZMK documents as sound: the gate skips when soft off is the
  only sleep built.
- Neither built: skip, there is nothing to wake from.

## Every other stack: skipped by name

| Evidence | Reason |
|---|---|
| a Zephyr `.config` with `CONFIG_POWEROFF=y` or `sys_poweroff` | the wake is set at run time |
| `HAL_PWR_EnterSTANDBYMode` and the like | STM32 HAL or LL: LL's wake enables are static inline and leave no symbol |
| `sleep_goto_dormant_until_pin` and the like | Pico SDK dormant: the wake is chosen at run time |
| `nrf_power_system_off`, `sd_power_system_off` | nRF5 SDK or bare-metal SYSTEMOFF |
| `keyboard_task`, `matrix_scan_kb` | QMK: no rule |
| none of these | nothing to wake from; a claim tagged `deep-sleep` is not Checked by the absence of sleep |

*Rejected:* an STM32 HAL symbol rule (LL enables leave no symbol, and CubeMX mixes
HAL and LL, so it would fail sound designs); a soft-off rule that requires a wake
list (ZMK names the reset button as a sound soft-off wake).
