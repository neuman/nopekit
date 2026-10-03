# SPDX-License-Identifier: Apache-2.0
"""An owner written into a claim file by hand — or by an agent — never counts.

GLOSSARY §3: *Assumed* is "accepted provisionally with a named reason and owner",
and with no owner the claim reads Gap. PLAN-v0.14 §1.4: the owner is set only
through the signing channel (D-12, D-13); "an owner written any other way — a
hand or agent edit of the claim file — reads unattributed and the claim stays
Gap". P2.1 adds `Claim.owner` (a nominee) and reads it only against `owners`,
the attributions the channel produces — and nothing in P2.1 produces one. So an
owner typed into `claims/C6.json` names who should record it, and changes
nothing else, on any channel.

* **AnOwnerWrittenByHandNeverCounts** (planned invariant 11, `test_meta`'s
  `PLANNED_INVARIANT_CLASSES[11]`: "a human or physical terminal is satisfied only
  by a result entered through a channel the proposer cannot author") — D8's rows
  in process, then end to end on a bracket copy whose `claims/C6.json` carries
  `"owner": "Sam"`, written as an agent's Edit would write it: C6 reads Gap with
  the unattributed reason naming Sam on `check` (exit 1, C6 listed), its JUnit
  report (C6 red), `status` and its JSON, the report and its JSON, `claim
  list`/`show`, `why` and `state.json` — and never the word *assumed*. No command
  writes the field, and the strict reader accepts `owner` and refuses `ownr`,
  naming it. It moves to `INVARIANT_CLASSES[11]` with CLAUDE.md's 11, when the
  signing channel lands.
* **ARecordedResultNeverOutranksTheEvaluators** — the same copy's C5 is tagged
  so the bracket's failing deflection evaluator covers it: a physical pass typed
  with `claim physical` leaves C5 Failing, and the command's own printed row
  says so (review of the P2.1 design: it printed `[ok   ]` from the result alone,
  a second status producer, while `status` read Failing).

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_owner.py -v
"""
from __future__ import annotations

import ast
import json
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from typing import Any, NamedTuple
from unittest import mock

import _env
import _projects
from atompipe import claims as claims_mod
from atompipe import report as report_mod
from atompipe import store as store_mod
from atompipe.models import ClaimKind, ClaimStatus, Ledger, ProjectMeta
from atompipe.util import AtompipeError

#: The name written into C6's file, as an agent's Edit would.
NOMINEE = "Sam"

#: The reason the unattributed claim must carry (P2.1-D15), typed here.
UNATTRIBUTED = f"owner {NOMINEE} is named in claims/C6.json and has not recorded it"

_RUNS = (("check", ["check", "--junit"]), ("status", ["status"]),
         ("status.json", ["status", "--json"]), ("report", ["report"]),
         ("report.json", ["report", "--json"]), ("claim.list", ["claim", "list"]),
         ("claim.list.json", ["claim", "list", "--json"]),
         ("claim.show", ["claim", "show", "C6"]), ("why", ["why", "C6"]),
         ("site.init", ["site", "init"]), ("site.build", ["site", "build"]),
         # Last: it records a result, and the runs above read C5 without one.
         ("claim.physical", ["claim", "physical", "C5", "--pass", "--detail", "looked fine"]),
         ("claim.physical.json", ["status", "--json"]))


class _Owned(NamedTuple):
    root: str
    out: dict[str, Any]
    junit: str
    state: dict
    c6_bytes: bytes


_OWNED: list[_Owned] = []


def _owned_project() -> _Owned:
    """The bracket, migrated, with C6 naming an owner by hand and C5 tagged so
    the failing deflection evaluator covers it; every command run once."""
    if _OWNED:
        return _OWNED[0]
    tmp = tempfile.mkdtemp(prefix="atompipe-owner-")
    unittest.addModuleCleanup(_env._rmtree, tmp)
    root = _projects.bracket_copy(os.path.join(tmp, "bracket"), migrated=True)
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    path = os.path.join(root, "claims", "C6.json")
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    record["owner"] = NOMINEE
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    with open(path, "rb") as fh:
        c6_bytes = fh.read()
    c5 = os.path.join(root, "claims", "C5.json")
    with open(c5, encoding="utf-8") as fh:
        record = json.load(fh)
    record["tags"] = ["stiffness"]
    with open(c5, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    out = {key: _env.atompipe(argv, cwd=root, home=home) for key, argv in _RUNS}
    for key, proc in out.items():
        want = 1 if key == "check" else 0
        if proc.returncode != want:
            raise AssertionError(f"`atompipe {key}` exited {proc.returncode}, not {want}:\n"
                                 f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    with open(os.path.join(root, ".atompipe", "out", "junit.xml"), encoding="utf-8") as fh:
        junit = fh.read()
    with open(os.path.join(root, "site", "data", "state.json"), encoding="utf-8") as fh:
        state = json.load(fh)
    run = _Owned(root, out, junit, state, c6_bytes)
    _OWNED.append(run)
    return run


def _c6_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if re.match(r"^\[.{5}\] C6\b", ln)]


def owner_problems(run: _Owned) -> list[str]:
    """Every channel where C6 — an owner in its file, no attribution — reads
    anything but Gap with the unattributed reason, or says *assumed*."""
    out: list[str] = []
    check = run.out["check"].stdout
    rows = _c6_lines(check)
    if not rows or not rows[0].startswith("[gap  ] C6") or not rows[0].endswith(UNATTRIBUTED):
        out.append(f"check: C6 {rows}")
    case = ET.fromstring(run.junit).find("testsuite[@name='claims.critical']/testcase[@name='C6']")
    kids = [(k.tag, k.get("type"), k.get("message")) for k in case] if case is not None else []
    if kids != [("failure", "unclaimed", UNATTRIBUTED)]:
        out.append(f"check.junit: C6 {kids}")
    for key in ("status.json", "report.json"):
        doc = json.loads(run.out[key].stdout)
        view = (doc.get("statuses") or {}).get("C6") or {}
        if (doc["claims"].get("C6"), view.get("word"), view.get("cause"), view.get("reason")) \
                != ("unclaimed", "gap", "owner-unattributed", UNATTRIBUTED):
            out.append(f"{key}: C6 {doc['claims'].get('C6')} {view}")
        if "C6" not in doc["summary"].get("blocking_ids", []):
            out.append(f"{key}: C6 is not blocking")
    status = _c6_lines(run.out["status"].stdout)
    if not status or not status[0].startswith("[gap  ] C6") or UNATTRIBUTED not in status[0]:
        out.append(f"status: C6 {status}")
    report = run.out["report"].stdout
    gaps = report.split("## Gaps", 1)[-1].split("\n## ", 1)[0]
    if not re.search(rf"^- \*\*C6\*\* .* — {re.escape(UNATTRIBUTED)}$", gaps, re.M):
        out.append("report: C6 is not under Gaps with its reason")
    assumed = report.split("## Assumed", 1)[-1].split("\n## ", 1)[0]
    if re.search(r"\bC6\b", assumed):
        out.append("report: C6 is under Assumed")
    listed = _c6_lines(run.out["claim.list"].stdout)
    if not listed or not listed[0].startswith("[gap  ] C6"):
        out.append(f"claim.list: C6 {listed}")
    rows = {r["id"]: r for r in json.loads(run.out["claim.list.json"].stdout)["claims"]}
    if (rows["C6"].get("status"), rows["C6"].get("cause")) != ("unclaimed", "owner-unattributed"):
        out.append(f"claim.list.json: C6 {rows['C6'].get('status')}/{rows['C6'].get('cause')}")
    if not run.out["claim.show"].stdout.startswith("[gap  ] C6"):
        out.append(f"claim.show: {run.out['claim.show'].stdout.splitlines()[:1]}")
    why = run.out["why"].stdout
    if UNATTRIBUTED not in " ".join(why.split()):
        out.append("why: C6's line does not name the unattributed owner")
    row = next((r for r in run.state.get("claims") or () if r.get("id") == "C6"), {})
    if (row.get("status"), row.get("cause"), row.get("reason")) != (
            "unclaimed", "owner-unattributed", UNATTRIBUTED):
        out.append(f"site: C6 {row.get('status')}/{row.get('cause')}")
    for key in ("check", "status", "report", "claim.list", "claim.show", "why"):
        for line in run.out[key].stdout.splitlines():
            if re.search(r"\bC6\b", line) and re.search(r"\bassumed\b", line, re.I):
                out.append(f"{key}: says assumed for C6: {line[:80]}")
    return out


class AnOwnerWrittenByHandNeverCounts(_env.EnvCase):
    """(planned 11) An owner named in a claim file is a nominee, never an
    attribution: the assumption reads Gap until the signing channel records it."""

    # -- in process: D8's rows -------------------------------------------- #
    def _claim(self, **kw):
        from atompipe.models import Claim
        fields = dict(id="C6", statement="the load is static", kind=ClaimKind.ASSUMPTION,
                      rationale="no fatigue term", owner=NOMINEE)
        fields.update(kw)
        return Claim(**fields)

    def test_an_owner_counts_only_as_recorded_and_only_as_it_still_reads(self):
        signed = {"C6": claims_mod.Attribution(NOMINEE, "no fatigue term")}
        cases = {
            "an owner in the file only": (self._claim(), None, "owner-unattributed"),
            "no owner named": (self._claim(owner=""), signed, "no-owner"),
            "no reason": (self._claim(rationale=""), signed, "no-reason"),
            "recorded under another name": (self._claim(owner="Alex"), signed,
                                            "owner-unattributed"),
            "the rationale edited after": (self._claim(rationale="it is fine"), signed,
                                           "owner-unattributed"),
            "recorded, as it reads": (self._claim(), signed, "owned"),
        }
        for name, (claim, owners, cause) in cases.items():
            with self.subTest(name):
                found = claims_mod.compose(claim, [], owners=owners)
                self.assertEqual(found.cause.value, cause)
                self.assertEqual(found.status, ClaimStatus.ASSERTED if cause == "owned"
                                 else ClaimStatus.UNCLAIMED)

    def test_a_compose_that_trusts_the_file_is_caught(self):
        """Planted: a composition that reads `claim.owner` as attributed — C6
        Assumed with nothing recorded — and a report that lists it so."""
        real = claims_mod.compose

        def trusts(claim, verdicts, **kw):
            if claim.owner:
                kw["owners"] = {claim.id: claims_mod.Attribution(claim.owner, claim.rationale)}
            return real(claim, verdicts, **kw)

        ledger = Ledger(meta=ProjectMeta(name="t", revision="v0.1"), claims=[self._claim()])
        with mock.patch.object(claims_mod, "compose", trusts):
            md = report_mod.render_markdown(ledger, None)
            status = claims_mod.resolve_status(ledger.claims[0], [])
        self.assertEqual(status, ClaimStatus.ASSERTED)
        assumed = md.split("## Assumed", 1)[1].split("\n## ", 1)[0]
        self.assertIn("**C6**", assumed, "the planted compose did not reach the report")

    # -- end to end ---------------------------------------------------------- #
    def test_every_channel_reads_gap_with_the_unattributed_reason(self):
        self.assertEqual(owner_problems(_owned_project()), [])

    def test_the_channel_checks_refuse_what_they_forbid(self):
        """Planted: the outputs a trusting spine would print — C6 `[assum]` on
        `status`, Assumed in JSON, a skipped JUnit case saying "assumed"."""
        run = _owned_project()
        status = run.out["status"].stdout.replace("[gap  ] C6", "[assum] C6")
        doc = json.loads(run.out["status.json"].stdout)
        doc["claims"]["C6"] = "asserted"
        junit = re.sub(r'(<testcase[^>]*name="C6"[^>]*>).*?(</testcase>)',
                       r'\1<skipped message="assumed" />\2', run.junit, count=1, flags=re.S)

        class _Out(NamedTuple):
            stdout: str

        planted = run._replace(junit=junit, out=dict(run.out, status=_Out(status),
                                                     **{"status.json": _Out(json.dumps(doc))}))
        found = owner_problems(planted)
        for want in ("status: C6", "status.json: C6", "check.junit: C6"):
            with self.subTest(want):
                self.assertTrue(any(p.startswith(want) for p in found), found)

    def test_no_command_writes_the_owner(self):
        """Every command above left C6's file as the hand edit wrote it, and no
        code under `src/atompipe` sets `owner` on a claim: the field's only
        producer is a file edit, and the edit counts for nothing (the signing
        channel, when it lands, writes attributions, not this field)."""
        run = _owned_project()
        with open(os.path.join(run.root, "claims", "C6.json"), "rb") as fh:
            self.assertEqual(fh.read(), run.c6_bytes)
        hits = []
        src = os.path.join(_env.REPO, "src", "atompipe")
        for name in sorted(os.listdir(src)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(src, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                # A record built or copied with an owner: `Claim(owner=…)`,
                # `replace(claim, owner=…)`. (`str.format(owner=…)` is a word.)
                if isinstance(node, ast.Call) and any(k.arg == "owner" for k in node.keywords):
                    func = node.func
                    called = func.attr if isinstance(func, ast.Attribute) else getattr(
                        func, "id", "")
                    if called in ("Claim", "replace"):
                        hits.append(f"{name}:{node.lineno}")
                if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Attribute) and target.attr == "owner" \
                                and name != "models.py":
                            hits.append(f"{name}:{target.lineno}")
        self.assertEqual(hits, [], "a spine path sets Claim.owner")

    def test_the_strict_reader_accepts_owner_and_names_a_misspelling(self):
        root = self.tmp()
        good = os.path.join(root, "C6.json")
        with open(good, "w", encoding="utf-8") as fh:
            json.dump({"statement": "s", "kind": "assumption", "owner": NOMINEE}, fh)
        self.assertEqual(store_mod.read_record(good, "claims").owner, NOMINEE)
        bad = os.path.join(root, "C7.json")
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump({"statement": "s", "kind": "assumption", "ownr": NOMINEE}, fh)
        with self.assertRaises(AtompipeError) as caught:
            store_mod.read_record(bad, "claims")
        self.assertIn("ownr", str(caught.exception))
        self.assertIn("owner", str(caught.exception))


def physical_row_problems(stdout: str) -> list[str]:
    """`claim physical C5 --pass`'s printed row, beside the failing deflection
    evaluator that covers C5: Failing, citing that evaluator."""
    rows = [ln for ln in stdout.splitlines() if re.match(r"^\[.{5}\] C5 ", ln)]
    if len(rows) != 1:
        return [f"{len(rows)} rows for C5"]
    if not rows[0].startswith("[FAIL ] C5 "):
        return [f"C5 tagged {rows[0][:7]}"]
    if "bracket.deflection : 0.700 mm" not in rows[0]:
        return ["C5's reason does not cite the failing evaluator"]
    return []


class ARecordedResultNeverOutranksTheEvaluators(_env.EnvCase):
    """A physical pass typed with `claim physical` beside a covering evaluator
    that fails leaves the claim Failing (S-49, R-3), and the command's own row
    says so — `claim physical` prints the status every reader composes, never
    one built from the result alone (R-5)."""

    def test_the_command_prints_the_composed_status(self):
        run = _owned_project()
        self.assertEqual(physical_row_problems(run.out["claim.physical"].stdout), [])
        after = json.loads(run.out["claim.physical.json"].stdout)
        self.assertEqual(after["claims"]["C5"], "fail")

    def test_a_status_built_from_the_result_alone_is_caught(self):
        """Planted: the row `860ffa6` printed — from the result alone."""
        planted = "[ok-hw] C5 Survives two winters outdoors — looked fine (unattributed, t)\n"
        self.assertEqual(physical_row_problems(planted), ["C5 tagged [ok-hw]"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
