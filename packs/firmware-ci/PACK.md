# firmware-ci

Replay the **CI job that builds the firmware** with act, in the job's own containers,
and read three facts from what it writes: the build passed, the image fits its region,
and an image that can enter deep sleep can wake. The object judged is the image the
named CI job writes into its workspace when act replays it, on a copy of the files
the next commit would hold. Addresses and sizes are in bytes. Flash use is in % of
the region that the bootloader or the partition table leaves the app. RAM banks come
from the part's datasheet.

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

`fw.ci_job` is the replay's validity guard. It reads the workflow in about 0.03 s,
before a container starts. `fw.build` needs it, and the two readers need `fw.build`.
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
  {0}`), which has no pipefail. `shell: bash` has it. It is **errored**, never failed,
  on a shape the replay cannot run: a Windows, macOS or self-hosted runner with no
  container, docker inside the job (`espressif/esp-idf-ci-action`), a shell that is
  not bash or sh, a reusable workflow, a `${{ }}` in `if:` or `continue-on-error`, and
  a job or step it cannot choose.
- **`fw.build`** runs act v0.2.97 on that job: `--bind`, so the job writes into the
  copy; no docker socket in the jobs; no secrets, variables or caches; pinned runner
  images; `--pull=false`. The verdict is the build step's own result. It **fails** on a
  line from a closed list in a failed build step (`FAILED: ….obj`, `undefined
  reference to`, `region … overflowed`, `Invalid BOARD`, `devicetree error:`, a Kconfig
  abort), on such a line in a step that read success (masked), on ZMK's `Unable to
  locate ZMK config at:` warning, and on a `firmware_image` the job did not write.
- **`fw.image_fits`** compares the bytes the image writes with a region from a cited
  record. It reports flash use in %, rounded up to 0.1 %, limit 100. The pass is
  decided on bytes: one byte past the slot fails. The spine's cross-check holds a
  claim's headroom (`flash use <= 85 %`). It **fails** with no limit on bytes in
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

A tool fault is errored, never a fail: a step before the build that fails, act or
docker stopping, a timeout (1800 s), a compiler the machine stopped (OOM, an internal
compiler error, a full disk), a network failure, and a failure on a line the closed
list does not hold.

## Model projection

Keys use scope `fw` and are read with `ctx.param`, so `fw.<key>` beats the bare key.
`selftest/baseline.json` has a `_notes` line for each.

| Key | Meaning |
|---|---|
| `firmware_ci_workflow` | the workflow that builds the firmware, from the project root; every gate skips without it |
| `firmware_ci_job` | the job id (optional) |
| `firmware_ci_step` | the build step's name or `id:`, when its command is not on the list (optional); act logs a step with an `id:` under it |
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
  "units": "%"}`; `memory-fit`, `ota-slot`.
- `deep-sleep`, `wakeup-source`: the image arms a deep-sleep wake. A wake on real
  hardware is a physical claim.

## Tools and installation

`fw.ci_job` needs Python and PyYAML only. The other gates need `act` (v0.2.97
exactly; another version reads Skipped and names the install line), `docker` and
`git` on PATH, and the job's images already pulled. `references/installing.md` has
the steps and the checksum. The baseline's replay took 40-49 s on seven cores. The
three tier-1 gates share one replay per check. They read through subprocesses the
trace cannot see into, so they run again on every check at tier 1 or above.

## What this pack cannot settle

- A wake on real hardware, sleep current, and whether the board routed the button
  to a pin that can wake (an ESP32 RTC GPIO, an STM32 WKUP pin). A symbol shows that
  an image arms a wake somewhere, not that every path arms it before the sleep call.
- Whether the stated `app_memory_map` is right. A person confirms its cited source.
- act differs from GitHub: runner images, secrets, caches and `if:` contexts. A
  JavaScript action fails in a job image with no `node`. In bind mode a matrix's
  entries share one workspace in turn, so `fw.image_fits` and `fw.sleep_wake` are
  errored on a matrix job. A tracked symlink is copied as the link, never followed,
  so one to a directory outside the repository dangles in the replay as it does on
  the runner. Each difference shows as errored or in the detail, never as a pass
  GitHub would not give.
- Dependencies the job fetches live (west, the component manager, PlatformIO).
  Two checks of one tree can differ when an upstream moves.
- ZMK's reusable `build-user-config` workflow: it builds outside its workspace, and
  its replay was not measured. `fw.ci_job` refuses it; use the self-contained
  template.
- Projects with no GitHub-style CI (local builds, GitLab) skip. The remedy is a
  workflow of about 15 lines from `references/ci-templates.md`.
- Runtime stack and heap use, OTA image validity, signing and rollback, partitions
  that overlap inside the app region, a watchdog and a brown-out level.
- Design lines for PlatformIO, Arduino, QMK, ESP-IDF's partition check and generic
  GCC errors read errored until their log excerpts are committed.
- Profiles other than the baseline's (ZMK, the address route, the vector check) are
  qualified by unit tests on recorded inputs, not by a live control.

## Sources and further reading

- `references/installing.md`: act, its checksum, the images and a smoke test.
- `references/ci-templates.md`: one workflow per stack, each building in its
  workspace with no masked pipe.
- `references/memory-maps.md`: how to state `app_memory_map` for each bootloader.
- `references/wake-rules.md` and `references/signatures.md`: the rule tables and the
  closed lists, each line with its committed log excerpt.
- `references/what-slipped-through.md`: the faults this pack was written for.
- `lenses.md`: what a reviewer should ask of a firmware build.

The baseline is the pack's own ESP-IDF app (`selftest/assets/esp-sleep`): an
ESP32-S3 that starts BLE, arms a button wake and sleeps, built in ESP-IDF v5.5.6's
image pinned by digest. `selftest/assets/esp-sleep/LICENSE` says where each file is
from.
