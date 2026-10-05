# SPDX-License-Identifier: Apache-2.0
"""The known-good BOM for the sourcing gates: the baseline, through the channel
their known-bad controls use.

Each known-bad fixture in ``bad_boms.py`` hands its gate the mutated BOM in
``ctx.extra["bom"]``, and every gate reads ``extra`` before the projection. The
pack's baseline context (``packs.baseline_context``) carries the same BOM in
``params["bom"]`` and nothing in ``extra``. A known-good control built from that
reaches the gate through a different channel than the known-bad one, and P2.3
refuses the pair (``channels differ``, P2.3-D5, D-26): a gate that answered
``extra`` and ``params`` differently could pass the good half on one channel and
fail the bad half on the other, without either showing it measures anything.
So the known-good control is declared here (``good=`` on each of the seven
gates) and hands the baseline BOM, unchanged, exactly as the fixtures hand their
one-defect copies.

**Sealed** as the known-bad fixtures are (``docs/PACK_FORMAT.md``, SEALED
FIXTURES): built from this pack's own ``selftest/baseline.json`` and nothing of
the host — its params replaced, its ``extra`` replaced by ``{"bom": …}`` alone,
the key set the known-bad fixtures hand. *Rejected:* importing ``_base`` from
``bad_boms.py`` — the known-good design would then move with an edit made to a
known-bad helper, and each module would be read as the other's code.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import os

_PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The pack's own passing projection: the known-good design.
BASELINE = os.path.join(_PACK_DIR, "selftest", "baseline.json")


def baseline_bom(ctx):
    """The baseline, unchanged, in ``ctx.extra["bom"]`` — every gate passes it."""
    with open(BASELINE, encoding="utf-8") as fh:
        params = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    doc = params.get("bom")
    if not isinstance(doc, dict) or not isinstance(doc.get("lines"), list):
        raise AssertionError(
            f"{BASELINE} carries no 'bom' document with a 'lines' array — the known-good "
            f"control in this pack is sealed to it and cannot be built without it")
    doc = copy.deepcopy(doc)
    # The marker every known-bad fixture sets inside the document it hands
    # (`bad_boms._note`), set here too: what the gate is handed differs between
    # the halves in the one change each fixture names, and in nothing else.
    doc["_control"] = "the baseline, unchanged"
    return dataclasses.replace(ctx, params=copy.deepcopy(params), extra={"bom": doc})
