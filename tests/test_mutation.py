# SPDX-License-Identifier: Apache-2.0
"""MutationIsSealed: the mutation half of invariant 15, landed before the code it holds.

PLAN-v0.14 §1.5 makes a mutation pass part of a project evaluator's
qualification: in process, on the known-good control, changing the parameters
its run read, with no tree written (old 5.1 redesigned so that P2 can hold it).
Two things can go wrong there that no later test would see, because a mutation
pass that misbehaves still prints a plausible line — `mutation 4/4 fail →
qualified` — over an evaluator one mutation survived. So this harness holds ANY
mutation runner, through a small interface, to three properties (PLAN §4.0.1
invariant 15 as PLAN-v0.14 §4.1 splits it; "bench writes nothing" left with the
bench):

* **M1, the tree.** While the runner runs, nothing is written under the project
  or any pack directory: no create, modify, delete, rename, truncate, link,
  chmod or utime, and no write-then-restore. Watched two ways: an audit hook,
  armed only inside the run, and a content digest of every watched tree before
  and after. Bytecode is not exempt: the window sets `sys.dont_write_bytecode`,
  so a `.pyc` that appears was written by the runner (review of the design: an
  exemption for `__pycache__/` let a runner rewrite a `.pyc` whose header still
  matched its source — the next import loads it instead of the source).
* **M2, memory.** Afterwards the known-good parameters, the module-level design
  they came from, and a live run of the evaluator are what they were. In process
  the likelier leak is aliasing, not a file: a mutation applied to the design in
  place, or to a memo hit (`gates.py`: "A hit hands every caller the SAME
  object"), poisons every later control and the real verdict.
* **M3, honest results.** Every result is checked against an ORACLE that calls
  the evaluator's function directly on a context whose params record every path
  read through them, nested levels included, and applies its own outcome rules.
  A mutation is *conclusive* when the known-good run read its key and the re-run
  passes or fails (GLOSSARY §2, mutation); an inconclusive one is never reported
  as a fail; a reported outcome is the re-run's, in GLOSSARY's four words; a
  conclusive pass is never a fail. Every planned mutation has exactly one result
  — and with no plan handed in (the production mode), the results are exactly
  the runner's OWN plan for the keys the oracle saw read, which the runner
  draws up without running anything, so it cannot pick its plan after seeing
  which mutations survive. The line counts conclusive mutations only:
  `mutation n/m fail`, ` · k inconclusive` when k > 0, `mutation 0 conclusive`
  when none is (PLAN-v0.14 §1.5's panel default) — never `0/0`, and never the
  words GLOSSARY's mutation row forbids.

*Rejected* (P2.0 design D-10 and its review): the M1 check as a before/after
diff alone — a mutation that writes the model and restores it leaves the tree
identical and is exactly the in-place mutation §1.5 redesigned away (a crash
mid-run leaves it mutated; a concurrent reader sees it), so a planted
write-and-restore shows the diff missing it; a subprocess driver like
`test_records._WRITES_DRIVER` — half a second a runner, and the planted runners
would have to live in an importable module; refactoring `_WRITES_DRIVER` into a
shared helper — an edit to invariant 8's test for no strength gained (R-6), so
the forty lines of hook logic are written again here, on purpose; ignored
scratch under `.atompipe/out` during the run — evidence from a design that
never existed, beside real evidence a report can cite (mutated runs take an out
dir outside the project, and recording an outcome is the caller's write, held
by invariant 8); the spine's own tracer as the oracle (the test would call the
code it guards; a tracer that under-records would make the runner and the
oracle agree that a read key was unread); dotted keys (`a.b`) — pack keys carry
dots (`fdm.bbox_mm`), so a key is a tuple path.

The stated limit: a write made by a subprocess, or by C code that bypasses
`open`, and undone before the run ends is seen by neither the hook nor the
digest. A write that persists is seen by the digest whoever made it.

**Planned, not yet numbered.** CLAUDE.md states invariants 1–9; this class is
`test_meta.PLANNED_INVARIANT_CLASSES[15]` (R-7 binds it from its first line)
and moves to `INVARIANT_CLASSES` with CLAUDE.md's new item in the checkpoint
that makes mutation mechanical (P2.3). Until then `SUBJECTS` is empty and the
tripwire below keeps it from staying empty once the spine runs a mutation.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_mutation.py -v
"""
from __future__ import annotations

import ast
import collections
import contextlib
import copy
import dataclasses
import hashlib
import math
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from typing import Any, Callable, Iterator, NamedTuple

import _env
import _projects
from atompipe import gates as gates_mod
from atompipe import modelio
from atompipe import packs as packs_mod
from atompipe.models import GateSpec, NegativeControl, Tier, Verdict
from atompipe.verdicts import GateTrace

SRC = os.path.join(_env.REPO, "src", "atompipe")

#: GLOSSARY §2's outcome words, which a mutation result's `outcome` takes, or
#: None when the runner did not run it (its key is outside the read set).
OUTCOMES = ("pass", "fail", "skipped", "errored")

#: `Verdict.outcome` -> GLOSSARY's word. The spine says `error` today.
_WORD = {"pass": "pass", "fail": "fail", "skipped": "skipped", "error": "errored"}

#: GLOSSARY §2, mutation, *Never say*: none of these may reach the line.
NEVER_SAY = re.compile(r"(?i)\b(?:killed|kill|survived?|survivors?|flip\w*|mutants?|perturb\w*)\b")


# --------------------------------------------------------------------------- #
# the interface a runner meets
# --------------------------------------------------------------------------- #
class Mutation(NamedTuple):
    """One planned change: the param at `key` (a tuple path) set to `after`."""
    key: tuple
    after: Any


class MutationResult(NamedTuple):
    """What a runner says happened to one mutation. `outcome` is the run's, in
    GLOSSARY's four words, or None when the runner did not run it; `conclusive`
    is whether it counts toward `n/m`. *Rejected:* an `inconclusive` outcome
    value (review): GLOSSARY allows an outcome only its four words, and the
    single value dropped which one the run produced — a crash read the same as
    a key never read."""
    key: tuple
    before: Any
    after: Any
    outcome: str | None
    conclusive: bool
    why: str = ""


def _get(params: Any, key: tuple) -> Any:
    for part in key:
        params = params[part]
    return params


def _set(params: dict, key: tuple, value: Any) -> None:
    for part in key[:-1]:
        params = params[part]
    params[key[-1]] = value


def _leaves(params: Any, prefix: tuple = ()) -> list[tuple]:
    if isinstance(params, dict):
        out: list[tuple] = []
        for k, v in params.items():
            out += _leaves(v, prefix + (k,))
        return out
    return [prefix] if prefix else []


def _numbers(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def mutable(value: Any) -> bool:
    """Whether a small deliberate change of `value` exists: a number, a string,
    a flag, or a list or tuple of numbers (a bounding box). Every read key
    holding one must be changed by a runner's own plan; a read key holding
    anything else (None, a nested structure) is not required to be."""
    return (isinstance(value, (bool, str)) or _numbers(value)
            or (isinstance(value, (list, tuple)) and bool(value) and all(map(_numbers, value))))


def _operators(value: Any) -> list[Any]:
    """The reference runner's operator set: deterministic, blind to outcomes."""
    if isinstance(value, bool):
        return [not value]
    if _numbers(value):
        return [value * 2, -value] if value else [1]
    if isinstance(value, str):
        return [value + "-mutated"]
    if mutable(value):
        return [type(value)(x * 2 for x in value)]
    return []


class ReferenceRunner:
    """The harness's positive control: honest on every rule, and the shape a
    real runner (P2.3) takes from outside. It copies the design before each
    change, runs through `gates.run_gate` in an out dir outside every project,
    reads the read set from the spine's trace, and reports by the rules."""

    def __init__(self, **where: str) -> None:
        self.where = where              # the planted runners' targets; unused here

    def plan(self, spec: GateSpec, good_params: dict, read: set) -> list[Mutation]:
        """The mutations for the keys in `read`, from the params alone: no run."""
        out = []
        for key in sorted(read, key=repr):
            try:
                value = _get(good_params, key)
            except (KeyError, TypeError, IndexError):
                continue
            out += [Mutation(key, after) for after in _operators(value)]
        return out

    def read_set(self, spec: GateSpec, fn: Callable, good_ctx: Any, out_dir: str) -> set:
        trace = GateTrace()
        gates_mod.run_gate(spec, fn, dataclasses.replace(
            good_ctx, params=copy.deepcopy(good_ctx.params), out_dir=out_dir), trace=trace)
        leaves = set(_leaves(good_ctx.params))
        whole = set(trace.whole)
        return {leaf for leaf in leaves
                if leaf in trace.params or any(leaf[:i] in whole for i in range(len(leaf)))}

    def one(self, spec: GateSpec, fn: Callable, good_ctx: Any, base: dict, read: set,
            m: Mutation, out_dir: str) -> MutationResult:
        before = _get(base, m.key)
        if m.key not in read:
            return MutationResult(m.key, before, m.after, None, False, "outside the read set")
        params = copy.deepcopy(base)
        _set(params, m.key, m.after)
        verdict = gates_mod.run_gate(spec, fn, dataclasses.replace(good_ctx, params=params,
                                                                   out_dir=out_dir))
        outcome = _WORD[verdict.outcome]
        return MutationResult(m.key, before, m.after, outcome, outcome in ("pass", "fail"),
                              verdict.error or verdict.skip_reason or verdict.detail)

    def run(self, spec: GateSpec, fn: Callable, good_ctx: Any,
            plan: list[Mutation] | None = None) -> list[MutationResult]:
        out_dir = tempfile.mkdtemp(prefix="atompipe-mutation-")
        try:
            base = copy.deepcopy(good_ctx.params)
            read = self.read_set(spec, fn, good_ctx, out_dir)
            todo = self.plan(spec, base, read) if plan is None else plan
            return [self.one(spec, fn, good_ctx, base, read, m, out_dir) for m in todo]
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    @staticmethod
    def line(results: list[MutationResult]) -> str:
        conclusive = [r for r in results if r.conclusive]
        fails = sum(1 for r in conclusive if r.outcome == "fail")
        k = len(results) - len(conclusive)
        head = f"mutation {fails}/{len(conclusive)} fail" if conclusive else "mutation 0 conclusive"
        return head + (f" · {k} inconclusive" if k else "")


def expected_line(truth: list[tuple[str, bool]]) -> str:
    """The line for `(oracle outcome, conclusive)` pairs, written out again here
    rather than borrowed from the reference runner it judges."""
    m = sum(1 for _o, c in truth if c)
    n = sum(1 for o, c in truth if c and o == "fail")
    k = len(truth) - m
    return (f"mutation {n}/{m} fail" if m else "mutation 0 conclusive") + (
        f" · {k} inconclusive" if k else "")


# --------------------------------------------------------------------------- #
# M3's oracle: not the spine's tracer, not the spine's outcome rules
# --------------------------------------------------------------------------- #
class _Recorder(dict):
    """A dict that notes every path read through it — `[k]`, `get`, `in` — and
    wraps a nested dict as it hands it out, so `params["config"]["load_n"]` is
    recorded as `("config", "load_n")`. A bulk read (iteration, `items`, a copy)
    records every leaf below it. *Rejected:* replaying the spine's trace through
    a mapping (`gates._replay_reads`), which follows two levels and only what the
    tracer saw — the tracer is what this oracle exists to second-guess."""

    def __init__(self, data: dict, path: tuple, log: set) -> None:
        super().__init__(data)
        self._path = path
        self._log = log

    def _note(self, key: Any) -> None:
        self._log.add(self._path + (key,))

    def _all(self) -> None:
        for leaf in _leaves(dict(dict.items(self))):
            self._log.add(self._path + leaf)
        self._log.add(self._path)

    def __getitem__(self, key: Any) -> Any:
        value = dict.__getitem__(self, key)
        self._note(key)
        return _Recorder(value, self._path + (key,), self._log) if isinstance(value, dict) else value

    def get(self, key: Any, default: Any = None) -> Any:
        if dict.__contains__(self, key):
            return self[key]
        self._note(key)
        return default

    def __contains__(self, key: Any) -> bool:
        self._note(key)
        return dict.__contains__(self, key)

    def __iter__(self) -> Iterator:
        self._all()
        return dict.__iter__(self)

    def keys(self):
        self._all()
        return dict.keys(self)

    def values(self):
        self._all()
        return dict.values(self)

    def items(self):
        self._all()
        return dict.items(self)

    def copy(self) -> dict:
        self._all()
        return copy.deepcopy(dict(dict.items(self)))

    def __copy__(self) -> dict:
        return self.copy()

    def __deepcopy__(self, memo: dict) -> dict:
        return self.copy()


def _oracle_outcome(result: Any) -> str:
    """The outcome of whatever a gate function returned, by the rules as stated
    (gates.run_gate's docstring, GLOSSARY §2) and restated here: a crash or an
    unreadable answer is errored; error beats skipped beats the pass flag; a pass
    needs a builtin `True` and a finite measurement."""
    if isinstance(result, Verdict):
        error, skipped, passed, measured = result.error, result.skipped, result.passed, result.measured
    elif isinstance(result, dict):
        passed = result.get("passed", result.get("pass", result.get("ok", None)))
        if passed is None and not result.get("skipped"):
            return "errored"
        error, skipped, measured = result.get("error"), result.get("skipped"), result.get("measured")
    elif isinstance(result, bool):
        error, skipped, passed, measured = "", False, result, None
    elif isinstance(result, (tuple, list)) and 1 <= len(result) <= 2 and isinstance(result[0], bool):
        error, skipped, passed, measured = "", False, result[0], None
    else:
        return "errored"
    if error:
        return "errored"
    if skipped:
        return "skipped"
    if isinstance(measured, float) and not math.isfinite(measured):
        return "errored"
    return "pass" if passed is True else "fail"


def oracle(spec: GateSpec, fn: Callable, good_ctx: Any, params: dict,
           mutation: Mutation | None = None) -> tuple[str, set]:
    """`fn` called directly on a copy of `params` (mutated when asked) behind a
    recorder: the outcome and every path read."""
    data = copy.deepcopy(params)
    if mutation is not None:
        _set(data, mutation.key, mutation.after)
    log: set = set()
    out_dir = tempfile.mkdtemp(prefix="atompipe-oracle-")
    try:
        ctx = dataclasses.replace(good_ctx, params=_Recorder(data, (), log), out_dir=out_dir)
        try:
            return _oracle_outcome(fn(ctx)), log
        except Exception:                            # noqa: BLE001 - a crash is an outcome
            return "errored", log
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# M1's watch: an audit hook armed only inside the run, and a tree digest
# --------------------------------------------------------------------------- #
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


class _Window:
    def __init__(self, roots: list[str]) -> None:
        self.roots = [os.path.realpath(r) for r in roots]
        self.events: list[str] = []

    def inside(self, path: Any) -> str | None:
        if isinstance(path, int) or path is None:
            return None
        try:
            full = os.path.realpath(os.path.abspath(os.fsdecode(path)))
        except (TypeError, ValueError):
            return None
        for root in self.roots:
            if full == root or full.startswith(root + os.sep):
                return full
        return None

    def note(self, op: str, *paths: Any) -> None:
        hits = [p for p in (self.inside(path) for path in paths) if p]
        if hits:
            self.events.append(f"{op} {' -> '.join(hits)}")


_ARMED: list[_Window] = []
_HOOK: list[bool] = []


def _hook(event: str, args: tuple) -> None:
    if not _ARMED:
        return
    window = _ARMED[-1]
    try:
        if event == "open":
            path, mode, flags = args
            if (isinstance(mode, str) and set(mode) & set("wax+")) or (
                    isinstance(flags, int) and flags & _WRITE_FLAGS):
                window.note("write", path)
        elif event == "os.rename":
            window.note("rename", args[0], args[1])
        elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown",
                       "os.utime", "os.truncate", "shutil.rmtree"):
            window.note(event.split(".", 1)[1], args[0])
        elif event in ("os.link", "os.symlink", "shutil.copyfile", "shutil.copymode",
                       "shutil.copystat"):
            window.note(event.split(".", 1)[1], args[1])
    except Exception as exc:                         # noqa: BLE001 - a hook must not raise
        window.events.append(f"hook-error {exc!r}")


@contextlib.contextmanager
def watching(roots: list[str]) -> Iterator[_Window]:
    """Arm the process-wide hook (installed on first use; it returns on its first
    line while unarmed) for `roots`, with bytecode writes off for the window."""
    if not _HOOK:
        sys.addaudithook(_hook)
        _HOOK.append(True)
    window = _Window(roots)
    bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    _ARMED.append(window)
    try:
        yield window
    finally:
        _ARMED.pop()
        sys.dont_write_bytecode = bytecode


def tree_digest(root: str) -> dict[str, tuple]:
    """Every entry under `root`: mode, size, mtime and content hash for a file,
    mode and mtime for a directory (a file created and removed moves it)."""
    out: dict[str, tuple] = {}
    if not os.path.lexists(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        st = os.lstat(dirpath)
        out[os.path.relpath(dirpath, root) + os.sep] = (st.st_mode, st.st_mtime_ns)
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                content = os.readlink(path)
            else:
                with open(path, "rb") as fh:
                    content = hashlib.sha256(fh.read()).hexdigest()
            out[os.path.relpath(path, root)] = (st.st_mode, st.st_size, st.st_mtime_ns, content)
    return out


def _diff(before: dict, after: dict) -> list[str]:
    return sorted(f"{'+' if k not in before else '-' if k not in after else '~'}{k}"
                  for k in set(before) | set(after) if before.get(k) != after.get(k))


# --------------------------------------------------------------------------- #
# the judge
# --------------------------------------------------------------------------- #
class Judgement(NamedTuple):
    hook: list[str]        # M1, what the audit hook saw written
    tree: list[str]        # M1, what the digest saw change
    memory: list[str]      # M2
    honesty: list[str]     # M3
    line: str

    def problems(self) -> list[str]:
        return ([f"M1 hook: {e}" for e in self.hook] + [f"M1 tree: {e}" for e in self.tree]
                + [f"M2: {e}" for e in self.memory] + [f"M3: {e}" for e in self.honesty])


def _live(spec: GateSpec, fn: Callable, good_ctx: Any, out_dir: str) -> tuple:
    v = gates_mod.run_gate(spec, fn, dataclasses.replace(good_ctx, out_dir=out_dir))
    return (v.outcome, v.measured, v.detail, v.error)


def judge(runner: Any, spec: GateSpec, fn: Callable, good_ctx: Any, *, roots: list[str],
          plan: list[Mutation] | None = None, design: dict | None = None) -> Judgement:
    """Run `runner` on one evaluator and hold it to M1–M3."""
    if spec.requires_tools or spec.requires_python or getattr(spec, "requires_one_of", ()):
        raise AssertionError(f"{spec.id}: the oracle does not judge availability; "
                             f"judge an evaluator with no requirements")
    snapshot = copy.deepcopy(good_ctx.params)
    design_before = copy.deepcopy(design)
    scratch = tempfile.mkdtemp(prefix="atompipe-judge-")
    try:
        live_before = _live(spec, fn, good_ctx, scratch)
        _known, log = oracle(spec, fn, good_ctx, snapshot)
        read = {leaf for leaf in _leaves(snapshot) if leaf in log}
        before = {root: tree_digest(root) for root in roots}
        with watching(roots) as window:
            results = list(runner.run(spec, fn, good_ctx, plan))
        tree = [f"{root}: {change}" for root in roots
                for change in _diff(before[root], tree_digest(root))]

        memory = []
        if good_ctx.params != snapshot:
            memory.append("the known-good params it was handed changed")
        if design is not None and design != design_before:
            memory.append("the module-level known-good design changed")
        live_after = _live(spec, fn, good_ctx, scratch)
        if live_after != live_before:
            memory.append(f"a live run reads {live_after[:3]} after, {live_before[:3]} before")

        honesty: list[str] = []
        expected = plan if plan is not None else runner.plan(spec, copy.deepcopy(snapshot), set(read))
        if plan is None:
            for leaf in sorted(read, key=repr):
                if mutable(_get(snapshot, leaf)) and not any(m.key == leaf for m in expected):
                    honesty.append(f"{leaf} was read and the runner's own plan never changes it")
        want = collections.Counter((m.key, repr(m.after)) for m in expected)
        got = collections.Counter((r.key, repr(r.after)) for r in results)
        for key, after in sorted((want - got).elements()):
            honesty.append(f"{key} -> {after}: planned, no result")
        for key, after in sorted((got - want).elements()):
            honesty.append(f"{key} -> {after}: a result for nothing planned")
        truth = []
        for m in expected:
            outcome, _log = oracle(spec, fn, good_ctx, snapshot, m)
            truth.append((outcome, m.key in read and outcome in ("pass", "fail")))
        by_key = {(m.key, repr(m.after)): t for m, t in zip(expected, truth)}
        for r in results:
            if (r.key, repr(r.after)) not in by_key:
                continue
            outcome, conclusive = by_key[(r.key, repr(r.after))]
            where = f"{r.key} -> {r.after!r}"
            if r.outcome is not None and r.outcome not in OUTCOMES:
                honesty.append(f"{where}: outcome {r.outcome!r} is not one of {OUTCOMES}")
            elif r.outcome is not None and r.outcome != outcome:
                honesty.append(f"{where}: reported {r.outcome}, the run is {outcome}")
            if r.outcome is None and r.key in read:
                honesty.append(f"{where}: its key was read and it was never run")
            if bool(r.conclusive) != conclusive:
                honesty.append(f"{where}: conclusive={r.conclusive}, the run says {conclusive}")
        line = runner.line(results)
        if line != expected_line(truth):
            honesty.append(f"line {line!r}, the results say {expected_line(truth)!r}")
        if NEVER_SAY.search(line):
            honesty.append(f"line {line!r} uses a word GLOSSARY's mutation row forbids")
        return Judgement(list(window.events), tree, memory, honesty, line)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# --------------------------------------------------------------------------- #
# the toy evaluator, its design and its ground truth
# --------------------------------------------------------------------------- #
#: The module-level known-good design (M2 watches it, as it watches the
#: bracket's `known_good.CONFIG`).
TOY_DESIGN: dict[str, Any] = {"a": 5, "b": 1}

TOY_SPEC = GateSpec(id="toy.limit", claims=["toy"], tier=Tier.INSTANT,
                    negative_control=NegativeControl(fixture="x:y"))


def toy_gate(ctx: Any) -> Verdict:
    """Reads `a` only: skips below zero, crashes at 13, passes up to 10."""
    a = ctx.params["a"]
    if a < 0:
        return Verdict(gate=TOY_SPEC.id, passed=False, skipped=True,
                       skip_reason="needs a non-negative a")
    if a == 13:
        raise ZeroDivisionError("planted at 13")
    return Verdict(gate=TOY_SPEC.id, passed=a <= 10, measured=float(a), limit=10.0,
                   detail=f"a = {a}")


#: The plan, and the truth about each mutation: (outcome, conclusive).
TOY_PLAN = [Mutation(("a",), 20), Mutation(("a",), 7), Mutation(("b",), 100),
            Mutation(("a",), -1), Mutation(("a",), 13)]
TOY_TRUTH = [("fail", True), ("pass", True), ("pass", False), ("skipped", False),
             ("errored", False)]
TOY_INCONCLUSIVE = [TOY_PLAN[2], TOY_PLAN[3], TOY_PLAN[4]]


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# -- the planted runners ------------------------------------------------------ #
class WritesIntoTheTree(ReferenceRunner):
    def run(self, spec, fn, good_ctx, plan=None):
        _write(os.path.join(self.where["project"], "model", "mutated.json"), '{"a": 20}\n')
        return super().run(spec, fn, good_ctx, plan)


class WritesThenRestores(ReferenceRunner):
    """The in-place mutation §1.5 redesigned away: edit the model, run, put the
    bytes and the mtime back. The digest cannot see it; the hook must."""

    def run(self, spec, fn, good_ctx, plan=None):
        path = os.path.join(self.where["project"], "model", "toy.py")
        with open(path, "rb") as fh:
            original = fh.read()
        st = os.stat(path)
        with open(path, "wb") as fh:
            fh.write(original.replace(b"5", b"20"))
        try:
            return super().run(spec, fn, good_ctx, plan)
        finally:
            with open(path, "wb") as fh:
                fh.write(original)
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


class WritesIntoAPack(ReferenceRunner):
    def run(self, spec, fn, good_ctx, plan=None):
        with open(os.path.join(self.where["pack"], "gates", "x.py"), "a", encoding="utf-8") as fh:
            fh.write("# mutated\n")
        return super().run(spec, fn, good_ctx, plan)


class WritesIgnoredScratch(ReferenceRunner):
    def run(self, spec, fn, good_ctx, plan=None):
        _write(os.path.join(self.where["project"], ".atompipe", "out", "mutation", "a.json"),
               "{}\n")
        return super().run(spec, fn, good_ctx, plan)


class WritesBytecode(ReferenceRunner):
    def run(self, spec, fn, good_ctx, plan=None):
        _write(os.path.join(self.where["project"], "model", "__pycache__",
                            "toy.cpython-312.pyc"), "not bytecode")
        return super().run(spec, fn, good_ctx, plan)


class AliasesTheContext(ReferenceRunner):
    """Applies each mutation to the params it was handed, in place."""

    def one(self, spec, fn, good_ctx, base, read, m, out_dir):
        before = _get(good_ctx.params, m.key)
        result = super().one(spec, fn, good_ctx, base, read, m, out_dir)
        _set(good_ctx.params, m.key, m.after)
        return result._replace(before=before)


class AliasesTheDesign(ReferenceRunner):
    def run(self, spec, fn, good_ctx, plan=None):
        results = super().run(spec, fn, good_ctx, plan)
        TOY_DESIGN["a"] = 20
        return results


class ReportsAsFail(ReferenceRunner):
    """Reports the mutation planned at `which` as a conclusive fail."""

    def __init__(self, which: Mutation, **where: str) -> None:
        super().__init__(**where)
        self.which = which

    def one(self, spec, fn, good_ctx, base, read, m, out_dir):
        result = super().one(spec, fn, good_ctx, base, read, m, out_dir)
        return result._replace(outcome="fail", conclusive=True) if m == self.which else result


class DropsOne(ReferenceRunner):
    def __init__(self, which: Mutation, **where: str) -> None:
        super().__init__(**where)
        self.which = which

    def run(self, spec, fn, good_ctx, plan=None):
        return [r for r in super().run(spec, fn, good_ctx, plan)
                if Mutation(r.key, r.after) != self.which]


class OmitsTheSurvivor(ReferenceRunner):
    """With no plan handed in: draws up its own plan honestly, runs it, and
    leaves out the mutation that passed — the qualification-inflating direction."""

    def plan(self, spec, good_params, read):
        return [m for m in TOY_PLAN if m.key in read]

    def run(self, spec, fn, good_ctx, plan=None):
        return [r for r in super().run(spec, fn, good_ctx, plan) if r.outcome != "pass"]


class UnderRecords(ReferenceRunner):
    """Its tracer misses `a`: every mutation of `a` reads as outside the read set."""

    def read_set(self, spec, fn, good_ctx, out_dir):
        return super().read_set(spec, fn, good_ctx, out_dir) - {("a",)}


def _line_runner(fn: Callable[[list[MutationResult]], str]) -> type:
    return type("LineRunner", (ReferenceRunner,), {"line": staticmethod(fn)})


def _fails(results):
    return sum(1 for r in results if r.conclusive and r.outcome == "fail")


#: Honest results, a wrong line: each must be caught on its own.
WRONG_LINES = {
    "inconclusive folded into the fails":
        lambda rs: f"mutation {_fails(rs) + sum(1 for r in rs if not r.conclusive)}/{len(rs)} fail",
    "inconclusive in the denominator":
        lambda rs: f"mutation {_fails(rs)}/{len(rs)} fail",
    "a forbidden word":
        lambda rs: (f"mutation {_fails(rs)}/{sum(1 for r in rs if r.conclusive)} killed"
                    f" · {sum(1 for r in rs if not r.conclusive)} inconclusive"),
}


def _zero_over_zero(rs):
    return f"mutation {_fails(rs)}/{sum(1 for r in rs if r.conclusive)} fail"


# --------------------------------------------------------------------------- #
# real evaluators: the bracket's six and a bundled pack, for SUBJECTS
# --------------------------------------------------------------------------- #
#: Mutation entry points in `src/atompipe`, each run through M1–M3 on real
#: evaluators: `(name, factory)`, the factory returning an object with `plan`,
#: `run` and `line` as `ReferenceRunner` has them (an adapter over the spine's
#: entry point). Empty until P2.3, which must add its own — the tripwire below
#: is red until it does.
SUBJECTS: list[tuple[str, Callable[..., Any]]] = []

#: Names in `src/atompipe` that match `(?i)mutat` and are not a mutation entry
#: point, each with why. Empty: today no name matches.
NOT_A_MUTATION: dict[str, str] = {}

#: The bundled pack every subject also runs over, installed into a project.
#: Why beam-analytic: no tool requirement, so every gate runs on any machine,
#: and its baseline is the pack's known-good design (invariant 6). Loaded from
#: the checkout, not from a copy: one process loads a pack from one place
#: (`packs._load_dir` refuses a second copy under the same name), and the
#: wrapper below has already loaded the bundled one to name its claims.
SUBJECT_PACK = "beam-analytic"


class RealEvaluator(NamedTuple):
    where: str
    spec: GateSpec
    fn: Callable
    good: Any                 # the known-good context
    roots: list[str]          # watched: the project and every pack directory
    design: dict | None       # the module-level design the context came from
    project: str


def real_evaluators(tmp: str) -> list[RealEvaluator]:
    """The bracket's six gates on its known-good design, and `SUBJECT_PACK`'s
    gates on their baseline in a project that installs the pack. Watched: the
    project, the repository's `packs/` (where the installed pack lives), and
    every pack directory the search order names, the user's store included."""
    out = []
    scratch = os.path.join(tmp, "out")
    os.makedirs(scratch)
    root = _projects.bracket_copy(os.path.join(tmp, "bracket"), migrated=True)
    registry = gates_mod.Registry()
    gates_mod.load_project_gates(root, registry)
    known_good = modelio.load_path(os.path.join(root, "selftest", "known_good.py"))
    good = known_good.context(gates_mod.GateContext(root=root, out_dir=scratch, tier=0))
    roots = list(dict.fromkeys([root, _projects.PACKS]
                               + packs_mod.search_paths(root, existing_only=False)))
    for spec, fn in registry.pairs():
        out.append(RealEvaluator("bracket", spec, fn, good, roots, known_good.CONFIG, root))
    wrap = _projects.wrap_pack_baseline(SUBJECT_PACK, os.path.join(tmp, "wrap"))
    pregistry = gates_mod.Registry()
    packs_mod.load_gates(SUBJECT_PACK, pregistry, root=wrap, include_env=False,
                         include_user=False)
    pgood = packs_mod.baseline_context(os.path.join(_projects.PACKS, SUBJECT_PACK),
                                       out_dir=scratch)
    proots = list(dict.fromkeys([wrap, _projects.PACKS]
                                + packs_mod.search_paths(wrap, existing_only=False)))
    for spec, fn in pregistry.pairs():
        out.append(RealEvaluator(SUBJECT_PACK, spec, fn, pgood, proots, None, wrap))
    return out


def judge_on_real_evaluators(test: Any, make: Callable[[RealEvaluator], Any]
                             ) -> dict[str, list[str]]:
    """`make(evaluator)` -> a runner; every problem it shows, by evaluator."""
    found: dict[str, list[str]] = {}
    evaluators = real_evaluators(test.tmp())
    test.assertGreaterEqual(len([e for e in evaluators if e.where == "bracket"]), 6)
    test.assertGreaterEqual(len([e for e in evaluators if e.where == SUBJECT_PACK]), 1)
    for e in evaluators:
        judgement = judge(make(e), e.spec, e.fn, e.good, roots=e.roots, design=e.design)
        if judgement.problems():
            found[f"{e.where}:{e.spec.id}"] = judgement.problems()
    return found


# -- the tripwire's scan ---------------------------------------------------- #
_NAMED = re.compile(r"(?i)mutat")
_SPOKEN = re.compile(r"(?i)\bmutations?\b")


def _docstrings(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def mutation_sites(sources: dict[str, str]) -> tuple[list[str], list[str]]:
    """`(named, spoken)`: modules, functions and classes whose name matches
    `mutat`, and string constants that are not docstrings saying "mutation".
    Comments and docstrings are not walked — "mutates" in a comment is the
    spine explaining itself; a name or a printed word is behaviour."""
    named: list[str] = []
    spoken: list[str] = []
    for rel, text in sorted(sources.items()):
        stem = os.path.splitext(os.path.basename(rel))[0]
        if rel.endswith(".py"):
            if _NAMED.search(stem):
                named.append(rel)
            tree = ast.parse(text, rel)
            docs = _docstrings(tree)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                        and _NAMED.search(node.name):
                    named.append(f"{rel}:{node.name}")
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and id(node) not in docs and _SPOKEN.search(node.value):
                    spoken.append(f"{rel}:{node.lineno}")
        elif _SPOKEN.search(text):
            spoken.append(rel)
    return named, spoken


def spine_sources() -> dict[str, str]:
    """Every `.py` under `src/atompipe` and every text file of the site template
    (a qualification line printed by the page counts as spoken)."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if name.endswith((".py", ".js", ".html", ".css")):
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as fh:
                    out[os.path.relpath(path, SRC).replace(os.sep, "/")] = fh.read()
    return out


# --------------------------------------------------------------------------- #
class MutationIsSealed(_env.EnvCase):
    """(planned 15) A mutation pass writes nothing in the project or a pack
    directory, leaves the known-good design as it found it, and never reports an
    inconclusive mutation as a fail — over the runner it holds, every planted
    runner below is caught by the rule it breaks."""

    def setUp(self):
        self.project = self.tmp()
        self.pack = self.tmp()
        _write(os.path.join(self.project, "model", "toy.py"), "A = 5\nB = 1\n")
        os.makedirs(os.path.join(self.project, ".atompipe"))
        _write(os.path.join(self.pack, "gates", "x.py"), "X = 1\n")
        self.roots = [self.project, self.pack]
        design = copy.deepcopy(TOY_DESIGN)
        self.addCleanup(lambda: (TOY_DESIGN.clear(), TOY_DESIGN.update(design)))

    def good(self) -> gates_mod.GateContext:
        return gates_mod.GateContext(root=self.project, params=copy.deepcopy(TOY_DESIGN),
                                     out_dir=self.tmp(), tier=0)

    def judged(self, runner_cls: type, *args: Any, plan: Any = TOY_PLAN) -> Judgement:
        runner = runner_cls(*args, project=self.project, pack=self.pack)
        return judge(runner, TOY_SPEC, toy_gate, self.good(), roots=self.roots, plan=plan,
                     design=TOY_DESIGN)

    # -- positive controls --------------------------------------------------- #
    def test_the_honest_runner_passes_every_check(self):
        for plan in (TOY_PLAN, None):
            with self.subTest(plan="given" if plan else "its own"):
                judgement = self.judged(ReferenceRunner, plan=plan)
                self.assertEqual(judgement.problems(), [])
        self.assertEqual(self.judged(ReferenceRunner).line,
                         "mutation 1/2 fail · 3 inconclusive")

    def test_the_oracle_agrees_with_the_ground_truth(self):
        good = self.good()
        _outcome, log = oracle(TOY_SPEC, toy_gate, good, TOY_DESIGN)
        read = {leaf for leaf in _leaves(TOY_DESIGN) if leaf in log}
        self.assertEqual(read, {("a",)})
        truth = []
        for m in TOY_PLAN:
            outcome, _log = oracle(TOY_SPEC, toy_gate, good, TOY_DESIGN, m)
            truth.append((outcome, m.key in read and outcome in ("pass", "fail")))
        self.assertEqual(truth, TOY_TRUTH)
        _o, nested = oracle(TOY_SPEC, lambda ctx: ctx.params["n"]["c"] > 0, good,
                            {"n": {"c": 1, "d": 2}})
        self.assertIn(("n", "c"), nested)
        self.assertNotIn(("n", "d"), nested)
        self.assertEqual(expected_line(TOY_TRUTH), "mutation 1/2 fail · 3 inconclusive")
        self.assertEqual(expected_line([TOY_TRUTH[i] for i in (2, 3, 4)]),
                         "mutation 0 conclusive · 3 inconclusive")

    # -- M1 ------------------------------------------------------------------ #
    def test_a_mutation_that_writes_into_the_tree_is_caught(self):
        judgement = self.judged(WritesIntoTheTree)
        self.assertTrue(judgement.hook, judgement)
        self.assertTrue(judgement.tree, judgement)

    def test_a_write_then_restore_is_caught_by_the_hook(self):
        judgement = self.judged(WritesThenRestores)
        self.assertEqual(judgement.tree, [], "the digest half was expected to miss it")
        self.assertTrue(any("toy.py" in e for e in judgement.hook), judgement)

    def test_a_mutation_that_writes_into_a_pack_is_caught(self):
        judgement = self.judged(WritesIntoAPack)
        self.assertTrue(judgement.hook and judgement.tree, judgement)

    def test_ignored_scratch_under_the_project_is_caught(self):
        judgement = self.judged(WritesIgnoredScratch)
        self.assertTrue(judgement.hook and judgement.tree, judgement)

    def test_bytecode_written_into_the_tree_is_caught(self):
        judgement = self.judged(WritesBytecode)
        self.assertTrue(judgement.hook and judgement.tree, judgement)

    # -- M2 ------------------------------------------------------------------ #
    def test_a_mutation_that_aliases_the_known_good_design_is_caught(self):
        for runner in (AliasesTheContext, AliasesTheDesign):
            with self.subTest(runner=runner.__name__):
                judgement = self.judged(runner)
                self.assertTrue(judgement.memory, judgement)
                TOY_DESIGN.update({"a": 5, "b": 1})

    # -- M3 ------------------------------------------------------------------ #
    def test_an_inconclusive_mutation_reported_as_a_fail_is_caught(self):
        for which in TOY_INCONCLUSIVE:
            with self.subTest(mutation=which):
                self.assertTrue(self.judged(ReportsAsFail, which).honesty)

    def test_a_conclusive_pass_reported_as_a_fail_is_caught(self):
        judgement = self.judged(ReportsAsFail, TOY_PLAN[1])
        self.assertTrue(any("reported fail, the run is pass" in p for p in judgement.honesty),
                        judgement)

    def test_a_runner_that_drops_an_inconclusive_mutation_is_caught(self):
        judgement = self.judged(DropsOne, TOY_PLAN[2])
        self.assertTrue(any("planned, no result" in p for p in judgement.honesty), judgement)

    def test_a_runner_that_omits_the_survivor_from_its_own_plan_is_caught(self):
        judgement = self.judged(OmitsTheSurvivor, plan=None)
        self.assertTrue(any("planned, no result" in p for p in judgement.honesty), judgement)

    def test_a_tracer_that_under_records_is_caught(self):
        for plan in (TOY_PLAN, None):
            with self.subTest(plan="given" if plan else "its own"):
                self.assertTrue(self.judged(UnderRecords, plan=plan).honesty)

    def test_a_line_that_miscounts_honest_results_is_caught(self):
        for name, line in WRONG_LINES.items():
            with self.subTest(name):
                judgement = self.judged(_line_runner(line))
                self.assertTrue(any(p.startswith("line ") for p in judgement.honesty), judgement)

    def test_none_conclusive_says_so(self):
        honest = self.judged(ReferenceRunner, plan=TOY_INCONCLUSIVE)
        self.assertEqual(honest.problems(), [])
        self.assertEqual(honest.line, "mutation 0 conclusive · 3 inconclusive")
        planted = self.judged(_line_runner(_zero_over_zero), plan=TOY_INCONCLUSIVE)
        self.assertEqual(planted.line, "mutation 0/0 fail")
        self.assertTrue(planted.honesty)

    # -- the tripwire ---------------------------------------------------------- #
    def test_every_mutation_entry_point_is_a_subject(self):
        """D-11, by name AND by behaviour: a function, class or module named for
        mutation, or a string the spine would print saying "mutation", needs a
        subject under this harness — a pass named `_flip_params` that prints
        `mutation 4/4 fail` trips the second half. And every subject runs through
        M1–M3 on the bracket's six gates and on an installed pack's."""
        named, spoken = mutation_sites(spine_sources())
        listed = {name for name, _factory in SUBJECTS} | set(NOT_A_MUTATION)
        self.assertEqual([n for n in named if n not in listed], [])
        if spoken:
            self.assertTrue(SUBJECTS, f"the spine speaks of mutation ({spoken[:3]}) and no "
                                      f"entry point is in SUBJECTS")
        for name, factory in SUBJECTS:
            with self.subTest(subject=name):
                self.assertEqual(judge_on_real_evaluators(self, lambda _e: factory()), {})

    def test_the_tripwire_refuses_what_it_forbids(self):
        named, spoken = mutation_sites({"planted.py": "def mutate_params(ctx):\n    return ctx\n"})
        self.assertEqual((named, spoken), (["planted.py:mutate_params"], []))
        named, spoken = mutation_sites({"planted.py": (
            '"""Talks about mutation in a docstring only."""\n'
            'def flip(rs):\n    """A mutation pass."""\n'
            '    return f"mutation {rs}/4 fail"\n')})
        self.assertEqual((named, spoken), ([], ["planted.py:4"]))
        self.assertEqual(mutation_sites({"lib/planted.js": "const L = 'mutation 4/4 fail';"}),
                         ([], ["lib/planted.js"]))
        self.assertEqual(mutation_sites({"mutation.py": ""}), (["mutation.py"], []))
        self.assertGreater(len(spine_sources()), 15)

    def test_the_subject_harness_holds_real_evaluators(self):
        """The harness SUBJECTS will run through, exercised before any subject
        exists: the reference runner is clean on the bracket's six gates and on
        an installed pack's, and a runner that writes into the project is caught
        on both (the toy tests above hold a pack directory, where planting a
        write into this checkout's `packs/` would dirty it)."""
        self.assertEqual(judge_on_real_evaluators(self, lambda _e: ReferenceRunner()), {})

        class WritesIntoItsProject(ReferenceRunner):
            def run(self, spec, fn, good_ctx, plan=None):
                _write(os.path.join(self.where["project"], f"mutated-{spec.id}.json"), "{}\n")
                return super().run(spec, fn, good_ctx, plan)

        found = judge_on_real_evaluators(
            self, lambda e: WritesIntoItsProject(project=e.project))
        self.assertTrue(any(k.startswith("bracket:") for k in found), found)
        self.assertTrue(any(k.startswith(f"{SUBJECT_PACK}:") for k in found), found)
        for key, problems in found.items():
            self.assertTrue(any(p.startswith("M1 hook") for p in problems), (key, problems))
            self.assertTrue(any(p.startswith("M1 tree") for p in problems), (key, problems))

if __name__ == "__main__":
    unittest.main(verbosity=2)
