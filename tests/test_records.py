# SPDX-License-Identifier: Apache-2.0
"""Records as files (checkpoint 1.3): the strict reader, the writer, the
generated index, and the one-time migration of a legacy ledger.

What slipped through before this existed:

* **S-40.** A typo'd key in a hand-edited ledger (`rejectd`) was dropped on load
  by the lenient `from_dict`, and the next `check` wrote the ledger back without
  it: the rejected alternatives — the highest-value field there is — were erased
  from disk by a command that reads like a query. The reader of a record FILE is
  strict now, and so is the migration, which refuses before it writes anything.
* **S-45 (the index).** Ingested evidence was hashed once, at ingest, and never
  again; after the bytes were tampered with, `doctor` still said nothing had
  changed. The index recomputes every input's digest from its bytes and says
  `drift` when they no longer match the pin.
* **S-76 (the block).** `init` wrote an ignore file that ALLOWED `ledger.json`
  (`!ledger.json`), so once the ledger became a generated index, the next
  `git add -A` would track an output. The deny-list now lives in a marked block
  the migration rewrites idempotently, keeping every user line.

Invariant 8 is two classes, and neither ever skips:

* `IndexNeverDisagreesWithRecords` — the library half (a hand-edited index loses
  to the records; every record change reaches it; the build reads no clock and
  no listing order) and the CLI half: every command, run after a record was
  edited by hand, leaves `agree()` empty; a legacy project's index arrives with
  its migration and agrees from its first byte.
* `NoCommandWritesARecord` — every record's bytes, mtime and inode survive every
  command that is not a shim, on a migrated project and on a legacy one (where
  nothing migrates); and the property: every path `check` writes is ignored by
  git at the instant it is written or is a new verdict entry, except the marked
  blocks the ensure step writes once and the migration's own files. The instant
  comes from an audit hook in the child (`_WRITES_DRIVER`), the verdict on
  "ignored" from `git check-ignore` in a scratch repository.

The params rule is tested with a FAKE `model_prose`: the migration is a pure
function of the legacy file and what the model states, and the real reader of
what the model states (`modelio.static_param_prose`) is another unit's; a fake
states exactly what each test needs and records that it was asked.

Run:  PYTHONPATH=src python3 -m unittest tests.test_records -v
"""
from __future__ import annotations

import ast
import contextlib
import copy
import fnmatch
import hashlib
import io
import json
import os
import sys
import textwrap
import time
import unittest
from typing import Any, NamedTuple
from unittest import mock

import _env
import _projects
import _transcript
from atompipe import claims as claims_mod
from atompipe import modelio, models, store, util
from atompipe.models import (
    Acceptance,
    Claim,
    ClaimStatus,
    GateSpec,
    InputArtifact,
    Ledger,
    NegativeControl,
    Param,
    PhysicalResult,
    ProjectMeta,
    Rejected,
    Verdict,
)
from atompipe.util import AtompipeError

PHASE_1 = os.path.join(_env.REPO, "docs", "plan", "phase-1.md")

#: The bytes of the one evidence file the rich legacy ledger ingested.
NAPKIN = b"\x89PNG not really a png\n"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _write(path: str, text: str | bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(text, bytes) else "w"
    kw = {} if isinstance(text, bytes) else {"encoding": "utf-8", "newline": "\n"}
    with open(path, mode, **kw) as fh:
        fh.write(text)
    return path


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _tree(root: str) -> dict[str, bytes]:
    """Every file under ``root`` by its root-relative posix path, with its bytes."""
    out: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            out[os.path.relpath(path, root).replace(os.sep, "/")] = _read_bytes(path)
    return out


def _pairs(text: str):
    """JSON with every object as its ordered list of ``(key, value)`` pairs, so
    two documents compare equal only when their key ORDER is equal too."""
    return json.loads(text, object_pairs_hook=lambda pairs: pairs)


def _keys_anywhere(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            found |= _keys_anywhere(item)
    elif isinstance(value, list):
        for item in value:
            found |= _keys_anywhere(item)
    return found


def _rich_legacy() -> dict:
    """A legacy ledger holding one of everything the migration must carry, in the
    shape the 1e09113 spine wrote (every field, keys sorted)."""
    return {
        "meta": {"name": "rich", "summary": "every record kind", "created": "2026-09-01T00:00:00Z",
                 "revision": "v0.3", "model_entry": "model/m.py", "packs": ["cad-solid"],
                 "spine_version": "0.1.0"},
        "claims": [
            {"id": "C1", "statement": "Tip sags no more than 0.5 mm", "kind": "measurable",
             "acceptance": {"quantity": "tip deflection", "comparator": "<=", "limit": 0.5,
                            "limit_hi": None, "units": "mm"},
             "rationale": "visible droop", "source": "intake", "grounded_by": ["napkin-png"],
             "gates": ["g.one"], "tags": ["stiffness"], "critical": True,
             "physical_result": None, "note": ""},
            {"id": "C2", "statement": "The seam holds water", "kind": "physical",
             "acceptance": {"quantity": "", "comparator": "<=", "limit": None, "limit_hi": None,
                            "units": ""},
             "rationale": "", "source": "", "grounded_by": [], "gates": [], "tags": [],
             "critical": True,
             "physical_result": {"passed": True, "when": "2026-09-02", "who": "a tester",
                                 "detail": "dunked 10 min, dry", "evidence": ["inputs/data/dunk.jpg"]},
             "note": "fill, invert, wait ten minutes"},
            {"id": "C3", "statement": "Users mount it at chest height", "kind": "assumption",
             "acceptance": {"quantity": "", "comparator": "<=", "limit": None, "limit_hi": None,
                            "units": ""},
             "rationale": "", "source": "", "grounded_by": [], "gates": [], "tags": [],
             "critical": False, "physical_result": None, "note": "asked two people"},
            {"id": "C4", "statement": "First mode clear of the pump", "kind": "measurable",
             "acceptance": {"quantity": "first natural frequency", "comparator": ">=",
                            "limit": 60.0, "limit_hi": None, "units": "Hz"},
             "rationale": "", "source": "", "grounded_by": [], "gates": [], "tags": ["modal"],
             "critical": True, "physical_result": None, "note": ""},
            {"id": "C5", "statement": "Bolts carry the load", "kind": "measurable",
             "acceptance": {"quantity": "bearing stress", "comparator": "<=", "limit": 20.0,
                            "limit_hi": None, "units": "MPa"},
             "rationale": "", "source": "", "grounded_by": [], "gates": [], "tags": [],
             "critical": True, "physical_result": None, "note": ""},
        ],
        "params": [
            {"name": "thickness", "value": 7.0, "units": "mm", "rationale": "hand-written why",
             "rejected": [{"value": "4.0 mm", "why": "3.75 mm deflection, 7.5x the limit",
                           "evidence": ""}],
             "source": "datasheet p3", "grounded_by": ["napkin-png"], "gates": ["g.one"],
             "derived_from": [], "changed_in": "thicker-arm", "tags": ["structural"]},
            {"name": "width", "value": 30.0, "units": "", "rationale": "the docstring's prose",
             "rejected": [], "source": "", "grounded_by": [], "gates": ["g.one"],
             "derived_from": [], "changed_in": "", "tags": []},
            {"name": "span", "value": 90.0, "units": "", "rationale": "", "rejected": [],
             "source": "", "grounded_by": [], "gates": [], "derived_from": ["width"],
             "changed_in": "", "tags": []},
        ],
        "inputs": [
            {"id": "napkin-png", "path": "inputs/sketches/napkin.png", "url": "",
             "kind": "sketch", "description": "midship section",
             "sha256": hashlib.sha256(NAPKIN).hexdigest(), "bytes": len(NAPKIN),
             "added": "2026-09-01", "licence": "", "note": "",
             "extractions": [{"what": "thickness reads 7 mm", "grounds": ["thickness"],
                              "confidence": "measured", "note": "calipers on the print"}]},
        ],
        "needs": [
            {"id": "N-C4", "claim_ids": ["C4"], "quantity": "first natural frequency",
             "claim_class": "structural-dynamics", "status": "proposed",
             "candidates": [{"name": "modal-fea", "kind": "solver", "why": "beam theory is off",
                             "cost": "~20 min", "install": "pip install x==1.0",
                             "licence": "MIT", "pack_would_be": "modal"}],
             "chosen": "modal-fea", "note": "asked on 09-03"},
            {"id": "N-C5", "claim_ids": ["C5"], "quantity": "bearing stress", "claim_class": "",
             "status": "open", "candidates": [], "chosen": "", "note": ""},
        ],
        "decisions": [
            {"id": "thicker-arm", "title": "Thicker arm", "when": "2026-09-03T10:00:00Z",
             "summary": "7 mm, not 4", "rejected": [{"value": "4.0 mm", "why": "sagged",
                                                      "evidence": "runs/0002"}],
             "params_changed": ["thickness"], "claims_changed": ["C1"], "evidence": [],
             "body": "### Why\n\nIt sagged."},
            {"id": "older", "title": "Older", "when": "2026-09-01T09:00:00Z", "summary": "",
             "rejected": [], "params_changed": [], "claims_changed": [], "evidence": [],
             "body": ""},
        ],
        "verdicts": [
            {"gate": "g.one", "passed": True, "claims": ["C1", "C5"], "measured": 0.1,
             "limit": 0.5, "units": "mm", "detail": "forged", "evidence": [],
             "duration_s": 0.0, "tier": 0, "skipped": False, "skip_reason": "", "error": "",
             "pack": "", "locators": []},
        ],
        "views": [
            {"id": "curve", "kind": "chart", "title": "Deflection", "src": "", "description": "",
             "gates": ["g.one"], "data": {"series": [[1, 2]]}, "meta": {}, "pack": "",
             "order": 10},
        ],
        "last_run": {"when": "2026-09-03T10:00:00Z", "tier": 0, "model_hash": "abc",
                     "inputs_hash": "def", "spine_version": "0.1.0", "duration_s": 0.1},
    }


def _plant_legacy(root: str, data: dict, *, gitignore: str | None = None) -> str:
    """``root`` as the 1e09113 spine left it: the ledger, its ignore file, and
    the one evidence file the rich ledger ingested."""
    dot = os.path.join(root, ".atompipe")
    _write(os.path.join(dot, ".gitignore"),
           _projects.LEGACY_GITIGNORE if gitignore is None else gitignore)
    _write(os.path.join(dot, "ledger.json"),
           json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    _write(os.path.join(root, "inputs", "sketches", "napkin.png"), NAPKIN)
    return root


class _Prose:
    """A fake `model_prose`: states exactly what it is given, records each call.

    The real one (`modelio.static_param_prose`) parses the model entry; this one
    stands in for "what the model states" so each test decides it."""

    def __init__(self, stated: dict[str, dict[str, str]]) -> None:
        self.stated = stated
        self.calls: list[tuple[str, str]] = []

    def __call__(self, root: str, entry: str) -> dict[str, dict[str, str]]:
        self.calls.append((root, entry))
        return copy.deepcopy(self.stated)


def _migrated(test: _env.EnvCase, data: dict | None = None, *, prose=None) -> str:
    root = _plant_legacy(test.tmp(), _rich_legacy() if data is None else data)
    store.migrate_legacy(root, apply=True, when="2026-09-28T00:00:00Z", model_prose=prose)
    return root


def _bracket(test: _env.EnvCase) -> str:
    return _projects.bracket_copy(os.path.join(test.tmp(), "bracket"))


def _registry_covering(*pairs: tuple[str, str]):
    """A fresh registry: one always-passing gate per id, covering the claims
    paired with it in ``(gate id, claim id)`` pairs."""
    from atompipe import gates as gates_mod
    covers: dict[str, list[str]] = {}
    for gid, cid in pairs:
        covers.setdefault(gid, []).append(cid)
    registry = gates_mod.Registry()
    for gid, cids in covers.items():
        registry.register(
            GateSpec(id=gid, claims=cids,
                     negative_control=NegativeControl(fixture="selftest/bad.py")),
            lambda ctx: Verdict(gate="x", passed=True))
    return registry


# --------------------------------------------------------------------------- #
# the strict reader
# --------------------------------------------------------------------------- #
class StrictReader(_env.EnvCase):
    """Q1.9: every refusal names the file, the key and — where there is one —
    the suggestion. The in-memory `from_dict` stays lenient."""

    def setUp(self) -> None:
        self.root = self.tmp()

    def _plant(self, rel: str, text: str) -> str:
        return _write(os.path.join(self.root, *rel.split("/")), text)

    def _refusal(self, rel: str, text: str, kind: str, **kw) -> str:
        path = self._plant(rel, text)
        with self.assertRaises(AtompipeError) as caught:
            store.read_record(path, kind, **kw)
        return str(caught.exception)

    def _names(self, message: str, *needles: str) -> None:
        for needle in needles:
            self.assertIn(needle, message, f"the refusal does not name {needle!r}: {message}")

    def test_a_clean_record_reads_in_any_key_order(self):
        path = self._plant("claims/C1.json", json.dumps({
            "tags": ["stiffness"], "acceptance": {"limit": 0.5, "comparator": "<="},
            "statement": "sags no more than 0.5 mm"}))
        claim = store.read_record(path, "claims")
        self.assertIsInstance(claim, Claim)
        self.assertEqual((claim.id, claim.statement, claim.acceptance.limit, claim.critical),
                         ("C1", "sags no more than 0.5 mm", 0.5, True))
        self.assertEqual(claim.kind, models.ClaimKind.MEASURABLE, "an absent field reads as its default")

    def test_an_unknown_key_is_refused_with_a_suggestion(self):
        message = self._refusal("claims/C1.json", '{"statment": "s"}', "claims")
        self._names(message, "claims/C1.json", '"statment"', '"statement"')

    def test_an_unknown_nested_key_is_refused_with_its_path(self):
        message = self._refusal(
            "claims/C1.json", '{"statement": "s", "acceptance": {"limt": 0.5}}', "claims")
        self._names(message, "claims/C1.json", "acceptance.limt", '"limit"')
        message = self._refusal(
            "params/thickness.json", '{"rejected": [{"value": "4 mm", "whu": "sags"}]}', "params")
        self._names(message, "params/thickness.json", "rejected[0].whu", '"why"')

    def test_a_duplicate_key_is_refused(self):
        message = self._refusal(
            "claims/C1.json", '{"statement": "a", "statement": "b"}', "claims")
        self._names(message, "claims/C1.json", '"statement"', "twice")

    def test_nan_and_infinity_are_refused(self):
        for token in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(token=token):
                message = self._refusal(
                    "claims/C1.json",
                    '{"statement": "s", "acceptance": {"limit": %s}}' % token, "claims")
                self._names(message, "claims/C1.json", "acceptance.limit")

    def test_an_empty_file_is_refused(self):
        for text in ("", "  \n"):
            with self.subTest(text=text):
                self._names(self._refusal("claims/C1.json", text, "claims"),
                            "claims/C1.json", "empty")

    def test_a_non_object_is_refused(self):
        for text in ("[1, 2]", '"C1"', "7"):
            with self.subTest(text=text):
                self._names(self._refusal("claims/C1.json", text, "claims"),
                            "claims/C1.json", "object")

    def test_the_stem_must_agree_with_an_internal_id(self):
        message = self._refusal("claims/C1.json", '{"id": "C2", "statement": "s"}', "claims")
        self._names(message, "claims/C1.json", '"id"', "C2", "C1")
        message = self._refusal("params/thickness.json", '{"name": "width"}', "params")
        self._names(message, "params/thickness.json", '"name"', "width")
        path = self._plant("claims/C3.json", '{"id": "C3", "statement": "s"}')
        self.assertEqual(store.read_record(path, "claims").id, "C3",
                         "an internal id that agrees with the stem is harmless")

    def test_ids_differing_only_in_case_are_refused(self):
        root = self.tmp()
        store.init(root, ProjectMeta(name="case", created="2026-09-28T00:00:00Z"))
        directory = os.path.join(root, "claims")
        _write(os.path.join(directory, "C1.json"), '{"statement": "upper"}\n')
        _write(os.path.join(directory, "c1.json"), '{"statement": "lower"}\n')
        listing = sorted(os.listdir(directory))
        context = contextlib.nullcontext()
        if len(listing) < 2:
            # A case-insensitive filesystem merged the two files: plant the two
            # names the way a case-sensitive clone would list them.
            real = os.listdir
            context = mock.patch("os.listdir", lambda p=".": (
                ["C1.json", "c1.json"] if os.path.abspath(p) == directory else real(p)))
        with context, self.assertRaises(AtompipeError) as caught:
            store.load(root)
        self._names(str(caught.exception), "claims/C1.json", "claims/c1.json", "case")

    def test_each_forbidden_key_names_its_owner(self):
        cases = [
            ("claims/C1.json", '{"statement": "s", "gates": ["g.one"]}', "claims",
             ('"gates"', "derived from gate coverage")),
            ("claims/C1.json", '{"statement": "s", "physical_result": {"passed": true}}',
             "claims", ('"physical_result"', "results/")),
            ("params/thickness.json", '{"value": 7.0}', "params",
             ('"value"', "model/bracket.py")),
            ("params/thickness.json", '{"derived_from": ["width"]}', "params",
             ('"derived_from"', "model/bracket.py")),
            ("params/thickness.json", '{"gates": ["g.one"]}', "params", ('"gates"', "derived")),
            ("params/thickness.json", '{"changed_in": "d"}', "params",
             ('"changed_in"', "derived")),
            ("inputs/napkin-png.json", '{"path": "inputs/sketches/n.png", "bytes": 5}', "inputs",
             ('"bytes"', "computed from the file")),
        ]
        for rel, text, kind, needles in cases:
            with self.subTest(rel=rel, text=text):
                message = self._refusal(rel, text, kind, model_entry="model/bracket.py")
                self._names(message, rel, *needles)

    def test_independence_is_refused_anywhere(self):
        cases = [
            ("claims/C1.json", '{"statement": "s", "independence": "high"}', "claims",
             "independence"),
            ("claims/C1.json", '{"statement": "s", "acceptance": {"independence": 1}}', "claims",
             "acceptance.independence"),
            ("params/thickness.json",
             '{"rejected": [{"value": "4", "why": "w", "independence": "x"}]}', "params",
             "rejected[0].independence"),
            ("views/curve.json", '{"kind": "chart", "data": {"rows": [{"independence": 1}]}}',
             "views", "independence"),
            ("results/C2.json", '{"results": [{"passed": true, "independence": "me"}]}',
             "results", "independence"),
        ]
        for rel, text, kind, where in cases:
            with self.subTest(rel=rel):
                message = self._refusal(rel, text, kind)
                self._names(message, rel, where, "derived from origin")

    def test_a_stray_evidence_json_in_inputs_is_refused(self):
        message = self._refusal("inputs/loads.json", '{"load_n": 15, "unit": "N"}', "inputs")
        self._names(message, "inputs/loads.json", '"load_n"', "atompipe ingest",
                    "inputs/data/", "inputs/measurements/")

    def test_results_are_an_object_holding_a_list(self):
        path = self._plant("results/C2.json",
                           '{"results": [{"passed": true, "who": "a tester"}, {"passed": false}]}')
        results = store.read_record(path, "results")
        self.assertEqual([r.passed for r in results], [True, False])
        self.assertIsInstance(results[0], PhysicalResult)
        for text, needle in (('[{"passed": true}]', "object"),
                             ('{"results": {"passed": true}}', "list"),
                             ('{"result": []}', '"results"'),
                             ('{"results": [{"pased": true}]}', '"passed"')):
            with self.subTest(text=text):
                self._names(self._refusal("results/C2.json", text, "results"),
                            "results/C2.json", needle)

    def test_project_json_is_strict(self):
        root = self.tmp()
        store.init(root, ProjectMeta(name="strict", created="2026-09-28T00:00:00Z"))
        path = os.path.join(root, ".atompipe", "project.json")
        good = json.loads(_read_bytes(path))
        for mutate, needles in (
                (lambda d: d.update(modle_entry="m.py"), ('"modle_entry"', '"model_entry"')),
                (lambda d: d.update(schema=99), ("schema", "99")),
                (lambda d: d.pop("schema"), ("schema",)),
                (lambda d: d.update(independence="x"), ("independence",))):
            with self.subTest(needles=needles):
                data = dict(good)
                mutate(data)
                _write(path, json.dumps(data))
                with self.assertRaises(AtompipeError) as caught:
                    store.read_project(root)
                self._names(str(caught.exception), "project.json", *needles)

    def test_the_in_memory_reader_stays_lenient(self):
        claim = Claim.from_dict({"id": "C1", "statement": "s", "statment": "typo"})
        self.assertEqual(claim.statement, "s")
        self.assertEqual(Ledger.from_dict({"claims": [], "last_run": {}}).claims, [])

    def test_record_dirs_are_the_record_kinds(self):
        self.assertEqual(store.RECORD_DIRS, tuple(models.RECORD_KINDS))


# --------------------------------------------------------------------------- #
# the writer
# --------------------------------------------------------------------------- #
def _phase_1_c1() -> str:
    """phase-1.md's `claims/C1.json` example, as the plan writes it."""
    with open(PHASE_1, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    start = lines.index("// claims/C1.json") + 1
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("```"):
            break
        body.append(line)
    return "\n".join(body)


class RecordWriter(_env.EnvCase):
    def setUp(self) -> None:
        self.root = self.tmp()

    def _written(self, kind: str, record, **kw) -> dict:
        path = store.write_record(self.root, kind, record, **kw)
        self.assertIsNotNone(path)
        return json.loads(_read_bytes(path))

    def test_c1_from_the_bracket_equals_the_phase_1_example(self):
        project = _bracket(self)
        plan = store.migrate_legacy(project, apply=False, when="")
        written = plan.files["claims/C1.json"].decode("utf-8")
        self.assertEqual(_pairs(written), _pairs(_phase_1_c1()),
                         "C1 as migrated is not phase-1's example key for key:\n" + written)
        store.migrate_legacy(project, apply=True, when="2026-09-28T00:00:00Z")
        self.assertEqual(_read_bytes(os.path.join(project, "claims", "C1.json")),
                         plan.files["claims/C1.json"])

    def test_a_second_write_changes_no_byte(self):
        claim = Claim(id="C1", statement="s", acceptance=Acceptance(quantity="q", limit=1.0))
        path = store.write_record(self.root, "claims", claim)
        before = (_read_bytes(path), os.stat(path).st_mtime_ns, os.stat(path).st_ino)
        self.assertIsNone(store.write_record(self.root, "claims", claim))
        self.assertEqual((_read_bytes(path), os.stat(path).st_mtime_ns, os.stat(path).st_ino),
                         before)

    def test_kind_and_comparator_are_always_written(self):
        data = self._written("claims", Claim(id="C1", statement="s",
                                             acceptance=Acceptance(quantity="q", limit=1.0)))
        self.assertEqual(data["kind"], "measurable")
        self.assertEqual(data["acceptance"]["comparator"], "<=")
        for key in ("critical", "note", "source", "grounded_by", "tags", "rationale"):
            self.assertNotIn(key, data, f"{key} at its default was written")
        self.assertNotIn("limit_hi", data["acceptance"])
        bare = self._written("claims", Claim(id="C2", statement="s"))
        self.assertEqual(list(bare), ["statement", "kind"],
                         "an acceptance equal to its whole default is omitted entirely")

    def test_forbidden_keys_are_never_written(self):
        claim = self._written("claims", Claim(
            id="C1", statement="s", gates=["g.one"],
            physical_result=PhysicalResult(passed=True, who="me")))
        param = self._written("params", Param(
            name="thickness", value=7.0, units="mm", gates=["g.one"], derived_from=["w"],
            changed_in="d", source="datasheet"))
        artifact = self._written("inputs", InputArtifact(
            id="napkin-png", path="inputs/sketches/napkin.png", bytes=5, sha256="ab"))
        self.assertFalse({"gates", "physical_result", "id"} & set(claim), claim)
        self.assertEqual(param, {"units": "mm", "source": "datasheet"})
        self.assertFalse({"bytes", "id"} & set(artifact), artifact)
        self.assertEqual(artifact["sha256"], "ab", "the pinned digest is the record's")

    def test_the_bytes_are_indent_two_utf8_and_a_newline(self):
        claim = Claim(id="C1", statement="Ø 5 mm bore — held", tags=["fit"])
        path = store.write_record(self.root, "claims", claim)
        raw = _read_bytes(path)
        self.assertTrue(raw.endswith(b"}\n"))
        self.assertIn("Ø 5 mm bore — held".encode("utf-8"), raw, "non-ASCII must stay literal")
        self.assertIn(b'\n  "tags": [\n    "fit"\n  ]', raw)
        with self.assertRaises(AtompipeError) as caught:
            store.write_record(self.root, "claims", Claim(
                id="C9", statement="s", acceptance=Acceptance(limit=float("nan"))))
        self.assertIn("claims/C9.json", str(caught.exception))
        self.assertFalse(os.path.exists(os.path.join(self.root, "claims", "C9.json")))

    def test_results_are_written_as_an_object(self):
        data = self._written("results", [PhysicalResult(passed=True, who="me"),
                                         PhysicalResult(passed=False)], record_id="C2")
        self.assertEqual(data, {"results": [{"passed": True, "who": "me"}, {"passed": False}]})

    def test_unsafe_ids_are_refused(self):
        for rid in ("", "../escape", "a/b", "a\\b", ".hidden", "C1:x", "  "):
            with self.subTest(rid=rid):
                with self.assertRaises(AtompipeError):
                    store.write_record(self.root, "claims", Claim(id=rid, statement="s"))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.root), "escape.json")))

    def test_a_case_variant_of_an_existing_record_is_refused(self):
        store.write_record(self.root, "claims", Claim(id="C1", statement="s"))
        with self.assertRaises(AtompipeError) as caught:
            store.write_record(self.root, "claims", Claim(id="c1", statement="t"))
        self.assertIn("C1", str(caught.exception))

    def test_what_is_written_reads_back(self):
        records = [
            ("claims", Claim(id="C1", statement="s", kind=models.ClaimKind.PHYSICAL,
                             acceptance=Acceptance(quantity="q", comparator=models.Comparator.BETWEEN,
                                                   limit=1.0, limit_hi=2.0, units="mm"),
                             critical=False, note="n", tags=["t"])),
            ("params", Param(name="thickness", value=None, units="mm", rationale="r",
                             rejected=[Rejected(value="4 mm", why="sags", evidence="e")])),
            ("inputs", InputArtifact(id="napkin-png", path="inputs/sketches/n.png",
                                     kind=models.ArtifactKind.SKETCH, sha256="ab",
                                     extractions=[models.Extraction(what="w", grounds=["g"])])),
        ]
        for kind, record in records:
            with self.subTest(kind=kind):
                path = store.write_record(self.root, kind, record)
                self.assertEqual(store.read_record(path, kind), record)


# --------------------------------------------------------------------------- #
# invariant 8, library half
# --------------------------------------------------------------------------- #
class IndexNeverDisagreesWithRecords(_env.EnvCase):
    """The index is an OUTPUT of the records: a hand edit to it loses, every
    record change reaches it, and its build reads no clock and no listing order."""

    def setUp(self) -> None:
        self.root = _migrated(self)
        self.index = os.path.join(self.root, ".atompipe", "ledger.json")
        self.assertTrue(store.write_index(self.root))

    def _index(self) -> dict:
        with open(self.index, encoding="utf-8") as fh:
            return json.load(fh)

    def _by_id(self, section: str) -> dict:
        key = "name" if section == "params" else "id"
        return {row[key]: row for row in self._index()[section]}

    def test_a_hand_edited_index_loses_to_the_records(self):
        index = self._index()
        next(c for c in index["claims"] if c["id"] == "C1")["acceptance"]["limit"] = 5.0
        index["claims"].append({"id": "C99", "statement": "injected", "kind": "measurable"})
        index["results"]["C5"] = [{"passed": True, "who": "a forger"}]
        _write(self.index, json.dumps(index, indent=2))

        ledger = store.load(self.root)
        self.assertEqual(ledger.claim("C1").acceptance.limit, 0.5)
        self.assertIsNone(ledger.claim("C99"))
        self.assertIsNone(ledger.claim("C5").physical_result)
        problems = store.agree(self.root)
        for needle in ("C1", "acceptance.limit", "C99", "C5"):
            self.assertTrue(any(needle in p for p in problems), (needle, problems))

        self.assertTrue(store.write_index(self.root))
        self.assertEqual(store.agree(self.root), [])
        self.assertEqual(self._by_id("claims")["C1"]["acceptance"]["limit"], 0.5)
        self.assertNotIn("C99", self._by_id("claims"))
        self.assertNotIn("C5", self._index()["results"])

    def test_every_record_change_reaches_the_index(self):
        claims = os.path.join(self.root, "claims")

        def delete():
            os.remove(os.path.join(claims, "C3.json"))

        def add():
            store.write_record(self.root, "claims", Claim(id="C6", statement="added"))

        def rename():
            os.replace(os.path.join(claims, "C4.json"), os.path.join(claims, "C40.json"))

        def edit_in_place():
            path = os.path.join(claims, "C1.json")
            data = json.loads(_read_bytes(path))
            data["acceptance"]["limit"] = 0.4
            _write(path, json.dumps(data, indent=2) + "\n")

        for label, change, check in (
                ("delete", delete, lambda c: "C3" not in c),
                ("add", add, lambda c: c["C6"]["statement"] == "added"),
                ("rename", rename, lambda c: "C4" not in c and "C40" in c),
                ("edit in place", edit_in_place,
                 lambda c: c["C1"]["acceptance"]["limit"] == 0.4)):
            with self.subTest(change=label):
                change()
                self.assertNotEqual(store.agree(self.root), [],
                                    f"the index still agrees after a {label}")
                self.assertTrue(check({r["id"]: r for r in store.build_index(self.root)["claims"]}))
                self.assertTrue(store.write_index(self.root))
                self.assertEqual(store.agree(self.root), [])
                self.assertTrue(check(self._by_id("claims")))

    def test_the_build_is_deterministic(self):
        first = store.build_index(self.root)
        real_listdir = os.listdir

        def reversed_listdir(path="."):
            return list(reversed(sorted(real_listdir(path))))

        def clock(*_a, **_k):
            raise AssertionError("the index build read the clock")

        with mock.patch("os.listdir", reversed_listdir), \
                mock.patch.object(util, "utcnow_iso", clock), \
                mock.patch.object(time, "time", clock), \
                mock.patch.object(time, "time_ns", clock), \
                mock.patch.object(time, "localtime", clock), \
                mock.patch.object(time, "gmtime", clock):
            second = store.build_index(self.root)
        self.assertEqual(json.dumps(first, indent=2), json.dumps(second, indent=2))
        self.assertEqual(json.dumps(first), json.dumps(store.build_index(self.root)))

    def test_the_index_is_ignored_through_fnmatch(self):
        with open(os.path.join(self.root, ".atompipe", ".gitignore"), encoding="utf-8") as fh:
            patterns = [line.strip() for line in fh
                        if line.strip() and not line.lstrip().startswith("#")]

        def ignored(name: str) -> bool:
            return any(fnmatch.fnmatch(name, pat.rstrip("/")) for pat in patterns
                       if not pat.startswith("!"))

        for name in ("ledger.json", "ledger.legacy.json", "cache", "obs", "out", "a.tmp",
                     "build.lock"):
            self.assertTrue(ignored(name), f"{name} is not ignored: {patterns}")
        for name in ("project.json", "verdicts", "packs", "model.json", ".gitignore"):
            self.assertFalse(ignored(name), f"{name} is ignored: {patterns}")
        self.assertFalse([p for p in patterns if p.startswith("!")],
                         "an allow line would re-add an output")

    def test_an_input_rewritten_in_place_drifts(self):
        """S-45: the pin is the record's; the digest is the bytes'."""
        row = self._by_id("inputs")["napkin-png"]
        self.assertEqual((row["sha256"], row["drift"], row["exists"]),
                         (row["pinned"], False, True))
        path = os.path.join(self.root, "inputs", "sketches", "napkin.png")
        st = os.stat(path)
        with open(path, "r+b") as fh:           # same size, same mtime: only the bytes moved
            fh.write(b"\x00")
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        built = {r["id"]: r for r in store.build_index(self.root)["inputs"]}["napkin-png"]
        self.assertNotEqual(built["sha256"], built["pinned"])
        self.assertTrue(built["drift"])
        self.assertTrue(any("napkin" in p and "changed" in p
                            for p in store.build_index(self.root)["problems"]))
        os.remove(path)
        built = {r["id"]: r for r in store.build_index(self.root)["inputs"]}["napkin-png"]
        self.assertEqual((built["exists"], built["sha256"]), (False, None))
        self.assertTrue(any("missing" in p for p in store.build_index(self.root)["problems"]))

    def test_unregistered_inputs_are_hand_dropped_evidence(self):
        _write(os.path.join(self.root, "inputs", "references", "teardown.jpg"), b"jpg")
        _write(os.path.join(self.root, "inputs", "sketches", ".napkin.png.swp"), b"swap")
        _write(os.path.join(self.root, "inputs", "README.md"), "# inputs\n")
        index = store.build_index(self.root)
        self.assertEqual(index["unregistered_inputs"], ["inputs/references/teardown.jpg"])

    def test_the_index_is_written_only_when_it_changes(self):
        before = (_read_bytes(self.index), os.stat(self.index).st_mtime_ns)
        self.assertFalse(store.write_index(self.root))
        self.assertEqual((_read_bytes(self.index), os.stat(self.index).st_mtime_ns), before)

    def test_the_index_holds_no_verdict_status_or_coverage(self):
        """Statuses, coverage and verdicts live in `last_check.json` and the
        verdict cache; an index that carried them could disagree with them."""
        index = self._index()
        self.assertEqual(list(index), [
            "generated", "schema", "records_digest", "meta", "claims", "params", "decisions",
            "needs", "inputs", "results", "views", "unregistered_inputs", "problems"])
        self.assertEqual(index["generated"], store.INDEX_BANNER)
        self.assertEqual(index["schema"], store.PROJECT_SCHEMA)
        self.assertEqual(index["records_digest"], store.records_digest(self.root))
        for row in index["claims"]:
            self.assertFalse({"gates", "status", "physical_result"} & set(row), row)
        for row in index["params"]:
            self.assertFalse({"value", "gates", "derived_from", "changed_in"} & set(row), row)
        # A Need's own `status` (open, proposed, …) is a record field, not a verdict.
        self.assertFalse(_keys_anywhere(index) & {"verdicts", "statuses", "last_run"})

    def test_the_records_digest_moves_with_a_record_and_not_with_the_index(self):
        digest = store.records_digest(self.root)
        _write(self.index, "{}\n")
        self.assertEqual(store.records_digest(self.root), digest)
        store.write_record(self.root, "claims", Claim(id="C7", statement="new"))
        self.assertNotEqual(store.records_digest(self.root), digest)

    def test_a_legacy_project_gets_no_index(self):
        root = _plant_legacy(self.tmp(), _rich_legacy())
        ledger = os.path.join(root, ".atompipe", "ledger.json")
        before = _read_bytes(ledger)
        self.assertTrue(store.is_legacy(root))
        self.assertFalse(store.write_index(root))
        self.assertEqual(store.agree(root), [])
        self.assertEqual(_read_bytes(ledger), before, "the index overwrote a legacy ledger")

    # -- the CLI half: what a person or an agent sees after any command ----- #
    # The library half above proves the index CAN be rebuilt from the records;
    # this half proves every command DOES leave it so, including after a record
    # was edited by hand since the last command — the index is the one file an
    # agent reads first, and a hand edit is how records change (D-06). Each
    # command runs in a fresh process on the enriched bracket (`_migrated_bracket`).
    def test_every_command_leaves_the_index_agreeing(self):
        """Before each command a record is edited by hand, so the index is behind;
        after it, the index exists and agrees with the records. `doctor`, `init`
        and the dry runs repair nothing by design — a doctor that rebuilt the index
        would hide the disagreement it is there to name — so they run on an index
        that agrees, and must leave it agreeing and unwritten."""
        project = _migrated_bracket(os.path.join(self.tmp(), "bracket"))
        index = os.path.join(project, ".atompipe", "ledger.json")
        commands = ([argv for argv, _output in _NON_SHIM] + [("check",), ("check", "--force")]
                    + [argv for argv, _named in _shims(self.tmp())])
        for n, argv in enumerate(commands):
            with self.subTest(argv=argv):
                repairs = argv[0] not in ("doctor", "init") and "--no-record" not in argv
                if repairs:
                    _hand_edit(project, n)
                    if os.path.isfile(index):
                        self.assertNotEqual(store.agree(project), [],
                                            "the hand edit left the index agreeing: this "
                                            "row would pass without a rebuild")
                else:
                    self.assertEqual(store.agree(project), [])
                    unwritten = _read_bytes(index) if os.path.isfile(index) else None
                proc = _env.atompipe(list(argv), cwd=project)
                self.assertIn(proc.returncode, _codes(argv), proc.stdout + proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                if repairs:
                    self.assertTrue(os.path.isfile(index),
                                    f"`atompipe {' '.join(argv)}` left no index")
                else:
                    self.assertEqual(_read_bytes(index) if os.path.isfile(index) else None,
                                     unwritten, f"`atompipe {' '.join(argv)}` wrote the index")
                self.assertEqual(store.agree(project), [],
                                 f"the index disagrees with the records after "
                                 f"`atompipe {' '.join(argv)}`")

    def test_a_migration_leaves_the_index_agreeing(self):
        """On a legacy project the index arrives with the one-time migration —
        `check` or a shim — and agrees from its first byte. Every other command
        leaves a legacy `ledger.json`, which IS the records, as it was
        (`NoCommandWritesARecord`)."""
        for argv in [("check",)] + [argv for argv, _named in _shims(self.tmp())]:
            with self.subTest(argv=argv[0]):
                project = _legacy_bracket(os.path.join(self.tmp(), "legacy"))
                proc = _env.atompipe(list(argv), cwd=project)
                self.assertIn(proc.returncode, _WORKED, proc.stdout + proc.stderr)
                self.assertFalse(store.is_legacy(project), "the trigger did not migrate")
                with open(os.path.join(project, ".atompipe", "ledger.json"),
                          encoding="utf-8") as fh:
                    self.assertEqual(json.load(fh)["generated"], store.INDEX_BANNER,
                                     "ledger.json is not the index after the migration")
                self.assertEqual(store.agree(project), [])


# --------------------------------------------------------------------------- #
# the migration
# --------------------------------------------------------------------------- #
class LegacyLedgerMigrates(_env.EnvCase):
    """phase-1.md's migration table, row by row, plus the params rule."""

    WHEN = "2026-09-28T00:00:00Z"

    def _migrate(self, root: str, **kw):
        kw.setdefault("apply", True)
        kw.setdefault("when", self.WHEN)
        return store.migrate_legacy(root, **kw)

    # -- field-for-field ---------------------------------------------------- #
    def test_every_field_survives(self):
        legacy = _rich_legacy()
        root = _plant_legacy(self.tmp(), legacy)
        self._migrate(root)
        ledger = store.load(root)

        c1 = ledger.claim("C1")
        self.assertEqual((c1.statement, c1.rationale, c1.source, c1.grounded_by, c1.tags),
                         ("Tip sags no more than 0.5 mm", "visible droop", "intake",
                          ["napkin-png"], ["stiffness"]))
        self.assertEqual(c1.acceptance, Acceptance(quantity="tip deflection", limit=0.5,
                                                   units="mm"))
        c2 = ledger.claim("C2")
        self.assertEqual(c2.physical_result, PhysicalResult(
            passed=True, when="2026-09-02", who="a tester", detail="dunked 10 min, dry",
            evidence=["inputs/data/dunk.jpg"]))
        self.assertEqual(c2.note, "fill, invert, wait ten minutes")
        self.assertEqual((ledger.claim("C3").critical, ledger.claim("C3").note),
                         (False, "asked two people"))

        thickness = ledger.param("thickness")
        self.assertEqual(thickness.rejected, [Rejected(value="4.0 mm",
                                                       why="3.75 mm deflection, 7.5x the limit")])
        self.assertEqual((thickness.source, thickness.grounded_by, thickness.tags,
                          thickness.units, thickness.rationale),
                         ("datasheet p3", ["napkin-png"], ["structural"], "mm",
                          "hand-written why"))

        napkin = ledger.artifact("napkin-png")
        self.assertEqual(napkin.extractions, [models.Extraction(
            what="thickness reads 7 mm", grounds=["thickness"], confidence="measured",
            note="calipers on the print")])
        self.assertEqual((napkin.sha256, napkin.bytes),
                         (hashlib.sha256(NAPKIN).hexdigest(), len(NAPKIN)),
                         "bytes is computed from the file, the pin kept")

        need = ledger.need("N-C4")
        self.assertEqual((need.claim_class, need.chosen, need.note, str(need.status)),
                         ("structural-dynamics", "modal-fea", "asked on 09-03", "proposed"))
        self.assertEqual(need.candidates[0].install, "pip install x==1.0")
        self.assertIsNone(ledger.need("N-C5"), "a need `gap` derives by itself is not a record")

        self.assertEqual([d.id for d in ledger.decisions], ["thicker-arm", "older"],
                         "decisions read newest first")
        self.assertEqual(ledger.decisions[0].rejected,
                         [Rejected(value="4.0 mm", why="sagged", evidence="runs/0002")])
        self.assertEqual(ledger.decisions[0].body, "### Why\n\nIt sagged.")
        self.assertEqual(ledger.views[0].data, {"series": [[1, 2]]})
        self.assertEqual(ledger.meta, ProjectMeta.from_dict(legacy["meta"]))
        self.assertEqual(ledger.verdicts, [])

        with open(os.path.join(root, "results", "C2.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"results": [legacy["claims"][1]["physical_result"]]})

    def test_derived_fields_reach_no_record(self):
        """`gates`, `value`, verdicts and `last_run` appear in no record: each
        kind's forbidden keys, its id, and the two things migration drops."""
        root = _migrated(self)
        top_level_never = {
            "claims": {"gates", "physical_result", "id"},
            "params": {"value", "gates", "derived_from", "changed_in", "name"},
            "inputs": {"bytes", "id"},
            "decisions": {"id"}, "needs": {"id"}, "views": {"id"}, "results": set(),
        }
        written = {rel: json.loads(data) for rel, data in _tree(root).items()
                   if rel.split("/")[0] in store.RECORD_DIRS and rel.count("/") == 1
                   and rel.endswith(".json")}
        self.assertEqual({rel.split("/")[0] for rel in written},
                         {"claims", "params", "inputs", "decisions", "needs", "views",
                          "results"})
        for rel, data in written.items():
            with self.subTest(rel=rel):
                leaked = set(data) & top_level_never[rel.split("/")[0]]
                self.assertFalse(leaked, f"{rel} carries {sorted(leaked)}")
                self.assertFalse(_keys_anywhere(data) & {"verdicts", "last_run", "independence"})

    # -- the params rule ---------------------------------------------------- #
    def test_the_model_stating_a_rationale_drops_the_copy(self):
        prose = _Prose({"width": {"rationale": "the docstring's prose", "units": ""},
                        "thickness": {"rationale": "the model's why", "units": "mm"}})
        root = _migrated(self, prose=prose)
        self.assertEqual(prose.calls, [(root, "model/m.py")])
        self.assertFalse(os.path.exists(os.path.join(root, "params", "width.json")),
                         "width's rationale is the model's; its record had nothing else")
        with open(os.path.join(root, "params", "thickness.json"), encoding="utf-8") as fh:
            thickness = json.load(fh)
        self.assertNotIn("rationale", thickness, "the model states one: the copy goes")
        self.assertNotIn("units", thickness, "the model states the units: the copy goes")
        self.assertEqual(thickness["source"], "datasheet p3", "provenance the model cannot hold stays")

    def test_the_model_silent_keeps_the_copy(self):
        prose = _Prose({"thickness": {"rationale": "", "units": ""}})
        root = _migrated(self, prose=prose)
        with open(os.path.join(root, "params", "thickness.json"), encoding="utf-8") as fh:
            thickness = json.load(fh)
        self.assertEqual((thickness["rationale"], thickness["units"]), ("hand-written why", "mm"))
        with open(os.path.join(root, "params", "width.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"rationale": "the docstring's prose"},
                             "a param the model says nothing about keeps its rationale")

    def test_no_model_prose_is_lossless(self):
        root = _migrated(self, prose=None)
        ledger = store.load(root)
        self.assertEqual(ledger.param("width").rationale, "the docstring's prose")
        self.assertEqual(ledger.param("thickness").units, "mm")
        legacy = _rich_legacy()
        legacy["meta"]["model_entry"] = ""
        prose = _Prose({"width": {"rationale": "stated", "units": ""}})
        root = _migrated(self, legacy, prose=prose)
        self.assertEqual(prose.calls, [], "no model entry: nothing to ask the model")
        self.assertTrue(os.path.exists(os.path.join(root, "params", "width.json")))

    def test_a_param_left_with_nothing_writes_no_file(self):
        root = _migrated(self)
        self.assertFalse(os.path.exists(os.path.join(root, "params", "span.json")),
                         "span held only its value and derived_from, both the model's")
        self.assertIsNone(store.load(root).param("span"))

    def test_the_bracket_writes_no_params_by_rule(self):
        project = _bracket(self)
        with open(os.path.join(project, ".atompipe", "ledger.json"), encoding="utf-8") as fh:
            legacy = json.load(fh)
        prose = _Prose({p["name"]: {"rationale": p["rationale"], "units": ""}
                        for p in legacy["params"]})
        plan = self._migrate(project, model_prose=prose)
        claims = sorted(os.listdir(os.path.join(project, "claims")))
        self.assertEqual(claims, [f"C{n}.json" for n in range(1, 8)])
        self.assertEqual(os.listdir(os.path.join(project, "params")), [])
        self.assertFalse([rel for rel in plan.files if rel.startswith("params/")])
        dot = os.path.join(project, ".atompipe")
        self.assertTrue(os.path.isfile(os.path.join(dot, "project.json")))
        self.assertTrue(os.path.isfile(os.path.join(dot, "ledger.legacy.json")))
        self.assertFalse(os.path.exists(os.path.join(dot, "ledger.json")))
        self.assertIn("git rm --cached .atompipe/ledger.json", plan.notice)

    # -- the ignore blocks -------------------------------------------------- #
    def _ignore(self, root: str) -> str:
        with open(os.path.join(root, ".atompipe", ".gitignore"), encoding="utf-8") as fh:
            return fh.read()

    def test_a_recognised_template_is_replaced_by_the_block(self):
        for label, template in zip(("1e09113", "1.2"), store.LEGACY_GITIGNORE_TEMPLATES):
            with self.subTest(template=label):
                root = self.tmp()
                _write(os.path.join(root, ".atompipe", ".gitignore"), template)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    changed = store.ensure_ignore_blocks(root)
                self.assertIn(".atompipe/.gitignore", changed)
                text = self._ignore(root)
                self.assertTrue(text.startswith("# atompipe:begin\n"), text)
                self.assertTrue(text.endswith("# atompipe:end\n"), text)
                self.assertEqual(err.getvalue(), "", "a template is not a user's line")

    def test_allow_lines_go_with_a_notice_and_user_lines_stay(self):
        root = self.tmp()
        _write(os.path.join(root, ".atompipe", ".gitignore"),
               "# mine\nscratch/\n!ledger.json\n\n!runs/\ncache/\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            store.ensure_ignore_blocks(root)
        text = self._ignore(root)
        lines = text.splitlines()
        self.assertNotIn("!ledger.json", lines)
        self.assertNotIn("!runs/", lines)
        self.assertEqual(lines.count("cache/"), 1, "a user line duplicating the block goes")
        self.assertIn("# mine", lines)
        self.assertIn("scratch/", lines)
        self.assertLess(lines.index("# atompipe:end"), lines.index("scratch/"))
        for removed in ("!ledger.json", "!runs/"):
            self.assertIn(removed, err.getvalue())

    def test_the_rewrite_is_idempotent(self):
        root = _migrated(self)
        _write(os.path.join(root, ".gitignore"), "node_modules/\n")
        _write(os.path.join(root, ".gitattributes"), "*.pdf binary\n")
        store.ensure_ignore_blocks(root)
        before = _tree(root)
        self.assertEqual(store.ensure_ignore_blocks(root), [])
        self.assertEqual(_tree(root), before)
        with open(os.path.join(root, ".gitignore"), encoding="utf-8") as fh:
            ignore = fh.read().splitlines()
        with open(os.path.join(root, ".gitattributes"), encoding="utf-8") as fh:
            attributes = fh.read().splitlines()
        for needed in ("__pycache__/", "*.py[cod]", "node_modules/"):
            self.assertIn(needed, ignore)
        for needed in ("* text=auto eol=lf", "*.stl -text", "*.pdf binary"):
            self.assertIn(needed, attributes)

    def test_the_bracket_file_becomes_the_block(self):
        """The bracket's tracked file is the 1e09113 template plus two appended
        lines and the comment that explains them (U23). The template goes, the
        two lines are the block's own, and a comment that explained only lines
        the block now owns goes with them."""
        project = _bracket(self)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            store.ensure_ignore_blocks(project)
        text = self._ignore(project)
        self.assertTrue(text.startswith("# atompipe:begin\n") and text.endswith("# atompipe:end\n"),
                        text)
        self.assertNotIn("cli:H2", text)

    # -- resume, refuse ----------------------------------------------------- #
    def test_a_migration_killed_halfway_completes_on_the_next_run(self):
        root = _plant_legacy(self.tmp(), _rich_legacy())
        plan = self._migrate(root, apply=False)
        records = [rel for rel in plan.files if not rel.startswith(".atompipe/")]
        real = store.atomic_write_text
        written: list[str] = []

        def dies_halfway(path, text, **kw):
            if len(written) >= len(records) // 2:
                raise KeyboardInterrupt("killed")
            written.append(path)
            return real(path, text, **kw)

        with mock.patch.object(store, "atomic_write_text", dies_halfway), \
                self.assertRaises(KeyboardInterrupt):
            self._migrate(root)
        self.assertTrue(written)
        self.assertTrue(store.is_legacy(root), "no project.json: the migration did not commit")
        self.assertEqual(store.load(root), plan.ledger, "a half-migrated project reads as its plan")

        self._migrate(root)
        self.assertFalse(store.is_legacy(root))
        for rel, data in plan.files.items():
            self.assertEqual(_read_bytes(os.path.join(root, *rel.split("/"))), data, rel)
        self.assertEqual(store.load(root), plan.ledger)

    def test_a_hand_edited_half_migrated_record_is_refused(self):
        root = _plant_legacy(self.tmp(), _rich_legacy())
        plan = self._migrate(root, apply=False)
        path = os.path.join(root, "claims", "C1.json")
        _write(path, plan.files["claims/C1.json"].decode().replace("0.5", "0.6"))
        before = _tree(root)
        for apply in (True, False):
            with self.subTest(apply=apply), self.assertRaises(AtompipeError) as caught:
                self._migrate(root, apply=apply)
            self.assertIn("claims/C1.json", str(caught.exception))
            self.assertIn("ledger.json", str(caught.exception))
        with self.assertRaises(AtompipeError):
            store.load(root)
        self.assertEqual(_tree(root), before, "a refused migration wrote something")

    def test_an_old_shape_ledger_holding_a_forged_pass_changes_no_status(self):
        root = _migrated(self)
        registry = _registry_covering(("g.one", "C1"), ("g.one", "C5"))
        before = claims_mod.statuses(store.load(root), registry=registry)
        forged = _rich_legacy()
        forged["verdicts"][0]["detail"] = "written by an older spine"
        _write(os.path.join(root, ".atompipe", "ledger.json"), json.dumps(forged, indent=2))
        after = store.load(root)
        self.assertEqual(after.verdicts, [])
        self.assertEqual(claims_mod.statuses(after, registry=registry), before)
        self.assertNotEqual(before["C1"], ClaimStatus.PASS)

    def test_no_migrated_legacy_verdict_can_make_pass(self):
        registry = _registry_covering(("g.one", "C1"), ("g.one", "C5"))
        for apply in (False, True):
            with self.subTest(apply=apply):
                root = _plant_legacy(self.tmp(), _rich_legacy())
                plan = self._migrate(root, apply=apply)
                for ledger in (plan.ledger, store.load(root)):
                    self.assertEqual(ledger.verdicts, [])
                    statuses = claims_mod.statuses(ledger, registry=registry)
                    self.assertEqual((statuses["C1"], statuses["C5"]),
                                     (ClaimStatus.PENDING, ClaimStatus.PENDING))

    def test_rejectd_is_refused_and_nothing_is_written(self):
        """S-40: the typo is refused, never dropped and erased."""
        legacy = _rich_legacy()
        thickness = legacy["params"][0]
        thickness["rejectd"] = thickness.pop("rejected")
        root = _plant_legacy(self.tmp(), legacy)
        before = _tree(root)
        with self.assertRaises(AtompipeError) as caught:
            store.load(root)
        message = str(caught.exception)
        for needle in (".atompipe/ledger.json", "thickness", '"rejectd"', '"rejected"'):
            self.assertIn(needle, message)
        with self.assertRaises(AtompipeError):
            self._migrate(root)
        self.assertEqual(_tree(root), before, "a refused migration wrote something")

    def test_unknown_keys_are_refused_at_every_level(self):
        for label, plant in (
                ("top level", lambda d: d.update(claimz=[])),
                ("meta", lambda d: d["meta"].update(modle_entry="x")),
                ("acceptance", lambda d: d["claims"][0]["acceptance"].update(limt=1)),
                ("a result", lambda d: d["claims"][1]["physical_result"].update(whom="x")),
                ("an extraction", lambda d: d["inputs"][0]["extractions"][0].update(grnds=[])),
                ("a candidate", lambda d: d["needs"][0]["candidates"][0].update(cst="x")),
                ("independence", lambda d: d["claims"][0].update(independence="high"))):
            with self.subTest(where=label):
                legacy = _rich_legacy()
                plant(legacy)
                root = _plant_legacy(self.tmp(), legacy)
                before = _tree(root)
                with self.assertRaises(AtompipeError):
                    self._migrate(root)
                self.assertEqual(_tree(root), before)

    def test_a_newer_schema_is_refused(self):
        root = _migrated(self)
        path = os.path.join(root, ".atompipe", "project.json")
        data = json.loads(_read_bytes(path))
        data["schema"] = 99
        _write(path, json.dumps(data, indent=2) + "\n")
        for call in (lambda: store.load(root), lambda: self._migrate(root),
                     lambda: store.build_index(root)):
            with self.assertRaises(AtompipeError) as caught:
                call()
            self.assertIn("99", str(caught.exception))

    def test_an_index_with_no_project_json_is_refused(self):
        root = _migrated(self)
        store.write_index(root)
        os.remove(os.path.join(root, ".atompipe", "project.json"))
        with self.assertRaises(AtompipeError) as caught:
            self._migrate(root)
        self.assertIn("project.json", str(caught.exception))

    # -- idempotence and order --------------------------------------------- #
    def test_a_second_migration_changes_no_byte(self):
        root = _migrated(self)
        before = {rel: (data, os.stat(os.path.join(root, *rel.split("/"))).st_mtime_ns)
                  for rel, data in _tree(root).items()}
        plan = self._migrate(root)
        self.assertEqual(plan.files, {})
        self.assertEqual(plan.notice, "")
        after = {rel: (data, os.stat(os.path.join(root, *rel.split("/"))).st_mtime_ns)
                 for rel, data in _tree(root).items()}
        self.assertEqual(after, before)

    def test_project_json_is_written_last(self):
        root = _plant_legacy(self.tmp(), _rich_legacy())
        real = store.atomic_write_text
        order: list[tuple[str, bool, bool]] = []
        dot = os.path.join(root, ".atompipe")

        def recording(path, text, **kw):
            order.append((os.path.relpath(path, root).replace(os.sep, "/"),
                          os.path.exists(os.path.join(dot, "ledger.json")),
                          os.path.exists(os.path.join(dot, "ledger.legacy.json"))))
            return real(path, text, **kw)

        with mock.patch.object(store, "atomic_write_text", recording):
            self._migrate(root)
        paths = [rel for rel, _l, _g in order]
        self.assertEqual(paths[-1], ".atompipe/project.json", paths)
        self.assertLess(paths.index(".atompipe/.gitignore"), len(paths) - 1)
        self.assertLess(max(paths.index(rel) for rel in paths if rel.startswith("claims/")),
                        len(paths) - 1)
        self.assertEqual(order[-1][1:], (True, False),
                         "the legacy ledger is renamed only after the commit marker")
        self.assertTrue(os.path.isfile(os.path.join(dot, "ledger.legacy.json")))

    def test_in_memory_migration_writes_nothing_and_matches_the_applied_one(self):
        root = _plant_legacy(self.tmp(), _rich_legacy())
        before = _tree(root)
        plan = self._migrate(root, apply=False)
        self.assertEqual(_tree(root), before)
        self.assertIn("will migrate", plan.notice)
        self.assertEqual(store.load(root), plan.ledger)
        self.assertIn("claims/C1.json", plan.files)
        self.assertIn(".atompipe/project.json", plan.files)
        self._migrate(root)
        self.assertEqual(store.load(root), plan.ledger,
                         "a command answers the same before and after the migration")

    def test_every_wrapped_pack_baseline_migrates(self):
        """R-4: the strict reader, measured on every bundled pack's wrapped
        baseline before it can refuse one — the R-8 oracle migrates these."""
        names = sorted(d for d in os.listdir(_projects.PACKS)
                       if os.path.isfile(os.path.join(_projects.PACKS, d, "pack.json")))
        self.assertGreaterEqual(len(names), 7, names)
        for name in names:
            with self.subTest(pack=name):
                root = _projects.wrap_pack_baseline(name, os.path.join(self.tmp(), name))
                with open(os.path.join(root, ".atompipe", "ledger.json"), encoding="utf-8") as fh:
                    legacy = json.load(fh)
                plan = self._migrate(root)
                ledger = store.load(root)
                self.assertEqual([c.id for c in ledger.claims],
                                 [c["id"] for c in legacy["claims"]])
                self.assertEqual(ledger, plan.ledger)
                self.assertEqual(ledger.meta.packs, [name])


# --------------------------------------------------------------------------- #
# the store's own writers on the new layout
# --------------------------------------------------------------------------- #
class InitIsTheNewLayout(_env.EnvCase):
    def test_init_writes_the_layout_and_never_a_ledger(self):
        root = self.tmp()
        real = store.atomic_write_text
        order: list[str] = []

        def recording(path, text, **kw):
            order.append(os.path.relpath(path, root).replace(os.sep, "/"))
            return real(path, text, **kw)

        meta = ProjectMeta(name="fresh", created="2026-09-28T00:00:00Z", model_entry="model/m.py")
        with mock.patch.object(store, "atomic_write_text", recording):
            ledger = store.init(root, meta)
        self.assertEqual(ledger, Ledger(meta=meta))
        self.assertEqual(order[-1], ".atompipe/project.json", order)
        dot = os.path.join(root, ".atompipe")
        self.assertFalse(os.path.exists(os.path.join(dot, "ledger.json")))
        self.assertFalse(store.is_legacy(root))
        for name in store.RECORD_DIRS:
            self.assertTrue(os.path.isdir(os.path.join(root, name)), name)
        for rel in (".atompipe/.gitignore", ".gitignore", ".gitattributes", "inputs/README.md"):
            self.assertTrue(os.path.isfile(os.path.join(root, *rel.split("/"))), rel)
        self.assertEqual(store.read_project(root), meta)
        self.assertEqual(store.load(root), Ledger(meta=meta))
        self.assertEqual(store.find_root(os.path.join(root, "claims")), root)

    def test_the_inputs_readme_says_where_bytes_and_records_live(self):
        root = self.tmp()
        store.init(root, ProjectMeta(name="fresh"))
        with open(os.path.join(root, "inputs", "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        for needle in ("inputs/<id>.json", "record", "bucket"):
            self.assertIn(needle, text)


class SaveWritesTheLayoutItFinds(_env.EnvCase):
    """§3.17: `save` stays for tests and the migration. On a migrated project it
    writes records (and keeps the index agreeing); on a legacy one, the legacy
    file without verdicts."""

    def test_on_a_migrated_project_save_writes_records(self):
        root = self.tmp()
        store.init(root, ProjectMeta(name="saved"))
        ledger = store.load(root)
        ledger.claims = [Claim(id="C1", statement="one", gates=["g.one"]),
                         Claim(id="C2", statement="two")]
        store.save(root, ledger)
        self.assertEqual(sorted(os.listdir(os.path.join(root, "claims"))), ["C1.json", "C2.json"])
        self.assertEqual([c.id for c in store.load(root).claims], ["C1", "C2"])
        self.assertEqual(store.agree(root), [])
        self.assertTrue(os.path.isfile(os.path.join(root, ".atompipe", "ledger.json")))

        ledger.claims = [Claim(id="C2", statement="two, edited")]
        store.save(root, ledger)
        self.assertEqual(os.listdir(os.path.join(root, "claims")), ["C2.json"])
        self.assertEqual(store.load(root).claim("C2").statement, "two, edited")
        self.assertEqual(store.agree(root), [])

    def test_results_are_appended_never_truncated(self):
        root = self.tmp()
        store.init(root, ProjectMeta(name="results"))
        ledger = store.load(root)
        first = PhysicalResult(passed=False, who="a", when="2026-09-01")
        second = PhysicalResult(passed=True, who="b", when="2026-09-02")
        ledger.claims = [Claim(id="C1", statement="s", kind=models.ClaimKind.PHYSICAL,
                               physical_result=first)]
        store.save(root, ledger)
        ledger.claims[0].physical_result = second
        store.save(root, ledger)
        store.save(root, ledger)
        path = os.path.join(root, "results", "C1.json")
        self.assertEqual(store.read_record(path, "results"), [first, second])
        self.assertEqual(store.load(root).claim("C1").physical_result, second)
        ledger.claims[0].physical_result = None
        store.save(root, ledger)
        self.assertEqual(store.read_record(path, "results"), [first, second],
                         "a result is never deleted by a save")

    def test_on_a_legacy_project_save_keeps_the_legacy_file_without_verdicts(self):
        root = _plant_legacy(self.tmp(), _rich_legacy())
        ledger = store.load(root)
        ledger.verdicts = [Verdict(gate="g.one", passed=True)]
        store.save(root, ledger)
        with open(os.path.join(root, ".atompipe", "ledger.json"), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["verdicts"], [])
        self.assertNotIn("last_run", saved)
        self.assertTrue(store.is_legacy(root), "save does not migrate")
        self.assertFalse(os.path.exists(os.path.join(root, "claims")))


# --------------------------------------------------------------------------- #
# invariant 8, CLI half: the projects every command runs on
# --------------------------------------------------------------------------- #
#: The stamp of the in-process migration that builds the migrated fixture. It
#: appears only in the migration's notice, never in a record.
_WHEN = "2026-09-28T00:00:00Z"

#: The evidence bytes the enriched bracket ingested before checkpoint 1.3.
_CALIPER = b"arm measured at 60.2 mm with calipers\n"

#: The one ingested artifact of the enriched bracket, as its legacy ledger names it.
_CALIPER_ID = "caliper-txt"

#: The migration's rename: the legacy ledger goes, kept under its new name.
_LEGACY_REL = f"{store.ATOMPIPE_DIR}/{store.LEDGER_NAME}"
_KEPT_REL = f"{store.ATOMPIPE_DIR}/{store.LEGACY_LEDGER_NAME}"
#: The migration's commit marker, written last.
_PROJECT_REL = f"{store.ATOMPIPE_DIR}/{store.PROJECT_NAME}"

#: A claim the migrated fixture carries formatted as a person formats it (indent
#: 4, keys in their own order, no trailing newline) rather than as `write_record`
#: would: a command that loads a record it was only reading and saves it back
#: "normalised" changes its bytes, and that is a write nobody asked for.
_HAND_FORMATTED = "claims/C6.json"


def _enrich(project: str) -> str:
    """The bracket's legacy ledger with one record of every kind the bracket
    lacks: an ingested measurement with an extraction, a decision that names what
    lost, an enriched need, a physical result, a parameter's provenance and a view.

    Written in the legacy file's own shape (keys sorted, indent 2), by hand —
    never through the spine's writers, for `_projects`' reason: a fixture written
    by the code under test changes shape with it. What it buys: a command that
    rewrites a decision, a result or an input record can only be caught on a
    project that HAS one, and the bracket has none."""
    path = os.path.join(project, ".atompipe", "ledger.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    _write(os.path.join(project, "inputs", "data", "caliper.txt"), _CALIPER)
    data["inputs"].append({
        "id": _CALIPER_ID, "path": "inputs/data/caliper.txt", "url": "",
        "kind": "measurement", "description": "caliper reading of the printed arm",
        "sha256": hashlib.sha256(_CALIPER).hexdigest(), "bytes": len(_CALIPER),
        "added": "2026-09-20", "licence": "", "note": "",
        "extractions": [{"what": "arm is 60.2 mm", "grounds": ["arm_length"],
                         "confidence": "measured", "note": "calipers on the print"}]})
    data["decisions"].append({
        "id": "thickness-stays-7-mm", "title": "Thickness stays 7 mm",
        "when": "2026-09-20T10:00:00Z", "summary": "4 mm sagged",
        "rejected": [{"value": "4.0 mm", "why": "3.75 mm deflection, 7.5x the limit",
                      "evidence": ""}],
        "params_changed": ["thickness"], "claims_changed": ["C1"], "evidence": [],
        "body": "### Why\n\nIt sagged."})
    data["needs"].append({
        "id": "N-C7", "claim_ids": ["C7"], "quantity": "first mode",
        "claim_class": "structural-dynamics", "status": "proposed",
        "candidates": [{"name": "modal-fea", "kind": "solver", "why": "beam theory is off",
                        "cost": "~20 min", "install": "pip install x==1.0",
                        "licence": "MIT", "pack_would_be": "modal"}],
        "chosen": "modal-fea", "note": "asked on 09-20"})
    data["views"].append({
        "id": "deflection-curve", "kind": "chart", "title": "Tip deflection",
        "src": "", "description": "", "gates": ["bracket.deflection"],
        "data": {"series": [[4.0, 3.75], [7.0, 0.73]]}, "meta": {}, "pack": "",
        "order": 10})
    c5 = next(c for c in data["claims"] if c["id"] == "C5")
    c5["physical_result"] = {"passed": True, "when": "2026-09-21", "who": "a tester",
                             "detail": "one winter on the north wall, no chalking",
                             "evidence": []}
    thickness = next(p for p in data["params"] if p["name"] == "thickness")
    thickness["source"] = "the 4 mm print, loaded on the bench"
    _write(path, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return project


def _legacy_bracket(dest: str, *, template: bool = False) -> str:
    """The enriched bracket, legacy: its ledger, and — with ``template`` — the
    ignore file every project `init` made before checkpoint 1.2 carries, byte for
    byte (`_projects.LEGACY_GITIGNORE`), instead of the bracket's own, to which
    U23 appended `cache/` and `obs/`. Under the bare template nothing ignores
    `cache/` or `obs/` until the migration's block lands, which is what makes
    "ignored at the time of writing" bite."""
    project = _enrich(_projects.bracket_copy(dest))
    if template:
        _write(os.path.join(project, ".atompipe", ".gitignore"), _projects.LEGACY_GITIGNORE)
    return project


def _migrated_bracket(dest: str) -> str:
    """The enriched bracket, migrated in-process the way its first `check` would
    (the real `static_param_prose`), with `_HAND_FORMATTED` rewritten by hand."""
    project = _legacy_bracket(dest)
    store.migrate_legacy(project, apply=True, when=_WHEN,
                         model_prose=modelio.static_param_prose)
    path = os.path.join(project, *_HAND_FORMATTED.split("/"))
    data = json.loads(_read_bytes(path))
    _write(path, json.dumps(dict(reversed(list(data.items()))), indent=4, ensure_ascii=False))
    return project


def _hand_edit(project: str, n: int) -> None:
    """A record edited by hand between two commands, as a person (or an agent's
    Edit) makes it — each ``n`` one of three kinds in turn: a claim changed in
    place, a decision added, a parameter's provenance changed."""
    if n % 3 == 0:
        path = os.path.join(project, *_HAND_FORMATTED.split("/"))
        data = json.loads(_read_bytes(path))
        data["note"] = f"edited by hand before command {n}"
        _write(path, json.dumps(data, indent=4, ensure_ascii=False))
    elif n % 3 == 1:
        _write(os.path.join(project, "decisions", f"hand-{n}.json"),
               json.dumps({"title": f"By hand {n}", "summary": "written by a person"}) + "\n")
    else:
        path = os.path.join(project, "params", "thickness.json")
        data = json.loads(_read_bytes(path))
        data["source"] = f"the bench test, revisited before command {n}"
        _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _records(root: str) -> dict[str, tuple[bytes, int, int]]:
    """``{path: (bytes, mtime_ns, inode)}`` of every record: each file under the
    record directories (evidence bytes and viewgens included; bytecode not —
    importing a viewgen writes `__pycache__` beside it, which is Python's, cli:H15),
    the project marker, the kept legacy ledger, and, while the project is legacy,
    its `ledger.json`, which IS its records.

    mtime and inode as well as bytes: a command that loads a record and saves it
    back unchanged has still written it, and the day a person edits that file
    between the command's load and its save, the edit is gone — the whole-ledger
    writer's shape (S-37). Equal bytes cannot see that; a new mtime or inode can."""
    found: list[str] = []
    for kind in store.RECORD_DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, kind)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            found += [os.path.join(dirpath, name) for name in filenames]
    dot = os.path.join(root, store.ATOMPIPE_DIR)
    found += [os.path.join(dot, name) for name in (store.PROJECT_NAME, store.LEGACY_LEDGER_NAME)]
    if store.is_legacy(root):
        found.append(os.path.join(dot, store.LEDGER_NAME))
    out: dict[str, tuple[bytes, int, int]] = {}
    for path in found:
        if os.path.isfile(path):
            st = os.stat(path)
            out[os.path.relpath(path, root).replace(os.sep, "/")] = (
                _read_bytes(path), st.st_mtime_ns, st.st_ino)
    return out


def _touched_records(before: dict, after: dict) -> list[str]:
    """Every record added, removed or written between two `_records` snapshots."""
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


#: Every command that is neither `check` nor a shim, as a person types it, in
#: the order the tests run them (`site init` before the site commands that need
#: a site), with ``True`` where the command was ASKED to write an output that is
#: not a record — `report --write`'s `docs/readiness.md`, `model --write`'s
#: `.atompipe/model.json`, the site's scaffold and `data/`. `init` refuses on a
#: project before it writes anything; it is here so "every command" means every
#: command. `check --no-record` and `gate selftest --no-record` are the dry runs.
#: Not here: `site serve` (a server that does not return) and `site vendor` (the
#: network); P3.1's `ask --next` joins when it exists — a strengthening, never an
#: edit of what is here (PLAN §4.6).
_NON_SHIM: tuple[tuple[tuple[str, ...], bool], ...] = (
    (("status",), False), (("status", "--json"), False),
    (("why", "thickness"), False), (("why", "C1"), False),
    (("why", "arm_length", "--json"), False),
    (("gap",), False), (("gap", "--json"), False), (("gap", "--propose"), False),
    (("report",), False), (("report", "--json"), False),
    (("gate", "list"), False), (("gate", "list", "--json"), False),
    (("gate", "show", "bracket.deflection"), False),
    (("gate", "selftest"), False), (("gate", "selftest", "--json"), False),
    (("gate", "selftest", "--no-record"), False),
    (("claim", "list"), False), (("claim", "list", "--json"), False),
    (("claim", "show", "C1"), False), (("claim", "show", "C5", "--json"), False),
    (("inputs",), False), (("inputs", "--json"), False),
    (("ask",), False), (("ask", "--json"), False),
    (("packs", "list"), False), (("packs", "list", "--json"), False),
    (("packs", "show", "beam-analytic"), False),
    (("packs", "validate", "beam-analytic"), False),
    (("model",), False), (("model", "--json"), False),
    (("doctor",), False), (("doctor", "--json"), False),
    (("check", "--no-record"), False),
    (("init",), False),
    (("report", "--write"), True), (("model", "--write"), True),
    (("site", "init"), True), (("site", "build"), True), (("site", "status"), False),
)

#: The exit codes each command may end with while doing its work: 1 is a
#: verdict (`check`, `doctor`, `packs validate` reporting a problem), 2 is a
#: refusal — the right answer only for `init` on a project. A command that
#: crashed or was refused writes nothing and would pass vacuously.
_WORKED = (0, 1)
_REFUSED = (2,)


def _codes(argv: tuple[str, ...]) -> tuple[int, ...]:
    return _REFUSED if argv[0] == "init" else _WORKED


def _shims(outside: str) -> list[tuple[tuple[str, ...], set[str] | None]]:
    """Each shim with the record(s) it is asked to write; ``None`` for `ingest`,
    whose record and bytes are named by its own `--json` answer."""
    evidence = _write(os.path.join(outside, "notes.txt"), "the wall anchor is M5\n")
    return [
        (("decide", "--title", "Keep PETG", "--summary", "heat is fine"),
         {"decisions/keep-petg.json"}),
        (("extract", _CALIPER_ID, "--what", "arm is 60.2 mm", "--grounds", "arm_length",
          "--confidence", "measured"), {f"inputs/{_CALIPER_ID}.json"}),
        (("packs", "add", "beam-analytic"), {".atompipe/project.json"}),
        (("claim", "physical", "C5", "--fail", "--who", "a tester",
          "--detail", "chalked after the second winter"), {"results/C5.json"}),
        (("ingest", evidence, "--desc", "the anchor's datasheet line", "--json"), None),
    ]


def _asked(named: set[str] | None, proc) -> set[str]:
    """What a shim was asked to write: ``named``, or what `ingest --json` says it
    landed — its record and its bytes."""
    if named is not None:
        return named
    (artifact,) = json.loads(proc.stdout)["ingested"]
    return {f"inputs/{artifact['id']}.json", artifact["path"]}


# --------------------------------------------------------------------------- #
# invariant 8, CLI half: what a command writes, and when
# --------------------------------------------------------------------------- #
#: The child every watched command runs in: ONE command, under an audit hook
#: that records, in order, every path the process writes, renames or removes
#: inside the project — opens for writing (`open()` and `os.open`, so the temp of
#: every atomic write), renames and replaces, removals (a whole `rmtree` as its
#: top directory: its fd-relative unlinks name no path), links and truncations.
#: A snapshot diff alone cannot say WHEN a path was written, and "ignored at the
#: time of writing" is a statement about when; it also cannot see a temp file
#: that came and went.
#:
#: The hook opens no file and lists no directory. What slipped through while
#: designing it: a first version read every `.gitignore` at each write to learn
#: the rules in force — but the spine's own hook records what a gate reads while
#: the gate runs, so a gate that wrote a file would have had the `.gitignore`
#: files land in its rho, and the test would have changed the verdicts it
#: watched. The rules at each write are reconstructed afterwards instead, from
#: the rules before the run, after it, and the order of the writes (`_rules_at`).
#: An exception inside an audit hook aborts the audited operation, so the hook
#: never raises: it records what went wrong, and the test fails on that record.
_WRITES_DRIVER = r'''
import json, os, sys

out, root, argv = sys.argv[1], os.path.realpath(sys.argv[2]), sys.argv[3:]
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
events = []


def inside(path):
    try:
        full = os.path.realpath(os.path.abspath(os.fsdecode(path)))
    except (TypeError, ValueError):
        return None
    if not full.startswith(root + os.sep):
        return None
    return full[len(root) + 1:].replace(os.sep, "/")


def note(op, *paths):
    shown = [inside(path) for path in paths]
    if any(path is not None for path in shown):
        events.append([op, *shown])


def hook(event, args):
    try:
        if event == "open":
            path, mode, flags = args
            if isinstance(path, int):
                return
            if mode is None and flags & WRITE_FLAGS:
                note("write", path)
            elif isinstance(mode, str) and set(mode) & set("wax+"):
                note("write", path)
        elif event == "os.rename":
            note("rename", args[0], args[1])
        elif event == "os.remove" and args[1] in (None, -1):
            note("remove", args[0])
        elif event == "shutil.rmtree":
            note("remove-tree", args[0])
        elif event in ("os.link", "os.symlink"):
            note("write", args[1])
        elif event == "os.truncate" and not isinstance(args[0], int):
            note("write", args[0])
    except Exception as exc:
        events.append(["hook-error", repr(exc)])


sys.addaudithook(hook)
code = None
try:
    from atompipe import cli
    code = cli.main(argv)
finally:
    done = list(events)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"code": code, "events": done}, fh)
sys.exit(code)
'''

#: The two files git reads rules from that the ensure step writes a marked block
#: into (`.gitignore` for what is ignored, `.gitattributes` for line endings), by
#: git's own names — the property names what the files ARE, never which ones.
_GIT_RULE_FILES = (".gitignore", ".gitattributes")
_BLOCK_BEGIN, _BLOCK_END = b"# atompipe:begin", b"# atompipe:end"


class _Run(NamedTuple):
    """One watched command: the process, the tree before and after it, and what
    it wrote, in order."""

    proc: Any
    before: dict[str, bytes]
    after: dict[str, bytes]
    events: list[list]


def _watched(test: _env.EnvCase, project: str, *argv: str) -> _Run:
    """``atompipe <argv>`` in ``project``, in a fresh process under `_WRITES_DRIVER`."""
    out = os.path.join(test.tmp(), "writes.json")
    before = _tree(project)
    proc = _env.run([sys.executable, "-c", _WRITES_DRIVER, out, project, *argv], cwd=project)
    try:
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise AssertionError(f"the watched `atompipe {' '.join(argv)}` left no record of "
                             f"its writes ({exc}):\n{proc.stdout}\n{proc.stderr}") from None
    errors = [e for e in data["events"] if e[0] == "hook-error"]
    test.assertEqual(errors, [], "the audit hook failed: a write may have gone unseen")
    return _Run(proc, before, _tree(project), data["events"])


def _touches(run: _Run) -> list[tuple[int, str, str]]:
    """``(instant, op, path)`` for every path ``run`` wrote (``write``), removed
    (``remove``, ``remove-tree``) or renamed away (``remove``) inside the project.

    The temp of an atomic write is folded into its destination: a rename's source
    that did not exist before the run is the destination's bytes in transit
    (`atomic_write_text` and Python's own bytecode writer both write a temp beside
    the target, then replace), so its writes are not touches of their own, and the
    rename is the write of the destination, at the instant of the rename."""
    in_flight = {e[1] for e in run.events
                 if e[0] == "rename" and e[1] is not None and e[1] not in run.before}
    out: list[tuple[int, str, str]] = []
    for i, event in enumerate(run.events):
        op, paths = event[0], event[1:]
        if op == "rename":
            src, dst = paths
            if src is not None and src not in in_flight:
                out.append((i, "remove", src))
            if dst is not None:
                out.append((i, "write", dst))
        elif paths[0] is not None and paths[0] not in in_flight:
            out.append((i, op, paths[0]))
    return out


def _rule_files(tree: dict[str, bytes]) -> dict[str, bytes]:
    return {p: b for p, b in tree.items() if p.rsplit("/", 1)[-1] == ".gitignore"}


def _rules_at(instant: int, run: _Run, first: dict[str, int]) -> dict[str, bytes]:
    """The `.gitignore` files git would read at ``instant``: each as it was before
    the run until the run's one write of it (``first``: path -> the instant of its
    first touch), as it is after the run from then on. A file written twice in one
    run is refused by `_unexplained`: its rules between the two writes cannot be
    known."""
    rules: dict[str, bytes] = {}
    for path in set(_rule_files(run.before)) | set(_rule_files(run.after)):
        text = (run.after if first.get(path, len(run.events)) < instant else run.before).get(path)
        if text is not None:
            rules[path] = text
    return rules


def _ignored(test: _env.EnvCase, rules: dict[str, bytes],
             queries: dict[str, bool]) -> set[str]:
    """Which of ``queries`` (``{path: is a directory}``) git ignores under
    ``rules``: asked of `git check-ignore` in a scratch repository holding exactly
    those rule files and the queried paths — never the project, whose rules at
    the instant of a write may be gone by the time the test asks."""
    repo = test.tmp()
    proc = _env.git(["init", "-q"], cwd=repo)
    test.assertEqual(proc.returncode, 0, proc.stderr)
    for rel, text in rules.items():
        _write(os.path.join(repo, *rel.split("/")), text)
    for rel, is_dir in queries.items():
        full = os.path.join(repo, *rel.split("/"))
        if is_dir:
            os.makedirs(full, exist_ok=True)
        elif not os.path.exists(full):
            _write(full, b"")
    # One path per line, unquoted: `-z` is refused without `--stdin`, and `_env.run`
    # closes stdin so a child that asks a question fails instead of hanging.
    proc = _env.git(["-c", "core.quotePath=false", "check-ignore", "--no-index", "--",
                     *sorted(queries)], cwd=repo)
    test.assertIn(proc.returncode, (0, 1), f"git check-ignore failed: {proc.stderr}")
    return {p for p in proc.stdout.splitlines() if p}


def _unexplained(test: _env.EnvCase, run: _Run, *, blocks: bool = False,
                 carve_out: frozenset = frozenset()) -> list[str]:
    """Every touch in ``run`` the property does not explain. A path a run writes
    must be

    * ignored by git at the instant it is written (or removed); or
    * a NEW verdict or control entry: the entry shape, absent before the run and
      present after it — an existing entry rewritten is not new; or
    * with ``blocks``, a git rule file that now carries the marked block — the
      ensure step, which writes each such file once; or
    * one of ``carve_out``'s ``(op, path)`` touches: the one-time legacy
      migration's own files, and a shim's own record.

    A rule file written twice in one run is unexplained whatever it holds."""
    touches = _touches(run)
    first: dict[str, int] = {}
    for i, _op, path in touches:
        first.setdefault(path, i)
    problems: list[str] = []
    pending: dict[str, tuple[dict[str, bytes], dict[str, bool], list[tuple[int, str, str]]]] = {}
    written: dict[str, int] = {}
    for i, op, path in touches:
        name = path.rsplit("/", 1)[-1]
        if name in _GIT_RULE_FILES:
            written[path] = written.get(path, 0) + 1
        if (op, path) in carve_out:
            continue
        if (blocks and op == "write" and name in _GIT_RULE_FILES
                and _BLOCK_BEGIN in run.after.get(path, b"")
                and _BLOCK_END in run.after.get(path, b"")):
            continue
        if (op == "write" and path not in run.before and path in run.after
                and (_transcript.ENTRY_PATH.fullmatch(path)
                     or _transcript.CONTROL_ENTRY_PATH.fullmatch(path))):
            continue
        rules = _rules_at(i, run, first)
        key = json.dumps({k: v.decode("utf-8", "replace") for k, v in rules.items()},
                         sort_keys=True)
        entry = pending.setdefault(key, (rules, {}, []))
        entry[1][path] = entry[1].get(path, False) or op == "remove-tree"
        entry[2].append((i, op, path))
    for rules, queries, waiting in pending.values():
        ignored = _ignored(test, rules, queries)
        for i, op, path in waiting:
            if path not in ignored:
                problems.append(f"{op} {path} (event {i}): not ignored when it was written, "
                                f"and not a new verdict entry")
    for path, count in sorted(written.items()):
        if count > 1:
            problems.append(f"{path}: written {count} times in one run — the ensure step "
                            f"writes a marked block once")
    return sorted(set(problems))


def _migration(project: str) -> tuple[store.MigrationPlan, frozenset]:
    """The legacy ``project``'s migration plan (computed in memory, nothing
    written) and the touches it licenses: each of its files written, the legacy
    ledger renamed away, and its kept copy written."""
    plan = store.migrate_legacy(project, apply=False, when="",
                                model_prose=modelio.static_param_prose)
    return plan, frozenset({("write", rel) for rel in plan.files}
                           | {("remove", _LEGACY_REL), ("write", _KEPT_REL)})


class NoCommandWritesARecord(_env.EnvCase):
    """Invariant 8's second half: no command writes a record it was not asked to
    write. The one carve-out is the one-time migration of a legacy ledger, which
    only `check` and the shims perform.

    What slipped through before checkpoint 1.3: `check`, a sweep, re-synced the
    parameters from the model, copied grounding and coverage into records, and
    saved the whole ledger on every run, so a claim a human edited between two
    commands was put back from the sweep's memory (S-36, S-37); `gap`, which reads
    like a query, filed every gap it found as a record nobody wrote (S-43).

    Two statements, each on a migrated project and on a legacy one:

    * every record's bytes, mtime and inode survive every command that is not a
      shim — including the ones asked for an output (`report --write`, `model
      --write`, the site) — and on a legacy project nothing migrates;
    * **the property** — every path `check` writes is ignored by git at the
      instant it is written, or is a new verdict entry, except the marked blocks
      the ensure step writes once (and, on a legacy project, the migration's own
      files). Stated as that property, never as a list of paths, so P2.5's
      `REPORT.md` and P4.3's site rebuild meet it by adding an ignore rule first,
      and never by widening this test (PLAN §4.6). It holds for every read
      command on a migrated project too.

    Every command runs in a fresh process (`_env`); the property's is watched by
    an audit hook (`_WRITES_DRIVER`) and its ignore rules are asked of `git
    check-ignore` in a scratch repository.
    """

    def _assert_worked(self, argv, proc, codes=_WORKED) -> None:
        self.assertIn(proc.returncode, codes,
                      f"`atompipe {' '.join(argv)}` did not do its work, so it proves "
                      f"nothing about what it writes:\n{proc.stdout}\n{proc.stderr}")
        self.assertNotIn("Traceback", proc.stderr)

    def _no_record_written(self, project: str, *, legacy: bool, watched: bool) -> None:
        for argv, output in _NON_SHIM:
            with self.subTest(argv=argv):
                before = _records(project)
                if watched:
                    run = _watched(self, project, *argv)
                    proc = run.proc
                else:
                    proc = _env.atompipe(list(argv), cwd=project)
                self._assert_worked(argv, proc, _codes(argv))
                self.assertEqual(_touched_records(before, _records(project)), [],
                                 f"`atompipe {' '.join(argv)}` wrote a record")
                self.assertEqual(store.is_legacy(project), legacy,
                                 f"`atompipe {' '.join(argv)}` migrated a project it was "
                                 f"only reading" if legacy else "the project went legacy")
                if watched and not output:
                    self.assertEqual(_unexplained(self, run), [],
                                     f"`atompipe {' '.join(argv)}` wrote what git would track")

    # -- the non-shim commands ---------------------------------------------- #
    def test_no_command_writes_a_record_on_a_migrated_project(self):
        project = _migrated_bracket(os.path.join(self.tmp(), "migrated"))
        kinds = {rel.split("/", 1)[0] for rel in _records(project)}
        self.assertTrue(set(store.RECORD_DIRS) <= kinds,
                        f"the fixture lacks a record kind, so a command that rewrites one "
                        f"could not be seen: {sorted(set(store.RECORD_DIRS) - kinds)}")
        self._no_record_written(project, legacy=False, watched=True)

    def test_no_command_writes_a_record_on_a_legacy_project(self):
        """A legacy `ledger.json` IS the records: every command but `check` and the
        shims reads it — migrated in memory — and leaves it, and everything around
        it, as it was."""
        project = _legacy_bracket(os.path.join(self.tmp(), "legacy"))
        self._no_record_written(project, legacy=True, watched=False)

    # -- check -------------------------------------------------------------- #
    def test_check_writes_only_ignored_paths_and_new_entries(self):
        """On a migrated project `check` writes no record and meets the property
        with no exception: the blocks are already there, so it writes none."""
        project = _migrated_bracket(os.path.join(self.tmp(), "migrated"))
        for argv in (("check",), ("check",), ("check", "--force", "--junit"),
                     ("check", "--only", "bracket.deflection"), ("check", "--json")):
            with self.subTest(argv=argv):
                before = _records(project)
                run = _watched(self, project, *argv)
                self._assert_worked(argv, run.proc)
                self.assertEqual(_touched_records(before, _records(project)), [],
                                 f"`atompipe {' '.join(argv)}` wrote a record")
                self.assertEqual(_unexplained(self, run), [])
        self.assertTrue(any(_transcript.ENTRY_PATH.fullmatch(p) for p in _tree(project)),
                        "no check wrote a verdict entry: the property held vacuously")

    def test_check_on_a_legacy_project_writes_only_the_migration(self):
        """The first `check` migrates: the records it writes are exactly the plan's,
        byte for byte, the legacy ledger is kept under its new name, and every other
        path is ignored when written, a new entry, or a block written once. The
        second writes no record and no block. Under the bare legacy template too,
        where `cache/` and `obs/` are ignored only once the block lands — so a
        sweep that wrote there before the migration would be caught."""
        for label, template in (("the bracket's ignore file", False),
                                ("the legacy init template", True)):
            with self.subTest(fixture=label):
                project = _legacy_bracket(os.path.join(self.tmp(), "legacy"), template=template)
                plan, carve_out = _migration(project)
                self.assertTrue(plan.files)
                legacy = _read_bytes(os.path.join(project, *_LEGACY_REL.split("/")))
                before = _records(project)

                first = _watched(self, project, "check")
                self._assert_worked(("check",), first.proc)
                self.assertFalse(store.is_legacy(project), "the first check did not migrate")
                self.assertEqual(_unexplained(self, first, blocks=True, carve_out=carve_out), [])
                after = _records(project)
                self.assertEqual(
                    set(_touched_records(before, after)),
                    set(plan.files) | {_LEGACY_REL, _KEPT_REL},
                    "the first check wrote a record beyond the migration's own")
                for rel, data in plan.files.items():
                    self.assertEqual(after[rel][0], data, f"{rel} is not the plan's bytes")
                self.assertEqual(after[_KEPT_REL][0], legacy, "the legacy ledger was not kept")
                blocks = [p for i, op, p in _touches(first)
                          if p.rsplit("/", 1)[-1] in _GIT_RULE_FILES]
                self.assertTrue(blocks, "the migration wrote no ignore block")

                second = _watched(self, project, "check")
                self._assert_worked(("check",), second.proc)
                self.assertEqual(_touched_records(after, _records(project)), [])
                self.assertEqual(_unexplained(self, second), [],
                                 "the second check wrote what git would track — or a "
                                 "block the first one already wrote")

    # -- the shims on a legacy project --------------------------------------- #
    def test_a_shim_on_a_legacy_project_writes_the_migration_and_its_record(self):
        """Each shim migrates first, then writes the one record it was asked for:
        nothing else it writes is a record, and nothing else would be tracked."""
        outside = self.tmp()
        for argv, named in _shims(outside):
            with self.subTest(shim=argv[0]):
                project = _legacy_bracket(os.path.join(self.tmp(), "legacy"))
                plan, carve_out = _migration(project)
                before = _records(project)
                run = _watched(self, project, *argv)
                self._assert_worked(argv, run.proc, (0,))
                asked = _asked(named, run.proc)
                touched = set(_touched_records(before, _records(project)))
                self.assertTrue(asked <= touched, f"the shim did not write {asked}")
                self.assertEqual(
                    touched - asked - set(plan.files), {_LEGACY_REL, _KEPT_REL},
                    f"`atompipe {argv[0]}` wrote a record it was not asked to")
                licensed = carve_out | {("write", rel) for rel in asked}
                self.assertEqual(_unexplained(self, run, blocks=True, carve_out=licensed), [])

    # -- the checker refuses ------------------------------------------------- #
    def test_the_property_refuses_what_it_forbids(self):
        """V: each rule of `_unexplained` and `_records`, broken on a real run.

        A shim's record, written with no licence, is the one thing named. A cache
        file moved to before the migration's block is caught; the same file after
        the block is not. An existing verdict entry rewritten is not a new one. A
        block written twice is refused. A record saved back with equal bytes is a
        write."""
        project = _migrated_bracket(os.path.join(self.tmp(), "migrated"))
        run = _watched(self, project, "decide", "--title", "Keep PETG", "--summary", "s")
        self._assert_worked(("decide",), run.proc, (0,))
        found = _unexplained(self, run)
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("write decisions/keep-petg.json "), found)

        legacy = _legacy_bracket(os.path.join(self.tmp(), "legacy"), template=True)
        _plan, carve_out = _migration(legacy)
        first = _watched(self, legacy, "check")
        self.assertEqual(_unexplained(self, first, blocks=True, carve_out=carve_out), [])
        last = ".atompipe/cache/last_check.json"
        self.assertIn(last, first.after, "the legacy check wrote no last_check.json")
        moved = first._replace(events=[["write", last]] + first.events)
        self.assertEqual([p for p in _unexplained(self, moved, blocks=True, carve_out=carve_out)
                          if last in p],
                         [f"write {last} (event 0): not ignored when it was written, and not "
                          f"a new verdict entry"],
                         "a write before the block landed was read as ignored — or the same "
                         "file written after it was not")

        second = _watched(self, legacy, "check")
        entry = next(p for p in second.after if _transcript.ENTRY_PATH.fullmatch(p))
        rewritten = second._replace(events=second.events + [["write", entry]])
        self.assertTrue(any(p.startswith(f"write {entry} ") for p in _unexplained(self, rewritten)),
                        "an existing verdict entry rewritten passed as a new one")
        twice = first._replace(events=first.events + [["write", ".gitignore"]])
        self.assertTrue(any(p.startswith(".gitignore: written 2 times")
                            for p in _unexplained(self, twice, blocks=True, carve_out=carve_out)))

        before = _records(project)
        path = os.path.join(project, *_HAND_FORMATTED.split("/"))
        _write(path + ".saved", before[_HAND_FORMATTED][0])
        os.replace(path + ".saved", path)                  # an atomic save of the same bytes
        self.assertEqual(_read_bytes(path), before[_HAND_FORMATTED][0])
        self.assertEqual(_touched_records(before, _records(project)), [_HAND_FORMATTED],
                         "a record saved back with equal bytes was not seen")


# --------------------------------------------------------------------------- #
# every reader of a legacy project plans the migration `check` will carry out
# --------------------------------------------------------------------------- #
#: The commands a person runs on a legacy project before its first `check`. Each
#: reads the legacy ledger migrated in memory and writes nothing; `check
#: --no-record` is the same plan through `check`'s own path, `doctor` through its
#: own row. `why` is added per test with the parameter the test is about.
_LEGACY_READERS: tuple[tuple[str, ...], ...] = (
    ("status",), ("report",), ("doctor",), ("model",), ("claim", "list"),
    ("inputs",), ("ask",), ("gate", "list"), ("packs", "list"), ("site", "status"),
    ("check", "--no-record"),
)

#: The two model fields a case-only pair is made of, each with the docstring the
#: model states it with — the words the legacy parameter sync copied into the
#: legacy record's `rationale` — and the value.
_CASE_PAIR = (("D", 12.0, "mm, boss outer diameter: a washer seats on it."),
              ("d", 5.0, "mm, boss bore: an M4 clearance hole."))

#: A child that runs `atompipe <argv>` and dies the instant the command renames
#: its temp file onto `sys.argv[2]` (a root-relative path) — `os._exit`: no
#: `finally`, no lock release, no cleanup, as a SIGKILL or a power cut leaves it.
#: That file is never written; every write before it is.
_KILL_DRIVER = r"""
import os, sys

root, target, argv = os.path.realpath(sys.argv[1]), sys.argv[2], sys.argv[3:]
want = os.path.join(root, *target.split("/"))


def hook(event, args):
    if event == "os.rename":
        try:
            dst = os.path.realpath(os.path.abspath(os.fsdecode(args[1])))
        except (TypeError, ValueError):
            return
        if dst == want:
            os._exit(137)


sys.addaudithook(hook)
from atompipe import cli
sys.exit(cli.main(argv))
"""


def _case_pair_bracket(dest: str, *, stated: bool) -> str:
    """The legacy bracket with two model fields whose names differ only in case,
    `D` and `d`, and the legacy records the old parameter sync wrote for them.

    ``stated``: the model states each one's rationale (a docstring), and the
    legacy record holds that same text — what the sync copied — so the params
    rule leaves neither with anything to hold: no file, nothing to collide.
    Without it the model is silent and the legacy record's rationale is a
    person's, which the rule keeps: `params/D.json` beside `params/d.json`, one
    file on a case-insensitive filesystem, and every command must refuse it."""
    project = _projects.bracket_copy(dest)
    model = os.path.join(project, "model", "bracket.py")
    with open(model, encoding="utf-8") as fh:
        text = fh.read()
    anchor = "    thickness: float = 7.0\n"
    if anchor not in text:
        raise AssertionError(f"model/bracket.py no longer holds {anchor!r}: move the anchor")
    fields = "".join(f"    {name}: float = {value}\n"
                     + (f'    """{why}"""\n' if stated else "") + "\n"
                     for name, value, why in _CASE_PAIR)
    _write(model, text.replace(anchor, fields + anchor, 1))
    ledger = os.path.join(project, ".atompipe", "ledger.json")
    with open(ledger, encoding="utf-8") as fh:
        data = json.load(fh)
    data["params"] += [{"changed_in": "", "derived_from": [], "gates": [], "grounded_by": [],
                        "name": name, "rationale": why, "rejected": [], "source": "",
                        "tags": [], "units": "", "value": value}
                       for name, value, why in _CASE_PAIR]
    _write(ledger, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return project


def unmatched_migration_readers(source: str) -> list[str]:
    """``<function>:<line>`` for every way ``source`` reaches `store.load` or
    `store.migrate_legacy` other than a call passing `model_prose=` the reader
    `check` migrates with (`static_param_prose`, by any module path): a call
    without it, a bare reference (`f = store.load`), `getattr(store, "load")`,
    and either name imported from the store module under any name."""
    tree = ast.parse(source)
    owner: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                owner.setdefault(id(node), fn.name)
    store_names = {"store"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in (None, "atompipe"):
            store_names |= {a.asname or a.name for a in node.names if a.name == "store"}
        elif isinstance(node, ast.Import):
            store_names |= {a.asname for a in node.names
                            if a.name == "atompipe.store" and a.asname}
    readers = ("load", "migrate_legacy")

    def the_reader(value: ast.AST) -> bool:
        return ((isinstance(value, ast.Attribute) and value.attr == "static_param_prose")
                or (isinstance(value, ast.Name) and value.id == "static_param_prose"))

    judged: set[int] = set()
    found: list[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in readers and isinstance(node.func.value, ast.Name)
                and node.func.value.id in store_names):
            judged.add(id(node.func))
            if not any(k.arg == "model_prose" and the_reader(k.value) for k in node.keywords):
                found.append(f"{owner.get(id(node), '<module>')}:{node.lineno}")
    for node in ast.walk(tree):
        where = f"{owner.get(id(node), '<module>')}:{getattr(node, 'lineno', 0)}"
        if (isinstance(node, ast.Attribute) and node.attr in readers
                and isinstance(node.value, ast.Name) and node.value.id in store_names
                and id(node) not in judged):
            found.append(where)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "getattr" and len(node.args) >= 2
              and isinstance(node.args[0], ast.Name) and node.args[0].id in store_names
              and isinstance(node.args[1], ast.Constant) and node.args[1].value in readers):
            found.append(where)
        elif (isinstance(node, ast.ImportFrom)
              and (node.module or "").split(".")[-1] == "store"
              and any(a.name in readers for a in node.names)):
            found.append(where)
    return sorted(set(found), key=lambda s: (int(s.rsplit(":", 1)[1]), s))


class EveryReaderMigratesAsCheckDoes(_env.EnvCase):
    """A legacy project is read through its migration planned in memory, and
    that plan depends on what the model states (the params rule). Every reader
    must plan it with the reader `check` migrates with, or a read command and
    `check` disagree about the same bytes.

    What slipped through: `store.load` planned the migration with no
    `model_prose` — lossless, every param keeping its rationale — while `check`
    and `doctor`'s records row planned it with `modelio.static_param_prose`. So
    on a legacy project whose model states `D` and `d`, every read command
    refused ("params/D.json and params/d.json would name ids that differ only in
    case … rename one") while `check` migrated it cleanly and wrote neither
    file; and after a migration killed half way, every read command blamed a
    `params/thickness.json` that was byte for byte what `check` would write
    ("differs from what .atompipe/ledger.json migrates to … Move those files
    aside"), while `check` completed it. Both remedies were false, and a person
    who followed the first renamed a model field for nothing."""

    def _reads(self, project: str, why: str, *, false_remedy: str) -> None:
        """Every reader works on the legacy ``project``, says nothing of
        ``false_remedy``, and leaves it legacy."""
        for argv in _LEGACY_READERS + (("why", why),):
            with self.subTest(argv=argv):
                proc = _env.atompipe(list(argv), cwd=project)
                said = proc.stdout + proc.stderr
                self.assertIn(proc.returncode, (0, 1),
                              f"`atompipe {' '.join(argv)}` refused a project `check` "
                              f"migrates:\n{said}")
                self.assertNotIn("Traceback", proc.stderr)
                self.assertNotIn(false_remedy, said,
                                 f"`atompipe {' '.join(argv)}` named a refusal `check` "
                                 f"does not make")
                self.assertNotIn("[FAIL] records", proc.stdout)
                self.assertTrue(store.is_legacy(project),
                                f"`atompipe {' '.join(argv)}` migrated a project it was reading")

    def _check_completes(self, project: str, plan: store.MigrationPlan) -> None:
        proc = _env.atompipe(["check"], cwd=project)
        self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertFalse(store.is_legacy(project), "check did not migrate")
        for rel, data in plan.files.items():
            self.assertEqual(_read_bytes(os.path.join(project, *rel.split("/"))), data, rel)

    def test_a_case_pair_the_model_states_reads_as_check_migrates_it(self):
        """V: the review's repro (``ir2/case.py``). The model states `D` and `d`,
        so the migration writes neither: every read command must read the project
        — never refuse it with "rename one" — and `check` then migrates it."""
        project = _case_pair_bracket(os.path.join(self.tmp(), "case"), stated=True)
        plan = store.migrate_legacy(project, apply=False, when="",
                                    model_prose=modelio.static_param_prose)
        self.assertFalse([rel for rel in plan.files if rel.startswith("params/")],
                         "the fixture no longer exercises the params rule: a case pair the "
                         "model states must write no param file")
        self._reads(project, "D", false_remedy="differ only in case")
        why = _env.atompipe(["why", "D"], cwd=project)
        self.assertIn(_CASE_PAIR[0][2], why.stdout, "why D lost the rationale the model states")
        self._check_completes(project, plan)

    def test_a_case_pair_only_the_ledger_holds_is_refused_by_every_command(self):
        """V: the other direction, so the one above cannot pass by never refusing.
        The model is silent on `D` and `d`, so both records keep their rationale
        and would be one file on a case-insensitive filesystem: `check` refuses,
        and every reader refuses with the same words, and nothing is written."""
        project = _case_pair_bracket(os.path.join(self.tmp(), "case"), stated=False)
        before = _tree(project)
        for argv in _LEGACY_READERS + (("why", "D"), ("check",)):
            with self.subTest(argv=argv):
                proc = _env.atompipe(list(argv), cwd=project)
                said = proc.stdout + proc.stderr
                self.assertIn(proc.returncode, (1,) if argv == ("doctor",) else (2,), said)
                self.assertIn("params/D.json and params/d.json", said)
                self.assertIn("differ only in case", said)
                self.assertNotIn("Traceback", proc.stderr)
        self.assertTrue(store.is_legacy(project))
        self.assertEqual(
            {rel: data for rel, data in _tree(project).items()
             if not rel.startswith((".atompipe/cache/", ".atompipe/obs/", ".atompipe/out/"))},
            {rel: data for rel, data in before.items()
             if not rel.startswith((".atompipe/cache/", ".atompipe/obs/", ".atompipe/out/"))},
            "a refused migration wrote something")

    def test_a_check_killed_mid_migration_reads_as_its_plan_and_completes(self):
        """V: the review's repro (``ir2/crash.py 12``), through the CLI a person
        runs. `check` on the enriched legacy bracket — which has a
        `params/thickness.json` to write — is killed at the rename of one file:
        the first result (every claim, decision, input, need and param record on
        disk) and the commit marker (every record on disk, no `project.json`).
        Every read command must read the half-migrated project as its plan —
        never blame a record `check` itself wrote — and the next `check` must
        complete it, byte for byte."""
        for target in ("results/C5.json", _PROJECT_REL):
            with self.subTest(killed_at=target):
                project = _legacy_bracket(os.path.join(self.tmp(), "legacy"))
                plan = store.migrate_legacy(project, apply=False, when="",
                                            model_prose=modelio.static_param_prose)
                self.assertIn("params/thickness.json", plan.files,
                              "the fixture no longer writes a param record, so the plan "
                              "difference this repro turns on cannot be seen")
                self.assertIn(target, plan.files)
                killed = _env.run([sys.executable, "-c", _KILL_DRIVER, project, target,
                                   "check"], cwd=project)
                self.assertEqual(killed.returncode, 137, killed.stdout + killed.stderr)
                self.assertTrue(store.is_legacy(project), "the killed check committed")
                self.assertFalse(os.path.exists(os.path.join(project, *target.split("/"))))
                self.assertTrue(os.path.isfile(os.path.join(project, "params",
                                                            "thickness.json")),
                                "the kill came before the param record: nothing to blame")
                self._reads(project, "thickness", false_remedy="has not finished migrating")
                self._check_completes(project, plan)

    def test_every_spine_reader_passes_the_reader_check_migrates_with(self):
        """Every `store.load` and `store.migrate_legacy` in a spine module passes
        `model_prose=` `static_param_prose`: a reader added without it plans a
        different migration from `check`'s, which is this class's repro."""
        spine = os.path.join(_env.SRC, "atompipe")
        found: dict[str, list[str]] = {}
        calls = 0
        for name in sorted(os.listdir(spine)):
            if not name.endswith(".py") or name == "store.py":
                continue
            with open(os.path.join(spine, name), encoding="utf-8") as fh:
                source = fh.read()
            calls += source.count("store.load(") + source.count("store.migrate_legacy(")
            bad = unmatched_migration_readers(source)
            if bad:
                found[name] = bad
        self.assertTrue(calls, "no spine module reads a project: the rule held vacuously")
        self.assertEqual(found, {}, "a reader plans a different migration from check's")

    def test_the_rule_catches_a_planted_reader(self):
        """V: each spelling of a reader without `check`'s `model_prose` is found; a
        call passing it, by any module path, is not."""
        planted = {
            "no model_prose": ("def cmd(root):\n    return store.load(root)\n", ["cmd:2"]),
            "model_prose=None": ("def cmd(root):\n    return store.migrate_legacy(\n"
                                 "        root, apply=False, when='', model_prose=None)\n",
                                 ["cmd:2"]),
            "another reader": ("def cmd(root):\n    return store.load(root, model_prose=f)\n",
                               ["cmd:2"]),
            "a reference": ("def cmd(root):\n    read = store.load\n    return read(root)\n",
                            ["cmd:2"]),
            "getattr": ("def cmd(root):\n    return getattr(store, 'load')(root)\n", ["cmd:2"]),
            "imported": ("from .store import migrate_legacy as plan\n"
                         "def cmd(root):\n    return plan(root)\n", ["<module>:1"]),
            "an alias of the module": ("from . import store as st\n"
                                       "def cmd(root):\n    return st.load(root)\n", ["cmd:3"]),
        }
        for label, (source, want) in planted.items():
            with self.subTest(label):
                self.assertEqual(unmatched_migration_readers(textwrap.dedent(source)), want)
        clean = ("def cmd(root):\n"
                 "    a = store.load(root, model_prose=modelio.static_param_prose)\n"
                 "    b = store.migrate_legacy(root, apply=False, when='',\n"
                 "                             model_prose=static_param_prose)\n"
                 "    return json.load(root), self.load()\n")
        self.assertEqual(unmatched_migration_readers(clean), [])



if __name__ == "__main__":
    unittest.main()
