# SPDX-License-Identifier: Apache-2.0
"""The verdict cache: entries you can commit, keyed by exactly what a gate read.

A verdict entry is a tracked file (PLAN D-05) named by the content address of
its inputs, rho. Everything here holds one of the ways that address or that
file could lie:

* **A read that is not an input.** The file a gate opened (M11.4) and the claim
  it read through ``ctx.ledger`` (M11.6, S-23 — openmodelica reads a claim's
  limit there) must each move rho when their bytes move. Before 1.2 neither was
  in any staleness key: a limit file edited under a project gate left C4 PASS
  where a re-run FAILs 19.6 g against 1 g (S-22).
* **An entry that is not the same bytes everywhere.** omc's errors embed
  absolute ``.mo`` paths, 33 of 54 gates cite absolute evidence, fixtures set
  absolute mesh paths: a tracked entry holding any of those differs per clone,
  and every clone writes its own copy of every verdict (packs:H5, H6, H9).
* **An entry that is not what was measured.** A skip or a crash cached as if it
  were a measurement; a hand edit read back as a verdict; two outcomes for one
  rho silently resolved by picking one.
* **A control keyed by its lookup hint.** The fixture's code closure moves
  whenever the model does (the bracket's fixtures import it); keyed on that,
  every Config edit would write six tracked control files. It is a hint, not an
  input (``test_fixture_closure_is_not_in_rho_control``).
* **Latency mixed with controls** (S-31): the run history mixed
  ``<gate>#selftest`` rows with sweep rows, and no reader filtered them.

Run:  PYTHONPATH=src python3 -m unittest tests.test_cache -v
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import sys
import textwrap
import unittest
import uuid
from unittest import mock

from atompipe import gates, modelio, packs, store, vcs, verdicts
from atompipe.models import (
    Acceptance, Claim, GateSpec, Ledger, NegativeControl, Tier, Verdict,
)
from atompipe.util import AtompipeError, FileDigests
from atompipe.verdicts import Anchors, GateTrace

import _env
import _projects


REPO = _env.REPO
PACKS_DIR = os.path.join(REPO, "packs")

ENTRY_KEYS = ["schema", "gate", "rho", "code", "spine", "reads", "instruments",
              "verdict", "digest"]
READS_KEYS = ["params", "files", "dirs", "ledger", "model", "opaque"]
VERDICT_KEYS = ["passed", "measured", "limit", "units", "detail", "evidence",
                "locators", "claims", "tier", "pack"]
CONTROL_KEYS = ["schema", "kind", "gate", "rho", "static", "static_parts", "host",
                "fixture", "reads", "bad", "good", "admitted", "detail", "measured",
                "limit", "units", "digest"]
CONTROL_READS_KEYS = ["params", "files", "dirs", "ledger", "host", "opaque"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _canonical(obj) -> str:
    """The documented canonical form, re-stated here so the test is an oracle
    for the digest rule rather than a caller of the code that implements it."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def _sha(obj) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _write(root: str, rel: str, text: str) -> str:
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(textwrap.dedent(text))
    return path


def _project(case: _env.EnvCase) -> str:
    """An empty project directory: a marker and nothing else."""
    root = os.path.join(case.tmp(), "project")
    os.makedirs(os.path.join(root, ".atompipe"))
    _write(root, ".atompipe/project.json", '{"schema": 2}\n')
    return root


_GATE_HEAD = '''\
import os
import shutil
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict
'''


def _gate_source(gate_id: str, body: str, *, fixture: str = "selftest/bad.py:make",
                 extra_head: str = "") -> str:
    """A project gate module holding one gate whose function is ``body``."""
    name = gate_id.replace(".", "_")
    return (_GATE_HEAD + textwrap.dedent(extra_head) + f'''

@gate(id={gate_id!r}, title="t", claims=["t"],
      negative_control=NegativeControl(fixture={fixture!r}))
def {name}(ctx):
''' + textwrap.indent(textwrap.dedent(body), "    "))


def _registry(root: str) -> gates.Registry:
    """The project's gates in a fresh registry — never ``gates.REGISTRY``."""
    registry = gates.Registry()
    gates.load_project_gates(root, registry)
    return registry


def _ctx(root: str, params: dict, *, ledger: Ledger | None = None,
         out_dir: str | None = None) -> gates.GateContext:
    return gates.GateContext(root=root, ledger=ledger or Ledger(), model=None,
                             params=params, out_dir=out_dir or store.out_dir(root),
                             tier=3, log=lambda _m: None, extra={})


def _anchors(root: str, registry: gates.Registry | None = None) -> Anchors:
    return verdicts.anchors_for(root, registry, out_dir=store.out_dir(root))


def _run(registry: gates.Registry, gate_id: str, ctx: gates.GateContext,
         anchors: Anchors | None = None) -> tuple[Verdict, GateTrace]:
    spec, fn = registry.get(gate_id)
    trace = GateTrace(anchors=anchors)
    return gates.run_gate(spec, fn, ctx, trace=trace), trace


def _entry_files(root: str, gate_id: str, *, controls: bool = False) -> list[str]:
    directory = os.path.join(root, ".atompipe", "verdicts", gate_id)
    if not os.path.isdir(directory):
        return []
    return sorted(n for n in os.listdir(directory)
                  if n.endswith(".json") and n.startswith("control-") == controls)


def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _pack_dirs() -> list[str]:
    return sorted(os.path.join(PACKS_DIR, name) for name in os.listdir(PACKS_DIR)
                  if os.path.isfile(os.path.join(PACKS_DIR, name, "pack.json")))


def _plain_spec(gate_id: str = "t.plain", **kw) -> GateSpec:
    kw.setdefault("negative_control", NegativeControl(fixture="selftest/bad.py:make"))
    return GateSpec(id=gate_id, claims=kw.pop("claims", ["t"]), **kw)


def _plain_gate(ctx):
    """A gate defined in this file: in-process, so it has no recorded closure."""
    return Verdict(gate="t.plain", passed=True, measured=1.0, limit=2.0)


# --------------------------------------------------------------------------- #
class RhoIsWhatTheGateRead(_env.EnvCase):
    """M11.4 and M11.6: what a gate read is in its content address."""

    def test_a_file_the_gate_opened_is_part_of_rho(self):
        # S-22's shape: a limit file under a project gate, edited in place with
        # the same size and its mtime put back. Only the bytes can see it.
        root = _project(self)
        _write(root, "data/limit.txt", "1.00\n")
        _write(root, "gates/limits.py", _gate_source("t.limit_file", '''
            with open(os.path.join(ctx.root, "data", "limit.txt"), encoding="utf-8") as fh:
                limit = float(fh.read())
            x = float(ctx.params["x"])
            return Verdict(gate="t.limit_file", passed=x <= limit, measured=x,
                           limit=limit, units="g")
        '''))
        registry = _registry(root)
        anchors = _anchors(root, registry)
        spec, fn = registry.get("t.limit_file")

        first, trace = _run(registry, "t.limit_file", _ctx(root, {"x": 0.5}), anchors)
        self.assertEqual(first.outcome, "pass")
        wrote = verdicts.record_verdict(root, spec, fn, first, trace=trace,
                                        anchors=anchors, digests=FileDigests())
        entry = _load_json(wrote.path)
        limit_path = os.path.join(root, "data", "limit.txt")
        with open(limit_path, "rb") as fh:
            original = fh.read()
        self.assertEqual(entry["reads"]["files"],
                         {"data/limit.txt": hashlib.sha256(original).hexdigest()})

        stat = os.stat(limit_path)
        with open(limit_path, "w", encoding="utf-8") as fh:
            fh.write("2.00\n")                        # same size
        os.utime(limit_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

        # The old trace re-read against the new bytes: a different address.
        again = verdicts.Reads.from_trace(trace, anchors=anchors, digests=FileDigests())
        self.assertNotEqual(
            verdicts.rho("t.limit_file", verdicts.spine_digest(),
                         verdicts.code_digest(spec, fn, anchors=anchors), again),
            wrote.rho, "a same-size edit of a file the gate opened left rho unchanged")

        second, trace2 = _run(registry, "t.limit_file", _ctx(root, {"x": 0.5}), anchors)
        self.assertEqual(second.outcome, "pass", "same outcome: only the inputs moved")
        wrote2 = verdicts.record_verdict(root, spec, fn, second, trace=trace2,
                                         anchors=anchors, digests=FileDigests())
        self.assertNotEqual(wrote2.rho, wrote.rho)
        self.assertEqual(len(_entry_files(root, "t.limit_file")), 2)

    def test_reading_a_claim_makes_it_an_input(self):
        # S-23: openmodelica reads a claim's limit through ctx.ledger
        # (modelica.py:181-187, 256). Editing the claim's acceptance left the
        # verdict fresh. The claim the gate read is an input; another is not.
        root = _project(self)
        _write(root, "gates/claimed.py", _gate_source("t.claim_limit", '''
            claim = ctx.ledger.claim("C1")
            limit = claim.acceptance.limit
            x = float(ctx.params["x"])
            return Verdict(gate="t.claim_limit", passed=x <= limit, measured=x,
                           limit=limit, units="mm")
        '''))
        registry = _registry(root)
        anchors = _anchors(root, registry)
        spec, fn = registry.get("t.claim_limit")

        def ledger(c1_limit: float, c2_limit: float = 9.0) -> Ledger:
            return Ledger(claims=[
                Claim(id="C1", statement="tip sag",
                      acceptance=Acceptance(quantity="x", limit=c1_limit, units="mm")),
                Claim(id="C2", statement="unrelated",
                      acceptance=Acceptance(quantity="y", limit=c2_limit, units="mm")),
            ])

        def record(led: Ledger) -> verdicts.WriteResult:
            verdict, trace = _run(registry, "t.claim_limit", _ctx(root, {"x": 0.4}, ledger=led),
                                  anchors)
            self.assertEqual(verdict.outcome, "pass")
            return verdicts.record_verdict(root, spec, fn, verdict, trace=trace,
                                           anchors=anchors, digests=FileDigests())

        base = record(ledger(0.5))
        entry = _load_json(base.path)
        self.assertIn("claim:C1", entry["reads"]["ledger"])
        self.assertNotIn("claim:C2", entry["reads"]["ledger"])

        moved = record(ledger(0.6))
        self.assertNotEqual(moved.rho, base.rho,
                            "editing the claim the gate read left its verdict's rho "
                            "unchanged — a cached PASS would outlive a moved limit (S-23)")
        untouched = record(ledger(0.5, c2_limit=1.0))
        self.assertEqual(untouched.rho, base.rho,
                         "editing a claim the gate never read moved its rho")


# --------------------------------------------------------------------------- #
class EntriesAreWrittenOnce(_env.EnvCase):
    def _setup(self):
        root = _project(self)
        _write(root, "gates/plain.py", _gate_source("t.ok", '''
            return Verdict(gate="t.ok", passed=ctx.params["x"] < 1, measured=ctx.params["x"],
                           limit=1.0, units="mm", detail=f"{ctx.params['x']} mm")
        '''))
        registry = _registry(root)
        return root, registry, _anchors(root, registry)

    def test_o_excl_and_identical_bytes(self):
        root, registry, anchors = self._setup()
        spec, fn = registry.get("t.ok")
        verdict, trace = _run(registry, "t.ok", _ctx(root, {"x": 0.25}), anchors)
        first = verdicts.record_verdict(root, spec, fn, verdict, trace=trace, anchors=anchors)
        self.assertEqual(first.status, "written")
        self.assertTrue(first.written)
        name = os.path.basename(first.path)
        self.assertRegex(name, r"^[0-9a-f]{16}-[0-9a-f]{8}\.json$")
        self.assertEqual(name[:16], first.rho[:16])
        self.assertEqual(name[17:25], verdicts.out8(verdict))

        with open(first.path, "rb") as fh:
            data = fh.read()
        before = os.stat(first.path).st_mtime_ns
        second = verdicts.record_verdict(root, spec, fn, verdict, trace=trace, anchors=anchors)
        self.assertEqual(second.status, "exists")
        self.assertFalse(second.written)
        self.assertEqual(second.path, first.path)
        with open(first.path, "rb") as fh:
            self.assertEqual(fh.read(), data, "an entry was rewritten")
        self.assertEqual(os.stat(first.path).st_mtime_ns, before)

        # The bytes: fixed key order, indent 2, one trailing newline, strict JSON.
        text = data.decode("utf-8")
        self.assertTrue(text.endswith("}\n") and not text.endswith("\n\n"))
        body = json.loads(text)
        self.assertEqual(list(body), ENTRY_KEYS)
        self.assertEqual(list(body["reads"]), READS_KEYS)
        self.assertEqual(list(body["verdict"]), VERDICT_KEYS)
        self.assertEqual(list(body["code"]), ["digest", "files", "fallback"])
        self.assertEqual(text, json.dumps(body, indent=2, ensure_ascii=False) + "\n")
        self.assertEqual(body["schema"], 1)
        self.assertEqual(body["rho"], first.rho)
        self.assertEqual(body["spine"], verdicts.spine_digest())
        self.assertEqual(body["code"]["files"], ["gates/plain.py"])
        # the digest is the documented rule, recomputed here independently
        others = {k: v for k, v in body.items() if k != "digest"}
        self.assertEqual(body["digest"], _sha(others))
        # costs live in obs, never in a tracked entry
        for key in ("duration_s", "cpu_s", "rho", "skipped", "error", "gate"):
            self.assertNotIn(key, body["verdict"])
        self.assertEqual(body["reads"]["params"], [[["x"], verdicts.digest_value(0.25), 0.25]])
        self.assertEqual(body["verdict"]["passed"], True)

        # Read back: the verdict, with its full rho and no replayed cost.
        [entry] = verdicts.read_entries(root, "t.ok")
        back = entry.to_verdict()
        self.assertEqual((back.gate, back.passed, back.measured, back.limit, back.units),
                         ("t.ok", True, 0.25, 1.0, "mm"))
        self.assertEqual(back.rho, first.rho)
        self.assertEqual((back.duration_s, back.cpu_s), (0.0, 0.0))

    def test_same_name_different_bytes_keeps_first_and_warns(self):
        root, registry, anchors = self._setup()
        spec, fn = registry.get("t.ok")
        verdict, trace = _run(registry, "t.ok", _ctx(root, {"x": 0.25}), anchors)
        first = verdicts.record_verdict(root, spec, fn, verdict, trace=trace, anchors=anchors)
        with open(first.path, "rb") as fh:
            data = fh.read()
        # Same inputs, same outcome tuple, different detail: one name, two bytes.
        other = dataclasses.replace(verdict, detail="0.25 mm, said differently")
        again = verdicts.record_verdict(root, spec, fn, other, trace=trace, anchors=anchors)
        self.assertEqual(again.status, "kept-first")
        self.assertEqual(again.path, first.path)
        self.assertTrue(any("nondeterministic detail" in w for w in again.warnings),
                        again.warnings)
        with open(first.path, "rb") as fh:
            self.assertEqual(fh.read(), data, "the first entry must be kept")
        self.assertEqual(len(_entry_files(root, "t.ok")), 1)


# --------------------------------------------------------------------------- #
class EntryIntegrity(_env.EnvCase):
    def _written(self) -> tuple[str, str]:
        root = _project(self)
        verdict = Verdict(gate="t.plain", passed=True, measured=1.0, limit=2.0, units="mm")
        wrote = verdicts.record_verdict(root, _plain_spec(), _plain_gate, verdict)
        return root, wrote.path

    def _rewrite(self, path: str, mutate, *, reseal: bool) -> None:
        body = _load_json(path)
        mutate(body)
        if reseal:
            body["digest"] = _sha({k: v for k, v in body.items() if k != "digest"})
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(body, indent=2, ensure_ascii=False) + "\n")

    def test_hand_edit_is_detected(self):
        root, path = self._written()
        self.assertEqual(len(verdicts.read_entries(root, "t.plain")), 1)
        self._rewrite(path, lambda b: b["verdict"].update(measured=1.5), reseal=False)
        problems: list[str] = []
        self.assertEqual(verdicts.read_entries(root, "t.plain", problems=problems), [])
        self.assertTrue(any("hand-edited entry" in p for p in problems), problems)

    def test_non_bool_passed_in_an_entry_is_refused(self):
        cases = {
            "passed": lambda b: b["verdict"].update(passed="false"),
            "measured": lambda b: b["verdict"].update(measured="n/a"),
            "limit": lambda b: b["verdict"].update(limit=True),
        }
        for what, mutate in cases.items():
            with self.subTest(what):
                root, path = self._written()
                self._rewrite(path, mutate, reseal=True)     # a digest that matches
                problems: list[str] = []
                self.assertEqual(verdicts.read_entries(root, "t.plain", problems=problems), [])
                self.assertTrue(any(what in p for p in problems), problems)

        for what, edit in {
            "NaN": lambda t: t.replace('"measured": 1.0', '"measured": NaN'),
            "duplicate": lambda t: t.replace('"units": "mm"', '"units": "mm", "units": "m"'),
        }.items():
            with self.subTest(what):
                root, path = self._written()
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
                changed = edit(text)
                self.assertNotEqual(changed, text)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(changed)
                problems = []
                self.assertEqual(verdicts.read_entries(root, "t.plain", problems=problems), [])
                self.assertEqual(len(problems), 1, problems)

    def test_the_writer_refuses_a_non_bool_pass(self):
        root = _project(self)
        verdict = Verdict(gate="t.plain", passed=True, measured=1.0, limit=2.0)
        wrote = verdicts.record_verdict(root, _plain_spec(), _plain_gate, verdict)
        [entry] = verdicts.read_entries(root, "t.plain")
        bad = dataclasses.replace(entry, verdict={**entry.verdict, "passed": 1})
        with self.assertRaises(AtompipeError):
            verdicts.write_entry(root, bad)
        self.assertEqual(_entry_files(root, "t.plain"), [os.path.basename(wrote.path)])


# --------------------------------------------------------------------------- #
class OnlyMeasurementsAreCached(_env.EnvCase):
    def test_skip_and_error_are_remembered_not_cached(self):
        root = _project(self)
        skip = Verdict(gate="t.plain", skipped=True, skip_reason="requires python nothing")
        crash = Verdict(gate="t.plain", error="ZeroDivisionError: boom", detail="trace")
        for verdict in (skip, crash):
            self.assertIsNone(verdicts.record_verdict(root, _plain_spec(), _plain_gate, verdict))
        self.assertEqual(_entry_files(root, "t.plain"), [])
        self.assertFalse(os.path.isdir(os.path.join(root, ".atompipe", "verdicts")),
                         "nothing cacheable ran, so nothing may be written there")

        verdicts.remember(root, "t.plain", crash, input_rho="", kind="error",
                          when="2026-09-27T10:00:00Z")
        held = verdicts.remembered(root)
        self.assertEqual(list(held["t.plain"]), [""], "one record, at the rho it was given")
        self.assertEqual(held["t.plain"][""]["kind"], "error")
        self.assertEqual(held["t.plain"][""]["verdict"].outcome, "error")
        self.assertEqual(held["t.plain"][""]["when"], "2026-09-27T10:00:00Z")
        self.assertTrue(os.path.isfile(
            os.path.join(root, ".atompipe", "cache", "last_outcomes.json")))
        # A remembered outcome is never a pass: remembering one is refused.
        with self.assertRaises(AtompipeError):
            verdicts.remember(root, "t.plain", Verdict(gate="t.plain", passed=True),
                              input_rho="", kind="error", when="")
        with self.assertRaises(AtompipeError):
            verdicts.remember(root, "t.plain", crash, input_rho="", kind="bogus", when="")

    def test_an_unrecorded_gate_is_opaque_never_a_digest_of_nothing(self):
        root = _project(self)
        verdict = Verdict(gate="t.planted", passed=True)
        wrote = verdicts.record_verdict(root, None, None, verdict)
        body = _load_json(wrote.path)
        self.assertEqual(body["code"]["digest"], "")
        self.assertTrue(any(c.startswith("code:") for c in body["reads"]["opaque"]),
                        body["reads"]["opaque"])
        self.assertTrue(verdicts.CodeRef.unrecorded().opaque)


# --------------------------------------------------------------------------- #
_PORTABLE_GATE = '''
path = ctx.out_path("t", "ev.txt")
with open(path, "w", encoding="utf-8") as fh:
    fh.write("evidence\\n")
data = os.path.join(ctx.root, "data", "x.txt")
with open(data, encoding="utf-8") as fh:
    fh.read()
return Verdict(gate="t.portable", passed=True, measured=1.0, limit=2.0, units="",
               detail=f"read {data}; wrote {path}", evidence=[path])
'''


class Portability(_env.EnvCase):
    def _record_all(self, root: str) -> dict[str, bytes]:
        _write(root, "data/x.txt", "x\n")
        _write(root, "gates/portable.py",
               _gate_source("t.portable", _PORTABLE_GATE,
                            fixture="selftest/bad_configs.py:quarter_thickness"))
        registry = _registry(root)
        anchors = _anchors(root, registry)
        loaded = modelio.load_model(root, "model/bracket.py")
        params, _ = modelio.flat_params(modelio.project(loaded))
        ctx = _ctx(root, params)
        for spec in registry.specs():
            spec, fn = registry.get(spec.id)
            verdict, trace = _run(registry, spec.id, ctx, anchors)
            self.assertIn(verdict.outcome, ("pass", "fail"), verdict.render())
            wrote = verdicts.record_verdict(root, spec, fn, verdict, trace=trace,
                                            anchors=anchors, digests=FileDigests())
            self.assertEqual(wrote.status, "written")
        base = os.path.join(root, ".atompipe", "verdicts")
        found: dict[str, bytes] = {}
        for gate_id in sorted(os.listdir(base)):
            for name in sorted(os.listdir(os.path.join(base, gate_id))):
                with open(os.path.join(base, gate_id, name), "rb") as fh:
                    found[f"{gate_id}/{name}"] = fh.read()
        return found

    def test_entry_bytes_equal_from_two_directories(self):
        one = _projects.bracket_copy(os.path.join(self.tmp(), "one"))
        two = _projects.bracket_copy(os.path.join(self.tmp(), "deeper", "two"))
        first = self._record_all(one)
        second = self._record_all(two)
        self.assertEqual(len(first), 7, sorted(first))
        self.assertEqual(sorted(first), sorted(second), "different entry names per checkout")
        for name in first:
            self.assertEqual(first[name], second[name], f"{name} differs per checkout")
        [portable] = [k for k in first if k.startswith("t.portable/")]
        body = json.loads(first[portable])
        self.assertEqual(body["verdict"]["evidence"], ["<out>/t/ev.txt"])
        self.assertEqual(body["verdict"]["detail"],
                         "read <root>/data/x.txt; wrote <out>/t/ev.txt")
        self.assertEqual(body["reads"]["files"],
                         {"data/x.txt": hashlib.sha256(b"x\n").hexdigest()})
        self.assertNotIn(one, first[portable].decode())

    def test_omc_style_absolute_path_in_detail_is_portable(self):
        # packs:H9: a failing omc detail quotes the absolute .mo path.
        found = []
        for where in ("a", os.path.join("b", "c")):
            base = self.tmp()
            root = os.path.join(base, where, "project")
            os.makedirs(root)
            pack = os.path.join(base, "elsewhere", "openmodelica")
            os.makedirs(pack)
            mo = os.path.join(pack, "selftest", "assets", "bad", "WillNotCompile.mo")
            verdict = Verdict(gate="t.plain", passed=False, pack="openmodelica",
                              detail=f"[{mo}:31:5-31:35:writable] Error: Variable x not found")
            anchors = Anchors(root=root, packs={"openmodelica": pack},
                              out=store.out_dir(root),
                              controls_out=os.path.join(root, verdicts.CONTROL_OUT_DIR))
            wrote = verdicts.record_verdict(root, _plain_spec(pack="openmodelica"),
                                            _plain_gate, verdict, anchors=anchors)
            with open(wrote.path, "rb") as fh:
                found.append((os.path.basename(wrote.path), fh.read()))
        self.assertEqual(found[0], found[1])
        body = json.loads(found[0][1])
        self.assertEqual(body["verdict"]["detail"],
                         "[<pack:openmodelica>/selftest/assets/bad/WillNotCompile.mo:31:5-31:35:"
                         "writable] Error: Variable x not found")

    def test_evidence_outside_anchors_is_dropped(self):
        root = _project(self)
        out = store.out_dir(root)
        verdict = Verdict(gate="t.plain", passed=True, evidence=[
            os.path.join(out, "plot.png"),
            os.path.join(os.sep, "atompipe-nowhere", "elsewhere.png"),
            "relative/kept.png",
        ])
        wrote = verdicts.record_verdict(root, _plain_spec(), _plain_gate, verdict)
        body = _load_json(wrote.path)
        self.assertEqual(body["verdict"]["evidence"], ["<out>/plot.png", "relative/kept.png"])


# --------------------------------------------------------------------------- #
class ControlStatic(_env.EnvCase):
    def _tree(self, owner: str) -> None:
        _write(owner, "selftest/bad.py", "def make(ctx):\n    return ctx\n")
        _write(owner, "selftest/data/table.json", "{}\n")
        _write(owner, "selftest/__pycache__/bad.cpython-312.pyc", "bytecode")
        _write(owner, "selftest/stale.pyc", "bytecode")
        _write(owner, "selftest/.generated/out.csv", "a,b\n")

    def test_walk_ignores_bytecode_and_dotdirs_outside_git(self):
        owner = self.tmp()
        self._tree(owner)
        self.assertFalse(vcs.is_repo(owner))
        walk = verdicts.selftest_walk(owner, digests=FileDigests())
        self.assertEqual(sorted(walk), ["selftest/bad.py", "selftest/data/table.json"])
        self.assertEqual(walk["selftest/data/table.json"],
                         hashlib.sha256(b"{}\n").hexdigest())

    def test_walk_uses_ls_files_inside_git(self):
        owner = self.tmp()
        self._tree(owner)
        _write(owner, ".gitignore", "selftest/ignored.txt\n")
        _write(owner, "selftest/ignored.txt", "not an input\n")
        _write(owner, "selftest/untracked.py", "X = 1\n")
        for argv in (["init", "-q"], ["add", ".gitignore", "selftest/bad.py",
                                      "selftest/data/table.json"]):
            proc = _env.git(argv, cwd=owner)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        walk = verdicts.selftest_walk(owner, digests=FileDigests())
        # tracked plus untracked-not-ignored; bytecode and the dot-dir never
        self.assertEqual(sorted(walk), ["selftest/bad.py", "selftest/data/table.json",
                                        "selftest/untracked.py"])
        with mock.patch.object(vcs, "ls_files", return_value=None):
            outside = verdicts.selftest_walk(owner, digests=FileDigests())
        self.assertIn("selftest/ignored.txt", outside,
                      "the git answer was not used: the ignored file is only out of git's")

    def test_git_and_non_git_walks_agree_on_the_bracket(self):
        # verify.sh --dir copies have no .git (tests:H11), so a control entry
        # written in one mode must be found in the other.
        with_git = _projects.bracket_copy(os.path.join(self.tmp(), "b"), git=True)
        without = _projects.bracket_copy(os.path.join(self.tmp(), "b"))
        for root in (with_git, without):
            # a run leaves bytecode beside the fixtures; the copy has no ignore for it
            _write(root, "selftest/__pycache__/bad_configs.cpython-312.pyc", "bytecode")
        self.assertTrue(vcs.is_repo(with_git))
        self.assertIsNotNone(vcs.ls_files(with_git, ["selftest"]))
        self.assertIsNone(vcs.ls_files(without, ["selftest"]))
        git_walk = verdicts.selftest_walk(with_git, digests=FileDigests())
        plain_walk = verdicts.selftest_walk(without, digests=FileDigests())
        self.assertEqual(git_walk, plain_walk)
        self.assertEqual(sorted(git_walk), ["selftest/bad_configs.py", "selftest/known_good.py"])
        with mock.patch.object(vcs, "ls_files", return_value=None):
            self.assertEqual(verdicts.selftest_walk(with_git, digests=FileDigests()), git_walk)

        # and the checkout's own bracket, when a repository holds it
        bracket = _projects.BRACKET
        if vcs.ls_files(bracket, ["selftest"]) is not None:
            with mock.patch.object(vcs, "ls_files", return_value=None):
                walked = verdicts.selftest_walk(bracket, digests=FileDigests())
            self.assertEqual(verdicts.selftest_walk(bracket, digests=FileDigests()), walked)

    def test_fixture_closure_is_not_in_rho_control(self):
        root = _project(self)
        _write(root, "model/helper.py", "BAD_X = 9.0\n")
        _write(root, "selftest/bad.py", '''
            import dataclasses
            import os

            from atompipe.modelio import load_path

            helper = load_path(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "model", "helper.py"))


            def make(ctx):
                return dataclasses.replace(ctx, params={"x": helper.BAD_X})
        ''')
        _write(root, "gates/fixed.py", _gate_source("t.fixed", '''
            x = float(ctx.params["x"])
            return Verdict(gate="t.fixed", passed=x <= 1.0, measured=x, limit=1.0)
        '''))
        registry = _registry(root)
        anchors = _anchors(root, registry)
        spec, fn = registry.get("t.fixed")
        controls = os.path.join(root, verdicts.CONTROL_OUT_DIR, "t.fixed")

        def control() -> verdicts.WriteResult:
            trace = GateTrace(kind="control", anchors=anchors)
            result = gates.selftest(spec, fn, _ctx(root, {"x": 0.5}), trace=trace,
                                    out_dir=controls)
            self.assertEqual(result.outcome, "pass", result.render())
            return verdicts.record_control(root, spec, fn, result=result, trace=trace,
                                           host="known-good", anchors=anchors,
                                           digests=FileDigests())

        first = control()
        self.assertEqual(first.status, "written")
        body = _load_json(first.path)
        self.assertEqual(list(body), CONTROL_KEYS)
        self.assertEqual(list(body["reads"]), CONTROL_READS_KEYS)
        self.assertIn("model/helper.py", body["fixture"]["files"])
        self.assertIn("selftest/bad.py", body["fixture"]["files"])
        self.assertIn("selftest/bad.py", body["static_parts"]["selftest"]["files"])
        self.assertNotIn("model/helper.py", body["static_parts"]["selftest"]["files"])
        self.assertEqual(body["rho"], verdicts.rho_control(
            "t.fixed", body["static"], verdicts.Reads.from_dict(body["reads"])))

        # the helper moves, the value the fixture builds does not: same control
        _write(root, "model/helper.py", "# a comment: the helper moved\nBAD_X = 9.0\n")
        again = control()
        self.assertEqual(again.path, first.path,
                         "a fixture-closure move with equal control values wrote a new "
                         "control entry — the hint was keyed as an input")
        self.assertNotEqual(again.status, "written")
        self.assertEqual(again.warnings, ())
        self.assertEqual(len(_entry_files(root, "t.fixed", controls=True)), 1)

        # the value moves: a different control input, a different entry
        _write(root, "model/helper.py", "BAD_X = 10.0\n")
        moved = control()
        self.assertEqual(moved.status, "written")
        self.assertNotEqual(moved.path, first.path)


# --------------------------------------------------------------------------- #
class ControlEntries(_env.EnvCase):
    def _project(self, gate_body: str, fixture_body: str):
        root = _project(self)
        _write(root, "selftest/bad.py", fixture_body)
        _write(root, "gates/g.py", _gate_source("t.g", gate_body))
        registry = _registry(root)
        spec, fn = registry.get("t.g")
        return root, registry, spec, fn

    def _selftest(self, root, spec, fn, *, host_params=None):
        trace = GateTrace(kind="control", anchors=_anchors(root))
        result = gates.selftest(spec, fn, _ctx(root, host_params or {"x": 0.5}), trace=trace,
                                out_dir=os.path.join(root, verdicts.CONTROL_OUT_DIR, spec.id))
        return result, trace

    _BAD = '''
        import dataclasses

        def make(ctx):
            return dataclasses.replace(ctx, params={"x": 5.0})
    '''

    def test_a_fired_control_is_admitted_reject_only(self):
        root, _r, spec, fn = self._project('''
            x = float(ctx.params["x"])
            return Verdict(gate="t.g", passed=x <= 1.0, measured=x, limit=1.0, units="mm")
        ''', self._BAD)
        result, trace = self._selftest(root, spec, fn)
        wrote = verdicts.record_control(root, spec, fn, result=result, trace=trace)
        self.assertRegex(os.path.basename(wrote.path), r"^control-[0-9a-f]{16}-[0-9a-f]{8}\.json$")
        body = _load_json(wrote.path)
        self.assertEqual((body["kind"], body["bad"], body["good"], body["admitted"], body["host"]),
                         ("control", "fail", None, "reject-only", "live"))
        self.assertEqual((body["measured"], body["limit"], body["units"]), (5.0, 1.0, "mm"))
        self.assertEqual(body["digest"], _sha({k: v for k, v in body.items() if k != "digest"}))
        static, parts = verdicts.control_static(spec, fn, root, digests=FileDigests())
        self.assertEqual(body["static"], static)
        self.assertEqual(body["static_parts"], parts)
        self.assertEqual(parts["nc"], {"fixture": "selftest/bad.py:make", "expect": "fail",
                                       "note": ""})
        self.assertEqual(body["reads"]["params"], [[["x"], verdicts.digest_value(5.0), 5.0]])
        [entry] = verdicts.read_controls(root, "t.g")
        self.assertEqual((entry.bad, entry.admitted, entry.rho), ("fail", "reject-only", body["rho"]))
        self.assertEqual(verdicts.read_entries(root, "t.g"), [],
                         "a control entry read back as a verdict")

    def test_a_gate_passing_its_fixture_is_recorded_not_admitted(self):
        root, _r, spec, fn = self._project('''
            return Verdict(gate="t.g", passed=True, measured=0.0, limit=1.0)
        ''', self._BAD)
        result, trace = self._selftest(root, spec, fn)
        # The wording this reads is gates.selftest's own; pinned here so a
        # reworded message turns this red instead of filing a logger as a crash.
        self.assertTrue(result.detail.startswith("t.g PASSED its own known-bad fixture"),
                        result.detail)
        wrote = verdicts.record_control(root, spec, fn, result=result, trace=trace)
        body = _load_json(wrote.path)
        self.assertEqual((body["bad"], body["admitted"]), ("pass", "no"))

    def test_a_crash_is_remembered_not_cached(self):
        root, _r, spec, fn = self._project('''
            raise RuntimeError("the gate tripped over the fixture")
        ''', self._BAD)
        result, trace = self._selftest(root, spec, fn)
        self.assertEqual(result.outcome, "fail")
        self.assertIsNone(verdicts.record_control(root, spec, fn, result=result, trace=trace,
                                                  when="2026-09-27T10:00:00Z"))
        self.assertEqual(_entry_files(root, "t.g", controls=True), [])
        [held] = verdicts.remembered(root)["control:t.g"].values()
        static, _parts = verdicts.control_static(spec, fn, root, digests=FileDigests())
        self.assertEqual(held["input_rho"], static, "a control crash is keyed by the static part")
        self.assertEqual(held["kind"], "error")

        # a control entry written later clears it
        verdicts.record_control(root, spec, fn, bad="fail", detail="planted")
        self.assertNotIn("control:t.g", verdicts.remembered(root))

    def test_a_self_skip_is_remembered_as_one(self):
        # S-12's shape on a control: the gate decides its known-bad input does
        # not apply. Not a measurement; remembered, and its wording pinned.
        root, _r, spec, fn = self._project('''
            if float(ctx.params["x"]) > 1.0:
                return Verdict(gate="t.g", skipped=True, skip_reason="not my input")
            return Verdict(gate="t.g", passed=True)
        ''', self._BAD)
        result, trace = self._selftest(root, spec, fn)
        self.assertEqual(result.outcome, "error")
        self.assertTrue(result.error.startswith(
            "skipped on its own known-bad input while its tools are present"), result.error)
        self.assertIsNone(verdicts.record_control(root, spec, fn, result=result, trace=trace))
        [held] = verdicts.remembered(root)["control:t.g"].values()
        self.assertEqual(held["kind"], "self-skip")
        self.assertEqual(held["verdict"].outcome, "error")
        self.assertEqual(_entry_files(root, "t.g", controls=True), [])

    def test_the_forged_form(self):
        # test_site plants an admission this way (U21); it is a forgery of the
        # inner loop only, which R-9's re-execution at the boundary answers.
        root, _r, spec, fn = self._project('''
            return Verdict(gate="t.g", passed=True)
        ''', self._BAD)
        wrote = verdicts.record_control(root, spec, fn, bad="fail",
                                        detail="planted by a renderer test")
        body = _load_json(wrote.path)
        self.assertEqual((body["bad"], body["admitted"], body["detail"]),
                         ("fail", "reject-only", "planted by a renderer test"))
        self.assertEqual(body["reads"], {"params": [], "files": {}, "dirs": {}, "ledger": {},
                                         "host": [], "opaque": []})
        with self.assertRaises(AtompipeError):
            verdicts.record_control(root, spec, fn)       # neither a result nor bad=

    def test_live_host_reads_are_keyed_and_known_good_ones_are_not(self):
        # An identity fixture: the gate then reads the host's values through it.
        root, _r, spec, fn = self._project('''
            x = float(ctx.params["x"])
            return Verdict(gate="t.g", passed=x <= 1.0, measured=x, limit=1.0)
        ''', '''
            def make(ctx):
                return ctx
        ''')
        results = {}
        for host in ("live", "known-good"):
            result, trace = self._selftest(root, spec, fn, host_params={"x": 3.0})
            self.assertEqual(result.outcome, "pass")
            self.assertTrue(trace.host_reads)
            results[host] = _load_json(verdicts.record_control(
                root, spec, fn, result=result, trace=trace, host=host).path)
        self.assertEqual(results["live"]["reads"]["host"],
                         [[["x"], verdicts.digest_value(3.0)]])
        self.assertEqual(results["known-good"]["reads"]["host"], [])
        self.assertNotEqual(results["live"]["rho"], results["known-good"]["rho"])


# --------------------------------------------------------------------------- #
class CodeDigest(_env.EnvCase):
    def _module(self, text: str):
        """A module imported the stock way: no recorded closure."""
        path = _write(self.tmp(), f"inproc_{uuid.uuid4().hex}.py", text)
        name = os.path.splitext(os.path.basename(path))[0]
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        self.addCleanup(sys.modules.pop, name, None)
        spec.loader.exec_module(module)
        return path, module

    def test_defining_file_fallback_for_an_in_process_gate(self):
        path, module = self._module("def g(ctx):\n    return None\n")
        self.assertIsNone(modelio.code_closure(module.g))
        spec = _plain_spec("t.inproc")
        code = verdicts.code_digest(spec, module.g)
        self.assertEqual(code.fallback, "defining-file")
        self.assertEqual(code.opaque, "")
        self.assertRegex(code.digest, r"^[0-9a-f]{64}$")
        self.assertEqual(len(code.files), 1)
        self.assertTrue(code.files[0].endswith(os.path.basename(path)), code.files)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("# an edit\n")
        self.assertNotEqual(verdicts.code_digest(spec, module.g).digest, code.digest)

        # computed source has no file to digest: opaque, never a digest of nothing
        namespace: dict = {}
        exec(compile("def h(ctx):\n    return None\n", "<string>", "exec"), namespace)
        opaque = verdicts.code_digest(spec, namespace["h"])
        self.assertEqual(opaque.digest, "")
        self.assertIn("not loaded from a file", opaque.opaque)

    def test_spine_extras_enter_the_closure_digest(self):
        # cad and fdm import atompipe.site (packs:H4). It is not in the spine
        # digest (a page change would re-run every gate of every project), so it
        # enters the closure of exactly the gates that import it.
        root = _project(self)
        _write(root, "gates/viewer.py", _gate_source("t.viewer", '''
            return Verdict(gate="t.viewer", passed=True)
        ''', extra_head="import atompipe.site\n"))
        _write(root, "gates/plain.py", _gate_source("t.plainer", '''
            return Verdict(gate="t.plainer", passed=True)
        '''))
        registry = _registry(root)
        anchors = _anchors(root, registry)
        viewer = registry.get("t.viewer")
        plain = registry.get("t.plainer")
        self.assertIn("atompipe.site", modelio.code_closure(viewer[1]).spine_extras)
        self.assertEqual(modelio.code_closure(plain[1]).spine_extras, ())
        before = {gid: verdicts.code_digest(*registry.get(gid), anchors=anchors).digest
                  for gid in ("t.viewer", "t.plainer")}
        # every module's canonical digest moves: only the gate that imports one moves
        with mock.patch.object(verdicts, "canonical_ast_digest", return_value="0" * 64):
            after = {gid: verdicts.code_digest(*registry.get(gid), anchors=anchors).digest
                     for gid in ("t.viewer", "t.plainer")}
        self.assertNotEqual(after["t.viewer"], before["t.viewer"],
                            "a gate that imports atompipe.site did not follow a site edit")
        self.assertEqual(after["t.plainer"], before["t.plainer"])

    def test_prose_fields_do_not_move_it(self):
        path, module = self._module("def g(ctx):\n    return None\n")
        spec = _plain_spec("t.prose", tier=Tier.INSTANT, settles="tip deflection")
        base = verdicts.code_digest(spec, module.g).digest
        for change in ({"title": "a new title"}, {"description": "reworded"},
                       {"entry": "mod:fn"},
                       {"negative_control": NegativeControl(fixture="other.py:make")}):
            with self.subTest(change):
                self.assertEqual(verdicts.code_digest(
                    dataclasses.replace(spec, **change), module.g).digest, base)
        for change in ({"claims": ["t", "u"]}, {"tier": Tier.BUILD}, {"pack": "p"},
                       {"requires_tools": ["omc"]}, {"requires_python": ["numpy"]},
                       {"requires_one_of": ["python:manifold3d"]}, {"settles": "sag"}):
            with self.subTest(change):
                self.assertNotEqual(verdicts.code_digest(
                    dataclasses.replace(spec, **change), module.g).digest, base)
        self.assertEqual(verdicts.SPEC_FIELDS_IN_RHO,
                         ("id", "claims", "tier", "pack", "requires_tools",
                          "requires_python", "requires_one_of", "settles"))


# --------------------------------------------------------------------------- #
class OutDirReads(_env.EnvCase):
    def test_another_gates_output_is_opaque(self):
        root = _project(self)
        _write(root, "gates/pair.py", _GATE_HEAD + '''

@gate(id="t.writer", claims=["t"], negative_control=NegativeControl(fixture="selftest/bad.py:make"))
def writer(ctx):
    with open(ctx.out_path("shared.json"), "w", encoding="utf-8") as fh:
        fh.write("{}")
    with open(ctx.out_path("shared.json"), encoding="utf-8") as fh:
        fh.read()                      # its own output: never an input
    return Verdict(gate="t.writer", passed=True)


@gate(id="t.reader", claims=["t"], negative_control=NegativeControl(fixture="selftest/bad.py:make"))
def reader(ctx):
    with open(os.path.join(ctx.out_dir, "shared.json"), encoding="utf-8") as fh:
        fh.read()
    return Verdict(gate="t.reader", passed=True)
''')
        registry = _registry(root)
        anchors = _anchors(root, registry)
        ctx = _ctx(root, {})
        _v, wrote = _run(registry, "t.writer", ctx, anchors)
        _v, read = _run(registry, "t.reader", ctx, anchors)
        writer = verdicts.Reads.from_trace(wrote, anchors=anchors)
        reader = verdicts.Reads.from_trace(read, anchors=anchors)
        self.assertEqual(writer.opaque, [])
        self.assertEqual(writer.files, {})
        self.assertEqual(reader.opaque, ["out:shared.json (not written by this gate)"])
        self.assertEqual(reader.files, {})

    def test_only_subprocess_gates_hit_the_out_rule(self):
        """R-4: the out-dir rule measured on every bundled baseline and control
        before it binds. An ``out:`` channel on a gate that is not already opaque
        through a subprocess would make an honest gate never Fresh.

        Measured 2026-09-27 with trimesh, numpy and omc present, 108 traces
        (54 gates, baseline and control each): one hit, ``modelica.simulates``
        on its baseline, reading ``omc/ThermalTank.TankRun_res.csv`` — the result
        omc itself wrote, so the gate is already opaque through
        ``subprocess:omc``. The same sweep found the first mesh gate of the
        process listing every ``sys.path`` entry through importlib.metadata
        (``ImportNoise`` below); with that excluded, the only opaque channels on
        any bundled gate are omc's.
        """
        hits: dict[str, list[str]] = {}
        unexplained: dict[str, list[str]] = {}
        traced = 0
        for pack_dir in _pack_dirs():
            name = os.path.basename(pack_dir)
            registry = gates.Registry()
            packs.load_gates(name, registry, root=REPO, include_env=False, include_user=False)
            for listed in registry.specs():
                spec, fn = registry.get(listed.id)
                if not gates.availability(spec)[0]:
                    continue
                base = self.tmp()
                out = os.path.join(base, "out")
                controls = os.path.join(base, "controls")
                anchors = Anchors(root=pack_dir, packs={name: pack_dir}, out=out,
                                  controls_out=controls)
                for kind in ("gate", "control"):
                    trace = GateTrace(kind=kind, anchors=anchors)
                    ctx = packs.baseline_context(pack_dir, out_dir=out)
                    if kind == "gate":
                        gates.run_gate(spec, fn, ctx, trace=trace)
                    else:
                        gates.selftest(spec, fn, ctx, trace=trace, out_dir=controls)
                    traced += 1
                    opaque = verdicts.Reads.from_trace(trace, anchors=anchors).opaque
                    outs = [c for c in opaque if c.startswith("out:")]
                    if outs:
                        key = f"{spec.id} ({kind})"
                        hits[key] = outs
                        if not any(c.startswith("subprocess:") for c in opaque):
                            unexplained[key] = outs
        self.assertGreater(traced, 20, "the measurement traced almost nothing")
        self.assertEqual(unexplained, {},
                         f"out-dir reads on gates the tracer can otherwise see; all hits: {hits}")


class StatReads(_env.EnvCase):
    """What an existence, kind or size question becomes in an entry.

    ``os.stat`` raises no audit event, so until the stat probes a gate that
    decided on ``os.path.isfile`` recorded nothing: bundled
    ``modelica.source_hygiene`` kept a Fresh PASS after a ``.mo`` it had named,
    and skipped as missing, appeared (review round 1). Under the project or a
    pack a question is a file input — its bytes, ``DIRECTORY``, or missing.
    Everywhere else it must not become an opaque channel, or every gate that
    looks a tool up on PATH would never be Fresh.
    """

    def setUp(self):
        self.root = _project(self)
        self.pack = os.path.join(self.tmp(), "pack")
        os.makedirs(os.path.join(self.pack, "selftest"))
        self.out = store.out_dir(self.root)
        self.anchors = Anchors(root=self.root, packs={"p": self.pack}, out=self.out,
                               controls_out=os.path.join(self.out, "controls"))

    def at(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    def reads(self, ask, *, kind: str = "gate", static=None) -> verdicts.Reads:
        trace = GateTrace(kind=kind, anchors=self.anchors)
        with verdicts.tracing(trace):
            ask()
        return verdicts.Reads.from_trace(trace, anchors=self.anchors, static=static)

    def test_under_the_project_or_a_pack_a_question_is_a_file_input(self):
        there = _write(self.root, "data/there.txt", "1\n")
        os.makedirs(self.at("data", "sub"))
        reads = self.reads(lambda: (
            os.path.isfile(there),
            os.path.exists(self.at("data", "gone.txt")),
            os.path.isdir(self.at("data", "sub")),
            os.path.isfile(os.path.join(self.pack, "table.csv"))))
        self.assertEqual(reads.files, {
            "<pack:p>/table.csv": None,
            "data/gone.txt": None,
            "data/sub": verdicts.DIRECTORY,
            "data/there.txt": hashlib.sha256(b"1\n").hexdigest(),
        })
        self.assertEqual((reads.dirs, reads.opaque), ({}, []),
                         "a kind question is not a listing, and not opaque")
        self.assertNotIn(verdicts.DIRECTORY, (verdicts.ABSENT, verdicts.PRESENT))

    def test_a_question_about_a_path_it_also_opened_or_listed_is_keyed_once(self):
        there = _write(self.root, "data/there.txt", "1\n")

        def ask():
            os.path.isfile(there)
            with open(there, encoding="utf-8") as fh:
                fh.read()
            os.path.isdir(self.at("data"))
            os.listdir(self.at("data"))
        reads = self.reads(ask)
        self.assertEqual(list(reads.files), ["data/there.txt"])
        self.assertEqual(list(reads.dirs), ["data"])

    def test_outside_the_project_a_question_is_dropped_not_opaque(self):
        elsewhere = self.tmp()
        reads = self.reads(lambda: (
            os.path.exists(os.path.join(elsewhere, "x")),
            shutil.which("atompipe-no-such-tool"),
            os.path.realpath(self.at("data", "x.txt"))))
        self.assertEqual(reads.opaque, [])
        self.assertTrue(all(not key.startswith(("/", "~", "<tmp>")) for key in reads.files),
                        reads.files)
        self.assertEqual(reads.files.get("."), verdicts.DIRECTORY,
                         "realpath asked the root's kind: keyed as a kind, never a listing")

    def test_under_the_out_dir_only_another_gates_file_is_opaque(self):
        mine = os.path.join(self.out, "g", "mine.json")
        theirs = _write(self.out, "theirs.json", "{}")

        def ask():
            os.makedirs(os.path.dirname(mine), exist_ok=True)
            if not os.path.exists(mine):
                with open(mine, "w", encoding="utf-8") as fh:
                    fh.write("{}")
            os.path.getsize(mine)
            os.path.exists(theirs)
        reads = self.reads(ask)
        self.assertEqual(reads.files, {})
        self.assertEqual(reads.opaque, ["out:theirs.json (not written by this gate)"])

    def test_a_question_whose_answer_the_window_changed_is_self_modified(self):
        made = self.at("data", "made")
        removed = _write(self.root, "data/removed.txt", "x\n")
        written = self.at("data", "written.txt")

        def ask():
            if not os.path.isdir(made):
                os.makedirs(made)                   # asked while missing, then made
            if os.path.isfile(removed):
                os.remove(removed)                  # asked while there, then removed
            if not os.path.exists(written):
                with open(written, "w", encoding="utf-8") as fh:
                    fh.write("x")                   # asked, then written
        reads = self.reads(ask)
        self.assertEqual(reads.opaque, sorted(f"self-modified:<root>/data/{name}"
                                              for name in ("made", "removed.txt",
                                                           "written.txt")))
        self.assertEqual({k: v for k, v in reads.files.items() if k != "data"}, {})

    def test_a_controls_question_about_its_own_selftest_is_the_walks(self):
        _write(self.pack, "selftest/baseline.json", "{}")
        reads = self.reads(lambda: (
            os.path.isfile(os.path.join(self.pack, "selftest", "baseline.json")),
            os.path.isdir(os.path.join(self.pack, "selftest"))), kind="control")
        self.assertEqual((reads.files, reads.opaque), ({}, []))

    def test_a_listing_keys_each_entrys_kind_and_size(self):
        """``DirEntry.stat()`` is C and calls nothing a probe can see; the
        listing is the whole read, so it carries each entry's kind and size."""
        _write(self.root, "data/sizes/a.txt", "1\n")
        os.makedirs(self.at("data", "sizes", "sub"))
        before = verdicts._dir_digest(self.at("data", "sizes"))
        _write(self.root, "data/sizes/a.txt", "1234567890\n")
        grown = verdicts._dir_digest(self.at("data", "sizes"))
        self.assertNotEqual(before, grown, "a size decided over os.scandir moved nothing")
        os.rmdir(self.at("data", "sizes", "sub"))
        _write(self.root, "data/sizes/sub", "now a file\n")
        self.assertNotEqual(grown, verdicts._dir_digest(self.at("data", "sizes")),
                            "an entry that changed kind under the same name moved nothing")
        _write(self.root, "data/sizes/__pycache__/a.cpython-312.pyc", "bytecode")
        self.assertEqual(verdicts._dir_digest(self.at("data", "sizes")),
                         verdicts._dir_digest(self.at("data", "sizes")))


class ImportNoise(_env.EnvCase):
    def test_distribution_discovery_is_not_a_gate_read(self):
        # What the R-4 sweep above found first: numpy.testing asks
        # importlib.metadata for a distribution at import time, which lists
        # every sys.path entry. Only the first mesh gate of a process imports
        # it, so that gate alone carried `file-outside-project` channels —
        # never Fresh, and a different entry under `--only` than in a sweep.
        noise = self.tmp()
        read = self.tmp()
        sys.path.append(noise)
        self.addCleanup(lambda: sys.path.remove(noise) if noise in sys.path else None)
        trace = GateTrace()
        with verdicts.tracing(trace):
            list(importlib.metadata.distributions())
            os.listdir(read)                         # the positive control
        self.assertNotIn(noise, trace.dirs, "distribution discovery was filed as a gate read")
        self.assertIn(read, trace.dirs)


# --------------------------------------------------------------------------- #
class Obs(_env.EnvCase):
    def test_bounded_to_twenty_and_split_from_controls(self):
        # S-31: runs/ mixed `<gate>#selftest` rows with sweep rows.
        root = _project(self)
        for i in range(25):
            verdicts.record_obs(root, "t.g", entry=f"{i:016x}-00000000", when=f"t{i}",
                                duration_s=0.001 * i, cpu_s=0.0005 * i)
        for i in range(3):
            verdicts.record_obs(root, "t.g", entry=f"control-{i:016x}-00000000",
                                when=f"c{i}", duration_s=1.0, cpu_s=1.0, control=True)
        runs = verdicts.read_obs(root, "t.g")
        controls = verdicts.read_obs(root, "t.g", control=True)
        self.assertEqual(verdicts.OBS_KEEP, 20)
        self.assertEqual([r["when"] for r in runs], [f"t{i}" for i in range(5, 25)])
        self.assertEqual([r["when"] for r in controls], ["c0", "c1", "c2"])
        self.assertFalse(any(r["entry"].startswith("control-") for r in runs))
        self.assertEqual(set(runs[0]), {"entry", "when", "duration_s", "cpu_s"})
        obs = os.path.join(root, ".atompipe", "obs")
        self.assertEqual(sorted(os.listdir(obs)), ["t.g.control.json", "t.g.json"])
        self.assertEqual(verdicts.read_obs(root, "t.never"), [])


class Remembered(_env.EnvCase):
    def test_keyed_by_input_rho(self):
        # A crash reads fewer inputs than the pass it followed, so its own rho
        # is not the pass's. Keyed on that, "supersede at the same rho" would
        # never match and the next plain check would serve the old PASS.
        root = _project(self)
        crash = Verdict(gate="t.plain", error="RuntimeError: x", rho="partial" * 8)
        verdicts.remember(root, "t.plain", crash, input_rho="a" * 64, kind="error",
                          when="2026-09-27T10:00:00Z")
        [held] = verdicts.remembered(root)["t.plain"].values()
        self.assertEqual(held["input_rho"], "a" * 64)
        self.assertEqual(held["verdict"].rho, "partial" * 8)
        verdicts.remember(root, "control:t.plain", crash, input_rho="b" * 64,
                          kind="self-skip", when="")
        self.assertEqual(sorted(verdicts.remembered(root)), ["control:t.plain", "t.plain"])

        # A recorded pass or fail clears the gate's record at ITS rho, and no
        # other: not the crash at "a"*64 (remembered outcomes, round 1: it did,
        # and the PASS at "a"*64 the crash had superseded was served again), and
        # not its control's.
        wrote = verdicts.record_verdict(root, _plain_spec(), _plain_gate,
                                        Verdict(gate="t.plain", passed=True))
        [entry] = verdicts.read_entries(root, "t.plain")
        self.assertEqual(entry.path, wrote.path)
        self.assertNotEqual(entry.rho, "a" * 64)
        self.assertEqual(list(verdicts.remembered(root)["t.plain"]), ["a" * 64],
                         "a pass at another rho forgot the crash at this one")
        verdicts.remember(root, "t.plain", crash, input_rho=entry.rho, kind="error", when="")
        verdicts.record_verdict(root, _plain_spec(), _plain_gate,
                                Verdict(gate="t.plain", passed=True))
        self.assertEqual(list(verdicts.remembered(root)["t.plain"]), ["a" * 64],
                         "a pass at its own rho clears it")
        self.assertFalse(verdicts.forget(root, "t.plain", ["c" * 64]))
        self.assertTrue(verdicts.forget(root, "t.plain", ["a" * 64]))
        self.assertEqual(sorted(verdicts.remembered(root)), ["control:t.plain"])
        self.assertFalse(verdicts.forget(root, "control:t.plain", ["a" * 64]),
                         "the control's record is at its own static part")
        self.assertTrue(verdicts.forget(root, "control:t.plain", ["b" * 64]))
        self.assertFalse(verdicts.forget(root, "control:t.plain", ["b" * 64]))
        self.assertEqual(verdicts.remembered(root), {})

    def test_an_availability_skip_never_replaces_a_crash_or_a_self_skip(self):
        # Remembered outcomes, round 1: the sweep remembers a skip for a missing
        # tool under the rho a crash was remembered under, and it replaced the
        # crash — which the resolver then never counted.
        root = _project(self)
        crash = Verdict(gate="t.plain", error="RuntimeError: x")
        chose = Verdict(gate="t.plain", skipped=True, skip_reason="not my model")
        missing = Verdict(gate="t.plain", skipped=True, skip_reason="requires python nothing")
        a, b = "a" * 64, "b" * 64
        verdicts.remember(root, "t.plain", crash, input_rho=a, kind="error", when="t1")
        verdicts.remember(root, "t.plain", missing, input_rho=a, kind="availability", when="t2")
        verdicts.remember(root, "t.plain", missing, input_rho=b, kind="availability", when="t2")
        held = verdicts.remembered(root)["t.plain"]
        self.assertEqual({r: (held[r]["kind"], held[r]["when"]) for r in held},
                         {a: ("error", "t1"), b: ("availability", "t2")})
        # an availability skip supersedes nothing: the newest one per gate is kept
        c = "c" * 64
        verdicts.remember(root, "t.plain", missing, input_rho=c, kind="availability", when="t2b")
        held = verdicts.remembered(root)["t.plain"]
        self.assertEqual({r: held[r]["kind"] for r in held}, {a: "error", c: "availability"})
        verdicts.remember(root, "t.plain", missing, input_rho=b, kind="availability", when="t2c")
        # a run that proved nothing replaces one that proved nothing, newest first
        verdicts.remember(root, "t.plain", chose, input_rho=a, kind="self-skip", when="t3")
        verdicts.remember(root, "t.plain", missing, input_rho=a, kind="availability", when="t4")
        self.assertEqual(verdicts.remembered(root)["t.plain"][a]["kind"], "self-skip")
        verdicts.remember(root, "t.plain", crash, input_rho=b, kind="error", when="t5")
        self.assertEqual(verdicts.remembered(root)["t.plain"][b]["kind"], "error",
                         "a crash replaces an availability skip at the same rho")
        # the same rule for a control's records
        verdicts.remember(root, "control:t.plain", crash, input_rho=a, kind="error", when="")
        verdicts.remember(root, "control:t.plain", missing, input_rho=a,
                          kind="availability", when="")
        self.assertEqual(verdicts.remembered(root)["control:t.plain"][a]["kind"], "error")

    def test_the_one_record_per_gate_shape_is_refused(self):
        # The shape the hole was stored in: loud, never read as empty — an
        # empty read would hand the next check the PASS a crash superseded.
        root = _project(self)
        path = os.path.join(root, ".atompipe", "cache", "last_outcomes.json")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"t.plain": {"input_rho": "", "kind": "error", "when": "",
                                   "verdict": {"gate": "t.plain", "error": "x"}}}, fh)
        with self.assertRaises(AtompipeError) as caught:
            verdicts.remembered(root)
        self.assertIn("is not a remembered-outcomes file", str(caught.exception))


# --------------------------------------------------------------------------- #
class TwoOutcomes(_env.EnvCase):
    def _two(self, root: str, *, instruments=None):
        spec = _plain_spec()
        first = verdicts.record_verdict(root, spec, _plain_gate,
                                        Verdict(gate="t.plain", passed=True, measured=1.0))
        [entry] = verdicts.read_entries(root, "t.plain")
        other = dataclasses.replace(entry, verdict={**entry.verdict, "passed": False},
                                    instruments=instruments or entry.instruments)
        return first, verdicts.write_entry(root, other)

    def test_detected_as_a_warning_while_staged(self):
        # The detector, under both values of the flag. It was pinned at False
        # while staged (R-4); U25 flipped it once EntriesAreDeterministic had
        # measured the corpus, and the flag decides only how `resolve` shows two
        # outcomes (test_determinism.TwoOutcomes) — never whether the writer and
        # the reader see them. A refusal must not replace the report under it.
        for flag in (False, True):
            with self.subTest(TWO_OUTCOMES_IS_ERROR=flag), \
                    mock.patch.object(verdicts, "TWO_OUTCOMES_IS_ERROR", flag):
                root = _project(self)
                first, second = self._two(root)
                self.assertEqual(second.status, "written")
                self.assertEqual(second.rho, first.rho)
                self.assertNotEqual(second.path, first.path)
                self.assertTrue(any("two outcomes recorded for identical inputs" in w
                                    for w in second.warnings), second.warnings)
                problems: list[str] = []
                both = verdicts.read_entries(root, "t.plain", problems=problems)
                self.assertEqual(len(both), 2,
                                 "both files stay: neither outcome is silently picked")
                self.assertTrue(any("two outcomes recorded for identical inputs" in p
                                    for p in problems))

    def test_different_instruments_warns_and_prefers_local(self):
        root = _project(self)
        spec = _plain_spec()
        verdicts.record_verdict(root, spec, _plain_gate,
                                Verdict(gate="t.plain", passed=True, measured=1.0))
        [entry] = verdicts.read_entries(root, "t.plain")
        here = dataclasses.replace(entry, instruments={"numpy": "1.26.4"})
        there = dataclasses.replace(entry, instruments={"numpy": "2.1.0"},
                                    verdict={**entry.verdict, "passed": False})
        for path in [os.path.join(root, ".atompipe", "verdicts", "t.plain", n)
                     for n in _entry_files(root, "t.plain")]:
            os.unlink(path)
        verdicts.write_entry(root, here)
        wrote = verdicts.write_entry(root, there)
        self.assertTrue(any("outcome differs across instruments" in w for w in wrote.warnings),
                        wrote.warnings)
        self.assertEqual(len(_entry_files(root, "t.plain")), 2)

        problems: list[str] = []
        local = verdicts.read_entries(root, "t.plain", problems=problems,
                                      instruments={"numpy": "1.26.4"})
        self.assertEqual([e.verdict["passed"] for e in local], [True])
        self.assertTrue(any("outcome differs across instruments" in p for p in problems))
        other = verdicts.read_entries(root, "t.plain", instruments={"numpy": "2.1.0"})
        self.assertEqual([e.verdict["passed"] for e in other], [False])
        self.assertEqual(verdicts.read_entries(root, "t.plain", instruments={"numpy": "3.0"}),
                         [], "neither matches this machine: a local re-run, not a pick")
        self.assertEqual(len(verdicts.read_entries(root, "t.plain")), 2)


# --------------------------------------------------------------------------- #
class Instruments(_env.EnvCase):
    def test_derived_without_import_events(self):
        # packs:H3: from import events, only the first gate to import trimesh saw
        # it, and `--only` and a full sweep wrote different bytes for one rho.
        fake = f"apfake_{uuid.uuid4().hex[:10]}"
        site_dir = self.tmp()
        _write(site_dir, f"{fake}.py", "VALUE = 1\n")
        root = _project(self)
        _write(root, "gates/lazy.py", _gate_source("t.lazy", f'''
            if ctx.params.get("never"):
                import {fake}                 # a lazy import that never runs
            return Verdict(gate="t.lazy", passed=True)
        '''))
        registry = _registry(root)
        spec, fn = registry.get("t.lazy")
        spec = dataclasses.replace(spec, requires_python=["atompipe_no_such_module"])
        code = verdicts.code_digest(spec, fn)
        self.assertIn(fake, code.third_party)

        sys.path.insert(0, site_dir)
        self.addCleanup(lambda: sys.path.remove(site_dir) if site_dir in sys.path else None)
        importlib.invalidate_caches()
        before = verdicts.instruments_for(spec, code)
        self.assertNotIn(fake, sys.modules, "instruments_for imported the module")
        self.assertEqual(before, {fake: "unknown", "atompipe_no_such_module": "absent"})
        module = importlib.import_module(fake)
        self.addCleanup(sys.modules.pop, fake, None)
        self.assertEqual(verdicts.instruments_for(spec, code), before,
                         "instruments moved with import state")
        sys.path.remove(site_dir)
        sys.modules.pop(fake, None)
        importlib.invalidate_caches()
        self.assertEqual(verdicts.instruments_for(spec, code)[fake], "absent")
        del module

        # provenance, never part of rho: another version here is the same entry
        verdict = Verdict(gate="t.lazy", passed=True)
        wrote = verdicts.record_verdict(root, spec, fn, verdict)
        with mock.patch.object(verdicts, "instruments_for", return_value={fake: "9.9"}):
            again = verdicts.record_verdict(root, spec, fn, verdict)
        self.assertEqual(again.rho, wrote.rho)
        self.assertEqual(again.path, wrote.path)
        self.assertEqual(again.status, "exists")
        self.assertTrue(any("instruments" in w for w in again.warnings), again.warnings)


# --------------------------------------------------------------------------- #
class LastReadSets(_env.EnvCase):
    def test_the_latest_executed_entry_names_the_reads(self):
        root = _project(self)
        _write(root, "gates/reads.py", _gate_source("t.reads", '''
            key = ctx.params["which"]
            return Verdict(gate="t.reads", passed=True, measured=float(ctx.params[key]))
        '''))
        registry = _registry(root)
        anchors = _anchors(root, registry)
        spec, fn = registry.get("t.reads")
        names = []
        for which in ("a", "b"):
            verdict, trace = _run(registry, "t.reads",
                                  _ctx(root, {"which": which, "a": 1.0, "b": 2.0}), anchors)
            wrote = verdicts.record_verdict(root, spec, fn, verdict, trace=trace, anchors=anchors)
            names.append(wrote.name)
            verdicts.record_obs(root, "t.reads", entry=wrote.name, when=which,
                                duration_s=0.0, cpu_s=0.0)
        self.assertEqual(verdicts.last_read_sets(root), {"t.reads": {("which",), ("b",)}})
        verdicts.record_obs(root, "t.reads", entry=names[0], when="again",
                            duration_s=0.0, cpu_s=0.0)
        self.assertEqual(verdicts.last_read_sets(root), {"t.reads": {("which",), ("a",)}})


if __name__ == "__main__":
    unittest.main()
