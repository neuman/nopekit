# SPDX-License-Identifier: Apache-2.0
"""Tier-2 slicer gate: the part slices on the named slicer, and no bridge in the
toolpaths it wrote runs unsupported past the maximum.

OrcaSlicer decides what it can decide: whether the part can be placed and sliced
on the machine, and whether it warns about the part. The rest is glue, and each
piece is here because Orca 2.4.2 was seen to say "Success." over it:

- an 80 mm unsupported bridge slices with return code 0, ``Success.`` and no
  warning, though a span that long sags in print;
- a part whose lowest point is 15 mm above the bed is dropped to the bed and
  sliced, with no warning;
- a misspelled preset key (``enable_suport``) slices ``Success.`` and is simply
  not in the configuration Orca used;
- the first layer over a support is typed ``Bridge`` too, so the longest
  ``Bridge`` line is not the longest unsupported one;
- a span too narrow for bridge infill is printed as walls only, typed
  ``Overhang wall``, and a roof over 0% infill as ``Internal Bridge``: an 80 mm
  free bar 2 mm wide and an 87 mm roof sliced "Success." with no ``Bridge`` line.

So the verdict is bound to the slicer, its version and the three preset files
each part sliced with, and the span is read off Orca's own toolpaths: a bridge
line is unsupported where nothing was printed beneath it.

A project names the presets per part when one triple does not fit every part
(``_assign``): a part that must print on supports, in another material or on
another printer was judged on the one global triple, so its verdict was about a
print nobody would make.

The slicer runs as a subprocess only (OrcaSlicer is AGPL-3.0); no text of it is
in this pack. ``references/slicer.md`` says how to install it user-space. trimesh
and numpy are imported inside the gate, as in ``gates/mesh.py``.
"""
from __future__ import annotations

import bisect
import difflib
import fnmatch
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

#: Per part. Measured: 0.6 s for the pack's clamp and arch, plus the container
#: start. 600 s leaves room for a part of many more faces and a cold container
#: (``HELP_TIMEOUT_S``). The first runs also timed a part from outside the pack; it
#: was not measured again on the pack's probes (no OrcaSlicer 2.4.2 on the machine
#: that removed it), so no figure from it is given here. Past it the run is
#: errored, never a fail.
SLICE_TIMEOUT_S = 600

#: Return codes that are Orca's judgment of the PART on this machine: a FAIL, in
#: Orca's own words. Read in ``src/libslic3r/Utils.hpp`` and ``src/OrcaSlicer.cpp``
#: at tag v2.4.2. -50 was measured on a 300 mm bar and a 300 mm box on a 220 mm
#: machine; a third case, on a part from outside the pack, was not measured again
#: and is not given here. -52 ("Some objects are located over the boundary of the
#: heated bed") and -102 (G-code in an unprintable area) were read, not triggered.
#: Every other nonzero code (-51 bad parameters, -5 a bad preset file, -17 a
#: process not compatible with the printer, a signal) is the tool not working:
#: errored, never a fail.
REFUSALS = {-50: "CLI_NO_SUITABLE_OBJECTS", -52: "CLI_OBJECTS_PARTLY_INSIDE",
            -102: "CLI_GCODE_PATH_IN_UNPRINTABLE_AREA"}

PRESET_ROLES = ("machine", "process", "filament")

#: What makes a ``slicer_presets`` key a glob, matched by ``fnmatch.fnmatchcase``
#: (case-sensitive, so a key reads the same on every host). A key with none of these
#: names one part exactly. A part's name never holds one: ``fdm_print_parts.sanitise``
#: keeps letters, digits, ``-``, ``.`` and ``_`` only, so a key is a glob or a name,
#: never both. *Rejected:* ``fnmatch.fnmatch``, which folds case on Windows only.
GLOB_CHARS = frozenset("*?[")

#: The CONFIG_BLOCK key that names each preset file's ``name``.
SETTINGS_ID = {"machine": "printer_settings_id", "process": "print_settings_id",
               "filament": "filament_settings_id"}

#: Preset keys Orca does not echo into the G-code's CONFIG_BLOCK because they
#: describe the file, not a setting. Measured on the pack's presets and on one
#: complete, flattened printer and filament set: none of these is in the block.
#: The three ``*_settings_id`` keys are echoed, but as the preset's ``name``, and
#: are checked against it (``SETTINGS_ID``). Every other key must be echoed, or
#: Orca did not use it: a misspelled ``enable_suport`` slices "Success." and is
#: not in the block.
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
#: per axis. What it allows for: the outer wall's centre line runs half a line
#: width inside the surface on each side, so in X and Y the extrusions span about
#: one line width less than the mesh (the pack's process preset sets
#: ``line_width`` 0.42 mm), and in Z they reach the mesh's top. The readings that
#: first showed this were taken on a cube and an 80 mm bridge together with parts
#: from outside the pack; they were not measured again on the pack's probes alone
#: (no OrcaSlicer 2.4.2 on the machine that removed them), so no figure from them
#: is given here. *Rejected:* 0.5, too close to one line width: a wider wall line
#: would read as another part.
EXTENT_TOL_MM = 1.0

#: The toolpath roles the bridge reader measures: every extrusion Orca lays over
#: air. What slipped through on ``Bridge`` alone, both on Orca 2.4.2 with the
#: pack's presets: a bar 2 mm wide spanning 80 mm is too narrow for bridge infill
#: and prints as walls typed ``Overhang wall``, with no ``Bridge`` line (the gate
#: read 0.0, fdm.bridge_span 80.0; read with this set, 79.85 mm); and a closed
#: 90 mm block at 0% infill roofs its hollow with ``Internal Bridge`` (0.0, now
#: 87.06 mm). The two other roles also lay lines where something is printed
#: beneath: an overhang wall at the tip of a support, and a roof line across the
#: gap between two lines of sparse infill. Where nothing is printed within a cell
#: below such a line, the reader reads that stretch as a free run, short or long:
#: over sparse support tips a person must look at the slicer's preview ("does NOT
#: settle" in ``slice_part``). The earlier runs that first checked this held parts
#: from outside the pack. They were not measured again on the pack's own probes
#: (no OrcaSlicer 2.4.2 on the machine that removed them), so no number from them
#: is given here.
BRIDGE_ROLES = frozenset({"Bridge", "Internal Bridge", "Overhang wall"})

#: The grid the bridge reader marks printed material on, mm, about one line width
#: (0.42 mm on the pack's presets). 0.5 reads 10.01, 80.01 and 89.96 mm on drawn
#: 10, 80 and 90 mm spans. *Rejected:* 0.25 (finer than the line spacing, so a line
#: beside a printed one reads free: a bridge laid on support reads as a long free
#: span); 1.0 (the one-cell neighbourhood then holds a line on material up to 2 mm
#: aside: the 90 mm block's 87.06 mm roof read 86.06, short by a cell). The first
#: comparison also held parts from outside the pack; they were not measured again
#: on the pack's own probes, so no number from them is given here.
CELL_MM = 0.5
#: How finely a bridge line is walked, mm: half a cell. A point is held when a cell
#: of the 3 x 3 block around it was printed below, so a line that crosses a held
#: block puts a step in it. A line that only cuts a block's corner, on a chord
#: shorter than the step, can pass it at any step: two spans then read as one,
#: long, which is the safe side. Measured with this reader on 3000 random 20 mm
#: lines, each with one printed cell up to 1.2 mm to the side, against a 0.01 mm
#: walk, three seeds: of the lines the cell split, 0.25 joined about 4%, 0.5
#: 7-8% and 0.1 about 1%, and a joined run read up to 10 mm long. *Rejected:* 0.5,
#: which joins twice as often; 0.1, which joins less but walks every bridge line in
#: 2.5 times as many steps. The spans this was first compared on held parts from
#: outside the pack and were not measured again on the pack's own probes, so no
#: figure from them is given here.
STEP_MM = 0.25
#: A run ends at a turn sharper than 30 deg (cos 30 = 0.866): a run is one span,
#: and the next line of a zig-zag is another. A zig-zag turns by close to 180 deg,
#: so the result does not rest on 30; the run also ends wherever the line is held.
#: The spans this was first compared on were not measured again on the pack's own
#: probes, so no figure from them is given here.
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


def _hint(word: str, choices) -> str:
    """`` (did you mean "x"?)`` for the closest of ``choices``, or ``""``."""
    close = difflib.get_close_matches(word, sorted(choices), n=1, cutoff=0.6)
    return f' (did you mean "{close[0]}"?)' if close else ""


def _path(value: Any) -> str | None:
    """A preset path as stated, or None when the value is not one."""
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    return value if isinstance(value, str) else None


def _kind(value: Any) -> str:
    """What a value that is not a path is, in a person's words. Not its type's
    name: a mapping reaches the gate as the spine's recording view of it, and
    "is ParamTrace" names nothing the project wrote."""
    if isinstance(value, dict):
        return "a group"
    if isinstance(value, (list, tuple)):
        return "a list"
    if isinstance(value, bool):
        return "a yes/no"
    if isinstance(value, (int, float)):
        return "a number"
    return f"a {type(value).__name__}"


# Preset groups per part. ``slicer_presets`` holds the default triple, as before,
# and may also map a part's name or a glob to a group: ``{machine, process,
# filament}``, all three. A part slices on, in this order:
#   1. the group whose key is its name;
#   2. else the one group whose glob matches it (two or more is an error that names
#      them);
#   3. else the default triple;
#   4. else nothing: the part is not sliced and reads skipped, named.
# The order uses no key order. *Rejected:* the first glob in the order written. The
# verdict cache digests a parameter as JSON with its keys sorted
# (``verdicts.digest_value``), so two statements that differ only in key order key
# one entry, and the verdict for one would be served for the other. *Rejected:* a
# group that names one or two roles and takes the rest from the default: the part's
# triple would come from two places, and an edit to the default would move a part
# whose group looks complete. *Rejected:* a key that names no part, or a glob that
# matches none, read as harmless: a misspelled name sends that part back to the
# default triple, which is the silent mode the groups exist to close.

def _statement(ctx: GateContext):
    """``(default, groups, None)`` or ``(None, None, verdict)``: what
    ``slicer_presets`` says, read strictly, before any file is opened.

    ``default`` is ``{role: path}`` (empty when the project states none) and
    ``groups`` is ``{key: {role: path}}``. An unknown key is an error that names the
    closest known one: a misspelled ``filamnet`` read as nothing would leave the
    project's triple short with no word about why."""
    raw = ctx.first_pack_param(("slicer_presets",), None)
    if not isinstance(raw, dict):
        return None, None, _skip(
            "no slicer_presets: the slicer gate binds its verdict to a named preset and "
            "will not invent one. State {machine, process, filament}: paths to three "
            "complete OrcaSlicer preset files (references/slicer.md)")
    takes = ("it takes machine, process and filament, each a path to a preset file, and a "
             "part's name or a glob with a group {machine, process, filament}")
    default: dict[str, str] = {}
    groups: dict[str, dict[str, str]] = {}
    # Sorted, so the first problem named does not hang on key order either.
    for key, value in sorted(((str(k), v) for k, v in raw.items()), key=lambda kv: kv[0]):
        if key in PRESET_ROLES:
            if value is None or value == "":
                continue                    # read below: "names no <role> preset"
            if isinstance(value, dict):
                return None, None, _error(
                    f"slicer_presets.{key} is a group, but {key} is a preset role and takes "
                    f"a path; a part named {key} takes a glob that matches it, such as "
                    f"\"{key[:-1]}[{key[-1]}]\"")
            path = _path(value)
            if not path:
                return None, None, _error(f"slicer_presets.{key} is {_kind(value)}, not a "
                                          f"path to a preset file")
            default[key] = path
        elif isinstance(value, dict):
            group = {}
            for role, rel in sorted(value.items(), key=lambda kv: str(kv[0])):
                if str(role) not in PRESET_ROLES:
                    return None, None, _error(
                        f"slicer_presets[\"{key}\"] has an unknown key \"{role}\""
                        f"{_hint(str(role), PRESET_ROLES)}: a group takes machine, process "
                        f"and filament")
                path = _path(rel)
                if rel not in (None, "") and not path:
                    return None, None, _error(
                        f"slicer_presets[\"{key}\"].{role} is {_kind(rel)}, not a path "
                        f"to a preset file")
                if path:
                    group[str(role)] = path
            missing = [r for r in PRESET_ROLES if r not in group]
            if missing:
                return None, None, _error(
                    f"slicer_presets[\"{key}\"] names no {missing[0]} preset: a group names "
                    f"all three, and takes none from the default")
            groups[key] = group
        else:
            return None, None, _error(f"slicer_presets has an unknown key \"{key}\""
                                      f"{_hint(key, PRESET_ROLES)}: {takes}")
    if default or not groups:
        for role in PRESET_ROLES:
            if role not in default:
                return None, None, _skip(f"slicer_presets names no {role} preset; all three "
                                         f"are needed")
    return default, groups, None


def _load_presets(ctx: GateContext, where: str, roles: dict, seen: dict):
    """``({role: (name, bytes, data)}, None)`` or ``(None, verdict)`` for one
    triple. ``seen`` holds each file read so far, by path, so a file two groups
    share is read once."""
    out = {}
    for role in PRESET_ROLES:
        rel = roles[role]
        path = rel if os.path.isabs(rel) else os.path.join(ctx.root or os.curdir, rel)
        if path not in seen:
            if not os.path.isfile(path):
                return None, _skip(f"{where}.{role} points at {path}, which does not exist")
            with open(path, "rb") as fh:
                data = fh.read()
            try:
                parsed = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                return None, _error(f"{where}.{role} ({rel}) is not JSON: {exc}")
            if not isinstance(parsed, dict) or not parsed.get("name"):
                return None, _error(f"{where}.{role} ({rel}) is not a preset with a name")
            seen[path] = (str(parsed["name"]), data, parsed)
        out[role] = seen[path]
    return out, None


def _presets(ctx: GateContext):
    """``({group: {role: (name, bytes, data)}}, None)`` or ``(None, verdict)``.
    The default triple is the group ``None``; every other group is its key."""
    default, groups, bail = _statement(ctx)
    if bail is not None:
        return None, bail
    seen: dict = {}
    out: dict = {}
    # Sorted, so the first file reported missing does not hang on key order.
    for key, roles, where in ([(None, default, "slicer_presets")] if default else []) + \
            [(k, groups[k], f"slicer_presets[\"{k}\"]") for k in sorted(groups)]:
        loaded, bail = _load_presets(ctx, where, roles, seen)
        if bail is not None:
            return None, bail
        out[key] = loaded
    return out, None


def _is_glob(key: str) -> bool:
    return bool(GLOB_CHARS & set(key))


def _assign(names, keys, source: str) -> tuple[dict, list[str]]:
    """``({part name: group key, or None for the default}, problems)``: which of
    ``keys`` (the group keys of ``slicer_presets``) each part slices with, by the
    order above. ``problems`` names every key that reaches no part and every part
    two globs claim; with any, no part is sliced."""
    names = list(names)
    exact = {k for k in keys if not _is_glob(k)}
    globs = sorted(k for k in keys if _is_glob(k))
    chosen: dict = {}
    problems: list[str] = []
    for name in names:
        if name in exact:
            chosen[name] = name
            continue
        hits = [g for g in globs if fnmatch.fnmatchcase(name, g)]
        if len(hits) > 1:
            problems.append(f"part {name} is matched by {len(hits)} globs in slicer_presets ("
                            + ", ".join(f'"{g}"' for g in hits)
                            + "): name the part itself, or narrow the globs so one matches")
        chosen[name] = hits[0] if len(hits) == 1 else None
    for key in sorted(exact - set(names)):
        problems.append(f"slicer_presets[\"{key}\"] names no part in `{source}`"
                        f"{_hint(key, names)}")
    for glob in globs:
        if not any(fnmatch.fnmatchcase(n, glob) for n in names):
            problems.append(f"slicer_presets[\"{glob}\"] matches no part in `{source}`")
    return chosen, problems


def _label(group) -> str:
    """How a verdict names a group: ``default``, or its key in quotes."""
    return "default" if group is None else f'"{group}"'


def _names(presets: dict) -> str:
    return ", ".join(repr(presets[r][0]) for r in PRESET_ROLES)


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
    does not echo it. Measured on a complete, flattened filament preset (13 such
    keys)."""
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
    bar printed as overhang walls; 0.0 on ``Bridge`` lines over support. On a part
    printed on support, a line at the tip of a support with nothing printed within
    a cell below it reads as a free run, short or long: it was not measured again
    on the pack's probes, and a person must look at the slicer's preview there (see
    "does NOT settle" in ``slice_part``).
    *Rejected:* the longest Bridge segment (on a part whose bridges are all
    supported, it reads the whole first layer over a support as one free span:
    Orca types that layer Bridge); a free run with no one-cell neighbourhood (it
    reads a supported bridge as free, because the bridge line runs between the
    support-interface lines, parallel to them:
    ``tests/test_fdm_slice.ASupportedBridgeIsNotASpan`` draws that case); a 2.0 mm
    window (ten layers at 0.2 mm, so it holds a free roof on material printed far
    below it, which does not touch the roof). Each was first seen on parts from
    outside the pack. They were not measured again on the pack's own probes (no
    OrcaSlicer 2.4.2 on the machine that removed the figures), so no number from
    them is given here."""
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


def _slice(ctx: GateContext, part, mesh, presets: dict, version: str, limit: float,
           group=None, said: str = ""):
    """One part through Orca: a ``FOLD`` outcome, or raises ``_Failed`` / ``_Errored``.

    ``presets`` is the part's own triple and ``group`` the key that chose it
    (``None``: the default); ``said`` is what the note adds to name the group, empty
    when every part slices on one triple."""
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
                   "preset_group": group,
                   "presets": {r: {"name": n, "sha256": _sha(d)}
                               for r, (n, d, _p) in presets.items()},
                   "config_block_sha256": _sha(json.dumps(cfg, sort_keys=True).encode()),
                   "return_code": rc, "error_string": words, "warning_message": "",
                   "retired_keys_not_used": retired,
                   "window_mm": window, "worst_span_mm": span, "worst_at_mm": at,
                   "limit_mm": limit, "gcode": gcode}, fh, indent=1)
    note = (f"at {span:.1f} mm unsupported bridge at z {wz:.2f} vs {limit:.0f} mm"
            if span else "has no unsupported bridge line") + said
    locators = [Locator(view=PARTS.VIEW_ID, target=part.node, kind="point", position=at,
                        value=span, label=f"{span:.1f} mm unsupported bridge on OrcaSlicer "
                                          f"{version} vs {limit:.0f} mm{said}")
                ] if span > limit else []
    return FOLD.measured_part(part.name, score=span / limit, measured=span, limit=limit,
                              note=note, path=gcode,
                              row=f"{span:.2f}\t{limit:.2f}\t{at}\t{_label(group)}",
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
    """Slice each part on OrcaSlicer with the three presets the project names for
    it, then read the toolpaths Orca wrote.

    First, ``slicer_presets`` is read strictly: the default triple, and a group per
    part name or glob (``_assign`` says which group a part takes). A key it does not
    know, a group short of a role, a key that reaches no part and a part two globs
    claim are errors, a preset file that is missing is a skip, and with any of
    these no part is sliced; a part out of print pose still fails beside it. A part
    no group names, with no default triple, is skipped.

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
    # A statement the gate cannot read, or a preset file it cannot load, is held
    # here, not returned: it decides which triple a part slices on, never whether a
    # part is in print pose. What slipped through with it returned at once: a part
    # 15 mm above the bed beside a misspelled "filamnet", a group short of a role or
    # a missing preset file read errored or skipped, a known failure filed under
    # the statement's word; only a key that reached no part (``_assign``) was held.
    presets, bail = _presets(ctx)

    parts = PARTS.part_set(ctx)
    if parts.problem:
        return bail if bail is not None else _skip(parts.problem)
    if not (parts and PARTS.has_geometry(parts)):
        raw = PARTS.mesh_path(ctx)
        if not raw:
            return bail if bail is not None else _skip(
                "the projection has no mesh_path and no mesh_paths set: the slicer needs "
                "the exported part")
        parts = PARTS.PartSet(parts=(PARTS.Part(name=PARTS.mover(ctx), raw=raw),),
                              source="mesh_path")

    if presets is None:
        chosen, refused = {}, [bail.error or bail.skip_reason or "slicer_presets not read"]
    else:
        chosen, refused = _assign((p.name for p in parts),
                                  [k for k in presets if k is not None], parts.source)
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
        if presets is None:
            continue                        # no triple to slice on: named below
        group = chosen.get(part.name)
        if group not in presets:
            outcomes.append(FOLD.skipped_part(
                part.name, "no group in slicer_presets names it, and it states no default "
                           "triple; the gate will not invent one"))
            continue
        todo.append((part, mesh, group))
    # The groups the parts slice on, or, with none to slice, every group stated.
    used: dict = {}
    for _part, _mesh, group in todo:
        used[group] = used.get(group, 0) + 1
    if refused:
        # A key that reaches no part may be a misspelled name, and then any part may
        # be on the wrong triple: none is sliced. A part that failed above still
        # fails, with the statement named beside it. A statement the gate could not
        # read (``bail``) is here too, as its one problem.
        todo = []

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
    shown = sorted(used or presets or (), key=lambda g: (g is not None, g or ""))
    several = len(shown) > 1
    for part, mesh, group in todo:
        said = f" on presets {_label(group)}" if several else ""
        try:
            outcome, file = _slice(ctx, part, mesh, presets[group], version, limit,
                                   group=group, said=said)
        except _Failed as exc:
            failed.append((part.name, f"{exc}{said}"))
            continue
        except _Errored as exc:
            errored.append(f"{part.name}: {exc}{said}")
            continue
        outcomes.append(outcome)
        evidence.append(file)

    head = f"OrcaSlicer {version}" if version else "no slicer run"
    if not shown:
        short = bound = head                # slicer_presets not read
    elif several:
        # The groups go last in a FAIL's detail, after the parts: the detail is cut
        # at 480 characters, and the part that failed is the part worth keeping.
        short = f"{head}, presets by part"
        bound = f"{short}: " + "; ".join(
            f"{_label(g)} {_names(presets[g])}" + (f" ({used[g]} part(s))" if g in used else "")
            for g in shown)
    else:
        short = bound = f"{head}, presets {_names(presets[shown[0]])}" + (
            f" of {_label(shown[0])}" if shown[0] is not None else "")
    # A settled failure first: an errored part beside it is named, never louder.
    # What slipped through with the order the other way: part A out of print pose
    # and part B's run timed out read errored, so the claim read Skipped where A
    # had already failed (gates/fdm_print_fold.py orders it this way too). A
    # statement the gate cannot read is named the same way, and after the FAIL it
    # is returned in its own word: an error stays an error, a skip a skip.
    if failed:
        also = (f"; {len(errored)} part(s) errored, first {errored[0]}" if errored else "")
        also += (f"; no other part sliced: {refused[0]}" if refused else "")
        return Verdict(gate=GATE_ID, passed=False, measured=None,
                       detail=(f"{sum(1 for n, _ in failed if n)} of {len(parts)} part(s) in "
                               f"`{parts.source}` fail ({short}): "
                               + "; ".join(f"{n}: {w}" if n else w for n, w in failed[:2])
                               + also + (f"; {bound}" if several else ""))[:480],
                       evidence=evidence,
                       locators=[Locator(view=PARTS.VIEW_ID, target=PARTS.node(n), kind="part",
                                         label=w[:120])
                                 for n, w in failed[:FOLD.MAX_PART_LOCATORS] if n])
    if bail is not None:
        return bail
    if refused:
        return _error("; ".join(refused[:2]) + (f" (and {len(refused) - 2} more)"
                                                if len(refused) > 2 else "")
                      + ". No part was sliced")
    if errored:
        return _error(f"{errored[0]}" + (f" (and {len(errored) - 1} more)" if len(errored) > 1
                                         else ""), evidence)
    table = FOLD.write_table(ctx, "fdm-slice-parts.txt", GATE_ID, parts.source, outcomes,
                             header_lines=[bound, f"bridges judged at {limit:g} mm"],
                             columns="worst_span_mm\tlimit_mm\tat_mm\tpresets")
    return FOLD.fold(GATE_ID, outcomes, source=parts.source, units="mm",
                     quantity="unsupported bridge span", evidence=[table, *evidence[:3]],
                     extra_detail=bound)
