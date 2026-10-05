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
import unittest.mock

from nopekit import cli as cli_mod
from nopekit import gates as gates_mod
from nopekit import site as site_mod
from nopekit import store as store_mod
from nopekit.models import (
    Acceptance, Claim, Comparator, Locator, NegativeControl, ProjectMeta,
    Tier, Verdict, View, ViewKind,
)
from nopekit.util import NopekitError

import _env
import _projects

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
from nopekit.models import View, ViewKind
from nopekit.site import derive_explode, viewgen

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
         requires_python=["nopekit_no_such_solver"])
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
    and the NopekitError -> `error: ...` translation are part of what the site
    subcommand promises, and neither is visible from the inside.
    """
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli_mod.main(argv)
    return code, out.getvalue()


class _SiteCase(unittest.TestCase):
    """A throwaway project on disk. Every test gets its own root."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="nopekit-site-")
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
        from nopekit import verdicts as verdicts_mod

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

    def _qualified(self, registry, *gate_ids):
        """A forged whole qualification for each of ``gate_ids`` (R-6, P2.3):
        an evaluator never qualified reads not yet qualified — its row the
        unqualified one, its claim Gap — so a test about how a STALE entry
        renders plants a qualified evaluator first, as before P2.3 it had no
        need to."""
        from nopekit import verdicts as verdicts_mod
        for gid in gate_ids:
            spec, fn = registry.get(gid)
            verdicts_mod.record_control(
                self.root, spec, fn, bad="fail", detail="planted by a renderer test",
                good="pass", mutation=() if verdicts_mod._mutation_applies(fn, self.root)
                else None)

    def _spec(self, gid, claims=("C1",), **kw):
        from nopekit.models import GateSpec
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
        with self.assertRaises(NopekitError) as caught:
            site_mod.scaffold(self.root)
        self.assertIn("--force", str(caught.exception))

    def test_build_refreshes_a_renderer_an_older_nopekit_scaffolded(self):
        """A page scaffolded before P2.1 keeps its own `format.js`, which read
        `blocked` as "its tooling is missing" in a missing tool's tone — so a
        crash, filed under `blocked` from P2.1, would read on it exactly like a
        missing tool (invariant 2), and its READY headline read `ready`. `site
        build` refreshes every renderer file but the shell, which stays the
        project's (review of the P2.1 design)."""
        _capture(["site", "init", "-C", self.root])
        site_dir = os.path.join(self.root, site_mod.SITE_DIR)
        old_format = os.path.join(site_dir, "lib", "format.js")
        with open(old_format, "w", encoding="utf-8") as fh:
            fh.write('const CLAIM_STATUS = { blocked: { label: "BLOCKED", tone: "warn", '
                     'hint: "a gate covers it but its tooling is missing" } };\n')
        index = os.path.join(site_dir, "index.html")
        with open(index, "w", encoding="utf-8") as fh:
            fh.write("<!-- the project's own shell -->")
        code, out = _capture(["site", "build", "-C", self.root])
        self.assertEqual(code, 0, out)
        with open(old_format, encoding="utf-8") as fh, \
                open(os.path.join(site_mod.TEMPLATE_DIR, "lib", "format.js"),
                     encoding="utf-8") as want:
            self.assertEqual(fh.read(), want.read())
        with open(index, encoding="utf-8") as fh:
            self.assertIn("the project's own shell", fh.read())
        self.assertIn("refreshed the renderer", out)


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
        with self.assertRaises(NopekitError) as caught:
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
        with self.assertRaises(NopekitError):
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
        registry = self._registry(self._spec("g.one"))
        self._qualified(registry, "g.one")
        payload = site_mod.state(self.root, ledger, registry, now="2026-01-01T00:00:00Z")

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

    def test_the_judgement_digest_moves_with_a_judgement_and_not_with_the_clock(self):
        """`meta.judgement_digest` is what the page judged (review, `repro_site`).

        The negative half: the clock and the drawing move it not at all, or every
        check would send the reader to rebuild a page whose verdicts all stand.
        The positive half: a verdict's outcome, a claim's status, and a key no
        one has classified yet each move it — the deny-list's direction, so a
        judgement a later `state` adds is never silently unwatched."""
        self._ledger(claims=[self._claim("C1")],
                     verdicts=[Verdict(gate="g.one", claims=["C1"], passed=True,
                                       measured=0.31, limit=0.5, units="mm")])
        registry = self._registry(self._spec("g.one"))
        self._qualified(registry, "g.one")
        payload = site_mod.state(self.root, store_mod.load(self.root), registry,
                                 now="2026-01-01T00:00:00Z")
        digest = payload["meta"]["judgement_digest"]
        self.assertEqual(site_mod.judgement_digest(payload), digest,
                         "the digest `state` set is not the digest of what it returned")

        def moved(change) -> bool:
            copy = json.loads(json.dumps(payload))
            change(copy)
            return site_mod.judgement_digest(copy) != digest

        def clock(doc):
            doc["meta"]["built"] = "2027-01-01T00:00:00Z"
            doc["verdicts"][0]["when"] = "2027-01-01T00:00:00Z"
            doc["verdicts"][0]["age_s"] = 5.0
            doc["meta"]["records_digest"] = "0" * 64
            doc["views"] = [{"id": "assembly"}]
            doc["locator_problems"] = [{"gate": "g.one"}]

        self.assertFalse(moved(clock), "the clock or the drawing moved the judgement")
        self.assertTrue(moved(lambda d: d["verdicts"][0].update(status="fail")))
        self.assertTrue(moved(lambda d: d["verdicts"][0].update(fresh=True)))
        self.assertTrue(moved(lambda d: d["claims"][0].update(status="pass")))
        self.assertTrue(moved(lambda d: d["readiness"].update(ready=True)))
        self.assertTrue(moved(lambda d: d.update(results=[{"claim": "C1"}])),
                        "a key `state` adds later went unjudged")
        self.assertTrue(moved(lambda d: d["meta"].update(stale_reason="")))

    def test_what_moved_is_named_claims_first(self):
        """The reason names what the page shows that a rebuild would not."""
        shown = {"claims": [{"id": "C1", "status": "pass"}, {"id": "C2", "status": "pass"}],
                 "verdicts": [{"gate": "g.one", "status": "pass", "measured": 0.41,
                               "units": "mm", "stale_reason": ""}]}
        now = json.loads(json.dumps(shown))
        self.assertEqual(site_mod.judgement_moved(shown, now), [])
        now["verdicts"][0].update(measured=0.29)
        self.assertEqual(site_mod.judgement_moved(shown, now),
                         ["g.one pass 0.41 mm -> pass 0.29 mm"])
        now["verdicts"][0].update(stale_reason="config.thickness 8.0 -> 6.0")
        self.assertEqual(site_mod.judgement_moved(shown, now),
                         ["g.one pass 0.41 mm -> pass 0.29 mm (stale)"])
        now["claims"][0].update(status="fail")
        now["claims"].append({"id": "C9", "status": "pending"})
        del now["claims"][1]
        self.assertEqual(site_mod.judgement_moved(shown, now),
                         ["C1 pass -> fail", "C9 (none) -> pending", "C2 pass -> (none)"])


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
        FORGED fired control: `record_control(bad="fail", good="pass")` with no fixture run
        behind it. That is a forged admission, legitimate only in a renderer
        test — it forges the inner loop, and R-9's re-execution at every money
        boundary (`check --force` in CI, P2's `export`) is what a hand-placed
        entry cannot get past. It is here because these tests are about the
        page, and a Fresh PASS from a gate never shown to fail is not a PASS on
        the page (§3.10); `test_an_undemonstrated_gate_does_not_read_pass_on_the_page`
        builds without it and shows exactly that.

        `site.build` is handed no resolution: it resolves for itself.
        """
        from nopekit import verdicts as verdicts_mod

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
                # R-6 (P2.3): a whole qualification forged — the known-good
                # half passed and, where the walk applies, a walk that made
                # none — or the entry is incomplete and nothing counts (D19).
                verdicts_mod.record_control(
                    self.root, spec, fn, bad="fail", detail="planted by a renderer test",
                    good="pass", mutation=() if verdicts_mod._mutation_applies(fn, self.root)
                    else None)
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
        proof (invariant 9): not PASS on the page, and the row says why. R-6
        (P2.3): never qualified, it reads not yet qualified — the row
        ``unqualified``, the claim Gap — where P2.2 read it Stale."""
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
        self.assertEqual(verdict["status"], "unqualified")
        self.assertEqual(verdict["qualification"]["token"], "qualification:not-yet|0")
        self.assertIn("not yet qualified at this version", verdict["qualification"]["reason"])
        # The sentence the page shows (review of P2.3: it showed `error`, R-2's
        # fallback, `unqualified: qualification:not-yet|0`): the table's, no token.
        self.assertEqual(verdict["qualification"]["text"],
                         "unqualified: not yet qualified at this version — the next check "
                         "run qualifies it")
        self.assertIn("qualification:not-yet|0", verdict["error"], "the fallback stays")
        panels = os.path.join(os.path.dirname(site_mod.__file__), "site_template", "lib",
                              "panels.js")
        with open(panels, encoding="utf-8") as fh:
            js = fh.read()
        row = js[js.index("function verdictRow("):]
        row = row[:row.index("\n}\n")]
        self.assertIn("v.qualification && v.qualification.text", row,
                      "the page renders the qualification's text before `error`")
        self.assertLess(row.index("v.qualification.text"), row.index("v.error ||"))
        self.assertEqual(state["claims"][0]["status"], "unclaimed")
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

    def test_a_partially_covered_claim_reads_skipped_and_names_the_gate(self):
        """A claim covered by a cheap analytic gate and an uninstalled solver,
        on the page as in the report. Until P2.1 it resolved PASS and the page
        marked it PARTIAL; under GLOSSARY §3's composition it reads Skipped —
        never `pass` — the unproven gate named with its lead, and PARTIAL is
        gone (R-6, old 2.1's strengthening: `status != "pass"` and the gate
        named; P2.1-D18: no `partial` key at all, its contradiction flag false).
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
        self.assertNotIn("partial", row)
        self.assertNotEqual(row["status"], "pass",
                            "a claim whose covering solver never ran is not checked")
        self.assertEqual((row["status"], row["key"], row["word"], row["cause"]),
                         ("blocked", "skipped", "skipped", "skipped"))
        self.assertFalse(row["disagree"])
        unproven = {entry["gate"]: entry["why"] for entry in row["unproven"]}
        self.assertEqual(unproven.get("g.solve"), "skipped: requires openfoam (not on PATH)",
                         "the page must NAME the gate that did not pass, led by the fact")

    def test_an_errored_row_paints_in_failings_tone_and_sorts_first(self):
        """Invariant 2 on the page (P2.0 F-5): an errored claim's row is marked
        `errored`, the page paints it in Failing's tone (`format.js`), and the
        claims arrive in severity order — the crash above the skip whatever the
        record order says."""
        state = self._built_state(
            claims=[self._claim("C1", gates=["g.skip"]), self._claim("C2", gates=["g.boom"])],
            verdicts=[Verdict(gate="g.skip", claims=["C1"], skipped=True,
                              skip_reason="requires openfoam (not on PATH)"),
                      Verdict(gate="g.boom", claims=["C2"], error="RuntimeError: boom")],
            registry=self._registry(self._spec("g.skip"), self._spec("g.boom", claims=["C2"])),
        )
        self.assertEqual([r["id"] for r in state["claims"]], ["C2", "C1"])
        crash = state["claims"][0]
        self.assertEqual((crash["status"], crash["errored"]), ("blocked", True))
        self.assertTrue(crash["reason"].startswith("errored: g.boom : RuntimeError: boom"))
        with open(os.path.join(site_mod.TEMPLATE_DIR, "lib", "format.js"),
                  encoding="utf-8") as fh:
            self.assertIn('tone: errored ? "bad" : look.tone,', fh.read())

    def test_a_junk_pass_flag_is_a_fail_on_the_page(self):
        """A verdict row's status comes from `Verdict.outcome`, never the flags:
        `passed: "yes"` is not a pass (P2.1 design: the page read `passed` and
        printed `pass` beside `ok: false`)."""
        from nopekit import site as site_
        junk = Verdict(gate="g.one", claims=["C1"], passed="yes")
        resolution = unittest.mock.Mock(verdicts=[junk], stale_gates=frozenset(), rows={})
        ledger = store_mod.load(self.root)
        ledger.claims = [self._claim("C1")]
        state = site_.state(self.root, ledger, self._registry(self._spec("g.one")),
                            resolution=resolution, params=[])
        row = state["verdicts"][0]
        self.assertEqual((row["status"], row["ok"]), ("fail", False))

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
        self.assertIn("nopekit_no_such_solver", out,
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

    def test_records_moved_after_the_build_read_as_stale(self):
        """A stale page must LOOK stale — and a current one must not.

        The page records the digest of the records it was built from
        (`meta.records_digest`), and `_site_state` compares it with
        `store.records_digest` now (cli:H16). What it replaced compared the
        mtimes of `ledger.json` and `state.json`: from checkpoint 1.3 the ledger
        is a generated index that every command rewrites, so a page built from
        unchanged records read stale after any `status` — and a record edited by
        hand, before a command had rebuilt the index, read current."""
        self._ledger(claims=[self._claim("C1")])
        _capture(["site", "init", "-C", self.root])
        _capture(["site", "build", "-C", self.root])
        self.assertEqual(self._state()["meta"]["records_digest"],
                         store_mod.records_digest(self.root))
        self.assertFalse(cli_mod._site_state(self.root)["stale"])

        # The negative half: the index rewritten and every mtime moved past the
        # build, the records' bytes untouched — still current.
        state_path = os.path.join(self.root, site_mod.SITE_DIR,
                                  site_mod.DATA_DIR, site_mod.STATE_NAME)
        claim_path = os.path.join(self.root, "claims", "C1.json")
        index_path = store_mod.ledger_path(self.root)
        with open(index_path, "a", encoding="utf-8") as fh:
            fh.write("\n")
        later = os.path.getmtime(state_path) + 60
        for path in (index_path, claim_path):
            os.utime(path, (later, later))
        info = cli_mod._site_state(self.root)
        self.assertFalse(info["stale"], info["stale_reason"])

        # One record moves, and nothing is rebuilt: stale, and the fix is named.
        with open(claim_path, encoding="utf-8") as fh:
            record = json.load(fh)
        record["statement"] += " (edited by hand)"
        with open(claim_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        info = cli_mod._site_state(self.root)
        self.assertTrue(info["stale"])
        self.assertIn("site build", info["stale_reason"])

    def test_a_verdict_written_after_the_build_reads_as_stale(self):
        """A stale sweep must LOOK stale, and so must a stale page — the case
        the `records_digest` rewrite lost (review, `repro_site`).

        What this replaced touched `ledger.json` after the build, which is what
        a `check` did before checkpoint 1.3: the verdicts lived in the ledger.
        They live in the verdict cache now, which no record digest covers, so
        its replacement edited a claim file and no longer guarded the risk it
        was named for (R-6): a sweep lands after the build, not one record
        moves, and the page still shows what it showed. `meta.judgement_digest`
        is what the page judged; `_site_state` asks the resolver again."""
        from nopekit import verdicts as verdicts_mod

        self._ledger(claims=[self._claim("C1")],
                     verdicts=[Verdict(gate="g.one", claims=["C1"], passed=True,
                                       measured=0.31, limit=0.5, units="mm")])
        _capture(["site", "init", "-C", self.root])
        _capture(["site", "build", "-C", self.root])
        info = cli_mod._site_state(self.root)
        self.assertFalse(info["stale"], info["stale_reason"])

        # The sweep lands after the build: a verdict into the cache, no record.
        records = store_mod.records_digest(self.root)
        verdicts_mod.record_verdict(self.root, None, None, Verdict(
            gate="g.two", claims=["C1"], passed=False, measured=0.71, limit=0.5,
            units="mm"))
        self.assertEqual(store_mod.records_digest(self.root), records,
                         "the precondition: not one record moved")
        info = cli_mod._site_state(self.root)
        self.assertTrue(info["stale"], "a verdict the page does not show read current")
        self.assertIn("site build", info["stale_reason"])

        # The positive control: a rebuild shows it, and is current again.
        _capture(["site", "build", "-C", self.root])
        self.assertIn("g.two", [row["gate"] for row in self._state()["verdicts"]])
        info = cli_mod._site_state(self.root)
        self.assertFalse(info["stale"], info["stale_reason"])

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
class ThePageIsCurrentOnlyWithItsVerdicts(_env.EnvCase):
    """Whether the PAGE is current, asked of what the page renders.

    `state.json` renders the records AND the resolver's judgement of the verdict
    cache against the live model. The records are one digest; the judgement is
    neither in it nor in any record, so a page can be built from the records as
    they are now and still show a PASS that every other reader calls FAIL.
    """

    def _run(self, project: str, *argv: str):
        proc = _env.nopekit(list(argv), cwd=project)
        self.assertIn(proc.returncode, (0, 1),
                      f"{' '.join(argv)} crashed:\n{proc.stdout}\n{proc.stderr}")
        return proc

    def _json(self, project: str, *argv: str) -> dict:
        proc = self._run(project, *argv, "--json")
        try:
            return json.loads(proc.stdout)
        except ValueError as exc:                  # pragma: no cover - reported
            raise AssertionError(f"{' '.join(argv)} --json: {exc}\n{proc.stdout}")

    def _page(self, project: str) -> dict:
        path = os.path.join(project, site_mod.SITE_DIR, site_mod.DATA_DIR,
                            site_mod.STATE_NAME)
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _says_current(self, project: str, why: str) -> None:
        """Every reader of the page's staleness says current: `site status`,
        `status` (text and JSON) and `doctor`."""
        shown = self._json(project, "site", "status")
        self.assertFalse(shown["stale"], f"{why}: {shown['stale_reason']}")
        self.assertFalse(self._json(project, "status")["site"]["stale"], why)
        self.assertNotIn("STALE", self._site_line(project), why)
        self.assertEqual(self._doctor_row(project)["status"], "ok", why)

    def _site_line(self, project: str) -> str:
        [line] = [text for text in self._run(project, "status").stdout.splitlines()
                  if text.startswith("site:")]
        return line

    def _doctor_row(self, project: str) -> dict:
        [row] = [row for row in self._json(project, "doctor")["checks"]
                 if row["check"] == "site"]
        return row

    def test_cli_a_check_that_fails_a_claim_leaves_the_page_stale(self):
        """V: the review's repro (``repro_site``). A page built while C1 passed,
        then the model thinned from 8 mm to 6 mm and a ``check`` that FAILs C1:
        ``status`` printed ``[FAIL ] C1`` and, two lines down, ``site: … built
        0.8s ago``; ``site status`` said "current with the records", ``doctor``
        ``[ok] site``, and the page still read C1 PASS and ready. The staleness
        compared ``records_digest`` only, and a check moves no record: before
        checkpoint 1.3 it rewrote ``ledger.json`` and the mtime rule caught it,
        and the rewrite's test edited a claim file instead, so nothing guarded
        the case any more (R-6)."""
        project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"),
                                         thickness=8.0, migrated=True)
        self._run(project, "check")
        self.assertEqual(self._json(project, "status")["claims"]["C1"], "pass",
                         "the precondition: C1 passes at 8 mm")
        self.assertEqual(self._run(project, "site", "init").returncode, 0)
        self.assertEqual(self._run(project, "site", "build").returncode, 0)
        [shown] = [row for row in self._page(project)["claims"] if row["id"] == "C1"]
        self.assertEqual(shown["status"], "pass", "the precondition: the page shows C1 PASS")

        # The negative half (cli:H16's property, kept): readers and a check that
        # changes nothing leave a current page current.
        self._says_current(project, "a page read stale straight after its own build")
        self._run(project, "check")
        self._says_current(project, "a check that changed nothing made the page stale")

        # The model moves and nothing has run: a rebuild would read C1 STALE,
        # so the page's PASS is already not current.
        _projects.set_thickness(project, 6.0)
        shown = self._json(project, "site", "status")
        self.assertTrue(shown["stale"], "a model edit left the page's PASS current")

        # The check lands: C1 FAILs everywhere but on the page.
        self.assertEqual(self._run(project, "check").returncode, 1)
        self.assertEqual(self._json(project, "status")["claims"]["C1"], "fail",
                         "the precondition: the check fails C1")
        [shown] = [row for row in self._page(project)["claims"] if row["id"] == "C1"]
        self.assertEqual(shown["status"], "pass", "the precondition: nothing rebuilt the page")
        shown = self._json(project, "site", "status")
        self.assertTrue(shown["stale"], "site status read a page showing C1 PASS as current "
                                        "after a check that FAILs it")
        self.assertIn("site build", shown["stale_reason"])
        self.assertIn("C1 pass -> fail", shown["stale_reason"],
                      "the reason must name what the page shows that is no longer so")
        self.assertTrue(self._json(project, "status")["site"]["stale"])
        self.assertIn("STALE", self._site_line(project))
        row = self._doctor_row(project)
        self.assertEqual(row["status"], "warn", row)
        self.assertIn("C1 pass -> fail", row["detail"])

        # The positive control: a rebuild shows the FAIL and is current again.
        self.assertEqual(self._run(project, "site", "build").returncode, 0)
        [shown] = [row for row in self._page(project)["claims"] if row["id"] == "C1"]
        self.assertEqual(shown["status"], "fail")
        self._says_current(project, "a rebuilt page read stale")


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
            "vendor/three.module.js": "https://nopekit-no-such-host.invalid/three.js"}
        self.addCleanup(setattr, site_mod, "vendor_urls", original)

        code, _out = _capture(["site", "vendor", "-C", self.root])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(os.path.join(site_dir, site_mod.VENDOR_DIR)),
                         "a failed vendor left a directory that shadows the CDN")
        leftovers = [n for n in os.listdir(site_dir) if n.startswith(".nopekit-vendor")]
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

        with self.assertRaises(NopekitError) as caught:
            cli_mod._fetch_vendor_file("https://cdn.example/three.module.js")
        self.assertIn("HTML", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
