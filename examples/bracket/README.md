# Reference project — wall bracket

The smallest project that still exercises every rule in `METHOD.md`, with **zero
dependencies**. Standard library only, like the spine. You can run the whole
pipeline on a fresh machine with nothing installed, which is the point of having a
reference project at all.

```bash
cd examples/bracket
atompipe check
```

## What you should see

On a fresh clone, before you change anything:

```
[FAIL] bracket.deflection : 0.700 mm at 15 N (limit 0.5 mm)                   cached
6 gates: 0 executed, 6 cached — 5 ok, 1 FAIL — tier 0
BLOCKING — 2 critical claim(s) must not be spent against:
[FAIL ] C1 Tip sags no more than 0.5 mm at rated load — bracket.deflection : 0.700 mm at 15 N (limit 0.5 mm)
[gap  ] C7 First mode is clear of the pump that sits on the shelf — no gate covers it
```

Nothing ran. Every verdict came out of `.atompipe/verdicts/`, where it was recorded
against the exact bytes of the model, gates and selftest you just cloned; a cached
pass is not printed, a cached failure is, and `git status` stays empty afterwards.
The exit code is 1.

**The failure is deliberate.** The default thickness is 7 mm and the arm sags 0.70 mm
against a 0.5 mm limit. Open `model/bracket.py`, change `thickness` to `8.0`, and run
`atompipe check` again — `bracket.deflection` goes green at 0.47 mm (C7 still blocks:
no gate covers it yet). Before you run anything, `atompipe status` names each gate the
edit reached and what moved for it; `check` re-runs exactly those and serves the rest
from the cache.

That is the entire loop: one parameter, one gate, one verdict. Everything else in
atompipe is this loop with more expensive gates attached.

## What it demonstrates

**Rule 1 — one source of truth.** `model/bracket.py` holds every input. Section
properties, stresses, deflections and the print footprint are all computed by
`build()`. No gate recomputes geometry, and no record under `claims/` carries a
parameter's value: the model owns it.

**Rule 2 — derive, never duplicate.** `usable_bed = bed_xy - 2 * brim_mm`. The brim
allowance exists in one place, so it cannot drift.

**Rule 3 — provenance.** Every field in `Config` carries why it holds that value, and
`PARAMS` beside it says what was tried and lost, by how much: 4 mm of thickness sags
3.75 mm, 7.5x the limit; an 80 mm arm, 1.7 mm; a 5.0 mm hole is a line-to-line fit an
FDM hole will not hold. Run `atompipe why thickness`:

```
param thickness = 7.0 mm   (model/bracket.py Config.thickness)
...
REJECTED (1)
  4.0 mm — 3.75 mm deflection, 7.5x the limit   (model/bracket.py PARAMS)
```

**Rule 5 — falsification controls.** Six gates, six fixtures in
`selftest/bad_configs.py`, each changing exactly one physically meaningful thing in
the direction that gate cares about, starting from the known-good design in
`selftest/known_good.py` — never from whatever the model says today:

| Gate | Known-bad fixture |
|---|---|
| `bracket.deflection` | same bracket at 1/4 thickness (~64x worse) |
| `bracket.bending_stress` | same bracket at 20x load |
| `bracket.bearing` | one bolt in a thin plate, bearing area collapsed |
| `bracket.model_validity` | short deep arm where shear deflection stops being negligible |
| `bracket.bed_fit` | a 400 mm arm — nothing wrong but the footprint |
| `bracket.min_wall` | a 1.2 mm nozzle that cannot resolve the section |

```bash
atompipe gate selftest
```

Every gate must **fail** its fixture. One that passes is reported as broken, and a
gate whose control has not fired at the current version never counts as a pass.
`check` demonstrates each control at the current version too, and the fired controls
are committed beside the verdicts (`control-*.json`).

**A gate that polices another gate.** `bracket.model_validity` checks that
Euler-Bernoulli is applicable at all — below a slenderness of about 5, shear
deflection stops being negligible and the deflection gate quietly becomes optimistic.
Any pack shipping closed-form analysis wants one of these. Without it, the cheap gate
silently becomes the wrong gate as the design moves.

**Rule 9 — honest reporting.** `atompipe report` separates what was proven from what
was not. The bracket's outdoor-durability claim is `physical`: no gate will ever
settle whether it survives two winters on a wall, and the report says so rather than
finding something adjacent to turn green.

## Files

Where each fact lives. Everything marked *tracked* is a source, reviewed like code;
everything *ignored* is an output a command rebuilds.

```
model/bracket.py              tracked   the model: Config, PARAMS, build(). Runnable on its own.
gates/structural.py           tracked   six tier-0 gates, all pure arithmetic
selftest/known_good.py        tracked   the design every control starts from
selftest/bad_configs.py       tracked   one known-bad fixture per gate
claims/C1.json … C7.json      tracked   what must be true: one record per claim, the
                                        filename is its id. Edit these by hand.
.atompipe/project.json        tracked   name, revision, the model entry, installed packs
.atompipe/verdicts/<gate>/    tracked   the verdict cache: one entry per gate and one
                                        control entry per gate, keyed on the bytes
                                        each one read. Evidence, committed.
docs/readiness.md             tracked   generated by `atompipe report --write`; never
                                        hand-edited
.gitignore, .gitattributes,   tracked   marked blocks atompipe maintains: bytecode is
.atompipe/.gitignore                    ignored, line endings are pinned (digests are
                                        over bytes), outputs are ignored
.atompipe/ledger.json         ignored   the generated index of the records — the whole
                                        project in one read; edit the records, never it
.atompipe/cache/, obs/, out/  ignored   this checkout's memory, costs and gate scratch
```

There is no `params/` directory: a parameter record holds only what the model cannot
(where a number came from), and every value, rationale and rejected alternative here
lives in `model/bracket.py`.

## The committed cache

The twelve files under `.atompipe/verdicts/` are what make a fresh clone's first
`check` six cache hits. Each is keyed on the spine that wrote it (a canonical reading
of atompipe's verdict-path modules, blind to their comments and docstrings), on the
bytes of the gate code and selftest it ran, and on the values it read from the model.
Edit the spine's code, a gate, a fixture, or a value a gate reads, and the entries
that depended on it go stale: `check` runs those gates again and leaves new entries
for `git status` to show. When that is the atompipe checkout's own doing, the
committed cache must follow in the same change — `tests/test_bracket_cache.py` goes
red until it does, and names these commands. To regenerate, from this directory:

```bash
rm -rf .atompipe/verdicts .atompipe/cache .atompipe/obs .atompipe/out
atompipe check              # exits 1: bracket.deflection fails on purpose
atompipe report --write
atompipe gate selftest      # writes nothing new: the controls were just recorded
git add -A .
```

## Try breaking it

- Set `material="alu6061"` — watch the deflection claim pass by a wide margin and the
  mass triple.
- Set `arm_length=120` — deflection goes as L³, so it fails by ~8x.
- Set `thickness=2.0` — now `bracket.min_wall` and `bracket.deflection` both trip, and
  `bracket.model_validity` warns that the remaining numbers are not trustworthy.
- Delete a gate's `negative_control` — the registry refuses to load it at all.
