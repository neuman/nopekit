# SPDX-License-Identifier: Apache-2.0
"""The bracket's known-good design: the one every control is one change away from.

Thickness 8.0 mm, every other Config field at the model's defaults as they stood
when this file was written. Tip deflection comes out at 0.469 mm against the
0.5 mm limit, and all six of the bracket's gates pass on it, whatever the live
design or the host project looks like — `tests/test_fixture_hygiene.py`
(`KnownGoodPassesEverything`) holds both. It is the known-good control of every
bracket gate (P2.3): a gate must pass here AND fail on its fixture — and fail every
conclusive mutation of this design, each value it read pushed 15% past its limit —
or it is unqualified, and has only shown that it refuses everything.

Why it exists (S-07, D-27). `bad_configs._with` used to rebuild each known-bad
input from the HOST's `ctx.params["config"]`, which in `gate selftest` is the live
design. The live bracket fails deflection on purpose, so an identity fixture —
`return ctx` — was reported "correctly failed ... ~64x worse: 0.700 mm": the
control demonstrated nothing about its one change, and its note was false. The
other direction slipped through too (S-19): a host with a 1000 mm bed defused
`oversized`, and one in aluminium at a safety factor of 9 defused `thin_bearing`,
so a control fired in one repository and not in the next — the SEALED rule
(invariant 5) broken for a project. A fixture built from a design that PASSES,
changed one way, is diagnostic whatever the live design's state.

Why EVERY field is stated. A default edit in `model/bracket.py` — the transcript's
`bed_xy` — must not move the known-good design. When it did (through the host
read), every Config edit changed all six control inputs: six control re-runs and
six new tracked control entries per edit, on a cache that is committed. Rejected:
`dataclasses.replace(bracket.Config(), thickness=8.0)`, which reads the defaults —
the very dependency this file exists to cut.

Why thickness 8.0. It is the one-parameter fix the model's own docstring names
(7.0 fails at ~0.70 mm; 8.0 passes at 0.47), so the known-good design is the
bracket after the edit a user is expected to make. It is also close to the
deflection limit on purpose: a good half is diagnostic only if a gate whose limit
had drifted would fail on it. Rejected: 10.0 (0.24 mm), which passes by 2x and
would accept a deflection gate whose limit had silently become 0.3 mm; 7.5, which
fails (0.57 mm).

Why `modelio.flat_params`. It is the shape `check` hands a gate: derived values,
then every config field at the top level, plus the nested `config`. The raw
`build()` dict the fixtures used to return had no top-level config keys, so a new
gate reading `ctx.params["thickness"]` would have crashed on its control only
(packs:H19) — "CRASHED instead of failing", on an honest gate.

Why the model is loaded by path (`modelio.load_path`), not `import bracket`. A plain
module name is shared by every copy of the bracket in one process: the second
copy's fixtures would build through the FIRST copy's model, and a test comparing
two copies would pass for that reason alone (packs:H4). A path-salted name is one
module per file, content-keyed, and — loaded while the spine records a fixture —
part of that fixture's recorded code.

Why `CLAIMS` (P2.4). `bracket.deflection` reads its goalpost from C1
(`ctx.acceptance`), and a control reads the goalpost the design was calibrated
against — this file's, never the live `claims/C1.json`. Measured before it
landed: a known-good design handed the LIVE claim read `known-good fail` the
moment C1 was tightened to 0.4 (0.469 > 0.4), where 0.700 > 0.4 is plain
Failing — moving a goalpost changed a control's severity, invariant 5's failure
for a project gate; and one handed no claim errored on every control. Moving
the live C1 to 0.75 re-runs `bracket.deflection` against it and no control. The
spine holds the two to one shape — the claim, its quantity, comparator and units
— and lets only the limit differ, after moving this C1's limit both ways and
finding the gate's value unmoved (`gates.goalpost_runs`). *Rejected:* reading
`claims/C1.json` here (the coupling above, by a file read); a spine-supplied
goalpost on a control (the same coupling one level down).

`ctx.model` is left as it was handed. On a control the spine hands `context` no model,
no params, an empty ledger and no `extra` — only where the run lives — so nothing here
can take the live design by keeping a field (admission review, round 1: a `context`
that kept `ctx.params` made an identity fixture fire on a failing design, and stay
admitted, unkeyed, once the design passed). No bracket gate reads `ctx.model`.
"""
from __future__ import annotations

import copy
import dataclasses
import os

from atompipe.modelio import flat_params, load_path
from atompipe.models import Claim, Ledger

_HERE = os.path.dirname(os.path.abspath(__file__))

#: The model this design is a configuration of. `abspath`, never `resolve()`: the
#: spine decides whether a helper is part of a fixture's code by comparing
#: absolute paths, and a symlinked temp directory resolved here would put the
#: model outside the fixture's recorded closure.
bracket = load_path(os.path.join(_HERE, os.pardir, "model", "bracket.py"))

#: The known-good design, one entry per `bracket.Config` field. A field the model
#: grows later WITH a default would silently take that default here, moving the
#: design with the model; `KnownGoodPassesEverything.test_config_states_every_field`
#: goes red instead, and names it.
CONFIG: dict = {
    "arm_length": 60.0,
    "width": 30.0,
    "thickness": 8.0,        # the one departure from the model defaults: see above
    "hole_d": 5.5,
    "n_bolts": 2,
    "edge_margin": 8.0,
    "load_n": 15.0,
    "safety_factor": 2.0,
    "material": "petg",
    "nozzle_d": 0.4,
    "bed_xy": 220.0,
    "brim_mm": 8.0,
}


#: The claims this design was calibrated against, as claim records: C1's goalpost,
#: which `bracket.deflection` reads. Its limit is the one the design passes
#: (0.469 <= 0.5); a live C1 moved anywhere leaves it, and every control, where
#: it is.
CLAIMS: list = [
    {"id": "C1",
     "statement": "Tip sags no more than 0.5 mm at rated load",
     "kind": "measurable",
     "acceptance": {"quantity": "tip deflection", "comparator": "<=", "limit": 0.5,
                    "units": "mm"},
     "tags": ["stiffness"]},
]


def params(config: dict | None = None) -> dict:
    """`config` (default: CONFIG) built and flattened exactly as `check` flattens
    a projection. A fresh dict every call, so no caller can edit the design.

    A config/build() disagreement on a shared name resolves as `check` resolves
    it — the config value wins — rather than raising: the known-good design is
    what `check` would hand a gate at this config, conflicts included.
    """
    stated = dict(CONFIG if config is None else config)
    flat, _conflicts = flat_params({
        "config": stated,
        "derived": bracket.build(bracket.Config(**stated)),
    })
    return flat


def context(ctx):
    """`ctx` with the known-good design in place of whatever the host carried.

    `root`, `out_dir`, `tier` and the rest are kept, so relative paths still
    resolve and scratch still lands where the caller said. `params`, `ledger`
    and `extra` are replaced: the design, a ledger of the claims it states
    (`CLAIMS` — no host claim or verdict reaches a gate on a control) and an
    empty `extra`. Nothing of the host's params is read — not even whether it
    has any.
    """
    ledger = Ledger(claims=[Claim.from_dict(copy.deepcopy(row)) for row in CLAIMS])
    return dataclasses.replace(ctx, params=params(), ledger=ledger, extra={})
