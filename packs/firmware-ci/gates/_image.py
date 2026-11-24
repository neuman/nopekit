# SPDX-License-Identifier: Apache-2.0
"""firmware-ci's image reader: the bytes an image writes, the symbols an ELF links,
and the region a person stated for it. Standard library only.

**Where each fact comes from.** Every address and size here is read from the image's
own bytes, by the layout its format's specification gives: the ELF program and
section headers and the symbol table (the System V ABI, "ELF"), UF2 blocks
(Microsoft's UF2 specification, ``microsoft/uf2`` README), Intel HEX records (Intel's
"Hexadecimal Object File Format Specification", rev. A), and a raw ``.bin`` by its
size. The region comes from the person's ``app_memory_map`` record, or from an ESP
partition table read by ESP-IDF's own ``gen_esp32part.py`` (vendored). Nothing here
reads the build's linker script or devicetree: those are the files the linker and
each stack's size check already compare against, and a wrong one is the fault this
gate exists for.

*Rejected:* pyelftools and intelhex. The gates read only the program headers, the
symbol table and the DWARF producer strings, each a fixed layout; with the two
packages required, both image gates read Skipped on every machine without them,
the machine that measured them included, and their rules could not be walked on
CI. A format this reader does not know is errored, never guessed at.
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))


class ImageFault(Exception):
    """The image or the map cannot be judged: the gate is errored, never failed."""


# --------------------------------------------------------------------------- #
# ELF
# --------------------------------------------------------------------------- #
#: ``e_machine`` of 32-bit Arm (the ELF for the Arm Architecture, 5.2). The vector
#: rule is the Armv7-M exception model, so it runs on an Arm ELF only.
EM_ARM = 40

PT_LOAD = 1
SHT_SYMTAB = 2
STT_OBJECT = 1
STT_FUNC = 2
SHN_UNDEF = 0
SHN_XINDEX = 0xFFFF

#: How a compiler names itself in a DWARF ``DW_AT_producer`` string: GCC writes
#: ``GNU C17 14.2.0 -mlongcalls ...`` (measured on the baseline's ELF), clang
#: ``clang version 18.1.0 ...``.
_PRODUCER = re.compile(rb"(?:GNU [A-Za-z+0-9]+ |clang version |Apple clang version )"
                       rb"[^\x00]{0,8000}")


@dataclass(frozen=True)
class Elf:
    """What the gates read of one ELF."""
    machine: int
    little: bool
    #: ``(paddr, vaddr, filesz, memsz, flags, bytes)`` of each PT_LOAD segment.
    loads: tuple
    funcs: frozenset
    objects: frozenset
    producers: tuple
    sections: frozenset


def is_elf(data: bytes) -> bool:
    return data[:4] == b"\x7fELF"


def read_elf(data: bytes) -> Elf:
    """The ELF's load segments, defined symbols and producer strings, or
    :class:`ImageFault` for bytes that are not a whole ELF."""
    if not is_elf(data) or len(data) < 52:
        raise ImageFault("not an ELF file")
    cls, enc = data[4], data[5]
    if cls not in (1, 2) or enc not in (1, 2):
        raise ImageFault(f"an ELF of class {cls} and data encoding {enc}, which this "
                         f"reader does not know")
    e = "<" if enc == 1 else ">"
    try:
        if cls == 1:
            (_ident, _type, machine, _ver, _entry, phoff, shoff, _flags, _ehsize,
             phentsize, phnum, shentsize, shnum, shstrndx) = struct.unpack_from(
                e + "16sHHIIIIIHHHHHH", data, 0)
            ph_fmt, sh_fmt, sym_fmt = e + "IIIIIIII", e + "IIIIIIIIII", e + "IIIBBH"
        else:
            (_ident, _type, machine, _ver, _entry, phoff, shoff, _flags, _ehsize,
             phentsize, phnum, shentsize, shnum, shstrndx) = struct.unpack_from(
                e + "16sHHIQQQIHHHHHH", data, 0)
            ph_fmt, sh_fmt, sym_fmt = e + "IIQQQQQQ", e + "IIQQQQIIQQ", e + "IBBHQQ"

        loads = []
        for i in range(phnum):
            row = struct.unpack_from(ph_fmt, data, phoff + i * phentsize)
            if cls == 1:
                p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, _a = row
            else:
                p_type, p_flags, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, _a = row
            if p_type != PT_LOAD:
                continue
            if p_offset + p_filesz > len(data):
                raise ImageFault(f"a load segment runs past the end of the file "
                                 f"(offset {p_offset:#x}, {p_filesz} bytes)")
            loads.append((p_paddr, p_vaddr, p_filesz, p_memsz, p_flags,
                          bytes(data[p_offset:p_offset + p_filesz])))

        sections = []
        if shoff:
            first = struct.unpack_from(sh_fmt, data, shoff)
            if shnum == 0:
                shnum = first[5]                      # sh_size of section 0
            if shstrndx == SHN_XINDEX:
                shstrndx = first[6]                   # sh_link of section 0
            for i in range(shnum):
                sections.append(struct.unpack_from(sh_fmt, data, shoff + i * shentsize))
    except struct.error as exc:
        raise ImageFault(f"the ELF's headers run past the end of the file ({exc})") from exc

    def body(sec) -> bytes:
        offset, size = sec[4], sec[5]
        return bytes(data[offset:offset + size])

    def name_at(table: bytes, at: int) -> str:
        end = table.find(b"\x00", at)
        return table[at:end if end >= 0 else len(table)].decode("utf-8", "replace")

    names = {}
    if sections and 0 <= shstrndx < len(sections):
        strtab = body(sections[shstrndx])
        names = {i: name_at(strtab, sec[0]) for i, sec in enumerate(sections)}

    funcs, objects = set(), set()
    for sec in sections:
        if sec[1] != SHT_SYMTAB or not (0 <= sec[6] < len(sections)):
            continue
        strings = body(sections[sec[6]])
        table = body(sec)
        size = struct.calcsize(sym_fmt)
        for at in range(0, len(table) - size + 1, size):
            row = struct.unpack_from(sym_fmt, table, at)
            if cls == 1:
                st_name, _value, _size, st_info, _other, st_shndx = row
            else:
                st_name, st_info, _other, st_shndx, _value, _size = row
            if st_shndx == SHN_UNDEF or not st_name:
                continue
            kind = st_info & 0xF
            if kind == STT_FUNC:
                funcs.add(name_at(strings, st_name))
            elif kind == STT_OBJECT:
                objects.add(name_at(strings, st_name))

    producers = []
    for i, sec in enumerate(sections):
        if names.get(i) in (".debug_str", ".debug_line_str", ".debug_info"):
            producers += [m.group(0).decode("utf-8", "replace")
                          for m in _PRODUCER.finditer(body(sec))]
    return Elf(machine=machine, little=enc == 1, loads=tuple(loads),
               funcs=frozenset(funcs), objects=frozenset(objects),
               producers=tuple(dict.fromkeys(producers)),
               sections=frozenset(names.values()))


def lto(elf: Elf) -> bool | None:
    """Whether a producer string shows ``-flto``; ``None`` when the ELF holds no
    producer string to read (no DWARF, or stripped)."""
    if not elf.producers:
        return None
    return any(re.search(r"(?:^|\s)-flto(?:[=\s]|$)", p) for p in elf.producers)


# --------------------------------------------------------------------------- #
# UF2, Intel HEX, BIN
# --------------------------------------------------------------------------- #
UF2_MAGIC = (0x0A324655, 0x9E5D5157, 0x0AB16F30)
#: Flag 0x1, "not main flash": the block is not written to the device's flash
#: (Microsoft UF2 specification, "Flags").
UF2_NOT_MAIN_FLASH = 0x00000001


def is_uf2(data: bytes) -> bool:
    return len(data) >= 512 and struct.unpack_from("<II", data, 0) == UF2_MAGIC[:2]


def read_uf2(data: bytes) -> list[tuple[int, bytes]]:
    """``[(address, bytes)]`` of every block written to main flash."""
    if len(data) % 512:
        raise ImageFault(f"a UF2 file of {len(data)} bytes, not a whole number of "
                         f"512-byte blocks")
    out = []
    for at in range(0, len(data), 512):
        m0, m1, flags, addr, size = struct.unpack_from("<IIIII", data, at)
        (m_end,) = struct.unpack_from("<I", data, at + 508)
        if (m0, m1, m_end) != UF2_MAGIC:
            raise ImageFault(f"UF2 block {at // 512} has no UF2 magic numbers")
        if size > 476:
            raise ImageFault(f"UF2 block {at // 512} says {size} payload bytes; a block "
                             f"holds at most 476")
        if flags & UF2_NOT_MAIN_FLASH:
            continue
        out.append((addr, bytes(data[at + 32:at + 32 + size])))
    return out


def is_hex(data: bytes) -> bool:
    return data[:1] == b":" and all(c in b"0123456789ABCDEFabcdef:\r\n" for c in data[:64])


def read_hex(data: bytes) -> list[tuple[int, bytes]]:
    """``[(address, bytes)]`` of every data record (type 00), with the extended
    segment (02) and extended linear (04) address records applied."""
    out, base = [], 0
    for no, raw in enumerate(data.decode("ascii", "replace").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith(":") or len(line) < 11 or len(line) % 2 == 0:
            raise ImageFault(f"Intel HEX line {no} is not a record")
        try:
            rec = bytes.fromhex(line[1:])
        except ValueError as exc:
            raise ImageFault(f"Intel HEX line {no} is not hexadecimal") from exc
        count, addr, kind = rec[0], (rec[1] << 8) | rec[2], rec[3]
        if len(rec) != count + 5 or sum(rec) & 0xFF:
            raise ImageFault(f"Intel HEX line {no} fails its length or checksum")
        payload = rec[4:4 + count]
        if kind == 0x00:
            out.append((base + addr, payload))
        elif kind == 0x01:
            break
        elif kind == 0x02:
            base = int.from_bytes(payload, "big") << 4
        elif kind == 0x04:
            base = int.from_bytes(payload, "big") << 16
        elif kind in (0x03, 0x05):
            continue                                  # a start address, not bytes
        else:
            raise ImageFault(f"Intel HEX line {no} has record type {kind:#04x}")
    return out


def esp_image(data: bytes, elf: Elf | None = None) -> bool:
    """An ESP app image: a ``.bin`` whose first byte is the ESP image magic 0xE9
    (ESP-IDF ``esp_app_format``), or an ELF that links ``esp_app_desc``."""
    if elf is not None:
        return "esp_app_desc" in elf.objects
    return data[:1] == b"\xe9"


def ranges(writes: list[tuple[int, bytes]]) -> list[tuple[int, int]]:
    """The written ``[start, end)`` ranges, sorted and merged."""
    spans = sorted((a, a + len(b)) for a, b in writes if b)
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def word_at(writes: list[tuple[int, bytes]], addr: int) -> int | None:
    """The little-endian 32-bit word the image writes at ``addr``, or ``None``."""
    for a, b in writes:
        if a <= addr and addr + 4 <= a + len(b):
            return struct.unpack_from("<I", b, addr - a)[0]
    return None


# --------------------------------------------------------------------------- #
# the stated region
# --------------------------------------------------------------------------- #
def number(raw, where: str) -> int:
    """An int, or a ``"0x…"`` or decimal string; :class:`ImageFault` otherwise."""
    if isinstance(raw, bool):
        raise ImageFault(f"app_memory_map {where} is {raw!r}, not a number")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and re.fullmatch(r"0[xX][0-9a-fA-F]+|\d+", raw.strip()):
        return int(raw.strip(), 0)
    raise ImageFault(f"app_memory_map {where} is {raw!r}: write an int or a \"0x…\" string")


@dataclass(frozen=True)
class Region:
    start: int
    size: int
    name: str = ""

    @property
    def end(self) -> int:
        return self.start + self.size

    def __str__(self) -> str:
        label = f"{self.name} " if self.name else ""
        return f"{label}{self.start:#x}..{self.end - 1:#x}"


@dataclass(frozen=True)
class MemoryMap:
    shape: str                       # "address", "slot" or "esp"
    flash: Region | None = None
    app: Region | None = None
    vector_at: int | None = None
    ram: tuple = ()
    other: tuple = ()
    slot: int | None = None
    csv: str | None = None
    offset: int = 0x8000


def _region(raw, where: str, *, name: str = "") -> Region:
    if not isinstance(raw, dict) or "start" not in raw or "size" not in raw:
        raise ImageFault(f"app_memory_map {where} must be {{start, size}}")
    size = number(raw["size"], f"{where}.size")
    if size <= 0:
        raise ImageFault(f"app_memory_map {where}.size is {size}")
    return Region(number(raw["start"], f"{where}.start"), size, name or raw.get("name", ""))


#: The keys each shape may hold. Anything else is refused, never ignored: a
#: misspelled ``vector_at`` read as "no vector check" would pass an image linked
#: for another start.
_SHAPE_KEYS = {"address": {"flash", "app", "ram", "other"},
               "slot": {"app"},
               "esp": {"esp_partition_table", "offset"}}


def parse_map(raw) -> MemoryMap:
    """``app_memory_map`` as one of its three shapes, or :class:`ImageFault`."""
    if not isinstance(raw, dict):
        raise ImageFault(f"app_memory_map must be an object, not {type(raw).__name__}")
    if "esp_partition_table" in raw:
        _closed(raw, "esp")
        path = raw["esp_partition_table"]
        if not isinstance(path, str) or not path.strip():
            raise ImageFault("app_memory_map.esp_partition_table must name a CSV file")
        offset = number(raw.get("offset", 0x8000), "offset")
        return MemoryMap(shape="esp", csv=path, offset=offset)
    if "flash" in raw:
        _closed(raw, "address")
        flash = _region(raw["flash"], "flash")
        app_raw = raw.get("app")
        if not isinstance(app_raw, dict):
            raise ImageFault("app_memory_map.app must be {start, size, vector_at?}")
        unknown = sorted(set(app_raw) - {"start", "size", "vector_at"})
        if unknown:
            raise ImageFault(f"app_memory_map.app sets {unknown}, which this pack does "
                             f"not read (start, size, vector_at)")
        app = _region(app_raw, "app")
        vector = (number(app_raw["vector_at"], "app.vector_at")
                  if "vector_at" in app_raw else None)
        ram = tuple(_region(r, f"ram[{i}]") for i, r in enumerate(raw.get("ram") or ()))
        other = tuple(_region(r, f"other[{i}]") for i, r in enumerate(raw.get("other") or ()))
        if not (flash.start <= app.start and app.end <= flash.end):
            raise ImageFault(f"app_memory_map.app {app} lies outside flash {flash}")
        return MemoryMap(shape="address", flash=flash, app=app, vector_at=vector,
                         ram=ram, other=other)
    if "app" in raw:
        _closed(raw, "slot")
        app_raw = raw["app"]
        if not isinstance(app_raw, dict) or set(app_raw) != {"size"}:
            raise ImageFault("app_memory_map {app: {size}} states a slot size and "
                             "nothing else; give flash too for the address shape")
        size = number(app_raw["size"], "app.size")
        if size <= 0:
            raise ImageFault(f"app_memory_map app.size is {size}")
        return MemoryMap(shape="slot", slot=size)
    raise ImageFault("app_memory_map is none of its three shapes: {flash, app, ram?, "
                     "other?}, {app: {size}} or {esp_partition_table}")


def _closed(raw: dict, shape: str) -> None:
    unknown = sorted(set(raw) - _SHAPE_KEYS[shape])
    if unknown:
        raise ImageFault(f"app_memory_map sets {unknown}, which its {shape} shape does "
                         f"not read ({', '.join(sorted(_SHAPE_KEYS[shape]))})")


# --------------------------------------------------------------------------- #
# ESP partition tables, through ESP-IDF's own parser
# --------------------------------------------------------------------------- #
#: The app subtypes a TEE build reserves (gen_esp32part ``MIN_PARTITION_SUBTYPE_APP_TEE``,
#: two of them). They hold the TEE's own image, not the app's, so the slot left
#: out of the smallest app partition, as ESP-IDF master's ``check_sizes.py`` leaves
#: them out. *Rejected:* v5.5.6's ``check_sizes.py``, which counts them: a TEE slot
#: smaller than the app would read as an app that does not fit.
TEE_SUBTYPES = range(0x30, 0x32)


def esp_slots(csv_text: str, offset: int = 0x8000) -> list[tuple[str, int, int]]:
    """``[(name, subtype, size)]`` of each app partition, as gen_esp32part reads the
    CSV. ``offset`` is the table's own offset (CONFIG_PARTITION_TABLE_OFFSET,
    ESP-IDF's default 0x8000): it moves where partitions with no stated offset
    start, never their sizes."""
    from nopekit.modelio import load_path
    part = load_path(os.path.join(_HERE, "_vendor", "gen_esp32part.py"))
    part.quiet = True
    part.offset_part_table = offset
    try:
        table = part.PartitionTable.from_csv(csv_text)
        table.verify()
    except (part.InputError, ValueError) as exc:
        raise ImageFault(f"the partition table does not parse: {exc}") from exc
    app = part.get_ptype_as_int("app")
    return [(p.name, p.subtype, p.size) for p in table
            if p.type == app and p.subtype not in TEE_SUBTYPES]


# --------------------------------------------------------------------------- #
# the judgment
# --------------------------------------------------------------------------- #
@dataclass
class Fit:
    """One image against its region: the measured flash use in %, the problems that
    fail with no limit, and the line the verdict says."""
    measured: float | None = None
    problems: list = field(default_factory=list)
    said: str = ""


def judge_esp(image: bytes, name: str, slots: list[tuple[str, int, int]],
              csv_rel: str) -> Fit:
    if not slots:
        raise ImageFault(f"{csv_rel} has no app partition")
    if is_elf(image):
        raise ImageFault(f"{name} is an ELF: an ESP image reaches flash as the .bin "
                         f"the build writes beside it (esptool's image); name that .bin "
                         f"in firmware_image")
    if not esp_image(image):
        raise ImageFault(f"{name} is not an ESP app image (its first byte is not 0xE9), "
                         f"so it cannot be judged against an ESP partition table")
    smallest = min(slots, key=lambda s: (s[2], s[0]))
    used = round(100.0 * len(image) / smallest[2], 1)
    short = [s for s in slots if s[2] < len(image)]
    said = (f"{name} {len(image)} B in the smallest app slot {smallest[0]} "
            f"({smallest[2]:#x} B) of {csv_rel}: {used} % flash use")
    fit = Fit(measured=used, said=said)
    if short:
        fit.said += (f"; {', '.join(s[0] for s in short)} cannot take it, so an update "
                     f"into {'it' if len(short) == 1 else 'them'} cannot be written")
    return fit


def judge_slot(image: bytes, name: str, slot: int) -> Fit:
    used = round(100.0 * len(image) / slot, 1)
    return Fit(measured=used, said=f"{name} {len(image)} B in a {slot:#x} B slot: "
                                   f"{used} % flash use")


def writes_of(image: bytes, name: str) -> tuple[list[tuple[int, bytes]], Elf | None]:
    """What an image writes at its addresses, by format; a ``.bin`` has no
    addresses and is placed by the caller."""
    if is_elf(image):
        elf = read_elf(image)
        return [(p, b) for p, _v, filesz, _m, _f, b in elf.loads if filesz > 0], elf
    if is_uf2(image):
        return read_uf2(image), None
    if is_hex(image):
        return read_hex(image), None
    raise ImageFault(f"{name} is not an ELF, a UF2 or an Intel HEX file")


def judge_address(image: bytes, name: str, mm: MemoryMap, elf: Elf | None = None) -> Fit:
    """The address route: the bytes the image writes against ``mm``'s flash, app,
    RAM banks and other ranges. ``elf`` (the image's own, or firmware_elf beside a
    UF2 or HEX) carries the RAM segments and the machine."""
    if image[:1] == b"\xe9" and not is_elf(image):
        raise ImageFault(f"{name} is an ESP app image, which the chip maps through its "
                         f"cache: state the ESP partition table "
                         f"({{esp_partition_table: <csv>}}) or the slot ({{app: {{size}}}})")
    if is_elf(image) or is_uf2(image) or is_hex(image):
        writes, own = writes_of(image, name)
        elf = own or elf
    else:
        writes, elf = [(mm.app.start, image)], elf          # a .bin, from the slot start
    if elf is not None and esp_image(b"", elf):
        raise ImageFault(f"{name} is an ESP image (it links esp_app_desc), which the "
                         f"chip maps through its cache: state the ESP partition table "
                         f"or the slot instead of addresses")
    app, flash = mm.app, mm.flash
    fit = Fit()
    counted_end = None
    for a, b in ranges(writes):
        for lo, hi, where in _split(a, b, app, flash, mm.other):
            if where == "app":
                counted_end = hi if counted_end is None else max(counted_end, hi)
            elif where == "flash":
                fit.problems.append(f"bytes at {lo:#x}..{hi - 1:#x} lie in flash outside "
                                    f"the app region {app}: they would overwrite the "
                                    f"bootloader, the settings or another slot")
            elif where != "other":
                raise ImageFault(f"bytes at {lo:#x}..{hi - 1:#x} lie in no region the "
                                 f"map states (flash {flash}, app {app}"
                                 f"{', other ' + ', '.join(map(str, mm.other)) if mm.other else ''})")
    if counted_end is None:
        fit.problems.append(f"{name} writes no byte inside the app region {app}")
        fit.measured = None
    else:
        fit.measured = round(100.0 * (counted_end - app.start) / app.size, 1)
    if mm.vector_at is not None:
        if elf is not None and elf.machine != EM_ARM:
            raise ImageFault(f"vector_at is stated, and {name} is an ELF for machine "
                             f"{elf.machine}, not Arm: the vector check is Cortex-M's")
        fit.problems += _vector_problems(writes, mm)
    if elf is not None and mm.ram:
        fit.problems += _ram_problems(elf, mm.ram)
    others = [r for r in mm.other
              if any(r.start <= a < r.end or a <= r.start < b for a, b in ranges(writes))]
    fit.said = (f"{name}: {fit.measured} % flash use of app {app}"
                if fit.measured is not None else f"{name}: nothing in app {app}")
    if others:
        fit.said += f" (also writes {', '.join(map(str, others))}, not counted)"
    return fit


def _split(a: int, b: int, app: Region, flash: Region, other: tuple):
    """``[a, b)`` cut at the app's and the flash's edges: ``(lo, hi, where)`` with
    where ``app``, ``flash`` (flash outside the app), ``other`` or ``none``."""
    cuts = sorted({a, b} | {x for x in (app.start, app.end, flash.start, flash.end)
                            if a < x < b})
    for lo, hi in zip(cuts, cuts[1:]):
        if app.start <= lo and hi <= app.end:
            yield lo, hi, "app"
        elif flash.start <= lo and hi <= flash.end:
            yield lo, hi, "flash"
        elif any(r.start <= lo and hi <= r.end for r in other):
            yield lo, hi, "other"
        else:
            yield lo, hi, "none"


def _vector_problems(writes: list[tuple[int, bytes]], mm: MemoryMap) -> list[str]:
    """The Armv7-M exception model (ARM DDI 0403, B1.5.3): word 0 of the vector
    table is the initial SP, word 1 the reset handler with bit 0 set (Thumb).
    A full-descending stack starts one past its RAM's end, as STM32Cube's
    ``_estack = ORIGIN(RAM) + LENGTH(RAM)`` does, so the top of each bank is in."""
    at = mm.vector_at
    sp, reset = word_at(writes, at), word_at(writes, at + 4)
    if sp is None or reset is None:
        return [f"no image bytes at the vector table {at:#x}..{at + 7:#x}: the image is "
                f"linked for another start than the one the bootloader jumps to"]
    if not mm.ram:
        raise ImageFault("vector_at is stated with no ram banks, so the initial stack "
                         "pointer cannot be judged: state ram")
    out = []
    if not any(r.start < sp <= r.end for r in mm.ram):
        out.append(f"the initial stack pointer {sp:#x} at {at:#x} lies in no stated RAM "
                   f"bank ({', '.join(map(str, mm.ram))})")
    if not reset & 1:
        out.append(f"the reset vector {reset:#x} lacks the Thumb bit")
    elif not (mm.app.start <= (reset & ~1) < mm.app.end):
        out.append(f"the reset vector {reset:#x} points outside the app region {mm.app}")
    return out


def _ram_problems(elf: Elf, ram: tuple) -> list[str]:
    """An allocated segment (by VMA, ``p_memsz`` with ``.bss``) that starts in a
    stated RAM bank and runs past its end: firmware built for a bigger part than
    the one the map names."""
    out = []
    for _paddr, vaddr, _filesz, memsz, _flags, _b in elf.loads:
        if memsz <= 0:
            continue
        for bank in ram:
            if bank.start <= vaddr < bank.end and vaddr + memsz > bank.end:
                out.append(f"a segment at {vaddr:#x} of {memsz} B runs {vaddr + memsz - bank.end} "
                           f"B past the RAM bank {bank}: the firmware is built for a "
                           f"bigger part than the one the map states")
    return out
