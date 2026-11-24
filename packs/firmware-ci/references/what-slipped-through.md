# What slipped through

firmware-ci was written from four faults in a battery handheld's firmware. Each one
reached a release with every other check green. The stories are generic: no design
of that device came with the pack.

## The CI had never built the firmware

The workflow's build step named an extra module in a way the firmware's build
system did not read. The board in the config tree was then not found, and the build
stopped at `Invalid BOARD; see above.` before it compiled anything. The job was red
on GitHub for many revisions, and nobody read it.

`fw.build` replays the CI's own job, in its own container, on the working tree. The
verdict is on the tree you are about to commit, through CI's own steps and flags, so
the local build and CI's cannot drift apart. GitHub's own run conclusion was
rejected: it judges a pushed commit, and the red run in this story existed.

A second fault was found while the first pack was written: a wrong config path in
ZMK is a CMake warning (`Unable to locate ZMK config at:`), and the build exits 0 on
ZMK's default keymap. `fw.build` fails on that line.

## A failed build that read green

A build step piped its output into `tee` under GitHub's default shell, which has no
pipefail. A failed compile exited 0, the job went green, and the upload step shipped
the image the last good build had left. `fw.ci_job` fails such a step before any
container starts. (Measured on the pack's own app: a compile error in `main.c` under
`idf.py build 2>&1 | tee build.log` read success under act's `sh -e`.)

## The flash layout nobody checked

A hand-written board definition gave the app a flash start that the bootloader on
the device did not use. It built clean, because the linker compares the image with
the build's own layout, which was the wrong file. `fw.image_fits` compares the bytes
the image writes with a region from a cited record (the bootloader's documentation,
the datasheet, the partition table) and never with the build's own files. ESP-IDF
only warns when one OTA slot is too small; the gate fails it.

## Deep sleep with no wake source

Deep sleep was turned on, and the key scanner had no `wakeup-source`. The board would
sleep, and nothing would wake it until the cell ran flat. The build passed.
`fw.sleep_wake` reads the image: an ESP-IDF deep-sleep entry needs a deep-sleep wake
enable linked, and a ZMK build with deep sleep needs `wakeup-source` on each scanned
kscan.

## A transmit setting nobody compared

The firmware's Bluetooth transmit setting was raised, and nothing compared the new
setting with the setting the radio module's grant was tested at. The claim meant to
guard it compared the setting with the output the grant lists, which is what the
tested setting measured after the module's losses. Those are two quantities, so the
check could neither show nor rule out the fault. The build passed, and nothing on the
board changed.

`fw.tx_power` reads the setting the build resolved and judges it against the claim's
limit, the setting the grant was tested at. A run-time path that can raise the power
is judged at the highest setting Kconfig offered the SoC.

## What the design review caught before this pack shipped

- A build that the machine stopped (the OOM killer under `FAILED: ….obj`) read as a
  design fail. Tool-fault lines are now checked first, and errored.
- A rule that failed every image whose initial stack pointer sits one past the end
  of RAM, as STM32Cube's `_estack` does. The top of each RAM bank is now in.
- A soft-off rule that failed a ZMK design whose wake is the reset button, which
  ZMK documents as sound. Only a listed wake device without `wakeup-source` fails.
- A copy step that listed nothing when the repository was git-ignored (the known-bad
  copies under `.nopekit/out/`) or outside a work tree (an installed wheel). Every
  control read errored. The copy now walks such a tree.
- An act version check that errored, so the claim read as a crash. Another version
  now reads Skipped and names the install line.
