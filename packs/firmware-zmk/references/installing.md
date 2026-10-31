# Installing the tools

`zmk.ci_step` needs Python and PyYAML (`pip install pyyaml`). `zmk.build`,
`zmk.wakeup_source` and `zmk.tx_power` need `docker` and `zmk-west` on PATH. Without
`zmk-west` they read Skipped, never passed.

Linux with docker only. Your user must be in the `docker` group. No step needs
sudo. macOS and Windows are untested.

## 1. The image

```sh
docker pull zmkfirmware/zmk-build-arm@sha256:edb1c953438c6f720ddb79c3762f3972013b7fbbaf4fff3592fc869983e7afc5
```

About 3.1 GB. The gates run it with `--pull=never`, so they never fetch another.

## 2. The workspace

```sh
sh packs/firmware-zmk/bin/zmk-west --init ~/.cache/nopekit/zmk/v0.3.0
```

The path is relative to a nopekit checkout; an installed pack has the same file
under its own directory. This runs `west init` and `west update` for ZMK v0.3.0
inside the image, with the network on. It took 78-88 s here and made 2.1 GB:
zmk at edf5c08, zephyr at dacab48. It ends with `west config zephyr.base zephyr`:
without that, the first build writes `.west/config` and fails on the read-only
mount. Then it copies `zmk-west` into the workspace.

## 3. The PATH link

```sh
ln -s ~/.cache/nopekit/zmk/v0.3.0/zmk-west ~/.local/bin/
```

Use a link, not a copy: `zmk-west` finds its workspace from its own real path.
The gates compare the bytes it prints of itself (`zmk-west --self`) with this
pack's `bin/zmk-west`. After a pack upgrade that changes the file, copy the new
file into the workspace (`cp packs/firmware-zmk/bin/zmk-west
~/.cache/nopekit/zmk/v0.3.0/`), or every build gate is errored and says so.

## 4. A smoke test

```sh
PYTHONPATH=src python3 -m nopekit gate selftest --pack firmware-zmk
```

This builds the pack's baseline (ZMK's corne) and its known-bad copies. Each of
the four gates must say its known-good passed and its known-bad failed. A build
takes 12-27 s on an idle machine; the whole selftest took 8 minutes on a loaded
one.

## What the wrapper does

`zmk-west <project> <out> <cmd...>` runs `<cmd>` in the image with no network, as
your user id, in a container named `zmk-west-<basename of out>`: a gate that times
out kills the container by that name. The workspace is mounted read-only at `/zmk`, `<project>` read-only
at its own path, and `<out>` writable at its own path. The gates pass the CI step's
own `west build` argv, with a build directory of their own under the system's
temporary directory, and remove it afterwards.
