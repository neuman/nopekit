# SPDX-License-Identifier: Apache-2.0
"""cad-solid's part and node names, as ``ergo.layout_agrees`` reads them.

The scheme is cad-solid's interface (``packs/cad-solid/cad_solid_parts.py``,
published in its PACK.md, "The node naming scheme"):

    part ``ctl-btn y``  ->  mover ``ctl-btn_y``  ->  node ``ctl-btn_y__0``

so a project that publishes cad-solid's assembly (its mesh map, or the GLB its
``assembly`` view writes from it) is read here with no extra export.

**A copy, held to the original by a test.** ``sanitise``, ``movers``, ``node`` and
``MESH_KEYS`` below are cad-solid's, line for line;
``tests/test_hand_ergonomics.py`` (``CadSolidsAssemblyIsTheScene``) runs both on
the same names and goes red on the first that differs. What slipped through when
the gate loaded cad-solid's file instead: the spine spells a gate's code files
against the packs the project loaded (``verdicts.anchors_for``). In a project
with hand-ergonomics live and cad-solid not, cad-solid's file was spelled by its
absolute path, so the gate's code digest named one machine's checkout, and every
clone keyed a new verdict entry for code that had not moved. Rejected: loading
it anyway (that); a symlink into cad-solid (not portable to every checkout, and
the spine may spell it by its real path); a copy with no test (fdm-print's
``fdm_print_parts.py`` is one, and nothing would see it drift).

Standard library only, as cad-solid's original is.
"""
from __future__ import annotations

from typing import Iterable

from nopekit.site import MOVER_SEPARATOR

#: cad-solid's mesh map keys, in its order (``cad_solid_parts.MESH_KEYS``). The
#: gate reads each under its own scopes, then cad-solid's, then bare.
MESH_KEYS = ("meshes", "solids", "parts")

#: Characters that survive sanitisation (cad-solid's ``_KEEP``).
_KEEP = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-."
)


def sanitise(name: str) -> str:
    """One part name, reduced as cad-solid reduces it: anything outside
    :data:`_KEEP` becomes ``_``, runs of ``_`` collapse (``__`` is the body
    separator), the ends are stripped, and an empty result is ``part``."""
    out = []
    for char in str(name):
        out.append(char if char in _KEEP else "_")
    text = "".join(out)
    while "__" in text:
        text = text.replace("__", "_")
    text = text.strip("_")
    return text or "part"


def movers(parts: Iterable[str]) -> dict[str, str]:
    """``{part name: mover name}`` for a whole assembly, as cad-solid names it: in
    sorted order, a collision after sanitising taking ``-2``, ``-3`` ..."""
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
    """The glTF node name for one body of one mover: ``ctl-btn_y__0``."""
    return f"{mover}{MOVER_SEPARATOR}{int(body)}"
