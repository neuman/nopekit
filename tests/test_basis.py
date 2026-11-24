# SPDX-License-Identifier: Apache-2.0
"""Basis (SPEC 38, §0.4): where an evaluator's verdict logic comes from —
established, sourced or model-only — declared, shown, and never anything else.

It is never a status (invariant 4: a composition is identical whatever every
evaluator declares), never a key (invariant 7: rho is blind to it, and a
``pack.json`` basis edit re-keys nothing), and never a qualification route
(invariant 9: an evaluator outside the bundled packs that declares itself
``established`` is still mutation-walked, and unqualified when a mutation
passes). What slipped through the paper's first draft of the word: "grounded"
named both a whole system and the middle basis, so a reader could take a
basis for a verdict on the design.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import unittest
from unittest import mock

import _decisions as D
import _env

from nopekit import claims, gates, packs, report, verdicts
from nopekit.models import (BASES, BASIS_UNDECLARED, Acceptance, Claim, ClaimKind, Comparator,
                            GateSpec, Ledger, NegativeControl, PackManifest, Verdict)
from nopekit.util import NopekitError

#: SPEC §5.4's table, pinned as data: every bundled evaluator's basis. A change is
#: a decision, recorded in SPEC.md §8's phase-2 list, never a drift.
BUNDLED_BASES: dict[str, str] = {
    "simp.case_valid": "model-only",
    **{g: "established" for g in ("simp.volume", "simp.displacement", "simp.stress")},
    **{g: "sourced" for g in ("beam.deflection", "beam.deflection_ratio",
                              "beam.bending_stress", "beam.shear_stress", "beam.buckling",
                              "beam.bearing", "beam.model_validity")},
    "beam.input_sanity": "model-only",
    **{g: "model-only" for g in ("cad.bounding", "cad.watertight", "cad.degenerate_faces",
                                 "cad.wall_thickness", "cad.clash", "cad.assembly_connected")},
    "cad.is_volume": "established",
    "cad.clearance": "established",
    **{g: "established" for g in ("pcb.drc", "pcb.unconnected", "pcb.erc", "pcb.parity",
                                  "pcb.spice", "pcb.enclosure_fit")},
    # general-packs P5: charge_temperature replaces charge_protection and
    # fault_current replaces fuse_conductor, both sourced as before;
    # charge_limits is new and sourced (each limit a cell or charger datasheet
    # value in a record, against a published bound).
    **{g: "sourced" for g in ("pcb.pin_names", "pcb.net_width", "pcb.fab_rules",
                              "pcb.charge_temperature", "pcb.charge_limits",
                              "pcb.fault_current", "pcb.discharge_cutoff")},
    **{g: "model-only" for g in ("pcb.board_readable", "pcb.land_pattern")},
    # general-packs F1: firmware-ci replaces firmware-zmk. The guard follows
    # GitHub's documented shell and continue-on-error behaviour; the replay's
    # verdict is the toolchain's own exit code in the job's container; the image
    # and wake rules follow the bootloader's or partition table's cited layout and
    # each stack's sleep documentation. F2: fw.tx_power reads the stack's own
    # Kconfig resolution by rules cited to each stack's Kconfig and sources; its
    # setter scan is the pack's own and only moves a verdict toward a fail or a
    # skip, so the gate stays sourced (zmk.tx_power, which it replaces, was
    # established: it judged one symbol and no setter).
    "fw.build": "established",
    **{g: "sourced" for g in ("fw.ci_job", "fw.image_fits", "fw.sleep_wake",
                              "fw.tx_power")},
    "cfd.case_readable": "model-only",
    **{g: "model-only" for g in ("aero.case_readable", "aero.reference")},
    **{g: "sourced" for g in ("aero.regime", "aero.domain")},
    **{g: "established" for g in ("aero.mesh_quality", "aero.converged", "aero.drag_area")},
    **{g: "sourced" for g in ("aero.wall_treatment", "aero.mesh_independence")},
    **{g: "sourced" for g in ("cfd.regime", "cfd.mesh_independence")},
    **{g: "established" for g in ("cfd.mesh_quality", "cfd.converged", "cfd.pressure_drop")},
    **{g: "model-only" for g in ("fdm.overhang", "fdm.bridge_span", "fdm.bed_fit",
                                 "fdm.min_wall", "fdm.layer_alignment", "fdm.print_time_est",
                                 "fdm.process_model_valid", "fdm.slice")},
    **{g: "sourced" for g in ("fluid.drag", "fluid.pipe_pressure_drop", "fluid.flow_regime",
                              "fluid.buoyancy", "fluid.freeboard", "fluid.metacentric",
                              "fluid.righting_arm")},
    **{g: "model-only" for g in ("modelica.source_hygiene", "modelica.claims_addressable",
                                 "modelica.solution_valid", "modelica.mirror_agrees")},
    **{g: "established" for g in ("modelica.checks", "modelica.compiles", "modelica.simulates",
                                  "modelica.result_claim")},
    **{g: "model-only" for g in ("bom.complete", "bom.cost", "bom.availability", "bom.moq",
                                 "bom.single_source", "bom.currency")},
    "bom.process_rules": "sourced",
    **{g: "sourced" for g in ("thermal.conduction", "thermal.convection", "thermal.radiation",
                              "thermal.fin_efficiency", "thermal.steady_state_temp",
                              "thermal.time_constant", "solar.collector_output",
                              "solar.stagnation", "solar.irradiance",
                              "thermal.skin_temperature")},
    "thermal.h_agreement": "model-only",
    # general-packs E1: a count of ANSUR 1988 subjects through the survey's own
    # definitions (TR89-044 (125), (129), (130)); the girth term and the 20 deg
    # posture tolerance are the pack's, named in its PACK.md.
    "ergo.digit_reach": "sourced",
    # general-packs E2: two representations of one design held to agree, with
    # tessellation tolerances (0.5 mm, 1.0 mm) that are pack constants; no
    # outside source says what agreement is enough.
    "ergo.layout_agrees": "model-only",
}

#: The bracket's own evaluators (a project, per gate).
BRACKET_BASES = {"bracket.deflection": "sourced", "bracket.bending_stress": "sourced",
                 "bracket.bearing": "sourced", "bracket.model_validity": "sourced",
                 "bracket.bed_fit": "model-only", "bracket.min_wall": "model-only"}


def _ledger() -> Ledger:
    def claim(cid, quantity, limit):
        return Claim(id=cid, statement=cid, tags=[cid.lower()],
                     acceptance=Acceptance(quantity=quantity, comparator=Comparator.LE,
                                           limit=limit, units="mm"))

    def verdict(gate, cid, measured, passed=True, **kw):
        return Verdict(gate=gate, passed=passed, claims=[cid.lower()], measured=measured,
                       limit=1.0, units="mm", comparator="<=", settles="sag", **kw)
    return Ledger(claims=[claim("C1", "sag", 1.0), claim("C2", "sag", 1.0),
                          claim("C3", "sag", 1.0), claim("C4", "sag", 0.1)],
                  verdicts=[verdict("a.pass", "C1", 0.5), verdict("a.fail", "C2", 2.0, False),
                            verdict("a.skip", "C3", None, False, skipped=True,
                                    skip_reason="no tool"),
                            verdict("a.over", "C4", 0.5)])


def _specs(basis: str) -> list[GateSpec]:
    return [GateSpec(id=g, claims=[c], basis=basis,
                     negative_control=NegativeControl(fixture="x.py"))
            for g, c in (("a.pass", "c1"), ("a.fail", "c2"), ("a.skip", "c3"), ("a.over", "c4"))]


def basis_problems(compose) -> list[str]:
    """Every way ``compose`` (``claims.compositions`` or a planted one) lets the
    evaluators' declared basis move a status: the compositions under each basis,
    and undeclared, must be one."""
    ledger = _ledger()
    seen = {basis: {cid: (c.status, c.cause) for cid, c in
                    compose(ledger, registry=_specs(basis)).items()}
            for basis in (*BASES, "")}
    first = seen[""]
    return [f"{basis or 'undeclared'}: {found}" for basis, found in seen.items()
            if found != first]


class ABasisNeverChangesAStatus(_env.EnvCase):
    """(4) A claim's composition — and the summary every reader reads — is the
    same whatever basis its evaluators declare: Checked stays Checked beside a
    model-only evaluator, and an established one fails nothing it would not."""

    def test_the_composition(self):
        self.assertEqual(basis_problems(claims.compositions), [])

    def test_the_summary(self):
        ledger = _ledger()
        summaries = {basis: claims.summarise(ledger, _specs(basis))
                     for basis in (*BASES, "")}
        self.assertEqual(len({json.dumps(s, sort_keys=True, default=str)
                              for s in summaries.values()}), 1)

    def test_a_planted_compose_that_reads_basis_is_caught(self):
        real = claims.compositions

        def doubts_model_only(ledger, *, registry=None, **kw):
            out = real(ledger, registry=registry, **kw)
            if any(getattr(s, "basis", "") == "model-only" for s in registry or ()):
                return {cid: claims.Composed(claims.ClaimStatus.UNCLAIMED, c.cause)
                        for cid, c in out.items()}
            return out
        self.assertNotEqual(basis_problems(doubts_model_only), [])


class ABasisIsNeverAKey(_env.EnvCase):
    """(7) Basis is not in rho: specs differing only in basis digest alike, and a
    ``pack.json`` basis edit re-runs the module (it reaches the gate as
    ``PACK_BASIS``) and re-keys no entry."""

    def test_basis_is_not_a_rho_field(self):
        self.assertNotIn("basis", verdicts.SPEC_FIELDS_IN_RHO)

    def test_specs_differing_only_in_basis_digest_alike(self):
        registry = gates.Registry()
        packs.load_gates("beam-analytic", registry, root=None)
        spec, fn = registry.get("beam.deflection")
        digests = {verdicts.code_digest(dataclasses.replace(spec, basis=b), fn).digest
                   for b in (*BASES, "")}
        self.assertEqual(len(digests), 1)

    def test_a_pack_json_edit_moves_the_basis_and_no_key(self):
        base, name = D.fixture_pack(self.tmp())
        pack = os.path.join(base, name)
        registry = gates.Registry()
        with mock.patch.dict(os.environ, {packs.PACK_PATH_ENV: base}):
            packs.load_gates(name, registry, root=None)
            spec, fn = registry.get(D.GATE_ID)
            before = (spec.basis, verdicts.code_digest(spec, fn).digest)
            data = D.read_json(os.path.join(pack, "pack.json"))
            data["basis"] = "model-only"
            D.write_json(os.path.join(pack, "pack.json"), data)
            registry = gates.Registry()
            packs.load_gates(name, registry, root=None)
            spec, fn = registry.get(D.GATE_ID)
            after = (spec.basis, verdicts.code_digest(spec, fn).digest)
        self.assertEqual((before[0], after[0]), ("sourced", "model-only"),
                         "the module ran again with the new PACK_BASIS")
        self.assertEqual(before[1], after[1], "and no code digest moved")


class ABasisNeverQualifies(_env.EnvCase):
    """(9) Where an evaluator's code lives decides its qualification, never a
    basis it declares: one outside the bundled packs declaring ``established`` is
    still mutation-walked, and unqualified when a mutation passes."""

    def _keyed(self, planted=None):
        import test_admission as A
        p = A.QProject(self)
        spec, fn = p.registry.get("q.keyed")
        p.registry.register(dataclasses.replace(spec, basis="established"), fn, replace=True)
        if planted is None:
            return A.row(p.sweep(only=["q.keyed"]), "q.keyed").verdict.unqualified
        with mock.patch.object(verdicts, "_mutation_applies", planted(p.registry)):
            return A.row(p.sweep(only=["q.keyed"]), "q.keyed").verdict.unqualified

    def test_an_established_project_evaluator_is_still_walked(self):
        self.assertEqual(self._keyed(), "mutation:pass|0/1")

    def test_a_walk_that_trusts_established_is_caught(self):
        real = verdicts._mutation_applies

        def trusts(registry):
            def applies(fn, root):
                spec = next((s for s in registry.specs()
                             if registry.get(s.id)[1] is fn), None)
                if spec is not None and spec.basis == "established":
                    return False
                return real(fn, root)
            return applies
        self.assertEqual(self._keyed(trusts), "", "the planted walk qualifies it")


class BasisIsDeclaredAndShown(_env.EnvCase):
    """The keyword, the pack default, the refusals, and every place it is shown:
    ``gate list``, ``gate show``, ``gate list --json``, the report's evaluator
    cell and Reproduce comment, ``state.json``; ``pack validate`` requires it of
    a bundled pack and notes it elsewhere."""

    def test_the_keyword_and_the_pack_default(self):
        registry = gates.Registry()

        @gates.gate(id="t.declared", claims=["x"], basis="established", registry=registry,
                    negative_control=NegativeControl(fixture="x.py"))
        def declared(ctx):
            """t"""
        self.assertEqual(registry.get("t.declared")[0].basis, "established")
        module = sys.modules[__name__]
        with mock.patch.object(module, "PACK_BASIS", "sourced", create=True):
            @gates.gate(id="t.default", claims=["x"], registry=registry,
                        negative_control=NegativeControl(fixture="x.py"))
            def default(ctx):
                """t"""
        self.assertEqual(registry.get("t.default")[0].basis, "sourced")
        self.assertEqual(gates.basis_of(GateSpec(id="t.none")), BASIS_UNDECLARED)

    def test_a_malformed_basis_is_refused_and_grounded_is_told_its_word(self):
        registry = gates.Registry()
        for bad, said in (("grounded", "the basis word is sourced"),
                          ("solid", "is not a basis")):
            with self.subTest(bad), self.assertRaises(NopekitError) as caught:
                registry.register(GateSpec(id=f"t.{bad}", claims=["x"], basis=bad,
                                           negative_control=NegativeControl(fixture="x.py")),
                                  lambda ctx: None)
            self.assertIn(said, str(caught.exception))
            self.assertTrue(str(caught.exception).startswith(f"evaluator 't.{bad}': "),
                            "people see the word evaluator (SPEC §0.9)")
        base, name = D.fixture_pack(self.tmp())
        path = os.path.join(base, name, "pack.json")
        D.write_json(path, dict(D.read_json(path), basis="grounded"))
        with self.assertRaises(NopekitError) as caught:
            packs.read_manifest(os.path.dirname(path))
        self.assertIn("basis 'grounded' is not one of established, sourced, model-only",
                      str(caught.exception))

    def test_a_basis_word_is_said_beside_basis(self):
        """GLOSSARY §6: *established* is said only beside "basis", apart from
        invariant 10's *prerequisite not established*. Every human template that
        prints a basis word prints it as ``basis {basis}``. What slipped through
        (review of phase 2): ``proposed by x.y (established)`` in the same `why`
        that says *prerequisite not established*."""
        from nopekit import report
        bare = []
        for group, table in report.HUMAN.items():
            if not hasattr(table, "items"):
                continue
            for key, text in table.items():
                if isinstance(text, str) and "{basis}" in text \
                        and text.count("{basis}") != text.count("basis {basis}"):
                    bare.append(f"{group}.{key}: {text!r}")
        self.assertEqual(bare, [])
        said = report.HUMAN["basis"]["proposed_by"].format(id="x.y", basis="established")
        self.assertEqual(said, "proposed by x.y (basis established)")

    def test_describe_keeps_it_under_the_cap(self):
        spec = GateSpec(id="t.long", claims=["x"] * 80, basis="model-only", title="T" * 300,
                        requires_tools=["no-such-tool-anywhere"],
                        negative_control=NegativeControl(fixture="x.py"))
        line = gates.describe(spec)
        self.assertLessEqual(len(line), 240)
        self.assertIn("basis model-only", line)
        self.assertIn("not runnable here", line)
        self.assertIn("basis undeclared", gates.describe(GateSpec(id="t.u")))

    def test_the_bracket_shows_it(self):
        root = D.bracket(os.path.join(self.tmp(), "b"))
        _code, out, _err = D.main(root, "gate", "list")
        for gid, basis in BRACKET_BASES.items():
            self.assertIn(f"{gid} [t0] basis {basis} ", out)
        _code, out, _err = D.main(root, "gate", "list", "--json")
        doc = json.loads(out)
        self.assertEqual({g["id"]: g["basis"] for g in doc["gates"]}, BRACKET_BASES)
        self.assertEqual(doc["summary"]["by_basis"],
                         {"established": 0, "sourced": 4, "model-only": 2, "undeclared": 0})
        _code, out, _err = D.main(root, "gate", "show", "bracket.bearing")
        self.assertIn("  tier 0  pack (project)  basis sourced  entry ", out)
        _code, out, _err = D.main(root, "gate", "show", "bracket.bearing", "--json")
        self.assertEqual(json.loads(out)["basis"], "sourced")
        _code, out, _err = D.main(root, "report")
        self.assertIn("`bracket.bending_stress` (basis: sourced)", out)
        self.assertIn("— basis model-only — gates/structural.py", out)
        import _physical
        from nopekit import cli, site, store
        view, resolution = _physical.resolved(root)
        registry, _p = cli._registry(root, store.load(root), strict=False)
        state = site.state(root, view, registry, resolution=resolution)
        self.assertEqual({r["gate"]: r["basis"] for r in state["verdicts"]}, BRACKET_BASES)

    def test_validate_requires_it_of_a_bundled_pack(self):
        base, name = D.fixture_pack(self.tmp())
        pack = os.path.join(base, name)
        data = D.read_json(os.path.join(pack, "pack.json"))
        data.pop("basis")
        D.write_json(os.path.join(pack, "pack.json"), data)
        notes: list[str] = []
        problems = packs.validate(pack, notes=notes)
        self.assertNotIn(f"gate {D.GATE_ID}: no basis", " ".join(problems))
        self.assertIn(f"basis undeclared: {D.GATE_ID} (shows as undeclared)", notes)
        with mock.patch.object(packs, "origin_of", lambda *a, **k: "bundled"):
            problems = packs.validate(pack)
        self.assertTrue(any(p.startswith(f"evaluator {D.GATE_ID}: no basis — declare basis=")
                            for p in problems), problems)


class EveryBundledEvaluatorDeclaresABasis(_env.EnvCase):
    """Every bundled evaluator resolves to a basis, and to the one SPEC §5.4's
    table gives it; the bracket's six too."""

    def test_every_bundled_evaluator(self):
        registry = gates.Registry()
        for pack_dir, manifest in packs.discover_dirs(None):
            if packs.origin_of(pack_dir) == "bundled":
                packs.load_gates(manifest.name, registry, root=None)
        found = {spec.id: gates.basis_of(spec) for spec in registry.specs()}
        self.assertEqual(found, BUNDLED_BASES)

    def test_the_bracket(self):
        registry = gates.Registry()
        gates.load_project_gates(os.path.join(_env.REPO, "examples", "bracket"), registry)
        self.assertEqual({s.id: s.basis for s in registry.specs()}, BRACKET_BASES)

    def test_every_pack_json_says_one(self):
        for pack_dir, manifest in packs.discover_dirs(None):
            if packs.origin_of(pack_dir) == "bundled":
                with self.subTest(manifest.name):
                    self.assertIn(manifest.basis, BASES)


if __name__ == "__main__":
    unittest.main()
