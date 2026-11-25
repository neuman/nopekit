# firmware-ci

Replay the **CI job that builds the firmware** with act, in the job's own containers,
and read four facts from what it writes: the build passed, the image fits its region,
an image that can enter deep sleep can wake, and the highest Bluetooth LE transmit
setting the image can reach is inside the setting its radio's grant was tested at.
The object judged is the image and the Kconfig output the named CI job writes into
its workspace when act replays it, on a copy of the files the next commit would hold.
Addresses and sizes are in bytes. Flash use is in % of the region that the bootloader
or the partition table leaves the app. RAM banks come from the part's datasheet. A
transmit setting is in dBm.

One mechanism serves every stack that builds in a GitHub Actions job on Linux:
Zephyr, NCS, ZMK, ESP-IDF, Arduino, PlatformIO, the Pico SDK, QMK and STM32 make or
CMake builds. ZMK is one wake profile, one design line and one CI template here. It
is not the pack.

## What the gates settle

| Gate | Tier | Claim tags | Basis | Known-bad control |
|---|---|---|---|---|
| `fw.ci_job` | 0 | `firmware-build`, `ci-build` | sourced: GitHub's documented shell and `continue-on-error` behaviour | the build piped into `tee` with no `shell:` key |
| `fw.build` | 1 | `firmware-build` | established: the toolchain's exit code in the job's own container, as act reports it | `main.c` calls a helper that no source defines |
| `fw.image_fits` | 1 | `flash-fit`, `memory-fit`, `ota-slot` | sourced: the bootloader's layout, the datasheet or the ESP partition table | `ota_1` cut to 0x20000, below the image |
| `fw.sleep_wake` | 1 | `deep-sleep`, `wakeup-source` | sourced: ESP-IDF's Sleep Modes guide, ZMK's low-power docs | the ext0 wake line removed |
| `fw.tx_power` | 1 | `ble-tx-power` | sourced: the stack's own Kconfig resolution, by rules cited to its Kconfig and sources | the TX level line removed, so ESP-IDF's default +9 dBm applies |

`fw.ci_job` is the replay's validity guard. It reads the workflow in about 0.03 s,
before a container starts. `fw.build` needs it, and the three readers need `fw.build`.
A gate whose prerequisite did not pass reads Skipped.

- **`fw.ci_job`** chooses the job (`firmware_ci_job`, else the one job that runs a
  build command it knows) and the build step (`firmware_ci_step`, else the one step
  that runs `west build`, `idf.py build`, `pio run`, `arduino-cli compile`, `qmk
  compile`, `make`, `cmake --build` or `ninja`). It reads every build command in that
  step, each with the commands after it: later on its line (after a `;`) and on the
  lines below. It **fails** a CI that can read green on a failed build:
  `continue-on-error: true` on the job or the step; the build followed by `|| true`,
  `|| :`, `|| exit 0` or `|| echo`; `set +e`, or a custom shell with no `-e`, then
  another command and no status check; the build before an `&&`, then another
  command (`-e` does not stop on a command before the last `&&`); a pipe after the
  build under a shell with no pipefail. A status check counts only when it is the
  next command and fails the step: `exit $?`, `[ $? -eq 0 ] || exit 1`, an `if` on
  `$?` that exits, `rc=$?` and a later `exit $rc`, or after a pipe the same on
  `${PIPESTATUS[0]}`; under `-e`, a bare test on it counts too. `echo "exited $?"`
  does not. An unspecified shell runs `bash -e {0}` (or `sh -e
  {0}`), which has no pipefail. `shell: bash` has it. Its pass says what it read:
  `no masking in the step's own lines`, and it names each repository file the
  step runs (`python3 tools/build.py`, `./build.sh`) as not read. A script can
  discard a command's status, and the guard does not read scripts. It is
  **errored**, never failed,
  on a shape the replay cannot run: a Windows, macOS or self-hosted runner with no
  container, docker inside the job (`espressif/esp-idf-ci-action`), a shell that is
  not bash or sh, a reusable workflow, a `${{ }}` in `if:` or `continue-on-error`, and
  a job or step it cannot choose.
- **`fw.build`** runs act v0.2.97 on that job: `--bind`, so the job writes into the
  copy; no docker socket in the jobs; no secrets, variables or caches; pinned runner
  images; `--pull=false`; `--rm`, and every job container of the copy is removed
  after the replay, whatever its outcome. The verdict is the build step's own
  result. It **fails** on a line from a closed list in a failed build step
  (`FAILED: ….obj`, `undefined reference to`, `region … overflowed`, `Invalid
  BOARD`, `devicetree error:`, a Kconfig abort), on such a line in a step that read
  success (masked), on ZMK's `Unable to locate ZMK config at:` warning, and on a
  `firmware_image` the job did not write. A step that read success and says that a
  part of it did not run in the replay (docker with no daemon, a script the copy
  does not hold) is errored, before the image is looked for. Its evidence,
  `log.txt`, holds each step's own output on every outcome the replay reached, an
  errored one too, and an errored reason quotes the failing step's first cause line
  (`CMake Error`, apt's `E:`, `error:`).
- **`fw.image_fits`** compares the bytes the image writes with a region from a cited
  record. It reports flash use in %, rounded up to 0.1 %. Its limit is the headroom
  the `flash-fit` claim states (`flash use <= 85 %`), else 100, the region's own
  bound. One byte past the region fails, whatever the claim says: the bytes decide
  that. A `flash-fit` claim in another quantity, unit or direction is errored. It
  **fails** with no limit on bytes in
  flash outside the app region, on a vector table that is not at the start the
  bootloader jumps to, and, where `ram` is stated, on an ELF segment (by its run
  address, `.bss` included) that starts in a stated bank and runs past its end, or
  that lies in the Arm SRAM region (0x20000000-0x3fffffff) outside every stated
  bank: firmware built for a bigger part than the one the map names. A segment
  that no stated region holds anywhere else is errored.
- **`fw.sleep_wake`** reads only what the image shows. ESP-IDF family: a deep-sleep
  entry with no deep-sleep wake enable linked **fails**; UART, GPIO, Wi-Fi, BT and USB
  wakes are light-sleep only and never count. ZMK: deep sleep needs `wakeup-source` on
  each scanned kscan, and each listed soft-off wake device needs it too. Every other
  stack skips by name, with its reason.
- **`fw.tx_power`** reads the `.config` (Zephyr, NCS, ZMK) or `sdkconfig` (ESP-IDF)
  that the replayed build resolved: every one the job wrote, and the sdkconfig that
  ESP-IDF's build record (`project_description.json`) names, which ESP-IDF leaves
  as it was when a tracked sdkconfig is current. Never a build directory on this
  machine, and never a second build. Three profiles, from a closed table: Zephyr's
  LL controller (`CONFIG_BT_CTLR_TX_PWR_*`), NCS's SoftDevice Controller (the same
  choice, or `CONFIG_BT_CTLR_TX_PWR_ANTENNA` when it is not 0) and ESP-IDF's BLE
  controller on ESP32-C3 and ESP32-S3 (`CONFIG_BT_CTRL_DFT_TX_POWER_LEVEL_*`, default
  +9 dBm). A run-time path moves the judged value to the **ceiling**, the highest
  option Kconfig offered the SoC: `CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL`, LE Power
  Control, or a setter call in the repository's C sources (`esp_ble_tx_power_set`,
  `esp_ble_tx_power_set_enhanced`, `sdc_default_tx_power_set`,
  `sdc_hci_cmd_vs_zephyr_write_tx_power`). A setter whose level is a literal is
  judged at that literal, capped at the ceiling; with a front-end module the level
  is antenna-referenced, and it is judged as asked for. An advertising set's power
  (`esp_ble_gap_ext_adv_set_params`, `ble_gap_ext_adv_configure`,
  `sdc_hci_cmd_le_set_ext_adv_params`) is the member of the struct the call is
  handed: the scan reads every write to that member in the sources, and judges the
  highest literal (127, no preference, moves nothing) or, when a write is not a
  literal, the ceiling. It **fails** when the judged setting is past the claim's
  limit. Where the ceiling cannot be known (an antenna-referenced NCS setting, a
  front-end module), a path beside a setting that alone passes is skipped as
  undetermined and names the conducted measurement. Any other stack skips by name:
  ESP-IDF's BT_LE family (ESP32-C2, C5, C6, C61, H2), the ESP32's BTDM controller, a
  host whose controller is in another image or chip, and a stack with no Kconfig.

A tool fault is errored, never a fail: a step before the build that fails, act or
docker stopping, a timeout (`firmware_ci_timeout_s`, default 1800 s), a compiler the
machine stopped (OOM, an internal compiler error, a full disk), a network failure,
and a failure on a line the closed list does not hold. A step before the build that
fails on `E: Unable to locate package` in a pinned runner image, with no `apt-get
update` before it, is named as the replay's difference from GitHub: GitHub's hosted
image ships its apt lists filled, and act's runner images ship them empty. The
remedy is `sudo apt-get update` before the install, a step GitHub runs the same way.

## Model projection

Keys use scope `fw` and are read with `ctx.param`, so `fw.<key>` beats the bare key.
`selftest/baseline.json` has a `_notes` line for each. `fw.tx_power` reads no key
of its own: the workflow chooses the build, and the claim holds the limit.

| Key | Meaning |
|---|---|
| `firmware_ci_workflow` | the workflow that builds the firmware, from the project root; every gate skips without it |
| `firmware_ci_job` | the job id (optional) |
| `firmware_ci_step` | the build step, when its command is not on the list (optional): its name, its `id:`, or, for a step with neither, its run's first line (the name act logs for it) |
| `firmware_ci_matrix` | one entry of a matrix job, `{key: value}` with a value for each key (optional): act's `--matrix key:value`; the replay is errored unless exactly one entry runs |
| `firmware_ci_timeout_s` | the replay's wall clock in s (optional, default 1800) |
| `firmware_image` | the file the device receives, a glob in the job's workspace (`build/*.bin`: `*` stays inside one directory, `**/` crosses any number) |
| `firmware_elf` | the ELF in the job's workspace, a glob |
| `app_memory_map` | the region, one of three shapes (below) |
| `deep_sleep_wakes_by_reset` | what resets the chip when reset is the wake, e.g. "power button on EN" (optional) |

The repository is the directory above the workflow's `workflows/` directory's
parent: `.github/` in a real repository. The pack's own control keeps its workflow at
`github/workflows/`, because nopekit keys a pack's controls by a walk of `selftest/`
that skips dot-directories.

`app_memory_map` takes one of three shapes. Numbers are ints or `"0x…"` strings.
The param record's `source` cites the bootloader's documentation, the datasheet
section or the partition table, and a person confirms it:

- address: `{flash: {start, size}, app: {start, size, vector_at?}, ram?: [{start,
  size}], other?: [{name, start, size}]}`;
- slot: `{app: {size}}` (a `.bin`, or an MCUboot slot minus its trailer);
- ESP: `{esp_partition_table: "<csv>", offset?}`. The slot is the smallest app
  partition, TEE subtypes left out, read by ESP-IDF's own `gen_esp32part.py`.

The region is never taken from the linker script, the devicetree or the board's
maximum size: those are the files the linker already compares against. An ESP image
reaches flash through the cache, so it takes the ESP or slot shape only.

## Tag vocabulary

- `firmware-build`: the CI job builds this tree's firmware.
- `ci-build`: the CI job can be replayed, and no masking hides a failed build (the
  guard alone).
- `flash-fit`, with `{"quantity": "flash use", "comparator": "<=", "limit": 85,
  "units": "%"}`: `fw.image_fits` reports this limit. State the headroom here, once;
  `memory-fit` and `ota-slot` claims are covered and cross-checked, and their limits
  are not read.
- `deep-sleep`, `wakeup-source`: the image arms a deep-sleep wake. A wake on real
  hardware is a physical claim.
- `ble-tx-power`, with `{"quantity": "bluetooth le transmit power setting",
  "comparator": "<=", "limit": <dBm>, "units": "dBm"}`. The limit is the setting the
  radio module's grant was tested at, from its test report or integration manual (47
  CFR 15.204(c)(2): tested "at its maximum available output power level"). It is
  never the grant's listed output, which is what that setting measured after the
  module's losses. With NCS and a front-end module, the setting is
  antenna-referenced, and the statement says so. A LoRa, Wi-Fi or Thread power
  claim never carries this tag.

## Tools and installation

`fw.ci_job` needs Python and PyYAML only. The other gates need `act` (v0.2.97
exactly; another version reads Skipped and names the install line), `docker` and
`git` on PATH, and the job's images already pulled. `references/installing.md` has
the steps and the checksum. The baseline's replay took 40-49 s on seven cores. The
four tier-1 gates share one replay per check: they ask for it with one loader
(`_replay.loader`), and the replay asks nothing of a path the memo would see move.
They read through subprocesses the trace cannot see into, so they run again on every
check at tier 1 or above. So do their controls: a control that read through a
subprocess is never current, so each check replays the known-good and the
known-bad of each of the four gates again. A tier-1 check of the baseline is
nine replays: three checks measured on 2026-11-24 took 548-668 s on seven cores,
of which the gates' one replay took 61-82 s, each reader 0.01-0.04 s, and each
gate's two controls 118-169 s. A project's own build multiplies the first part, not the
controls, which replay the pack's own app.

The replay reads back whole only the images that `firmware_image` and
`firmware_elf` name, and every configuration file, to 64 MiB in all. Every other
image is listed by name, size and sha256 in `images.json`. Past the cap the replay
is errored and says so: name one file with each glob.

## What this pack cannot settle

- A wake on real hardware, sleep current, and whether the board routed the button
  to a pin that can wake (an ESP32 RTC GPIO, an STM32 WKUP pin). A symbol shows that
  an image arms a wake somewhere, not that every path arms it before the sleep call.
- Whether the stated `app_memory_map` is right. A person confirms its cited source.
- act differs from GitHub: runner images (their apt lists are empty), secrets,
  caches and `if:` contexts. A JavaScript action fails in a job image with no
  `node`. In bind mode a matrix's entries share one workspace in turn, so the
  readers are errored on a matrix job unless `firmware_ci_matrix` names one entry.
  A tracked symlink is copied as the link, never followed, so one to a directory
  outside the repository dangles in the replay as it does on the runner. Each
  difference shows as errored or in the detail, never as a pass GitHub would not
  give.
- A CI the replay cannot run (a matrix of toolchain installs past any timeout, a
  build inside docker, a runner act does not have). The route: a single-entry job
  from `references/ci-templates.md` beside your CI, recorded as a decision (`nopekit
  decide`) that says why, with the claim restated to name that job. The replay then
  judges that job, not your CI.
- What a script the build step runs does: `fw.ci_job` reads the step's own lines,
  and names each script it did not read.
- A job container act could not remove is named in the replay's cleanup. To find
  one by hand: `docker ps -a --filter volume=<copy>`, or `docker ps -a --filter
  name=act-`; remove it with `docker rm -f <id>`.
- Dependencies the job fetches live (west, the component manager, PlatformIO).
  Two checks of one tree can differ when an upstream moves.
- ZMK's reusable `build-user-config` workflow: it builds outside its workspace, and
  its replay was not measured. `fw.ci_job` refuses it; use the self-contained
  template.
- Projects with no GitHub-style CI (local builds, GitLab) skip. The remedy is a
  workflow of about 15 lines from `references/ci-templates.md`.
- Runtime stack and heap use, OTA image validity, signing and rollback, partitions
  that overlap inside the app region, a watchdog and a brown-out level.
- The conducted or radiated output, antenna gain, EIRP and regional limits: the gate
  judges a setting against a setting. A conducted measurement at the highest
  reachable setting is a physical claim (`nopekit claim physical`); it is also the
  route for every stack the gate skips.
- A transmit setter reached by indirection: a library outside the repository, a
  Zephyr or NCS module, an HCI command that an external host sends or that an app
  builds by hand for the Zephyr host (`bt_hci_cmd_create`). The scan reads the
  names in the repository's own C and C++ files, and a call the build does not
  compile still counts. An advertising set's power is read by its member's name: a
  struct filled by copying bytes, or initialized by position through a type alias,
  is not read.
- Design lines for PlatformIO, Arduino, QMK, ESP-IDF's partition check and generic
  GCC errors read errored until their log excerpts are committed.
- Profiles other than the baseline's (ZMK, the address route, the vector check) are
  qualified by unit tests on recorded inputs, not by a live control.

## Sources and further reading

- `references/installing.md`: act, its checksum, the images and a smoke test.
- `references/ci-templates.md`: one workflow per stack, each building in its
  workspace with no masked pipe, and each marked as replayed or not.
- `references/memory-maps.md`: how to state `app_memory_map` for each bootloader.
- `references/wake-rules.md` and `references/signatures.md`: the rule tables and the
  closed lists, each line with its committed log excerpt.
- `references/tx-power.md`: the transmit profiles, the ceiling rule and the setter
  scan, each with its source.
- `references/what-slipped-through.md`: the faults this pack was written for.
- `lenses.md`: what a reviewer should ask of a firmware build.

The baseline is the pack's own ESP-IDF app (`selftest/assets/esp-sleep`): an
ESP32-S3 that starts BLE at a +6 dBm default transmit level, arms a button wake and
sleeps, built in ESP-IDF v5.5.6's image pinned by digest. Its `_claims` hold one
calibration claim, TX, at 7 dBm: a value for the control, not any module's grant. `selftest/assets/esp-sleep/LICENSE` says where each file is
from.
