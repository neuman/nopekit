# SPDX-License-Identifier: Apache-2.0
"""The site is a fifth surface onto the same ledger, and it inherits its rules.

Two jobs justify this subsystem: explaining the project to someone who did not
build it, and DEBUGGING it — spin the thing, pull it apart, and see the latest
gate results anchored to the geometry they are about. The tests below protect
the parts of that which fail silently:

    (a) `site init` never eats the shell somebody hand-edited
    (b) a project with NO geometry still builds a complete, honest page
    (c) a locator that cannot be drawn is REPORTED, never dropped
    (d) the derived explode is a usable first draft, and authoring overrides it
    (e) `state.json` is a JSON document, not a pile of dataclasses
    (f) the honesty invariants survive the trip onto the page

(f) is the one that matters most. `state.json` is the artifact most people will
actually read, and a page that showed a skipped gate's claim as proven would
launder assumption into proof in the most public place available.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from atompipe import cli as cli_mod
from atompipe import gates as gates_mod
from atompipe import site as site_mod
from atompipe import store as store_mod
from atompipe.models import (
    Acceptance, Claim, Comparator, Locator, NegativeControl, ProjectMeta,
    Tier, Verdict, View, ViewKind,
)
from atompipe.util import AtompipeError

#: A three-plate stack: thin in z (9), widest in x (40). Used by the explode
#: tests and by the fixture viewgen, so the node names the locator tests aim at
#: are the same names a real `model3d` view would publish.
STACK = {
    "base": ([0.0, 0.0, 0.0], [40.0, 20.0, 3.0]),
    "web":  ([0.0, 0.0, 3.0], [40.0, 20.0, 6.0]),
    "cap":  ([0.0, 0.0, 6.0], [40.0, 20.0, 9.0]),
}

#: A viewgen module written into `<root>/views/` by the fixtures that need one.
#: A project's own `views/` is loaded exactly like a pack's, which is the point:
#: a project that had to publish a pack before it could draw its own assembly
#: would never draw it.
VIEWGEN_SOURCE = '''
from atompipe.models import View, ViewKind
from atompipe.site import derive_explode, viewgen

BOUNDS = {
    "base": ([0.0, 0.0, 0.0], [40.0, 20.0, 3.0]),
    "web":  ([0.0, 0.0, 3.0], [40.0, 20.0, 6.0]),
    "cap":  ([0.0, 0.0, 6.0], [40.0, 20.0, 9.0]),
}


@viewgen(id="assembly", kind=ViewKind.MODEL3D, title="Assembly")
def assembly(ctx):
    """Three stacked plates."""
    src = ctx.write_asset("assembly.glb", b"not-really-a-glb")
    return View(id="assembly", kind=ViewKind.MODEL3D, src=src,
                meta={"nodes": sorted(BOUNDS), "explode": derive_explode(BOUNDS)})


@viewgen(id="stress", kind=ViewKind.FIELD, title="Stress",
         requires_python=["atompipe_no_such_solver"])
def stress(ctx):
    """Its exporter is not installed anywhere, on purpose."""
    raise AssertionError("an unavailable viewgen must never be called")


@viewgen(id="sweep", kind=ViewKind.CHART, title="Sweep")
def sweep(ctx):
    """Nothing to draw is a normal outcome, not a failure."""
    return None
'''


def _capture(argv: list[str]) -> tuple[int, str]:
    """Run the CLI and return `(exit code, stdout)`.

    Through `cli.main` rather than the command function, because the exit code
    and the AtompipeError -> `error: ...` translation are part of what the site
    subcommand promises, and neither is visible from the inside.
    """
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli_mod.main(argv)
    return code, out.getvalue()


class _SiteCase(unittest.TestCase):
    """A throwaway project on disk. Every test gets its own root."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="atompipe-site-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        store_mod.init(self.root, ProjectMeta(name="site-test", revision="v0.1"))

    # -- ledger fixtures --------------------------------------------------- #
    def _ledger(self, *, claims=(), verdicts=(), views=()):
        """Claims and views through `store.save`; each verdict THROUGH THE CACHE.

        Never into `ledger.verdicts`: from 1.2 a page reads what
        `verdicts.resolve` makes of the verdict cache, and a verdict written
        into the ledger file would test a store the page no longer reads. A
        pass or a fail becomes an entry recorded with no gate behind it
        (`record_verdict(root, None, None, v)`: its code is unrecorded, so it
        is never Fresh) — these gates are not registered in the CLI's registry
        (tests:H2), so they reach the page as the stale rows of gates this
        project does not register, and never as proof. A skip or a crash is
        not a measurement and is never cached: it is remembered, as the sweep
        remembers one, with no date (`when=""`), so its age is null.
        """
        from atompipe import verdicts as verdicts_mod

        ledger = store_mod.load(self.root)
        ledger.claims = list(claims)
        ledger.views = list(views)
        store_mod.save(self.root, ledger)
        for verdict in verdicts:
            if verdict.outcome in ("pass", "fail"):
                verdicts_mod.record_verdict(self.root, None, None, verdict)
            else:
                verdicts_mod.remember(
                    self.root, verdict.gate, verdict, input_rho="",
                    kind="error" if verdict.outcome == "error" else "self-skip", when="")
        return ledger

    def _claim(self, cid="C1", gates=("g.one",), **kw):
        return Claim(
            id=cid,
            statement=kw.pop("statement", "the bracket holds 15 N"),
            acceptance=kw.pop("acceptance", Acceptance(
                quantity="tip deflection", comparator=Comparator.LE,
                limit=0.5, units="mm")),
            gates=list(gates),
            **kw,
        )

    def _registry(self, *specs):
        """A gate registry holding exactly the gates a test declares.

        Never the module-level one: the whole suite shares that, and a pack gate
        loaded by another test file would change this project's claim coverage —
        which is precisely the number these tests assert on.
        """
        registry = gates_mod.Registry()
        for spec in specs:
            registry.register(spec, lambda ctx: Verdict(gate="x", passed=True))
        return registry

    def _spec(self, gid, claims=("C1",), **kw):
        from atompipe.models import GateSpec
        return GateSpec(
            id=gid, claims=list(claims), tier=Tier.INSTANT,
            negative_control=NegativeControl(fixture="selftest/bad.py"), **kw)

    def _write_viewgens(self) -> str:
        directory = os.path.join(self.root, "views")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "assembly.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(VIEWGEN_SOURCE)
        return path

    def _state(self) -> dict:
        path = os.path.join(self.root, site_mod.SITE_DIR,
                            site_mod.DATA_DIR, site_mod.STATE_NAME)
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)


# --------------------------------------------------------------------------- #
class Scaffold(_SiteCase):
    """(a) The shell belongs to the project the moment it exists."""

    def test_init_creates_the_expected_files(self):
        code, out = _capture(["site", "init", "-C", self.root])
        self.assertEqual(code, 0, out)
        site_dir = os.path.join(self.root, site_mod.SITE_DIR)
        for relative in ("index.html", "app.js", "style.css", ".gitignore"):
            self.assertTrue(os.path.isfile(os.path.join(site_dir, relative)),
                            f"site init did not write {relative}")
        # Created empty so a freshly scaffolded site serves before any build,
        # instead of 404ing on its own data directory.
        for relative in (site_mod.DATA_DIR, site_mod.ASSETS_DIR, site_mod.VIEWS_DIR):
            self.assertTrue(
                os.path.isdir(os.path.join(site_dir, relative.replace("/", os.sep))),
                f"site init did not create {relative}/")

    def test_gitignore_covers_vendor_and_not_the_generated_data(self):
        """`vendor/` is a third-party copy; `data/` and `assets/` ARE the site.

        A repo that ignored the generated JSON would publish an empty page, and
        the failure would only show up for whoever cloned it.
        """
        _capture(["site", "init", "-C", self.root])
        with open(os.path.join(self.root, site_mod.SITE_DIR, ".gitignore"),
                  encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("vendor/", text)
        self.assertNotIn("data/", text)
        self.assertNotIn("assets/", text)

    def test_second_init_refuses_and_keeps_the_edited_shell(self):
        _capture(["site", "init", "-C", self.root])
        index = os.path.join(self.root, site_mod.SITE_DIR, "index.html")
        with open(index, "w", encoding="utf-8") as fh:
            fh.write("<!-- an afternoon of somebody's work -->")

        code, _out = _capture(["site", "init", "-C", self.root])
        self.assertEqual(code, 2, "a second `site init` must refuse, not overwrite")
        with open(index, encoding="utf-8") as fh:
            self.assertIn("an afternoon", fh.read(),
                          "site init destroyed a hand-edited index.html")

    def test_force_replaces_the_shell_and_says_it_will(self):
        _capture(["site", "init", "-C", self.root])
        index = os.path.join(self.root, site_mod.SITE_DIR, "index.html")
        with open(index, "w", encoding="utf-8") as fh:
            fh.write("<!-- replace me -->")
        code, _out = _capture(["site", "init", "--force", "-C", self.root])
        self.assertEqual(code, 0)
        with open(index, encoding="utf-8") as fh:
            self.assertNotIn("replace me", fh.read())

    def test_refusal_names_the_flag_that_gets_past_it(self):
        _capture(["site", "init", "-C", self.root])
        with self.assertRaises(AtompipeError) as caught:
            site_mod.scaffold(self.root)
        self.assertIn("--force", str(caught.exception))


# --------------------------------------------------------------------------- #
class BuildWithNoViews(_SiteCase):
    """(b) 3D is one view kind among six, not the point.

    A chemical process, a supply chain, a bracket with nothing exported yet —
    all of them get claims, verdicts, evidence, provenance and the readiness
    sentence. A build that looked broken here would push people into inventing
    geometry to make their page work.
    """

    def test_build_with_no_views_writes_a_complete_state(self):
        self._ledger(claims=[self._claim("C1"), self._claim("C2", gates=[])],
                     verdicts=[Verdict(gate="g.one", claims=["C1"], passed=True,
                                       measured=0.31, limit=0.5, units="mm")])
        _capture(["site", "init", "-C", self.root])
        code, out = _capture(["site", "build", "-C", self.root])
        self.assertEqual(code, 0, out)

        state = self._state()
        self.assertEqual(len(state["claims"]), 2,
                         "the page must carry every claim, geometry or not")
        self.assertEqual(state["views"], [])
        self.assertEqual(len(state["verdicts"]), 1)
        # The half of the page that has nothing to do with pictures:
        for key in ("readiness", "params", "inputs", "gaps", "decisions", "meta"):
            self.assertIn(key, state, f"state.json is missing {key}")
        self.assertTrue(state["readiness"]["verdict"].strip(),
                        "a project with no views still owes the reader one honest "
                        "sentence about whether it is ready")

    def test_build_says_what_it_did_instead_of_looking_broken(self):
        self._ledger(claims=[self._claim("C1")])
        _capture(["site", "init", "-C", self.root])
        _code, out = _capture(["site", "build", "-C", self.root])
        self.assertIn("no viewgens", out.lower(),
                      "a build with nothing to draw must say so in words")
        self.assertIn("claim(s)", out,
                      "it must also say what it DID write, or it reads as a failure")

    def test_build_without_init_points_at_init(self):
        code, _out = _capture(["site", "build", "-C", self.root])
        self.assertEqual(code, 2)
        with self.assertRaises(AtompipeError) as caught:
            site_mod.build(self.root, store_mod.load(self.root),
                           self._registry(), site_mod.ViewRegistry())
        self.assertIn("site init", str(caught.exception))

    def test_json_build_is_a_single_document(self):
        self._ledger(claims=[self._claim("C1")])
        _capture(["site", "init", "-C", self.root])
        _code, out = _capture(["site", "build", "--json", "-C", self.root])
        payload = json.loads(out)              # raises if prose leaked into stdout
        self.assertFalse(payload["has_dangling_locators"])
        self.assertEqual(payload["counts"]["views"], 0)


# --------------------------------------------------------------------------- #
class LocatorsAreReported(_SiteCase):
    """(c) A gate that thinks it is drawing and is not looks exactly like a gate
    that found nothing.

    Both ends of that failure are silent: the pack author sees a clean import
    and the reader sees an unannotated verdict. So a locator is never dropped
    for being unusable — it stays in `state.json` and the problem is published
    beside it.
    """

    def _build_with(self, *locators) -> tuple[dict, str]:
        self._ledger(
            claims=[self._claim("C1")],
            verdicts=[Verdict(gate="cad.clash", claims=["C1"], passed=False,
                              detail="1 interfering pair",
                              locators=list(locators))],
        )
        self._write_viewgens()
        _capture(["site", "init", "-C", self.root])
        _code, out = _capture(["site", "build", "-C", self.root])
        return self._state(), out

    def test_locator_naming_a_view_that_does_not_exist_is_reported(self):
        state, out = self._build_with(
            Locator(view="no_such_view", target="base", label="0.41 mm^3"))
        problems = state["locator_problems"]
        self.assertEqual(len(problems), 1, problems)
        self.assertEqual(problems[0]["gate"], "cad.clash")
        self.assertEqual(problems[0]["view"], "no_such_view")
        self.assertEqual(problems[0]["severity"], "missing-view")
        self.assertIn("no_such_view", out, "the build summary must name it out loud")
        # Reported, NOT dropped: the page still has the locator to show.
        self.assertEqual(
            state["verdicts"][0]["locators"][0]["view"], "no_such_view",
            "a dangling locator must survive into state.json so the page can "
            "explain it rather than silently render nothing")

    def test_locator_naming_a_node_the_view_does_not_declare_is_reported(self):
        state, out = self._build_with(Locator(view="assembly", target="ghost_part"))
        problems = state["locator_problems"]
        self.assertEqual(len(problems), 1, problems)
        self.assertEqual(problems[0]["severity"], "unknown-target")
        self.assertIn("ghost_part", problems[0]["problem"])
        self.assertIn("ghost_part", out)

    def test_a_locator_that_resolves_is_not_reported(self):
        """The other half of the invariant.

        A problem list that cried wolf on every working locator would be ignored
        within a week, and then the real ones go past unread too.
        """
        state, _out = self._build_with(
            Locator(view="assembly", target="base", label="0.41 mm^3", value=0.41),
            Locator(view="assembly", target="web"),
        )
        self.assertEqual(state["locator_problems"], [])

    def test_dangling_locators_are_a_flagged_warning_not_a_failure(self):
        """They never fail the build: the claims and verdicts on the page are
        still true, and it is the overlay that is wrong."""
        self._ledger(
            claims=[self._claim("C1")],
            verdicts=[Verdict(gate="cad.clash", claims=["C1"], passed=False,
                              locators=[Locator(view="gone", target="x")])])
        self._write_viewgens()
        _capture(["site", "init", "-C", self.root])
        code, out = _capture(["site", "build", "--json", "-C", self.root])
        self.assertEqual(code, 0, "a dangling locator is a warning, not an exit code")
        payload = json.loads(out)
        self.assertTrue(payload["has_dangling_locators"],
                        "--json must carry the flag; a reader parsing this output "
                        "never sees the printed warning")
        self.assertEqual(payload["counts"]["locator_problems"], 1)


# --------------------------------------------------------------------------- #
class DeriveExplode(unittest.TestCase):
    """(d) A derived first draft that removes transcription, not judgment."""

    def test_thin_axis_wins_and_movers_are_in_stack_order(self):
        manifest = site_mod.derive_explode(STACK)
        self.assertEqual(manifest["axes"]["thick"], 2, "z is the smallest extent")
        self.assertEqual(manifest["axes"]["width"], 0, "x is the widest")
        ranked = sorted(manifest["movers"], key=lambda m: manifest["movers"][m]["rank"])
        self.assertEqual(ranked, ["base", "web", "cap"],
                         "stack order is the centroid along the thin axis, and the "
                         "geometry already knows it")

    def test_offsets_actually_separate_the_parts(self):
        """An exploded view that does not explode reads as an unexploded one
        with a suspicious seam."""
        manifest = site_mod.derive_explode(STACK)
        thick = manifest["axes"]["thick"]
        spans = []
        for mover, record in manifest["movers"].items():
            offset = record["offset"][thick]
            low = min(STACK[n][0][thick] for n in record["nodes"]) + offset
            high = max(STACK[n][1][thick] for n in record["nodes"]) + offset
            spans.append((low, high, mover))
        spans.sort()
        for (_lo_a, hi_a, a), (lo_b, _hi_b, b) in zip(spans, spans[1:]):
            self.assertLess(hi_a, lo_b, f"{a} and {b} still overlap after exploding")

    def test_step_has_a_floor_so_thin_geometry_still_separates(self):
        """A 0.8 mm sheet stack derives a 0.92 mm step, which on screen is
        indistinguishable from not exploding at all."""
        thin = {"a": ([0, 0, 0.0], [40, 20, 0.4]),
                "b": ([0, 0, 0.4], [40, 20, 0.8])}
        self.assertGreaterEqual(site_mod.derive_explode(thin)["step"], 14.0)

    def test_nodes_group_into_movers_by_the_double_underscore(self):
        """`back_left` is one part; `cap__boss_a` is a feature of `cap`. A
        grouper that split on the first `_` would shatter every multi-word part
        into a mover of its own."""
        bounds = dict(STACK)
        bounds["cap__boss_a"] = ([2.0, 2.0, 9.0], [6.0, 6.0, 11.0])
        manifest = site_mod.derive_explode(bounds)
        self.assertIn("cap", manifest["movers"])
        self.assertNotIn("cap__boss_a", manifest["movers"])
        self.assertEqual(manifest["movers"]["cap"]["nodes"], ["cap", "cap__boss_a"])

    def test_overrides_merge_per_mover(self):
        """Correcting two parts must cost two entries.

        Hand-authored manifests rot because the cost of fixing one offset is
        retyping the whole table, so nobody does it twice. Assembly order is
        intent, not geometry, and authoring it is the normal path.
        """
        derived = site_mod.derive_explode(STACK)
        merged = site_mod.derive_explode(
            STACK, overrides={"movers": {"cap": {"offset": [0, 0, 40]}}, "step": 22})

        self.assertEqual(merged["movers"]["cap"]["offset"], [0.0, 0.0, 40.0])
        self.assertEqual(merged["step"], 22)
        for untouched in ("base", "web"):
            self.assertEqual(merged["movers"][untouched]["offset"],
                             derived["movers"][untouched]["offset"],
                             "an override for one mover moved another one")
        self.assertEqual(merged["movers"]["cap"]["nodes"], ["cap"],
                         "node membership is geometry and must survive an override")

    def test_the_bare_movers_shape_is_accepted_too(self):
        merged = site_mod.derive_explode(STACK, overrides={"web": {"offset": [1, 2, 3]}})
        self.assertEqual(merged["movers"]["web"]["offset"], [1.0, 2.0, 3.0])

    def test_an_override_for_a_part_that_no_longer_exists_is_reported(self):
        """A hand-edit that outlived its geometry, with the author still looking
        at a viewer that ignores them."""
        merged = site_mod.derive_explode(STACK, overrides={"lid": {"offset": [0, 0, 9]}})
        self.assertEqual(merged["stale_overrides"], ["lid"])

    def test_an_override_cannot_rewrite_node_membership(self):
        merged = site_mod.derive_explode(
            STACK, overrides={"cap": {"nodes": ["base", "web", "cap"]}})
        self.assertEqual(merged["movers"]["cap"]["nodes"], ["cap"])

    def test_a_two_component_offset_is_refused(self):
        with self.assertRaises(AtompipeError):
            site_mod.derive_explode(STACK, overrides={"cap": {"offset": [0, 40]}})

    def test_an_empty_assembly_still_produces_a_manifest(self):
        """The caller already produced a GLB; refusing to describe it would turn
        an empty picture into a failed build."""
        manifest = site_mod.derive_explode({})
        self.assertEqual(manifest["movers"], {})
        self.assertEqual(manifest["step"], 14.0)


# --------------------------------------------------------------------------- #
class StateIsJson(_SiteCase):
    """(e) `curl .../data/state.json` has to be enough to answer "what is the
    status of this project?" without a browser."""

    def test_state_round_trips_through_json(self):
        self._ledger(
            claims=[self._claim("C1")],
            verdicts=[Verdict(gate="g.one", claims=["C1"], passed=True,
                              tier=Tier.BUILD, measured=0.31, limit=0.5,
                              locators=[Locator(view="assembly", target="base")])],
            views=[View(id="bom", kind=ViewKind.TABLE, title="BOM",
                        data={"rows": [{"id": "r1", "part": "M3 screw"}]})],
        )
        ledger = store_mod.load(self.root)
        payload = site_mod.state(self.root, ledger, self._registry(self._spec("g.one")),
                                 now="2026-01-01T00:00:00Z")

        # No `default=` encoder: a stray dataclass or enum must raise here rather
        # than be silently stringified into something the page cannot read back.
        text = json.dumps(payload, sort_keys=True)
        self.assertEqual(json.loads(text), payload, "state.json does not round-trip")
        self.assertEqual(payload["verdicts"][0]["tier"], int(Tier.BUILD))
        self.assertEqual(payload["views"][0]["kind"], "table")

    def test_an_age_is_null_rather_than_zero_when_it_is_unknown(self):
        """An age of zero renders as "just now", which is the precise lie a
        staleness display exists to prevent."""
        self._ledger(claims=[self._claim("C1")],
                     verdicts=[Verdict(gate="g.one", claims=["C1"], passed=True)])
        payload = site_mod.state(self.root, store_mod.load(self.root),
                                 self._registry(self._spec("g.one")))
        self.assertIsNone(payload["verdicts"][0]["age_s"])

    def test_written_state_is_readable_json_on_disk(self):
        self._ledger(claims=[self._claim("C1")])
        _capture(["site", "init", "-C", self.root])
        _capture(["site", "build", "-C", self.root])
        self.assertIsInstance(self._state(), dict)


# --------------------------------------------------------------------------- #
class HonestyOnThePage(_SiteCase):
    """(f) THE honesty test. The site inherits the report's invariants.

    `state.json` is the artifact most people will actually read, and the page
    renders it without a second opinion. A skipped gate showing up as proof here
    would launder assumption into proof in the most public place the project
    has — and unlike the readiness report, nobody would be diffing it.
    """

    def _built_state(self, claims, verdicts, registry, *, controls=True) -> dict:
        """Plant `verdicts` under the registry's OWN gates, then build the page.

        A pass or a fail is recorded with the registered `(spec, fn)`, so the
        resolver can key it: a Fresh entry. A skip or a crash is remembered —
        as an availability skip when the gate's tooling is missing here, a
        self-skip otherwise — dated 2026-01-01T00:00:00Z, ten minutes before
        the build.

        With `controls`, every registered gate with a verdict also gets a
        FORGED fired control: `record_control(bad="fail")` with no fixture run
        behind it. That is a forged admission, legitimate only in a renderer
        test — it forges the inner loop, and R-9's re-execution at every money
        boundary (`check --force` in CI, P2's `export`) is what a hand-placed
        entry cannot get past. It is here because these tests are about the
        page, and a Fresh PASS from a gate never shown to fail is not a PASS on
        the page (§3.10); `test_an_undemonstrated_gate_does_not_read_pass_on_the_page`
        builds without it and shows exactly that.

        `site.build` is handed no resolution: it resolves for itself.
        """
        from atompipe import verdicts as verdicts_mod

        self._ledger(claims=claims)
        for verdict in verdicts:
            found = registry.get(verdict.gate)
            spec, fn = found if found is not None else (None, None)
            if verdict.outcome in ("pass", "fail"):
                verdicts_mod.record_verdict(self.root, spec, fn, verdict)
            else:
                missing = spec is not None and not gates_mod.availability(spec)[0]
                kind = ("error" if verdict.outcome == "error" else
                        "availability" if missing else "self-skip")
                verdicts_mod.remember(self.root, verdict.gate, verdict, input_rho="",
                                      kind=kind, when="2026-01-01T00:00:00Z")
            if controls and spec is not None:
                verdicts_mod.record_control(self.root, spec, fn, bad="fail",
                                            detail="planted by a renderer test")
        site_mod.scaffold(self.root)
        site_mod.build(self.root, store_mod.load(self.root), registry,
                       site_mod.ViewRegistry(), now="2026-01-01T00:10:00Z")
        return self._state()

    def test_a_passing_gate_reaches_the_page(self):
        """The positive control for everything in this class: a gate that ran,
        passed, and was shown to fail its own known-bad input reads PASS on the
        page. Without it, every "is not PASS" below could be a page that cannot
        show a pass at all."""
        state = self._built_state(
            claims=[self._claim("C1", gates=["g.one"])],
            verdicts=[Verdict(gate="g.one", claims=["C1"], passed=True,
                              measured=0.31, limit=0.5, units="mm")],
            registry=self._registry(self._spec("g.one")),
        )
        self.assertEqual(state["claims"][0]["status"], "pass")
        verdict = state["verdicts"][0]
        self.assertTrue(verdict["ok"])
        self.assertEqual(verdict["status"], "pass")
        self.assertTrue(verdict["cached"])
        self.assertTrue(verdict["fresh"])
        self.assertEqual(verdict["stale_reason"], "")
        self.assertFalse(state["meta"]["stale"])

    def test_an_undemonstrated_gate_does_not_read_pass_on_the_page(self):
        """The same build without the forged control. The verdict is Fresh and
        it passed — and the gate was never shown to fail, so its pass is not
        proof (invariant 9, the reject half): not PASS on the page, and the row
        says why."""
        state = self._built_state(
            claims=[self._claim("C1", gates=["g.one"])],
            verdicts=[Verdict(gate="g.one", claims=["C1"], passed=True,
                              measured=0.31, limit=0.5, units="mm")],
            registry=self._registry(self._spec("g.one")),
            controls=False,
        )
        self.assertNotEqual(state["claims"][0]["status"], "pass",
                            "a PASS from a gate never shown to fail reached the page")
        verdict = state["verdicts"][0]
        self.assertFalse(verdict["fresh"])
        self.assertIn("control not demonstrated", verdict["stale_reason"])
        self.assertTrue(state["meta"]["stale"])
        self.assertFalse(state["readiness"]["ready"])

    def test_a_claim_covered_only_by_a_skipped_gate_is_not_proven(self):
        state = self._built_state(
            claims=[self._claim("C1", gates=["g.solve"])],
            verdicts=[Verdict(gate="g.solve", claims=["C1"], passed=True, skipped=True,
                              skip_reason="requires openfoam (not on PATH)")],
            registry=self._registry(self._spec("g.solve", requires_python=["no_such_mod"])),
        )
        row = state["claims"][0]
        self.assertNotEqual(row["status"], "pass",
                            "a skipped gate proved nothing and the page said it did")
        self.assertEqual(row["status"], "blocked")

        verdict = state["verdicts"][0]
        self.assertFalse(verdict["ok"],
                         "`ok` is the only field that means it ran, did not crash, and "
                         "said yes — a page keying a green tick off `passed` would show "
                         "this skipped gate as a pass")
        self.assertEqual(verdict["status"], "skipped")

    def test_an_errored_gate_is_not_proof_either(self):
        state = self._built_state(
            claims=[self._claim("C1", gates=["g.one"])],
            verdicts=[Verdict(gate="g.one", claims=["C1"], passed=True,
                              error="ZeroDivisionError: float division by zero")],
            registry=self._registry(self._spec("g.one")),
        )
        self.assertNotEqual(state["claims"][0]["status"], "pass")
        self.assertEqual(state["verdicts"][0]["status"], "errored")
        self.assertFalse(state["verdicts"][0]["ok"])

    def test_a_partially_covered_claim_carries_the_partial_marker(self):
        """The PARTIAL row, on the page as in the report.

        Without it a claim covered by a cheap analytic gate and an uninstalled
        solver reads as fully proven, and the check that mattered has vanished
        from the document.
        """
        state = self._built_state(
            claims=[self._claim("C1", gates=["g.cheap", "g.solve"])],
            verdicts=[
                Verdict(gate="g.cheap", claims=["C1"], passed=True, measured=0.31,
                        limit=0.5, units="mm"),
                Verdict(gate="g.solve", claims=["C1"], passed=False, skipped=True,
                        skip_reason="requires openfoam (not on PATH)"),
            ],
            registry=self._registry(self._spec("g.cheap"), self._spec("g.solve")),
        )
        row = state["claims"][0]
        self.assertTrue(row["partial"],
                        "a claim whose covering solver never ran is PARTIAL, not proven")
        unproven = {entry["gate"] for entry in row["unproven"]}
        self.assertIn("g.solve", unproven,
                      "the page must NAME the gate that did not produce proof")

    def test_an_unanchored_failure_says_so_rather_than_looking_broken(self):
        """A gate attaches a locator only when it genuinely knows the position;
        a confident red highlight on the wrong part is worse than none."""
        state = self._built_state(
            claims=[self._claim("C1")],
            verdicts=[Verdict(gate="g.one", claims=["C1"], passed=False,
                              detail="deflection 0.71 mm exceeds 0.5 mm")],
            registry=self._registry(self._spec("g.one")),
        )
        self.assertTrue(state["verdicts"][0]["unanchored"])


# --------------------------------------------------------------------------- #
class ViewgensThroughTheCli(_SiteCase):
    """The loader: a project's own `views/`, and what happens when it cannot run.

    A view that is absent because an exporter is missing looks exactly like a
    view the project never had, and only one of those costs somebody an
    afternoon.
    """

    def setUp(self) -> None:
        super().setUp()
        self._ledger(claims=[self._claim("C1")])
        self._write_viewgens()
        _capture(["site", "init", "-C", self.root])

    def test_a_project_views_directory_is_loaded_and_its_asset_written(self):
        code, out = _capture(["site", "build", "-C", self.root])
        self.assertEqual(code, 0, out)
        state = self._state()
        self.assertEqual([v["id"] for v in state["views"]], ["assembly"])
        self.assertEqual(state["views"][0]["src"], "assets/assembly.glb")
        self.assertTrue(os.path.isfile(
            os.path.join(self.root, site_mod.SITE_DIR, "assets", "assembly.glb")))

    def test_an_unavailable_viewgen_names_its_missing_dependency(self):
        _code, out = _capture(["site", "build", "-C", self.root])
        self.assertIn("stress", out)
        self.assertIn("atompipe_no_such_solver", out,
                      "the build must NAME the module that is missing; 'unavailable' "
                      "alone sends the reader to go and find out")

    def test_nothing_to_draw_is_reported_but_is_not_a_warning(self):
        _code, out = _capture(["site", "build", "--json", "-C", self.root])
        payload = json.loads(out)
        by_id = {row["view"]: row for row in payload["viewgens"]}
        self.assertEqual(by_id["sweep"]["status"], "empty")
        self.assertEqual(by_id["stress"]["status"], "unavailable")
        self.assertFalse(any("sweep" in w for w in payload["warnings"]),
                         "a viewgen with nothing to draw is a normal outcome and must "
                         "not be reported as a problem")

    def test_building_twice_in_one_process_still_finds_the_viewgens(self):
        """Python will not execute a module twice.

        A second build in one process — a test, an agent that rebuilds after
        every edit — would otherwise decorate nothing, find an empty registry
        and report "this project has no views" about a project with three.
        """
        _capture(["site", "build", "-C", self.root])
        _code, out = _capture(["site", "build", "-C", self.root])
        self.assertIn("assembly", out)
        self.assertEqual([v["id"] for v in self._state()["views"]], ["assembly"])

    def test_a_stale_asset_from_a_renamed_view_is_removed(self):
        """A renamed view otherwise leaves a 12 MB GLB behind that renders
        perfectly and belongs to nothing."""
        _capture(["site", "build", "-C", self.root])
        orphan = os.path.join(self.root, site_mod.SITE_DIR, "assets", "old.glb")
        with open(orphan, "wb") as fh:
            fh.write(b"abandoned geometry")
        _code, out = _capture(["site", "build", "--json", "-C", self.root])
        self.assertFalse(os.path.exists(orphan))
        self.assertIn("site/assets/old.glb", json.loads(out)["removed"])


# --------------------------------------------------------------------------- #
class SiteStatusReports(_SiteCase):
    """`site status` answers what is built, how stale it is, and what dangles."""

    def test_status_on_a_project_with_no_site_points_at_init(self):
        code, out = _capture(["site", "status", "-C", self.root])
        self.assertEqual(code, 0, "status never fails; it is what you run when lost")
        self.assertIn("site init", out)

    def test_status_reports_dangling_locators_after_a_build(self):
        self._ledger(
            claims=[self._claim("C1")],
            verdicts=[Verdict(gate="cad.clash", claims=["C1"], passed=False,
                              locators=[Locator(view="gone", target="x")])])
        _capture(["site", "init", "-C", self.root])
        _capture(["site", "build", "-C", self.root])
        code, out = _capture(["site", "status", "--json", "-C", self.root])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["has_dangling_locators"])
        self.assertEqual(len(payload["locator_problems"]), 1)

    def test_a_ledger_written_after_the_build_reads_as_stale(self):
        """A stale sweep must LOOK stale, and so must a stale page."""
        self._ledger(claims=[self._claim("C1")])
        _capture(["site", "init", "-C", self.root])
        _capture(["site", "build", "-C", self.root])
        self.assertFalse(cli_mod._site_state(self.root)["stale"])

        state_path = os.path.join(self.root, site_mod.SITE_DIR,
                                  site_mod.DATA_DIR, site_mod.STATE_NAME)
        ledger_path = store_mod.ledger_path(self.root)
        mtime = os.path.getmtime(state_path)
        os.utime(ledger_path, (mtime + 60, mtime + 60))

        info = cli_mod._site_state(self.root)
        self.assertTrue(info["stale"])
        self.assertIn("site build", info["stale_reason"])

    def test_status_and_doctor_mention_a_site_that_exists(self):
        self._ledger(claims=[self._claim("C1")])
        _capture(["site", "init", "-C", self.root])
        _capture(["site", "build", "-C", self.root])

        _code, out = _capture(["status", "-C", self.root])
        self.assertIn("site:", out)
        _code, doctor = _capture(["doctor", "-C", self.root])
        self.assertIn("site", doctor)

    def test_status_does_not_mention_a_site_that_does_not_exist(self):
        """A line telling every project without a site that it has no site is
        noise in the one command that has to stay readable at a glance."""
        self._ledger(claims=[self._claim("C1")])
        _code, out = _capture(["status", "-C", self.root])
        self.assertNotIn("site:", out)

    def test_a_partially_vendored_directory_does_not_read_as_vendored(self):
        """vendor/ holding three of four files shadows the CDN import map and
        then 404s on the fourth — offline and online alike."""
        _capture(["site", "init", "-C", self.root])
        first = sorted(site_mod.vendor_urls())[0]
        target = os.path.join(self.root, site_mod.SITE_DIR, first.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("// half a vendor directory\n")
        info = cli_mod._site_state(self.root)
        self.assertFalse(info["vendored"])
        self.assertEqual(info["vendor_files"], 1)


# --------------------------------------------------------------------------- #
class VendorIsAllOrNothing(_SiteCase):
    """A half-written `vendor/` turns a working online site into a broken one.

    The import map prefers the local copy, so a missing file 404s rather than
    falling back — and it shows up offline, which is the one situation vendoring
    exists for and the one nobody tests before archiving a project. Nothing here
    touches the network: the failure path is the whole point.
    """

    def test_a_dead_host_writes_nothing_and_says_the_cdn_still_works(self):
        _capture(["site", "init", "-C", self.root])
        site_dir = os.path.join(self.root, site_mod.SITE_DIR)
        original = site_mod.vendor_urls
        site_mod.vendor_urls = lambda: {         # type: ignore[assignment]
            "vendor/three.module.js": "https://atompipe-no-such-host.invalid/three.js"}
        self.addCleanup(setattr, site_mod, "vendor_urls", original)

        code, _out = _capture(["site", "vendor", "-C", self.root])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(os.path.join(site_dir, site_mod.VENDOR_DIR)),
                         "a failed vendor left a directory that shadows the CDN")
        leftovers = [n for n in os.listdir(site_dir) if n.startswith(".atompipe-vendor")]
        self.assertEqual(leftovers, [], f"staging directory left behind: {leftovers}")

    def test_the_pinned_urls_are_https_and_mirror_the_package_layout(self):
        """`GLTFLoader.js` imports `../utils/BufferGeometryUtils.js` relatively,
        so flattening the files 404s on an import the CDN copy resolves fine."""
        urls = site_mod.vendor_urls()
        self.assertTrue(all(u.startswith("https://") for u in urls.values()))
        self.assertIn("vendor/jsm/utils/BufferGeometryUtils.js", urls)
        self.assertTrue(all(site_mod.THREE_VERSION in u for u in urls.values()),
                        "an unpinned CDN URL is a build step somebody else controls")

    def test_html_masquerading_as_javascript_is_refused(self):
        """A captive portal answers 200 with a login page. Vendored, that page
        shadows three.js and fails at parse time in a file nobody wrote."""
        class _Response:
            status = 200
            headers = {"Content-Type": "text/html; charset=utf-8"}

            def read(self):
                return b"<!DOCTYPE html><html><body>Sign in to the network</body></html>"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        import urllib.request
        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **kw: _Response()   # type: ignore[assignment]
        self.addCleanup(setattr, urllib.request, "urlopen", original)

        with self.assertRaises(AtompipeError) as caught:
            cli_mod._fetch_vendor_file("https://cdn.example/three.module.js")
        self.assertIn("HTML", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
