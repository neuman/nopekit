# What this pack cannot tell you, and what to reach for instead

The value of a tier-0 pack is entirely in knowing where it stops. Everything
below is a real question that a thermal design will eventually ask, that closed
form cannot answer, and that will be answered wrongly by this pack if you squint
at a verdict hard enough.

Method rule 10 applies to all of it: **offer the cheap analytic bound first, and
defer the solver until the design has stopped moving.** Running a transient
conjugate CFD on an enclosure you are still redrawing is a way of feeling
productive.

---

## 1. Transient, multi-dimensional conduction

**The gap.** `thermal.time_constant` gives one exponential for one lumped body,
and it refuses (correctly) when Biot exceeds 0.1. It cannot give you the
temperature *distribution* through a thick wall, a potted assembly, a battery
module or a laminated absorber, at a time of your choosing. Nor can it handle a
spatially varying source, an internal heat generation profile, or contact between
bodies with different time constants.

**Symptom that you have hit it.** `thermal.time_constant` fails with Bi > 0.1, or
you find yourself wanting to ask "how hot is the middle after ten minutes".

**Reach for.** A finite-element or finite-volume transient conduction solve.
Elmer FEM and CalculiX are open, scriptable and free; Code_Aster is heavier and
more capable. For 1D layered transient problems a small explicit or Crank-Nicolson
scheme you write yourself is often enough and is a tier-1 gate, not tier-2.

**Cost, honestly.** Elmer or CalculiX: a few hundred MB installed, hours to learn
the mesh and boundary-condition workflow, seconds to minutes per solve once set
up. Meshing is where the time goes, not solving.

## 2. CFD-level flow detail

**The gap.** Every convection number in this pack comes from a correlation fitted
to an idealised geometry in a well-behaved flow. Real enclosures do not have
well-behaved flow. This pack cannot see recirculation behind an obstruction, a
dead zone in a corner, hot exhaust re-ingested by an inlet, a fan operating off
its curve against a system impedance it cannot overcome, jet impingement,
duct-entry effects, or the difference between a heatsink's first fin channel and
its last.

**Symptom.** The convection gate fails with "outside the validated band", or you
cannot honestly say which correlation geometry your part is, or the measured
prototype disagrees with the calculation by more than 50%.

**Reach for.** OpenFOAM for general CFD; a dedicated electronics-cooling tool if
the budget exists. For enclosure air temperature specifically, a flow network
model (nodal, not CFD) is a much cheaper middle step and is frequently enough.

**Cost, honestly.** OpenFOAM: 2-3 GB installed, a real learning curve measured in
weeks not hours, and 20 minutes to many hours per case. Mesh quality dominates the
answer. Do not put this in an inner loop.

## 3. Condensation, humidity, and phase change

**The gap.** There is no humidity anywhere in this pack. It cannot tell you
whether a surface will reach dew point, where interstitial condensation forms in a
wall, whether a vapour barrier is on the correct side, or whether an enclosure
will sweat on the first cold morning. It cannot handle boiling, evaporative
cooling, heat pipes, vapour chambers, phase-change materials, frost, or ice.

**Symptom.** Any question with the words condensation, dew point, humidity, mould,
boiling, heat pipe, or freeze-thaw in it.

**Reach for.** WUFI or equivalent hygrothermal software for building envelopes
(this is a specialist field and the transient moisture physics genuinely needs
it); manufacturer models for heat pipes and vapour chambers, which are
empirically characterised devices, not analysable ones. Glaser-method
interstitial condensation is closed form and could be a tier-0 gate, but it is
a steady-state approximation that misrepresents summer drying, and it is not in
this pack.

## 4. Real weather and annual yield

**The gap.** `solar.irradiance` is a clear-sky model. It has no opinion about
cloud, and therefore none about energy over a year. It also does not model
shading of any kind.

**Symptom.** Anyone asks for kWh/year, a payback period, or a system size.

**Reach for.** A measured or satellite-derived weather series (TMY, PVGIS, NSRDB)
driving an hourly simulation — SAM for solar thermal and PV, or a spreadsheet if
the system is simple. This is a tier-3 dependency because it needs *data*, not
just a tool, and the data has its own licence and its own uncertainty.

**Cost.** The data is usually free; the honest statement of its uncertainty
(typically +-5-10% on annual GHI, worse on a tilted plane) is the part people
drop.

## 5. Contact resistance from first principles

**The gap.** You supply the interface resistance to
`thermal.steady_state_temp`; it multiplies. Predicting contact resistance from
surface roughness, flatness, hardness, clamp pressure and interstitial material is
a materials-science problem this pack does not attempt, and published correlations
for it are wide.

**Reach for.** Measurement. This is one of the cases where a thermocouple and an
afternoon beats any model, and the number you measure is specific to your joint.

## 6. Thermal stress and expansion

**The gap.** This pack gives temperatures. What those temperatures do to a bonded
joint, a solder fillet, a glazed pane in a frame, or a pipe run is structural
work.

**Reach for.** A structural pack with a thermal load case, or an FE solve with the
temperature field imported. The lens list in `lenses.md` tells you which joints to
ask about.

## 7. Spectral and directional radiation

**The gap.** `thermal.radiation` is grey-body: one emissivity, one view factor,
no wavelength dependence and no angular dependence. It cannot model a selective
surface's actual spectrum, a glazed cavity's greenhouse behaviour in detail, an
incidence-angle modifier on glazing transmittance, or participating media
(smoke, fog, combustion gas).

**Reach for.** A spectral radiation model or measured optical data for the
specific coating and glazing. For glazing incidence-angle modifiers, the
manufacturer's IAM curve is usually published and can enter a model as a
correction on `tau_alpha`.

## 8. Two-stream heat exchangers, ducts and pressure drop

**The gap.** No internal-flow correlations, no effectiveness-NTU, no pressure
drop, no pump or fan curves. A collector loop's pump, a plate heat exchanger's
approach temperature and a duct's static pressure are all outside this pack.

**Reach for.** Effectiveness-NTU and Darcy-Weisbach are both closed form and
would make a good sibling pack. They are not in this one, and pretending a
collector's output number accounts for its loop is how a system underperforms its
components.

---

## The escalation rule

Before installing anything, write down:

1. **The unvalidated physical quantity**, precisely. Not "check the thermals" —
   "core temperature of the potted module 600 s after a 40 W step".
2. **Why the closed-form bound cannot answer it**, in one sentence.
3. **What the tool costs** — install size, learning time, run time per case.
4. **The negative control the new gate will have to pass.** A CFD gate that
   cannot tell a finned heatsink from a solid block of the same envelope is not a
   gate.

Then offer the human the choice with the real cost attached, and default to the
cheap bound while the design is still moving.

## 9. The peak skin temperature of a handheld

**The gap.** `thermal.skin_temperature` gives the AVERAGE temperature of a
sealed shell's uncovered surface. It does not give the peak over a source. A
2 mm polymer wall spreads heat only about 6 mm, so the skin over a charger or a
processor can be many kelvin above the average. A touch limit applies at the
hottest point a person can touch.

**Symptom.** A claim about the hottest point, a certifier's thermocouple, or a
hot spot a person can feel.

**Reach for.** Measurement first: a thermocouple at the hottest point of a print
with the real parts (or a heater of the same power and footprint), in still air
at the claim's ambient, recorded with `claim physical`. For a design that is
still moving, a conduction solve of the wall with the board's sources (Elmer FEM
or CalculiX, as in section 1) gives the spread.

**Cost, honestly.** A print, a heater or a board, a thermocouple and an hour per
iteration. The solve costs what section 1 says, and it still needs the contact
between the parts and the wall, which section 5 says only a measurement gives.
