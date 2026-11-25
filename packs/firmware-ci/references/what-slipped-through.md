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
- A transmit setter under `#if CONFIG_...`, `#ifdef` or after `#endif`, read as a
  declaration: the scan took the directive's last word for a type. A +20 dBm call
  read "no run-time path", and the gate passed at the +6 dBm default. The type word
  must now be on the name's own line, on a line that is not a directive.
- An advertising set's power, filed as indirection. ESP-IDF says it has priority
  over the default on ESP32-C3/S3, and the call is in the app's own sources. A set
  that asks for +20 dBm passed at +6 dBm. The scan now reads the set's member.
- A front-end module's literal capped at the SoC's options. With a module, NCS's
  level is antenna-referenced, and the SoC's options do not cap it.
  `sdc_default_tx_power_set(20)` read +8 dBm and passed a 10 dBm grant.

## What the holdout projects found

Four real firmware repositories, replayed by people who had not written the pack:

- A read-back that took the bytes of every image the job wrote, against a cap sized
  on the baseline. An NCS sysbuild wrote 284 MB of images, and a sound build read
  errored. Now only the images the gates name come back whole.
- An errored replay that kept no log, with a reason that quoted act's `Job
  failed`. Each person replayed the failure by hand. The log is now kept on every
  outcome, and the reason quotes the failing step's first cause line.
- A failed job's container, left running after each failed replay. act now runs
  with `--rm`, and the replay removes the copy's containers on every outcome.
- Four replays in one check where the pack promised one. The replay's lock asked
  whether `/tmp` exists, and `/tmp` moved between every two gates. The lock now
  asks nothing of a path by name.
- A matrix job that could not be replayed one entry at a time, an install over the
  runner image's empty apt lists with no named cause, and a fixed 1800 s timeout.
  `firmware_ci_matrix`, the named apt difference and `firmware_ci_timeout_s` answer
  them.
- A guard pass that said "no masking" over a build inside a Python script that
  discards docker's status. The pass now says it read the step's own lines, and
  names the script.
- A fixed 100 % limit beside a claim that stated headroom, which made the spine
  warn "one number in two places". The gate now reports the `flash-fit` claim's
  limit.
- An arduino-cli template that never made its `BINDIR` and was never replayed.

## What the review of those fixes found

- A timeout that wrote an empty log. The replay returned before it read act's
  output. The log now holds what act wrote up to the stop, and the error names the
  step that was still running.
- A pass that named the matrix entry the project asked for, not the one act ran.
  act ignores a `--matrix` key the job's matrix does not have. Such a key is now
  errored, and the pass names the entry act logged.
- Two `flash-fit` claims with different limits, which errored the gate with the
  spine's advice to a gate's author. The error now says first to keep the tag on
  the headroom claim.
- A replay that read `HOME` through `os.path.expanduser`, where its docstring said
  it read no environment variable. The action cache and the fallback lock are now
  under the uid's home in the user database.
