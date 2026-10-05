# A worked hull, and a worked line

A complete example taken through all seven gates, with the actual verdict lines
the pack produced. **Numbers below are real output, not illustrations** — every
block was pasted from a run, including the Colebrook trace at the end, which is
the one a reader has no other way to check.

## The subject

A generic rectangular utility float — a work platform, a sensor raft, a pontoon.
Nothing about it is proprietary and nothing about it is clever; it is a box, and
a box is the right thing to work an example on because every approximation the
pack makes is exact for it.

| Quantity | Key | Value |
|---|---|---|
| Fluid | `fluid_density_kg_m3` | 998 (fresh water, 20 °C) |
| Viscosity | `kinematic_viscosity_m2_s` | 1.004e-6 |
| Waterline length | `waterline_length_m` | 3.00 m |
| Waterline beam | `waterline_beam_m` | 1.25 m |
| Waterplane area | `waterplane_area_m2` | 3.75 m² |
| Hull depth to deck | `hull_depth_m` | 0.50 m |
| Watertight volume | `hull_volume_m3` | 1.875 m³ |
| All-up mass, design load | `mass_total_kg` | 260 kg |
| Centre of gravity above the bottom | `kg_m` | 0.35 m |
| Design heel | `heel_angle_deg` | 8° |
| Design heeling moment | `heeling_moment_nm` | 450 N·m |

Note what is *not* here: no draft, no waterplane inertia, no KB. The pack derives
those and says so. A project that has a mesh can supply `displaced_volume_m3` and
`waterplane_inertia_m4` directly and the derivations step aside. Note also what
this hull does *not* state: no acceptance margins, so every limit below is the
pack's own default and the verdict says which. (`selftest/baseline.json` is the
other half of that lesson — it states its margins, and its verdicts say
`[stated min_gm_m]` instead.)

## The four hydrostatic gates at design load

```
[ok  ] fluid.buoyancy : floats: 260.0 kg needs 0.2605 m3 of the 1.8750 m3 watertight
       volume at rho 998 kg/m3 -> volume fraction 0.139 vs 0.90 limit (1.6145 m3 reserve
       buoyancy); this is a VOLUME fraction, not a draft fraction - see fluid.freeboard
       for draft [T=V/Awp, wall-sided]
[ok  ] fluid.freeboard : freeboard 0.431 m = depth 0.500 - draft 0.069 at 260.0 kg,
       vs 0.075 m minimum [15% of 0.500 m depth (pack default)] [T=V/Awp, wall-sided]
[ok  ] fluid.metacentric : GM 1.559 m = BM 1.874 - BG 0.315, vs 0.062 m [5% of 1.250 m beam
       (pack default)]; positive GM is necessary NOT sufficient - it is the slope at 0 deg only
       [T=V/Awp, wall-sided, I=Awp*B^2/12, rectangular waterplane, OPTIMISTIC for a finer
       shape, KB=T/2, box section]
[ok  ] fluid.righting_arm : GZ 0.2170 m at 8.0 deg (GM 1.559, BM 1.874), righting moment
       553 N.m vs demand 450 N.m; limit 0.1765 m [arm demanded by a 450 N.m heeling moment];
       small-angle void past 10 deg [...]
```

Reading it by hand, which is worth doing once:

- V required = 260/998 = **0.2605 m³**, which is 13.9% of the 1.875 m³ available.
- Draft T = 0.2605/3.75 = **0.0695 m**, so freeboard = 0.500 − 0.069 = **0.431 m**.
- I_waterplane = A·B²/12 = 3.75 × 1.25²/12 = **0.488 m⁴** (exact for a rectangle).
- BM = 0.488/0.2605 = **1.874 m**; KB = T/2 = **0.0347 m**;
  BG = 0.35 − 0.035 = **0.315 m**; GM = 1.874 − 0.315 = **1.559 m**.
- GZ = 1.559 · sin 8° = **0.217 m**; righting moment = 260 × 9.80665 × 0.217 =
  **553 N·m** against the 450 N·m demanded. Margin 1.23.

Every tag in square brackets is the pack telling you which approximation ran. For
this box they are all exact. For a hull with any shape to it they are not, and
the `OPTIMISTIC` one is the one to take seriously.

**And all four are transverse, at one uniform draft.** Nothing above says where
the mass sits fore and aft, so nothing above knows whether this float trims by the
head. The 0.431 m of freeboard is an average; the lowest deck edge of a trimmed
hull is lower. See `what_this_pack_cannot_tell_you.md`.

## The same hull at three loading conditions

This is lens 1 from `lenses.md`, run for real. Same hull, same water, three
states — and the state on the drawing is not the interesting one.

**Empty, 95 kg, payload gone from the bilge so KG rises slightly to 0.30 m:**

```
[ok  ] fluid.buoyancy : volume fraction 0.051 vs 0.90 limit (1.7798 m3 reserve buoyancy)
[ok  ] fluid.freeboard : freeboard 0.475 m = depth 0.500 - draft 0.025
[ok  ] fluid.metacentric : GM 4.842 m = BM 5.130 - BG 0.287
[ok  ] fluid.righting_arm : GZ 0.6739 m at 8.0 deg, righting moment 628 N.m vs demand 450
```

Light and very stiff — BM went up because the displaced volume went down while
the waterplane did not. A high GM is not comfort; this float will snap back hard
and ride badly.

**A 40 kg mast and canopy added at 1.9 m above the bottom — 300 kg, KG 0.556 m:**

```
[ok  ] fluid.buoyancy : volume fraction 0.160 vs 0.90 limit (1.5744 m3 reserve buoyancy)
[ok  ] fluid.freeboard : freeboard 0.420 m = depth 0.500 - draft 0.080
[ok  ] fluid.metacentric : GM 1.108 m = BM 1.624 - BG 0.516
[ok  ] fluid.righting_arm : GZ 0.1543 m at 8.0 deg, righting moment 454 N.m vs demand 450
```

Every gate still passes, and the stability margin has gone from 553.1 N·m against
450 to **454.0 against 450**. The margin over the demand was 103.1 N·m and is now
3.9 N·m: a 15% mass increase, all of it high, ate **96%** of it — and the buoyancy
and freeboard gates barely noticed, because buoyancy is not the thing that was
nearly lost. This is the single most useful thing the pack does: it shows you
which claim a change actually spends.

Run the gates for a plausible *future* addition, not just the current design.
Additions go up, because that is where the space is.

## The flow gates on a line and a strut

A 32 mm bore plastic line, 18 m long, carrying 1.0 L/s with 3.2 total K in
fittings, lifting 1.2 m against 3 m of pump head; and a 60 mm circular strut in a
0.5 m/s stream with a 12 N thrust budget.

```
[ok  ] fluid.drag : drag 6.74 N at 0.50 m/s vs 12.00 N budget (Cd 1.20, A 0.0450 m2,
       rho 998); Re 3.0e4 turbulent on L 0.060 m, nu from nu;
       table cylinder_cross (Re 1.0e4-2.0e5, ref D.L; long circular cylinder, flow across
       the axis)
[ok  ] fluid.pipe_pressure_drop : dp 12.09 kPa = major 9.62 + minor 2.47, vs 17.62 kPa
       budget [from 3.00 m of head less 1.20 m static lift = 1.80 m for friction];
       f 0.0222 Colebrook (11 it), Re 4.0e4 turbulent, e/D 4.7e-05, L/D 562, sumK 3.20,
       v 1.24 m/s (v=Q/A from 1.000 L/s).
[ok  ] fluid.flow_regime : regimes valid: internal Re 4.0e4 turbulent; e/D 4.7e-05,
       f 0.0222 inside 0.008-0.08; external Re 3.0e4 turbulent inside cylinder_cross band
       1.0e4-2.0e5 | rho 998 kg/m3, nu 1.00e-06 m2/s
```

Three things in those three lines are worth stopping on.

**The lift is a demand, not a supply.** 3 m of pump head less the 1.2 m the
liquid is raised leaves 1.8 m — 17.6 kPa — for friction. An earlier revision had
no concept of elevation at all and simply budgeted ρ·g·3.0 = 29.4 kPa, 1.7x what
this system can actually spend on the pipe; and because `static_head_m` sat in
the *available* head family, a project that stated only a 5 m lift and no pump at
all was handed 49 kPa of allowance invented out of a demand. The sign convention
is in PACK.md under Units and frames, and it is positive-upward.

**The minor losses are 20% of the total on an 18 m run** — and this is a *long*
run. On a 2 m run with the same fittings they would dominate.

**`fluid.flow_regime` classified both flows, including the external one.** On a
project with no piping at all it still returns a verdict on the strut alone; it
does not skip for want of a pipe.

The evidence file `fluid.pipe_pressure_drop.txt` carries the full Colebrook
trace. This is the whole of it for the line above, seed to convergence:

```
Reynolds Re       39630.2   (turbulent)
friction factor f 0.02216484   (Colebrook (11 it))

iteration trace
---------------
seed  x=1/sqrt(f)=6.73266055  f=0.02206107  (Swamee-Jain)
iter 1   x=6.71490195  f=0.02217791  |dx|=1.776e-02
iter 2   x=6.71712976  f=0.02216320  |dx|=2.228e-03
iter 3   x=6.71684997  f=0.02216505  |dx|=2.798e-04
iter 4   x=6.71688510  f=0.02216482  |dx|=3.513e-05
iter 5   x=6.71688069  f=0.02216485  |dx|=4.412e-06
iter 6   x=6.71688124  f=0.02216484  |dx|=5.540e-07
iter 7   x=6.71688117  f=0.02216484  |dx|=6.957e-08
iter 8   x=6.71688118  f=0.02216484  |dx|=8.737e-09
iter 9   x=6.71688118  f=0.02216484  |dx|=1.097e-09
iter 10  x=6.71688118  f=0.02216484  |dx|=1.378e-10
iter 11  x=6.71688118  f=0.02216484  |dx|=1.730e-11

losses
------
dynamic pressure  771.473 Pa
major  f.L/D.q    9618.516 Pa
minor  sum(K).q   2468.714 Pa   (sum K = 3.200)
TOTAL             12087.230 Pa   (12.0872 kPa, 1.2350 m of head)
static lift       1.2000 m   (elevation DEMAND, subtracted from supplied head)
budget            17616.666 Pa   (1.8000 m of head for friction)
```

An earlier revision of this file printed a *different* trace here — a seed of
6.88408298 converging to f = 0.02118363 in ten iterations — under the same "real
output" heading, and it contradicted the `f 0.0222 Colebrook (11 it)` in the
verdict line three paragraphs above it. It had been typed, not run. In a pack
whose entire pitch is auditable evidence that is worse than an obvious error,
because a reader has no way to tell which excerpt was run and which was not. If
you edit this file, regenerate the block.

## What a skip looks like, and why you want it

Remove one input, or state one from the wrong system, and the pack refuses rather
than guessing:

```
[skip] fluid.buoyancy : model provides no fluid density: one of fluid_density_kg_m3,
       water_density_kg_m3, ... (kg/m3; 998 fresh water at 20C, 1025 sea water)
[skip] fluid.righting_arm : heel_angle_deg 25.0 is past the 10 deg small-angle ceiling:
       GZ=GM.sin(theta) over-predicts once the waterplane moves, so this needs a full GZ
       curve from hydrostatics at each angle (tier 2), not a closed form
[skip] fluid.righting_arm : model states no heel angle: one of heel_angle_deg,
       design_heel_deg, heel_deg (deg, at or under 10). GZ is a function OF the angle, so a
       default here would be the gate choosing the load case - and the flattering end of it
[skip] fluid.drag : Cd 0.47 for 'sphere' is tabulated for Re 1.0e3-2.0e5 and this flow is at
       Re 3.0e6 (turbulent) - using it here would be a silent error, so nothing is settled
[skip] fluid.pipe_pressure_drop : model provides no flow IN THIS LINE: one of
       flow_rate_m3_s, ... (m3/s) or one of pipe_velocity_m_s, ... (m/s). A free-stream or
       cruise velocity is deliberately NOT accepted here - it is the speed of the body
       through the fluid, not the bulk velocity in the bore
[skip] fluid.pipe_pressure_drop : the 3.00 m of head available is entirely spent lifting
       the liquid 4.00 m (one of static_lift_m, elevation_head_m, static_head_m, ...):
       -1.00 m is left for friction ... This is a duty-point failure, not a pipe-sizing result
```

Each of those resolves its claim to **BLOCKED** in the readiness report — visible,
named, and fixable. A pack that defaulted the density to 1000 and carried on would
have produced four green rows and one quiet lie. The fifth and sixth are the two
that used to be *passes*: a boat's cruise speed silently became the bulk velocity
in a cooling line, and an elevation lift silently became a friction budget.

## Proving the gates can fail

Every gate ships a fixture that changes one physically meaningful quantity, and
`nopekit gate selftest` requires each gate to fail its own. The whole point is
that a gate you have not watched fail is a gate you have no reason to trust.

All seven fixtures below are built from `selftest/baseline.json` — the
instrument raft, not some other boat — and each solves for the value that misses
the acceptance **the baseline states** by 1.15x, rather than a typed number that
happens to exceed today's limit:

```
[FAIL] fluid.buoyancy : SINKS: 2908.7 kg needs 2.9145 m3 of the 2.8160 m3 watertight
       volume -> volume fraction 1.035 vs 0.90 limit (-0.0985 m3 reserve buoyancy)
[FAIL] fluid.freeboard : freeboard 0.130 m = depth 0.350 - draft 0.219 at 1120.0 kg,
       vs 0.150 m minimum [stated min_freeboard_m]
[FAIL] fluid.metacentric : GM 0.304 m = BM 0.973 - BG 0.669, vs 0.350 m [stated min_gm_m]
[FAIL] fluid.righting_arm : GZ 0.0475 m at 8.0 deg (GM 0.341, BM 0.973), righting moment
       522 N.m vs demand 600 N.m; limit 0.0546 m [arm demanded by a 600 N.m heeling moment]
[FAIL] fluid.drag : drag 88.03 N at 1.65 m/s vs 25.00 N budget (Cd 1.20, A 0.0540 m2)
[FAIL] fluid.pipe_pressure_drop : dp 62.91 kPa = major 50.94 + minor 11.97, vs 18.60 kPa
       budget [from 2.50 m of head less 0.60 m static lift = 1.90 m for friction]
[FAIL] fluid.flow_regime : REGIME INVALID: internal Re 3.2e3 is in the 2300-4000 transition
       gap where neither 64/Re nor Colebrook-White holds
```

Two of those are worth reading closely, because they are the two that were wrong.

**`fluid.metacentric` fails at GM +0.304 m, not at a negative GM.** A 3.20 × 1.60 m
rectangular waterplane gives BM 0.97 m: put the *entire* 1120 kg at the deck edge
and GM is still comfortably positive. Nothing that fits inside this raft can
capsize it. The old fixture had to place the centre of gravity 3.9 hull depths up,
in mid-air, to force a negative number, and then described that as "a battery
moved from the bilge to the deck" — a calibration a reader could not use for
anything. What actually happens to a raft like this, and what the control now
reproduces, is topside equipment spending the stiffness margin: KG 0.779 m, which
is 0.23 m above the deck edge, where a mast's mass really is.

**`fluid.righting_arm` fails on a bit-identical demand.** `heeling_moment_nm` is
still 600 N·m and the heel is still 8°, so the limit — 0.0546 m — is exactly the
baseline's. What moved is KG, and therefore GM, and therefore the measured GZ:
0.0923 m in the baseline, 0.0475 m here. The fixture this replaced multiplied the
heeling moment by six, which moved the *limit* and left measured GZ identical in
baseline and control (0.1656 m in both). That control proved only that `gz >=
limit` compares two floats; GZ could have been a constant and it would still have
fired. This one is solved backwards through GZ = GM·sin θ, so the trigonometry,
the angle unit and the magnitude are all on the hook.
