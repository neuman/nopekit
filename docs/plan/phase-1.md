<!-- Tier 3 of docs/PLAN.md: read docs/PLAN.md first (the map, the D-rows, the R-rules and G-criteria this file cites). Moved verbatim from PLAN.md §4.1; S-nn rows live in docs/plan/slipped.md. -->

# 4.1 Phase 1: records as files, per-gate ρ, admission that counts, no run history, JUnit

**Why this order.** This phase has the largest blast radius: it changes where every fact
lives. So it opens by hardening every guard its refactor passes through (1.0), then makes
the done criteria mean something — JUnit, pack-mode selftest, honest CI — *before*
moving storage (1.1). Verdicts move (1.2) before records (1.3), so verdicts never lose
their home mid-phase: at 1.2 claims are still in the old ledger while verdicts are
already in the cache. Claim-status semantics stay fixed throughout (R-8), which keeps the
differential oracle exact; Kleene waits for P2 (D-01).

#### Target transcript

```text
$ git clone … && cd atompipe/examples/bracket
$ atompipe check
[FAIL] bracket.deflection : 0.700 mm at 15 N (limit 0.5 mm)                  cached
6 gates: 0 executed, 6 cached — 5 ok, 1 FAIL — tier 0
BLOCKING — 2 critical claim(s) must not be spent against:
[FAIL ] C1 Tip sags no more than 0.5 mm at rated load — bracket.deflection : 0.700 mm at 15 N (limit 0.5 mm)
[gap  ] C7 First mode is clear of the pump that sits on the shelf — no gate covers it
$ echo $?
1
$ git status --porcelain
$                                        # nothing: check wrote only ignored outputs
$ $EDITOR model/bracket.py               # bed_xy 220 -> 250
$ atompipe status
…
stale: bracket.bed_fit — config.bed_xy 220.0 -> 250.0   (5 checks current)
$ atompipe check
[ok  ] bracket.bed_fit : 74 x 30 x 7 mm vs 234 mm usable (250 bed - 2x8 brim)
[FAIL] bracket.deflection : 0.700 mm at 15 N (limit 0.5 mm)                  cached
6 gates: 1 executed, 5 cached — 5 ok, 1 FAIL — tier 0
…
$ git status --porcelain                  # porcelain prints repo-root paths
 M examples/bracket/model/bracket.py
?? examples/bracket/.atompipe/verdicts/bracket.bed_fit/<rho16>-<out8>.json   # the new evidence, a new file
$ atompipe why thickness
param thickness = 7.0 mm   (model/bracket.py Config.thickness)
REJECTED (1)
  4.0 mm — 3.75 mm deflection, 7.5x the limit   (model/bracket.py PARAMS)
$ atompipe gate show bracket.deflection | tail -1
  last selftest: [ok  ] fired at this version (control <rhoC12>)
$ (cd "$(mktemp -d)" && atompipe gate selftest)   # no project here: pack mode, every bundled pack
<n> control(s) in <t>: <n> fired, 0 BROKEN, <k> skipped (tooling)
$ atompipe check --junit                          # .atompipe/out/junit.xml, rendered by any CI
```

What changed for the human, against today (§10):

- `check` stops dirtying the tree; `check` and `status` use the same tag and cite the
  same reason (S-68, S-69).
- `status` names the one check an edit affected, not "model hash changed" (M11.7).
- `why` shows the model's current number and what lost (S-39, S-42).
- `gate show` reports the last selftest (S-08).
- The pack author's command from CLAUDE.md works where CLAUDE.md says to run it (S-09).

#### Checkpoint 1.0: harden the guards (no behaviour change for honest code)

| Deliverable | Failure it prevents | Test |
|---|---|---|
| `report.SECTION_PROVEN = "## What is PROVEN"`. `_proven_section` raises when the heading is absent and never returns `""`; a positive control asserts a gate that ran ok **does** appear. | Today the helper returns `""` when the heading is missing (`tests/test_invariants.py:238-239`); P2's REPORT.md rewrite could rename the heading and three invariant-4 tests would pass on an empty string (S-15). | `V:` monkeypatch the heading → the helper raises. `V:` the positive control fails on an empty section. |
| Violation tests for the admission guards that have none. | Any of them could regress silently; only the None-control refusal is tested (`tests/test_invariants.py:182-190`; S-14). | `V:` `NegativeControl(fixture="")` and `"   "` raise. `V:` `expect="pass"` gives a selftest error. `V:` mutating the caller's spec after register does not reach the registry. `V:` the stored control set to None gives a `run_all` error verdict. `V:` a missing fixture file, a fixture returning None, a gate crashing on its fixture, and a gate passing its fixture each give a not-ok selftest, the last with "PASSED its own known-bad". |
| `Registry.specs()`, `get()` and `pairs()` return `_own_copy`. `Registry.set_pack(id, pack)` replaces `packs.load_gates`' mutation of the stored spec (`packs.py:740-742`). The `_own_copy`/`run_all` docstrings stop claiming the window is closed. | `reg.specs()[0].claims.append(…)` widens what a verdict settles (re-probed; S-13). | `V:` append through `specs()`, then `run_all`: `verdict.claims` unchanged. `V:` swap the fixture through `get()`: the control is unchanged. `C:` `test_pack_keys` stays green. |
| `Verdict.outcome -> "error" \| "skipped" \| "pass" \| "fail"` (P2 inserts `"unknown"` between error and skipped). `render()` and `resolve_status` call it. | Three re-derivations of one fact today; JUnit (1.1) would make four (R-5). | `V: RenderersAgree.table`: all 8 combinations of (passed, skipped, error) — 16 once P2 adds `blocked_by` — give agreeing `ok`, render tag, outcome and claim status. |
| **Strict pass values.** `_normalise` and `_stamp` accept a `bool`, or a 0-d object whose `dtype.kind == "b"` (numpy.bool_, duck-typed, no import), converted with `bool()`. Anything else becomes `error="gate reported passed=<repr> (<type>); a verdict must say True or False"`. `_reject_non_finite` refuses a `measured`/`limit` that is neither None nor a real number; `bool` is not a number. | `{"passed":"false"}` reads `[ok]` today (`gates.py:950`, `:895`; re-probed; S-01, S-02). *Rejected:* keeping `bool()`, which is the hole. *Rejected:* `isinstance(x, int)`, which accepts `1` and `2` as passes because `bool` subclasses `int`. | `V: PassMustBeABool` over `"false"`, `"no"`, `None`, `1.0`, `2`, `"n/a"` as measured, `True` as measured. `C:` all 54 bundled gates still pass their baseline — the regression guard for numpy-returning mesh gates. |
| `test_packs` baseline **and control** tests (and `packs.demonstrate`, P1.1): a skip is allowed **only** when `availability(spec)` fails; a self-skip on the pack's own baseline, or on its own known-bad input, is a failure. Makes `docs/PACK_FORMAT.md:365-366` ("nothing skips") true. | A gate that self-skips on a missing baseline key is never shown to accept anything while the suite reads green (`tests/test_packs.py:164-165`); a control that skips itself is accepted as "honestly blocked" and then skipped by `ControlsAreSealed` (`tests/test_packs.py:203-205, 262-263`; `gates.py:1537-1542`), so a fixture that deletes a needed key passes invariants 3 and 6 (both S-12). | `V:` a scratch pack whose gate returns `Verdict(skipped=True)` on its baseline turns the test red. `V:` a scratch pack whose fixture removes a key the gate needs turns `NegativeControlsFire` red and, from P1.1, `pack validate` non-zero. R-4: zero hits on the bundled packs with the tools present, first (0 of 54 controls skip). |
| `NoLeakedProvenance` prunes any subdirectory holding a `.git` entry (file or directory), and scans **every file that decodes as UTF-8**, skipping binaries by a NUL byte. | A linked worktree inside the repo carries a second `docs/ORIGINS.md` and turns the test red; the site template's JS is never scanned (`tests/test_packs.py:281-285`; S-65); any extension allow-list still leaves tracked text unscanned (23 files against today's list; 13 even with `.js .html .css .yml .sh` added — 4 `.mo`, 2 `.csv`, 5 `.gitignore`, `LICENSE`, `py.typed`; the other 5 unscanned files are binary `.stl`), and each phase adds types. | `V:` a temp tree with a `.git` *file* and a forbidden word built from `FORBIDDEN` at runtime (never typed) is skipped, and the same tree without `.git` is caught. `V:` the same runtime-built word in a `.mo` and in an extensionless file is caught. R-4: zero new hits in the tracked tree. |
| **One test environment**, `tests/_env.py`: a base class and a `run()` helper through which every test runs git and atompipe subprocesses — temp `HOME`, `ATOMPIPE_PACK_PATH` unset, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, explicit `GIT_AUTHOR_*`/`GIT_COMMITTER_*` only where a test wants an identity, `CLAUDE*`/`AI_AGENT` stripped unless the test sets them, and `PYTHONUSERBASE` pinned to the real user base. | Tests that pass on the dev machine and fail in CI, or the reverse: the dev machine has a global git identity and may have `~/.atompipe/packs`; the Bash tool sets `CLAUDE_CODE_CHILD_SESSION`, `CLAUDECODE`, `AI_AGENT` and `CLAUDE_CODE_SESSION_ID`, and GitHub runners set neither. `git clone --depth 1 <local path>` ignores `--depth`. A temp `HOME` alone drops the user site-packages, where trimesh and numpy live on this machine, so every mesh gate would read availability-skipped — honest-looking, and every subprocess test of those packs vacuous. | `V:` from P2.5, when the channel tests exist, `tests/test_meta.py` runs them with and without those variables in the parent env and gets the same results; an AST check finds no `subprocess` call in `tests/` outside `run()`; shallow clones use `file://`. `V:` `availability()` of every bundled gate is identical in-process, through `run()`, and, from P3.1, in an evaluation child. |
| `claims.explaining_verdict(claim, verdicts)`: the single ranked choice (ran-and-failed, then errored, then skipped), used by `cli._blocking_reason` and `report._terminal_reason`. | `status` cites a skipped pack gate as the reason a claim fails while `check` cites the real failure (`report.py:1131-1134` vs `cli.py:1075-1112`; S-68). | `V:` a fixture with a skip and a fail, in both orders: status, check and the report cite the failing gate. |
| `check`'s BLOCKING tag uses `report.status_tag` (`cli.py:1071`). | Three spellings of one status (S-69). | `V:` for each blocking status, the tag equals `status_tag`. |
| `util.atomic_write_json` uses `allow_nan=False` (`util.py:227` uses the json default) and raises `AtompipeError` naming the path. | NaN reaches `state.json` and the page advises `site build`, which cannot fix it (S-47). The P1 cache and index writers must emit strict JSON. | `V:` writing NaN raises naming the path. `C:` every existing writer stays green. |
| `tests/test_meta.py::EveryInvariantHasItsTest` (R-7). | An implementer renames or skips an invariant class to go green. | Maps CLAUDE.md numbers to classes; an AST check refuses `skip*`/`expectedFailure` in them. |
| `CLAUDE.md:19` reads "every test, must stay green"; the number is dropped. `CLAUDE.md:76`'s "14 stdlib-only modules" loses its count in the same change (this plan takes it to 21). | A typed count drifted from 32 to 92 unnoticed (S-84) — a duplicated constant is a misalignment with a date on it (rule 2). | — |

#### Checkpoint 1.1: make the done criteria mean something

- **JUnit.** `report.render_junit(ledger, verdicts, registry, *, tier, ready, exit_code,
  when)` with `xml.etree.ElementTree` (stdlib). A sanitiser replaces code points illegal in
  XML 1.0 with visible `\xNN` text, because ElementTree alone emits ill-formed XML for
  `\x1b`, `\x00`, `\x0c` and lone surrogates (slice probe). The target file is unlinked
  when the sweep starts and written atomically at the end, so a crashed sweep never leaves
  an older all-green file for CI to read. Default path `.atompipe/out/junit.xml`
  (already ignored). `check --junit [PATH]` and `gate selftest --junit [PATH]`.

  ```xml
  <testsuites>
    <properties><property name="spine_version" value="…"/><property name="exit_code" value="1"/>…</properties>
    <testsuite name="gates">           <!-- one testcase per REGISTERED gate, so `tests` is stable -->
      <testcase classname="project" name="bracket.deflection" time="0.0004">
        <failure type="fail" message="0.700 mm at 15 N (limit 0.5 mm)">measured 0.6997 mm vs limit 0.5</failure>
      </testcase>
      <testcase classname="pack.cad-solid" name="cad.watertight"><skipped message="requires python trimesh (not importable)"/></testcase>
      <!-- error -> <error type="error">; not admitted -> <error type="not-admitted">;
           above the tier ceiling or excluded by --only -> <skipped message="not run: …"/> -->
    </testsuite>
    <testsuite name="claims.critical"> <!-- each testcase asserts "does not block the spend" -->
      <testcase name="C1"><failure type="fail" message="bracket.deflection: 0.700 mm …"/></testcase>
      <testcase name="C5"><skipped message="needs a real part"/></testcase>
      <!-- PASS with a covering gate that did not pass (PARTIAL, until P2's Kleene rule):
           <skipped message="partial: g.solve requires openfoam"/> — never childless -->
    </testsuite>
    <testsuite name="claims.not-critical">…</testsuite>   <!-- FAIL/REFUTED -> <failure>; other non-pass -> <skipped> -->
  </testsuites>
  ```

- **The project marker.** `store.find_root` treats a directory as a project only if it
  holds `.atompipe/project.json` (or a legacy `.atompipe/ledger.json`), and stops at the
  first `.git` entry (S-64, brought forward from P3.1). `~/.atompipe/` is the user-pack
  home; as a marker it made every directory under `~` a project on a pack author's
  machine, so pack mode, the Stop hook's fast exit and `/start`'s `init` were unreachable.
- **Pack-mode selftest.** When `store.find_root()` is None, `gate selftest` runs pack mode:
  `--pack NAME|DIR`, else every **bundled** pack (and, inside a project, its own
  `.atompipe/packs/`); `--user-packs` adds `ATOMPIPE_PACK_PATH` and `~/.atompipe/packs`,
  so the host machine cannot change what G3 tests (S-87). It exits 1 when zero controls
  ran unless `--allow-empty` (today it exits 0, `cli.py:1772-1774`). Nothing is persisted
  in pack mode (Q1.8).
- **`packs.demonstrate(pack_dir) -> problems`**, per gate: it passes its own
  `baseline.json` or skips only for missing tooling (reported); its control fires, with a
  skip allowed only when `availability(spec)` fails (P1.0); the seal probe from
  `ControlsAreSealed` holds (the control fires against an empty host projection). Every
  baseline and control run gets an explicit temp `out_dir`, and openmodelica's
  `selftest/.generated/` is routed there too: today `tests/test_packs.py:132, 254` point
  `out_dir` into the pack, the fdm fixtures export meshes there
  (`packs/fdm-print/selftest/bad_meshes.py:205-206`), and openmodelica writes
  `.generated/` on every run (`packs/openmodelica/selftest/bad_modelica.py:61-65,
  141-157`) — into site-packages on a wheel install. `packs.validate` calls it at tier ≤ 1
  by default (G2). `NegativeControlsFire` and `ControlsAreSealed` **keep their own
  assertions**, at tier 3 as today (`tests/test_packs.py:132, 254`), as an independent
  oracle (D-25). `V: DemonstrateAgrees`: `packs.demonstrate(…, tier=Tier.EXTERNAL)`
  reports a problem on exactly the gates the two classes fail, on the bundled packs and on
  each planted violator, and excludes no gate by tier — openmodelica's three `Tier.SOLVE`
  gates included.
- **CI.** Remove every `|| true`. Run the bracket in a *temp copy*; assert exit code 1 and a
  JUnit failure set equal to `tests/expected_bracket.json`, with zero `<error>`. Run
  `gate selftest --junit` at the repo root (packs) and in the bracket copy. Drop
  `init … || true` and `model --set-entry` from CI (`.github/workflows/ci.yml:74-75`).
- **Docs that print commands that do not work.** Fix `CLAUDE.md:96-99`,
  `CONTRIBUTING.md:43-45`, `docs/PACK_FORMAT.md:488-500` (line 492 says CI runs
  `gate selftest` over every pack; it does not — S-11), `docs/EXTENSION_PROTOCOL.md:151-156`
  and `skills/pack-authoring/SKILL.md:29, 213-216`. `pack new` and `pack export` do not
  exist: strike them and name `gate selftest --pack <name> --junit <file>` as the
  admission evidence (S-10). Adding either command is refused (D-24).

| Change | Failure it could introduce | Test |
|---|---|---|
| JUnit | A skipped verdict with `passed=True` renders childless; the XML and exit code drift apart in the generous direction. | `V: test_junit`: a childless testcase iff the outcome is pass; a skip with `passed=True` is never childless; `claims.critical` failures + errors == `len(blocking())` (+1 at zero claims), and exit 1 iff that count > 0; the illegal-character fixture parses; a stale all-pass `junit.xml` plus a sweep that raises leaves no green file. |
| Pack-mode selftest | Runs zero controls and reports success. | `V:` an empty pack path → exit 1. `V:` a copied pack with a planted `return True` → exit 1 naming the gate. `V:` a planted `return False` → "fails its own baseline". |
| `demonstrate` in validate | `pack validate` becomes slow or tool-dependent. | Tier ≤ 1 by default; a missing-tool skip is reported, not failed; the CI step times it. |
| CI | The bracket's failure disappears unnoticed (a regression that makes deflection pass). | `V: tests/test_ci_config.py` refuses `\|\| true` on any `atompipe` line. The failure-signature script exits non-zero on a bracket copy at thickness 8.0 — it detects a *missing* expected failure. |
| Docs | Commands printed in docs drift again. | `V: tests/test_docs_commands.py` parses every `atompipe <words>` against `build_parser()` from an **explicit** file list (README, CLAUDE, CONTRIBUTING, METHOD, `docs/{EXTENSION_PROTOCOL,PACK_FORMAT,SITE_CONTRACT,SPINE_CONTRACT}.md`, `skills/*/SKILL.md`, `packs/*/PACK.md`, `packs/*/references/*.md`). It never globs `docs/*.md`, so the user's untracked draft cannot affect the suite. |

#### Checkpoint 1.2: per-gate content-addressed verdicts, admission's home, no run history, no global staleness

New stdlib module **`src/atompipe/verdicts.py`** is the single home of `ABSENT`,
`digest_value`, `ParamTrace`, `TracedContext`, the audit-hook recorder, `code_digest`,
`spine_digest`, `rho`, entry write/read, `freshness`, and obs read/write.

**`src/atompipe/vcs.py`** lands here too, not in P3.1, because this checkpoint is the first
to call git (the control entry's `ls-files`, ages from an entry's commit time) and P2.5
the next (the signer's identity, the export commit): it is the only git edge — argv form,
the clean environment P3.1 spells out, a timeout, never raising — with `git_head`,
`ls_files`, `ident` and `commit_times`. P3.1 adds `snapshot` and the candidate
operations; P4's bylines use it. `V:` with `GIT_DIR` and `GIT_INDEX_FILE` pointing at a
foreign repository in the parent env, every git-reading command answers for the
project's own; an AST test finds no git subprocess outside `vcs.py`.

- **`ParamTrace(dict)`** replaces `cli._ParamReads` (`cli.py:494-574`). It records **tuple
  paths** at every depth — `("config","load_n")` — each with `digest(value)`; a miss
  records `(path, ABSENT)`, a tagged digest that cannot collide with JSON null;
  `__contains__` records the path. Bulk access records a whole-value dependency at that
  level: `__iter__`, `keys`, `values`, `items`, `__len__`, `__eq__`, `__repr__`, `copy`,
  `__reduce_ex__`, `__or__`, `__reversed__`. **Mutators raise** "a gate cannot write
  another gate's inputs": `ctx.params` is one mutable dict shared by every gate today
  (slice probe: gate B read gate A's write; S-24). No bundled *gate* writes `ctx.params`,
  but cad-solid's selftest fixtures and check scripts assign it on contexts they build
  (`packs/cad-solid/selftest/bad_meshes.py:236-553`, 13 assignments;
  `check_connectivity.py:260`), so the read-only view wraps only the params a gate
  function receives, never a fixture's own context. It stays a dict subclass so
  `isinstance` checks keep working.
- **Escape hatches.** Any touch of `ctx.model` adds a whole-projection plus model-source
  dependency. `ctx.ledger` is a traced copy with per-record dependencies, because
  openmodelica reads claim limits from it (`modelica.py:181-187, 256`; S-23).
  `ctx.extra` becomes a **per-gate copy**, and `GateContext.load_file(path, loader)` is a
  per-sweep memo that records the file dependency for the *calling* gate on a hit or a
  miss. fdm-print's cross-gate mesh cache migrates to it **in the same checkpoint**
  (`packs/fdm-print/gates/fdm_print_fold.py:276-331`), and `packs/fdm-print/PACK.md:340-347`
  is updated; otherwise its second gate's file read vanishes from ρ (S-27).
- **Files.** A process-wide `sys.addaudithook`, routed to the active trace only while a
  gate function runs (or a fixture and its gate run on a control input), records `open`
  in read mode, directory listings, and `subprocess.Popen` (the executable plus argv
  paths under the root). Excluded: files the gate itself wrote; import-machinery opens
  (`.pyc` and module source — code is the code digest's job); and paths under the
  interpreter's prefixes (`sys.prefix`, `sys.base_prefix`, site-packages — library
  internals, which `instruments` records as provenance, Q1.3). Those opens are
  interpreter- and machine-specific (`import fractions` alone opens eleven
  `cpython-312.pyc` files), so recording them would make an entry ABSENT-stale on every
  other machine. Any other path outside the project root and the pack dirs is named as an
  opaque channel, not digested. Digests are taken after the gate
  returns, behind an untracked stat cache (`.atompipe/cache/digests.json`) keyed on
  `(size, mtime_ns, ctime_ns, ino, dev)`, with a racy-clean rule: a file whose mtime is
  not strictly older than the cache write is re-hashed. (git compares against its index
  file's own mtime and has no fixed window. *Rejected:* size and mtime alone, which a
  same-size edit with its mtime restored defeats — `os.utime` cannot restore ctime; a
  fixed 2 s window, misattributed to git in an earlier draft.) Every entry names its
  **opaque** channels (a subprocess's own reads, C-level `fopen`, env) and never implies
  they were covered (§18): **an entry with any opaque channel is never Fresh** (below).
- **Code digest, per gate.** The module that defines the gate, plus every module file
  **compiled while it was imported** that lives under its pack dir or the project root,
  plus the `pack.json` fields that shape its spec. The closure is recorded, not parsed
  from `import` statements: fdm-print loads its helpers by path
  (`spec_from_file_location` beside `__file__`, `packs/fdm-print/gates/mesh.py:67-91`,
  `printability.py:34-67`), which a static walk misses, and a keyword rule
  ("imports dynamically") would send fdm-print, cad-solid (`solid.py:1116`, a `find_spec`
  probe) and openmodelica (`modelica.py:242`, `__import__("re")`) — 3 of 7 packs — to
  whole-pack digests. A helper found already in `sys.modules` (fdm-print caches its fold
  under one shared name) is attributed to every module that loads it. Only `exec` of
  computed source, or an import the recorder cannot resolve to a file, falls back to
  every `*.py` under the pack dir or `gates/**`, and the entry says so. *Rejected:* the
  whole pack dir or `gates/**` for every gate — one module edit re-runs every gate,
  over-invalidating by construction and breaking E4's gate-version rows. Gate modules
  **and the helpers they load by path** go through a fresh-compiling loader (reusing
  `modelio._FreshLoader`, `modelio.py:354`; fdm-print's `_sibling_module` calls the
  spine's path loader instead, in the same change as its mesh-cache migration), and the
  digest covers **the bytes compiled** — a same-size, same-second edit ran the old
  bytecode (slice repro: source said 8.0, the verdict came from 7.0; S-26).
- **Spine digest.** sha256 over a **version-independent canonical walk** of the
  verdict-path modules' ASTs — `models.py`, `gates.py`, `modelio.py`, `verdicts.py` —
  docstrings stripped: node type and fields in a fixed order, skipping empty lists,
  `None`, `type_params`, `type_comment` and `kind`. A fixture module's digest is pinned
  as a constant and asserted on every Python in the CI matrix. *Rejected:* `ast.dump`,
  whose output differs across 3.10, 3.12 and 3.13 (3.12 adds `type_params=[]`; 3.13 omits
  empty fields), so every committed entry would be stale on two of CI's three Pythons and
  each collaborator's interpreter would commit its own generation. *Rejected:* `tokenize`
  (layout-sensitive). *Rejected:* the version string — 1e09113 changed verdict semantics
  without a bump (S-29). *Rejected:* the bytes of the whole spine — every comment edit
  would re-run every gate and churn every tracked entry.
- **`Verdict`** gains `rho: str = ""` and `cpu_s: float = 0.0` as the **last** fields
  (`cpu_s` from the `os.times()` delta, children included). Positional construction stays
  compatible; an old spine drops them harmlessly (R-2).
- **openmodelica's `compiles` and `simulates` stop writing `in {run.duration_s:.1f}s` into
  `detail`** (`packs/openmodelica/gates/modelica.py:1252`, `:1386`; D-29, S-34).
  `duration_s` already carries the time.

**Cache entry** (tracked): `.atompipe/verdicts/<gate-id>/<rho16>-<out8>.json`, written with
`O_EXCL`, never rewritten. Only verdicts that **ran and passed or failed** are cached;
skips and errors never are. They are still *remembered*: untracked
`.atompipe/cache/last_outcomes.json` keeps each gate's latest non-cacheable outcome keyed
by its ρ, so `status`, the report and the site show a crash as errored and a self-skip as
skipped — never as never run (S-68 again) — and nothing serves it as evidence. A
remembered outcome also **supersedes** a cached entry at the same ρ: when `check --force`
turns a cached PASS into an error or a skip, the next plain `check` resolves that error or
skip, not the older PASS, until a run at that ρ passes or fails again and clears it
(invariant 2: a crash proves nothing, and neither does a pass it followed). Gate ids containing `/`, `\`, `..` or `:` are refused at
registration (`gates.py:537-543` checks only emptiness and whitespace).

```json
{
  "schema": 1,
  "gate": "bracket.deflection",
  "rho": "<64 hex>",
  "code": {"digest": "<64 hex>", "files": ["gates/structural.py"]},
  "spine": "<64 hex>",
  "reads": {
    "params": [[["config", "load_n"], "<sha>", 15.0], [["deflection"], "<sha>", 0.6997]],
    "files": {}, "dirs": {}, "ledger": {}, "opaque": []
  },
  "instruments": {},
  "verdict": {"passed": false, "measured": 0.6997, "limit": 0.5, "units": "mm",
              "detail": "0.700 mm at 15 N (limit 0.5 mm)", "evidence": [], "locators": [],
              "claims": ["structural", "stiffness", "deflection"], "tier": 0, "pack": ""},
  "digest": "<sha256 over canonical JSON of every other field>"
}
```

Small scalar values sit next to their digests so `why stale` can print
`config.bed_xy 220.0 -> 250.0`. Paths are JSON lists because flat keys such as
`fdm.bbox_mm` contain dots. `out8` digests the outcome tuple (passed, measured, limit,
units), so two outcomes for one ρ are two files; `digest` catches a hand edit of any
field. Both are *integrity* checks, not a forgery defence — R-9 is the forgery defence.
`instruments` records third-party module versions seen by the import audit event as
provenance, never as part of ρ (Q1.3): a Fresh entry whose instruments differ from this
machine's stays Fresh and carries a note — `recorded under trimesh <a>; here <b>` — in
`freshness`, `status` and `doctor` (M11.5 records the decline).

**Control entry** (tracked): `.atompipe/verdicts/<gate-id>/control-<rhoC16>-<out8>.json`,
body `{"schema": 1, "kind": "control", "bad": "fail", "good": null, "admitted":
"reject-only", "detail": "…", "measured": …, "limit": …, "digest": "…"}`. ρ_control
hashes the gate's code digest; **every source file under the pack's (or project's)
`selftest/`** — git-tracked plus untracked-not-ignored, and outside git everything except
`__pycache__/`, `*.py[cod]` and dot-directories (openmodelica's `.generated/`), never
bytecode, whose header embeds an mtime and the interpreter's name; every other file opened while the fixture and the gate run on the control
(openmodelica's `assets/bad/*.mo`, the cad and fdm meshes, a `baseline.json` a sealed
fixture loads into a plain, untraced dict); the values the gate read from the control
context, through the same trace; the NegativeControl fields; the spine digest. P2 fills
`good`.

**D-27 lands here, not in P2.** The bracket ships `selftest/known_good.py` (thickness
8.0: 0.469 mm, all six pass; every Config field stated, so a model default edit does not
move it; from P2.4 it also carries the acceptance its controls are calibrated against),
and `bad_configs._with` rebuilds from it instead of from the host's
`ctx.params["config"]` (`examples/bracket/selftest/bad_configs.py:25-31`). That read was a
whole-value dependency on the live config: every Config edit — the transcript's `bed_xy`
— would change all six ρ_control, re-run all six controls and write six tracked files.
P2.3 reuses `known_good.py` as the good half.

**Obs** (untracked, `.atompipe/obs/<gate-id>.json`): `{"runs": [{"entry": "<rho16>-<out8>",
"when": "<edge clock>", "duration_s": 0.0004, "cpu_s": 0.0004}]}`, the last 20 executions.
20 covers a working session; 5 was rejected as too few for a median; unbounded rebuilds
the run history the brief removes. Written only at the CLI edge, which owns the clock.

**`last_check.json`** (untracked, `.atompipe/cache/last_check.json`, overwritten by every
full `check`, and from P2/P3 by the signing and `/pick` write paths, which re-resolve
statuses from the cache without running a gate): `{"when", "spine", "fingerprint": <sha
over WATCHED sources>, "reads": {gate: {path: digest}}, "statuses": {claim: status},
"counts", "worst": {"claim", "gate", "detail"}, "params": {}, "influence": {}}` (`params`,
the parameter view as the check saw it, filled from P1.3; `influence` from P3). It is what P3's `status --short` and Stop hook read so that neither imports `gates/`
or runs project code in-process (D-20); `reads` is what lets them say which checks a
change touched. WATCHED — the
record dirs, the verdict cache, `model/**`, `gates/**`, `selftest/**`,
`.atompipe/project.json`, `.atompipe/packs/**`, `objectives.json`, the union of the
`reads.files`/`reads.dirs` of the entries the last check used (a gate's `cad/part.stl`),
and the spine and pack digests — is exported by one function in `verdicts.py`, which P3's
hook imports.

**Freshness** — `verdicts.freshness(root, registry, projection, ledger) -> {gate:
Fresh(entry) | Stale(entry, reasons) | Unknown(reason) | Never}` — **never runs a gate**.
It groups entries by read-path signature, recomputes ρ from current digests, and looks the
entry up. A model that does not import makes every model-reading entry **Unknown**, which
resolves like stale, never fresh (closes `cli.py:407`; S-21). So does an entry that
recorded an opaque channel — "opaque inputs: <channels>" — which `check` re-runs and which
never reaches PASS or PROVEN: an input the tracer cannot see can change unseen, and
serving the cached PASS then is exactly the lie invariant 7 forbids. `claims.resolve_status`,
`statuses`, `blocking` and `summarise` take `stale_gates: Collection[str]`;
**`stale=True` is kept as the all-gates alias, so
`StatusPrecedence.test_stale_is_not_pass` is untouched** (R-6). `cli._staleness`,
`site._staleness` and both `_flat_params` copies are deleted; `modelio.flat_params` is the
one copy (S-28). A legacy ledger verdict with no ρ resolves STALE ("recorded before
per-gate tracing").

**`check`** runs only the affected gates. A Fresh FAIL entry is a cache hit and is not
run; a Fresh **PASS** entry is one only if `gates.availability(spec)` holds on this
machine and no later outcome at that ρ supersedes it (above). Otherwise a gate whose tool
is missing here resolves skipped — `cached pass exists; requires python trimesh (not
importable here)` — and its claim BLOCKED, so a PASS committed from a machine with trimesh
or omc never reads PASS on one without (invariant 1; CI installs neither, §10). `--force`
ignores the cache; on a control-entry miss it runs the control first,
within the tier ceiling, and a gate **not admitted gets `error="not admitted: <why>"` and
its function is never called** (D-07). JSON keeps the stable keys (§4.0.3) and adds
`outcome`, `cached`, `rho`, `fresh`, `stale_reason`; counts keep `ran` as today (verdicts
that passed, `cli.py:997`) and add `{executed, cached}` beside `failed`, `skipped`,
`errored`, with control runs counted apart as `controls: {executed, cached}`. A fresh cached FAIL still blocks. `--only` loses all last-run bookkeeping
(`cli.py:915-920, 989-990`). `check --no-record` and `gate selftest --no-record`, the
documented dry sweep (`cli.py:3260, 3413`), stay and now mean: write no cache, control or
obs entry and no `last_check.json`, and migrate only in memory — nothing global is
compared any more, so S-32's false STALE cannot recur. `V:` after a `--no-record` check,
`.atompipe/verdicts/`, `.atompipe/obs/` and `last_check.json` are byte-identical.

**Removed:** `store.record_run`, `load_runs`, `runs_dir`, `RUNS_NAME`, `_RUN_FILE_RE`, the
`!runs/` gitignore line, `models.RunMeta`, `Ledger.last_run` (old files still load:
`from_dict` ignores the key), and `model_hash`/`inputs_hash` as staleness drivers (both stay
as display ids). Site ages come from obs `when`, else the entry's git commit time, else
null — never 0 (`site.py:1836-1865`). The report drops sweep timestamps; its heading "What
is PROVEN (machine-verified this run)" becomes "(machine-verified, current)", since a
cached verdict is current but not "this run"; the `SECTION_PROVEN` prefix is unchanged.

**Param attribution.** A gate that did not execute (availability skip; in P2, blocked)
keeps the read set of its last executed entry. That set feeds `why`, never the cache key.
Today an availability skip erases it on every full sweep (`cli.py:587-590` vs
`gates.py:1066-1072`; S-30).

| Change | Failure it could introduce | Test |
|---|---|---|
| Per-gate ρ | **False-fresh**: an unrecorded channel keeps a PASS current after its input moved — the staleness lie, and the worst failure in the phase. | `V: StaleIsNotCurrent`, one scenario per reproduced defect: the first filtered sweep never goes stale (S-20); a broken model reads fresh (S-21); a data file edited in place with its mtime restored (S-22) — backdated beyond any racy window, so only the stat key can catch it, with the racy case (an edit in the same timestamp tick as the cache write) a test of its own; a gate whose subprocess reads a file not in its argv, which is then edited; a claim record the gate read is edited (S-23); bulk readers (`dict()`, `{**}`, `json.dumps`, `items()`, `repr`, `deepcopy`, `f(**p)`, top-level and nested; S-25); a missing key appears; state shared through `extra` (S-27); a helper module edited; a hand-edited entry; a same-second pyc edit (S-26). Each asserts the claim is not PASS and not under PROVEN. |
| Exact localisation (E4) | Over-invalidation is only a cost, but it breaks E4. | `tests/test_staleness.py` on a temp copy of the bracket with `gates.REGISTRY` isolated. For each of the 12 Config fields change one, then assert `stale == expected AND fresh == ALL - expected`, that `check` ran exactly `expected` and no control (controls are counted apart, and D-27 keeps them off the live config), and that `check --force` outcomes equal the affected-only outcomes (soundness). The table: arm_length → {deflection, bending_stress, model_validity, bed_fit}; width → {deflection, bending_stress, bed_fit}; thickness → all six; hole_d → {bearing, bed_fit}; n_bolts → {bearing}; edge_margin → {bed_fit}; load_n → {deflection, bending_stress, bearing}; safety_factor → {bending_stress, bearing}; material → {deflection, bending_stress, bearing}; nozzle_d → {min_wall}; bed_xy → {bed_fit}; brim_mm → {bed_fit}. Early-cutoff row: arm_length 120 with thickness 14 leaves deflection and model_validity fresh (L/t and L³/t³ unchanged — the `build()` precondition is asserted first). Claim level for bed_xy: C4 STALE, C2/C3 PASS, C1 FAIL. Revert gives all Fresh with zero runs. The table is re-derived in the test file from `gates/structural.py` reads × `build()`. Gate-version rows: editing one module of a multi-module pack (fdm-print's `mesh.py`) stales exactly the gates defined in it or importing it, and editing `_process_model.py` — loaded by path, so only the recorded closure sees it — stales exactly `printability.py`'s gates, never all seven; editing `fdm_print_fold.py`, shared through `sys.modules`, stales the gates of both modules that load it. The table is exact relative to the declared ρ (M11.5). |
| Spine digest | It differs across CI's Pythons, so the committed cache is stale everywhere but one. | `V: SpineDigestIsPortable`: the pinned fixture digest is asserted on 3.10, 3.12 and 3.13; a comment or docstring edit leaves it unchanged; a changed comparison changes it. |
| Deterministic entries | Wall-clock or other volatile text makes two cold runs write different bytes for one ρ: add/add conflicts across branches (D-29). | `V: EntriesAreDeterministic`: two cold runs of every bundled baseline (omc present) give byte-identical entries. R-4 for the next row. |
| Two outcomes for one ρ | Nondeterminism, or a merged hand edit. | `V:` two files with different `out8` for one ρ and equal `instruments` resolve as **error** "two outcomes recorded for identical inputs", `doctor` FAILs, and the claim FAILs. With different `instruments` (another numpy or trimesh, merged from another machine) it is not an error: `doctor` warns "outcome differs across instruments", the local re-run wins, and both files stay. A same-name entry with different bytes keeps the first and warns "nondeterministic detail". *Rejected:* "worse outcome wins", which silently picks. |
| Cached skip, and a cached PASS past what superseded it | A skip replayed as evidence; a crash forgotten; a committed PASS served where its tool is missing, or after the same inputs errored. | `V:` a `requires_python`-missing gate writes no entry and is re-attempted by the next check. `V:` a gate that crashes reads errored in `status`, the report and `state.json` — never "not yet checked" — through `last_outcomes.json`. `V:` a Fresh PASS entry for a `requires_python` gate, checked with availability patched to fail, resolves skipped with "cached pass exists; … not importable here", its claim BLOCKED and not under PROVEN. `V:` `check --force` with the gate patched to raise at an unchanged ρ, then a plain `check`, resolves errored, not the cached PASS; a later passing run clears it. `V: InstrumentMismatchIsNoted`: an entry recorded under another numpy version stays Fresh and `status` and `doctor` print the note. |
| Admission at sweep | A gate whose control fails at its current version still counts; or admission blocks honest gates. | `V: AdmissionIsDemonstrated`: an honest gate is admitted; editing its fixture into a no-op gives "not admitted" on the next check *even though the verdict cache would hit*; an always-True gate with an existing fixture never yields PASS through `check`; editing a `.mo`, an `.stl` or a `baseline.json` value misses the control entry and re-runs it; an identity fixture for `bracket.deflection` is not admitted, because it passes on the known-good design (D-27, S-07); a `git ls-files` copy of the bracket, checked on 3.10 and on 3.13 after its `selftest/__pycache__` exists, writes no new control entry. `C:` all 54 + 6 bundled controls fire (the R-4 measurement). `C:` `run_gate` on a spec with fixture `"x:y"` still returns its raw verdict, so the invariant tests at `tests/test_invariants.py:74-220` are untouched. |
| `gate show` | Still reads a key nothing writes. | `V:` after `gate selftest`, `gate show --json` has a non-null `last_selftest` (S-08). |
| Per-gate `extra` copy | fdm-print's documented load count changes or its read vanishes from ρ. | `V:` gate A loads a file into the memo and gate B uses it; edit the file and both go stale. `C:` fdm-print's gate-on-the-gates stays green. |
| Read-only `ctx.params` | A bundled fixture or gate that mutates in place turns red. | R-4: no bundled gate writes it; cad-solid's fixtures and check scripts do, on contexts they build, and keep working because the wrapper is applied only at the gate call. `C:` the full pack suite, cad-solid's `selftest/check_*.py` scripts included, stays green. |
| Obs and ages | Ages silently fall back to 0 when runs/ goes. | `V:` a verdict with no obs and no git renders `age_s: null`, never 0. |
| Measured cost | A cache hit replays a duration as a new measurement. | `V: CostIsKept`: a gate that sleeps 50 ms has `duration_s >= 0.05`; a second, cached check leaves the entry and the obs byte-identical, and its JSON row says `cached: true`. |
| `last_check.json` | It is written into the tracked tree, read as truth by `check`, or blind to an input ρ covers. | `V:` it lives under ignored `.atompipe/cache/`; `check` never reads it. `V:` editing `.atompipe/project.json`, a file under `.atompipe/packs/`, `objectives.json`, or a file a gate opened outside the record dirs each moves the WATCHED fingerprint. |
| Unsealed pack fixtures | A fixture reading host `ctx.params` defuses its control in some repositories (invariant 5). | P1.2 lands the **detector** (`doctor` warning) per §4.0.4 item 2, measured against a planted violator — a scratch pack fixture that layers its bad value over the host context's `ctx.params` — and zero hits on the 54 bundled fixtures. Then, in the same phase (R-4), `ControlsAreSealed` asserts that no fixture's trace reads a key of the host context's `ctx.params`: `V:` the planted violator turns it red and `pack validate` non-zero. |

#### Checkpoint 1.3: records as files, generated index, migration

**Layout** (relative to the project root):

```
.atompipe/project.json     TRACKED  {"schema": 2, "name", "summary", "created", "revision",
                                      "model_entry", "packs": [], "spine_version"}
.atompipe/.gitignore       TRACKED  deny-list: ledger.json, ledger.legacy.json, obs/, cache/,
                                      out/, export/, runs/, *.tmp, *.lock   (never an allow-list:
                                      .atompipe/packs/ is source and model.json is reviewed)
.gitignore                 TRACKED  a marked atompipe block: __pycache__/, *.py[cod] (P2.5 adds /REPORT.md)
.gitattributes             TRACKED  * text=auto eol=lf; binary kinds -text (*.stl *.step *.glb *.png *.jpg)
.atompipe/ledger.json      IGNORED  the generated index (below)
.atompipe/verdicts/**      TRACKED  cache and control entries (1.2)
claims/<id>.json           TRACKED  one Claim; filename stem IS the id; no "gates" key
params/<name>.json         TRACKED  SPARSE: only provenance the model cannot hold (source, grounded_by, tags)
decisions/<slug>.json      TRACKED  one Decision
needs/<id>.json            TRACKED  SPARSE: only an enriched Need
inputs/<id>.json           TRACKED  one InputArtifact, links included (bytes stay in inputs/<bucket>/)
results/<claim-id>.json    TRACKED  the claim's PhysicalResults, append-only (D-11, Q2.11; P2 extends the fields)
views/<id>.json            TRACKED  declared views, beside the viewgens views/*.py (cli.py:98-102)
```

`views/` already holds executable viewgens, so the two coexist by extension: the record
reader and the index read only `views/*.json`, the viewgen loader and the code digest only
`views/*.py`, and each excludes the other's files. Their permission classes are set
separately (P3.4).

The bracket after migration:

```jsonc
// claims/C1.json
{"statement": "Tip sags no more than 0.5 mm at rated load", "kind": "measurable",
 "acceptance": {"quantity": "tip deflection", "comparator": "<=", "limit": 0.5, "units": "mm"},
 "rationale": "past ~0.5 mm the droop is visible against a level shelf edge; this is a product decision, not a physics one",
 "tags": ["stiffness"]}
```

Migration and every shim write a record in its dataclass field order, omitting the `id`
(the stem is the id) and every field at its default (C1's `critical: true`, `note: ""`),
indent 2, trailing newline, so a second migration changes no byte; the reader accepts any
key order and reads an absent field as its default. A claim's authored test procedure is
its `note`, which `report._physical_test` already reads — the "procedure note" of P2.5 and
the test card's "authored procedure" (P4.3); no new field.

`model/bracket.py` gains a `PARAMS` list next to the values (D-30), in the shape
`modelio._explicit_params` already reads (`modelio.py:645-693`), carrying one `Rejected`
per parameter: thickness 4.0 mm ("3.75 mm deflection, 7.5x the limit"), arm_length 80 mm
("1.7 mm deflection, over 3x the limit"), hole_d 5.0 mm ("line-to-line fit an FDM hole
will not hold").

The three rejections are the ones the Config docstrings already name
(`examples/bracket/model/bracket.py:58-80`); the docstrings keep *why this value* and
point at PARAMS for *what lost*. The thickness docstring's "~5x" is wrong —
`build(Config(thickness=4.0))` gives 3.75 mm against 0.5 — and is corrected in the same
commit.

**Reader** (`store._read_record`) is **strict** for record files; the in-memory
`Record.from_dict` stays lenient. It refuses an unknown key (with a `difflib`
suggestion), a duplicate JSON key (`object_pairs_hook`), NaN/Infinity (`parse_constant`),
an empty file, a non-object, a stem that disagrees with an internal `id`, and two ids that
differ only in case. Per-kind forbidden keys are refused with their owner named:
`Claim.gates`, `Param.value`/`derived_from`/`gates`/`changed_in`, `InputArtifact.bytes`,
and `independence` anywhere.

**Index** = `build_index(root)`: a pure function of the record files plus the computed
digests of input bytes; no clock, model or registry. Contents: `{"generated": "<banner:
an output of claims/ params/ decisions/ needs/ inputs/ results/ views/
.atompipe/project.json; edit those, never this>", "schema": 2, "records_digest", "meta",
"claims", "params", "decisions", "needs", "inputs": [{…, "sha256": <computed>, "pinned",
"drift", "exists"}], "results", "unregistered_inputs", "problems"}`. It never contains
statuses, coverage or verdicts, which keeps the agreement test exact; statuses, counts and
the parameter view (values with their rejections, `modelio.param_view`) sit in its named
sibling `.atompipe/cache/last_check.json` (1.2), and the skill points the agent at the
two: the whole project in two reads (D-06). It is rebuilt by every command and, from P3.4,
by the Stop and PostToolUse(Edit|Write|NotebookEdit) hooks whenever a record file moved —
on a migrated project only; on a legacy one they write nothing (invariant 8) — so the
agent's own edit is in it when it next reads it. It is rewritten only when its content
changes, best-effort (a read-only filesystem warns). `doctor` never writes it. **No spine
code reads `ledger.json` for truth.**

**Ownership.** The model owns `value`, `derived_from`, `units`, `rationale` and its own
`PARAMS` rejections. A param record owns `source`, `grounded_by`, `tags`, and may
carry `rejected`, `units` or `rationale` only where the model states none. `gates` and
`changed_in` are derived. `modelio.param_view(records, model)` replaces the mutating
`sync_params` (`modelio.py:822`) and unions rejections from both homes, tagged with their
origin. When the model does not load, value is None and renderers say "model does not
load: …", never a cached number.

**Commands.** `check`, `gap` and `model` stop writing records (`cli.py:937-991,
1536-1547, 2133-2171`). Shims each write exactly one file under the lock, with the clock
read at the CLI edge:

- `ingest` — permanent: it moves bytes, dedupes by content and pins sha256;
- `claim physical` — kept; writes `results/<id>.json`; becomes the signing channel in P2;
- `extract`, `decide`, `packs add` — shims until A-5–A-7; the `decide` shim loses
  `--when`, because the edge stamps the time (`cli.py:3430`; S-44).

**`init`** writes `project.json`, the deny-list `.gitignore`, empty record dirs, and the
project-root `.gitignore` and `.gitattributes` blocks — importing `model/`, `gates/` and
`selftest/` writes bytecode beside the project, and a project outside this repo has no
root ignore to inherit; digests are over bytes, and a Windows clone with
`core.autocrlf=true` would check every source out CRLF, stale every entry and dirty the
tree — never a legacy ledger (`store.py:86-102, 360-361`); otherwise every new project, including
`/start`'s empty-directory case, is born legacy and auto-migrated with a spurious `git rm`
notice. `V:` `init` then `status` prints no migration notice and creates no
`ledger.legacy.json`.

Removed, with their strings rewritten in the same change (none is referenced by the
skill or a pack; A-8): `claim add`, `claim edit`, `packs remove`, and the
`model --set-entry` flag (`project.json` owns the entry). The kept `claim physical`'s
refusal (`cli.py:1489`) stops naming `claim edit` and names the file edit instead
(`"kind": "physical"` in `claims/<id>.json`).

**Migration**, `store.migrate_legacy(root)`:

| Situation | Behaviour | Test (`test_records.LegacyLedgerMigrates`) |
|---|---|---|
| Legacy `ledger.json` (no `generated` key), no `project.json`, no record dirs. | Migrate on the first `check`, `start` or shim, under the build lock; every other command, and every hook, runs the same pure function in memory, writes nothing, and says "will migrate on next check". A **pure function of the legacy file**: it never loads the model, so it can be resumed and tested. Record files are written first; **`project.json` is written last as the commit marker**; `ledger.json` is renamed `ledger.legacy.json`, never deleted. `claim.physical_result` moves to `results/<id>.json`. One notice prints the `git rm --cached` line; the spine does not run git. | Every rejected alternative, extraction, decision, need enrichment and physical result survives field for field. `gates`, `value`, verdicts and `last_run` appear in no record. |
| `.atompipe/.gitignore` as `init` wrote it (`out/ *.tmp *.lock !ledger.json !runs/`, `store.py:86-102`). | Rewritten, idempotently, **before** `project.json` is written: the deny-list goes inside a marked `# atompipe:begin` … `# atompipe:end` block, and user lines outside it are kept. Otherwise `!ledger.json` re-adds the index on the next `git add -A` (undoing D-06), and `cache/` and `obs/` show as untracked (G5). The project-root `.gitignore` and `.gitattributes` blocks are ensured the same way. | `V:` after migration and `check`, `git status --porcelain` lists only record files and new cache entries; a second migration changes no byte; a user line outside the block survives. |
| Record dirs present, no `project.json`, legacy file present (a migration that crashed). | Re-derive; if byte-identical, complete it; otherwise refuse, naming both. | `V:` kill after half the files: the next load completes. `V:` a hand-edited half-migrated record → refusal naming it. |
| `project.json` present, `ledger.json` of any shape, including one rewritten by an **older spine**. | `ledger.json` is output: overwritten, never read. | `V:` an old-shape ledger holding a forged PASS changes no status. |
| Legacy verdicts. | **Dropped** (D-09). Claims read PENDING until the first check. | `V:` no migrated legacy verdict can make a claim PASS. |
| An unknown or misspelled key in the legacy file (e.g. `rejectd`). | **Refuse**, naming file, record, key and suggestion. Today such a key is dropped on load and erased by the next save (S-40); a one-time manual fix beats losing the highest-value field. | `V:` `rejectd` → refusal, nothing written. |
| `runs/`. | Left untouched and no longer read; `doctor` warns. | — |
| `project.json` `schema` newer than this spine. | Refuse. Spines older than this plan cannot be guarded; the residual risk is named in the README. | `V:` `schema: 99` → refusal. |
| `doctor` on a legacy project. | Writes nothing; says "will migrate on next command". | `V:` byte snapshot of the whole tree. |

- **Site staleness** uses the index's `records_digest`, not mtime (`cli.py:2451-2463`); the
  mtime rule is meaningless once the index is regenerated on load.
- **The bracket**: commit `.atompipe/project.json`, its own root `.gitignore` and
  `.gitattributes`, `claims/C1..C7.json`, the `PARAMS` list,
  and the six cache entries and six control entries at the post-commit spine digest
  (entries from earlier digests are outputs and are removed in the phase commit);
  `git rm --cached .atompipe/ledger.json`; delete `runs/0001-0005`; regenerate
  `docs/readiness.md`, which has drifted from its own ledger (S-41); leave thickness at 7.0.

| Change | Failure it could introduce | Test |
|---|---|---|
| Generated index | The index and the records disagree; a hand edit to the index becomes a second source (rule 1). | `V: IndexNeverDisagreesWithRecords`: hand-edit the index (C1 limit 5.0, an injected C99, a forged result) and the records win; a deleted, added, renamed or edited-in-place record is reflected; the build is deterministic under a reversed `os.listdir` and a clock patched to raise; every command leaves `agree()` true, and so, from P3.4, does a simulated agent Edit of `claims/C1.json` followed by the PostToolUse hook alone, with no command in between; the index's gitignore matches through `fnmatch`. |
| Commands stop writing records | A command writes a record it was not asked to (today `check` rewrites `claim.gates` and `Param.gates`; `gap` persists Needs — S-43). | `V: NoCommandWritesARecord`: snapshot the bytes of every record, run every non-shim command, assert byte-identical — on a migrated fixture, and on a legacy fixture where only `check` and `start` write, and only the migration's own files; the Stop hook on a legacy project writes nothing. An AST test finds no call to a whole-ledger writer in `cli.py`. Every path `check` writes is gitignored at the time of writing or is a new verdict entry, except the marked ignore blocks the ensure step writes once — stated as that property, not a path list, so P2.5's `/REPORT.md` block and P4.3's site rebuild add ignore rules first and never widen the test (§4.6); from P3.1 `ask --next` is held to the same property. |
| Line endings | A CRLF checkout changes every byte digest: every entry stale, the tree dirtied. | `V:` a `git -c core.autocrlf=true clone` of the bracket checks out LF, reads every committed entry Fresh, and leaves porcelain empty after `check`; an `.stl` round-trips byte-identical. |
| Strict reader | A tolerated typo becomes a hard stop with an unhelpful message. | `V:` every refusal names the file, the key and the suggestion. |
| Param ownership | `why` imports user code and crashes; or shows a cached number. | `V:` edit the model's thickness to 8.0 with no check: `why thickness` prints 8.0 (today 7; S-39). A record carrying `value` is refused naming `model/bracket.py`. A model that raises gives "model does not load: …" and no number. `V:` a PARAMS rejection added after the first check appears (S-38). |
| Input digests from bytes | Evidence edited in place never goes stale (S-22, S-45); the stat cache serves a stale digest. | `V:` rewrite an ingested file in place: the index digest changes, `drift` is true, and ρ changes. `V:` a poisoned stat cache inside the racy window is re-hashed. |
| Derived grounding | A deleted extraction keeps its grounding (S-36). | `V:` delete the extraction; `why arm_length` and `inputs` agree. |
| Removed commands | A string in the spine still tells users to run them. | `V: test_docs_commands` extended to string literals under `src/atompipe/**/*.py` and `site_template/**/*.js`. |

**Refuter targets for Phase 1.** Make a PASS survive an input change (find any channel ρ
does not see). Make the index disagree with the records. Get a skip or an error cached, or a cached PASS
served where its tool is missing or after it errored.
Make `check` write a tracked record. Make migration lose a field or wedge. Make JUnit
greener than the exit code. Make `gate selftest` exit 0 having run nothing. Make a
non-admitted gate's verdict count. Make two cold runs write different bytes.

**Done criteria for Phase 1.** G1–G6, including the **clean tree after the four
commands** in the real `examples/bracket`; the R-8 oracle shows identical statuses; the E4
table is exact and the `--force` soundness oracle passes; `EntriesAreDeterministic` holds
on every bundled baseline; CLAUDE.md gains invariants 7, 8 and 9 (reject half); the commit
names S-01, S-02, S-04 (packs), S-05, S-07, S-08–S-15, S-19, S-20–S-34, S-36–S-40, S-42–S-44,
S-45 (digests from bytes), S-47,
S-64, S-65, S-68, S-69 (tags), S-76, S-83, S-84, S-87 (pack mode and tests), S-89.

**Open questions for the Phase 1 judge panel.**

| Q | Question | Recommended default | Rejected, and why |
|---|---|---|---|
| Q1.1 | Is the verdict cache tracked? | **Tracked**, deterministic content, plus R-9 re-execution at every money boundary (D-05). | Untracked: a fresh clone shows nothing and "git + cache is the history" fails. Tracked without R-9: a one-file forgery surface. One file per gate: the conflict generator. |
| Q1.2 | What is ρ's spine component? | A version-independent canonical AST walk of the verdict-path modules, docstrings stripped, pinned across the CI matrix. | `ast.dump` (differs across Python versions). The version string (S-29). Whole-spine bytes (churn). |
| Q1.3 | Are instrument versions (trimesh, numpy, omc) part of ρ? | No: provenance only, a decline recorded in M11.5; `freshness`, `status` and `doctor` note a mismatch, and `doctor` warns on an undeclared third-party import. A cached PASS is served only where `availability(spec)` holds (P1.2), so a missing instrument reads skipped, never PASS. Two outcomes for one ρ under different instruments warn and re-run locally, never error (P1.2). | In ρ: every machine would disagree on staleness, breaking claims.py's "same ledger, same answer on two machines". |
| Q1.4 | Legacy verdicts? | Drop (D-09). | Import as unknown-ρ: a permanent special case in the generous direction. |
| Q1.5 | How is migration triggered? | Automatically on the first `check`, `start` or shim; pure; resumable. Every other command and every hook migrates in memory only (invariant 8's carve-out). | An `atompipe migrate` command: one more record-mutating command. |
| Q1.6 | Does `check` run a control on a cache miss? | Yes, within the tier ceiling; tier-0 controls cost about one gate run and stay cached. | Leaving it to `gate selftest` keeps demonstration optional; S-05 shows optional counts for nothing. |
| Q1.7 | Where does `needs` sit relative to ρ (decided now so the cache shape holds)? | Not a ρ edge (D-04). | The prerequisite's ok-bit in ρ re-runs dependents whose inputs never moved. |
| Q1.8 | Are pack-mode selftest results persisted? | No: stdout and `--junit` only, and every control runs with a temp `out_dir` (P1.1). | Writing into a pack directory — which today's fixtures and `test_packs` already do — breaks SEALED hygiene and wheel installs. |
| Q1.9 | Is the strict reader worth the friction? | Yes, for record files. | Lenient: `rejectd` is dropped and then erased (S-40). |
| Q1.10 | Where does a physical result live? | `results/<claim-id>.json` (D-11). | Inside `claims/<id>.json`: the trade overlay would erase a candidate's own refutation. |
| Q1.11 | Where do the bracket's rejected alternatives live? | `PARAMS` in the model (D-30). | `params/*.json` copies of docstring prose: two homes for one fact. |
