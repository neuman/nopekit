# SPDX-License-Identifier: Apache-2.0
"""Known-bad fixtures for the bracket's gates.

Each function returns a GateContext whose params are the KNOWN-GOOD design
(`known_good.py`: thickness 8.0, all six gates pass) rebuilt with ONE physically
meaningful change, in the direction the gate under test cares about.

That constraint is the whole point. A fixture that is bad in some *other* way — a
corrupt file, a missing field, an empty mesh — proves the gate handles garbage, not
that it measures what it claims. `nopekit gate selftest` requires each gate to FAIL
on its own fixture; a gate that passes here is reported as broken.

And the base must be a design that passes. These fixtures used to rebuild from the
host's `ctx.params["config"]` — the live bracket, which fails deflection on purpose —
so an identity fixture was reported "correctly failed", and a host with a big
enough bed or a strong enough material defused `oversized` and `thin_bearing`
(S-07, S-19). Nothing here reads the host context now: a control is a pure function
of `known_good.CONFIG` and its one change.
"""
from __future__ import annotations

import dataclasses
import os

from nopekit.modelio import load_path

#: By path, like the model inside it: a plain `import known_good` would hand a
#: second bracket copy in the same process the first copy's module (packs:H4).
known_good = load_path(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "known_good.py"))


def _with(ctx, **overrides):
    """The known-good design with `overrides` applied, as the context a gate reads.

    Starts from `known_good.CONFIG`, never from `ctx`: `ctx` contributes only where
    the run lives (root, out_dir, tier), through `known_good.context`, which also
    drops the host's ledger and extra. An override naming no Config field raises
    `TypeError` from `bracket.Config` — a typo in a fixture is a broken control,
    and must read as one rather than as a quietly unchanged design.
    """
    config = dict(known_good.CONFIG)
    config.update(overrides)
    return dataclasses.replace(known_good.context(ctx), params=known_good.params(config))


def quarter_thickness(ctx):
    """bracket.deflection — a quarter of the known-good thickness, 8.0 -> 2.0 mm.

    Deflection goes as 1/t^3, so this is 64x worse: 0.469 mm becomes 30.0 mm. It was
    1.75 mm while the base was the live 7.0; on the known-good 8.0 that would be
    ~95x, and the gate's note ("1/4 thickness ... ~64x worse") would be false.

    Far past the limit on purpose: the design with its protective element removed.
    The at-the-limit evidence S-17 asked a control for is the mutation pass's
    (P2.3): it pushes the known-good deflection until it lands 15% past the limit,
    0.575 mm against 0.5, and the gate must fail that run. *Rejected:* recalibrating
    this fixture to ~1.15x (the old checkpoint 2.3, the margin beam-analytic's
    fixtures use) — the known-bad control and the at-the-limit test would be one
    input under two names, and a tracked fixture and its entry would move for
    evidence the walk now gives.
    """
    return _with(ctx, thickness=known_good.CONFIG["thickness"] / 4.0)


def overloaded(ctx):
    """bracket.bending_stress — 20x load, geometry untouched."""
    return _with(ctx, load_n=300.0)


def thin_bearing(ctx):
    """bracket.bearing — one bolt in a thin plate with a large hole.

    Thickness moves here too, which also trips the deflection gate; that is fine and
    expected. What matters is that the bearing gate trips, and that the reason it
    trips is a collapsed bearing area rather than an unrelated defect.
    """
    return _with(ctx, n_bolts=1, hole_d=10.0, thickness=1.5, load_n=400.0)


def stubby(ctx):
    """bracket.model_validity — a 20 mm arm on the known-good 8 mm section: L/h of
    2.5, well inside where shear matters. The arm is the only change; the section
    is the known-good one (it used to be pinned here, against a live 7.0)."""
    return _with(ctx, arm_length=20.0)


def oversized(ctx):
    """bracket.bed_fit — a 400 mm arm. Nothing wrong but the footprint."""
    return _with(ctx, arm_length=400.0)


def fat_nozzle(ctx):
    """bracket.min_wall — a 1.2 mm nozzle needs 3.6 mm of wall; the part has 8 mm...

    ...so push the section under it too. The change is still one idea: this part is
    being made by a process that cannot resolve it.
    """
    return _with(ctx, nozzle_d=1.2, thickness=3.0)
