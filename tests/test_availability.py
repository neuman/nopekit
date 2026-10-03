# SPDX-License-Identifier: Apache-2.0
"""Availability: the one place that decides a gate's tooling is missing.

A skip is honest only when the tooling really is absent, and the only judge of
that is ``gates.availability`` — never the gate's own opinion of why it skipped.
So a gate that needs ANY ONE of several unrelated things must be able to say so
in its declaration, or it has to probe for them in its own body and skip there,
where availability cannot see it.

That is what got through. ``cad.clash`` needs a boolean engine — manifold3d, a
headless Blender, or OpenSCAD — and the declaration could only AND its
requirements, so the disjunction lived in the body. On a machine with trimesh
but no engine, availability said yes and the gate skipped its own baseline and
its own negative control: a control that never fired, counted as honestly
blocked.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import dataclasses
import importlib.util
import os
import unittest
from unittest import mock

from atompipe import gates as gates_mod
from atompipe import packs as packs_mod
from atompipe.models import GateSpec, NegativeControl

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENGINES = ["python:manifold3d", "tool:blender", "tool:openscad"]
NONE_FOUND = ("requires one of python manifold3d, tool blender, tool openscad "
              "(none found)")

_REAL_FIND_SPEC = importlib.util.find_spec


def _world(*, modules=(), tools=()):
    """Patch the two probes availability uses so exactly ``modules`` import and
    exactly ``tools`` are on PATH — among the names this test asks about. Every
    other module resolves for real, so a real ``requires_python`` still works."""
    asked = {"manifold3d", "atompipe_absent_mod_3b9e"}

    def find_spec(name, *args, **kwargs):
        if name in asked:
            return object() if name in modules else None
        return _REAL_FIND_SPEC(name, *args, **kwargs)

    def which(name, *args, **kwargs):
        return f"/opt/fake/bin/{name}" if name in tools else None

    return (mock.patch.object(gates_mod.importlib.util, "find_spec", side_effect=find_spec),
            mock.patch.object(gates_mod.shutil, "which", side_effect=which))


def _spec(**kw):
    return GateSpec(id="g.boolean", claims=["C1"],
                    negative_control=NegativeControl(fixture="x:y"), **kw)


class ToolingDisjunction(unittest.TestCase):

    def _availability(self, spec, **world):
        find, which = _world(**world)
        with find, which:
            return gates_mod.availability(spec)

    def test_none_present(self):
        ok, reason = self._availability(_spec(requires_one_of=list(ENGINES)))
        self.assertFalse(ok)
        self.assertEqual(reason, NONE_FOUND)

    def test_one_present(self):
        for world in ({"modules": ("manifold3d",)}, {"tools": ("blender",)},
                      {"tools": ("openscad",)}):
            with self.subTest(**world):
                self.assertEqual(
                    self._availability(_spec(requires_one_of=list(ENGINES)), **world),
                    (True, ""))

    def test_all_present(self):
        self.assertEqual(
            self._availability(_spec(requires_one_of=list(ENGINES)),
                               modules=("manifold3d",), tools=("blender", "openscad")),
            (True, ""))

    def test_an_empty_disjunction_requires_nothing(self):
        self.assertEqual(self._availability(_spec()), (True, ""))

    def test_the_disjunction_is_anded_with_the_other_requirements(self):
        """Any one engine does not excuse a missing required tool, and every
        missing thing is named at once."""
        spec = _spec(requires_tools=["atompipe-no-such-tool-7c1f"],
                     requires_python=["atompipe_absent_mod_3b9e"],
                     requires_one_of=list(ENGINES))
        ok, reason = self._availability(spec)
        self.assertFalse(ok)
        self.assertIn("atompipe-no-such-tool-7c1f", reason)
        self.assertIn("atompipe_absent_mod_3b9e", reason)
        self.assertIn(NONE_FOUND, reason)
        ok, reason = self._availability(spec, tools=("blender",))
        self.assertFalse(ok)
        self.assertNotIn("requires one of", reason)

    def test_a_malformed_entry_is_named_not_guessed(self):
        """``manifold3d`` with no kind could be a module or an executable; guessing
        would make one spelling work by accident on some machines."""
        ok, reason = self._availability(_spec(requires_one_of=["manifold3d", "pip:foo"]),
                                        modules=("manifold3d",), tools=("manifold3d",))
        self.assertFalse(ok)
        self.assertIn("'manifold3d'", reason)
        self.assertIn("'pip:foo'", reason)
        self.assertIn("python:<module> or tool:<executable>", reason)

    def test_it_is_the_last_field_and_round_trips(self):
        """After every field it was added beside, so positional construction of
        every existing GateSpec still works. It was the last field until P2.2
        appended ``needs`` after it (moved under R-6: the property is "nothing
        new is inserted before an old field", and ``needs`` is held to the
        same — the last field now)."""
        names = [f.name for f in dataclasses.fields(GateSpec)]
        self.assertEqual(names[-2:], ["requires_one_of", "needs"])
        spec = GateSpec.from_dict({"id": "g.x", "requires_one_of": list(ENGINES)})
        self.assertEqual(spec.requires_one_of, ENGINES)
        self.assertEqual(GateSpec.from_dict(spec.to_dict()).requires_one_of, ENGINES)
        self.assertEqual(GateSpec(id="g.y").requires_one_of, [])

    def test_the_decorator_declares_it_and_the_registry_keeps_its_own_copy(self):
        reg = gates_mod.Registry()
        engines = list(ENGINES)
        gates_mod.gate(id="g.decl", claims=["C1"], registry=reg, requires_one_of=engines,
                       negative_control=NegativeControl(fixture="x:y"))(lambda ctx: True)
        engines.append("tool:anything")
        reg.get("g.decl")[0].requires_one_of.append("tool:anything")
        self.assertEqual(reg.get("g.decl")[0].requires_one_of, ENGINES)

    def test_a_gate_with_no_engine_skips_before_it_runs(self):
        """The skip comes from availability, so the gate body is never reached."""
        reg = gates_mod.Registry()
        ran = []
        gates_mod.gate(id="g.decl", claims=["C1"], registry=reg,
                       requires_one_of=list(ENGINES),
                       negative_control=NegativeControl(fixture="x:y"))(
            lambda ctx: ran.append(1) or True)
        spec, fn = reg.get("g.decl")
        find, which = _world()
        with find, which:
            v = gates_mod.run_gate(spec, fn, gates_mod.GateContext())
        self.assertTrue(v.skipped)
        self.assertEqual(v.skip_reason, NONE_FOUND)
        self.assertEqual(ran, [])

    def test_cad_clash_declares_its_engines(self):
        """The bundled case: with no engine, availability says so — in CI (no
        trimesh either) and on a machine with trimesh alone."""
        reg = gates_mod.Registry()
        packs_mod.load_gates("cad-solid", reg, root=REPO)
        spec, _fn = reg.get("cad.clash")
        self.assertEqual(spec.requires_one_of, ENGINES)
        ok, reason = self._availability(spec)
        self.assertFalse(ok)
        self.assertIn(NONE_FOUND, reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
