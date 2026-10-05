# SPDX-License-Identifier: Apache-2.0
"""Which parts this pack is looking at, and what they are called in the viewer.

Two questions live here because both of them have to be answered IDENTICALLY by
``gates/solid.py`` and by ``views/assembly.py``, and neither side can see the
other's answer:

1. **Where is the geometry?** :func:`mesh_sources` is the one lookup. A gate that
   read ``params['solids']`` while the viewgen read ``extra['meshes']`` would
   produce locators naming parts that are not in the picture — and the site would
   report a dangling anchor on a gate that was working perfectly.
2. **What is each part called once it is a node?** The naming scheme below.

``views/assembly.py`` names the nodes it puts in the GLB. Every gate in this pack
that knows *where* a problem is has to spell the same name back, or its locator
addresses nothing and the site reports a dangling anchor instead of lighting a
part up. That makes the scheme an INTERFACE, not an implementation detail — the
contract says so, ``PACK.md`` publishes it, and it lives here so that the two
sides cannot drift (method rule 2: derive, never duplicate).

The scheme, in one line:

    part ``guide roller``  ->  mover ``guide_roller``  ->  node ``guide_roller__0``

* **The mover is what a gate targets.** A gate knows which PART interferes; it
  does not know which body inside that part the exporter emitted, and it must
  not pretend to. ``site.derive_explode`` groups nodes by the prefix before
  ``__`` and ``site._view_targets`` accepts a mover name as a locator target, so
  targeting the part is both honest and drawable.
* **The node is what the viewgen emits**, one per body, suffixed with the body
  index. A part that is a single solid today and two solids after somebody splits
  it for printing keeps its mover name and its locators; only the node count
  changes. Without the suffix that split would silently rename the thing every
  locator points at.

Names are sanitised because they become glTF node names, and glTF node names get
used as HTML ids, query fragments and CSS selectors by whatever renders them.
The sanitiser is deliberately aggressive and collision-aware rather than clever:
two parts whose names differ only in punctuation (``back left`` and
``back-left``) must not end up sharing a node, because a shared node is a
confident highlight on the wrong geometry — the exact failure the contract calls
worse than no highlight at all.

Both sides call :func:`movers` with the SAME sorted part list (the keys of the
projection's mesh map), so the collision suffixes come out identical on both
sides without either side reading the other's output.
"""
from __future__ import annotations

import os
from typing import Any, Iterable

from nopekit.site import MOVER_SEPARATOR

#: Keys, in order, that may carry the solid geometry. Three spellings because a
#: model that calls them ``solids`` is not wrong, and one lookup because a gate
#: and a viewgen disagreeing about which key wins is a locator pointing at a part
#: the picture does not contain.
# Read through ``ctx.param``, so the PACK-SCOPED spelling wins first:
# ``cad.meshes`` beats the bare ``meshes``, and a scoped key beats an unscoped one
# whatever its rank in the family. ``parts`` is the collision to watch — fdm-print
# reads it as the PRINT SET, this pack as the placed solids of the assembly — which
# is why `nopekit doctor` diffs the installed packs' key vocabularies and warns.
MESH_KEYS = ("meshes", "solids", "parts")

MISSING_GEOMETRY = (
    "no solid geometry in the projection: looked for ctx.extra['meshes'] and params "
    "'meshes' / 'solids' / 'parts', each a {name: path-or-mesh} map (or a list of "
    "paths). Export the placed parts from the model and put the map in the "
    "projection — this gate will not guess a filename"
)


def mesh_sources(ctx: Any) -> tuple[dict[str, Any] | None, str]:
    """``({name: source}, where)`` or ``(None, reason)``.

    ``ctx.extra`` is consulted first so a negative-control fixture can hand a gate
    known-bad geometry without touching the projection. Then the projection, under
    any of three spellings.

    Takes any context with ``extra`` and ``param`` — a ``GateContext`` from a
    sweep or a ``ViewContext`` from ``nopekit site build`` — because the whole
    point of the function is that both get the same answer.
    """
    extra = getattr(ctx, "extra", None)
    extra = extra if isinstance(extra, dict) else {}
    for key in MESH_KEYS:
        value = extra.get(key)
        if isinstance(value, dict) and value:
            return dict(value), f"ctx.extra[{key!r}]"
    for key in MESH_KEYS:
        value = ctx.param(key)
        if isinstance(value, dict) and value:
            return dict(value), f"params[{key!r}]"
        if isinstance(value, (list, tuple)) and value:
            named = {}
            for i, item in enumerate(value):
                stem = os.path.splitext(os.path.basename(str(item)))[0] or f"part{i}"
                named[stem] = item
            return named, f"params[{key!r}]"
    return None, MISSING_GEOMETRY


#: The view id this pack's assembly viewgen registers, and therefore the string
#: every ``Locator.view`` in this pack must carry. Imported rather than typed
#: twice: a typo here is a locator that silently addresses nothing, and nothing
#: about a mistyped view id looks different from a gate that found no problem.
VIEW_ID = "assembly"

#: Characters that survive sanitisation. ``.`` is in because part names carry
#: revisions (``bracket.v3``); ``__`` is NOT reachable because the sanitiser
#: collapses runs — see :func:`sanitise`, where the reason is the mover split.
_KEEP = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-."
)


def sanitise(name: str) -> str:
    """One part name, reduced to something usable as a glTF node name.

    Anything outside :data:`_KEEP` becomes ``_``, and runs collapse to a single
    ``_``. The collapse is the load-bearing part: ``MOVER_SEPARATOR`` is ``__``,
    so a part called ``lid  left`` would otherwise sanitise to ``lid__left`` and
    :func:`nopekit.site.derive_explode` would file it as a *body of the mover
    ``lid``* — silently merging two parts into one exploded group, with both
    parts' locators landing on whichever one the viewer isolated.

    An empty result (a part named ``"???"``) becomes ``part``; :func:`movers`
    then de-duplicates it. Returning an empty string would produce the node name
    ``__0``, whose mover is the empty string.
    """
    out = []
    for char in str(name):
        out.append(char if char in _KEEP else "_")
    text = "".join(out)
    while "__" in text:
        text = text.replace("__", "_")
    text = text.strip("_")
    return text or "part"


def movers(parts: Iterable[str]) -> dict[str, str]:
    """``{part name: mover name}`` for a whole assembly, collision-free.

    Call it with the same part list on both sides of the interface — the keys of
    the projection's mesh map, sorted — and both sides get the same answer
    without consulting each other. The sort happens here rather than at the call
    sites so that "the same list" cannot mean two different orders: two gates
    iterating one dict in insertion order and a viewgen iterating it sorted would
    agree on every name until the first collision and then disagree about which
    part got the suffix.

    A collision takes a ``-2``, ``-3`` ... suffix in sorted order. It is rare and
    it is a smell (two parts whose names differ only in punctuation), so the
    suffix is visible in the viewer rather than hidden: a reader who sees
    ``back_left-2`` learns that something upstream is naming parts carelessly.
    """
    out: dict[str, str] = {}
    taken: set[str] = set()
    for part in sorted(str(p) for p in parts):
        base = sanitise(part)
        name = base
        bump = 2
        while name in taken:
            name = f"{base}-{bump}"
            bump += 1
        taken.add(name)
        out[part] = name
    return out


def node(mover: str, body: int = 0) -> str:
    """The glTF node name for one body of one mover: ``guide_roller__0``."""
    return f"{mover}{MOVER_SEPARATOR}{int(body)}"
