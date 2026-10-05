# SPDX-License-Identifier: Apache-2.0
"""The JSON every command prints keeps its keys and their value domains.

Phase 2 changes what a claim's status IS (PLAN-v0.14 §1.4) and every word a
human reads for one (GLOSSARY §3). It does not change a machine's keys: GLOSSARY
§7 keeps JSON keys and enum values out of the human channels, and moves them only
in the rename pass. A reader that wrote ``if doc["claims"]["C2"] == "pass"`` or
``summary["by_status"]["blocked"]`` keeps working; the words arrive under new
keys beside the old ones (P2.1-D12).

* **JsonSaysTheWord** (V12) — beside every kept enum value, its GLOSSARY word
  and machine token (``statuses[id]``, the rows' ``key``/``word``), the same on
  every document; the crash mark (``errored``) on the claims a crash Skipped and
  no other; ``counts`` summing to ``n_claims``; one reason per claim, whichever
  document prints it.
* **CheckStatusAndCacheAgree** (V13) — what ``check`` wrote to
  ``last_check.json`` is what ``status --json`` reads after it: statuses, the
  errored list, the blocking claims.
* **JsonKeysAreKept** (C) — every key of ``status --json``, ``report --json``,
  ``claim list --json`` rows, ``claim show --json``, ``check --json`` (and its
  ``blocking`` rows), ``state.json``'s claim rows and readiness, and
  ``last_check.json``, as each stood at ``8eb0b3d``: a superset of these literal
  sets, with every status-valued key's values inside ``ClaimStatus`` and
  ``by_status`` keyed by exactly the ten values, summing to ``n_claims``. Written
  as the contract it keeps, so the same test is green before P2.1 and after it.
  One key is named as removed: a ``state.json`` claim row's ``partial`` (P2.1-D18,
  GLOSSARY §3 "Partial goes").

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_json_keys.py -v
"""
from __future__ import annotations

import json
import os
import unittest
from typing import Any

import _env
import test_louder
import test_status_table
from atompipe.models import ClaimStatus

#: The ten enum values a kept status key may hold, typed here (P2.0 D-7: never
#: read from the code under test).
STATUS_VALUES = frozenset({"pass", "fail", "stale", "unclaimed", "blocked", "pending",
                           "unverified", "verified", "refuted", "asserted"})

_SUMMARY = {"blocking_ids", "by_kind", "by_status", "n_blocking", "n_claims",
            "n_covered_claims", "n_critical", "n_gaps", "n_gates", "n_gates_binding",
            "n_verdicts", "ready", "stale"}

#: Every key at `8eb0b3d`, by document and place: what a reader may rely on.
AT_8EB0B3D: dict[str, set[str]] = {
    "status.json": {"claims", "freshness", "gaps", "inputs", "last_check", "meta", "model",
                    "packs", "problems", "root", "site", "stale", "stale_gates",
                    "stale_reason", "summary"},
    "status.json summary": _SUMMARY,
    "report.json": {"claims", "coverage", "coverage_understated", "gaps", "model_error",
                    "problems", "stale", "stale_gates", "stale_reason", "summary", "verdicts"},
    "report.json summary": _SUMMARY,
    "claim.list.json": {"claims", "stale", "stale_gates"},
    "claim.list.json row": {"acceptance", "covered_by", "critical", "gates", "grounded_by",
                            "id", "kind", "note", "physical_result", "rationale", "source",
                            "statement", "status", "tags"},
    "claim.show.json": {"acceptance", "covered_by", "critical", "gates", "grounded_by", "id",
                        "kind", "note", "physical_result", "rationale", "source", "statement",
                        "status", "tags", "verdicts", "why"},
    "check.json": {"blocking", "carried_over", "claims_recorded", "counts", "duration_s",
                   "junit", "model_hash", "notes", "only", "ready", "run", "spine", "stale",
                   "stale_reason", "summary", "tier", "verdicts"},
    "check.json blocking row": {"claim", "statement", "status"},
    "check.json summary": _SUMMARY,
    "state": {"claims", "decisions", "gaps", "inputs", "locator_problems", "meta", "params",
              "readiness", "verdicts", "views"},
    # `partial` left the row in P2.1 (D18): under GLOSSARY §3's composition no
    # Checked claim has an evaluator that did not pass, so nothing is left to mark.
    "state claim row": {"acceptance", "acceptance_render", "critical", "evidence", "gates",
                        "grounded_by", "id", "kind", "note", "physical_result", "rationale",
                        "source", "statement", "status", "tags", "unproven", "verdicts"},
    "state readiness": {"blocking", "counts", "kinds", "n_claims", "n_critical", "n_gaps",
                        "n_gates", "ready", "verdict"},
    "last_check": {"counts", "fingerprint", "influence", "params", "reads", "spine",
                   "statuses", "when", "worst"},
    "last_check worst": {"claim", "detail", "gate"},
}


def documents(run: Any) -> dict[str, Any]:
    """Every JSON document the louder world printed or wrote, parsed. Only what
    the world captured while it existed: its directory goes with its module."""
    return {
        "status.json": json.loads(run.out["status.json"].stdout),
        "report.json": json.loads(run.out["report.json"].stdout),
        "claim.list.json": json.loads(run.out["claim.list.json"].stdout),
        "claim.show.json": json.loads(run.out["claim.show.json"].stdout),
        "check.json": json.loads(run.out["check.json"].stdout),
        "state": json.loads(run.files["state"]),
        "last_check": json.loads(run.files["last_check"]),
    }


def places(docs: dict[str, Any]) -> dict[str, list[dict]]:
    """Each place `AT_8EB0B3D` names -> the dicts found there (rows: every row)."""
    return {
        "status.json": [docs["status.json"]],
        "status.json summary": [docs["status.json"]["summary"]],
        "report.json": [docs["report.json"]],
        "report.json summary": [docs["report.json"]["summary"]],
        "claim.list.json": [docs["claim.list.json"]],
        "claim.list.json row": list(docs["claim.list.json"]["claims"]),
        "claim.show.json": [docs["claim.show.json"]],
        "check.json": [docs["check.json"]],
        "check.json blocking row": list(docs["check.json"]["blocking"]),
        "check.json summary": [docs["check.json"]["summary"]],
        "state": [docs["state"]],
        "state claim row": list(docs["state"]["claims"]),
        "state readiness": [docs["state"]["readiness"]],
        "last_check": [docs["last_check"]],
        "last_check worst": [docs["last_check"]["worst"]],
    }


def kept_key_problems(docs: dict[str, Any]) -> list[str]:
    """What a reader of `8eb0b3d`'s JSON would find missing or moved."""
    out: list[str] = []
    for place, found in places(docs).items():
        if not found:
            out.append(f"{place}: nothing to read (floor)")
        for row in found:
            missing = AT_8EB0B3D[place] - set(row)
            if missing:
                out.append(f"{place}: lost {sorted(missing)}")
    # Every status-valued kept key holds an enum value, never a word.
    status_maps = {"status.json claims": docs["status.json"]["claims"],
                   "report.json claims": docs["report.json"]["claims"],
                   "last_check statuses": docs["last_check"]["statuses"]}
    for where, mapping in status_maps.items():
        if not mapping:
            out.append(f"{where}: empty (floor)")
        for cid, value in mapping.items():
            if value not in STATUS_VALUES:
                out.append(f"{where}: {cid} = {value!r}, not a ClaimStatus value")
    rows = {"claim.list.json row": docs["claim.list.json"]["claims"],
            "check.json blocking row": docs["check.json"]["blocking"],
            "state claim row": docs["state"]["claims"],
            "claim.show.json": [docs["claim.show.json"]]}
    for where, found in rows.items():
        for row in found:
            if row.get("status") not in STATUS_VALUES:
                out.append(f"{where}: {row.get('id', row.get('claim'))} status "
                           f"{row.get('status')!r}, not a ClaimStatus value")
    for where in ("status.json", "report.json", "check.json"):
        summary = docs[where]["summary"]
        by_status = summary.get("by_status") or {}
        if set(by_status) != STATUS_VALUES:
            out.append(f"{where} by_status keys {sorted(by_status)}")
        if sum(by_status.values()) != summary.get("n_claims"):
            out.append(f"{where} by_status sums to {sum(by_status.values())} for "
                       f"{summary.get('n_claims')} claims")
    counts = docs["state"]["readiness"].get("counts") or {}
    if set(counts) != STATUS_VALUES:
        out.append(f"state readiness counts keys {sorted(counts)}")
    return out


class JsonKeysAreKept(_env.EnvCase):
    """(C) Every JSON key a reader had at `8eb0b3d` is still there, and every kept
    status key still holds an enum value."""

    def test_every_kept_key_and_domain(self):
        self.assertEqual(kept_key_problems(documents(test_louder._louder_project())), [])

    def test_the_contract_refuses_what_it_forbids(self):
        """Planted: a summary that lost `ready`, a claim map re-valued to words, a
        `by_status` with an eleventh key, an empty last_check."""
        docs = json.loads(json.dumps(documents(test_louder._louder_project())))
        self.assertEqual(ClaimStatus("blocked").value, "blocked")
        planted = {
            "status.json summary: lost ['ready']":
                lambda d: d["status.json"]["summary"].pop("ready"),
            "status.json claims: C1 = 'failing', not a ClaimStatus value":
                lambda d: d["status.json"]["claims"].__setitem__("C1", "failing"),
            "report.json by_status keys":
                lambda d: d["report.json"]["summary"]["by_status"].__setitem__("errored", 0),
            "last_check statuses: empty (floor)":
                lambda d: d["last_check"]["statuses"].clear(),
        }
        for want, plant in planted.items():
            with self.subTest(want):
                copy = json.loads(json.dumps(docs))
                plant(copy)
                found = kept_key_problems(copy)
                self.assertTrue(any(p.startswith(want) for p in found), found)


#: Each enum value's machine token and word (GLOSSARY §3, §8), typed here.
WORDS: dict[str, tuple[str, str]] = {
    "pass": ("checked", "checked"), "verified": ("checked", "checked"),
    "fail": ("failing", "failing"), "refuted": ("failing", "failing"),
    "stale": ("stale", "stale"), "asserted": ("assumed", "assumed"),
    "unverified": ("pending_build", "pending build"), "unclaimed": ("gap", "gap"),
    "blocked": ("skipped", "skipped"), "pending": ("open", "open"),
}


def word_problems(docs: dict[str, Any], errored: set[str]) -> list[str]:
    """Every place a JSON document's word or token disagrees with its own enum
    value, the crash mark is on the wrong claims, a count does not add up, or
    two documents give one claim two reasons."""
    out: list[str] = []
    reasons: dict[str, set[str]] = {}
    for where in ("status.json", "report.json"):
        doc = docs[where]
        views = doc.get("statuses") or {}
        if set(views) != set(doc["claims"]):
            out.append(f"{where}: statuses name {sorted(views)}, claims {sorted(doc['claims'])}")
        for cid, status in doc["claims"].items():
            view = views.get(cid) or {}
            if (view.get("key"), view.get("word")) != WORDS[status]:
                out.append(f"{where}: {cid} {status} reads {view.get('key')}/{view.get('word')}")
            reasons.setdefault(cid, set()).add(str(view.get("reason")))
        marked = {cid for cid, view in views.items() if view.get("errored")}
        if marked != errored or set(doc.get("errored") or ()) != errored:
            out.append(f"{where}: errored {sorted(marked)} / {sorted(doc.get('errored') or ())}")
        summary = doc["summary"]
        if sum(summary["counts"].values()) != summary["n_claims"]:
            out.append(f"{where}: counts add to {sum(summary['counts'].values())}")
        if summary["errored"] != len(errored):
            out.append(f"{where}: summary.errored {summary['errored']}")
    rows = {"claim.list.json": docs["claim.list.json"]["claims"],
            "state": docs["state"]["claims"], "claim.show.json": [docs["claim.show.json"]],
            "check.json blocking": docs["check.json"]["blocking"]}
    for where, found in rows.items():
        for row in found:
            cid = row.get("id", row.get("claim"))
            if (row.get("key"), row.get("word")) != WORDS[row["status"]]:
                out.append(f"{where}: {cid} {row['status']} reads {row.get('key')}/{row.get('word')}")
            if bool(row.get("errored")) != (cid in errored):
                out.append(f"{where}: {cid} errored {row.get('errored')}")
            if where != "check.json blocking":       # check ran before the model edit
                reasons.setdefault(cid, set()).add(str(row.get("reason")))
    for cid, said in reasons.items():
        if len(said) != 1:
            out.append(f"{cid}: {len(said)} reasons across documents: {sorted(said)}")
    return out


class JsonSaysTheWord(_env.EnvCase):
    """(V12) The words beside the kept enum values, on every JSON document."""

    def test_every_document_says_the_word(self):
        docs = documents(test_louder._louder_project())
        self.assertEqual(word_problems(docs, set(test_louder.ERRORED_CLAIMS)), [])

    def test_a_word_from_coverage_is_caught(self):
        """Planted: a writer that derives the word from coverage — `checked` when
        any verdict passed — so P5, a pass beside a crash, reads checked."""
        docs = json.loads(json.dumps(documents(test_louder._louder_project())))
        docs["status.json"]["statuses"]["P5"].update(key="checked", word="checked")
        found = word_problems(docs, set(test_louder.ERRORED_CLAIMS))
        self.assertIn("status.json: P5 blocked reads checked/checked", found)


def agree_problems(last: dict, status: dict, check: dict, *, moved: set[str] = frozenset()
                   ) -> list[str]:
    """`last_check.json` against `status --json` read after it and `check --json`:
    the same statuses (but the claims a later edit `moved`), errored list and
    blocking claims."""
    out = []
    for cid, value in status["claims"].items():
        if cid not in moved and last["statuses"].get(cid) != value:
            out.append(f"{cid}: last_check {last['statuses'].get(cid)}, status {value}")
    if set(last.get("errored") or ()) != set(status.get("errored") or ()):
        out.append(f"errored: last_check {last.get('errored')}, status {status.get('errored')}")
    if [b["claim"] for b in check["blocking"]] != [c for c in status["summary"]["blocking_ids"]
                                                   if c not in moved]:
        out.append("check --json blocking is not status's")
    return out


class CheckStatusAndCacheAgree(_env.EnvCase):
    """(V13) `check`'s own record of its run, `last_check.json`, and every reader
    after it agree — on the refused world (nothing moved after `check`) and the
    louder world (the claim the model edit moved named and set apart)."""

    def test_on_the_refused_world(self):
        run = test_status_table._refused_project()
        self.assertEqual(agree_problems(run.last_check,
                                        json.loads(run.out["status.json"].stdout),
                                        json.loads(run.out["check.json"].stdout)), [])

    def test_on_the_louder_world(self):
        docs = documents(test_louder._louder_project())
        self.assertEqual(agree_problems(docs["last_check"], docs["status.json"],
                                        docs["check.json"], moved={"C4"}), [])

    def test_a_cache_that_drops_the_refusal_is_caught(self):
        """Planted: a `last_check.json` resolved over the sweep's rows with the
        refusal dropped — C2 reads pass there, and the logger-only claim open."""
        run = test_status_table._refused_project()
        last = json.loads(json.dumps(run.last_check))
        last["statuses"].update(C2="pass", Q1="pending")
        found = agree_problems(last, json.loads(run.out["status.json"].stdout),
                               json.loads(run.out["check.json"].stdout))
        self.assertIn("C2: last_check pass, status unclaimed", found)


if __name__ == "__main__":
    unittest.main(verbosity=2)
