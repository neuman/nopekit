# fluids-analytic

Closed-form fluid mechanics for things that float and things that carry flow.
Seven gates, all tier 0, all pure stdlib arithmetic, all of them finishing in
well under a millisecond.

**Reach for this before you reach for a solver.** A CFD install is two gigabytes,
an afternoon of setup, a meshing skill you may not have, and fifteen minutes a
run — and for the questions below it will tell you what a page of algebra already
told you, only later and with a mesh-dependence argument attached. Running CFD on
a hull you are still redrawing is a way of feeling productive. The honest
sequence is: settle everything in this pack first, let the design stop moving,
and *then* spend the solver on the questions listed under "what this pack cannot
settle" — which is where a solver actually earns its install.

## What this pack settles

| Claim, in plain language | Gate |
|---|---|
| It floats, and with reserve volume left over | `fluid.buoyancy` |
| The deck edge is still above the water when fully loaded | `fluid.freeboard` |
| It is upright-stable at zero heel, with a margin | `fluid.metacentric` |
| It can resist the heeling moment it will actually see | `fluid.righting_arm` |
| Drag at cruise is inside the thrust budget | `fluid.drag` |
| The pump/head available covers the losses in the line | `fluid.pipe_pressure_drop` |
| Every correlation above is being used inside its Reynolds range | `fluid.flow_regime` |

## What this pack CANNOT settle

This is the section that prevents the damage. All of these are things a reader
will assume are covered and are not:

- **Planing.** Everything here is hydrostatic. Above roughly Froude number 0.4
  the hull starts to be supported dynamically, the waterline shortens and rises,
  and every number in this pack becomes a statement about a boat at rest.
- **Wave-making resistance.** `fluid.drag` is form drag on a submerged body. A
  surface-piercing hull also makes waves, and near hull speed that term can
  dominate everything else. This pack does not compute it and does not know it
  exists.
- **Free-surface effects.** Liquid free to move inside the hull (fuel, bilge
  water, a part-full tank) subtracts i/V from GM every roll. `fluid.metacentric`
  does not know about your tanks. See `lenses.md`.
- **Dynamic stability.** GM and GZ here are static. A hull that rights itself
  slowly, or rolls in resonance with a wave period, or is knocked down by a gust
  and has no energy margin left, is not covered by any gate in this pack.
- **Trim and longitudinal stability.** This is the nearest omission of all, and
  the easiest to miss precisely because three hydrostatic gates go green next to
  it. Everything here is TRANSVERSE: `waterplane_inertia_m4` is the transverse
  second moment, GM and GZ are transverse, and the draft is one uniform number,
  V/Awp — which is to say the pack assumes the hull floats level. It does not
  balance LCG against LCB, it has no longitudinal GM, and it cannot tell you the
  trim angle. A hull trimmed by the head or stern has a lowest deck-edge
  freeboard materially below the single figure `fluid.freeboard` reports, so a
  green freeboard row is a statement about a level hull and not about yours.
- **Static lift and the pump duty point.** The pressure-drop gate now subtracts a
  stated elevation lift from the head supplied, but it takes that head as a given
  at this flow. A real pump curve falls as flow rises, so the duty point is the
  intersection of the curve and the system, and nothing here finds it.
- **Pipe networks.** One bore, one length, one ΣK. Branches, manifolds, parallel
  paths, loops and anything that needs flow to be distributed rather than stated
  are outside this gate entirely.
- **Slamming, sloshing, vortex shedding, yaw at speed, cavitation, water hammer,
  two-phase flow, compressible flow.** None of it. Each is named in
  `references/what_this_pack_cannot_tell_you.md` with the class of tool that can
  answer it and the tier that tool lives at.

And the standing caveat: a passing sweep here means the closed-form arithmetic is
satisfied at one loading condition, at rest, in still water. It is not a
seaworthy boat and it is not a commissioned system.

## Gates

| Gate | Tier | Measures | Limit comes from | Known-bad fixture |
|---|---|---|---|---|
| `fluid.buoyancy` | 0 | displaced volume / watertight volume | `max_volume_fraction`, else 0.90 | `overloaded` — loaded to 1.15x the allowed volume fraction |
| `fluid.freeboard` | 0 | deck edge above the waterline, m | `min_freeboard_m`, else 15% of hull depth | `low_deck_edge` — gunwale dropped to 1/1.15 of the minimum |
| `fluid.metacentric` | 0 | GM = BM − BG, m | `min_gm_m`, else 5% of waterline beam | `top_heavy` — KG raised until GM is 1/1.15 of the minimum |
| `fluid.righting_arm` | 0 | GZ at the stated heel, m | `min_righting_arm_m`, else the arm a stated `heeling_moment_nm` demands, else 1% of beam | `masthead_load` — KG raised until GZ misses an **unchanged** demand |
| `fluid.drag` | 0 | drag force, N | `max_drag_force_n` / `thrust_available_n` | `oversped` — 3x speed, so ~9x force |
| `fluid.pipe_pressure_drop` | 0 | total Δp, Pa | `max_pressure_drop_pa`, else ρ·g·(`available_head_m` − `static_lift_m`) | `pinched_bore` — 60% of the bore at the same flow |
| `fluid.flow_regime` | 0 | Reynolds number and ε/D vs the correlations' valid ranges | the offended band edge | `transitional_line` — flow turned down into the 2300–4000 gap |

Every fixture above solves for the value that misses, by 1.15x, the acceptance
`selftest/baseline.json` states — not a literal that happens to exceed the limit
the pack ships today. A control calibrated to a typed number stops being a control
the moment someone relaxes the threshold, and nothing says so.

`fluid.flow_regime` is the one gate here bound broadly, to every flow claim. It
measures no design quantity; it decides whether the other gates' numbers mean
anything, the way a slenderness check polices beam theory. When it trips, treat
every other flow verdict in the sweep as unproven. It checks three things: the
internal Reynolds number against the 2300–4000 transition gap, the external
Reynolds number against the band its table Cd was measured over, and — on the
roughness side of the same correlation — ε/D against the 0.05 ceiling of the
Moody chart, plus the resulting turbulent *f* against the 0.008–0.08 band a Darcy
friction factor cannot physically leave. Because it binds broadly, its *ability
to reach a verdict* matters as much as its verdict: an external-only project (a
strut, a float, a hull with no piping) must get a **valid** from it, not a skip.

### Prerequisites

`fluid.flow_regime` is the prerequisite (`needs`) of `fluid.drag` and
`fluid.pipe_pressure_drop`: it checks the Reynolds and roughness bands their
correlations were fitted in, and outside them the arithmetic still returns a confident
number. Below it failing they are not run; their claims read Skipped, `prerequisite
failed: fluid.flow_regime`. The hydrostatics gates (buoyancy, freeboard, metacentric,
righting arm) have no prerequisite: the flow regime does not guard them, and one
analysis failing is not a reason another's number is meaningless.

## Views

| View | Kind | Payload | Addressed by |
|---|---|---|---|
| `righting_arm_curve` | `chart` | GZ from upright to the small-angle ceiling, with the required arm as the limit line and the design heel marked | series key `gz`, or an x value |

Stability is a question about a curve and `fluid.righting_arm` answers it at one
angle. GZ climbs with heel here, so a hull that clears its demand at 8° may be
under it at 4°, and a verdict about 8° says nothing at all about 4°.

**The curve stops at `SMALL_ANGLE_MAX_DEG` (10°), and the chart says so in
`meta.validity`.** GZ = GM·sin(θ) is the initial slope of the real righting-arm
curve; past ten degrees the immersed shape has moved, the deck edge may be in the
water, and the closed form over-predicts. The gate SKIPS there. A chart drawn to
forty degrees from the same formula would commit the identical over-prediction in
a picture — and a picture is the more persuasive of the two, so it would be the
worse lie. Past that angle you need a full hydrostatic GZ curve at each angle,
which is tier 2 and is not in this pack.

The limit line resolves in the same order the gate resolves it — stated
`min_righting_arm_m`, else the arm a stated heeling moment demands, else a
fraction of beam — so the chart and the verdict draw one line. With none of the
three there is no chart: a GZ curve with nothing across it invites the reader to
decide for themselves whether the arm is enough, which is the judgement the ledger
is supposed to be carrying. Every approximation that produced GM (a wall-sided
draft, a rectangular waterplane, which is OPTIMISTIC for a finer hull) rides in
`meta.approximations`, because a curve is exactly where an optimistic assumption
stops being visible.

The design point is marked only when the model states a heel inside the ceiling —
the two cases where `fluid.righting_arm` skips get no marker, because a marked
point on a chart whose gate settled nothing would show a number the ledger does
not carry.

No gate in this pack emits locators: a hull's buoyancy, freeboard, GM and GZ are
properties of the whole body, and this pack never sees geometry it could point at.

## Units and frames

**SI only, and unprefixed.** Metres, kilograms, seconds, m², m³, m⁴, kg/m³,
pascals, newtons, N·m. Angles are degrees at the parameter interface and radians
inside. Two slips account for most of the errors in this domain:

- **Roughness in millimetres.** Every published pipe-roughness table is in mm or
  in feet. This pack wants `pipe_roughness_m` in **metres**: drawn plastic is
  `1.5e-6`, not `0.0015`. A factor of a thousand here changes a friction factor
  by about 3x and never looks wrong.
- **Dynamic vs kinematic viscosity.** ν (m²/s) and μ (Pa·s) differ by ρ, about a
  thousand for water. Either key is accepted; state which one you mean by using
  its own name (`kinematic_viscosity_m2_s` or `dynamic_viscosity_pa_s`).

**Vertical datum.** `kg_m` (centre of gravity), `kb_m` (centre of buoyancy),
`draft_m` and `hull_depth_m` must all be measured from the **same** datum — the
keel, or the lowest point of the hull, consistently. Mixing datums produces a
confidently positive GM for a boat that is about to roll over, and nothing in the
arithmetic can detect it.

**Head: supply and demand have opposite signs.** `available_head_m` and
`pump_head_m` are what the system **supplies**; ρ·g·h of it becomes the friction
allowance. `static_lift_m`, `elevation_head_m` and `static_head_m` are the
elevation the liquid is **lifted** — a demand, subtracted from the supply before
anything is compared, positive upward, negative for a downhill run. Stating only
a lift settles nothing and the gate skips. Reading a lift as a supply is not a
small error: 5 m of lift read backwards invents 49 kPa of allowance that does not
exist, and the verdict line looks entirely normal.

**Reference area.** A drag coefficient is meaningless without the area it was
defined against. Every Cd in this pack's table is referenced to **frontal** area —
*except where the row says otherwise*, and one row does: `cube_edge_on` is
referenced to the cube's plain face a², not to the √2·a² a yawed cube actually
presents, so following the blanket rule there overstates drag by 41%. Automotive
and aerofoil sources are usually referenced to planform or wetted area; using one
of those with a frontal area is a factor-of-several error with no symptom.

**Heel angle.** There is no default. GZ is a function *of* the angle, so a gate
that supplied one would be choosing the load case — and the old default of 10°
sat exactly on the small-angle ceiling, i.e. at the largest arm the gate can ever
report. State `heel_angle_deg`, at or under 10, or `fluid.righting_arm` skips.

## The physics in one paragraph

A floating body displaces its own mass of fluid (V = m/ρ), so the useful question
is what fraction of the watertight volume that already consumes. Tilt it and the
centre of buoyancy shifts sideways by roughly (I_waterplane/V)·θ, which puts the
metacentre a height BM = I/V above the centre of buoyancy; the body is stable at
small heel if the metacentre is above the centre of gravity, GM = BM − BG > 0,
and the righting arm is GZ ≈ GM·sin θ until the heel gets big enough to move the
waterplane. Push a body through fluid and it feels ½ρv²·Cd·A, where Cd is a
function of the Reynolds number Re = vL/ν and not a property of the shape. Push
fluid through a pipe and it loses (f·L/D + ΣK)·½ρv², where f is 64/Re while the
flow is laminar and the Colebrook-White root once it is turbulent. Every one of
those is a closed form; the whole pack is those five sentences and the discipline
of checking that Re is where the correlation was fitted.

Sanity anchors: fresh water is 998 kg/m³, sea water 1025; ν for water at 20 °C is
1.0e-6 m²/s; a friction factor outside 0.01–0.08 in turbulent flow is a mistake,
and `fluid.flow_regime` refuses outright outside the slightly wider 0.008–0.08 so
it never argues with a legitimately smooth, very fast line; ε/D above 0.05 is off
the Moody chart and refused too; a GM of tens of metres means you gave a
waterplane inertia in the wrong units.

## Common failure modes, and what they look like in a verdict

- **A model that gives no acceptance.** `fluid.drag` computes 6.74 N and skips,
  because a measurement with nothing to compare it against settles no claim. The
  skip names the key to add. The claim goes BLOCKED, visibly.
- **A Cd quoted outside its regime.** `[skip] fluid.drag : Cd 0.47 for 'sphere'
  is tabulated for Re 1.0e3-2.0e5 and this flow is at Re 3.0e6`. This is the
  single most common silent error in the domain and the pack refuses to commit it.
- **Flow in the transition band.** `[FAIL] fluid.flow_regime : internal Re 3.0e3
  is in the 2300-4000 transition gap`. Darcy-Weisbach will still hand you a
  number there; it may be wrong by two to one.
- **Small-angle stability quoted at a large angle.** Ask `fluid.righting_arm` for
  25 degrees and it skips rather than answering: past ~10 degrees the waterplane
  has moved and GM·sin θ over-predicts. That needs a GZ curve, which is tier 2.
- **A quantity from the wrong system.** `fluid.pipe_pressure_drop` will not read
  a cruise or free-stream velocity as the bulk velocity in a bore, because this
  pack ships hull gates and pipe gates in one manifest and a project stating both
  is the normal case. It skips and names `flow_rate_m3_s` or `pipe_velocity_m_s`.
- **An approximation silently doing the work.** Verdicts tag which path ran —
  `[T=V/Awp, wall-sided]`, `[I=Awp*B^2/12, rectangular waterplane, OPTIMISTIC for
  a finer shape]`, `[KB=T/2, box section]`. If a verdict is marginal and carries
  one of those tags, supply the real quantity before believing it.

## Parameters

Every quantity is looked up under several spellings; if none is present the gate
SKIPS and names the keys. Nothing is defaulted except the acceptance margins, and
those are stated in the verdict. The full accepted-key lists are at the top of
`gates/_fluids_analytic.py` and `gates/flow.py`; the shapes a project can take
are worked in `references/worked_hull.md`.

## Where to look next

- `references/drag_coefficients.md` — the Cd table with the Reynolds band each
  value is valid over, and why the band is the important column.
- `references/pipe_roughness.md` — absolute roughness by material, ageing, and
  the K-factors for fittings.
- `references/worked_hull.md` — a complete hull and a complete line taken through
  all seven gates, with the real output.
- `references/what_this_pack_cannot_tell_you.md` — the honest list, each item with
  the class of tool that can answer it.
- `lenses.md` — the adversarial review to run before anything is built.
- `sourcing.md` — the constraints that are not physics.
