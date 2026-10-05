# SPDX-License-Identifier: Apache-2.0
"""The model owns a parameter's value; what lost has two homes, and both are shown.

Checkpoint 1.3 (spec §3.15, §4 U27; D-30). Three classes:

    ParamValueHasOneHome   `modelio.param_view` takes value, units, rationale and
                           derived_from from the MODEL, `rejected` from the model's
                           PARAMS and the record together — each row tagged with
                           the file it lives in — and `decisions.why` renders it.
    ChangedInIsDerived     `decisions.add` no longer writes `Param.changed_in`;
                           `decisions.changed_in` reads it off the log.
    StaticProse            `modelio.static_param_prose` says what the model states
                           about each parameter by READING its text, never by
                           running it. The migration's params rule (a record keeps
                           a rationale or units only where the model states none)
                           stands on it, so it must never claim more than the
                           running model would.

What slipped through before (docs/plan/slipped.md):

    S-39  `why` quoted the ledger's copy of a value: "thickness = 7" after the
          model had said 8.0 — a cached number standing where a fact should be.
    S-38  a rejection added to the model's PARAMS after the param existed never
          reached the ledger: `sync_params` kept the record's `rejected` whole and
          dropped the model's, silently — rule 3's highest-value field.
    S-42  the reference model promised "every parameter carries its rationale and
          what was rejected (see PARAMS)"; no PARAMS existed, all twelve params
          carried `rejected: []`, and the one number it did quote ("misses by
          ~5x") was wrong: `build(Config(thickness=4.0))` gives 3.75 mm against
          0.5, which is 7.5x.

Every project here is a copy under the temp directory (tests:H3); nothing loads
the tracked bracket in place.

Run:  PYTHONPATH=src python3 -m unittest tests.test_param_view -v
"""
from __future__ import annotations

import json
import os
import re
import sys
import textwrap
import unittest

import _env
import _projects
import _transcript
from atompipe import decisions, modelio, store
from atompipe import site as site_mod
from atompipe.models import Claim, Decision, Ledger, Param, Rejected, Verdict
from atompipe.util import AtompipeError

ENTRY = "model/bracket.py"

#: What the bracket's PARAMS must say, and nothing more (D-30): three params with a
#: real loser, each in millimetres. A fourth entry, or units on a field PARAMS has
#: nothing to add to, would be the parallel table `field_docstrings` warns about.
BRACKET_PARAMS = {
    "thickness": ("mm", [("4.0 mm", "3.75 mm deflection, 7.5x the limit")]),
    "arm_length": ("mm", [("80 mm", "1.7 mm deflection, over 3x the limit")]),
    "hole_d": ("mm", [("5.0 mm", "line-to-line fit an FDM hole will not hold")]),
}

#: The transcript's three lines (docs/plan/phase-1.md), spelled out here once more
#: on purpose: the step in `_transcript` is matched too, and a test that only
#: re-used the code's own strings would agree with the code by construction.
WHY_LINE = "param thickness = 7.0 mm   (model/bracket.py Config.thickness)"
WHY_HEAD = "REJECTED (1)"
WHY_ROW = "  4.0 mm — 3.75 mm deflection, 7.5x the limit   (model/bracket.py PARAMS)"


def _append(root: str, text: str, path: str = ENTRY) -> None:
    with open(os.path.join(root, *path.split("/")), "a", encoding="utf-8") as fh:
        fh.write("\n" + textwrap.dedent(text))


def _replace_once(root: str, old: str, new: str, path: str = ENTRY) -> None:
    full = os.path.join(root, *path.split("/"))
    with open(full, encoding="utf-8") as fh:
        text = fh.read()
    if text.count(old) != 1:
        raise AssertionError(f"{path}: expected exactly one {old!r}, found {text.count(old)}")
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text.replace(old, new))


def _by_name(views) -> dict:
    return {view.name: view for view in views}


def _block(lines: list[str], head: str) -> list[str]:
    """``head`` and the lines under it, up to the next blank line."""
    start = lines.index(head)
    end = next((i for i in range(start, len(lines)) if not lines[i].strip()), len(lines))
    return lines[start:end]


class _Bracket(_env.EnvCase):
    def bracket(self, **kw) -> str:
        return _projects.bracket_copy(os.path.join(self.tmp(), "bracket"), **kw)


# --------------------------------------------------------------------------- #
# the model owns the value
# --------------------------------------------------------------------------- #
class ParamValueHasOneHome(_Bracket):

    def test_the_bracket_states_what_lost_in_params(self):
        """S-42: the model says "see PARAMS"; PARAMS must exist and say it."""
        model = modelio.load_model(self.bracket(), ENTRY)
        stated = {p.name: (p.units, [(r.value, r.why) for r in p.rejected])
                  for p in model.params if p.units or p.rejected}
        self.assertEqual(stated, BRACKET_PARAMS)

    def test_the_rejections_quote_the_models_own_numbers(self):
        """A rejection's number is only worth keeping if it is the model's: each one
        is recomputed here from `build()`, against C1's own limit."""
        root = self.bracket()
        model = modelio.load_model(root, ENTRY)
        limit = store.load(root).claim("C1").acceptance.limit
        self.assertEqual(limit, 0.5)
        Config, build = model.module.Config, model.module.build
        at_four = build(Config(thickness=4.0))["deflection"]
        self.assertAlmostEqual(at_four, 3.75, places=9)
        self.assertAlmostEqual(at_four / limit, 7.5, places=9)
        at_eighty = build(Config(arm_length=80.0))["deflection"]
        self.assertEqual(round(at_eighty, 1), 1.7)
        self.assertGreater(at_eighty, 3 * limit)
        self.assertEqual(model.config.thickness, 7.0, "thickness stays 7.0 (failing on purpose)")
        docs = modelio.field_docstrings(model.file, "Config")
        self.assertNotIn("~5x", docs["thickness"])
        self.assertIn("3.75 mm, 7.5x the limit", docs["thickness"])
        for name in BRACKET_PARAMS:
            with self.subTest(param=name):
                self.assertIn("PARAMS", docs[name], "the docstring points at what lost")

    def test_the_model_value_wins_over_a_stale_record(self):
        """S-39: a record holding yesterday's number is not where the number lives."""
        root = self.bracket(thickness=8.0)
        model = modelio.load_model(root, ENTRY)
        ledger = Ledger(params=[Param(name="thickness", value=7.0, units="in",
                                      rationale="a stale copy", source="hand calc",
                                      grounded_by=["sk-1"], tags=["stiffness"])])
        view = _by_name(modelio.param_view(ledger, model))["thickness"]
        self.assertEqual(view.value, 8.0)
        self.assertEqual(view.units, "mm", "the model states units; the record's lose")
        self.assertNotEqual(view.rationale, "a stale copy")
        self.assertIn("DELIBERATELY", view.rationale)
        self.assertEqual((view.source, tuple(view.grounded_by), tuple(view.tags)),
                         ("hand calc", ("sk-1",), ("stiffness",)), "the record owns these")
        self.assertEqual(view.home, "model/bracket.py Config.thickness")
        self.assertEqual(view.model_error, "")
        text = decisions.why(ledger, "thickness", view=modelio.param_view(ledger, model))
        self.assertEqual(text.splitlines()[0],
                         "param thickness = 8.0 mm   (model/bracket.py Config.thickness)")
        self.assertNotRegex(text, r"= 7(\.0)?\b")

    def test_a_params_rejection_added_later_appears(self):
        """S-38: the record exists first; the model's PARAMS gains a loser after."""
        root = self.bracket()
        ledger = Ledger(params=[
            Param(name="width", value=30.0, rejected=[Rejected("20 mm", "flexes")])])
        _append(root, """\
            PARAMS.append({"name": "width",
                           "rejected": [{"value": "40 mm", "why": "overhangs the shelf lip"}]})
            """)
        model = modelio.load_model(root, ENTRY)
        view = _by_name(modelio.param_view(ledger, model))["width"]
        self.assertEqual(
            [(r.value, r.why, origin) for r, origin in view.rejected],
            [("40 mm", "overhangs the shelf lip", "model/bracket.py PARAMS"),
             ("20 mm", "flexes", "params/width.json")])
        text = decisions.why(ledger, "width", view=[view])
        self.assertEqual(_block(text.splitlines(), "REJECTED (2)"), [
            "REJECTED (2)",
            "  40 mm — overhangs the shelf lip   (model/bracket.py PARAMS)",
            "  20 mm — flexes   (params/width.json)"])

    def test_the_union_is_tagged_with_its_origin_and_said_once(self):
        """A record that repeats the model's rejection shows it once, as the
        model's; a record's own loser keeps its own origin. Evidence rides on a
        continuation line, so the row keeps its shape."""
        root = self.bracket()
        ledger = Ledger(params=[Param(name="thickness", value=None, rejected=[
            Rejected("4.0 MM", "3.75 mm deflection, 7.5X the limit"),
            Rejected("6.0 mm", "0.83 mm deflection", evidence="inputs/sag-test.csv")])])
        view = _by_name(modelio.param_view(ledger, modelio.load_model(root, ENTRY)))["thickness"]
        self.assertEqual([(r.value, origin) for r, origin in view.rejected],
                         [("4.0 mm", "model/bracket.py PARAMS"),
                          ("6.0 mm", "params/thickness.json")])
        lines = decisions.why(ledger, "thickness", view=[view]).splitlines()
        self.assertEqual(_block(lines, "REJECTED (2)"), [
            "REJECTED (2)", WHY_ROW,
            "  6.0 mm — 0.83 mm deflection   (params/thickness.json)",
            "      evidence: inputs/sag-test.csv"])
        for row in _block(lines, "REJECTED (2)")[1:3]:
            self.assertRegex(row, _transcript.WHY_REJECTED_ROW)

    def test_a_model_that_raises_shows_no_number(self):
        """No cached number where the model should answer: the value is None and
        `why` says the model does not load."""
        root = self.bracket()
        _append(root, 'raise RuntimeError("the model is broken on purpose")\n')
        with self.assertRaises(AtompipeError) as caught:
            modelio.load_model(root, ENTRY)
        ledger = Ledger(params=[Param(name="thickness", value=7.0, units="mm",
                                      rationale="recorded before the model broke")])
        views = modelio.param_view(ledger, None, model_error=str(caught.exception))
        view = _by_name(views)["thickness"]
        self.assertIsNone(view.value)
        self.assertIn("the model is broken on purpose", view.model_error)
        self.assertEqual(view.home, "")
        text = decisions.why(ledger, "thickness", view=views)
        self.assertIn("model does not load: ", text)
        self.assertIn("the model is broken on purpose", text)
        self.assertFalse([ln for ln in text.splitlines()
                          if _transcript.WHY_PARAM.fullmatch(ln)], text)
        self.assertNotRegex(text, r"= 7(\.0)?\b")
        # A caller that forgot the reason still gets a view that cannot pass for
        # a loaded model.
        self.assertTrue(modelio.param_view(ledger, None)[0].model_error)

    def test_why_thickness_is_the_transcript(self):
        """The bracket's `why thickness`: the value line, then exactly one
        rejection under REJECTED (1) — the transcript's three lines."""
        root = self.bracket()
        ledger = store.load(root)
        views = modelio.param_view(ledger, modelio.load_model(root, ENTRY))
        text = decisions.why(ledger, "thickness", view=views)
        lines = text.splitlines()
        self.assertEqual(lines[0], WHY_LINE)
        self.assertEqual(_block(lines, WHY_HEAD), [WHY_HEAD, WHY_ROW])
        step = next(s for s in _transcript.STEPS if s.id == "why-thickness")
        result = _transcript.Result("atompipe why thickness", 0, text, "", root)
        for expect in step.expect:
            self.assertIsNone(_transcript.problem(expect, result), text)

    def test_a_params_dict_typo_is_refused_with_a_suggestion(self):
        """The model-side cousin of S-40: `Param.from_dict` drops a key it does not
        know, so `"unit": "mm"` used to load as no units at all. R-10: the
        bracket is the only bundled PARAMS, and it loads (below, and in every
        other test here)."""
        for old, new, key, hint in (
                ('{"name": "thickness", "units": "mm"', '{"name": "thickness", "unit": "mm"',
                 "unit", "units"),
                ('"why": "3.75 mm', '"whi": "3.75 mm', "whi", "why")):
            with self.subTest(key=key):
                root = self.bracket()
                _replace_once(root, old, new)
                with self.assertRaises(AtompipeError) as caught:
                    modelio.load_model(root, ENTRY)
                message = str(caught.exception)
                self.assertIn(ENTRY, message)
                self.assertIn(repr(key), message)
                self.assertIn(f"did you mean {hint!r}", message)
        modelio.load_model(self.bracket(), ENTRY)

    def test_a_record_the_model_dropped_is_an_orphan_with_no_value(self):
        root = self.bracket()
        model = modelio.load_model(root, ENTRY)
        ledger = Ledger(params=[Param(name="hook_mm", value=4.0, rationale="the old hook",
                                      rejected=[Rejected("2 mm", "snapped")])])
        self.assertEqual(modelio.orphan_params(ledger, model), ["hook_mm"])
        views = modelio.param_view(ledger, model)
        self.assertEqual([v.name for v in views][:12],
                         [p.name for p in model.params], "the model's order first")
        orphan = views[-1]
        self.assertEqual((orphan.name, orphan.value, orphan.home, orphan.record),
                         ("hook_mm", None, "", "params/hook_mm.json"))
        self.assertEqual(orphan.rationale, "the old hook")
        text = decisions.why(ledger, "hook_mm", view=views)
        self.assertNotRegex(text, r"= 4(\.0)?\b")
        self.assertIn("(params/hook_mm.json)", text.splitlines()[0])

    def test_why_without_a_view_is_todays_block(self):
        """Every default keeps today's behaviour: the not-yet-switched `cmd_why`
        renders the ledger alone, as it did."""
        ledger = Ledger(params=[Param(name="thickness", value=7.0, rationale="why 7",
                                      gates=["bracket.min_wall"])])
        lines = decisions.why(ledger, "thickness").splitlines()
        self.assertEqual(lines[0], "param  thickness = 7")
        for head in ("WHY", "REJECTED (0)", "GATES (1)", "GROUNDED BY (0)",
                     "DECISIONS (0, newest first)"):
            self.assertIn(head, lines)
        self.assertIn("  [ -- ] bracket.min_wall : unrun", lines)

    def test_coverage_read_sets_and_verdicts_stand_in_for_the_stored_copies(self):
        """The CLI hands `why` registry coverage, the recorded read sets and the
        effective verdicts; the claim's and the param's stored `gates` and the
        ledger's verdicts are then not consulted."""
        ledger = Ledger(
            claims=[Claim(id="C1", statement="tip sags no more than 0.5 mm",
                          gates=["stale.gate"])],
            params=[Param(name="thickness", value=None, gates=["stale.gate"])],
            verdicts=[Verdict(gate="stale.gate", passed=True, detail="an old pass")])
        fresh = [Verdict(gate="g.one", passed=False, measured=0.7, limit=0.5,
                         units="mm", detail="0.700 mm")]
        claim = decisions.why(ledger, "C1", coverage={"C1": ["g.one"]}, verdicts=fresh)
        self.assertIn("GATES (1)", claim)
        self.assertIn("[FAIL] g.one : 0.700 mm", claim)
        self.assertNotIn("stale.gate", claim)
        views = modelio.param_view(ledger, None, model_error="not loaded in this test")
        param = decisions.why(ledger, "thickness", view=views, verdicts=fresh,
                              read_sets={"g.one": {("config", "thickness")},
                                         "g.two": {("width",)},
                                         "g.deep": {("bom", "x", "thickness")}})
        self.assertIn("GATES (1)", param)
        self.assertIn("[FAIL] g.one : 0.700 mm", param)
        self.assertNotIn("stale.gate", param)
        self.assertNotIn("g.deep", param, "a read deeper than two keys is not this param")


# --------------------------------------------------------------------------- #
# changed_in is read off the log, never written
# --------------------------------------------------------------------------- #
class ChangedInIsDerived(unittest.TestCase):

    def test_add_writes_no_param_and_the_log_answers(self):
        ledger = Ledger(params=[Param(name="thickness", value=None)])
        self.assertEqual(decisions.changed_in(ledger, "thickness"), "")
        first = decisions.add(ledger, title="Thicken to 7", summary="7 mm, from 6",
                              when="2026-09-01", params_changed=["thickness"])
        self.assertEqual(ledger.param("thickness").changed_in, "",
                         "a decision is one record: it writes no param record")
        self.assertEqual(decisions.changed_in(ledger, "thickness"), first.id)
        second = decisions.add(ledger, title="Thicken to 8", summary="8 mm, from 7",
                               when="2026-09-02", params_changed=["width", "thickness"])
        self.assertEqual(decisions.changed_in(ledger, "thickness"), second.id)
        self.assertEqual(decisions.changed_in(ledger, "width"), second.id)
        self.assertEqual(decisions.changed_in(ledger, "brim_mm"), "")

    def test_why_names_the_decision_that_last_moved_it(self):
        ledger = Ledger(params=[Param(name="thickness", value=None)],
                        decisions=[Decision(id="thicken-to-8", title="Thicken to 8",
                                            when="2026-09-02", summary="8 mm",
                                            params_changed=["thickness"])])
        views = modelio.param_view(ledger, None, model_error="not loaded in this test")
        self.assertIn("  last moved in: thicken-to-8",
                      decisions.why(ledger, "thickness", view=views).splitlines())


# --------------------------------------------------------------------------- #
# what the model states, read and never run
# --------------------------------------------------------------------------- #
_SYNTHETIC = '''\
from dataclasses import dataclass

import atompipe.models as models
from atompipe.models import Param


@dataclass
class Config:
    a: float = 1.0
    """a's docstring."""
    b: float = 2.0
    """b's   docstring,
    over two lines."""
    c: float = 3.0


@dataclass
class Fixture:
    c: float = 9.0
    """a second class's field of the same name: not the config's."""


CONFIG = Config()
UNITS = "kg"
PARAMS = [
    Param(name="a", value=None, units="mm", rationale="PARAMS wins over the docstring"),
    models.Param("b", None, "N"),
    {"name": "d", "value": 1.0, "units": UNITS, "rationale": "a constant outside the config"},
    {"name": "e", "value": 2.0, "units": "s"},
]


def build(config=None):
    return {}
'''


class StaticProse(_Bracket):

    def test_bracket_states_every_rationale_and_three_units(self):
        root = self.bracket()
        prose = modelio.static_param_prose(root, ENTRY)
        self.assertEqual(len(prose), 12, sorted(prose))
        self.assertTrue(all(row["rationale"] for row in prose.values()), prose)
        self.assertEqual({name: row["units"] for name, row in prose.items() if row["units"]},
                         {name: units for name, (units, _r) in BRACKET_PARAMS.items()})
        # What it read is what the running model states, field for field.
        model = modelio.load_model(root, ENTRY)
        self.assertEqual(prose, {p.name: {"rationale": p.rationale, "units": p.units}
                                 for p in model.params})

    def test_never_imports(self):
        """An entry whose import raises — after touching a marker — still yields
        its prose, and the marker is never touched."""
        root = self.bracket()
        marker = os.path.join(self.tmp(), "imported")
        # After the imports (a statement before `from __future__` would make the
        # control below fail as a SyntaxError, proving nothing), before Config.
        anchor = "from dataclasses import dataclass, asdict\n"
        _replace_once(root, anchor, anchor
                      + f"open({marker!r}, 'w').close()\n"
                      + "raise ImportError('this model must never be imported by the reader')\n")
        before = set(sys.modules)
        prose = modelio.static_param_prose(root, ENTRY)
        self.assertFalse(os.path.exists(marker), "static_param_prose ran the model")
        self.assertEqual(set(sys.modules) - before, set())
        self.assertEqual(len(prose), 12)
        self.assertEqual(prose["thickness"]["units"], "mm")
        # The control: the same entry really does raise when it is run, after
        # touching the marker.
        with self.assertRaises(AtompipeError) as caught:
            modelio.load_model(root, ENTRY)
        self.assertIn("this model must never be imported", str(caught.exception))
        self.assertTrue(os.path.exists(marker))

    def test_dict_items_param_calls_and_the_config_class(self):
        root = self.tmp()
        os.makedirs(os.path.join(root, "model"))
        with open(os.path.join(root, "model", "m.py"), "w", encoding="utf-8") as fh:
            fh.write(_SYNTHETIC)
        prose = modelio.static_param_prose(root, "model/m.py")
        self.assertEqual(prose, {
            "a": {"rationale": "PARAMS wins over the docstring", "units": "mm"},
            "b": {"rationale": "b's docstring, over two lines.", "units": "N"},
            "d": {"rationale": "a constant outside the config", "units": ""},
            "e": {"rationale": "", "units": "s"},
        })
        # Never more than the running model states: whatever the reader says is
        # stated, the model states identically. (It may say less — `d`'s units
        # are a name, not a constant — which keeps the migration lossless.)
        model = modelio.load_model(root, "model/m.py")
        running = {p.name: p for p in model.params}
        for name, row in prose.items():
            for key in ("rationale", "units"):
                if row[key]:
                    with self.subTest(param=name, key=key):
                        self.assertEqual(getattr(running[name], key), row[key])
        self.assertEqual(running["c"].rationale, "", "Fixture.c is not the config's")
        self.assertEqual(running["d"].units, "kg")

    def test_nothing_readable_states_nothing(self):
        root = self.tmp()
        os.makedirs(os.path.join(root, "model", "pkg"))
        with open(os.path.join(root, "model", "broken.py"), "w", encoding="utf-8") as fh:
            fh.write("class Config:\n    x: float = (\n")
        with open(os.path.join(root, "model", "pkg", "__init__.py"), "w",
                  encoding="utf-8") as fh:
            fh.write('class Config:\n    x: float = 1.0\n    """x, from the package."""\n')
        self.assertEqual(modelio.static_param_prose(root, ""), {})
        self.assertEqual(modelio.static_param_prose(root, None), {})
        self.assertEqual(modelio.static_param_prose(root, "model/missing.py"), {})
        self.assertEqual(modelio.static_param_prose(root, "model/broken.py"), {})
        self.assertEqual(modelio.static_param_prose(root, "model/pkg"),
                         {"x": {"rationale": "x, from the package.", "units": ""}})


# --------------------------------------------------------------------------- #
# every reader shows the view, and they agree on what is undefended
# --------------------------------------------------------------------------- #
#: `doctor`'s `model-provenance` detail when it names parameters.
_DOCTOR_UNDEFENDED = re.compile(r"\d+ param\(s\) with no rationale: (?P<names>.+)")

#: `status`'s line naming them.
_STATUS_UNDEFENDED = re.compile(r"undefended params: (?P<names>.+?)(?:, \+\d+)? — "
                                r"no rationale recorded")

#: The report's section heading, and the first cell of each of its rows.
_REPORT_HEAD = "### Parameters with no recorded rationale"
_REPORT_ROW = re.compile(r"^\| `(?P<name>[^`]+)` \| (?P<value>[^|]*) \|")

#: A model field that states nothing about itself: no docstring, no PARAMS entry,
#: so no home says why it is 1.5.
_SILENT_FIELD = "    fillet_r: float = 1.5\n"

#: Where it goes: after the last field's docstring (exactly once in the model).
_LAST_DOCSTRING = 'which is the kind of thing you discover at 11pm."""\n'


def _names(text: str) -> list[str]:
    return [name.strip() for name in text.split(",") if name.strip()]


class RenderersReadTheParamView(_Bracket):
    """`status`, `report` and the page take a parameter's value and rationale from
    `modelio.param_view` — the model's, with the record's provenance — exactly as
    `why` and `doctor` do, and all of them name the same undefended parameters.

    What slipped through (review, checkpoint 1.3): from 1.3 the model owns a
    parameter and a param record is SPARSE — `params/<name>.json` holds only what
    the model cannot (source, grounding, tags) — but `site.state`, `report`'s
    standing constraints and terminal block, and `status` still read
    `ledger.params`, the records alone. So the migrated bracket's page showed no
    parameters at all (it has no record); a record holding only `"source"` made
    `status` call `thickness` undefended and the report print it with value
    `None`, while `doctor` — reading the model — said every parameter carried a
    rationale; and a model field nobody explained was flagged by `doctor` and by
    nothing a reader of the page, the report or `status` would see.

    Every command runs in a subprocess on a temp copy of the migrated bracket.
    """

    def project(self, *, silent_field: bool = False, records: dict | None = None) -> str:
        root = self.bracket(migrated=True)
        if silent_field:
            _replace_once(root, _LAST_DOCSTRING, _LAST_DOCSTRING + "\n" + _SILENT_FIELD)
        for name, body in (records or {}).items():
            os.makedirs(os.path.join(root, "params"), exist_ok=True)
            with open(os.path.join(root, "params", f"{name}.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(body, fh, indent=2)
                fh.write("\n")
        return root

    def run_ok(self, root: str, *argv: str):
        proc = _env.atompipe(list(argv), cwd=root)
        self.assertEqual(proc.returncode, 0,
                         f"atompipe {' '.join(argv)}\n{proc.stdout}\n{proc.stderr}")
        return proc

    def page(self, root: str) -> dict:
        if not os.path.isdir(os.path.join(root, "site")):
            self.run_ok(root, "site", "init")
        self.run_ok(root, "site", "build")
        with open(os.path.join(root, "site", "data", "state.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def readers(self, root: str, *, loads: bool = True) -> dict[str, list[str]]:
        """``{reader: [undefended parameter names]}`` as each one says it. With
        ``loads=False`` (the model is broken) `model` and `site build` are left
        out: both refuse a model that does not load, with exit 2."""
        said: dict[str, list[str]] = {}
        doctor = json.loads(_env.atompipe(["doctor", "--json"], cwd=root).stdout)
        rows = [row for row in doctor["checks"] if row["check"] == "model-provenance"]
        said["doctor"] = ([] if not rows or rows[0]["status"] == "ok" else
                          _names(_DOCTOR_UNDEFENDED.fullmatch(rows[0]["detail"])["names"]))
        status = json.loads(self.run_ok(root, "status", "--json").stdout)
        said["status --json"] = list(status["model"]["undocumented_params"])
        lines = self.run_ok(root, "status").stdout.splitlines()
        found = [_STATUS_UNDEFENDED.fullmatch(line) for line in lines]
        said["status"] = next((_names(m["names"]) for m in found if m), [])
        said["report"] = [row["name"] for row in self.report_rows(root)]
        if loads:
            model = json.loads(self.run_ok(root, "model", "--json").stdout)
            said["model --json"] = list(model["undocumented"])
            said["page"] = [row["name"] for row in self.page(root)["params"]
                            if row["defended"] is False]
        return said

    def report_rows(self, root: str) -> list[dict]:
        lines = self.run_ok(root, "report").stdout.splitlines()
        if _REPORT_HEAD not in lines:
            return []
        rows = []
        for line in lines[lines.index(_REPORT_HEAD) + 1:]:
            if line.startswith("#"):
                break
            match = _REPORT_ROW.match(line)
            if match and match["name"] != "Param":
                rows.append(match.groupdict())
        return rows

    def assertAgree(self, said: dict[str, list[str]], expected: list[str]) -> None:
        self.assertEqual(said, {reader: expected for reader in said},
                         "every reader names the same undefended parameters")

    def test_the_bracket_page_shows_the_models_parameters(self):
        """V: the migrated bracket holds no param record; its page shows every
        parameter the model holds, with the model's value, rationale and losers."""
        root = self.project()
        model = modelio.load_model(root, ENTRY)
        rows = {row["name"]: row for row in self.page(root)["params"]}
        self.assertEqual(list(rows), [p.name for p in model.params],
                         "the page shows the model's parameters, in its order")
        thickness = rows["thickness"]
        self.assertEqual((thickness["value"], thickness["units"], thickness["home"]),
                         (7.0, "mm", "model/bracket.py Config.thickness"))
        self.assertIn("DELIBERATELY", thickness["rationale"])
        self.assertIs(thickness["defended"], True)
        self.assertEqual([(r["value"], r["origin"]) for r in thickness["rejected"]],
                         [("4.0 mm", "model/bracket.py PARAMS")])
        self.assertEqual(thickness["model_error"], "")
        self.assertAgree(self.readers(root), [])

    def test_a_record_holding_only_provenance_is_defended_by_the_model(self):
        """V: the review's repro. `params/thickness.json` says only where the number
        came from; the model's docstring defends it. Nobody calls it undefended,
        and nobody shows it without its value."""
        root = self.project(records={"thickness": {"source": "hand calc"}})
        said = self.readers(root)
        self.assertAgree(said, [])
        self.assertNotIn("undefended params:", self.run_ok(root, "status").stdout)
        rows = {row["name"]: row for row in self.page(root)["params"]}
        self.assertEqual((rows["thickness"]["value"], rows["thickness"]["source"],
                          rows["thickness"]["record"]),
                         (7.0, "hand calc", "params/thickness.json"))
        self.assertIn("DELIBERATELY", rows["thickness"]["rationale"])

    def test_an_unexplained_model_field_is_flagged_by_every_reader(self):
        """V: a field the model adds with no word about it — no record, so the
        records alone never saw it."""
        root = self.project(silent_field=True)
        self.assertAgree(self.readers(root), ["fillet_r"])
        self.assertEqual(self.report_rows(root), [{"name": "fillet_r", "value": "1.5"}])
        row = next(r for r in self.page(root)["params"] if r["name"] == "fillet_r")
        self.assertEqual((row["value"], row["home"]),
                         (1.5, "model/bracket.py Config.fillet_r"))

    def test_a_record_rationale_defends_a_field_the_model_leaves_silent(self):
        """V: the other direction. A record may carry a rationale where the model
        states none (phase-1.md, Ownership); then no reader — `doctor` included,
        which read the model alone — calls it undefended."""
        why = "the smallest fillet a 0.4 mm nozzle lays down without a seam"
        root = self.project(silent_field=True, records={"fillet_r": {"rationale": why}})
        self.assertAgree(self.readers(root), [])
        row = next(r for r in self.page(root)["params"] if r["name"] == "fillet_r")
        self.assertEqual((row["value"], row["rationale"], row["defended"]), (1.5, why, True))

    def test_the_page_reads_current_until_what_it_shows_moves(self):
        """V: the page's parameters are part of what it judged. Built now, it reads
        current; a docstring edit moves no record and no verdict, and a page that
        still shows the old rationale reads stale, naming the parameter."""
        root = self.project()
        self.page(root)
        info = json.loads(self.run_ok(root, "site", "status", "--json").stdout)
        self.assertEqual((info["stale"], info["stale_reason"]),
                         (False, "current with the records and the verdicts"))
        _replace_once(root, "it comes out at ~0.70mm against a 0.5mm limit.",
                      "it comes out at ~0.70 mm against a 0.5 mm limit.")
        info = json.loads(self.run_ok(root, "site", "status", "--json").stdout)
        self.assertTrue(info["stale"], info)
        self.assertIn("(thickness rationale)", info["stale_reason"])
        self.page(root)
        info = json.loads(self.run_ok(root, "site", "status", "--json").stdout)
        self.assertFalse(info["stale"], info)

    def test_what_moved_names_a_parameter_after_claims_and_verdicts(self):
        """`judgement_moved`'s parameter tier: a value, then the first other field
        in the row's order; claims and verdict rows still come first."""
        row = {"name": "thickness", "value": 7.0, "units": "mm", "rationale": "why 7",
               "defended": True}
        shown = {"claims": [{"id": "C1", "status": "fail"}], "verdicts": [],
                 "params": [dict(row), {"name": "width", "value": 30.0, "units": "mm"}]}
        now = json.loads(json.dumps(shown))
        self.assertEqual(site_mod.judgement_moved(shown, now), [])
        now["params"][0].update(rationale="", defended=False)
        self.assertEqual(site_mod.judgement_moved(shown, now), ["thickness rationale"])
        now["params"][0].update(value=8.0)
        del now["params"][1]
        now["params"].append({"name": "fillet_r", "value": None, "model_error": "boom"})
        self.assertEqual(site_mod.judgement_moved(shown, now),
                         ["thickness 7 mm -> 8 mm", "fillet_r (none) -> (no value)",
                          "width 30 mm -> (none)"])
        now["claims"][0].update(status="pass")
        self.assertEqual(site_mod.judgement_moved(shown, now), ["C1 fail -> pass"])

    def test_a_model_that_does_not_load_is_called_neither(self):
        """V: with the model broken there is no number and no rationale to judge:
        no reader names an undefended parameter from the records alone, the
        report says it does not know rather than listing a record at `None`, and
        the page's rows carry the load error with `defended` null."""
        root = self.project(records={"thickness": {"source": "hand calc"}})
        _append(root, 'raise RuntimeError("the model is broken on purpose")\n')
        said = self.readers(root, loads=False)
        self.assertEqual(said["doctor"], [], "doctor judges no provenance it cannot read")
        self.assertAgree(said, [])
        report_text = self.run_ok(root, "report").stdout
        self.assertNotIn(_REPORT_HEAD, report_text)
        self.assertIn("Parameter rationales are not known: the model does not load",
                      report_text)
        self.assertNotIn("every parameter carries a rationale", report_text)
        payload = site_mod.state(root, store.load(root), None)
        rows = {row["name"]: row for row in payload["params"]}
        self.assertIn("thickness", rows)
        for name, row in rows.items():
            with self.subTest(param=name):
                self.assertIsNone(row["value"])
                self.assertIsNone(row["defended"])
                self.assertIn("the model is broken on purpose", row["model_error"])


if __name__ == "__main__":
    unittest.main()
