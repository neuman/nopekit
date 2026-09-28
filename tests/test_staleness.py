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

``LastCheck`` holds ``last_check.json`` to its contract: written only after a
full recorded sweep, never read by the sweep, and fingerprinting what is watched.

Run:  PYTHONPATH=src python3 -m unittest tests.test_staleness -v
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sys
import unittest
from unittest import mock

from atompipe import claims, gates, modelio, store, util, verdicts
from atompipe.models import Acceptance, Claim, ClaimStatus, Ledger
from atompipe.util import FileDigests

import _env
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
    """``config`` the inputs, ``derived`` what a build() would return:
    stress = 0.013 x load, 0.195 MPa at the 15 N default (S-20's number)."""
    cfg = {"x": 1.0, "load_n": 15.0}
    cfg.update(over)
    built = {"stress": round(cfg["load_n"] * 0.013, 6)}
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
        self.assertIn("load_n 15.0 -> 20000.0", resolution.rows["t.stress"].stale_reason)
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


if __name__ == "__main__":
    unittest.main()
