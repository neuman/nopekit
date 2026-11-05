# What slipped through

firmware-zmk was written for a handheld keyboard deck: two boards made
by a generator, ZMK firmware on an E73-2G4M08S1C (nRF52840) module. Three faults
reached its v0.36 with every other check green.

## The CI had never built the firmware

The workflow's build step passed a module as `-DZEPHYR_EXTRA_MODULES`. ZMK v0.3.0
sets that variable itself from `ZMK_EXTRA_MODULES` (`app/CMakeLists.txt:6`), and
the build stopped at `Invalid BOARD; see above.` in 1.2 s. The job was red and
nobody read it. With `-DZMK_EXTRA_MODULES` it builds.

`zmk.build` runs the CI step's own argv, read from the workflow file, so the
local verdict and CI's cannot drift apart. `zmk.ci_step` reads the step first, and
the setup, the image and the manifest around it: a first version read the `west
build` line alone, and passed a CI with no `west init`, with no container, or with
a manifest that imported no Zephyr, each of which cannot build.

A second fault was found while the gate was written: a wrong ZMK_CONFIG path is a
CMake warning, "Unable to locate ZMK config", and the build exits 0 with ZMK's
default keymap. `zmk.build` reads every `Using keymap file:` line and fails one
outside ZMK_CONFIG.

## Deep sleep with no wakeup source

The firmware set `CONFIG_ZMK_SLEEP=y`. The board's kscan had no `wakeup-source`.
ZMK's docs say deep sleep needs it (`low-power-states.md:27`). The board would
sleep and no key would wake it. The build passed.

`zmk.wakeup_source` reads the build's `edt.pickle` with Zephyr's own `edtlib` and
fails each kscan ZMK scans that is not a wakeup source. edtlib lists a boolean
property that the devicetree leaves out as false, with no warning.

## The radio past its module's grant

The firmware set `CONFIG_BT_CTLR_TX_PWR_PLUS_8=y`, +8 dBm. The module's FCC grant,
2ALPH-E73, lists 0.00243 W conducted (3.86 dBm) on fccid.io.

`zmk.tx_power` reads Kconfig's `CONFIG_BT_CTLR_TX_PWR_DBM` and judges it against
the claim tagged `tx-power`. The limit is a claim, because only a person can say
which grant covers which module.

## What the first version of the pack let through

Review found these before the pack was merged:

- A compiler that the OOM killer stopped printed `FAILED: ...obj`, and read as a
  design fail on known-good upstream ZMK. Now `Killed signal terminated program`
  and the other lines of a machine fault are errored.
- The build read the board's `.dtsi` and `.conf` inside the container, and the
  gates' key held neither. A fix to either recorded two outcomes for one key. Now
  every file under ZMK_CONFIG and each module path is read before the build.
- With no deep sleep built, `zmk.wakeup_source` passed. A claim tagged
  `deep-sleep` read Checked on firmware that did not sleep. Now it reads Skipped.
- A timeout killed the docker client, and the container went on building. Now the
  gate kills the container by name.

## Why the build runs in a container

ZMK's build needs the Zephyr SDK, west and a 2 GB workspace. The pack runs ZMK's
own image, pinned by digest, so the compiler and Zephyr are the ones CI uses (at
that digest). The wrapper mounts with `--mount`: with `-v`, a missing source
becomes an empty directory owned by root, and docker exits 0.
