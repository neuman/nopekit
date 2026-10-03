# thermal-analytic

Closed-form heat transfer and solar collection. Ten gates, all tier 0, all
stdlib — no solver, no mesh, no network call, nothing to install. Every gate runs
in under a millisecond, which is the point: heat transfer is where a design gets
sanity-checked on every edit, and a twenty-minute CFD run in that loop means the
check does not happen.

It serves four kinds of problem with one vocabulary: solar thermal, electronics
cooling, heated enclosures, and insulation.

---

## 1. What this pack settles

- **The U-value of a wall, roof or enclosure stack** including framing, so the
  answer is the wall's heat loss and not the insulation's.
- **The convective coefficient**, natural or forced, with the correlation and its
  Rayleigh/Reynolds regime named in the verdict.
- **Net radiant exchange** between two grey surfaces with a real view factor, and
  the linearised `h_rad` that lets radiation enter a resistance network.
- **Whether a fin or heatsink is worth fitting at all** — effectiveness, not
  efficiency, including the case where adding the fin makes the part hotter.
- **A component temperature** from a power input and a named resistance network.
- **The lumped time constant, and whether lumped capacitance is legitimate** for
  that body at all.
- **Useful heat from a flat-plate solar collector**, and — separately, because it
  is a separate question with the opposite sensitivity — the **stagnation
  temperature** it reaches with no flow.
- **That the `h` handed to the lumped gates is the `h` the model's own convection
  inputs produce.** The one cross-representation check this pack can make.
- **Clear-sky irradiance on a tilted, oriented surface** — pure geometry plus a
  clear-sky model.

## 2. What this pack CANNOT settle

This section is longer than the last one on purpose. Everything here is a thing a
reader will otherwise assume is covered.

- **Transient, multi-dimensional conduction.** One time constant on one lumped
  body is the entire transient capability; a temperature *distribution* through a
  thick wall or a potted assembly needs an FE solver.
- **Anything that requires knowing the flow field.** Recirculation behind an
  obstruction, a stalled duct, jet impingement, a fan curve against system
  impedance, hot air re-ingested into an inlet. A Nusselt correlation assumes the
  flow it was fitted for actually exists, and in a real enclosure it usually does
  not.
- **Condensation, evaporation, boiling, freezing, any phase change.** No
  humidity anywhere. A dew-point question it cannot represent, let alone answer.
- **Real weather, and therefore annual yield.** `solar.irradiance` is a
  CLEAR-SKY model. It answers "is the geometry right?"; it has no opinion about
  cloud and cannot produce kWh/year.
- **Contact resistance from first principles.** You supply the interface
  resistance; this pack multiplies. Predicting it from surface finish, pressure
  and flatness is a materials problem it does not touch.
- **Thermal stress, expansion mismatch, fatigue.** It gives you temperatures;
  what they do to a bonded joint is a structural pack's work.
- **Radiation in participating media,** spectral or directional properties, or
  anything a grey-body assumption cannot carry (a selective surface's spectrum).
- **Ducts, plenums, heat exchangers with two streams, and pressure drop.** There
  is no internal-flow or effectiveness-NTU capability here.
- **Shading, of any kind** — no horizon profile, no neighbour, no row-to-row
  self-shading. `solar.irradiance` computes what an *unobstructed* plane sees,
  and it advertises "plane-of-array irradiance", which is the phrase somebody
  with a shading question will match on.
- **Photovoltaics.** `settles` and `claim_classes` will match a PV claim through
  `atompipe gap`, and there is no cell model here, no temperature coefficient, no
  spectral response, no inverter, no derate. Sunlight onto a plane is where this
  pack stops.
- **Most agreement between numbers a model states twice.**
  `thermal.h_agreement` reconciles one pair — `body_h_w_m2k` against the
  correlation — and that is the whole of method rule 6 here. Unreconciled, each
  one silently breakable by a user: `fin_h_w_m2k` (usually a different fluid on a
  different surface, so agreement would be *wrong*, not merely unchecked);
  `surface_c` against `thermal.steady_state_temp`'s network; the sink node
  against `1/(h*A)`, whose area is nowhere in the projection; and
  `irradiance_w_m2` against `solar.irradiance` for the same roof. The baseline
  couples all of those by hand.
- **A loss coefficient that varies with temperature.** `collector_ul_w_m2k` is
  constant, so `solar.stagnation` over-estimates by an unquantified margin — the
  conservative direction, and still not a measurement.

When the cheap gate cannot answer and the design has stopped moving, see
`references/limits.md` for which tier-2 tool to reach for and what it costs.

## 3. Gates

| Gate | Tier | Measures | Limit comes from | Fails its fixture because |
|---|---|---|---|---|
| `thermal.conduction` | 0 | U-value, W/m2K | `wall_u_limit_w_m2k` (project/code) | the highest-resistance layer's k is raised until the insulation is gone |
| `thermal.convection` | 0 | h, W/m2K, + Ra or Re | `h_required_w_m2k` | the driving velocity (or the driving dT) collapses |
| `thermal.h_agreement` | 0 | relative drift between two h | `h_agreement_tol`, default 0.10 | `body_h_w_m2k` is left stale after the flow changed |
| `thermal.radiation` | 0 | net radiant exchange, W | `rad_limit_w` with `rad_sense` | the coating degrades, in whichever direction `rad_sense` says hurts |
| `thermal.fin_efficiency` | 0 | effectiveness | `fin_effectiveness_min`, default 2.0 | the fin is moulded in a polymer and its ceiling drops under the floor |
| `thermal.steady_state_temp` | 0 | temperature, degC | `temp_limit_c` (datasheet) | the interface node goes dry, or a dry interface is inserted where none was named |
| `thermal.time_constant` | 0 | Biot number (+ tau) | `biot_limit` 0.1, the lumped-model licence | the body is moulded in a polymer and Bi passes the licence |
| `solar.collector_output` | 0 | useful heat, W | `collector_output_min_w` | the back insulation waterlogs and U_L rises |
| `solar.stagnation` | 0 | absorber temperature with no flow, degC | `stagnation_limit_c` (seals and fluid) | the collector is *improved* — U_L falls and it stagnates hotter |
| `solar.irradiance` | 0 | plane-of-array, W/m2 | `design_irradiance_min_w_m2` | the array is mounted facing away from the sun |

Fixtures are in `selftest/bad_thermal.py`, one function each, each changing one
physically meaningful quantity. Run `atompipe gate selftest` and watch all ten
fail.

**Fixtures are sealed, and their severity is derived** — two properties, both
needed. *Sealed*: each fixture builds its projection from
`selftest/baseline.json` and inherits nothing from the host project, so an honest
value in your model cannot defuse a control. *Derived*: each computes its
perturbation from **the limit the gate will compare against**, read out of the
baseline, and applies the worse of that and the physically named fault. The named
fault is what runs on the shipped baseline; on any other set of limits the
fixture escalates until the gate must refuse. A fixture holding a literal only
ever falsifies the thresholds the pack happened to ship with.

**Two honest gaps, stated rather than hidden.**

1. `thermal.time_constant` enforces Biot *and*, when `time_constant_limit_s` is
   projected, tau — and the shipped control attacks Biot only. It cannot do
   otherwise: `tau = rho*V*cp/(h*A_s)` does not contain the body's conductivity.
   If you lean on the tau limit, add a local fixture raising `body_h_w_m2k` until
   tau passes its ceiling. (When tau is the failing branch the verdict reports
   tau and its limit in seconds, not Biot.)
2. The clock-time-to-`solar_hour` conversion is deliberately outside this pack,
   so **no gate anywhere can catch a sign error in it** — and it is the most
   common silent error in solar geometry work. `references/solar-model.md` has
   the formula, its convention and three worked sites. Check it by hand.

Stagnation used to be a third entry here, as a second inequality inside
`solar.collector_output`. It is now its own gate, because the fixture that
falsifies output raises `U_L`, which makes stagnation *safer* — see
`references/collector-example.md` step 4.

**`thermal.time_constant` is the policeman.** It is the only gate here bound
broadly, and deliberately: Biot above 0.1 means the body does not have *a*
temperature, so the single-node result from `thermal.steady_state_temp` and the
time constant are both describing a fiction. When it trips, stop trusting those
numbers rather than tuning them.

### Prerequisites

None yet, on purpose. `thermal.time_constant` holds the Biot check that guards lumped
capacitance, but it also fails on a time constant over `time_constant_limit_s` — a
measurement, which says nothing about the steady-state node — so making it
`thermal.steady_state_temp`'s prerequisite would hide that gate's number behind an
unrelated fail. The edge lands once the Biot half is its own evaluator.
`thermal.h_agreement` compares two representations rather than guarding one, and
`thermal.convection`'s own control (`fan_stopped`) fails it.

## 3b. Views

| View | Kind | Payload | Addressed by |
|---|---|---|---|
| `collector_curve` | `chart` | Hottel-Whillier useful output against `T_inlet − T_ambient`, with `collector_output_min_w` as the limit line, the design point marked, and the stagnation crossing where the line reaches zero | series key `useful`, or an x value |

Hottel-Whillier is a straight line and everything a flat-plate collector does sits
on it. The intercept is the optics, the slope is the losses, and **the point where
the line reaches zero is stagnation** — `dT = tau_alpha·G/U_L`, which is exactly
the rise `solar.stagnation` measures. Two gates read the one line from opposite
ends: `solar.collector_output` wants the design point high enough,
`solar.stagnation` wants the zero crossing low enough. A table of numbers keeps
that relationship invisible; on the chart it is the same line, and the inversion
this pack keeps warning about — *the better the collector, the hotter it
stagnates* — becomes something you can see, because better insulation is a
shallower slope and a shallower slope reaches zero further right.

Three markers: the design point (with its efficiency and its share of the required
output), the stagnation crossing (with the absorber temperature), and
`stagnation_limit_c` on the same axis when the model states one, so the headroom
between "where it stagnates" and "what the seals and the glycol survive" is a
distance rather than a subtraction.

The series stops at the crossing rather than running into negative output: past
stagnation the formula describes a collector being fed fluid hotter than it can
reach, which is a real thing and a different question, and a plunging negative
tail invites a reader to read a loss rate off a model fitted to gains.
`meta.validity` says the rest — one irradiance, one constant `U_L`, and real
`U_L` climbs with absorber temperature, so the crossing shown is a conservative
bound.

No gate in this pack emits locators. Every one measures a scalar over a named path
or surface that has no geometry in the projection — a resistance node, a wall
stack, an aperture. `resistance_path_k_w` names its nodes, and when this pack
grows a `diagram` view of that network those names are where its locators will
land; until there is something drawn, a locator would point at nothing.

## 4. Units and frames

SI throughout, with three places where people get hurt:

- **Temperature.** Every parameter ending `_c` is degrees Celsius; every
  calculation is in kelvin internally. Differences are identical in both, absolute
  values are not, and radiation is the fourth power of the absolute one.
- **Resistance has two flavours.** `m2*K/W` is *per unit area* (wall stacks,
  `thermal.conduction`). `K/W` is *absolute* (a component's path,
  `thermal.steady_state_temp`). They differ by an area and the units are the only
  warning you get.
- **"Ambient" is not one number, and this pack refuses to pretend it is.**
  `ambient_c` is the air the part actually sees (inside an enclosure, often
  15-25 K above room). `collector_ambient_c` is outdoor air, and it is a separate
  key on purpose — sharing one "ambient" across a building and its outside is a
  units-grade error that reads as a perfectly plausible number.

Solar frames, stated once and meant literally:

- `solar_hour` is **solar time** (noon = sun on the local meridian), not clock
  time. The longitude and equation-of-time correction belongs in your model; the
  formula is in `references/solar-model.md`.
- `surface_azimuth_deg`: **0 = due south, +90 = west, -90 = east.** In the
  southern hemisphere the equator-facing orientation is 180, not 0.
- `latitude_deg`: **north positive**. Longitude is **east positive** (Berlin
  13.4, Vigo -8.7). The pack never reads a longitude — it is needed only for the
  solar-time conversion, which is in your model — but the convention has to be
  written down somewhere or the conversion's sign is a coin toss.

`char_length_m` is defined **per geometry** and it is not one rule. Using the
wrong one is wrong by `L^3` inside Rayleigh and nothing about the answer looks
wrong:

| Geometry | `char_length_m` is |
|---|---|
| `vertical_plate` | the plate height |
| `horizontal_plate_hot_up` / `_hot_down` | **area / perimeter** — for a square plate of side `a` that is `a/4`, not `a` |
| `horizontal_cylinder` | the diameter |
| `flat_plate` (forced) | the streamwise length along the flow, not the span |
| `cylinder_cross_flow` | the diameter |
| lumped body, for Biot | volume / surface area |

`thermal.convection` echoes the expected definition for the geometry you chose
into its own verdict line, so a mismatch shows up in the output rather than only
here.

**The sign of the temperature difference also chooses a correlation.** A *cold*
plate facing up traps its plume exactly as a *hot* one facing down does, so the
two `horizontal_plate_*` forms are swapped automatically when the surface is the
colder one, and the substitution is named in the verdict. Without it a chilled
plate declared hot-face-up gets `h` 2x too high using nothing but correct
correlations (`references/correlations.md`).

`selftest/baseline.json` is the one projection this pack owns: every key any gate
reads, plus a `_notes` line per key giving its unit and why it holds that value.
It is what CI verifies every gate against. `scaffold/reference_params.py` loads
it and exposes it as `PARAMS` — a view, not a second copy, deliberately: the two
used to be separate files with different numbers and they drifted.

## 5. The physics in one paragraph

Heat moves three ways and they add. Conduction is `q = kA*dT/L`, so resistances
add in series and conductances add in parallel — that is the whole of the wall
stack and the whole of the component network. Convection is `q = hA*dT`, where
`h` is not a material property but the output of a correlation
`Nu = f(Ra or Re, Pr)` fitted to one geometry over one range; in air, natural
convection gives 2-25 W/m2K and forced 10-200, and a number outside that for air
is wrong. Radiation is `q = eps*F*A*sigma*(T1^4 - T2^4)` in ABSOLUTE temperature;
it is comparable with natural convection at small dT and dominant at large dT, and
its linearised `h_rad` for a black surface near room temperature is about 5-6
W/m2K. Transients are governed by `tau = rho*V*cp/(h*A)` provided `Bi = hL_c/k`
stays below 0.1. If a result violates any of those magnitudes, suspect the input
before the arithmetic.

## 6. Common failure modes, and what they look like

- **`skip` naming a key.** Designed behaviour, not breakage: the claim goes
  BLOCKED and stays visible. Add the key to the model, not a default to the gate.
- **`FAIL` saying "outside the validated band".** Ra or Re left the range the
  correlation was fitted over, or the film temperature left the property table.
  The arithmetic produced a number; it is not evidence. Usually the wrong
  geometry, or a flow that does not actually exist.
- **`FAIL` on `thermal.time_constant` with Bi > 0.1.** Treat every other lumped
  result as void until the body is subdivided or the model moves to a solver.
- **Effectiveness below 1 on `thermal.fin_efficiency`.** The fin is a liability.
  Check the reported ceiling `sqrt(kP/(hA_c))` before reaching for more length —
  when the ceiling is the binding number, a taller fin changes nothing.
- **`solar.collector_output` green and `solar.stagnation` red.** Correct and
  important, and the reason they are two gates. The better the collector, the
  hotter it stagnates, because stagnation is `tau_alpha*G/U_L` and does not care
  about area or flow. Do not fix it by making the collector worse.
- **`thermal.h_agreement` red with every other gate green.** Two numbers that
  describe one surface have drifted apart — almost always a velocity, a length or
  a surface temperature edited without re-deriving `body_h_w_m2k`. Nothing is
  wrong with the design yet; the model has stopped being one model.
- **A U-value that looks too good.** Almost always a stack modelled without its
  framing. Use the `parallel` layer shape.

## 7. Where to look next

- `references/materials.md` — conductivity, density, specific heat, emissivity.
- `references/correlations.md` — every correlation with its valid Ra/Re band.
- `references/collector-example.md` — a flat-plate collector worked end to end.
- `references/solar-model.md` — the clear-sky model, its coefficients, its error
  band, and the solar-time conversion.
- `references/limits.md` — what this pack cannot tell you, and the tier-2 tools.
- `lenses.md` — the adversarial review dimensions. Read before sizing anything.
- `sourcing.md` — insulation, glazing, fluids, TIM and coatings as procurable
  things rather than properties.
