# Signatures: the lines that decide a failed build

`fw.build` reads the build step's own result as act reports it. When the step
failed, the pack sorts its output against two closed lists. A tool line makes the
gate errored. A design line makes it fail. A failure on no line from either list
is errored: the pack does not guess. A line joins a list only with a committed log
excerpt that shows it, in `tests/fw_logs/` of a nopekit checkout, and
`tests/test_firmware_ci.py` fails when a line on a list has no excerpt.

The lists live in `gates/_replay.py` (`TOOL_FAULT_LINES`, `DESIGN_LINES`,
`ZMK_NO_CONFIG`). The tool lines are checked first.

## Before any line: the step's own exit

| What act reports | Outcome | Excerpt |
|---|---|---|
| the build step's process ended on a signal (`exitcode '137'`: 128 + 9) | errored | `act_esp_signal_137.jsonl`: a second replay of the same workflow removed this one's container mid-build |
| act or docker stops before any step of the job | errored | `docker_125.log`: `docker: Error response from daemon: invalid mount config` |
| a step before the build fails | errored, naming the step and quoting its first cause line | (the pack cannot tell a manifest error from a network fault) |
| a step before the build fails on `E: Unable to locate package` in a pinned runner image, with no `apt-get update` before it | errored, naming the empty apt lists as the replay's difference from GitHub | `act_apt_empty_lists.jsonl`: `sudo apt-get install -y gcc-arm-none-eabi make` in act-24.04 |
| the run passes its timeout (`firmware_ci_timeout_s`, default 1800 s) | errored; the job's containers are removed | (none needed: the pack's own clock) |

## Tool lines: errored, never a fail

| Line | Excerpt |
|---|---|
| `Killed signal terminated program` | `oom_killed.log`: upstream ZMK corne under `docker run --memory=200m`. Ten `FAILED: ….obj` lines, each under a compiler the OOM killer stopped. Before this list, the first `FAILED` read as a design fail on a sound design. |
| `internal compiler error`, `No space left on device`, `virtual memory exhausted`, `Cannot allocate memory`, `ccache: error:`, a name-resolution, connection or TLS failure | named, not measured: GCC's, the kernel's, ccache's and the resolver's own words for the same kind of stop |

A failed step whose output holds none of these lines and no design line is errored
too. `docker_127.log` (`exec: "no-such-command": executable file not found`) is
the excerpt for that case. The reason quotes the step's first cause line, in this
order of the log: `CMake Error`, apt's `E:`, `error:`, `FATAL ERROR`. A west
configure failure ends with west's `FATAL ERROR`, and its cause is the `CMake
Error` line above it.

## Replay gaps in a step that read success: errored

| Line | Excerpt |
|---|---|
| `failed to connect to the docker API`, `Cannot connect to the Docker daemon` | `act_script_docker.jsonl`: `python3 scripts/build.py`, which runs `docker build` with no status check, in act-24.04 (the replay mounts no docker socket into jobs) |
| `python3: can't open file` | the same excerpt: the script runs a file the copy does not hold |
| a shell's `docker: not found`, a shell's `No such file or directory` for a script | named, not measured |

These are checked before the image the job wrote. A script that ignores its
commands' status built nothing here, and the image it did not write is the
replay's gap, never the design's fault.

## Design lines: a fail

| Line | Stack | Excerpt |
|---|---|---|
| `^FAILED: .*\.obj` | Ninja (every CMake build) | `esp_compile_error.log`: a missing `;` in the pack's own `main.c`, `FAILED: esp-idf/main/CMakeFiles/__idf_main.dir/main.c.obj` |
| `undefined reference to` | GNU ld | `act_esp_undefined_reference.jsonl`: the `unresolved_symbol` control, `` undefined reference to `board_power_init' `` |
| `region .* overflowed` | GNU ld | `esp_region_overflow.log`: a static buffer too large for the ESP32-S3's DRAM, ``region `dram0_0_seg' overflowed by 368248 bytes`` |
| `Invalid BOARD` | Zephyr, NCS, ZMK | `invalid_board.log`: a board that no board root holds (`demo_board`), in ZMK v0.3.0's build image |
| `^devicetree error:` | Zephyr, NCS, ZMK | `devicetree_tabb.log`: a typo in upstream corne's keymap |
| `^error: Aborting due to Kconfig warnings` | Zephyr, NCS, ZMK | `kconfig_abort.log`: a misspelled symbol (`ZMK_SLEEEP`) in upstream corne's `.conf` |

The same lines in a step that read **success** are a fail too: a failed build
that something masked, which the guard did not see. `act_esp_masked_pipe.jsonl` is
the `masked_pipe` control: `idf.py build 2>&1 | tee build.log` with a compile error
in `main.c` read success under act, and its output holds Ninja's `FAILED:` line.

## ZMK's wrong config: a fail at exit 0

`Unable to locate ZMK config at:` is a CMake **warning**. The build goes on and
exits 0 with ZMK's own default keymap, not the repository's. `wrong_config.log`
(upstream corne with the config path misspelled) is the excerpt; `corne_full.log`
(the same build with the right path) holds no such line and passes.

## Not on the list yet: errored until measured

Each of these is a real failure line, and each reads errored, never failed, until
its excerpt is committed with a test:

- ESP-IDF: `partitions are too small for binary` (it exits non-zero only when every
  app partition is too small; one short slot is a warning, which `fw.image_fits`
  judges);
- PlatformIO: `The program size (N bytes) is greater than maximum allowed`;
- Arduino: `Sketch too big`;
- QMK: `The firmware is too large`;
- GCC's generic `file:line:col: error:` (a compile error under a build system that
  is not Ninja).

To add one: record the failing build with act, commit a trimmed excerpt with no
path, host, user or clock in it, add the pattern to `DESIGN_LINES` with its
provenance, and run `tests/test_firmware_ci.py`.
