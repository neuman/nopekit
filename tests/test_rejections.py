# SPDX-License-Identifier: Apache-2.0
"""Typed rejections (SPEC 14, §0.2): a decision's rejected alternative carries
its type, its person (``by``, a preferred_against's nominee), its return
condition and the input it rests on. The strict reader refuses each malformed
one, naming the entry; a legacy entry reads untyped; the model's PARAMS and a
param record's losers stay text. ``decide`` writes them and never what the
reader would refuse.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import unittest

import _decisions as D
import _env

from nopekit import cli, decisions, modelio, store
from nopekit.models import Ledger, Rejection, ReturnCondition
from nopekit.util import NopekitError

LABEL = "decisions/d.json"


def _read(rejected, *, kind: str = "decisions", stem: str = "d"):
    raw = json.dumps({"title": "t", "rejected": rejected}
                     if kind == "decisions" else {"rejected": rejected}).encode()
    return store._parse_record(f"{kind}/{stem}.json", kind, stem, raw)


class TypedRejectionsAreRead(_env.EnvCase):
    """Every §2.2 rule of the strict reader, each refusal naming the file and the
    entry; a legacy entry reads untyped; the writer omits every default."""

    BASE = {"value": "alu", "why": "Robin prints"}

    def _refused(self, item: dict, said: str) -> None:
        with self.assertRaises(NopekitError) as caught:
            _read([dict(self.BASE), dict(self.BASE, **item)])
        text = str(caught.exception)
        self.assertTrue(text.startswith(f"{LABEL}: "), text)
        self.assertIn("rejected[1]", text)
        self.assertIn(said, text)

    def test_each_rule(self):
        cond = {"claim": "C3", "quantity": "margin", "comparator": "<", "value": 0.1}
        cases = (
            ({"type": "preferred_agianst", "by": "Robin"},
             '.type "preferred_agianst" is not a rejection type (did you mean '
             '"preferred_against"?) — one of infeasible, dominated, preferred_against, '
             'unevaluated'),
            ({"type": "infeasible", "by": "Robin"},
             '.by is for a preferred_against rejection, and this one is "infeasible"'),
            ({"type": "preferred_against", "by": "  "}, ".by must name a person"),
            ({"type": "preferred_against"},
             "is preferred_against and names no one — a preference is a person's: name them "
             'in "by", and they record it with nopekit prefer d'),
            ({"type": "dominated", "returns_if": "C3 margin < 0.1"},
             '.returns_if must be an object — {"claim", "quantity", "comparator", "value"}, '
             'or {"text": "..."}'),
            ({"type": "dominated", "returns_if": dict(cond, text="when")},
             '.returns_if holds "text" beside "claim"'),
            ({"type": "dominated", "returns_if": {"text": " "}}, ".returns_if.text is empty"),
            ({"type": "dominated", "returns_if": {"claim": "C3", "quantity": "margin",
                                                  "comparator": "<"}},
             '.returns_if has no "value" — a structured return condition names the claim'),
            ({"type": "dominated", "returns_if": dict(cond, claim="C 3/")},
             '.returns_if.claim must be a claim id, not "C 3/"'),
            ({"type": "dominated", "returns_if": dict(cond, quantity="ratio")},
             '.returns_if.quantity "ratio" is not one check reads — margin'),
            ({"type": "dominated", "returns_if": dict(cond, comparator="==")},
             '.returns_if.comparator "==" is not a return comparator — one of <, <=, >, >='),
            ({"type": "dominated", "returns_if": dict(cond, value=True)},
             ".returns_if.value must be a number, not true"),
            ({"type": "dominated", "returns_if": dict(cond, value="0.1")},
             '.returns_if.value must be a number, not "0.1"'),
            ({"type": "dominated", "input": "../x"},
             '.input must be an input id (the stem of a file in inputs/), not "../x"'),
            ({"type": "dominated", "returns_if": dict(cond, units="mm")}, 'unknown key "units"'),
            ({"type": "dominated", "recorded": True},
             "a preference is recorded in preferences/<id>.json by `nopekit prefer <id>`"),
            ({"type": "dominated", "who": "Robin"},
             "a preference is recorded in preferences/<id>.json"),
        )
        for item, said in cases:
            with self.subTest(said):
                self._refused(item, said)

    def test_what_reads(self):
        decision = _read([
            {"value": "PLA", "why": "creeps"},
            {"value": "ABS", "why": "warps", "type": "dominated", "returns_if": {}},
            {"value": "alu", "why": "Robin prints", "type": "preferred_against", "by": "Robin",
             "returns_if": {"claim": "C99", "quantity": "limit", "comparator": ">=",
                            "value": 1}, "input": "no-such-input"},
            {"value": "steel", "why": "heavy", "type": "unevaluated",
             "returns_if": {"text": "when weight stops mattering"}}])
        legacy, empty, preferred, text = decision.rejected
        self.assertEqual(legacy.type, "")
        self.assertIsNone(empty.returns_if, '"returns_if": {} reads as absent')
        self.assertEqual(preferred.returns_if, ReturnCondition("C99", "limit", ">=", 1))
        self.assertEqual(preferred.input, "no-such-input",
                         "a dangling pointer reads: an index problem, never a refusal")
        self.assertEqual(text.returns_if.form(), {"text": "when weight stops mattering"})
        self.assertEqual(legacy.to_dict(), {"value": "PLA", "why": "creeps", "evidence": ""})

    def test_the_writer_omits_defaults_and_refuses_what_the_reader_would(self):
        root = D.bracket(os.path.join(self.tmp(), "b"))
        decision = store.load(root).decisions[0]
        path = store.write_record(root, "decisions", decision)
        self.assertIsNone(path, "the bracket's decision re-writes byte for byte")
        written = D.read_json(D.decision_path(root))
        self.assertEqual(sorted(written["rejected"][0]), ["returns_if", "type", "value", "why"])
        bad = decision.__class__(id="bad", title="t", rejected=[
            Rejection(value="alu", why="x", type="preferred_against")])
        with self.assertRaises(NopekitError):
            store.write_record(root, "decisions", bad)
        self.assertFalse(os.path.exists(D.decision_path(root, "bad")))

    def test_a_param_record_keeps_its_losers_text(self):
        for key in ("type", "by", "returns_if", "input"):
            with self.subTest(key), self.assertRaises(NopekitError) as caught:
                _read([{"value": "4 mm", "why": "sags", key: "x"}], kind="params",
                      stem="thickness")
            self.assertIn(f'"{key}" at rejected[0] belongs to a decision\'s rejection — type, '
                          f"by, returns_if and input are written with nopekit decide",
                          str(caught.exception))

    def test_the_models_params_keep_their_losers_text(self):
        import typing
        from nopekit.models import Param, Rejected
        self.assertEqual(typing.get_type_hints(Param)["rejected"], list[Rejected])
        with self.assertRaises(NopekitError) as caught:
            modelio._strict_item({"name": "thickness", "rejected": [
                {"value": "4 mm", "why": "sags", "type": "infeasible"}]}, "model/m.py",
                "PARAMS[0]")
        self.assertIn("type", str(caught.exception))


    def test_the_bracket_log_is_its_records_rendered(self):
        """The bracket's committed ``docs/decisions.md`` is ``render_log`` of its
        decision records, byte for byte, and its header names the one command
        that writes it and the records to edit. What slipped through (review of
        phase 2): the header said ``nopekit decide`` wrote it and to "edit the
        ledger" — the generated index, rebuilt after the next command."""
        from nopekit import decisions
        root = D.bracket(os.path.join(self.tmp(), "b"))
        with open(os.path.join(root, "docs", "decisions.md"), encoding="utf-8") as fh:
            committed = fh.read()
        self.assertEqual(committed, decisions.render_log(store.load(root)),
                         "regenerate it: nopekit report --write, in examples/bracket")
        self.assertIn("Generated by `nopekit report --write` from decisions/ — edit the "
                      "decision records,", committed)
        self.assertNotIn("edit the ledger", committed)
        self.assertNotIn("`nopekit decide`", committed)


class DecideWritesTypedRejections(_env.EnvCase):
    """``decide``'s part-flags apply to the ``--rejected`` before them; each
    refusal writes nothing; what it writes is what the reader reads; ``why``
    shows the facts line under each rejection, and nowhere under a PARAMS row."""

    def setUp(self):
        self.root = D.bracket(os.path.join(self.tmp(), "b"))
        self.before = D.tree(self.root)

    def _decide(self, *argv: str) -> tuple[int, str, str]:
        return D.main(self.root, "decide", "--title", "Keep the arm", "--summary", "s",
                      *argv)

    def test_each_refusal_writes_nothing(self):
        cases = (
            (("--type", "dominated", "--rejected", "a|b"),
             "--type applies to the --rejected before it, and none comes before it"),
            (("--rejected", "a|b", "--type", "dominated", "--type", "infeasible"),
             '--type is given twice for "a"'),
            (("--rejected", "a|b", "--returns-if", "C1 margin > 0.1", "--returns-text", "x"),
             '--returns-if and --returns-text both given for "a"'),
            (("--rejected", "a|b", "--type", "dominant"),
             '--type "dominant" is not a rejection type (did you mean "dominated"?)'),
            (("--rejected", "a|b", "--returns-if", "C1 margin is big"),
             '--returns-if "C1 margin is big" is not a return condition'),
            (("--rejected", "a|b", "--returns-if", "C1 margin > inf"),
             "is not a return condition"),
            (("--rejected", "a|b", "--returns-if", "C9 margin > 0.1"),
             "--returns-if names C9, which is no claim (claims/C9.json)"),
            (("--rejected", "a|b", "--input", "nothing-here"),
             "--input 'nothing-here' is no input (inputs/nothing-here.json)"),
            (("--rejected", "a|b", "--type", "preferred_against"),
             "a preferred_against rejection is a person's preference: name them with --by"),
            (("--rejected", "a|b", "--by", "Robin Okafor"),
             '--by names whose preference a rejection is, and "a" is not --type '
             "preferred_against"),
            (("--rejected", "no reason here", "--type", "dominated"), "has no reason"))
        for argv, said in cases:
            with self.subTest(said):
                code, _out, err = self._decide(*argv)
                self.assertEqual(code, 2, err)
                self.assertIn(said, err)
                self.assertEqual(D.tree(self.root), self.before, "nothing was written")

    def test_the_flags_reach_the_right_loser(self):
        code, out, err = self._decide(
            "--param", "arm_length",
            "--rejected", "80 mm|sags 2.4x", "--type", "infeasible",
            "--returns-if", "C1 margin >= 0.5",
            "--rejected", "50 mm|the pump overhangs", "--returns-text", "with a smaller pump",
            "--type", "dominated",
            "--rejected", "a folded steel arm|Robin prints their own", "--by", "Robin Okafor",
            "--type", "preferred_against",
            "--rejected", "plain|no flags at all")
        self.assertEqual(code, 0, err)
        rows = D.read_json(D.decision_path(self.root, "keep-the-arm"))["rejected"]
        self.assertEqual(rows, [
            {"value": "80 mm", "why": "sags 2.4x", "type": "infeasible",
             "returns_if": {"claim": "C1", "quantity": "margin", "comparator": ">=",
                            "value": 0.5}},
            {"value": "50 mm", "why": "the pump overhangs", "type": "dominated",
             "returns_if": {"text": "with a smaller pump"}},
            {"value": "a folded steel arm", "why": "Robin prints their own",
             "type": "preferred_against", "by": "Robin Okafor"},
            {"value": "plain", "why": "no flags at all"}])
        self.assertIn("  rejected 80 mm: sags 2.4x — infeasible · returns if C1 margin >= 0.5",
                      out)
        self.assertIn("  rejected plain: no flags at all\n", out)
        self.assertIn("Robin Okafor records it in their own shell: nopekit prefer keep-the-arm",
                      out)
        _code, why, _err = D.main(self.root, "why", "keep-the-arm")
        self.assertIn("      infeasible · cannot be evaluated: C1 is failing", why)
        self.assertIn("      dominated · not machine-checkable: with a smaller pump", why)
        self.assertIn("      untyped · no return condition", why)
        _code, why, _err = D.main(self.root, "why", "arm_length")
        self.assertIn("  80 mm — sags 2.4x   (decisions/keep-the-arm.json)\n"
                      "      infeasible · cannot be evaluated: C1 is failing", why)

    def test_the_bracket_decision_reads_in_why(self):
        _code, why, _err = D.main(self.root, "why", "print-in-petg")
        self.assertIn("REJECTED (3)", why)
        self.assertIn("      infeasible · not machine-checkable: when the load is no longer "
                      "sustained", why)
        self.assertIn("      unevaluated · not machine-checkable: when ABS is added", why)
        self.assertIn("      preferred against, not recorded by Robin Okafor · does not hold: "
                      "C3 margin < 0.1, now 0.98", why)
        _code, why, _err = D.main(self.root, "why", "C3")
        self.assertIn("preferred against, not recorded by Robin Okafor", why)
        _code, why, _err = D.main(self.root, "why", "PRINT-IN-PETG")
        self.assertIn("decision  print-in-petg", why)


# --------------------------------------------------------------------------- #
# the example a refusal or a help gives is the bracket's own
# --------------------------------------------------------------------------- #
#: Text files a scan reads; every other file (an image, a mesh) is skipped.
_TEXT = (".py", ".md", ".js", ".mjs", ".html", ".css", ".json", ".txt", ".yaml", ".yml")


def _flat(text: str) -> str:
    """``text`` lower-cased on one line: each line break, with the comment or
    quote marker that opens the next line, reads as one space. So a phrase a
    docstring, a ``#:`` comment or a ``>`` quote wraps is still one phrase."""
    return re.sub(r"\s*\n\s*(?:#:?|//|\*|>)?\s*", " ", text).lower()


class TheExamplesAreTheBracketsOwn(unittest.TestCase):
    """The example of a rejection that ``decide``'s refusals and its help give is
    the bracket's own loser, and no parent story stands where an example is read.
    What slipped through until X1 and its review: these examples echoed the parent
    device's stories ("0.5 mm|the router could not close that net", "a rigid
    hook"), whose one home is docs/ORIGINS.md (CLAUDE.md, "No reference to the
    parent project"). X1 changed the refusals and the help and left the same
    sentence in three spine docstrings and in METHOD.md, and nothing read any of
    them: a merge that took an older side back would have brought the story back
    unseen."""

    #: The bracket's loser as model/bracket.py's PARAMS records it (thickness):
    #: build(Config(thickness=4.0)) deflects 3.75 mm against C1's 0.5 mm limit.
    EXAMPLE = "4.0 mm|it deflected 3.75 mm, 7.5x the limit"
    #: The parent's phrases that stood in these examples. Whole phrases, not
    #: words: "trace" and "router" are nopekit's own words too.
    ECHOES = ("router could not close", "wider trace was tried", "rigid hook",
              "rigid catch")
    #: Where an example of a rejection is read: the spine (its help included),
    #: the doctrine, the docs but docs/ORIGINS.md, and the skills.
    PLACES = ("src/nopekit", "docs", "skills", "METHOD.md", "README.md", "CLAUDE.md")
    ORIGINS = os.path.join("docs", "ORIGINS.md")

    def _echoes(self, text: str) -> list[str]:
        flat = _flat(text)
        return [echo for echo in self.ECHOES if echo in flat]

    def test_each_refusal_names_the_brackets_example(self):
        value, why = self.EXAMPLE.split("|")
        for rejected, said in (([value], f"'{why}'"),
                               ([("", why)], f"('{value}', 'PLA')")):
            with self.subTest(said), self.assertRaises(NopekitError) as caught:
                decisions.add(Ledger(), title="t", summary="s", when="2026-10-15",
                              rejected=rejected)
            self.assertIn(said, str(caught.exception))
            self.assertEqual(self._echoes(str(caught.exception)), [])

    def test_decides_help_gives_it(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = cli.main(["decide", "--help"])
            except SystemExit as exc:           # argparse's own exit, if main lets it out
                code = exc.code
        self.assertEqual(code or 0, 0)
        said = " ".join(out.getvalue().split())
        self.assertIn(f'"{self.EXAMPLE}"', said)
        self.assertNotIn('"0.5 mm|', said)
        self.assertEqual(self._echoes(said), [])

    def test_no_parent_story_stands_where_an_example_is_read(self):
        found = []
        for place in self.PLACES:
            top = os.path.join(_env.REPO, place)
            paths = [top] if os.path.isfile(top) else [
                os.path.join(here, name) for here, _dirs, names in os.walk(top)
                for name in names]
            self.assertTrue(paths, f"{place} holds no file: the scan read nothing")
            for path in sorted(paths):
                rel = os.path.relpath(path, _env.REPO)
                if rel == self.ORIGINS or not rel.endswith(_TEXT):
                    continue
                with open(path, encoding="utf-8", errors="replace") as fh:
                    found += [f"{rel}: {echo}" for echo in self._echoes(fh.read())]
        self.assertEqual(found, [])

    def test_a_wrapped_echo_is_found(self):
        """The scan's own control: the phrase as the spine wrapped it, in a
        docstring, a ``#:`` comment and METHOD.md's quote, is found."""
        self.assertEqual(self._echoes(
            '"the wider trace\n    was tried and the router could not\n    close that net"'),
            ["router could not close", "wider trace was tried"])
        self.assertEqual(self._echoes("#: a rigid\n#: hook was tried"), ["rigid hook"])
        self.assertEqual(self._echoes("> *A rigid catch was tried*"), ["rigid catch"])
        self.assertEqual(self._echoes("the wider trace of the model"), [])


if __name__ == "__main__":
    unittest.main()
