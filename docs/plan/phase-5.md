<!-- Tier 3 of docs/PLAN.md: read docs/PLAN.md first (the map, the D-rows, the R-rules and G-criteria this file cites). Moved verbatim from PLAN.md §4.5; S-nn rows live in docs/plan/slipped.md. -->

# 4.5 Phase 5: model auto-mutation, derived independence, `bench/`

**Why last.** Mutation needs the cache (P1: `mutation-*` entries), paired admission (P2:
a survived mutant makes a gate inadmissible *for a claim*), `vcs.snapshot` (P3: the temp
worktree, D-23), and `modelio.influence` (P3: driver discovery).

#### Target transcript

```text
$ atompipe gate selftest --mutate
[ok  ] bracket.deflection#mutation : flips when pushed past C1's limit — arm_length 60 -> 56.2 gives 0.575 mm (fails), 51.2 gives 0.435 mm (passes)
[--  ] bracket.bending_stress#mutation : inconclusive — thickness x0.5 gives util 0.98, short of C2's limit 1.0 (searched <n> inputs; --range widens)
[--  ] bracket.bearing#mutation : inconclusive — no one-number change within x0.5–x2 reaches C3's limit (searched <n> inputs)
[LOGGER] demo.deflection#mutation : C1's limit was crossed (0.575 mm > 0.5) and the check stayed green — it does not refuse at C1's limit
$ atompipe report | sed -n '/deflection/p'
| C1 | … | ends in a closed-form calculation · flips when pushed past its limit (<k> of <n> inputs) · independence (formula class) <lo>–<hi>, under operators mutate v1 |
$ PYTHONPATH=src python -m bench --quick
E1 admission ladder: always_pass admitted 0/<n> at A3 … all real gates admitted at A3
E3 dependence: bracket.deflection formula-class upper bound <hi> (< 0.5, preregistered); beam-analytic own-table arm lower bound <lo> (> 0.5)
E4 staleness: recall 1.00 on <n> subjects; over-invalidation <k> vs <g> for the global hash
```

What changed for the human: a check that stays green past a claim's limit is named — a
fact about what it refuses, not a diagnosis of what it measures; independence appears as an interval, per class, with its operator set,
never as a scalar and never as the word *independent* on a proposer-written check; the
paper's promised experiment protocols exist and run.

**Checkpoint 5.0, `C:`.** A tree-hash harness: sha256 of every tracked file plus `git
status --porcelain`, taken before and after any mutation or bench run. Bench asserts it
unchanged; mutation asserts the diff is exactly its own new `mutation-*` paths
(invariant 15).

#### Checkpoint 5.1: `src/atompipe/mutate.py`, exposed as `gate selftest --mutate`

No new command. For each MEASURABLE claim C with a limit, and each covering gate G:

- **Scoring** is decided from the graph, never from `settles`, which the proposer writes
  (P2.4 edits it by hand): G is exempt ("guard, not scored") only if it is a
  `needs`-prerequisite of another scored covering gate of C; every other covering gate is
  scored. A claim with no scored covering gate reads "no check flips at C's limit" and is
  treated as a logger gap: `export` refuses it (M18.4). *Rejected:* exemption by a
  mismatched `settles`, which let a gate that stays green past the limit opt out of being
  called a logger.
- **Drivers** from `modelio.influence` (P3) plus config fields in G's recorded reads;
  mutants are built with `modelio.load_model(root, entry, overrides=…)`, which is
  `dataclasses.replace`-based, never a source edit, and refuses unknown fields or wrong
  types. Ints step by ±1; categorical fields are reported "not mutated".
- **Mutants.** Two bracketing mutants at the **claim's** limit — inside = limit / 1.15 for
  `<=`/`<`, ×1.15 for `>=`/`>`; outside the reverse; BETWEEN gets four; EQ, NE or a zero
  limit is inconclusive. *Why 1.15:* the margin the packs' own fixtures already use
  (`packs/beam-analytic/selftest/bad_beams.py:48-65`). *Rejected:* 1.02, inside verdict
  rounding and nonlinear-solve noise (false results in the prototype); absolute offsets,
  unit-dependent.
- **Solving.** Log-space bisection over `[x0/10, 10·x0]` (Q5.2 narrows the default search
  to ×0.5–×2), strongest driver first, reading **unrounded** projection values (the rounded
  `measured` produced spurious elasticities and a false "survived" in the first prototype;
  S-18); falling back to a projection-level mutant.
- **Outcomes.** The outside mutant must **FAIL** — not error, not skip — to count as
  killed; if G stays ok, the mutant **SURVIVED** and G is a logger for C; an error is
  "crashed on mutant", not a kill; **inconclusive is never survived**; the inside mutant
  must pass, else "over-refuses". A currently-red gate gets the reverse push first.
- **Isolation.** One fresh subprocess per `vcs.snapshot` temp worktree with the
  working-tree delta overlaid (D-23), launched as in P3.1 so the snapshot cannot supply
  its own spine; `out_dir` inside the snapshot; nothing is written into a pack directory
  (the temp `out_dir` of P1.1), and pack fixtures keep reading their own `baseline.json`,
  so SEALED holds.
- **Enforcement.** A survived outside mutant makes G **inadmissible for C** (R-4: measured
  first on the bracket and the four stdlib packs). Results are
  `.atompipe/verdicts/<gate>/mutation-<key16>-<out8>.json`, keyed by G's code digest, C's
  acceptance, the driver set and the spine digest — not the model source, so an
  auto-accepted model edit does not erase a result. A LOGGER result stays inadmissible
  until a re-run kills (asymmetric, like R-3); `export` and `check --force` re-run the
  mutation results a critical claim relies on, or refuse them as unreproduced (R-9). **`check` never runs mutation** (it spawns
  worktrees and subprocesses; rule 10 keeps the inner loop fast): it reads stored results,
  and a gate with none is reported "not mutation-tested", never refused. `trade` reads the
  same results: a LOGGER pair leaves its candidate unevaluated for that claim (P3.1). The
  bracket commits its mutation entries, regenerated with its cache and control entries in each
  phase commit, so G5 holds and a fresh clone's report shows them.

**Mutation entry** (tracked, written once like a cache entry; `out8` digests `result` and
the two mutant outcomes):

```jsonc
// .atompipe/verdicts/bracket.deflection/mutation-<key16>-<out8>.json
{"schema": 1, "kind": "mutation", "gate": "bracket.deflection", "claim": "C1",
 "acceptance": {"comparator": "<=", "limit": 0.5, "units": "mm"},
 "operator_set": "mutate v1 <sha12>", "drivers": ["arm_length", "thickness"],
 "range": [0.5, 2.0], "searched": 14,
 "inside":  {"driver": "arm_length", "value": 51.2, "quantity": 0.435, "outcome": "pass"},
 "outside": {"driver": "arm_length", "value": 56.2, "quantity": 0.575, "outcome": "fail"},
 "result": "killed", "reason": "", "code": "<64 hex>", "spine": "<64 hex>",
 "digest": "<sha256 over every other field>"}
```

`result` is a strict StrEnum: **killed** (the outside mutant FAILs); **logger** (the
quantity crossed C's limit, the gate's own measurement crossed with it, and it stayed ok);
**insensitive** (the quantity crossed C's limit in the projection while the gate's
measurement did not move at all — the frozen-input case); **inconclusive**, with `reason`
∈ {`no-boundary` within `range` (Q5.2), `comparator` (EQ, NE), `zero-limit`,
`categorical-only`}; **crashed** (an error on a mutant, never a kill); **over-refuses**
(the inside mutant fails); **exempt** (a guard, not scored). Only `logger` and
`insensitive` make G inadmissible for C and print `[LOGGER]`; every other result is
reported and refuses nothing (invariant 15). *Rejected:* `insensitive` as advisory — a
scored gate that reads nothing the claim's quantity depends on would be the cheapest way
never to be called a logger.

`V: MutationIsSealed`: the tree diff before and after is exactly the new
`.atompipe/verdicts/*/mutation-*.json` paths, with every verdict and control entry,
`model/` and every pack directory byte-identical; a mutation run patched to write a
verdict entry, or to edit `model/`, turns it red; `ControlsAreSealed`
still passes after a mutation run; a logger gate (computes the measurement, returns
`passed=True`) is LOGGER naming the field, and every mutation line matches a fact-only
template in which `test_shapes` refuses "measur" and "valid"; a gate with its limit hard-coded at 5.0 is LOGGER
against C1 even though its fixture fires; a frozen-input gate is INSENSITIVE; a gate that
crashes on the mutant is not a kill; `model_validity` on C1 and C2 is exempt, as a
prerequisite of the scored `deflection` and `bending_stress`, never LOGGER; a covering
gate with a mismatched `settles` that is nobody's prerequisite and stays green is LOGGER;
a model edit leaves a LOGGER result standing until a re-run kills; a two-tree run scores
each tree against its own model.

#### Checkpoint 5.2: derived independence (D-32)

```json
{"gate": "bracket.deflection", "claim": "C1", "protocol": "project",
 "origin": "agent-session", "reads": "derived", "operator_set": "mutate v1 <sha12>",
 "classes": {"formula": [0, 17], "table": [0, 4], "boundary": [2, 2]},
 "bracket": {"formula": [0.0, 0.18], "table": null, "boundary": [0.34, 1.0]}}
```

- The record is keyed by **(gate, claim, protocol)** — §3 makes I a property of a
  source/protocol pair, P5.1 scores per (gate, claim), and E3's two adapter arms give one
  gate opposite brackets.
- `origin`: `pack:<name>@<ver> (<origin_of>)` only for a **bundled** pack whose content
  digest matches the shipped release (`packs.origin_of`, `packs.py:237-260`); a user,
  `ATOMPIPE_PACK_PATH` or path pack, or a copy shadowing a bundled name, is
  `agent-session` unless its digest matches, because the agent session can write those
  places (D-32); `solver:<tool>` for the wrapped half of a `requires_tools` gate;
  `agent-session` for **everything inside the project**, including project-local packs —
  nothing the proposer writes can raise it; `human:<who>` only via a signed terminal.
  `PackManifest.origin` prose is excluded (S-85). Measurement and human terminals carry
  **no bracket**: nothing here measures them, and physical is not I = 1 by being physical.
- `reads` classifies the recorded leaf paths against `modelio.project`'s config/derived
  split. Intervals are Wilson 95%, or null — never NaN. The report prints "under
  F=<operators>" and never a scalar or threshold.
- The openmodelica mirror's default label becomes "(mirror origin not stated)"
  (`modelica.py:956`; S-86).

`V:` a `claims/*.json` containing `"independence": 1.0` is refused, naming the file;
`gate(…, independence=0.9)` raises TypeError; the formula-class upper bound for
`bracket.deflection` is < 0.5; a beam adapter using the pack's own material table gets a
formula-class lower bound > 0.5, and the same adapter fed the model's table gets a
table-class upper bound < 0.5 — origin buys nothing against shared inputs;
`json.dumps(allow_nan=False)` never raises; the mirror verdict with no source stated does
not contain the word "independent"; a beam-analytic copy in `~/.atompipe/packs` or on
`ATOMPIPE_PACK_PATH` derives `agent-session`; no measurement or human terminal renders a
bracket or the word "independent".

#### Checkpoint 5.3: `bench/`

- A stdlib package at the repo root, run as `PYTHONPATH=src python3 -m bench
  [e1|…|e8|all] [--quick] [--json] [--keep]`: `__main__.py` (exit 1 when a preregistered
  expectation fails; `e2`, `e5`–`e8` print SKIPPED naming the missing input, and a skip is
  never a pass); `PROTOCOL.md` (**all eight** experiments, preregistered before any data
  is analysed: the paper's question verbatim, the estimand, population, fields recorded,
  operator set and version, admission levels, the analysis, the falsifier, expectations,
  *what this does not show*, and for E2 and E5–E8 why it is not runnable yet — otherwise
  their data is collected with no estimand and any later analysis is post hoc; the case
  study is "the case study (paper §14)"); `_sandbox.py`;
  `_record.py` (the envelope: commit, dirty flag, spine version, python, tool fingerprint,
  pack origins, operator hash, edge `when`, `allow_nan=False`, undefined ratios as null with
  a reason); `e1_vacuity.py`; `e3_dependence.py`; `e4_staleness.py`;
  `adapters/bracket_as_beam.py`; `corpus/e1/README.md`.
- The sandbox copies packs and the bracket into a temp dir, runs under the shared test
  environment (temp `HOME` with `PYTHONUSERBASE` pinned, `ATOMPIPE_PACK_PATH` pointed at the copies; P1.0) so a user's
  `~/.atompipe/packs` cannot shadow the bundled ones (S-87), and asserts the repo's tree
  hash at exit.
- **Nothing under `src/atompipe` or `site/` reads bench output**; numbers are never
  committed (D-33). Model operators are imported from `mutate.py` (one definition, rule 2);
  E1's wrapper and AST operators live in bench only.
- CI extends the stdlib AST walk to `bench/` (allowed: stdlib, atompipe, bench), forbids
  `urllib`/`http`/`socket` there, and runs `--quick`. Quick mode (bracket plus the four
  stdlib packs) stays in single-digit seconds; full mode adds cad-solid, fdm-print and
  openmodelica when their tools exist, and a missing tool is a SKIPPED subject row, never a
  pass.

`V: test_bench`: `--quick --json` parses with `allow_nan=False` and carries the envelope
keys; the tree hash and `git status` are unchanged; an AST walk finds no `bench` import in
`src/atompipe` and no non-stdlib or network import in `bench/`; flipping one expectation via
a test hook exits 1 (the harness can fail); `E1Quick`, `E3Quick`, `E4Quick` (§6); every
E1–E8 has a PROTOCOL.md entry with a falsifier, and `bench e2` exits 0 printing SKIPPED.

**Refuter targets for Phase 5.** Report an inconclusive mutant as survived. Let mutation
touch the real tree beyond its own new entries, or a pack directory. Put a proposer-filled number into the
independence bracket. Make bench output reachable from the spine. Make a preregistered
expectation unable to fail.

**Done criteria for Phase 5.** G1–G8; `python -m bench --quick` green in CI with the tree
hash unchanged; CLAUDE.md gains invariant 15; the commit names S-18 (mutation half), S-19
(measured), S-85–S-87.

**Open questions for the Phase 5 judge panel.**

| Q | Question | Recommended default | Rejected, and why |
|---|---|---|---|
| Q5.1 | Is a survived mutant enforcing or advisory? | Enforcing, per (gate, claim), from stored results: `check` never runs mutation, and a LOGGER stands until a re-run kills (P5.1). | Advisory: a claim with no admissible refusal source stays a gap (§13). `check` running mutation on a miss: worktrees and subprocesses in the inner loop, and tracked writes from a phase-gate command. |
| Q5.2 | Search range? | ×0.5–×2 by default with `--range`; NO-BOUNDARY states the range searched. | ×0.1–×10 by default: 17 of 54 pack gates have no one-parameter boundary even there, and the wider range explores designs nobody would build. |
| Q5.3 | Does gate-code AST mutation gate admission? | Report it as control coverage only. | Gating: 94 of 189 comparison mutants survive in the stdlib packs, mostly guard branches one control cannot reach; that is validity or coverage, not non-vacuity (§2.2). |
| Q5.4 | Can git trailers upgrade origin? | No; provenance text only (D-32). | The proposer writes the commits. |
| Q5.5 | Is a known-good mandatory for *project* gates? | Optional and loudly reported ("acceptance not demonstrated") in every phase, as P2.3 and invariant 9 say; a reject-only FAIL refutes nothing (P3.1); the inside mutant supplies one automatically from here on. | Mandatory: the model itself is allowed to be red (the bracket is), so every existing project would break on upgrade. |
| Q5.6 | In E3, which material table does the beam adapter use? | Both arms, reported separately. | Pack table only hides the finding: sharing the proposer's table makes a pack-origin gate dependent on the proposer's data. |
