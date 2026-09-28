# SPDX-License-Identifier: Apache-2.0
"""The recording loader: fresh bytes, a recorded closure, a content-keyed cache.

What slipped through without it (S-26): the model entry was already compiled
from its source every time (`modelio._FreshLoader`), and nothing else was. Gate
modules, pack helpers and model siblings went through the stock import
machinery, which trusts a `.pyc` whose recorded source size and whole-second
mtime still match. `7.0` and `8.0` are the same size and a tier-0 loop re-runs
inside the same second, so the source said 8.0 and the verdict came from 7.0 —
and an in-process `sys.modules` hit served the old module with no pyc involved
at all. A per-gate code digest taken from the file on disk would then key the
old behaviour under the new bytes.

What slipped through beside it (S-28): the rule that flattens a projection into
`GateContext.params` existed twice, in `cli` and `site`, "byte-for-byte" and
kept in sync by a comment. `modelio.flat_params` is the one copy; the other two
go when their callers move onto it (U21, U22). `FlatParamsIsTheOldShape` pins
the old output so the move is a refactor, not a behaviour change.

Every scenario here edits files in-process on purpose: the content-keyed cache
IS the in-process defence, so it is what is under test. Each test builds its own
tree under the temp dir with salted module names and removes every module it
loaded, so nothing leaks into the next test or the rest of the suite.

Run:  PYTHONPATH=src python3 -m unittest tests.test_codeload -v
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import py_compile
import shutil
import sys
import tempfile
import textwrap
import unittest
import uuid

from atompipe import gates, modelio
from atompipe.models import NegativeControl


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Sandbox(unittest.TestCase):
    """A project root under the temp dir, salted names, and a clean exit."""

    def setUp(self) -> None:
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="atompipe-codeload-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "proj")
        os.makedirs(self.root)
        self.salt = uuid.uuid4().hex[:10]
        self.addCleanup(self._forget)

    # -- building the tree ------------------------------------------------- #
    def n(self, stem: str) -> str:
        """A module name no other test (or run) can collide with."""
        return f"{stem}_{self.salt}"

    def put(self, rel: str, text: str, *, base: str | None = None) -> str:
        path = os.path.join(base or self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(text).lstrip("\n"))
        importlib.invalidate_caches()        # a FileFinder listing from this same tick
        return path

    def on_path(self, directory: str) -> None:
        sys.path.insert(0, directory)
        self.addCleanup(lambda: directory in sys.path and sys.path.remove(directory))

    def edit_same_size(self, path: str, old: str, new: str) -> None:
        """Rewrite `old` -> `new` in place and put the mtime back: S-26's edit."""
        self.assertEqual(len(old), len(new), "the scenario needs a same-size edit")
        before = os.stat(path)
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(old, new))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.stat(path)
        self.assertEqual((after.st_size, after.st_mtime_ns),
                         (before.st_size, before.st_mtime_ns))

    def stale_pyc(self, path: str) -> str:
        """Leave the pyc the stock loader would trust beside `path`."""
        pyc = importlib.util.cache_from_source(path)
        py_compile.compile(path, doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        self.assertTrue(os.path.isfile(pyc))
        return pyc

    # -- loading ----------------------------------------------------------- #
    def load(self, rel: str, *, name: str | None = None, registry=None,
             roots=None):
        path = os.path.join(self.root, rel)
        stem = os.path.splitext(os.path.basename(rel))[0]
        return modelio.load_source_module(
            path, name=name or f"codeload_{self.salt}_{stem}",
            roots=roots or [self.root], registry=registry)

    def closure(self, obj) -> modelio.CodeClosure:
        closure = modelio.code_closure(obj)
        self.assertIsInstance(closure, modelio.CodeClosure,
                              f"no closure recorded for {obj!r}")
        return closure

    def files(self, obj) -> dict[str, str]:
        return dict(self.closure(obj).files)

    def _forget(self) -> None:
        """Remove every module, finder cache and path entry this test created."""
        tmp = self.tmp + os.sep
        for name, module in list(sys.modules.items()):
            where = [getattr(module, "__file__", None) or ""]
            where += list(getattr(module, "__path__", None) or [])
            if any(isinstance(w, str) and os.path.abspath(w).startswith(tmp) for w in where):
                sys.modules.pop(name, None)
        for key in list(sys.path_importer_cache):
            if isinstance(key, str) and os.path.abspath(key).startswith(tmp):
                sys.path_importer_cache.pop(key, None)
        # A planted module may extend sys.path itself, as the bundled fixtures do.
        sys.path[:] = [entry for entry in sys.path
                       if not (isinstance(entry, str) and entry
                               and os.path.abspath(entry).startswith(tmp))]


class RecordingLoader(_Sandbox):

    # -- S-26: the bytes that run are the bytes on disk -------------------- #
    def test_same_size_same_second_edit_runs_new_bytes(self):
        path = self.put("thick.py", "VALUE = 7.0\n")
        pyc = self.stale_pyc(path)
        with open(pyc, "rb") as fh:
            pyc_bytes = fh.read()
        name = self.n("thick")

        first = self.load("thick.py", name=name)
        self.assertEqual(first.VALUE, 7.0)

        self.edit_same_size(path, "7.0", "8.0")

        # The negative control: the stock loader, handed the same file, runs the
        # stale pyc. If it ever stops doing so the scenario is gone and the
        # assertions below would pass for a loader that fixes nothing.
        stock_spec = importlib.util.spec_from_file_location(self.n("stock"), path)
        stock = importlib.util.module_from_spec(stock_spec)
        stock_spec.loader.exec_module(stock)
        self.assertEqual(stock.VALUE, 7.0,
                         "the stock loader no longer serves the stale pyc, so this "
                         "test no longer demonstrates S-26")

        second = self.load("thick.py", name=name)
        self.assertEqual(second.VALUE, 8.0,
                         "an in-process reload ran the old module (S-26)")
        self.assertIsNot(second, first)
        self.assertEqual(self.files(second)[path], _sha(b"VALUE = 8.0\n"),
                         "the closure must digest the bytes that were compiled")

        # A first load under a fresh name, with the stale pyc still beside the
        # source, compiles the source too: no pyc is ever read...
        third = self.load("thick.py", name=self.n("thick_again"))
        self.assertEqual(third.VALUE, 8.0)
        # ...and none is ever written.
        with open(pyc, "rb") as fh:
            self.assertEqual(fh.read(), pyc_bytes, "the loader rewrote a pyc")
        other = self.put("never_compiled.py", "X = 1\n")
        self.load("never_compiled.py")
        self.assertFalse(os.path.exists(importlib.util.cache_from_source(other)),
                         "the loader wrote a pyc")

    def test_an_unchanged_closure_is_served_from_the_cache(self):
        self.put("steady.py", "import random\nTOKEN = random.random()\n")
        name = self.n("steady")
        first = self.load("steady.py", name=name)
        second = self.load("steady.py", name=name)
        self.assertIs(second, first, "an unchanged module was executed twice")
        self.assertEqual(second.TOKEN, first.TOKEN)

    def test_file_is_the_absolute_path(self):
        path = self.put("where.py", "X = 1\n")
        module = modelio.load_source_module(os.path.relpath(path), name=self.n("where"),
                                            roots=[self.root])
        self.assertEqual(module.__file__, os.path.abspath(path))

    # -- the closure: helpers however they arrive -------------------------- #
    def test_a_helper_imported_by_name_is_recorded(self):
        self.on_path(self.root)
        helper = self.put(f"{self.n('helper')}.py", "VALUE = 3\n")
        gate = self.put("gate.py", f"import {self.n('helper')} as h\nX = h.VALUE\n")
        module = self.load("gate.py")
        files = self.files(module)
        self.assertEqual(set(files), {gate, helper})
        with open(helper, "rb") as fh:
            self.assertEqual(files[helper], _sha(fh.read()))
        self.assertEqual(self.closure(module).fallback, "")
        self.assertEqual(self.files(sys.modules[self.n("helper")]), {helper: files[helper]},
                         "the helper carries its own closure for the next importer")

    def test_a_cached_helper_is_attributed_to_a_second_module(self):
        self.on_path(self.root)
        helper_name = self.n("shared")
        helper = self.put(f"{helper_name}.py", "def fold(x):\n    return x\n")
        self.put("first.py", f"import {helper_name}\n")
        self.put("by_module.py", f"import {helper_name} as shared\n")
        self.put("by_name.py", f"from {helper_name} import fold\n")
        # No import statement names it, so only the globals walk can see it.
        self.put("by_lookup.py", f"import importlib\nH = importlib.import_module({helper_name!r})\n")

        self.load("first.py")
        cached = sys.modules[helper_name]
        by_module = self.load("by_module.py")      # fires no import event at all
        by_name = self.load("by_name.py")          # binds a function, not the module
        by_lookup = self.load("by_lookup.py")
        self.assertIs(sys.modules[helper_name], cached, "the helper ran twice")
        self.assertIn(helper, self.files(by_module))
        self.assertIn(helper, self.files(by_name))
        self.assertIn(helper, self.files(by_lookup))

    def test_a_helper_through_a_namespace_package_is_attributed(self):
        self.on_path(self.root)
        package = self.n("gatespkg")                # no __init__.py: a namespace package
        helper = self.put(f"{package}/_analytic.py", "def name():\n    return 1\n")
        self.put("flow.py", f"from {package}._analytic import name\n")
        self.put("hydro.py", f"from {package}._analytic import name\n")
        flow = self.load("flow.py")
        hydro = self.load("hydro.py")               # the second one hits the cache
        for module in (flow, hydro):
            closure = self.closure(module)
            self.assertIn(helper, dict(closure.files))
            self.assertEqual(closure.fallback, "",
                             "a namespace package resolves to nothing; it is not an "
                             "unmappable import")

    def test_a_by_path_helper_is_attributed_to_its_caller(self):
        helper = self.put("sub/fold.py", "LIMIT = 4\n")
        caller = """
            import os
            from atompipe import modelio
            FOLD = modelio.load_path(os.path.join(os.path.dirname(__file__), "sub", "fold.py"))
            """
        self.put("mesh.py", caller)
        self.put("printability.py", caller)
        stock = f"""
            import importlib.util, os, sys
            _spec = importlib.util.spec_from_file_location(
                "{self.n('stock_fold')}", os.path.join(os.path.dirname(__file__), "sub", "fold.py"))
            FOLD = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(FOLD)
            """
        self.put("by_stock_loader.py", stock)

        mesh = self.load("mesh.py")
        printability = self.load("printability.py")
        self.assertIn(helper, self.files(mesh))
        self.assertIn(helper, self.files(printability))
        self.assertIs(mesh.FOLD, printability.FOLD, "two copies of one helper (packs:H4)")
        # A helper some other machinery loaded by path is still attributed: the
        # globals walk finds the module object and digests its file.
        self.assertIn(helper, self.files(self.load("by_stock_loader.py")))

    def test_load_path_names_are_path_salted(self):
        one = self.put("a/twin.py", "WHO = 'a'\n")
        two = self.put("b/twin.py", "WHO = 'b'\n")
        first = modelio.load_path(one)
        second = modelio.load_path(two)
        self.assertEqual((first.WHO, second.WHO), ("a", "b"),
                         "a second copy ran the first copy's helper (packs:H4)")
        self.assertEqual(first.__name__,
                         "atompipe_path_twin_" + _sha(os.fsencode(one))[:12])
        self.assertEqual(first.__file__, one)
        self.assertIs(modelio.load_path(one), first)

    def test_a_lazy_local_import_is_attributed_without_importing_it(self):
        self.on_path(self.root)
        lazy_name = self.n("lazyhelper")
        helper = self.put(f"{lazy_name}.py", "X = 1\n")
        self.put("gate.py", f"""
            def run():
                import {lazy_name}
                return {lazy_name}.X
            """)
        module = self.load("gate.py")
        self.assertNotIn(lazy_name, sys.modules, "the static supplement imported it")
        self.assertIn(helper, self.files(module))

    # -- the fallback ------------------------------------------------------ #
    def test_dataclass_does_not_trigger_the_fallback(self):
        self.put("shapes.py", """
            import collections, dataclasses, enum, typing

            @dataclasses.dataclass(frozen=True)
            class Part:
                name: str = "bracket"
                mass_g: float = 0.0

            Pair = collections.namedtuple("Pair", "a b")

            class Row(typing.NamedTuple):
                gate: str
                ok: bool

            class Kind(enum.Enum):
                A = 1
            """)
        closure = self.closure(self.load("shapes.py"))
        self.assertEqual(closure.fallback, "",
                         "stdlib code generation is not computed source in the pack")

    def test_exec_of_computed_source_triggers_the_fallback(self):
        bystander = self.put("unrelated.py", "Z = 0\n")
        gate = self.put("generated.py", """
            SOURCE = "Y = " + "2"
            exec(compile(SOURCE, "<generated>", "exec"))
            """)
        closure = self.closure(self.load("generated.py"))
        self.assertTrue(closure.fallback.startswith("computed source at generated.py:"),
                        closure.fallback)
        self.assertEqual(set(dict(closure.files)), {gate, bystander},
                         "the fallback digests every *.py under the owning root")

    def test_exec_of_computed_source_beside_the_root_triggers_the_fallback(self):
        # A helper beside the root is code (the `mono` repro), so computed source
        # it executes is invisible code too: its directory is walked, as a root is.
        shared = os.path.join(self.tmp, "shared")
        name = self.n("rulebook")
        rules = self.put("rules_src.py", "LIMIT = 3\n", base=shared)
        self.put(f"{name}.py", """
            import os
            with open(os.path.join(os.path.dirname(__file__), "rules_src.py")) as fh:
                exec(compile(fh.read(), "<rules>", "exec"))
            """, base=shared)
        self.on_path(shared)
        self.put("gate.py", f"import {name}\nX = {name}.LIMIT\n")
        closure = self.closure(self.load("gate.py"))
        self.assertTrue(closure.fallback.startswith(f"computed source at {name}.py:"),
                        closure.fallback)
        self.assertIn(rules, dict(closure.files),
                      "the fallback walks the helper's directory, where its source came from")

    def test_an_import_that_is_not_source_triggers_the_fallback(self):
        self.on_path(self.root)
        compiled = self.n("compiled")
        source = self.put(f"src_{compiled}.py", "X = 1\n")
        py_compile.compile(source, cfile=os.path.join(self.root, f"{compiled}.pyc"),
                           doraise=True)
        os.remove(source)
        importlib.invalidate_caches()
        self.put("gate.py", f"import {compiled}\n")
        closure = self.closure(self.load("gate.py"))
        self.assertIn(compiled, closure.fallback)

    # -- the content-keyed cache ------------------------------------------- #
    def test_a_helper_edit_reexecutes_the_module_and_purges_the_helper(self):
        self.on_path(self.root)
        helper_name = self.n("process_model")
        helper = self.put(f"{helper_name}.py", "RATE = 7\n")
        self.stale_pyc(helper)
        self.put("gate.py", f"import {helper_name}\nSEEN = {helper_name}.RATE\n")
        name = self.n("gate")

        first = self.load("gate.py", name=name)
        old_helper = sys.modules[helper_name]
        self.assertEqual(first.SEEN, 7)

        self.edit_same_size(helper, "7", "8")
        second = self.load("gate.py", name=name)
        self.assertIsNot(second, first, "a helper edit did not re-execute the gate module")
        self.assertEqual(second.SEEN, 8)
        self.assertIsNot(sys.modules[helper_name], old_helper,
                         "the stale helper was not purged from sys.modules")

    def test_a_second_importer_never_gets_a_stale_helper(self):
        # The first importer is never reloaded here, so nothing re-executes on
        # its account: only a purge before the second importer runs keeps the
        # cached helper object from answering with the old value.
        self.on_path(self.root)
        helper_name = self.n("fold")
        helper = self.put(f"{helper_name}.py", "LIMIT = 4\n")
        self.put("first.py", f"import {helper_name}\nSEEN = {helper_name}.LIMIT\n")
        self.put("second.py", f"import {helper_name}\nSEEN = {helper_name}.LIMIT\n")
        self.assertEqual(self.load("first.py").SEEN, 4)
        self.edit_same_size(helper, "4", "5")
        self.assertEqual(self.load("second.py").SEEN, 5,
                         "a helper edited after its first importer loaded was served "
                         "stale to the second (S-26, in-process)")

    # -- gates: recorded, re-adopted ---------------------------------------- #
    def _gate_source(self, gate_id: str, verdict: str) -> str:
        return textwrap.dedent(f"""
            from atompipe.gates import gate
            from atompipe.models import NegativeControl

            @gate(id="{gate_id}", negative_control=NegativeControl(
                fixture="selftest/bad.py", note="planted"))
            def check(ctx):
                return {verdict}
            """)

    def test_gates_are_readopted_into_a_fresh_registry(self):
        gate_id = f"codeload.readopt_{self.salt}"
        path = self.put("gatemod.py", self._gate_source(gate_id, "True "))
        name = self.n("gatemod")

        first_registry = gates.Registry()
        with gates.use_registry(first_registry):
            module = self.load("gatemod.py", name=name, registry=first_registry)
        self.assertEqual(first_registry.ids(), [gate_id])
        self.assertEqual(module.__atompipe_gates__[0][0].id, gate_id)

        fresh = gates.Registry()
        with gates.use_registry(fresh):
            again = self.load("gatemod.py", name=name, registry=fresh)
        self.assertIs(again, module, "re-adoption must not re-execute the module")
        self.assertEqual(fresh.ids(), [gate_id], "the fresh registry got no gates")
        self.assertIs(fresh.get(gate_id)[1], module.check)

        # An edit re-executes the module; the stale registration is dropped first,
        # so neither registry reports the gate as registered twice.
        self.edit_same_size(path, "True ", "False")
        with gates.use_registry(first_registry):
            edited = self.load("gatemod.py", name=name, registry=first_registry)
        self.assertIsNot(edited, module)
        self.assertIs(first_registry.get(gate_id)[1], edited.check)
        with gates.use_registry(fresh):
            self.load("gatemod.py", name=name, registry=fresh)
        self.assertEqual(fresh.ids(), [gate_id])
        self.assertIs(fresh.get(gate_id)[1], edited.check,
                      "the fresh registry kept the old function")
        self.assertIs(edited.check(None), False)

    def test_a_module_that_fails_leaves_nothing_behind(self):
        gate_id = f"codeload.broken_{self.salt}"
        self.put("broken.py", self._gate_source(gate_id, "True") + "\nraise RuntimeError('boom')\n")
        name = self.n("broken")
        registry = gates.Registry()
        with gates.use_registry(registry), self.assertRaises(RuntimeError):
            self.load("broken.py", name=name, registry=registry)
        self.assertNotIn(name, sys.modules)
        self.assertEqual(registry.ids(), [], "a module that failed to import registered a gate")

    # -- what the closure names beyond files -------------------------------- #
    def test_third_party_list_is_static_and_order_independent(self):
        self.on_path(self.root)
        # Outside the root AND installed: a site-packages tree, as pip leaves one.
        # A plain directory outside the root is code, not third-party (the
        # `mono` repro, test_a_helper_beside_the_root_is_code_not_third_party).
        vendor = os.path.join(self.tmp, "vendor", "site-packages")
        alpha, beta = self.n("alpha"), self.n("beta")
        missing = self.n("not_installed")
        self.put(f"{alpha}.py", "THING = 1\n", base=vendor)
        self.put(f"{beta}/__init__.py", "", base=vendor)
        self.put(f"{beta}/sub.py", "", base=vendor)
        self.on_path(vendor)
        local = self.n("localhelper")
        self.put(f"{local}.py", "X = 1\n")

        self.put("one.py", f"""
            import json, os
            import {local}
            def run():
                import {alpha}
                import {beta}.sub
                import {missing}
            """)
        self.put("two.py", f"""
            import {local}
            from collections import OrderedDict
            def first():
                from {beta} import sub
            def second():
                from {alpha} import THING
                import {missing}
            import atompipe.models
            """)
        expected = tuple(sorted((alpha, beta, missing)))
        in_order = (self.closure(self.load("one.py", name=self.n("one_a"))).third_party,
                    self.closure(self.load("two.py", name=self.n("two_a"))).third_party)
        self.assertNotIn(alpha, sys.modules, "the third-party list imported something")
        self.assertNotIn(beta, sys.modules, "the third-party list imported something")

        # The other order, with the stand-ins actually imported first: a list
        # built from import events would now come out different.
        importlib.import_module(beta)
        importlib.import_module(alpha)
        reversed_order = (self.closure(self.load("two.py", name=self.n("two_b"))).third_party,
                          self.closure(self.load("one.py", name=self.n("one_b"))).third_party)
        self.assertEqual(set(in_order + reversed_order), {expected})

    def test_a_helper_beside_the_root_is_code_not_third_party(self):
        """V: the review's ``mono`` repro, at the loader. A monorepo's
        ``shared/`` sits beside the project: under no root and under none of the
        interpreter's trees. The recording finder declined every import from it,
        so the stock loader ran it from its pyc, the closure left it out, the
        static pass listed its name as third-party, and a verdict filed it as an
        instrument (``unknown``) that is never part of rho: an edit to it moved
        nothing, and a same-second edit ran the old bytecode (S-26 again)."""
        shared = os.path.join(self.tmp, "shared")
        name, lazy = self.n("beamlib"), self.n("lazylib")
        helper = self.put(f"{name}.py", "ALLOWABLE = 5.0\n", base=shared)
        lazy_helper = self.put(f"{lazy}.py", "X = 1\n", base=shared)
        self.stale_pyc(helper)
        self.on_path(shared)
        # Beside the root too, but installed: an instrument, never code.
        installed = os.path.join(self.tmp, "venv2", "lib", "site-packages")
        library = self.n("installedlib")
        library_file = self.put(f"{library}.py", "Y = 2\n", base=installed)
        self.on_path(installed)
        self.put("gate.py", f"""
            import {name}
            import {library}
            SEEN = {name}.ALLOWABLE

            def run():
                import {lazy}
                return {lazy}.X
            """)
        self.put("second.py", f"import {name}\nSEEN = {name}.ALLOWABLE\n")
        gate_name = self.n("gate")

        first = self.load("gate.py", name=gate_name)
        self.assertEqual(first.SEEN, 5.0)
        closure = self.closure(first)
        with open(helper, "rb") as fh:
            self.assertEqual(dict(closure.files).get(helper), _sha(fh.read()),
                             "the helper beside the root is not in the closure")
        self.assertIn(lazy_helper, dict(closure.files),
                      "a lazy import beside the root is not in the closure")
        self.assertNotIn(lazy, sys.modules, "the static supplement imported it")
        self.assertEqual(closure.third_party, (library,),
                         "a helper beside the root was listed as third-party, or an "
                         "installed one was not")
        self.assertNotIn(library_file, dict(closure.files),
                         "an installed library was recorded as the gate's code")
        self.assertIn(helper, self.files(sys.modules[name]),
                      "the helper carries its own closure for the next importer")
        # A second importer is served the module from sys.modules, with no
        # import event: it is still that importer's code.
        self.assertIn(helper, self.files(self.load("second.py")))

        self.edit_same_size(helper, "5.0", "0.1")
        # A first importer under a new name has no previous closure to purge by:
        # the stale helper is dropped because it is code, as one under the root is.
        self.assertEqual(self.load("second.py", name=self.n("second_after")).SEEN, 0.1,
                         "a helper beside the root, edited after its first importer "
                         "loaded, was served stale to the next (S-26, in-process)")
        again = self.load("gate.py", name=gate_name)
        self.assertIsNot(again, first, "an edit beside the root did not re-execute the module")
        self.assertEqual(again.SEEN, 0.1, "the helper ran its stale pyc (S-26, beside the root)")
        self.assertEqual(self.files(again)[helper], _sha(b"ALLOWABLE = 0.1\n"))

    def test_a_helper_on_a_path_the_module_adds_is_not_third_party(self):
        # beam, thermal and openmodelica fixtures put `gates/` on sys.path and
        # import a helper by its bare name; that name resolves under the root.
        helper_name = self.n("_physics")
        helper = self.put(f"gates/{helper_name}.py", "K = 1\n")
        self.put("selftest/bad.py", f"""
            import os, sys
            _GATES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gates")
            if _GATES not in sys.path:
                sys.path.insert(0, _GATES)
            import {helper_name}
            def make(ctx):
                import {helper_name} as again
                return ctx
            """)
        closure = self.closure(self.load("selftest/bad.py"))
        self.assertIn(helper, dict(closure.files))
        self.assertEqual(closure.third_party, (), "a local helper was listed as third-party")

    def test_spine_extras_record_an_atompipe_site_import(self):
        self.put("parts.py", """
            from atompipe.gates import gate
            from atompipe.models import Verdict
            from atompipe.site import MOVER_SEPARATOR

            def later():
                from atompipe import util
                return util
            """)
        closure = self.closure(self.load("parts.py"))
        self.assertEqual(closure.spine_extras, ("atompipe.site", "atompipe.util"))
        self.assertEqual(closure.third_party, ())

    def test_the_spine_set_is_the_verdicts_one(self):
        # modelio may not import verdicts (verdicts imports modelio), so it
        # mirrors the set; this is what keeps the mirror from drifting (rule 2).
        from atompipe import verdicts
        self.assertEqual(modelio._SPINE_MODULE_FILES, frozenset(verdicts.SPINE_MODULES))

    def test_code_closure_of_a_function_is_its_modules(self):
        self.put("fns.py", "def make(ctx):\n    return ctx\n")
        module = self.load("fns.py")
        self.assertIs(modelio.code_closure(module.make), modelio.code_closure(module))
        self.assertIsNone(modelio.code_closure(lambda ctx: ctx),
                          "a function from a module this loader never ran has no closure")

    # -- the model --------------------------------------------------------- #
    def test_load_model_runs_fresh_siblings_and_records_them(self):
        geom_name = self.n("geom")
        geom = self.put(f"model/{geom_name}.py", "FACTOR = 3.0\n")
        self.stale_pyc(geom)
        entry = self.put("model/part.py", f"""
            from dataclasses import dataclass
            import {geom_name} as geom

            @dataclass
            class Config:
                width: float = 2.0

            CONFIG = Config()

            def build(c):
                return {{"area": c.width * geom.FACTOR}}
            """)
        first = modelio.project(modelio.load_model(self.root, "model/part.py"))
        self.assertEqual(first["derived"]["area"], 6.0)

        self.edit_same_size(geom, "3.0", "4.0")
        model = modelio.load_model(self.root, "model/part.py")
        self.assertEqual(modelio.project(model)["derived"]["area"], 8.0,
                         "a model sibling ran stale code after a same-size edit")
        files = self.files(model.module)
        self.assertEqual(set(files), {entry, geom})
        self.assertEqual(files[geom], _sha(b"FACTOR = 4.0\n"))


# --------------------------------------------------------------------------- #
# C: the one copy of the flattening rule is the old rule (S-28)
# --------------------------------------------------------------------------- #
#: The bracket's projection at thickness 7.0 (examples/bracket at 3278158), in
#: `project()`'s own key order, and `cli._flat_params`'s output on it as ordered
#: pairs — captured by running the old function, not by reading it. Pinned as
#: data, so the pin outlives the two copies it replaces and any later edit to the
#: bracket's build().
_BRACKET_PROJECTION = json.loads(
    '{"config": {"arm_length": 60.0, "width": 30.0, "thickness": 7.0, "hole_d": 5.5, '
    '"n_bolts": 2, "edge_margin": 8.0, "load_n": 15.0, "safety_factor": 2.0, '
    '"material": "petg", "nozzle_d": 0.4, "bed_xy": 220.0, "brim_mm": 8.0}, '
    '"derived": {"material": "petg", "E": 1800.0, "yield": 30.0, "area": 210.0, '
    '"inertia": 857.5, "section_mod": 245.0, "moment_root": 900.0, '
    '"stress_root": 3.673469387755102, "design_stress": 15.0, '
    '"stress_margin": 11.326530612244898, "utilisation": 0.24489795918367346, '
    '"deflection": 0.6997084548104956, "slenderness": 8.571428571428571, '
    '"bearing_area": 77.0, "bearing_stress": 0.19480519480519481, '
    '"min_wall": 1.2000000000000002, "usable_bed": 204.0, "plate_len": 73.5, '
    '"bbox": [73.5, 30.0, 7.0], "bbox_max": 73.5, "mass_g": 19.60245, '
    '"config": {"arm_length": 60.0, "width": 30.0, "thickness": 7.0, "hole_d": 5.5, '
    '"n_bolts": 2, "edge_margin": 8.0, "load_n": 15.0, "safety_factor": 2.0, '
    '"material": "petg", "nozzle_d": 0.4, "bed_xy": 220.0, "brim_mm": 8.0}}}'
)
_BRACKET_OLD_FLAT = json.loads(
    '[["material", "petg"], ["E", 1800.0], ["yield", 30.0], ["area", 210.0], '
    '["inertia", 857.5], ["section_mod", 245.0], ["moment_root", 900.0], '
    '["stress_root", 3.673469387755102], ["design_stress", 15.0], '
    '["stress_margin", 11.326530612244898], ["utilisation", 0.24489795918367346], '
    '["deflection", 0.6997084548104956], ["slenderness", 8.571428571428571], '
    '["bearing_area", 77.0], ["bearing_stress", 0.19480519480519481], '
    '["min_wall", 1.2000000000000002], ["usable_bed", 204.0], ["plate_len", 73.5], '
    '["bbox", [73.5, 30.0, 7.0]], ["bbox_max", 73.5], ["mass_g", 19.60245], '
    '["config", {"arm_length": 60.0, "width": 30.0, "thickness": 7.0, "hole_d": 5.5, '
    '"n_bolts": 2, "edge_margin": 8.0, "load_n": 15.0, "safety_factor": 2.0, '
    '"material": "petg", "nozzle_d": 0.4, "bed_xy": 220.0, "brim_mm": 8.0}], '
    '["arm_length", 60.0], ["width", 30.0], ["thickness", 7.0], ["hole_d", 5.5], '
    '["n_bolts", 2], ["edge_margin", 8.0], ["load_n", 15.0], ["safety_factor", 2.0], '
    '["nozzle_d", 0.4], ["bed_xy", 220.0], ["brim_mm", 8.0]]'
)


def _typed(pairs) -> list:
    """`(key, type name, value)`: `7 == 7.0` and `True == 1`, so equality alone
    would pass a flattening that changed a value's type."""
    return [(k, type(v).__name__, v) for k, v in pairs]


class FlatParamsIsTheOldShape(unittest.TestCase):

    def test_bracket_projection_matches_the_captured_old_output(self):
        flat, conflicts = modelio.flat_params(_BRACKET_PROJECTION)
        self.assertEqual(_typed(flat.items()), _typed(_BRACKET_OLD_FLAT),
                         "derived first, config on top — in that order")
        self.assertEqual(conflicts, [])

    def test_a_planted_collision_keeps_the_input_and_names_it(self):
        # Captured from cli._flat_params (site._flat_params gave the same):
        # `7.0 vs 7` and `True vs 1` compare equal and are NOT conflicts.
        planted = {
            "config": {"a": 1, "m": "petg", "t": 7.0, "b": True, "x": [1, 2]},
            "derived": {"d": 3, "a": 2, "m": "petg", "t": 7, "b": 1, "x": [1, 3], "z": None},
        }
        flat, conflicts = modelio.flat_params(planted)
        self.assertEqual(
            _typed(flat.items()),
            _typed([("d", 3), ("a", 1), ("m", "petg"), ("t", 7.0), ("b", True),
                    ("x", [1, 2]), ("z", None)]))
        self.assertEqual(conflicts, ["a: config 1 vs build() 2",
                                     "x: config [1, 2] vs build() [1, 3]"])

    def test_no_projection_flattens_to_nothing(self):
        for projection in (None, {}, {"config": None, "derived": None}):
            self.assertEqual(modelio.flat_params(projection), ({}, []))

    def test_the_input_is_not_mutated(self):
        planted = {"config": {"a": 1}, "derived": {"a": 2, "d": 3}}
        snapshot = json.dumps(planted)
        flat, _ = modelio.flat_params(planted)
        flat["d"] = 99
        self.assertEqual(json.dumps(planted), snapshot)


if __name__ == "__main__":
    unittest.main()
