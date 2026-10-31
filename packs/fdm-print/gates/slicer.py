# SPDX-License-Identifier: Apache-2.0
"""Tier-2 slicer gate: the part slices on the named slicer, and no bridge in the
toolpaths it wrote runs unsupported past the maximum.

OrcaSlicer decides what it can decide: whether the part can be placed and sliced
on the machine, and whether it warns about the part. The rest is glue, and each
piece is here because Orca 2.4.2 was seen to say "Success." over it:

- an 80 mm unsupported bridge slices with return code 0, ``Success.`` and no
  warning (and so did the 80 mm tunnel roof of a part that then failed in print);
- a part whose lowest point is 15 mm above the bed is dropped to the bed and
  sliced, with no warning;
- a misspelled preset key (``enable_suport``) slices ``Success.`` and is simply
  not in the configuration Orca used;
- the first layer over a support is typed ``Bridge`` too, so the longest
  ``Bridge`` line is not the longest unsupported one;
- a span too narrow for bridge infill is printed as walls only, typed
  ``Overhang wall``, and a roof over 0% infill as ``Internal Bridge``: an 80 mm
  free bar 2 mm wide and an 87 mm roof sliced "Success." with no ``Bridge`` line.

So the verdict is bound to the slicer, its version and the three preset files,
and the span is read off Orca's own toolpaths: a bridge line is unsupported where
nothing was printed beneath it.

The slicer runs as a subprocess only (OrcaSlicer is AGPL-3.0); no text of it is
in this pack. ``references/slicer.md`` says how to install it user-space. trimesh
and numpy are imported inside the gate, as in ``gates/mesh.py``.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import re
import subprocess
from collections import defaultdict
from typing import Any

from nopekit.gates import gate, GateContext
from nopekit.modelio import load_path as _load_path
from nopekit.models import Locator, NegativeControl, Tier, Verdict

_HERE = os.path.dirname(os.path.abspath(__file__))
#: The part set and its viewer names, and the one verdict over a set: the same
#: modules ``gates/mesh.py`` loads, by path (see the comment there).
PARTS = _load_path(os.path.join(os.path.dirname(_HERE), "fdm_print_parts.py"))
FOLD = _load_path(os.path.join(_HERE, "fdm_print_fold.py"))
#: The mesh loader ``gates/mesh.py`` hands the memo: one loader, one parse a sweep.
MESH_FILE = _load_path(os.path.join(_HERE, "_mesh_file.py"))

GATE_ID = "fdm.slice"
ORCA = "orca-slicer"

#: The OrcaSlicer releases this gate was written and measured against. Another
#: release is a skip that names it, never a run on trust: the CLI's return codes,
#: ``result.json`` and the G-code's comments are what this gate reads, and none of
#: them is a published interface. Rejected: PrusaSlicer, measured first by the
#: prior survey, because its CLI writes no machine-readable result; and any 2.x
#: (one unchecked release can turn a reader's miss into a pass).
SUPPORTED_ORCA = ("2.4.2",)

#: ``orca-slicer --help`` runs in a container on a host without the GUI
#: libraries (references/slicer.md); 120 s covers a cold container start.
HELP_TIMEOUT_S = 120

#: Per part. Measured: 0.6 s for the pack's clamp and arch, 2.7 s for a 50k-face
#: printed part, plus the container start. Past it the run is errored, never a fail.
SLICE_TIMEOUT_S = 600

#: Return codes that are Orca's judgment of the PART on this machine: a FAIL, in
#: Orca's own words. Read in ``src/libslic3r/Utils.hpp`` and ``src/OrcaSlicer.cpp``
#: at tag v2.4.2. -50 was measured (a 300 mm bar and a 300 mm box on a 220 mm
#: machine, and a part in its assembly frame with ``--arrange 0``); -52 ("Some
#: objects are located over the boundary of the heated bed") and -102 (G-code in
#: an unprintable area) were read, not triggered. Every other nonzero code (-51
#: bad parameters, -5 a bad preset file, -17 a process not compatible with the
#: printer, a signal) is the tool not working: errored, never a fail.
REFUSALS = {-50: "CLI_NO_SUITABLE_OBJECTS", -52: "CLI_OBJECTS_PARTLY_INSIDE",
            -102: "CLI_GCODE_PATH_IN_UNPRINTABLE_AREA"}

PRESET_ROLES = ("machine", "process", "filament")

#: The CONFIG_BLOCK key that names each preset file's ``name``.
SETTINGS_ID = {"machine": "printer_settings_id", "process": "print_settings_id",
               "filament": "filament_settings_id"}

#: Preset keys Orca does not echo into the G-code's CONFIG_BLOCK because they
#: describe the file, not a setting. Measured on the pack's presets and on a
#: complete Ender-3 V2 / PETG set: none of these is in the block. The three
#: ``*_settings_id`` keys are echoed, but as the preset's ``name``, and are checked
#: against it (``SETTINGS_ID``). Every other key must be echoed, or Orca did not
#: use it: a misspelled ``enable_suport`` slices "Success." and is not in the block.
PRESET_META_KEYS = frozenset({
    "type", "name", "from", "instantiation", "setting_id", "filament_id",
    "compatible_printers", *SETTINGS_ID.values()})

#: Keys Orca 2.4.2 reads under another name, ``{old: new}``: transcribed from
#: ``PrintConfigDef::handle_legacy`` in ``src/libslic3r/PrintConfig.cpp`` at tag
#: v2.4.2, the renames that hold for every value. Such a key is not in the block,
#: its new name is, at a value Orca translated, so the gate asks for the new name
#: and not the value. What slipped through before the table: ``wall_infill_order``
#: (in 486 of Orca's own vendor profiles) was reported as "Orca ignored", and
#: measured, Orca used it: ``infill/inner wall/outer wall`` sliced with
#: ``is_infill_first = 1``, ``wall_sequence = inner wall/outer wall``. The renames
#: that hold only for some values (``top_one_wall_type``, ``prime_tower_rib_wall``)
#: are not here: a key Orca drops for a value is a key it did not use.
ORCA_RENAMED = {
    "enable_wipe_tower": "enable_prime_tower", "wipe_tower_width": "prime_tower_width",
    "wiping_volume": "prime_volume", "wipe_tower_brim_width": "prime_tower_brim_width",
    "tool_change_gcode": "change_filament_gcode", "bridge_fan_speed": "overhang_fan_speed",
    "infill_extruder": "sparse_infill_filament_id",
    "sparse_infill_filament": "sparse_infill_filament_id",
    "solid_infill_extruder": "internal_solid_filament_id",
    "solid_infill_filament": "internal_solid_filament_id",
    "top_solid_infill_filament": "top_surface_filament_id",
    "bottom_solid_infill_filament": "bottom_surface_filament_id",
    "perimeter_extruder": "outer_wall_filament_id", "wall_filament": "outer_wall_filament_id",
    "wall_filament_id": "outer_wall_filament_id", "outer_wall_filament": "outer_wall_filament_id",
    "inner_wall_filament": "inner_wall_filament_id",
    "wipe_tower_extruder": "wipe_tower_filament",
    "support_material_extruder": "support_filament",
    "support_material_interface_extruder": "support_interface_filament",
    "support_material_angle": "support_angle",
    "support_material_enforce_layers": "enforce_support_layers",
    "inherits_cummulative": "inherits_group",
    "compatible_printers_condition_cummulative": "compatible_machine_expression_group",
    "compatible_prints_condition_cummulative": "compatible_process_expression_group",
    "cooling": "slow_down_for_layer_cooling", "timelapse_no_toolhead": "timelapse_type",
    "wall_infill_order": "wall_sequence",
    "rotate_solid_infill_direction": "solid_infill_rotate_template",
    "sparse_infill_anchor": "infill_anchor", "sparse_infill_anchor_max": "infill_anchor_max",
    "chamber_temperatures": "chamber_temperature", "thumbnail_size": "thumbnails",
    "initial_layer_flow_ratio": "bottom_solid_infill_flow_ratio",
    "ironing_direction": "ironing_angle",
    "counterbole_hole_bridging": "counterbore_hole_bridging",
    "prime_tower_extra_rib_length": "wipe_tower_extra_rib_length",
    "prime_tower_rib_width": "wipe_tower_rib_width",
    "prime_tower_fillet_wall": "wipe_tower_fillet_wall",
    "extruder_clearance_max_radius": "extruder_clearance_radius",
    "machine_switch_extruder_time": "machine_tool_change_time",
}

#: Keys Orca 2.4.2 drops by name as obsolete: the ``ignore`` set of the same
#: function, transcribed. A preset exported from Orca's own profiles carries some
#: of them (``adaptive_layer_height`` is in 476 vendor profiles), and no setting in
#: Orca reads them, so a person cannot have meant one: the gate names them in its
#: evidence and goes on. Any other key not in the block is still an error: Orca
#: drops an unknown key in the same branch, so a misspelling and a key Orca never
#: had read the same, and only the person can say which.
ORCA_RETIRED = frozenset({
    "acceleration", "scale", "rotate", "duplicate", "duplicate_grid", "bed_size",
    "print_center", "g0", "wipe_tower_per_color_wipe", "support_sharp_tails",
    "support_remove_small_overhangs", "support_with_sheath",
    "tree_support_collision_resolution", "tree_support_with_infill",
    "max_volumetric_speed", "max_print_speed", "support_closing_radius",
    "remove_freq_sweep", "remove_bed_leveling", "remove_extrusion_calibration",
    "support_transition_line_width", "support_transition_speed", "bed_temperature",
    "bed_temperature_initial_layer", "can_switch_nozzle_type", "can_add_auxiliary_fan",
    "extra_flush_volume", "spaghetti_detector", "adaptive_layer_height", "z_hop_type",
    "z_lift_type", "bed_temperature_difference", "long_retraction_when_cut",
    "retraction_distance_when_cut", "internal_bridge_support_thickness",
    "top_area_threshold", "reduce_wall_solid_infill", "filament_load_time",
    "filament_unload_time", "smooth_coefficient", "overhang_totally_speed", "silent_mode",
    "overhang_speed_classic", "filament_prime_volume"})

#: Roles that are not the part, for "the G-code is this part": the bed adhesion,
#: the start and end G-code, the towers and every support role (``Support``,
#: ``Support interface``, ``Support transition``).
NOT_THE_PART = ("Brim", "Skirt", "Custom", "Prime tower", "Wipe tower")

#: How far the extents of the part's own extrusions may be from the mesh's, mm,
#: per axis. Measured: |delta| 0.41-0.45 mm in X and Y (one outer line width: the
#: wall's centre line runs half a width inside the surface on each side) on a cube,
#: an 80 mm bridge and a 50k-face part; 0.00 in Z on 4 parts. Rejected: 0.5, too
#: close to 0.45.
EXTENT_TOL_MM = 1.0

#: The toolpath roles the bridge reader measures: every extrusion Orca lays over
#: air. What slipped through on ``Bridge`` alone, both on Orca 2.4.2 with the
#: pack's presets: a bar 2 mm wide spanning 80 mm is too narrow for bridge infill
#: and prints as walls typed ``Overhang wall``, with no ``Bridge`` line (the gate
#: read 0.0, fdm.bridge_span 80.0; read with this set, 79.85 mm); and a closed
#: 90 mm block at 0% infill roofs its hollow with ``Internal Bridge`` (0.0, now
#: 87.06 mm). Measured on 84 G-codes from earlier runs: no part that read under the
#: pack's 30 mm on ``Bridge`` alone reads over it now; the largest rises are 0.0 to 15.83
#: mm (a part on tree support, overhang walls at a support tip) and 0.0 to 3.5 mm
#: (a roof over 35% infill, the gap between two infill lines).
BRIDGE_ROLES = frozenset({"Bridge", "Internal Bridge", "Overhang wall"})

#: The grid the bridge reader marks printed material on, mm, about one line width
#: (0.42 mm on every preset measured). Measured on 12 G-codes at 0.25, 0.5 and 1.0
#: mm: 0.5 reads 10.01, 80.01 and 89.96 mm on drawn 10, 80 and 90 mm spans. *Rejected:*
#: 0.25 (finer than the line spacing, so a line beside a printed one reads free: a
#: part on support read 82.50 mm where 0.5 reads 15.83); 1.0 (the one-cell
#: neighbourhood then holds a line on material up to 2 mm aside: a free 80.05 mm
#: roof read 79.05 and an 87.06 mm one 86.06, short by a cell).
CELL_MM = 0.5
#: How finely a bridge line is walked, mm: half a cell, so no cell along a line is
#: stepped over. Measured on the same 12: 0.125 moves no span by more than 0.13 mm
#: and doubles the walk. *Rejected:* 0.5, a cell (spans move by up to 0.30 mm).
STEP_MM = 0.25
#: A run ends at a turn sharper than 30 deg (cos 30 = 0.866): a run is one span,
#: and the next line of a zig-zag is another. Measured on the same 12 at 15, 30, 45
#: and 60 deg: every span the same. 30 is not what any number here rests on; the
#: run also ends wherever the line is held.
TURN_COS = 0.866
#: Added to the window below a bridge, mm, so a layer exactly at its edge counts.
WINDOW_PAD_MM = 0.05


def _skip(reason: str) -> Verdict:
    return Verdict(gate=GATE_ID, passed=False, skipped=True, skip_reason=reason)


def _error(message: str, evidence=None) -> Verdict:
    return Verdict(gate=GATE_ID, passed=False, error=message, evidence=list(evidence or []))


class _Errored(Exception):
    """The slicer did not run as asked: the part's verdict is errored."""


class _Failed(Exception):
    """A finding about the part, with no span to report: the part fails."""


def _plus_z(axis: Any) -> bool:
    """Is a stated build axis +Z? Read by the pack's one parser
    (``fdm_print_parts.direction``), so the slicer and the mesh gates agree."""
    vec = PARTS.direction(axis)
    norm = math.sqrt(sum(c * c for c in vec)) if vec else 0.0
    return norm > 0 and abs(vec[0]) / norm < 1e-9 and abs(vec[1]) / norm < 1e-9 and vec[2] > 0


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _presets(ctx: GateContext):
    """``({role: (name, bytes, data)}, None)`` or ``(None, verdict)``."""
    raw = ctx.first_pack_param(("slicer_presets",), None)
    if not isinstance(raw, dict):
        return None, _skip("no slicer_presets: the slicer gate binds its verdict to a named "
                           "preset and will not invent one. State {machine, process, filament}: "
                           "paths to three complete OrcaSlicer preset files "
                           "(references/slicer.md)")
    out = {}
    for role in PRESET_ROLES:
        rel = raw.get(role)
        if not rel:
            return None, _skip(f"slicer_presets names no {role} preset; all three are needed")
        path = str(rel) if os.path.isabs(str(rel)) else os.path.join(ctx.root or os.curdir, str(rel))
        if not os.path.isfile(path):
            return None, _skip(f"slicer_presets.{role} points at {path}, which does not exist")
        with open(path, "rb") as fh:
            data = fh.read()
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            return None, _error(f"slicer_presets.{role} ({rel}) is not JSON: {exc}")
        if not isinstance(parsed, dict) or not parsed.get("name"):
            return None, _error(f"slicer_presets.{role} ({rel}) is not a preset with a name")
        out[role] = (str(parsed["name"]), data, parsed)
    return out, None


def _version(ctx: GateContext) -> tuple[str, str]:
    """``(version, banner)``: line 1 of ``orca-slicer --help`` is ``OrcaSlicer-<v>:``.

    Raises ``_Errored`` when ``--help`` exits nonzero or prints no such banner: the
    slicer did not start. What slipped through: a slicer on PATH that could not
    load its libraries printed its loader error, exited 127, and read as a quiet
    skip, "measured on OrcaSlicer 2.4.2 only", a crash in the words of an
    unsupported release. A banner that names another release is a skip."""
    try:
        proc = subprocess.run([ORCA, "--help"], cwd=ctx.out_path(), stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              errors="replace", timeout=HELP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise _Errored(f"{ORCA} --help ran past {HELP_TIMEOUT_S} s")
    first = (proc.stdout or "").strip().splitlines()[:1]
    banner = first[0] if first else ""
    m = re.fullmatch(r"OrcaSlicer-(\S+):", banner.strip())
    if proc.returncode != 0 or not m:
        raise _Errored(f"{ORCA} --help exited {proc.returncode} and printed "
                       f"{banner[:80]!r}, not an OrcaSlicer banner: the slicer did not start")
    return m.group(1), banner


def _fresh(path: str) -> None:
    """Empty ``path`` by ``rm -rf``: a stale ``result.json`` must never be read as
    this run's. Not ``shutil.rmtree``, which the verdict trace reads as reads of
    the project (cfd-openfoam's ``_tool.stage`` says how that was found)."""
    removed = subprocess.run(["rm", "-rf", "--", path], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if removed.returncode != 0 or os.path.lexists(path):
        raise _Errored(f"rm could not empty {path}")
    os.makedirs(path)


def _config(lines: list[str]) -> dict[str, str]:
    """The ``; key = value`` lines between CONFIG_BLOCK_START and _END."""
    out: dict[str, str] = {}
    inside = False
    for line in lines:
        if line.startswith("; CONFIG_BLOCK_START"):
            inside = True
        elif line.startswith("; CONFIG_BLOCK_END"):
            break
        elif inside and line.startswith("; ") and " = " in line:
            key, _, value = line[2:].partition(" = ")
            out.setdefault(key.strip(), value)
    return out


def _echoed(value: str) -> str:
    """A preset string as the CONFIG_BLOCK spells it: newlines escaped, one pair of
    quotes removed (measured: ``filament_settings_id = "nk test PETG"``)."""
    text = value.replace("\n", "\\n").strip()
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] == '"' else text


def _unset(value: Any) -> bool:
    """``"nil"`` (or a list of them) is a filament preset's "not overridden": Orca
    does not echo it. Measured on the Ender-3 V2 PETG preset (13 keys)."""
    items = value if isinstance(value, list) else [value]
    return bool(items) and all(str(v).strip() == "nil" for v in items)


def _binding(config: dict, presets: dict) -> list[str]:
    """Every preset key reached the configuration Orca used, at the preset's value.
    Returns the retired keys it passed over (``ORCA_RETIRED``), for the evidence."""
    retired = []
    for role, (name, _data, parsed) in presets.items():
        said = _echoed(config.get(SETTINGS_ID[role], ""))
        if said != name.strip():
            raise _Errored(f"{SETTINGS_ID[role]} in the G-code is {said!r}, not the {role} "
                           f"preset's name {name!r}")
        for key, value in parsed.items():
            if key in PRESET_META_KEYS or _unset(value):
                continue
            if key in ORCA_RETIRED and key not in config:
                retired.append(f"{role}.{key}")
                continue
            if key in ORCA_RENAMED and key not in config:
                if ORCA_RENAMED[key] not in config:
                    raise _Errored(f"Orca reads {key} (the {role} preset's) as "
                                   f"{ORCA_RENAMED[key]}, and the configuration the G-code "
                                   f"was sliced with has neither")
                continue
            if key not in config:
                raise _Errored(f"Orca did not use {key} (the {role} preset's): it is not in "
                               f"the configuration the G-code was sliced with, and Orca "
                               f"{SUPPORTED_ORCA[0]} neither renames nor retires it, so it "
                               f"is misspelled or not a key this release has; correct it or "
                               f"remove it")
            # A list value is compared for presence only: Orca echoes some lists
            # joined by commas and others quoted, per key, so a value test there
            # would be this gate's guess at Orca's format.
            if isinstance(value, str) and _echoed(value) != _echoed(config[key]):
                raise _Errored(f"Orca used {key} = {config[key].strip()[:60]!r} where the "
                               f"{role} preset says {value.strip()[:60]!r}")
    return retired


def _read_gcode(path: str) -> dict:
    """What this gate reads off Orca's G-code, in one pass. Decides nothing."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    header = next((m.group(1) for m in (re.match(r"; generated by OrcaSlicer (\S+) on", ln)
                                        for ln in lines[:20]) if m), "")
    config = _config(lines)
    x = y = z = 0.0
    role = ""
    arcs = relative = absolute = 0
    printed: dict[float, set] = defaultdict(set)
    moves = []
    box = [math.inf, math.inf, -math.inf, -math.inf, -math.inf]   # x0 y0 x1 y1 z1
    for line in lines:
        if line.startswith(";TYPE:"):
            role = line[6:].strip()
            continue
        if line.startswith("M83"):
            relative += 1
        elif line.startswith("M82"):
            absolute += 1
        if line.startswith(("G2 ", "G3 ")):
            arcs += 1
            continue
        if not line.startswith(("G0 ", "G1 ")):
            continue
        words = dict(re.findall(r"([XYZE])(-?\d*\.?\d+)", line.split(";", 1)[0]))
        nx, ny = float(words.get("X", x)), float(words.get("Y", y))
        nz = float(words.get("Z", z))
        laid = float(words.get("E", 0.0)) > 0 and ("X" in words or "Y" in words)
        if laid and role != "Custom":
            n = max(1, int(math.hypot(nx - x, ny - y) / (CELL_MM / 2)))
            cells = printed[round(z, 3)]
            for i in range(n + 1):
                t = i / n
                cells.add((math.floor((x + (nx - x) * t) / CELL_MM),
                           math.floor((y + (ny - y) * t) / CELL_MM)))
            if role not in NOT_THE_PART and not role.startswith("Support"):
                box = [min(box[0], x, nx), min(box[1], y, ny), max(box[2], x, nx),
                       max(box[3], y, ny), max(box[4], z)]
        moves.append((laid, role, z, x, y, nx, ny))
        x, y, z = nx, ny, nz
    return {"header": header, "config": config, "arcs": arcs, "relative": relative,
            "absolute": absolute, "printed": printed, "moves": moves, "box": box}


def _worst_run(g: dict) -> tuple[float, tuple, float]:
    """``(span, (z, x, y), window)``: the longest unsupported run of bridge lines
    (``BRIDGE_ROLES``), and the middle of it.

    A run is consecutive extrusions of those roles with no travel between them
    and no turn past 30 deg; it ends at a travel, at any other role, and on the
    first layer (the bed holds it). A point of the run is held when any cell within
    one cell of it was printed in a layer less than WINDOW below it, where WINDOW is
    two layers plus the support's top gap (both from Orca's own configuration) plus
    ``WINDOW_PAD_MM``: 0.65 mm on every preset measured. The span is the longest
    unheld stretch plus 2 cells, because the one-cell neighbourhood trims one cell
    at each end (without it the reader under-reads by 1.04-1.07 mm).

    Measured against drawn spans: 9.91 for 10 mm, 79.96 for 80, 18.02 for the
    baseline clamp's 18 mm ceiling, 89.96 for the 90 mm arch, 79.85 for an 80 mm
    bar printed as overhang walls; 0.0 on ``Bridge`` lines over support, and at
    most 15.83 mm on any supported part (an overhang wall at a support tip).
    *Rejected*, all measured: the longest Bridge segment (150.34 mm
    on a part whose bridges were all supported: Orca types the first layer over a
    support Bridge); a free run with no one-cell neighbourhood (79.55 mm and 9.04 mm
    read on supported parts: the bridge line runs between the support-interface
    lines, parallel to them); a 2.0 mm window (it held a free 82 mm roof on the
    print two layers below it)."""
    cfg = g["config"]
    layer = float(cfg.get("layer_height", "0.2"))
    window = 2 * layer + float(cfg.get("support_top_z_distance", "0") or 0) + WINDOW_PAD_MM
    first = float(cfg.get("initial_layer_print_height", layer) or layer)
    printed = g["printed"]
    zs = sorted(printed)
    best, where = 0.0, (0.0, 0.0, 0.0)
    run, start, last_end, last_dir = 0.0, None, None, None
    for laid, role, mz, x0, y0, x1, y1 in g["moves"]:
        if not laid or role not in BRIDGE_ROLES or mz <= first + 1e-6:
            run, last_end, last_dir = 0.0, None, None
            continue
        d = (x1 - x0, y1 - y0)
        length = math.hypot(*d)
        if last_end != (x0, y0) or (last_dir and d[0] * last_dir[0] + d[1] * last_dir[1]
                                    < TURN_COS * length * math.hypot(*last_dir)):
            run = 0.0
        last_dir, last_end = d, (x1, y1)
        below = [printed[k] for k in zs[bisect.bisect_left(zs, mz - window - 1e-6):
                                        bisect.bisect_left(zs, mz - 1e-6)]]
        n = max(1, int(length / STEP_MM))
        for i in range(1, n + 1):
            px, py = x0 + d[0] * i / n, y0 + d[1] * i / n
            cx, cy = math.floor(px / CELL_MM), math.floor(py / CELL_MM)
            held = any((cx + dx, cy + dy) in cells for cells in below
                       for dx in (-1, 0, 1) for dy in (-1, 0, 1))
            if held:
                run = 0.0
                continue
            start = (px, py) if run == 0.0 else start
            run += length / n
            if run + 2 * CELL_MM > best:
                best = run + 2 * CELL_MM
                where = (mz, (start[0] + px) / 2, (start[1] + py) / 2)
    return round(best, 2), where, round(window, 3)


def _slice(ctx: GateContext, part, mesh, presets: dict, version: str, limit: float):
    """One part through Orca: a ``FOLD`` outcome, or raises ``_Failed`` / ``_Errored``."""
    work = ctx.out_path("fdm-slice", part.name)
    _fresh(work)
    mesh.export(os.path.join(work, "part.stl"))
    for role, (_name, data, _parsed) in presets.items():
        with open(os.path.join(work, f"{role}.json"), "wb") as fh:
            fh.write(data)
    # Relative paths and the part's own directory as cwd, so a wrapper that runs
    # Orca in a container needs to mount nothing but $PWD.
    argv = [ORCA, "--slice", "0", "--load-settings", "machine.json;process.json",
            "--load-filaments", "filament.json", "--outputdir", "out", "part.stl"]
    log = os.path.join(work, "orca.log")
    try:
        with open(log, "w", encoding="utf-8") as fh:
            proc = subprocess.run(argv, cwd=work, stdin=subprocess.DEVNULL, stdout=fh,
                                  stderr=subprocess.STDOUT, timeout=SLICE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise _Errored(f"{ORCA} ran past {SLICE_TIMEOUT_S} s and was killed")
    result_path = os.path.join(work, "out", "result.json")
    if not os.path.isfile(result_path):
        raise _Errored(f"{ORCA} exited {proc.returncode} and wrote no out/result.json")
    with open(result_path, encoding="utf-8") as fh:
        result = json.load(fh)
    rc = result.get("return_code") if isinstance(result, dict) else None
    if not isinstance(rc, int) or isinstance(rc, bool):
        raise _Errored(f"{ORCA}'s result.json states no integer return_code")
    words = str(result.get("error_string", "")).strip()
    if proc.returncode < 0 or proc.returncode != rc % 256:
        # Measured: -50 exits 206 and -51 exits 205. Anything else is not a run
        # that wrote this result.json.
        raise _Errored(f"{ORCA} exited {proc.returncode} but its result.json says "
                       f"return_code {rc}")
    if rc in REFUSALS:
        raise _Failed(f"Orca refused it: {rc} {REFUSALS[rc]} \"{words}\"")
    if rc != 0:
        raise _Errored(f"{ORCA} returned {rc} \"{words}\" — the tool, not the part")
    found = sorted(f for f in os.listdir(os.path.join(work, "out")) if f.endswith(".gcode"))
    if not found:
        raise _Errored(f"{ORCA} returned 0 and wrote no G-code file")
    gcode = os.path.join(work, "out", found[0])
    g = _read_gcode(gcode)
    if g["header"] != version:
        raise _Errored(f"the G-code says it was generated by OrcaSlicer {g['header'] or '?'}; "
                       f"--help said {version}")
    cfg = g["config"]
    if g["arcs"] or cfg.get("enable_arc_fitting", "0").strip() == "1":
        raise _Errored("the G-code holds arc moves: set enable_arc_fitting to 0; this "
                       "reader follows straight moves only")
    if not g["relative"] or g["absolute"]:
        raise _Errored("the G-code does not use relative extrusion (M83) throughout; this "
                       "reader takes E > 0 as material laid")
    retired = _binding(cfg, presets)
    warned = [str(p.get("warning_message", "")).strip()
              for p in result.get("sliced_plates", []) if isinstance(p, dict)]
    warned = [w for w in warned if w]
    if warned:
        raise _Failed(f"Orca warned: \"{warned[0][:200]}\"")
    import numpy as np                                # noqa: PLC0415 - lazy on purpose

    lo, hi = np.asarray(mesh.bounds, dtype=float)
    box = g["box"]
    if not math.isfinite(box[0]):
        raise _Failed("the G-code lays no extrusion of the part itself")
    got = (box[2] - box[0], box[3] - box[1], box[4])
    want = tuple(float(v) for v in hi - lo)
    if any(abs(a - b) > EXTENT_TOL_MM for a, b in zip(got, want)):
        raise _Failed(f"the G-code is not this part: its extrusions span "
                      f"{got[0]:.1f} x {got[1]:.1f} x {got[2]:.1f} mm, the mesh "
                      f"{want[0]:.1f} x {want[1]:.1f} x {want[2]:.1f} mm")
    span, (wz, wx, wy), window = _worst_run(g)
    # Orca places the part on its bed; the point goes back to the mesh's frame by
    # the centres of the two boxes (the half-width inset cancels).
    at = [round(wx - (box[0] + box[2]) / 2 + float(lo[0] + hi[0]) / 2, 2),
          round(wy - (box[1] + box[3]) / 2 + float(lo[1] + hi[1]) / 2, 2), round(wz, 2)]
    evidence = os.path.join(work, "fdm-slice.json")
    with open(evidence, "w", encoding="utf-8") as fh:
        json.dump({"slicer": f"OrcaSlicer {version}",
                   "presets": {r: {"name": n, "sha256": _sha(d)}
                               for r, (n, d, _p) in presets.items()},
                   "config_block_sha256": _sha(json.dumps(cfg, sort_keys=True).encode()),
                   "return_code": rc, "error_string": words, "warning_message": "",
                   "retired_keys_not_used": retired,
                   "window_mm": window, "worst_span_mm": span, "worst_at_mm": at,
                   "limit_mm": limit, "gcode": gcode}, fh, indent=1)
    note = (f"at {span:.1f} mm unsupported bridge at z {wz:.2f} vs {limit:.0f} mm"
            if span else "has no unsupported bridge line")
    locators = [Locator(view=PARTS.VIEW_ID, target=part.node, kind="point", position=at,
                        value=span, label=f"{span:.1f} mm unsupported bridge on OrcaSlicer "
                                          f"{version} vs {limit:.0f} mm")] if span > limit else []
    return FOLD.measured_part(part.name, score=span / limit, measured=span, limit=limit,
                              note=note, path=gcode, row=f"{span:.2f}\t{limit:.2f}\t{at}",
                              locators=locators), evidence


@gate(
    id=GATE_ID,
    title="The part slices on the named slicer with no unsupported bridge past the maximum",
    # A tag of its own, on purpose: check's default tier is 0, and an unrun or
    # skipped gate keeps its claim from reading Checked. Under the fdm tags every
    # fdm claim in every project would read Open below tier 2, and Skipped wherever
    # Orca is absent. A project opts in by tagging a claim `slicer`.
    claims=["slicer"],
    tier=Tier.SOLVE,
    # The string fdm.bridge_span settles, so claims.cross_check holds a claim on
    # "bridge span" with a tighter limit against both gates.
    settles="bridge span",
    requires_tools=[ORCA],
    requires_python=["trimesh", "numpy"],
    # Prerequisite fdm.process_model_valid: the units and the process envelope.
    #    Isolated: it passes long_bridge (test_packs.ControlsAreIsolated).
    needs=["fdm.process_model_valid"],
    basis="model-only",
    negative_control=NegativeControl(
        fixture="selftest/bad_meshes.py:long_bridge",
        note="the plate of clamp, 90 mm arch, clamp that fdm.bridge_span fails: Orca "
             "slices the arch 'Success.' with no warning, and its own toolpaths lay a "
             "~90 mm bridge line over nothing, three times the maximum",
    ),
)
def slice_part(ctx: GateContext) -> Verdict:
    """Slice each part on OrcaSlicer with the project's three presets, then read
    the toolpaths Orca wrote.

    Per part, in this order: the mesh must be in print pose (its lowest point at
    z = 0); the slicer's ``--help`` banner must name a supported version; Orca
    slices it in a fresh directory; a refusal (-50, -52, -102) or a warning in
    ``result.json`` is a FAIL in Orca's words; the G-code must say the same
    version, use relative extrusion and straight moves only, and hold every
    preset key at the preset's value; the extents of the part's own extrusions
    must be the mesh's; and the longest unsupported run of Bridge lines is the
    measured value, against ``max_bridge_mm`` (the key ``fdm.bridge_span`` reads).
    A part that fails is a FAIL even when another part's run errored: a known
    failure is never filed under a tool's.

    What this does NOT settle: a thin feature Orca drops from the toolpaths
    (a 0.15 mm fin gives no extrusion and no warning), the time and mass Orca
    estimates, or a bridge over the sparse tips of tree supports (the reader may
    read it long; a person must look at the preview there).
    """
    limit = ctx.first_pack_param(("max_bridge_mm", "bridge_limit_mm"), None)
    if not isinstance(limit, (int, float)) or isinstance(limit, bool) or limit <= 0:
        return _skip("no max_bridge_mm: the slicer gate judges the unsupported bridge "
                     "against the maximum the project states, and will not invent one")
    limit = float(limit)
    axis = ctx.first_pack_param(PARTS.BUILD_AXIS_KEYS, None)
    if axis is not None and not _plus_z(axis):
        return _skip(f"build_axis is {axis!r}: Orca slices along +Z, so the mesh must be "
                     f"exported in print pose with +Z up")
    presets, bail = _presets(ctx)
    if bail is not None:
        return bail

    parts = PARTS.part_set(ctx)
    if parts.problem:
        return _skip(parts.problem)
    if not (parts and PARTS.has_geometry(parts)):
        raw = PARTS.mesh_path(ctx)
        if not raw:
            return _skip("the projection has no mesh_path and no mesh_paths set: the "
                         "slicer needs the exported part")
        parts = PARTS.PartSet(parts=(PARTS.Part(name=PARTS.mover(ctx), raw=raw),),
                              source="mesh_path")

    outcomes, failed, errored, evidence, todo = [], [], [], [], []
    for part in parts:
        path = part.raw if os.path.isabs(part.raw) else os.path.join(ctx.root or os.curdir, part.raw)
        parsed = (MESH_FILE.parse_mesh(os.path.abspath(path)) if getattr(ctx, "memo", None) is None
                  else ctx.load_file(path, loader=MESH_FILE.parse_mesh)) if part.raw else None
        mesh = parsed[0] if parsed else None
        if mesh is None:
            why = parsed[2] if parsed else f"no mesh at {part.raw or '(none)'}"
            outcomes.append(FOLD.skipped_part(part.name, why))
            continue
        zmin = float(mesh.bounds[0][2])
        if abs(zmin) > PARTS.BED_TOLERANCE_MM:
            failed.append((part.name, f"not in print pose: z-min {zmin:.2f} mm; the "
                                      f"slicer drops it to the bed and says nothing"))
            continue
        todo.append((part, mesh))

    version = ""
    if todo:
        try:
            version, banner = _version(ctx)
        except _Errored as exc:
            return _error(str(exc))
        if version not in SUPPORTED_ORCA:
            if failed:
                failed.append(("", f"{len(todo)} other part(s) not sliced: {banner[:40]!r} "
                                   f"is not a supported OrcaSlicer"))
            else:
                return _skip(f"{ORCA} --help says {banner[:80]!r}; this gate is measured on "
                             f"OrcaSlicer {', '.join(SUPPORTED_ORCA)} only")
            todo = []
    for part, mesh in todo:
        try:
            outcome, file = _slice(ctx, part, mesh, presets, version, limit)
        except _Failed as exc:
            failed.append((part.name, str(exc)))
            continue
        except _Errored as exc:
            errored.append(f"{part.name}: {exc}")
            continue
        outcomes.append(outcome)
        evidence.append(file)

    bound = (f"OrcaSlicer {version}" if version else "no slicer run") + ", presets " + \
        ", ".join(repr(presets[r][0]) for r in PRESET_ROLES)
    # A settled failure first: an errored part beside it is named, never louder.
    # What slipped through with the order the other way: part A out of print pose
    # and part B's run timed out read errored, so the claim read Skipped where A
    # had already failed (gates/fdm_print_fold.py orders it this way too).
    if failed:
        also = (f"; {len(errored)} part(s) errored, first {errored[0]}" if errored else "")
        return Verdict(gate=GATE_ID, passed=False, measured=None,
                       detail=(f"{sum(1 for n, _ in failed if n)} of {len(parts)} part(s) in "
                               f"`{parts.source}` fail ({bound}): "
                               + "; ".join(f"{n}: {w}" if n else w for n, w in failed[:2])
                               + also)[:480],
                       evidence=evidence,
                       locators=[Locator(view=PARTS.VIEW_ID, target=PARTS.node(n), kind="part",
                                         label=w[:120])
                                 for n, w in failed[:FOLD.MAX_PART_LOCATORS] if n])
    if errored:
        return _error(f"{errored[0]}" + (f" (and {len(errored) - 1} more)" if len(errored) > 1
                                         else ""), evidence)
    table = FOLD.write_table(ctx, "fdm-slice-parts.txt", GATE_ID, parts.source, outcomes,
                             header_lines=[bound, f"bridges judged at {limit:g} mm"],
                             columns="worst_span_mm\tlimit_mm\tat_mm")
    return FOLD.fold(GATE_ID, outcomes, source=parts.source, units="mm",
                     quantity="unsupported bridge span", evidence=[table, *evidence[:3]],
                     extra_detail=bound)
