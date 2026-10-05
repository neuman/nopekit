# SPDX-License-Identifier: Apache-2.0
"""What the printed part is called once the site has drawn it, and where it lives.

The same interface ``cad-solid`` carries, for a pack that looks at one part at a
time instead of an assembly. ``views/part.py`` names the node it puts in the GLB;
``gates/mesh.py`` and ``gates/printability.py`` spell that name back in their
locators. Two copies of a naming convention is a convention that drifts, and the
drift is silent on both ends — the gate reports a problem it believes it is
drawing and the page draws nothing — so both sides read this file.

The scheme::

    mesh_path "build/saddle_clamp.stl"  ->  mover "saddle_clamp"  ->  node "saddle_clamp__0"

One part, so one mover. The ``__<body>`` suffix is still there, and it is not
ceremony: a part exported as a multi-body scene (a print plate with the part and
its test coupon) becomes ``saddle_clamp__0`` and ``saddle_clamp__1`` under one
mover, and a part that grows a second body later does not rename the thing every
locator points at.

The mover name comes from ``part_name`` in the projection if the model states one,
and otherwise from the mesh filename. Derived rather than required (method rule 2):
a project that has already named its build output should not have to name it a
second time for the viewer, and a pack that demanded the name would make the whole
view optional on a key nobody thinks to set.

Many parts
----------

A real mechanical project prints a set, not a part, and :func:`part_set` is how
this pack learns the whole set. It reads ``mesh_paths`` (and the spellings beside
it) as a mapping of name -> path, or a list of paths, and ``bbox_by_part_mm`` as a
mapping of name -> ``[x, y, z]`` extent, and returns one :class:`Part` per member
of their union.

**It returns an empty set when the projection describes a single part**, and every
gate then runs its original one-part path untouched. That is deliberate: the
single-part behaviour is the one that shipped and the one the negative controls
exercise, and a "multi-part set of one" would have quietly rewritten it.

The precedence is stated here and in ``PACK.md`` because the last time this pack
resolved a family of synonyms silently it cost a user an afternoon: ``bbox_mm``
won over ``bbox`` with nothing on the screen to say so. So: **a part SET wins over
a single ``mesh_path``** when a project states both, because the set is the more
complete statement of what is being printed — and every multi-part verdict names
the key the set came from, so the reader never has to guess which one won.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from nopekit.site import MOVER_SEPARATOR

#: The view id ``views/part.py`` registers, and therefore the string every
#: ``Locator.view`` in this pack must carry. Spelled ``print_part`` rather than
#: ``part`` because a view id is global to a project: a project running this pack
#: beside a CAD pack needs the two to be distinguishable, and two views claiming
#: one id means every locator aimed at that id lights up whichever registered
#: last — a confident highlight on the wrong geometry.
VIEW_ID = "print_part"

# Every key family below is read through ``ctx.param``, which resolves the
# PACK-SCOPED spelling first: ``fdm.mesh_path`` (flat, or nested under an ``fdm``
# group) beats the bare ``mesh_path``, and a scoped key beats an unscoped one
# whatever its rank in the family. That is what lets a project satisfy this pack
# and ``cad-solid`` from one projection when they want the same word for different
# objects — see PACK.md, "Keys, frames and resolution order".

#: Where the exported part mesh is, in order. Shared with ``gates/mesh.py`` so the
#: viewgen and the gates cannot end up looking at two different files — locators
#: measured on one mesh and drawn on another would be wrong in a way nothing on
#: the page could show.
MESH_PATH_KEYS = ("mesh_path", "stl_path", "part_mesh", "geometry_path")

#: Where the part's name is, in order. Optional; the mesh filename is the fallback.
PART_NAME_KEYS = ("part_name", "part", "name")

#: Where the WHOLE PRINT SET lives, in order. A mapping of ``name -> path`` is the
#: spelling to prefer — it names the parts, and a named part is what a verdict and
#: a locator can point at. A bare list of paths works too and takes each name from
#: its filename.
#:
#: ``parts`` is second rather than first on purpose: it is the most likely key for
#: a project to already be using for something else, so a project that means "the
#: print set" should say ``mesh_paths`` and a project that has a ``parts`` table of
#: its own is asked for one explicit key rather than surprised by it. A ``parts``
#: value this module cannot read as paths or boxes is ignored, not guessed at.
PART_SET_KEYS = ("mesh_paths", "parts", "part_meshes", "mesh_path_by_part")

#: Where a per-part bounding box lives, in order: a mapping of ``name -> [x, y, z]``
#: extent in the part's PRINT orientation, mm. ``fdm.bed_fit`` needs no mesh, so a
#: project that has not exported geometry yet can still have all of its parts
#: judged against the machine by stating this alone.
BBOX_SET_KEYS = ("bbox_by_part_mm", "print_bbox_by_part_mm", "bbox_mm_by_part",
                 "bboxes_mm")

#: Where one part's mesh path lives inside a ``{"name": {...}}`` entry.
_ENTRY_PATH_KEYS = ("mesh_path", "path", "stl_path", "stl", "mesh", "file")
#: Where one part's box lives inside such an entry.
_ENTRY_BBOX_KEYS = ("bbox_mm", "bbox", "print_bbox_mm", "extent_mm", "footprint_mm")
#: Where an entry in a LIST of parts carries its own name.
_ENTRY_NAME_KEYS = ("name", "part", "part_name", "id")

_KEEP = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-."
)


def sanitise(name: str) -> str:
    """A name reduced to something usable as a glTF node name.

    Runs of replaced characters collapse to one ``_``, because ``MOVER_SEPARATOR``
    is ``__``: a part called ``saddle clamp`` would otherwise sanitise to
    ``saddle__clamp`` and :func:`nopekit.site.derive_explode` would read it as a
    *body* of a mover called ``saddle``.
    """
    text = "".join(ch if ch in _KEEP else "_" for ch in str(name))
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_") or "part"


def mesh_path(ctx: Any) -> str:
    """The raw ``mesh_path`` value from the projection, or ``""``. Resolves nothing.

    Callers that need a filesystem path join it against ``ctx.root`` themselves;
    callers that only need to know *whether there is geometry* — ``fdm.bed_fit``
    deciding whether a locator would have anything to point at — get their answer
    without touching the disk.
    """
    for key in MESH_PATH_KEYS:
        value = ctx.param(key)
        if value:
            return str(value)
    return ""


def mover(ctx: Any) -> str:
    """The part's name in the viewer: stated, else derived from the mesh filename."""
    for key in PART_NAME_KEYS:
        value = ctx.param(key)
        if value:
            return sanitise(value)
    path = mesh_path(ctx)
    if path:
        return sanitise(os.path.splitext(os.path.basename(path))[0])
    return "part"


def node(mover_name: str, body: int = 0) -> str:
    """The glTF node name for one body: ``saddle_clamp__0``."""
    return f"{mover_name}{MOVER_SEPARATOR}{int(body)}"


# --------------------------------------------------------------------------- #
# the print SET
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Part:
    """One member of the print set: what it is called, where it is, how big it is.

    ``raw`` is the path exactly as the model stated it and is resolved against
    ``ctx.root`` by whoever opens the file — the same division of labour
    :func:`mesh_path` already has, so a caller that only needs to know *whether*
    there is geometry never touches the disk.

    ``bbox`` is the part's extent in its print orientation, mm, when the model
    stated one. ``None`` is ordinary: a project can describe its set by meshes
    alone, or by boxes alone, and the gates say which parts they could not judge
    rather than inventing the missing half.
    """

    name: str
    """Sanitised mover name — the string every locator for this part carries."""

    raw: str = ""
    """The mesh path as projected, or ``""`` when this part was named by a box only."""

    bbox: tuple[float, float, float] | None = None
    """``[x, y, z]`` extent in print orientation, mm, or None."""

    @property
    def node(self) -> str:
        """The glTF node name of this part's first body."""
        return node(self.name)


@dataclass(frozen=True)
class PartSet:
    """The parts to judge, and the projection key that named them.

    ``source`` is in every multi-part verdict's evidence header and in its skip
    reasons. The last time this pack resolved a synonym family without saying
    which spelling won (``bbox_mm`` over ``bbox``), a user spent an afternoon
    proving the gate was reading the key they had not meant it to. One string
    closes that permanently.
    """

    parts: tuple[Part, ...] = ()
    source: str = ""
    problem: str = ""
    """Why a stated set produced no parts, or ``""``.

    A projection that says ``mesh_paths`` and means it, in a shape this module
    cannot read, must not fall quietly back to the single-part path: that is a
    verdict about one part where the reader asked for thirteen, with nothing on
    the screen to say so. The gates turn this into a SKIP naming the key, so the
    claim resolves BLOCKED and stays visible.
    """

    def __bool__(self) -> bool:
        return bool(self.parts)

    def __len__(self) -> int:
        return len(self.parts)

    def __iter__(self):
        return iter(self.parts)


def _numbers(value: Any, n: int) -> tuple[float, ...] | None:
    """``value`` as exactly ``n`` floats, or None. Strings are never sequences here."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return None
    try:
        out = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return None
    return out if len(out) == n else None


def _entry(value: Any) -> tuple[str, tuple[float, float, float] | None] | None:
    """One set member's ``(path, bbox)``, or None when the value says neither.

    Accepts the three shapes a model actually writes: a bare path string, a
    ``[x, y, z]`` box, and a dict carrying either or both.
    """
    if isinstance(value, str):
        return (value, None)
    box = _numbers(value, 3)
    if box is not None:
        return ("", (box[0], box[1], box[2]))
    if isinstance(value, dict):
        path = ""
        for key in _ENTRY_PATH_KEYS:
            found = value.get(key)
            if isinstance(found, str) and found:
                path = found
                break
        bbox = None
        for key in _ENTRY_BBOX_KEYS:
            found = _numbers(value.get(key), 3)
            if found is not None:
                bbox = (found[0], found[1], found[2])
                break
        if path or bbox is not None:
            return (path, bbox)
    return None


def _named_entries(value: Any) -> list[tuple[str, str, tuple | None]]:
    """``[(name, path, bbox), ...]`` from a mapping or a list, in the stated order.

    Order is the model's, not sorted: a project that lists its parts in print
    order means something by it, and the per-part table in the evidence is easier
    to read against the model when the two agree. It is deterministic either way,
    because the projection is JSON and JSON preserves mapping order.
    """
    out: list[tuple[str, str, tuple | None]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            parsed = _entry(item)
            if parsed is None:
                continue
            out.append((str(key), parsed[0], parsed[1]))
        return out
    if isinstance(value, str) or not isinstance(value, Iterable):
        return out
    for item in value:
        # a bare (name, path) pair, before _entry sees it as an unreadable sequence
        if (isinstance(item, Sequence) and not isinstance(item, (str, bytes))
                and len(item) == 2 and isinstance(item[0], str)
                and isinstance(item[1], str)):
            out.append((item[0], item[1], None))
            continue
        parsed = _entry(item)
        if parsed is None:
            continue
        name = ""
        if isinstance(item, dict):
            for key in _ENTRY_NAME_KEYS:
                found = item.get(key)
                if isinstance(found, str) and found:
                    name = found
                    break
        if not name:
            # from the filename, which is where a list of paths keeps its names
            name = os.path.splitext(os.path.basename(parsed[0]))[0] if parsed[0] else ""
        if not name:
            continue                         # a box with no name cannot be pointed at
        out.append((name, parsed[0], parsed[1]))
    return out


def _first_stated(ctx: Any, keys: Sequence[str]) -> tuple[Any, str]:
    """The first of ``keys`` the projection states, and the spelling that won.

    Through ``first_pack_param_named`` where the context has it, so the scoped
    spelling (``fdm.mesh_paths``) beats the bare one and the name that comes back
    is the FULL one — which is then printed in the verdict. Naming the winner is
    not decoration here: this pack has already cost a user an afternoon by
    resolving a synonym family silently, and a multi-part verdict is a statement
    about a set somebody has to be able to audit against the model.

    ``ViewContext`` has the plain ``param`` and no scoped resolver, so the
    fallback is the bare sweep. An empty mapping or empty list counts as absent —
    a model that projected ``mesh_paths: {}`` has named no parts, and treating
    that as "a set with nothing in it" would turn every gate into a skip with no
    way to tell why.
    """
    named = getattr(ctx, "first_pack_param_named", None)
    if callable(named):
        value, key = named(keys, None)
        return (value, key) if value else (None, "")
    for key in keys:
        value = ctx.param(key)
        if value:
            return value, key
    return None, ""


def has_geometry(parts: "PartSet") -> bool:
    """True when at least one member of the set states a mesh.

    A part set can be assembled from mesh paths, from bounding boxes, or from
    both. Boxes alone are a complete input for a gate that only needs an extent
    (bed fit), and NO input at all for a gate that reads face normals.

    Keeping the two apart matters because the obvious implementation regressed a
    real project: it published thirteen per-part bounding boxes and one mesh, so
    the mesh gates saw a thirteen-member set, found no geometry in any of it, and
    reported "13 of 13 parts could not be measured" — replacing two working
    measurements with two skips. A set that cannot answer a gate's question should
    hand that gate back to the single-part path, not answer with nothing.
    """
    return any(part.raw for part in parts.parts)


def part_set(ctx: Any) -> PartSet:
    """Every part this pack has been asked to judge, or an EMPTY set for one part.

    An empty return is the signal to run the original single-part path, and every
    gate in the pack treats it that way. That is why this never fabricates a
    one-member set out of ``mesh_path``: the single-part path is the one the
    pack's negative controls exercise, and rewriting it as "a set of one" would
    have moved the code the controls prove.

    Names are sanitised for the viewer with :func:`sanitise`, and a collision
    after sanitising is disambiguated with a ``-2`` suffix rather than silently
    merged: two parts sharing one node name would put both of their locators on
    whichever body the viewer drew last, which is a confident highlight on the
    wrong geometry — the one failure mode ``Locator`` exists to avoid.
    """
    meshes, mesh_key = _first_stated(ctx, PART_SET_KEYS)
    boxes, box_key = _first_stated(ctx, BBOX_SET_KEYS)

    entries = _named_entries(meshes) if meshes is not None else []
    box_entries = _named_entries(boxes) if boxes is not None else []
    sources = [key for key, found in ((mesh_key, entries), (box_key, box_entries))
               if key and found]
    if not sources:
        stated = [key for key, raw in ((mesh_key, meshes), (box_key, boxes)) if key]
        if stated:
            return PartSet(problem=(
                f"{' and '.join(stated)} is stated but no part could be read out "
                f"of it. Expected a mapping of name -> mesh path (or name -> "
                f"[x, y, z] extent), or a list of paths; got "
                f"{type(meshes if mesh_key else boxes).__name__}. Fix the key or "
                f"remove it — falling back to the single part would answer a "
                f"question about one part that was asked about several"))
        return PartSet()

    by_name: dict[str, list] = {}
    order: list[str] = []
    for name, path, bbox in [*entries, *box_entries]:
        if name not in by_name:
            by_name[name] = [path, bbox]
            order.append(name)
            continue
        slot = by_name[name]
        slot[0] = slot[0] or path
        slot[1] = slot[1] if slot[1] is not None else bbox

    parts: list[Part] = []
    used: dict[str, int] = {}
    for name in order:
        path, bbox = by_name[name]
        clean = sanitise(name)
        used[clean] = used.get(clean, 0) + 1
        if used[clean] > 1:
            clean = f"{clean}-{used[clean]}"
        parts.append(Part(name=clean, raw=str(path or ""), bbox=bbox))
    return PartSet(parts=tuple(parts), source=" + ".join(sources))
