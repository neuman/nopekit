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

`IndexNeverDisagreesWithRecords` is the library half of invariant 8 (the CLI
half, every command leaving `agree()` empty, is U30's). It never skips.

The params rule is tested with a FAKE `model_prose`: the migration is a pure
function of the legacy file and what the model states, and the real reader of
what the model states (`modelio.static_param_prose`) is another unit's; a fake
states exactly what each test needs and records that it was asked.

Run:  PYTHONPATH=src python3 -m unittest tests.test_records -v
"""
from __future__ import annotations

import contextlib
import copy
import fnmatch
import hashlib
import io
import json
import os
import time
import unittest
from unittest import mock

import _env
import _projects
from atompipe import claims as claims_mod
from atompipe import models, store, util
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


if __name__ == "__main__":
    unittest.main()
