# SPDX-License-Identifier: Apache-2.0
"""Known-bad hydrostatic fixtures. One physical change each, nothing else.

Every function returns a `GateContext` whose params are the pack's own baseline
raft with exactly ONE quantity moved, in the direction the gate under test cares
about. A fixture that was bad in some other way — an empty dict, a negative
density, a missing key — would prove the gate survives garbage, not that it
measures anything.

**The baseline is `selftest/baseline.json`, loaded from disk.** There is one
design in this pack and this is it: the same raft the manifest, the references
and the teaching notes describe. These modules used to carry hand-maintained
BASE dicts of their own (a 180 kg float here, a 25 mm line in `bad_flow.py`),
which meant the control verdicts CI printed — `measured=1.6`, `measured=-0.25` —
were computed on a hull no reader had ever seen. Two hand-maintained designs is
one too many (method rule 2), and the one that survives is the one the reader
was taught on.

**Why the fixture is sealed rather than layered over the project.** These params
are `{**baseline(), **override}` and nothing else: the host project's projection
is deliberately NOT inherited. A negative control has to produce the same verdict
in every project that installs this pack; a control whose severity depends on the
host project's numbers is a control that passes in some repositories and fails in
others, and then nobody knows whether the gate works.

That is not hypothetical. This module used to layer the baseline over the project
(`{**ctx.params, **BASE, **override}`), which looks safe because the baseline
wins on every key it states — but the gates read SYNONYM FAMILIES and DERIVED
quantities, so a key the baseline never mentions still lands. A project that
states `waterplane_inertia_m4` (an honest thing to state: it is better than the
pack's rectangular fallback) replaced the inertia `top_heavy` had computed its
raised KG against, GM came out large and positive, and `fluid.metacentric`
PASSED its own known-bad fixture. Same hole for `kb_m` and `bg_m` against the
centre-of-gravity fixtures, `draft_m` against `low_deck_edge`, and
`max_volume_fraction` against `overloaded`. Inheriting nothing closes all of them
at once, and costs nothing: every quantity these four gates read is in the
baseline.

**Why every severity is computed rather than typed.** Each fixture solves for the
value that misses, by `SEVERITY`, the acceptance THE BASELINE STATES — not a
literal that happens to exceed the limit the pack ships today. A hardcoded 1.60
volume fraction is a control that silently stops being a control the day somebody
relaxes `max_volume_fraction` to 1.7; a value derived from the limit cannot. It
also keeps the fixtures physically honest: solving for the threshold is what puts
`top_heavy`'s centre of gravity 0.23 m above the deck edge, where a mast's mass
actually is, instead of 3.9 hull depths up in mid-air, which is where the old
hardcoded figure had to put it to move a number this stiff.
"""
from __future__ import annotations

import dataclasses
import math

from gates._fluids_analytic import G, baseline

#: A plausible, passing design: this pack's own baseline projection.
BASE: dict = baseline()

#: How far past its acceptance each fixture lands, as a ratio.
#:
#: 1.15 and not 10: a control is a test of the INSTRUMENT, and the sharp question
#: is whether the gate resolves a design that misses by a realistic margin, not
#: whether it notices a catastrophe. A gate that only fires at 10x has an
#: undiscovered dead band just above its limit and the selftest would read green
#: the whole time. 1.01 was rejected as the other failure: it puts the control
#: inside float noise and inside the rounding the verdicts print at.
SEVERITY = 1.15


def _ctx(ctx, **override):
    """The baseline with one override, as a context the gate can read.

    The real context is reused for its root, ledger and out_dir; its params are
    replaced outright rather than merged, so no key the host project happens to
    declare can reach the gate and shift the quantity under test.
    """
    return dataclasses.replace(ctx, params={**BASE, **override})


def _stated(key: str) -> float:
    """An acceptance the baseline must state, because a fixture is calibrated to it.

    Raising is the point. If the key goes missing the control is no longer
    derived from anything, and `nopekit gate selftest` reports the fixture as
    unusable — which is the honest outcome, and the only one that cannot be
    mistaken for a passing gate.
    """
    value = BASE.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(
            f"selftest/baseline.json must state {key!r}: the negative control below is "
            f"solved for the value that misses that acceptance by {SEVERITY}x, and with "
            f"no stated acceptance there is nothing to miss")
    return float(value)


def _draft() -> float:
    """Baseline draft: displaced volume over waterplane area (wall-sided).

    The same derivation `gates/_fluids_analytic.hydro` runs, and exact for this
    box of a hull.
    """
    return (BASE["mass_total_kg"] / BASE["fluid_density_kg_m3"]) / BASE["waterplane_area_m2"]


def _bm() -> float:
    """Baseline BM = I_waterplane / displaced volume. Unchanged by any fixture here."""
    return BASE["waterplane_inertia_m4"] / (BASE["mass_total_kg"] / BASE["fluid_density_kg_m3"])


def _kg_for_gm(target_gm: float) -> float:
    """The centre of gravity that produces `target_gm`, given the baseline hull.

    GM = BM - (KG - KB), so KG = KB + BM - GM. Every fixture that needs to move
    stability moves KG, because that is the only term in that identity a loading
    change actually touches.
    """
    return BASE["kb_m"] + _bm() - target_gm


def overloaded(ctx):
    """fluid.buoyancy — loaded until flotation consumes SEVERITY x the allowed volume.

    Archimedes is linear in mass, so nothing about the geometry moves: same hull,
    same waterplane, same watertight volume, more cargo than there is boat. The
    mass is solved from the baseline's own `max_volume_fraction`, so at the
    shipped 0.90 the raft is asked to displace 1.035 of itself and the verdict
    reads SINKS rather than merely over-limit.
    """
    fraction = SEVERITY * _stated("max_volume_fraction")
    mass = fraction * BASE["fluid_density_kg_m3"] * BASE["hull_volume_m3"]
    return _ctx(ctx, mass_total_kg=round(mass, 1))


def low_deck_edge(ctx):
    """fluid.freeboard — the deck edge dropped until freeboard is 1/SEVERITY of its minimum.

    Mass, waterplane and therefore draft are untouched; the only thing that
    changed is how far the water has to climb to come aboard. That isolates the
    quantity this gate measures, which is a distance and not a volume — a hull
    can have plenty of reserve buoyancy and still ship water over a low gunwale.

    `hull_volume_m3` is re-derived from the new depth rather than left at the
    baseline's. Lowering the deck without it asserted a watertight volume 6.2x
    the box the fixture describes, which is not "one physically meaningful
    change" — it is one change plus an incoherent hull (method rule 2).
    """
    depth = _draft() + _stated("min_freeboard_m") / SEVERITY
    return _ctx(ctx,
                hull_depth_m=round(depth, 5),
                hull_volume_m3=round(BASE["waterplane_area_m2"] * depth, 5))


def top_heavy(ctx):
    """fluid.metacentric — mast-head mass raised until GM is 1/SEVERITY of its minimum.

    The hull, the waterplane and the displacement are identical, so BM is
    identical; only KG grows, and GM = BM - (KG - KB) falls through the margin.

    Read the number this produces before trusting any intuition about it: on the
    baseline raft it is KG 0.779 m, which is 0.23 m ABOVE the deck edge. That is
    a mast, an antenna and a solar array — not, as this fixture's note used to
    claim, "a battery moved from the bilge to the deck". A 3.20 x 1.60 m
    rectangular waterplane gives BM 0.97 m, so putting the ENTIRE displacement at
    the deck edge still leaves GM positive; nothing that fits inside this hull
    can capsize it, and a fixture that said otherwise was teaching a calibration
    that does not exist. What does happen to a raft like this, and what this
    control reproduces, is the stiffness margin being spent by topside equipment.
    """
    return _ctx(ctx, kg_m=round(_kg_for_gm(_stated("min_gm_m") / SEVERITY), 4))


def masthead_load(ctx):
    """fluid.righting_arm — the arm falls short of an UNCHANGED heeling moment.

    The demand side is deliberately untouched: `heeling_moment_nm` stays at the
    baseline's 600 N.m and the heel stays at 8 deg, so the limit the gate
    computes is bit-identical to the baseline's. What moves is KG, and therefore
    GM, and therefore the measured GZ.

    That is the whole point of replacing the fixture that used to live here. It
    multiplied `heeling_moment_nm` by 6 — which moves the LIMIT and not the
    MEASUREMENT, leaving measured GZ bit-identical (0.1656 m) in the baseline and
    in the control. A control like that proves only that `gz >= limit` compares
    two floats: GZ could have been coded as a constant and it would still have
    fired. This one is solved backwards through GZ = GM.sin(theta), so the
    trigonometry, the angle unit and the magnitude are all on the hook — degrees
    left in place of radians gives GZ 0.338 m here and the control stops firing,
    loudly.

    What it honestly cannot falsify is sin versus tan: at 8 deg they differ by
    1%, well inside the 15% margin, and at small angles that is a distinction
    without a difference. The SMALL_ANGLE_MAX_DEG ceiling is what protects the
    place where it would start to matter.
    """
    weight = BASE["mass_total_kg"] * G
    demanded_gz = BASE["heeling_moment_nm"] / weight      # mirrors the gate's limit
    target_gz = demanded_gz / SEVERITY
    target_gm = target_gz / math.sin(math.radians(BASE["heel_angle_deg"]))
    return _ctx(ctx, kg_m=round(_kg_for_gm(target_gm), 4))
