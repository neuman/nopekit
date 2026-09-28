# SPDX-License-Identifier: Apache-2.0
"""What a gate read is what its verdict is keyed by — so the record must be exact.

These are the trace primitives of `atompipe.verdicts` (PLAN M11.2, M13.7):

* `ParamTrace` — `ctx.params` as a gate sees it. What slipped through before it:
  `ctx.params` was ONE mutable dict shared by every gate, so gate A could forge
  gate B's inputs (S-24; `ctx.ledger` had a defensive copy for exactly that attack
  and `params` did not), and `cli._ParamReads` recorded nothing for bulk access —
  `dict(p)`, `{**p}`, `json.dumps(p)`, `.items()`, `repr`, `deepcopy` — so a gate
  that read its inputs in bulk would have been permanently fresh (S-25).
* `LedgerView` and `ModelProxy` — the other two ways a gate reaches the project.
  openmodelica reads a claim's limit through `ctx.ledger` (S-23's primitive).
* `digest_value` and `Anchors` — a value's digest that is the same in every
  checkout: fixtures set absolute mesh and `.mo` paths, and a raw digest of those
  would write a new tracked entry per clone (packs:H6).
* The audit hook — the files a gate opened. Import machinery, linecache, the
  interpreter's own trees and the user site are noise no gate decides on
  (packs:H1-H2): the first mesh gate alone opened 719 `.pyc` files. But only a
  module's SOURCE is linecache's: a data file read through it, through tokenize
  or through `pkgutil.get_data` is the gate's, and so is a sqlite database, which
  SQLite opens in C (review round 1, `probe.linecache`, `probe.sqlite`).
* Process starts that raise no audit event — ``_posixsubprocess.fork_exec``,
  how multiprocessing's spawn and forkserver methods exec an interpreter on
  POSIX — and ``_winapi.CreateProcess``, which does raise one that no handler
  took. A spawn worker reading a project file was invisible (review round 3,
  ``probe.mp``); a forkserver's child was named ``network``.
* The environment — no audit event at all; each variable a gate reads is named as
  the opaque channel `env:<NAME>` (review round 1, `probe.env`).
* The stat probes — the paths a gate asked the existence, kind or size of.
  `os.stat` raises no audit event, so `os.path.isfile` on a named input that
  was not there yet recorded nothing, and bundled `modelica.source_hygiene`
  kept a Fresh PASS after the `.mo` it had skipped appeared (review round 1).

Every recording test checks the trace, not just "no crash": a recorder that
records nothing passes every "no exception" test there is.

Run:  PYTHONPATH=src python3 -m unittest tests.test_trace -v
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import glob
import importlib
import importlib.util
import io
import json
import linecache
import math
import os
import pathlib
import pickle
import site
import socket
import sys
import textwrap
import threading
import tokenize
import traceback
import unittest
import uuid
import warnings
from unittest import mock

from atompipe import verdicts
from atompipe.gates import GateContext
from atompipe.models import (
    Acceptance, Claim, Ledger, Param, PhysicalResult, ProjectMeta, Tier, Verdict,
)
from atompipe.util import AtompipeError
from atompipe.verdicts import (
    ABSENT, PRESENT, Anchors, GateInputWriteError, GateTrace, LedgerView, ModelProxy,
    ParamTrace, digest_value, portable, small_value, traced_context, tracing,
)

import _env


def _params() -> dict:
    """A projection-shaped dict, fresh per call so no test sees another's edits."""
    return {
        "config": {"thickness": 8.0, "load_n": 50.0, "holes": [4.0, 6.0],
                   "material": "petg", "bed_xy": 220.0},
        "derived": {"deflection_mm": 0.469, "mass_g": 31.5},
        "name": "bracket",
        "long_text": "x" * 200,
    }


class ParamTraceRecords(unittest.TestCase):
    def setUp(self):
        self.trace = GateTrace()
        self.data = _params()
        self.p = ParamTrace(self.data, self.trace)

    def test_leaf_read_records_path_and_digest(self):
        self.assertEqual(self.p["config"]["thickness"], 8.0)
        self.assertEqual(self.trace.params, {("config", "thickness"): digest_value(8.0)})
        self.assertEqual(self.trace.values, {("config", "thickness"): 8.0})
        self.assertEqual(self.p.get("name"), "bracket")
        self.assertEqual(self.trace.params[("name",)], digest_value("bracket"))
        self.assertEqual(self.trace.whole, set())

    def test_container_traversal_is_not_a_whole_value_read(self):
        config = self.p["config"]
        self.assertIs(self.p["config"], config, "a nested view must be identity-stable")
        self.assertIs(self.p.get("config"), config)
        self.assertEqual(self.trace.params, {})
        self.assertEqual(self.trace.whole, set())
        config["load_n"]
        self.assertEqual(set(self.trace.params), {("config", "load_n")},
                         "reading one Config field must not make the gate depend on "
                         "every Config field (packs:H11)")

    def test_contains_records_presence_only(self):
        self.assertIn("name", self.p)
        self.assertNotIn("nope", self.p)
        self.assertIn("thickness", self.p["config"])
        self.assertEqual(self.trace.params, {("name",): PRESENT, ("nope",): ABSENT,
                                             ("config", "thickness"): PRESENT})
        self.assertEqual(self.trace.values, {}, "presence is not a value")
        self.p["name"]
        self.assertEqual(self.trace.params[("name",)], digest_value("bracket"),
                         "a value read must supersede a presence read")

    def test_missing_key_records_absent_not_null(self):
        self.assertIsNone(self.p.get("nope"))
        self.assertEqual(self.trace.params[("nope",)], ABSENT)
        with self.assertRaises(KeyError):
            self.p["config"]["nope"]
        self.assertEqual(self.trace.params[("config", "nope")], ABSENT)
        self.assertNotEqual(ABSENT, digest_value(None),
                            "a missing key and a None value must be two inputs")
        self.assertNotEqual(PRESENT, ABSENT)

    #: M11.2's proof list: every way to read a dict in bulk.
    BULK = {
        "dict()": lambda p: dict(p),
        "{**p}": lambda p: {**p},
        "f(**p)": lambda p: (lambda **kw: kw)(**p),
        "json.dumps": lambda p: json.dumps(p),
        "json.dumps(sort_keys)": lambda p: json.dumps(p, sort_keys=True),
        "items": lambda p: list(p.items()),
        "keys": lambda p: list(p.keys()),
        "values": lambda p: list(p.values()),
        "iter": lambda p: list(p),
        "for": lambda p: [k for k in p],
        "sorted": sorted,
        "len": len,
        "bool": bool,
        "==": lambda p: p == {},
        "== (reflected)": lambda p: {} == p,
        "!=": lambda p: p != {},
        "repr": repr,
        "str": str,
        "f-string": lambda p: f"{p}",
        "copy": lambda p: p.copy(),
        "copy.copy": copy.copy,
        "deepcopy": copy.deepcopy,
        "pickle": lambda p: pickle.dumps(p),
        "|": lambda p: p | {},
        "| (reflected)": lambda p: {} | p,
        "reversed": lambda p: list(reversed(p)),
    }

    def test_bulk_reads_are_whole_value_dependencies(self):
        for label, op in self.BULK.items():
            for where, path in (("top level", ()), ("nested", ("config",))):
                with self.subTest(op=label, where=where):
                    trace = GateTrace()
                    view = ParamTrace(_params(), trace)
                    level = view["config"] if path else view
                    raw = _params()["config"] if path else _params()
                    op(level)
                    self.assertIn(path, trace.whole,
                                  f"{label} read the whole {where} level and recorded "
                                  f"no dependency on it (S-25)")
                    self.assertEqual(trace.params[path], digest_value(raw))

    def test_copies_are_plain_dicts(self):
        def plain(value) -> bool:
            if isinstance(value, dict):
                return type(value) is dict and all(plain(v) for v in value.values())
            if isinstance(value, list):
                return all(plain(v) for v in value)
            return True

        for label, make in (("copy()", lambda p: p.copy()),
                            ("copy.copy", copy.copy),
                            ("copy.deepcopy", copy.deepcopy),
                            ("pickle", lambda p: pickle.loads(pickle.dumps(p))),
                            ("p | {}", lambda p: p | {})):
            with self.subTest(copy=label):
                out = make(self.p)
                self.assertTrue(plain(out), f"{label} handed back a ParamTrace inside")
                self.assertEqual(out, _params())
                out["config"]["thickness"] = -1.0
                out["config"]["holes"].append(99.0)
                self.assertEqual(self.data["config"]["thickness"], 8.0)
                self.assertEqual(self.data["config"]["holes"], [4.0, 6.0])

    def test_mutators_raise(self):
        def ior(p):
            p |= {"x": 1}

        cases = {
            "setitem": (lambda p: p.__setitem__("x", 1), "ctx.params['x']"),
            "nested setitem": (lambda p: p["config"].__setitem__("thickness", 7.0),
                               "ctx.params['config']['thickness']"),
            "delitem": (lambda p: p.__delitem__("name"), "ctx.params['name']"),
            "update": (lambda p: p.update(x=1), "ctx.params"),
            "nested update": (lambda p: p["config"].update(thickness=7.0),
                              "ctx.params['config']"),
            "pop": (lambda p: p.pop("name"), "ctx.params['name']"),
            "popitem": (lambda p: p.popitem(), "ctx.params"),
            "clear": (lambda p: p.clear(), "ctx.params"),
            "setdefault": (lambda p: p.setdefault("name", "x"), "ctx.params['name']"),
            "|=": (ior, "ctx.params"),
        }
        for label, (op, where) in cases.items():
            with self.subTest(mutator=label):
                with self.assertRaises(GateInputWriteError) as caught:
                    op(self.p)
                self.assertIsInstance(caught.exception, AtompipeError)
                self.assertEqual(str(caught.exception),
                                 f"a gate cannot write another gate's inputs: {where}")
        self.assertEqual(self.data, _params(), "a refused write still landed")
        self.assertEqual(dict(dict.items(self.p))["config"], _params()["config"])

    def test_host_view_is_writable_and_recorded(self):
        trace = GateTrace(kind="control")
        host = ParamTrace(self.data, trace, readonly=False, host=True)
        self.assertEqual(host["config"]["thickness"], 8.0)
        self.assertIn("name", host)
        self.assertEqual(trace.host_reads, {("config", "thickness"): digest_value(8.0),
                                            ("name",): PRESENT})
        self.assertEqual(trace.params, {}, "a fixture's host reads are not gate reads")

        host["config"]["thickness"] = 7.0
        host["fresh"] = {"a": 1}
        del host["name"]
        self.assertEqual(host["config"]["thickness"], 7.0)
        self.assertEqual(host["fresh"]["a"], 1)
        self.assertNotIn("name", host)
        self.assertEqual(self.data, _params(), "a fixture's write reached the sweep context")
        self.assertEqual(trace.host_reads[("config", "thickness")], digest_value(8.0),
                         "a read of the fixture's own write is not a host read")
        self.assertNotIn(("fresh", "a"), trace.host_reads)
        self.assertEqual(trace.host_reads[("name",)], PRESENT)

    def test_reads_through_a_returned_host_view_are_host_reads(self):
        """A fixture that returns the host context (`return ctx`, or layers one
        value over it) hands the gate the HOST's design: every read through that
        view, by any reader, is a host read — the seal detector's input (U18)."""
        control = GateTrace(kind="control")
        host_ctx = traced_context(GateContext(params=self.data), control, readonly=False)
        host_ctx.params["config"]["thickness"] = 1.0          # the fixture's one change
        gate_ctx = traced_context(host_ctx, control)             # `return ctx`
        self.assertEqual(gate_ctx.params["config"]["load_n"], 50.0)
        self.assertEqual(gate_ctx.params["config"]["thickness"], 1.0)
        self.assertEqual(control.params[("config", "load_n")], digest_value(50.0))
        self.assertEqual(control.params[("config", "thickness")], digest_value(1.0))
        self.assertEqual(control.host_reads, {("config", "load_n"): digest_value(50.0)},
                         "the gate's read of the fixture's own value is not a host read")

        other = GateTrace()                                      # any reader, not just this trace
        traced_context(host_ctx, other).params["name"]
        self.assertEqual(other.params[("name",)], digest_value("bracket"))
        self.assertIn(("name",), control.host_reads)

        copied = GateTrace(kind="control")                       # `dict(ctx.params)` layering
        host2 = traced_context(GateContext(params=_params()), copied, readonly=False)
        layered = dict(host2.params)
        layered["name"] = "planted"
        gate2 = traced_context(dataclasses.replace(host2, params=layered), copied)
        gate2.params["derived"]["mass_g"]
        self.assertIn((), copied.host_reads, "dict(host params) is a whole host read")
        self.assertIn(("derived", "mass_g"), copied.host_reads)

    def test_list_leaves_are_copies(self):
        holes = self.p["config"]["holes"]
        self.assertEqual(holes, [4.0, 6.0])
        holes.append(99.0)
        self.assertEqual(self.p["config"]["holes"], [4.0, 6.0],
                         "a returned list became a cross-gate write channel")
        self.assertEqual(self.trace.params[("config", "holes")], digest_value([4.0, 6.0]))
        self.assertNotIn(("config", "holes"), self.trace.whole)

    def test_isinstance_dict_holds(self):
        self.assertIsInstance(self.p, dict)
        self.assertIsInstance(self.p["config"], dict)
        self.assertIsInstance(ParamTrace(), dict)
        self.assertEqual(len(ParamTrace()), 0)

    def test_non_json_value_is_opaque_not_a_crash(self):
        class Mesh:
            pass

        looped: list = []
        looped.append(looped)
        trace = GateTrace()
        p = ParamTrace({"mesh": Mesh(), "loop": looped, "fine": 1.0}, trace)
        self.assertIsInstance(p["mesh"], Mesh)
        p["loop"]
        name = f"{Mesh.__module__}.{Mesh.__qualname__}"
        self.assertIn(f"param mesh: {name} is not JSON", trace.opaque)
        self.assertIn("param loop: builtins.list is not JSON", trace.opaque)
        for value in (Mesh(), looped, object(), {1, 2}, b"raw", float, print):
            with self.subTest(value=repr(value)[:40]):
                self.assertRegex(digest_value(value), r"^[0-9a-f]{64}$")
        p["fine"]
        self.assertEqual(len(trace.opaque), 2)

    def test_infinity_is_tagged_not_opaque(self):
        trace = GateTrace()
        p = ParamTrace({"stiffness": math.inf, "floor": -math.inf, "bad": math.nan}, trace)
        p["stiffness"], p["floor"], p["bad"]
        self.assertEqual(trace.opaque, set(), "an infinity is a value, not an opaque input")
        digests = {digest_value(math.inf), digest_value(-math.inf), digest_value(math.nan)}
        self.assertEqual(len(digests), 3)
        self.assertNotEqual(digest_value(math.inf), digest_value({"$float": "inf"}),
                            "a dict spelling the tag must not collide with the tag")
        self.assertNotIn(("stiffness",), trace.values, "inf has no JSON display value")

    def test_numpy_like_scalars_digest_as_their_item(self):
        class Scalar:
            """What numpy's 0-d types look like, duck-typed (the spine never imports it)."""
            dtype = "float64"
            shape = ()

            def __init__(self, value):
                self.value = value

            def item(self):
                return self.value

        self.assertEqual(digest_value(Scalar(2.5)), digest_value(2.5))
        self.assertEqual(digest_value({"a": [Scalar(1)]}), digest_value({"a": [1]}))
        self.assertEqual(small_value(Scalar(2.5)), (True, 2.5))

    def test_path_values_are_portable(self):
        a = Anchors(root="/a/proj", tmp="/a-tmp", home="/a-home")
        b = Anchors(root="/b/elsewhere/checkout", tmp="/b-tmp", home="/b-home")
        self.assertEqual(digest_value("/a/proj/model/x.stl", a),
                         digest_value("/b/elsewhere/checkout/model/x.stl", b))
        self.assertEqual(digest_value({"mesh": ["/a/proj/m.stl"]}, a),
                         digest_value({"mesh": ["/b/elsewhere/checkout/m.stl"]}, b))
        self.assertNotEqual(digest_value("/a/proj/model/x.stl"),
                            digest_value("/b/elsewhere/checkout/model/x.stl"))
        self.assertNotEqual(digest_value("/a/proj/model/x.stl", a),
                            digest_value("/b/elsewhere/checkout/model/y.stl", b))
        self.assertEqual(digest_value("/outside/x.stl", a), digest_value("/outside/x.stl"),
                         "a path under no anchor is hashed as written")

        traces = []
        for anchors, root in ((a, "/a/proj"), (b, "/b/elsewhere/checkout")):
            trace = GateTrace(anchors=anchors)
            ParamTrace({"mesh_path": f"{root}/model/part.stl"}, trace)["mesh_path"]
            traces.append(trace)
        self.assertEqual(traces[0].params, traces[1].params)
        self.assertEqual(traces[0].values[("mesh_path",)], "<root>/model/part.stl")


class LedgerViewRecords(unittest.TestCase):
    def ledger(self) -> Ledger:
        c1 = Claim(id="C1", statement="tip deflection stays small",
                   acceptance=Acceptance(quantity="tip deflection", limit=0.5, units="mm"))
        c2 = Claim(id="C2", statement="fits the bed")
        return Ledger(meta=ProjectMeta(name="bracket"), claims=[c1, c2],
                      params=[Param(name="thickness", value=8.0)],
                      verdicts=[Verdict(gate="bracket.deflection", passed=True)])

    def test_claim_read_records_that_claim(self):
        trace = GateTrace()
        view = LedgerView(self.ledger(), trace)
        self.assertEqual(view.claim("C1").acceptance.limit, 0.5)
        self.assertIsNone(view.claim("nope"))
        self.assertEqual(set(trace.ledger), {"claim:C1", "claim:nope"})
        self.assertEqual(trace.ledger["claim:nope"], ABSENT)
        self.assertNotIn("claims", trace.ledger, "one claim read is not a whole-list read")

    def test_an_acceptance_edit_moves_the_claim_digest(self):
        """S-23's primitive: openmodelica reads a limit through ctx.ledger, so a
        limit edit must change what that gate read."""
        before, after = GateTrace(), GateTrace()
        LedgerView(self.ledger(), before).claim("C1")
        edited = self.ledger()
        edited.claims[0].acceptance.limit = 0.4
        LedgerView(edited, after).claim("C1")
        self.assertNotEqual(before.ledger["claim:C1"], after.ledger["claim:C1"])

    def test_in_memory_fields_are_stripped(self):
        plain, dressed = GateTrace(), GateTrace()
        LedgerView(self.ledger(), plain).claim("C1")
        ledger = self.ledger()
        ledger.claims[0].gates = ["bracket.deflection"]
        ledger.claims[0].physical_result = PhysicalResult(passed=True, who="bench")
        LedgerView(ledger, dressed).claim("C1")
        self.assertEqual(plain.ledger, dressed.ledger,
                         "coverage and a bench result are not what a gate read")
        LedgerView(self.ledger(), plain).claims
        LedgerView(ledger, dressed).claims
        self.assertEqual(plain.ledger["claims"], dressed.ledger["claims"])

    def test_list_access_is_whole(self):
        trace = GateTrace()
        view = LedgerView(self.ledger(), trace)
        getattr(view, "claims", [])                      # openmodelica's spelling
        view.params
        view.meta
        view.inputs, view.needs, view.decisions, view.views
        self.assertEqual(set(trace.ledger), {"claims", "params", "meta", "inputs",
                                             "needs", "decisions", "views"})
        self.assertRegex(trace.ledger["claims"], r"^[0-9a-f]{64}$")

    def test_verdicts_are_not_an_input(self):
        trace = GateTrace()
        view = LedgerView(self.ledger(), trace)
        self.assertEqual(view.verdicts, [])
        self.assertIsNone(view.verdict("bracket.deflection"))
        self.assertEqual(trace.ledger, {},
                         "a gate reading other gates' verdicts would put verdicts in rho")

    def test_writes_stay_in_the_view(self):
        source = self.ledger()
        first = LedgerView(source, GateTrace())
        first.claims[0].statement = "forged"
        first.claim("C2").statement = "forged too"
        self.assertEqual(source.claims[0].statement, "tip deflection stays small")
        self.assertEqual(LedgerView(source, GateTrace()).claim("C2").statement,
                         "fits the bed")

    def test_bulk_forms_are_whole_reads_and_copies_are_plain(self):
        for label, op in (("to_dict", lambda v: v.to_dict()),
                          ("repr", repr),
                          ("deepcopy", copy.deepcopy),
                          ("replace", lambda v: dataclasses.replace(v, views=[])),
                          ("pickle", lambda v: pickle.loads(pickle.dumps(v)))):
            with self.subTest(op=label):
                trace = GateTrace()
                out = op(LedgerView(self.ledger(), trace))
                self.assertIn("claims", trace.ledger)
                if label in ("deepcopy", "pickle"):
                    self.assertIs(type(out), Ledger)
                    self.assertEqual(out.verdicts, [])

    def test_isinstance_ledger_holds(self):
        self.assertIsInstance(LedgerView(self.ledger(), GateTrace()), Ledger)


class ModelProxyRecords(unittest.TestCase):
    class Loaded:
        def __init__(self):
            self.config = {"thickness": 8.0}
            self.entry = "model/bracket.py"

    def test_untouched_model_is_not_used(self):
        trace = GateTrace()
        ctx = traced_context(GateContext(model=self.Loaded()), trace)
        dataclasses.replace(ctx, tier=1)
        copy.deepcopy(ctx)
        self.assertTrue(ctx.model is not None and bool(ctx.model))
        self.assertFalse(trace.model_used,
                         "passing the context around is not reading the model (packs:H10)")

    def test_attribute_access_marks_the_model_used(self):
        trace = GateTrace()
        proxy = ModelProxy(self.Loaded(), trace)
        self.assertEqual(proxy.config["thickness"], 8.0)
        self.assertTrue(trace.model_used)
        other = GateTrace()
        self.assertEqual(ModelProxy(self.Loaded(), other).entry, "model/bracket.py")
        self.assertTrue(other.model_used)
        repr_trace = GateTrace()
        repr(ModelProxy(self.Loaded(), repr_trace))
        self.assertTrue(repr_trace.model_used)

    def test_no_model_stays_none(self):
        ctx = traced_context(GateContext(model=None), GateTrace())
        self.assertIsNone(ctx.model)


class TierReadRecords(unittest.TestCase):
    """``ctx.tier`` as a gate sees it: every use of the value is a read.

    ``GateContext`` tells a gate it may pick a cheaper path by the sweep's tier,
    and nothing recorded that one had: rho never keyed it, and a PASS from the
    cheap path at tier 0 was served Fresh to ``check --tier 2`` (false-fresh
    probes, round 1, ``probe.tier``). An ``int`` subclass would miss the uses
    CPython serves from the integer's own digits without calling a method —
    indexing, ``range``, slicing — so the view is an integer-like object whose
    every extraction of the value goes through it."""

    def _view(self, tier: int = 2):
        trace = GateTrace()
        return traced_context(GateContext(tier=tier), trace), trace

    def test_passing_it_around_is_not_a_read(self):
        ctx, trace = self._view()
        dataclasses.replace(ctx, out_dir="/o")
        copy.deepcopy(ctx)
        copy.copy(ctx.tier)
        self.assertIsNotNone(ctx.tier)
        self.assertIsNone(trace.tier, "carrying the context is not reading its tier")

    def test_every_use_of_the_value_is_a_read(self):
        uses = {
            "a comparison": lambda t: t < 2,
            "a reflected comparison": lambda t: 2 > t,
            "equality with a Tier": lambda t: t == Tier.SOLVE,
            "a Tier's equality with it": lambda t: Tier.SOLVE == t,
            "truth": lambda t: bool(t),
            "int()": lambda t: int(t),
            "indexing a table": lambda t: ("a", "b", "c", "d")[t],
            "range()": lambda t: list(range(t)),
            "a slice": lambda t: "abcd"[:t],
            "arithmetic": lambda t: t + 1,
            "reflected arithmetic": lambda t: 1 + t,
            "Tier()": lambda t: Tier(t),
            "a dict key": lambda t: {2: "x"}[t],
            "an f-string": lambda t: f"{t}",
            "%d": lambda t: "%d" % t,
            "str()": lambda t: str(t),
            "isinstance(int)": lambda t: isinstance(t, int),
            "pickling": lambda t: pickle.dumps(t),
            "an int method": lambda t: t.bit_length(),
            "math": lambda t: math.floor(t),
        }
        for name, use in uses.items():
            with self.subTest(use=name):
                ctx, trace = self._view()
                use(ctx.tier)
                self.assertEqual(trace.tier, 2, f"{name} used ctx.tier and recorded nothing")

    def test_it_behaves_as_the_int_it_is(self):
        ctx, _trace = self._view(2)
        tier = ctx.tier
        self.assertEqual(tier, 2)
        self.assertTrue(tier == Tier.SOLVE and Tier.SOLVE == tier and not tier != 2)
        self.assertIs(Tier(tier), Tier.SOLVE)
        self.assertEqual(("a", "b", "c", "d")[tier], "c")
        self.assertEqual(hash(tier), hash(2))
        self.assertIsInstance(tier, int)
        self.assertEqual((f"{tier:02d}", str(tier), repr(tier)), ("02", "2", "2"))
        self.assertTrue(tier < 3 and tier >= 2 and 1 < tier and max(tier, 1) == 2)
        loaded = pickle.loads(pickle.dumps(tier))
        self.assertIs(type(loaded), int, "what leaves by pickle is a plain int")
        self.assertEqual(json.dumps(int(tier)), "2")
        with self.assertRaisesRegex(TypeError, "Object of type TierRead is not JSON"):
            json.dumps(tier)          # loud, and naming the view: not "type int"

    def test_a_fixture_view_hands_its_gate_the_same_read(self):
        """A control's gate reads the tier its fixture was handed — the sweep's,
        wrapped on the control's trace as ``_control_host`` hands it — and the
        read lands there; a tier the fixture chose itself is a constant of the
        fixture, not an input of the control."""
        trace = GateTrace(kind="control")
        host = traced_context(GateContext(tier=verdicts.TierRead(2, trace)), trace,
                              readonly=False)
        passed = traced_context(dataclasses.replace(host, params={"x": 1}), trace)
        self.assertIs(passed.tier, host.tier, "the host's tier was re-wrapped")
        self.assertIsNone(trace.tier)
        _ = passed.tier < 2
        self.assertEqual(trace.tier, 2, "the gate read the host's tier and the control "
                                        "recorded nothing")
        chosen = GateTrace(kind="control")
        view = traced_context(GateContext(tier=3), chosen)
        _ = view.tier < 2
        self.assertIsNone(chosen.tier, "a tier the fixture set is its own constant")

    def test_the_value_read_is_keyed_and_an_unread_tier_is_not(self):
        ctx, trace = self._view(0)
        _ = ctx.tier < 2
        reads = verdicts.Reads.from_trace(trace)
        self.assertEqual(reads.tier, 0)
        self.assertEqual(reads.to_dict()["tier"], 0)
        unread = verdicts.Reads.from_trace(GateTrace())
        self.assertIsNone(unread.tier)
        self.assertNotIn("tier", unread.to_dict(),
                         "a gate that never read the tier is keyed exactly as before")
        at_two = dataclasses.replace(reads, tier=2)
        self.assertNotEqual(verdicts.rho("g", "s", "c", reads), verdicts.rho("g", "s", "c", at_two))
        self.assertEqual(verdicts.rho("g", "s", "c", unread), verdicts.rho("g", "s", "c", {}),
                         "no tier read, no tier in rho")


class TracedContextShape(unittest.TestCase):
    def test_same_type_read_only_params_and_a_private_extra(self):
        extra = {"pack_dirs": {"x": "/p"}}
        base = GateContext(params=_params(), extra=extra, ledger=Ledger(), out_dir="/o")
        trace = GateTrace()
        ctx = traced_context(base, trace)
        self.assertIs(type(ctx), GateContext)
        self.assertIsInstance(ctx.params, ParamTrace)
        self.assertIsInstance(ctx.ledger, LedgerView)
        self.assertEqual(ctx.out_dir, "/o")
        ctx.extra["planted"] = 1
        self.assertNotIn("planted", extra, "a gate's extra write reached the sweep")
        with self.assertRaises(GateInputWriteError):
            ctx.params["name"] = "x"
        self.assertEqual(ctx.param("thickness", scope="config"), 8.0)
        self.assertEqual(trace.params[("config", "thickness")], digest_value(8.0))


class PortableText(unittest.TestCase):
    ANCHORS = Anchors(
        root="/w/proj",
        packs={"fdm-print": "/w/proj/.atompipe/packs/fdm-print",
               "cad-solid": "/opt/packs/cad-solid"},
        out="/w/proj/.atompipe/out",
        controls_out="/w/proj/.atompipe/out/controls",
        tmp="/t", home="/h/u")

    def test_every_anchor(self):
        cases = {
            "/w/proj/model/x.stl": "<root>/model/x.stl",
            "/w/proj": "<root>",
            "/w/proj/.atompipe/packs/fdm-print/gates/mesh.py":
                "<pack:fdm-print>/gates/mesh.py",
            "/opt/packs/cad-solid/selftest/cube.stl": "<pack:cad-solid>/selftest/cube.stl",
            "/w/proj/.atompipe/out/deflection.png": "<out>/deflection.png",
            "/w/proj/.atompipe/out/controls/g/x.png": "<out:controls>/g/x.png",
            "/t/atompipe-x/run.mos": "<tmp>/atompipe-x/run.mos",
            "/h/u/.local/share/f": "~/.local/share/f",
        }
        for raw, want in cases.items():
            with self.subTest(path=raw):
                self.assertEqual(portable(raw, self.ANCHORS), want)
                self.assertEqual(self.ANCHORS.portable_path(raw), want)

    def test_boundaries(self):
        for raw in ("/w/project2/x", "/w/proj.bak/x", "/x/w/proj/y", "w/proj/rel"):
            with self.subTest(path=raw):
                self.assertEqual(portable(raw, self.ANCHORS), raw)

    def test_free_text(self):
        text = ('[/w/proj/model/Tank.mo:12:3-12:9] Error: Class "X" not found; '
                "see /w/proj/.atompipe/out/log.txt and /elsewhere/y")
        self.assertEqual(portable(text, self.ANCHORS),
                         '[<root>/model/Tank.mo:12:3-12:9] Error: Class "X" not found; '
                         "see <out>/log.txt and /elsewhere/y")
        self.assertEqual(portable(text, None), text)

    def test_outside_anchor_evidence_is_dropped(self):
        self.assertEqual(
            self.ANCHORS.evidence(["/w/proj/.atompipe/out/p.png", "/elsewhere/q.png",
                                   "rel/r.png", "/t/s.csv"]),
            ["<out>/p.png", "rel/r.png", "<tmp>/s.csv"])
        self.assertIsNone(self.ANCHORS.portable_path("/elsewhere/q.png"))

    def test_a_filesystem_root_is_never_an_anchor(self):
        anchors = Anchors(root="/", home="/", tmp="/")
        self.assertEqual(portable("/etc/hosts", anchors), "/etc/hosts")
        self.assertEqual(anchors.evidence(["/etc/hosts"]), [])

    def test_small_values(self):
        self.assertEqual(small_value(220.0), (True, 220.0))
        self.assertEqual(small_value(True), (True, True))
        self.assertEqual(small_value(None), (True, None))
        self.assertEqual(small_value("petg"), (True, "petg"))
        self.assertEqual(small_value("x" * verdicts.SMALL_VALUE_MAX_CHARS)[0], True)
        self.assertEqual(small_value("x" * (verdicts.SMALL_VALUE_MAX_CHARS + 1)),
                         (False, None))
        self.assertEqual(small_value([1.0]), (False, None))
        self.assertEqual(small_value(math.inf), (False, None))
        self.assertEqual(small_value("/w/proj/m.stl", self.ANCHORS), (True, "<root>/m.stl"))


def _swallow(kind: type, read) -> None:
    """``read()``, with ``kind`` raised by it ignored: a lookup that misses."""
    try:
        read()
    except kind:
        pass


class AuditTrace(_env.EnvCase):
    def setUp(self):
        self.dir = self.tmp()
        self.path = self.file("input.csv")

    def file(self, name: str, text: str = "1,2\n") -> str:
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_read_is_recorded(self):
        trace = GateTrace()
        with tracing(trace):
            with open(self.path, encoding="utf-8") as fh:
                fh.read()
            pathlib.Path(self.path).read_bytes()
        self.assertEqual(trace.files_read, [self.path])
        self.assertEqual(trace.files_written, set())

    def test_a_write_is_recorded_and_reads_of_it_are_not(self):
        out = os.path.join(self.dir, "out.png")
        moved = os.path.join(self.dir, "final.png")
        trace = GateTrace()
        with tracing(trace):
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("x")
            with open(out, encoding="utf-8") as fh:
                fh.read()
            os.replace(out, moved)
            with open(moved, encoding="utf-8") as fh:
                fh.read()
        self.assertEqual(trace.files_read, [], "the gate's own output is not its input")
        self.assertTrue({out, moved} <= trace.files_written)

    def test_read_then_write_is_self_modified(self):
        trace = GateTrace()
        with tracing(trace):
            with open(self.path, encoding="utf-8") as fh:
                fh.read()
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write("3,4\n")
        self.assertEqual(trace.files_read, [self.path])
        self.assertEqual(trace.self_modified(), [self.path],
                         "its pre-write bytes are gone: classification must name it")
        rplus = GateTrace()
        other = self.file("other.csv")
        with tracing(rplus):
            with open(other, "r+", encoding="utf-8") as fh:
                fh.read()
        self.assertEqual(rplus.self_modified(), [other])

    def test_os_open_flags(self):
        trace = GateTrace()
        created = os.path.join(self.dir, "created.bin")
        both = self.file("both.bin")
        with tracing(trace):
            os.close(os.open(self.path, os.O_RDONLY))
            os.close(os.open(created, os.O_WRONLY | os.O_CREAT))
            os.close(os.open(both, os.O_RDWR))
            os.close(os.open(self.dir, os.O_RDONLY))          # a directory fd, not a read
        self.assertEqual(trace.files_read, [self.path, both])
        self.assertEqual(trace.files_written, {created, both})

    def test_an_int_fd_is_ignored(self):
        fd = os.open(self.path, os.O_RDONLY)
        trace = GateTrace()
        with tracing(trace):
            with open(fd, encoding="utf-8", closefd=True) as fh:
                fh.read()
        self.assertEqual(trace.files_read, [])

    def test_bytes_pathlike_and_relative_paths(self):
        trace = GateTrace()
        second = self.file("second.csv")
        third = self.file("third.csv")
        with tracing(trace):
            open(os.fsencode(self.path), "rb").close()
            open(pathlib.Path(second), "rb").close()
            cwd = os.getcwd()
            os.chdir(self.dir)
            try:
                open("third.csv", "rb").close()
            finally:
                os.chdir(cwd)
        self.assertEqual(trace.files_read, [self.path, second, third])
        self.assertTrue(all(isinstance(p, str) for p in trace.files_read))

    def test_listdir_and_scandir(self):
        sub = os.path.join(self.dir, "sub")
        os.mkdir(sub)
        trace = GateTrace()
        with tracing(trace):
            os.listdir(self.dir)
            with os.scandir(sub) as it:
                list(it)
        self.assertEqual(trace.dirs, {self.dir, sub})

    def test_a_fresh_import_records_nothing(self):
        name = f"atompipe_trace_probe_{uuid.uuid4().hex}"
        self.file(f"{name}.py", "VALUE = 41 + 1\n")
        sys.path.insert(0, self.dir)
        self.addCleanup(sys.path.remove, self.dir)
        self.addCleanup(sys.modules.pop, name, None)
        importlib.invalidate_caches()
        trace = GateTrace()
        with tracing(trace):
            module = importlib.import_module(name)
        self.assertEqual(module.VALUE, 42)
        self.assertEqual((trace.files_read, trace.files_written, trace.dirs, trace.opaque,
                          trace.stats),
                         ([], set(), set(), set(), []),
                         "import machinery's opens, listings, stats and .pyc writes are "
                         "not gate inputs (packs:H1)")

    # -- code loaded at RUN time (admission review, round 2) ------------------ #
    def _forget_loaded(self) -> None:
        """Drop every module this test's directory put in ``sys.modules``."""
        base = os.path.abspath(self.dir) + os.sep
        for name, module in list(sys.modules.items()):
            where = getattr(module, "__file__", None)
            if isinstance(where, str) and os.path.abspath(where).startswith(base):
                sys.modules.pop(name, None)

    def test_a_stock_import_at_run_time_is_a_source_under_the_project(self):
        """V: the admission review's round-2 repro (c). A gate taking its limit
        from ``importlib.import_module(name)`` loads the module through the
        STOCK import system, while no load is being recorded: the source read
        was the import system's, dropped as the closure's business, and no
        closure was being built — so nothing keyed it. Its source is a module
        source of the window now (``GateTrace.sources``, apart from
        ``files_read``), keyed under the project and dropped — never opaque —
        anywhere else; read from a valid ``.pyc`` instead, it is still its
        source. The named residual: served from ``sys.modules``, it opens
        nothing and keys nothing."""
        self.addCleanup(self._forget_loaded)
        name = f"atompipe_trace_rules_{uuid.uuid4().hex}"
        path = self.file(f"{name}.py", "LIMIT = 50.0\n")
        sys.path.insert(0, self.dir)
        self.addCleanup(sys.path.remove, self.dir)
        importlib.invalidate_caches()
        first = GateTrace()
        with tracing(first):
            self.assertEqual(importlib.import_module(name).LIMIT, 50.0)
        self.assertEqual(first.sources, [path])
        self.assertEqual(first.files_read, [], "a module's source is not a data read")

        import py_compile
        py_compile.compile(path, cfile=importlib.util.cache_from_source(path), doraise=True)
        sys.modules.pop(name)
        from_pyc = GateTrace()
        with tracing(from_pyc):
            importlib.import_module(name)
        self.assertEqual(from_pyc.sources, [path],
                         "a valid .pyc read in place of the source hid the module")
        served = GateTrace()
        with tracing(served):
            importlib.import_module(name)
        self.assertEqual(served.sources, [], "served from sys.modules, nothing is opened")

        inside = verdicts.Reads.from_trace(first, anchors=Anchors(root=self.dir))
        self.assertEqual(list(inside.files), [f"{name}.py"])
        outside = verdicts.Reads.from_trace(
            first, anchors=Anchors(root=os.path.join(self.dir, "elsewhere")))
        self.assertEqual((outside.files, outside.opaque), ({}, []),
                         "code elsewhere on sys.path is not an input of this project")

    def test_a_recorded_load_at_run_time_is_a_read_whether_it_runs_or_is_served(self):
        """V: the admission review's round-2 repros (a)-(c). ``modelio.load_path``
        called while a gate, a fixture or ``known_good.context`` RUNS records
        no closure (none is being built) — and a cache hit joined one only
        while one was: the helper's code was keyed by nothing, and neither was
        what it read at its import once it was served to a second gate. Run or
        served, every file of its closure is a read of the window now."""
        from atompipe import modelio
        self.addCleanup(self._forget_loaded)
        data = self.file("table.json", '{"limit": 50}\n')
        helper = self.file(f"tables_{uuid.uuid4().hex}.py",
                           f"import json\nwith open({data!r}, encoding='utf-8') as fh:\n"
                           f"    LIMIT = json.load(fh)['limit']\n")
        ran = GateTrace()
        with tracing(ran):
            module = modelio.load_path(helper)
        self.assertEqual(module.LIMIT, 50)
        self.assertEqual(sorted(ran.files_read), sorted([helper, data]))
        self.assertEqual(ran.sources, [], "a recorded load is the loader's, not a stock import")
        served = GateTrace()
        with tracing(served):
            self.assertIs(modelio.load_path(helper), module, "the unchanged helper ran twice")
        self.assertEqual(sorted(served.files_read), sorted([helper, data]),
                         "the second gate to ask keyed less than the first")
        nobody = GateTrace()
        self.assertIs(modelio.load_path(helper), module)          # no window: nothing, no error
        self.assertEqual(nobody.files_read, [])

    def test_the_loaders_own_digests_are_nobodys_read(self):
        """V: the loader digests code and data for its own bookkeeping — a load
        purges every stale module under its roots first, and serving one
        re-checks its closure. Under a window those reads were filed as the
        running gate's: a ``.py`` was dropped as a module's source, a ``.json``
        was not, so a gate's first ``load_path`` keyed the DATA of an unrelated
        module it never read, and only when it was the first to load anything."""
        from atompipe import modelio
        self.addCleanup(self._forget_loaded)
        other_data = self.file("other.json", "{}\n")
        other = self.file(f"other_{uuid.uuid4().hex}.py",
                          f"with open({other_data!r}, encoding='utf-8') as fh:\n"
                          f"    X = fh.read()\n")
        mine = self.file(f"mine_{uuid.uuid4().hex}.py", "Y = 1\n")
        modelio.load_path(other)                    # before any window
        trace = GateTrace()
        with tracing(trace):
            modelio.load_path(mine)                 # its load checks `other`'s closure
        self.assertEqual(trace.files_read, [mine])
        self.assertEqual((trace.stats, trace.dirs, trace.opaque), ([], set(), set()))

    def _fresh_function(self, body: str):
        """A function compiled from a file linecache has never seen."""
        path = self.file(f"fresh_{uuid.uuid4().hex}.py", body)
        namespace: dict = {}
        with open(path, encoding="utf-8") as fh:
            exec(compile(fh.read(), path, "exec"), namespace)
        linecache.checkcache(path)
        return path, namespace

    def test_a_warnings_linecache_read_is_excluded(self):
        path, ns = self._fresh_function(
            "import warnings\n\ndef f():\n    warnings.warn('careful now', UserWarning)\n")
        trace = GateTrace()
        stderr = io.StringIO()
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            with contextlib.redirect_stderr(stderr):
                with tracing(trace):
                    ns["f"]()
        self.assertIn("warnings.warn('careful now'", stderr.getvalue(),
                      "linecache never read the source, so this test proves nothing")
        self.assertEqual(trace.files_read, [])
        self.assertEqual(trace.stats, [], "linecache's own os.stat is the formatter's")

    def test_traceback_formatting_is_excluded(self):
        path, ns = self._fresh_function("def g():\n    raise ValueError('boom')\n")
        inside, after = GateTrace(), GateTrace()
        with tracing(inside):
            try:
                ns["g"]()
            except ValueError:
                text = traceback.format_exc()
        self.assertIn("raise ValueError('boom')", text)
        linecache.clearcache()
        try:
            with tracing(after):
                ns["g"]()
        except ValueError:
            text = traceback.format_exc()                  # the window is closed by now
        self.assertIn("raise ValueError('boom')", text)
        self.assertEqual((inside.files_read, after.files_read), ([], []))
        self.assertEqual((inside.stats, after.stats), ([], []))
        self.assertNotIn(after, verdicts._STACK, "an exception must still close the window")

    def test_a_pseudo_filename_is_not_a_file(self):
        """3.13's traceback parses line fragments for its carets; the SyntaxError
        that often raises makes CPython open "<unknown>" to quote the line."""
        import ast
        trace = GateTrace()
        with tracing(trace):
            with self.assertRaises(SyntaxError):
                ast.parse("x = (", filename="<unknown>")
            verdicts._audit("open", ("<string>", "r", 0))
        self.assertEqual(trace.files_read, [])

    # -- the source readers read data too ------------------------------------ #
    def test_a_data_file_read_through_a_source_reader_is_recorded(self):
        """V: linecache, tokenize and import machinery were excluded whole, as
        readers of a module's source — so a gate's data read through them
        recorded nothing (review round 1, ``probe.linecache``: ``files={}``, a
        Fresh PASS after the file went to 0). Each spelling must record the data
        file, and ``linecache.checkcache``'s stat of it is a question like any
        other."""
        lines = self.file("lines.txt", "5\n")
        tokens = self.file("tokens.txt", "5\n")
        package = os.path.join(self.dir, f"datapkg_{uuid.uuid4().hex}")
        os.mkdir(package)
        with open(os.path.join(package, "__init__.py"), "w", encoding="utf-8"):
            pass
        table = os.path.join(package, "table.txt")
        with open(table, "w", encoding="utf-8") as fh:
            fh.write("5\n")
        sys.path.insert(0, self.dir)
        self.addCleanup(sys.path.remove, self.dir)
        self.addCleanup(sys.modules.pop, os.path.basename(package), None)
        importlib.invalidate_caches()
        importlib.import_module(os.path.basename(package))
        import pkgutil

        trace = GateTrace()
        with tracing(trace):
            linecache.checkcache(lines)
            self.assertEqual(linecache.getline(lines, 1), "5\n")
            with tokenize.open(tokens) as fh:
                self.assertEqual(fh.read(), "5\n")
            self.assertEqual(pkgutil.get_data(os.path.basename(package), "table.txt"), b"5\n")
        self.assertEqual(trace.files_read, [lines, tokens, table])
        self.assertIn(lines, trace.stats, "linecache's stat of a data file is not the "
                                          "formatter's")

    def test_a_window_forgets_linecache_data_but_keeps_sources(self):
        """linecache is a memo: a data file it read before the window opened is
        served with no open — the admission control's run warms it for the
        gate's. A window's push forgets data lines; a source's lines, and a
        pseudo-named entry nothing can read again, stay for the formatter."""
        data = self.file("warm.txt", "5\n")
        source, _ns = self._fresh_function("def h():\n    return 1\n")
        linecache.getline(data, 1)
        linecache.getline(source, 1)
        linecache.cache["<atompipe-trace-probe>"] = (1, None, ["x = 1\n"], "<atompipe-trace-probe>")
        self.addCleanup(linecache.cache.pop, "<atompipe-trace-probe>", None)
        trace = GateTrace()
        with tracing(trace):
            self.assertEqual(linecache.getline(data, 1), "5\n")
        self.assertEqual(trace.files_read, [data], "a warm linecache hid the file")
        self.assertIn(source, linecache.cache)
        self.assertIn("<atompipe-trace-probe>", linecache.cache)

    # -- sqlite ------------------------------------------------------------ #
    def _db(self, name: str) -> str:
        import sqlite3
        path = os.path.join(self.dir, name)
        con = sqlite3.connect(path)
        con.execute("create table t (v real)")
        con.execute("insert into t values (5)")
        con.commit()
        con.close()
        return path

    def test_a_sqlite_database_is_a_read_and_a_writable_one_is_opaque(self):
        """V: SQLite opens its files in C — no ``open`` event — so a gate's
        database recorded nothing (review round 1, ``probe.sqlite``: ``files={}
        opaque=[]``). A read-only connection reads the file and its ``-wal``; a
        writable one is named opaque; a private in-memory one reads nothing."""
        import sqlite3
        ro, rw = self._db("ro.db"), self._db("rw.db")
        read_only, writable, memory = GateTrace(), GateTrace(), GateTrace()
        with tracing(read_only):
            con = sqlite3.connect(pathlib.Path(ro).as_uri() + "?mode=ro", uri=True)
            con.execute("select v from t").fetchone()
            con.close()
        with tracing(writable):
            con = sqlite3.connect(rw)
            con.execute("select v from t").fetchone()
            con.close()
        with tracing(memory):
            sqlite3.connect(":memory:").close()
            sqlite3.connect("file::memory:?cache=shared", uri=True).close()
            sqlite3.connect("file:mem_probe?mode=memory&cache=shared", uri=True).close()
        self.assertEqual(read_only.files_read, [ro, ro + "-wal"])
        self.assertEqual(read_only.opaque, set())
        self.assertEqual(writable.files_read, [rw, rw + "-wal"])
        self.assertEqual(writable.opaque, {f"sqlite-writable:{rw}"})
        self.assertEqual((memory.files_read, memory.opaque), ([], set()))

    def test_a_sqlite_uri_is_read_as_sqlite_reads_it(self):
        cases = {
            "": (None, False),
            ":memory:": (None, False),
            "data/m.db": ("data/m.db", True),
            "file:data/m.db": ("data/m.db", True),
            "file:data/m.db?mode=ro": ("data/m.db", False),
            "file:data/m.db?immutable=1": ("data/m.db", False),
            "file:data/m.db?mode=rw#frag": ("data/m.db", True),
            "file:///w/a%20b.db?mode=ro": ("/w/a b.db", False),
            "file://localhost/w/m.db": ("/w/m.db", True),
            "file://elsewhere/w/m.db": (None, False),
            "file::memory:": (None, False),
            "file:x?mode=memory": (None, False),
        }
        for database, expected in cases.items():
            with self.subTest(database=database):
                self.assertEqual(verdicts._sqlite_target(database), expected)

    # -- the environment --------------------------------------------------- #
    def test_an_environment_read_is_named_opaque(self):
        """V: an environment read fires no audit event, so a gate that decided on
        a variable recorded nothing (review round 1, ``probe.env``). Each
        spelling names the variable — a miss included, since its absence is
        what the gate decided on — and a bulk read names the whole
        environment."""
        name = f"ATOMPIPE_TRACE_PROBE_{uuid.uuid4().hex.upper()}"
        spellings = {
            "os.environ.get": lambda: os.environ.get(name),
            "os.getenv": lambda: os.getenv(name),
            "in": lambda: name in os.environ,
            "[]": lambda: _swallow(KeyError, lambda: os.environ[name]),
            "os.environb.get": lambda: os.environb.get(os.fsencode(name)),
            "expandvars": lambda: os.path.expandvars(f"${name}"),
        }
        for label, read in spellings.items():
            with self.subTest(spelling=label):
                trace = GateTrace()
                with tracing(trace):
                    read()
                self.assertEqual(trace.opaque, {f"env:{name}"})
        for label, read in {"dict": lambda: dict(os.environ),
                            "copy": os.environ.copy,
                            "items": lambda: list(os.environ.items()),
                            "len": lambda: len(os.environ)}.items():
            with self.subTest(spelling=label):
                trace = GateTrace()
                with tracing(trace):
                    read()
                self.assertEqual(trace.opaque, {"env:*"})

    def test_the_environment_a_library_reads_is_not_the_gates(self):
        """The cost side: the variables the standard library reads on a gate's
        behalf — ``PATH`` for ``shutil.which`` and a subprocess, ``TMPDIR`` for
        ``tempfile`` — and the spine's own reads are not the gate's, or every
        gate that starts a tool would be opaque twice over."""
        import shutil
        import tempfile
        trace = GateTrace()
        with tracing(trace):
            shutil.which("atompipe-no-such-tool")
            with mock.patch.object(tempfile, "tempdir", None):
                tempfile.gettempdir()               # TMPDIR, TEMP, TMP, through os.getenv
        self.assertEqual(trace.opaque, set())
        # the test's own negative control: the same variable, asked by the gate
        with tracing(trace):
            os.get_exec_path()
        self.assertEqual(trace.opaque, {"env:PATH"})

    def test_the_environment_is_the_same_object_and_writes_are_untouched(self):
        """What a replacement proxy would have broken (``doctor``'s reason for
        rejecting one): the object every binding holds, and the ``putenv`` each
        write makes, which is what a subprocess started with no ``env=``
        inherits. Only reads are recorded; a write goes through
        ``os._Environ``'s own methods."""
        before = os.environ
        with tracing(GateTrace()):
            pass
        self.assertIs(os.environ, before)
        self.assertIsInstance(os.environ, os._Environ)
        for method in ("__setitem__", "__delitem__"):
            self.assertIs(getattr(type(os.environ), method), getattr(os._Environ, method))
        name = f"ATOMPIPE_TRACE_CHILD_{uuid.uuid4().hex.upper()}"
        with mock.patch.dict(os.environ):
            with tracing(GateTrace()):
                os.environ[name] = "inherited"
                out = _env.run([sys.executable, "-c", f"import os; print(os.environ[{name!r}])"],
                               cwd=self.dir)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "inherited"), out.stderr)
        self.assertNotIn(name, os.environ)
        outside = GateTrace()
        with tracing(outside):
            pass
        os.environ.get(name)
        self.assertEqual(outside.opaque, set(), "a read outside every window was recorded")

    def test_interpreter_and_user_site_are_excluded(self):
        control = GateTrace()
        with tracing(control):
            open(self.path, "rb").close()
        self.assertEqual(control.files_read, [self.path], "an ordinary file must record")

        trace = GateTrace()
        with tracing(trace):
            open(os.__file__, "rb").close()                  # the stdlib, under sys.prefix
            open(verdicts.__file__, "rb").close()            # the atompipe package itself
            open(self.file("cached.pyc"), "rb").close()
            if os.path.exists("/proc/self/status"):
                open("/proc/self/status", "rb").close()
        self.assertEqual(trace.files_read, [])

        user_site = self.tmp()
        library = os.path.join(user_site, "trimesh_like.py")
        with open(library, "w", encoding="utf-8") as fh:
            fh.write("")
        self.addCleanup(verdicts._library_roots.cache_clear)
        with mock.patch.object(site, "USER_SITE", user_site):
            verdicts._library_roots.cache_clear()
            user = GateTrace()
            with tracing(user):
                open(library, "rb").close()
        self.assertEqual(user.files_read, [],
                         "trimesh and numpy live in the user site on the dev box")

    def test_packs_shipped_inside_the_wheel_are_not_library(self):
        """pyproject maps packs/ onto atompipe/bundled/, under site-packages AND
        the atompipe package: a bundled pack's data read must still record."""
        verdicts.spine_digest()                    # memoised before __file__ is patched
        site_packages = self.tmp()
        package = os.path.join(site_packages, "atompipe")
        data = os.path.join(package, "bundled", "fdm-print", "selftest", "baseline.json")
        spine_file = os.path.join(package, "site_template", "index.html")
        for path in (data, spine_file):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{}")
        self.addCleanup(verdicts._library_roots.cache_clear)
        with mock.patch.object(verdicts, "__file__", os.path.join(package, "verdicts.py")), \
                mock.patch.object(site, "getsitepackages", lambda: [site_packages],
                                  create=True):
            verdicts._library_roots.cache_clear()
            trace = GateTrace()
            with tracing(trace):
                open(spine_file, "rb").close()
                open(data, "rb").close()
        self.assertEqual(trace.files_read, [data])

    def test_popen_is_opaque_and_names_its_argv_files(self):
        trace = GateTrace()
        with tracing(trace):
            proc = _env.run([sys.executable, "-c", "pass", self.path, "--not-a-file"],
                            cwd=self.dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"subprocess:{os.path.basename(sys.executable)}", trace.opaque)
        self.assertIn(self.path, trace.files_read)

    def test_socket_connect_is_network(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(client.close)
        trace = GateTrace()
        with tracing(trace):
            client.connect(server.getsockname())
        self.assertEqual(trace.opaque, {"network"})

    def test_a_worker_of_every_start_method_is_a_process(self):
        """V: the false-fresh review's ``probe.mp`` (round 3). A spawn worker —
        the default start method on macOS and Windows — is exec'd by
        ``_posixsubprocess.fork_exec``, which raises no audit event, so a gate
        that read its limit in one recorded ``opaque=[]`` and kept a Fresh PASS
        after the file went to 0. A forkserver's child was opaque only by
        accident, as ``network`` (the unix socket the request goes over).
        Each method, twice in one fresh process — the second forkserver window
        finds its server running, so its child's only trace is the request on
        the server's socket — must name a process, and never the network."""
        code = textwrap.dedent("""\
            import concurrent.futures, json, multiprocessing, pathlib, sys
            from atompipe.verdicts import GateTrace, tracing
            seen = {}
            for method in multiprocessing.get_all_start_methods():
                windows = []
                for _ in range(2):
                    trace = GateTrace()
                    context = multiprocessing.get_context(method)
                    with tracing(trace):
                        with concurrent.futures.ProcessPoolExecutor(
                                1, mp_context=context) as pool:
                            pool.submit(pathlib.Path(sys.argv[1]).read_text).result()
                    windows.append(sorted(trace.opaque))
                seen[method] = windows
            print(json.dumps(seen))
            """)
        proc = _env.run([sys.executable, "-c", code, self.path], cwd=self.dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        seen = json.loads(proc.stdout)
        self.assertIn("spawn", seen, "spawn is a start method on every platform")
        for method, windows in seen.items():
            for i, opaque in enumerate(windows):
                with self.subTest(method=method, window=i):
                    self.assertTrue(any(c.startswith("subprocess:") for c in opaque),
                                    f"a {method} worker read a file and no channel named it")
                    self.assertNotIn("network", opaque,
                                     "a forkserver's child is a process, not the network")
                    if method == "forkserver":
                        self.assertIn("subprocess:forkserver", opaque)

    def test_the_fork_exec_probe_records_and_hands_the_call_through(self):
        """V: ``_posixsubprocess.fork_exec`` raises no audit event, so it is
        replaced by a probe. The probe must record the process and the argv
        files on every open window, pass the call through untouched — same
        arguments, same result — and record nothing outside a window. A stub
        stands in for the C function: no process is started here."""
        calls: list = []

        def original(*args, **kwargs):
            calls.append((args, kwargs))
            return 4242

        probe = verdicts._process_probe(original)
        tool = os.path.join(self.dir, "bin", "omc")
        argv = [os.fsencode(tool), b"--check", os.fsencode(self.path), b"--not-a-file"]
        args = (argv, [os.fsencode(tool)], True, (), self.dir, None) + (-1,) * 8
        outer, inner = GateTrace(), GateTrace()
        with tracing(outer):
            with tracing(inner):
                self.assertEqual(probe(*args), 4242)
        self.assertEqual(calls, [(args, {})], "the call must reach the original as made")
        for trace in (outer, inner):
            self.assertEqual(trace.opaque, {"subprocess:omc"})
            self.assertEqual(trace.files_read, [self.path])

        after = GateTrace()
        with tracing(after):
            pass
        self.assertEqual(probe(*args), 4242)
        self.assertEqual((after.opaque, after.files_read), (set(), []))

        try:
            posix = importlib.import_module("_posixsubprocess")
        except ImportError:                    # Windows: CreateProcess audits itself
            posix = None
        if posix is not None:
            self.assertTrue(getattr(posix.fork_exec, "__atompipe_probe__", False),
                            "the probe must be installed where the function exists")

    def test_winapi_create_process_is_opaque_and_names_its_argv_files(self):
        """V: on Windows every process multiprocessing starts — spawn is the
        only method there — goes through ``_winapi.CreateProcess``, which
        raises its own audit event, and no handler took it. Driven through
        ``sys.audit`` so the whole hook runs, on any platform."""
        trace = GateTrace()
        with tracing(trace):
            sys.audit("_winapi.CreateProcess", sys.executable,
                      f'"{sys.executable}" -c pass "{self.path}" --not-a-file', self.dir)
            sys.audit("_winapi.CreateProcess", None, "omc.exe --check", None)
        self.assertEqual(trace.opaque, {f"subprocess:{os.path.basename(sys.executable)}",
                                        "subprocess:omc.exe"})
        self.assertEqual(trace.files_read, [self.path])

    def test_nested_traces_both_receive_the_event(self):
        outer, inner = GateTrace(), GateTrace()
        later = self.file("later.csv")
        with tracing(outer):
            with tracing(inner):
                open(self.path, "rb").close()
            open(later, "rb").close()
        self.assertEqual(outer.files_read, [self.path, later])
        self.assertEqual(inner.files_read, [self.path])

    def test_a_worker_thread_is_routed_too(self):
        trace = GateTrace()
        with tracing(trace):
            worker = threading.Thread(target=lambda: open(self.path, "rb").close())
            worker.start()
            worker.join()
        self.assertEqual(trace.files_read, [self.path],
                         "thread-local routing would miss a gate's worker threads")

    def test_the_hook_never_raises_on_odd_arguments(self):
        trace = GateTrace()
        odd = [
            ("open", ()), ("open", (object(), None, None)), ("open", (b"\xff\xfe", "r", 0)),
            ("open", ("", 1, "x")), ("open", ("a\x00b", "r", 0)),
            ("os.listdir", (3,)), ("os.listdir", ()), ("os.scandir", (object(),)),
            ("os.rename", ("only-one",)), ("os.replace", (None, 5, -1, -1)),
            ("subprocess.Popen", (None, None, None, None)), ("subprocess.Popen", ()),
            ("subprocess.Popen", (b"/bin/x", b"/bin/x --flag", "/nonexistent", {})),
            ("os.system", (b"\xff",)), ("os.exec", (1, 2, 3)), ("os.spawn", (None,)),
            ("os.posix_spawn", ("p", object(), None)),
            ("socket.connect", ()), ("socket.connect", (None, None)),
            ("_winapi.CreateProcess", ()), ("_winapi.CreateProcess", (None, None, None)),
            ("_winapi.CreateProcess", (b"\xff", '"unterminated', 5)),
            ("_winapi.CreateProcess", (object(), object(), object())),
            ("_posixsubprocess.fork_exec", ()),
            ("_posixsubprocess.fork_exec", (object(), object(), None, None, 3)),
            ("_posixsubprocess.fork_exec", ([b"\xff"], [], True, (), b"\x00")),
        ]
        with tracing(trace):
            for event, args in odd:
                with self.subTest(event=event, args=repr(args)[:60]):
                    verdicts._audit(event, args)
            sys.audit("open", object(), 5, "not-flags")
            sys.audit("os.listdir", None)

    def test_the_hook_is_not_reentered_by_its_own_work(self):
        """`sys._getframe` audits itself; a hook that re-enters on its own events
        recursed to the limit and aborted the `open` it was auditing."""
        calls: list = []

        def handler(traces, args):
            calls.append(args[0])
            with open(self.path, "rb"):             # a handled event, from inside the hook
                pass

        with mock.patch.dict(verdicts._HANDLERS, {"open": handler}):
            with tracing(GateTrace()):
                open(self.path, "rb").close()
        self.assertEqual(len(calls), 1)

    def test_the_hook_is_installed_once(self):
        with tracing(GateTrace()):
            with tracing(GateTrace()):
                pass
        with tracing(GateTrace()):
            pass
        self.assertEqual(verdicts._HOOK_INSTALLS, 1)
        code = ("from atompipe import verdicts as v\n"
                "print(v._HOOK_INSTALLS)\n"
                "for _ in range(3):\n"
                "    with v.tracing(v.GateTrace()):\n"
                "        pass\n"
                "with v.tracing(v.GateTrace()):\n"
                "    with v.tracing(v.GateTrace()):\n"
                "        pass\n"
                "print(v._HOOK_INSTALLS)\n")
        proc = _env.run([sys.executable, "-c", code], cwd=self.dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(), ["0", "1"],
                         "installed lazily on the first push, and never again")

    def test_nothing_outside_a_window_is_recorded(self):
        trace = GateTrace()
        with tracing(trace):
            pass
        open(self.path, "rb").close()
        os.path.isfile(self.path)
        self.assertEqual(trace.files_read, [])
        self.assertEqual(trace.stats, [])
        self.assertEqual(verdicts._STACK, [])

    # -- the stat probes -------------------------------------------------- #
    def test_every_existence_kind_and_size_question_is_recorded(self):
        """V: ``os.stat`` raises no audit event, and every existence, kind and
        size question the standard library asks ends in it (or in ``os.lstat``)
        — so a gate that skipped a named input because ``os.path.isfile`` said
        no had recorded nothing, and its PASS outlived the file appearing
        (bundled ``modelica.source_hygiene``, review round 1). Each spelling,
        asked of a path that is not there, must land on ``trace.stats`` — and
        none is a read of bytes."""
        missing = os.path.join(self.dir, "missing.txt")
        spellings = {
            "os.stat": os.stat,
            "os.stat(follow_symlinks=False)": lambda p: os.stat(p, follow_symlinks=False),
            "os.lstat": os.lstat,
            "os.path.exists": os.path.exists,
            "os.path.lexists": os.path.lexists,
            "os.path.isfile": os.path.isfile,
            "os.path.isdir": os.path.isdir,
            "os.path.islink": os.path.islink,
            "os.path.getsize": os.path.getsize,
            "os.path.getmtime": os.path.getmtime,
            "os.path.samefile": lambda p: os.path.samefile(p, self.path),
            "Path.exists": lambda p: pathlib.Path(p).exists(),
            "Path.is_file": lambda p: pathlib.Path(p).is_file(),
            "Path.is_dir": lambda p: pathlib.Path(p).is_dir(),
            "Path.stat": lambda p: pathlib.Path(p).stat(),
            "glob of a literal path": glob.glob,
            "bytes": lambda p: os.path.exists(os.fsencode(p)),
        }
        for name, ask in spellings.items():
            with self.subTest(spelling=name):
                trace = GateTrace()
                with tracing(trace):
                    try:
                        ask(missing)
                    except OSError:
                        pass
                self.assertIn(missing, trace.stats)
                self.assertTrue(all(isinstance(p, str) for p in trace.stats))
                self.assertEqual(trace.files_read, [], "a stat is not a read of bytes")

    def test_a_relative_stat_and_nested_traces(self):
        outer, inner = GateTrace(), GateTrace()
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            with tracing(outer):
                with tracing(inner):
                    os.path.exists("relative.txt")
        finally:
            os.chdir(cwd)
        where = os.path.join(self.dir, "relative.txt")
        self.assertEqual((outer.stats, inner.stats), ([where], [where]))

    def test_the_probes_answer_exactly_as_the_originals(self):
        """A probe records and gets out of the way: same result, same errors,
        keyword arguments passed through, and an fd (no path) records nothing."""
        trace = GateTrace()
        link = os.path.join(self.dir, "link")
        with contextlib.suppress(OSError, NotImplementedError):
            os.symlink(self.path, link)
        with tracing(trace):
            self.assertEqual(os.stat(self.path).st_size, os.path.getsize(self.path))
            if os.path.islink(link):
                self.assertNotEqual(os.stat(link, follow_symlinks=False).st_mode,
                                    os.stat(link).st_mode)
            with self.assertRaises(FileNotFoundError):
                os.stat(os.path.join(self.dir, "nope"))
            with open(self.path, "rb") as fh:
                os.stat(fh.fileno())
        self.assertIn(self.path, trace.stats)
        self.assertNotIn(None, trace.stats)

    def test_the_hooks_own_questions_are_not_the_gates(self):
        """The hook asks ``os.path.isdir`` of an ``os.open`` path and
        ``os.path.isfile`` of a child's argv: its own work, never the gate's."""
        trace = GateTrace()
        with tracing(trace):
            os.close(os.open(self.path, os.O_RDONLY))
        self.assertEqual(trace.files_read, [self.path])
        self.assertEqual(trace.stats, [], "the hook's own isdir was filed as the gate's")

    def test_a_stat_of_its_own_output_is_not_an_input(self):
        out = os.path.join(self.dir, "out.png")
        trace = GateTrace()
        with tracing(trace):
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("x")
            os.path.getsize(out)
        self.assertEqual(trace.stats, [], "written first, then asked about: its own output")


if __name__ == "__main__":
    unittest.main()
