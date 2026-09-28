# SPDX-License-Identifier: Apache-2.0
"""A stale verdict is never served as current (invariant 7, the class that carries it).

Per-gate content addressing is only as honest as its read set. Every channel a
gate can consume an input through, and the tracer misses, keeps a PASS current
after its input moved — the staleness lie, the worst failure Phase 1 can
introduce. So each scenario below is one reproduced defect or one channel,
driven the way the sweep drives it, and each asserts, through
``claims.statuses(view, stale_gates=...)`` on ``verdicts.resolve``'s view, that
the claim is NOT PASS once the input moved — after first asserting, as its own
positive control, that the claim WAS PASS before.

* **S-20** — a first sweep run with ``--only`` never set ``last_run``, so its
  verdicts could never go stale: C3 stayed PROVEN at 0.195 MPa after ``load_n``
  became 20000, where a re-run gives ~260 against 15.
* **S-22** — files a gate opens were in no staleness key; a limit file edited
  under a gate left C4 passing. Edited here in place, same size, with its mtime
  put back and backdated past any racy window: only the stat key's ctime and
  inode can see it. **The racy tick** — the same-size edit landing in the tick
  the digest cache was written — only the racy-clean rule can see.
* a subprocess reading a file its argv does not name — opaque, so never Fresh.
* **S-23** — a claim record a gate reads (openmodelica reads limits that way).
* **S-25** — every bulk reader, top level and nested: ``dict()``, ``{**p}``,
  ``json.dumps``, ``items()``, ``repr``, ``deepcopy``, ``f(**p)``.
* a key the gate asked for and did not find, which then appears.
* **S-27** — one file shared through the sweep's memo: the second gate's read is
  a memo hit that opens nothing, and it must still be that gate's input.
* a helper module edited; **S-26** — the gate's own file edited inside the same
  second with its mtime restored (both in a fresh process: an in-process module
  cache must never be what makes an edit visible).
* a hand-edited entry.
* **PD-29** — a crash under ``--force`` at unchanged inputs, then a plain sweep:
  it reads errored, never the cached PASS the crash superseded.

The same class then drives four of those defects through the CLI a person runs,
on a copy of the bracket, and reads the verdict where a person reads it: the
claim in ``status`` and the PROVEN section of ``report``. A library that resolves
honestly is not yet a command that prints honestly.

* **S-20** — ``check --only bracket.bearing`` first, then ``load_n`` 15 -> 20000.
* **S-21** — a model that does not import: nothing PASS, nothing PROVEN.
* **S-32** — ``check --no-record`` after an edit runs the moved gate and records
  nothing, so the claim it covers is still not current afterwards.
* **S-33** — an unrelated ingest stales nothing; an ingested file a gate DID open,
  edited, stales exactly that gate's claim.

``LastCheck`` holds ``last_check.json`` to its contract: written only after a
full recorded sweep, never read by the sweep, and fingerprinting what is watched.

What the rest of the file proves end to end, through ``_env.atompipe``
subprocesses on temp copies (spec §4, W13 · U24):

* ``E4Localisation`` — E4 as a unit test. For each of the bracket's twelve Config
  fields, a default edit stales exactly the gates whose recorded reads moved:
  ``status`` names exactly those, ``check`` executes exactly those and no
  control, and ``check --force`` agrees with the affected-only answer (the
  soundness oracle). The expected table is derived TWICE — from the first
  check's traced reads crossed with ``build()``, and typed from phase-1.md — and
  the two must agree. Over-invalidation is only a cost, but it is the cost E4
  measures; under-recording is the staleness lie.
* ``GateVersionRows`` — the same exactness for code: one module of fdm-print
  edited stales exactly the gates whose recorded closure holds it.
* ``CostIsKept`` — a cache hit never replays a duration as a new measurement.
* ``InstrumentMismatchIsNoted`` — an entry recorded under another library version
  stays Fresh (instruments are provenance, never part of rho) and says so.
* ``LastCheckWatches`` — ``last_check.json`` is the checkout's memory, ignored and
  never read by ``check``; its fingerprint moves with what it watches.

Run:  PYTHONPATH=src python3 -m unittest tests.test_staleness -v
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from typing import Any
from unittest import mock

from atompipe import claims, gates, modelio, report, store, util, verdicts
from atompipe.models import Acceptance, Claim, ClaimStatus, Ledger
from atompipe.util import FileDigests

import _env
import _projects
import _transcript
from test_admission import NOW, Driven, edit, row, write

PASS = ClaimStatus.PASS

# --------------------------------------------------------------------------- #
# the project: one gate per channel
# --------------------------------------------------------------------------- #
#: Bulk readers of a params level ``P``, each ending in a plain mapping the gate
#: then indexes. S-25: ``cli._ParamReads`` recorded nothing for any of these.
READERS = {
    "dict": "dict({p})",
    "splat": "{{**{p}}}",
    "json": "json.loads(json.dumps({p}))",
    "items": "dict({p}.items())",
    "repr": "ast.literal_eval(repr({p}))",
    "deepcopy": "copy.deepcopy({p})",
    "kwargs": "(lambda **kw: kw)(**{p})",
}
LEVELS = {"top": "ctx.params", "nested": "ctx.params['config']"}
BULK = [f"t.bulk_{r}_{l}" for r in READERS for l in LEVELS]

_BULK_GATE = '''

@gate(id="t.bulk_{r}_{l}", title="t", claims=["bulk_{r}_{l}"], negative_control=_nc("bad_x"))
def bulk_{r}_{l}(ctx):
    got = {expr}
    x = float(got["x"])
    return Verdict(gate="t.bulk_{r}_{l}", passed=x < 10.0, measured=x, limit=10.0)
'''

GATES = '''\
import ast
import copy
import json
import os
import subprocess
import sys

from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict

#: t.crashy crashes on the REAL design (x below 10) while "crash" is set, and not
#: on its control's known-bad input (x = 50): a crash of the gate, not of its
#: control, at inputs that did not move.
FLAGS = {"crash": False}


def _nc(name):
    return NegativeControl(fixture=f"selftest/bad.py:{name}")


def _number(ctx, rel):
    with open(os.path.join(ctx.root, *rel.split("/")), encoding="utf-8") as fh:
        return float(fh.read())


@gate(id="t.stress", title="t", claims=["stress"], negative_control=_nc("bad_stress"))
def stress(ctx):
    s = float(ctx.params["stress"])
    return Verdict(gate="t.stress", passed=s <= 15.0, measured=s, limit=15.0, units="MPa")


@gate(id="t.file", title="t", claims=["file"], negative_control=_nc("low_root"))
def from_file(ctx):
    value = _number(ctx, "data/limit.txt")
    return Verdict(gate="t.file", passed=value >= 1.0, measured=value, limit=1.0)


@gate(id="t.sub", title="t", claims=["sub"], negative_control=_nc("low_root"))
def through_a_process(ctx):
    # The child opens data/hidden.txt itself, a path its argv does not name:
    # a read no audit event of THIS process reports.
    out = subprocess.run([sys.executable, "-c", "print(open('data/hidden.txt').read())"],
                         cwd=ctx.root, capture_output=True, text=True, check=True)
    value = float(out.stdout)
    return Verdict(gate="t.sub", passed=value >= 1.0, measured=value, limit=1.0)


@gate(id="t.claim", title="t", claims=["claimread"], negative_control=_nc("bad_claim"))
def claim_read(ctx):
    c = ctx.ledger.claim("C_CL")
    limit = c.acceptance.limit if c is not None and c.acceptance.limit is not None else 0.0
    x = float(ctx.params["config"]["x"])
    return Verdict(gate="t.claim", passed=x <= limit, measured=x, limit=limit)


@gate(id="t.opt", title="t", claims=["opt"], negative_control=_nc("bad_opt"))
def optional(ctx):
    margin = float(ctx.params.get("margin", 0.0))
    return Verdict(gate="t.opt", passed=margin < 10.0, measured=margin, limit=10.0)


def _shared(gate_id, ctx):
    value = float(ctx.load_file("data/shared.txt"))
    return Verdict(gate=gate_id, passed=value >= 1.0, measured=value, limit=1.0)


@gate(id="t.memo_a", title="t", claims=["memo_a"], negative_control=_nc("low_root"))
def memo_a(ctx):
    return _shared("t.memo_a", ctx)


@gate(id="t.memo_b", title="t", claims=["memo_b"], negative_control=_nc("low_root"))
def memo_b(ctx):
    return _shared("t.memo_b", ctx)


@gate(id="t.crashy", title="t", claims=["crashy"], negative_control=_nc("bad_x"))
def crashy(ctx):
    x = float(ctx.params["config"]["x"])
    if FLAGS["crash"] and x < 10.0:
        raise RuntimeError("tripped over the real design")
    return Verdict(gate="t.crashy", passed=x < 10.0, measured=x, limit=10.0)
''' + "".join(_BULK_GATE.format(r=r, l=l, expr=READERS[r].format(p=LEVELS[l]))
              for r in READERS for l in LEVELS)

#: S-26's gate, in a module of its own: an edit to it moves no other gate's code.
SAME = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict

LIMIT = 9.0


@gate(id="t.same", title="t", claims=["same"],
      negative_control=NegativeControl(fixture="selftest/bad.py:bad_x"))
def same(ctx):
    x = float(ctx.params["config"]["x"])
    return Verdict(gate="t.same", passed=x <= LIMIT, measured=x, limit=LIMIT)
'''

#: A gate whose limit lives in a helper loaded by path: the helper's bytes are
#: part of the gate's code, recorded while the module loaded.
HELPED = '''\
import os

from atompipe.gates import gate
from atompipe.modelio import load_path
from atompipe.models import NegativeControl, Verdict

limits = load_path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "model", "limits.py"))


@gate(id="t.helped", title="t", claims=["helped"],
      negative_control=NegativeControl(fixture="selftest/bad.py:bad_x"))
def helped(ctx):
    x = float(ctx.params["config"]["x"])
    return Verdict(gate="t.helped", passed=x <= limits.LIMIT, measured=x, limit=limits.LIMIT)
'''

LIMITS = "LIMIT = 9.0\n"

#: Sealed fixtures: each builds its own context and reads nothing of the host.
FIXTURES = '''\
import dataclasses
import os

from atompipe.models import Acceptance, Claim, Ledger

LOW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "low")


def _ctx(ctx, params, **more):
    return dataclasses.replace(ctx, params=params, **more)


def bad_stress(ctx):
    return _ctx(ctx, {"stress": 260.0, "load_n": 20000.0,
                      "config": {"x": 1.0, "load_n": 20000.0}})


def low_root(ctx):
    # the same gate, pointed at a project whose data files are all too low
    return dataclasses.replace(ctx, root=LOW)


def bad_claim(ctx):
    ledger = Ledger(claims=[Claim(id="C_CL", statement="read by its own gate",
                                  acceptance=Acceptance(limit=10.0), tags=["claimread"])])
    return _ctx(ctx, {"x": 50.0, "config": {"x": 50.0}}, ledger=ledger)


def bad_opt(ctx):
    return _ctx(ctx, {"margin": 50.0})


def bad_x(ctx):
    return _ctx(ctx, {"x": 50.0, "config": {"x": 50.0}})
'''

TAGS = {"C_ST": "stress", "C_FI": "file", "C_SU": "sub", "C_CL": "claimread",
        "C_OP": "opt", "C_MA": "memo_a", "C_MB": "memo_b", "C_CR": "crashy",
        "C_SA": "same", "C_HE": "helped"}
TAGS.update({f"C_{gid[len('t.'):]}": gid[len("t."):] for gid in BULK})
CLAIM_OF = {f"t.{tag}": cid for cid, tag in TAGS.items()}
CLAIM_OF["t.claim"] = "C_CL"


def projection(derived: dict | None = None, **over) -> dict:
    """``config`` the inputs, ``derived`` what a build() would return — the
    bracket's shape, which echoes ``config`` back, so ``modelio.flat_params``
    hands a gate both ``x`` and ``config.x``: stress = 0.013 x load, 0.195 MPa
    at the 15 N default (S-20's number)."""
    cfg = {"x": 1.0, "load_n": 15.0}
    cfg.update(over)
    built = {"stress": round(cfg["load_n"] * 0.013, 6), "config": dict(cfg)}
    built.update(derived or {})
    return {"config": cfg, "derived": built}


def ledger(limit: float = 10.0) -> Ledger:
    """One claim per gate; C_CL carries the limit its gate reads from it."""
    return Ledger(claims=[Claim(id=cid, statement=f"claim {cid}", tags=[tag],
                                acceptance=Acceptance(limit=limit if cid == "C_CL" else None))
                          for cid, tag in TAGS.items()])


def plant(root: str) -> str:
    write(root, "gates/g.py", GATES)
    write(root, "gates/same.py", SAME)
    write(root, "gates/helped.py", HELPED)
    write(root, "model/limits.py", LIMITS)
    write(root, "selftest/bad.py", FIXTURES)
    for name in ("limit", "shared", "hidden"):
        write(root, f"data/{name}.txt", "2.0\n")
        write(root, f"selftest/low/data/{name}.txt", "0.5\n")
    return root


class Project:
    """The project above, loaded into a fresh registry in this process."""

    def __init__(self, case: _env.EnvCase) -> None:
        self.root = plant(os.path.join(case.tmp(), "project"))
        self.registry = gates.Registry()
        gates.load_project_gates(self.root, self.registry)
        self.ledger = ledger()
        self.digests_path = os.path.join(self.root, ".atompipe", "cache", "digests.json")

    @property
    def gate_module(self):
        _spec, fn = self.registry.get("t.crashy")
        return sys.modules[fn.__module__]

    def path(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/"))

    def ctx(self, proj: dict, led: Ledger | None = None) -> gates.GateContext:
        flat, _conflicts = modelio.flat_params(proj)
        return gates.GateContext(root=self.root, ledger=led or self.ledger, model=None,
                                 params=flat, out_dir=store.out_dir(self.root), tier=0,
                                 extra={})

    def sweep(self, proj: dict, **kw) -> verdicts.SweepResult:
        kw.setdefault("max_tier", 0)
        kw.setdefault("now", NOW)
        return verdicts.sweep(self.root, self.registry, self.ctx(proj), projection=proj,
                              ledger=self.ledger, **kw)

    def resolve(self, proj: dict, *, led: Ledger | None = None,
                digests: FileDigests | None = None) -> verdicts.Resolution:
        return verdicts.resolve(self.root, self.registry, proj, led or self.ledger,
                                now=NOW, digests=digests)

    def statuses(self, proj: dict, resolution: verdicts.Resolution | None = None, *,
                 led: Ledger | None = None) -> dict:
        led = led or self.ledger
        resolution = resolution or self.resolve(proj, led=led)
        view = dataclasses.replace(led, verdicts=resolution.verdicts)
        return claims.statuses(view, registry=self.registry,
                               stale_gates=resolution.stale_gates)

    def entry(self, gate_id: str) -> verdicts.Entry:
        found = verdicts.read_entries(self.root, gate_id)
        assert len(found) == 1, (gate_id, [e.name for e in found])
        return found[0]


# --------------------------------------------------------------------------- #
# the CLI side: real projects, copied to temp, driven through `_env.atompipe`
# --------------------------------------------------------------------------- #
#: The bracket's six gates, in registration order.
BRACKET_GATES = ("bracket.deflection", "bracket.bending_stress", "bracket.bearing",
                 "bracket.model_validity", "bracket.bed_fit", "bracket.min_wall")
ALL = frozenset(BRACKET_GATES)

#: The model file every E4 edit rewrites, as an entry spells a project path.
MODEL_REL = "model/bracket.py"


def _cli(project: str, *argv: str):
    """``atompipe <argv>`` in ``project``: a fresh process, so no module cache in
    this one decides what an edit looks like (spec §0.4)."""
    return _env.atompipe(list(argv), cwd=project)


def _doc(proc, *codes: int) -> dict:
    """``proc``'s stdout as the one JSON document ``--json`` promises, after
    checking its exit code is one of ``codes`` (default 0 or 1: 2 is a crash)."""
    if proc.returncode not in (codes or (0, 1)):
        raise AssertionError(f"exit {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:                      # pragma: no cover - reported, not raised
        raise AssertionError(f"not one JSON document ({exc}):\n{proc.stdout}\n{proc.stderr}")


def _text(proc, *codes: int) -> str:
    if proc.returncode not in (codes or (0, 1)):
        raise AssertionError(f"exit {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def _replace_once(project: str, rel: str, old: str, new: str) -> None:
    """Replace exactly one ``old`` with ``new`` in ``rel``: an edit that matched
    twice, or nowhere, is not the edit the test says it made."""
    path = os.path.join(project, *rel.split("/"))
    with open(path, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    if text.count(old) != 1:
        raise AssertionError(f"{rel}: expected exactly one {old!r}, found {text.count(old)}")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace(old, new))


def _literal(value: Any) -> str:
    """``value`` as the model spells a default: ``"pla"``, ``3``, ``66.0``."""
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, int):                     # bool included: `True`, never `1.0`
        return repr(value)
    return repr(float(value))


def _set_default(project: str, field: str, value: Any) -> None:
    """Edit ``Config.<field>``'s default in the copy's model, and nothing else.

    Refuses unless exactly one ``    <field>: <type> = <default>`` line matches —
    the same rule as ``_projects.set_thickness``, for every field."""
    path = os.path.join(project, *MODEL_REL.split("/"))
    with open(path, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    line = re.compile(rf"^(    {re.escape(field)}: [A-Za-z_][\w\[\]., |]* = )(.+)$",
                      re.MULTILINE)
    found = line.findall(text)
    if len(found) != 1:
        raise AssertionError(f"{MODEL_REL}: expected one `{field}: <type> = <default>` "
                             f"line, found {len(found)}")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(line.sub(lambda m: m.group(1) + _literal(value), text))


def _put(project: str, rel: str, text: str) -> str:
    path = os.path.join(project, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def _tree(path: str) -> dict[str, bytes]:
    """``{relative path: bytes}`` of every file under ``path`` (``{}`` when absent)."""
    out: dict[str, bytes] = {}
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, path).replace(os.sep, "/")] = fh.read()
    return out


def _cache(project: str) -> dict[str, bytes]:
    """The verdict cache, byte for byte: every entry and control entry."""
    return _tree(os.path.join(project, ".atompipe", "verdicts"))


def _split(cache: dict[str, bytes]) -> tuple[set[str], set[str]]:
    """``(verdict entries, control entries)`` of a ``_cache`` snapshot, as
    ``<gate>/<name>`` — each matched against the transcript's shapes, so a file
    that is neither kind shows up as a failure rather than as a third set."""
    entries, controls = set(), set()
    for rel in cache:
        spelled = f".atompipe/verdicts/{rel}"
        if _transcript.CONTROL_ENTRY_PATH.fullmatch(spelled):
            controls.add(rel)
        elif _transcript.ENTRY_PATH.fullmatch(spelled):
            entries.add(rel)
        else:
            raise AssertionError(f"{spelled} is neither a verdict entry nor a control entry")
    return entries, controls


def _entry_docs(project: str) -> dict[str, dict]:
    """``{gate: entry JSON}`` — exactly one verdict entry per gate, as a first
    check leaves them."""
    out: dict[str, dict] = {}
    entries, _controls = _split(_cache(project))
    for rel in sorted(entries):
        gate_id = rel.split("/")[0]
        if gate_id in out:
            raise AssertionError(f"{gate_id}: more than one entry after one check")
        with open(os.path.join(project, ".atompipe", "verdicts", *rel.split("/")),
                  encoding="utf-8") as fh:
            out[gate_id] = json.load(fh)
    return out


def _rows(data: dict) -> dict[str, dict]:
    return {r["gate"]: r for r in data["verdicts"]}


def _executed(data: dict) -> set[str]:
    """The gates a ``check --json`` actually ran: not served from the cache, not
    skipped, not refused before running."""
    return {r["gate"] for r in data["verdicts"]
            if not r["cached"] and not r["skipped"] and not r.get("error")}


def _outcomes(data: dict) -> dict[str, tuple]:
    """What a sweep concluded per gate — outcome, measurement, limit and the rho it
    was concluded at — whether the row was executed or served."""
    return {r["gate"]: (r["outcome"], r.get("measured"), r.get("limit"), r.get("rho"))
            for r in data["verdicts"]}


def _fresh(status: dict) -> set[str]:
    return {g for g, r in status["freshness"].items() if r["state"] == "fresh"}


def _proven(md: str) -> set[str]:
    """The claim ids in ``report``'s PROVEN section.

    Found by ``report.SECTION_PROVEN`` — the constant the report itself emits —
    and it FAILS rather than returning nothing when the heading is missing, twice,
    or followed by nothing: an absent section is not an empty one, and "C3 is not
    under PROVEN" asserted against no section proves nothing (S-15's lesson,
    `test_invariants.ReportNeverOverclaims._proven_section`). Every test that
    asserts a claim is absent here first asserts, as its positive control, that a
    claim that should be there IS: a row format that stopped matching would turn
    that control red, never this helper vacuous.
    """
    heading = report.SECTION_PROVEN
    lines = md.splitlines()
    at = [i for i, line in enumerate(lines) if line == heading or line.startswith(heading + " ")]
    if len(at) != 1:
        raise AssertionError(f"the report has {len(at)} {heading!r} headings, not one:\n{md}")
    body: list[str] = []
    for line in lines[at[0] + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    section = "\n".join(body)
    if not section.strip():
        raise AssertionError(f"the {heading!r} section is empty:\n{md}")
    return set(re.findall(r"\*\*(C\d+)\*\*", section))


def _seen(project: str) -> tuple[dict, set[str]]:
    """``(status --json claims, report's PROVEN ids)``: a claim where a person
    reads it, twice."""
    status = _doc(_cli(project, "status", "--json"), 0)
    md = _text(_cli(project, "report"), 0)
    return status["claims"], _proven(md)


#: Planted into bracket copies. The fixtures are sealed (invariant 5): each
#: starts from the host it is handed — the bracket's known-good design, since the
#: copy has `selftest/known_good.py` (D-27) — and changes one value.
_PLANTED_FIXTURES = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_staleness.py: known-bad inputs for the planted gates."""
import dataclasses


def _with(ctx, **over):
    params = dict(ctx.params)
    params.update(over)
    return dataclasses.replace(ctx, params=params)


def far_past(ctx):
    """The known-good design with its deflection far past any planted limit."""
    return _with(ctx, deflection=1000.0)


def bearing_far_past(ctx):
    """The known-good design with its bearing stress far past any allowable."""
    return _with(ctx, bearing_stress=1.0e6)
'''

#: A gate whose limit lives in a file it opens (``REL``, under the project).
_OPENS_GATE = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_staleness.py: a gate whose limit is a file it opens."""
import os

from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict

REL = {rel!r}


@gate(id={gate_id!r}, title="a derived value against a limit read from a file",
      claims=[{tag!r}], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/planted.py:{fixture}",
                                       note="the value pushed far past any limit"))
def opens(ctx):
    """Passes while the value stays within the last number the file holds."""
    with open(os.path.join(ctx.root, *REL.split("/")), encoding="utf-8") as fh:
        limit = float(fh.read().split()[-1])
    value = float(ctx.params[{param!r}])
    return Verdict(gate={gate_id!r}, passed=value <= limit, measured=value, limit=limit)
'''


def _plant_opener(project: str, *, gate_id: str, rel: str, tag: str, param: str,
                  fixture: str) -> None:
    _put(project, "selftest/planted.py", _PLANTED_FIXTURES)
    _put(project, f"gates/{gate_id.split('.')[-1]}.py",
         _OPENS_GATE.format(gate_id=gate_id, rel=rel, tag=tag, param=param, fixture=fixture))


class _CheckedProject:
    """One project that has run its first ``check``, copied per test: a copy
    carries its cache, so every row starts from "everything current" without
    paying for a first sweep of its own, and no row sees another's edit."""

    def __init__(self, prefix: str, make, *check_args: str) -> None:
        self.base = tempfile.mkdtemp(prefix=prefix)
        try:
            self.project = make(os.path.join(self.base, "project"))
            self.first = _doc(_cli(self.project, "check", "--json", *check_args))
        except BaseException:
            _env._rmtree(self.base)
            raise

    def copy(self, case: _env.EnvCase) -> str:
        dest = os.path.join(case.tmp(), "project")
        shutil.copytree(self.project, dest)
        return dest

    def close(self) -> None:
        _env._rmtree(self.base)


# --------------------------------------------------------------------------- #
class StaleIsNotCurrent(_env.EnvCase):
    """Once an input a verdict consumed has moved, that verdict is not PASS."""

    def test_s20_the_first_filtered_sweep_then_an_input_change(self):
        p = Project(self)
        base = projection()
        first = p.sweep(base, only=["t.stress"])
        self.assertEqual(row(first, "t.stress").verdict.outcome, "pass")
        self.assertEqual(p.statuses(base)["C_ST"], PASS, "the positive control")

        moved = projection(load_n=20000.0)
        resolution = p.resolve(moved)
        self.assertIn("t.stress", resolution.stale_gates)
        # it read the derived stress, not load_n: the move it names is the one it saw
        self.assertIn("stress 0.195 -> 260.0", resolution.rows["t.stress"].stale_reason)
        self.assertNotEqual(p.statuses(moved, resolution)["C_ST"], PASS)

        again = p.sweep(moved)
        got = row(again, "t.stress")
        self.assertTrue(got.executed, "the moved gate re-runs, filtered first sweep or not")
        self.assertEqual(got.verdict.outcome, "fail")
        self.assertEqual(p.statuses(moved)["C_ST"], ClaimStatus.FAIL)

    def test_s22_a_data_file_edited_in_place_with_its_mtime_restored_and_backdated(self):
        p = Project(self)
        base = projection()
        p.sweep(base, only=["t.file"])
        self.assertTrue(os.path.isfile(p.digests_path), "the sweep kept its stat cache")
        self.assertEqual(p.statuses(base)["C_FI"], PASS, "the positive control")

        path = p.path("data/limit.txt")
        before = os.stat(path)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("0.5\n")                              # same size as "2.0\n"
        day = 86_400 * 10 ** 9
        os.utime(path, ns=(before.st_atime_ns - day, before.st_mtime_ns - day))
        after = os.stat(path)
        self.assertEqual(after.st_size, before.st_size)
        self.assertLess(after.st_mtime_ns, before.st_mtime_ns)

        resolution = p.resolve(base, digests=FileDigests(p.digests_path))
        self.assertIn("t.file", resolution.stale_gates)
        self.assertIn("data/limit.txt changed", resolution.rows["t.file"].stale_reason)
        self.assertNotEqual(p.statuses(base, resolution)["C_FI"], PASS)
        got = row(p.sweep(base, only=["t.file"]), "t.file")
        self.assertTrue(got.executed)
        self.assertEqual(got.verdict.outcome, "fail")

    def test_a_racy_tick_edit_is_rehashed(self):
        # The digest cache is saved in the same timestamp tick as a same-size
        # edit: the stat key it holds IS the file's key now, beside the digest of
        # the bytes before the edit. Only the racy-clean rule (the file was
        # touched no earlier than the cache was written) sends it to be re-read.
        p = Project(self)
        base = projection()
        p.sweep(base, only=["t.file"])
        path = p.path("data/limit.txt")
        with open(path, "rb") as fh:
            old_sha = hashlib.sha256(fh.read()).hexdigest()
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("0.5\n")
        st = os.stat(path)
        with open(p.digests_path, encoding="utf-8") as fh:
            cache = json.load(fh)
        cache["files"][os.path.abspath(path)] = [st.st_size, st.st_mtime_ns, st.st_ctime_ns,
                                                 st.st_ino, st.st_dev, old_sha]
        with open(p.digests_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        tick = max(st.st_mtime_ns, st.st_ctime_ns)
        os.utime(p.digests_path, ns=(tick, tick))

        resolution = p.resolve(base, digests=FileDigests(p.digests_path))
        self.assertIn("t.file", resolution.stale_gates)
        self.assertNotEqual(p.statuses(base, resolution)["C_FI"], PASS)

        # The test's own negative control: with the racy rule off, that cache
        # entry is trusted and the moved file reads as the PASS it no longer is.
        with mock.patch.object(util, "_racy", return_value=False):
            fooled = p.resolve(base, digests=FileDigests(p.digests_path))
        self.assertNotIn("t.file", fooled.stale_gates)
        self.assertEqual(p.statuses(base, fooled)["C_FI"], PASS)

    def test_a_subprocess_reading_an_unlisted_file_is_never_fresh(self):
        p = Project(self)
        base = projection()
        first = p.sweep(base, only=["t.sub"])
        got = row(first, "t.sub")
        self.assertEqual((got.verdict.outcome, got.executed), ("pass", True))
        opaque = p.entry("t.sub").reads["opaque"]
        self.assertTrue(any(name.startswith("subprocess:") for name in opaque), opaque)
        # Never Fresh — not even before anything moved: what the child read is
        # an input nothing can re-check without running it (§8).
        self.assertIn("t.sub", p.resolve(base).stale_gates)
        self.assertNotEqual(p.statuses(base)["C_SU"], PASS)

        write(p.root, "data/hidden.txt", "0.5\n")
        self.assertNotEqual(p.statuses(base)["C_SU"], PASS)
        got = row(p.sweep(base, only=["t.sub"]), "t.sub")
        self.assertTrue(got.executed, "an opaque entry is never served from the cache")
        self.assertFalse(got.cached)
        self.assertEqual(got.verdict.outcome, "fail")

    def test_s23_a_claim_record_edit(self):
        p = Project(self)
        base = projection()
        p.sweep(base, only=["t.claim"])
        self.assertEqual(p.statuses(base)["C_CL"], PASS, "the positive control")
        edited = ledger(limit=0.5)
        resolution = p.resolve(base, led=edited)
        self.assertIn("t.claim", resolution.stale_gates)
        self.assertIn("claim C_CL changed", resolution.rows["t.claim"].stale_reason)
        self.assertNotEqual(p.statuses(base, resolution, led=edited)["C_CL"], PASS)

    def test_s25_each_bulk_reader_top_level_and_nested(self):
        p = Project(self)
        base = projection()
        p.sweep(base, only=BULK)
        before = p.statuses(base)
        moved = projection(x=2.0)
        resolution = p.resolve(moved)
        after = p.statuses(moved, resolution)
        for gate_id in BULK:
            cid = CLAIM_OF[gate_id]
            with self.subTest(reader=gate_id):
                self.assertEqual(before[cid], PASS, "the positive control")
                self.assertIn(gate_id, resolution.stale_gates)
                self.assertNotEqual(after[cid], PASS)

    def test_a_missing_key_that_appears(self):
        p = Project(self)
        base = projection()
        p.sweep(base, only=["t.opt"])
        self.assertEqual(p.statuses(base)["C_OP"], PASS, "the positive control")
        grown = projection(derived={"margin": 50.0})
        resolution = p.resolve(grown)
        self.assertIn("t.opt", resolution.stale_gates)
        self.assertIn("margin absent -> 50.0", resolution.rows["t.opt"].stale_reason)
        self.assertNotEqual(p.statuses(grown, resolution)["C_OP"], PASS)
        self.assertEqual(row(p.sweep(grown, only=["t.opt"]), "t.opt").verdict.outcome, "fail")

    def test_s27_a_memo_shared_file_edit_stales_both_gates(self):
        p = Project(self)
        base = projection()
        loads = []
        real = gates._load

        def counting(abspath, loader):
            loads.append(abspath)
            return real(abspath, loader)

        with mock.patch.object(gates, "_load", side_effect=counting):
            p.sweep(base, only=["t.memo_a", "t.memo_b"])
        shared = os.path.abspath(p.path("data/shared.txt"))
        self.assertEqual(loads.count(shared), 1, "the second gate was served by the memo")
        for gate_id in ("t.memo_a", "t.memo_b"):
            self.assertIn("data/shared.txt", p.entry(gate_id).reads["files"],
                          f"{gate_id}: a memo hit is still this gate's read")
        self.assertEqual(p.statuses(base)["C_MB"], PASS, "the positive control")

        write(p.root, "data/shared.txt", "0.5\n")
        resolution = p.resolve(base)
        after = p.statuses(base, resolution)
        for gate_id in ("t.memo_a", "t.memo_b"):
            self.assertIn(gate_id, resolution.stale_gates)
            self.assertNotEqual(after[CLAIM_OF[gate_id]], PASS)

    def test_a_hand_edited_entry(self):
        p = Project(self)
        base = projection()
        write(p.root, "data/limit.txt", "0.5\n")
        p.sweep(base, only=["t.file"])
        entry = p.entry("t.file")
        self.assertIs(entry.verdict["passed"], False)
        with open(entry.path, encoding="utf-8") as fh:
            text = fh.read()
        with open(entry.path, "w", encoding="utf-8") as fh:
            fh.write(text.replace('"passed": false', '"passed": true', 1))

        resolution = p.resolve(base)
        self.assertNotEqual(p.statuses(base, resolution)["C_FI"], PASS)
        self.assertTrue(any("hand-edited" in note for note in resolution.notes),
                        resolution.notes)
        got = row(p.sweep(base, only=["t.file"]), "t.file")
        self.assertTrue(got.executed, "a hand-edited entry is a cache miss")
        self.assertEqual(got.verdict.outcome, "fail")
        self.assertNotEqual(p.statuses(base)["C_FI"], PASS)

    def test_a_crash_under_force_then_a_plain_sweep_reads_errored(self):
        # PD-29. A crash at unchanged inputs proves nothing, and neither does the
        # PASS it followed: the next plain sweep must not serve that PASS.
        p = Project(self)
        base = projection()
        p.sweep(base, only=["t.crashy"])
        self.assertEqual(p.statuses(base)["C_CR"], PASS, "the positive control")

        p.gate_module.FLAGS["crash"] = True
        self.addCleanup(p.gate_module.FLAGS.__setitem__, "crash", False)
        forced = row(p.sweep(base, only=["t.crashy"], force=True), "t.crashy")
        self.assertEqual(forced.verdict.outcome, "error", forced.verdict)
        self.assertEqual(forced.admission.state, "admitted",
                         "the control still fires: this is the gate crashing, not it")
        self.assertNotEqual(p.statuses(base)["C_CR"], PASS)

        plain = row(p.sweep(base, only=["t.crashy"]), "t.crashy")
        self.assertEqual(plain.verdict.outcome, "error", plain.verdict)
        self.assertTrue(plain.executed, "the superseded PASS is re-run, never served")
        self.assertFalse(plain.cached)
        self.assertNotEqual(p.statuses(base)["C_CR"], PASS)

        p.gate_module.FLAGS["crash"] = False
        healed = row(p.sweep(base, only=["t.crashy"]), "t.crashy")
        self.assertEqual(healed.verdict.outcome, "pass")
        self.assertEqual(p.statuses(base)["C_CR"], PASS, "a run that passes clears it")

    # -- code edits: a fresh process per step ------------------------------- #
    def _driven(self) -> Driven:
        root = plant(os.path.join(self.tmp(), "project"))
        return Driven(self, root, TAGS, projection=projection())

    def test_a_helper_module_edit(self):
        d = self._driven()
        first = d.run("--only", "t.helped")
        self.assertEqual(first["statuses"]["C_HE"], "pass", "the positive control")
        edit(d.root, "model/limits.py", "LIMIT = 9.0", "LIMIT = 0.5")
        judged = d.run(mode="resolve")
        self.assertIn("t.helped", judged["stale_gates"])
        self.assertNotEqual(judged["statuses"]["C_HE"], "pass")
        again = d.run("--only", "t.helped")
        self.assertTrue(again["rows"]["t.helped"]["executed"])
        self.assertEqual(again["rows"]["t.helped"]["outcome"], "fail")

    def test_s26_a_same_second_gate_edit(self):
        d = self._driven()
        first = d.run("--only", "t.same")
        self.assertEqual(first["statuses"]["C_SA"], "pass", "the positive control")
        path = os.path.join(d.root, "gates", "same.py")
        st = os.stat(path)
        edit(d.root, "gates/same.py", "LIMIT = 9.0", "LIMIT = 0.5")
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual((os.stat(path).st_size, os.stat(path).st_mtime_ns),
                         (st.st_size, st.st_mtime_ns), "same size, same second")
        judged = d.run(mode="resolve")
        self.assertIn("t.same", judged["stale_gates"])
        self.assertNotEqual(judged["statuses"]["C_SA"], "pass")
        again = d.run("--only", "t.same")
        self.assertTrue(again["rows"]["t.same"]["executed"])
        self.assertEqual(again["rows"]["t.same"]["outcome"], "fail", "the new bytes ran")

    # -- the CLI a person runs: `status` and `report` on a bracket copy ------ #
    def _bracket(self) -> str:
        return _projects.bracket_copy(os.path.join(self.tmp(), "bracket"))

    def test_s20_cli_a_filtered_first_check_then_an_input_change(self):
        # S-20 as it was read: C3 stayed PROVEN at 0.195 MPa after load_n became
        # 20000, because the only sweep ever run was filtered and never set the
        # one clock staleness was measured against.
        project = self._bracket()
        first = _doc(_cli(project, "check", "--only", "bracket.bearing", "--json"))
        self.assertEqual(list(_rows(first)), ["bracket.bearing"])
        self.assertEqual(_rows(first)["bracket.bearing"]["outcome"], "pass")
        seen, proven = _seen(project)
        self.assertEqual(seen["C3"], "pass", "the positive control")
        self.assertIn("C3", proven, "the positive control: C3 is PROVEN before the edit")

        _set_default(project, "load_n", 20000.0)
        status = _doc(_cli(project, "status", "--json"), 0)
        self.assertIn("bracket.bearing", status["stale_gates"])
        self.assertEqual(status["freshness"]["bracket.bearing"]["state"], "stale")
        seen, proven = _seen(project)
        self.assertNotEqual(seen["C3"], "pass", "S-20: a filtered first sweep never went stale")
        self.assertNotIn("C3", proven, "S-20: C3 stayed under PROVEN after load_n moved")

        again = _doc(_cli(project, "check", "--only", "bracket.bearing", "--json"))
        row_ = _rows(again)["bracket.bearing"]
        self.assertFalse(row_["cached"], "the moved gate re-runs, filtered or not")
        self.assertEqual((row_["outcome"], row_["measured"]), ("fail", 259.74))
        seen, proven = _seen(project)
        self.assertEqual(seen["C3"], "fail")
        self.assertNotIn("C3", proven)

    def test_s21_cli_a_model_that_does_not_load_proves_nothing(self):
        project = self._bracket()
        _doc(_cli(project, "check", "--json"))
        seen, proven = _seen(project)
        self.assertEqual({seen[c] for c in ("C2", "C3", "C4")}, {"pass"}, "the positive control")
        self.assertEqual(proven, {"C2", "C3", "C4"}, "the positive control")

        with open(os.path.join(project, *MODEL_REL.split("/")), "a", encoding="utf-8") as fh:
            fh.write('\n\nraise RuntimeError("the model is mid-edit")\n')
        status = _doc(_cli(project, "status", "--json"), 0)
        self.assertFalse(status["model"]["loaded"])
        self.assertEqual(set(status["stale_gates"]), ALL,
                         "S-21: a gate that reads the model read as current without one")
        seen, proven = _seen(project)
        self.assertNotIn("pass", set(seen.values()),
                         f"S-21: a claim read PASS against a model that does not load: {seen}")
        self.assertEqual(proven, set(), "S-21: the report listed PROVEN claims for a "
                                        "design that cannot be built")
        reported = _doc(_cli(project, "report", "--json"), 0)
        self.assertNotIn("pass", set(reported["claims"].values()), reported["claims"])
        self.assertTrue(reported["model_error"], "the report does not say the model is broken")

    def test_s32_cli_a_dry_check_leaves_a_moved_gate_stale(self):
        # A dry sweep runs what moved and records nothing. So what it saw is
        # printed once and then gone: afterwards the claim is exactly as current
        # as before it ran — not PASS — until a check that records.
        project = self._bracket()
        _doc(_cli(project, "check", "--json"))
        _set_default(project, "bed_xy", 250.0)
        cache = _cache(project)

        dry = _doc(_cli(project, "check", "--no-record", "--json"))
        dried = _rows(dry)["bracket.bed_fit"]
        self.assertEqual((dried["outcome"], dried["cached"], dried["fresh"]),
                         ("pass", False, True), dried)
        self.assertEqual(dry["summary"]["by_status"]["stale"], 0,
                         "S-32: the dry sweep read its own fresh pass as STALE")
        self.assertEqual(_cache(project), cache, "--no-record wrote the verdict cache")

        status = _doc(_cli(project, "status", "--json"), 0)
        self.assertEqual(status["stale_gates"], ["bracket.bed_fit"])
        seen, proven = _seen(project)
        self.assertEqual(seen["C4"], "stale", "a dry sweep made a moved gate current")
        self.assertNotIn("C4", proven, "a dry sweep's pass reached PROVEN")
        self.assertTrue({"C2", "C3"} <= proven, "the positive control: the report lists "
                                                "the claims that are current")

        _doc(_cli(project, "check", "--json"))
        seen, proven = _seen(project)
        self.assertEqual(seen["C4"], "pass", "the recorded check makes it current")
        self.assertIn("C4", proven)

    def test_s33_cli_an_unrelated_ingest_stales_nothing_and_a_read_input_its_gate(self):
        # S-33 as it was read: ingesting one unrelated artifact made every
        # measurable claim STALE through one hash over every input. An input is a
        # gate's input only if the gate read it — in both directions.
        project = self._bracket()
        outside = self.tmp()
        datasheet = _put(outside, "allowable.txt", "bearing allowable, MPa: 15.0\n")
        ingested = _doc(_cli(project, "ingest", datasheet, "--kind", "datasheet",
                             "--desc", "the fastener bearing allowable", "--json"), 0)
        rel = ingested["ingested"][0]["path"]
        _plant_opener(project, gate_id="bracket.datasheet", rel=rel, tag="bearing",
                      param="bearing_stress", fixture="bearing_far_past")
        first = _doc(_cli(project, "check", "--json"))
        self.assertEqual(_rows(first)["bracket.datasheet"]["outcome"], "pass")
        self.assertEqual(_doc(_cli(project, "claim", "show", "C3", "--json"), 0)["covered_by"],
                         ["bracket.bearing", "bracket.datasheet"])
        entry = _entry_docs(project)["bracket.datasheet"]
        self.assertIn(rel, entry["reads"]["files"], "the gate's opened file is not its input")

        notes = _put(outside, "shelf-notes.txt", "painted pine; nobody measured anything\n")
        _doc(_cli(project, "ingest", notes, "--desc", "unrelated notes", "--json"), 0)
        status = _doc(_cli(project, "status", "--json"), 0)
        self.assertEqual(status["stale_gates"], [],
                         "S-33: an input no gate read staled a gate")
        seen, proven = _seen(project)
        self.assertEqual({seen[c] for c in ("C2", "C3", "C4")}, {"pass"}, seen)
        self.assertEqual(proven, {"C2", "C3", "C4"}, "the positive control")

        _replace_once(project, rel, "15.0", "0.10")
        status = _doc(_cli(project, "status", "--json"), 0)
        self.assertEqual(status["stale_gates"], ["bracket.datasheet"],
                         "an ingested file a gate opened moved; only that gate is stale")
        self.assertIn(f"{rel} changed", status["freshness"]["bracket.datasheet"]["reasons"][0])
        seen, proven = _seen(project)
        self.assertNotEqual(seen["C3"], "pass", "a moved input left its claim PASS")
        self.assertNotIn("C3", proven, "a moved input left its claim under PROVEN")
        self.assertEqual({seen["C2"], seen["C4"]}, {"pass"}, "localised: nothing else moved")
        self.assertEqual(proven, {"C2", "C4"})

        again = _doc(_cli(project, "check", "--json"))
        self.assertEqual(_executed(again), {"bracket.datasheet"})
        self.assertEqual(_rows(again)["bracket.datasheet"]["outcome"], "fail")
        seen, _proven_now = _seen(project)
        self.assertEqual(seen["C3"], "fail")


# --------------------------------------------------------------------------- #
class LastCheck(_env.EnvCase):
    """``.atompipe/cache/last_check.json``: a full recorded sweep's summary, for
    the readers that must not import a project's code (P3's hook) — never an
    input to the sweep."""

    def _path(self, root: str) -> str:
        return os.path.join(root, ".atompipe", "cache", "last_check.json")

    def test_written_only_after_a_full_recorded_sweep(self):
        p = Project(self)
        base = projection()
        filtered = p.sweep(base, only=["t.stress"])
        self.assertIsNone(verdicts.write_last_check(p.root, filtered, p.resolve(base), now=NOW))
        dry = p.sweep(base, record=False)
        self.assertIsNone(verdicts.write_last_check(p.root, dry, p.resolve(base), now=NOW))
        self.assertFalse(os.path.exists(self._path(p.root)))

        full = p.sweep(base)
        resolution = p.resolve(base)
        path = verdicts.write_last_check(p.root, full, resolution, now=NOW)
        self.assertEqual(path, self._path(p.root))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(list(data), ["when", "spine", "fingerprint", "reads", "statuses",
                                      "counts", "worst", "params", "influence"])
        self.assertEqual(data["when"], NOW)
        self.assertEqual(data["spine"], verdicts.spine_digest())
        self.assertEqual(data["statuses"], {cid: status.value for cid, status in
                                            p.statuses(base, resolution).items()})
        self.assertIn("file:data/limit.txt", data["reads"]["t.file"])
        self.assertEqual(data["counts"]["executed"], full.counts["executed"])
        self.assertEqual((data["params"], data["influence"]), ({}, {}))

    def test_the_sweep_never_reads_it(self):
        p = Project(self)
        base = projection()
        os.makedirs(self._path(p.root))               # any open of it would raise
        first = p.sweep(base, only=["t.stress"])
        self.assertEqual(row(first, "t.stress").verdict.outcome, "pass")
        again = p.sweep(base, only=["t.stress"])
        self.assertTrue(row(again, "t.stress").cached)

    def test_the_fingerprint_moves_with_what_is_watched(self):
        p = Project(self)
        base = projection()
        p.sweep(base, only=["t.file"])

        def fingerprint() -> str:
            paths = verdicts.watched_paths(p.root, p.resolve(base))
            return verdicts.fingerprint(p.root, paths, digests=FileDigests())

        self.assertIn(os.path.abspath(p.path("data/limit.txt")),
                      verdicts.watched_paths(p.root, p.resolve(base)),
                      "a file a gate opened is watched")
        first = fingerprint()
        self.assertEqual(fingerprint(), first, "nothing moved, nothing moves")
        write(p.root, "notes.txt", "not an input\n")
        self.assertEqual(fingerprint(), first, "an unwatched file moves nothing")
        moved = first
        for rel, text in ((".atompipe/project.json", '{"schema": 2}\n'),
                          ("objectives.json", "{}\n"),
                          (".atompipe/packs/extra/pack.json", "{}\n"),
                          ("data/limit.txt", "3.0\n")):
            with self.subTest(watched=rel):
                write(p.root, rel, text)
                now = fingerprint()
                self.assertNotEqual(now, moved, f"{rel} did not move the fingerprint")
                moved = now


# --------------------------------------------------------------------------- #
# E4Localisation: one Config field at a time, through the CLI
# --------------------------------------------------------------------------- #
def _g(*short: str) -> frozenset[str]:
    return frozenset(f"bracket.{name}" for name in short)


#: E4 as docs/plan/phase-1.md types it (checkpoint 1.2, "Exact localisation"): per
#: Config field, the gates a default edit to it must stale, and no others. Typed
#: from the plan, which read `gates/structural.py`'s reads and `build()`'s
#: formulas by hand before any tracer existed. That is why it stands beside the
#: derived table rather than being replaced by it: an over-recording tracer
#: moves the derived table and never this one (judge J2 — a table derived only
#: from traced reads agrees with any tracer, including one that records a whole
#: projection per read and stales all six on every row). Rejected, the other
#: way: typing only — a table nobody recomputes asserts the wrong thing forever
#: the day `build()` grows a dependency; the derived side says so first.
PLAN_E4_TABLE: dict[str, frozenset[str]] = {
    "arm_length": _g("deflection", "bending_stress", "model_validity", "bed_fit"),
    "width": _g("deflection", "bending_stress", "bed_fit"),
    "thickness": ALL,
    "hole_d": _g("bearing", "bed_fit"),
    "n_bolts": _g("bearing"),
    "edge_margin": _g("bed_fit"),
    "load_n": _g("deflection", "bending_stress", "bearing"),
    "safety_factor": _g("bending_stress", "bearing"),
    "material": _g("deflection", "bending_stress", "bearing"),
    "nozzle_d": _g("min_wall"),
    "bed_xy": _g("bed_fit"),
    "brim_mm": _g("bed_fit"),
}

#: A float field's perturbation: x1.1, rounded so the model line reads `66.0`
#: rather than `66.00000000000001` (spec §7). A 10% move is an ordinary design
#: iteration, far above float rounding, and inside every range `build()`
#: accepts. Rejected: one value for every field — an int field or a material key
#: has no x1.1.
PERTURB_FACTOR = 1.1
PERTURB_PLACES = 6

#: The fields with no x1.1 (spec §7): `bed_xy` 220 -> 250 is the transcript's own
#: edit (phase-1.md), so the E4 row and the transcript are one fact; `n_bolts`
#: 2 -> 3 is an int; `material` petg -> pla is a key into `MATERIALS`. Rejected:
#: `n_bolts` 2 -> 1, which moves bearing just as well but is the one-bolt case the
#: model's own docstring calls unmodelled.
PERTURB_SPECIAL: dict[str, Any] = {"bed_xy": 250.0, "n_bolts": 3, "material": "pla"}

#: The early-cutoff row (phase-1.md): arm_length 60 -> 120 WITH thickness 7 -> 14.
#: L/t and L^3/t^3 do not move, so `build()` returns the same deflection and
#: slenderness to the bit, and the two gates that read only those (plus the
#: untouched `config.load_n`) stay fresh although both of the fields upstream of
#: what they read moved: staleness follows the values a gate read, never the
#: fields they were built from. Why doubling: every operand stays an exact
#: integer (120^3, 14^3 and the rest are far inside 2^53), so each quotient is
#: the correctly rounded value of the same rational as at 60 and 7 — equal by
#: arithmetic, not by luck — and the test still asserts that precondition before
#: relying on it. Rejected: x1.1 on both (66, 7.7). Measured 2026-09-28 on 3.12,
#: it happens to give the same bits too, but 7.7 has no exact binary form, so
#: that equality is a rounding accident a different `pow` could undo.
EARLY_CUTOFF: dict[str, Any] = {"arm_length": 120.0, "thickness": 14.0}
EARLY_CUTOFF_UNCHANGED = ("deflection", "slenderness")
EARLY_CUTOFF_FRESH = _g("deflection", "model_validity")


class _Absent:
    """A path the projection does not hold: the tracer's ``ABSENT``."""

    def __repr__(self) -> str:
        return "absent"


_NOT_THERE = _Absent()


def _at(flat: Any, path: list) -> Any:
    node = flat
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return _NOT_THERE
        node = node[key]
    return node


def _bits(value: Any) -> Any:
    """``value`` for an exact comparison: JSON text, where a float is its shortest
    round-trip repr (equal text is equal bits) and ``1`` is not ``1.0``."""
    return value if value is _NOT_THERE else json.dumps(value, sort_keys=True)


def _moved_reads(entry: dict, before: dict, after: dict) -> list[str]:
    """The recorded reads of ``entry`` whose inputs differ between two
    projections, as dotted paths (``[]`` when none did) — for a default edit to
    the model file, which is what every E4 row makes.

    Deliberately NOT the spine's digests: it flattens both projections the way
    `check` does (`modelio.flat_params`, the one copy) and compares the VALUES at
    each recorded path exactly. A digest that collapsed two values would then
    show up here as a disagreement, instead of agreeing with itself.
    """
    reads = entry["reads"]
    moved = [f"opaque {name}" for name in reads.get("opaque") or ()]
    if reads.get("model") is not None and _bits(before) != _bits(after):
        moved.append("the whole projection (ctx.model)")
    touched = set(reads.get("files") or {}) | set(reads.get("dirs") or {})
    if MODEL_REL in touched | set(entry["code"]["files"]):
        moved.append(f"{MODEL_REL} itself")
    flat_before, _conflicts = modelio.flat_params(before)
    flat_after, _conflicts = modelio.flat_params(after)
    for path, digest, *_small in reads.get("params") or ():
        was, now = _at(flat_before, path), _at(flat_after, path)
        if digest in (verdicts.PRESENT, verdicts.ABSENT):      # a presence read
            differs = (was is _NOT_THERE) != (now is _NOT_THERE)
        else:
            differs = _bits(was) != _bits(now)
        if differs:
            moved.append(".".join(str(key) for key in path))
    return moved


def _derived_table(entries: dict[str, dict], before: dict, after: dict) -> dict[str, list[str]]:
    """``{gate: the reads that moved}`` for every gate at least one read of which moved."""
    return {gate_id: moved for gate_id, entry in sorted(entries.items())
            if (moved := _moved_reads(entry, before, after))}


def _side_that_moved(label: str, derived: dict[str, list[str]], typed: frozenset[str]) -> str:
    """The message for a disagreement between the two E4 tables, naming the side
    that moved and which way. The typed table only moves by an edit to this file
    (after one to the plan); the derived one moves with the tracer, the gates
    and the model — so a disagreement is the derived side moving, and the
    direction says whether it is a cost or a lie."""
    got = frozenset(derived)
    parts = [f"E4 row {label}: the DERIVED table (the first check's traced reads x "
             f"build()) stales {sorted(got)}; the TYPED table (PLAN_E4_TABLE, from "
             f"docs/plan/phase-1.md) stales {sorted(typed)}."]
    extra, missing = sorted(got - typed), sorted(typed - got)
    if extra:
        parts.append(
            "The derived side moved UP (over-recording): " + "; ".join(
                f"{gate_id} via {', '.join(derived[gate_id])}" for gate_id in extra)
            + ". A tracer that records a read the gate never made moves the derived "
              "table and never the typed one. If the gate or build() really changed, "
              "the typed table is stale: change the plan first, then PLAN_E4_TABLE.")
    if missing:
        parts.append(
            f"The derived side moved DOWN (under-recording): {', '.join(missing)} recorded "
            f"no read that this edit moves. That is the staleness lie — an input the "
            f"gate depends on is not in its entry, so a PASS would outlive its edit. A "
            f"tracer that lost a read moves the derived table and never the typed one.")
    return " ".join(parts)


class E4Localisation(_env.EnvCase):
    """E4: a Config default edit stales exactly the gates whose reads it moved.

    One bracket copy runs its first check (six gates, six controls); every row
    works on a copy of that copy, edits one default in `model/bracket.py`, and
    reads the answer where a person would: `status --json`, `check --json`, then
    `check --force --json` as the soundness oracle (R-9: re-running everything
    must reach exactly what the affected-only sweep served).
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.base = _CheckedProject("atompipe-e4-", _projects.bracket_copy)
        cls.addClassCleanup(cls.base.close)
        cls.entries = _entry_docs(cls.base.project)
        cls.model = modelio.load_model(cls.base.project, MODEL_REL)
        cls.before = modelio.project(cls.model)
        cls.defaults = {f.name: getattr(cls.model.config, f.name)
                        for f in dataclasses.fields(cls.model.config)}

    @classmethod
    def _projection(cls, edits: dict[str, Any]) -> dict:
        """The projection `check` would build after ``edits`` — `build()` of the
        edited config, through the same `modelio.project`."""
        config = dataclasses.replace(cls.model.config, **edits)
        return modelio.project(dataclasses.replace(cls.model, config=config))

    @classmethod
    def _perturbation(cls, field: str) -> Any:
        if field in PERTURB_SPECIAL:
            return PERTURB_SPECIAL[field]
        default = cls.defaults[field]
        if isinstance(default, float):
            return round(default * PERTURB_FACTOR, PERTURB_PLACES)
        raise AssertionError(f"Config.{field} = {default!r} has no E4 perturbation: a "
                             f"non-float field needs a PERTURB_SPECIAL entry")

    def test_the_first_check_ran_everything_it_is_measured_against(self):
        first = self.base.first
        self.assertEqual(_executed(first), ALL)
        self.assertEqual(first["counts"]["controls"]["executed"], len(ALL))
        self.assertEqual(set(self.entries), ALL)
        for gate_id, entry in self.entries.items():
            with self.subTest(gate=gate_id):
                self.assertEqual(entry["reads"]["opaque"], [], "an opaque gate is never "
                                 "Fresh: it could not take part in E4 at all")
                self.assertTrue(entry["reads"]["params"], "a gate that read nothing")
                self.assertNotIn(MODEL_REL, set(entry["code"]["files"]) |
                                 set(entry["reads"]["files"]),
                                 "the model file is in a gate's code or reads: every "
                                 "Config edit would stale it whatever it read")

    def test_every_config_field_has_a_typed_row(self):
        self.assertEqual(sorted(self.defaults), sorted(PLAN_E4_TABLE),
                         "Config and PLAN_E4_TABLE disagree on the fields: a field the "
                         "model grew needs a typed row (and a perturbation), in the "
                         "plan first")
        for field in self.defaults:
            with self.subTest(field=field):
                self.assertNotEqual(_bits(self._perturbation(field)),
                                    _bits(self.defaults[field]), "a perturbation that moves "
                                                                 "nothing")

    def test_the_derived_table_is_the_typed_table(self):
        for field, typed in PLAN_E4_TABLE.items():
            with self.subTest(field=field):
                derived = _derived_table(self.entries, self.before,
                                         self._projection({field: self._perturbation(field)}))
                self.assertEqual(frozenset(derived), typed,
                                 _side_that_moved(field, derived, typed))

    def test_each_config_field_stales_exactly_its_gates(self):
        for field, typed in PLAN_E4_TABLE.items():
            value = self._perturbation(field)
            with self.subTest(field=field, value=value):
                derived = _derived_table(self.entries, self.before,
                                         self._projection({field: value}))
                self._row({field: value}, typed, frozenset(derived))

    def test_the_early_cutoff(self):
        edited = self._projection(EARLY_CUTOFF)
        for key in EARLY_CUTOFF_UNCHANGED:
            self.assertEqual(float(edited["derived"][key]).hex(),
                             float(self.before["derived"][key]).hex(),
                             f"precondition: build() no longer returns {key} to the bit "
                             f"at {EARLY_CUTOFF}, so this row cannot test early cutoff")
        for field in EARLY_CUTOFF:
            self.assertTrue(EARLY_CUTOFF_FRESH <= PLAN_E4_TABLE[field],
                            f"precondition: {field} alone must stale {sorted(EARLY_CUTOFF_FRESH)}, "
                            f"or 'they stay fresh' says nothing about cutoff")
        typed = frozenset().union(*(PLAN_E4_TABLE[f] for f in EARLY_CUTOFF)) - EARLY_CUTOFF_FRESH
        derived = _derived_table(self.entries, self.before, edited)
        self.assertEqual(frozenset(derived), typed,
                         _side_that_moved("early cutoff", derived, typed))
        self._row(EARLY_CUTOFF, typed, frozenset(derived))

    def test_bed_xy_at_the_claim_level_and_back(self):
        project = self.base.copy(self)
        _set_default(project, "bed_xy", PERTURB_SPECIAL["bed_xy"])
        seen, proven = _seen(project)
        self.assertEqual({c: seen[c] for c in ("C1", "C2", "C3", "C4")},
                         {"C1": "fail", "C2": "pass", "C3": "pass", "C4": "stale"})
        self.assertEqual(proven, {"C2", "C3"})

        moved = _doc(_cli(project, "check", "--json"))
        self.assertEqual(_executed(moved), {"bracket.bed_fit"})
        self.assertEqual(moved["counts"]["controls"]["executed"], 0)
        seen, proven = _seen(project)
        self.assertEqual(seen["C4"], "pass")
        self.assertEqual(proven, {"C2", "C3", "C4"})

        # Revert: the 220 entry is still in the cache, addressed by the inputs
        # it was recorded at. Everything is current again without running anything.
        cache = _cache(project)
        _set_default(project, "bed_xy", self.defaults["bed_xy"])
        status = _doc(_cli(project, "status", "--json"), 0)
        self.assertEqual(status["stale_gates"], [])
        self.assertEqual(_fresh(status), ALL)
        back = _doc(_cli(project, "check", "--json"))
        self.assertEqual(_executed(back), set(), "a revert re-ran a gate")
        self.assertEqual((back["counts"]["executed"], back["counts"]["cached"]), (0, len(ALL)))
        self.assertEqual(back["counts"]["controls"]["executed"], 0, "a revert re-ran a control")
        self.assertEqual(_cache(project), cache, "a revert wrote to the cache")
        self.assertEqual(_seen(project)[1], {"C2", "C3", "C4"})

    def _row(self, edits: dict[str, Any], typed: frozenset[str], derived: frozenset[str]) -> None:
        label = ", ".join(f"{k} -> {_literal(v)}" for k, v in edits.items())
        project = self.base.copy(self)
        cache = _cache(project)
        entries, controls = _split(cache)
        for field, value in edits.items():
            _set_default(project, field, value)

        status = _doc(_cli(project, "status", "--json"), 0)
        stale = set(status["stale_gates"])
        self.assertEqual(stale, typed, f"{label}: `status` disagrees with the TYPED table")
        self.assertEqual(stale, derived, f"{label}: `status` disagrees with the DERIVED table")
        self.assertEqual(_fresh(status), ALL - typed, f"{label}: fresh is not ALL - expected")
        for gate_id in typed:
            self.assertEqual(status["freshness"][gate_id]["state"], "stale",
                             f"{label}: {gate_id} {status['freshness'][gate_id]}")

        check = _doc(_cli(project, "check", "--json"))
        self.assertEqual(_executed(check), typed, f"{label}: `check` ran other than expected")
        self.assertEqual((check["counts"]["executed"], check["counts"]["cached"]),
                         (len(typed), len(ALL) - len(typed)), f"{label}: {check['counts']}")
        demonstrated = check["counts"]["controls"]
        self.assertEqual(demonstrated["executed"], 0,
                         f"{label}: a control ran — controls stand on the known-good "
                         f"design (D-27), and a default edit moves none of their values")
        self.assertEqual(demonstrated["cached"] + demonstrated["reverified"], len(ALL),
                         f"{label}: {demonstrated}")
        after = _cache(project)
        entries_now, controls_now = _split(after)
        self.assertEqual(controls_now, controls, f"{label}: a control entry was written")
        self.assertEqual(sorted(rel.split("/")[0] for rel in entries_now - entries), sorted(typed),
                         f"{label}: not exactly one new entry per re-run gate")
        self.assertEqual({rel: after[rel] for rel in cache}, cache,
                         f"{label}: an existing entry was rewritten")
        self.assertEqual(_doc(_cli(project, "status", "--json"), 0)["stale_gates"], [],
                         f"{label}: stale right after the check that re-ran it")

        forced = _doc(_cli(project, "check", "--force", "--json"))
        self.assertEqual((forced["counts"]["executed"], forced["counts"]["controls"]["executed"]),
                         (len(ALL), len(ALL)), f"{label}: --force did not re-run everything")
        self.assertEqual(_outcomes(forced), _outcomes(check),
                         f"{label}: the soundness oracle — re-running every gate reached "
                         f"another answer than the affected-only sweep served")
        self.assertEqual(_cache(project), after,
                         f"{label}: --force at unchanged inputs wrote to the cache")


# --------------------------------------------------------------------------- #
# GateVersionRows: one module of a multi-module pack, edited
# --------------------------------------------------------------------------- #
FDM_MESH = frozenset({"fdm.overhang", "fdm.bridge_span"})
FDM_PRINTABILITY = frozenset({"fdm.bed_fit", "fdm.min_wall", "fdm.layer_alignment",
                              "fdm.print_time_est", "fdm.process_model_valid"})

#: fdm-print's gate-version rows (phase-1.md), typed from the pack's layout:
#: `mesh.py` defines the two mesh gates; `_process_model.py` is loaded BY PATH by
#: `printability.py` alone (a fixture loads it too, which is control input, not
#: gate code), so only the recorded closure can see it; `fdm_print_fold.py` is
#: loaded by both gate modules. Rejected: a whole-pack digest per gate, which
#: makes every row "all seven" (spec §3.6).
GATE_VERSION_TABLE: dict[str, frozenset[str]] = {
    "gates/mesh.py": FDM_MESH,
    "gates/_process_model.py": FDM_PRINTABILITY,
    "gates/fdm_print_fold.py": FDM_MESH | FDM_PRINTABILITY,
}

#: What a row appends to the copied module: a new top-level name, so the edit is
#: one a reviewer would call a code change. Rejected: whitespace or a comment,
#: which move the bytes just as well but test less than the row claims.
GATE_EDIT = "\n\n# edited by tests/test_staleness.py (GateVersionRows)\n_EDITED_BY_A_TEST = True\n"


class GateVersionRows(_env.EnvCase):
    """Editing one module of fdm-print stales exactly the gates whose recorded
    code closure holds it — through a copy of the pack in `.atompipe/packs/`, on
    the pack's own baseline wrapped as a project. Where trimesh or numpy is
    missing (CI), the mesh gates' outcome is their availability skip, asserted as
    such: they have no entry to go stale, and a row that expects them simply
    expects nothing of them."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.specs = {spec.id: spec for spec in _projects.pack_gates("fdm-print")}
        cls.tier = str(max(int(spec.tier) for spec in cls.specs.values()))
        cls.available = {gid: gates.availability(spec) for gid, spec in cls.specs.items()}
        cls.ran = frozenset(gid for gid, (ok, _why) in cls.available.items() if ok)
        cls.base = _CheckedProject(
            "atompipe-gate-version-",
            lambda dest: _projects.wrap_pack_baseline("fdm-print", dest, copy_pack=True),
            "--tier", cls.tier)
        cls.addClassCleanup(cls.base.close)
        cls.entries = _entry_docs(cls.base.project)

    def _assert_skips(self, data: dict) -> None:
        rows = _rows(data)
        for gate_id, (ok, why) in self.available.items():
            if not ok:
                self.assertTrue(rows[gate_id]["skipped"], rows[gate_id])
                self.assertEqual(rows[gate_id]["skip_reason"], why)

    def test_the_first_check(self):
        self.assertEqual(set(self.specs), FDM_MESH | FDM_PRINTABILITY,
                         "fdm-print's gates moved: GATE_VERSION_TABLE is typed against them")
        self.assertTrue(FDM_PRINTABILITY <= self.ran, "the printability gates need no tool")
        first = self.base.first
        self.assertEqual(_executed(first), self.ran)
        self.assertEqual({g for g, r in _rows(first).items() if r["outcome"] == "pass"},
                         self.ran, "the pack's own baseline must pass its own gates")
        self._assert_skips(first)
        self.assertEqual(set(self.entries), self.ran)

    def test_editing_one_module_stales_exactly_its_gates(self):
        for rel, typed in GATE_VERSION_TABLE.items():
            with self.subTest(module=rel):
                expected = typed & self.ran
                spelled = f"<pack:fdm-print>/{rel}"
                derived = {gid for gid, entry in self.entries.items()
                           if spelled in entry["code"]["files"]}
                self.assertEqual(derived, expected,
                                 f"{rel}: the recorded closures (derived) and the typed "
                                 f"table disagree — over-recording if derived is larger, "
                                 f"a lost closure file (a code edit that stales nothing) "
                                 f"if smaller")
                project = self.base.copy(self)
                path = os.path.join(project, ".atompipe", "packs", "fdm-print", *rel.split("/"))
                self.assertTrue(os.path.isfile(path), path)
                with open(path, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(GATE_EDIT)

                status = _doc(_cli(project, "status", "--json"), 0)
                self.assertEqual(set(status["stale_gates"]), expected)
                self.assertEqual(_fresh(status), self.ran - expected)
                for gid in expected:
                    self.assertIn("code", status["freshness"][gid]["reasons"][0])
                for gid in self.specs.keys() - self.ran:
                    self.assertEqual(status["freshness"][gid]["state"], "never",
                                     "a gate that never ran has nothing to go stale")

                check = _doc(_cli(project, "check", "--tier", self.tier, "--json"))
                self.assertEqual(_executed(check), expected)
                self.assertEqual(check["counts"]["controls"]["executed"], len(expected),
                                 "an edited gate's control is demonstrated again at its new "
                                 "version (D-07), and no other control runs")
                self._assert_skips(check)
                self.assertEqual(_doc(_cli(project, "status", "--json"), 0)["stale_gates"], [])


# --------------------------------------------------------------------------- #
# CostIsKept, InstrumentMismatchIsNoted, LastCheckWatches
# --------------------------------------------------------------------------- #
#: How long the planted gate takes (phase-1.md's `V: CostIsKept`): far above any
#: runner's timer resolution, far below anything that slows the suite. Rejected:
#: 1 ms (a coarse clock rounds it towards zero, and a replayed duration of zero
#: would look like no replay at all); a second (the check runs it twice, the
#: control once more).
SLEEP_S = 0.05

_SLOW_GATE = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_staleness.py (CostIsKept): a gate that takes a while."""
import time

from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict

SLEEP_S = {sleep!r}


@gate(id="bracket.slow", title="takes a measurable time to judge the deflection",
      claims=["cost-probe"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/planted.py:far_past",
                                       note="the deflection pushed far past the limit"))
def slow(ctx):
    """Sleeps, then passes while the tip deflection stays under 5 mm."""
    time.sleep(SLEEP_S)
    value = float(ctx.params["deflection"])
    return Verdict(gate="bracket.slow", passed=value < 5.0, measured=value, limit=5.0,
                   units="mm")
'''


def _obs(project: str, gate_id: str, *, control: bool = False) -> dict:
    name = f"{gate_id}.control.json" if control else f"{gate_id}.json"
    with open(os.path.join(project, ".atompipe", "obs", name), encoding="utf-8") as fh:
        return json.load(fh)


class CostIsKept(_env.EnvCase):
    """What a run cost is measured once, when it ran, and a cache hit never
    replays it as a new measurement (phase-1.md: "a cache hit replays a duration
    as a new measurement" is the failure)."""

    def test_a_cache_hit_replays_no_duration(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"))
        _put(project, "selftest/planted.py", _PLANTED_FIXTURES)
        _put(project, "gates/slow.py", _SLOW_GATE.format(sleep=SLEEP_S))

        first = _doc(_cli(project, "check", "--json"))
        ran = _rows(first)["bracket.slow"]
        self.assertEqual((ran["outcome"], ran["cached"]), ("pass", False), ran)
        self.assertGreaterEqual(ran["duration_s"], SLEEP_S)
        entries, _controls = _split(_cache(project))
        names = [rel.split("/")[1][:-len(".json")] for rel in entries
                 if rel.startswith("bracket.slow/")]
        self.assertEqual(len(names), 1, names)
        runs = _obs(project, "bracket.slow")["runs"]
        self.assertEqual([r["entry"] for r in runs], names, "obs does not name the entry")
        self.assertGreaterEqual(runs[0]["duration_s"], SLEEP_S, "obs lost what the run cost")
        control_runs = _obs(project, "bracket.slow", control=True)["runs"]
        self.assertEqual(len(control_runs), 1)
        self.assertTrue(control_runs[0]["entry"].startswith("control-"),
                        "a control run in the gate's own series (S-31)")

        cache = _cache(project)
        obs = _tree(os.path.join(project, ".atompipe", "obs"))
        second = _doc(_cli(project, "check", "--json"))
        row_ = _rows(second)["bracket.slow"]
        self.assertTrue(row_["cached"], row_)
        self.assertNotIn("duration_s", row_, "a cached row replayed a duration")
        self.assertNotIn("cpu_s", row_, "a cached row replayed a duration")
        self.assertEqual(second["counts"]["executed"], 0)
        self.assertEqual(_cache(project), cache, "a cache hit wrote to the cache")
        self.assertEqual(_tree(os.path.join(project, ".atompipe", "obs")), obs,
                         "a cache hit was recorded as a run")


#: The numpy version a re-signed entry claims it was recorded under: a PEP 440
#: local version no index has ever published, so it is never what this machine
#: has, numpy installed or not. Rejected: a real old release ("1.26.4") — a
#: machine running this suite may have exactly that installed, and the note the
#: test demands would rightly not exist (a false red).
ELSEWHERE_NUMPY = "0.0.0+elsewhere"


def _resign(path: str, instruments: dict[str, str], *, digest: bool = True) -> None:
    """Rewrite the entry at ``path`` with other ``instruments`` — and with
    ``digest`` its integrity digest recomputed by the documented rule (sha256 of
    the canonical JSON of every other field), as the spine on a machine with
    those libraries would have written it. The rule is spelled out here rather
    than borrowed from the spine: a test that signs with the code under test
    agrees with it by construction."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data["instruments"] = dict(sorted(instruments.items()))
    if digest:
        body = {key: value for key, value in data.items() if key != "digest"}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False)
        data["digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


class InstrumentMismatchIsNoted(_env.EnvCase):
    """Instruments are provenance, never part of rho (Q1.3, M11.5): an entry
    recorded under another numpy stays Fresh, and `status` and `doctor` say so."""

    def test_another_numpy_stays_fresh_and_is_named(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"))
        _doc(_cli(project, "check", "--json"))
        entries, _controls = _split(_cache(project))
        mine = [rel for rel in entries if rel.startswith("bracket.deflection/")]
        self.assertEqual(len(mine), 1, mine)
        path = os.path.join(project, ".atompipe", "verdicts", *mine[0].split("/"))
        with open(path, "rb") as fh:
            self.assertNotIn(b"numpy", fh.read(), "the premise: recorded without numpy")

        # The test's own negative control: the same edit WITHOUT the signature is
        # a hand edit — ignored, and nothing it says is current.
        _resign(path, {"numpy": ELSEWHERE_NUMPY}, digest=False)
        status = _doc(_cli(project, "status", "--json"), 0)
        self.assertNotEqual(status["freshness"]["bracket.deflection"]["state"], "fresh")
        doctor = {row_["check"]: row_ for row_ in
                  _doc(_cli(project, "doctor", "--json"))["checks"]}
        self.assertIn("hand-edited entry", doctor["cache-entries"]["detail"])

        _resign(path, {"numpy": ELSEWHERE_NUMPY})
        with open(path, "rb") as fh:
            signed = fh.read()
        status = _doc(_cli(project, "status", "--json"), 0)
        row_ = status["freshness"]["bracket.deflection"]
        self.assertEqual((row_["state"], row_["admission"]), ("fresh", "admitted"), row_)
        self.assertEqual(status["stale_gates"], [])

        text = _text(_cli(project, "status"), 0).splitlines()
        notes = [m for line in text if (m := _transcript.NOTE_INSTRUMENT.fullmatch(line))]
        self.assertEqual([(m.group("gate"), m.group("module"), m.group("recorded"))
                          for m in notes],
                         [("bracket.deflection", "numpy", ELSEWHERE_NUMPY)], text)
        here = notes[0].group("here")
        self.assertNotEqual(here, ELSEWHERE_NUMPY)
        if importlib.util.find_spec("numpy") is None:
            self.assertEqual(here, "absent")

        proc = _cli(project, "doctor", "--json")
        doctor = {row_["check"]: row_ for row_ in _doc(proc)["checks"]}
        self.assertEqual(doctor["instruments"]["status"], "warn", doctor["instruments"])
        self.assertIn(f"bracket.deflection — recorded under numpy {ELSEWHERE_NUMPY}; here {here}",
                      doctor["instruments"]["detail"])
        self.assertEqual(proc.returncode, 0, "provenance never fails doctor")

        check = _doc(_cli(project, "check", "--json"))
        self.assertTrue(_rows(check)["bracket.deflection"]["cached"],
                        "an entry from another numpy was re-run: instruments reached rho")
        self.assertEqual(check["counts"]["executed"], 0)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), signed, "check rewrote an entry it served")


def _stable(data: dict) -> dict:
    """A ``check --json`` document without what a clock decides."""
    out = {key: value for key, value in data.items() if key != "duration_s"}
    out["verdicts"] = [{k: v for k, v in r.items() if k not in ("duration_s", "cpu_s")}
                       for r in data["verdicts"]]
    return out


class LastCheckWatches(_env.EnvCase):
    """`last_check.json` through the CLI: the checkout's memory — under the
    ignored `cache/`, never read by `check` — whose fingerprint moves with what
    it watches (phase-1.md: "blind to an input rho covers" is the failure)."""

    def setUp(self) -> None:
        self.project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"), git=True)
        _put(self.project, "data/limit.txt", "2.0\n")
        _plant_opener(self.project, gate_id="bracket.opens", rel="data/limit.txt",
                      tag="limit-probe", param="deflection", fixture="far_past")
        self.first = _doc(_cli(self.project, "check", "--json"))
        self.assertEqual(_rows(self.first)["bracket.opens"]["outcome"], "pass")
        self.path = os.path.join(self.project, ".atompipe", "cache", "last_check.json")

    def _last(self) -> dict:
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_it_is_the_checkouts_memory(self):
        self.assertTrue(os.path.isfile(self.path), "a full check wrote no last_check.json")
        ignored = _env.git(["check-ignore", "-q", "--", ".atompipe/cache/last_check.json"],
                           cwd=self.project)
        self.assertEqual(ignored.returncode, 0, "last_check.json is not ignored")
        porcelain = _env.git(["status", "--porcelain", "--untracked-files=all"], cwd=self.project)
        self.assertEqual(porcelain.returncode, 0, porcelain.stderr)
        self.assertEqual([line for line in porcelain.stdout.splitlines()
                          if ".atompipe/cache/" in line], [], porcelain.stdout)

    def test_check_never_reads_it(self):
        before = _doc(_cli(self.project, "check", "--json"))
        self.assertEqual(before["counts"]["executed"], 0)
        cache = _cache(self.project)
        obs = _tree(os.path.join(self.project, ".atompipe", "obs"))
        recorded = self._last()

        os.remove(self.path)
        gone = _doc(_cli(self.project, "check", "--json"))
        self.assertEqual(_stable(gone), _stable(before), "deleting it changed the check")
        self.assertEqual(_cache(self.project), cache)
        self.assertEqual(_tree(os.path.join(self.project, ".atompipe", "obs")), obs)
        self.assertEqual(self._last()["fingerprint"], recorded["fingerprint"],
                         "the next full check wrote it again, with nothing moved")

        # A lying one: every claim PASS, nothing counted. A check that trusted
        # its own summary of a previous run would print some of it.
        lie = dict(recorded, statuses={cid: "pass" for cid in recorded["statuses"]},
                   counts={}, worst={"claim": None, "gate": None, "detail": None})
        _put(self.project, ".atompipe/cache/last_check.json", json.dumps(lie, indent=2) + "\n")
        lied = _doc(_cli(self.project, "check", "--json"))
        self.assertEqual(_stable(lied), _stable(before), "a forged last_check.json changed "
                                                         "the check")
        self.assertEqual(self._last()["statuses"], recorded["statuses"],
                         "the forged statuses survived the next full check")

    def test_the_fingerprint_moves_with_what_is_watched(self):
        def checked() -> str:
            _doc(_cli(self.project, "check", "--json"))
            return self._last()["fingerprint"]

        start = self._last()["fingerprint"]
        _put(self.project, "notes.txt", "not an input\n")
        self.assertEqual(checked(), start, "the negative control: an unwatched file moved it")

        was = start
        for rel, text in ((".atompipe/packs/extra/NOTES.md", "a pack in progress\n"),
                          ("objectives.json", "{}\n")):
            with self.subTest(watched=rel):
                cache = _cache(self.project)
                _put(self.project, rel, text)
                now = checked()
                self.assertEqual(_cache(self.project), cache,
                                 "the check wrote an entry, which moves the fingerprint "
                                 "by itself: this row would pass vacuously")
                self.assertNotEqual(now, was, f"{rel} did not move the fingerprint")
                was = now

        # A file the gate opened. Its edit re-runs the gate and writes an entry,
        # which would move the fingerprint by itself — so the proof is the
        # revert: the old entry serves again, nothing is written, and only the
        # file's bytes differ between the two checks.
        _replace_once(self.project, "data/limit.txt", "2.0", "3.0")
        edited = _doc(_cli(self.project, "check", "--json"))
        self.assertEqual(_executed(edited), {"bracket.opens"})
        moved = self._last()["fingerprint"]
        cache = _cache(self.project)
        _replace_once(self.project, "data/limit.txt", "3.0", "2.0")
        back = _doc(_cli(self.project, "check", "--json"))
        self.assertEqual(_executed(back), set(), "the reverted file's entry did not serve")
        self.assertEqual(_cache(self.project), cache, "the revert wrote to the cache")
        self.assertNotEqual(self._last()["fingerprint"], moved,
                            "a file a gate opened did not move the fingerprint")

    def test_project_json_moves_the_fingerprint(self):
        """`.atompipe/project.json` is the project marker, and since checkpoint 1.3
        the one home of `packs` and `model_entry` — which gates exist, and which
        model they read. What would slip through: those two moved out of
        `ledger.json`, which is now the generated index and deliberately unwatched
        (every command rewrites it), so a watch list that kept only the record
        directories would be blind to a hand edit of the file that now holds them.
        The edit here changes no verdict (the cache is asserted unchanged), so only
        the watch can see it; and the fingerprint comes back when the bytes do — it
        is a function of what is watched, not a count of checks."""
        def checked() -> str:
            _doc(_cli(self.project, "check", "--json"))
            return self._last()["fingerprint"]

        rel = ".atompipe/project.json"
        self.assertTrue(os.path.isfile(os.path.join(self.project, *rel.split("/"))),
                        "the first check did not migrate the bracket: there is no marker")
        start = self._last()["fingerprint"]
        self.assertEqual(checked(), start, "the negative control: nothing moved, and it did")
        cache = _cache(self.project)

        _replace_once(self.project, rel, "printed in PETG", "printed in PETG, edited by hand")
        edited = checked()
        self.assertEqual(_cache(self.project), cache,
                         "the edit re-ran a gate, which moves the fingerprint by itself: "
                         "this row would pass vacuously")
        self.assertNotEqual(edited, start, f"{rel} did not move the fingerprint")

        _replace_once(self.project, rel, "printed in PETG, edited by hand", "printed in PETG")
        self.assertEqual(checked(), start,
                         f"{rel} restored byte for byte, and the fingerprint did not come back")


if __name__ == "__main__":
    unittest.main()
