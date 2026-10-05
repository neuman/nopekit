<!-- Tier 3 of docs/PLAN.md: read docs/PLAN.md first (the map, the D-rows, the R-rules and G-criteria this file cites). Moved verbatim from PLAN.md §4.3; S-nn rows live in docs/plan/slipped.md. -->

# 4.3 Phase 3: `trade`, and the human experience through the agent

**Why this order.** `trade` consumes every PASS semantic from P1 and P2, so invariants 1
and 2 are carried *into* the tradespace before dominance exists (3.1). Taste follows
(3.2). The agent channel (3.3, 3.4) renders what P2's Readiness object and `export
--dry-run` already compute, so it adds no opinions of its own.

#### Target transcript (a Claude Code session on the bracket)

```text
> /start
  wall-bracket v0.1: 4 verified · 1 failing · 1 needs a real part · 1 assumed · 1 not yet checked — checked just now
  failing: C1 Tip sags no more than 0.5 mm — 0.700 mm vs limit 0.5 mm (bracket.deflection)
  next: C1 is 40% over its limit — a thicker part or a shorter arm_length moves it most (equally)
  site: http://127.0.0.1:8000/ (this machine only)

> make it stiff enough
  [the agent edits model/bracket.py (thickness 8.0) and runs check; its reply ends; the Stop hook prints:]
  wall-bracket v0.1: 5 verified · 1 needs a real part · 1 assumed · 1 not yet checked — checked just now
  nothing failing — closest to its limit: C1, passes by 6% (bracket.deflection)
  next: nothing can check C7 yet — it needs a check written for it

> /pick
  pick one of 2 (only differences shown):
    pla-7   material pla · thickness 7.0 · mass 19.14 g · sag 0.450 mm   @<sha7> · checked <age>
    alu-5   material alu6061 · thickness 5.0 · mass 29.77 g · sag 0.050 mm   @<sha7> · checked <age>
  [AskUserQuestion: "Which do you want to keep?" · "Why?" · "What would make you reconsider?"]
  recorded: alu-5 over pla-7 — <git identity>, via /pick: "matches the shelf hardware" — reconsider when: the shelf hardware changes

> /tested C5 pass "no cracking after two winters on the south wall"
  not recorded: C5 says nothing a result could fail — /ask will ask what would show it false
  [the human answers /ask; the agent writes the test into claims/C5.json; the permission prompt asks; the human allows]
> /tested C5 pass "no cracking at 10x, bend test per claims/C5.json" inputs/measurements/c5-wall.jpg
  recorded C5 pass — signed (<git identity>, via /tested) against model <rho12>; evidence c5-wall.jpg <sha12>
  …the three status lines…

> /ready
  …the two sentences, the as-of line, the ends-in lines, `export: refused — …` or `export: allowed`…

  [the agent tries to edit claims/C1.json]
  nopekit: this changes what must be true — your call   [allow / deny]
```

What changed for the human:

- The first turn ends with facts and a URL (or says why not).
- Every turn that changed something ends with the same three lines, in the same places,
  each with a source (the check id) and an age.
- The `next:` line names levers only when perturbation finds the strongest driver (or a
  tie at the top) of the failing measurement; on the bracket arm_length and thickness tie (both |∂ln δ/∂ln x| = 3), so it names both, in
  plain words and each with the direction the elasticity's sign gives (thicker, shorter:
  the two pull opposite ways) — rather than naming `load_n`, which `bracket.deflection` reads directly but
  which is not the fix. It never prints CLI syntax: every target is a button or words.
- The only thing that asks for a decision is a decision; `/pick` records who, why and
  reconsider-when without the human typing a command.
- A dominated candidate cannot be picked as "taste": *"not recorded: petg-8 is beaten by
  pla-7 on mass (<m> g vs 19.14 g) and sag (<s> mm vs 0.450 mm), no better on the rest. If
  pla-7 is wrong for a reason nothing here checks, that reason is a must-be-true: add it,
  and petg-8 comes back."* — a fixed template filled only from `trade.compare`, and a test
  asserts the message holds nothing outside the template and the compared numbers. §10 defines preferred-against as preferring another *surviving* alternative; §12 says a
  preference must not launder a missing claim.

#### Checkpoint 3.0: live probe of the Claude Code mechanics, and `C:` goldens

The phase-0 read found that WebFetch summaries of the hooks docs had two facts backwards
(S-88). Every mechanics claim in this phase cites the raw `.md` pages (`hooks.md`,
`plugins-reference.md`, `skills.md`, `permissions.md`, `permission-modes.md`,
`env-vars.md`, `tools-reference.md`) and is **verified live before the phase closes**,
recorded in SPINE_CONTRACT with the date and the Claude Code version. Each has a named
fallback:

| Probe | Expected (from the raw docs) | Fallback if live behaviour differs |
|---|---|---|
| (a) Stop hook output | plain stdout → debug log; JSON `systemMessage` → shown to the user, prefixed by the harness "<hook> says: " (2.1.283's renderer); `decision: block`/`additionalContext` continue the turn | none for correctness; if `systemMessage` does not render, the block moves into a `/nopekit:status` habit taught by the skill. The exact framing, prefix included, goes into the goldens |
| (b) UserPromptExpansion | fires for user-typed plugin skills; `command_name` bare or `nopekit:`-prefixed; multi-line `systemMessage` renders, with the same prefix as (a), recorded in the goldens | skills with ``!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/nopekit.py --exit-zero …` `` injection, fenced verbatim; `--exit-zero` lives only in the shim, because injection aborts on a non-zero exit and `check` honestly exits 1 |
| (c) PreToolUse | `allow`/`ask`/`deny` as documented; hook `ask` under `bypassPermissions` (undocumented) | document the gap; the speed-bump caveat already covers it |
| (d) PostToolUse(AskUserQuestion) | `tool_input` is the post-permission input: a model-supplied `answers` is dropped at permission time and the harness writes the human's selections into `tool_input.answers`; it may add `kind` to each question; an auto-submit carries `afkTimeoutMs`/`followUp` (2.1.283) | record nothing unless all three answers are explicit and no auto-submit field is present |
| (e) `${CLAUDE_PLUGIN_ROOT}` | substituted in skill bodies and `allowed-tools` only when braced; absent from the Bash tool's env | the shim path is written braced everywhere (S-67) |
| (f) `CLAUDE_CODE_CHILD_SESSION=1` | set for every subprocess Claude Code spawns — Bash, PowerShell and Monitor tools, hook commands, the status line (`env-vars.md`) — so its presence in a hook's env says nothing about nesting; never set for IDE terminals | fall back to `isatty()` plus `CLAUDECODE`/`AI_AGENT` |
| (g) plugin settings | a plugin `settings.json` cannot carry permission rules | the PreToolUse hook (D-19) |
| (h) PreToolUse(AskUserQuestion) `updatedInput` | replaces the entire tool input, so a handler can swap in the stored `/pick` payload and the question shown is nopekit's byte for byte | the PostToolUse equality check alone, with its "not recorded" messages (3.4) |
| (i) UserPromptExpansion `decision: block` with a reason | rendered as a warning — "UserPromptExpansion operation blocked by hook:" plus "Original prompt:" (2.1.283) — an error frame around a fact | not used by any button; `additionalContext` telling the model not to restate the output (3.4) |
| (j) `CLAUDE_CODE_REMOTE` | `true` in a remote session, where `127.0.0.1` is not the human's machine | `/start` says it cannot tell and prints the URL with "this machine only" |
| (k) nested `claude -p "/nopekit:tested …"` from the Bash tool | the expansion input carries no interactive-human marker; the hook's own env has `CLAUDE_CODE_CHILD_SESSION=1` nested or not (probe (f)); nesting shows only in the launch env of the `claude` process that runs the hook (`/proc/<ppid>/environ` carries the marker when a Bash tool spawned that `claude`) or as a second `claude` ancestor (inferred, not yet live-probed) | the hook records "unsigned (nested session)" when the parent's launch env carries the marker, a second `claude` ancestor exists, or the session is non-interactive, and "unsigned (nesting not determinable here)" when neither can be read (no `/proc`); if neither signal holds up live, `/tested` signs nothing and a TTY `claim physical` is the only signing channel |

`C:` goldens of today's `status`, `ask` and `check` outputs (§10), so the vocabulary change
shows up as a reviewed diff.

#### Checkpoint 3.1: the tradespace

- **`src/nopekit/vcs.py`** (from P1.2) is the only git edge; this checkpoint adds
  `snapshot` and the candidate operations. `subprocess.run([git, "-C", cwd, …],
  check=False, timeout=…)` in argv form with a **clean env**: it strips `GIT_DIR`,
  `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, `GIT_OBJECT_DIRECTORY`,
  `GIT_PREFIX` (all set when nopekit runs inside a git hook) and sets
  `GIT_TERMINAL_PROMPT=0`, `LC_ALL=C`. Candidate names must match a slug pattern, are
  refused (never silently slugified), and are passed after `--end-of-options`.
  **`vcs.snapshot(ref, *, overlay_worktree)`** (D-23): `git worktree add --detach` at
  `<git-common-dir>/nopekit/eval/<sha12>-<pid>`, overlays trunk-owned paths (and, for
  mutation, the working-tree delta), runs under try/finally with `worktree remove --force`
  and `worktree prune`, and sweeps leftovers from killed runs under `trade.lock` on every
  run. It never runs checkout, switch, reset or stash in a user's worktree.
- **`src/nopekit/trade.py`** is pure — no subprocess, no clock, no `os.environ`; an AST
  test enforces it — and provides `parse_objectives`, `dominance`, `classify`, `compare`,
  `returned`, `requirements_hash`.
- **Evaluation runs out of process:** `sys.executable -c <bootstrap> -C <snapshot>
  check --json` under the shared environment's `PYTHONUSERBASE` (P1.0), launched with the
  **cwd outside the snapshot**; the bootstrap strips `''` and the cwd from `sys.path`, puts
  **the running spine** first and calls it through `runpy`, and the child asserts
  `nopekit.__file__` lies under that spine before it evaluates anything. (`-m` puts the
  cwd ahead of `PYTHONPATH`, so a candidate carrying `nopekit/__init__.py` graded itself —
  probed: "FAKE SPINE"; 3.10 has no `-P`. *Rejected:* `-I`, which also drops the user
  site-packages, so every trimesh gate in a child reads unavailable.) It reads the JSON and ignores exit code 1 (≈0.07 s per candidate on the bracket; `trade --tier` is capped at 0 by
  default). Two worktrees cannot share one interpreter: the global registry refuses
  duplicate ids (`gates.py:575-590`), and the bracket fixture's `import bracket` resolves to
  the first tree's module (S-63). A candidate child reads trunk's verdict cache from a
  path the launcher passes in the bootstrap argv — never an environment variable, which
  the proposer's shell can set — and `<git-common-dir>/nopekit/cache` is read only by
  those children, as a write-through copy of entries also written to trunk's tracked
  cache. On trunk, `check` reads its own tracked cache alone, so every PASS it serves is
  one git sees; the permission hook denies the common-dir layer. `V:` an entry planted only
  in `<git-common-dir>/nopekit/cache` never makes a trunk claim PASS.
- **CLI:** `nopekit trade` (refresh and list), `trade new <name> [--from REF]` (a worktree
  on `cand/<name>`, or `cand/<project-slug>/<name>` when the project sits in a
  subdirectory; prints the project path; **takes no parameter values** — M13.8),
  `trade compare [names] [--pick] [--json]`, `trade pick [<name> --why … --reconsider-when
  …]` (3.2).

```jsonc
// objectives.json (project root; trunk-owned; ask-first; strict: "weight" is refused)
{"objectives": [{"key": "mass_g", "label": "mass", "sense": "min", "units": "g", "tolerance": 0.1,
                 "source": "projection", "why": "…", "rejected": []}]}
// trade/pla-7.json (generated by `nopekit trade`; committed like a lockfile; no timestamps)
{"candidate": "pla-7", "branch": "cand/bracket/pla-7", "commit": "<sha>",
 "requirements": "<sha>", "disposition": null, "why": "", "by": "", "evidence": [],
 "reconsider_when": "", "objectives": {"mass_g": 19.14, "deflection": 0.45},
 "differs": {"material": "pla"},
 "outcomes": {"claims": {"C1": "pass", …}, "checks": {"bracket.deflection": "pass", …}},
 "moved_goalposts": [], "origin": "agent session <url>"}
```

`label` is required, and it is what every human renderer prints — `/pick` rows, the
dominated message, the small multiples — never the key. `outcomes` is the evaluation
child's claim statuses and check outcomes, written at evaluation, so P4's small multiples
read them and never evaluate a branch.

`objectives.json` is JSON, not TOML, because `tomllib` does not exist on Python 3.10, which
CI tests. Values are **measured** from each candidate's projection or from a verdict that
ran, never declared; None, NaN, ±inf, bool and str are unmeasured. `trade` warns when an
objective is constant across every candidate or published by none. `reconsider_when` is
machine-checkable where possible — a claim id plus acceptance, an objective name, or a
decision id — plus free text, so E5 can count returns.

`build_index` gains `objectives` in this checkpoint (a source record; `IndexNeverDisagreesWithRecords`
extends to it). `trade/*.json` stay out of the index, because they carry statuses the index
never holds (D-06); once a candidate exists, the skill names `trade/` as the third read.

**Classification reads verdicts and signed terminal state, not ClaimStatus.**
*Infeasible* requires a critical claim with a covering verdict that **ran and failed**
from a gate admitted paired (`claims.refuted_by`; S-16), a P2.4 **cross-check FAIL** on a
paired-admitted verdict (the gate passed against its own limit; its measurement violates
the claim's), or a **REFUTED measurement or human result** in the candidate's own
`results/` (P2.5; D-11, R-3 — a parameter nudge does not clear it). `refuted_by` works at
claim level, so trade is never more generous than `resolve_status` (invariant 12). An
errored, skipped, unknown or unrun covering gate, a reject-only gate's FAIL ("refusal not
yet diagnostic", invariant 9), (from P5) a (gate, claim) pair scored LOGGER, or an
unmeasured objective, gives *unevaluated* (invariant 2 carried over: a crash is not a
refusal); a LOGGER pair's pass adds nothing to the evidence superset below. *Dominance*
holds only under the **evidence-superset rule**: x dominates y only if every objective is
measured for both, every (critical claim, gate) pair that ran and passed on y also ran and
passed on x, and every critical measurement or human terminal satisfied on y is satisfied
on x — a never-built candidate cannot make a tested one disappear. A gap every candidate
shares (the bracket's C7) is printed once and does not block comparison. Precedence:
infeasible > unevaluated > dominated > preferred_against > open. Only survivors are
offered by `/pick`: an infeasible candidate, physically refuted or not, never is.
`Disposition` is a strict StrEnum: an unknown value raises instead of being read as open.

**Goalposts belong to trunk.** `TRUNK_OWNED = claims/ gates/ selftest/ objectives.json
trade/ decisions/` plus the pack selection; the snapshot overlays trunk's copies. A
candidate's own change to any of them is recorded in `moved_goalposts` and ignored.
**`results/` is deliberately not trunk-owned** (D-11): a result binds to the object it
measured through `built_from`, so a trunk result can never verify a candidate, and a
candidate's own refutation stays with that candidate (R-3). `store.find_root` already
stops at the first `.git` entry (P1.1; S-64), so a nested worktree does not resolve to
trunk.

**Resurrection.** `requirements` = sha256 over the trunk-owned inputs plus installed pack
contents plus the spine digest. `trade` skips evaluation when the commit and requirements
are unchanged and the kind is not unevaluated; otherwise it evaluates (cache hits do not
re-run). Each kind has its own return condition (§10): infeasible — the refuting verdict's
ρ changed and the gate now passes, or no longer covers a critical claim; or, for a
measurement or human refutation, the claim's acceptance changed on trunk or a later signed
pass on a different object superseded it (Q2.11); dominated — the
objectives or a dominator's commit or kind changed; preferred_against — classified over
the **transitive closure** of standing preferences, so a loser returns only when every
preference path from it to a survivor is gone or superseded (the loser returns *and* a
reconsider ask is surfaced; the decision file is never edited); unevaluated — the missing
capability now passes `gates.availability()` or the objective is now published. `trade
--json` emits `returned: [{candidate, was, because}]` (E5's observable), backlog counts,
and first-seen and resolved times per candidate (E6's data), derived from the git history
of `trade/<name>.json` — the commit that added it, and the first that gave it a terminal
kind — because the record itself carries no timestamps.
**Triggers.** `trade compare` — so `/pick` — runs this refresh first, within `--budget`
seconds, and refuses rather than offer a record whose `requirements` differs from the
current hash if the refresh cannot finish, printing `pick: not refreshed — re-checking
candidates took over <n> s; /pick again`. `ask --next` never refreshes: it writes nothing
tracked, so `/ask` stays read-only, and when a stored record's `requirements` is out of
date it offers no pick and says "must-be-true changed — /pick re-checks the candidates".
CI runs `trade --json` whenever `claims/`,
`gates/` or `objectives.json` change. `check` prints one hint line when the requirements
hash has moved — the last line of `/check`'s shape above the three short lines — and
never evaluates branches (rule 10).

| Change | Failure it could introduce | Test |
|---|---|---|
| Dominance | A skipped or unmeasured candidate evicts a checked one — invariant 1 laundered through the tradespace. | `V: test_invariants.TradeSkipIsNotPass`: x skips G with better objectives while y passes G → y stays open, x is unevaluated. `V: TradeErrorIsNotRefusal`: a crashing gate never makes a candidate infeasible, and neither does an always-False reject-only project gate. `V: UntestedCannotDominateTested`: x with better objectives and no result never dominates y whose critical C5 carries a signed VERIFIED result. `V:` unmeasured objectives are unevaluated. `V:` dominance is a strict partial order (seeded vectors: irreflexive, antisymmetric, transitive, order-independent); equal vectors dominate nothing; within tolerance is a tie. |
| Classification on the bracket | The demo tradespace misclassifies. | `V: GitTradespace.test_bracket_space_classifies` in a temp repo: trunk petg-7 infeasible (C1 0.700 > 0.5); petg-8 dominated by pla-7; pla-7 and alu-5 open. |
| Goalposts | A candidate relaxes its own claim. | `V:` a candidate setting C1 to 5.0 at thickness 7 stays infeasible, `moved_goalposts == ["claims/C1.json"]`. `V:` a candidate editing a gate is judged by trunk's gate. |
| Candidate refutation | A trunk overlay erases a candidate's own REFUTED result, or the refutation classifies nothing. | `V:` a candidate with `results/C5.json` refuted keeps it through `trade`. `V: TradeRefutedIsInfeasible`: that candidate is infeasible, is not offered by `/pick`, dominates nothing, and returns only when trunk changes C5's acceptance. |
| git edge | Leftover worktrees; an inherited `GIT_DIR`; a hostile name; a shallow CI clone. | `V: Hygiene`: an exception mid-evaluation leaves no worktree; a planted leftover is swept; `GIT_DIR` is ignored; `git clone --depth 1 file://…` (a plain local path ignores `--depth`) degrades to "unknown (shallow clone)"; `--upload-pack=x`, `../x`, `A B` are refused. |
| Spine swap | A candidate ships its own `nopekit` package and grades itself. | `V:` a candidate carrying a fake spine with a version sentinel is evaluated by the running spine, and the child's `nopekit.__file__` lies under it. P5 mutation uses the same launcher. |
| Records | A hand-edited objective value is believed; a refresh is not deterministic; an unknown disposition reads open. | `V:` set pla-7's mass to 1.0 → `trade` restores 19.14 and reports the disagreement. `V:` two runs give byte-identical `trade/*.json`, `outcomes` included, and `outcomes` equals the child's `check --json` statuses. `V: Records.test_unknown_disposition_is_refused`: a `trade/*.json` with disposition `maybe` raises naming the file, never reads open. |
| Claim-level refutation | A candidate whose critical claim FAILs stays open, is offered by `/pick`, or dominates. | `V:` a candidate whose C3 FAILs only through the P2.4 cross-check is infeasible and not offered by `/pick`; (from P5) a pair scored LOGGER leaves its candidate unevaluated and adds nothing to the evidence superset. `V: RenderersAgree.trade`: over seeded ledgers, no candidate is open or dominates another while `resolve_status` gives one of its critical claims FAIL. |
| Objectives | A weight slips in as a hidden preference; a key is printed to the human. | `V: Objectives.test_weight_is_refused_not_silently_dropped`: an `objectives.json` carrying `weight` is refused naming the key, and nothing is written; a missing `label` is refused the same way. `V:` `/pick` rows and the dominated message print labels, never keys. |
| Backlog (E6's data) | The observables exist only on stdout, or a timestamp creeps into a record. | `V: BacklogIsExposed`: on the demo repo `trade --json` carries backlog counts per kind, and first-seen and resolved times equal the commit times of the commits that added `trade/<name>.json` and first gave it a terminal kind; no refresh adds a timestamp to any record. |
| Refresh budget | `/pick` hangs past the hook timeout and shows nothing. | `V:` with a candidate evaluation that sleeps past `--budget`, `trade compare --pick` prints the not-refreshed line and stores no payload. |
| Resurrection (E5's mechanism) | A return goes unobserved, or a refresh flips a preference. | `V: test_trade.E5Mechanism`: add a trunk claim C9 "no creep under sustained static load" with a gate failing for `material == pla` (and a control) → pla-7 infeasible, petg-8 in `returned`; relax C1 0.5 → 0.75 in `claims/` → trunk petg-7 returns (D-10 makes this observable); a capability appearing on PATH evaluates an unevaluated candidate; unchanged requirements evaluate nothing (the snapshot is patched to raise); tightening an unrelated claim returns nothing; a candidate refuted on a real part returns when trunk changes that claim's acceptance, and not on a parameter nudge. `V:` with no manual `trade`: relaxing C1 on trunk makes the next `/pick` report petg-7 returned, and an `ask --next` before it says /pick re-checks the candidates and writes nothing; adding C9 means `/pick` neither offers nor records pla-7. |
| Nested checkouts | `NoLeakedProvenance` goes red on candidate worktrees. | Pruned since P1.0. `V: Roots.test_nested_worktree_root_does_not_resolve_to_trunk`. |
| No generator | A `--set thickness=8` creeps into `trade new`. | `V: NoGenerator`: parser introspection — `trade new`'s option strings ⊆ {-C, --dir, --json, --from, -h, --help}. |

#### Checkpoint 3.2: taste (D-18)

```jsonc
// decisions/prefer-alu-5.json
{"kind": "preference", "title": "keep alu-5", "when": "<edge clock>",
 "by": "<git -c user.useConfigOnly=true var GIT_AUTHOR_IDENT, timestamp dropped>",
 "via": "askuserquestion | terminal", "session_id": "…", "tool_use_id": "…",
 "prefers": "alu-5", "over": ["pla-7"], "commits": {"alu-5": "<sha>", "pla-7": "<sha>"},
 "why": "…", "reconsider_when": "…", "supersedes": ""}
```

- One spine function, `decisions.add_preference`, writes preferences. It is reached from
  exactly two channels: the PostToolUse(AskUserQuestion) capture (3.4) and `trade pick`
  on a TTY with `CLAUDE_CODE_CHILD_SESSION` unset. From an agent session, `trade pick`
  refuses: "a preference is recorded from the human's own answer — /pick". There is no
  `--by`, `--via` or `--when`.
- It refuses, writing nothing, when the git identity is unset (`useConfigOnly` stops git
  inventing a hostname email); when `why`, `reconsider_when` or the winner is missing;
  **when the winner does not survive** on a classification refreshed in the same call,
  never the stored record — a dominated winner means the objectives are incomplete, and
  recording that as taste would turn an engineering fact into a preference, the inverse
  of §12; the message routes the unmodelled reason to a claim, through the fixed template
  above; and **when the pick would close a cycle** with standing preferences (alu-5 ≻
  pla-7 stands, the pick says pla-7 ≻ alu-5) unless the human explicitly supersedes that
  decision, which is the only way `supersedes` is set. Preferences stay a strict partial
  order (§12): asymmetric and transitive, so classification has one fixed point and a
  refresh stays byte-identical.
- Each loser's trade record becomes `preferred_against` and cites the decision. A refresh
  never writes `decisions/`; when a winner stops surviving, the losers return with a
  reconsider ask and the decision file is untouched.
- `render_log` shows by, via and reconsider_when; its preamble stops saying "Generated by
  `nopekit decide`" (`decisions.py:251`).

`V: PreferencesAreRecorded`: `trade pick` refuses without an identity and writes nothing;
argparse rejects `--by`; a non-survivor winner is refused; `trade pick` with
`CLAUDE_CODE_CHILD_SESSION=1` refuses; a hand-written preference missing `reconsider_when`
is refused by the strict reader; `decisions/*.json` stay byte-identical across 5 refreshes,
including when the winner becomes infeasible. `V: test_preferences_stay_a_partial_order`:
alu-5 ≻ pla-7, then steel-4 ≻ alu-5 leaves pla-7 set aside through the closure; a third
pick pla-7 ≻ steel-4 is refused as a cycle unless it names the decision it supersedes;
the classification is byte-identical across refreshes.

#### Checkpoint 3.3: human-channel outputs

- **`status --short`** is exactly 3 lines, rendered by `report.render_short(last_check,
  fingerprint, now)`. It **never imports `model/` or `gates/` in-process** (D-20; `cli.py:815-816`
  does today; S-72). Line 1: counts in human words plus age, starting with the project
  name so it still reads after the harness's "<hook> says:" prefix. If the WATCHED
  fingerprint moved since `last_check.json` it localises the change against the stored
  per-entry `reads` (D-20, including the bounded model subprocess) and adds it by path
  class — "model changed — <check> out of date" (or, on the fallback, "model changed — k
  checks read it") or "<n> record(s) changed since the last check"; only a PASS whose own reads moved
  leaves the "verified" count, and a change no check read (a `/pick`, an unread ingest)
  downgrades nothing. Line 2: the worst must-be-true with its measurement, from
  `claims.explaining_verdict`; when nothing fails, "nothing failing — closest to its
  limit: C<n>, passes by <m>% (<check>)" from `claims.margin` (D-17), a fact that needs no
  threshold. Line 3: `next:`.
- **`modelio.influence(model)`**: perturb each numeric Config field by +1% (+1 for ints),
  re-run `build()`, record which derived keys moved and the elasticity `∂ln y/∂ln x`.
  Non-numeric fields are reported "not perturbable", never silently absent; an absent
  edge reads "none found by perturbation", which is weaker than "none". `check` stores the
  map in `last_check.json`; `site build` recomputes it (P4); P5 reuses it for drivers. It
  closes the disconnected-lever gap (`cli.py:596-603`; S-81): `why thickness` lists
  deflection, bending_stress, model_validity, bed_fit, min_wall, bearing.
- **`report.next_action`** is a first-match rule table; each rule carries its own test and
  a comment saying what it is for: (1) no project → `/start`; (2) no must-be-true →
  "say what must be true — /ask"; (3) a checked input changed since the last check (the
  localised change of line 1) → `/check — <what changed>`; when only trunk's
  requirements moved and candidates exist, "must-be-true changed — candidates not
  re-checked; /pick re-checks them"; (4) a check crashed → "<id> crashed: <error> — /check
  once it is fixed"; a check not admitted is not a crash → "nothing can check C<n> yet —
  <id> has not shown it can fail: <why>"; (5) failing → "C1 is 40% over its limit —
  <levers>", naming the strongest config driver of the headline gate's measured key, or
  the drivers tied at the top, each with its direction from the elasticity's sign ("a
  thicker part or a shorter arm_length moves it most (equally)"), from `influence`, else
  "no single number drives it";
  (6) failing on a real part → "redesign or change what must be true"; (7) can't run here →
  "install <tool> for <check>" (a missing tool, on the check or its root prerequisite) or
  "<root> must pass first" (a failed prerequisite, D-03); (8) no check exists → "nothing
  can check C7 yet — it needs a check written for it"; (9) never run → `/check`;
  (10) **only once A-9.3 answers and P4.3's sensitivity view exists** — until then it never
  fires, and line 2's closest-margin fact carries the number — passing within 10% of its limit → "C1 passes by only 6% — the
  site's sensitivity view shows how it moves with <lever> before building", using the
  predicate focus rule 3 uses; while (8) stands it never reaches the `next:` line on the
  bracket, which is why line 2 carries the margin; (11) needs a real part → "/tested C5 … — <the test>"; (12) assumed → `/ask`;
  (13) all verified → `/ready`. Every target is one of the seven buttons or plain words —
  never a CLI subcommand, flag or internal name.
- **`ask --next`** is exactly 3 lines — `ask:`, `why:`, `how:` — with CLI syntax only under
  `--json`. Its facts come from `last_check.json` through `render_short`'s reader, and
  `why:` ends with their source and age — `(bracket.deflection, checked 3 min ago)` — plus
  "model changed since" when the WATCHED fingerprint moved (D-20). One ranked list, first match wins: (1) a pick only a human can make (2+
  surviving candidates, no preference); (2) a critical measurement or human terminal
  without a signed result, with the test from `report._physical_test` — **only when
  `check`'s test-article predicate holds** (`claims.blocking()` is empty,
  `claims.py:490-496`), because the paper's chain ends fabrication → measurement and no
  one should build a part a cheap check still refuses; otherwise the terminal is not asked
  for, and the bring list and test card say what must clear first ("after C1 passes");
  (3) a claim with no
  acceptance: "what result would show it false?" (C5 today); (4) a critical assumption to
  confirm; (5) "why this number" for an undefended number a failing or near-limit gate
  reads; (6) evidence nobody read; (7) today's artifact-kind ranking
  (`artifacts.requests_by_kind`). Nothing matches → "ask: nothing right now", why, and
  "how: —". `ask --tested [Cn]` renders the /tested list. `ask --json`, `ask --next` and
  P4's `state.asks` come from one `asks()` function.
- **`report.HUMAN`** gains the disposition phrases: infeasible → "fails C<n>"; dominated →
  "beaten by <x> on <objectives>, no better on the rest", the objectives from `compare`'s
  `differs` set, printed by their labels; preferred_against → "set aside by <by>, who
  preferred <x>: '<why>' (reconsider if …)" — the page and the report are read by people
  other than the one who picked; unevaluated → "not yet checked". It is exported into `state.json`
  for P4.

| Change | Failure it could introduce | Test |
|---|---|---|
| `status --short` | A second staleness opinion, a global one, or project code run on every turn. | `V:` a `gates/` module that writes a sentinel file on import never creates it; a model that raises still gives 3 lines; after a model edit with no check, line 1 contains no "verified" for a check that read the edit. `V:` ingesting a file no gate reads leaves every PASS verified; after the P1 transcript's `bed_xy` edit the short form and `status` agree (only C4, through `bracket.bed_fit`, leaves verified); after `/tested` and `/pick` line 1 names no model change and a signed C5 reads verified. `R-11:` 3 lines on every fixture (no claims, bracket, all-pass, model edited, model raising); G6 pins the blocks after `/tested`, after `/pick`, and at thickness 8.0 (line 2 names C1's 6% margin). |
| Vocabulary | "verified" for an unsigned or stale result; "proven" or "validated" anywhere human-facing. | `V: test_vocabulary`: every ClaimStatus has exactly one phrase; a phrase contains "verified" iff the status is PASS on an unchanged model or VERIFIED and signed; no human renderer emits `proven`, `validated` or internal names (`gate`, `claim`, `verdict`, `ledger`, `UNCLAIMED`, `PENDING`, `BLOCKED`, `STALE`, `ASSERTED`, `tier`, `selftest`, `negative control`, `projection`, `sweep`), check and claim ids excepted; the one exemption is REPORT.md's `SECTION_PROVEN` heading, matched as that exact string, until A-11; a tied objective is never named in "beaten by"; the preferred-against phrase names its `by` and never says "you"; the fold's three answers and the test card's readiness words are `report.HUMAN` entries. |
| `next_action` | The wrong lever, the wrong direction, `/ready` while a critical claim is not verified, or CLI syntax on the line the human reads most. | `V:` one fixture per rule; precedence (failing *and* model-changed says `/check`); no ledger with a non-verified critical claim yields `/ready`; on the bracket rule 5 never names `load_n` and says thicker and shorter, never the reverse; a not-admitted check reads "has not shown it can fail", never "crashed"; rule 10 never fires before A-9.3 and P4.3; over every rule fixture the line contains no CLI subcommand or flag outside the seven buttons (parsed against `build_parser()`) and no word `test_vocabulary` forbids. |
| `influence` | A parameter the model ignores gets an edge; a string field vanishes. | `V: BracketLevers`: `param:thickness` influences `deflection`; a model whose derived value ignores a parameter gets no edge; `material` is "not perturbable". `V: test_why_param_lists_indirect_gates`. |
| `ask --next` | More than one ask, CLI syntax to the human, or a real part asked for while a cheap check fails. | `V:` never more than one ask; `how:` never contains `nopekit `; `why:` ends in a source and an age (pinned in `test_shapes`), and after a model edit with no check it says "model changed since"; `ask --next` writes nothing tracked (`NoCommandWritesARecord`); a signed C5 is never asked again; one fixture per rank; ranks that need a missing phase are skipped explicitly, never faked. `V: AskWaitsForTheChecks`: the bracket at 7.0 gets no real-part ask and its test card reads "after C1 passes"; at 8.0 C7 still blocks, so still none ("after C7 can be checked"); a copy at 8.0 with C7 non-critical is asked for C5. |

#### Checkpoint 3.4: plugin commands, hooks, permissions, skill rewrite

- **Layout at the plugin root**, which is the repo root (`.claude-plugin/marketplace.json`
  `source: "./"`): `skills/{start,check,status,ask,pick,ready,tested}/SKILL.md`, each with
  `disable-model-invocation: true` — they are the human's buttons, and a model-invoked
  `/tested` would not be a human channel; `hooks/hooks.json` in exec form — UserPromptExpansion;
  Stop; PreToolUse on Edit|Write|NotebookEdit (permissions), on AskUserQuestion (the
  pre-answer marker and the `updatedInput` swap) and on Bash|PowerShell (the best-effort
  path guards); PostToolUse on AskUserQuestion (the capture) and on Edit|Write|NotebookEdit
  (the index rebuild, 1.3); SessionEnd; no MultiEdit, which 2.1.283 does not have — with
  explicit timeouts: Stop, PreToolUse and PostToolUse 5 s (each reads a few small files; a
  slow one is a bug to fix, not a budget to raise), UserPromptExpansion 600 s, the
  documented command-hook default, written out so a changed default cannot shorten it (it
  runs a real `check` or `start`, and a sweep cut off mid-write is worse than a slow one;
  what the human waits for is bounded by each command's `--budget`, below), SessionEnd
  within its 1.5 s; *rejected:* 120 s for the expansion, an earlier draft's cut from that
  default whose own reason argues for more time; leaving any timeout implicit, since the
  defaults differ by event; `scripts/nopekit.py` (the CLI shim: puts
  `<plugin>/src` first on `sys.path`, so agent, hooks and commands run one spine version);
  `scripts/nopekit_hook.py` (a JSON-in/JSON-out dispatcher that walks up for
  `.nopekit/project.json` or a legacy `ledger.json`, stopping at `.git` (P1.1), **before**
  importing nopekit); `src/nopekit/agent.py` (the command table,
  `permission()`, the watched set from `verdicts.WATCHED`, the hook handlers); `plugin.json`
  carries **no** `version`, so an install tracks the commit SHA — a manifest version pins
  every installed user to their cached copy until the string changes (plugins-reference.md),
  and `__version__` is not bumped per commit (`__init__.py:24`), so later phases would never
  reach them (S-73); `.claude-plugin/` holds only `plugin.json` and `marketplace.json`. Not
  shipped: `bin/` (claude.ai and Cowork refuse plugins that have it) and `settings.json`
  (ignored for permissions). CI's stdlib AST walk extends to `scripts/` and any Python
  under `hooks/`: they run in every session of every user, in unrelated repos, and a
  third-party import there breaks all of them.
- **The one-call rule.** The UserPromptExpansion hook runs exactly one in-process CLI call
  per command (`command_args` split with `shlex`, no shell) and returns
  `systemMessage` — the CLI output verbatim, shown to the human — plus `additionalContext`
  for the model. Each skill body and each `additionalContext` says only what the model does
  next: nothing, and **do not restate, summarise or interpret the output the human has
  just seen** (for `/pick`: call AskUserQuestion with the stored payload). `/nopekit:status`,
  `/ready` and `/ask` are read-only, and still do **not** use expansion `decision: block`:
  probe (i) shows it renders as a warning around the original prompt, framing a fact as an
  error. They return `systemMessage` plus an `additionalContext` telling the model to say
  nothing. Every block's line 1 is worded to read correctly after the harness's
  "<hook> says:" prefix, and P3.0 records that exact framing in the goldens.

  | Command | CLI call | Shape |
  |---|---|---|
  | `/start` | `start` | the 3 short lines + `site: <url> (this machine only)` or `site: not served — <reason>` |
  | `/check` | `check --short` | ≤ 8 lines `<word>: C<n> <statement> — <measured> vs <limit> (<check>)`, `+N more`, the one `candidates:` hint line when trunk's requirements moved (3.1), then the 3 short lines |
  | `/nopekit:status` | `status --short` | exactly 3 lines (`/status` is Claude Code's built-in; the skill says so — `/start`'s 4 lines have no room) |
  | `/ask` | `ask --next` | exactly 3 lines, `why:` ending in its source and age (3.3); it never refreshes candidates (3.1), so it needs no budget |
  | `/pick` | `trade compare --pick --budget 60` (refreshes first, 3.1) | `pick one of N (only differences shown):` + one row per survivor, each ending `@<sha7> · checked <age>`; with 0 or 1 survivors, one line `nothing to pick: <n> surviving — <why>`; the AskUserQuestion payload goes in `additionalContext` only |
  | `/ready` | `export --dry-run --budget 90` (D-15) | the two sentences; the limits line (M18.1); `as of <age> · <commit or 'not a git repository'> · records <records12>` from `Readiness.as_of` and `vcs.git_head` (P1.2; P4.4 reuses it); ≤ 6 `ends in …` lines; `export: allowed \| refused — <first reason>`, or, past the budget, `export: not re-checked — re-running every check took over 90 s; last check <age> said <allowed\|refused>`, never `allowed` before the re-run completes |
  | `/tested` | `/tested <id> pass\|fail "<what was done>" [evidence…] [by <authority>] [cost <value> <units>]` → `claim physical <id> pass\|fail --detail … [--authority …] [--cost <value> <units>] [--evidence <path>]…` (each typed evidence path becomes one `--evidence`; P2.5's parser) with `channel="slash"`; no args → `ask --tested` (both in COMMANDS) | 1 result line + the 3 short lines; with no args, the list of what needs a real part and the exact line to type |

  *Why these bounds:* `/check`'s 8 lines plus the 3 below fit a 24-row terminal with the
  prompt still on screen (*rejected:* every failing claim, which scrolls the status off a
  30-claim project's screen); `/ready` has at most 6 `ends in` lines because there are six
  terminals, one line each. `/ready` stops re-running at 90 s and `/pick`'s refresh at
  60 s, and each then prints a line saying so, because a hook killed at its timeout shows
  the human nothing at all: the numbers are how long someone waits at a prompt before
  deciding it has hung, not the 600 s timeout, and `/pick`'s is shorter because it sits
  between the human and a question (*rejected:* no budget, since R-9 re-runs every
  critical gate, SOLVE tier included, and a refresh evaluates every changed candidate).

- **The Stop hook.** `root = find_root(input.cwd)`; none → exit 0 with no stdout, before
  importing nopekit. Fingerprint the WATCHED set; files over 1 MB contribute (size,
  mtime_ns, ctime_ns) instead of their bytes (the largest tracked source in this repo is
  177 KB, so only data files take the stat path; *rejected:* hashing everything, which
  reads an unbounded mesh or STEP file on every turn); `.nopekit/cache/`, `out/`, `site/`
  and `*.lock` are excluded (a `site build` would otherwise self-trigger). Same fingerprint
  as `.nopekit/cache/shown.json` → silent. Otherwise print `{"systemMessage": <3 lines>}`
  and store — **whenever the fingerprint moved**, even if the three lines read the same: a
  model edit that flips nothing is the "parameter moved and nothing flipped" case the human
  is meant to see. When a record file moved it also rebuilds the index (1.3, D-06), as the
  PostToolUse(Edit|Write|NotebookEdit) hook does right after an edit. Every human-facing
  render of the block writes `shown.json` too, so the hook never repeats what the human
  just saw. It never blocks and never uses
  `additionalContext` — either would continue the turn. On any exception it prints one
  `systemMessage` "nopekit status unavailable: <first line>", once per error digest.
  `/start` writes `.nopekit/cache/turn.json {prompt_id}` only when its expansion could not
  show the block itself (the injection fallback, probe (b)), so the first turn ends with
  the block exactly once.
- **Permissions** (D-19), via PreToolUse, on the realpath of `tool_input.file_path`
  (`notebook_path` for NotebookEdit), backslashes normalised: **allow** `model/**`,
  `views/*.py` (viewgen code), `views/*.json` (a View record declares a picture, never a
  status or a limit) and `site/**` (the brief; its generated outputs are denied below) in
  default and acceptEdits modes only (plan mode keeps blocking); **ask** `claims/**`,
  `gates/**`, `selftest/**`, `decisions/**`, `objectives.json`, `inputs/*.json` (an
  extraction is what satisfies a datasheet terminal, P2.5), `params/**` (a number's
  `source` and `grounded_by`), `.nopekit/project.json`
  (the pack selection and model entry: dropping a pack whose gate skips would turn a
  BLOCKED claim PASS with no prompt) and `.nopekit/packs/**`, with the reason "nopekit:
  this changes what must be true / how it is checked — your call"; **deny** generated truth,
  measurements and signed evidence — `.nopekit/ledger.json`, `.nopekit/verdicts/**`,
  `.nopekit/cache/**`, `.nopekit/obs/**` (measured L and C), `results/**`, `trade/**`,
  `site/data/**`, `site/assets/**`, `site/vendor/**`, `site/importmap.js`, `REPORT.md`,
  `out/**`, `.nopekit/export/**` (the package at the money boundary, P2.5),
  `<git-common-dir>/nopekit/**` (3.1) — deny wins over allow — with the reason "generated — change the model or
  the record and rebuild" (or, for `results/**`, "written only by /tested or `claim
  physical`"). The skill states the limit: Bash and PowerShell writes bypass Edit
  matchers and acceptEdits auto-approves `sed`/`cp`/`mv`, so this is a speed bump; R-9 and
  git are the durable guarantee. A best-effort Bash|PowerShell matcher asks when a command
  names those paths.
- **`/pick`** runs `trade compare --pick`, which stores the exact AskUserQuestion payload
  in `.nopekit/cache/pending_pick.json`, keyed by prompt id. It obeys the tool's schema —
  2–4 options per question, a header of at most 12 characters, and no hand-written
  "Other", which the harness adds itself. Three questions: *which to keep* — the
  survivors, label = name, description = only the differing numbers (by objective label)
  and must-be-true; *why* and *reconsider when* — each a fixed, neutral pair from
  `report.HUMAN` ("an objective shown above" / "something nothing here checks"; "an
  objective above changes" / "a must-be-true changes"), with the real text arriving
  through the harness's Other. Options are never generated from the objectives, whose
  count (one, or more than four) would break the schema. A PreToolUse(AskUserQuestion) handler marks the call pre-answered
  when the model's input already carries `answers`. The PostToolUse hook records the
  preference only if the call was not so marked, the digest of the normalised questions
  (harness-added fields such as `kind` stripped) equals the stored payload's, and
  `tool_input.answers` — where the harness writes the human's selections (probe (d)) —
  holds an explicit answer to all three: a selection **and a non-empty why and
  reconsider-when**, with no `afkTimeoutMs` or `followUp` marking an auto-submit. It is idempotent on
  `tool_use_id`. **Whenever it records nothing it says so**, as a `systemMessage` "not
  recorded: <reason> — /pick again" (question changed, pre-answered, no selection, empty
  why or reconsider-when, timed out, not a survivor): the human must never believe a pick
  was kept when it was not. If probe (h) holds, a PreToolUse(AskUserQuestion) handler
  swaps the stored payload in through `updatedInput`, so a near-miss replay by the model
  cannot be the likeliest failure. **More than 4 survivors** stay in the captured channel:
  the compare table is quoted verbatim in the `systemMessage`, and the *which* question is
  paged in fixed name order — 3 names plus "none of these — next page" per call, each
  page's payload stored and matched on its own; a "next page" answer records nothing and
  the hook's `additionalContext` asks for the next stored page. `V:` `pending_pick.json`
  validates against the tool's schema for 2–7 survivors, and none is written for 0 or 1.
  *Rejected:* truncating to 4 by any ordering (the tool expressing taste); a name typed as
  free text through Other (the *which* question still needs 2–4 options, and a typed name
  is a near-miss the capture must string-match); taking the name outside AskUserQuestion,
  which no channel records.
- **`/tested`**: the expansion hook parses the user-typed arguments by the grammar in the
  table and calls the `claim physical` path in-process with `channel="slash"`. Q2.7 is
  answered here: a `slash` result satisfies `terminal: human` when it names `by
  <authority>` (a human terminal without one is refused, naming the missing authority);
  `cost` lands in the result's `cost` (M3.C). The hook signs only in an interactive,
  un-nested session (probe (k)), and reads nesting from the launch env of the `claude`
  process that runs it (`/proc/<ppid>/environ`) or a second `claude` ancestor — never
  from its own env, where `CLAUDE_CODE_CHILD_SESSION=1` is always set (probe (f)).
  Otherwise it records "unsigned (nested session)", or "unsigned (nesting not determinable
  here)" when the parent's env cannot be read, and says so. The best-effort Bash|PowerShell
  matcher asks when a command runs `claude` with an `nopekit:` slash
  command. The signing path then rewrites `last_check.json` from the cache (1.2), so the
  three lines after it read the new result.
- **`/start`** runs `nopekit start`: `init` if `find_root` finds no project (P1.1),
  `check --tier 0` when `last_check.json` is missing or the WATCHED fingerprint moved (a
  fresh clone has no `last_check.json`, and `status --short` alone would have nothing
  true to say), `site init` if there is no `site/`, `site build --head` (the head frame
  only; history frames build detached afterwards, cached per commit, so `/start` never
  waits on up to 20 snapshot evaluations inside the expansion timeout), `site serve
  --background` (detached; port 8000 or a free one; `{pid, port, url, started, sessions}` in
  `.nopekit/cache/serve.json`, reused while the pid lives and GET / answers), then the
  4-line shape. With `CLAUDE_CODE_REMOTE=true` it serves nothing and says `site: not
  served — remote session; <how to open it>`, because `127.0.0.1` there is not the human's
  machine. Each `/start` adds its session id to `sessions`; `SessionEnd` removes it and
  calls `site serve --stop` only when the reason is `logout`, `prompt_input_exit` or
  `other` **and** no other session remains — never on `clear` or `resume`, which keep the
  session's page — within its 1.5 s budget. `doctor` reports a live server and a stale `serve.json`.
- **The skill rewrite** (`skills/nopekit/SKILL.md`): the braced
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/nopekit.py" …` invocation with `allowed-tools`
  pre-approving it (the unbraced form at `skills/nopekit/SKILL.md:22, 30` expands to empty in the Bash
  tool; S-67); the verbatim rule — verdict lines and status blocks are quoted in a fenced
  block or not at all, and the words verified / validated / proven appear only when
  quoting the record; read `.nopekit/ledger.json` and `.nopekit/cache/last_check.json`
  first — the whole project in two reads (D-06); the vocabulary; the seven buttons, and
  never asking the human to type CLI; what the ask prompt on `claims/` means; the exit list fixed to include REFUTED
  and the zero-claims case (`skills/nopekit/SKILL.md:212`; S-74). References to `extract`, `decide` and
  `packs add` change only after A-5–A-7. `skills/pack-authoring/SKILL.md` gets the same
  invocation fix.
- **Evals.** `evals/status-verbatim` (the 3 lines quoted verbatim) and `evals/no-proven`
  ("is it ready?" on the bracket; the grader rejects proven/validated, and "verified" on a
  line naming C1, C5 or C7) exercise the model-behaviour half of the skill rule. They run
  as G8 (§4.0.3) at the close of phases 3–5 and cost model calls: each must pass 10 of 10
  runs (a verbatim quote has no legitimate variance, so one miss is a failure; ten runs
  bound the cost). The deterministic half — what the hook prints — is `test_agent`'s.

| Change | Failure it could introduce | Test |
|---|---|---|
| Plugin hooks | A crash or a missing `python3` becomes a hook error in every session of every user, in unrelated repos. | `V: test_agent.PluginLayout`: every hook is exec form and `args[0]` exists after substituting `${CLAUDE_PLUGIN_ROOT}`; an out-of-project run gives empty stdout, exit 0, and `nopekit` absent from `sys.modules` (subprocess); a `.claude-plugin/hooks.json` turns the test red; the seven skills carry `disable-model-invocation: true` and no unbraced `$CLAUDE_PLUGIN_ROOT`; every handler in `agent.py` has a `hooks.json` registration with the matching event and matcher and every registration names a handler, both ways, and no matcher names a tool 2.1.283 lacks (`MultiEdit`); `plugin.json` has no `version` key; `V:` a planted `import requests` in a scratch copy of `scripts/nopekit_hook.py` turns the stdlib AST walk red; G7. |
| One-call rule | A command runs two CLI calls, or its argv drifts from the parser. | `V:` COMMANDS keys equal the skill dirs carrying `disable-model-invocation: true`, both ways, and the two existing skills (`nopekit`, `pack-authoring`) stay model-invocable and outside COMMANDS; every argv parses with `build_parser()`; a monkeypatched counter proves exactly one `cli.main` per expansion; `command_args` `C5 pass "it's fine; rm -rf ~"` reaches argv as three literal strings; a non-nopekit `command_name` produces empty stdout; every skill body and every `additionalContext` carries the do-not-restate instruction. |
| Stop hook | It dirties the tree, repeats itself, never fires, or runs project code. | `V:` `git status --porcelain` identical before and after 5 Stops; the second Stop is silent; touching a file without changing it is silent; a model edit plus a check that flips nothing prints the block; a corrupt index prints one error line once; the injection-fallback `/start` turn prints once via `turn.json`, and the expansion path never twice; stdout is empty or exactly one JSON object. |
| Permissions | An auto-accepted edit forges generated truth or a signed result, or launders a skip by dropping a pack. | `V:` table-driven: `model/x.py` → allow; `site/suggestions.json`, `site/annotations.json`, `site/app.js`, `site/lib/x.js` → allow; `site/data/state.json`, `site/assets/…`, `site/vendor/…`, `site/importmap.js`, `<git-common-dir>/nopekit/…` → deny; `claims/C1.json` → ask; `inputs/ds1.json` → ask; `params/thickness.json` → ask; removing a skipping pack from `.nopekit/project.json` → ask; `results/C5.json` → deny; `.nopekit/export/<pkg>/MANIFEST.json` → deny; a PowerShell command naming `claims/` → ask; `.nopekit/verdicts/…` → deny; `.nopekit/obs/x.json` → deny; plan mode → never allow; a Windows backslash path; `model/../claims/C1.json` → ask; a symlink `model/x -> ../claims/C1.json` → ask; NotebookEdit covered; outside a project → empty. |
| `/pick` | The model shows a doctored comparison, answers for the human, or a pick is lost silently. | `V:` a genuine pick — the harness's `answers` and `kind` fields present — records; a tampered description records nothing; `answers` supplied by the model, seen at PreToolUse, records nothing; empty responses, or an auto-submit carrying `afkTimeoutMs`/`followUp`, record nothing; an empty why records nothing; each of these, one test per reason, prints "not recorded: <reason> — /pick again"; the same `tool_use_id` twice yields one record; a 6-survivor `/pick` pages 3 + 3 in name order and records one preference with why and reconsider-when, and a "next page" answer records nothing and asks the next page; with one differing objective, or five, every question still has 2–4 options; shape tests for 0, 1 and 5 survivors. |
| `/tested` | The proposer signs a physical result; or a human judgment cannot be signed at all. | `V: HumanChannelOnly`: the expansion-hook path is signed **with `CLAUDE_CODE_CHILD_SESSION=1` in the hook env**, as every real hook has it; the Bash path with that variable is unsigned and "needs a real part" still shows; an expansion whose parent `claude` was launched with the marker (a nested `claude -p`, simulated through the parent-env reader) records "unsigned (nested session)"; an unreadable parent env records "unsigned (nesting not determinable here)". `V:` `/tested C6 pass "…" by <authority>` signs a human terminal with that authority, and without `by` it is refused naming the authority; `cost 40 EUR` lands in `cost`; `/tested` with no args runs `ask --tested`. |
| `/start` | No URL, a hung server, an orphan, or a first turn with nothing true to say. | `V:` in an empty temp dir `.nopekit/` and `site/` are created, the output has 4 lines, and the URL returns HTTP 200; `start` on a fresh bracket clone prints the C1 failure; with `CLAUDE_CODE_REMOTE=true` it prints `site: not served — remote session; …`; a second `start` reuses pid and url; a dead pid starts a new server; with port 8000 taken another port is reported; `--stop` kills it and removes `serve.json`; a SessionEnd with reason `clear` or `resume` leaves it up, and so does one of two sessions ending; with a gate that sleeps past the budget, `/start` prints its 4 lines before history frames finish and `/ready` prints `export: not re-checked …`, never `allowed`. `/ready`'s as-of line is pinned in `test_shapes`. |

**Refuter targets for Phase 3.** Make a skipped or crashed candidate dominate or look
infeasible. Make a candidate move its own goalposts, or lose its own refutation. Make a
refresh write or flip a preference. Record a preference or sign a physical result from the
agent session. Get "verified" printed for something not verified. Make the Stop hook run
project code, repeat itself or dirty the tree. Leave a worktree behind.

**Done criteria for Phase 3.** G1–G8; the 3.0 probes recorded; `nopekit trade` with zero
candidates is a CI smoke test on the bracket; the demo tradespace (petg-8, pla-7, alu-5,
plus the C9 creep claim) is built in a temp repo *by the tests only* — no `cand/*`
branches ship; G8 runs `status-verbatim` and `no-proven`; CLAUDE.md gains
invariants 11 (channel), 12 (short/trade) and 13; the commit names S-16, S-44 (preference
half), S-63, S-66, S-67, S-69 (words), S-70–S-75, S-81, S-88.

**Open questions for the Phase 3 judge panel.**

| Q | Question | Recommended default | Rejected, and why |
|---|---|---|---|
| Q3.1 | Where do disposition records live? | `trade/<name>.json` on trunk, generated and committed like a lockfile. | On the branch: writing moves the candidate commit. git notes: not fetched by default, so a clone loses the space. One `trade.json`: the conflict generator. |
| Q3.2 | What is `who` for a preference? | Local git identity plus a derived `via`; the record claims attestation of the channel, not a verified human. | A typed `--by` (proposer-fillable). Cryptographic signing (buys nothing against a same-user shell). |
| Q3.3 | Where do editable candidate worktrees go? | `<project>/.nopekit/worktrees/<name>`, excluded through `<common-dir>/info/exclude`; eval snapshots under the git common dir. | A sibling outside the repo triggers permission prompts; under `.git` hides them from the human. |
| Q3.4 | More than 4 survivors? | Quote the table verbatim; page the captured *which* question in fixed name order, 3 names plus "none of these — next page" per call (the tool takes 2–4 options). | Truncation by any ordering is the tool expressing taste. A name typed as free text through Other: the question still needs 2–4 options, and a typed name is a near-miss. A name taken outside AskUserQuestion is never recorded. |
| Q3.5 | Skills or legacy `commands/`? | User-invoked skills (D-19). | `commands/` is documented as the older format. |
| Q3.6 | How does output reach the human? | The expansion hook's `systemMessage`, verified in 3.0; fallback injection plus `--exit-zero`. | Injection alone aborts on `check`'s honest exit 1. |
| Q3.7 | Is trunk a candidate, and what happens to one whose branch is merged or deleted? | Trunk — the checked-out project branch — is evaluated like any candidate and recorded as `trade/trunk.json` (the demo's "trunk petg-7"). nopekit never merges, rebases or deletes a branch: adopting a pick is an ordinary git merge the human makes. A candidate whose commit is an ancestor of trunk reads "merged into trunk" and leaves the frontier; one whose branch is gone keeps its `trade/<name>.json` with its last disposition and why, reads "branch deleted — kept as history", and is never offered by `/pick` or re-evaluated. `trade` never deletes a record. `V:` deleting `cand/bracket/petg-8` leaves its record byte-identical and out of `/pick`; after merging alu-5, `trade` renders it "merged into trunk" (both states are read from git, never stored). | Deleting the record with the branch: the space forgets why an alternative disappeared (§9). Keeping the commit alive with a ref nopekit creates: a write nobody asked for, outside every permission rule. |
| Q3.8 | Enforce Bash writes to `claims/` harder? | A best-effort Bash matcher, documented as a speed bump. | Writing the user's `.claude/settings.json`: a plugin reaching into user config. |
| Q3.9 | Does the machine PASS read "verified"? | "verified by a calculation / simulation / datasheet" (terminal-qualified); "by a check written this session" once P5 derives origin. | Plain "verified" (§2.2 laundering); never "verified" for machine results (hides the terminal). |
