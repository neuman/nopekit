# The slicer fdm.slice runs

`fdm.slice` runs OrcaSlicer **2.4.2** and no other release (`SUPPORTED_ORCA` in
`gates/slicer.py`). It finds it as `orca-slicer` on `PATH`. Without it the gate
reads SKIPPED, and so does every claim tagged `slicer`.

OrcaSlicer is AGPL-3.0. This pack runs it as a subprocess only, and carries none
of its code, text or vendor profiles.

## Installing it user-space

1. Download `OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.2.AppImage` from the
   project's GitHub release v2.4.2. Check it:
   `sha256sum` must give
   `d12fb8c8eac1aecd2dfb6377acd48f994f8fa439ed5292fa532dd82880f029fd`
   (the digest GitHub shows for the asset).
2. Extract it, outside any repository:

   ```sh
   mkdir -p ~/.cache/nopekit/orca-2.4.2 && cd ~/.cache/nopekit/orca-2.4.2
   chmod +x /path/to/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.2.AppImage
   /path/to/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.2.AppImage --appimage-extract
   ```

   Where FUSE or the AppImage runtime is missing, `unsquashfs -o 944632 <AppImage>`
   extracts the same tree (944632 is the offset of the squashfs image in this file).
3. Put a wrapper named `orca-slicer` on `PATH` that runs
   `~/.cache/nopekit/orca-2.4.2/squashfs-root/AppRun "$@"`, and check it:
   `orca-slicer --help | head -1` must print `OrcaSlicer-2.4.2:`.

The CLI loads WebKitGTK and GStreamer even when it slices without a window. On a
host without them, the wrapper can run the extracted tree in an Ubuntu 24.04
container that has them, with no network, mounting the working directory at the
same path. The gate runs Orca with the part's own directory as the working
directory and relative paths only, so the wrapper needs to mount `$PWD` and the
extracted tree, and nothing else:

```sh
#!/bin/sh
exec docker run --rm -i --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$HOME/.cache/nopekit/orca-2.4.2/squashfs-root:/opt/orca:ro" \
  -v "$PWD:$PWD" -w "$PWD" <an Ubuntu 24.04 image with WebKitGTK and GStreamer> \
  /opt/orca/AppRun "$@"
```

That is machine setup, not pack code. Measured with such a wrapper: 0.6 s per slice
for the pack's controls, plus the container start. A part of many more faces takes
longer; the gate allows 600 s a part (`SLICE_TIMEOUT_S`).

## Writing the three presets

`slicer_presets` names three JSON files — `machine`, `process`, `filament` — and the
verdict names each file's `name` and holds its sha256. A part that prints on
supports, in another material or on another printer takes a group of its own: a
key with its name or a glob, holding its own three files (`PACK.md`, "Preset groups
per part"). Each file must be **complete**: the gate binds the bytes of the files,
so a preset that leans on a vendor parent (`inherits`) slices with settings no
bound file shows. Each file:

- states `"type"` (`machine`, `process` or `filament`), a `"name"`,
  `"from": "system"` and `"instantiation": "true"`;
- in the process and the filament, names the machine in `compatible_printers`
  (measured: without it Orca exits 239, -17, and writes no result, which the gate
  reads as errored);
- in the machine, states `layer_change_gcode` with `G92 E0` at least: without it
  Orca refuses relative extrusion with -51;
- in the process, sets `enable_arc_fitting` to `"0"`: the gate reads straight moves
  only, and errors on an arc;
- states every setting it means. A key Orca does not use is an error, not a no-op:
  the gate checks that every key of each file (but the file keys `type`, `name`,
  `from`, `instantiation`, `setting_id`, `filament_id`, `compatible_printers`) is in
  the configuration the G-code was sliced with, at the file's value, and a
  misspelled `enable_suport` slices "Success." without it. A filament value of
  `"nil"` means "not overridden" and is not checked. Two lists, transcribed from
  Orca 2.4.2's `handle_legacy`, are not errors: a key Orca reads under a new name
  (`wall_infill_order` as `wall_sequence`) must reach the block under that name,
  and a key Orca retires (`adaptive_layer_height`) is named in the evidence. A
  vendor profile flattened from the GUI carries both kinds. Any other key Orca does
  not know (a filament `bed_type`, say) must be removed: Orca drops a misspelling
  and a key it never had the same way, and only you can tell which it is.

One way to write them: export the three presets from the OrcaSlicer GUI after
choosing the printer, process and filament you print with, then flatten each into a
single file, keep the keys you mean, and slice once by hand to see that the gate
binds them. The pack's own presets (`selftest/orca/`) are three short files written
by hand for its baseline machine; they are a shape to copy, not a printer profile.

`fdm.slice` reads its limit from `max_bridge_mm`, the key `fdm.bridge_span` reads.
Supports belong in the process preset: a project that prints with supports on the
build plate says so there, and the gate then reads every bridge over that support
as held. When only some parts print on supports, give those parts a group whose
process turns support on, and leave the rest on the default triple.
