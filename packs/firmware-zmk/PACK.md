# firmware-zmk

Build **ZMK keyboard firmware** as its CI builds it, and read two facts from that
build: whether deep sleep can wake, and the radio's transmit power. The object
judged is the firmware that the CI's one `west build` step builds from the working
tree. The build runs in ZMK's image, pinned by digest, on ZMK v0.3.0 and Zephyr
3.5.0, with no network.

## What the gates settle

| Gate | Tier | Claim tags | Basis | Known-bad control |
|---|---|---|---|---|
| `zmk.ci_step` | 0 | `firmware-build`, `ci-build-step` | model-only: reads the workflow and `config/west.yml` | `west.yml` as ZMK's config template ships it (`revision: v0.3`, a branch that moves) |
| `zmk.build` | 1 | `firmware-build` | established: CMake's and Zephyr's exit code and log | `-DZMK_CONFIG` set to a directory that is not there |
| `zmk.wakeup_source` | 1 | `deep-sleep`, `wakeup-source` | sourced: ZMK's low-power docs, read through Zephyr's `edtlib` | `/delete-property/ wakeup-source` on the kscan |
| `zmk.tx_power` | 1 | `tx-power`, `radio` | established: Kconfig's `CONFIG_BT_CTLR_TX_PWR_DBM` | `CONFIG_BT_CTLR_TX_PWR_PLUS_4=y` against 3.86 dBm |

Three gates settle claims: the build, the wakeup source and the transmit power.
`zmk.ci_step` is the build's guard. It reads the CI's flags in 0.03 s, before a
container starts. `zmk.build` needs it, and the two build readers need `zmk.build`.
A gate whose prerequisite did not pass reads Skipped.

- **`zmk.ci_step`** finds the one run step that holds `west build`. The step must
  be one plain bash command. It may expand only `${GITHUB_WORKSPACE}`, every `-D`
  path must be absolute, and `-DZMK_CONFIG` must be there. Before it, in its
  working directory, the job must run `west init -l config`, `west update` and
  `west zephyr-export`, in that order, in a container of
  `zmkfirmware/zmk-build-arm:stable` or of this pack's digest. The manifest
  `<working-directory>/config/west.yml` must pin `zmk` at `v0.3.0` from
  `https://github.com/zmkfirmware/zmk`, with `import: app/west.yml`, and fetch
  nothing else. A CI that cannot build or is not pinned fails. A CI whose
  workspace this pack does not hold, or a shape it cannot run, is errored (see
  below).
- **`zmk.build`** runs that step's own argv in the image. It fails on a line from a
  closed list (`Invalid BOARD`, `devicetree error:`, a Kconfig abort, a failed
  object, a region overflow, an undefined reference), on no `zmk.uf2`, and on a
  `Using keymap file:` path outside ZMK_CONFIG. A wrong ZMK_CONFIG builds with exit
  0 on ZMK's default keymap: only that line shows it.
- **`zmk.wakeup_source`** applies when `CONFIG_ZMK_SLEEP=y`. Each kscan that ZMK
  scans must have `wakeup-source` true: each physical layout's `kscan`, else the
  chosen `zmk,kscan`. Source: ZMK v0.3.0 `docs/docs/features/low-power-states.md`
  line 27. With no deep sleep built it reads Skipped and says so: a claim
  tagged `deep-sleep` is not Checked by the absence of sleep.
- **`zmk.tx_power`** compares `CONFIG_BT_CTLR_TX_PWR_DBM` with the claim tagged
  `tx-power`, in dBm. `CONFIG_BT_CTLR_TX_PWR_DYNAMIC_CONTROL=y` fails: the host can
  raise the power at run time.

A tool fault is errored, never a fail: docker's own exit codes, a timeout (the
container is killed by name), a run that never reached Zephyr, a Zephyr other than
3.5.0, a workspace at other commits, a compiler the machine stopped (the OOM
killer, an internal compiler error, a full disk), and a failure on a line the
closed list does not hold.

The gates read every file under ZMK_CONFIG and under each absolute `-D` path in
the repository (a module) before the build, so an edit to a `.conf`, a `.dtsi` or
a module source moves their key. The build reads these files inside the
container, where the trace cannot see.

## Model projection

| Key | Meaning |
|---|---|
| `zmk_ci_workflow` | the GitHub workflow that builds the firmware, from the project root |

The repository is the directory above the workflow's `workflows/` directory's
parent: `.github/` in a real repository. The pack's own control keeps its
workflow at `github/workflows/`, because nopekit keys a pack's controls by a walk
of `selftest/` that skips dot-directories.

The transmit limit is a claim, never a pack constant:

```json
{"id": "C7", "statement": "The radio transmits at most 3.86 dBm, the conducted power FCC grant 2ALPH-E73 lists for the module",
 "kind": "measurable", "tags": ["tx-power"],
 "acceptance": {"quantity": "radio transmit power", "comparator": "<=", "limit": 3.86, "units": "dBm"}}
```

A person must confirm which grant covers which module. The pack reads the limit.
It cannot say whether the limit is right.

## Tag vocabulary

- `firmware-build`: the CI's step builds this repository's firmware.
- `ci-build-step`: the CI's step can be read and is pinned (the guard alone).
- `deep-sleep`, `wakeup-source`: a board that sleeps can wake on a key.
- `tx-power`, `radio`: the built transmit power is inside the claim's limit.

## Tools and installation

`zmk.ci_step` needs Python and PyYAML only. The other gates need `docker` and
`zmk-west` on PATH, where `zmk-west` is this pack's `bin/zmk-west`, copied into a
ZMK workspace. The gates compare the bytes it prints of itself (`zmk-west --self`)
with the pack's own file, so a verdict is bound to the image digest, the mounts and
`--network none` in it. Without
`zmk-west` the three tier-1 gates read Skipped. `references/installing.md` has the
steps: one image pull (about 3 GB) and one workspace (about 2.1 GB, 80 s).

Each check runs one build (12-27 s on an idle machine), and the three build gates
share it. These gates read inside a container that the trace cannot see into, so
they run again on every check.

## What this pack cannot settle

- Wake current, or a wake on real hardware. The gate reads the devicetree, not the
  board.
- Conducted or radiated power, antenna gain, or whether grant 2ALPH-E73 covers the
  E73-2G4M08S1C module. That is the person's claim record.
- Wakeup sources for soft off (`zmk,soft-off-wakeup-sources`). Only deep sleep is
  judged.
- CI's own image. CI runs `zmk-build-arm:stable`; this pack runs one digest of it.
  A later `:stable` can give another result. `zmk.ci_step` accepts the tag, because
  it is the one ZMK's own workflow uses; any other image, and no container, is
  errored.
- What a setup step does besides `west init -l config`, `west update` and `west
  zephyr-export` (a `sed` on the manifest, a `uses:` action that edits the tree).
  It is not read and not reproduced.
- A manifest project beside `zmk` (a board or behavior module), `self: import:`,
  a group filter, or zmk at another path. Each is errored: CI's `west update`
  would fetch it, and this pack's workspace does not hold it.
- A file the build reads from outside ZMK_CONFIG and the `-D` module paths (an
  `#include` of `../..`). It is not in the gates' key.
- ZMK's reusable `build-user-config` workflow, split halves built in one job,
  `if:` or `continue-on-error` on the job or a step it reads, and `env:` on the
  workflow, job or step. Each is errored.
- A `${{ }}` expression in the step (a matrix value), a glob or a shell
  expansion, or a relative `-D` path. Each is errored.
- ZMK other than v0.3.0, and Zephyr's hardware model v2 (Zephyr 3.7 and later).
- Sideband and composite kscans. The gate judges the node ZMK scans, and does not
  follow what that node wraps.

## Sources and further reading

- `references/installing.md`: the image, the workspace, the PATH link and a smoke
  test.
- `references/what-slipped-through.md`: the three faults this pack was written
  for, and what each gate does about them.
- `lenses.md`: what a reviewer should ask of a firmware build.
- ZMK v0.3.0: `app/CMakeLists.txt`, `app/src/physical_layouts.c`, `app/src/pm.c`,
  `docs/docs/features/low-power-states.md`. Zephyr 3.5.0:
  `subsys/bluetooth/controller/Kconfig`, `scripts/dts/python-devicetree`.

The baseline is ZMK's own corne (left half on a nice!nano v2) at v0.3.0, with
deep sleep turned on. `selftest/assets/corne/LICENSE` says where each file is from.
