# SPDX-License-Identifier: Apache-2.0
"""Proposers (SPEC 22, 25, 38): tools a pack ships to propose options or
evidence. A proposal settles nothing, so a proposer is never registered as an
evaluator and needs no control (invariant 3's other side), and ``propose`` writes
only what ``ingest`` would — one input record and its bytes per file the proposer
wrote (invariant 8). An input records the proposer that produced it; its basis is
looked up from that proposer and never stated by the input — a basis the input
stated would vouch for itself, the reason ``independence`` is forbidden.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from unittest import mock

import _decisions as D
import _env

from nopekit import gates, packs, store
from nopekit.util import NopekitError

_ENV = packs.PACK_PATH_ENV


def _pack(case: _env.EnvCase) -> tuple[str, str, str]:
    """``(base, pack directory, pack name)`` of a fresh fixture pack."""
    base, name = D.fixture_pack(case.tmp())
    return base, os.path.join(base, name), name


class AProposerIsNeverAnEvaluator(_env.EnvCase):
    """(3) A proposer is declared apart (``provides_proposers``), never imported
    by the spine, never registered, never listed among the evaluators, and
    ``pack validate`` demands no control of it."""

    def test_loading_the_pack_registers_only_its_evaluator(self):
        base, pack, name = _pack(self)
        registry = gates.Registry()
        before = set(sys.modules)
        with mock.patch.dict(os.environ, {_ENV: base}):
            packs.load_gates(name, registry, root=None)
        self.assertEqual(registry.ids(), [D.GATE_ID])
        self.assertNotIn(D.PROPOSER_ID, registry)
        loaded = {name for name in set(sys.modules) - before}
        self.assertFalse([n for n in loaded if "parts" in n or "proposer" in n], loaded)

    def test_the_spine_never_imports_a_proposer(self):
        base, pack, name = _pack(self)
        trap = os.path.join(pack, "proposers", "parts.py")
        with open(trap, "a", encoding="utf-8") as fh:
            fh.write("\nraise SystemExit('a proposer was imported')\n")
        registry = gates.Registry()
        with mock.patch.dict(os.environ, {_ENV: base}):
            packs.load_gates(name, registry, root=None)
            found = packs.proposers(None)
            problems = packs.validate(pack)
        self.assertIn(D.PROPOSER_ID, found)
        self.assertEqual(problems, [])

    def test_gate_list_omits_it(self):
        base, _pack_dir, name = _pack(self)
        root = D.bracket(os.path.join(self.tmp(), "b"))
        with mock.patch.dict(os.environ, {_ENV: base}):
            D.main(root, "packs", "add", name)
            _code, out, _err = D.main(root, "gate", "list", "--json")
            ids = [g["id"] for g in json.loads(out)["gates"]]
            _code, listed, _err = D.main(root, "propose")
        self.assertIn(D.GATE_ID, ids)
        self.assertNotIn(D.PROPOSER_ID, ids)
        self.assertIn(f"{D.PROPOSER_ID}  basis sourced  lists two parts from a fixed catalogue  "
                      f"(pack {name})", listed)

    def test_validate_demands_no_control_of_it(self):
        _base, pack, _name = _pack(self)
        self.assertEqual(packs.validate(pack), [])


class ProposeWritesOnlyItsInputs(_env.EnvCase):
    """(8) ``propose`` runs the proposer as a subprocess and ingests every file it
    wrote — one input record and its bytes each, with ``proposer`` — and nothing
    else; a non-zero exit, a timeout, a moved record or no file ingests nothing;
    known bytes leave their record unchanged; ``ingest --proposer`` records it for
    a proposer the agent ran by hand."""

    def setUp(self):
        self.base, _pack_dir, _name = _pack(self)
        self.root = D.bracket(os.path.join(self.tmp(), "b"))

    def _run(self, *argv: str, mode: str = "", code: int | None = None):
        env = {_ENV: self.base}
        if mode:
            env["FIXTURE_PROPOSER_MODE"] = mode
        proc = _env.nopekit(list(argv), cwd=self.root, env=env)
        if code is not None:
            self.assertEqual(proc.returncode, code, proc.stderr + proc.stdout)
        return proc

    def _inputs(self) -> dict[str, dict]:
        return {stem: D.read_json(path) for stem, path in store._record_files(self.root,
                                                                              "inputs")}

    def test_two_files_two_records(self):
        before = D.tree(self.root)
        proc = self._run("propose", D.PROPOSER_ID, "--", "M5", "anchor", code=0)
        self.assertIn(f"2 input(s) recorded, proposed by {D.PROPOSER_ID} (basis sourced)",
                      proc.stdout)
        found = self._inputs()
        self.assertEqual(sorted(r.get("proposer") for r in found.values()),
                         [D.PROPOSER_ID, D.PROPOSER_ID])
        args = next(r for r in found.values() if "args" in r["path"])
        with open(os.path.join(self.root, args["path"]), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "asked: M5 anchor\n")
        after = D.tree(self.root)
        written = sorted(p for p in set(after) | set(before) if after.get(p) != before.get(p))
        expected = sorted([f"inputs/{stem}.json" for stem in found]
                          + [r["path"] for r in found.values()])
        self.assertEqual(written, expected, "propose wrote only its inputs")
        # Known bytes: the fixed file's record is unchanged by a second run.
        fixed = next(stem for stem, r in found.items() if "fixed" in r["path"])
        record = D.read_json(os.path.join(self.root, "inputs", f"{fixed}.json"))
        proc = self._run("propose", D.PROPOSER_ID, "again", code=0)
        self.assertIn(f"is already recorded as {fixed}", proc.stdout)
        self.assertEqual(D.read_json(os.path.join(self.root, "inputs", f"{fixed}.json")),
                         record)

    def test_each_failure_ingests_nothing(self):
        """A non-zero exit, a write anywhere in the project outside the proposal
        directory — a record, the model, the tracked verdict cache — and no file
        each ingest nothing, and the refusal names what moved. What slipped
        through (review of phase 2): only the records were watched, so a
        proposer that edited ``model/bracket.py`` was ingested and nothing said so."""
        before = D.tree(self.root)
        model = os.path.join(self.root, "model", "bracket.py")
        planted = (os.path.join(self.root, "claims", "C99.json"),
                   os.path.join(self.root, ".nopekit", "verdicts", "bracket.bed_fit",
                                "planted.json"))
        outside = "wrote outside $NOPEKIT_PROPOSAL_DIR while it ran: "
        for mode, code, said in (("fail", 2, "exited 3; nothing was recorded"),
                                 ("touch-records", 2, outside + "claims/C99.json; nothing"),
                                 ("touch-model", 2, outside + "model/bracket.py; nothing"),
                                 ("touch-verdicts", 2, outside + ".nopekit/verdicts/"
                                                             "bracket.bed_fit/planted.json"),
                                 ("nothing", 0, "wrote no file; nothing was recorded")):
            with self.subTest(mode):
                proc = self._run("propose", D.PROPOSER_ID, mode=mode, code=code)
                self.assertIn(said, proc.stderr + proc.stdout)
                self.assertEqual(self._inputs(), {})
                for path in planted:
                    if os.path.exists(path):
                        os.remove(path)
                with open(model, "wb") as fh:
                    fh.write(before["model/bracket.py"])
        proc = self._run("propose", "--timeout", "1", D.PROPOSER_ID, mode="sleep", code=2)
        self.assertIn("ran past --timeout 1 s", proc.stderr)
        self.assertEqual(self._inputs(), {})
        after = D.tree(self.root)
        self.assertEqual(sorted(p for p in set(after) | set(before)
                                if after.get(p) != before.get(p)), [])

    def test_ignored_scratch_is_not_outside(self):
        """The guard watches what is tracked, not the ignored scratch: bytecode
        beside the model and `.nopekit/cache/` are no design edit."""
        proc = self._run("propose", D.PROPOSER_ID, mode="scratch", code=0)
        self.assertIn(f"2 input(s) recorded, proposed by {D.PROPOSER_ID}", proc.stdout)

    def test_its_own_flags_never_reach_the_script(self):
        """nopekit's own flags are nopekit's wherever they are typed — after the id
        too — a flag of the script's goes after ``--``, and one before it is an
        unrecognised argument, never a silent hand-off; and ``--json`` prints JSON
        on every run that succeeded, a run that wrote no file included. What
        slipped through (review of phase 2): ``propose <id> --json`` handed
        ``--json`` to the script and printed sentences, ``--timeout 30`` kept
        600 s, and ``--json`` over a run with no file printed a sentence."""
        proc = self._run("propose", D.PROPOSER_ID, "--json", "--", "--json", code=0)
        document = json.loads(proc.stdout)
        self.assertEqual(document["proposer"]["id"], D.PROPOSER_ID)
        args = next(r for r in self._inputs().values() if "args" in r["path"])
        with open(os.path.join(self.root, args["path"]), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "asked: --json\n", "after -- it is the script's")
        proc = self._run("propose", D.PROPOSER_ID, "--timeout", "1", mode="sleep", code=2)
        self.assertIn("ran past --timeout 1 s", proc.stderr)
        before = D.tree(self.root)
        proc = self._run("propose", D.PROPOSER_ID, "--limit", "5", code=2)
        self.assertIn("unrecognized arguments: --limit", proc.stderr)
        self.assertEqual(D.tree(self.root), before, "nothing was run")
        proc = self._run("propose", D.PROPOSER_ID, "--json", mode="nothing", code=0)
        self.assertEqual(json.loads(proc.stdout),
                         {"proposer": document["proposer"], "ingested": [], "known": []})

    def test_in_process_a_refusal_writes_nothing(self):
        """The in-process half: an unknown id and a proposer that exits non-zero
        each leave the tree as it was (the subprocess rows below run the rest)."""
        before = D.tree(self.root)
        with mock.patch.dict(os.environ, {_ENV: self.base, "FIXTURE_PROPOSER_MODE": "fail"}):
            code, _out, err = D.main(self.root, "propose", "nobody.ships_this")
            self.assertEqual(code, 2)
            self.assertIn("no discoverable pack declares 'nobody.ships_this'", err)
            code, _out, err = D.main(self.root, "propose", D.PROPOSER_ID)
            self.assertEqual(code, 2)
            self.assertIn("exited 3; nothing was recorded. Its last lines: the catalogue is "
                          "unreachable", err)
        self.assertEqual(D.tree(self.root), before)

    def test_an_unknown_proposer_is_refused(self):
        proc = self._run("propose", "nobody.ships_this", code=2)
        self.assertIn("no discoverable pack declares 'nobody.ships_this'", proc.stderr)
        evidence = os.path.join(self.tmp(), "quote.txt")
        with open(evidence, "w", encoding="utf-8") as fh:
            fh.write("a quote\n")
        proc = self._run("ingest", evidence, "--proposer", "nobody.ships_this", code=2)
        self.assertEqual(self._inputs(), {})

    def test_ingest_proposer_records_it_on_a_new_record_only(self):
        evidence = os.path.join(self.tmp(), "quote.txt")
        with open(evidence, "w", encoding="utf-8") as fh:
            fh.write("a quote for the anchors\n")
        proc = self._run("ingest", evidence, code=0)
        (record,) = self._inputs().values()
        self.assertNotIn("proposer", record)
        proc = self._run("ingest", evidence, "--proposer", D.PROPOSER_ID, code=0)
        self.assertIn("with no proposer; its record is unchanged", proc.stdout)
        (record,) = self._inputs().values()
        self.assertNotIn("proposer", record)
        other = os.path.join(self.tmp(), "quote2.txt")
        with open(other, "w", encoding="utf-8") as fh:
            fh.write("a second quote\n")
        proc = self._run("ingest", other, "--proposer", D.PROPOSER_ID, code=0)
        self.assertIn(f"proposed by {D.PROPOSER_ID} (basis sourced)", proc.stdout)
        self.assertEqual(sorted(r.get("proposer", "") for r in self._inputs().values()),
                         ["", D.PROPOSER_ID])


class AnInputNeverStatesItsBasis(_env.EnvCase):
    """An input's basis is its proposer's, looked up — ``basis`` on an input is
    refused naming that home, ``independence`` still is — and shown where the
    input is: ``inputs``, ``why`` and ``state.json``; a proposer no discoverable
    pack declares now reads undeclared."""

    def _project(self) -> str:
        root = D.bracket(os.path.join(self.tmp(), "b"))
        os.makedirs(os.path.join(root, "inputs", "data"))
        with open(os.path.join(root, "inputs", "data", "q.txt"), "w") as fh:
            fh.write("a quote\n")
        import hashlib
        D.write_json(os.path.join(root, "inputs", "quote.json"), {
            "path": "inputs/data/q.txt", "kind": "data",
            "sha256": hashlib.sha256(b"a quote\n").hexdigest(),
            "extractions": [{"what": "the anchor is M5", "grounds": ["C3"]}],
            "proposer": D.PROPOSER_ID})
        return root

    def test_a_stated_basis_is_refused(self):
        root = self._project()
        path = os.path.join(root, "inputs", "quote.json")
        for key, said in (("basis", "looked up from the proposer that produced the input"),
                          ("independence", "never a field the proposer fills in")):
            with self.subTest(key):
                D.write_json(path, dict(D.read_json(path), **{key: "established"}))
                with self.assertRaises(NopekitError) as caught:
                    store.load(root)
                self.assertIn(said, str(caught.exception))
                data = D.read_json(path)
                data.pop(key)
                D.write_json(path, data)
        D.write_json(path, dict(D.read_json(path), proposer="Not An Id"))
        with self.assertRaises(NopekitError) as caught:
            store.load(root)
        self.assertIn("is not a proposer id", str(caught.exception))

    def test_the_basis_is_looked_up_where_the_input_is_shown(self):
        root = self._project()
        base, _name = D.fixture_pack(self.tmp())
        for env, basis in (({_ENV: base}, "sourced"), ({}, "undeclared")):
            with self.subTest(basis), mock.patch.dict(os.environ, env):
                _code, out, _err = D.main(root, "inputs")
                self.assertIn(f"proposed by {D.PROPOSER_ID} (basis {basis})", out)
                _code, out, _err = D.main(root, "inputs", "--json")
                (row,) = json.loads(out)["inputs"]
                self.assertEqual((row["proposer"], row["basis"]), (D.PROPOSER_ID, basis))
                _code, out, _err = D.main(root, "why", "C3")
                self.assertIn(f"proposed by {D.PROPOSER_ID} (basis {basis})", out)
                import _physical
                from nopekit import cli, site
                view, resolution = _physical.resolved(root)
                registry, _p = cli._registry(root, store.load(root), strict=False)
                state = site.state(root, view, registry, resolution=resolution)
                (row,) = state["inputs"]
                self.assertEqual(row["basis"], basis)


class PackValidateChecksProposers(_env.EnvCase):
    """Every way a pack can declare a proposer wrongly, each named."""

    def _problems(self, edit=None, *, files=()) -> list[str]:
        _base, pack, _name = _pack(self)
        data = D.read_json(os.path.join(pack, "pack.json"))
        if edit is not None:
            edit(data)
        D.write_json(os.path.join(pack, "pack.json"), data)
        for rel in files:
            with open(os.path.join(pack, *rel.split("/")), "w") as fh:
                fh.write("print('hi')\n")
        return packs.validate(pack)

    def test_each_message(self):
        def entry(**kw):
            def edit(data):
                data["provides_proposers"][0].update(kw)
            return edit

        def drop(key):
            def edit(data):
                data["provides_proposers"][0].pop(key)
            return edit

        def twice(data):
            data["provides_proposers"].append(dict(data["provides_proposers"][0]))

        def not_a_list(data):
            data["provides_proposers"] = "fixture.parts"

        cases = (
            (not_a_list, "pack.json: provides_proposers must be a list of objects"),
            (entry(runs="x"), 'provides_proposers[0] has unknown key "runs" (did you mean '
                              '"run"?)'),
            (drop("basis"), 'pack.json: provides_proposers[0] has no "basis"'),
            (entry(id="parts"), "proposer id 'parts' must be dotted, e.g. sourcing.parts_search"),
            (entry(id=D.GATE_ID), f"proposer id '{D.GATE_ID}' is also an evaluator id in this "
                                  f"pack"),
            (twice, f"proposer '{D.PROPOSER_ID}' declared twice"),
            (entry(basis="grounded"), f'proposer {D.PROPOSER_ID}: basis "grounded" is not a '
                                      f"basis — established, sourced or model-only"),
            (entry(description="two\nlines"), f"proposer {D.PROPOSER_ID}: description must be "
                                               f"one line of at most 200 characters"),
            (entry(run="scripts/parts.py"), f"proposer {D.PROPOSER_ID}: run must be a .py file "
                                            f"under proposers/, not 'scripts/parts.py'"),
            (entry(run="proposers/gone.py"), f"proposer {D.PROPOSER_ID}: provides_proposers "
                                             f"names proposers/gone.py, and there is no such "
                                             f"file"),
        )
        for edit, said in cases:
            with self.subTest(said):
                problems = self._problems(edit)
                self.assertTrue(any(said in p for p in problems), problems)
        problems = self._problems(files=("proposers/stray.py", "proposers/_helper.py"))
        self.assertEqual(problems, ["proposers/stray.py is declared by no proposer in "
                                    "provides_proposers"])


if __name__ == "__main__":
    unittest.main()


class TheBundledAutorouteRefusesWithoutItsTools(_env.EnvCase):
    """pcb-kicad's ``pcb.autoroute``, the first bundled proposer, needs three tools
    nopekit does not ship (a Python with KiCad's ``pcbnew``, Java, the Freerouting
    jar). Without one it exits 2 naming what to set, and writes nothing: a
    proposer that half-ran must leave no file for ``propose`` to ingest. Held
    with the tools absent, so it runs on every host (CI has none of the three)."""

    RUN = os.path.join(_env.REPO, "packs", "pcb-kicad", "proposers", "autoroute.py")
    BOARD = os.path.join(_env.REPO, "packs", "pcb-kicad", "selftest", "assets",
                         "esd_guard.kicad_pcb")

    def _run(self, env):
        return _env.run([sys.executable, self.RUN, "--board", self.BOARD, "--jar",
                         os.path.join(self.tmp(), "no-such.jar")], cwd=self.tmp(), env=env)

    def test_a_missing_tool_exits_2_naming_what_to_set(self):
        out = self.tmp()
        proc = self._run({"NOPEKIT_PROPOSAL_DIR": out})
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertRegex(proc.stderr, r"pcb\.autoroute: no .*(KICAD_PYTHON|JAVA|FREEROUTING_JAR)")
        self.assertEqual(os.listdir(out), [])

    def test_outside_propose_it_refuses(self):
        proc = self._run({"NOPEKIT_PROPOSAL_DIR": ""})
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("nopekit propose pcb.autoroute", proc.stderr)


class TheBundledSchematicWriterWritesOnlyItsProposal(_env.EnvCase):
    """pcb-kicad's ``pcb.schematic_from_board`` reads the board and KiCad's stock
    symbols, and writes only under ``$NOPEKIT_PROPOSAL_DIR``: the project around it
    is as it was, whether it wrote its three files or refused."""

    RUN = os.path.join(_env.REPO, "packs", "pcb-kicad", "proposers", "schematic_from_board.py")
    ASSETS = os.path.join(_env.REPO, "packs", "pcb-kicad", "selftest", "assets")
    STOCK = next((d for d in (os.environ.get("KICAD9_SYMBOL_DIR", ""), "/usr/share/kicad/symbols")
                  if d and os.path.isfile(os.path.join(d, "Device.kicad_sym"))), None)

    def _tree(self, root):
        return sorted((os.path.relpath(os.path.join(dp, f), root),
                       os.path.getsize(os.path.join(dp, f)))
                      for dp, _dn, fs in os.walk(root) for f in fs)

    def _run(self, symbols: dict):
        project = self.tmp()
        for name in ("esd_guard.kicad_pcb", "esd_guard.kicad_pro"):
            shutil.copyfile(os.path.join(self.ASSETS, name), os.path.join(project, name))
        with open(os.path.join(project, "s.json"), "w", encoding="utf-8") as fh:
            json.dump(symbols, fh)
        out = self.tmp()
        before = self._tree(project)
        proc = _env.run([sys.executable, self.RUN, "--board", "esd_guard.kicad_pcb",
                         "--symbols", "s.json"], cwd=project,
                        env={"NOPEKIT_PROPOSAL_DIR": out})
        self.assertEqual(self._tree(project), before, "it wrote outside its proposal dir")
        return proc, out

    def _symbols(self):
        with open(os.path.join(self.ASSETS, "esd_guard.symbols.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_refusal_writes_nothing(self):
        symbols = self._symbols()
        symbols["symbols"].pop("U1")
        proc, out = self._run(symbols)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(os.listdir(out), [])

    def test_a_proposal_is_three_files_in_its_dir(self):
        if self.STOCK is None:
            self.skipTest("needs KiCad's stock symbols")
        proc, out = self._run(self._symbols())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(sorted(os.listdir(out)), ["esd_guard.kicad_sch",
                                                   "esd_guard.schematic.json", "sym-lib-table"])

    def test_a_multi_unit_part_is_three_files_in_its_dir(self):
        """The power board, whose Q1 is a two-unit symbol: the writer refused it
        (exit 2) before every unit was placed; now it writes its three files, and
        still nothing outside its directory."""
        if self.STOCK is None:
            self.skipTest("needs KiCad's stock symbols")
        project, out = self.tmp(), self.tmp()
        shutil.copyfile(os.path.join(self.ASSETS, "power_board.kicad_pcb"),
                        os.path.join(project, "p.kicad_pcb"))
        D.write_json(os.path.join(project, "s.json"), {"symbols": {
            "J1": "Connector:USB_C_Receptacle_USB2.0_16P", "R*": "Device:R", "C*": "Device:C",
            "U1": "Battery_Management:MCP73833-xxx-UN", "TH1": "Device:Thermistor_NTC",
            "F1": "Device:Polyfuse", "BT1": "Connector_Generic:Conn_01x02",
            "U2": "Battery_Management:AP9101CK", "Q1": "Transistor_FET:FDS6890A",
            "J6": "Connector_Generic:Conn_01x02", "J1?": "Connector_Generic:Conn_01x08"}})
        before = self._tree(project)
        proc = _env.run([sys.executable, self.RUN, "--board", "p.kicad_pcb", "--symbols",
                         "s.json"], cwd=project, env={"NOPEKIT_PROPOSAL_DIR": out})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._tree(project), before, "it wrote outside its proposal dir")
        self.assertEqual(sorted(os.listdir(out)), ["p.kicad_sch", "p.schematic.json",
                                                   "sym-lib-table"])
        self.assertEqual(sorted(D.read_json(os.path.join(out, "p.schematic.json"))["units"]),
                         ["Q1"])


class TheBundledBatteryDraftWritesOnlyItsProposal(_env.EnvCase):
    """pcb-kicad's ``pcb.battery_record_draft`` reads the boards it is given and
    writes only under ``$NOPEKIT_PROPOSAL_DIR``, a draft and its summary; a
    refusal writes nothing. It needs no KiCad: the pack's reader is enough."""

    RUN = os.path.join(_env.REPO, "packs", "pcb-kicad", "proposers", "battery_record_draft.py")
    BOARD = os.path.join(_env.REPO, "packs", "pcb-kicad", "selftest", "assets",
                         "power_board.kicad_pcb")

    _tree = TheBundledSchematicWriterWritesOnlyItsProposal._tree

    def _run(self, *boards: str, env=None):
        project, out = self.tmp(), self.tmp()
        shutil.copyfile(self.BOARD, os.path.join(project, "power_board.kicad_pcb"))
        before = self._tree(project)
        argv = [a for b in boards for a in ("--board", b)]
        proc = _env.run([sys.executable, self.RUN, *argv], cwd=project,
                        env={"NOPEKIT_PROPOSAL_DIR": out, **(env or {})})
        self.assertEqual(self._tree(project), before, "it wrote outside its proposal dir")
        return proc, out

    def test_a_draft_is_two_files_in_its_dir(self):
        proc, out = self._run("power_board.kicad_pcb")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(sorted(os.listdir(out)), ["power_board.power_draft.json",
                                                   "power_board.power_records.json"])
        self.assertIn("a draft settles nothing", proc.stdout)

    def test_a_refusal_writes_nothing(self):
        for boards in (("gone.kicad_pcb",), ("power_board.kicad_pcb",) * 2):
            with self.subTest(boards=boards):
                proc, out = self._run(*boards)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertEqual(os.listdir(out), [])

    def test_outside_propose_it_refuses(self):
        proc, out = self._run("power_board.kicad_pcb", env={"NOPEKIT_PROPOSAL_DIR": ""})
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("nopekit propose pcb.battery_record_draft", proc.stderr)


class ProposeRunsEachBundledPcbProposer(_env.EnvCase):
    """``nopekit propose`` on a project holding a copy of the pack's power board,
    for the draft and for the schematic writer (a multi-unit part on it): each
    file it wrote is one input record, proposed by its proposer at its basis, and
    nothing else in the project moves."""

    BOARD = TheBundledBatteryDraftWritesOnlyItsProposal.BOARD

    def setUp(self):
        self.root = D.bracket(os.path.join(self.tmp(), "b"))
        shutil.copyfile(self.BOARD, os.path.join(self.root, "power_board.kicad_pcb"))

    def _propose(self, pid: str, *argv: str) -> list[str]:
        before = D.tree(self.root)
        proc = _env.nopekit(["propose", pid, "--", *argv], cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        after = D.tree(self.root)
        written = sorted(p for p in set(after) | set(before) if after.get(p) != before.get(p))
        found = {stem: D.read_json(path) for stem, path in store._record_files(self.root,
                                                                              "inputs")}
        mine = {stem: rec for stem, rec in found.items() if rec.get("proposer") == pid}
        self.assertEqual(written, sorted([f"inputs/{stem}.json" for stem in mine]
                                         + [rec["path"] for rec in mine.values()]))
        self.assertIn(f"proposed by {pid} (basis model-only)", proc.stdout)
        return sorted(os.path.basename(rec["path"]) for rec in mine.values())

    def test_the_draft_is_recorded_as_two_inputs(self):
        self.assertEqual(self._propose("pcb.battery_record_draft", "--board",
                                       "power_board.kicad_pcb"),
                         ["power_board.power_draft.json", "power_board.power_records.json"])

    def test_the_schematic_is_recorded_as_three_inputs(self):
        if TheBundledSchematicWriterWritesOnlyItsProposal.STOCK is None:
            self.skipTest("needs KiCad's stock symbols")
        D.write_json(os.path.join(self.root, "power.symbols.json"), {"symbols": {
            "J1": "Connector:USB_C_Receptacle_USB2.0_16P", "R*": "Device:R", "C*": "Device:C",
            "U1": "Battery_Management:MCP73833-xxx-UN", "TH1": "Device:Thermistor_NTC",
            "F1": "Device:Polyfuse", "BT1": "Connector_Generic:Conn_01x02",
            "U2": "Battery_Management:AP9101CK", "Q1": "Transistor_FET:FDS6890A",
            "J6": "Connector_Generic:Conn_01x02", "J1?": "Connector_Generic:Conn_01x08"}})
        self.assertEqual(self._propose("pcb.schematic_from_board", "--board",
                                       "power_board.kicad_pcb", "--symbols",
                                       "power.symbols.json"),
                         ["power_board.kicad_sch", "power_board.schematic.json",
                          "sym-lib-table"])
