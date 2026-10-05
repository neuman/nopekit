# SPDX-License-Identifier: Apache-2.0
"""Invariant 12: no reader says more than the composition, and the boundary that
spends re-executes (P2.5b — milestones, `export`, the readiness sentence).

A milestone (`milestones/<name>.json`) names a spend and the claims it requires;
*ready* for it is GLOSSARY §4's: every required claim reads Checked. `export
<milestone>` is the boundary that costs money (R-9): it re-runs every evaluator
a required claim rests on, with its controls and prerequisites, at the top
tier, refuses while a required claim is unresolved or a re-run disagrees with
the entry the cache served, and `--dry-run` is the same code path writing
nothing (D-15). Going ahead over an unresolved claim is a person's recorded
decision.

What slipped through before this file: the page said ready whenever nothing
stopped `check`, with a required physical claim untested and an assumption
unowned (S-60); the committed readiness report had drifted from its ledger
(S-41); and the tracked cache is forgeable in the inner loop — a hand-placed
entry was served Checked until something re-ran it, and nothing at a spend did.

* **TheProjectSentenceHeadIsUnmoved** (C-6) — the bracket's bold head, exactly
  as ecaad99 printed it: P2.5b adds clauses after it, never edits it.
* **ReadyIsOnePredicate** (V-1) — over seeded ledgers, every reader of *ready*
  for a milestone says what `claims.unresolved` says, and a milestone rendering
  never calls one claim both required and not required.
* **TheBoundaryReExecutes** (V-2), **ACacheThatLiesIsCaughtAtTheBoundary**
  (V-3), **DryRunIsTheSamePath** (V-4), **GoingAheadIsAPersonsDecision** (V-5),
  **ThePackageIsWhatWasRecorded** (V-7) — the commands.

Each row carries a planted violator the check must catch (R-12).

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_export.py -v
"""
from __future__ import annotations

import contextlib
import dataclasses
import glob
import importlib
import io
import json
import os
import random
import re
import shutil
import sys
import unittest
from typing import Any, Mapping
from unittest import mock

import _env
import _physical as P
import _projects
from atompipe import claims as claims_mod
from atompipe import cli as cli_mod
from atompipe import gates as gates_mod
from atompipe import report as report_mod
from atompipe import store
from atompipe import verdicts
from atompipe.util import AtompipeError
from atompipe.models import (AttributionRecord, Claim, ClaimKind, ClaimStatus,
                             EntryStanding, Ledger, PhysicalResult, Standing, Verdict)

#: The bracket's milestone (D23): the print C1-C4 must be checked for.
MILESTONE = "print-v1"
REQUIRES = ["C1", "C2", "C3", "C4"]

#: The bracket's bold head at ecaad99, verbatim (C-6).
HEAD = ("**v0.1 is NOT ready: 4 of 7 required claims are unresolved — 1 failing (C1); "
        "2 gaps (C6, C7); 1 pending build (C5).**")


def _need(module: Any, name: str) -> Any:
    """``module.name``, or an assertion naming what is missing — so a test written
    before the code it holds is red for its reason, never an ImportError."""
    found = getattr(module, name, None)
    if found is None:
        raise AssertionError(f"{getattr(module, '__name__', module)}.{name} does not exist yet")
    return found


def milestones_mod() -> Any:
    """``atompipe.milestones``, or an assertion that it does not exist yet."""
    try:
        return importlib.import_module("atompipe.milestones")
    except ImportError:
        raise AssertionError("atompipe.milestones does not exist yet") from None


def milestone_cls() -> Any:
    """``models.Milestone``, or an assertion that it does not exist yet."""
    from atompipe import models
    return _need(models, "Milestone")


def captured(argv: list[str], *, stdin: str | None = None) -> tuple[int, str, str]:
    """``atompipe <argv>`` in THIS process (so a test can patch the spine):
    ``(exit code, stdout, stderr)``."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        if stdin is not None:
            with mock.patch.object(sys, "stdin", io.StringIO(stdin)):
                code = cli_mod.main(argv)
        else:
            code = cli_mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def bracket(dest: str, *, thickness: float | None = None, git: bool = False) -> str:
    """A migrated bracket copy (C5's test written down, its evidence file) that
    declares ``print-v1`` as the committed bracket does."""
    root = P.project(dest, thickness=thickness, git=git)
    if not os.path.isfile(os.path.join(root, "milestones", f"{MILESTONE}.json")):
        raise AssertionError("the bracket declares no milestones/print-v1.json yet (D23)")
    return root


def export_json(root: str, *args: str, code: int | None = None, **kw: Any) -> tuple[Any, dict]:
    """``atompipe export <args> --json``: the process and its document."""
    proc = P.run(root, "export", *args, "--json", code=code, **kw)
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        raise AssertionError(f"`export {' '.join(args)} --json` printed no JSON "
                             f"(exit {proc.returncode}):\n{proc.stdout[-2000:]}\n"
                             f"{proc.stderr[-2000:]}") from None
    return proc, doc


def tree(root: str) -> dict[str, bytes]:
    """Every file under ``root`` but this checkout's memory and scratch — what a
    command that writes nothing must leave byte for byte."""
    out: dict[str, bytes] = {}
    ignored = {".atompipe/ledger.json", ".atompipe/build.lock"}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        dirnames[:] = [d for d in dirnames if d != "__pycache__"
                       and not (rel_dir == ".atompipe" and d in ("cache", "obs", "out"))]
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel in ignored:
                continue
            with open(path, "rb") as fh:
                out[rel] = fh.read()
    return out


def changed(before: Mapping[str, bytes], after: Mapping[str, bytes]) -> list[str]:
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


def forge_entry(root: str, gate: str, *, passed: bool, instruments: dict | None = None) -> str:
    """Replace ``gate``'s Fresh entry by one with the other outcome, written
    through ``verdicts.write_entry`` — ``out8`` and ``digest`` self-consistent:
    the forgery SPINE_CONTRACT's limits describe. ``instruments`` overrides the
    entry's (a foreign machine's). Returns the forged entry's name."""
    entries = verdicts.read_entries(root, gate)
    if not entries:
        raise AssertionError(f"{gate} has no entry to forge")
    view, resolution = P.resolved(root)
    row = resolution.rows.get(gate)
    real = row.entry if row is not None and row.entry is not None else entries[-1]
    verdict = dict(real.verdict)
    verdict["passed"] = passed
    limit = verdict.get("limit")
    if passed and isinstance(limit, (int, float)) and not isinstance(limit, bool):
        # A forger who knows the claim is compared with its own acceptance
        # condition (P2.4's cross-check) states a value that meets it too.
        verdict["measured"] = round(float(limit) * 0.9, 4)
        verdict["detail"] = f"{verdict['measured']} (forged)"
    forged = dataclasses.replace(real, verdict=verdict, path="",
                                 instruments=dict(instruments) if instruments is not None
                                 else real.instruments)
    os.remove(real.path)
    wrote = verdicts.write_entry(root, forged)
    return os.path.basename(wrote.path) if getattr(wrote, "path", "") else ""


# --------------------------------------------------------------------------- #
# C-6
# --------------------------------------------------------------------------- #
class TheProjectSentenceHeadIsUnmoved(unittest.TestCase):
    """(C-6) The bracket's readiness report opens with ecaad99's bold head: what
    is unresolved among the claims `check` blocks on. P2.5b adds the hardware
    clause and the limits paragraph AFTER it, never edits it (S-59: the head
    names every unresolved required claim)."""

    def test_the_head(self):
        root = os.path.join(_env.REPO, "examples", "bracket")
        view, resolution = P.resolved(root)
        ledger = store.load(root)
        registry, _ = cli_mod._registry(root, ledger, strict=False)
        md = report_mod.render_markdown(view, registry, stale_gates=resolution.stale_gates,
                                        root=root)
        sentence = md.splitlines()[2]
        self.assertTrue(sentence.startswith(HEAD), sentence)

    def test_a_moved_head_is_caught(self):
        """Planted: the head reworded."""
        planted = HEAD.replace("4 of 7 required claims are unresolved",
                               "4 of 7 claims need work")
        self.assertFalse(planted.startswith(HEAD))


# --------------------------------------------------------------------------- #
# V-1 — one predicate, every reader
# --------------------------------------------------------------------------- #
#: The outcomes a seeded automated claim's evaluator may have.
_AUTOMATED = ("pass", "fail", "skipped", "error", "unqualified", "unrun", "stale")
#: What a seeded physical claim's results may be.
_PHYSICAL = ("none", "fail", "counted", "moved", "typed")
#: And an assumption's owner.
_ASSUMED = ("owned", "unowned")


def _physical_claim(cid: str, how: str, critical: bool) -> Claim:
    claim = Claim(id=cid, statement=f"physical {cid}", kind=ClaimKind.PHYSICAL,
                  critical=critical, note="a written test")
    if how == "none":
        return claim
    passed = how != "fail"
    result = PhysicalResult(passed=passed, who="Dana", channel="interactive",
                            article={"source": "design", "hash": "a" * 64})
    standing = None
    if how == "counted":
        standing = Standing("current", 0, "a" * 64, (),
                            (EntryStanding(0, True, True, "", "a" * 64, "current"),))
    elif how == "moved":
        standing = Standing("article-moved", 0, "a" * 64, ("config.x 1 -> 2",),
                            (EntryStanding(0, True, False, "article-moved", "a" * 64, "moved",
                                           ("config.x 1 -> 2",)),))
    elif how == "fail":
        standing = Standing("", None, "", (),
                            (EntryStanding(0, False, False, "", "a" * 64, "current"),))
    return dataclasses.replace(claim, results=(result,), physical_result=result,
                               standing=standing)


def seeded(seed: int) -> tuple[Ledger, frozenset]:
    """A small ledger of each kind of claim at random statuses, and 0-3
    milestones with random ``requires`` — some naming an id no claim holds, one
    possibly empty. In memory, for every reader of *ready*."""
    Milestone = milestone_cls()
    rng = random.Random(seed)
    claims: list[Claim] = []
    found: list[Verdict] = []
    stale: set[str] = set()
    n = 0
    for kind in ("measurable", "physical", "assumption"):
        for _ in range(rng.randint(0, 2)):
            n += 1
            cid = f"C{n}"
            critical = rng.random() < 0.7
            if kind == "measurable":
                gate = f"g.{cid}"
                how = rng.choice(_AUTOMATED)
                claims.append(Claim(id=cid, statement=f"measured {cid}", gates=[gate],
                                    critical=critical))
                if how == "unrun":
                    continue
                verdict = Verdict(gate=gate, claims=[cid], passed=how in ("pass", "stale"),
                                  skipped=how == "skipped",
                                  skip_reason="requires x" if how == "skipped" else "",
                                  error={"error": "boom",
                                         "unqualified": "unqualified: known-bad pass"}.get(how, ""),
                                  unqualified="known-bad:pass" if how == "unqualified" else "")
                found.append(verdict)
                if how == "stale":
                    stale.add(gate)
            elif kind == "physical":
                claims.append(_physical_claim(cid, rng.choice(_PHYSICAL), critical))
            else:
                owned = rng.choice(_ASSUMED) == "owned"
                attributions = (AttributionRecord(role="owner", name="Dana", reason="carried",
                                                  who="Dana", channel="interactive"),) \
                    if owned else ()
                claims.append(Claim(id=cid, statement=f"assumed {cid}",
                                    kind=ClaimKind.ASSUMPTION, critical=critical,
                                    owner="Dana", rationale="carried",
                                    attributions=attributions))
    if not claims:
        claims.append(Claim(id="C1", statement="measured C1", gates=["g.C1"]))
        found.append(Verdict(gate="g.C1", claims=["C1"], passed=True))
    ids = [c.id for c in claims]
    milestones = []
    for i in range(rng.randint(0, 3)):
        requires = rng.sample(ids, rng.randint(0, len(ids)))
        if rng.random() < 0.2:
            requires.append("Z9")
        milestones.append(Milestone(id=f"m{i}", description=f"spend {i}", requires=requires))
    if rng.random() < 0.15:
        milestones.append(Milestone(id="empty", description="nothing", requires=[]))
    ledger = Ledger(claims=claims, verdicts=found)
    ledger = dataclasses.replace(ledger, milestones=milestones) \
        if "milestones" in {f.name for f in dataclasses.fields(Ledger)} else ledger
    if "milestones" not in {f.name for f in dataclasses.fields(Ledger)}:
        object.__setattr__(ledger, "_planned_milestones", milestones)
    return ledger, frozenset(stale)


def _milestones_of(ledger: Ledger) -> list:
    found = getattr(ledger, "milestones", None)
    if found is None:
        found = getattr(ledger, "_planned_milestones", [])
    return list(found)


def expected_ready(ledger: Ledger, composed: Mapping[str, Any], milestone: Any) -> bool:
    """GLOSSARY §4, typed here and never read from the code under test: at least
    one required id, every one held by a claim that reads Checked."""
    held = {c.id for c in ledger.claims}
    requires = list(milestone.requires or ())
    return bool(requires) and all(cid in held for cid in requires) and all(
        composed[cid].status in (ClaimStatus.PASS, ClaimStatus.VERIFIED) for cid in requires)


def readers_of_ready(ledger: Ledger, stale: frozenset, milestone: Any, *,
                     site: bool = False, root: str = "") -> dict[str, Any]:
    """What every reader of *ready* says for ``milestone``: ``{reader: bool}``."""
    m = milestones_mod()
    composed = claims_mod.compositions(ledger, stale_gates=stale)
    out: dict[str, Any] = {}
    out["claims.unresolved"] = _need(claims_mod, "unresolved")(
        ledger, composed, milestone).ready
    out["report.readiness"] = report_mod.readiness(ledger, composed, milestone=milestone)["ready"]
    out["milestones.refusals"] = not [r for r in _need(m, "refusals")(ledger, composed, milestone)
                                      if r.kind != "disagrees"]
    out["milestones.judge"] = _need(m, "judge")(ledger, composed, milestone).ready
    summary = claims_mod.summarise(ledger, None, stale_gates=stale)
    out["summary.milestones"] = (summary.get("milestones") or {}).get(
        milestone.id, {}).get("ready")
    sentence = report_mod._verdict_sentence(ledger, composed, None, stale=False, markdown=False,
                                            milestone=milestone)
    out["sentence"] = bool(re.search(r"\bis ready for\b", sentence))
    if site:
        from atompipe import site as site_mod
        # The page re-views the ledger (`verdicts.view`): hand it the seeded
        # standings, as the resolver would.
        resolution = verdicts.Resolution(
            verdicts=list(ledger.verdicts), stale_gates=stale,
            standings={c.id: c.standing for c in ledger.claims if c.standing is not None})
        state = site_mod.state(root, ledger, None, resolution=resolution, params=[])
        out["state.json"] = (((state.get("readiness") or {}).get("milestones") or {})
                             .get(milestone.id) or {}).get("ready")
    return out


def required_flag_problems(ledger: Ledger, stale: frozenset, milestone: Any) -> list[str]:
    """A milestone rendering of the report never flags a claim it requires as
    not required, nor one it does not require as required (critique 7 of the
    P2.5b design: the package's body read `critical` while its head read the
    milestone)."""
    md = report_mod.render_markdown(ledger, None, stale_gates=stale, milestone=milestone)
    out = []
    requires = set(milestone.requires or ())
    for line in md.splitlines():
        match = re.match(r"^(?:### \[.{5}\] |- \*\*)(?P<id>C\d+)\b", line)
        if not match:
            continue
        cid = match.group("id")
        says_not = "not required" in line
        if cid in requires and says_not:
            out.append(f"{milestone.id}: {cid} is required and flagged not required: {line}")
        if cid not in requires and re.search(r"\*\((?:[^)]*, )?(?:required|critical)\)\*", line):
            out.append(f"{milestone.id}: {cid} is not required and flagged required: {line}")
    return out


class ReadyIsOnePredicate(_env.EnvCase):
    """(V-1, invariant 12) *Ready* for a milestone is `claims.unresolved`'s, and
    every reader of it — `report.readiness`, the export's refusals and its
    judgment, `summarise`'s `milestones`, the readiness sentence, `state.json`
    — says the same over 400 seeded ledgers. Rows by hand: an owned assumption,
    a person-awaiting Stale, a required id no claim holds, `requires: []`, and a
    fail no claim file holds each keep a milestone from ready."""

    SEEDS = 400

    def problems(self, seeds: range) -> list[str]:
        out: list[str] = []
        root = self.tmp()
        for seed in seeds:
            ledger, stale = seeded(seed)
            composed = claims_mod.compositions(ledger, stale_gates=stale)
            for milestone in _milestones_of(ledger):
                want = expected_ready(ledger, composed, milestone)
                said = readers_of_ready(ledger, stale, milestone, site=seed % 25 == 0,
                                        root=root)
                for reader, got in said.items():
                    if got is not want:
                        out.append(f"seed {seed} {milestone.id} {milestone.requires}: "
                                   f"{reader} says {got}, not {want}")
                if seed % 10 == 0:
                    out += [f"seed {seed}: {p}"
                            for p in required_flag_problems(ledger, stale, milestone)]
        return out

    def test_every_reader_says_the_one_predicate(self):
        found = self.problems(range(self.SEEDS))
        self.assertEqual(found[:12], [], f"{len(found)} disagreements")

    def test_the_seeds_reach_both_answers(self):
        """A seeding that never reached `ready` (or never its negation) would
        hold every reader equal by having nothing to say."""
        answers = set()
        for seed in range(self.SEEDS):
            ledger, stale = seeded(seed)
            composed = claims_mod.compositions(ledger, stale_gates=stale)
            for milestone in _milestones_of(ledger):
                answers.add(expected_ready(ledger, composed, milestone))
        self.assertEqual(answers, {True, False})

    def _hand(self, claim: Claim, *, extra: list | None = None, requires: list | None = None,
              stale: frozenset = frozenset()) -> dict[str, Any]:
        Milestone = milestone_cls()
        milestone = Milestone(id="m", description="a spend",
                              requires=[claim.id] if requires is None else requires)
        verdicts_ = list(extra or [])
        ledger = Ledger(claims=[claim], verdicts=verdicts_)
        ledger = dataclasses.replace(ledger, milestones=[milestone])
        return readers_of_ready(ledger, stale, milestone)

    def test_the_rows_by_hand(self):
        owned = Claim(id="C6", statement="static", kind=ClaimKind.ASSUMPTION, owner="Dana",
                      rationale="carried", attributions=(AttributionRecord(
                          role="owner", name="Dana", reason="carried", who="Dana",
                          channel="interactive"),))
        rows = {
            "an owned assumption": (owned, None),
            "a person-awaiting Stale (article-moved, which does not stop check)":
                (_physical_claim("C5", "moved", True), None),
            "a required id no claim holds": (_physical_claim("C5", "counted", True),
                                             ["C5", "C9"]),
            "requires nothing": (_physical_claim("C5", "counted", True), []),
            "a fail no claim file holds (composed beside the claims)": (dataclasses.replace(
                _physical_claim("C3", "fail", True),
                standing=Standing(verdicts.REMOVED)), None),
        }
        for name, (claim, requires) in rows.items():
            with self.subTest(name):
                said = self._hand(claim, requires=requires)
                self.assertEqual({k: v for k, v in said.items() if v is not False}, {},
                                 f"{name}: a reader said ready")
        with self.subTest("the counted pass alone is ready (the rows are not vacuous)"):
            said = self._hand(_physical_claim("C5", "counted", True))
            self.assertEqual({k: v for k, v in said.items() if v is not True}, {})

    def test_a_missing_id_is_named(self):
        Milestone = milestone_cls()
        claim = _physical_claim("C5", "counted", True)
        milestone = Milestone(id="m", description="a spend", requires=["C5", "C9"])
        ledger = dataclasses.replace(Ledger(claims=[claim]), milestones=[milestone])
        composed = claims_mod.compositions(ledger)
        found = _need(claims_mod, "unresolved")(ledger, composed, milestone)
        self.assertEqual(list(found.missing), ["C9"])
        kinds = {r.kind: r.subject for r in milestones_mod().refusals(ledger, composed, milestone)}
        self.assertEqual(kinds.get("missing"), "C9")

    def test_the_planted_predicates_are_caught(self):
        """Planted, each into `claims.unresolved`: Assumed read as resolved;
        `critical` read in place of `requires`; missing ids dropped; zero
        required read ready."""
        real = _need(claims_mod, "unresolved")

        def assumed_resolved(ledger, composed, milestone=None):
            found = real(ledger, composed, milestone)
            left = [c for c in found.unresolved
                    if composed[c.id].status is not ClaimStatus.ASSERTED]
            return found._replace(unresolved=left, ready=bool(found.required) and not left
                                  and not found.missing)

        def critical_read(ledger, composed, milestone=None):
            return real(ledger, composed, None)

        def missing_dropped(ledger, composed, milestone=None):
            found = real(ledger, composed, milestone)
            return found._replace(missing=[], ready=bool(found.required)
                                  and not found.unresolved)

        def zero_ready(ledger, composed, milestone=None):
            found = real(ledger, composed, milestone)
            return found._replace(ready=found.ready or (not found.required
                                                        and not found.missing))

        for name, planted in (("assumed resolved", assumed_resolved),
                              ("critical in place of requires", critical_read),
                              ("missing ids dropped", missing_dropped),
                              ("zero required ready", zero_ready)):
            with self.subTest(name), mock.patch.object(claims_mod, "unresolved", planted):
                self.assertTrue(self.problems(range(120)), f"{name} was not caught")


# --------------------------------------------------------------------------- #
# V-2 — the boundary re-executes
# --------------------------------------------------------------------------- #
#: Fig. 4 with a guard: `fig4.guard` covers no claim (its tag is no claim's)
#: and is the prerequisite of `fig4.enclosure_fit`, which K1 rests on. A
#: closure that re-ran only the covering evaluators would leave it out.
GUARD = '''

@gate(id="fig4.guard", claims=["no-claim-here"], settles="enclosure sanity",
      negative_control=_bad("huge"))
def guard(ctx):
    return _judge("fig4.guard", float(ctx.params["enclosure"]), 200.0, "mm", "<=")
'''

GUARD_FIXTURE = '''

def huge(ctx):
    return _with(ctx, enclosure=400.0)
'''


def guarded_fig4(dest: str) -> str:
    """Fig. 4 (``tests/test_fig4.py``) with ``fig4.guard`` before the enclosure
    evaluator, and a milestone ``enclosure`` requiring K1-K3."""
    import test_fig4 as F
    root = F.fig4(dest)
    path = os.path.join(root, "gates", "g.py")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    text = text.replace('@gate(id="fig4.enclosure_fit", claims=["K1"], settles="enclosure width",',
                        '@gate(id="fig4.enclosure_fit", claims=["K1"], settles="enclosure width",'
                        ' needs=["fig4.guard"],')
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + GUARD)
    with open(os.path.join(root, "selftest", "bad.py"), "a", encoding="utf-8") as fh:
        fh.write(GUARD_FIXTURE)
    P.write_json(os.path.join(root, "milestones", "enclosure.json"),
                 {"description": "Print the enclosure", "requires": ["K1", "K2", "K3"]})
    return root


def rerun_rows(doc: Mapping[str, Any]) -> dict[str, dict]:
    return {row.get("gate"): row for row in doc.get("reran") or ()}


def rerun_problems(doc: Mapping[str, Any], wanted: set[str]) -> list[str]:
    """Each gate in ``wanted`` re-run on the real design at the top tier, its
    controls run again, its verdict never served from the cache."""
    rows = rerun_rows(doc)
    out = []
    for gate in sorted(wanted):
        row = rows.get(gate)
        if row is None:
            out.append(f"{gate}: not re-run")
            continue
        if row.get("executed") is not True or row.get("cached") is True:
            out.append(f"{gate}: served from the cache, not re-run ({row})")
        if row.get("control_executed") is not True:
            out.append(f"{gate}: its controls were not run again ({row})")
        if row.get("tier") != 3:
            out.append(f"{gate}: re-run at tier {row.get('tier')}, not the top tier")
    return out


class TheBoundaryReExecutes(_env.EnvCase):
    """(V-2, R-9, invariant 12) `export <m> --dry-run` re-runs every evaluator
    a required claim rests on — and each one's prerequisites — at tier 3, with
    both controls and the walk, whatever the cache holds."""

    BRACKET_GATES = {"bracket.deflection", "bracket.bending_stress", "bracket.bearing",
                     "bracket.model_validity", "bracket.bed_fit", "bracket.min_wall"}

    def test_the_bracket_at_eight(self):
        root = bracket(os.path.join(self.tmp(), "b"), thickness=8.0)
        P.run(root, "check")
        _proc, doc = export_json(root, MILESTONE, "--dry-run")
        self.assertEqual(rerun_problems(doc, self.BRACKET_GATES), [])
        self.assertIs(doc.get("dry_run"), True)

    def test_a_prerequisite_covering_no_required_claim_is_re_run(self):
        root = guarded_fig4(os.path.join(self.tmp(), "g"))
        P.run(root, "check")
        _proc, doc = export_json(root, "enclosure", "--dry-run")
        self.assertEqual(rerun_problems(doc, {"fig4.guard", "fig4.enclosure_fit",
                                              "fig4.port_align", "fig4.cable"}), [])
        self.assertNotIn("fig4.power", rerun_rows(doc), "the boundary pays for what the "
                                                        "spend requires, not every evaluator")

    def test_a_model_that_does_not_load_is_said_and_never_covered(self):
        """Review of P2.5b (finding 11): with a model that does not load the
        boundary ran nothing and said "no evaluator settles a claim print-v1
        requires" — false: six do — and refused on the cache's statuses alone,
        which a go-ahead covers. It says the model does not load, as a refusal
        no decision covers."""
        root = bracket(os.path.join(self.tmp(), "m"), thickness=8.0, git=True)
        P.run(root, "check")
        with open(os.path.join(root, "model", "bracket.py"), "a", encoding="utf-8") as fh:
            fh.write('\nraise RuntimeError("broken model")\n')
        proc, doc = export_json(root, MILESTONE, "--dry-run", code=1)
        self.assertIn("model", refusal_kinds(doc))
        self.assertIn("broken model", json.dumps(doc["refusals"]))
        text = P.run(root, "export", MILESTONE, "--dry-run", code=1).stdout
        self.assertNotIn("no evaluator settles", text)
        self.assertRegex(text, r"(?m)^re-run: none — the model does not load")
        P.tty(root, "export", MILESTONE, "--proceed", "--why", "print it anyway",
              answer=MILESTONE, code=1)
        self.assertEqual(P.exports(root, MILESTONE), {}, "a go-ahead covered a model that "
                                                        "does not load")

    def test_the_planted_boundaries_are_caught(self):
        """Planted: the re-run served from the cache (`force=False`), at tier 0,
        and with no prerequisite expansion (`gates.plan` returning the
        selection alone)."""
        root = guarded_fig4(os.path.join(self.tmp(), "g"))
        code, _out, err = captured(["check", "-C", root])     # in process: a fast-tier row
        self.assertIn(code, (0, 1), err)
        real = verdicts.sweep

        def plant(**over):
            def sweep(*args, **kwargs):
                kwargs.update(over)
                return real(*args, **kwargs)
            return sweep

        real_plan = gates_mod.plan

        def no_expansion(registry, selected):
            names = {s.id if hasattr(s, "id") else str(s) for s in selected}
            return [s for s in real_plan(registry, selected) if s.id in names]

        wanted = {"fig4.guard", "fig4.enclosure_fit", "fig4.port_align", "fig4.cable"}
        # The real boundary first, in process (the fast tier's row for V-2; the
        # end-to-end rows, through the command, are left to the gate).
        code, out, err = captured(["export", "enclosure", "--dry-run", "--json", "-C", root])
        self.assertEqual(rerun_problems(json.loads(out), wanted), [], f"exit {code}: {err}")
        for name, patch in (("served from the cache",
                             mock.patch.object(verdicts, "sweep", plant(force=False))),
                            ("at tier 0", mock.patch.object(verdicts, "sweep",
                                                            plant(max_tier=0))),
                            ("no prerequisite", mock.patch.object(gates_mod, "plan",
                                                                  no_expansion))):
            with self.subTest(name), patch:
                code, out, err = captured(["export", "enclosure", "--dry-run", "--json",
                                           "-C", root])
                try:
                    doc = json.loads(out)
                except ValueError:
                    self.fail(f"{name}: no JSON (exit {code}): {out[-500:]} {err[-500:]}")
                self.assertTrue(rerun_problems(doc, wanted), f"{name} was not caught")


# --------------------------------------------------------------------------- #
# V-3 — a cache that lies is caught where it would cost money
# --------------------------------------------------------------------------- #
def refusal_kinds(doc: Mapping[str, Any]) -> list[str]:
    return [r.get("kind") for r in doc.get("refusals") or ()]


class ACacheThatLiesIsCaughtAtTheBoundary(_env.EnvCase):
    """(V-3, R-9) A forged pass in the tracked cache — `out8` and digest
    self-consistent — reads Checked in the inner loop (the stated limit), and
    `export` refuses it: a re-run lands at the entry's ρ with the other outcome.
    The refused export files the re-run, so the forgery is loud everywhere after,
    and no later go-ahead covers it (critique 3 of the P2.5b design)."""

    def forged(self, name: str = "f", **kw: Any) -> str:
        root = bracket(os.path.join(self.tmp(), name), thickness=7.0, git=True)
        forge_entry(root, "bracket.deflection", passed=True, **kw)
        status = json.loads(P.run(root, "status", "--json", code=0).stdout)
        self.assertEqual(status["claims"]["C1"], "pass",
                         "the forgery did not read Checked in the inner loop: this test "
                         "proves nothing about the boundary")
        return root

    def test_a_forged_pass_is_refused_and_the_re_run_filed(self):
        root = self.forged()
        before = tree(root)
        proc, doc = export_json(root, MILESTONE, "--dry-run", code=1)
        self.assertIn("disagrees", refusal_kinds(doc))
        self.assertEqual(changed(before, tree(root)), [], "--dry-run wrote something")
        text = P.run(root, "export", MILESTONE, "--dry-run", code=1).stdout
        self.assertRegex(text, r"(?m)^\[two outcomes\] bracket\.deflection : ")
        proc, doc = export_json(root, MILESTONE, code=1)
        self.assertIn("disagrees", refusal_kinds(doc))
        self.assertFalse(os.path.exists(os.path.join(root, "out", MILESTONE)))
        self.assertFalse(os.path.exists(os.path.join(root, "exports", f"{MILESTONE}.json")))
        status = json.loads(P.run(root, "status", "--json", code=0).stdout)
        self.assertEqual((status["claims"]["C1"], status["statuses"]["C1"]["cause"]),
                         ("blocked", "errored"), "the refused export did not file the re-run")

    def test_no_go_ahead_covers_it_after_the_first_refusal(self):
        """The refused export filed the re-run: the records now hold two outcomes
        at one ρ, and C1 reads Skipped (errored). Still `disagrees` — never an
        ordinary unresolved claim a go-ahead covers (critique 3)."""
        root = self.forged()
        export_json(root, MILESTONE, code=1)
        proc = P.tty(root, "export", MILESTONE, "--proceed", "--why", "a fit print",
                     answer=MILESTONE)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(root, "exports", f"{MILESTONE}.json")))
        again = self.forged("g")
        P.run(again, "check", "--force")
        proc = P.tty(again, "export", MILESTONE, "--proceed", "--why", "a fit print",
                     answer=MILESTONE)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(again, "exports", f"{MILESTONE}.json")))

    def test_the_refusal_names_the_way_out_that_works(self):
        """Review of P2.5b (findings 12, 20): the refusal told the person to run
        `atompipe check --force` — which the refused export had already done, and
        which never clears two outcomes at one ρ — so the next export said the
        same. It names the entry and the way out: the entry that is not the
        evaluator's output removed, the disagreement is gone (C1 then reads the
        honest re-run: an ordinary unresolved claim)."""
        root = self.forged("w")
        forged = [name for name in os.listdir(os.path.join(
            root, ".atompipe", "verdicts", "bracket.deflection"))
            if not name.startswith("control-")]
        text = P.run(root, "export", MILESTONE, code=1).stdout
        self.assertNotIn("check --force", text)
        self.assertIn(".atompipe/verdicts/bracket.deflection/", text)
        served = [name[:-len(".json")] for name in forged if name[:-len(".json")] in text]
        self.assertTrue(served, f"no entry named: {forged}\n{text}")
        again = P.run(root, "export", MILESTONE, "--dry-run", code=1).stdout
        self.assertNotIn("check --force", again)
        os.remove(os.path.join(root, ".atompipe", "verdicts", "bracket.deflection",
                               served[0] + ".json"))
        _proc, doc = export_json(root, MILESTONE, "--dry-run", code=1)
        self.assertNotIn("disagrees", refusal_kinds(doc))
        self.assertEqual([(r["kind"], r["subject"]) for r in doc["refusals"]],
                         [("unresolved", "C1")])

    def test_a_forgery_under_foreign_instruments_is_refused(self):
        """Stamped with another machine's instruments, the forgery is no clash
        under ``_contradicted`` — the served entry and the re-run are still two
        outcomes at one ρ (critique 3)."""
        root = self.forged("i", instruments={"python": "0.0-elsewhere"})
        _proc, doc = export_json(root, MILESTONE, "--dry-run", code=1)
        self.assertIn("disagrees", refusal_kinds(doc))

    def test_a_planted_disagreement_finder_is_caught(self):
        """Planted: `milestones.disagreements` returning nothing. The export still
        refuses — through C1 read from the re-run — but not as a disagreement,
        and the test asks for the kind."""
        # In process throughout — the fast tier's row for V-3 (the end-to-end
        # one, which files the re-run and reads it back through `status`, is
        # left to the gate): the forgery reads Checked, the real finder names it.
        root = bracket(os.path.join(self.tmp(), "p"), thickness=7.0)
        forge_entry(root, "bracket.deflection", passed=True)
        code, out, err = captured(["status", "--json", "-C", root])
        self.assertEqual(json.loads(out)["claims"]["C1"], "pass",
                         "the forgery did not read Checked in the inner loop")
        code, out, err = captured(["export", MILESTONE, "--dry-run", "--json", "-C", root])
        self.assertEqual(code, 1, err)
        self.assertIn("disagrees", refusal_kinds(json.loads(out)))
        with mock.patch.object(milestones_mod(), "disagreements", lambda *a, **k: []):
            code, out, err = captured(["export", MILESTONE, "--dry-run", "--json", "-C", root])
        doc = json.loads(out)
        self.assertEqual(code, 1, err)
        self.assertNotIn("disagrees", refusal_kinds(doc))


# --------------------------------------------------------------------------- #
# V-4 — --dry-run is the same path
# --------------------------------------------------------------------------- #
#: The outcome line, the one line `--dry-run` and the written export may differ on.
OUTCOME = re.compile(r"^export: ")


#: Generators that handle their own directory as ordinary code does (review of
#: P2.5b, finding 6): make it, list it to write an index, copy a file into it.
OWN_DIRECTORY = '''

def makes_it(ctx):
    import os
    os.makedirs(ctx.out_dir, exist_ok=True)
    side_profile(ctx)


def lists_it(ctx):
    import os
    side_profile(ctx)
    with open(os.path.join(ctx.out_dir, "INDEX.txt"), "w", encoding="utf-8") as fh:
        fh.write("\\n".join(sorted(os.listdir(ctx.out_dir))) + "\\n")


def copies_into_it(ctx):
    import os
    import shutil
    side_profile(ctx)
    shutil.copy(os.path.join(ctx.out_dir, "print-settings.txt"),
                os.path.join(ctx.out_dir, "settings-copy.txt"))
    shutil.copy(os.path.join(ctx.root, "claims", "C1.json"), ctx.out_dir)
'''


def comparable(text: str) -> list[str]:
    """The lines two modes must print alike: all but the outcome line and the
    prompt and decision lines a written export prints around it."""
    return [ln for ln in text.splitlines() if not OUTCOME.match(ln)
            and not ln.startswith(("decided by ", "go ahead over "))]


class DryRunIsTheSamePath(_env.EnvCase):
    """(V-4, D-15) `export <m> --dry-run` and `export <m>` print the same lines
    but the outcome, the same refusals, the same article and package hash;
    `--dry-run` writes nothing but ignored scratch, removed by the end."""

    def test_at_seven_eight_and_forged(self):
        for name, thickness, forge in (("seven", 7.0, False), ("eight", 8.0, False),
                                       ("forged", 7.0, True)):
            with self.subTest(name):
                root = bracket(os.path.join(self.tmp(), name), thickness=thickness, git=True)
                P.run(root, "check")
                if forge:
                    forge_entry(root, "bracket.deflection", passed=True)
                before = tree(root)
                dry = P.run(root, "export", MILESTONE, "--dry-run")
                _p, dry_doc = export_json(root, MILESTONE, "--dry-run")
                self.assertEqual(changed(before, tree(root)), [], "--dry-run wrote something")
                self.assertFalse(glob.glob(os.path.join(root, ".atompipe", "out", "export-*")))
                proc, doc = export_json(root, MILESTONE)
                self.assertEqual(refusal_kinds(dry_doc), refusal_kinds(doc))
                self.assertEqual(dry.returncode, proc.returncode)
                self.assertEqual(dry_doc.get("ready"), doc.get("ready"))
                for key in ("article", "package"):
                    self.assertEqual(dry_doc.get(key), doc.get(key), key)
                if thickness == 8.0 and not forge:
                    self.assertTrue(doc.get("written"))
                    self.assertTrue((dry_doc.get("article") or {}).get("hash"))

    def test_the_text_differs_only_in_the_outcome_line(self):
        root = bracket(os.path.join(self.tmp(), "t"), thickness=8.0, git=True)
        P.run(root, "check")
        dry = P.run(root, "export", MILESTONE, "--dry-run", code=0).stdout
        written = P.run(root, "export", MILESTONE, code=0).stdout
        self.assertEqual(comparable(dry), comparable(written))
        self.assertRegex(dry, r"(?m)^export: would write out/print-v1/ — article [0-9a-f]{12} "
                              r"· package [0-9a-f]{12} · \d+ files \(dry run: nothing written\)$")
        self.assertRegex(written, r"(?m)^export: out/print-v1/ — article [0-9a-f]{12} · "
                                  r"package [0-9a-f]{12} · \d+ files, recorded in "
                                  r"exports/print-v1\.json$")

    def test_a_planted_dry_run_predicate_is_caught(self):
        """Planted: `--dry-run` skips the re-run (serves the cache). On the
        forged copy it reads "would write" where the boundary refuses."""
        root = bracket(os.path.join(self.tmp(), "p"), thickness=7.0)
        code, _out, err = captured(["check", "-C", root])     # in process: a fast-tier row
        self.assertIn(code, (0, 1), err)
        forge_entry(root, "bracket.deflection", passed=True)
        real = verdicts.sweep

        def no_rerun_when_dry(*args, **kwargs):
            if not kwargs.get("record", True):
                kwargs["force"] = False
            return real(*args, **kwargs)

        code, out, _err = captured(["export", MILESTONE, "--dry-run", "-C", root])
        self.assertEqual(code, 1, out)
        with mock.patch.object(verdicts, "sweep", no_rerun_when_dry):
            code, out, _err = captured(["export", MILESTONE, "--dry-run", "-C", root])
        self.assertEqual(code, 0, "the planted dry-run predicate was not caught")
        self.assertIn("would write", out)

    def test_dry_run_says_what_the_written_export_would_refuse(self):
        """No git identity, and a project not yet migrated: `export` refuses, and
        `--dry-run` says it would (critique 21 of the P2.5b design: /ready must
        never promise a spend the boundary refuses)."""
        root = bracket(os.path.join(self.tmp(), "n"), thickness=8.0)
        P.run(root, "check")
        dry = P.run(root, "export", MILESTONE, "--dry-run", identity=False)
        self.assertEqual(dry.returncode, 1, dry.stdout + dry.stderr)
        self.assertRegex(dry.stdout, r"(?m)^export: would refuse — .*git identity")
        written = P.run(root, "export", MILESTONE, identity=False)
        self.assertEqual(written.returncode, 2, written.stdout + written.stderr)
        legacy = _projects.bracket_copy(os.path.join(self.tmp(), "legacy"), thickness=8.0)
        dry = P.run(legacy, "export", MILESTONE, "--dry-run")
        self.assertEqual(dry.returncode, 1, dry.stdout + dry.stderr)
        self.assertRegex(dry.stdout, r"(?m)^export: would refuse — .*atompipe check")
        self.assertTrue(store.is_legacy(legacy), "--dry-run migrated a legacy project")

    def test_a_generator_that_handles_its_own_directory_builds_one_article(self):
        """Review of P2.5b (finding 6): the written export built in
        `out/.<m>.tmp-<pid>/`, a path the trace did not know for an out dir —
        so `os.makedirs(ctx.out_dir)` put the pid into the article (a scratch
        removed by the swap: the article read moved the moment it existed), a
        listing of it refused every written export, and a copy into it made
        the written article untraced while the dry run's was traced. Each such
        generator: the dry run's article and package are the written one's,
        traced, and a pass on it counts."""
        root = bracket(os.path.join(self.tmp(), "g"), thickness=8.0, git=True)
        with open(os.path.join(root, "generators", "profile.py"), "a", encoding="utf-8") as fh:
            fh.write(OWN_DIRECTORY)
        P.run(root, "check")
        for name in ("makes_it", "lists_it", "copies_into_it"):
            with self.subTest(name):
                P.write_json(os.path.join(root, "milestones", f"{MILESTONE}.json"),
                             {"description": "the print", "requires": REQUIRES,
                              "generator": f"generators/profile.py:{name}"})
                _p, dry = export_json(root, MILESTONE, "--dry-run", code=0)
                _p, written = export_json(root, MILESTONE, code=0)
                self.assertEqual(dry["article"], written["article"])
                self.assertEqual(dry["package"], written["package"])
                self.assertIs(written["article"]["traced"], True)
                recorded = P.exports(root, MILESTONE)["exports"][-1]["article"]
                self.assertEqual([f for f in recorded["built_from"].get("files") or {}
                                  if f.startswith(("out/", ".atompipe/"))], [],
                                 "the package's own directory is in the article")
        article = P.exports(root, MILESTONE)["exports"][-1]["article"]["hash"]
        P.tty(root, "claim", "physical", "C5", "pass", "--detail", "no crazing",
              "--evidence", P.EVIDENCE, "--article", article, answer="C5", code=0)
        status = json.loads(P.run(root, "status", "--json", code=0).stdout)
        self.assertEqual((status["claims"]["C5"], status["statuses"]["C5"]["cause"]),
                         ("verified", "on-article"))
        self.assertEqual(status.get("rebuild"), [])

    def test_a_generator_that_writes_under_dry_run_is_refused_and_named(self):
        """A generator is project code, and `--dry-run` runs it: one that writes
        outside its directory is refused and named — detected, not undone
        (critique 5 of the P2.5b design)."""
        root = bracket(os.path.join(self.tmp(), "w"), thickness=8.0)
        P.run(root, "check")
        with open(os.path.join(root, "generators", "profile.py"), "a", encoding="utf-8") as fh:
            fh.write("\n\ndef leaky(ctx):\n    import os\n"
                     "    open(os.path.join(ctx.root, 'claims', 'C9.json'), 'w').write('{}')\n"
                     "    open(os.path.join(ctx.out_dir, 'x.txt'), 'w').write('x')\n")
        P.write_json(os.path.join(root, "milestones", "leak.json"),
                     {"description": "a leak", "requires": REQUIRES,
                      "generator": "generators/profile.py:leaky"})
        proc = P.run(root, "export", "leak", "--dry-run")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("claims/C9.json", proc.stdout)
        self.assertRegex(proc.stdout, r"(?m)^export: would refuse — .*generator")
        os.remove(os.path.join(root, "claims", "C9.json"))


# --------------------------------------------------------------------------- #
# invariant 12's second sentence: every reader but export says ready as last
# evaluated (review of P2.5b, findings 3, 5, 10, 18)
# --------------------------------------------------------------------------- #
#: A *ready* sentence: the project's or a milestone's head.
READY_SAID = re.compile(r"\bis ready(?: for \S+?)?(?P<tail>[:,][^.]*)")


def cache_ready_problems(text: str) -> list[str]:
    """Each place ``text`` says *ready* without saying it is as last evaluated
    — a cache reader's words (invariant 12)."""
    return [m.group(0) for m in READY_SAID.finditer(text)
            if not m.group("tail").startswith(", as last evaluated")]


class EveryCacheReaderSaysAsLastEvaluated(_env.EnvCase):
    """(Invariant 12; D17) Every reader but `export` reads the verdict cache,
    which the inner loop never re-executes and a hand can forge: its *ready* is
    as last evaluated, and it says so — `report`, `report --milestone` (and its
    JSON, which carries the milestone's own predicate), `status` (which lists
    every milestone's line) — while `export <m> --dry-run`, the boundary, says
    it plainly over its re-run. What slipped through (review of P2.5b): on a
    forged cache `report --milestone` opened "v0.1 is ready for print-v1: every
    claim it requires is checked against the current inputs" and implied its
    required claims had been re-run, its JSON was the project's document (ready
    false beside a human page saying ready), and `status` printed no milestone
    at all."""

    _forged: list[str] = []

    @property
    def root(self) -> str:
        """The bracket at 7.0 with a forged pass on `bracket.deflection` and C5-C7
        not required — built once, for the commands' rows only (the in-process
        row needs none, and runs in the fast tier)."""
        if not self._forged:
            import tempfile
            base = tempfile.mkdtemp(prefix="atompipe-last-evaluated-")
            type(self).addClassCleanup(_env._rmtree, base)
            root = bracket(os.path.join(base, "f"), thickness=7.0, git=True)
            P.run(root, "check")
            forge_entry(root, "bracket.deflection", passed=True)
            for cid in ("C5", "C6", "C7"):
                P.edit_claim(root, cid, critical=False)
            self._forged.append(root)
        return self._forged[0]

    def test_a_cache_reader_says_ready_as_last_evaluated(self):
        """Invariant 12's second sentence, in process over the seeds (review of
        P2.5b): the cache's readiness sentence — the project's and each
        milestone's, and `report --milestone`'s whole page — says *ready* only
        "as last evaluated"; the boundary's says it plainly, and the checker
        refuses that as a cache reader's (it is not vacuous)."""
        boundary_ready = 0
        for seed in range(0, ReadyIsOnePredicate.SEEDS, 8):
            ledger, stale = seeded(seed)
            composed = claims_mod.compositions(ledger, stale_gates=stale)
            project = report_mod._verdict_sentence(ledger, composed, None, stale=False,
                                                   markdown=False)
            self.assertEqual(cache_ready_problems(project), [], f"seed {seed}")
            for milestone in _milestones_of(ledger):
                with self.subTest(seed=seed, milestone=milestone.id):
                    cache = report_mod._verdict_sentence(ledger, composed, None, stale=False,
                                                         markdown=False, milestone=milestone)
                    page = report_mod.render_markdown(ledger, None, stale_gates=stale,
                                                      milestone=milestone)
                    self.assertEqual(cache_ready_problems(cache), [])
                    self.assertEqual(cache_ready_problems(page), [])
                    if expected_ready(ledger, composed, milestone):
                        boundary = report_mod._verdict_sentence(
                            ledger, composed, None, stale=False, markdown=False,
                            milestone=milestone, boundary=True)
                        self.assertTrue(cache_ready_problems(boundary), boundary)
                        boundary_ready += 1
        self.assertTrue(boundary_ready, "no seed was ready: the boundary rows are vacuous")


    def test_the_milestone_report(self):
        text = P.run(self.root, "report", "--milestone", MILESTONE, code=0).stdout
        self.assertIn(f"is ready for {MILESTONE}, as last evaluated", text)
        self.assertEqual(cache_ready_problems(text), [])
        self.assertNotIn("re-runs only the evaluators", text,
                         "the cache's report implies its required claims were re-run")
        self.assertIn("Nothing here was re-run", text)

    def test_the_milestone_reports_json_is_the_milestones(self):
        proc, _doc = export_json(self.root, MILESTONE, "--dry-run")
        boundary = json.loads(proc.stdout)
        doc = json.loads(P.run(self.root, "report", "--milestone", MILESTONE, "--json",
                               code=0).stdout)
        project = json.loads(P.run(self.root, "report", "--json", code=0).stdout)
        found = doc.get("milestone") or {}
        self.assertEqual(found.get("name"), MILESTONE)
        self.assertIs(found.get("ready"), True)
        self.assertIs(found.get("last_evaluated"), True)
        self.assertEqual(found.get("required"), REQUIRES)
        self.assertEqual(found.get("unresolved"), [])
        self.assertNotIn("milestone", project)
        self.assertIs(boundary.get("ready"), False,
                      "the boundary agrees with the forged cache: the row is vacuous")

    def test_status_lists_each_milestone_as_last_evaluated(self):
        text = P.run(self.root, "status", code=0).stdout
        lines = text.splitlines()
        head = report_mod.HUMAN["milestone"]["report_head"]
        self.assertIn(head, lines)
        row = lines[lines.index(head) + 1]
        self.assertRegex(row, rf"^  {MILESTONE}: 4 of 4 required claims checked · 0 stale$")
        self.assertEqual(cache_ready_problems(text), [])
        self.assertRegex(text, r"\bis ready, as last evaluated: ")

    def test_the_project_report(self):
        text = P.run(self.root, "report", code=0).stdout
        self.assertRegex(text, r"\bis ready, as last evaluated: ")
        self.assertEqual(cache_ready_problems(text), [])

    def test_the_boundary_says_it_plainly(self):
        """The control: the boundary's sentence over its re-run, at 8.0 where it
        is ready, carries no qualifier — and the checker refuses it as a cache
        reader's (the checker is not vacuous)."""
        eight = bracket(os.path.join(self.tmp(), "e"), thickness=8.0)
        P.run(eight, "check")
        text = P.run(eight, "export", MILESTONE, "--dry-run", code=0).stdout
        self.assertIn(f"is ready for {MILESTONE}: every claim it requires", text)
        self.assertTrue(cache_ready_problems(text))


class TheReproduceBlockRunsAsPrinted(unittest.TestCase):
    """Review of P2.5b (findings 13, 21): REPORT.md's Reproduce block padded
    each export line to 33 columns and put `#` straight after it, so for a
    milestone name of seven characters or more — the bracket's own `print-v1`
    — the line read `--dry-run# re-runs …` and failed in a shell, and it
    listed four milestones and dropped the rest unsaid. Every line, pasted,
    is the command and nothing else, and every milestone has one."""

    def lines(self, names: list[str]) -> list[str]:
        from atompipe.models import Milestone, ProjectMeta
        ledger = Ledger(meta=ProjectMeta(name="t", revision="v0.1"),
                        milestones=[Milestone(id=name, requires=["C1"]) for name in names])
        text = report_mod.render_markdown(ledger, None)
        return [ln for ln in text.splitlines() if ln.startswith("atompipe export ")]

    @staticmethod
    def argv(line: str) -> list[str]:
        """What a POSIX shell runs: the words before the first word that
        starts with `#` (a `#` inside a word starts nothing). `shlex` is not
        that rule — it cuts at a `#` anywhere — so it is not used here."""
        words = line.split()
        cut = next((i for i, word in enumerate(words) if word.startswith("#")), len(words))
        return words[:cut]

    def test_each_line_is_the_command(self):
        names = ["p1", "print-v1", "a-much-longer-milestone-name", "x" * 40, "five", "sixth"]
        found = self.lines(names)
        self.assertEqual(len(found), len(names), found)
        for name, line in zip(names, found):
            with self.subTest(name):
                self.assertEqual(self.argv(line), ["atompipe", "export", name, "--dry-run"])

    def test_the_glued_line_is_caught(self):
        glued = "atompipe export print-v1 --dry-run# re-runs what it requires"
        self.assertNotEqual(self.argv(glued), ["atompipe", "export", "print-v1", "--dry-run"])


# --------------------------------------------------------------------------- #
# V-5 — going ahead is a person's decision
# --------------------------------------------------------------------------- #
def proceed_problems(entry: Mapping[str, Any], *, ids: list[tuple[str, str, str]],
                     why: str) -> list[str]:
    """The sealed record's decision: every unresolved required claim named with
    its status and cause, the reason as typed, the person, their own shell."""
    out = []
    decided = entry.get("proceed") or {}
    got = [(c.get("id"), c.get("status"), c.get("cause")) for c in decided.get("claims") or ()]
    if got != ids:
        out.append(f"proceed.claims {got}, not {ids}")
    if decided.get("why") != why:
        out.append(f"proceed.why {decided.get('why')!r}, not {why!r}")
    if entry.get("who") != P.WHO:
        out.append(f"who {entry.get('who')!r}")
    if entry.get("channel") != "interactive":
        out.append(f"channel {entry.get('channel')!r}")
    return out


class GoingAheadIsAPersonsDecision(_env.EnvCase):
    """(V-5, §6.1, invariant 12) Going ahead with an unresolved required claim
    is a decision a person records in their own shell, typing the milestone's
    name; it names each claim with its status, and it is sealed into the export
    record. From an agent session or a pipe it is refused before anything runs."""

    WHY = "a fit print; C1's fix waits on this one"

    def test_the_channels(self):
        root = bracket(os.path.join(self.tmp(), "b"), thickness=7.0, git=True)
        P.run(root, "check")
        before = tree(root)
        agent = P.run(root, "export", MILESTONE, "--proceed", "--why", self.WHY, agent=True)
        self.assertEqual(agent.returncode, 2, agent.stdout + agent.stderr)
        self.assertIn(f"atompipe export {MILESTONE} --proceed --why", agent.stderr)
        self.assertIn("CLAUDECODE", agent.stderr)
        pipe = P.run(root, "export", MILESTONE, "--proceed", "--why", self.WHY)
        self.assertEqual(pipe.returncode, 2, pipe.stdout + pipe.stderr)
        no_why = P.tty(root, "export", MILESTONE, "--proceed", answer=MILESTONE)
        self.assertEqual(no_why.returncode, 2, no_why.stdout + no_why.stderr)
        wrong = P.tty(root, "export", MILESTONE, "--proceed", "--why", self.WHY,
                      answer="print-v2")
        self.assertEqual(wrong.returncode, 2, wrong.stdout + wrong.stderr)
        self.assertEqual(changed(before, tree(root)), [], "a refused go-ahead wrote something")
        proc = P.tty(root, "export", MILESTONE, "--proceed", "--why", self.WHY,
                     answer=MILESTONE, code=0)
        self.assertIn("decided by", proc.stdout + proc.stderr)
        entry = P.exports(root, MILESTONE)["exports"][-1]
        self.assertEqual(proceed_problems(entry, ids=[("C1", "fail", "failed")], why=self.WHY),
                         [])
        self.assertTrue(os.path.isdir(os.path.join(root, "out", MILESTONE)))

    def test_a_record_that_drops_the_claims_is_caught(self):
        """Planted: an entry writer that drops `proceed.claims`. The check reads
        the sealed record, never the output."""
        entry = {"who": P.WHO, "channel": "interactive",
                 "proceed": {"claims": [{"id": "C1", "status": "fail", "cause": "failed"}],
                             "why": self.WHY}}
        self.assertEqual(proceed_problems(entry, ids=[("C1", "fail", "failed")], why=self.WHY),
                         [])
        planted = dict(entry, proceed={"why": self.WHY})
        self.assertTrue(proceed_problems(planted, ids=[("C1", "fail", "failed")], why=self.WHY))

    def test_ready_needs_no_decision(self):
        root = bracket(os.path.join(self.tmp(), "e"), thickness=8.0, git=True)
        P.run(root, "check")
        proc = P.tty(root, "export", MILESTONE, "--proceed", "--why", "x", answer=MILESTONE,
                     code=0)
        self.assertIn("nothing to decide: every required claim is checked", proc.stdout)
        self.assertIsNone(P.exports(root, MILESTONE)["exports"][-1].get("proceed"))


# --------------------------------------------------------------------------- #
# V-7 — the package is what was recorded
# --------------------------------------------------------------------------- #
def package(root: str) -> dict[str, bytes]:
    base = os.path.join(root, "out", MILESTONE)
    out = {}
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, base).replace(os.sep, "/")] = fh.read()
    return out


CRASHING = '''

def crashing(ctx):
    import os
    with open(os.path.join(ctx.out_dir, "part.svg"), "w") as fh:
        fh.write("<svg/>")
    raise RuntimeError("the generator broke half way")


def silent(ctx):
    return None


def sentinel(ctx):
    with open({called!r}, "w") as fh:
        fh.write("called")
    with open(__import__("os").path.join(ctx.out_dir, "x.txt"), "w") as fh:
        fh.write("x")


def oops(ctx):
    import os
    with open(os.path.join(ctx.root, "model", "oops.txt"), "w") as fh:
        fh.write("oops")
    with open(os.path.join(ctx.out_dir, "x.txt"), "w") as fh:
        fh.write("x")


def linking(ctx):
    import os
    os.link(os.path.join(ctx.root, "cad", "insert.stl"),
            os.path.join(ctx.out_dir, "insert.stl"))


def symlinking(ctx):
    import os
    os.symlink(os.path.join(ctx.root, "cad", "insert.stl"),
               os.path.join(ctx.out_dir, "insert.stl"))


def unseen(ctx):
    import os
    from atompipe import verdicts
    saved = dict(verdicts._HANDLERS)
    verdicts._HANDLERS.clear()
    try:
        with open(os.path.join(ctx.out_dir, "x.txt"), "w") as fh:
            fh.write("x")
    finally:
        verdicts._HANDLERS.update(saved)


def seen(ctx):
    import os
    from atompipe import verdicts
    saved = dict(verdicts._HANDLERS)
    try:
        with open(os.path.join(ctx.out_dir, "x.txt"), "w") as fh:
            fh.write("x")
    finally:
        verdicts._HANDLERS.update(saved)
'''


def as_the_cache_reads_it(package_md: str, article: str) -> str:
    """The package's REPORT.md (the boundary's) as `report --milestone` (the
    cache's) renders the same view: the sentence's "as last evaluated", the
    cache's nothing-re-run line for the boundary's, no test card, and each
    record line without `--article` — every other byte the same (V-7; review
    of P2.5b, findings 3 and 16)."""
    said = report_mod.HUMAN["readiness"]
    text = re.sub(r"\*\*(\S+) is (NOT )?ready for (\S+?):",
                  r"**\1 is \2ready for \3, as last evaluated:", package_md, count=1)
    text = re.sub(r"(?m)^Claims (\S+) does not require are shown as last evaluated: .*$",
                  lambda m: said["nothing_rerun"].format(m=m.group(1), n=len(REQUIRES)),
                  text, count=1)
    text = re.sub(r"(?ms)^### Test card for article [0-9a-f]{12}\n\n```\n.*?```\n\n", "",
                  text, count=1)
    return text.replace(f" --article {article[:12]}", "")


class ThePackageIsWhatWasRecorded(_env.EnvCase):
    """(V-7) The package in `out/<m>/` is what the export record says: two
    exports with no edit between are byte-identical, `MANIFEST.json` names every
    file's digest and the article, the package's `REPORT.md` is `report
    --milestone`'s; a person's file there is never deleted; a refused or broken
    export leaves the older package as it was."""

    def setUp(self):
        super().setUp()
        self.root = bracket(os.path.join(self.tmp(), "b"), thickness=8.0, git=True)
        P.run(self.root, "check")

    def test_two_exports_are_one_package(self):
        root = self.root
        _p, first = export_json(root, MILESTONE, code=0)
        one = package(root)
        _p, second = export_json(root, MILESTONE, code=0)
        self.assertEqual(package(root), one)
        self.assertEqual(first["article"], second["article"])
        self.assertEqual(first["package"], second["package"])
        manifest = json.loads(one["MANIFEST.json"])
        self.assertEqual(manifest.get("article"), first["article"]["hash"])
        listed = manifest.get("files") or {}
        self.assertEqual(set(listed), set(one) - {"MANIFEST.json"})
        import hashlib
        for rel, digest in listed.items():
            self.assertEqual(hashlib.sha256(one[rel]).hexdigest(), digest, rel)
        report = P.run(root, "report", "--milestone", MILESTONE, code=0).stdout
        self.assertEqual(as_the_cache_reads_it(one["REPORT.md"].decode("utf-8"),
                                               first["article"]["hash"]), report)
        model = json.loads(one["model.json"])
        self.assertNotIn("load_n", json.dumps(model), "the package hands the builder a value "
                                                       "the article does not record")

    def test_the_packages_report_records_on_its_article(self):
        """Review of P2.5b (finding 16): the package's REPORT.md — the one
        document a builder reads — said to record results without `--article`,
        and named the article nowhere, so a builder following it bound a fail
        to a design article no reprint could ever answer. It names the article
        on every record line and carries the test card; the card's commands,
        run as printed, record on it — a cross-check whose value agrees
        included (finding 22: a hard-coded `fail` was refused for it)."""
        _p, doc = export_json(self.root, MILESTONE, code=0)
        article = doc["article"]["hash"]
        text = package(self.root)["REPORT.md"].decode("utf-8")
        records = re.findall(r"(?m)^\s*- \*\*Record the result:\*\* (.+)$", text)
        self.assertTrue(records, "the package's REPORT.md has no record line")
        for line in records:
            self.assertIn(f"--article {article[:12]}", line)
        self.assertIn(f"### Test card for article {article[:12]}", text)
        card = text.split(f"### Test card for article {article[:12]}", 1)[1]
        cross = re.search(r"(?m)^  (atompipe claim physical <id> --article [0-9a-f]{12} "
                          r"--measured <value>)", card)
        self.assertIsNotNone(cross, card[:800])
        argv = cross.group(1).replace("<id>", "C1").replace("<value>", "0.3").split()[1:]
        proc = P.run(self.root, *argv, "--detail", "ruler at the tip", code=0)
        entry = P.results(self.root, "C1")["results"][-1]
        self.assertIs(entry["passed"], True, proc.stdout + proc.stderr)
        self.assertEqual(entry["article"]["hash"], article)

    def test_a_persons_file_is_never_replaced(self):
        root = self.root
        export_json(root, MILESTONE, code=0)
        svg = os.path.join(root, "out", MILESTONE, "bracket-profile.svg")
        with open(svg, "a", encoding="utf-8") as fh:
            fh.write("<!-- sliced at 0.2 mm -->")
        edited = package(root)
        doctor = P.run(root, "doctor")
        self.assertIn("bracket-profile.svg", doctor.stdout)
        proc, doc = export_json(root, MILESTONE, code=1)
        self.assertIn("package", refusal_kinds(doc))
        self.assertIn("bracket-profile.svg", proc.stdout)
        self.assertEqual(package(root), edited)
        os.remove(svg)
        shutil.rmtree(os.path.join(root, "out", MILESTONE))
        export_json(root, MILESTONE, code=0)
        with open(os.path.join(root, "out", MILESTONE, "notes.txt"), "w") as fh:
            fh.write("printed on the left printer")
        proc, doc = export_json(root, MILESTONE, code=1)
        self.assertIn("package", refusal_kinds(doc))
        self.assertIn("notes.txt", proc.stdout)
        self.assertTrue(os.path.isfile(os.path.join(root, "out", MILESTONE, "notes.txt")))

    def generator(self, name: str) -> None:
        called = os.path.join(self.tmp(), "called")
        with open(os.path.join(self.root, "generators", "profile.py"), "a",
                  encoding="utf-8") as fh:
            fh.write(CRASHING.format(called=called))
        P.write_json(os.path.join(self.root, "milestones", f"{MILESTONE}.json"),
                     {"description": "the print", "requires": REQUIRES,
                      "generator": f"generators/profile.py:{name}"})
        self.called = called

    def test_a_refused_export_calls_no_generator(self):
        export_json(self.root, MILESTONE, code=0)
        older = package(self.root)
        self.generator("sentinel")
        _projects.set_thickness(self.root, 7.0)
        export_json(self.root, MILESTONE, code=1)
        self.assertFalse(os.path.exists(self.called), "the generator ran on a refusal")
        self.assertEqual(package(self.root), older)

    def test_a_broken_generator_leaves_the_older_package(self):
        export_json(self.root, MILESTONE, code=0)
        older = package(self.root)
        for name, words in (("crashing", "generator errored"), ("silent", "wrote no file"),
                            ("oops", "model/oops.txt")):
            with self.subTest(name):
                self.generator(name)
                proc, doc = export_json(self.root, MILESTONE, code=1)
                self.assertIn("generator", refusal_kinds(doc))
                self.assertIn(words, proc.stdout)
                self.assertEqual(package(self.root), older)
                self.assertFalse(glob.glob(os.path.join(self.root, "out", ".*")),
                                 "scratch left in out/")
                self.assertFalse(glob.glob(os.path.join(self.root, ".atompipe", "out",
                                                        "export-*")),
                                 "scratch left in .atompipe/out/")
                oops = os.path.join(self.root, "model", "oops.txt")
                if os.path.exists(oops):
                    os.remove(oops)

    def test_a_run_the_lock_refuses_leaves_the_running_ones_scratch(self):
        """Review of P2.5b (finding 8): a dry run refused by the build lock
        removed the scratch package the running export was building into, and
        that one refused with a false "generator errored". Another process holds
        the lock here (the test's parent: alive, on this host); the scratch it
        builds in must be as it was."""
        from atompipe import util
        scratch = milestones_mod().scratch_dir(self.root, MILESTONE, True)
        os.makedirs(scratch, exist_ok=True)
        sentinel = os.path.join(scratch, "bracket-profile.svg")
        with open(sentinel, "w", encoding="utf-8") as fh:
            fh.write("<svg/>")
        lock = os.path.join(self.root, ".atompipe", cli_mod.LOCK_NAME)
        with open(lock, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getppid(), "host": util._HOST, "when": "2026-10-04T10:00:00Z",
                       "command": "atompipe export print-v1"}, fh)
        try:
            for argv in (["export", MILESTONE, "--dry-run"], ["export", MILESTONE]):
                with self.subTest(" ".join(argv)):
                    code, _out, err = captured(argv + ["-C", self.root])
                    self.assertEqual(code, 2, err)
                    self.assertIn(str(os.getppid()), err)
                    self.assertTrue(os.path.isfile(sentinel),
                                    "a run the lock refused removed the running one's scratch")
        finally:
            os.remove(lock)

    def test_a_record_that_cannot_be_written_leaves_no_package(self):
        """Review of P2.5b (finding 9): the package was swapped in before its
        record was appended, so an unwritable `exports/` left a package no
        record names — the next export refused all of it as "not written by an
        export", and `doctor` said every package was as written. Now the older
        package stays, the next export writes, and `doctor` judges a package
        with no export record too."""
        export_json(self.root, MILESTONE, code=0)
        older = package(self.root)
        records = len(P.exports(self.root, MILESTONE)["exports"])
        _projects.set_thickness(self.root, 8.5)

        def unwritable(*_args, **_kwargs):
            raise AtompipeError("cannot write exports/print-v1.json: Permission denied")

        with mock.patch.object(store, "append_sealed", unwritable):
            code, out, err = captured(["export", MILESTONE, "-C", self.root])
        self.assertEqual(code, 2, out + err)
        self.assertEqual(package(self.root), older, "a package no record names was left")
        self.assertEqual(len(P.exports(self.root, MILESTONE)["exports"]), records)
        self.assertFalse(glob.glob(os.path.join(self.root, "out", ".*")))
        export_json(self.root, MILESTONE, code=0)
        self.assertEqual(len(P.exports(self.root, MILESTONE)["exports"]), records + 1)

        stray = bracket(os.path.join(self.tmp(), "stray"), thickness=8.0)
        os.makedirs(os.path.join(stray, "out", MILESTONE))
        with open(os.path.join(stray, "out", MILESTONE, "bracket-profile.svg"), "w") as fh:
            fh.write("<svg/>")
        doctor = P.run(stray, "doctor")
        self.assertRegex(doctor.stdout, r"(?m)^\[FAIL\] +exports .*out/print-v1/"
                                        r"bracket-profile\.svg was not written by an export")

    def test_a_linked_project_file_is_refused(self):
        """Review of P2.5b (finding 2): a generator that links a project file
        into its package — hard or symbolic — hands the builder bytes no open
        named, and a package that aliases the project changes with it. Refused,
        named, nothing written."""
        os.makedirs(os.path.join(self.root, "cad"), exist_ok=True)
        with open(os.path.join(self.root, "cad", "insert.stl"), "wb") as fh:
            fh.write(b"solid insert\nendsolid insert\n")
        for name in ("linking", "symlinking"):
            with self.subTest(name):
                self.generator(name)
                proc, doc = export_json(self.root, MILESTONE, "--dry-run", code=1)
                self.assertIn(("generator", "insert.stl"),
                              [(r["kind"], r["subject"]) for r in doc["refusals"]])
                self.assertIn("linked", proc.stdout)
                self.assertFalse(os.path.exists(os.path.join(self.root, "out", MILESTONE)))

    def test_a_file_written_where_the_trace_never_saw_is_untraced(self):
        """Review of P2.5b (finding 2's backstop): a file the generator put in
        its directory with no write the trace saw — a C library's, a channel no
        handler takes — names no read either, so the article cannot be what the
        generator read: it is the whole design (over-prediction, never under).
        The control: the same generator with the hook intact stays traced."""
        for name, traced in (("seen", True), ("unseen", False)):
            with self.subTest(name):
                self.generator(name)
                _proc, doc = export_json(self.root, MILESTONE, "--dry-run", code=0)
                self.assertIs(doc["article"]["traced"], traced)

    def test_a_planted_in_place_writer_is_caught(self):
        """Planted: the package built in place, no scratch directory. The crash
        row then leaves a half package."""
        export_json(self.root, MILESTONE, code=0)
        older = package(self.root)
        self.generator("crashing")
        m = milestones_mod()
        real = _need(m, "scratch_dir")
        with mock.patch.object(m, "scratch_dir",
                               lambda root, name, dry_run: os.path.join(root, "out", name)
                               if not dry_run else real(root, name, dry_run)):
            code, _out, _err = captured(["export", MILESTONE, "-C", self.root])
        self.assertEqual(code, 1)
        self.assertNotEqual(package(self.root), older, "the planted writer was not caught")

    def test_inputs_moved_during_export_are_refused(self):
        """A file a required evaluator read, edited between the judgment and the
        swap (an editor, while a tier-3 re-run took minutes): refused, nothing
        swapped, nothing recorded (critique 4 of the P2.5b design)."""
        m = milestones_mod()
        real = _need(m, "build_package")
        model = os.path.join(self.root, "model", "bracket.py")

        def edits_meanwhile(*args, **kwargs):
            found = real(*args, **kwargs)
            _projects.set_thickness(self.root, 8.5)
            return found

        with mock.patch.object(m, "build_package", edits_meanwhile):
            code, out, err = captured(["export", MILESTONE, "--json", "-C", self.root])
        doc = json.loads(out)
        self.assertEqual(code, 1, err)
        self.assertIn("package", refusal_kinds(doc))
        self.assertIn("moved", json.dumps(doc.get("refusals")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "exports",
                                                     f"{MILESTONE}.json")))
        self.assertTrue(os.path.isfile(model))


# --------------------------------------------------------------------------- #
# the bracket's own milestone (the done criterion)
# --------------------------------------------------------------------------- #
class TheBracketExportsAtEightAndRefusesAtSeven(_env.EnvCase):
    """PLAN-v0.14 §3 row P2's: the bracket exports `print-v1` at 8.0 and refuses
    it at 7.0, from a fresh clone, its cache current."""

    def test_from_a_fresh_clone(self):
        seven = _projects.bracket_copy(os.path.join(self.tmp(), "seven"), migrated=True,
                                       git=True)
        proc, doc = export_json(seven, MILESTONE, code=1)
        self.assertEqual([(r["kind"], r["subject"]) for r in doc["refusals"]],
                         [("unresolved", "C1")])
        self.assertFalse(doc["ready"])
        eight = _projects.bracket_copy(os.path.join(self.tmp(), "eight"), migrated=True,
                                       thickness=8.0, git=True)
        proc, doc = export_json(eight, MILESTONE, code=0)
        self.assertTrue(doc["ready"])
        self.assertTrue(doc["written"])
        self.assertIs(doc["article"]["traced"], True)
        self.assertTrue(os.path.isfile(os.path.join(eight, "out", MILESTONE,
                                                    "bracket-profile.svg")))

    def test_the_print_moves_with_its_material_and_not_its_load(self):
        """Critique 1 of the P2.5b design: the package hands the builder the
        material, so the article records it — a pass on the print reads Stale
        when the material changes. The rated load is no part of the object
        printed: a load change asks for no new print."""
        root = bracket(os.path.join(self.tmp(), "m"), thickness=8.0, git=True)
        P.run(root, "check")
        article = export_json(root, MILESTONE, code=0)[1]["article"]["hash"]
        P.tty(root, "claim", "physical", "C5", "pass", "--detail", "no crazing",
              "--evidence", P.EVIDENCE, "--article", article, answer="C5", code=0)
        status = json.loads(P.run(root, "status", "--json", code=0).stdout)
        self.assertEqual((status["claims"]["C5"], status["statuses"]["C5"]["cause"]),
                         ("verified", "on-article"))
        model = os.path.join(root, "model", "bracket.py")
        with open(model, encoding="utf-8") as fh:
            text = fh.read()
        for old_, new_, moved in (('    load_n: float = 15.0', '    load_n: float = 14.0', False),
                                  ('    material: str = "petg"', '    material: str = "pla"',
                                   True)):
            with self.subTest(new_.strip()):
                self.assertEqual(text.count(old_), 1, old_)
                with open(model, "w", encoding="utf-8") as fh:
                    fh.write(text.replace(old_, new_))
                status = json.loads(P.run(root, "status", "--json", code=0).stdout)
                with open(model, "w", encoding="utf-8") as fh:
                    fh.write(text)
                want = ("stale", "article-moved") if moved else ("verified", "on-article")
                self.assertEqual((status["claims"]["C5"], status["statuses"]["C5"]["cause"]),
                                 want)
                rebuilt = [r["article"] for r in status.get("rebuild") or ()]
                self.assertEqual(rebuilt, [article] if moved else [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
