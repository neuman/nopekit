# A sealed handheld's skin temperature

Load this when a claim is about how hot the outside of a handheld, a wearable or
another small sealed device gets. It covers what `thermal.skin_temperature`
computes, how to write its records and its claim, the touch limits a claim
author picks from, and where each limit comes from.

---

## 1. What the gate computes

For each power state, the gate adds the sources in that state and solves

    P = A (h_c + h_r) (T_s - T_a)

for the average surface temperature `T_s`. The hottest state gives the value.

- `A` is `shell_area_m2 x (1 - shell_covered_fraction)`. The covered part (a
  hand, a strap, a desk) gets no credit. This errs hot. Had the covered 35% of
  the baseline shed as the open area does, the baseline would read 4.2 K
  cooler (34.10 C, not 38.26 C), and the same shell at 48 C would read 7.1 K
  cooler.
- `h_c` is Churchill-Chu for a vertical plate, at the shell's longest outside
  dimension (`shell_length_m`), with air properties at the film temperature.
  On this pack's own correlations, that gives 81-92% of the convection that the
  top and bottom faces of a level flat device get (McAdams hot-face-up plus
  hot-face-down at L = A/P, for 160 x 80, 140 x 60 and 40 x 40 mm faces at
  dT 5-25 K). In that range this errs hot. It has no Rayleigh band, so it
  refuses no small face.
- `h_r` is grey-body radiation to a large surround at the air temperature
  (`shell_emissivity`, F = 1). That is a room's walls. Outdoors, the sky is
  colder and the sun is a heat source: state the sun as a source.

Some biases err cold. These are the ones known:

- Outside the measured range, the vertical plate can give more convection than
  a level device's faces: 1.5% more for a 400 x 300 mm face at dT 15 K, 3% at
  23 K, 6% for 500 x 350 mm at 25 K, and 4% for a 20 x 20 mm face at 2 K.
- The model gives a face that points down (a tag under a shelf, its top face
  covered) 1.2-1.6x the convection that hot-face-down gives it. A 160 x 80 mm
  face that the model puts at 48 C is about 50.2 C.
- The uncovered surface radiates partly to the hand or the body that holds it.
  They are warmer than the room, so they take less heat than the model's
  surround. This is not measured.

`tests/test_thermal_handheld.py` holds every number in this section.

The value is the AVERAGE. Inside the measured range its biases err hot, so a
PASS there is the conservative verdict for an average claim. A FAIL says only
that the model cannot show the shell sheds its power under those biases: a
sound shell can fail. The covered-area credit alone is worth 4.2 K on the
baseline and 7.1 K at 48 C. Outside the range, or on a face that points down, a
PASS can be generous by the numbers above. The peak over a source is above the
average, so a PASS says nothing about the peak. The peak needs a measurement or
a solver (`references/limits.md`, section 9).

A balance that needs a film above the air table (600 K) has no value. If the
claim's limit is below that bound, the gate fails with no value. If the limit is
above it, the gate errors: the table cannot decide it.

## 2. The records

| Key | What | Notes |
|---|---|---|
| `shell_area_m2` | whole outside area of the closed shell | from CAD |
| `shell_length_m` | the longest outside dimension | the convective length |
| `shell_emissivity` | outside infrared emissivity, (0, 1] | no default: moulded or painted about 0.9, anodised about 0.8, bare metal 0.05-0.1 |
| `shell_covered_fraction` | area under a hand, a strap or a desk, [0, 1) | credited nothing |
| `shell_cooling` | `sealed`, `vented` or `fan` | only `sealed` is judged; the others skip |
| `shell_ambient_c` | the room air the claim is made in, degC | 25 for a TS1 claim (section 4) |
| `heat_sources` | `{name: {w or linear, states, source}}` | below |

Each heat source is one entry of an object, keyed by its name:

```json
"heat_sources": {
  "system at full load": {"w": 2.20, "states": ["use", "charge-in-use"],
                          "source": "power budget at full load"},
  "linear charger": {"linear": {"vin_max_v": 5.50, "vout_min_v": 3.00, "i_max_a": 0.45},
                     "states": ["charge", "charge-in-use"],
                     "source": "VBUS max per USB 2.0/3.1; fast-charge entry; current at top of tolerance"}
}
```

- State exactly one of `w` (watts) or `linear`.
- `states` are free labels. The gate judges every state a source names.
- `source` is required: a dissipation nobody can trace is a guess.
- State each source at its worst SUSTAINED case. A burst above it is a
  transient, which this gate does not judge.
- A switching stage is a `w` entry at its datasheet loss.
- Sun on the shell is a `w` entry: absorptance x irradiance x projected area.

It is an object and not a list on purpose. A mutation walk, the spine's mutation
pass and the trace push only the leaves of objects. In a list, every source's
numbers are one structure that nothing pushes.

A record stated wrongly is an error that names the field. A missing key is a
skip that names the key, before the gate reads the claim. So a project with no
handheld never reads errored.

## 3. A linear stage at its worst corner

    P = (vin_max_v - vout_min_v) x i_max_a

- `vin_max_v`: the highest input. USB 2.0 and 3.1 let VBUS be 4.75-5.5 V at
  the connector (TI TPS25821 datasheet, power supply recommendations). Use 5.5.
- `vout_min_v`: the lowest output while the stage carries its full current. For
  a linear charger, that is the cell voltage where fast charge starts (below it
  the charger precharges at a lower current). For an LDO, it is its output.
- `i_max_a`: the programmed current at the top of its tolerance.

TI SLUAAG9 (tips for the BQ2517x linear chargers) says: "Required power
dissipation in IC is greatest during fast charge with a low battery voltage and
a high input voltage."

What slipped through without the corner, as a generic story: a charger's loss
was estimated at 5 V in and 3.7 V out, mid-charge. That is 1.3 V of headroom.
At 5.5 V in and a 3.0 V fast-charge entry it is 2.5 V. The estimate was about
half the worst case, for a part in the closed volume beside the cell.

## 4. Touch limits, and where they come from

The limit lives in the claim, not in the gate. The claim author picks it. Write
the claim so:

```json
{"id": "TH1", "statement": "...", "kind": "measurable",
 "acceptance": {"quantity": "average skin temperature", "comparator": "<=",
                "limit": 48.0, "units": "degC"},
 "tags": ["average-skin-temperature"]}
```

The gate accepts only `<=` or `<`, and only `degC`. Any other side or units is
an error.

### IEC 62368-1 TS1 limits

IEC 62368-1 classes a touchable surface by its thermal energy source class.
TS1 needs no safeguard between it and any person. The limits depend on the
material and on how long the surface is touched.

| Contact | Metal | Glass, ceramic (vitreous) | Plastic, rubber | Confirmed by |
|---|---|---|---|---|
| worn on the body, > 8 h | 43-48 C | 43-48 C | 43-48 C | Electronics Cooling 2025 only |
| held or touched, 1 min - 8 h | 48 C | 48 C | 48 C | UL 2010 (metal, plastic); Electronics Cooling 2025 (all three) |
| 10 s - 1 min | 51 C | 56 C | 60 C | UL 2010 (metal, plastic); Electronics Cooling 2025 (all three) |
| 1 s - 10 s | 60 C | 71 C | 77 C | UL 2010 (metal, plastic); Electronics Cooling 2025 (all three) |
| < 1 s | 70 C | 80 C | 94 C | UL 2010 (metal, plastic); Electronics Cooling 2025 (all three) |

TS2 limits are TS1 + 10 K (UL 2010). IEC 62368-1 took ISO 13732-1:2006 (hot
surfaces, burn thresholds) as the basis of its limits (UL 2010).

**What each source is, and what is NOT confirmed.** These are secondary
sources. Nobody who wrote this pack read the normative text of IEC 62368-1 or
ISO 13732-1.

- UL 2010: Flore Chiang (UL Taiwan), "IEC 62368-1 Technical Brief: Touch
  Temperature Limits", 22 October 2010. Read in full. It gives the metal and
  plastic columns for four contact times, says they derive from "Table 42" of
  the edition it describes, gives TS2 = TS1 + 10 K, and names ISO 13732-1:2006
  as the basis. It has no worn row and no vitreous column.
- Electronics Cooling 2025: Azita Soleymani, "Thermal Design for Externally
  Worn Wearable Electronics", 9 July 2025, Table 2, citing IEC 62368-1 (2018).
  Read in full. It gives all five rows above and the vitreous column. Its worn
  row says "43-48 C" with no note that explains the range.
- **Not confirmed:**
  - The worn row. One secondary source gives it, as a range. Until the normative
    text is read, a worn claim must say which end it takes, and why.
  - A wood column. No source seen gives one. Do not map wood to plastic without a
    source.
  - The table number. It is 42 in the UL 2010 brief, and one maker's blog cites
    Table 38 of the 2nd edition. Other editions are not checked.
  - The 25 C reference ambient. A secondary FAQ (regulatorydecoded.com) says the
    48 C limit is referenced to 25 C ambient. A TÜV Rheinland FAQ is said to
    state it too, but only a search summary of it was seen. So state
    `shell_ambient_c` 25 for a TS1 claim, and know that the clause is not
    confirmed. A gate that rewrote the ambient to 25 C would hide the input the
    reader must see, so the gate uses the ambient as stated.
  - The ISO 13732-1:2006 burn thresholds themselves.

Read the purchased standards before a certification depends on these numbers.

### Why the claim is about the average

The 48 C row applies at the surface a person holds. The hottest point of that
surface is above the average. So a claim on `average skin temperature` is
necessary for TS1, and not enough. Write the peak as its own claim, tagged apart
(not `average-skin-temperature`), and settle it with `claim physical` or a
solver. A claim tagged `touch-temperature`, `skin-temperature` or `burn-hazard`
is not covered by this gate, on purpose: with those tags a peak claim would read
Checked on an average.

## 5. The baseline, worked

The pack's baseline is the installation's handheld commissioning terminal:
sealed fixture data, not a product.

- Shell: 160 x 80 x 28 mm moulded PC/ABS. Area 2(lw + lh + wh) = 0.03904 m2,
  length 0.160 m, emissivity 0.90, 35% covered by a one-hand grip, so
  0.02538 m2 sheds. Room air 25 C.
- States: use 2.2768 W -> 34.46 C; charge 1.4493 W -> 31.34 C; charging while
  in use 3.3493 W -> 38.26 C. The last decides, with h_c 4.17 and h_r 5.78
  W/m2K: radiation is 58% of the loss.
- The linear charger is 1.125 W at its worst corner (5.5 V in, 3.0 V out,
  0.45 A). At 5 V in and 3.7 V out it would be 0.585 W.
- 38.26 C against TH1's 48 C: PASS. Half the linear size (a quarter of the
  area) gives 65.84 C: the known-bad control.
- The same shell in bare metal (eps 0.10) gives 48.78 C, 10.5 K hotter.

## 6. What the gate does not judge

- The peak over a source (section 4, and `references/limits.md` section 9).
- A vented or fan-cooled shell: it skips. A device in a pocket, a bag or under
  bedding is outside the still-air model.
- Transients: a burst above the sustained budget, the warm-up time, a fast
  charge that starts hotter than the steady state.
- Abnormal and single-fault heating: TS2 limits under a fault, a shorted pass
  element, a cell in runaway.
- The cell's temperature at its rated charge ambient, and the charger's or the
  LDO's junction temperature. A linear charger folds back its current at its
  regulation temperature, so a hot junction costs charge time. A junction claim
  through `thermal.steady_state_temp` must be tagged `cooling` alone, or state a
  lumped body: `thermal.time_constant` also covers `component-temperature` and
  `steady-state-temperature`, and it skips without the `body_*` keys.
- The charger's temperature cutoffs. No bundled gate computes them yet.
  pcb-kicad's `pcb.charge_protection` checks only that the charger's THERM pin
  is wired to an NTC and that the part has a safety timer. pcb-kicad's
  `pcb.charge_temperature`, which will read the thermistor network off the
  board, is planned and not built. Until then, settle a cutoff claim with
  `claim physical` or a project gate. Do not give it a tag that
  `pcb.charge_protection` covers (`pcb`, `battery-safety`,
  `charge-protection`): that gate passes on the wiring alone.
- Cell ageing. I2R rises as a cell ages: state the end-of-life resistance.
  Nothing checks that you did.
