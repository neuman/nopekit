# SPDX-License-Identifier: Apache-2.0
"""The tests about the tests.

The invariants in CLAUDE.md are only as strong as the tests that carry them, and
the cheapest way to go green is to weaken one of those tests: rename the class,
decorate it ``@skip``, or have it call ``skipTest`` where the tool is missing. An
agent under pressure to make a change pass will find that route, and every rule
here exists because it is the route of least resistance (R-6, R-7):

* **EveryInvariantHasItsTest** — each numbered invariant in CLAUDE.md maps to a
  test class that exists and holds at least one test, and CLAUDE.md numbers
  exactly the invariants mapped here: a new invariant without a test, or a test
  class renamed out from under its invariant, is red.
* **InvariantClassesNeverSkip** — no skip and no expected-failure anywhere in
  those classes, nor in a class PLANNED to carry a later invariant, from the
  commit that first creates it (before CLAUDE.md names it). A tool-dependent
  scenario inside one asserts the availability skip as its outcome instead
  (tests:H13). An expected failure is a red test wearing green.
* **NoSubprocessOutsideRun** — every subprocess in ``tests/`` goes through
  ``_env.run``, the one environment that is the same on a dev box and a runner.
* **NoWallClockBelowTheEdge** — only ``cli.py`` reads the clock; the modules
  that shape a verdict or a record take ``now`` as an argument (spec §0.6).
* **NoGitOutsideVcs** — only ``src/atompipe/vcs.py`` starts git, so one clean
  environment, one timeout and one never-raise rule cover every git call.
* **EnvIsFaithful** — ``_env`` hides the machine without hiding the tools.

Every static check is a pure function of source text, and each has a planted
violator it must catch — a checker that has never refused anything is a logger.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import re
import shutil
import site
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

import _env

TESTS = os.path.join(_env.REPO, "tests")
CLAUDE_MD = os.path.join(_env.REPO, "CLAUDE.md")

#: CLAUDE.md's numbered invariants -> the class(es) that carry each. A value may
#: be a list. When CLAUDE.md gains an invariant, it gains its entry here in the
#: same commit, moved out of PLANNED_INVARIANT_CLASSES.
INVARIANT_CLASSES: dict[int, str | list[str]] = {
    # 1 and 4 through GLOSSARY §3's composition, exhaustively (P2.1): a claim
    # reads Checked iff every evaluator ran, passed, is qualified and current.
    1: ["test_invariants.SkipIsNotPass", "test_invariants.CheckedMeansEveryEvaluatorPassed"],
    # 2's second sentence, "reads louder than a missing tool", in what every
    # command prints (P2.0, R-1): from P2.1 an errored claim reads Skipped, and
    # "louder" is carried only by these tests. And a gate cannot make its crash
    # read the quieter Gap: the unqualified mark is the spine's (P2.1).
    2: ["test_invariants.ErrorIsNotPass", "test_louder.ErrorIsLouder",
        "test_invariants.UnqualifiedIsTheSpinesWord",
        # P2.2: a crash behind a prerequisite reaches a claim bound only to the
        # dependent, and must read as loud there as anywhere (its critique).
        "test_prerequisites.AnErroredPrerequisiteStaysLouder"],
    3: "test_invariants.RegistryRefusesLoggers",
    # 4 over a pass beside an evaluator that is not admitted (P2.0): an
    # invariant-9 change that drops the refused verdict would leave the pass
    # alone and the claim under PROVEN. In process on a planted verdict, and
    # end to end on a gate refused at its first check — where P2.0's readers
    # dropped the refusal, until P2.1 made `resolve` say it (the ratchet,
    # KNOWN_OVERCLAIMS, emptied and gone).
    4: ["test_invariants.ReportNeverOverclaims",
        "test_status_table.UnqualifiedBesideAPassIsNeverChecked",
        "test_invariants.CheckedMeansEveryEvaluatorPassed",
        # P2.4: a pass must meet the acceptance condition it read, and a value
        # outside its claim's acceptance condition is never Checked (D-23 of its
        # design) — nor one whose evaluator repeats the claim's units back
        # (review of P2.4: C1 in `um` read Checked at 700 um against 600).
        "test_goalposts.APassMustMeetTheAcceptanceItRead",
        "test_goalposts.AValueOutsideTheAcceptanceIsNeverChecked",
        "test_goalposts.AnEvaluatorStatesItsOwnUnits",
        # P2.5a: a physical or expert-judgment claim is listed Checked only on a
        # result that counts, and every claim in exactly one section.
        "test_physical.TheCheckedSectionHoldsOnlyBoundResults"],
    5: "test_packs.ControlsAreSealed",
    6: "test_packs.NegativeControlsFire",
    # 7, and P2.4: a goalpost edit re-keys exactly the gates that read it.
    7: ["test_staleness.StaleIsNotCurrent", "test_goalposts.TheGoalpostLivesInClaims",
        # P2.5a: a physical pass whose article's design moved reads Stale.
        "test_physical.AMovedArticleReadsStale"],
    8: ["test_records.IndexNeverDisagreesWithRecords",
        "test_records.NoCommandWritesARecord"],
    # 9 is the paired rule from P2.3: both controls (QualificationIsPaired), and
    # every conclusive mutation a fail for an evaluator outside the bundled packs
    # (EveryConclusiveMutationMustFail); the known-bad half's guards stay where
    # they were written (AdmissionIsDemonstrated); and the check run handed no
    # channel its qualification runs were not (TheCheckRunTakesTheQualifiedPath,
    # review of P2.3).
    9: ["test_admission.AdmissionIsDemonstrated", "test_admission.QualificationIsPaired",
        "test_admission.EveryConclusiveMutationMustFail",
        "test_admission.TheCheckRunTakesTheQualifiedPath",
        # P2.4: only inside its operating context does a pass count, and a fail
        # outside still does; the known-good control lies inside it; a mutation
        # counts wherever it lands; and a goalpost is never a key (critiques 1-2).
        "test_context.OutsideTheContextAPassDoesNotCount",
        "test_context.AFailOutsideStillCounts",
        "test_context.KnownGoodOutsideIsUnqualified",
        "test_context.AMutationPassingOutsideStillCounts",
        "test_goalposts.AGoalpostIsNeverAKey"],
    # 10 (P2.2): the rule in the run loop, the resolver and the composition; a
    # cached pass behind a failed guard, end to end in process; and the graph
    # the rule walks — a cycle or an inversion refused at registration.
    10: ["test_prerequisites.PrerequisiteFailureIsNeverAPass",
         "test_prerequisites.ACachedPassNeverSurvivesAFailedPrerequisite",
         "test_prerequisites.NeedsCycleRefused", "test_prerequisites.TierInversionRefused"],
    # 11 (P2.5a): the channel, the sealed results file, an owner and an
    # authority only as recorded, and what a physical result can and cannot do —
    # moved here from PLANNED_INVARIANT_CLASSES with CLAUDE.md's item.
    11: ["test_owner.AnOwnerWrittenByHandNeverCounts", "test_signing.HumanChannelOnly",
         "test_signing.WhoAndWhenAreNeverTyped", "test_signing.TheResultsFileIsSealedAndChained",
         "test_signing.AnOwnerOnlyThroughTheChannel", "test_physical.SignedMeansSomething",
         "test_physical.APhysicalFailNeverLosesItsPowerToFail",
         "test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord",
         "test_physical.AnExpertJudgmentStaysWithItsAuthority"],
    # 15 (P2.3): the mutation pass's seal, over the real walk and every planted
    # runner — moved here from PLANNED_INVARIANT_CLASSES with CLAUDE.md's item.
    15: "test_mutation.MutationIsSealed",
}

#: Classes a later checkpoint will make invariants, guarded against skips from
#: the commit that creates each one: R-7 has to bite from a class's first line,
#: not from the later commit that writes its number into CLAUDE.md — by then a
#: skip could already be load-bearing. Phase 1 planned 7-9 here; 7 and 9 moved to
#: INVARIANT_CLASSES when CLAUDE.md numbered them (checkpoint 1.2), and 8 with the
#: records layout (1.3), each the day its number landed.
#:
#: 15 was PLAN §4.0.1's mutation half (PLAN-v0.14 §4.1: "the mutation half lands
#: in P2 as MutationIsSealed, in P2.0"), planned here — not mapped, because
#: CLAUDE.md stated 1-10 and `EveryInvariantHasItsTest` refuses a mapped number
#: CLAUDE.md does not state — until P2.3 made mutation mechanical and moved it to
#: INVARIANT_CLASSES with CLAUDE.md's item (PLAN §4.0.1: "each is added to
#: CLAUDE.md in the phase that makes it mechanical"). *Rejected:* keeping it
#: planned past P2 — mutation would ship inside qualification with its seal
#: stated nowhere a reader of CLAUDE.md looks.
#:
#: 11 was PLAN §4.0.1's first half — planned here from P2.1, which landed
#: `Claim.owner` and refused to count one written by hand — until P2.5a's channel
#: made it mechanical and moved it to INVARIANT_CLASSES with CLAUDE.md's 11.
#:
#: 12 is "no renderer is more generous than the composition" (PLAN §4.0.1),
#: planned from P2.5a's renderer agreement over physical claims; it lands with
#: P2.5b's readiness object.
PLANNED_INVARIANT_CLASSES: dict[int, str | list[str]] = {
    12: "test_physical.RenderersAgreeOnPhysicalClaims",
}


def _refs(mapping: dict[int, str | list[str]]) -> list[str]:
    out: list[str] = []
    for value in mapping.values():
        out.extend([value] if isinstance(value, str) else value)
    return out


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# EveryInvariantHasItsTest
# --------------------------------------------------------------------------- #
def _numbered_invariants(claude_text: str) -> list[int]:
    """The ``N. **...`` items of CLAUDE.md's ``## Invariants`` section, in order."""
    match = re.search(r"^## Invariants\b[^\n]*\n(.*?)(?=^## |\Z)", claude_text, re.M | re.S)
    if not match:
        return []
    return [int(n) for n in re.findall(r"^(\d+)\.\s+\*\*", match.group(1), re.M)]


def _resolve(ref: str) -> type | None:
    module_name, _, class_name = ref.partition(".")
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    cls = getattr(module, class_name, None)
    return cls if isinstance(cls, type) and issubclass(cls, unittest.TestCase) else None


def _mapping_problems(claude_text: str, mapping: dict[int, str | list[str]]) -> list[str]:
    numbers = _numbered_invariants(claude_text)
    problems: list[str] = []
    if not numbers:
        problems.append("CLAUDE.md has no numbered items under '## Invariants'")
    if len(numbers) != len(set(numbers)):
        problems.append(f"CLAUDE.md numbers an invariant twice: {numbers}")
    if sorted(set(numbers)) != sorted(mapping):
        problems.append(f"CLAUDE.md numbers invariants {sorted(set(numbers))} but "
                        f"INVARIANT_CLASSES maps {sorted(mapping)} — an invariant "
                        f"with no test class, or a class for one CLAUDE.md dropped")
    loader = unittest.TestLoader()
    for number, value in sorted(mapping.items()):
        for ref in [value] if isinstance(value, str) else value:
            cls = _resolve(ref)
            if cls is None:
                problems.append(f"invariant {number}: {ref} does not import as a "
                                f"TestCase — renamed or deleted?")
            elif not loader.getTestCaseNames(cls):
                problems.append(f"invariant {number}: {ref} holds no test_* method")
    return problems


class EveryInvariantHasItsTest(unittest.TestCase):
    def test_claude_md_and_the_map_agree(self):
        problems = _mapping_problems(_read(CLAUDE_MD), INVARIANT_CLASSES)
        self.assertEqual(problems, [], "\n".join(problems))

    def test_a_planted_unmapped_invariant_is_caught(self):
        planted = _read(CLAUDE_MD).replace(
            "Two more, learned the hard way",
            "7. **A planted invariant.** Nobody wrote its test.\n\nTwo more, learned the hard way")
        self.assertIn(7, _numbered_invariants(planted))
        self.assertTrue(_mapping_problems(planted, INVARIANT_CLASSES))

    def test_a_planted_unmapped_number_is_caught(self):
        """The unmapped path on its own. Once CLAUDE.md numbered 7, the planted
        7 above is caught as a duplicate instead; this keeps the "an invariant
        with no test class" refusal exercised by a number nothing maps."""
        number = max(INVARIANT_CLASSES) + 10
        planted = _read(CLAUDE_MD).replace(
            "Two more, learned the hard way",
            f"{number}. **A planted invariant.** Nobody wrote its test.\n\n"
            f"Two more, learned the hard way")
        self.assertIn(number, _numbered_invariants(planted))
        problems = _mapping_problems(planted, INVARIANT_CLASSES)
        self.assertTrue(any("INVARIANT_CLASSES maps" in p for p in problems), problems)
        self.assertFalse(any("twice" in p for p in problems), problems)

    def test_a_planted_missing_class_is_caught(self):
        mapping = dict(INVARIANT_CLASSES)
        mapping[1] = "test_invariants.SkipIsNotPassRenamed"
        problems = _mapping_problems(_read(CLAUDE_MD), mapping)
        self.assertTrue(any("SkipIsNotPassRenamed" in p for p in problems), problems)


# --------------------------------------------------------------------------- #
# InvariantClassesNeverSkip
# --------------------------------------------------------------------------- #
#: Every spelling of "do not run this" or "expect this to fail" in unittest and
#: pytest. Exact names only: `verdict.skipped`, `skip_reason` and a list called
#: `skipped` are how the invariant tests talk ABOUT skips, and must not trip it.
_SKIP_NAMES = frozenset({
    "skip", "skipIf", "skipUnless", "skipTest", "SkipTest", "expectedFailure",
    "__unittest_skip__", "__unittest_skip_why__", "__unittest_expecting_failure__",
    "skipif", "xfail", "importorskip",
})

#: Of those, the ones unambiguous enough to catch as a string too
#: (``getattr(self, "skipTest")``). "skip" alone is too common a word.
_SKIP_STRINGS = frozenset({
    "skipTest", "SkipTest", "expectedFailure", "__unittest_skip__",
    "__unittest_expecting_failure__",
})


def _skip_findings(source: str, class_name: str, filename: str = "<planted>") -> list[str] | None:
    """Skip spellings in what runs when ``class_name`` runs; None if it is absent.

    "What runs" is the class body, its bases defined in the same module, the
    module's ``setUpModule``/``tearDownModule``, and — transitively — every
    module-level function, class or assignment any of those names: a helper that
    raises ``SkipTest`` skips the invariant just as surely as a decorator.
    """
    tree = ast.parse(source, filename)
    defs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    defs[target.id] = node
    root = defs.get(class_name)
    if not isinstance(root, ast.ClassDef):
        return None
    queue: list[ast.AST] = [root]
    queue += [defs[h] for h in ("setUpModule", "tearDownModule") if h in defs]
    seen: set[int] = set()
    findings: list[str] = []
    while queue:
        node = queue.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        for sub in ast.walk(node):
            name = None
            if isinstance(sub, ast.Name):
                name = sub.id
                if isinstance(sub.ctx, ast.Load) and sub.id in defs:
                    queue.append(defs[sub.id])
            elif isinstance(sub, ast.Attribute):
                name = sub.attr
            elif isinstance(sub, ast.alias):            # an import inside a def
                name = sub.name.rsplit(".", 1)[-1]
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if sub.value in _SKIP_STRINGS:
                    findings.append(f"{filename}:{sub.lineno}: {sub.value!r}")
                continue
            if name in _SKIP_NAMES:
                findings.append(f"{filename}:{getattr(sub, 'lineno', '?')}: {name}")
    return sorted(set(findings))


def _runtime_skip_marks(cls: type) -> list[str]:
    """The markers ``unittest`` itself reads, however they were applied."""
    marks: list[str] = []
    if getattr(cls, "__unittest_skip__", False):
        marks.append(f"{cls.__name__} is marked skip")
    for name in unittest.TestLoader().getTestCaseNames(cls):
        fn = getattr(cls, name)
        if getattr(fn, "__unittest_skip__", False):
            marks.append(f"{cls.__name__}.{name} is marked skip")
        if getattr(fn, "__unittest_expecting_failure__", False):
            marks.append(f"{cls.__name__}.{name} is marked expectedFailure")
    return marks


def _class_source(ref: str) -> tuple[str, str, str] | None:
    """``(source, class_name, path)`` for ``module.Class`` under tests/, or None."""
    module_name, _, class_name = ref.partition(".")
    path = os.path.join(TESTS, f"{module_name}.py")
    if not os.path.isfile(path):
        return None
    return _read(path), class_name, path


class InvariantClassesNeverSkip(unittest.TestCase):
    def _check(self, refs: list[str], *, must_exist: bool) -> int:
        checked = 0
        for ref in refs:
            with self.subTest(cls=ref):
                found = _class_source(ref)
                if found is None:
                    self.assertFalse(must_exist, f"{ref}: no such test file")
                    continue
                source, class_name, path = found
                findings = _skip_findings(source, class_name, os.path.relpath(path, _env.REPO))
                if findings is None:
                    self.assertFalse(must_exist, f"{ref}: no such class")
                    continue
                checked += 1
                self.assertEqual(
                    findings, [],
                    f"{ref} can skip or expect failure — an invariant class asserts "
                    f"the availability skip as its outcome instead (R-7, tests:H13): "
                    f"{findings}")
                cls = _resolve(ref)
                self.assertIsNotNone(cls, f"{ref} is in {path} but does not import")
                self.assertEqual(_runtime_skip_marks(cls), [])
        return checked

    def test_mapped_classes_never_skip(self):
        self.assertEqual(self._check(_refs(INVARIANT_CLASSES), must_exist=True),
                         len(_refs(INVARIANT_CLASSES)))

    def test_planned_classes_never_skip_once_they_exist(self):
        self._check(_refs(PLANNED_INVARIANT_CLASSES), must_exist=False)

    def test_planted_skips_are_caught(self):
        planted = {
            "class decorator": """
                import unittest
                @unittest.skip("no solver here")
                class Inv(unittest.TestCase):
                    def test_x(self): pass
            """,
            "method decorator, from-imported": """
                from unittest import TestCase, skipIf
                class Inv(TestCase):
                    @skipIf(True, "flaky")
                    def test_x(self): pass
            """,
            "expected failure": """
                import unittest
                class Inv(unittest.TestCase):
                    @unittest.expectedFailure
                    def test_x(self): self.fail()
            """,
            "skipTest in a body": """
                import unittest
                class Inv(unittest.TestCase):
                    def test_x(self):
                        self.skipTest("trimesh missing")
            """,
            "raise SkipTest in setUp": """
                import unittest
                class Inv(unittest.TestCase):
                    def setUp(self):
                        raise unittest.SkipTest("no omc")
                    def test_x(self): pass
            """,
            "a module helper that skips": """
                import unittest
                def _need_tool():
                    raise unittest.SkipTest("absent")
                class Inv(unittest.TestCase):
                    def test_x(self):
                        _need_tool()
            """,
            "a same-module base class that skips": """
                import unittest
                class _Base(unittest.TestCase):
                    def setUp(self):
                        self.skipTest("absent")
                class Inv(_Base):
                    def test_x(self): pass
            """,
            "setUpModule": """
                import unittest
                def setUpModule():
                    raise unittest.SkipTest("absent")
                class Inv(unittest.TestCase):
                    def test_x(self): pass
            """,
            "an alias": """
                import unittest
                _later = unittest.skip("later")
                class Inv(unittest.TestCase):
                    @_later
                    def test_x(self): pass
            """,
            "by string": """
                import unittest
                class Inv(unittest.TestCase):
                    def test_x(self):
                        getattr(self, "skipTest")("absent")
            """,
            "pytest": """
                import pytest, unittest
                class Inv(unittest.TestCase):
                    @pytest.mark.xfail
                    def test_x(self): pass
            """,
        }
        for label, source in planted.items():
            with self.subTest(planted=label):
                findings = _skip_findings(textwrap.dedent(source), "Inv")
                self.assertTrue(findings, f"{label}: not caught")

    def test_talking_about_skips_is_not_skipping(self):
        """The positive control: the invariant tests read `verdict.skipped` and
        keep lists called `skipped`; none of that is a skip."""
        source = textwrap.dedent("""
            import unittest
            class Inv(unittest.TestCase):
                def test_x(self):
                    skipped = []
                    v = run()
                    self.assertTrue(v.skipped)
                    skipped.append(v.skip_reason)
        """)
        self.assertEqual(_skip_findings(source, "Inv"), [])
        self.assertIsNone(_skip_findings(source, "Absent"))

    def test_a_planted_runtime_mark_is_caught(self):
        @unittest.skip("planted")
        class Planted(unittest.TestCase):
            def test_x(self):
                pass
        self.assertTrue(_runtime_skip_marks(Planted))


# --------------------------------------------------------------------------- #
# NoSubprocessOutsideRun
# --------------------------------------------------------------------------- #
def _os_spawner(name: str) -> bool:
    return (name in {"system", "popen", "fork", "forkpty", "startfile"}
            or name.startswith(("spawn", "exec", "posix_spawn")))


def _subprocess_findings(source: str, filename: str = "<planted>") -> list[str]:
    """Every way ``source`` could start a process without ``_env.run``."""
    tree = ast.parse(source, filename)
    os_names = {"os"}
    sub_names = {"subprocess"}
    for node in ast.walk(tree):                # aliases first, wherever they are bound
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_names.add(alias.asname or "os")
                elif alias.name == "subprocess":
                    sub_names.add(alias.asname or "subprocess")
    findings: list[str] = []

    def hit(node: ast.AST, what: str) -> None:
        findings.append(f"{filename}:{getattr(node, 'lineno', '?')}: {what}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "subprocess":
                    hit(node, f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if module.split(".")[0] == "subprocess":
                    hit(node, f"from {module} import {alias.name}")
                elif module == "os" and _os_spawner(alias.name):
                    hit(node, f"from os import {alias.name}")
                elif module == "asyncio" and alias.name.startswith("create_subprocess"):
                    hit(node, f"from asyncio import {alias.name}")
                elif module == "pty":
                    # P2.5a: a pty is a person's own shell to `claim physical`, so
                    # only `_env.run_tty` opens one (`from pty import openpty` too).
                    hit(node, f"from pty import {alias.name}")
                elif module == "os" and alias.name == "openpty":
                    hit(node, "from os import openpty")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            base = node.value.id
            if base in sub_names:
                hit(node, f"{base}.{node.attr}")
            elif base in os_names and _os_spawner(node.attr):
                hit(node, f"{base}.{node.attr}")
            elif base == "asyncio" and node.attr.startswith("create_subprocess"):
                hit(node, f"asyncio.{node.attr}")
            elif base == "pty":
                hit(node, f"pty.{node.attr}")
            elif base in os_names and node.attr == "openpty":
                hit(node, f"{base}.openpty")
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            called = (func.id if isinstance(func, ast.Name)
                      else func.attr if isinstance(func, ast.Attribute) else "")
            first = node.args[0]
            if (called in {"__import__", "import_module"} and isinstance(first, ast.Constant)
                    and isinstance(first.value, str) and first.value.split(".")[0] == "subprocess"):
                hit(node, f"{called}({first.value!r})")
    return sorted(set(findings))


def _test_sources() -> list[str]:
    """Every ``.py`` under tests/, oracle scripts included, except ``_env.py``."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(TESTS):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for filename in sorted(filenames):
            path = os.path.join(dirpath, filename)
            if filename.endswith(".py") and path != os.path.join(TESTS, "_env.py"):
                out.append(path)
    return out


class NoSubprocessOutsideRun(unittest.TestCase):
    def test_no_test_spawns_a_process_outside_run(self):
        sources = _test_sources()
        self.assertIn(os.path.abspath(__file__), [os.path.abspath(p) for p in sources])
        findings: list[str] = []
        for path in sources:
            findings += _subprocess_findings(_read(path), os.path.relpath(path, _env.REPO))
        self.assertEqual(
            findings, [],
            "a subprocess outside tests/_env.run inherits this machine's HOME, git "
            "identity, user packs and agent variables — use _env.run, _env.atompipe "
            "or _env.git: " + "; ".join(findings))

    def test_planted_spawns_are_caught(self):
        planted = [
            "import subprocess\n",
            "import subprocess as sp\nsp.run(['git', 'status'])\n",
            "from subprocess import run\nrun(['git'])\n",
            "import os\nos.system('git status')\n",
            "import os as o\no.popen('ls')\n",
            "from os import execvp\n",
            "import os\nos.posix_spawn('/bin/true', ['true'], {})\n",
            "def f():\n    import subprocess\n    return subprocess.check_output(['git'])\n",
            "__import__('subprocess').run(['x'])\n",
            "import importlib\nimportlib.import_module('subprocess')\n",
            "import asyncio\nasyncio.create_subprocess_exec('git')\n",
            # P2.5a: a pty or a fork outside `_env.run_tty` is a channel a test
            # would open on its own terms.
            "import pty\nm, s = pty.openpty()\n",
            "from pty import openpty\n",
            "import os\nos.openpty()\n",
            "import os\nos.fork()\n",
        ]
        for source in planted:
            with self.subTest(source=source):
                self.assertTrue(_subprocess_findings(source), f"not caught: {source!r}")

    def test_the_sanctioned_route_is_clean(self):
        source = ("import os, sys, _env\n"
                  "_env.run([sys.executable, '-c', 'pass'], cwd='.')\n"
                  "_env.git(['status'], cwd='.')\n"
                  "os.path.join(os.getcwd(), 'x')\n")
        self.assertEqual(_subprocess_findings(source), [])


# --------------------------------------------------------------------------- #
# NoWallClockBelowTheEdge
# --------------------------------------------------------------------------- #
#: ``(what the name is reached through, the name)`` for every wall-clock read.
#: ``time.perf_counter`` and ``os.times`` are measurements of THIS run, not dates,
#: and stay allowed. ``time.localtime(ts)``/``gmtime(ts)`` only convert a stamp,
#: but a module below the edge has no business formatting dates either: the edge
#: stamps ``now`` once per command and passes it down (spec §0.6).
_CLOCK_ATTRS = frozenset({
    ("time", "time"), ("time", "time_ns"), ("time", "localtime"), ("time", "gmtime"),
    ("datetime", "now"), ("datetime", "utcnow"), ("datetime", "today"),
    ("date", "today"),
})

#: The spine modules that must never read the clock: they shape a verdict, a
#: record or a digest, and a timestamp read inside one makes two identical runs
#: write different bytes. The first three exist today; the others arrive in P1.2.
CLOCKLESS_MODULES = ("store", "gates", "modelio", "verdicts", "vcs")


def _clock_findings(source: str, filename: str = "<planted>") -> list[str]:
    """Every wall-clock read in ``source`` — calls and bare references alike: a
    ``default_factory=utcnow_iso`` reads the clock later, but it reads it here."""
    tree = ast.parse(source, filename)
    findings: list[str] = []

    def hit(node: ast.AST, what: str) -> None:
        findings.append(f"{filename}:{getattr(node, 'lineno', '?')}: {what}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "utcnow_iso":
            hit(node, "utcnow_iso")
        elif isinstance(node, ast.Attribute):
            if node.attr == "utcnow_iso":
                hit(node, "utcnow_iso")
            base = node.value
            base_name = (base.id if isinstance(base, ast.Name)
                         else base.attr if isinstance(base, ast.Attribute) else None)
            if (base_name, node.attr) in _CLOCK_ATTRS:
                hit(node, f"{base_name}.{node.attr}")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "utcnow_iso":
                    hit(node, "import utcnow_iso")
                elif (node.module, alias.name) in _CLOCK_ATTRS:
                    hit(node, f"from {node.module} import {alias.name}")
    return sorted(set(findings))


class NoWallClockBelowTheEdge(unittest.TestCase):
    def test_no_clock_below_the_edge(self):
        spine = os.path.join(_env.SRC, "atompipe")
        present = [m for m in CLOCKLESS_MODULES
                   if os.path.isfile(os.path.join(spine, f"{m}.py"))]
        for required in ("store", "gates", "modelio"):
            self.assertIn(required, present, f"src/atompipe/{required}.py is missing")
        findings: list[str] = []
        for module in present:
            path = os.path.join(spine, f"{module}.py")
            findings += _clock_findings(_read(path), os.path.relpath(path, _env.REPO))
        self.assertEqual(
            findings, [],
            "only cli.py reads the wall clock; stamp `now` there and pass it down: "
            + "; ".join(findings))

    def test_planted_clock_reads_are_caught(self):
        planted = [
            "import time\nstamp = time.time()\n",
            "from datetime import datetime\nwhen = datetime.now()\n",
            "import datetime\nwhen = datetime.datetime.utcnow()\n",
            "from datetime import date\nday = date.today()\n",
            "from .util import utcnow_iso\nwhen = utcnow_iso()\n",
            "from . import util\nwhen = util.utcnow_iso()\n",
            "from time import time\n",
            "import time\nparts = time.localtime()\n",
            "import dataclasses\nfrom .util import utcnow_iso\n"
            "@dataclasses.dataclass\nclass E:\n    when: str = dataclasses.field(default_factory=utcnow_iso)\n",
        ]
        for source in planted:
            with self.subTest(source=source):
                self.assertTrue(_clock_findings(source), f"not caught: {source!r}")

    def test_measuring_a_duration_is_not_reading_the_clock(self):
        source = ("import os, time\nstarted = time.perf_counter()\n"
                  "cpu = os.times()\nelapsed = time.perf_counter() - started\n"
                  "def f(now): return now\n")
        self.assertEqual(_clock_findings(source), [])


# --------------------------------------------------------------------------- #
# NoGitOutsideVcs
# --------------------------------------------------------------------------- #
#: The one spine module that may start git (spec §3.1). A git call anywhere else
#: inherits the process's GIT_DIR — a hook's OUTER repository — has no timeout,
#: and can raise where "not a repository" is the honest answer; vcs.py exists so
#: those three rules are written once.
GIT_EDGE = os.path.join("atompipe", "vcs.py")

#: Calls that start a process, by the name they are reached through:
#: subprocess's, the os spawners NoSubprocessOutsideRun already knows, asyncio's.
_PROCESS_CALLS = frozenset({
    "run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput",
})


def _is_git_program(text: str) -> bool:
    """``git``, ``/usr/bin/git``, ``C:\\...\\git.exe`` — an argv[0] naming git."""
    return text.replace("\\", "/").rsplit("/", 1)[-1].lower() in {"git", "git.exe"}


def _starts_process(func: ast.AST) -> bool:
    name = (func.attr if isinstance(func, ast.Attribute)
            else func.id if isinstance(func, ast.Name) else "")
    return name in _PROCESS_CALLS or _os_spawner(name) or name.startswith("create_subprocess")


def _is_which_git(node: ast.AST) -> bool:
    """``shutil.which("git")``: locating git is reaching for it."""
    if not (isinstance(node, ast.Call) and node.args):
        return False
    func = node.func
    name = (func.attr if isinstance(func, ast.Attribute)
            else func.id if isinstance(func, ast.Name) else "")
    first = node.args[0]
    return (name == "which" and isinstance(first, ast.Constant)
            and isinstance(first.value, str) and _is_git_program(first.value))


def _names_git(node: ast.AST, aliases: set[str]) -> bool:
    """``node`` evaluates to the git program: the string, a name bound to it, or
    ``shutil.which("git")``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _is_git_program(node.value)
    if isinstance(node, ast.Name):
        return node.id in aliases
    return _is_which_git(node)


def _is_git_shell(node: ast.AST) -> bool:
    """A shell command whose first word is git: ``"git status"``, ``f"git log {x}"``."""
    if isinstance(node, ast.JoinedStr) and node.values:
        node = node.values[0]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        words = node.value.split()
        return bool(words) and _is_git_program(words[0])
    return False


def _git_findings(source: str, filename: str = "<planted>") -> list[str]:
    """Every place ``source`` could start git.

    An argv literal whose program is git counts wherever it appears — assembled
    in a variable and passed along later is still a git call — as does a process
    call whose first argument or ``executable=`` names git, a shell string that
    starts with git, and ``shutil.which("git")``. A tuple that merely starts with
    the word ("git", "hg") reads as an argv too: rename it rather than teach the
    scanner an exception. What does not count: ``".git"``, prose, a dict key.
    """
    tree = ast.parse(source, filename)
    aliases: set[str] = set()
    for node in ast.walk(tree):                   # GIT = "git"; GIT = shutil.which("git")
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            if _names_git(node.value, set()):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                aliases.update(t.id for t in targets if isinstance(t, ast.Name))
    findings: list[str] = []

    def hit(node: ast.AST, what: str) -> None:
        findings.append(f"{filename}:{getattr(node, 'lineno', '?')}: {what}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            if _names_git(node.elts[0], aliases):
                hit(node, "an argv whose program is git")
        elif isinstance(node, ast.Call):
            if _is_which_git(node):
                hit(node, "which('git')")
            elif _starts_process(node.func):
                first = node.args[0] if node.args else None
                if first is not None and (_names_git(first, aliases) or _is_git_shell(first)):
                    hit(node, "a process call that starts git")
                for keyword in node.keywords:
                    if keyword.arg in ("executable", "args") and (
                            _names_git(keyword.value, aliases) or _is_git_shell(keyword.value)):
                        hit(node, f"{keyword.arg}= names git")
    return sorted(set(findings))


def _spine_sources() -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(_env.SRC):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        out += [os.path.join(dirpath, f) for f in sorted(filenames) if f.endswith(".py")]
    return out


class NoGitOutsideVcs(unittest.TestCase):
    def test_no_git_outside_vcs(self):
        sources = _spine_sources()
        edge = os.path.join(_env.SRC, GIT_EDGE)
        self.assertIn(edge, sources, f"src/{GIT_EDGE} is missing")
        findings: list[str] = []
        for path in sources:
            if path != edge:
                findings += _git_findings(_read(path), os.path.relpath(path, _env.REPO))
        self.assertEqual(
            findings, [],
            "git is started only from src/atompipe/vcs.py — its clean environment, "
            "timeout and never-raise rule are the reason it exists; add the question "
            "to vcs instead: " + "; ".join(findings))

    def test_the_edge_itself_is_seen(self):
        """The positive control: the scanner recognises vcs.py's own git call, so
        an empty result above is a spine without git, not a scanner that is blind."""
        self.assertTrue(_git_findings(_read(os.path.join(_env.SRC, GIT_EDGE))))

    def test_planted_git_calls_are_caught(self):
        planted = [
            "import subprocess\nsubprocess.run(['git', 'status'])\n",
            "import subprocess as sp\nsp.check_output(('git', 'rev-parse', 'HEAD'))\n",
            "from subprocess import Popen\nPopen(['/usr/bin/git', 'log'])\n",
            "import os\nos.system('git status --porcelain')\n",
            "import os\nos.execvp('git', argv)\n",
            "cmd = ['git']\ncmd += ['log']\n",
            "GIT = 'git'\nimport subprocess\nsubprocess.run([GIT, 'log'])\n",
            "import shutil\nexe = shutil.which('git')\n",
            "import subprocess\nsubprocess.run(f'git log {ref}', shell=True)\n",
            "import asyncio\nasyncio.create_subprocess_exec('git', 'log')\n",
            "def f():\n    import subprocess\n    return subprocess.run(['git.exe', 'log'])\n",
            "import subprocess\nsubprocess.run(argv, executable='git')\n",
        ]
        for source in planted:
            with self.subTest(source=source):
                self.assertTrue(_git_findings(source), f"not caught: {source!r}")

    def test_talking_about_git_is_not_calling_it(self):
        source = textwrap.dedent('''
            """Stops at the repository's `.git`; `git ls-files` is vcs's job."""
            import os, subprocess
            _GIT_ENTRY = ".git"
            marker = os.path.join(root, ".git")
            names = {"git": "the one VCS"}
            print("git status")
            subprocess.run(["python3", "-c", "print('git')"])
            ignores = [".gitignore", ".gitattributes"]
        ''')
        self.assertEqual(_git_findings(source), [])


# --------------------------------------------------------------------------- #
# EnvIsFaithful
# --------------------------------------------------------------------------- #
_AVAILABILITY_SCRIPT = """\
import json, sys
from atompipe import gates, packs
root, names = sys.argv[1], sys.argv[2:]
out = {}
for name in names:
    registry = gates.Registry()
    packs.load_gates(name, registry, root=root)
    for spec in registry.specs():
        out[spec.id] = list(gates.availability(spec))
print(json.dumps(out, sort_keys=True))
"""

_ENV_SCRIPT = "import json, os; print(json.dumps(dict(os.environ)))"


def _bundled_pack_names() -> list[str]:
    packs_dir = os.path.join(_env.REPO, "packs")
    return sorted(n for n in os.listdir(packs_dir)
                  if os.path.isfile(os.path.join(packs_dir, n, "pack.json")))


class EnvIsFaithful(_env.EnvCase):
    """``_env`` hides the machine and keeps the tools.

    Two ways a test environment lies: it leaks the dev box into the child (a git
    identity, a user pack, an agent session's variables), so the test passes here
    and fails on a runner; or it hides too much (a temp HOME that drops the user
    site-packages), so every tool-dependent test in a child is a vacuous
    availability skip. One test for each.
    """

    def _child_env(self, **kw) -> dict[str, str]:
        proc = _env.run([sys.executable, "-c", _ENV_SCRIPT], cwd=self.tmp(), **kw)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_availability_is_identical_in_process_and_through_run(self):
        from atompipe import gates, packs
        names = _bundled_pack_names()
        here: dict[str, list] = {}
        for name in names:
            registry = gates.Registry()
            packs.load_gates(name, registry, root=_env.REPO)
            for spec in registry.specs():
                here[spec.id] = list(gates.availability(spec))
        self.assertTrue(here, "no bundled gate loaded in-process")
        proc = _env.run([sys.executable, "-c", _AVAILABILITY_SCRIPT, _env.REPO, *names],
                        cwd=self.tmp())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        there = json.loads(proc.stdout)
        differ = {g: (here.get(g), there.get(g)) for g in sorted(set(here) | set(there))
                  if here.get(g) != there.get(g)}
        self.assertEqual(differ, {},
                         "a child of _env.run sees different tools than this process "
                         "(in-process, child): a test run through _env would measure "
                         "the environment, not the code")

    def test_agent_variables_are_stripped(self):
        planted = {
            "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "planted", "AI_AGENT": "planted",
            "GIT_DIR": "/nonexistent/.git", "GIT_WORK_TREE": "/nonexistent",
            "GIT_INDEX_FILE": "/nonexistent/index", "GIT_CONFIG_PARAMETERS": "'x.y=z'",
            "GIT_AUTHOR_NAME": "a person", "EMAIL": "person@example.invalid",
            "ATOMPIPE_PACK_PATH": "/nonexistent/packs", "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_CONFIG_HOME": "/nonexistent/config",
        }
        with mock.patch.dict(os.environ, planted):
            child = self._child_env()
            kept = self._child_env(env={"AI_AGENT": "asked for"})
            ident = self._child_env(identity=True)
        leaked = sorted(k for k in child if k in planted)
        self.assertEqual(leaked, [], f"inherited from the parent: {leaked}")
        self.assertEqual(child["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(child["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertNotEqual(child["HOME"], os.environ.get("HOME"))
        self.assertTrue(os.path.realpath(child["HOME"]).startswith(
            os.path.realpath(tempfile.gettempdir())), child["HOME"])
        self.assertEqual(child["PYTHONUSERBASE"], site.getuserbase())
        self.assertEqual(child.get("PATH"), os.environ.get("PATH"))
        self.assertEqual(child.get("PYTHONNOUSERSITE"), os.environ.get("PYTHONNOUSERSITE"))
        self.assertEqual(child["PYTHONPATH"].split(os.pathsep)[0], _env.SRC)
        self.assertFalse(any(k.startswith("GIT_AUTHOR") or k.startswith("GIT_COMMITTER")
                             for k in child))
        self.assertEqual(kept.get("AI_AGENT"), "asked for")
        for key, value in _env.IDENTITY.items():
            self.assertEqual(ident.get(key), value)

    def test_git_has_no_identity_unless_asked(self):
        """The dev box has a global identity and a runner has none; a child of
        _env.run must look like the runner. Holds against THIS machine's real
        ~/.gitconfig, which is the point.

        Asserted on the config, not on a failing commit: where the host name
        resolves to a dotted FQDN, git invents `user@host.domain` and commits
        anyway, so "an anonymous commit fails" is a fact about the runner's DNS.
        """
        self._require_git()
        repo = self.tmp()
        self.assertEqual(_env.git(["init", "-q"], cwd=repo).returncode, 0)
        for key in ("user.name", "user.email"):
            seen = _env.git(["config", key], cwd=repo)
            self.assertNotEqual(seen.returncode, 0,
                                f"{key} = {seen.stdout.strip()!r} leaked into the child")
        with open(os.path.join(repo, "f"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        _env.git(["add", "f"], cwd=repo)
        signed = _env.git(["commit", "-qm", "signed"], cwd=repo, identity=True)
        self.assertEqual(signed.returncode, 0, signed.stderr)
        author = _env.git(["log", "-1", "--format=%an <%ae>"], cwd=repo).stdout.strip()
        self.assertEqual(author, f"{_env.IDENTITY['GIT_AUTHOR_NAME']} "
                                 f"<{_env.IDENTITY['GIT_AUTHOR_EMAIL']}>")

    def test_shallow_clone_is_shallow(self):
        self._require_git()
        src = os.path.join(self.tmp(), "src")
        os.makedirs(src)
        _env.git(["init", "-q"], cwd=src)
        for n in range(3):
            with open(os.path.join(src, "f"), "w", encoding="utf-8") as fh:
                fh.write(f"{n}\n")
            _env.git(["add", "f"], cwd=src)
            self.assertEqual(
                _env.git(["commit", "-qm", f"c{n}"], cwd=src, identity=True).returncode, 0)
        dst = _env.shallow_clone(src, os.path.join(self.tmp(), "clone"))
        count = _env.git(["rev-list", "--count", "HEAD"], cwd=dst)
        self.assertEqual(count.stdout.strip(), "1",
                         "the clone carries the whole history: --depth was ignored")

    def _require_git(self) -> None:
        # Not an invariant class, so this skip is allowed; every CI runner and
        # every checkout of this repository has git.
        if shutil.which("git") is None:
            self.skipTest("git is not on PATH")


if __name__ == "__main__":
    unittest.main(verbosity=2)
