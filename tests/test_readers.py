# SPDX-License-Identifier: Apache-2.0
"""The report and the site render a resolution; neither keeps a staleness rule of its own.

Before 1.2 there were two copies of "is this verdict current?" — ``cli._staleness``
and ``site._staleness``, one hash of the whole projection each, kept "byte-for-byte"
in sync by a comment (S-28) — and two copies of ``_flat_params`` beside them. The
report and the page were then handed ONE flag for the whole project. Per-gate
content-addressed verdicts give the readers something finer, and a way to get it
wrong: a reader that forgets to ask the resolver serves every cached PASS as current
and never asks whether the gate's control was demonstrated (invariant 7 by
omission). What each class below holds against:

* **ReadersTakeStaleGates.** ``render_markdown``, ``render_terminal`` and
  ``write_report`` take ``stale_gates``: a PASS whose covering gate is stale is
  STALE and never under PROVEN. The PROVEN heading says "current", not "this run"
  — a cached verdict is current without having run this time. And the report
  carries no sweep timestamp and no rho, so a regenerated ``docs/readiness.md``
  changes only when the claims or the verdict outcomes do (S-89, the report half).
* **SiteAgesAreNeverZero.** A verdict's age comes from the obs run that last hit
  or wrote its entry, else the entry's commit time, else it is ``null`` — never 0,
  which renders as "just now".
* **SiteStateHasNoLastRun.** ``meta.last_run`` described the last *sweep*, a
  record the brief removes; ``meta.stale`` is the resolution's.
* **SiteResolvesForItself.** ``site.state``/``site.build`` without a
  ``resolution=`` ask ``verdicts.resolve`` themselves — no generous default that
  lists recorded entries as current — and keep no copy of the rules (S-28, the
  site half).

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_readers.py -v
"""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import re
import shutil
import tempfile
import unittest

from atompipe import gates as gates_mod
from atompipe import report as report_mod
from atompipe import site as site_mod
from atompipe import store as store_mod
from atompipe import verdicts as verdicts_mod
from atompipe.models import (
    Acceptance, Claim, ClaimStatus, Comparator, GateSpec, Ledger, NegativeControl,
    ProjectMeta, Tier, Verdict,
)

import _env

SITE_PY = os.path.join(_env.SRC, "atompipe", "site.py")
REPORT_PY = os.path.join(_env.SRC, "atompipe", "report.py")

#: An ISO-8601 timestamp to the minute — what a sweep time looks like wherever it
#: is printed (`2026-09-11T23:31:00Z`).
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")

#: A rho, full or as an entry-name prefix: 16 or more hex characters in a row.
RHO_LIKE = re.compile(r"\b[0-9a-f]{16,}\b")

#: The words `verdicts.resolve` gives a Fresh PASS whose control was never shown
#: to fail at this version (§3.10 step 3).
UNDEMONSTRATED = "not yet qualified at this version"


# --------------------------------------------------------------------------- #
# gates the resolver can key: module-level functions, digested as this file
# --------------------------------------------------------------------------- #
def _stiffness(ctx):
    """Never called: the verdicts below are recorded, not run."""
    raise AssertionError("a reader must never run a gate")


def _fit(ctx):
    """Never called either."""
    raise AssertionError("a reader must never run a gate")


def _spec(gid: str, claims=("C1",)) -> GateSpec:
    return GateSpec(id=gid, title=gid, claims=list(claims), tier=Tier.INSTANT,
                    negative_control=NegativeControl(fixture="selftest/bad.py"))


def _registry(*pairs) -> gates_mod.Registry:
    """A fresh registry — never `gates.REGISTRY`, which the whole suite shares."""
    registry = gates_mod.Registry()
    for spec, fn in pairs:
        registry.register(spec, fn)
    return registry


def _claim(cid: str = "C1", gates=("r.stiff",)) -> Claim:
    return Claim(id=cid, statement=f"claim {cid} holds",
                 acceptance=Acceptance(quantity="tip deflection", comparator=Comparator.LE,
                                       limit=0.5, units="mm"),
                 gates=list(gates))


def _pass(gid: str = "r.stiff", claims=("C1",)) -> Verdict:
    return Verdict(gate=gid, claims=list(claims), passed=True, measured=0.31, limit=0.5,
                   units="mm", detail="0.310 mm at 15 N (limit 0.5 mm)")


def _proven(md: str) -> str:
    """The PROVEN section's body — and a failure, never "", when it is missing."""
    lines = md.splitlines()
    at = [i for i, line in enumerate(lines)
          if line == report_mod.SECTION_PROVEN
          or line.startswith(report_mod.SECTION_PROVEN + " ")]
    if len(at) != 1:
        raise AssertionError(f"the report has {len(at)} PROVEN headings, not one")
    body = []
    for line in lines[at[0] + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body)


class _Project(_env.EnvCase):
    """A throwaway project: a temp root (never a git repository) with a meta."""

    def setUp(self) -> None:
        self.root = self.tmp()
        store_mod.init(self.root, ProjectMeta(name="readers", revision="v0.3"))

    def _ledger(self, *claims) -> Ledger:
        ledger = store_mod.load(self.root)
        ledger.claims = list(claims) or [_claim()]
        store_mod.save(self.root, ledger)
        return store_mod.load(self.root)

    def _plant(self, registry, verdict: Verdict, *, control: bool = True):
        """Record `verdict` as a cache entry of its registered gate and, with
        `control`, a forged fired control beside it.

        The control is a FORGED admission — `record_control(bad="fail", good="pass")` with no
        run behind it. That is legitimate only in a renderer test: it forges the
        inner loop, and R-9's re-execution at every money boundary (`check
        --force` in CI, P2's `export`) is what a hand-placed entry cannot get
        past. It is here so a test can tell "the page resolved" from "the page
        read PASS", which a registry of never-demonstrated gates cannot show.
        """
        spec, fn = registry.get(verdict.gate)
        written = verdicts_mod.record_verdict(self.root, spec, fn, verdict)
        if control:
            # R-6 (P2.3): the forged form plants a whole qualification — the
            # known-good half passed and, where the walk applies, a walk that
            # made none — or the entry is incomplete and nothing counts (D19).
            verdicts_mod.record_control(
                self.root, spec, fn, bad="fail", detail="planted by a reader test",
                good="pass", mutation=() if verdicts_mod._mutation_applies(fn, self.root)
                else None)
        return written


# --------------------------------------------------------------------------- #
class ReadersTakeStaleGates(unittest.TestCase):
    """The report renders what the resolver found stale — per gate, not per project."""

    def setUp(self) -> None:
        self.registry = _registry((_spec("r.stiff"), _stiffness),
                                  (_spec("r.fit", claims=("C2",)), _fit))
        self.ledger = Ledger(meta=ProjectMeta(name="readers", revision="v0.3"),
                             claims=[_claim("C1", ("r.stiff",)), _claim("C2", ("r.fit",))],
                             verdicts=[_pass("r.stiff"), _pass("r.fit", claims=("C2",))])

    def test_a_stale_pass_is_stale_and_not_under_proven(self):
        md = report_mod.render_markdown(self.ledger, self.registry,
                                        stale_gates=frozenset({"r.stiff"}))
        proven = _proven(md)
        self.assertNotIn("**C1**", proven,
                         "a PASS whose gate is stale is not proof: it was measured "
                         "against inputs that have moved")
        self.assertIn("**C2**", proven, "the stale gate staled only its own claim")
        self.assertIn(f"{report_mod.status_tag(ClaimStatus.STALE)} C1", md,
                      "the stale claim must be listed as STALE, not dropped")

    def test_without_stale_gates_both_are_proven(self):
        """The positive half: the claim above is missing from PROVEN because its
        gate is stale, not because the fixture cannot put it there."""
        proven = _proven(report_mod.render_markdown(self.ledger, self.registry))
        self.assertIn("**C1**", proven)
        self.assertIn("**C2**", proven)

    def test_the_terminal_render_takes_stale_gates(self):
        text = report_mod.render_terminal(self.ledger, self.registry,
                                          stale_gates=frozenset({"r.fit"}))
        rows = [line for line in text.splitlines() if re.match(r"^\[.{5}\] C2 ", line)]
        self.assertEqual(len(rows), 1, text)
        self.assertTrue(rows[0].startswith(f"{report_mod.status_tag(ClaimStatus.STALE)} C2"),
                        rows)
        self.assertFalse(any(re.match(r"^\[.{5}\] C1 ", line) for line in text.splitlines()),
                         "a passing claim is counted, never listed")

    def test_write_report_takes_stale_gates(self):
        root = tempfile.mkdtemp(prefix="atompipe-readers-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        store_mod.init(root, ProjectMeta(name="readers", revision="v0.3"))
        path = report_mod.write_report(root, self.ledger, self.registry,
                                       stale_gates=frozenset({"r.stiff"}))
        with open(path, encoding="utf-8") as fh:
            proven = _proven(fh.read())
        self.assertNotIn("**C1**", proven)
        self.assertIn("**C2**", proven)

    def test_the_proven_heading_says_current(self):
        """A cached verdict is current without having run this time: "this run"
        would be false on every cache hit. The qualifier in GLOSSARY §3's words
        from P2.1 (R-6, words only: *machine-verified* is a Never-say), saying
        what it is not: checked does not mean true."""
        md = report_mod.render_markdown(self.ledger, self.registry)
        self.assertIn(report_mod.SECTION_PROVEN + " (checked: every evaluator passed on the "
                      "current inputs — checked does not mean true)", md.splitlines())

    def test_the_title_names_the_revision_and_nothing_that_moves(self):
        md = report_mod.render_markdown(self.ledger, self.registry)
        self.assertEqual(md.splitlines()[0], "# readers — readiness (v0.3)")

    def test_no_timestamp_and_no_rho_reach_the_report(self):
        """S-89, the report half: a regenerated `docs/readiness.md` changes only
        when the claims or the verdict outcomes do. A legacy ledger still
        carrying a sweep record, and verdicts carrying rho and costs, render
        nothing a re-run would move."""
        legacy = self.ledger.to_dict()
        legacy["last_run"] = {"when": "2026-09-11T23:31:00Z", "tier": 0,
                              "model_hash": "321107b55616", "inputs_hash": "e3b0c44298fc",
                              "spine_version": "0.1.0", "duration_s": 12.5}
        for row in legacy["verdicts"]:
            row.update(rho="ab" * 32, duration_s=3.25, cpu_s=3.0)
        md = report_mod.render_markdown(Ledger.from_dict(legacy), self.registry)
        self.assertIsNone(TIMESTAMP.search(md), TIMESTAMP.search(md))
        self.assertIsNone(RHO_LIKE.search(md), RHO_LIKE.search(md))
        for moving in ("321107b55616", "e3b0c44298fc", "12.5", "3.25"):
            self.assertNotIn(moving, md)

    def test_a_rerun_with_the_same_outcomes_renders_the_same_bytes(self):
        """The same claims and outcomes, recorded at another time with another
        rho and another cost: byte-identical."""
        other = Ledger.from_dict(self.ledger.to_dict())
        other.verdicts = [dataclasses.replace(v, rho="cd" * 32, duration_s=9.0, cpu_s=8.5)
                          for v in other.verdicts]
        self.assertEqual(report_mod.render_markdown(self.ledger, self.registry),
                         report_mod.render_markdown(other, self.registry))

    def test_the_report_reads_no_sweep_record(self):
        """No `last_run` read survives anywhere in the module (S-89)."""
        with open(REPORT_PY, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        reads = [node.lineno for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute) and node.attr == "last_run"]
        self.assertEqual(reads, [], f"report.py reads last_run at lines {reads}")


# --------------------------------------------------------------------------- #
class SiteAgesAreNeverZero(_Project):
    """An age of zero renders as "just now": the exact lie a staleness display
    exists to prevent. Unknown is `null`."""

    def setUp(self) -> None:
        super().setUp()
        self.registry = _registry((_spec("r.stiff"), _stiffness))
        self.ledger = self._ledger()
        self.written = self._plant(self.registry, _pass())

    def _row(self, payload: dict) -> dict:
        rows = [v for v in payload["verdicts"] if v["gate"] == "r.stiff"]
        self.assertEqual(len(rows), 1, payload["verdicts"])
        return rows[0]

    def test_no_obs_no_git_gives_null(self):
        payload = site_mod.state(self.root, self.ledger, self.registry,
                                 now="2026-01-01T00:10:00Z")
        row = self._row(payload)
        self.assertTrue(row["fresh"], "the fixture must be a current row, or the age "
                                      "assertion tests nothing")
        self.assertFalse(row["when"], "no obs run and no commit: nothing dates this row")
        self.assertIsNone(row["age_s"],
                          "an unknown age is null — a 0 here renders as 'just now'")

    def test_an_obs_run_dates_the_row(self):
        verdicts_mod.record_obs(self.root, "r.stiff", entry=self.written.name,
                                when="2026-01-01T00:00:00Z", duration_s=0.01, cpu_s=0.01)
        row = self._row(site_mod.state(self.root, self.ledger, self.registry,
                                       now="2026-01-01T00:10:00Z"))
        self.assertEqual(row["when"], "2026-01-01T00:00:00Z")
        self.assertEqual(row["age_s"], 600.0)

    def test_a_commit_dates_a_row_no_obs_run_names(self):
        if shutil.which("git") is None:
            self.skipTest("git is not on PATH")
        for args in (["init", "--quiet"], ["add", "-A"],
                     ["commit", "--quiet", "--no-gpg-sign", "-m", "the cache"]):
            proc = _env.git(args, cwd=self.root, identity=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        committed = _env.git(["log", "-1", "--format=%cI"], cwd=self.root).stdout.strip()
        row = self._row(site_mod.state(self.root, self.ledger, self.registry,
                                       now="2099-01-01T00:00:00Z"))
        self.assertTrue(row["when"], "a committed entry is dated by its commit")
        self.assertEqual(site_mod._parse_iso(row["when"]), site_mod._parse_iso(committed))
        self.assertGreater(row["age_s"], 0.0)

    def test_an_unknown_now_is_null_too(self):
        verdicts_mod.record_obs(self.root, "r.stiff", entry=self.written.name,
                                when="2026-01-01T00:00:00Z", duration_s=0.01, cpu_s=0.01)
        self.assertIsNone(self._row(site_mod.state(self.root, self.ledger,
                                                   self.registry))["age_s"])


# --------------------------------------------------------------------------- #
class SiteStateHasNoLastRun(_Project):
    """`meta.last_run` described the last sweep — a record the brief removes, and
    a global answer to a per-gate question."""

    def setUp(self) -> None:
        super().setUp()
        self.registry = _registry((_spec("r.stiff"), _stiffness))
        self._plant(self.registry, _pass())
        legacy = self._ledger().to_dict()
        legacy["last_run"] = {"when": "2026-09-11T23:31:00Z", "tier": 0,
                              "model_hash": "321107b55616"}
        self.ledger = Ledger.from_dict(legacy)

    def test_meta_has_no_last_run(self):
        meta = site_mod.state(self.root, self.ledger, self.registry,
                              now="2026-09-12T00:00:00Z")["meta"]
        self.assertNotIn("last_run", meta)
        self.assertNotIn("321107b55616", json.dumps(meta))

    def test_the_built_page_has_none_either(self):
        site_mod.scaffold(self.root)
        site_mod.build(self.root, self.ledger, self.registry, site_mod.ViewRegistry(),
                       now="2026-09-12T00:00:00Z")
        with open(os.path.join(self.root, site_mod.SITE_DIR, site_mod.DATA_DIR,
                               site_mod.STATE_NAME), encoding="utf-8") as fh:
            self.assertNotIn("last_run", json.load(fh)["meta"])

    def test_meta_stale_is_the_resolutions(self):
        current = site_mod.state(self.root, self.ledger, self.registry)["meta"]
        self.assertFalse(current["stale"], current)
        resolution = verdicts_mod.resolve(self.root, self.registry, None, self.ledger)
        forced = dataclasses.replace(resolution, stale_gates=frozenset({"r.stiff"}))
        rows = dict(forced.rows)
        rows["r.stiff"] = dataclasses.replace(rows["r.stiff"], fresh=False,
                                              stale_reason="config.width 30.0 -> 32.0")
        forced = dataclasses.replace(forced, rows=rows)
        meta = site_mod.state(self.root, self.ledger, self.registry,
                              resolution=forced)["meta"]
        self.assertTrue(meta["stale"])
        self.assertIn("r.stiff", meta["stale_reason"],
                      "the stale reason names the gate, not a hash of the project")
        self.assertIn("config.width 30.0 -> 32.0", meta["stale_reason"])


# --------------------------------------------------------------------------- #
class SiteResolvesForItself(_Project):
    """No generous default (§3.10, SF PD-07): a caller that forgets to hand the
    page a resolution gets the resolver's answer, never "every recorded entry is
    current"."""

    def setUp(self) -> None:
        super().setUp()
        self.registry = _registry((_spec("r.stiff"), _stiffness))
        self.ledger = self._ledger()

    def _claim_status(self, payload: dict) -> str:
        return {row["id"]: row["status"] for row in payload["claims"]}["C1"]

    def test_state_without_a_resolution_is_not_generous(self):
        """A Fresh PASS whose control was never demonstrated: it reaches the page,
        and it does not read PASS there. R-6 (P2.3): never qualified, it reads
        not yet qualified — the row ``unqualified``, the claim Gap — where P2.2
        read it Stale (``control not demonstrated``)."""
        self._plant(self.registry, _pass(), control=False)
        payload = site_mod.state(self.root, self.ledger, self.registry,
                                 now="2026-01-01T00:00:00Z")
        rows = [v for v in payload["verdicts"] if v["gate"] == "r.stiff"]
        self.assertEqual(len(rows), 1, "the cached verdict must reach the page — "
                                       "hidden, it would read as never run")
        self.assertNotEqual(self._claim_status(payload), "pass",
                            "a PASS from a gate never shown to fail is not proof")
        self.assertFalse(rows[0]["fresh"])
        self.assertEqual(rows[0]["status"], "unqualified")
        self.assertIn(UNDEMONSTRATED, rows[0]["qualification"]["reason"])
        self.assertEqual(self._claim_status(payload), "unclaimed")
        self.assertFalse(payload["readiness"]["ready"])

    def test_build_without_a_resolution_is_not_generous(self):
        # R-6 (P2.3): the never-demonstrated pass reads not yet qualified (Gap)
        # on the page build writes, where P2.2 read it Stale (`summary["stale"]`).
        self._plant(self.registry, _pass(), control=False)
        site_mod.scaffold(self.root)
        site_mod.build(self.root, self.ledger, self.registry, site_mod.ViewRegistry(),
                       now="2026-01-01T00:00:00Z")
        with open(os.path.join(self.root, site_mod.SITE_DIR, site_mod.DATA_DIR,
                               site_mod.STATE_NAME), encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertNotEqual(self._claim_status(payload), "pass")
        self.assertEqual(self._claim_status(payload), "unclaimed")
        self.assertEqual([v["status"] for v in payload["verdicts"] if v["gate"] == "r.stiff"],
                         ["unqualified"])

    def test_the_same_pass_with_a_demonstrated_control_reads_pass(self):
        """The positive half: the test above fails for the missing control, not
        because the page cannot show a pass."""
        self._plant(self.registry, _pass(), control=True)
        payload = site_mod.state(self.root, self.ledger, self.registry)
        self.assertEqual(self._claim_status(payload), "pass")
        row = [v for v in payload["verdicts"] if v["gate"] == "r.stiff"][0]
        self.assertTrue(row["fresh"])
        self.assertTrue(row["cached"])
        self.assertEqual(row["stale_reason"], "")
        self.assertFalse(payload["meta"]["stale"])

    def test_stale_false_cannot_make_a_stale_gate_current(self):
        """`stale=True` stays an override that marks everything stale; `False`
        is no override at all — a caller cannot declare the cache current. R-6
        (P2.3): the stale gate is a qualified one whose entry's code is
        unrecorded — a never-demonstrated gate reads unqualified now, not
        stale, and would test nothing about the override."""
        verdicts_mod.record_verdict(self.root, None, None, _pass())   # unkeyed code
        spec, fn = self.registry.get("r.stiff")
        verdicts_mod.record_control(
            self.root, spec, fn, bad="fail", detail="planted by a reader test", good="pass",
            mutation=() if verdicts_mod._mutation_applies(fn, self.root) else None)
        payload = site_mod.state(self.root, self.ledger, self.registry, stale=False)
        self.assertNotEqual(self._claim_status(payload), "pass")
        self.assertTrue(payload["meta"]["stale"])

    def test_stale_true_is_still_the_all_stale_override(self):
        self._plant(self.registry, _pass(), control=True)
        payload = site_mod.state(self.root, self.ledger, self.registry, stale=True)
        self.assertEqual(self._claim_status(payload), "stale")
        self.assertTrue(payload["meta"]["stale"])
        self.assertFalse(payload["verdicts"][0]["fresh"])

    def test_a_handed_resolution_is_the_one_rendered(self):
        """`resolution=` is rendered, not second-guessed: the page is a view of
        the resolver, never a second opinion beside it."""
        self._plant(self.registry, _pass(), control=True)
        mine = verdicts_mod.Resolution()          # "nothing has a verdict"
        payload = site_mod.state(self.root, self.ledger, self.registry, resolution=mine)
        self.assertEqual(payload["verdicts"], [])
        self.assertEqual(self._claim_status(payload), "pending")

    def test_a_cached_row_shows_the_tier_and_pack_it_ran_under(self):
        """The resolver re-stamps a stale verdict with the gate's current spec,
        because its claims decide coverage; the page takes the claims from it,
        and shows the tier and pack the entry was measured under beside why it
        is not current. The claim still reads stale, never pass."""
        planted = dataclasses.replace(_pass(claims=("C9",)), tier=Tier.BUILD,
                                      pack="elsewhere")
        verdicts_mod.record_verdict(self.root, None, None, planted)   # unkeyed code
        # R-6 (P2.3): the gate qualified, or its row is "not yet qualified".
        spec, fn = self.registry.get("r.stiff")
        verdicts_mod.record_control(
            self.root, spec, fn, bad="fail", detail="planted by a reader test", good="pass",
            mutation=() if verdicts_mod._mutation_applies(fn, self.root) else None)
        payload = site_mod.state(self.root, self.ledger, self.registry)
        row = [v for v in payload["verdicts"] if v["gate"] == "r.stiff"][0]
        self.assertTrue(row["cached"])
        self.assertFalse(row["fresh"])
        self.assertTrue(row["stale_reason"])
        self.assertEqual((row["tier"], row["pack"]), (int(Tier.BUILD), "elsewhere"))
        self.assertEqual(row["claims"], ["C1"], "coverage is the resolver's, not the entry's")
        self.assertEqual(self._claim_status(payload), "stale")

    def test_the_site_keeps_no_copy_of_the_rules(self):
        """S-28, the site half: no staleness rule, no flattening, no run-history
        dating of its own. `verdicts.resolve` decides what is current and
        `modelio.flat_params` flattens, once each."""
        with open(SITE_PY, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        defined = {node.name for node in ast.walk(tree)
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertEqual(defined & {"_staleness", "_flat_params", "_verdict_dates"}, set())
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertEqual(names & {"last_run", "load_runs", "model_hash", "inputs_hash"},
                         set(), "the site still reads the global staleness inputs")
        called = {f"{node.func.value.id}.{node.func.attr}" for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name)}
        self.assertIn("modelio.flat_params", called)
        self.assertTrue(any(c.endswith(".resolve") for c in called),
                        "the site must ask the resolver when it is not handed a resolution")


if __name__ == "__main__":
    unittest.main(verbosity=2)
