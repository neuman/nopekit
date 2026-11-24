# Stating `app_memory_map`

`fw.image_fits` compares the bytes an image writes with a region a person stated,
never with the build's own linker script or devicetree. State the region from the
bootloader's own documentation and the part's datasheet, cite them in the param
record's `source`, and have a person confirm it. Numbers are ints or `"0x…"`
strings, in bytes. The values below are placeholders (`<…>`); read yours from your
part's documents.

## The three shapes

| Shape | Form | Route |
|---|---|---|
| address | `{flash: {start, size}, app: {start, size, vector_at?}, ram?: [{start, size}], other?: [{name, start, size}]}` | the highest byte written past `app.start`, against `app.size` |
| slot | `{app: {size}}` | the file's size against the slot |
| ESP | `{esp_partition_table: "<csv>", offset?}` | the file's size against the smallest app partition |

On the address route the gate also fails, with no limit: a byte in `flash` but
outside `app` (it would overwrite the bootloader, the settings or the other slot); a
`vector_at` with no image bytes there, an initial stack pointer in no stated RAM bank
(the top of a bank counts: a full-descending stack starts one past its end), or a
reset vector without the Thumb bit or outside `app`. Where `ram` is stated, each ELF
segment, by its run address with `.bss`, must lie inside one stated region (a RAM
bank, `flash` or an `other` range): one that starts in a bank and runs past its end
fails, and so does one in the Arm SRAM region (0x20000000-0x3fffffff, ARM DDI 0403
B3.1) that no stated region holds. A segment anywhere else that no stated region
holds is errored: state every RAM the firmware uses (CCM, a TCM, external SDRAM) in
`ram` or `other`. Bytes in an `other` range (external XIP flash, a second bank) are
reported, not counted. Bytes in no stated range are errored.

Flash use is rounded up to 0.1 %, and the pass is decided on bytes: an image one
byte past its region fails, and a headroom claim never reads a figure under the
true one.

## MCUboot

MCUboot's design document (`docs/design.md`, "Image Format" and "Image Trailer")
puts an image header before the vector table and a trailer at the end of each slot.
State the primary slot as the app, less its trailer, and the vector table after the
header:

```json
{"flash": {"start": "<flash base>", "size": "<flash size>"},
 "app": {"start": "<slot0 start>", "size": "<slot0 size - trailer size>",
         "vector_at": "<slot0 start + header size>"},
 "ram": [{"start": "<SRAM base>", "size": "<SRAM size>"}]}
```

The header size is the image's own (`imgtool sign --header-size`, the build's
`CONFIG_ROM_START_OFFSET` on Zephyr). The trailer size depends on the swap mode and
the sector count: MCUboot's documentation gives the formula.

## RP2040, XIP flash

The RP2040 datasheet maps the flash at 0x10000000, and the bootrom runs the
256-byte second-stage boot from its start; the Pico SDK puts the vector table
after it, at 0x10000100. The boot stage is part of the image, so the app is the whole
flash, with the vector table stated:

```json
{"flash": {"start": "0x10000000", "size": "<flash size on your board>"},
 "app": {"start": "0x10000000", "size": "<flash size on your board>",
         "vector_at": "0x10000100"},
 "ram": [{"start": "0x20000000", "size": "0x42000"}]}
```

The SRAM is 264 KB from 0x20000000 (the RP2040 datasheet's address map); the SDK's
initial stack pointer sits one past its end. The image is the `.uf2` the SDK writes.

## STM32 with a custom bootloader

The part's reference manual gives the flash base and the RAM banks. A bootloader in
the first sectors starts the app at a later sector and moves VTOR there. State the
app from that sector, and every RAM bank (SRAM1, SRAM2, CCM or DTCM are separate
banks on some parts):

```json
{"flash": {"start": "0x08000000", "size": "<flash size>"},
 "app": {"start": "<first app sector>", "size": "<flash size - bootloader sectors>",
         "vector_at": "<first app sector>"},
 "ram": [{"start": "0x20000000", "size": "<SRAM1 size>"},
         {"start": "<SRAM2 base>", "size": "<SRAM2 size>"}]}
```

A RAM segment past a stated bank is firmware built for a bigger part than the one on
your bill of materials.

## ESP-IDF partition tables

Name the CSV the build flashes (`CONFIG_PARTITION_TABLE_CUSTOM_FILENAME`):

```json
{"esp_partition_table": "partitions.csv"}
```

The gate reads it with ESP-IDF's own `gen_esp32part.py` (vendored at v5.5.6) and
takes the smallest app partition (factory, ota_*, test; the two TEE subtypes are
left out, as ESP-IDF master's `check_sizes.py` leaves them out). `offset` is the
table's own offset, `CONFIG_PARTITION_TABLE_OFFSET`, default 0x8000; it moves where
partitions with no stated offset start, never their sizes. ESP-IDF only warns when
some app partitions are too small and others fit; the gate fails it. Name the `.bin`
the build writes as `firmware_image`: an ESP image reaches flash through the cache,
so it takes this shape or the slot shape, never the address shape.
