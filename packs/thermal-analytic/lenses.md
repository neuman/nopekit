# Adversarial lenses — thermal

Run these before anything is built, and run them against the *design case the
model does not contain*. Every heading below has ended a real project's week.
Each question is meant to be answerable with a number, not a shrug.

---

## Worst-case ambient

The ambient in the model is almost never the ambient in service.

- What is the air temperature **inside** the enclosure after an hour at full load
  with the lid on? Not the room — the room is where you measured, not where the
  part lives. A sealed box routinely sits 15-25 K above room air.
- What is the ambient on the hottest day this will ever see, in the position it
  will be installed — on a roof, against a south wall, in a vehicle in the sun,
  in a rack behind another machine's exhaust?
- If it is outdoors, is the **sky** temperature being used for radiation rather
  than the air temperature? On a clear night the effective sky is 15-25 K below
  air, and that is a radiator's best friend and a frost's cause.
- Does anything derate with temperature — a battery, a fan bearing, an
  electrolytic capacitor, an adhesive? At what ambient does the derating curve
  start, and is that inside your design window?
- Altitude: air density falls, so forced convection falls with it. Was the fan
  sized at sea level for a product that ships to 2000 m?

## Stagnation, and the day the flow stops

The design case everybody forgets is the one with no flow on a sunny day.

- **What temperature does the absorber reach with the pump off and full sun?** It
  is `T_amb + tau_alpha*G/U_L`, it does not depend on area or flow rate, and it
  gets *worse* the better you insulate. What survives it: the seals, the glazing
  gasket, the absorber coating, the heat transfer fluid, the pipe insulation, the
  sensor?
- Glycol degrades irreversibly above roughly 120-140 degC and turns acidic. Does
  your stagnation temperature exceed that, and if so what is the plan — a dump
  load, a drain-back, a cover, an accepted fluid change interval?
- Steam. If the fluid flashes, where does it go, what pressure does the loop see,
  and is the expansion vessel sized for vapour rather than for thermal expansion
  of liquid?
- The same question outside solar: what happens when the **fan stops**? Not "does
  it alarm" — what is the steady temperature with h reduced to the natural
  convection value, and how long does it take to get there? That second number is
  your time constant, and it decides whether a shutdown is fast enough to matter.
- What happens when the flow is merely *reduced* — a partly blocked filter, a
  fouled pipe, a fan at half speed on a quiet-mode setting nobody told you about?

## Thermal expansion at the joints

Heat gets into structure through fasteners and bonds.

- Over the full temperature range, how much does each member grow, and what
  restrains it? Aluminium against steel is roughly 12 um/m/K of differential;
  aluminium against a polymer can be 100.
- Across a bonded or bolted joint of length L with a differential expansion
  `dalpha*dT*L`, what shear does the adhesive or the fastener see? Is the joint
  slotted, or is it going to do the yielding for you?
- Glazing in a frame: is there room for the pane to grow, at both ends, at the
  highest temperature the frame will ever see — including stagnation, not just
  operation?
- Copper pipe in a long run grows about 1.7 mm per metre per 100 K. Where is the
  expansion loop, and is it somewhere a fitter will actually leave it?
- Does anything clamp a thermal interface? A TIM's resistance is a function of
  pressure, and the pressure changes with temperature as the stack expands.

## Freeze protection

The cheapest failure and the most total.

- What is the lowest temperature any wetted part will ever see, including the
  outdoor run, the roof penetration, and the bit inside the wall near a vent?
- If the answer is below zero: glycol, drain-back, or trace heating? Each has a
  cost the model does not carry — glycol cuts specific heat and raises viscosity
  (so the pump, the heat exchanger and the collector output all change), drain-back
  constrains the entire pipe geometry to a continuous fall, trace heating fails
  when the power does.
- A freeze event usually happens during a **power cut**, which is exactly when
  the pump and the trace heating are off. Does the protection survive losing
  power, or does it depend on it?
- Water expands about 9% on freezing and it does not care about your pipe's
  pressure rating.

## Coating and surface degradation

Every optical and radiative number in the model is a new-condition number.

- A selective absorber coating that starts at alpha 0.95 / eps 0.05 does not stay
  there. UV, heat cycling and humidity all move emissivity **up**, which raises
  loss and lowers output. What does the model assume at end of life, and what
  does the warranty assume?
- Glazing transmittance falls with soiling, and soiling depends on the tilt and
  the site. A shallow tilt does not self-clean in the rain. What is the cleaning
  interval and who does it?
- Black anodising on a heatsink is worth having for radiation and is worth
  nothing if the part is inside a metal box that sees itself: check the view
  factor before paying for the finish.
- Dust on a heatsink is an insulating layer and a flow restriction at once. How
  much fin pitch closes up in a year in this environment, and is the pitch
  serviceable?
- Does anything outgas onto an optical surface at stagnation temperature?

## What happens when the pump stops

Give this its own review even though it overlaps the others, because it is a
sequence rather than a state.

- Trace the whole sequence: flow stops -> absorber climbs -> fluid expands ->
  fluid boils -> vapour displaces liquid -> the loop cools -> liquid returns onto
  a surface that is still hot. Which component sees the worst condition, and at
  which step?
- What tells the controller the pump stopped? A relay it commanded is not a flow
  measurement. If the sensor is in the wrong place it reports a temperature
  nobody cares about and the control loop is decorative.
- On restart, cold fluid hits a very hot absorber. Thermal shock on a glazed
  plate, and possibly a steam hammer in the pipework. Is the restart delayed
  until the plate has cooled?
- Is there a path for heat to go *backwards* overnight — a thermosiphon running
  the collector as a radiator and emptying the store into the sky? Where is the
  check valve, and is it the kind that actually closes at these flow rates?

## Transient and startup

- The model is steady state. How long does the design take to *get* there, and is
  anything worse on the way? A cold-start inrush, a fan that spins up late, a
  heater at full duty before the sensor has caught up.
- Is the sensor's own time constant comparable with the thing it is measuring? A
  probe in a thermowell can lag by minutes, and a controller tuned against a lag
  it does not know about will oscillate.
- What is the duty cycle? A part that is over its steady limit but only runs for
  a quarter of a time constant may be fine — and that argument is only valid if
  the Biot number says the body has one temperature.

## Sensor and control placement

- Where is the limit actually defined — junction, case, surface, air? The
  datasheet limit and the place you can physically measure are rarely the same
  point, and the difference is a resistance you must write down.
- Is the sensor measuring the hot spot or the average? The average is comforting
  and the hot spot is what fails.
- If the sensor falls off, opens, or shorts, what does the control do? "Full
  power" is a common and expensive default.

## A handheld's skin and its dissipation

A sealed handheld has one way to lose heat: its outside surface. Ask these before
the shell size is fixed. `thermal.skin_temperature` answers the average; these
questions find what it does not see.

- **Which state is the hottest?** It is usually charging while in use: the
  system at full load and the charger's loss at the same time. Name every state
  the device can stay in for minutes, and give each source the states it is in.
- **Is each source at its worst corner?** A linear stage loses
  `(vin_max - vout_min) x i_max`. USB VBUS goes to 5.5 V at the connector. A
  charger's output is lowest at its fast-charge entry voltage, and its current
  is highest at the top of its tolerance. A loss estimated at 5 V in and 3.7 V
  out is about half of the worst corner.
- **Where is the peak?** The gate gives the average over the uncovered shell.
  A 2 mm polymer wall spreads heat about 6 mm, so the skin over a charger or a
  processor is much hotter than the average. Measure the peak with a
  thermocouple on a print with the real parts or a heater, and record it with
  `claim physical`.
- **What is the shell's emissivity?** At handheld size, radiation is more than
  half the loss. On the baseline, the same shell in bare metal (eps 0.10) runs
  10.5 K hotter than in moulded plastic (eps 0.90). A glossy metallic paint is
  closer to bare metal than to paint.
- **How much of the shell is covered?** A hand, a strap or a desk covers part
  of it. The gate gives that part no credit. Is the grip you assumed the grip
  people use?
- **What is the room?** State 25 C for an IEC 62368-1 TS1 claim. A device in a
  pocket, a bag or under bedding is outside the still-air model: there the air
  is not still and not at room temperature.
- **Does the sun reach it?** Sun on the shell is a heat source: absorptance x
  irradiance x projected area. `solar.irradiance` can give the irradiance.
- **What else is at the skin temperature?** Inside a held device the parts sit
  at the skin temperature or above, not at room temperature. Check the cell,
  the adhesives and the polymer parts against their ratings at 35-50 C.
- **Is the charger's current one number?** Take `i_max_a` from the same place
  that sets the charger's programmed current. A second copy typed by hand
  drifts when the board changes.
