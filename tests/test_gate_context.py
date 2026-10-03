# SPDX-License-Identifier: Apache-2.0
"""The gate sees a traced, read-only world (PLAN M11.5, M11.6, M13.7).

What slipped through before `run_gate` handed every gate a traced view of its own:

* ``ctx.params`` was ONE mutable dict shared by every gate in a sweep, so gate A
  could assign ``ctx.params["load_n"] = 0`` and gate B measured the forgery (S-24).
  ``ctx.ledger`` already had a defensive copy for exactly that attack; ``params``
  did not.
* ``ctx.extra`` was shared too, and fdm-print rode a cross-gate mesh cache on it:
  the second gate's read of the part was a cache hit that opened nothing, so no
  audit event said the gate depended on the file (S-27). ``ctx.load_file`` is the
  replacement — a per-sweep memo that records the file for EVERY caller, hit or
  miss.
* Gate modules, helpers and fixtures ran stale bytecode after a same-size,
  same-second edit: the source said 8.0 and the verdict came from 7.0 (S-26). The
  model entry had a fresh loader; nothing else did.
* A verdict carried no cost but wall time, so a gate that did its work in a
  solver subprocess looked free.

Every recording test checks the trace, not only the verdict: a tracer that
records nothing passes every "no exception" test there is. The R-4 measurements
(``GateIdsAreDirectoryNames.test_every_bundled_id_registers``,
``ReadOnlyBlastRadius``) run over the bundled corpus, so a refusal is known to
bite nothing honest before it lands.

Run:  PYTHONPATH=src python3 -m unittest tests.test_gate_context -v
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sys
import textwrap
import time
import unittest
from unittest import mock

from atompipe import gates as gates_mod
from atompipe import packs as packs_mod
from atompipe import verdicts
from atompipe.gates import GateContext
from atompipe.models import Claim, GateSpec, Ledger, NegativeControl, Verdict
from atompipe.util import AtompipeError
from atompipe.verdicts import ABSENT, GateInputWriteError, GateTrace, ParamTrace, digest_value

import _env

REPO = _env.REPO
PACKS_DIR = os.path.join(REPO, "packs")
BRACKET = os.path.join(REPO, "examples", "bracket")

#: The bundled corpus the R-4 measurements run over, as floors rather than exact
#: pins: 54 pack gates and the bracket's 6 when this was written. A floor keeps a
#: later pack from turning these red for being added, and keeps a loader that
#: silently loaded nothing from reporting "zero hits, all green". Rejected: exact
#: counts (every new gate would edit this file); no floor (vacuous when empty).
MIN_PACK_GATES = 54
MIN_BRACKET_GATES = 6

#: The words a write through a gate's read-only view produces.
_WRITE_REFUSED = "a gate cannot write another gate's inputs"


def _pack_dirs() -> list[str]:
    return [os.path.join(PACKS_DIR, name) for name in sorted(os.listdir(PACKS_DIR))
            if os.path.isfile(os.path.join(PACKS_DIR, name, "pack.json"))]


def _load_pack(name: str) -> gates_mod.Registry:
    """One bundled pack, alone, into a fresh registry — never a user or env copy."""
    registry = gates_mod.Registry()
    packs_mod.load_gates(name, registry, root=REPO, include_env=False, include_user=False)
    return registry


def _register(registry, gate_id, fn, **kw):
    """A gate with a control that is declared but never built (``x:y``): these tests
    run gates, not controls, unless they say otherwise."""
    kw.setdefault("negative_control", NegativeControl(fixture="x:y"))
    kw.setdefault("claims", ["C1"])
    gates_mod.gate(id=gate_id, registry=registry, **kw)(fn)
    return registry.get(gate_id)


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(text))
    return path


def _with_include(path: str) -> bytes:
    """A ``load_file`` loader for a two-file format: the bytes of the file the one
    it is handed names, found by listing their directory. Module-level, so its id
    is stable and the memo can hit."""
    folder = os.path.dirname(path)
    with open(path, encoding="utf-8") as fh:
        name = fh.read().split()[-1]
    if name not in os.listdir(folder):
        raise FileNotFoundError(os.path.join(folder, name))
    with open(os.path.join(folder, name), "rb") as fh:
        return fh.read()


def _with_optional_sidecar(path: str) -> bytes:
    """A loader that asks whether an optional sidecar exists (an ``.obj``'s
    ``.mtl``) before opening it — a question, not a read, when it is absent."""
    sidecar = os.path.splitext(path)[0] + ".mtl"
    with open(path, "rb") as fh:
        data = fh.read()
    if os.path.exists(sidecar):
        with open(sidecar, "rb") as fh:
            data += fh.read()
    return data


def _with_include_and_a_child(path: str) -> bytes:
    """``_with_include`` after running a child process: an opaque channel too.
    ``HOME`` is the project root, so ``_env.run`` makes and removes no temp home
    inside the load (its removal lists directories)."""
    folder = os.path.dirname(path)
    proc = _env.run([sys.executable, "-c", "pass"], cwd=folder,
                    home=os.path.dirname(folder))
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return _with_include(path)


# --------------------------------------------------------------------------- #
# S-24: a gate cannot forge the next gate's inputs
# --------------------------------------------------------------------------- #
class ReadOnlyParams(_env.EnvCase):
    """M13.7: one gate's write never reaches another gate's input."""

    def test_a_gate_cannot_write_another_gates_inputs(self):
        writes = {
            "assign": lambda p: p.__setitem__("load_n", 0.0),
            "nested assign": lambda p: p["config"].__setitem__("load_n", 0.0),
            "update": lambda p: p.update(load_n=0.0),
            "pop": lambda p: p.pop("load_n"),
        }
        for label, write in writes.items():
            with self.subTest(write=label):
                registry = gates_mod.Registry()
                seen: dict = {}

                def forger(ctx, write=write):
                    write(ctx.params)
                    return True

                def reader(ctx):
                    seen["top"] = ctx.params.get("load_n")
                    seen["nested"] = ctx.params["config"]["load_n"]
                    return seen["top"] > 1.0 and seen["nested"] > 1.0

                _register(registry, "g.forger", forger)
                _register(registry, "g.reader", reader)
                ctx = GateContext(root=self.tmp(), ledger=Ledger(),
                                  params={"load_n": 50.0, "config": {"load_n": 50.0}})
                forged, read = gates_mod.run_all(registry, ctx)

                self.assertEqual(forged.outcome, "error",
                                 f"the {label} went through: {forged.render()}")
                self.assertIn("GateInputWriteError", forged.error)
                self.assertIn(_WRITE_REFUSED, forged.error)
                self.assertEqual(seen, {"top": 50.0, "nested": 50.0},
                                 "the second gate read the first gate's write")
                self.assertEqual(read.outcome, "pass", read.render())
                self.assertEqual(ctx.params, {"load_n": 50.0, "config": {"load_n": 50.0}},
                                 "the sweep's own context was edited")

    def test_run_gate_without_a_trace_is_read_only_too(self):
        """The path with no sweep around it — a test, a pack's own ``__main__``, a
        fixture's nested call — gets a throwaway trace, not the raw dict."""
        registry = gates_mod.Registry()
        spec, fn = _register(registry, "g.alone",
                             lambda ctx: ctx.params.__setitem__("x", 1) or True)
        params = {"x": 0}
        v = gates_mod.run_gate(spec, fn, GateContext(params=params))
        self.assertEqual(v.outcome, "error")
        self.assertIn(_WRITE_REFUSED, v.error)
        self.assertEqual(params, {"x": 0})

    def test_the_gate_reads_are_on_the_trace_it_was_handed(self):
        registry = gates_mod.Registry()
        spec, fn = _register(registry, "g.reads",
                             lambda ctx: ctx.params["config"]["thickness"] > 1.0)
        trace = GateTrace()
        v = gates_mod.run_gate(spec, fn, GateContext(params={"config": {"thickness": 8.0}}),
                               trace=trace)
        self.assertTrue(v.ok, v.render())
        self.assertEqual(trace.params, {("config", "thickness"): digest_value(8.0)})


class PerGateExtra(_env.EnvCase):
    """S-27, the shared-channel half: ``ctx.extra`` is a per-gate copy."""

    def test_extra_writes_do_not_leak(self):
        registry = gates_mod.Registry()
        seen: dict = {}

        def writer(ctx):
            ctx.extra["_mesh_cache"] = {"part.stl": "the mesh"}
            return True

        def reader(ctx):
            seen["cache"] = ctx.extra.get("_mesh_cache")
            return True

        _register(registry, "g.writer", writer)
        _register(registry, "g.reader", reader)
        ctx = GateContext(root=self.tmp(), ledger=Ledger(), extra={"pack_dir": "/p"})
        verdicts_ = gates_mod.run_all(registry, ctx)
        self.assertTrue(all(v.ok for v in verdicts_), [v.render() for v in verdicts_])
        self.assertIsNone(seen["cache"], "the second gate read the first gate's extra — a "
                                         "cross-gate channel no trace records")
        self.assertEqual(ctx.extra, {"pack_dir": "/p"})


class MemoIsLoadFilesAlone(_env.EnvCase):
    """S-27, one field over: ``ctx.memo`` is a handle only ``load_file`` opens.

    ``traced_context`` copied ``extra`` per gate and handed every view the
    sweep's memo as the raw dict, so a gate could cache a parsed file in it
    directly and the next gate's hit opened nothing (review, ``ffr3/p2``: yield
    50 -> 10 re-ran the first gate alone, ``status`` named nothing stale, and
    ``check --force`` filed the second's FAIL where its Fresh PASS had been
    served). ``StaleIsNotCurrent`` drives that repro through the sweep; these
    hold the handle to its surface."""

    def test_a_value_one_gate_leaves_in_the_memo_never_reaches_the_next(self):
        registry = gates_mod.Registry()
        seen: dict = {}

        def writer(ctx):
            ctx.memo["t:table"] = {"yield": "50"}
            return True

        def reader(ctx):
            seen["table"] = ctx.memo.get("t:table")
            return True

        _register(registry, "g.writer", writer)
        _register(registry, "g.reader", reader)
        brought: dict = {}
        got = gates_mod.run_all(registry, GateContext(root=self.tmp(), memo=brought))
        self.assertEqual([v.outcome for v in got], ["error", "error"],
                         [v.render() for v in got])
        for v in got:
            with self.subTest(gate=v.gate):
                self.assertIn("GateMemoError", v.error)
                self.assertIn("ctx.load_file", v.error, "the refusal names the way to share")
        self.assertNotIn("table", seen, "the reader got past the refusal")
        self.assertEqual(brought, {}, "a gate wrote into the sweep's memo")

    def test_every_use_but_load_file_is_refused(self):
        """Each way a dict is read or written, on the view a gate holds and on the
        one a fixture holds — a TypeError where CPython swaps the refusal for its
        own ("not a mapping"), never an answer."""
        import operator
        entries = {("k",): "held"}
        uses = {
            "get": lambda m: m.get(("k",)),
            "getitem": lambda m: m[("k",)],
            "setitem": lambda m: operator.setitem(m, "k", 1),
            "delitem": lambda m: operator.delitem(m, ("k",)),
            "contains": lambda m: ("k",) in m,
            "len": len,
            "iter": list,
            "reversed": reversed,
            "keys": lambda m: m.keys(),
            "items": lambda m: m.items(),
            "values": lambda m: m.values(),
            "setdefault": lambda m: m.setdefault("k", 1),
            "update": lambda m: m.update({"k": 1}),
            "pop": lambda m: m.pop(("k",), None),
            "clear": lambda m: m.clear(),
            "copy": lambda m: m.copy(),
            "splat": lambda m: {**m},
            "dict": dict,
            "or": lambda m: m | {},
            "ror": lambda m: {} | m,
            "setattr": lambda m: setattr(m, "_entries", {}),
            "json": lambda m: json.dumps(m),
        }
        for readonly in (True, False):
            memo = verdicts.traced_context(GateContext(memo=entries), GateTrace(),
                                           readonly=readonly).memo
            for name, use in uses.items():
                with self.subTest(view="gate" if readonly else "fixture", use=name):
                    with self.assertRaises((verdicts.GateMemoError, TypeError)) as caught:
                        use(memo)
                    if name in ("get", "getitem", "setitem", "contains", "setdefault"):
                        self.assertIsInstance(caught.exception, verdicts.GateMemoError)
                        self.assertIn("ctx.load_file", str(caught.exception))
        self.assertEqual(entries, {("k",): "held"}, "a refused use reached the entries")

    def test_what_it_still_answers(self):
        import copy
        import pickle
        brought: dict = {}
        view = verdicts.traced_context(GateContext(memo=brought), GateTrace())
        memo = view.memo
        self.assertIsInstance(memo, verdicts.SweepMemo)
        self.assertIs(verdicts.memo_entries(memo), brought, "the entries are the caller's")
        self.assertIsNotNone(memo, "fdm-print asks `is None`")
        self.assertTrue(memo)
        self.assertFalse(hasattr(memo, "get"))
        self.assertIsNone(getattr(memo, "get", None))
        self.assertIs(copy.copy(view).memo, memo)
        self.assertIs(copy.deepcopy(view).memo, memo)
        brought["entry"] = "a parsed mesh"
        shipped = pickle.loads(pickle.dumps(memo))
        self.assertIsInstance(shipped, verdicts.SweepMemo)
        self.assertEqual(verdicts.memo_entries(shipped), {},
                         "pickling carried the sweep's entries out of the sweep")
        again = verdicts.traced_context(view, GateTrace())
        self.assertIs(again.memo, memo, "a view of a view holds the same handle")
        self.assertIsNone(verdicts.traced_context(GateContext(), GateTrace()).memo)


# --------------------------------------------------------------------------- #
# ctx.load_file: one load per sweep, recorded for every caller
# --------------------------------------------------------------------------- #
class LoadFileMemo(_env.EnvCase):
    def setUp(self):
        self.root = self.tmp()
        self.path = _write(os.path.join(self.root, "data", "part.bin"), "PART v1\n")
        self.calls: list[str] = []

    def _loader(self, path):
        self.calls.append(path)
        with open(path, "rb") as fh:
            return fh.read()

    def _sweep(self, registry, **kw):
        traces: dict = {}
        verdicts_ = gates_mod.run_all(
            registry, GateContext(root=self.root, ledger=Ledger()),
            after=lambda spec, fn, verdict, trace: traces.__setitem__(spec.id, trace), **kw)
        self.assertTrue(all(v.ok for v in verdicts_), [v.render() for v in verdicts_])
        return traces

    def test_second_gate_hit_records_the_file_for_itself(self):
        registry = gates_mod.Registry()
        for gate_id in ("g.first", "g.second"):
            _register(registry, gate_id, lambda ctx: ctx.load_file(
                "data/part.bin", loader=self._loader) == b"PART v1\n")
        traces = self._sweep(registry)
        self.assertEqual(len(self.calls), 1, "the memo did not serve the second gate")
        for gate_id in ("g.first", "g.second"):
            self.assertIn(os.path.abspath(self.path), traces[gate_id].files_read,
                          f"{gate_id}'s read of the part is on no trace: the memo hit "
                          f"opened nothing, which is S-27 again")
        self.assertIsNot(traces["g.first"], traces["g.second"], "one trace per gate")

    def test_load_file_without_memo_still_loads(self):
        """A hand-run check script calls a gate on a plain context (packs:H15)."""
        ctx = GateContext(root=self.root)
        self.assertIsNone(ctx.memo)
        self.assertEqual(ctx.load_file("data/part.bin"), b"PART v1\n")
        self.assertEqual(ctx.load_file(self.path, loader=self._loader), b"PART v1\n")
        self.assertEqual(ctx.load_file("data/part.bin", loader=self._loader), b"PART v1\n")
        self.assertEqual(len(self.calls), 2, "no memo means every call loads")

    def test_the_memo_is_per_sweep_and_per_loader(self):
        registry = gates_mod.Registry()
        _register(registry, "g.bytes", lambda ctx: ctx.load_file("data/part.bin") == b"PART v1\n")
        _register(registry, "g.loader", lambda ctx: ctx.load_file(
            "data/part.bin", loader=self._loader) == b"PART v1\n")
        self._sweep(registry)
        self._sweep(registry)
        self.assertEqual(len(self.calls), 2, "a memo outlived its sweep, or two loaders "
                                             "shared one entry")

    def test_a_file_rewritten_mid_sweep_is_loaded_again(self):
        """The memo is keyed on the path, and the bytes under a path can move between
        two gates of one sweep. A hit that served the old bytes would put the new
        bytes' digest on an entry whose verdict came from the old ones."""
        registry = gates_mod.Registry()
        _register(registry, "g.first", lambda ctx: ctx.load_file(
            "data/part.bin", loader=self._loader) == b"PART v1\n")

        def rewrite(ctx):
            with open(self.path, "w", encoding="utf-8") as fh:
                fh.write("PART v2, longer\n")
            return True

        _register(registry, "g.rewrite", rewrite)
        _register(registry, "g.second", lambda ctx: ctx.load_file(
            "data/part.bin", loader=self._loader) == b"PART v2, longer\n")
        self._sweep(registry)
        self.assertEqual(len(self.calls), 2)

    # -- a loader that opens more than the file it was handed ---------------- #
    def _plant_include(self) -> str:
        """``data/scene.txt`` names ``buf.bin`` beside it, as a .gltf names its
        .bin buffers and an .obj its .mtl. Returns the include's path."""
        _write(os.path.join(self.root, "data", "scene.txt"), "buffer buf.bin\n")
        return _write(os.path.join(self.root, "data", "buf.bin"), "BUF v1\n")

    def test_a_hit_records_every_file_the_loader_opened(self):
        """V: the miss ran the loader inside the first gate's window, so its trace
        got the scene and the buffer; the hit reported the scene alone, and the
        second gate's verdict was keyed as if the buffer were no input of it —
        bundled fdm.bridge_span on a .gltf (review round 1)."""
        buffer = os.path.abspath(self._plant_include())
        registry = gates_mod.Registry()
        for gate_id in ("g.first", "g.second"):
            _register(registry, gate_id, lambda ctx: ctx.load_file(
                "data/scene.txt", loader=_with_include) == b"BUF v1\n")
        loads = []
        real = gates_mod._load
        with mock.patch.object(gates_mod, "_load", side_effect=lambda path, loader: (
                loads.append(path), real(path, loader))[1]):
            traces = self._sweep(registry)
        self.assertEqual(len(loads), 1, "the second gate was not served by the memo — "
                                        "without a hit this tests nothing")
        for gate_id in ("g.first", "g.second"):
            with self.subTest(gate=gate_id):
                self.assertIn(buffer, traces[gate_id].files_read,
                              f"{gate_id}: a file its loader opened is on no trace")
                self.assertIn(os.path.abspath(os.path.join(self.root, "data")),
                              traces[gate_id].dirs, "the loader's listing was dropped")

    def test_a_hit_replays_to_the_view_and_every_trace_open_around_it(self):
        """A hit is recorded where a miss would have been: on the view's own trace
        with no window open (a test calling a gate's view directly), and on every
        window open around it (a control's, around a fixture's nested gate) —
        files, listings and the opaque channels alike."""
        buffer = os.path.abspath(self._plant_include())
        memo: dict = {}
        first, second, outer = GateTrace(), GateTrace(), GateTrace()
        GateContext(root=self.root, memo=memo, trace=first).load_file(
            "data/scene.txt", loader=_with_include_and_a_child)
        with verdicts.tracing(outer):
            got = GateContext(root=self.root, memo=memo, trace=second).load_file(
                "data/scene.txt", loader=_with_include_and_a_child)
        self.assertEqual(got, b"BUF v1\n")
        self.assertEqual(len(memo), 1, "one entry: the second call was a hit")
        for name, trace in (("first (a miss, no window)", first),
                            ("second (a hit)", second), ("outer (around the hit)", outer)):
            with self.subTest(trace=name):
                self.assertIn(buffer, trace.files_read)
                self.assertIn(os.path.abspath(os.path.join(self.root, "data")), trace.dirs)
                self.assertTrue(any(c.startswith("subprocess:") for c in trace.opaque),
                                f"the child the loader ran is not opaque here: "
                                f"{sorted(trace.opaque)}")

    def test_a_hit_replays_the_loaders_existence_questions(self):
        """V: a loader that asks ``os.path.exists`` of an optional sidecar and
        finds none. The miss's trace holds the question; a hit must hand it to
        its gate too, or the second gate's PASS outlives the sidecar appearing —
        the fdm.bridge_span shape again, through a stat instead of an open."""
        sidecar = os.path.abspath(os.path.join(self.root, "data", "part.mtl"))
        registry = gates_mod.Registry()
        for gate_id in ("g.first", "g.second"):
            _register(registry, gate_id, lambda ctx: ctx.load_file(
                "data/part.bin", loader=_with_optional_sidecar) == b"PART v1\n")
        loads = []
        real = gates_mod._load
        with mock.patch.object(gates_mod, "_load", side_effect=lambda path, loader: (
                loads.append(path), real(path, loader))[1]):
            traces = self._sweep(registry)
        self.assertEqual(len(loads), 1, "the second gate was not served by the memo")
        for gate_id in ("g.first", "g.second"):
            with self.subTest(gate=gate_id):
                self.assertIn(sidecar, traces[gate_id].stats,
                              f"{gate_id}: the sidecar the loader asked about is on no trace")
                self.assertIs(traces[gate_id].stat_existed(sidecar), False)

    def test_a_sidecar_that_appears_mid_sweep_is_loaded_again(self):
        """The memo signs what a loader asked about as it signs what it read: a
        sidecar missing at the first gate and present at the second is a miss."""
        registry = gates_mod.Registry()
        _register(registry, "g.first", lambda ctx: ctx.load_file(
            "data/part.bin", loader=_with_optional_sidecar) == b"PART v1\n")

        def plant(ctx):
            _write(os.path.join(self.root, "data", "part.mtl"), "MTL\n")
            return True

        _register(registry, "g.plant", plant)
        _register(registry, "g.second", lambda ctx: ctx.load_file(
            "data/part.bin", loader=_with_optional_sidecar) == b"PART v1\nMTL\n")
        self._sweep(registry)

    def test_an_include_rewritten_mid_sweep_is_loaded_again(self):
        """The memo's stat signature covers every file the loader read, not only the
        one it was handed: a buffer rewritten between two gates of one sweep, under
        an unchanged scene, is loaded again."""
        buffer = self._plant_include()
        registry = gates_mod.Registry()
        _register(registry, "g.first", lambda ctx: ctx.load_file(
            "data/scene.txt", loader=_with_include) == b"BUF v1\n")

        def rewrite(ctx):
            with open(buffer, "w", encoding="utf-8") as fh:
                fh.write("BUF v2, longer\n")
            return True

        _register(registry, "g.rewrite", rewrite)
        _register(registry, "g.second", lambda ctx: ctx.load_file(
            "data/scene.txt", loader=_with_include) == b"BUF v2, longer\n")
        self._sweep(registry)


# --------------------------------------------------------------------------- #
# the ledger and the cost
# --------------------------------------------------------------------------- #
class LedgerIsTracedCopy(unittest.TestCase):
    def test_gate_sees_claims_not_verdicts(self):
        ledger = Ledger(claims=[Claim(id="C1", statement="deflection under 0.5 mm")],
                        verdicts=[Verdict(gate="g.other", passed=True)])
        seen: dict = {}

        def peek(ctx):
            seen["claim"] = ctx.ledger.claim("C1")
            seen["verdicts"] = list(ctx.ledger.verdicts)
            ctx.ledger.claims.append(Claim(id="C9", statement="forged"))
            return True

        registry = gates_mod.Registry()
        spec, fn = _register(registry, "g.peek", peek)
        trace = GateTrace()
        v = gates_mod.run_gate(spec, fn, GateContext(ledger=ledger), trace=trace)
        self.assertTrue(v.ok, v.render())
        self.assertEqual(seen["claim"].id, "C1")
        self.assertEqual(seen["verdicts"], [], "a gate read other gates' verdicts: verdicts "
                                               "inside rho, a staleness that feeds itself")
        self.assertIn("claim:C1", trace.ledger)
        self.assertEqual([c.id for c in ledger.claims], ["C1"], "the gate edited the ledger")
        self.assertEqual(len(ledger.verdicts), 1)


class CostIsMeasured(_env.EnvCase):
    def test_duration_and_cpu_are_set(self):
        """``cpu_s`` counts the gate's children: omc does its work in a subprocess, and
        a cost that left it out would call the most expensive gate free."""
        work = self.tmp()
        child = ("import time\nt = time.process_time()\n"
                 "while time.process_time() - t < 0.3:\n    pass\n")

        def burner(ctx):
            started = time.process_time()
            while time.process_time() - started < 0.05:
                pass
            proc = _env.run([sys.executable, "-c", child], cwd=work)
            return proc.returncode == 0

        registry = gates_mod.Registry()
        spec, fn = _register(registry, "g.burner", burner)
        v = gates_mod.run_gate(spec, fn, GateContext(root=work))
        self.assertTrue(v.ok, v.render())
        self.assertGreater(v.duration_s, 0.3)
        self.assertGreaterEqual(v.cpu_s, 0.3, "the child's CPU time is not in cpu_s")

    def test_a_gate_cannot_declare_its_own_rho_or_cost(self):
        registry = gates_mod.Registry()
        spec, fn = _register(registry, "g.liar", lambda ctx: Verdict(
            gate="g.liar", passed=True, rho="f" * 64, cpu_s=99.0, duration_s=99.0))
        v = gates_mod.run_gate(spec, fn, GateContext())
        self.assertTrue(v.ok, v.render())
        self.assertEqual(v.rho, "", "rho is the sweep's to compute, never the gate's to say")
        self.assertLess(v.cpu_s, 99.0)
        self.assertLess(v.duration_s, 99.0)

    def test_a_skip_costs_nothing_and_the_new_fields_are_last(self):
        registry = gates_mod.Registry()
        spec, fn = _register(registry, "g.absent", lambda ctx: True,
                             requires_tools=["atompipe-no-such-tool-5d2e"])
        v = gates_mod.run_gate(spec, fn, GateContext())
        self.assertTrue(v.skipped)
        self.assertEqual((v.duration_s, v.cpu_s, v.rho), (0.0, 0.0, ""))
        names = [f.name for f in dataclasses.fields(Verdict)]
        # P2.1 put `unqualified` after them, on the end too, and P2.2
        # `blocked_by` and `blocked_kind` after that (R-6: the window widened,
        # the order it pins unchanged).
        self.assertEqual(names[-5:], ["rho", "cpu_s", "unqualified", "blocked_by",
                                      "blocked_kind"],
                         "R-2: new fields go on the end")
        self.assertEqual(v.unqualified, "", "a skip carries no refusal mark")
        self.assertEqual((v.blocked_by, v.blocked_kind), ([], ""),
                         "a missing tool is not a prerequisite's mark")
        self.assertEqual([f.name for f in dataclasses.fields(GateContext)][-2:],
                         ["memo", "trace"])


# --------------------------------------------------------------------------- #
# a gate id names a directory in the verdict cache
# --------------------------------------------------------------------------- #
def _id_problems(ids) -> list[str]:
    """The ids that cannot name a verdict-cache directory: a path character, or a
    second spelling of an id that differs only in case (one directory on a
    case-insensitive filesystem). Pure, so the corpus measurement does not depend
    on the refusal it justifies."""
    problems: list[str] = []
    folded: dict[str, str] = {}
    for gate_id in ids:
        bad = [c for c in ("/", "\\", "..", ":") if c in gate_id]
        if bad:
            problems.append(f"{gate_id!r} contains {', '.join(map(repr, bad))}")
        other = folded.setdefault(gate_id.casefold(), gate_id)
        if other != gate_id:
            problems.append(f"{gate_id!r} and {other!r} differ only in case")
    return problems


class GateIdsAreDirectoryNames(_env.EnvCase):
    def test_every_bundled_id_registers(self):
        """R-4, measured before the refusal lands: zero hits over the corpus."""
        registry = gates_mod.Registry()
        pack_ids: list[str] = []
        for pack_dir in _pack_dirs():
            loaded = _load_pack(os.path.basename(pack_dir))
            for spec, fn in loaded.pairs():
                pack_ids.append(spec.id)
                registry.register(spec, fn)
        # The bracket's gates through the CLI in a copy, so this measurement does
        # not depend on which module owns the project-gate loader.
        project = os.path.join(self.tmp(), "bracket")
        shutil.copytree(BRACKET, project, ignore=shutil.ignore_patterns("__pycache__", "out"))
        proc = _env.atompipe(["gate", "list", "--json"], cwd=project)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = json.loads(proc.stdout)["gates"]
        bracket_ids = [row["id"] for row in rows if not row.get("pack")]
        for row in rows:
            if not row.get("pack"):
                registry.register(GateSpec(id=row["id"], negative_control=NegativeControl(
                    fixture="selftest/bad_configs.py")), lambda ctx: True)

        self.assertGreaterEqual(len(pack_ids), MIN_PACK_GATES)
        self.assertGreaterEqual(len(bracket_ids), MIN_BRACKET_GATES)
        self.assertEqual(_id_problems(pack_ids + bracket_ids), [])
        self.assertEqual(len(registry), len(pack_ids) + len(bracket_ids))

    def test_the_measurement_can_fail(self):
        """The oracle's own negative control: each planted id is named."""
        self.assertEqual(len(_id_problems(["a/b", "a\\b", "a..b", "a:b"])), 4)
        self.assertEqual(len(_id_problems(["fdm.overhang", "FDM.Overhang"])), 1)
        self.assertEqual(_id_problems(["fdm.overhang", "fdm.bed_fit", "a.b.c"]), [])

    def test_path_characters_refused(self):
        for bad in ("fdm/overhang", "fdm\\overhang", "fdm..overhang", "fdm:overhang"):
            with self.subTest(gate_id=bad):
                with self.assertRaises(AtompipeError) as cm:
                    gates_mod.Registry().register(
                        GateSpec(id=bad, negative_control=NegativeControl(fixture="x:y")),
                        lambda ctx: True)
                self.assertIn("names a directory in the verdict cache", str(cm.exception))
                with self.assertRaises(AtompipeError):
                    gates_mod.gate(id=bad, registry=gates_mod.Registry(),
                                   negative_control=NegativeControl(fixture="x:y"))(
                        lambda ctx: True)
        registry = gates_mod.Registry()
        _register(registry, "fdm.over.hang", lambda ctx: True)
        self.assertIn("fdm.over.hang", registry)

    def test_case_only_duplicates_refused(self):
        registry = gates_mod.Registry()
        spec, fn = _register(registry, "fdm.overhang", lambda ctx: True)
        registry.register(spec, fn)                          # the same gate again: a no-op
        for other in ("fdm.Overhang", "FDM.OVERHANG"):
            for replace in (False, True):
                with self.subTest(gate_id=other, replace=replace):
                    with self.assertRaises(AtompipeError) as cm:
                        registry.register(dataclasses.replace(spec, id=other),
                                          lambda ctx: True, replace=replace)
                    self.assertIn("only in case", str(cm.exception))
                    self.assertIn("fdm.overhang", str(cm.exception))
        self.assertEqual(registry.ids(), ["fdm.overhang"])


# --------------------------------------------------------------------------- #
# controls: the fixture on a writable host copy, traced; the fixture alone
# --------------------------------------------------------------------------- #
_SELFTEST_GATE_LIMIT = 10.0

_TRACED_FIXTURE = """\
import dataclasses, json, os

def make(ctx):
    with open(os.path.join(ctx.root, "data", "limits.json"), encoding="utf-8") as fh:
        bad = json.load(fh)["bad_span_mm"]
    ctx.params.get("span_mm")                  # a read of the HOST's projection
    return dataclasses.replace(ctx, params={"span_mm": bad})
"""

#: ``_TRACED_FIXTURE`` with its file read behind a module-level ``lru_cache``.
_MEMO_FIXTURE = """\
import dataclasses, functools, json, os

@functools.lru_cache(maxsize=None)
def _bad_span(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["bad_span_mm"]

def make(ctx):
    return dataclasses.replace(
        ctx, params={"span_mm": _bad_span(os.path.join(ctx.root, "data", "limits.json"))})
"""

#: A known-good design built from a file read behind a module-level ``cache``.
_MEMO_KNOWN_GOOD = """\
import dataclasses, functools, json, os

@functools.cache
def _span(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["good_span_mm"]

def context(ctx):
    return dataclasses.replace(
        ctx, params={"span_mm": _span(os.path.join(ctx.root, "data", "good.json"))})
"""

_MUTATING_FIXTURE = """\
def make(ctx):
    ctx.params["span_mm"] = 99.0
    ctx.params["config"]["x"] = 1
    ctx.extra["planted"] = True
    return ctx
"""

_DICT_FIXTURE = """\
def make(ctx):
    return {"bad": True, "seen_out": ctx.out_dir}
"""


class SelftestTraces(_env.EnvCase):
    def setUp(self):
        self.root = self.tmp()
        _write(os.path.join(self.root, "data", "limits.json"), '{"bad_span_mm": 42.0}\n')
        self.calls: list = []

    def _gate(self, fixture_body: str, name: str = "bad.py"):
        _write(os.path.join(self.root, "selftest", name), fixture_body)

        def span_gate(ctx):
            self.calls.append(ctx)
            span = ctx.params.get("span_mm", 0.0)
            return (span <= _SELFTEST_GATE_LIMIT, f"span {span} mm")

        registry = gates_mod.Registry()
        return _register(registry, "g.span", span_gate,
                         negative_control=NegativeControl(fixture=f"selftest/{name}"))

    def _host(self, **params):
        return GateContext(root=self.root, ledger=Ledger(), params=params,
                           out_dir=os.path.join(self.root, "out"))

    def test_control_trace_sees_fixture_opens_and_gate_reads(self):
        spec, fn = self._gate(_TRACED_FIXTURE)
        trace = GateTrace(kind="control")
        v = gates_mod.selftest(spec, fn, self._host(span_mm=5.0), trace=trace)
        self.assertTrue(v.ok, v.detail or v.error)
        limits = os.path.join(self.root, "data", "limits.json")
        self.assertIn(limits, trace.files_read, "the fixture's open is on no trace")
        self.assertEqual(trace.params.get(("span_mm",)), digest_value(42.0),
                         "the gate's read of the control context is not on the trace")
        self.assertEqual(trace.host_reads.get(("span_mm",)), digest_value(5.0),
                         "the fixture's read of the host is not a host read")
        closure = trace.fixture_code
        self.assertIsNotNone(closure, "the fixture's code closure was not recorded")
        self.assertIn(os.path.join(self.root, "selftest", "bad.py"),
                      [path for path, _sha in closure.files])

    def test_fixture_mutation_does_not_reach_the_sweep(self):
        spec, fn = self._gate(_MUTATING_FIXTURE)
        host = self._host(span_mm=5.0, config={"x": 0})
        v = gates_mod.selftest(spec, fn, host)
        self.assertTrue(v.ok, v.detail or v.error)
        self.assertEqual(host.params, {"span_mm": 5.0, "config": {"x": 0}},
                         "the fixture edited the context every later gate reads")
        self.assertEqual(host.extra, {})
        registry = gates_mod.Registry()
        seen: dict = {}
        _register(registry, "g.after",
                  lambda ctx: seen.setdefault("span", ctx.params["span_mm"]) and True)
        gates_mod.run_all(registry, host)
        self.assertEqual(seen["span"], 5.0)

    def test_the_fixture_gets_the_control_out_dir(self):
        spec, fn = self._gate(_DICT_FIXTURE)
        seen: dict = {}

        def gate(ctx):
            seen.update(ctx.extra)
            return not ctx.extra.get("bad")

        control_out = os.path.join(self.root, "controls", "g.span")
        v = gates_mod.selftest(spec, gate, self._host(), out_dir=control_out)
        self.assertTrue(v.ok, v.detail or v.error)
        self.assertEqual(seen["seen_out"], control_out,
                         "the fixture wrote into the host out_dir, where a cached PASS "
                         "keeps its evidence (packs:H5)")

    def test_a_fixture_memo_is_emptied_before_every_control_run(self):
        """V: a control runs its fixture more than once in one process —
        re-verification runs it alone, and a miss then runs it again with the
        gate — so a fixture that reads its known-bad input through a module-level
        ``lru_cache`` opened the file on the first run only, and the control
        entry the second run filed keyed nothing it was built from (the gate-side
        hole, ``probe.lru``, on the fixture's side). Every run, gate or fixture,
        reads its files itself."""
        spec, fn = self._gate(_MEMO_FIXTURE, name="memo_bad.py")
        limits = os.path.join(self.root, "data", "limits.json")
        control_out = os.path.join(self.root, "controls", "g.span")
        first = GateTrace(kind="control")
        gates_mod.run_fixture(spec, fn, self._host(span_mm=5.0), trace=first,
                              out_dir=control_out)
        self.assertIn(limits, first.files_read, "the positive control: a miss opens it")
        for run in range(2):
            with self.subTest(run=run):
                trace = GateTrace(kind="control")
                v = gates_mod.selftest(spec, fn, self._host(span_mm=5.0), trace=trace)
                self.assertTrue(v.ok, v.detail or v.error)
                self.assertIn(limits, trace.files_read,
                              "a warm fixture memo hid its known-bad input from the "
                              "control's trace")
        # The test's own negative control: the memo left warm hides the file.
        with mock.patch.object(gates_mod.modelio, "clear_caches", lambda obj: ()):
            warm = GateTrace(kind="control")
            gates_mod.run_fixture(spec, fn, self._host(span_mm=5.0), trace=warm,
                                  out_dir=control_out)
        self.assertNotIn(limits, warm.files_read)

    def test_a_known_good_memo_is_emptied_before_every_control_run(self):
        """V: the same hole in ``selftest/known_good.py``, whose ``context`` runs
        inside each project control's trace window: a memo it filled for one
        control opened nothing for the next."""
        _write(os.path.join(self.root, "data", "good.json"), '{"good_span_mm": 5.0}\n')
        _write(os.path.join(self.root, "selftest", "known_good.py"), _MEMO_KNOWN_GOOD)
        good = os.path.join(self.root, "data", "good.json")
        for run in range(2):
            with self.subTest(run=run):
                trace = GateTrace(kind="control")
                built, _closure = verdicts._known_good(self.root, self._host(), trace)
                self.assertEqual(built.params, {"span_mm": 5.0})
                self.assertIn(good, trace.files_read,
                              "a warm known-good memo hid its file from this control")
        with mock.patch.object(verdicts.modelio, "clear_caches", lambda obj: ()):
            warm = GateTrace(kind="control")
            verdicts._known_good(self.root, self._host(), warm)
        self.assertNotIn(good, warm.files_read, "the test's own negative control")

    def test_run_fixture_does_not_call_the_gate(self):
        spec, fn = self._gate(_TRACED_FIXTURE)
        trace = GateTrace(kind="control")
        control_out = os.path.join(self.root, "controls", "g.span")
        built = gates_mod.run_fixture(spec, fn, self._host(span_mm=5.0), trace=trace,
                                      out_dir=control_out)
        self.assertEqual(self.calls, [], "run_fixture ran the gate")
        self.assertIsInstance(built, GateContext)
        self.assertEqual(built.params["span_mm"], 42.0)
        self.assertEqual(built.out_dir, control_out)
        self.assertIn(os.path.join(self.root, "data", "limits.json"), trace.files_read)
        self.assertEqual(trace.host_reads.get(("span_mm",)), digest_value(5.0))
        self.assertIsNotNone(trace.fixture_code)

        spec, fn = self._gate(_DICT_FIXTURE, name="dict_bad.py")
        built = gates_mod.run_fixture(spec, fn, self._host(), trace=GateTrace(kind="control"),
                                      out_dir=control_out)
        self.assertEqual(built.extra.get("bad"), True)
        self.assertEqual(self.calls, [])

        spec, fn = self._gate("def make(ctx):\n    return None\n", name="none_bad.py")
        with self.assertRaises(AtompipeError) as cm:
            gates_mod.run_fixture(spec, fn, self._host(), trace=GateTrace(kind="control"),
                                  out_dir=control_out)
        self.assertIn("returned None", str(cm.exception))


# --------------------------------------------------------------------------- #
# S-26: the bytes that run are the bytes on disk
# --------------------------------------------------------------------------- #
_SPAN_GATE = """\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="proj.span", claims=["C1"],
      negative_control=NegativeControl(fixture="selftest/bad.py"))
def span(ctx):
    return Verdict(gate="proj.span", passed=True, measured=7.0)
"""

_EDIT_AND_RELOAD = """\
import json, os, py_compile, sys
from atompipe import gates

root = sys.argv[1]
path = os.path.join(root, "gates", "span.py")
py_compile.compile(path)          # a warm __pycache__ that validates by mtime and size


def measured():
    registry = gates.Registry()   # a fresh registry per load, as every command gets
    ids = gates.load_project_gates(root, registry)
    spec, fn = registry.get("proj.span")
    return ids, gates.run_gate(spec, fn, gates.GateContext(root=root)).measured


first = measured()
stat = os.stat(path)
with open(path, encoding="utf-8") as fh:
    source = fh.read()
with open(path, "w", encoding="utf-8") as fh:
    fh.write(source.replace("7.0", "8.0"))           # same size
os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))   # same second
print(json.dumps([first, measured()]))
"""


_EDIT_PACK_AND_RELOAD = """\
import json, os, py_compile, sys
from atompipe import gates, packs

pack_dir = sys.argv[1]
path = os.path.join(pack_dir, "gates", "span.py")
py_compile.compile(path)


def measured():
    registry = gates.Registry()
    packs.load_gates("scratchspan", registry, root=None, include_user=False)
    spec, fn = registry.get("proj.span")
    return registry.pack_dirs, gates.run_gate(spec, fn, gates.GateContext()).measured


first = measured()
stat = os.stat(path)
with open(path, encoding="utf-8") as fh:
    source = fh.read()
with open(path, "w", encoding="utf-8") as fh:
    fh.write(source.replace("7.0", "8.0"))
os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
print(json.dumps([first, measured()]))
"""


class GateCodeIsFresh(_env.EnvCase):
    def test_same_size_same_second_gate_edit_runs_new_code(self):
        """M11.5: a same-size edit with its mtime restored — exactly what a pyc
        validates by — runs the new bytes, through a fresh registry, in one process."""
        root = self.tmp()
        _write(os.path.join(root, "gates", "span.py"), _SPAN_GATE)
        proc = _env.run([sys.executable, "-c", _EDIT_AND_RELOAD, root], cwd=root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        (ids_1, first), (ids_2, second) = json.loads(proc.stdout)
        self.assertEqual(first, 7.0)
        self.assertEqual(second, 8.0, "the edited gate ran its old bytecode (S-26)")
        self.assertEqual(ids_1, ["proj.span"])
        self.assertEqual(ids_2, ["proj.span"], "the fresh registry came back empty")

    def test_same_size_same_second_pack_gate_edit_runs_new_code(self):
        """The same, through ``packs.load_gates`` — which also says where the pack
        was loaded from, on the registry that holds its gates."""
        packs_root = self.tmp()
        pack_dir = os.path.join(packs_root, "scratchspan")
        _write(os.path.join(pack_dir, "pack.json"), '{"name": "scratchspan"}\n')
        _write(os.path.join(pack_dir, "gates", "span.py"), _SPAN_GATE)
        proc = _env.run([sys.executable, "-c", _EDIT_PACK_AND_RELOAD, pack_dir], cwd=packs_root,
                        env={packs_mod.PACK_PATH_ENV: packs_root})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        (dirs_1, first), (dirs_2, second) = json.loads(proc.stdout)
        self.assertEqual((first, second), (7.0, 8.0), "the edited pack gate ran its old bytecode")
        for dirs in (dirs_1, dirs_2):
            self.assertEqual({k: os.path.realpath(v) for k, v in dirs.items()},
                             {"scratchspan": os.path.realpath(pack_dir)})

    def test_same_size_same_second_fixture_edit_runs_new_code(self):
        """A fixture was cached by its path forever: an in-process edit — a test that
        defuses a control to prove admission notices — re-ran the old fixture."""
        root = self.tmp()
        fixture = _write(os.path.join(root, "selftest", "bad.py"),
                         "def make(ctx):\n    return {'span_mm': 42.0}\n")
        registry = gates_mod.Registry()
        spec, fn = _register(registry, "g.span",
                             lambda ctx: ctx.extra.get("span_mm", 0.0) <= 10.0,
                             negative_control=NegativeControl(fixture="selftest/bad.py"))
        ctx = GateContext(root=root, ledger=Ledger())
        before = gates_mod.selftest(spec, fn, ctx)
        self.assertTrue(before.ok, before.detail or before.error)
        stat = os.stat(fixture)
        with open(fixture, "w", encoding="utf-8") as fh:
            fh.write("def make(ctx):\n    return {'span_mm': 05.0}\n")     # same size
        os.utime(fixture, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(os.stat(fixture).st_size, stat.st_size)
        after = gates_mod.selftest(spec, fn, ctx)
        self.assertFalse(after.ok, "the defused fixture's old bytes ran")
        self.assertIn("PASSED its own known-bad", after.detail)


# --------------------------------------------------------------------------- #
# a fixture that runs a gate (fluids-analytic, packs:H14)
# --------------------------------------------------------------------------- #
class NestedRunGate(_env.EnvCase):
    def test_flow_regime_control_still_fires_and_outer_trace_sees_inner_reads(self):
        """``transitional_line`` runs ``fluid.flow_regime`` itself, on an
        external-only projection, before it builds the known-bad input. The nested
        run must stay read-only, its FILE reads must reach the control's trace (the
        audit stack routes to every open window), and its PARAM reads must not: a
        read of the external-only projection is not a read of the control's."""
        registry = _load_pack("fluids-analytic")
        spec, fn = registry.get("fluid.flow_regime")
        pack_dir = os.path.join(PACKS_DIR, "fluids-analytic")
        with open(os.path.join(pack_dir, "selftest", "baseline.json"), encoding="utf-8") as fh:
            baseline = json.load(fh)
        sentinel = _write(os.path.join(self.tmp(), "sentinel.txt"), "read me\n")
        real = gates_mod.run_gate
        nested: list = []

        def spy(spec_, fn_, ctx_, *, trace=None):
            if trace is not None:
                return real(spec_, fn_, ctx_, trace=trace)
            mine = GateTrace()

            def reads_a_file_too(view):
                with open(sentinel, encoding="utf-8") as handle:
                    handle.read()
                nested.append((mine, view))
                return fn_(view)

            return real(spec_, reads_a_file_too, ctx_, trace=mine)

        outer = GateTrace(kind="control")
        ctx = packs_mod.baseline_context(pack_dir, out_dir=self.tmp())
        with mock.patch.object(gates_mod, "run_gate", spy):
            v = gates_mod.selftest(spec, fn, ctx, trace=outer)

        self.assertTrue(v.ok, v.detail or v.error)
        self.assertIn("correctly failed", v.detail)
        self.assertEqual(len(nested), 1, "the fixture's regression run did not happen")
        inner, view = nested[0]
        self.assertIsInstance(view.params, ParamTrace)
        with self.assertRaises(GateInputWriteError):
            view.params["pipe_diameter_m"] = 1.0
        self.assertIn(sentinel, inner.files_read)
        self.assertIn(sentinel, outer.files_read, "a nested gate's file read is on no "
                                                  "enclosing trace")
        self.assertEqual(inner.params.get(("pipe_diameter_m",)), ABSENT)
        self.assertEqual(outer.params.get(("pipe_diameter_m",)),
                         digest_value(baseline["pipe_diameter_m"]),
                         "the nested run's reads of another projection landed on the "
                         "control's trace")


# --------------------------------------------------------------------------- #
# R-4: read-only params, measured on the bundled corpus
# --------------------------------------------------------------------------- #
class ReadOnlyBlastRadius(_env.EnvCase):
    def test_every_bundled_baseline_and_control_runs_read_only(self):
        hits: list[str] = []
        counts = {"gates": 0, "baseline ran": 0, "control ran": 0, "skipped": 0}
        for pack_dir in _pack_dirs():
            registry = _load_pack(os.path.basename(pack_dir))
            for spec, fn in registry.pairs():
                counts["gates"] += 1
                out = os.path.join(self.tmp(), spec.id)
                base = gates_mod.run_gate(
                    spec, fn, packs_mod.baseline_context(pack_dir, out_dir=os.path.join(out, "b")))
                control = gates_mod.selftest(
                    spec, fn, packs_mod.baseline_context(pack_dir, out_dir=os.path.join(out, "c")))
                for kind, verdict in (("baseline", base), ("control", control)):
                    said = f"{verdict.error} {verdict.detail}"
                    if "GateInputWriteError" in said or _WRITE_REFUSED in said:
                        hits.append(f"{spec.id} {kind}: {verdict.error or verdict.detail}")
                    if verdict.skipped:
                        counts["skipped"] += 1
                    else:
                        counts[f"{kind} ran"] += 1
        self.assertGreaterEqual(counts["gates"], MIN_PACK_GATES, counts)
        self.assertEqual(hits, [], f"a bundled gate or fixture writes a gate's params: {counts}")

    def test_every_bundled_baseline_and_control_leaves_the_memo_to_load_file(self):
        """R-4 for ``GateMemoError``: on the contexts a sweep hands them — one
        ``SweepMemo`` shared by a pack's baselines as ``run_all`` shares it, a
        fresh one per control as ``_control_host`` makes it — no bundled gate or
        fixture uses ``ctx.memo`` but through ``load_file``."""
        hits: list[str] = []
        counts = {"gates": 0, "baseline ran": 0, "control ran": 0, "skipped": 0}
        for pack_dir in _pack_dirs():
            registry = _load_pack(os.path.basename(pack_dir))
            shared = verdicts.SweepMemo()
            for spec, fn in registry.pairs():
                counts["gates"] += 1
                out = os.path.join(self.tmp(), spec.id)
                base_ctx = packs_mod.baseline_context(pack_dir, out_dir=os.path.join(out, "b"))
                base = gates_mod.run_gate(spec, fn, dataclasses.replace(base_ctx, memo=shared))
                bad_ctx = packs_mod.baseline_context(pack_dir, out_dir=os.path.join(out, "c"))
                control = gates_mod.selftest(
                    spec, fn, dataclasses.replace(bad_ctx, memo=verdicts.SweepMemo()))
                for kind, verdict in (("baseline", base), ("control", control)):
                    if "GateMemoError" in f"{verdict.error} {verdict.detail}":
                        hits.append(f"{spec.id} {kind}: {verdict.error or verdict.detail}")
                    if verdict.skipped:
                        counts["skipped"] += 1
                    else:
                        counts[f"{kind} ran"] += 1
        self.assertGreaterEqual(counts["gates"], MIN_PACK_GATES, counts)
        self.assertGreater(counts["baseline ran"], 0, counts)
        self.assertEqual(hits, [], f"a bundled gate or fixture uses ctx.memo directly: {counts}")

    def test_cad_solid_check_scripts_run(self):
        """The hand-run behaviour matrices call gate functions directly on plain
        contexts and assign params on contexts they built (packs:H15): nothing here
        may assume a traced view or a memo. Run from a copy, so their
        ``.selftest-out`` lands in the temp dir, never in the tracked tree."""
        registry = _load_pack("cad-solid")
        ok, why = gates_mod.availability(registry.get("cad.assembly_connected")[0])
        if not ok:
            # The outcome here is the availability skip, stated (R-7's rule): the
            # scripts need the mesh stack every one of these gates declares.
            self.assertRegex(why, r"trimesh|numpy")
            return
        copy = os.path.join(self.tmp(), "packs", "cad-solid")
        shutil.copytree(os.path.join(PACKS_DIR, "cad-solid"), copy,
                        ignore=shutil.ignore_patterns("__pycache__", ".selftest-out"))
        scripts = sorted(name for name in os.listdir(os.path.join(copy, "selftest"))
                         if name.startswith("check_") and name.endswith(".py"))
        self.assertGreaterEqual(len(scripts), 3, scripts)
        for name in scripts:
            with self.subTest(script=name):
                proc = _env.run([sys.executable, os.path.join(copy, "selftest", name)],
                                cwd=os.path.dirname(copy))
                self.assertEqual(proc.returncode, 0,
                                 f"{name}:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")


class ReadsStillAttributed(_env.EnvCase):
    """The gate reads a traced COPY of the params now, and a caller that noted
    which keys each gate read on its own mapping sees nothing of a copy being made.
    What that would have looked like: every ``Param.gates`` empty after a check,
    and ``atompipe why thickness`` saying no gate would notice the one number the
    bracket's failing claim turns on."""

    def test_why_names_the_gate_that_read_the_param(self):
        project = os.path.join(self.tmp(), "bracket")
        shutil.copytree(BRACKET, project, ignore=shutil.ignore_patterns("__pycache__", "out"))
        checked = _env.atompipe(["check"], cwd=project)
        self.assertIn(checked.returncode, (0, 1), checked.stderr)
        proc = _env.atompipe(["why", "thickness", "--json"], cwd=project)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = json.loads(proc.stdout)["why"]
        _head, sep, gates_part = text.partition("GATES (")
        self.assertTrue(sep, text)
        self.assertFalse(gates_part.startswith("0)"), text)
        self.assertIn("bracket.min_wall", gates_part.split("\n\n")[0], text)


# --------------------------------------------------------------------------- #
# run_all's hooks
# --------------------------------------------------------------------------- #
class RunAllHooks(_env.EnvCase):
    def test_before_stands_in_and_the_gate_never_runs(self):
        registry = gates_mod.Registry()
        ran: list = []
        _register(registry, "g.cached", lambda ctx: ran.append("cached") or True)
        _register(registry, "g.live", lambda ctx: ran.append("live") or True)
        served = Verdict(gate="g.cached", passed=False, detail="served from the cache")
        after: list = []
        streamed: list = []
        out = gates_mod.run_all(
            registry, GateContext(),
            before=lambda spec, fn: served if spec.id == "g.cached" else None,
            after=lambda spec, fn, verdict, trace: after.append((spec.id, trace)),
            on_verdict=streamed.append)
        self.assertEqual(ran, ["live"])
        self.assertIs(out[0], served)
        self.assertEqual([v.gate for v in streamed], ["g.cached", "g.live"])
        self.assertEqual([gate_id for gate_id, _ in after], ["g.live"],
                         "after() is for a gate that ran")
        self.assertIsInstance(after[0][1], GateTrace)

    def test_a_withdrawn_control_is_refused_before_before_is_asked(self):
        registry = gates_mod.Registry()
        _register(registry, "g.withdrawn", lambda ctx: True)
        stored, stored_fn = registry._gates["g.withdrawn"]
        registry._gates["g.withdrawn"] = (dataclasses.replace(stored, negative_control=None),
                                          stored_fn)
        asked: list = []
        [v] = gates_mod.run_all(registry, GateContext(),
                                before=lambda spec, fn: asked.append(spec.id))
        self.assertEqual(v.outcome, "error")
        self.assertEqual(asked, [])

    def test_the_sweep_shares_one_memo_and_the_caller_keeps_none(self):
        registry = gates_mod.Registry()
        memos: list = []
        _register(registry, "g.a", lambda ctx: memos.append(ctx.memo) or True)
        _register(registry, "g.b", lambda ctx: memos.append(ctx.memo) or True)
        ctx = GateContext()
        gates_mod.run_all(registry, ctx)
        self.assertIsNotNone(memos[0])
        self.assertIs(memos[0], memos[1])
        self.assertIsNone(ctx.memo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
