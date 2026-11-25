# CI templates

One GitHub Actions workflow per stack. Each builds in its own workspace (so the
replay's `--bind` brings the image back) and has no masked pipe. The Zephyr one
shows an upload step after the build; add one to the others the same way. Put the
file at `.github/workflows/build.yml` and point `firmware_ci_workflow` at it. Replace each `<…>` with your own value; nothing here
is a design value.

Rules each template keeps, and why:

- **One job builds.** Two jobs that build need `firmware_ci_job`.
- **No pipe after the build** under the default shell. If you want a log file, set
  `shell: bash` on the step (it runs with pipefail), or write `set -o pipefail` first.
- **No `continue-on-error`, no `|| true`** on the build, and no `&& …` after it
  with more lines below: `-e` does not stop on a command before the last `&&`.
- **A container job where the stack ships an image.** act runs the job in that image,
  as GitHub does. A job with no container runs in the pack's pinned runner image
  (references/installing.md), which is close to GitHub's but not the same.
- **Uploads come after the build.** `actions/upload-artifact` is a JavaScript action
  and needs `node` in the job's image; where there is none it fails under act. A step
  after the build is named in the verdict's detail and not judged.
- **`apt-get update` before `apt-get install`.** GitHub's hosted runner image ships
  its apt lists filled. act's runner images ship them empty, so an install with no
  update fails in a replay with `E: Unable to locate package`, and works on GitHub.
- **One entry of a matrix.** Set `firmware_ci_matrix` (`{key: value}`, a value for
  each key) to replay one entry. Without it the entries share one workspace in
  turn, and the readers error.

Each template says whether this pack has replayed it. A template that says **Not
replayed** passes `fw.ci_job` (a test holds each one to it), and nothing more is
known: replay it once on your project and read the detail.

## Zephyr or NCS, in a container

**Not replayed** by this pack: no Zephyr image was pulled when it was written.

```yaml
name: Build firmware
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-24.04
    container: ghcr.io/zephyrproject-rtos/ci:<pinned tag or digest>
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
        with:
          path: app
      - name: West init and update
        run: |
          west init -l app
          west update --narrow -o=--depth=1
      - name: Build
        run: west build -s app -d build -b <board>
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02  # v4.6.2
        with:
          name: firmware
          path: |
            build/zephyr/zephyr.elf
            build/zephyr/zephyr.hex
```

`firmware_image`: `build/zephyr/zephyr.hex`. `firmware_elf`: `build/zephyr/zephyr.elf`.
A failed `west update` is a step before the build: errored, never failed.

## ZMK, self-contained

**Not replayed** in this exact form. Its build line was measured in ZMK v0.3.0's
image on a neutral board and on upstream corne (`references/signatures.md`).

ZMK's reusable `build-user-config` workflow builds in `mktemp -d`, outside the
workspace, and its replay was not measured: `fw.ci_job` refuses it (errored, not
failed). This job builds one board and shield in the workspace instead:

```yaml
name: Build ZMK firmware
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-24.04
    container: zmkfirmware/zmk-build-arm:<pinned tag or digest>
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
      - name: West init and update
        run: |
          west init -l config
          west update
          west zephyr-export
      - name: Build
        run: west build -s zmk/app -d build -b <board> -- -DSHIELD=<shield> -DZMK_CONFIG="${GITHUB_WORKSPACE}/config"
```

`config/west.yml` pins ZMK at a release tag, not a branch. `firmware_image`:
`build/zephyr/zmk.uf2`. `firmware_elf`: `build/zephyr/zmk.elf`; the `.config` and
`zephyr.dts` beside it carry the wake rule. A split keyboard needs a job per half,
or a matrix with `firmware_ci_matrix` naming the half to replay: in a replay a
matrix's entries share one workspace in turn.

## ESP-IDF, in a container

**Replayed** on every `gate selftest`: it is the shape of this pack's own control
(`selftest/assets/esp-sleep/github/workflows/build.yml`), with act 0.2.97 in ESP-IDF
v5.5.6's image.

```yaml
name: Build firmware
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-24.04
    container: espressif/idf@sha256:<digest of a release>
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
      - name: Build
        run: . $IDF_PATH/export.sh && idf.py build
```

act starts the job's container with its own entrypoint, so the step sources
`export.sh` itself. Set `PROJECT_VER` in `CMakeLists.txt` and
`CONFIG_APP_REPRODUCIBLE_BUILD=y`, so the image holds no path, no clock and no
`git describe` of the copy. `firmware_image`: `build/<project>.bin`. `firmware_elf`:
`build/<project>.elf`. Do not use `espressif/esp-idf-ci-action`: it runs `docker run`
inside the job, and the replay mounts no docker socket into jobs.

## PlatformIO

**Not replayed** by this pack.

```yaml
name: Build firmware
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065  # v5.6.0
        with:
          python-version: "3.12"
      - name: Install PlatformIO
        run: pip install platformio==<pinned version>
      - name: Build
        run: pio run -e <env>
```

`firmware_image`: `.pio/build/<env>/firmware.bin` (or `.hex`, `.uf2`).
`firmware_elf`: `.pio/build/<env>/firmware.elf`. PlatformIO fetches its platforms
live: two replays of one tree can differ when a platform moves. PlatformIO's "The
program size … is greater than maximum allowed" reads errored until its log excerpt
is committed.

## arduino-cli

**Replayed** through this pack's replay on 2026-11-24, with act 0.2.97 in the
pinned act-24.04 runner image: arduino-cli 1.1.1, `arduino:avr@1.8.6` and
`arduino:avr:uno` on a blink sketch. It built `build/blink.ino.hex` in 2 % of the
flash. No ESP32 core was replayed.

```yaml
name: Build firmware
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
      - name: Install arduino-cli and the core
        run: |
          mkdir -p "$HOME/bin"
          curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/<pinned tag>/install.sh | BINDIR="$HOME/bin" sh -s <pinned version>
          "$HOME/bin/arduino-cli" core update-index
          "$HOME/bin/arduino-cli" core install <vendor:arch>@<pinned version>
      - name: Build
        run: $HOME/bin/arduino-cli compile --fqbn <vendor:arch:board> --output-dir build <sketch folder>
```

`<sketch folder>` holds `<sketch folder>.ino`: arduino-cli wants the main file
named as its folder, and a replay's workspace is not named as your repository.
`firmware_image`: `build/<sketch folder>.ino.hex` (or `.bin`). `firmware_elf`:
`build/<sketch folder>.ino.elf`. A glob such as `build/*.hex` names two files
(`.ino.hex` and `.ino.with_bootloader.hex`, measured), and the gates refuse to
choose. The install step pipes into `sh`, which is fine: it is not the build step. The
`mkdir` is needed: `install.sh` stops with `$BINDIR folder not found` when the
folder is not there, as in act's runner image. What slipped through (holdout
review, a watch's first replay): the template had no `mkdir`, was never replayed,
and failed before the build. An ESP32 core of the 2.x series also needs pyserial
for its `esptool.py`; Ubuntu 24.04 installs it only with `pip3 install
--break-system-packages pyserial`, as a line before the core install.

## Pico SDK

**Not replayed** by this pack.

```yaml
name: Build firmware
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
      - name: Toolchain and SDK
        run: |
          sudo apt-get update && sudo apt-get install -y cmake gcc-arm-none-eabi libnewlib-arm-none-eabi
          git clone --depth 1 --branch <sdk tag> https://github.com/raspberrypi/pico-sdk.git
          git -C pico-sdk submodule update --init
      - name: Build
        run: |
          cmake -S . -B build -DPICO_SDK_PATH=$GITHUB_WORKSPACE/pico-sdk
          cmake --build build
```

`firmware_image`: `build/<target>.uf2`. `firmware_elf`: `build/<target>.elf`. See
references/memory-maps.md for the RP2040 map.

## STM32, make or CMake

**Not replayed** by this pack.

```yaml
name: Build firmware
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
      - name: Toolchain
        run: sudo apt-get update && sudo apt-get install -y gcc-arm-none-eabi make
      - name: Build
        run: make -j4
```

`firmware_image`: `build/<project>.hex` (or `.bin`). `firmware_elf`:
`build/<project>.elf`. STM32CubeIDE projects that build only on Windows are out of
reach: export a Makefile or CMake project first.
