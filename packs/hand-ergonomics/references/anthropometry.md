# Anthropometry: the data under ergo.digit_reach

Tier 3. Read this when you need to know where a number came from, or whether the
population fits the users.

---

## The dataset: ANSUR 1988

The 1988 Anthropometric Survey of U.S. Army Personnel measured 132 dimensions on
each subject. Its working data base holds 2,208 women and 1,774 men. The public
files give each subject's values in mm, one row per subject:

| File | Subjects | sha256 |
|---|---|---|
| `ansurWomen.csv` | 2,208 | `74fb63a9efacd27000dbe3bb664f5dc261d0692a0c71b14a893deca041a12992` |
| `ansurMen.csv` | 1,774 | `bd7b4bc27db15e6131a44f17836228c131c73de9692794afc123aee2d2e9004e` |

Both are linked from https://www.openlab.psu.edu/ansur/ and served from
`https://tools.openlab.psu.edu/publicData/`. The report that defines each dimension
is Gordon CC et al., *1988 Anthropometric Survey of U.S. Army Personnel: Methods and
Summary Statistics*, NATICK/TR-89/044, September 1989 (sha256 `331508cd...4fbc1`). Its
cover says: approved for public release, distribution unlimited.

**Ages** (Table 1, p. 4): men 17 to 51, women 18 to 50. This is why the operating
context starts at 17.

The pack ships seven columns of each subject in `gates/data/ansur1988_hand.csv`.
`gates/data/derive_hand_table.py` rebuilds it byte for byte from the two files and
refuses a file whose sha256 differs. `gates/data/PROVENANCE.json` records all of it.

## The definitions

Printed page numbers of TR89-044 (the PDF page is the printed number plus 13).

| Column | Dimension | Page | How it is measured | Mean, women / men |
|---|---|---|---|---|
| `wrist_thumbtip_mm` | (130) Wrist-Thumbtip Length | 332 | stylion to the tip of the thumb; little-finger side of the hand on a surface, thumb straight and in line with the forearm, resting on the first knuckle of the curved index finger | 117.6 / 124.4 |
| `wrist_grip_mm` | (125) Wrist-Center of Grip Length | 322 | stylion to the centre of a 1-1/4 in (31.75 mm) dowel gripped in the hand; base of the dowel flush with the bottom of the fist, fist in line with the forearm | 66.3 / 69.7 |
| `wrist_index_mm` | (129) Wrist-Index Finger Length | 330 | stylion to the tip of the index finger; palm on a table, fingers together | 169.2 / 180.8 |
| `hand_length_mm` | (59) Hand Length | 190 | stylion to the tip of the middle finger | 180.5 / 193.8 |
| `hand_breadth_mm` | (57) Hand Breadth | 186 | metacarpale II to metacarpale V | 79.4 / 90.4 |
| `thumb_breadth_mm` | (105) Thumb Breadth | 282 | maximum breadth of the thumb perpendicular to its axis | 20.7 / 24.1 |
| `wrist_circ_mm` | (126) Wrist Circumference | 324 | over the stylion, perpendicular to the forearm | 151.2 / 174.2 |

The stylion is the tip of the radial styloid process: the bony point on the thumb
side of the wrist. Every reach length in the gate starts there.

The means of the shipped table agree with the report's within 0.1 mm.

## Why subjects, not percentiles

Reach beyond the grip is wrist-thumbtip minus wrist-centre of grip, for each person.
The two lengths correlate (Pearson r = 0.54 for women, 0.58 for men), but not
perfectly. So:

| Women | mm |
|---|---|
| P5 of wrist-thumbtip minus P5 of wrist-centre of grip | 48 |
| P5 of (wrist-thumbtip minus wrist-centre of grip), subject by subject | 42 |

The percentile pair is 6 mm generous, in the direction that hides the fault.
Percentiles do not add (Robinette and McConville 1981, SAE 810217). The gate counts
the subjects who reach every control; it never builds a limit from two percentiles.

Reach beyond the grip, per subject: women P5 42, P10 44, P90 59 mm (SD 5.76); men P5
46, P10 47.3, P90 62 mm (SD 5.58). The physical test picks its testers at or below the
women's P10 (44 mm) and at or above the men's P90 (62 mm).

Percentiles in this pack are type 7 (linear interpolation between order statistics,
numpy's default). They appear only in design ranges and tester thresholds.

## The cross-check: ANSUR II

ANSUR II (2012; 1,986 women and 4,082 men, aged 17 to 58) is the newer survey. Its
public files (`ANSUR_II_FEMALE_Public.csv`, sha256 `ed7e800a...1757a`;
`ANSUR_II_MALE_Public.csv`, `0547aea0...caac64`) have **no** wrist-thumbtip,
wrist-index finger or wrist-centre of grip length. (`forearmcenterofgriplength` is
elbow to grip.) So the reach rule cannot run on its subjects.

It agrees with 1988 where both measured the same thing:

| | 1988 women | ANSUR II women | 1988 men | ANSUR II men |
|---|---|---|---|---|
| Hand length P5 | 165 | 165 | 179 | 177 |
| Hand length P95 | 197 | 199 | 210 | 210 |
| Wrist circumference | P5 140 | P5 142.2 | P95 188.3 | P95 191 |

Within 2 mm on hand length at P5 and P95. The 1988 hands are not a different
population from the 2012 ones on the dimensions that can be compared.

## What the population is not

- Not civilians. Soldiers are screened for fitness; the extremes of the general
  population are thinner here.
- Not children, and not people over 51.
- Not a world population. Hand size differs between populations by more than the
  margin of a 95% claim. A market elsewhere sets a higher limit or brings its own
  per-subject data with the three wrist lengths.
- Worst sex, no mix. The claim says 95% of the women **and** 95% of the men. A pooled
  share at a stated mix is not offered.
