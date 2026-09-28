# SPDX-License-Identifier: Apache-2.0
"""Every shipped pack must validate, and every gate must prove it can fail.

This is the gate on the gates. A pack whose validators have never demonstrated
failure is a pack of loggers, and merging one would quietly convert atompipe from
a thing that checks designs into a thing that agrees with them.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import uuid
from unittest import mock

import _env
from atompipe import gates as gates_mod
from atompipe import packs as packs_mod
from atompipe.models import Ledger, ProjectMeta, Tier
from atompipe.verdicts import GateTrace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKS_DIR = os.path.join(REPO, "packs")

#: Text that must never appear anywhere in this repository. atompipe was extracted
#: from a parent project (see docs/ORIGINS.md) and carries its method, not its
#: content — a leaked identifier means a pack is documenting somebody else's device
#: instead of its own domain.
FORBIDDEN = ("cyberdeck", "gripster", "thumbdeck", "keymat", "snap dome")


def _pack_dirs():
    if not os.path.isdir(PACKS_DIR):
        return []
    return sorted(
        os.path.join(PACKS_DIR, name)
        for name in os.listdir(PACKS_DIR)
        if os.path.isdir(os.path.join(PACKS_DIR, name))
        and os.path.isfile(os.path.join(PACKS_DIR, name, "pack.json"))
    )


# --------------------------------------------------------------------------- #
# the gate on the gates, as functions of one pack
# --------------------------------------------------------------------------- #
# Factored out of NegativeControlsFire so the rule is stated once and can be
# pointed at a planted pack as well as the bundled ones: a rule that has only
# ever been run against honest packs has never been shown to refuse anything.
# Every problem line starts with the gate id it is about.
#
# THE SKIP RULE (S-12). A skip is allowed only when `gates.availability(spec)`
# fails — the gate's declared tools are not on this machine. What slipped
# through before: the baseline test `continue`d past every skip, and the control
# test accepted any skipped control as "honestly blocked". So a gate that skipped
# its own baseline for want of a key was never shown to accept anything, and a
# fixture that DELETED the key its gate reads — instead of making it bad — made
# the gate skip on its known-bad input and passed invariants 3 and 6 while
# proving nothing. PACK_FORMAT's "nothing skips" was true only of honest packs.
# Rejected: judging a skip by its reason string ("tooling", "not installed"),
# which lets the gate decide for itself that its own skip is excusable.
def _read_baseline(pack_dir: str) -> dict | None:
    path = os.path.join(pack_dir, "selftest", "baseline.json")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


#: The one temp directory this process's contexts write under, removed at exit.
_OUT_ROOT: list[str] = []


def _scratch_out() -> str:
    """A fresh, empty ``out_dir`` under the temp dir, for one context.

    What slipped through: every context here pointed ``out_dir`` at
    ``<pack>/.selftest-out``, so the suite wrote 33 gates' evidence and the fdm
    fixtures' exported meshes into the tree it was testing — kept out of a commit
    only by an ignore rule, and on a wheel install written into site-packages
    (packs:H16). One directory per context, never one per pack: a file a
    previous run left behind must not become something the next run reads
    (packs:H5). The root goes at process exit, so a child process that imports
    these helpers cleans up after itself too.
    """
    if not _OUT_ROOT:
        root = tempfile.mkdtemp(prefix="atompipe-test-packs-out-")
        atexit.register(shutil.rmtree, root, True)
        _OUT_ROOT.append(root)
    return tempfile.mkdtemp(prefix="ctx-", dir=_OUT_ROOT[0])


def _pack_ctx(pack_dir: str, params: dict) -> gates_mod.GateContext:
    """A context carrying ``params`` as the projection, rooted in the pack.

    Without the pack's own baseline a gate SKIPS for want of a parameter and
    its control never fires — so the gate ships unproven while the suite reads
    green. A skipped control is not a passing control, and the whole
    credibility of this project rests on that distinction, so every pack ships
    `selftest/baseline.json`: a plausible, physically coherent projection that
    every one of its gates PASSES. The fixtures then move one thing and the
    gate must flip to FAIL.

    Its evidence goes to a fresh temp directory (``_scratch_out``), never into
    the pack under test.
    """
    return gates_mod.GateContext(
        root=pack_dir, ledger=Ledger(meta=ProjectMeta(name="selftest")), model=None,
        params=params, out_dir=_scratch_out(), tier=3,
        log=lambda _m: None, extra={})


def _tooling_absent(spec) -> str:
    """Why this gate cannot run on this machine, or ``""`` when it can."""
    ok, reason = gates_mod.availability(spec)
    return "" if ok else (reason or "not available here")


def _baseline_problems(pack_dir: str, registry: gates_mod.Registry, *,
                       skipped: list[str] | None = None) -> list[str]:
    """Every gate in ``registry`` that does not PASS the pack's own baseline.

    A skip counts only when the gate's tools are absent (the skip rule above);
    those land in ``skipped``, when given, so they are reported rather than hidden.
    """
    params = _read_baseline(pack_dir)
    if params is None:
        return [f"{os.path.basename(pack_dir)}: no selftest/baseline.json — without a "
                f"plausible projection its gates skip and its negative controls never "
                f"fire, so nothing here is proven"]
    ctx = _pack_ctx(pack_dir, params)
    problems: list[str] = []
    for spec in registry.specs():
        entry = registry.get(spec.id)
        if entry is None:
            problems.append(f"{spec.id}: vanished from the registry")
            continue
        _spec, fn = entry
        verdict = gates_mod.run_gate(spec, fn, ctx)
        if verdict.skipped:
            missing = _tooling_absent(spec)
            if missing:
                if skipped is not None:
                    skipped.append(f"{spec.id} ({missing})")
                continue
            problems.append(
                f"{spec.id}: SKIPPED its own pack's baseline while its tools are present "
                f"({verdict.skip_reason}) — a gate never shown to accept a good design "
                f"is not shown to measure anything; state what it reads in "
                f"selftest/baseline.json")
            continue
        if not verdict.ok:
            problems.append(f"{spec.id}: FAILS its own pack's baseline: "
                            f"{verdict.detail or verdict.error}")
    return problems


def _control_problems(pack_dir: str, registry: gates_mod.Registry, *, host: dict,
                      skipped: list[str] | None = None) -> list[str]:
    """Every gate in ``registry`` whose control does not fire with ``host`` as the projection.

    ``host`` is the projection the control's context carries — the pack's own
    baseline for NegativeControlsFire. A control that skips counts only when the
    gate's tools are absent (the skip rule above); those land in ``skipped``.
    """
    ctx = _pack_ctx(pack_dir, dict(host))
    problems: list[str] = []
    for spec in registry.specs():
        entry = registry.get(spec.id)
        if entry is None:
            problems.append(f"{spec.id}: vanished from the registry")
            continue
        _spec, fn = entry
        fixture = spec.negative_control.fixture if spec.negative_control else "?"
        verdict = gates_mod.selftest(spec, fn, ctx)
        if verdict.skipped:
            missing = _tooling_absent(spec)
            if missing:
                if skipped is not None:
                    skipped.append(f"{spec.id} ({missing})")
                continue
            problems.append(
                f"{spec.id}: SKIPPED its own known-bad fixture ({fixture}) while its "
                f"tools are present ({verdict.skip_reason}) — a control that makes its "
                f"gate skip has not fired; the fixture must make the input BAD, not "
                f"remove it")
            continue
        if not verdict.ok:
            problems.append(f"{spec.id}: did NOT fail its known-bad fixture ({fixture}): "
                            f"{verdict.detail or verdict.error} — the gate is a logger")
    return problems


# --------------------------------------------------------------------------- #
# planted packs: the violations the rules above must refuse
# --------------------------------------------------------------------------- #
_SCRATCH_GATE = """\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict


@gate(id={gate_id!r}, claims=["scratch"], tier=Tier.INSTANT,
      requires_python={requires_python!r},
      negative_control=NegativeControl(fixture="selftest/bad.py:{fixture}",
                                       note="planted by tests/test_packs.py"))
def span(ctx):
    \"\"\"A span limit that skips when its one input is missing.\"\"\"
    value = ctx.params.get("span_mm")
    if value is None:
        return Verdict(gate={gate_id!r}, skipped=True,
                       skip_reason="no span_mm in the projection")
    return Verdict(gate={gate_id!r}, passed=float(value) <= 100.0,
                   measured=float(value), limit=100.0, units="mm")
"""

_SCRATCH_FIXTURES = """\
import dataclasses


def too_long(ctx):
    \"\"\"The honest control: the one input moved past its limit.\"\"\"
    params = dict(ctx.params)
    params["span_mm"] = 500.0
    return dataclasses.replace(ctx, params=params)


def drop_span(ctx):
    \"\"\"The dishonest one: delete the input, so the gate cannot even look.\"\"\"
    params = {{k: v for k, v in ctx.params.items() if k != "span_mm"}}
    return dataclasses.replace(ctx, params=params)
"""


def _scratch_pack(case: unittest.TestCase, *, baseline: dict, fixture: str = "too_long",
                  requires_python: tuple[str, ...] = ()) -> tuple[str, gates_mod.Registry, str]:
    """A one-gate pack under the temp dir, loaded into a fresh registry.

    A unique pack name per call: pack modules are cached in ``sys.modules`` by
    pack NAME, and a second directory under a name already loaded is refused
    (tests:H6). The load runs under ``use_registry`` so the planted gate never
    reaches ``gates.REGISTRY``, which later in-process CLI tests read, and the
    modules it imported are dropped again on cleanup.
    """
    root = os.path.realpath(tempfile.mkdtemp(prefix="atompipe-scratch-pack-"))
    case.addCleanup(shutil.rmtree, root, True)
    name = f"scratch-{uuid.uuid4().hex[:12]}"
    gate_id = f"{name}.span"
    pack_dir = os.path.join(root, ".atompipe", "packs", name)
    os.makedirs(os.path.join(pack_dir, "gates"))
    os.makedirs(os.path.join(pack_dir, "selftest"))
    files = {
        "pack.json": json.dumps({"name": name}),
        os.path.join("gates", "span.py"): _SCRATCH_GATE.format(
            gate_id=gate_id, fixture=fixture, requires_python=list(requires_python)),
        os.path.join("selftest", "bad.py"): _SCRATCH_FIXTURES.format(),
        os.path.join("selftest", "baseline.json"): json.dumps(baseline),
    }
    for rel, text in files.items():
        with open(os.path.join(pack_dir, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def forget_modules() -> None:
        for mod_name, module in list(sys.modules.items()):
            origin = getattr(module, "__file__", None) or ""
            if origin and os.path.realpath(origin).startswith(root + os.sep):
                sys.modules.pop(mod_name, None)

    case.addCleanup(forget_modules)
    registry = gates_mod.Registry()
    with gates_mod.use_registry(registry):
        packs_mod.load_gates(name, registry, root=root)
    case.assertIsNotNone(registry.get(gate_id), f"the planted pack {name} registered nothing")
    return pack_dir, registry, gate_id


class PacksValidate(unittest.TestCase):
    def test_at_least_one_pack_ships(self):
        self.assertTrue(_pack_dirs(), "no packs found — packs/ is empty")

    def test_every_pack_validates(self):
        for path in _pack_dirs():
            with self.subTest(pack=os.path.basename(path)):
                problems = packs_mod.validate(path)
                self.assertEqual(problems, [], f"{os.path.basename(path)}: {problems}")

    def test_every_pack_ships_a_tier0_gate(self):
        """A pack of only expensive gates cannot be run in the inner loop, so it
        catches nothing while the design is still cheap to change (rule 10)."""
        for path in _pack_dirs():
            name = os.path.basename(path)
            with self.subTest(pack=name):
                registry = gates_mod.Registry()
                packs_mod.load_gates(name, registry, root=REPO)
                specs = registry.specs()
                self.assertTrue(specs, f"{name} registered no gates")
                self.assertTrue(
                    any(int(s.tier) == int(Tier.INSTANT) for s in specs),
                    f"{name} ships no tier-0 gate: "
                    f"{[(s.id, int(s.tier)) for s in specs]}")

    def test_every_pack_ships_a_validity_guard(self):
        """A pack of closed-form or correlation-based gates needs one gate that
        decides whether its other numbers mean anything.

        Slenderness guards beam theory; Biot guards lumped capacitance; Reynolds
        guards a drag correlation. Without one, the cheap gate silently becomes the
        wrong gate as the design moves out of the range it was valid for — and it
        keeps returning a confident pass the whole way.

        Packs that do not analyse anything (a sourcing or BOM pack has no model to
        outgrow) are exempt.
        """
        exempt = {"sourcing"}
        for path in _pack_dirs():
            name = os.path.basename(path)
            if name in exempt:
                continue
            with self.subTest(pack=name):
                registry = gates_mod.Registry()
                packs_mod.load_gates(name, registry, root=REPO)
                guards = [
                    s.id for s in registry.specs()
                    if any(w in f"{s.id} {s.settles} {s.title} {s.description}".lower()
                           for w in ("valid", "applicab", "regime", "guard", "polic",
                                     "assumption", "hygiene", "is_volume"))
                ]
                self.assertTrue(
                    guards,
                    f"{name} ships no validity guard — nothing tells a caller when the "
                    f"pack's own model has stopped applying (see docs/PACK_FORMAT.md)")

    def test_packs_publish_their_tag_vocabulary(self):
        """A claim can only bind correctly to a pack whose tag names are written
        down. `settles` is that vocabulary and it is what `atompipe gap` matches
        against, so an empty one makes the pack invisible to the capability-gap
        search that is supposed to find it."""
        for path in _pack_dirs():
            name = os.path.basename(path)
            with self.subTest(pack=name):
                manifest = packs_mod.read_manifest(path)
                self.assertTrue(
                    manifest.settles,
                    f"{name}: pack.json declares no `settles` vocabulary, so "
                    f"`atompipe gap` can never propose it for a capability gap")

class NegativeControlsFire(unittest.TestCase):
    """The central invariant, applied to every shipped gate."""

    def test_every_pack_ships_a_baseline(self):
        """No baseline means the pack's controls cannot be exercised in CI."""
        for path in _pack_dirs():
            with self.subTest(pack=os.path.basename(path)):
                self.assertTrue(
                    os.path.isfile(os.path.join(path, "selftest", "baseline.json")),
                    f"{os.path.basename(path)}: no selftest/baseline.json — without a "
                    f"plausible projection its gates skip and its negative controls "
                    f"never fire, so nothing here is proven")

    def test_every_gate_passes_its_own_baseline(self):
        """The baseline is supposed to describe a GOOD design.

        A gate that fails on it means one of two things, and both need a human:
        the baseline is not actually good, or the gate is wrong. Either way the
        negative control below proves nothing, because the gate was already
        failing before the fixture touched anything.

        A gate that SKIPS on it with its tools present is the same problem one
        step removed: it was never shown to pass anything (the skip rule, S-12).
        """
        for path in _pack_dirs():
            name = os.path.basename(path)
            registry = gates_mod.Registry()
            packs_mod.load_gates(name, registry, root=REPO)
            with self.subTest(pack=name):
                problems = _baseline_problems(path, registry)
                self.assertEqual(problems, [], "\n".join(problems))

    def test_every_gate_declares_a_negative_control(self):
        for path in _pack_dirs():
            name = os.path.basename(path)
            registry = gates_mod.Registry()
            packs_mod.load_gates(name, registry, root=REPO)
            for spec in registry.specs():
                with self.subTest(gate=spec.id):
                    self.assertIsNotNone(
                        spec.negative_control,
                        f"{spec.id} has no negative control — it should not have been "
                        f"registerable at all")

    def test_every_negative_control_fires_or_is_honestly_blocked(self):
        """Each gate must FAIL its own known-bad fixture.

        A gate whose dependency is absent reports SKIPPED, which is not a pass and
        not a failure — it is an absence of evidence, and it is allowed here
        because the alternative is refusing to run the suite anywhere the heavy
        tooling is not installed. It is reported so it cannot hide.

        Allowed ONLY then: a control that skips while `availability` says its
        tools are present is a problem (the skip rule, S-12).
        """
        skipped: list[str] = []
        for path in _pack_dirs():
            name = os.path.basename(path)
            registry = gates_mod.Registry()
            packs_mod.load_gates(name, registry, root=REPO)
            with self.subTest(pack=name):
                problems = _control_problems(path, registry,
                                             host=_read_baseline(path) or {},
                                             skipped=skipped)
                self.assertEqual(problems, [], "\n".join(problems))
        if skipped:
            print(f"\n  note: {len(skipped)} control(s) skipped for missing tooling: "
                  f"{', '.join(skipped[:4])}"
                  + ("..." if len(skipped) > 4 else ""))

    # -- the rule, shown to refuse (S-12) ------------------------------------ #
    def test_a_gate_that_skips_its_own_baseline_is_a_problem(self):
        """The baseline lacks the gate's one input, so the gate skips on it.

        Its tools are all present, so nothing about this machine excuses the
        skip: the gate has never been shown to accept anything.
        """
        pack_dir, registry, gate_id = _scratch_pack(self, baseline={"width_mm": 10.0})
        problems = _baseline_problems(pack_dir, registry)
        self.assertTrue(any(p.startswith(f"{gate_id}:") for p in problems),
                        f"a gate that skipped its own baseline with its tools present "
                        f"was accepted: {problems}")

    def test_a_fixture_that_deletes_a_needed_key_is_a_problem(self):
        """The control removes the input instead of making it bad, so the gate
        skips on its own known-bad fixture — which reads as "honestly blocked"
        unless the skip is held to availability."""
        pack_dir, registry, gate_id = _scratch_pack(
            self, baseline={"span_mm": 50.0}, fixture="drop_span")
        problems = _control_problems(pack_dir, registry, host=_read_baseline(pack_dir))
        self.assertTrue(any(p.startswith(f"{gate_id}:") for p in problems),
                        f"a control that skipped itself with its tools present was "
                        f"accepted as honestly blocked: {problems}")

    def test_the_planted_pack_is_clean_when_nothing_is_planted(self):
        """The positive control for the two above: the same pack with a baseline
        that states the input and a fixture that moves it has no problems, so
        they fail for the reason they name and not for a broken scratch pack."""
        pack_dir, registry, _gate_id = _scratch_pack(self, baseline={"span_mm": 50.0})
        self.assertEqual(_baseline_problems(pack_dir, registry), [])
        self.assertEqual(
            _control_problems(pack_dir, registry, host=_read_baseline(pack_dir)), [])

    def test_a_skip_for_missing_tooling_stays_honest(self):
        """The exception the rule keeps: a gate whose declared tools are absent
        skips, is reported, and is not a problem — or the suite could not run
        anywhere the heavy tooling is not installed."""
        absent = f"atompipe_absent_{uuid.uuid4().hex[:12]}"
        pack_dir, registry, gate_id = _scratch_pack(
            self, baseline={"width_mm": 10.0}, fixture="drop_span",
            requires_python=(absent,))
        skipped_base: list[str] = []
        skipped_ctl: list[str] = []
        self.assertEqual(_baseline_problems(pack_dir, registry, skipped=skipped_base), [])
        self.assertEqual(_control_problems(pack_dir, registry, host={},
                                           skipped=skipped_ctl), [])
        self.assertTrue(any(s.startswith(gate_id) and absent in s for s in skipped_base),
                        skipped_base)
        self.assertTrue(any(s.startswith(gate_id) and absent in s for s in skipped_ctl),
                        skipped_ctl)


# --------------------------------------------------------------------------- #
# the seal, read off the trace (invariant 5 at runtime)
# --------------------------------------------------------------------------- #
def _host_reads(spec, fn, host: gates_mod.GateContext) -> tuple[str, ...] | None:
    """This file's own reading of one control's trace: every host-param path its
    fixture read — or its gate read, through a context the fixture handed back —
    dotted, ``"(all params)"`` for the whole top level. ``None`` when the gate's
    tools are absent, so the control could not run and read nothing.

    Kept beside ``packs.seal_findings`` rather than trusting it alone, for the
    reason DemonstrateAgrees gives: a test that only calls the code it guards is
    relaxed by relaxing that code, with no test file touched (R-6). A detector
    that quietly stopped looking at some gates would still return ``[]``.
    """
    if _tooling_absent(spec):
        return None
    trace = GateTrace(kind="control")
    gates_mod.selftest(spec, fn, host, trace=trace, out_dir=_scratch_out())
    return tuple(sorted(".".join(str(part) for part in path) or "(all params)"
                        for path in trace.host_reads))


def _beam_copy(case: unittest.TestCase, plant=None) -> tuple[str, str]:
    """``(root, pack_dir)``: beam-analytic copied under a unique directory AND pack
    name, with ``plant(pack_dir)`` applied. Unplanted, the copy is publishable
    (DemonstrateAgrees' positive control), so a plant is the only thing `pack
    validate` can object to. Judge it in a child process only: pack modules are
    cached by pack name, one directory per name per process (tests:H6)."""
    root = os.path.realpath(tempfile.mkdtemp(prefix="atompipe-sealed-beam-"))
    case.addCleanup(shutil.rmtree, root, True)
    name = f"beam-sealed-{uuid.uuid4().hex[:12]}"
    pack_dir = os.path.join(root, ".atompipe", "packs", name)
    shutil.copytree(os.path.join(PACKS_DIR, "beam-analytic"), pack_dir,
                    ignore=shutil.ignore_patterns("__pycache__", ".selftest-out"))
    manifest_path = os.path.join(pack_dir, "pack.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest["name"] = name
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    if plant is not None:
        plant(pack_dir)
    return root, pack_dir


#: The unsealed form PACK_FORMAT warns about, planted in beam.deflection's
#: fixture: the pack's WHOLE baseline and the bad limit, layered over the host's
#: ctx.params. Over the baseline it fires, and over an empty host it fires
#: identically — it states every key its gate reads — so the empty-host probe
#: passes it. What it lets through is any key the host states and the baseline
#: does not: a synonym, a derived quantity, the waterplane inertia that defused
#: a hull control in the case the class docstring below tells.
_LAYERED_OVER_HOST = ('    return dataclasses.replace(\n'
                      '        ctx, params={**ctx.params, **BASELINE, '
                      '"deflection_limit_mm": 1e-6})\n')

#: The words every seal problem starts with, after ``"<gate id>: "``.
_SEAL_PHRASE = "control reads the host's ctx.params"


class ControlsAreSealed(unittest.TestCase):
    """A pack's negative control must fire in EVERY project, not just a friendly one.

    A pack ships to strangers. If its fixture layers the known-bad values over the
    host project's projection, a key the fixture never mentions can still arrive
    from the project and neutralise the control — and gates resolve synonym
    families and derived quantities, so this happens without anyone writing
    anything wrong.

    It was observed live: a hull fixture raised the centre of gravity to make a
    boat unstable, the host project happened to state a waterplane inertia (a
    perfectly honest thing to state, better than the pack's own fallback), the
    metacentric height came out strongly positive, and the gate PASSED ITS OWN
    KNOWN-BAD FIXTURE. A control whose severity depends on the host project's
    numbers is a control that passes in some repositories and fails in others,
    which is the same as having none.

    The probe: run every control against an EMPTY projection as well as against
    the pack's baseline. A sealed fixture states everything its gate reads and
    behaves identically; an inheriting one skips or flips. Project-local fixtures
    are exempt from this — deriving from the project's own model is correct there
    — but a pack's are not.

    The probe sees only what an empty host changes, and that is not everything:
    a fixture that layers the pack's WHOLE baseline over the host fires the same
    both ways and passes it, while any key the host states beyond the baseline
    still reaches the gate. So the seal is also read off the trace: every read a
    control makes through its host's ``ctx.params`` is recorded, and a SEALED
    fixture makes none (``packs.seal_findings``, which ``pack validate`` refuses
    on). Staged as R-4 asks: zero findings over the bundled controls first, then
    the planted violators.
    """

    def test_controls_fire_without_a_host_projection(self):
        for path in _pack_dirs():
            name = os.path.basename(path)
            registry = gates_mod.Registry()
            packs_mod.load_gates(name, registry, root=REPO)
            baseline_path = os.path.join(path, "selftest", "baseline.json")
            with open(baseline_path, "r", encoding="utf-8") as fh:
                baseline = json.load(fh)

            def ctx_with(params):
                return gates_mod.GateContext(
                    root=path, ledger=Ledger(meta=ProjectMeta(name="sealed")),
                    model=None, params=params,
                    out_dir=_scratch_out(), tier=3,
                    log=lambda _m: None, extra={})

            for spec in registry.specs():
                _spec, fn = registry.get(spec.id)
                with self.subTest(gate=spec.id):
                    rich = gates_mod.selftest(spec, fn, ctx_with(dict(baseline)))
                    bare = gates_mod.selftest(spec, fn, ctx_with({}))
                    if not rich.passed:
                        continue          # covered by the other suite
                    self.assertTrue(
                        bare.passed and not bare.skipped,
                        f"{spec.id}: its control fires against the pack baseline but "
                        f"{'skips' if bare.skipped else 'does NOT fire'} against an empty "
                        f"projection — the fixture is inheriting from the host project "
                        f"instead of stating everything its gate reads, so installing "
                        f"this pack in a different project can silently defuse it "
                        f"({bare.skip_reason or bare.detail})")

    # -- the trace sees what the probe cannot (R-4: the measurement first) --- #
    def test_no_bundled_fixture_reads_host_params(self):
        """Every bundled control, against a RICH host — its pack's own baseline,
        every key its gates read there to be read — reads nothing of the host's
        ``ctx.params``: by the spine's detector, and by this file's own reading
        of the same traces. This is the measurement the refusal stands on (R-4):
        a detector with hits on honest bundled code would turn the suite red the
        moment it refused.

        Measured when it landed (2026-09-27, U18): zero host reads over 54 of 54
        controls with trimesh, numpy and omc present.
        """
        exercised = 0
        skipped: list[str] = []
        for path in _pack_dirs():
            name = os.path.basename(path)
            registry = gates_mod.Registry()
            packs_mod.load_gates(name, registry, root=REPO)
            host = _pack_ctx(path, _read_baseline(path) or {})
            with self.subTest(pack=name):
                findings = packs_mod.seal_findings(registry, host, tier=Tier.EXTERNAL,
                                                   out_dir=_scratch_out())
                own: dict[str, tuple[str, ...]] = {}
                for spec in registry.specs():
                    _spec, fn = registry.get(spec.id)
                    reads = _host_reads(spec, fn, host)
                    if reads is None:
                        skipped.append(f"{spec.id} ({_tooling_absent(spec)})")
                        continue
                    exercised += 1
                    if reads:
                        own[spec.id] = reads
                self.assertEqual(own, {}, f"{name}: fixtures reading the host's ctx.params, "
                                          f"by this file's reading of the traces: {own}")
                self.assertEqual(findings, [], f"{name}: {findings}")
        # Not vacuous: the analytic packs declare no tools, so on any machine —
        # a CI runner without trimesh or omc included — their controls ran.
        self.assertGreater(exercised, 0, "no control ran, so nothing was measured")
        if skipped:
            print(f"\n  note: seal detector: {exercised} control(s) traced, "
                  f"{len(skipped)} skipped for missing tooling: "
                  f"{', '.join(skipped[:4])}" + ("..." if len(skipped) > 4 else ""))

    def test_planted_unsealed_fixture_is_caught(self):
        """V: a fixture that layers over the host is named even where the
        empty-host probe cannot see it — by the detector, by ``demonstrate``,
        and by `pack validate`'s exit status.

        End to end first, because that is the hole: a copy of beam-analytic
        whose deflection fixture states the whole baseline over the host
        (``_LAYERED_OVER_HOST``) fires with any host, so before the detector
        `pack validate` certified it publishable. It is the only thing wrong
        with the copy, so the exit status is about it and nothing else.
        """
        root, pack_dir = _beam_copy(self, lambda d: _plant(
            d, "selftest/bad_beams.py", _SHALLOW_DEF, _LAYERED_OVER_HOST))
        proc = _env.atompipe(["pack", "validate", pack_dir, "--json"], cwd=root)
        self.assertEqual(proc.returncode, 1,
                         f"pack validate certified an unsealed fixture as publishable:\n"
                         f"{proc.stdout}\n{proc.stderr}")
        problems = json.loads(proc.stdout)["problems"]
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith(f"beam.deflection: {_SEAL_PHRASE} ("),
                        problems)

        # In process, on the scratch pack: its `too_long` fixture copies the
        # host's params and moves the one input. The probe passes it — it fires
        # over the baseline and over an empty host alike — and the trace does not.
        pack_dir, registry, gate_id = _scratch_pack(self, baseline={"span_mm": 50.0})
        baseline = _read_baseline(pack_dir)
        self.assertEqual(_control_problems(pack_dir, registry, host=baseline), [])
        self.assertEqual(_control_problems(pack_dir, registry, host={}), [])
        host = _pack_ctx(pack_dir, baseline)
        findings = packs_mod.seal_findings(registry, host, out_dir=_scratch_out())
        self.assertEqual([f.gate for f in findings], [gate_id], findings)
        self.assertEqual(findings[0].fixture, "selftest/bad.py:too_long")
        self.assertIn("(all params)", findings[0].host_paths)   # dict(ctx.params)
        _spec, fn = registry.get(gate_id)
        self.assertEqual(findings[0].host_paths, _host_reads(_spec, fn, host))
        shown = packs_mod.demonstrate(pack_dir, tier=Tier.EXTERNAL)
        self.assertEqual(len(shown.problems), 1, shown.problems)
        self.assertTrue(shown.problems[0].startswith(f"{gate_id}: {_SEAL_PHRASE} ("),
                        shown.problems)

    def test_identity_fixture_in_a_pack_is_caught(self):
        """V: ``return ctx`` hands the gate the host's own projection, so the
        gate's reads through the host view are the findings. Against a good
        host its control does not fire; against a host that is itself bad it
        "fires" — for the host's reason, not its own — and the trace names it
        just the same, which no outcome-based probe over that host could."""
        pack_dir, registry, gate_id = _scratch_pack(self, baseline={"span_mm": 50.0},
                                                    fixture="identity")
        with open(os.path.join(pack_dir, "selftest", "bad.py"), "a", encoding="utf-8") as fh:
            fh.write('\n\ndef identity(ctx):\n'
                     '    """Planted: the host context, handed back untouched."""\n'
                     '    return ctx\n')
        _spec, fn = registry.get(gate_id)
        for span in (50.0, 500.0):
            with self.subTest(host_span_mm=span):
                host = _pack_ctx(pack_dir, {"span_mm": span})
                fired = gates_mod.selftest(_spec, fn, host)
                self.assertEqual(fired.passed, span > 100.0, fired.detail or fired.error)
                findings = packs_mod.seal_findings(registry, host, out_dir=_scratch_out())
                self.assertEqual([(f.gate, f.fixture, f.host_paths) for f in findings],
                                 [(gate_id, "selftest/bad.py:identity", ("span_mm",))])
                self.assertEqual(_host_reads(_spec, fn, host), ("span_mm",))
        shown = packs_mod.demonstrate(pack_dir, tier=Tier.EXTERNAL)
        self.assertTrue(any(line.startswith(f"{gate_id}: {_SEAL_PHRASE} (span_mm)")
                            for line in shown.problems), shown.problems)


# --------------------------------------------------------------------------- #
# the spine's copy of the gate on the gates, held to this file's (D-25)
# --------------------------------------------------------------------------- #
def _oracle(pack_dir: str, registry: gates_mod.Registry) -> dict[str, list[str]]:
    """This file's own verdict on one pack, as the classes above compute it.

    Three passes: the pack's baseline (NegativeControlsFire), each control over
    that baseline (NegativeControlsFire), and each control over an EMPTY host
    (ControlsAreSealed's probe). Problems and tooling skips, each line starting
    with the gate id it is about.
    """
    skipped: list[str] = []
    problems = _baseline_problems(pack_dir, registry, skipped=skipped)
    problems += _control_problems(pack_dir, registry, host=_read_baseline(pack_dir) or {},
                                  skipped=skipped)
    problems += _control_problems(pack_dir, registry, host={}, skipped=skipped)
    return {"problems": problems, "skipped": skipped}


def _flagged(problems: list[str]) -> set[str]:
    """The gate ids a list of ``"<gate id>: <why>"`` lines is about."""
    return {line.split(":", 1)[0] for line in problems}


def _skipped_ids(entries: list[str]) -> set[str]:
    """The gate ids a list of ``"<gate id> (<reason>)"`` skip lines names."""
    return {entry.split(" (", 1)[0] for entry in entries}


#: What a child process prints for DemonstrateAgrees: both verdicts on one
#: planted pack, and `packs.validate`'s, as ONE JSON document on its last stdout
#: line. A child because pack modules are cached by pack NAME in sys.modules, and
#: a second copy of a pack in one process is refused or — worse — answered from
#: the first copy's code (tests:H6, core:§5.12). `demonstrate` runs first, so its
#: own loader meets the pack cold; the oracle then loads it by name, as every
#: class above does.
_AGREE_SCRIPT = """\
import json, os, sys
tests_dir, root, name = sys.argv[1:4]
sys.path.insert(0, tests_dir)
import test_packs as T
from atompipe import gates, packs
from atompipe.models import Tier
pack_dir = os.path.join(root, ".atompipe", "packs", name)
demo = packs.demonstrate(pack_dir, tier=Tier.EXTERNAL)
registry = gates.Registry()
packs.load_gates(name, registry, root=root)
oracle = T._oracle(pack_dir, registry)
notes = []
validated = packs.validate(pack_dir, notes=notes)
print(json.dumps({"gates": registry.ids(), "oracle": oracle,
                  "demonstrate": {"problems": demo.problems, "skipped": demo.skipped,
                                  "ran": demo.ran},
                  "validate": validated, "notes": notes}))
"""

#: The line each plant anchors on. Exactly one occurrence is demanded, so an edit
#: to beam-analytic that moves it turns these tests red instead of planting
#: nothing and passing on a clean pack.
_DEFLECTION_DEF = "def deflection(ctx: GateContext) -> Verdict:\n"
_SHALLOW_DEF = "def shallow_section(ctx):\n"


def _plant(pack_dir: str, rel: str, anchor: str, body: str) -> None:
    """Insert ``body`` as the first statement after ``anchor`` in one pack file."""
    path = os.path.join(pack_dir, *rel.split("/"))
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if text.count(anchor) != 1:
        raise AssertionError(f"{rel}: expected exactly one {anchor.strip()!r} to plant "
                             f"after, found {text.count(anchor)}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(anchor, anchor + body))


def _snapshot(root: str) -> dict[str, tuple]:
    """Every directory and file under ``root``: size, mtime and the bytes' digest.

    ``__pycache__`` is left out by name: Python's import system writes bytecode
    beside a module the first time anything imports it — the pack's code writes
    none of it, the tree ignores it, and whether it is already there depends on
    which test ran first. A directory is a key with no stamp: a new one is
    caught, while its mtime would move with that bytecode.
    """
    out: dict[str, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        out[os.path.relpath(dirpath, root) + "/"] = ()
        for filename in sorted(filenames):
            full = os.path.join(dirpath, filename)
            info = os.lstat(full)
            with open(full, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            out[os.path.relpath(full, root)] = (info.st_size, info.st_mtime_ns, digest)
    return out


def _changed(before: dict[str, tuple], after: dict[str, tuple]) -> list[str]:
    """What differs between two snapshots, one line per path."""
    lines = [f"{p}: created" for p in sorted(set(after) - set(before))]
    lines += [f"{p}: removed" for p in sorted(set(before) - set(after))]
    lines += [f"{p}: rewritten" for p in sorted(set(before) & set(after))
              if before[p] != after[p]]
    return lines


class DemonstrateAgrees(unittest.TestCase):
    """``packs.demonstrate`` names exactly the gates this file's oracle names (D-25).

    `pack validate` runs the spine's COPY of the gate on the gates. The classes
    above keep their own assertions as an independent
    oracle rather than delegating to that copy: a test that calls the code it
    guards is relaxed by relaxing the code, with no test file touched (R-6). This
    class is what stops the two drifting apart — on every bundled pack, and on
    planted violators, because a rule that has only met honest packs has never
    been shown to refuse anything.

    Before it: `pack validate` never ran a control and certified a planted
    ``return True`` as publishable (S-09), and `gate selftest` tested only the
    reject half, so an always-False gate passed it (S-04).
    """

    def test_bundled_packs(self):
        """Both sides empty on every bundled pack, openmodelica's three SOLVE
        gates included or availability-skipped on both sides alike."""
        for path in _pack_dirs():
            name = os.path.basename(path)
            with self.subTest(pack=name):
                registry = gates_mod.Registry()
                packs_mod.load_gates(name, registry, root=REPO)
                oracle = _oracle(path, registry)
                demo = packs_mod.demonstrate(path, tier=Tier.EXTERNAL)
                self.assertEqual(_flagged(demo.problems), _flagged(oracle["problems"]),
                                 f"demonstrate: {demo.problems}\noracle: {oracle['problems']}")
                self.assertEqual(demo.problems, [], "\n".join(demo.problems))
                skipped = _skipped_ids(demo.skipped)
                self.assertEqual(skipped, _skipped_ids(oracle["skipped"]))
                # At tier 3 every gate is in scope: each one either ran its
                # control or was skipped for missing tooling. A demonstration
                # that dropped a gate would otherwise read as a clean one.
                self.assertEqual(demo.ran + len(skipped), len(registry.specs()),
                                 f"{name}: ran {demo.ran}, skipped {sorted(skipped)}, "
                                 f"of {registry.ids()}")

    # -- planted violators, each in its own process -------------------------- #
    def _planted(self, plant=None) -> dict:
        """A copy of beam-analytic under a unique directory AND pack name, with
        ``plant(pack_dir)`` applied, judged by both sides in a child process."""
        root = os.path.realpath(tempfile.mkdtemp(prefix="atompipe-planted-beam-"))
        self.addCleanup(shutil.rmtree, root, True)
        name = f"beam-planted-{uuid.uuid4().hex[:12]}"
        pack_dir = os.path.join(root, ".atompipe", "packs", name)
        shutil.copytree(os.path.join(PACKS_DIR, "beam-analytic"), pack_dir,
                        ignore=shutil.ignore_patterns("__pycache__", ".selftest-out"))
        manifest_path = os.path.join(pack_dir, "pack.json")
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        manifest["name"] = name
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        if plant is not None:
            plant(pack_dir)
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        proc = _env.run([sys.executable, "-c", _AGREE_SCRIPT, tests_dir, root, name],
                        cwd=root)
        self.assertEqual(proc.returncode, 0, f"the agreement child failed:\n{proc.stderr}")
        lines = proc.stdout.strip().splitlines()
        self.assertTrue(lines, f"the agreement child printed nothing:\n{proc.stderr}")
        return json.loads(lines[-1])

    def _assert_named(self, out: dict, gate_id: str, phrase: str) -> None:
        oracle = out["oracle"]["problems"]
        demo = out["demonstrate"]["problems"]
        self.assertEqual(_flagged(oracle), {gate_id}, f"the oracle: {oracle}")
        self.assertEqual(_flagged(demo), {gate_id},
                         f"demonstrate disagrees with the oracle ({oracle}): {demo}")
        self.assertTrue(any(phrase in line for line in demo),
                        f"demonstrate named {gate_id} for another reason than "
                        f"{phrase!r}: {demo}")
        # `pack validate` refuses it too, with nothing else to say: the copy is
        # otherwise publishable, and before demonstrate this list was empty for a
        # planted logger (S-09).
        self.assertEqual(out["validate"], demo)

    def test_the_planted_copy_is_clean_when_nothing_is_planted(self):
        """The positive control for the violators below: the copy itself — new
        directory, new pack name, a cold process — is clean on both sides and
        publishable, so each of them fails for what was planted and not for the
        copying."""
        out = self._planted()
        self.assertEqual(out["oracle"]["problems"], [])
        self.assertEqual(out["demonstrate"]["problems"], [])
        self.assertEqual(out["demonstrate"]["skipped"], [])
        self.assertEqual(out["demonstrate"]["ran"], len(out["gates"]))
        self.assertEqual(out["validate"], [])
        self.assertEqual(out["notes"], [])

    def test_a_gate_that_always_passes_is_named(self):
        """``return True``: passes its baseline, and its known-bad input too."""
        out = self._planted(lambda d: _plant(d, "gates/beam.py", _DEFLECTION_DEF,
                                             "    return True\n"))
        self._assert_named(out, "beam.deflection", "control did not fire")

    def test_a_gate_that_always_fails_is_named(self):
        """``return False``: its control fires, and proves nothing, because it
        fails the good design as well (S-04)."""
        out = self._planted(lambda d: _plant(d, "gates/beam.py", _DEFLECTION_DEF,
                                             "    return False\n"))
        self._assert_named(out, "beam.deflection", "fails its own baseline")

    def test_a_fixture_that_deletes_a_key_is_named(self):
        """The control removes the input the gate reads instead of making it bad,
        so the gate skips on its known-bad input with its tools present (S-12)."""
        body = ('    params = {k: v for k, v in BASELINE.items() '
                'if k != "deflection_limit_mm"}\n'
                '    return dataclasses.replace(ctx, params=params)\n')
        out = self._planted(lambda d: _plant(d, "selftest/bad_beams.py", _SHALLOW_DEF, body))
        self._assert_named(out, "beam.deflection",
                           "control did not fire: skipped on its own known-bad input")

    def test_a_gate_that_skips_its_own_baseline_is_named(self):
        """The gate reads a key its pack's baseline never states, so it skips
        the good design with every tool it declares present: never shown to
        accept anything (S-12)."""
        body = ('    if "planted_key_mm" not in ctx.params:\n'
                '        return Verdict(gate="beam.deflection", passed=False, skipped=True,\n'
                '                       skip_reason="no planted_key_mm in the projection")\n')
        out = self._planted(lambda d: _plant(d, "gates/beam.py", _DEFLECTION_DEF, body))
        self._assert_named(out, "beam.deflection",
                           "skips its own baseline while its tools are present")

    def test_a_fixture_that_inherits_from_the_host_is_named(self):
        """The seal probe, shown to refuse: the fixture layers its bad limit over
        whatever the host states instead of over the pack's own baseline. Over
        the baseline it fires; over an empty host the gate has nothing to read
        (invariant 5)."""
        body = ('    return dataclasses.replace(\n'
                '        ctx, params={**ctx.params, "deflection_limit_mm": 1e-6})\n')
        out = self._planted(lambda d: _plant(d, "selftest/bad_beams.py", _SHALLOW_DEF, body))
        self._assert_named(out, "beam.deflection", "fires only with the baseline as host")

    def test_a_missing_fixture_file_is_named_once_by_validate(self):
        """Both sides name a control whose fixture file does not exist; `pack
        validate` names it ONCE, by its root cause, not again as a control that
        did not fire."""
        def plant(pack_dir: str) -> None:
            path = os.path.join(pack_dir, "gates", "beam.py")
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            ref = 'fixture="selftest/bad_beams.py:shallow_section"'
            self.assertEqual(text.count(ref), 1)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text.replace(ref, 'fixture="selftest/no_such_fixture.py"'))

        out = self._planted(plant)
        self.assertEqual(_flagged(out["oracle"]["problems"]), {"beam.deflection"})
        self.assertEqual(_flagged(out["demonstrate"]["problems"]), {"beam.deflection"})
        about = [line for line in out["validate"] if "beam.deflection" in line]
        self.assertEqual(len(about), 1, out["validate"])
        self.assertIn("does not exist", about[0])

    # -- nothing written into a pack ----------------------------------------- #
    def test_openmodelica_selftest_writes_nothing_in_the_pack(self):
        """Running every openmodelica control — this file's way and the spine's —
        leaves the pack directory byte- and mtime-identical.

        What slipped through: its five tier-0 fixtures wrote their known-bad
        files into ``selftest/.generated/`` whatever ``out_dir`` the caller gave,
        falling back to a fixed ``$TMPDIR/atompipe-openmodelica`` shared by every
        user on the machine — so a wheel install wrote into site-packages, and
        the repository's own suite rewrote files inside the tree it tests
        (packs:H16).
        """
        pack = os.path.join(PACKS_DIR, "openmodelica")
        registry = gates_mod.Registry()
        packs_mod.load_gates("openmodelica", registry, root=REPO)
        before = _snapshot(pack)

        skipped: list[str] = []
        _control_problems(pack, registry, host=_read_baseline(pack) or {}, skipped=skipped)
        _control_problems(pack, registry, host={}, skipped=skipped)
        # Not vacuous: the tier-0 gates declare no tools, so their controls — the
        # five fixtures that generate files — ran in both passes.
        writers = {s.id for s in registry.specs() if int(s.tier) == int(Tier.INSTANT)}
        self.assertEqual(len(writers), 5, registry.ids())
        self.assertFalse(writers & _skipped_ids(skipped), skipped)
        self.assertEqual(_changed(before, _snapshot(pack)), [],
                         "this file's controls wrote into packs/openmodelica")

        packs_mod.demonstrate(pack, tier=Tier.EXTERNAL)
        self.assertEqual(_changed(before, _snapshot(pack)), [],
                         "packs.demonstrate wrote into packs/openmodelica")


# --------------------------------------------------------------------------- #
# the provenance scan
# --------------------------------------------------------------------------- #
#: Directory names never descended into: git's object store and bytecode caches.
_PRUNE_NAMES = frozenset({".git", "__pycache__"})

#: Exempt from the scan, relative to the root walked: the one file allowed to
#: name the parent project, and this one, which must spell the words to refuse
#: them.
_ALLOWED = frozenset({os.path.join("docs", "ORIGINS.md"),
                      os.path.join("tests", "test_packs.py")})

#: The real walk must read more files than this, or it is not the walk it claims
#: to be: pruning the root itself scans nothing and passes (tests:H3), and that
#: shape of vacuity is invisible from the hit list alone.
#: Why 150 (measured 2026-09-27 on `git archive` exports): the tracked tree at
#: 7ecf953 holds 182 files and this walk reads 175 of them (5 binary STLs, the 2
#: exempt files); with this commit's three new test files, 178. Phase 1.3 deletes
#: a handful (runs/) and adds more (claims/, cache entries), so 150 sits below the
#: real count with room to spare and far above any broken walk. Rejected: 200 (the
#: spec's figure — a clean clone, which is what `verify.sh <ref>` and CI scan, has
#: 178 and would read red for nothing; only a dev checkout with an untracked
#: build/ clears 200); any figure near 0 (a walk that read one README would pass
#: it). The count cannot tell the old allow-list (152 files at 7ecf953) from this
#: walk; test_every_kind_of_tracked_text_is_read carries that.
_MIN_SCANNED = 150


def _other_tree(path: str) -> bool:
    """Is the subdirectory ``path`` somebody else's tree, not this repository's?

    * A ``.git`` entry — a directory (a clone) or a FILE (a linked worktree or a
      submodule). What slipped through: a worktree created inside the repo
      carries a second copy of docs/ORIGINS.md, and the name-based prune (".git"
      as a directory name only) walked straight into it and turned the suite red
      (S-65). Only ever asked of a SUBDIRECTORY: the root of a linked worktree
      holds a ``.git`` file too, and pruning it would scan nothing (tests:H3).
    * A ``pyvenv.cfg`` — a virtual environment (PEP 405; ``python -m venv`` and
      virtualenv both write it). Third-party code is not this repository's
      content, and some of it spells ``FORBIDDEN[3]`` for reasons of its own:
      measured 2026-09-27 on this machine, it occurs in lxml's bundled libxslt
      header, configobj, pygments' FoxPro lexer and twisted's TLS tests. The
      old prune named ``.venv`` only, so an environment called ``env`` or
      ``venv`` was scanned; a marker the tool writes beats a name people choose.
      Rejected: pruning ``build/`` and ``dist/`` by name as before — they hold
      copies of this repository's own files, and a leak copied into a wheel is
      still a leak.
    """
    return (os.path.lexists(os.path.join(path, ".git"))
            or os.path.isfile(os.path.join(path, "pyvenv.cfg")))


def _scan(root: str) -> tuple[list[str], list[str]]:
    """``(hits, scanned)`` for the tree at ``root``.

    Every regular file is read; one holding a NUL byte is binary (an STL mesh)
    and is skipped. What slipped through before: an extension allow-list (.py
    .md .json .toml .txt) left 23 tracked files unread — the site's JS, HTML and
    CSS, the CI config, four Modelica sources, two CSVs, LICENSE, py.typed and
    every .gitignore — and each phase adds kinds (S-65). Text that is not valid
    UTF-8 is still read, with replacement characters: the words are ASCII, and a
    Latin-1 file is still somebody's prose.

    A hit reads ``<path>: FORBIDDEN[i]`` — the index, never the word, so a
    failure quoted into a commit message or a report does not leak it a second
    time. A file that cannot be read is a hit: a scan that skips what it cannot
    open is the allow-list again.
    """
    hits: list[str] = []
    scanned: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in _PRUNE_NAMES
                             and not _other_tree(os.path.join(dirpath, d)))
        for filename in sorted(filenames):
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, root)
            if rel in _ALLOWED:
                continue
            try:
                if not stat.S_ISREG(os.lstat(full).st_mode):
                    continue          # a socket or a FIFO would block the read
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                hits.append(f"{rel}: unreadable ({exc.strerror or exc})")
                continue
            if b"\x00" in data:
                continue
            scanned.append(rel)
            text = data.decode("utf-8", errors="replace").lower()
            for index, word in enumerate(FORBIDDEN):
                if word in text:
                    hits.append(f"{rel}: FORBIDDEN[{index}]")
    return hits, scanned


def _leaks(root: str) -> list[str]:
    """Every forbidden word under ``root``, as ``<path>: FORBIDDEN[i]``."""
    return _scan(root)[0]


class NoLeakedProvenance(unittest.TestCase):
    """atompipe carries the method of its parent project, not its content."""

    def test_repo_is_clean(self):
        hits, scanned = _scan(REPO)
        self.assertEqual(hits, [], f"leaked references: {hits}")
        self.assertGreater(
            len(scanned), _MIN_SCANNED,
            f"the walk read only {len(scanned)} files under {REPO} — a scan that "
            f"pruned the repository's own root, or most of it, passes on nothing")

    def test_every_kind_of_tracked_text_is_read(self):
        """The walk reads the site's JS, the CI config and the Modelica sources,
        not only the extensions somebody thought to list (S-65)."""
        _hits, scanned = _scan(REPO)
        kinds = {os.path.splitext(rel)[1] for rel in scanned}
        missing = sorted({".js", ".html", ".css", ".yml", ".mo", ".csv"} - kinds)
        self.assertEqual(missing, [], f"no file of these kinds was read: {missing}")
        self.assertTrue(
            any(not os.path.splitext(os.path.basename(rel))[1] for rel in scanned),
            "no extensionless file (LICENSE, .gitignore) was read")

    # -- the walk, shown to refuse and to prune (S-65, tests:H3) ------------- #
    def _plant(self, files: dict[str, str | bytes]) -> str:
        """A tree under the temp dir — never inside the repo, which is the tree
        being scanned (tests:H3)."""
        root = tempfile.mkdtemp(prefix="atompipe-provenance-")
        self.addCleanup(shutil.rmtree, root, True)
        for rel, content in files.items():
            full = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            data = content.encode("utf-8") if isinstance(content, str) else content
            with open(full, "wb") as fh:
                fh.write(data)
        return root

    @staticmethod
    def _word() -> str:
        """A forbidden word, built from the tuple at runtime — never typed."""
        return FORBIDDEN[0]

    def _nested_checkout(self, *, with_git: bool) -> str:
        files: dict[str, str | bytes] = {
            "README.md": "a clean top level\n",
            "wt/docs/ORIGINS.md": f"a second copy that names the {self._word()}\n",
        }
        if with_git:
            files["wt/.git"] = "gitdir: /elsewhere/.git/worktrees/wt\n"
        return self._plant(files)

    def test_a_nested_checkout_is_skipped(self):
        """A linked worktree inside the repo carries its own docs/ORIGINS.md; its
        `.git` is a FILE, and it is somebody else's tree to scan."""
        self.assertEqual(_leaks(self._nested_checkout(with_git=True)), [])

    def test_the_same_tree_without_git_is_caught(self):
        hits = _leaks(self._nested_checkout(with_git=False))
        self.assertEqual(hits, [f"{os.path.join('wt', 'docs', 'ORIGINS.md')}: FORBIDDEN[0]"])

    def test_the_root_is_never_pruned(self):
        """In a linked worktree the root ITSELF holds a `.git` file; pruning it
        would scan nothing and pass (tests:H3)."""
        root = self._plant({".git": "gitdir: /elsewhere/.git/worktrees/x\n",
                            "notes.md": f"{self._word()}\n"})
        self.assertEqual(len(_leaks(root)), 1)

    def test_a_mo_and_an_extensionless_file_are_scanned(self):
        root = self._plant({
            "model/Tank.mo": f"model Tank // {self._word()}\nend Tank;\n",
            "NOTICE": f"{self._word().upper()}\n",
        })
        hits = _leaks(root)
        for rel in (os.path.join("model", "Tank.mo"), "NOTICE"):
            self.assertTrue(any(h.startswith(f"{rel}:") for h in hits), f"{rel} unread: {hits}")

    def test_a_binary_file_is_not_read_as_text(self):
        """A NUL byte marks a binary (an STL, a PNG); its bytes are geometry,
        not prose, and the word inside one is noise."""
        root = self._plant({"mesh.stl": b"solid\x00" + self._word().encode() + b"\x00"})
        self.assertEqual(_leaks(root), [])

    def test_a_virtualenv_is_skipped(self):
        """Third-party code is not this repository's content, and some of it
        spells a forbidden word for reasons of its own (see ``_other_tree``)."""
        planted = {"env/lib/site-packages/lib.py": f"# {self._word()}\n"}
        self.assertEqual(len(_leaks(self._plant(planted))), 1)
        planted["env/pyvenv.cfg"] = "home = /usr/bin\n"
        self.assertEqual(_leaks(self._plant(planted)), [])


def _shadowed(pack_dirs: list[str]) -> list[str]:
    """Each bundled pack that ``packs.find`` resolves somewhere else, one line each."""
    out: list[str] = []
    for path in pack_dirs:
        name = os.path.basename(path)
        found = packs_mod.find(name, root=REPO)
        if os.path.realpath(found or "") != os.path.realpath(path):
            out.append(f"{name}: resolves to {found}, not the bundled copy at {path}")
    return out


class TestsTheBundledCopy(unittest.TestCase):
    """Every test in this file loads packs BY NAME, so it tests whichever copy the
    search path resolves first — and ``$ATOMPIPE_PACK_PATH`` and
    ``~/.atompipe/packs`` outrank the bundled directory. On a machine holding a
    same-named user pack the suite would validate that copy and report on this
    one (S-87; latent, not observed). Nothing else in the suite would notice: the
    wrong copy is usually a perfectly good pack. So say it here, loudly.
    """

    def test_every_bundled_pack_resolves_to_this_checkout(self):
        shadowed = _shadowed(_pack_dirs())
        self.assertEqual(
            shadowed, [],
            f"every pack test here would exercise the shadowing copy instead: "
            f"{shadowed}. Unset ${packs_mod.PACK_PATH_ENV} or move the pack out of "
            f"~/.atompipe/packs before trusting this suite")

    def test_a_shadowing_pack_is_reported(self):
        """V: a same-named pack on ``$ATOMPIPE_PACK_PATH`` is named."""
        victim = _pack_dirs()[0]
        shadow_root = tempfile.mkdtemp(prefix="atompipe-shadow-")
        self.addCleanup(shutil.rmtree, shadow_root, True)
        shadow = os.path.join(shadow_root, os.path.basename(victim))
        os.makedirs(shadow)
        with open(os.path.join(shadow, "pack.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        with mock.patch.dict(os.environ, {packs_mod.PACK_PATH_ENV: shadow_root}):
            shadowed = _shadowed([victim])
        self.assertEqual(len(shadowed), 1, shadowed)
        self.assertIn(shadow, shadowed[0])

    def test_the_spine_under_test_is_this_checkout(self):
        """The same failure one level up: an installed atompipe imported ahead of
        ``src/`` would test its own bundled packs and its own spine."""
        here = os.path.realpath(os.path.join(REPO, "src", "atompipe"))
        spine = os.path.realpath(os.path.dirname(gates_mod.__file__))
        self.assertEqual(spine, here,
                         f"atompipe was imported from {spine}, not {here} — run the "
                         f"suite with PYTHONPATH=src")
        self.assertEqual(os.path.realpath(packs_mod.BUNDLED_PACKS),
                         os.path.realpath(PACKS_DIR))


if __name__ == "__main__":
    unittest.main(verbosity=2)
