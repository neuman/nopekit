# SPDX-License-Identifier: Apache-2.0
"""The known-good geometry for the mesh gates: the baseline assembly, through the
channel their known-bad controls use.

Each known-bad fixture in ``bad_meshes.py`` hands its gate its geometry in
``ctx.extra["meshes"]``, which the gates read before the projection. The pack's
baseline context (``packs.baseline_context``) names the same parts in
``params["meshes"]`` and puts nothing in ``extra``. A known-good control built
from that reaches the gate through a different channel than the known-bad one,
and P2.3 refuses the pair (``channels differ``, P2.3-D5, D-26): a gate that
loaded ``extra`` and ``params`` differently could pass on one and fail on the
other without showing it measures anything. So the known-good control is
declared here (``good=`` on the six mesh gates) and hands the baseline
assembly's four solids in ``extra["meshes"]``, exactly as the fixtures hand
their one-defect parts.

The parts go in as ABSOLUTE paths into this pack's ``selftest/meshes/``, never
as the baseline's pack-relative strings and never pre-loaded. Relative, they
resolve against ``ctx.root`` — the host project's in a project's check run,
where no ``selftest/meshes/`` exists, so every part would be unreadable and the
gate would skip its known-good control. Pre-loaded, they would skip
``_load_one``'s weld of a soup format on load (an STL has no vertex identity),
and ``cad.degenerate_faces`` would count every corner of every part as a
duplicate. A path is loaded by the gate exactly as the projection's path is.

**Sealed** as the known-bad fixtures are: params from this pack's own
``selftest/baseline.json``; ``extra`` the host's plus ``meshes``, as
``bad_meshes._sealed`` builds it, so both halves hand the same keys.
"""
from __future__ import annotations

import dataclasses
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_PACK_DIR = os.path.dirname(_HERE)

#: The pack's own passing projection: the known-good design.
BASELINE = os.path.join(_HERE, "baseline.json")


def baseline_meshes(ctx):
    """The baseline assembly, its parts by absolute path in ``ctx.extra["meshes"]``."""
    with open(BASELINE, encoding="utf-8") as fh:
        params = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    named = params.get("meshes")
    if not isinstance(named, dict) or not named:
        raise AssertionError(f"{BASELINE} names no meshes — the known-good control in this "
                             f"pack is sealed to its assembly and cannot be built without it")
    meshes = {str(name): os.path.join(_PACK_DIR, *str(rel).split("/"))
              for name, rel in named.items()}
    return dataclasses.replace(ctx, params=params,
                               extra=dict(ctx.extra or {}, meshes=meshes))
