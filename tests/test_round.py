# SPDX-License-Identifier: Apache-2.0
"""The round record, `.nopekit/round.json`, and `nopekit round` (SPEC 18, 24).

Only `round` writes the record, and only when what it holds moved (invariant 8);
nothing reads it for truth (invariants 8 and 12); what it holds is what the
composition says (invariant 12); a record the project has moved past says so
(invariant 7); its bytes are the same on every path and under any hash seed;
and `round` never blocks — it is the Stop hook's future body.

What slipped through in the design this replaced: a tracked summary `check`
rewrote would have made every check a tracked write, and one a reader trusted
would have been a second producer of every status (S-41: the committed
readiness report had drifted from its ledger).

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_round.py'
"""
from __future__ import annotations

import ast
import contextlib
import functools
import io
import json
import os
import re
import sys
import tempfile
import unittest
from typing import Any
from unittest import mock

import _env
import _projects
from nopekit import claims as claims_mod
from nopekit import cli, derivation, graph, report as report_mod, rounds, util
from nopekit.models import ClaimStatus

NOW = "2026-10-15T00:00:00Z"

#: The round the bracket's committed record holds: it moves each time the
#: bracket's cache is regenerated (architecture phase 5 wrote round 2), so the
#: tests read it rather than repeat it.
with open(os.path.join(_projects.BRACKET, ".nopekit", "round.json"), encoding="utf-8") as _fh:
    R = json.load(_fh)["round"]
SRC = os.path.join(_env.REPO, "src", "nopekit")


def main(root: str, *argv: str) -> tuple[int, str, str]:
    """``nopekit <argv>`` in process against ``root``: (code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["-C", root, *argv])
    return code, out.getvalue(), err.getvalue()


def record_path(root: str) -> str:
    return os.path.join(root, *rounds.ROUND_REL.split("/"))


def stamp(root: str) -> tuple[bytes, int, int] | None:
    """The round record's bytes, mtime and inode — a rewrite of equal bytes is
    still a write."""
    path = record_path(root)
    if not os.path.isfile(path):
        return None
    st = os.stat(path)
    with open(path, "rb") as fh:
        return fh.read(), st.st_mtime_ns, st.st_ino


def bracket(dest: str) -> str:
    return _projects.bracket_copy(dest, migrated=True)


def _edit_json(path: str, **changes: Any) -> None:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.update(changes)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def resolved(root: str):
    ledger = cli._load(root)
    registry, _ = cli._registry(root, ledger)
    model, projection, error = cli._projection_safe(root, ledger)
    view, resolution = cli._resolved(root, ledger, registry, projection, error, now=NOW,
                                     model=model)
    return ledger, registry, model, projection, view, resolution


def round_state(root: str) -> rounds.RoundState:
    ledger, registry, model, projection, view, resolution = resolved(root)
    return cli._round_state(root, ledger, model, projection, resolution, registry, view)


# --------------------------------------------------------------------------- #
#: The commands that read or sweep and must never write the round record: each
#: `check` spelling, the readers, and the site. `round` is the one writer.
_NOT_ROUND = (("check",), ("check", "--force"), ("check", "--only", "bracket.deflection"),
              ("check", "--json"), ("check", "--no-record"), ("status",), ("status", "--json"),
              ("report",), ("why", "C1"), ("why", "deflection"), ("doctor",), ("inputs",),
              ("site", "init"), ("site", "build"))


def untouched_problems(root: str, runs: Any) -> list[str]:
    """Each command in ``runs`` (argv tuples, or callables) that left the round
    record other than it found it — bytes, mtime or inode."""
    problems = []
    for argv in runs:
        before = stamp(root)
        if callable(argv):
            argv()
            name = getattr(argv, "__name__", "callable")
        else:
            main(root, *argv)
            name = " ".join(argv)
        if stamp(root) != before:
            problems.append(f"`{name}` wrote {rounds.ROUND_REL}")
    return problems


class OnlyRoundWritesTheRoundRecord(_env.EnvCase):
    """Invariant 8: only `round` writes `.nopekit/round.json`, at most once a run,
    and only when what it holds moved."""

    def test_no_other_command_writes_it(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        self.assertIsNotNone(stamp(root), "the bracket commits a round record")
        self.assertEqual(untouched_problems(root, _NOT_ROUND), [])
        code, out, err = main(root, "round")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith(f"round {R} stands: nothing it records moved."), out)

    def test_round_writes_the_next_round_and_names_the_flip(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        _edit_json(os.path.join(root, "claims", "C1.json"),
                   acceptance={"quantity": "tip deflection", "comparator": "<=",
                               "limit": 0.8, "units": "mm"})
        before = stamp(root)
        code, out, err = main(root, "round")
        self.assertEqual(code, 0, err)
        self.assertNotEqual(stamp(root), before)
        self.assertTrue(out.startswith(f"round {R + 1}: C1 failing -> checked"), out)
        self.assertIn("Wrote .nopekit/round.json.", out)
        with open(record_path(root), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual((doc["round"], doc["claims"]["C1"]), (R + 1, "checked"))

    def test_a_check_that_writes_it_is_caught(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        _edit_json(os.path.join(root, "claims", "C7.json"), rationale="edited by hand")
        real = cli._check_run

        def writes_it(root_, ledger, **kw):
            ran = real(root_, ledger, **kw)
            body, _now = cli._round_inputs(root_, cli._load(root_), ran.registry, ran.model,
                                           ran.projection, ran.view, ran.resolution)
            rounds.write(root_, body)
            return ran

        with mock.patch.object(cli, "_check_run", writes_it):
            problems = untouched_problems(root, [("check",)])
        self.assertEqual(problems, [f"`check` wrote {rounds.ROUND_REL}"])


# --------------------------------------------------------------------------- #
def _uses(tree: ast.AST) -> list[tuple[str, str, int]]:
    """``(top-level function, attribute, line)`` for every ``rounds.<x>`` — a
    call, or a name read (``rounds.ROUND_REL``) — ``<module>`` outside any
    function."""
    found = []

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            here = owner
            if owner == "<module>" and isinstance(child, (ast.FunctionDef,
                                                          ast.AsyncFunctionDef)):
                here = child.name
            if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name) \
                    and child.value.id == "rounds":
                found.append((here, child.attr, child.lineno))
            visit(child, here)

    visit(tree, "<module>")
    return found


#: What reaches the record's bytes: asking `rounds` about it, or holding its
#: path. Only `cmd_round` (which writes it and says where), `_round_state`
#: (`status` and `doctor`'s one question) and `build_parser` (the help names
#: the file) may. What slipped through (review of phase 3a): only `read` and
#: `state` calls were held, so a reader that opened `rounds.path_of(root)` or
#: joined `rounds.ROUND_REL` itself passed.
_RECORD_ACCESS = frozenset({"read", "state", "write", "path_of", "ROUND_REL", "ROUND_NAME"})
#: Architecture phase 6 moved `cmd_round`'s body to `_round_run` (the hook runs
#: it too), and `_round_commit` names the record's path twice: it stages the file
#: as the round's own, and reads HEAD's copy for one number — which rounds the
#: commit also holds ("rounds 2 to 3 were not committed") — never a status.
#: `_round_label` reads the record's round number alone, to label a trace
#: "traced at round N" (architecture phase 3b, decision 39) — never a status.
#: Eddy milestone 0: `receive` holds the state file a pushed tree carries —
#: `rounds.read` on a MATERIALISED COPY of that tree, from `state_file_problems`
#: alone, to say whether the record is that tree's own (present, sealed, computed
#: from the tree's records and verdict cache, in rendered form, its round above
#: the previous claiming commit's): the question `_round_state` asks for `status`
#: and `doctor`, never a status. Its module level names the file once
#: (`STATE_REL = rounds.ROUND_REL`, the path every tree rule judges). No other
#: `receive` function reaches `read`, `state`, `write` or `path_of`
#: (`_RECEIVE_HELD`, held below): a reader of the record for truth would sit in
#: the walk, not in the one function that holds it.
_RECORD_ACCESS_ALLOWED = frozenset({("cli", "cmd_round"), ("cli", "_round_run"),
                                    ("cli", "_round_commit"), ("cli", "_round_state"),
                                    ("cli", "build_parser"), ("cli", "_round_label"),
                                    ("receive", "state_file_problems"), ("receive", "<module>")})
_RECEIVE_HELD = frozenset({"read", "state", "write", "path_of"})


def round_reader_problems(sources: dict[str, str]) -> list[str]:
    """Every way a module under ``sources`` (``{module name: source}``) could read
    the round record for truth: naming its file (only `rounds` may), importing
    `rounds` (only `cli` may) or a name from it (none may: the attribute is what
    is held), or reaching the record (`_RECORD_ACCESS`) from anywhere but
    `_RECORD_ACCESS_ALLOWED`."""
    problems = []
    for name, source in sorted(sources.items()):
        problems += _module_problems(name, source)
    return problems


@functools.lru_cache(maxsize=None)
def _module_problems(name: str, source: str) -> tuple[str, ...]:
    """`round_reader_problems` of one module, cached by its source: the planted
    row re-reads only the modules it plants into."""
    problems: list[str] = []
    tree = ast.parse(source)
    if name != "rounds":
        # A docstring may say where the record lives; a value may not.
        prose = {id(n.value) for n in ast.walk(tree)
                 if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in prose and "round.json" in node.value:
                problems.append(f"{name}:{node.lineno}: names round.json")
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports |= {alias.name for alias in node.names}
            if (node.module or "").endswith("rounds"):
                imports.add("rounds")
                if name != "rounds":
                    problems.append(f"{name}:{node.lineno}: imports "
                                    f"{', '.join(a.name for a in node.names)} from rounds")
        elif isinstance(node, ast.Import):
            imports |= {alias.name.rsplit(".", 1)[-1] for alias in node.names}
    # Eddy milestone 0: `receive` imports `rounds` to hold a pushed tree's state
    # file (`_RECORD_ACCESS_ALLOWED`'s note); `claims` or `report` importing it
    # is still a reader of the record for truth.
    if "rounds" in imports and name not in ("cli", "rounds", "receive"):
        problems.append(f"{name}: imports rounds")
    for func, attr, line in _uses(tree):
        if attr in _RECORD_ACCESS and (name, func) not in _RECORD_ACCESS_ALLOWED:
            problems.append(f"{name}:{line}: {func} reaches rounds.{attr}")
        if name == "receive" and attr in _RECEIVE_HELD and func != "state_file_problems":
            problems.append(f"{name}:{line}: {func} reaches rounds.{attr}; only "
                            f"state_file_problems holds the state file")
    return tuple(problems)


def _sources() -> dict[str, str]:
    out = {}
    for entry in sorted(os.listdir(SRC)):
        if entry.endswith(".py"):
            with open(os.path.join(SRC, entry), encoding="utf-8") as fh:
                out[entry[:-3]] = fh.read()
    return out


def _clockless(doc: Any) -> Any:
    """``doc`` without the build's clock (``built``) and each verdict's age."""
    if isinstance(doc, dict):
        return {k: _clockless(v) for k, v in doc.items() if k not in ("built", "age_s")}
    if isinstance(doc, list):
        return [_clockless(v) for v in doc]
    return doc


def forge(root: str) -> None:
    """A round record every claim of which reads checked, ready, and resealed —
    so it reads current: the forgery a reader that trusted it would believe."""
    with open(record_path(root), encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["claims"] = {cid: "checked" for cid in doc["claims"]}
    doc["requirements"] = {rid: "checked" for rid in doc["requirements"]}
    doc["readiness"] = "ready"
    doc["milestones"] = {m: "ready" for m in doc["milestones"]}
    doc["self"] = rounds.self_digest(doc)
    with open(record_path(root), "wb") as fh:
        fh.write(rounds.render(doc))


class NothingReadsTheRoundRecordForTruth(_env.EnvCase):
    """Invariants 8 and 12: the record is an output. No module but `rounds` names
    its file, none but `cli` imports `rounds`, only `cmd_round` and
    `_round_state` ask it anything — and a forged record, resealed so it reads
    current, moves no status, no count, no readiness and no exit code."""

    def test_the_spine_reads_it_nowhere(self):
        self.assertEqual(round_reader_problems(_sources()), [])

    def test_a_planted_reader_is_caught(self):
        sources = _sources()
        sources["claims"] += ("\n\ndef _peek(root):\n    from . import rounds\n"
                              "    return rounds.read(root)\n")
        sources["planted"] = "PATH = '.nopekit/round.json'\n"
        sources["cli"] += ("\n\ndef cmd_report_planted(args):\n"
                           "    return json.load(open(rounds.path_of(args.root)))['claims']\n"
                           "\n\ndef _joined(root):\n"
                           "    return json.load(open(os.path.join(root, rounds.ROUND_REL)))\n"
                           "\n\ndef _named(root):\n"
                           "    from .rounds import path_of\n    return path_of(root)\n")
        problems = round_reader_problems(sources)
        self.assertTrue(any(p.startswith("claims: imports rounds") for p in problems), problems)
        self.assertTrue(any("_peek reaches rounds.read" in p for p in problems), problems)
        self.assertTrue(any("cmd_report_planted reaches rounds.path_of" in p
                            for p in problems), problems)
        self.assertTrue(any("_joined reaches rounds.ROUND_REL" in p for p in problems),
                        problems)
        self.assertTrue(any("imports path_of from rounds" in p for p in problems), problems)
        self.assertTrue(any(p.startswith("planted:1: names round.json") for p in problems),
                        problems)

    def test_a_forged_record_moves_nothing(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        ledger, registry, model, projection, view, resolution = resolved(root)

        def facts() -> tuple:
            _l, reg, _m, _p, view_, res = resolved(root)
            composed = claims_mod.compositions(view_, registry=reg,
                                               stale_gates=res.stale_gates)
            summary = claims_mod.summarise(view_, reg, stale_gates=res.stale_gates)
            text = report_mod.render_terminal(view_, reg, stale_gates=res.stale_gates)
            code, out, _err = main(root, "status", "--json")
            status = json.loads(out)
            status.pop("round")
            status.pop("last_check", None)
            return ({c: (str(x.status), x.errored) for c, x in composed.items()},
                    summary, text, status)

        def readers() -> dict:
            """What `report`, `claim list`, `why`, `gap` and `site build` say."""
            said = {}
            for argv in (("report",), ("report", "--json"), ("claim", "list", "--json"),
                         ("why", "C1"), ("gap", "--json")):
                code, out, err = main(root, *argv)
                said[" ".join(argv)] = (code, out)
            code, _out, err = main(root, "site", "build")
            self.assertEqual(code, 0, err)
            with open(os.path.join(root, "site", "data", "state.json"),
                      encoding="utf-8") as fh:
                said["site build"] = _clockless(json.load(fh))
            return said

        self.assertEqual(main(root, "site", "init")[0], 0)
        shown = readers()
        before = facts()
        forge(root)
        # Resealed, it reads as no hand edit — and still never as current: what
        # it records is not what `round` would write now (review of phase 3a).
        found = round_state(root)
        self.assertEqual((found.state, found.moved), ("behind", ("resolution",)))
        # `facts` before `readers`: the page `status` reports on is the one
        # built before the forgery, so its build time cannot move.
        self.assertEqual(facts(), before)
        self.assertEqual(readers(), shown)

    def test_check_and_export_ignore_a_forged_record(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        def junit_now() -> str:
            with open(os.path.join(root, report_mod.JUNIT_DEFAULT), encoding="utf-8") as fh:
                text = fh.read()
            # The clock and the run's durations are the run's, never the record's.
            return re.sub(r'="\d{4}-\d\d-\d\dT[^"]*Z"|(timestamp|time)="[^"]*"', "", text)

        before = _env.nopekit(["check", "--junit"], cwd=root)
        junit = junit_now()
        forge(root)
        after = _env.nopekit(["check", "--junit"], cwd=root)
        junit_after = junit_now()
        self.assertEqual((before.returncode, after.returncode), (1, 1))
        self.assertEqual(junit_after, junit)
        dry = _env.nopekit(["export", "print-v1", "--dry-run"], cwd=root)
        self.assertEqual(dry.returncode, 1, dry.stdout + dry.stderr)


# --------------------------------------------------------------------------- #
def round_status_problems(doc: dict, view: Any, composed: dict, summary: dict,
                          requirements: dict) -> list[str]:
    """Every token in ``doc`` (a round record, or `body`) that is not what its one
    producer says over ``view``: a claim's `STATUS_KEY` (or `errored`), a
    requirement's, the counts, readiness per milestone and for the project."""
    problems = []

    def token(status: Any, errored: bool) -> str:
        return "errored" if errored else claims_mod.STATUS_KEY[status]

    want = {cid: token(c.status, c.errored) for cid, c in composed.items()}
    if doc.get("claims") != want:
        problems.append(f"claims: {doc.get('claims')} != {want}")
    want = {rid: token(r.status, r.errored) for rid, r in requirements.items()}
    if doc.get("requirements") != want:
        problems.append(f"requirements: {doc.get('requirements')} != {want}")
    counts = dict(summary["counts"], errored=summary["errored"])
    if doc.get("counts") != counts:
        problems.append(f"counts: {doc.get('counts')} != {counts}")
    ready = "ready" if claims_mod.unresolved(view, composed).ready else "not-ready"
    if doc.get("readiness") != ready:
        problems.append(f"readiness: {doc.get('readiness')} != {ready}")
    for milestone in view.milestones:
        ready = "ready" if claims_mod.unresolved(view, composed, milestone).ready \
            else "not-ready"
        if (doc.get("milestones") or {}).get(milestone.id) != ready:
            problems.append(f"milestone {milestone.id}: != {ready}")
    return problems


class TheRoundRecordSaysWhatTheCompositionSays(_env.EnvCase):
    """Invariant 12: the round record says no more and no less than the
    composition — a crash as `errored`, Pending build and an unowned assumption
    as not ready, and a status the evidence contradicts in `disagree`, never a
    silent `checked`."""

    @classmethod
    def setUpClass(cls) -> None:
        tmp = tempfile.mkdtemp(prefix="nopekit-round-")
        cls.addClassCleanup(_env._rmtree, tmp)
        cls.root = bracket(os.path.join(tmp, "b"))
        (cls.ledger, cls.registry, cls.model, cls.projection, cls.view,
         cls.resolution) = resolved(cls.root)

    def _facts(self, composed=None):
        res = self.resolution
        composed = composed or claims_mod.compositions(self.view, registry=self.registry,
                                                       stale_gates=res.stale_gates)
        summary = claims_mod.summarise(self.view, self.registry, stale_gates=res.stale_gates)
        cover = claims_mod.effective_gates(self.view, self.registry)
        return composed, summary, report_mod.requirement_statuses(self.view, composed, cover)

    def test_the_record_is_the_composition(self):
        body, _now = cli._round_inputs(self.root, self.ledger, self.registry, self.model,
                                       self.projection, self.view, self.resolution)
        composed, summary, requirements = self._facts()
        self.assertEqual(round_status_problems(body, self.view, composed, summary,
                                               requirements), [])
        self.assertEqual(body["readiness"], "not-ready")       # C5 pending build, C6 gap
        self.assertEqual(body["claims"]["C5"], "pending_build")
        self.assertEqual(body["disagree"], {"claims": [], "requirements": []})
        with open(record_path(self.root), encoding="utf-8") as fh:
            committed = json.load(fh)
        self.assertEqual(round_status_problems(committed, self.view, composed, summary,
                                               requirements), [])

    def test_a_crash_reads_errored(self):
        composed, _summary, _req = self._facts()
        crashed = dict(composed, C2=claims_mod.Composed(ClaimStatus.BLOCKED,
                                                         claims_mod.ClaimCause.ERRORED))
        found = graph.tokens(self.view, self.resolution, self.registry, composed=crashed)
        self.assertEqual(found.claims["C2"], "errored")
        self.assertEqual(found.requirements["R1"], "failing")

    def test_a_resolver_that_lies_is_caught(self):
        real = claims_mod.compose

        def lying(claim, *args, **kwargs):
            found = real(claim, *args, **kwargs)
            if claim.id == "C1":
                return claims_mod.Composed(ClaimStatus.PASS, claims_mod.ClaimCause.CHECKED)
            return found

        with mock.patch.object(claims_mod, "compose", lying):
            found = graph.tokens(self.view, self.resolution, self.registry)
        self.assertEqual(found.claims["C1"], "checked")
        self.assertIn("C1", found.disagree["claims"], found.disagree)
        body, _now = cli._round_inputs(self.root, self.ledger, self.registry, self.model,
                                       self.projection, self.view, self.resolution)
        body["claims"]["C1"] = "checked"
        composed, summary, requirements = self._facts()
        self.assertTrue(round_status_problems(body, self.view, composed, summary,
                                              requirements))


# --------------------------------------------------------------------------- #
#: Each edit after a round, and what the record must say moved.
def _edit_record(root: str) -> None:
    _edit_json(os.path.join(root, "claims", "C7.json"), rationale="edited after the round")


def _new_entry(root: str) -> None:
    folder = os.path.join(root, ".nopekit", "verdicts", "bracket.deflection")
    name = next(n for n in sorted(os.listdir(folder)) if not n.startswith("control-"))
    with open(os.path.join(folder, name), "rb") as fh:
        data = fh.read()
    with open(os.path.join(folder, "0000000000000000-00000000.json"), "wb") as fh:
        fh.write(data)


def _edit_model(root: str) -> None:
    path = os.path.join(root, "model", "bracket.py")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace("    bed_xy: float = 220.0", "    bed_xy: float = 250.0", 1))


#: A model edit moves the design and, since the trace is keyed by the
#: choices' values, the trace the design has (architecture phase 3b: none now).
_MOVES = ((_edit_record, ("records",)), (_new_entry, ("verdicts",)),
          (_edit_model, ("design", "trace")))


def behind_problems(make: Any) -> list[str]:
    """Each edit in `_MOVES`, on a fresh copy ``make()`` returns, that the round
    record's state does not name as moved."""
    problems = []
    for edit, moved in _MOVES:
        root = make()
        if round_state(root).state != "current":
            problems.append(f"{edit.__name__}: the copy's record was not current to begin")
            continue
        edit(root)
        found = round_state(root)
        if (found.state, found.moved) != ("behind", moved):
            problems.append(f"{edit.__name__}: {found.state} {found.moved}, not behind {moved}")
    return problems


def doctor_round_row(root: str) -> dict:
    """`doctor --json`'s `round` row, in process."""
    code, out, err = main(root, "doctor", "--json")
    rows = [row for row in json.loads(out)["checks"] if row["check"] == "round"]
    if len(rows) != 1:
        raise AssertionError(f"doctor printed {len(rows)} round rows: {out}{err}")
    return rows[0]


class ABehindRoundRecordSaysSo(_env.EnvCase):
    """Invariant 7: a round record the project has moved past is never shown as
    current — `status` says what moved; a token edited by hand, a record that
    does not read, and one a newer nopekit wrote each say so, and `round` never
    rewrites the newer one."""

    def test_status_says_current_then_what_moved(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        code, out, err = main(root, "status")
        self.assertIn(f"round record: round {R}, current with the records, the verdict cache "
                      "and the design", out)
        _edit_record(root)
        code, out, err = main(root, "status")
        self.assertIn(f"round record: round {R}, behind the records — the records moved since "
                      "it was written; `nopekit round` writes the next", out)
        doc = json.loads(main(root, "status", "--json")[1])["round"]
        self.assertEqual(doc, {"state": "behind", "round": R, "moved": ["records"], "why": ""})

    def test_each_input_is_seen(self):
        self.assertEqual(behind_problems(lambda: bracket(os.path.join(self.tmp(), "b"))), [])

    def test_a_state_blind_to_the_cache_is_caught(self):
        real = rounds.state

        def blind(root, computed_now, content=None):
            held = (rounds.read(root).doc or {}).get("computed_from") or {}
            return real(root, dict(computed_now, verdicts=held.get("verdicts")))

        with mock.patch.object(rounds, "state", blind):
            problems = behind_problems(lambda: bracket(os.path.join(self.tmp(), "b")))
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith("_new_entry: current"), problems)

    def test_edited_unreadable_and_newer(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        path = record_path(root)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace('"C1": "failing"', '"C1": "checked"'))
        self.assertEqual(round_state(root).state, "edited")
        self.assertIn(f"round record: round {R}, edited by hand — its digest does not match "
                      "what it holds", main(root, "status")[1])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("<<<<<<< HEAD\n" + text + "=======\n" + text + ">>>>>>> theirs\n")
        found = round_state(root)
        self.assertEqual(found.state, "unreadable")
        self.assertIn("round record: does not read (", main(root, "status")[1])
        # A schema bumped by hand, beside a forged token, is an edit: the seal
        # rule holds at every schema, so a record whose seal fails was never
        # written by any nopekit — and `round` writes it again. What slipped
        # through (review of phase 3a): `newer` was decided before the seal.
        doc = json.loads(text)
        doc["schema"] = 2
        doc["claims"]["C1"] = "checked"
        doc["readiness"] = "ready"
        with open(path, "wb") as fh:
            fh.write(rounds.render(doc))
        self.assertEqual(round_state(root).state, "edited")
        self.assertIn(f"round record: round {R}, edited by hand", main(root, "status")[1])
        code, out, err = main(root, "round")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith(f"round {R + 1}: the last round record was edited by hand"),
                        out)
        with open(path, encoding="utf-8") as fh:
            rewritten = json.load(fh)
        self.assertEqual((rewritten["schema"], rewritten["claims"]["C1"],
                          rewritten["readiness"]), (1, "failing", "not-ready"))
        # One a newer nopekit sealed reads newer, and is never rewritten.
        doc = json.loads(text)
        doc["schema"] = 2
        doc["self"] = rounds.self_digest(doc)
        newer = rounds.render(doc)
        with open(path, "wb") as fh:
            fh.write(newer)
        self.assertEqual(round_state(root).state, "newer")
        before = stamp(root)
        code, out, err = main(root, "round")
        self.assertEqual(code, 0, err)
        self.assertEqual(stamp(root), before)
        self.assertIn("was written by a newer nopekit (schema 2)", out)

    def test_an_evaluator_edit_reads_behind(self):
        """A move none of the three digests covers — an evaluator's code — that
        moves the resolution reads behind, naming it, in `status` and
        `doctor`. What slipped through (review of phase 3a): after a gate-code
        edit `status` listed C2, C3 and C4 Stale beside "round 1, current"."""
        root = bracket(os.path.join(self.tmp(), "b"))
        with open(os.path.join(root, "gates", "structural.py"), "a", encoding="utf-8") as fh:
            fh.write("\n\ndef _added_after_the_round():\n    return None\n")
        found = round_state(root)
        self.assertEqual((found.state, found.moved), ("behind", ("resolution",)))
        out = main(root, "status")[1]
        self.assertIn(f"round record: round {R}, behind the records — the resolution (an "
                      "evaluator's code, its controls, a pack or a tool) moved since it was "
                      "written", out)
        row = doctor_round_row(root)
        self.assertEqual(row["status"], "warn", row)
        self.assertIn("the resolution (an evaluator's code", row["detail"])

    def test_doctor_says_what_status_says(self):
        """`doctor`'s `round` row: ok while current, a warning naming what moved
        once behind, and "edited by hand" over a hand edit — never a FAIL."""
        root = bracket(os.path.join(self.tmp(), "b"))
        row = doctor_round_row(root)
        self.assertEqual(row["status"], "ok", row)
        self.assertEqual(row["detail"], f"round {R}, current with the records, the verdict "
                                        "cache and the design")
        _edit_record(root)
        row = doctor_round_row(root)
        self.assertEqual(row["status"], "warn", row)
        self.assertIn(f"round {R}, behind the records — the records moved", row["detail"])
        path = record_path(root)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace('"C1": "failing"', '"C1": "checked"'))
        row = doctor_round_row(root)
        self.assertEqual(row["status"], "warn", row)
        self.assertIn(f"round {R}, edited by hand", row["detail"])


# --------------------------------------------------------------------------- #
class TheRoundRecordIsByteStable(_env.EnvCase):
    """The same records, cache and design give the same round record, wherever
    the project lives, whatever the words say, under any hash seed; a second
    round writes nothing; and the record is tracked, never ignored."""

    def _fresh_round(self, root: str) -> bytes:
        os.remove(record_path(root))
        code, out, err = main(root, "round")
        self.assertEqual(code, 0, err + out)
        with open(record_path(root), "rb") as fh:
            return fh.read()

    def test_two_paths_and_any_words_give_the_same_bytes(self):
        one = self._fresh_round(bracket(os.path.join(self.tmp(), "b")))
        two_root = bracket(os.path.join(self.tmp(), "elsewhere", "deeper", "b"))
        import test_vocabulary
        with mock.patch.object(report_mod, "HUMAN", test_vocabulary.sentinel_human()):
            two = self._fresh_round(two_root)
        self.assertEqual(one, two)
        before = stamp(two_root)
        self.assertEqual(main(two_root, "round")[0], 0)
        self.assertEqual(stamp(two_root), before, "a second round rewrote the record")
        for word in ("checked", "failing"):
            self.assertIn(f'"{word}"', one.decode("utf-8"))
        self.assertNotIn("Tip sags", one.decode("utf-8"))

    def test_it_is_tracked_in_an_init_project(self):
        root = self.tmp()
        self.assertEqual(_env.git(["init", "-q"], cwd=root).returncode, 0)
        self.assertEqual(main(root, "init")[0], 0)
        proc = _env.git(["check-ignore", "-q", rounds.ROUND_REL], cwd=root)
        self.assertEqual(proc.returncode, 1, "the round record is ignored in a new project")

    def test_two_hash_seeds_give_the_same_bytes(self):
        outs = []
        for seed in ("0", "4242"):
            root = bracket(os.path.join(self.tmp(), f"s{seed}"))
            os.remove(record_path(root))
            proc = _env.nopekit(["round"], cwd=root, env={"PYTHONHASHSEED": seed})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(record_path(root), "rb") as fh:
                outs.append(fh.read())
        self.assertEqual(outs[0], outs[1])


# --------------------------------------------------------------------------- #
def round_escape_problems(source: str, name: str = "_round_run") -> list[str]:
    """Whether the round's body (`_round_run` since phase 6, which `cmd_round`
    and the Stop hook both call) in ``source`` lets a `NopekitError` escape: its
    locked work must sit inside a `try` that catches `NopekitError`."""
    tree = ast.parse(source)
    func = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                 and n.name == name), None)
    if func is None:
        return [f"no {name}"]
    for node in ast.walk(func):
        if not isinstance(node, ast.Try):
            continue
        caught = {getattr(h.type, "id", None) for h in node.handlers}
        locked = any(isinstance(s, ast.With) and "_lock" in ast.dump(s) for s in node.body)
        if "NopekitError" in caught and locked:
            return []
    return [f"{name}'s locked work is not inside a try that catches NopekitError"]


class RoundNeverBlocks(_env.EnvCase):
    """`round` is the Stop hook's future body, and a hook's exit 2 blocks the
    turn: it exits 0 whatever the project says, and 1 only on a crash."""

    def test_a_failing_claim(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        code, out, err = main(root, "round")
        self.assertEqual(code, 0, err)
        self.assertIn("1 failing", out)

    def test_a_held_lock_writes_nothing(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        lock = os.path.join(root, ".nopekit", cli.LOCK_NAME)
        with open(lock, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getppid(), "host": util._HOST, "when": NOW,
                       "command": "nopekit check"}, fh)
        _edit_record(root)
        before = stamp(root)
        code, out, err = main(root, "round")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("round not run: "), out)
        self.assertEqual(stamp(root), before)

    def test_a_model_that_does_not_load(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        self.assertEqual(main(root, "site", "init")[0], 0)
        self.assertEqual(main(root, "site", "build")[0], 0)
        state_json = os.path.join(root, "site", "data", "state.json")
        with open(state_json, "rb") as fh:
            page = fh.read()
        with open(os.path.join(root, "model", "bracket.py"), "a", encoding="utf-8") as fh:
            fh.write("\nthis is not python\n")
        code, out, err = main(root, "round")
        self.assertEqual(code, 0, err)
        self.assertIn("Tier-0 check not run: ", out)
        self.assertIn("Wrote .nopekit/round.json.", out)
        # No page over a model `site build` refuses (review of phase 3a).
        self.assertIn(" Site not built: ", out)
        self.assertNotIn("Site built", out)
        with open(state_json, "rb") as fh:
            self.assertEqual(fh.read(), page, "round rebuilt the page over a broken model")
        with open(record_path(root), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["computed_from"]["design"], "")

    def test_a_pack_that_does_not_import_writes_no_record(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        with open(os.path.join(root, "gates", "zz_broken.py"), "w", encoding="utf-8") as fh:
            fh.write("raise RuntimeError('this gate module does not import')\n")
        before = stamp(root)
        code, out, err = main(root, "round")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("round not run: "), out)
        self.assertEqual(stamp(root), before)

    def test_outside_a_project(self):
        code, out, err = main(self.tmp(), "round")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "round: no nopekit project here — nothing to record.")

    def test_no_refusal_escapes(self):
        with open(os.path.join(SRC, "cli.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(round_escape_problems(source), [])
        planted = ("def _round_run(root):\n    with _round_locked(root, 0):\n"
                   "        return 0\n")
        self.assertTrue(round_escape_problems(planted))


# --------------------------------------------------------------------------- #
def _changes(claims=(), requirements=(), verdicts=(), instructions=(), moved=()):
    return rounds.Changes(True, "", tuple(claims), tuple(requirements), tuple(verdicts),
                          tuple(instructions), None, tuple(moved))


class RoundSummary(_env.EnvCase):
    """`round`'s one paragraph, word for word, and its `--json`."""

    @classmethod
    def setUpClass(cls) -> None:
        tmp = tempfile.mkdtemp(prefix="nopekit-round-")
        cls.addClassCleanup(_env._rmtree, tmp)
        cls.root = bracket(os.path.join(tmp, "b"))
        _l, cls.registry, _m, _p, cls.view, res = resolved(cls.root)
        cls.composed = claims_mod.compositions(cls.view, registry=cls.registry,
                                               stale_gates=res.stale_gates)

    def _say(self, shape, changes, **kw):
        kw.setdefault("check", {"ran": True, "executed": 0, "cached": 6})
        kw.setdefault("site", {"present": False})
        kw.setdefault("wrote", shape != "unchanged")
        return report_mod.round_paragraph(shape, kw.pop("number", 2), changes, view=self.view,
                                          composed=self.composed, schema=1,
                                          path=rounds.ROUND_REL, **kw)

    def test_the_sentences(self):
        flips = [rounds.Flip("C1", "failing", "checked"), rounds.Flip("C8", None, "gap"),
                 rounds.Flip("C7", "gap", None)]
        said = self._say("written", _changes(claims=flips, verdicts=["bracket.deflection"]))
        self.assertEqual(said, "round 2: C1 failing -> checked, C8 new (gap), C7 gone (was "
                               "gap); 1 new verdict (bracket.deflection). 7 claims · 3 checked "
                               "· 1 failing · 2 gaps · 1 pending build. NOT ready, as last "
                               "evaluated: 4 of 7 required claims are unresolved. Tier-0 "
                               "check: 0 run, 6 from the cache. Wrote .nopekit/round.json.")
        said = self._say("unchanged", _changes(), number=3)
        self.assertTrue(said.startswith("round 3 stands: nothing it records moved. "), said)
        self.assertFalse(said.endswith("Wrote .nopekit/round.json."), said)
        said = self._say("written", _changes(moved=["records", "design"]))
        self.assertTrue(said.startswith("round 2: no status moved (the records and the "
                                        "design moved). "), said)
        said = self._say("first", _changes(), number=1,
                         site={"present": True, "built": True, "warnings": 2})
        self.assertIn("round 1: the first round record.", said)
        self.assertIn(" Site built with 2 warnings — `nopekit site build` lists them.", said)
        said = self._say("replaced", rounds.Changes(False, "edited", (), (), (), (), None, ()),
                         check={"ran": False, "why": "the model does not load."})
        self.assertIn("round 2: the last round record was edited by hand, so nothing is "
                      "compared.", said)
        self.assertIn("Tier-0 check not run: the model does not load.", said)

    def test_the_caps_and_a_crash(self):
        flips = [rounds.Flip(f"C{i}", "checked", "failing") for i in range(1, 11)]
        flips[0] = rounds.Flip("C1", "checked", "errored")
        said = self._say("written", _changes(claims=flips,
                                             verdicts=[f"g.{i}" for i in range(6)]))
        self.assertIn("C1 checked -> skipped, errored", said)
        self.assertIn("C8 checked -> failing, and 2 more;", said)
        self.assertNotIn("C9 checked", said)
        self.assertIn("6 new verdicts (g.0, g.1, g.2, g.3, +2 more)", said)

    def test_the_json(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        code, out, err = main(root, "round", "--json")
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertEqual(set(doc), {"round", "written", "path", "previous", "changes",
                                    "counts", "readiness", "check", "site", "summary",
                                    "trace"})
        # Architecture phase 3b: the committed trace is the design's, so no
        # background trace starts.
        self.assertEqual(doc["trace"]["state"], "current")
        self.assertEqual((doc["round"], doc["written"], doc["path"]),
                         (R, False, rounds.ROUND_REL))
        self.assertEqual(set(doc["changes"]), {"compared", "replaced", "claims", "requirements",
                                               "verdicts", "instructions",
                                               "instructions_unrecorded", "readiness",
                                               "moved"})
        self.assertTrue(doc["summary"].startswith(f"round {R} stands"), doc["summary"])

    def test_what_changed_end_to_end(self):
        """`changes()` over a real run: a claim's flip and its requirement's, the
        verdict its re-keyed evaluator filed, a readiness change, and the
        instruction file a model edit moved — in `--json` and in the paragraph.
        What slipped through (review of phase 3a): only the paragraph's first
        flip was held, so `changes()` could drop requirements, new verdicts,
        instructions or readiness and every test stayed green."""
        root = bracket(os.path.join(self.tmp(), "b"))
        path = record_path(root)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        # The last round said ready, sealed as `round` seals it: this round
        # must say readiness moved.
        doc["readiness"] = "ready"
        with open(path, "wb") as fh:
            fh.write(rounds.render(rounds.sealed(doc, 1)))
        _edit_json(os.path.join(root, "claims", "C1.json"),
                   acceptance={"quantity": "tip deflection", "comparator": "<=",
                               "limit": 0.8, "units": "mm"})
        code, out, err = main(root, "round", "--json")
        self.assertEqual(code, 0, err)
        found = json.loads(out)
        changes = found["changes"]
        self.assertIn({"id": "C1", "from": "failing", "to": "checked"}, changes["claims"])
        self.assertIn("R1", {f["id"] for f in changes["requirements"]}, changes)
        r1 = next(f for f in changes["requirements"] if f["id"] == "R1")
        self.assertEqual(r1["from"], "failing", changes)
        self.assertNotEqual(r1["to"], "failing", changes)
        self.assertIn("bracket.deflection", changes["verdicts"])
        self.assertEqual(changes["readiness"], {"from": "ready", "to": "not-ready"})
        self.assertEqual((changes["instructions"], changes["instructions_unrecorded"]),
                         ([], False))
        summary = found["summary"]
        self.assertIn("C1 failing -> checked", summary)
        self.assertIn(f"R1 failing -> {report_mod.status_token_word(r1['to'])}", summary)
        self.assertRegex(summary, r"\d+ new verdicts? \([^)]*bracket\.deflection")
        self.assertNotIn("instructions", summary)
        # A model edit moves its file.
        _edit_model(root)
        code, out, err = main(root, "round", "--json")
        self.assertEqual(code, 0, err)
        found = json.loads(out)
        self.assertEqual(found["changes"]["instructions"], ["instruction:model/bracket.py"])
        self.assertIn("instructions moved (model/bracket.py)", found["summary"])

    def test_a_model_that_stops_loading_moved_no_instruction(self):
        """A model that stops loading records no instruction node; that is not
        every file moving. What slipped through (review of phase 3a): the
        paragraph said "instructions moved (model/bracket.py)" for a model
        whose bytes had not changed — a third-party import missing here."""
        root = bracket(os.path.join(self.tmp(), "b"))
        with open(os.path.join(root, "model", "bracket.py"), encoding="utf-8") as fh:
            text = fh.read()
        with mock.patch.object(cli, "_projection_safe",
                               lambda root_, ledger: (None, None, "no module named 'planted'")):
            code, out, err = main(root, "round", "--json")
        self.assertEqual(code, 0, err)
        found = json.loads(out)
        with open(os.path.join(root, "model", "bracket.py"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), text)
        self.assertEqual(found["changes"]["instructions"], [])
        self.assertTrue(found["changes"]["instructions_unrecorded"])
        self.assertIn("instructions not recorded (the model does not load)", found["summary"])
        self.assertNotIn("instructions moved", found["summary"])


# --------------------------------------------------------------------------- #
_COSTLIER_GATE = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_round.py: an evaluator a tier-0 round must never run."""
from nopekit.gates import gate
from nopekit.models import NegativeControl, Tier, Verdict


@gate(id="bracket.costlier", title="a tier-1 evaluator", claims=["costlier"],
      tier=Tier.BUILD,
      negative_control=NegativeControl(fixture="selftest/bad_configs.py:quarter_thickness",
                                       note="planted"))
def costlier(ctx):
    """The tip deflection against C1's limit, as a slower build would."""
    value = float(ctx.params["deflection"])
    return Verdict(gate="bracket.costlier", passed=value <= 0.5, measured=value,
                   limit=0.5, units="mm")
'''


class RoundRunsTierZero(_env.EnvCase):
    """`round` runs a tier-0 check exactly as `check --tier 0` does: a costlier
    evaluator is neither run nor filed, and the counts are the same. What
    slipped through (review of phase 3a): every bracket gate is tier 0, so a
    round that ran every tier — FEA on every turn, once it is the Stop hook —
    passed every test."""

    def _project(self) -> str:
        root = bracket(os.path.join(self.tmp(), "b"))
        with open(os.path.join(root, "gates", "zz_costlier.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(_COSTLIER_GATE)
        return root

    def _filed(self, root: str) -> list[str]:
        folder = os.path.join(root, ".nopekit", "verdicts", "bracket.costlier")
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def test_round_never_runs_a_costlier_evaluator(self):
        root = self._project()
        code, out, err = main(root, "round", "--json")
        self.assertEqual(code, 0, err)
        found = json.loads(out)
        self.assertTrue(found["check"]["ran"], found)
        self.assertEqual(self._filed(root), [])
        twin = self._project()
        code, out, err = main(twin, "check", "--tier", "0", "--json")
        self.assertIn(code, (0, 1), err)
        self.assertEqual(self._filed(twin), [])
        checked = json.loads(out)
        counts = checked.get("counts") or checked.get("summary", {}).get("counts") or {}
        self.assertEqual((found["check"]["executed"], found["check"]["cached"]),
                         (int(counts.get("executed", 0)), int(counts.get("cached", 0))),
                         (found["check"], counts))
        # The control: a check that runs every tier files the costlier entry.
        code, _out, err = main(twin, "check", "--tier", "1")
        self.assertIn(code, (0, 1), err)
        self.assertTrue(self._filed(twin), "the planted tier-1 evaluator never ran at tier 1")


# --------------------------------------------------------------------------- #
# Architecture phase 6: the round's commit and the Stop hook (SPEC 24 with §0.3)
# --------------------------------------------------------------------------- #
IDENTITY = _env.IDENTITY
IDENT = f"{IDENTITY['GIT_AUTHOR_NAME']} <{IDENTITY['GIT_AUTHOR_EMAIL']}>"
LISTED = [{"name": IDENTITY["GIT_AUTHOR_NAME"], "email": IDENTITY["GIT_AUTHOR_EMAIL"]}]


class _InProcessGit(_env.EnvCase):
    """`round --commit` runs in THIS process, so its git calls inherit
    `os.environ`: pinned here as `test_vcs` pins it — no global or system git
    config, no user excludes, no inherited `GIT_*` or agent marker — with the
    test identity set, so the dev box's own name never decides a test."""

    identity = True

    def setUp(self):
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for name in list(os.environ):
            if name.startswith(("GIT_", "CLAUDE")) or name in ("EMAIL", "XDG_CONFIG_HOME",
                                                               "AI_AGENT"):
                del os.environ[name]
        home = self.tmp()
        os.environ.update({"HOME": home, "XDG_CONFIG_HOME": home,
                           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        if self.identity:
            os.environ.update(IDENTITY)

    def git(self, root: str, *argv: str, ok: bool = True) -> str:
        proc = _env.git(list(argv), cwd=root, identity=True)
        if ok:
            self.assertEqual(proc.returncode, 0, f"git {argv}: {proc.stderr}")
        return proc.stdout

    def repo(self) -> str:
        root = self.tmp()
        self.git(root, "-c", "init.defaultBranch=main", "init", "-q")
        return root

    def project_in(self, repo: str, rel: str = "sub/proj", *, people=LISTED) -> str:
        """The bracket at ``repo/rel``, listing ``people``, committed as the base."""
        project = bracket(os.path.join(repo, *rel.split("/")))
        _edit_json(os.path.join(project, ".nopekit", "project.json"), people=list(people))
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "base")
        return project

    def head(self, root: str) -> str:
        return self.git(root, "rev-parse", "HEAD").strip()

    def committed(self, repo: str) -> list[str]:
        """HEAD's changed paths, repo-relative (deletions included)."""
        out = self.git(repo, "show", "--name-only", "--format=", "--no-renames", "HEAD")
        return [line for line in out.splitlines() if line.strip()]

    def cached(self, repo: str) -> str:
        return self.git(repo, "diff", "--cached", "--name-only")


def commit_problems(files: list[str], prefix: str, ignored: frozenset = frozenset()) -> list[str]:
    """Every path in a round's commit (``files``, repo-relative) that is outside
    ``prefix`` (the project's directory, repo-relative, with a trailing slash),
    whose name looks like a secret, or that git ignores (``ignored``, git's own
    answer)."""
    out = []
    for path in files:
        if not path.startswith(prefix):
            out.append(f"{path}: outside the project's directory")
        elif rounds.secret_name(path[len(prefix):]):
            out.append(f"{path}: named like a secret")
        if path in ignored:
            out.append(f"{path}: ignored by git")
    return out


def _write(root: str, rel: str, text: str) -> str:
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


#: Files named like secrets, planted inside the project: a dotenv file and one of
#: its variants, a private key, an SSH key, an OAuth client file and a cloud
#: credentials file. Named here, not read from `rounds.ROUND_SECRET_NAMES`, so a
#: pattern dropped there is a file committed here.
PLANTED_SECRETS = (".env", ".env.local", "keys/deploy.pem", "id_ed25519",
                   "config/client_secret.json", "aws/credentials")


def _record_bytes(root: str) -> dict[str, bytes]:
    from nopekit import store
    out = {}
    for kind in store.RECORD_DIRS:
        base = os.path.join(root, kind)
        for directory, _dirs, names in os.walk(base):
            for name in names:
                path = os.path.join(directory, name)
                with open(path, "rb") as fh:
                    out[os.path.relpath(path, root)] = fh.read()
    with open(os.path.join(root, ".nopekit", "project.json"), "rb") as fh:
        out[".nopekit/project.json"] = fh.read()
    return out


class TheRoundCommitStagesOnlyTheProjectsPaths(_InProcessGit):
    """Invariant 8: `round --commit` writes no record, and commits every file
    inside the project's directory that git does not ignore — less a directory,
    a file over the cap and a name that looks like a secret, each named instead —
    and never a person's unrelated work beside it, never what they staged
    themselves, never pushed. Designed against in phase 6: a hook that ran
    `git add -A` would commit a person's unrelated work as "round N". What
    slipped through the acceptance run after phase 3b: the staging list was
    closed, and the agent's own `bom/bom.json`, which its sourcing evaluators
    read, was "not staged, outside the project's paths" at the end of every
    round."""

    def test_every_file_git_does_not_ignore_inside_its_directory(self):
        repo = self.repo()
        bare = self.tmp()
        self.git(bare, "init", "-q", "--bare")
        with open(os.path.join(repo, "top.txt"), "w", encoding="utf-8") as fh:
            fh.write("the person's own file\n")
        project = self.project_in(repo)
        self.git(repo, "remote", "add", "origin", bare)
        self.git(repo, "push", "-q", "origin", "main")
        remote = self.git(repo, "ls-remote", "origin")
        # The person's work beside the project: modified, staged, untracked.
        with open(os.path.join(repo, "top.txt"), "a", encoding="utf-8") as fh:
            fh.write("an edit in progress\n")
        with open(os.path.join(repo, "prestaged.txt"), "w", encoding="utf-8") as fh:
            fh.write("staged by the person\n")
        self.git(repo, "add", "prestaged.txt")
        with open(os.path.join(repo, "stray.txt"), "w", encoding="utf-8") as fh:
            fh.write("untracked\n")
        # Inside the project: a claim edit, a deleted record, the agent's own
        # files in directories nopekit never names (the acceptance run's parts
        # list and a generator's mesh), a note, a scratch file the project's
        # .gitignore ignores, and files named like secrets.
        _write(project, "notes.txt", "the person's notes\n")
        _write(project, "bom/bom.json", '{"lines": []}\n')
        _write(project, "build/print/frame.stl", "solid frame\nendsolid frame\n")
        with open(os.path.join(project, ".gitignore"), "a", encoding="utf-8") as fh:
            fh.write("/scratch/\n")
        _write(project, "scratch/run.log", "a scratch file\n")
        for rel in PLANTED_SECRETS:
            _write(project, rel, "KEY=not-for-git\n")
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="edited this round")
        os.remove(os.path.join(project, "decisions", "print-in-petg.json"))
        records = _record_bytes(project)
        base = self.head(repo)

        code, out, err = main(project, "round", "--commit")
        self.assertEqual(code, 0, err)
        self.assertNotEqual(self.head(repo), base, out)
        files = self.committed(repo)
        ignored = frozenset(self.git(repo, "check-ignore", "--no-index", *files,
                                     ok=False).split())
        self.assertEqual(commit_problems(files, "sub/proj/", ignored), [], files)
        for rel in ("claims/C7.json", "decisions/print-in-petg.json", "notes.txt",
                    "bom/bom.json", "build/print/frame.stl", ".gitignore"):
            self.assertIn(f"sub/proj/{rel}", files)
        self.assertNotIn("sub/proj/scratch/run.log", files)
        for rel in PLANTED_SECRETS:
            self.assertNotIn(f"sub/proj/{rel}", files)
        self.assertIn(" Not staged, named like a secret: .env, .env.local, aws/credentials, "
                      "and 3 more.", out)
        self.assertNotIn("outside the project", out)
        body = " ".join(self.git(repo, "log", "-1", "--format=%B").split())
        self.assertIn("Not committed, named like a secret: " + ", ".join(sorted(PLANTED_SECRETS))
                      + ".", body)
        self.assertIn(f"as {IDENT}", out)
        # The person's work is as they left it.
        self.assertEqual(self.cached(repo).split(), ["prestaged.txt"])
        status = self.git(repo, "status", "--porcelain", "--untracked-files=all")
        self.assertIn(" M top.txt", status)
        self.assertIn("?? stray.txt", status)
        self.assertIn("?? sub/proj/.env", status)
        self.assertEqual(_record_bytes(project), records, "the round's commit wrote a record")
        self.assertEqual(self.git(repo, "ls-remote", "origin"), remote, "a round pushed")
        # Nothing else changed: no commit, and what it never stages is named
        # without saying that nothing changed.
        head = self.head(repo)
        code, out, err = main(project, "round", "--commit")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.head(repo), head, out)
        self.assertIn(" Nothing to commit but what a round never stages. Not staged, named "
                      "like a secret: .env,", out)

    def test_nothing_staged_never_reads_as_nothing_changed(self):
        """The acceptance run after phase 3b: with every file a round stages, a
        round that stages nothing beside a file it names must not say that no
        file changed — in process, over `report.round_commit_sentence` alone."""
        empty = cli._no_commit("nothing-changed")
        self.assertEqual(report_mod.round_commit_sentence(empty),
                         " Nothing to commit: no file in the project's directory changed.")
        for plan, said in (
                (rounds.StagePlan((), (), (), (".env",)),
                 " Not staged, named like a secret: .env."),
                (rounds.StagePlan((), (), (("scan.bin", 60 << 20),), ()),
                 " Not staged, over 50 MiB: scan.bin (60 MiB)."),
                (rounds.StagePlan((), ("lib/",), (), ()), " Not staged, not a file: lib/.")):
            self.assertEqual(
                report_mod.round_commit_sentence(cli._no_commit("nothing-changed", plan=plan)),
                " Nothing to commit but what a round never stages." + said)

    def test_an_untracked_nested_repository_is_named_never_committed(self):
        """Review of phase 6: `vcs.changed_paths` stripped the `/` git lists an
        untracked nested repository with, the path check refused only a name
        ending in `/`, and `generators/cadlib`, a clone, was committed as a
        160000 gitlink — a submodule pointer nobody chose."""
        repo = self.repo()
        project = self.project_in(repo)
        nested = os.path.join(project, "generators", "cadlib")
        os.makedirs(nested)
        self.git(nested, "init", "-q")
        with open(os.path.join(nested, "lib.py"), "w", encoding="utf-8") as fh:
            fh.write("X = 1\n")
        self.git(nested, "add", "lib.py")
        self.git(nested, "commit", "-q", "-m", "a library")
        from nopekit import vcs
        self.assertIn("generators/cadlib/", vcs.changed_paths(project))
        # Slash or no slash, a directory is named, never staged.
        for listed in ("generators/cadlib/", "generators/cadlib"):
            plan = rounds.stage_plan(project, [listed])
            self.assertEqual((plan.staged, plan.not_files), ((), ("generators/cadlib/",)),
                             listed)
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="a change to commit")
        code, out, err = main(project, "round", "--commit")
        self.assertEqual(code, 0, err)
        self.assertIn("Committed ", out)
        self.assertIn(" Not staged, not a file: generators/cadlib/.", out)
        files = self.committed(repo)
        self.assertEqual(commit_problems(files, "sub/proj/"), [], files)
        self.assertNotIn("sub/proj/generators/cadlib", files)
        modes = self.git(repo, "ls-files", "-s").split("\n")
        self.assertEqual([m for m in modes if m.startswith("160000")], [], "a gitlink")

    def test_the_checker_catches_what_it_forbids(self):
        self.assertEqual(commit_problems(["sub/proj/claims/C1.json",
                                          "sub/proj/.nopekit/round.json",
                                          "sub/proj/bom/bom.json", "sub/proj/notes.txt"],
                                         "sub/proj/"), [])
        self.assertEqual(commit_problems(["top.txt"], "sub/proj/"),
                         ["top.txt: outside the project's directory"])
        self.assertEqual(commit_problems(["sub/proj/.env"], "sub/proj/"),
                         ["sub/proj/.env: named like a secret"])
        self.assertEqual(commit_problems(["sub/proj/site/data/build.json"], "sub/proj/",
                                         frozenset({"sub/proj/site/data/build.json"})),
                         ["sub/proj/site/data/build.json: ignored by git"])

    def test_names_that_look_like_secrets(self):
        """The list is small and pinned whole here, so a pattern dropped or
        added is a red row, never a quiet change in what a turn's end commits."""
        self.assertEqual(rounds.ROUND_SECRET_NAMES,
                         (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*",
                          "id_dsa*", "id_ecdsa*", "id_ed25519*", "*credentials*", "*secret*"))
        for rel in (*PLANTED_SECRETS, "config/.ENV.production", "tls/server.key",
                    "certs/bundle.p12", "certs/Bundle.PFX", "id_rsa", "id_rsa.pub",
                    "ssh/id_dsa", "id_ecdsa.pub", "credentials.json", "Secrets.yaml",
                    "secrets/api.txt", ".env/lib/site.py"):
            self.assertIsNotNone(rounds.secret_name(rel), rel)
        for rel in ("claims/C1.json", "bom/bom.json", "build/print/frame.stl", "README.md",
                    ".gitignore", rounds.ROUND_REL, "model/keyboard.py", "inputs/env/notes.txt",
                    "environment.yml", "keys.md", "pem_notes.txt", "ids.csv", "envelope.dxf",
                    "site/content/strength.md", "",
                    # nopekit names these from an evaluator's id: a gate called
                    # `cad.key` keeps its verdicts in the history.
                    ".nopekit/verdicts/cad.key/0a1b2c3d4e5f6a7b-0a1b2c3d.json",
                    ".nopekit/trace/secret.json"):
            self.assertIsNone(rounds.secret_name(rel), rel)
        self.assertIsNotNone(rounds.secret_name(".nopekit/packs/p/.env"))

    def test_the_plan_names_what_it_never_stages(self):
        """`rounds.stage_plan` alone, on disk with no git: a deletion is staged
        whatever its name (it publishes no byte); a directory, a path that leaves
        the project, a secret's name and a file over the cap are named."""
        root = self.tmp()
        for rel, size in (("bom/bom.json", 2), ("build/frame.stl", 64), (".env", 5),
                          ("keys/deploy.pem", 3), ("inputs/scan.bin", 2048)):
            _write(root, rel, "x" * size)
        os.makedirs(os.path.join(root, "generators", "cadlib"))
        changed = ["bom/bom.json", "build/frame.stl", ".env", "keys/deploy.pem",
                   "inputs/scan.bin", "generators/cadlib", "generators/lib/", "gone.txt",
                   ".env.old", "../outside.txt", "/abs.txt"]
        with mock.patch.object(rounds, "ROUND_MAX_BYTES", 1024):
            plan = rounds.stage_plan(root, changed)
        self.assertEqual(plan.staged, (".env.old", "bom/bom.json", "build/frame.stl", "gone.txt"))
        self.assertEqual(plan.secret, (".env", "keys/deploy.pem"))
        self.assertEqual(plan.too_large, (("inputs/scan.bin", 2048),))
        self.assertEqual(plan.not_files, ("../outside.txt", "/abs.txt", "generators/cadlib/",
                                          "generators/lib/"))


class RoundsCommitOnlyAsAListedPerson(_InProcessGit):
    """SPEC §0.3: a round commits only when git's name AND email are one of
    `people`; every refusal says why in one sentence and leaves the index as it
    was."""

    def _project(self, *, people=LISTED) -> tuple[str, str]:
        repo = self.repo()
        return repo, self.project_in(repo, people=people)

    def _refused(self, repo: str, project: str, needle: str) -> str:
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="a change to commit")
        head, index = self.head(repo), self.cached(repo)
        code, out, err = main(project, "round", "--commit")
        self.assertEqual(code, 0, err)
        self.assertIn(needle, out)
        self.assertEqual(self.head(repo), head, out)
        self.assertEqual(self.cached(repo), index, "a refusal touched the index")
        return out

    def test_a_listed_person_commits_as_themselves(self):
        repo, project = self._project()
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="a change to commit")
        code, out, err = main(project, "round", "--commit", "--json")
        self.assertEqual(code, 0, err)
        found = json.loads(out)["commit"]
        self.assertEqual((found["made"], found["why"], found["who"]), (True, "committed", IDENT))
        self.assertEqual(found["oid"], self.head(repo))
        author = self.git(repo, "log", "-1", "--format=%an <%ae>").strip()
        self.assertEqual(author, IDENT)

    def test_an_email_in_another_case_is_the_same_person(self):
        repo, project = self._project(people=[{"name": IDENTITY["GIT_AUTHOR_NAME"],
                                               "email": IDENTITY["GIT_AUTHOR_EMAIL"].upper()}])
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="a change to commit")
        head = self.head(repo)
        self.assertEqual(main(project, "round", "--commit")[0], 0)
        self.assertNotEqual(self.head(repo), head)

    def test_another_identity(self):
        repo, project = self._project()
        os.environ["GIT_AUTHOR_NAME"] = "Someone Else"
        out = self._refused(repo, project, "Not committed: git's identity here, Someone Else "
                                           "<tests@nopekit.invalid>, is not in")
        self.assertNotIn("Committed ", out)

    def test_a_listed_name_at_another_address(self):
        repo, project = self._project()
        os.environ["GIT_AUTHOR_EMAIL"] = "elsewhere@nopekit.invalid"
        self._refused(repo, project, "is not in .nopekit/project.json's people")

    def test_a_committer_people_does_not_list(self):
        """Review of phase 6: the gate read the author alone, and a committer
        from `GIT_COMMITTER_*` (a CI runner's, a wrapper's) reached the commit."""
        repo, project = self._project()
        os.environ["GIT_COMMITTER_NAME"] = "Mallory"
        os.environ["GIT_COMMITTER_EMAIL"] = "m@evil.example"
        out = self._refused(repo, project, "Not committed: git's committer here, Mallory "
                                           "<m@evil.example> (committer.name and "
                                           "committer.email, or GIT_COMMITTER_*), is not in")
        self.assertNotIn("Committed ", out)

    def test_git_does_not_list_the_changes(self):
        from nopekit import vcs
        repo, project = self._project()
        with mock.patch.object(vcs, "changed_paths", return_value=None):
            out = self._refused(repo, project, " Not committed: git did not answer when asked "
                                               "which files changed.")
        self.assertNotIn("()", out)

    def test_git_does_not_stage(self):
        from nopekit import vcs
        repo, project = self._project()
        with mock.patch.object(vcs, "add",
                               return_value=vcs.Wrote(False, None, "refused: planted")):
            self._refused(repo, project, " Not committed: git did not stage the round's "
                                         "files (refused: planted).")

    def test_a_commit_that_does_not_finish(self):
        """The CLI's word for `vcs.commit`'s timeout: a hung gpg pinentry or commit
        hook reads "did not finish", never "git refused", and the round's files
        stay staged as the sentence says."""
        from nopekit import vcs
        repo, project = self._project()
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="a change to commit")
        head = self.head(repo)
        with mock.patch.object(vcs, "commit", return_value=vcs.Wrote(False, None, "timeout")):
            code, out, err = main(project, "round", "--commit")
        self.assertEqual(code, 0, err)
        self.assertIn(f" Not committed: git commit did not finish in {vcs.VCS_WRITE_TIMEOUT_S} "
                      f"s (a slow commit hook?); the round's files are left staged.", out)
        self.assertNotIn("refused", out)
        self.assertEqual(self.head(repo), head)
        self.assertIn("sub/proj/claims/C7.json", self.cached(repo))

    def test_no_identity(self):
        repo, project = self._project()
        for name in IDENTITY:
            os.environ.pop(name, None)
        self._refused(repo, project, "Not committed: git has no user.name and user.email")

    def test_no_people(self):
        repo, project = self._project(people=[])
        self._refused(repo, project, "Not committed: .nopekit/project.json lists no people")

    def test_detached_head(self):
        repo, project = self._project()
        self.git(repo, "checkout", "-q", "--detach")
        self._refused(repo, project, "Not committed: HEAD is detached")

    def test_a_merge_in_progress(self):
        repo, project = self._project()
        with open(os.path.join(repo, ".git", "MERGE_HEAD"), "w", encoding="utf-8") as fh:
            fh.write(self.head(repo) + "\n")
        self._refused(repo, project, "Not committed: a merge is in progress")

    def test_a_newer_round_record(self):
        repo, project = self._project()
        path = record_path(project)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["schema"] = rounds.SCHEMA + 1
        doc["self"] = rounds.self_digest(doc)
        with open(path, "wb") as fh:
            fh.write(rounds.render(doc))
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "a newer record")
        self._refused(repo, project, "Not committed: the round record was written by a newer")

    def test_no_repository(self):
        project = bracket(os.path.join(self.tmp(), "b"))
        _edit_json(os.path.join(project, ".nopekit", "project.json"), people=LISTED)
        code, out, err = main(project, "round", "--commit")
        self.assertEqual(code, 0, err)
        self.assertIn("Not committed: the project is in no git repository", out)


class TheFirstSessionCommits(_InProcessGit):
    """SPEC §7's criterion 4 on the path a newcomer takes (review of phase 6):
    `init` where no repository is, so the first round is the repository's first
    commit on an unborn branch, at its top level; then the Stop hook. Every other
    commit row runs in a subdirectory over a base commit."""

    def test_init_then_rounds_on_an_unborn_branch(self):
        root = os.path.join(self.tmp(), "fresh")
        code, out, err = main(root, "init", "--name", "fresh")
        self.assertEqual(code, 0, err)
        self.assertIn("a new git repository", out)
        self.assertIsNone(self.git(root, "rev-parse", "--verify", "--quiet", "HEAD",
                                   ok=False).strip() or None)
        code, out, err = main(root, "round", "--commit")
        self.assertEqual(code, 0, err)
        self.assertIn("Committed ", out)
        self.assertEqual(self.git(root, "rev-list", "--count", "HEAD").strip(), "1")
        self.assertEqual(self.git(root, "log", "-1", "--format=%s").strip(),
                         "round 1: the first round record")
        files = self.committed(root)
        for rel in (".gitignore", ".gitattributes", "README.md", ".nopekit/project.json",
                    rounds.ROUND_REL, ".nopekit/.gitignore"):
            self.assertIn(rel, files)
        self.assertEqual(commit_problems(files, ""), [], files)
        # The editor's launch entry init wrote is ignored, never named on a round.
        self.assertTrue(os.path.isfile(os.path.join(root, ".vscode", "launch.json")))
        self.assertNotIn(".vscode/launch.json", files)
        self.assertNotIn("Not staged", out)
        self.assertEqual(self.git(root, "status", "--porcelain", "--untracked-files=all"), "")
        # The Stop hook, as Claude Code runs it, over the person's first requirement.
        os.makedirs(os.path.join(root, "requirements"), exist_ok=True)
        with open(os.path.join(root, "requirements", "R1.json"), "w", encoding="utf-8") as fh:
            json.dump({"statement": "It holds the pump", "source": "interview"}, fh)
        proc = _env.run([sys.executable, os.path.join(_env.REPO, "hooks", "stop.py")],
                        cwd=root, identity=True, input=json.dumps({"cwd": root}))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.count("\n"), 1, proc.stdout)
        said = json.loads(proc.stdout)["systemMessage"]
        self.assertIn("Committed ", said)
        self.assertEqual(self.git(root, "rev-list", "--count", "HEAD").strip(), "2")
        self.assertIn("requirements/R1.json", self.committed(root))
        self.assertEqual(self.git(root, "status", "--porcelain", "--untracked-files=all"), "")

    def test_a_repository_that_ignores_the_project(self):
        """Review of phase 6: under a directory its enclosing repository ignores,
        no change is ever listed, and every round said "Nothing to commit"."""
        repo = self.repo()
        with open(os.path.join(repo, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write("*\n")
        self.git(repo, "add", "-f", ".gitignore")
        self.git(repo, "commit", "-q", "-m", "ignore everything")
        root = os.path.join(repo, "work", "proj")
        code, out, err = main(root, "init", "--name", "proj")
        self.assertEqual(code, 0, err)
        self.assertIn("git: the enclosing repository ignores this directory", out)
        self.assertFalse(os.path.exists(os.path.join(root, ".git")))
        os.makedirs(os.path.join(root, "requirements"), exist_ok=True)
        with open(os.path.join(root, "requirements", "R1.json"), "w", encoding="utf-8") as fh:
            json.dump({"statement": "It holds the pump", "source": "interview"}, fh)
        head = self.head(repo)
        code, out, err = main(root, "round", "--commit")
        self.assertEqual(code, 0, err)
        self.assertIn(" Not committed: the enclosing git repository ignores this project", out)
        self.assertNotIn("Nothing to commit", out)
        self.assertEqual(self.head(repo), head)


class TheRoundCommitIsHistoryNotAGate(_InProcessGit):
    """SPEC 24: the commit is history, not a quality gate — a failing claim still
    commits — and it says what changed in git's own shape: `round N: …` within
    72 characters, a body, no trailer, no attribution. A refusing commit hook is
    an answer, never bypassed."""

    def test_a_failing_claim_still_commits_and_the_message(self):
        repo = self.repo()
        project = self.project_in(repo)
        # Two rounds nobody committed, then a committed one that holds them.
        for limit in (0.8, 0.9):
            _edit_json(os.path.join(project, "claims", "C1.json"),
                       acceptance={"quantity": "tip deflection", "comparator": "<=",
                                   "limit": limit, "units": "mm"})
            self.assertEqual(main(project, "round")[0], 0)
        _edit_json(os.path.join(project, "claims", "C1.json"),
                   acceptance={"quantity": "tip deflection", "comparator": "<=",
                               "limit": 0.5, "units": "mm"})
        code, out, err = main(project, "round", "--commit")
        self.assertEqual(code, 0, err)
        self.assertIn("1 failing", out)
        self.assertIn("Committed ", out)
        subject = self.git(repo, "log", "-1", "--format=%s").strip()
        message = self.git(repo, "log", "-1", "--format=%B")
        self.assertRegex(subject, rf"^round {R + 3}: ")
        self.assertLessEqual(len(subject), report_mod.COMMIT_SUBJECT_MAX)
        # The flips do not fit in 72 characters: the subject counts them.
        self.assertEqual(subject, f"round {R + 3}: 2 statuses moved; 1 new verdict")
        body = message.split("\n", 2)[2]
        self.assertIn("1 failing", " ".join(body.split()))
        self.assertIn("C1 checked -> failing", " ".join(body.split()))
        self.assertRegex(" ".join(body.split()), r"\d+ files? committed: .*claims \(1\)")
        self.assertIn(f"Rounds {R + 1} to {R + 2} were not committed", " ".join(body.split()))
        for word in ("Co-Authored-By", "Generated with", "Signed-off-by"):
            self.assertNotIn(word, message)
        proc = _env.run(["git", "interpret-trailers", "--parse"], cwd=repo, input=message)
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, ""), proc.stdout)

    def test_a_refusing_commit_hook_is_never_bypassed(self):
        repo = self.repo()
        project = self.project_in(repo)
        marker = os.path.join(self.tmp(), "hook-ran")
        hook = os.path.join(repo, ".git", "hooks", "pre-commit")
        with open(hook, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\ntouch '{marker}'\necho 'planted hook says no' >&2\nexit 1\n")
        os.chmod(hook, 0o755)
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="a change to commit")
        head = self.head(repo)
        code, out, err = main(project, "round", "--commit")
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(marker), "the commit hook did not run")
        self.assertEqual(self.head(repo), head)
        self.assertIn("Not committed: git refused the commit (refused: planted hook says no); "
                      "the round's files are left staged.", out)
        self.assertIn("sub/proj/claims/C7.json", self.cached(repo))

    def test_a_file_over_the_cap_is_named_not_staged(self):
        repo = self.repo()
        project = self.project_in(repo)
        os.makedirs(os.path.join(project, "inputs", "cad"), exist_ok=True)
        with open(os.path.join(project, "inputs", "cad", "scan.bin"), "wb") as fh:
            fh.write(b"\0" * (2 << 20))
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="a change to commit")
        with mock.patch.object(rounds, "ROUND_MAX_BYTES", 1 << 20):
            code, out, err = main(project, "round", "--commit")
        self.assertEqual(code, 0, err)
        self.assertIn("Not staged, over 50 MiB: inputs/cad/scan.bin (2 MiB).", out)
        self.assertNotIn("sub/proj/inputs/cad/scan.bin", self.committed(repo))
        self.assertIn("sub/proj/claims/C7.json", self.committed(repo))


class TheStopHookNeverBlocks(_env.EnvCase):
    """The Stop hook (`round --hook`): one JSON object on stdout, `{}` when there
    is nothing to say, always exit 0 — never 2 and never `"decision": "block"`,
    which Claude Code reads as "continue the turn"."""

    def _hook(self, cwd: str, payload: Any = None, *, raw: str | None = None,
              env: dict | None = None, codes: list | None = None):
        text = raw if raw is not None else json.dumps(payload or {})
        proc = _env.nopekit(["round", "--hook"], cwd=cwd, input=text, identity=True, env=env)
        if codes is not None:
            codes.append(proc.returncode)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.endswith("\n"), proc.stdout)
        self.assertEqual(proc.stdout.count("\n"), 1, proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertIsInstance(doc, dict)
        self.assertNotIn("decision", doc)
        self.assertLessEqual(set(doc), {"systemMessage"})
        return doc

    def _project(self) -> str:
        repo = self.tmp()
        project = bracket(os.path.join(repo, "proj"))
        _edit_json(os.path.join(project, ".nopekit", "project.json"), people=LISTED)
        for argv in (["-c", "init.defaultBranch=main", "init", "-q"], ["add", "-A"],
                     ["commit", "-q", "-m", "base"]):
            self.assertEqual(_env.git(argv, cwd=repo, identity=True).returncode, 0)
        return project

    def test_the_matrix(self):
        codes: list = []
        outside = self.tmp()
        self.assertEqual(self._hook(outside, {"cwd": outside}, codes=codes), {})
        self.assertEqual(self._hook(outside, raw="not json at all", codes=codes), {})
        self.assertEqual(self._hook(outside, raw="", codes=codes), {})
        project = self._project()
        # The payload's cwd, deep inside the project, from a process elsewhere.
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="a change to commit")
        doc = self._hook(outside, {"cwd": os.path.join(project, "claims"),
                                   "stop_hook_active": True, "session_id": "s"}, codes=codes)
        said = doc["systemMessage"]
        self.assertTrue(said.startswith("nopekit round "), said)
        self.assertIn("1 failing", said)
        self.assertIn("Committed ", said)
        # Nothing happened since: quiet.
        self.assertEqual(self._hook(outside, {"cwd": project}, codes=codes), {})
        # $CLAUDE_PROJECT_DIR when the payload names no project.
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="another change")
        doc = self._hook(outside, {}, env={"CLAUDE_PROJECT_DIR": project}, codes=codes)
        self.assertIn("Committed ", doc["systemMessage"])
        # Garbage on stdin, from inside the project: the process's cwd.
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="a third change")
        doc = self._hook(project, raw="{{{", codes=codes)
        self.assertIn("Committed ", doc["systemMessage"])
        self.assertNotIn(2, codes)

    def test_cursors_payload_and_its_launcher(self):
        """Cursor's stop payload names no ``cwd``: its project is in
        ``workspace_roots`` or ``$CURSOR_PROJECT_DIR``, and a plugin hook's cwd
        there is not reliably the project's. Through ``hooks/stop.py --cursor``
        stdout is ``{}`` — Cursor's one key would start another turn — and the
        paragraph is on stderr."""
        codes: list = []
        outside = self.tmp()
        project = self._project()
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="a change to commit")
        doc = self._hook(outside, {"workspace_roots": [outside, project], "status": "completed",
                                   "loop_count": 0}, codes=codes)
        self.assertIn("Committed ", doc["systemMessage"])
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="another change")
        doc = self._hook(outside, {}, env={"CURSOR_PROJECT_DIR": project}, codes=codes)
        self.assertIn("Committed ", doc["systemMessage"])
        self.assertNotIn(2, codes)
        _edit_json(os.path.join(project, "claims", "C7.json"), rationale="a third change")
        proc = _env.run([sys.executable, os.path.join(_env.REPO, "hooks", "stop.py"), "--cursor"],
                        cwd=outside, input=json.dumps({"workspace_roots": [project]}),
                        identity=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "{}\n")
        self.assertIn("nopekit round ", proc.stderr)
        self.assertIn("Committed ", proc.stderr)

    def test_a_write_to_file_descriptor_1_reaches_no_stdout(self):
        """`_quiet_fd(1)`: a model or a solver writes to the inherited fd 1, below
        `redirect_stdout`, and one stray line before the JSON object would keep
        the systemMessage from the person. Planted: a model that writes there
        each time it loads."""
        project = self._project()
        with open(os.path.join(project, "model", "bracket.py"), "a", encoding="utf-8") as fh:
            fh.write("\nimport os as _planted_os\n"
                     "_planted_os.write(1, b'stray line from the model\\n')\n")
        doc = self._hook(project, {"cwd": project})
        self.assertIn("Committed ", doc["systemMessage"])

    def test_a_standing_misconfiguration_is_said_every_turn(self):
        project = self._project()
        _edit_json(os.path.join(project, ".nopekit", "project.json"), people=[])
        for _ in range(2):
            doc = self._hook(project, {"cwd": project})
            self.assertIn("lists no people", doc["systemMessage"])

    def test_board_text_behind_the_results_is_named(self):
        project = self._project()
        _edit_json(os.path.join(project, "claims", "C1.json"),
                   acceptance={"quantity": "tip deflection", "comparator": "<=",
                               "limit": 0.8, "units": "mm"})
        doc = self._hook(project, {"cwd": project})
        self.assertRegex(doc["systemMessage"],
                         r"Board text written before the latest results: [^—]*\bstrength\b")
        self.assertIn("nopekit site stamp <board>", doc["systemMessage"])


class TheStopHookInProcess(_InProcessGit):
    """The hook's crash and its held lock, planted in process."""

    def _main_hook(self, root: str, payload: str) -> tuple[int, dict]:
        out = io.StringIO()
        stdin = io.TextIOWrapper(io.BytesIO(payload.encode("utf-8")), encoding="utf-8")
        with mock.patch.object(cli.sys, "stdin", stdin), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["round", "--hook"])
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, out.getvalue())
        return code, json.loads(lines[0])

    def test_a_crash_is_one_line_and_exit_zero(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        for exc in (RuntimeError("planted crash\nsecond line"), SystemExit(3)):
            with self.subTest(exc=type(exc).__name__), \
                    mock.patch.object(cli, "_round_run", side_effect=exc):
                code, doc = self._main_hook(root, json.dumps({"cwd": root}))
            self.assertEqual(code, 0)
            said = doc["systemMessage"]
            self.assertTrue(said.startswith("nopekit round crashed ("), said)
            self.assertNotIn("second line", said)

    def test_a_held_lock_is_waited_on_then_said(self):
        root = bracket(os.path.join(self.tmp(), "b"))
        lock = os.path.join(root, ".nopekit", cli.LOCK_NAME)
        with open(lock, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getppid(), "host": util._HOST, "when": NOW,
                       "command": "nopekit check"}, fh)
        before = stamp(root)
        _edit_record(root)
        with mock.patch.object(cli, "ROUND_LOCK_WAIT_S", 0.6):
            code, doc = self._main_hook(root, json.dumps({"cwd": root}))
        self.assertEqual(code, 0)
        self.assertTrue(doc["systemMessage"].startswith("nopekit round not run: "), doc)
        self.assertTrue(doc["systemMessage"].endswith("Nothing was committed."), doc)
        self.assertEqual(stamp(root), before)


class TheTraceStartsAfterTheCommit(_InProcessGit):
    """Phase 3b's background start in phase 6's seam (`cli._round_start_trace`,
    the merge of the two): after the commit and outside the build lock, its
    sentence after the commit's, never quiet in the Stop hook, and `--json`
    holding both objects. What the merge had to join: phase 6 moved
    `cmd_round`'s body to `_round_run` and left the seam returning "", and phase
    3b started the trace at the end of the old `cmd_round`."""

    def _fake_start(self, seen: list):
        def fake(root, *, round_, key, popen=None):
            seen.append({"locked": os.path.exists(os.path.join(root, ".nopekit",
                                                               cli.LOCK_NAME)),
                         "head": self.head(root), "round": round_})
            return "started", derivation.LOG_REL
        return mock.patch.object(derivation, "start_background", fake)

    def _hook(self, root: str) -> dict:
        out = io.StringIO()
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps({"cwd": root}).encode("utf-8")),
                                 encoding="utf-8")
        with mock.patch.object(cli.sys, "stdin", stdin), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["round", "--hook"]), 0)
        return json.loads(out.getvalue())

    def _untraced(self) -> tuple[str, str]:
        repo = self.repo()
        project = self.project_in(repo)
        _env._rmtree(os.path.join(project, ".nopekit", "trace"))
        return repo, project

    def test_after_the_commit_and_outside_the_lock(self):
        repo, project = self._untraced()
        base = self.head(repo)
        seen: list = []
        with self._fake_start(seen):
            code, out, err = main(project, "round", "--commit", "--json")
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertEqual((doc["trace"]["state"], doc["trace"]["log"]),
                         ("started", derivation.LOG_REL))
        self.assertEqual((doc["commit"]["made"], doc["commit"]["why"]), (True, "committed"))
        self.assertEqual(len(seen), 1, seen)
        self.assertFalse(seen[0]["locked"], "the trace started under the build lock")
        self.assertNotEqual(seen[0]["head"], base, "the trace started before the commit")
        self.assertEqual(seen[0]["round"], doc["round"])
        said = doc["summary"]
        self.assertLess(said.index("Committed "),
                        said.index(" Tracing the derivation in the background"))
        self.assertNotIn("Tracing the derivation",
                         self.git(repo, "log", "-1", "--format=%B"),
                         "the commit's body said a trace it had not started")

    def test_the_hook_is_never_quiet_over_a_trace(self):
        _repo, project = self._untraced()
        self.assertEqual(main(project, "round", "--commit")[0], 0)
        # Nothing moved since, and the switch is off in tests: quiet.
        self.assertEqual(self._hook(project), {})
        seen: list = []
        with self._fake_start(seen):
            said = self._hook(project).get("systemMessage", "")
        self.assertEqual(len(seen), 1, seen)
        self.assertIn("Nothing to commit", said)
        self.assertTrue(said.endswith("Tracing the derivation in the background "
                                      f"(log {derivation.LOG_REL})."), said)


if __name__ == "__main__":
    unittest.main()
