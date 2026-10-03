# beam-analytic

Closed-form structural analysis. Eight gates, all tier 0, all pure arithmetic,
**zero dependencies** — this pack runs on a fresh install with nothing installed
and returns in microseconds.

It is the "analytic first" answer. Before an agent proposes downloading a solver,
it should have run these, because a closed-form bound that says *utilisation
0.05* has settled the question and a closed-form bound that says *utilisation
1.4* has settled it the other way. FEA is for the band in between, and for the
geometry these formulae do not describe.

## 1. What this pack settles

| Question a person actually asks | Gate |
|---|---|
| How much will it sag? | `beam.deflection` |
| Is that sag visible / acceptable for the span? | `beam.deflection_ratio` |
| Will it break? | `beam.bending_stress` |
| Will it tear through near the support? | `beam.shear_stress` |
| Will that strut fold up under compression? | `beam.buckling` |
| Will the bolt wallow out its hole? | `beam.bearing` |
| **Do the six answers above mean anything for this shape?** | `beam.model_validity` |
| **Are the numbers in the units and signs this pack needs?** | `beam.input_sanity` |

Everything is for a **prismatic** member (constant section along its length), a
**linear-elastic** material, **static** load in a **principal plane**. Strength
and stiffness are taken as isotropic; the one place orthotropy is carried is the
E/G ratio in `beam.model_validity`, because assuming it on timber under-reports
the omitted shear deflection by four to six times.

## 2. What it cannot settle

This section prevents more damage than the one above it. This pack **cannot**:

- **See geometry.** It reads numbers from the projection. It does not know there
  is a hole in the web, a step in the section, a fillet, a notch, a keyway or a
  layer boundary. Every stress it reports is **nominal**. A 3 mm hole in a 10 mm
  beam is a stress concentration of roughly 2.2x that appears nowhere in these
  numbers.
- **Do fatigue.** Every gate is static. A part at utilisation 0.4 under a load
  that cycles ten million times is not covered by anything here — for most metals
  the endurance limit is around 0.4-0.5 of *ultimate*, and for aluminium there
  is no endurance limit at all.
- **Do combined or off-axis loading.** Bending about one axis. No biaxial
  bending, no torsion, no combined bending-plus-axial interaction, no
  lateral-torsional buckling of a deep thin beam, no local buckling of a thin
  tube wall.
- **Do large deflections.** Small-deflection theory. Once the tip has moved more
  than about 10% of the span the geometry has changed and the answers drift
  optimistic.
- **Do non-prismatic, tapered, curved or built-up members**, or shear lag in a
  wide flange, or composite/laminate layups.
- **Judge a joint.** `beam.bearing` checks one failure mode of one hole. It does
  not check shear-out, tear-out, net-section tension, bolt shear, thread
  stripping, preload, prying, or whether the load actually shares between the
  bolts the way the arithmetic assumes.
- **Know your material.** It ships an order-of-magnitude table so it is usable
  with a name alone, and **every verdict that reads from that table says so on
  its own line**. A table in a validator is not a mill certificate and not a
  coupon test.
- **Tell you the load.** Rated load, impact factor and duty cycle are inputs. The
  most common way an analysis passes and a part breaks is not a wrong formula,
  it is a load that was never the real load. `beam.input_sanity` checks a load's
  SIGN and not its magnitude: newtons and kilonewtons are indistinguishable to
  any check this pack could write.
- **Find a shear failure in a solid metal member.** `beam.shear_stress` is
  arithmetically correct and, for a solid metal section, it cannot be the binding
  constraint anywhere inside the L/h >= 5 regime this pack declares valid — the
  crossover is at L/h 0.43 for a cantilever and 2.60 at the most generous case.
  It earns its place on wood (shear strength ~1/10 of bending), on thin-webbed
  explicit sections, and on bond lines and print layers. `references/formulae.md`
  section 3 tabulates the crossover per case. Do not read a green shear verdict on
  an aluminium bracket as evidence of anything; it could not have been red.
- **Cover initial bow or eccentricity in a column.** `beam.buckling` assumes a
  perfectly straight, concentrically loaded member. A real column is neither, and
  the secant formula or a code column curve typically costs 10-25% of the capacity
  this gate reports. `references/formulae.md` section 4 covers it; nothing here
  gates it.

Anything in this list that matters to your design is a claim that stays
ASSUMED or UNCLAIMED in the ledger until something else settles it. Say so in
the report rather than letting the green ticks imply coverage they do not have.

## 3. Gates

All tier 0. All negative controls live in `selftest/bad_beams.py`, each of which
takes the documented known-good `BASELINE` beam and moves **one physically
meaningful thing** — which is one key for seven of the eight. `shear_governed`
needs three (material, support case, load), because for a solid metal section
there is no single number that makes shear govern; that is a fact about the
physics, not a liberty taken with the fixture, and its docstring says so with the
arithmetic.

Every fixture **derives** its bad value from the threshold it has to beat — 15%
past the limit the baseline itself carries — rather than hardcoding a number that
happens to exceed the limit as shipped. A literal would be a second copy of the
threshold with a different date on it, and the selftest would still read green
after it drifted. 15% is deliberately small: these gates are deterministic closed
form, so a control 60x past the limit would still fire if the threshold were wrong
by a factor of ten, and one 15% past fires only if the limit is where the pack says
it is.

| Gate | Measures | Limit comes from | Known-bad fixture |
|---|---|---|---|
| `beam.deflection` | delta, mm | `deflection_limit_mm` — a product decision; **skips if absent** | `shallow_section`: depth 10 -> 8.17 mm, delta 15% past the limit |
| `beam.deflection_ratio` | L/delta | `deflection_ratio_limit`, default 180 (disclosed) | `long_span`: span 60 -> 66.4 mm, L/157 vs L/180 — **and no other gate fires** |
| `beam.bending_stress` | utilisation | yield / safety factor | `overloaded`: 300 -> 529 N, stress is linear in load |
| `beam.shear_stress` | utilisation | shear allowable / SF | `shear_governed`: plywood on simple supports under a UDL at L/h 6, load derived — **bending still passes** |
| `beam.buckling` | utilisation | Euler or Johnson P_cr, / SF | `slender_strut`: column 60 -> 471 mm, P_cr goes as 1/L^2 |
| `beam.bearing` | utilisation | yield / SF on d x t | `thin_flange`: bearing thickness 6 -> 0.47 mm |
| `beam.model_validity` | utilisation | L/h >= 5 **and** omitted shear deflection <= 10% | `stubby`: span 60 -> 43.5 mm, L/h 4.35 vs a floor of 5 |
| `beam.input_sanity` | fault count | 0 faults: sign convention and unit plausibility bands | `negated_load`: every load key flipped — the ordinary downward-negative convention |

Two fixtures fire more than one gate and say so in their own docstrings, because
the baseline cannot be moved past one limit without passing another:
`shallow_section` and `overloaded` also trip the deflection-ratio gate, since the
baseline's 0.5 mm limit on a 60 mm span is already L/120, tighter than L/180.
`shear_governed` also trips `beam.model_validity`, which is not contamination but
a theorem — see below. `selftest/bad_beams.py` also ships `si_units`, a runnable
(not declared) fixture that restates the whole baseline in metres and pascals.

`beam.model_validity` is the most valuable gate here and the reason the pack is
worth shipping rather than inlining. **Five** of the others are Euler-Bernoulli
(`deflection`, `deflection_ratio`, `bending_stress`, `shear_stress`, `buckling`),
which ignores shear deflection; that is excellent for a slender member and
progressively wrong as the member gets stubby, the support case gets stiff, or the
material's shear modulus falls away from the isotropic value — and **the error is
silent and in the optimistic direction**. `beam.bearing` is the one gate it does
not guard: `P/(n*d*t)` is not beam theory and does not care how slender the member
is.

It applies two criteria and reports the worse as a utilisation:

1. **L/h >= 5.** For a rectangular metal cantilever that corresponds to an
   omission of about 3%. It is the generous end of the range — many texts want
   L/h >= 10 — and is set there so the gate refuses only where the omission is
   unambiguous.
2. **The omission itself <= 10%**, computed as
   `(k_s/k_d) * alpha * (E/G) * I/(A*L^2)`.

The second criterion exists because the first is not sufficient and shipping only
the first was a real defect. `k_s/k_d` runs from 3 (cantilever) to 48
(fixed-fixed) and `E/G` from 2.6 (any metal) to 12-16 (wood), so a birch plywood
beam on simple supports at a perfectly respectable L/h of 6 clears the slenderness
test and omits about **32%** of its deflection. A gate that printed that number and
passed would be a logger.

It follows — worked out in `references/formulae.md` section 3 — that for every
material and case in this pack's table, the region where shear STRESS governs lies
entirely inside the region where shear DEFLECTION exceeds 10%. Wherever
`beam.shear_stress` binds, the deflection numbers are already untrustworthy.

Note what it does *not* need: no modulus, no yield — only the RATIO E/G, which
cancels E. Where the material is unknown or is not a known metal and no
`e_over_g` is given, it assumes isotropy, **says so on its verdict line**, and its
number is then a stated lower bound. Same for `alpha` on an explicit section,
where 1.2 (the rectangle's) is assumed and a thin web's true factor is 3-6x that.
A validity check has to keep working on the half-specified models where it is
needed most, and the price of that is disclosing every assumption it made to do so.

`beam.input_sanity` is the eighth gate and the only one that FAILS rather than
skips: its inputs are wrong, not missing. It catches a load carrying a sign (the
ordinary downward-negative convention used to produce a full sweep of PASSes
reading `util -0.65` and `L/inf`) and a modulus, stress or span whose magnitude
says it is in the wrong unit. The other gates SKIP on a signed load, resolving
their claims BLOCKED; this one puts a red line in the report so six quiet blanks
are not the only signal.

### Prerequisites

`beam.model_validity` is the prerequisite (`needs`) of `beam.deflection`,
`beam.deflection_ratio`, `beam.bending_stress` and `beam.buckling`: each of those numbers
is Euler-Bernoulli's, and every way the guard fails — L/h under its floor, or the shear
share the theory omits past its limit — is a beam where they do not apply.
`beam.input_sanity` is the prerequisite of `beam.model_validity`, `beam.shear_stress` and
`beam.bearing`: a load with the wrong sign or a length in metres makes their arithmetic
meaningless. Below a failed guard the dependents are not run, and a claim bound only to
one reads Skipped, `prerequisite failed: beam.model_validity` — a claim tagged only
`deflection` used to read Checked on a beam whose guard reported 32% of the deflection
omitted. The guard keeps its broad tags too, so a claim tagged `stiffness` still reads
Failing through it.

No edge from `beam.shear_stress` to the slenderness guard: its own control,
`shear_governed` (L/h 6.0), fails the guard, which would pre-empt the control; and
transverse shear does not rest on the no-shear assumption. None from `beam.bearing`:
bearing stress is F/(d·t·n), whatever the beam model.

## 3b. Views

| View | Kind | Payload | Addressed by |
|---|---|---|---|
| `deflection_curve` | `chart` | deflection swept across the dimension that drives it, with `deflection_limit_mm` as the limit line and the current design marked | series key `deflection`, or an x value |

A verdict answers at one point: *0.31 mm, under the 0.5 mm limit*. What it cannot
say in one line is whether that is a design in the flat part of the curve or one
perched where the next half-millimetre of section costs it everything — and *how
far inside a limit* is the question every review asks. The chart is that answer.

- **The swept dimension is derived**, and it is the one the design can actually
  change: the section depth for a rectangle, the diameter for a round bar or tube,
  and the span for a section this pack has no formula for (an explicit `I_mm4`).
  `deflection_sweep_param` overrides it; a name the formula does not read is
  refused rather than swept, because overriding a key nothing looks at draws a flat
  line and calls it a sensitivity study.
- **The bracket is ×0.5 to ×2** of the current value, widened once to ×0.25–×4 if
  the limit is not crossed inside it. A chart with no crossing on it cannot answer
  the question it exists for.
- **The limit line** is `deflection_limit_mm`, falling back to the serviceability
  ratio `L/N` that `beam.deflection_ratio` uses — pack default included and
  disclosed in the label, exactly as the gate discloses it.
- **The arithmetic is `_beam_analytic_lib`**, the gates' own. A chart drawn from a
  second copy of `delta = k·P·L³/(E·I)` can disagree with the verdict beside it,
  and a reader has no way to tell which is the project's status.
- **`meta.agreement` checks that it does not.** The curve's value at the design
  point is compared against the ledger's recorded `beam.deflection` measurement;
  they came from one formula and one projection, so a disagreement means the site
  was built from a projection the sweep never saw (method rule 6).

No gate in this pack emits locators. Every one of them measures a scalar over a
whole member — there is no part to blame and no point to pin, and a marker placed
at "the root" would be a position nothing here computed.

## 4. Units and frames

**mm, N, MPa (N/mm^2), mm^4, degrees.** No exceptions, no conversion factors
anywhere in the pack: `P*L^3/(E*I)` comes out in millimetres directly. A model
that feeds metres into these keys is wrong by 10^3 to 10^12 and every answer will
still look plausible, which is why it is stated here twice.

- `height_mm` is the section depth **in the bending direction**. Swapping it with
  `width_mm` on a rectangle changes I by (b/h)^2. The verdicts print `b x h` for
  exactly this reason — read it.
- `load_n` is the **total** applied load in newtons. For the `*_udl` cases that
  means the whole distributed load W = w*L, not the intensity w.
- `span_mm` is the **free span**: support face to support face, or the root of a
  cantilever to the load. Measure it from the weld, not from the wish.
- `I_min` is used for buckling, not the bending I. A rectangle bent the strong
  way buckles the other way. The pack derives `I_min` for `rect`, `circle` and
  `tube`; on an explicit `I_mm4` section it **refuses** rather than substituting
  the bending I, so `beam.buckling` skips for want of `i_min_mm4`. State
  `i_min_mm4 = I_mm4` if the section really is symmetric.
- **Every load is a positive magnitude.** `load_n` is positive in the direction it
  bends the member, `axial_load_n` is positive in COMPRESSION, `bearing_load_n` is
  a magnitude. A negative value is REFUSED, not absolute-valued: the pack cannot
  tell a downward-negative convention from a member in tension, and on
  `axial_load_n` the second means buckling does not apply at all.

### Parameter vocabulary

Physical inputs (no default — the gate SKIPS and names the key):
`span_mm`, `load_n`, `section` + (`width_mm`/`height_mm` | `dia_mm` | `dia_mm`+`wall_mm`)
or `I_mm4`+`area_mm2`+`c_mm`, `modulus_mpa` | `material`, `yield_mpa` | `material`,
`deflection_limit_mm`, `axial_load_n`, `column_k` | `column_end_condition`,
`bolt_dia_mm`, `plate_thickness_mm`.

Policy inputs (documented default, **disclosed in the verdict line**):
`safety_factor` (2.0), `deflection_ratio_limit` (180), `beam_case`
(`cantilever_end`, the most demanding), `n_bolts` (1), `min_slenderness` (5.0),
`max_shear_deflection_fraction` (0.10), `poisson` (0.33 or the material row).

Optional inputs that stop a gate ASSUMING: `e_over_g` or `shear_modulus_mpa`
(otherwise isotropy is assumed and disclosed), `alpha_shear` or `shear_area_mm2`
on an explicit section (otherwise 1.2 is assumed and disclosed), `i_min_mm4` on an
explicit section (otherwise `beam.buckling` skips).

`beam_case` accepts: `cantilever_end`, `cantilever_udl`,
`simply_supported_centre`, `simply_supported_udl`, `fixed_fixed_centre`,
`fixed_fixed_udl`, `propped_cantilever_udl`, plus common aliases.

### Claim-tag vocabulary

A gate's `claims` list is every vocabulary its result is relevant *evidence* for;
a **claim** must carry the NARROWEST tag that describes what it actually asserts.
Tagging "root stress stays under half of yield" as `structural` gets it covered by
every structural gate in every installed pack, so a deflection failure makes a
stress claim read FAIL and the reader goes hunting in the wrong place. The twelve
tags this pack binds, narrowest first:

| tag | a claim carrying it asserts | gates that bear on it |
|---|---|---|
| `deflection` | a sag, in mm or as a span fraction | `beam.deflection`, `beam.deflection_ratio` |
| `serviceability` | sag is acceptable to look at or live with | `beam.deflection_ratio` |
| `stiffness` | the member is stiff enough | `beam.deflection`, `beam.deflection_ratio`, `beam.model_validity`, `beam.input_sanity` |
| `stress` | a normal stress or its utilisation | `beam.bending_stress` |
| `shear` | a transverse shear stress or its utilisation | `beam.shear_stress` |
| `strength` | the member does not break under load | `beam.bending_stress`, `beam.shear_stress`, `beam.model_validity`, `beam.input_sanity` |
| `buckling` | a critical load or column stability margin | `beam.buckling` |
| `stability` | the member does not go unstable | `beam.buckling`, `beam.input_sanity` |
| `joint` | a connection carries its load | `beam.bearing` |
| `fastener` | a bolt or its hole carries its load | `beam.bearing`, `beam.input_sanity` |
| `model-validity` | the analysis method applies to this problem | `beam.model_validity`, `beam.input_sanity` |
| `structural` | the member is structurally adequate, broadly | every gate in the pack |

`model-validity` and `input_sanity` bind broadly **and** narrowly on purpose: when
one of them trips, every other number in the domain really is untrustworthy, so
dragging the whole domain down is the correct behaviour. No other gate should.

## 5. The physics in one paragraph

A loaded beam curves; the far side of the section stretches and the near side
squashes, and the stress in each fibre is proportional to its distance from the
neutral axis. That gives `sigma = M*c/I` and `delta = k*P*L^3/(E*I)`, with `k`
set entirely by how the ends are held. Two consequences do all the work: stiffness
goes as the **cube** of depth, so 20% more depth is 70% more stiffness and moving
material away from the neutral axis is always the cheapest thing you can do; and
deflection goes as the **cube** of span, so doubling a span is eight times the
sag while only doubling the stress. Shear is different in kind — it does not care
about span at all — so as a member gets shorter the moment shrinks and the shear
does not, until shear governs. A compressive member has a third failure mode with
no relation to strength: it goes unstable at `pi^2*E*I/(K*L)^2`, which depends on
stiffness and length and end fixity and **not at all** on how strong the material
is. If a result here surprises you, check that ratio-of-cubes intuition first;
it is right far more often than the model is.

## 6. Common failure modes in this domain

- **Passing on a coupon number.** The single most common way an FDM part passes
  analysis and snaps in service. Published polymer datasheet values are injection
  moulded or printed in-plane and overstate a printed part's layer-normal
  strength by roughly 30-50%. See `references/materials.md`.
- **A "fixed" end that is not.** A bolt in a slot is a pin. Taking K = 0.5 for a
  joint that behaves as K = 1.0 overstates the buckling load by four; treating a
  pinned bracket as built in understates a cantilever's deflection by four.
- **Measuring the span from the drawing.** The free span is where the part is
  actually restrained, which is frequently further out than intended.
- **A stubby member analysed as a beam.** `beam.model_validity` FAILs — and the
  right reading of that verdict is "these numbers are not trustworthy", not "the
  part is weak". It is usually stronger than reported, and the honest answer is
  that this pack does not know by how much.
- **Depth and width transposed.** Verdicts print the section; look at it.
- **A thicker part with an unchanged joint.** Thickening fixes deflection and
  bending and does nothing whatever for the bolt, so `beam.bearing` becomes the
  binding constraint and nobody notices because the other four went green.
- **A load carrying a sign.** Downward-negative is an ordinary convention and it
  used to produce seven PASSes reading `util -0.65`, `tau -3.75 MPa` and `L/inf`.
  Now the analysis gates SKIP (claims resolve BLOCKED) and `beam.input_sanity`
  FAILs. If your model really means tension, this pack does not settle it — a
  tension member needs a net-section check nothing here provides.
- **Metres and pascals.** Utilisation, L/delta and L/h are all scale-invariant, so
  an SI projection reads completely normal to anything scanning verdict status.
  `beam.input_sanity` bands the two quantities that cannot be scale-free (modulus
  and stress) plus their ratio, which is the only thing that catches GIGApascals
  beside megapascal stresses.

## 7. Where to look next

- `selftest/baseline.json` — the pack's known-good beam, every key a gate reads,
  each one annotated with what it is and its unit. Read it first if you are
  writing a model for this pack; it is also the projection CI runs every gate
  against, and the beam each fixture in `selftest/bad_beams.py` moves one number
  from.
- `references/formulae.md` — every coefficient, the assumption behind it, and
  where each one breaks down.
- `references/materials.md` — the material table, the printed-polymer derating,
  the `e_over_g` column, and why a coupon number is the wrong number.
- `references/when_to_use_fea.md` — the decision rule for stopping with closed
  form and the shape of what to do next.
- `lenses.md` — the seven adversarial review dimensions to run **before**
  anything is built, including the units-and-signs pass nobody runs.
- `sourcing.md` — stock sizes, certificates and the constraints that are not
  physics.
