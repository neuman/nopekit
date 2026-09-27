# SPDX-License-Identifier: Apache-2.0
"""Every shipped pack must validate, and every gate must prove it can fail.

This is the gate on the gates. A pack whose validators have never demonstrated
failure is a pack of loggers, and merging one would quietly convert atompipe from
a thing that checks designs into a thing that agrees with them.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import uuid
from unittest import mock

from atompipe import gates as gates_mod
from atompipe import packs as packs_mod
from atompipe.models import Ledger, ProjectMeta, Tier

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


def _pack_ctx(pack_dir: str, params: dict) -> gates_mod.GateContext:
    """A context carrying ``params`` as the projection, rooted in the pack.

    Without the pack's own baseline a gate SKIPS for want of a parameter and
    its control never fires — so the gate ships unproven while the suite reads
    green. A skipped control is not a passing control, and the whole
    credibility of this project rests on that distinction, so every pack ships
    `selftest/baseline.json`: a plausible, physically coherent projection that
    every one of its gates PASSES. The fixtures then move one thing and the
    gate must flip to FAIL.
    """
    return gates_mod.GateContext(
        root=pack_dir, ledger=Ledger(meta=ProjectMeta(name="selftest")), model=None,
        params=params, out_dir=os.path.join(pack_dir, ".selftest-out"), tier=3,
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
                    out_dir=os.path.join(path, ".selftest-out"), tier=3,
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
