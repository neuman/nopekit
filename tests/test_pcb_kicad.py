# SPDX-License-Identifier: Apache-2.0
"""pcb-kicad, run on real boards: what its selftest could not see.

What slipped through: the pack's first run on real boards (script-generated,
4 layers, about a hundred footprints each, no schematic) found three faults in the
pack, none of which its baseline exercises, and the review of their fixes found
more.

- ``pcb.fab_rules`` counted silk on silk as past the fab's limits. KiCad reports
  silk on silk as ``silk_overlap``, the type the fab's silk-to-pad rule reports
  under, so every overlap ``pcb.drc`` found was counted again, beside the
  findings that were the fab's. Its detail line read "Silkscreen overlap" three times,
  so nobody could see that no copper was past a limit. The baseline has no silk on
  silk. The first fix matched a pad on the file's text alone, and in review a
  silk line across a pad with no id, an uppercase id or a legacy stamp passed,
  where the count before it had failed. Older than these fixes, and found in
  their review: a project that set a rule type to "ignore" hid the fab's own rule
  of that type, a pad's ``(clearance 0.01)`` passed a 0.047 mm gap under the fab's
  0.1 mm, and a microvia's hole breach, reported under another type, passed.
- No tag let a claim bind ``pcb.unconnected`` alone. ``connectivity`` brings in
  ``pcb.parity``, which skips with no schematic, so "every connection is made"
  read Skipped beside a pass.
- ``pcb.net_width`` named the hottest track "narrowest" and one net of a tie, and
  the reader took an arc's chord as its length.

One class runs the real kicad-cli, where it is installed, on the one case a
stand-in cannot show: how KiCad itself resolves a local clearance override.

The fab cases run the real gate through a stand-in ``kicad-cli`` on PATH that
writes a report shaped as kicad-cli 9.0.9 wrote them (silk item first, pad
second), with the baseline's own ids. No KiCad is needed to run this file.

Run:  PYTHONPATH=src:tests python3 -m unittest tests.test_pcb_kicad -v
"""
from __future__ import annotations

import copy
import importlib.util
import json
import math
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

import _env
from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit.modelio import load_path
from nopekit.models import Claim, Ledger, ProjectMeta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(REPO, "packs", "pcb-kicad")
ASSETS = os.path.join(PACK, "selftest", "assets")
BOARD = os.path.join(ASSETS, "esd_guard.kicad_pcb")

#: The pack's own reader, loaded as its gates and fixtures load it.
K = load_path(os.path.join(PACK, "gates", "_kicad.py"))


def _baseline() -> dict:
    with open(os.path.join(PACK, "selftest", "baseline.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _registry() -> gates_mod.Registry:
    registry = gates_mod.Registry()
    packs_mod.load_gates("pcb-kicad", registry, root=REPO, include_env=False,
                         include_user=False)
    return registry


def _run(registry, gid: str, root: str, params: dict):
    spec_fn = registry.get(gid)
    assert spec_fn is not None, f"{gid} is not in the pack"
    ctx = gates_mod.GateContext(
        root=root, ledger=Ledger(meta=ProjectMeta(name="pcb")), model=None,
        params=dict(params), out_dir=os.path.join(root, "out", gid), tier=1,
        log=lambda _m: None, extra={})
    return gates_mod.run_gate(*spec_fn, ctx)


def _footprint(tree, ref: str):
    for fp in K.children(tree, "footprint"):
        if K.footprint_ref(fp)[0] == ref:
            return fp
    raise KeyError(ref)


def _pad(tree, ref: str, number: str):
    for pad in K.children(_footprint(tree, ref), "pad"):
        if len(pad) > 1 and str(pad[1]) == number:
            return pad
    raise KeyError(f"{ref} pad {number}")


def _net_code(tree, name: str) -> str:
    return next(str(n[1]) for n in K.children(tree, "net") if len(n) >= 3 and str(n[2]) == name)


#: The baseline's own items: J1's pad 1 and two of its silkscreen children, so a
#: count that took every id under a footprint for a pad's would be caught.
TREE = K.load_tree(BOARD)
PAD = str(K.value(_pad(TREE, "J1", "1"), "uuid"))
SILK_REF = next(str(K.value(p, "uuid")) for p in K.children(_footprint(TREE, "J1"), "property")
                if p[1] == "Reference")
SILK_LINE = next(str(K.value(ln, "uuid")) for ln in K.children(_footprint(TREE, "J1"), "fp_line")
                 if str(K.value(ln, "layer")) == "F.SilkS")


def _silk_on_silk() -> dict:
    return {"description": "Silkscreen overlap", "severity": "warning", "type": "silk_overlap",
            "items": [{"description": "Segment of J1 on F.Silkscreen", "pos": {"x": 3.9, "y": 2.6},
                       "uuid": SILK_LINE},
                      {"description": "Reference field of J1", "pos": {"x": 4.0, "y": 2.4},
                       "uuid": SILK_REF}]}


def _silk_to_pad(pad: str = PAD, *, pad_first: bool = False,
                 words: str = "PTH pad 1 [DP] of J1", named: bool = False) -> dict:
    """KiCad 9.0.9 lists the silk item first and the pad second. Its words name the
    fab rule ("(fab_silk_to_pad clearance ...)"); here they do not unless ``named``,
    so that the pad's id alone must carry the count."""
    items = [{"description": "Segment of J1 on F.Silkscreen", "pos": {"x": 3.9, "y": 4.0},
              "uuid": SILK_LINE},
             {"description": words, "pos": {"x": 4.0, "y": 4.0}, "uuid": pad}]
    return {"description": "Silkscreen overlap" + (
                " (fab_silk_to_pad clearance 0.1500 mm; actual 0.0000 mm)" if named else ""),
            "severity": "warning", "type": "silk_overlap",
            "items": items[::-1] if pad_first else items}


def _text_height() -> dict:
    return {"description": "Text height out of range (rule 'fab_silk_text_height' min height "
                           "1.0000 mm; actual 0.8000 mm)",
            "severity": "warning", "type": "text_height",
            "items": [{"description": "Reference field of J1", "pos": {"x": 4.0, "y": 2.4},
                       "uuid": SILK_REF}]}


def _board_copy(root: str, text: str, stem: str = "b") -> str:
    """``text`` as ``<root>/<stem>.kicad_pcb``, with the baseline's project file
    and library tables beside it, as the gate stages them."""
    shutil.copyfile(os.path.join(ASSETS, "esd_guard.kicad_pro"),
                    os.path.join(root, stem + ".kicad_pro"))
    for name in ("sym-lib-table", "fp-lib-table"):
        shutil.copyfile(os.path.join(ASSETS, name), os.path.join(root, name))
    path = os.path.join(root, stem + ".kicad_pcb")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _board_text() -> str:
    with open(BOARD, encoding="utf-8") as fh:
        return fh.read()


def _pad_ids_rewritten(fn) -> str:
    """The baseline with every pad's ``(uuid "...")`` passed through ``fn`` (which
    returns the new text, or "" to drop it); no other id is touched."""
    text, out, i = _board_text(), [], 0
    pad_ids = {str(K.value(p, "uuid")) for fp in K.children(TREE, "footprint")
               for p in K.children(fp, "pad")}
    for m in re.finditer(r'\(uuid "([0-9a-f-]+)"\)', text):
        if m.group(1) in pad_ids:
            out.append(text[i:m.start()])
            out.append(fn(m.group(1)))
            i = m.end()
    out.append(text[i:])
    return "".join(out)


@unittest.skipIf(os.name == "nt", "the stand-in kicad-cli is a #! script")
class TheFabCountsOnlyItsOwnFindings(unittest.TestCase):
    """A silk overlap is left to pcb.drc only when shown to be silk on silk."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nopekit-pcb-fab-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = os.path.join(self.dir, "bin")
        os.makedirs(self.bin)
        self.calls = os.path.join(self.dir, "calls")
        self.registry = _registry()
        self.params = _baseline()

    def _fab(self, violations: list, board: str | None = None, *, runs: int = 1):
        # The baseline's own board has custom rules beside it (0.2.0), so a case
        # about a board without them runs on a copy that has none.
        board = board or _board_copy(self.dir, _board_text(), stem="plain")
        report = os.path.join(self.dir, "report.json")
        with open(report, "w", encoding="utf-8") as fh:
            json.dump({"$schema": "https://schemas.kicad.org/drc.v1.json",
                       "coordinate_units": "mm", "kicad_version": "9.0.9",
                       "included_severities": ["error", "warning"],
                       "source": os.path.basename(board), "violations": violations,
                       "unconnected_items": [], "schematic_parity": []}, fh)
        cli = os.path.join(self.bin, "kicad-cli")
        with open(cli, "w", encoding="utf-8") as fh:
            fh.write(f"#!{sys.executable}\n"
                     f"import shutil, sys\n"
                     f"open({self.calls!r}, 'a').write('x')\n"
                     f"shutil.copyfile({report!r}, sys.argv[sys.argv.index('-o') + 1])\n"
                     f"sys.exit({5 if violations else 0})\n")
        os.chmod(cli, os.stat(cli).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        params = dict(self.params, pcb_board=board)
        with mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"]}):
            verdict = _run(self.registry, "pcb.fab_rules", self.dir, params)
        # A stand-in that never ran (a noexec temp dir) skips the gate for the
        # wrong reason; the count keeps that from reading as the fix working.
        ran = 0
        if os.path.exists(self.calls):
            with open(self.calls, encoding="utf-8") as fh:
                ran = len(fh.read())
            os.remove(self.calls)
        self.assertEqual(ran, runs, "the stand-in kicad-cli was not launched as many times "
                                    "as the case expects")
        return verdict

    def test_silk_on_silk_is_pcb_drcs(self):
        verdict = self._fab([_silk_on_silk()])
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertEqual(verdict.measured, 0)
        self.assertIn("1 other finding(s) in the same run are pcb.drc's", verdict.detail)

    def test_silk_to_pad_is_the_fabs_whichever_item_the_pad_is(self):
        for pad_first in (False, True):
            with self.subTest(pad_first=pad_first):
                verdict = self._fab([_silk_to_pad(pad_first=pad_first)])
                self.assertEqual(verdict.outcome, "fail", verdict.render())
                self.assertEqual(verdict.measured, 1)
                self.assertIn("1 finding(s) past its limits: 1x silk_overlap (silk_to_pad)",
                              verdict.detail)

    def test_the_pad_is_found_by_id_not_by_its_words(self):
        """kicad-cli translates item descriptions into the host's language."""
        verdict = self._fab([_silk_to_pad(words="Pastille 1 [DP] de J1")])
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())

    def test_the_rule_named_in_the_words_counts_without_the_pad(self):
        """The run's own rule name is the file's string, never translated: a
        finding that names it is the fab's even where no item is a known pad."""
        verdict = self._fab([_silk_to_pad("00000000-0000-4000-8000-0000000000ff", named=True)])
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())

    def test_a_kicad_7_board_matches_its_pads_by_tstamp(self):
        """The tstamp branch had no input: a pad_ids that read only ``uuid`` passed
        every test and would pass a KiCad 7 board with silk across a pad."""
        text = _board_text().replace("(version 20241229)", "(version 20221018)", 1)
        text = text.replace("(uuid ", "(tstamp ")
        self.assertNotIn("(uuid ", text)
        board = _board_copy(self.dir, text)
        self.assertEqual(K.pad_ids(K.load_tree(board)), K.pad_ids(TREE))
        verdict = self._fab([_silk_on_silk(), _silk_to_pad()], board=board)
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())

    def test_an_id_kicad_rewrites_still_matches(self):
        """kicad-cli 9.0.9 reports an uppercase or undashed UUID lowercase and
        dashed, and an 8-digit legacy stamp as 00000000-0000-0000-0000-0000<stamp>."""
        legacy = iter(range(1000))
        cases = {
            "uppercase": lambda u: f'(uuid "{u.upper()}")',
            "undashed": lambda u: f'(uuid "{u.replace("-", "")}")',
            "legacy_stamp": lambda u: f'(tstamp "{0x5C3A1B00 + next(legacy):08X}")',
        }
        for name, rewrite in cases.items():
            with self.subTest(case=name):
                board = _board_copy(self.dir, _pad_ids_rewritten(rewrite), stem=name)
                tree = K.load_tree(board)
                ids, unknown = K.pad_ids(tree)
                self.assertEqual(unknown, 0)
                written = _pad(tree, "J1", "1")
                written = str(K.value(written, "uuid") or K.value(written, "tstamp"))
                reported = (f"00000000-0000-0000-0000-0000{written.lower()}"
                            if name == "legacy_stamp" else PAD)
                self.assertIn(reported, ids)
                verdict = self._fab([_silk_on_silk(), _silk_to_pad(reported)], board=board)
                self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1),
                                 verdict.render())
                self.assertNotIn("cannot be told", verdict.detail)

    def test_a_pad_kicad_cannot_name_never_lets_silk_pass(self):
        """A pad with no id, or one that is not a UUID, gets a random one from
        KiCad, so no report can name it: every silk overlap is counted then, and
        the line says why. The fab's silk-to-pad finding there carries a pad id
        nobody wrote and, here, no rule name."""
        for name, rewrite in (("no_id", lambda u: ""),
                              ("not_a_uuid", lambda u: '(uuid "pad-x")')):
            with self.subTest(case=name):
                board = _board_copy(self.dir, _pad_ids_rewritten(rewrite), stem=name)
                self.assertEqual(K.pad_ids(K.load_tree(board)), (set(), 14))
                verdict = self._fab([_silk_to_pad("4fdea1bf-0000-4000-8000-000000000001")],
                                    board=board)
                self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1),
                                 verdict.render())
                self.assertIn("1 silk overlap(s) counted although no pad is named in them: "
                              "14 pad(s) on the board get a random id from KiCad",
                              verdict.detail)

    def test_the_line_says_what_kind_each_finding_is_most_first(self):
        verdict = self._fab([_text_height(), _silk_on_silk(), _silk_to_pad(), _silk_to_pad()])
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 3), verdict.render())
        self.assertIn("3 finding(s) past its limits: 2x silk_overlap (silk_to_pad), "
                      "1x text_height (silk_text_height); 1 other finding(s) in the same run "
                      "are pcb.drc's", verdict.detail)
        self.assertNotIn("Silkscreen overlap", verdict.detail)

    def test_ids_kicad_keeps_in_other_spellings_still_match(self):
        """kicad-cli 9.0.9 keeps a braced UUID (without its braces) and a bare
        legacy stamp shorter than 8 digits (zero-filled); a padded id and a braced
        stamp get a random one."""
        short = iter(range(1000))
        braced = iter(range(1000))
        for name, rewrite, unknown in (
                ("braced", lambda u: f'(uuid "{{{u}}}")', 0),
                ("short_stamp", lambda u: f'(tstamp "{0x3A10 + next(short):X}")', 0),
                ("padded", lambda u: f'(uuid " {u}")', 14),
                ("braced_stamp", lambda u: f'(tstamp "{{{0x3A10 + next(braced):X}}}")', 14)):
            with self.subTest(case=name):
                board = _board_copy(self.dir, _pad_ids_rewritten(rewrite), stem=name)
                tree = K.load_tree(board)
                ids, found_unknown = K.pad_ids(tree)
                self.assertEqual((len(ids), found_unknown), (14 - unknown, unknown))
                verdict = self._fab([_silk_on_silk()], board=board)
                if unknown:
                    self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1),
                                     verdict.render())
                    continue
                self.assertEqual((verdict.outcome, verdict.measured), ("pass", 0),
                                 verdict.render())
                written = _pad(tree, "J1", "1")
                written = str(K.value(written, "uuid") or K.value(written, "tstamp"))
                reported = (PAD if name == "braced" else
                            "00000000-0000-0000-0000-0000" + written.lower().zfill(8))
                self.assertIn(reported, ids)
                verdict = self._fab([_silk_to_pad(reported)], board=board)
                self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1),
                                 verdict.render())

    def test_a_microvia_hole_breach_is_the_fabs(self):
        """KiCad 9.0.9 reports the fab's via-hole rule broken on a microvia as
        microvia_drill_out_of_range, with the board's words, not the rule's."""
        verdict = self._fab([{
            "description": "Microvia hole size out of range (board setup constraints min hole "
                           "0.1500 mm; actual 0.1200 mm)",
            "severity": "error", "type": "microvia_drill_out_of_range",
            "items": [{"description": "Micro via [VBUS] on F.Cu - In1.Cu",
                       "pos": {"x": 8.0, "y": 4.0}, "uuid": SILK_LINE}]}])
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())
        self.assertIn("1x microvia_drill_out_of_range (via_hole/pth_hole/npth_hole)",
                      verdict.detail)

    def test_two_holes_on_one_centre_are_the_fabs(self):
        """KiCad 9.0.9 reports a via on top of another as holes_co_located and never
        tests hole_to_hole there."""
        verdict = self._fab([{
            "description": "Drilled holes co-located", "severity": "warning",
            "type": "holes_co_located",
            "items": [{"description": "Via [VBUS] on F.Cu - B.Cu", "pos": {"x": 8.0, "y": 4.0},
                       "uuid": SILK_LINE},
                      {"description": "Via [VBUS] on F.Cu - B.Cu", "pos": {"x": 8.0, "y": 4.0},
                       "uuid": SILK_REF}]}])
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())
        self.assertIn("1x holes_co_located (hole_to_hole/via_hole_to_hole)", verdict.detail)

    def test_a_project_kicad_cannot_read_is_an_error_never_a_run(self):
        """KiCad drops a project file it cannot parse and runs on defaults, the fab's
        clearance floor gone with it: a NaN or an overflowing number is refused."""
        with open(os.path.join(ASSETS, "esd_guard.kicad_pro"), encoding="utf-8") as fh:
            text = fh.read()
        # A value the fab run copies through unchanged: a minimum would be zeroed
        # or floored, and the bad number with it.
        anchor = '"board_outline_line_width": 0.05'
        self.assertEqual(text.count(anchor), 1)
        project = json.loads(text)
        project["board"]["design_settings"]["defaults"]["board_outline_line_width"] = float("nan")
        for name, written in (("nan", json.dumps(project)),
                              ("overflow", text.replace(anchor, '"board_outline_line_width": 1e999'))):
            with self.subTest(case=name):
                board = _board_copy(self.dir, _board_text(), stem=name)
                with open(os.path.join(self.dir, name + ".kicad_pro"), "w",
                          encoding="utf-8") as fh:
                    fh.write(written)
                # Through the stand-in, which must never be launched: the gate
                # errors before kicad-cli runs. (What slipped through: called
                # without it, this case skipped for the missing tool on CI, where
                # no kicad-cli is installed, and passed only where one was.)
                verdict = self._fab([], board=board, runs=0)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn("a value KiCad cannot read", verdict.error)

    def test_a_project_with_rules_of_its_own_never_hears_pcb_drcs(self):
        """The project's own custom rules are left out of the fab run, and one of
        them may waive in pcb.drc what this run reports."""
        board = _board_copy(self.dir, _board_text())
        with open(os.path.join(self.dir, "b.kicad_dru"), "w", encoding="utf-8") as fh:
            fh.write('(version 1)\n(rule "silk_waived"\n\t(constraint silk_clearance)\n'
                     '\t(severity ignore))\n')
        verdict = self._fab([_silk_on_silk()], board=board)
        self.assertEqual((verdict.outcome, verdict.measured), ("pass", 0), verdict.render())
        self.assertIn("1 other finding(s) in the same run are not the fab's: pcb.drc judges "
                      "them under the project's own rules and severities", verdict.detail)
        self.assertNotIn("are pcb.drc's", verdict.detail)

    def test_the_clearance_floor_is_the_fabs(self):
        """The fab run's board minimum clearance is the fab's own, the floor KiCad
        holds a local override to; every other minimum is 0."""
        self._fab([])
        staged = [os.path.join(dp, f) for dp, _dn, fs in os.walk(os.path.join(self.dir, "out"))
                  for f in fs if f == "plain.kicad_pro"]
        self.assertEqual(len(staged), 1, staged)
        with open(staged[0], encoding="utf-8") as fh:
            rules = json.load(fh)["board"]["design_settings"]["rules"]
        self.assertEqual(rules["min_clearance"], 0.1, "JLCPCB, 1-2 layers, 1 oz")
        self.assertEqual({k: v for k, v in rules.items() if k.startswith("min_")
                          and k not in ("min_clearance", "min_resolved_spokes")
                          and isinstance(v, (int, float)) and v != 0}, {})

    def test_a_type_the_project_ignores_is_never_called_pcb_drcs(self):
        """pcb.drc runs under the project's own severities, so silk on silk the
        project ignores, raised for the fab run, is said apart."""
        with open(os.path.join(ASSETS, "esd_guard.kicad_pro"), encoding="utf-8") as fh:
            project = json.load(fh)
        project["board"]["design_settings"]["rule_severities"]["silk_overlap"] = "ignore"
        board = _board_copy(self.dir, _board_text())
        with open(os.path.join(self.dir, "b.kicad_pro"), "w", encoding="utf-8") as fh:
            json.dump(project, fh)
        verdict = self._fab([_silk_on_silk()], board=board)
        self.assertEqual((verdict.outcome, verdict.measured), ("pass", 0), verdict.render())
        self.assertIn("1 other finding(s) of a type the project ignores (1x silk_overlap), "
                      "raised for this run only: pcb.drc does not see them", verdict.detail)
        self.assertNotIn("are pcb.drc's", verdict.detail)

    def test_an_ignored_rule_type_never_relaxes_the_fab(self):
        """KiCad does not test a type the project set to "ignore", even with
        --severity-all; the fab run raises each constrained type back to error."""
        with open(os.path.join(ASSETS, "esd_guard.kicad_pro"), encoding="utf-8") as fh:
            project = json.load(fh)
        severities = project["board"]["design_settings"]["rule_severities"]
        for kind in ("track_width", "silk_overlap", "clearance", "microvia_drill_out_of_range",
                     "holes_co_located", "unconnected_items"):
            severities[kind] = "ignore"
        board = _board_copy(self.dir, _board_text())
        with open(os.path.join(self.dir, "b.kicad_pro"), "w", encoding="utf-8") as fh:
            json.dump(project, fh)
        self._fab([], board=board)
        staged = [os.path.join(dp, f) for dp, _dn, fs in os.walk(os.path.join(self.dir, "out"))
                  for f in fs if f == "b.kicad_pro"]
        self.assertEqual(len(staged), 1, staged)
        with open(staged[0], encoding="utf-8") as fh:
            ran = json.load(fh)["board"]["design_settings"]["rule_severities"]
        raised = ("track_width", "silk_overlap", "clearance", "microvia_drill_out_of_range",
                  "holes_co_located")
        self.assertEqual({k: ran[k] for k in raised}, dict.fromkeys(raised, "error"))
        self.assertEqual(ran["unconnected_items"], "ignore", "not the fab's: left as it was")


@unittest.skipIf(shutil.which("kicad-cli") is None,
                 "how KiCad resolves a local clearance override needs the real kicad-cli")
class ALocalClearanceNeverRelaxesTheFab(unittest.TestCase):
    """The GND track from J1 ends inside U1's ground pad, 0.047 mm from U1 pad 1
    (D+): past JLCPCB's 0.1 mm. A ``(clearance 0.01)`` on that pad, or on U1, must
    not pass it: the fab run's board minimum is the fab's clearance, the one floor
    KiCad holds an override to."""

    SEGMENT = "\t\t(start 4 6.54)\n\t\t(end 13.8625 7.81)\n\t\t(width 0.25)"
    PAD1 = '\t\t\t(pintype "passive")\n\t\t\t(uuid "7372cdbb-0e58-490c-95c6-be877509477d")'
    U1 = '\t\t(uuid "115dd51a-0341-43d9-b8b8-9e756f67ffa9")\n\t\t(at 15 7.81)'

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nopekit-pcb-override-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.registry = _registry()
        text = _board_text()
        for anchor in (self.SEGMENT, self.PAD1, self.U1):
            self.assertEqual(text.count(anchor), 1, f"the baseline moved: {anchor!r}")
        self.near = text.replace(self.SEGMENT,
                                 "\t\t(start 4 6.54)\n\t\t(end 13.6 7.38)\n\t\t(width 0.3)")

    def _fab(self, text: str, stem: str):
        board = _board_copy(self.dir, text, stem=stem)
        return _run(self.registry, "pcb.fab_rules", self.dir,
                    dict(_baseline(), pcb_board=board))

    def test_the_gap_is_past_the_fab_and_an_override_never_hides_it(self):
        pad_override = self.near.replace(self.PAD1, self.PAD1.replace(
            '\t\t\t(uuid', '\t\t\t(clearance 0.01)\n\t\t\t(uuid'))
        footprint_override = self.near.replace(self.U1, self.U1 + "\n\t\t(clearance 0.01)")
        for stem, text in (("no_override", self.near), ("pad_override", pad_override),
                           ("footprint_override", footprint_override)):
            with self.subTest(case=stem):
                verdict = self._fab(text, stem)
                self.assertEqual(verdict.outcome, "fail", verdict.render())
                self.assertIn("x clearance (clearance)", verdict.detail)


class ASilkRuleIsSilkToPad(unittest.TestCase):
    """The count reads a silk overlap as the fab's beside a pad, so the rule the run
    writes must be silk to pad too, or the two would disagree; and the run floors
    local clearance overrides at a row's one clearance rule, so a row has one."""

    def test_a_row_holds_one_clearance_rule_with_no_condition(self):
        one = {"name": "clearance", "constraint": "clearance", "min_mm": 0.1}
        for rules in ([one, dict(one, name="clearance_2", min_mm=0.15)],
                      [dict(one, condition="A.Type == 'Pad' && B.Type == 'Pad'")]):
            with self.subTest(rules=[r["name"] for r in rules]):
                with self.assertRaises(ValueError) as caught:
                    K.dru_text({"name": "t", "rules": rules}, "fab")
                self.assertIn("one clearance rule", str(caught.exception))
        self.assertEqual(K.fab_clearance_floor({"name": "t", "rules": [one]}), 0.1)

    def test_any_other_silk_rule_is_refused(self):
        for condition in (None, "A.Type == 'Text' && B.Type == 'Text'",
                          "B.Type == 'Pad' || A.Type == 'Pad'"):
            with self.subTest(condition=condition):
                rule = {"name": "silk", "constraint": "silk_clearance", "min_mm": 0.15}
                if condition:
                    rule["condition"] = condition
                with self.assertRaises(ValueError) as caught:
                    K.dru_text({"name": "t", "rules": [rule]}, "fab")
                self.assertIn(K.SILK_TO_PAD, str(caught.exception))

    def test_a_silk_rule_at_zero_is_refused(self):
        """At a minimum of 0 KiCad leaves the rule's name out of the finding."""
        rule = {"name": "silk", "constraint": "silk_clearance", "condition": K.SILK_TO_PAD,
                "min_mm": 0}
        with self.assertRaises(ValueError) as caught:
            K.dru_text({"name": "t", "rules": [rule]}, "fab")
        self.assertIn("above 0", str(caught.exception))

    def test_every_bundled_fab_file_writes(self):
        """Every row of every service of every bundled fab file (0.3.0: a fab
        file lists its services, each with its own common rules and rows)."""
        fab_dir = os.path.join(PACK, "gates", "fab")
        for name in sorted(os.listdir(fab_dir)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(fab_dir, name), encoding="utf-8") as fh:
                fab = json.load(fh)
            for service in K.fab_services(fab):
                for row in service["tables"]:
                    with self.subTest(fab=name, service=service["name"], row=row["name"]):
                        rules = list(service["common"]) + list(row["rules"])
                        text = K.dru_text({"name": row["name"], "rules": rules}, "fab")
                        if any(r["constraint"] == "silk_clearance" for r in rules):
                            self.assertIn(f'(condition "{K.SILK_TO_PAD}")', text)


class TheHottestTrackIsNamed(unittest.TestCase):
    """``pcb.net_width``'s line names the hottest stretch (its width, the layers it
    runs on, its length along the copper, the same copper once) and up to four
    stretches as hot, then how many more; never "narrowest". Expected lengths are
    worked here from the tracks' ends, not read back from the reader the gate uses.
    On the baseline VBUS runs on B.Cu as one track, and GND on both sides."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nopekit-pcb-width-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.registry = _registry()
        self.params = _baseline()

    @staticmethod
    def _segments(tree, code: str, layer: str):
        return [s for s in K.children(tree, "segment")
                if K.value(s, "net") == code and str(K.value(s, "layer")) == layer]

    def _length(self, net: str, layer: str) -> float:
        """The length of the baseline's ``net`` segments on ``layer``, from their ends."""
        total = 0.0
        for seg in self._segments(TREE, _net_code(TREE, net), layer):
            (x0, y0), (x1, y1) = ((float(K.child(seg, e)[1]), float(K.child(seg, e)[2]))
                                  for e in ("start", "end"))
            total += math.hypot(x1 - x0, y1 - y0)
        return total

    def _width(self, edit=None, net: str = "VBUS") -> str:
        tree = K.load_tree(BOARD)
        if edit:
            edit(tree, _net_code(tree, net))
        board = os.path.join(self.dir, "w.kicad_pcb")
        with open(board, "w", encoding="utf-8") as fh:
            fh.write(K.dump(tree))
        verdict = _run(self.registry, "pcb.net_width", self.dir,
                       dict(self.params, pcb_board=board))
        self.assertNotIn("narrowest", verdict.detail)
        return verdict.detail

    def test_the_baseline_names_both_nets_of_its_tie(self):
        """VBUS and GND both carry 0.5 A on 0.25 mm outer copper: equally hot."""
        detail = self._width()
        gnd = self._length("GND", "F.Cu") + self._length("GND", "B.Cu")
        self.assertTrue(detail.startswith(
            f"VBUS: hottest track 0.25 mm on B.Cu ({self._length('VBUS', 'B.Cu'):.1f} mm) "
            f"rises "), detail)
        self.assertIn(f"; as hot: GND 0.25 mm on B.Cu, F.Cu ({gnd:.1f} mm)", detail)

    def test_a_stretch_hotter_only_past_the_printed_digit_is_named_too(self):
        """GND at 0.501 A runs a hair hotter than VBUS at 0.5 A on the same copper.
        Both print as the same rise, so a person who widened only GND would read
        the same number again: the line names both."""
        a = K.temp_rise_c(0.5, 0.25, 1.0, True)
        b = K.temp_rise_c(0.501, 0.25, 1.0, True)
        self.assertTrue(b > a and round(a, 1) == round(b, 1), (a, b))
        self.params["pcb_net_currents_a"] = {"VBUS": 0.5, "GND": 0.501}
        detail = self._width()
        self.assertTrue(detail.startswith("GND: hottest track 0.25 mm on B.Cu, F.Cu ("), detail)
        self.assertIn("; as hot: VBUS 0.25 mm on B.Cu (", detail)

    def test_past_four_ties_the_line_counts_the_rest(self):
        """Every net at 0.5 A, and one segment of each net that runs as more than
        one moved inside at the width that heats as 0.25 mm outside: seven
        stretches as hot (VBUS, one track, stays outside). The line names four
        after the first, counts the other two, and the evidence lists all seven."""
        rise = K.temp_rise_c(0.5, 0.25, 1.0, True)
        inner = round(K.width_for_rise_mm(0.5, rise, 0.5, False), 4)
        self.assertEqual(round(K.temp_rise_c(0.5, inner, 0.5, False), 1), round(rise, 1))
        nets = ("DM", "DP", "GND", "VBUS")
        split = ("DM", "DP", "GND")
        self.params["pcb_net_currents_a"] = dict.fromkeys(nets, 0.5)
        tree = K.load_tree(BOARD)
        self.assertEqual(len([s for s in K.children(tree, "segment")
                              if K.value(s, "net") == _net_code(tree, "VBUS")]), 1,
                         "the baseline's VBUS runs as more than one track now: move one "
                         "of its segments inside too, and count eight")
        for net in split:
            code = _net_code(tree, net)
            seg = next(s for s in K.children(tree, "segment") if K.value(s, "net") == code)
            K.child(seg, "layer")[1] = K.Q("In1.Cu")
            K.child(seg, "width")[1] = f"{inner:g}"
        board = os.path.join(self.dir, "ties.kicad_pcb")
        with open(board, "w", encoding="utf-8") as fh:
            fh.write(K.dump(tree))
        verdict = _run(self.registry, "pcb.net_width", self.dir,
                       dict(self.params, pcb_board=board))
        self.assertEqual(verdict.detail.count("; as hot: "), 4, verdict.detail)
        self.assertIn("; and 2 more stretch(es) as hot (evidence: as_hot)", verdict.detail)
        with open(verdict.evidence[0], encoding="utf-8") as fh:
            as_hot = json.load(fh)["as_hot"]
        self.assertEqual(sorted((h["net"], h["side"]) for h in as_hot),
                         sorted([(n, side) for n in split for side in ("inner", "outer")]
                                + [("VBUS", "outer")]))

    def test_a_tie_outside_the_fitted_range_is_named_with_its_net(self):
        """GND at 36 A on a track wide enough to heat as VBUS's 0.5 A on 0.25 mm:
        as hot, and past IPC-2221's fitted current and width, whichever of the two
        is the hotter below the printed digit."""
        rise = K.temp_rise_c(0.5, 0.25, 1.0, True)
        wide = round(K.width_for_rise_mm(36.0, rise, 1.0, True), 4)
        self.params["pcb_net_currents_a"] = {"VBUS": 0.5, "GND": 36.0}
        def edit(tree, _code):
            gnd = _net_code(tree, "GND")
            for seg in K.children(tree, "segment"):
                if K.value(seg, "net") == gnd:
                    K.child(seg, "width")[1] = f"{wide:g}"
        detail = self._width(edit)
        self.assertIn("as hot: ", detail)
        self.assertIn(f"outside IPC-2221's fitted range: a {wide:g} mm track (GND), 36 A (GND)",
                      detail)

    def test_a_wider_inner_track_is_the_hottest(self):
        """GND's front copper moved inside and widened to 0.3 mm: 0.25 mm still runs
        on the back, and the 0.3 mm inner track is the hotter. "Narrowest 0.3 mm"
        would be false."""
        def edit(tree, code):
            for seg in self._segments(tree, code, "F.Cu"):
                K.child(seg, "layer")[1] = K.Q("In1.Cu")
                K.child(seg, "width")[1] = "0.3"
        detail = self._width(edit, net="GND")
        self.assertTrue(detail.startswith(
            f"GND: hottest track 0.3 mm on In1.Cu ({self._length('GND', 'F.Cu'):.1f} mm) "
            f"rises "), detail)
        self.assertIn("IPC-2221 internal", detail)

    def test_the_same_width_inside_is_named_apart_from_outside(self):
        """GND's front copper moved inside at its 0.25 mm: the back's 0.25 mm, GND's
        and VBUS's, is the cooler copper and is no part of the hottest stretch."""
        def edit(tree, code):
            for seg in self._segments(tree, code, "F.Cu"):
                K.child(seg, "layer")[1] = K.Q("In1.Cu")
        detail = self._width(edit, net="GND")
        self.assertTrue(detail.startswith(
            f"GND: hottest track 0.25 mm on In1.Cu ({self._length('GND', 'F.Cu'):.1f} mm) "
            f"rises "), detail)
        self.assertNotIn("B.Cu", detail)

    def test_a_wider_track_on_the_same_side_is_no_part_of_it(self):
        """GND's back copper widened to 0.5 mm: only its front's 0.25 mm is as hot
        as VBUS's back, and the wider copper on the same side is no part of it."""
        def edit(tree, code):
            for seg in self._segments(tree, code, "B.Cu"):
                K.child(seg, "width")[1] = "0.5"
        detail = self._width(edit, net="GND")
        self.assertTrue(detail.startswith(
            f"VBUS: hottest track 0.25 mm on B.Cu ({self._length('VBUS', 'B.Cu'):.1f} mm) "
            f"rises "), detail)
        self.assertIn(f"; as hot: GND 0.25 mm on F.Cu ({self._length('GND', 'F.Cu'):.1f} mm)",
                      detail)

    def test_an_arc_is_measured_along_it_and_counted_once(self):
        """A 0.2 mm half-circle of radius 2 mm on VBUS, written twice: 2 pi mm of
        copper (6.3), not its 4 mm chord, and not 12.6."""
        def edit(tree, code):
            for _ in range(2):
                tree.append(["arc", ["start", "10", "20"], ["mid", "12", "22"],
                             ["end", "14", "20"], ["width", "0.2"], ["layer", K.Q("F.Cu")],
                             ["net", code], ["uuid", K.Q("00000000-0000-4000-8000-0000000000aa")]])
        detail = self._width(edit)
        self.assertTrue(detail.startswith(
            f"VBUS: hottest track 0.2 mm on F.Cu ({2 * math.pi:.1f} mm) rises "), detail)


class ABoardWithNoSchematicCanClaimItsCopper(unittest.TestCase):
    """``copper-connectivity`` binds ``pcb.unconnected`` and nothing else."""

    def test_the_tag_binds_one_gate(self):
        registry = _registry()
        self.assertEqual([s.id for s in registry.for_claim("C1", ["copper-connectivity"])],
                         ["pcb.unconnected"])
        # Why it is needed: the broader tag brings in the gate that needs a schematic.
        self.assertEqual(sorted(s.id for s in registry.for_claim("C1", ["connectivity"])),
                         ["pcb.parity", "pcb.unconnected"])


class TheTagTableIsTheGates(unittest.TestCase):
    """PACK.md's claim-tag table says which gates each tag binds; it is held to the
    gates' own ``claims``, so a tag added to a gate cannot go unsaid there."""

    def setUp(self):
        self.gates = {s.id: {c.lower() for c in s.claims} for s in _registry().specs()}

    def _table(self) -> dict[str, set[str]]:
        with open(os.path.join(PACK, "PACK.md"), encoding="utf-8") as fh:
            text = fh.read()
        section = text.split("## Claim tags", 1)[1].split("\n## ", 1)[0]
        table = {}
        for line in section.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != 2 or not cells[0].startswith("`"):
                continue
            bound = (set(self.gates) if cells[1].startswith("every gate")
                     else set(re.findall(r"`([^`]+)`", cells[1])))
            for tag in re.findall(r"`([^`]+)`", cells[0]):
                table[tag] = bound
        return table

    def test_every_row_names_exactly_the_gates_that_list_it(self):
        table = self._table()
        tags = set().union(*self.gates.values())
        self.assertEqual(set(table), tags, "a tag a gate lists is missing from the table, "
                                           "or the table names one no gate lists")
        for tag, bound in table.items():
            with self.subTest(tag=tag):
                self.assertEqual(bound, {g for g, c in self.gates.items() if tag in c})


# --------------------------------------------------------------------------- #
# 0.2.0: what each gate passed over in silence, and the five it gained
# --------------------------------------------------------------------------- #
DECK = os.path.join(ASSETS, "vbus_droop.cir")
POWER_BOARD = os.path.join(ASSETS, "power_board.kicad_pcb")
SYMBOLS = next((d for d in (os.environ.get("KICAD9_SYMBOL_DIR", ""), "/usr/share/kicad/symbols")
                if d and os.path.isfile(os.path.join(d, "Device.kicad_sym"))), None)

#: The words kicad-cli 9.0.9 gave an open pin's pad in a parity report, on a
#: generated board with a written schematic (measured): pcb.parity reads the
#: ``(unconnected-(`` in them, KiCad's net name, which is not translated.
OPEN_PIN_WORDS = "Pad missing net given by schematic (unconnected-(J1-SBU1-PadA8))."


def _stand_in(bin_dir: str, name: str, body: str) -> str:
    """A stand-in tool ``name`` on ``bin_dir``: a Python script, ``body`` its code."""
    path = os.path.join(bin_dir, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"#!{sys.executable}\n{body}")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _deck(root: str, name: str, edit) -> str:
    with open(DECK, encoding="utf-8") as fh:
        text = edit(fh.read())
    path = os.path.join(root, name + ".cir")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _before_tran(card: str):
    return lambda text: text.replace(".tran 1u 4m", f"{card}\n.tran 1u 4m")


def _window(new: str):
    return lambda text: text.replace("from=0.5m to=4m", new)


def _meas(new: str):
    return lambda text: text.replace(".meas tran vbus_min MIN v(j2) from=0.5m to=4m", new)


@unittest.skipIf(os.name == "nt", "the stand-in ngspice is a #! script")
class NgspiceIsHeldToTheDeckAsWritten(unittest.TestCase):
    """ngspice-42 takes an option name it does not know without a word (``tmep=127``
    runs at 27 C, exit 0), reads every card that begins ``.opt`` as ``.options``,
    and measures a ``.meas tran`` window past the run as 0 V, exit 0. Both refusals
    come before ngspice is launched, so a stand-in that prints the baseline's
    value shows them, and counts that it was never run."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nopekit-pcb-spice-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = os.path.join(self.dir, "bin")
        os.makedirs(self.bin)
        self.calls = os.path.join(self.dir, "calls")
        _stand_in(self.bin, "ngspice",
                  f"open({self.calls!r}, 'a').write('x')\n"
                  f"print('vbus_min            =  4.832500e+00 at=  1.095200e-03')\n")
        self.registry = _registry()

    def _spice(self, name: str, edit):
        params = dict(_baseline(), pcb_spice_deck=_deck(self.dir, name, edit))
        with mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"]}):
            verdict = _run(self.registry, "pcb.spice", self.dir, params)
        ran = 0
        if os.path.exists(self.calls):
            with open(self.calls, encoding="utf-8") as fh:
                ran = len(fh.read())
            os.remove(self.calls)
        return verdict, ran

    def test_an_option_name_ngspice_does_not_know_is_refused_before_the_run(self):
        for name, card, word in (("tmep", ".options tmep=127", "tmep"),
                                 ("foo", ".options foo", "foo"),
                                 ("optoins", ".optoins tmep=1", "tmep"),
                                 ("continued", ".options reltol=1e-3\n+ tmep=127", "tmep")):
            with self.subTest(card=card):
                verdict, ran = self._spice(name, _before_tran(card))
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn(f"without a word: {word}", verdict.error)
                self.assertEqual(ran, 0)

    def test_an_option_in_an_included_file_is_read_too(self):
        with open(os.path.join(self.dir, "opts.inc"), "w", encoding="utf-8") as fh:
            fh.write(".options tmep=127\n")
        verdict, ran = self._spice("included", _before_tran(".include opts.inc"))
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("tmep (in opts.inc", verdict.error)
        self.assertEqual(ran, 0)

    def test_an_option_command_in_a_control_block_is_read_too(self):
        """ngspice-42 takes ``option tmep=127`` in a .control block as silently as
        on a card: 27 C, exit 0 (measured: 4.8325 V, against 4.62 V at 127 C)."""
        control = lambda opt: (lambda text: text.replace(
            ".end", f".control\n{opt}\nrun\n.endc\n.end"))
        verdict, ran = self._spice("ctrl_typo", control("option tmep=127"))
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("without a word: tmep", verdict.error)
        self.assertEqual(ran, 0)
        verdict, ran = self._spice("ctrl_ok", control("options temp=127"))
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertEqual(ran, 1)

    def test_a_measure_two_cards_define_is_refused(self):
        """ngspice prints a value per card and the gate reads the first; a window
        past the run put before a good one read 0 V past a guard that checked the
        last (in review)."""
        good = ".meas tran vbus_min MIN v(j2) from=0.5m to=4m"
        verdict, ran = self._spice("twice", _meas(
            ".meas tran vbus_min MIN v(j2) from=5m to=6m\n" + good))
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("2 .meas cards define 'vbus_min'", verdict.error)
        self.assertEqual(ran, 0)
        with open(os.path.join(self.dir, "meas.inc"), "w", encoding="utf-8") as fh:
            fh.write(".meas tran vbus_min MIN v(j2) from=5m to=6m\n")
        verdict, ran = self._spice("twice_inc", _before_tran(".include meas.inc"))
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("meas.inc", verdict.error)
        self.assertEqual(ran, 0)

    def test_known_options_run(self):
        verdict, ran = self._spice("known", _before_tran(".options reltol=1e-3 temp=127"))
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertEqual(ran, 1)

    def test_a_window_outside_the_run_is_refused_before_the_run(self):
        for name, window in (("past", "from=5m to=6m"), ("reversed", "from=2m to=1m"),
                             ("seconds", "from=0.5m to=4")):
            with self.subTest(window=window):
                verdict, ran = self._spice(name, _window(window))
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn("not inside the 0.004 s run", verdict.error)
                self.assertEqual(ran, 0)

    def test_a_window_it_cannot_read_is_refused(self):
        for name, edit in (("param", _window("from={t0} to=4m")),
                           ("elsewhere", _meas("* the .meas is elsewhere"))):
            with self.subTest(case=name):
                verdict, ran = self._spice(name, edit)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn("cannot show the window was simulated", verdict.error)
                self.assertEqual(ran, 0)

    def test_spice_numbers_read_as_ngspice_reads_them(self):
        number = self.registry.get("pcb.spice")[1].__globals__["spice_number"]
        for text, want in (("0.5m", 5e-4), ("4MEG", 4e6), ("4ms", 4e-3), ("4", 4.0),
                           ("1M", 1e-3), ("10u", 1e-5), ("2.5e-3", 2.5e-3)):
            with self.subTest(text=text):
                self.assertAlmostEqual(number(text), want, places=15)
        self.assertIsNone(number("{tstop}"))


@unittest.skipIf(shutil.which("ngspice") is None, "needs the real ngspice")
class NgspiceErrorsAreErrors(unittest.TestCase):
    """What ngspice-42 itself prints ``Error:`` on: a ``.meas op`` (it has none), a
    WHEN that never happens, a FIND past the run (refused before the run here)."""

    def test_each_errors_and_the_baseline_passes(self):
        root = tempfile.mkdtemp(prefix="nopekit-pcb-ngspice-")
        self.addCleanup(shutil.rmtree, root, True)
        registry = _registry()
        for name, edit in (("meas_op", _meas(".meas op vbus_min FIND v(j2)")),
                           ("meas_never", _meas(".meas tran vbus_min WHEN v(j2)=9")),
                           ("find_past_end", _meas(".meas tran vbus_min FIND v(j2) AT=5m")),
                           # A misspelled command in .control: printed and skipped,
                           # exit 0 (measured).
                           ("no_command", lambda t: t.replace(
                               ".end", ".control\nopt temp=127\nrun\n.endc\n.end"))):
            with self.subTest(case=name):
                verdict = _run(registry, "pcb.spice", root,
                               dict(_baseline(), pcb_spice_deck=_deck(root, name, edit)))
                self.assertEqual(verdict.outcome, "error", verdict.render())
        verdict = _run(registry, "pcb.spice", root, dict(_baseline(), pcb_spice_deck=DECK))
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertAlmostEqual(verdict.measured, 4.8325, places=4)


class AProbeIsWrittenForEveryRuleAndProperty(unittest.TestCase):
    """The probes ``pcb.drc`` appends to a project's rules file."""

    def test_b_is_probed_as_a_and_a_function_only_through_its_rule(self):
        # A rule of this test's own: an A-only term beside an OR that reads B.
        # (Z1: the rule here was the parent device's own rule file line, word
        # for word under another name; docs/ORIGINS.md is its one home.)
        rules = K.dru_rules("(version 1)\n# a comment (with a paren\n"
                            "(rule edge (layer outer) (condition \"A.NetClass == 'Power' && "
                            "(A.Type == 'Via' || B.Type == 'Via')\") "
                            "(constraint edge_clearance (min 0.5mm)))\n"
                            "(rule plated (condition \"A.isPlated()\") "
                            "(constraint hole_size (min 0.3mm)))\n"
                            "(rule open (constraint track_width (min 0.1mm)))\n")
        probes = K.probe_rules(rules)
        self.assertEqual([(p["kind"], p["what"], p["reads_b"]) for p in probes],
                         [("rule", "edge", False), ("rule", "plated", False),
                          ("rule", "open", False), ("property", "NetClass", False),
                          ("property", "Type", False)])
        self.assertIn("(layer outer)", probes[0]["text"])
        # A rule that reads B is probed on the terms that read A alone.
        self.assertEqual(probes[0]["a_side"], "A.NetClass == 'Power'")
        self.assertIn("(condition \"A.NetClass == 'Power'\")", probes[0]["text"])
        self.assertIn("(constraint assertion \"false\")", probes[1]["text"])
        self.assertNotIn("condition", probes[2]["text"])
        self.assertIn("A.Type == A.Type", probes[4]["text"])

    def test_a_rule_with_no_a_side_is_only_shown_live(self):
        rules = K.dru_rules("(rule pads (condition \"A.Type == 'Pad' || B.Type == 'Pad'\") "
                            "(constraint clearance (min 0.1mm)))")
        probe = K.probe_rules(rules)[0]
        self.assertTrue(probe["reads_b"])
        self.assertIsNone(probe["a_side"])

    def test_conjuncts_keep_quotes_and_parentheses_whole(self):
        self.assertEqual(K.conjuncts("(A.NetName == 'a && b' && (A.x || B.y) && B.z)"),
                         ["A.NetName == 'a && b'", "(A.x || B.y)", "B.z"])
        self.assertEqual(K.conjuncts("(A.a) || (B.b)"), ["(A.a) || (B.b)"])

    def test_a_file_that_does_not_parse_raises(self):
        with self.assertRaises(K.KicadFormatError):
            K.dru_rules("(version 1)\n(rule x (condition \"A.NetName == 'DP'\")")


@unittest.skipIf(os.name == "nt", "the stand-in kicad-cli is a #! script")
class AProbeThatNeverFiresIsAnError(unittest.TestCase):
    """Through a stand-in kicad-cli, on CI: the verdict run is clean, and the probe
    runs report what the stand-in says."""

    #: Writes the report ``-o`` names. In the probe stage, a probe rule fires when
    #: the rules file holds it alone (``mode`` "alone"), or never ("silent").
    CLI = ("import json, os, re, sys\n"
           "out = sys.argv[sys.argv.index('-o') + 1]\n"
           "board = sys.argv[-1]\n"
           "open({calls!r}, 'a').write('x')\n"
           "names = []\n"
           "dru = board[:-len('.kicad_pcb')] + '.kicad_dru'\n"
           "if 'pcb_drc_probe' in board and os.path.exists(dru):\n"
           "    names = re.findall(r'\\(rule (nopekit_\\w+)', open(dru).read())\n"
           "fire = names if ({mode!r} == 'alone' and len(names) == 1) else []\n"
           "v = [dict(type='assertion_failure', severity='error', items=[],\n"
           "          description=\"Assertion failure (rule '%s')\" % n) for n in fire]\n"
           "json.dump(dict(violations=v, unconnected_items=[], schematic_parity=[]), open(out, 'w'))\n"
           "sys.exit(5 if v else 0)\n")

    def _drc(self, mode: str):
        root = tempfile.mkdtemp(prefix="nopekit-pcb-probe-")
        self.addCleanup(shutil.rmtree, root, True)
        bin_dir = os.path.join(root, "bin")
        os.makedirs(bin_dir)
        calls = os.path.join(root, "calls")
        _stand_in(bin_dir, "kicad-cli", self.CLI.format(calls=calls, mode=mode))
        board = _board_copy(root, _board_text())
        shutil.copyfile(os.path.join(ASSETS, "esd_guard.kicad_dru"),
                        os.path.join(root, "b.kicad_dru"))
        with mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]}):
            verdict = _run(_registry(), "pcb.drc", root, dict(_baseline(), pcb_board=board))
        with open(calls, encoding="utf-8") as fh:
            return verdict, len(fh.read())

    def test_a_probe_report_with_nothing_in_it_errors(self):
        verdict, ran = self._drc("silent")
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("KiCad dropped the whole rules file", verdict.error)
        self.assertEqual(ran, 3, "the verdict run, the probe run and the canary")

    def test_a_probe_masked_beside_the_others_is_live_alone(self):
        verdict, ran = self._drc("alone")
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertIn("2 shown live", verdict.detail)
        self.assertEqual(ran, 5, "the verdict run, the probe run, the canary, two alone")


@unittest.skipIf(shutil.which("kicad-cli") is None, "needs the real kicad-cli")
class EveryCustomRuleMustSelectSomething(unittest.TestCase):
    """KiCad 9.0.9, on the baseline board: a rule it cannot evaluate gives a clean
    run, exit 0 and empty stderr. The probes make each one an error."""

    def _drc(self, rules: str):
        root = tempfile.mkdtemp(prefix="nopekit-pcb-rules-")
        self.addCleanup(shutil.rmtree, root, True)
        board = _board_copy(root, _board_text())
        with open(os.path.join(root, "b.kicad_dru"), "w", encoding="utf-8") as fh:
            fh.write("(version 1)\n" + rules + "\n")
        return _run(_registry(), "pcb.drc", root, dict(_baseline(), pcb_board=board))

    def test_each_silent_rule_errors_and_the_live_ones_pass(self):
        cases = (
            ("(rule usb_pair (condition \"A.NetNmae == 'DP'\") "
             "(constraint track_width (min 0.2mm)))",
             "rule usb_pair selects nothing on this board"),
            ("(rule usb_pair (condition \"A.NetName == 'DP'\") "
             "(constraint track_widht (min 0.2mm)))",
             "KiCad dropped the whole rules file"),
            ("(rule usb_pair (condition \"A.NetName == 'DP' && !(A.Pin_Nmae == 'zz')\") "
             "(constraint track_width (min 0.2mm)))",
             "property Pin_Nmae: no item has it"),
            # A two-item clearance rule with its net misspelled on the A side: in
            # review it passed, its rule "not shown live" because it reads B.
            ("(rule hv_gap (condition \"A.NetName == 'VBUSS' && B.NetName == 'GND'\") "
             "(constraint clearance (min 3mm)))",
             "its A side, A.NetName == 'VBUSS', selects no item"),
        )
        for rules, said in cases:
            with self.subTest(said=said):
                verdict = self._drc(rules)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn(said, verdict.error)
        verdict = self._drc(
            "(rule pa (condition \"A.NetName == 'DP' && A.Type == 'Track'\") "
            "(constraint track_width (min 0.2mm)))\n"
            "(rule pb (condition \"A.Type == 'Track'\") (constraint track_width (min 0.2mm)))")
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertIn("4 shown live", verdict.detail)


def _strip_pin_names(text: str) -> str:
    tree = K.parse(text)
    for fp in K.children(tree, "footprint"):
        for pad in K.children(fp, "pad"):
            pad[:] = [x for x in pad if not (isinstance(x, list)
                                             and K.head(x) in ("pinfunction", "pintype"))]
    return K.dump(tree) + "\n"


class PinNamesComeFromTheSchematic(unittest.TestCase):
    """A board a script generated carries no pinfunction. With a schematic beside
    it, pcb.pin_names reads the names from the schematic's symbols, so the
    baseline with every pinfunction stripped gives the verdicts it gave before."""

    def _pin_names(self, text: str, *, schematic: bool):
        root = tempfile.mkdtemp(prefix="nopekit-pcb-names-")
        self.addCleanup(shutil.rmtree, root, True)
        board = _board_copy(root, text)
        if schematic:
            shutil.copyfile(os.path.join(ASSETS, "esd_guard.kicad_sch"),
                            os.path.join(root, "b.kicad_sch"))
        return _run(_registry(), "pcb.pin_names", root, dict(_baseline(), pcb_board=board))

    def test_stripped_boards_read_the_same_through_the_schematic(self):
        tree = K.load_tree(BOARD)
        net = K.child(_pad(tree, "U1", "5"), "net")
        net[1:] = [_net_code(tree, "VBUS"), K.Q("VBUS")]
        split = K.dump(tree) + "\n"
        for name, text, outcome in (("baseline", _board_text(), "pass"),
                                    ("split_pin_pair", split, "fail")):
            with self.subTest(board=name):
                stripped = _strip_pin_names(text)
                self.assertNotIn("pinfunction", stripped)
                verdict = self._pin_names(stripped, schematic=True)
                self.assertEqual(verdict.outcome, outcome, verdict.render())
                self.assertEqual(verdict.outcome,
                                 self._pin_names(text, schematic=False).outcome)
                verdict = self._pin_names(stripped, schematic=False)
                self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("U1 COM: GND on 2, VBUS on 5",
                      self._pin_names(_strip_pin_names(split), schematic=True).detail)


def _lib_name_schematic() -> str:
    """The baseline schematic with U1's symbol cached under a ``lib_name``, as
    eeschema writes a placed symbol that differs from its library: the cache
    entry ``ESDA6V1BC6_1``, its units ``ESDA6V1BC6_1_<unit>_<style>``, and
    ``(lib_name "ESDA6V1BC6_1")`` on the placed symbol (kicad-cli 9.0.9's ERC
    reads it with 0 violations: measured)."""
    with open(os.path.join(ASSETS, "esd_guard.kicad_sch"), encoding="utf-8") as fh:
        tree = K.parse(fh.read())
    for sym in K.children(K.child(tree, "lib_symbols"), "symbol"):
        if str(sym[1]) == "Power_Protection:ESDA6V1BC6":
            sym[1] = K.Q("ESDA6V1BC6_1")
            for unit in K.children(sym, "symbol"):
                unit[1] = K.Q(str(unit[1]).replace("ESDA6V1BC6_", "ESDA6V1BC6_1_", 1))
    for sym in K.children(tree, "symbol"):
        if str(K.value(sym, "lib_id")) == "Power_Protection:ESDA6V1BC6":
            sym.insert(1, ["lib_name", K.Q("ESDA6V1BC6_1")])
    return K.dump(tree) + "\n"


class APartCachedUnderItsLibNameKeepsItsNames(unittest.TestCase):
    """In review: names looked up by ``lib_id`` alone read none for a part KiCad
    caches under ``lib_name``, and erased its pads' pinfunction with them, so the
    ESD array's shorted pin pair, which the pads alone had shown, passed."""

    def test_the_split_pair_fails_with_and_without_pinfunction(self):
        tree = K.load_tree(BOARD)
        K.child(_pad(tree, "U1", "5"), "net")[1:] = [_net_code(tree, "VBUS"), K.Q("VBUS")]
        split = K.dump(tree) + "\n"
        for name, text in (("pinfunction", split), ("stripped", _strip_pin_names(split))):
            with self.subTest(board=name):
                root = tempfile.mkdtemp(prefix="nopekit-pcb-libname-")
                self.addCleanup(shutil.rmtree, root, True)
                board = _board_copy(root, text)
                with open(os.path.join(root, "b.kicad_sch"), "w", encoding="utf-8") as fh:
                    fh.write(_lib_name_schematic())
                verdict = _run(_registry(), "pcb.pin_names", root,
                               dict(_baseline(), pcb_board=board))
                self.assertEqual(verdict.outcome, "fail", verdict.render())
                self.assertIn("U1 COM: GND on 2, VBUS on 5", verdict.detail)


@unittest.skipIf(os.name == "nt", "the stand-in kicad-cli is a #! script")
class AnOpenPinOnAPadWithNoNetIsAgreement(unittest.TestCase):
    """KiCad 9.0.9 names every open schematic pin's net ``unconnected-(...)`` and
    reports the netless pad under it as a parity difference. Matched by the pad's
    id: a pad that HAS a net still counts."""

    def _parity(self, pad_net: bool):
        root = tempfile.mkdtemp(prefix="nopekit-pcb-open-")
        self.addCleanup(shutil.rmtree, root, True)
        tree = K.load_tree(BOARD)
        pad = _pad(tree, "J2", "1")
        if not pad_net:
            pad.remove(K.child(pad, "net"))
        board = _board_copy(root, K.dump(tree) + "\n")
        shutil.copyfile(os.path.join(ASSETS, "esd_guard.kicad_sch"),
                        os.path.join(root, "b.kicad_sch"))
        item = {"type": "net_conflict", "severity": "warning", "description": OPEN_PIN_WORDS,
                "items": [{"description": "Pad 1 [<no net>] of J2", "pos": {"x": 26, "y": 4},
                           "uuid": str(K.value(pad, "uuid"))}]}
        report = os.path.join(root, "report.json")
        with open(report, "w", encoding="utf-8") as fh:
            json.dump({"violations": [], "unconnected_items": [],
                       "schematic_parity": [item]}, fh)
        bin_dir = os.path.join(root, "bin")
        os.makedirs(bin_dir)
        _stand_in(bin_dir, "kicad-cli", f"import shutil, sys\n"
                  f"shutil.copyfile({report!r}, sys.argv[sys.argv.index('-o') + 1])\n"
                  f"sys.exit(5)\n")
        with mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]}):
            return _run(_registry(), "pcb.parity", root, dict(_baseline(), pcb_board=board))

    def test_a_netless_pad_agrees_and_a_netted_one_differs(self):
        verdict = self._parity(pad_net=False)
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertIn("1 open pin(s) sit on pads with no net", verdict.detail)
        verdict = self._parity(pad_net=True)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertEqual(verdict.measured, 1)

    def test_the_words_are_kicads(self):
        self.assertIn("(unconnected-(", OPEN_PIN_WORDS)


def _proposer(board: str, symbols: dict, out_dir: str, cwd: str):
    path = os.path.join(cwd, "s.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(symbols, fh)
    return _env.run([sys.executable, os.path.join(PACK, "proposers", "schematic_from_board.py"),
                     "--board", board, "--symbols", path], cwd=cwd,
                    env={"NOPEKIT_PROPOSAL_DIR": out_dir})


def _esd_symbols() -> dict:
    with open(os.path.join(ASSETS, "esd_guard.symbols.json"), encoding="utf-8") as fh:
        return json.load(fh)


class TheWriterRefusesWhatAPersonMustState(unittest.TestCase):
    """No KiCad needed: an unmapped part exits 2, names the part and writes nothing."""

    def test_an_unmapped_part_exits_2_and_writes_nothing(self):
        root = tempfile.mkdtemp(prefix="nopekit-pcb-writer-")
        self.addCleanup(shutil.rmtree, root, True)
        out = os.path.join(root, "out")
        os.makedirs(out)
        symbols = _esd_symbols()
        del symbols["symbols"]["U1"]
        proc = _proposer(BOARD, symbols, out, root)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("U1 (ESDA6V1BC6, footprint Package_TO_SOT_SMD:SOT-23-6) matches no "
                      "symbol", proc.stderr)
        self.assertEqual(os.listdir(out), [])

    def test_a_statement_with_a_misspelled_key_is_refused(self):
        root = tempfile.mkdtemp(prefix="nopekit-pcb-writer-")
        self.addCleanup(shutil.rmtree, root, True)
        out = os.path.join(root, "out")
        os.makedirs(out)
        proc = _proposer(BOARD, dict(_esd_symbols(), no_conect={}), out, root)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("unknown key(s) ['no_conect']", proc.stderr)
        self.assertEqual(os.listdir(out), [])


@unittest.skipIf(SYMBOLS is None or shutil.which("kicad-cli") is None,
                 "needs KiCad's stock symbols and the real kicad-cli")
class AWrittenSchematicIsJudgedByKiCad(unittest.TestCase):
    """The writer on the baseline board: KiCad's ERC and parity find nothing, the
    bytes repeat, a map whose symbol lacks a pad's pin is refused, and a label
    moved after writing is the difference parity reports."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nopekit-pcb-written-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.board = _board_copy(self.root, _board_text())

    def _write(self, symbols: dict, name: str):
        out = os.path.join(self.root, name)
        os.makedirs(out)
        return _proposer(self.board, symbols, out, self.root), out

    def _adopt(self, out: str):
        for name in ("b.kicad_sch", "sym-lib-table"):
            shutil.copyfile(os.path.join(out, name), os.path.join(self.root, name))

    def test_erc_and_parity_find_nothing_and_the_bytes_repeat(self):
        (one, a), (two, b) = self._write(_esd_symbols(), "a"), self._write(_esd_symbols(), "b")
        self.assertEqual((one.returncode, two.returncode), (0, 0), one.stderr + two.stderr)
        self.assertEqual(sorted(os.listdir(a)), ["b.kicad_sch", "b.schematic.json",
                                                 "sym-lib-table"])
        with open(os.path.join(a, "b.kicad_sch"), "rb") as fa, \
                open(os.path.join(b, "b.kicad_sch"), "rb") as fb:
            self.assertEqual(fa.read(), fb.read())
        self._adopt(a)
        registry = _registry()
        params = dict(_baseline(), pcb_board=self.board)
        for gid in ("pcb.erc", "pcb.parity", "pcb.pin_names"):
            with self.subTest(gate=gid):
                verdict = _run(registry, gid, self.root, params)
                self.assertEqual(verdict.outcome, "pass", verdict.render())
        # The writer's check: one label moved after writing, U1 pin 6 from DP to DM.
        sch = os.path.join(self.root, "b.kicad_sch")
        tree = K.load_tree(sch)
        u1 = next(s for s in K.children(tree, "symbol")
                  if any(len(p) > 2 and p[1] == "Reference" and p[2] == "U1"
                         for p in K.children(s, "property")))
        x, y = float(K.child(u1, "at")[1]), float(K.child(u1, "at")[2])
        lib = next(s for s in K.children(K.child(tree, "lib_symbols"), "symbol")
                   if str(s[1]) == "Power_Protection:ESDA6V1BC6")
        pins = {}

        def walk(node):
            for c in node:
                if isinstance(c, list):
                    if K.head(c) == "pin":
                        pins[str(K.value(c, "number"))] = K.child(c, "at")
                    walk(c)
        walk(lib)
        end = (x + float(pins["6"][1]), y - float(pins["6"][2]))
        label = next(lab for lab in K.children(tree, "global_label")
                     if abs(float(K.child(lab, "at")[1]) - end[0]) < 1e-6
                     and abs(float(K.child(lab, "at")[2]) - end[1]) < 1e-6)
        self.assertEqual(str(label[1]), "DP")
        label[1] = K.Q("DM")
        with open(sch, "w", encoding="utf-8") as fh:
            fh.write(K.dump(tree) + "\n")
        verdict = _run(registry, "pcb.parity", self.root, params)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertEqual(verdict.measured, 1)

    def test_a_symbol_without_the_pads_pins_is_refused(self):
        symbols = _esd_symbols()
        symbols["symbols"]["U1"] = "Device:R"
        proc, out = self._write(symbols, "wrong")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("U1 pad 3 carries DM, and Device:R has no pin 3", proc.stderr)
        self.assertEqual(os.listdir(out), [])


@unittest.skipIf(SYMBOLS is None, "needs KiCad's stock symbols")
class AStackedPinTakesOneLabel(unittest.TestCase):
    """U1's two COM pins sit on one point of its symbol. The writer puts one GND
    label there, and no id in the file repeats; nor in the baseline's own
    schematic. (What slipped through, when the baseline's part changed to one with
    stacked pins: the written file held one label's uuid5 twice, and KiCad's ERC
    and parity passed it.)"""

    def test_one_label_on_the_stacked_pins_and_no_id_twice(self):
        root = tempfile.mkdtemp(prefix="nopekit-pcb-stacked-")
        self.addCleanup(shutil.rmtree, root, True)
        out = os.path.join(root, "out")
        os.makedirs(out)
        board = _board_copy(root, _board_text())
        proc = _proposer(board, _esd_symbols(), out, root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for path in (os.path.join(out, "b.kicad_sch"),
                     os.path.join(ASSETS, "esd_guard.kicad_sch")):
            with self.subTest(schematic=os.path.basename(os.path.dirname(path))):
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                ids = re.findall(r'\(uuid "([^"]+)"\)', text)
                self.assertEqual(sorted(i for i in set(ids) if ids.count(i) > 1), [])
        with open(os.path.join(out, "b.kicad_sch"), encoding="utf-8") as fh:
            written = K.parse(fh.read())
        lib = next(sym for sym in K.children(K.child(written, "lib_symbols"), "symbol")
                   if str(sym[1]) == "Power_Protection:ESDA6V1BC6")
        ends = {}

        def walk(node):
            for c in node:
                if isinstance(c, list):
                    if K.head(c) == "pin":
                        at = K.child(c, "at")
                        ends[str(K.value(c, "number"))] = (float(at[1]), float(at[2]))
                    walk(c)
        walk(lib)
        self.assertEqual(ends["2"], ends["5"], "the library no longer stacks COM")
        u1 = next(sym for sym in K.children(written, "symbol")
                  if any(len(p) > 2 and p[1] == "Reference" and p[2] == "U1"
                         for p in K.children(sym, "property")))
        x, y = float(K.child(u1, "at")[1]), float(K.child(u1, "at")[2])
        point = (x + ends["2"][0], y - ends["2"][1])
        there = [str(lab[1]) for lab in K.children(written, "global_label")
                 if abs(float(K.child(lab, "at")[1]) - point[0]) < 1e-6
                 and abs(float(K.child(lab, "at")[2]) - point[1]) < 1e-6]
        self.assertEqual(there, ["GND"])


#: A one-symbol schematic around a derived stock symbol, as the pack's writer
#: caches it: run in a child, because the writer's module puts the pack's gates/
#: on sys.path and switches bytecode off for the process that imports it. Every
#: pin carries a no-connect flag. Prints what the cached symbol holds.
_DERIVED = r"""
import json, os, sys
sys.dont_write_bytecode = True
proposers, symbols, out, lib, child, parent = sys.argv[1:7]
sys.path.insert(0, proposers)
import _schematic as W
K = W.K
sym = W.library_symbol(symbols, lib, child)
root = W.uid("derived:root")
pins = W.pins(sym)
x = y = 101.6
items = [W.placed(f"{lib}:{child}", "U1", child, "", x, y, sorted(pins), "derived:U1",
                  project="d", root_uuid=root)]
items += [W.no_connect(x + px, y - py, f"derived:nc:{n}")
          for n, (px, py) in sorted(pins.items())]
with open(os.path.join(out, "d.kicad_sch"), "w", encoding="utf-8") as fh:
    fh.write(K.dump(W.sheet(root, [sym], items)) + "\n")
heads = [K.head(x) for x in sym if isinstance(x, list)]
print(json.dumps({
    "name": str(sym[1]), "extends": K.value(sym, "extends"),
    "value": next(str(p[2]) for p in K.children(sym, "property") if p[1] == "Value"),
    "units": [str(u[1]) for u in K.children(sym, "symbol")],
    "properties_first": heads.index("symbol") > max(i for i, h in enumerate(heads)
                                                    if h == "property"),
    "pins": W.pins(sym), "parent_pins": W.pins(W.library_symbol(symbols, lib, parent))}))
"""


@unittest.skipIf(SYMBOLS is None, "needs KiCad's stock symbols")
class ADerivedSymbolIsFlattenedAsEeschemaCachesIt(unittest.TestCase):
    """A stock symbol that ``extends`` another is cached flattened: its own name
    and properties, its parent's body and pins, the units renamed to it. KiCad's
    ERC, at every severity, finds nothing in a schematic that holds it. (What
    slipped through, in this pack's ORIGINS scrub: the baseline's old ESD array
    was the only derived symbol any test read, so the swap to a part that extends
    nothing left the flattening unexercised. NCP349MNAE extends NCP349MN in
    KiCad 9's Power_Protection library. And every assertion on the cache holds
    for a symbol that was flat to begin with, when its pins match the parent's,
    so the raw library is read first: in review, a library update that drew the
    child standalone would have left this test green and the flattening untested
    again.)"""

    LIB, CHILD, PARENT = "Power_Protection", "NCP349MNAE", "NCP349MN"

    def setUp(self):
        with open(os.path.join(SYMBOLS, f"{self.LIB}.kicad_sym"), encoding="utf-8") as fh:
            raw = K.parse(fh.read())
        entry = next((s for s in K.children(raw, "symbol") if str(s[1]) == self.CHILD), None)
        self.assertIsNotNone(entry, f"the stock {self.LIB} library no longer holds "
                                    f"{self.CHILD}: pick another derived symbol")
        self.assertEqual(str(K.value(entry, "extends")), self.PARENT,
                         f"the stock {self.LIB} library no longer derives {self.CHILD} "
                         f"from {self.PARENT}: pick another symbol that extends one")
        self.root = tempfile.mkdtemp(prefix="nopekit-pcb-derived-")
        self.addCleanup(shutil.rmtree, self.root, True)
        proc = _env.run([sys.executable, "-c", _DERIVED, os.path.join(PACK, "proposers"),
                         SYMBOLS, self.root, self.LIB, self.CHILD, self.PARENT],
                        cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.cached = json.loads(proc.stdout)

    def test_the_cache_is_the_child_on_its_parents_body(self):
        c = self.cached
        self.assertEqual(c["name"], f"{self.LIB}:{self.CHILD}")
        self.assertIsNone(c["extends"])
        self.assertEqual(c["value"], self.CHILD)
        self.assertTrue(c["units"])
        self.assertTrue(all(u.startswith(self.CHILD + "_") for u in c["units"]), c["units"])
        self.assertTrue(c["properties_first"])
        self.assertTrue(c["pins"])
        self.assertEqual(c["pins"], c["parent_pins"])

    @unittest.skipIf(shutil.which("kicad-cli") is None, "ERC needs the real kicad-cli")
    def test_erc_finds_nothing_in_it(self):
        shutil.copyfile(os.path.join(ASSETS, "esd_guard.kicad_pro"),
                        os.path.join(self.root, "d.kicad_pro"))
        with open(os.path.join(self.root, "sym-lib-table"), "w", encoding="utf-8") as fh:
            fh.write(f'(sym_lib_table\n\t(version 7)\n\t(lib (name "{self.LIB}")(type "KiCad")'
                     f'(uri "${{KICAD9_SYMBOL_DIR}}/{self.LIB}.kicad_sym")(options "")'
                     f'(descr ""))\n)\n')
        report = os.path.join(self.root, "erc.json")
        proc = _env.run(["kicad-cli", "sch", "erc", "--severity-all", "--format", "json",
                         "-o", report, os.path.join(self.root, "d.kicad_sch")], cwd=self.root,
                        env={"KICAD9_SYMBOL_DIR": SYMBOLS})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(report, encoding="utf-8") as fh:
            found = [(v["type"], v["description"]) for sheet in json.load(fh)["sheets"]
                     for v in sheet["violations"]]
        self.assertEqual(found, [])


class TheLandIsPairedByFunction(unittest.TestCase):
    """pcb.land_pattern pairs pads by pin function, never by number."""

    def setUp(self):
        self.registry = _registry()
        self.fit = self.registry.get("pcb.land_pattern")[1].__globals__["_land_fit"]

    def test_an_led_numbered_the_other_way_fits_by_its_cathode(self):
        """An LED whose vendor numbers its cathode pad 2; KiCad's LED_0603 numbers it 1.
        Turned 90 degrees, the drawing fits the land with no residual, cathode on
        cathode."""
        board = [("K", K.LandPad("1", -0.7875, 0.0, 0.875, 0.95)),
                 ("A", K.LandPad("2", 0.7875, 0.0, 0.875, 0.95))]
        drawing = [{"number": "2", "function": "K", "x": 0.0, "y": -0.7875, "w": 0.95, "h": 0.875},
                   {"number": "1", "function": "A", "x": 0.0, "y": 0.7875, "w": 0.95, "h": 0.875}]
        fit = self.fit("D1", board, drawing)
        self.assertEqual([(p["function"], p["board_pad"], p["drawing_pad"]) for p in fit["pairs"]],
                         [("A", "2", "1"), ("K", "1", "2")])
        self.assertAlmostEqual(fit["worst"]["residual_mm"], 0.0, places=6)
        self.assertAlmostEqual(abs(fit["rotation_deg"]), 90.0, places=6)

    def _land(self, text: str, drawings=None, schematic=False):
        root = tempfile.mkdtemp(prefix="nopekit-pcb-land-")
        self.addCleanup(shutil.rmtree, root, True)
        board = _board_copy(root, text)
        if schematic:
            shutil.copyfile(os.path.join(ASSETS, "esd_guard.kicad_sch"),
                            os.path.join(root, "b.kicad_sch"))
        drawings = drawings or {"U1": os.path.join(ASSETS, "esda6v1bc6_land.json")}
        return _run(self.registry, "pcb.land_pattern", root,
                    dict(_baseline(), pcb_board=board, pcb_land_drawings=drawings))

    def test_a_footprint_on_the_back_passes(self):
        tree = K.load_tree(BOARD)
        u1 = _footprint(tree, "U1")
        K.child(u1, "layer")[1] = K.Q("B.Cu")
        for pad in K.children(u1, "pad"):
            at = K.child(pad, "at")
            at[2] = f"{-float(at[2]):g}"
        verdict = self._land(K.dump(tree) + "\n")
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertLess(verdict.measured, 0.02)

    def test_a_pad_with_no_name_is_never_paired_by_number(self):
        verdict = self._land(_strip_pin_names(_board_text()))
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("never pairs by number", verdict.error)
        # The schematic names them, and the fit runs.
        verdict = self._land(_strip_pin_names(_board_text()), schematic=True)
        self.assertEqual(verdict.outcome, "pass", verdict.render())

    def test_a_drawing_that_does_not_say_its_y_axis_is_refused(self):
        with open(os.path.join(ASSETS, "esda6v1bc6_land.json"), encoding="utf-8") as fh:
            record = json.load(fh)
        del record["y_axis"]
        root = tempfile.mkdtemp(prefix="nopekit-pcb-land-")
        self.addCleanup(shutil.rmtree, root, True)
        path = os.path.join(root, "d.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        verdict = self._land(_board_text(), {"U1": path})
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("y_axis", verdict.error)


def _move_pad_net(tree, ref: str, number: str, net_name: str) -> None:
    """Pad ``number`` of ``ref`` on a new net of its own, added to the table."""
    nets = K.children(tree, "net")
    node = ["net", str(max(int(n[1]) for n in nets) + 1), K.Q(net_name)]
    tree.insert(tree.index(nets[-1]) + 1, node)
    K.child(_pad(tree, ref, number), "net")[1:] = [node[1], K.Q(net_name)]


def _power_params(board: str = POWER_BOARD) -> dict:
    """The baseline's params, every power, mating and USB-C record pointed at
    ``board`` (a battery's or an external far side names no board), and
    ``pcb_board`` made absolute: ``pcb.usbc_receptacle`` reads it for a
    receptacle no record names."""
    params = copy.deepcopy(_baseline())
    params["pcb_board"] = os.path.join(PACK, params["pcb_board"])
    for key in ("pcb_batteries", "pcb_protectors", "pcb_conductors", "pcb_matings", "pcb_usbc",
                "pcb_rails"):
        for rec in params[key].values():
            for item in ([rec["a"], rec["b"]] if key == "pcb_matings" else [rec]):
                if "board" in item:
                    item["board"] = board
    return params


def _set_value(tree, ref: str, value: str) -> None:
    for prop in K.children(_footprint(tree, ref), "property"):
        if len(prop) >= 3 and prop[1] == "Value":
            prop[2] = K.Q(value)
            return
    raise KeyError(ref)


#: A schematic that places one symbol, ``TH1``, on ``lib_id``: all the
#: defeat check reads (KiCad 9's root sheet form, cut to what is read).
_ONE_SYMBOL_SCH = """(kicad_sch
  (version 20250114)
  (generator "eeschema")
  (uuid "00000000-0000-4000-8000-0000000000a1")
  (paper "A4")
  (lib_symbols)
  (symbol
    (lib_id "{lib_id}")
    (at 100 100 0)
    (uuid "00000000-0000-4000-8000-0000000000a2")
    (property "Reference" "TH1" (at 100 95 0))
    (property "Value" "NTC 10k B4250" (at 100 105 0))
  )
)
"""


class _PowerCase(unittest.TestCase):
    """Runs the battery rules on the baseline's power board, or on a copy of it
    edited by a test, with the baseline's records or a test's edit of them."""

    def setUp(self):
        self.registry = _registry()
        self.power = load_path(os.path.join(PACK, "gates", "power.py"))
        self.helpers = load_path(os.path.join(PACK, "gates", "_power.py"))
        self.root = tempfile.mkdtemp(prefix="nopekit-pcb-power-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def board(self, edit=None, stem: str = "edited") -> str:
        tree = K.load_tree(POWER_BOARD)
        if edit is not None:
            edit(tree)
        path = os.path.join(self.root, stem + ".kicad_pcb")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(K.dump(tree) + "\n")
        return path

    def run_gate(self, gid: str, edit=None, records=None, board: str | None = None):
        params = _power_params(board or (self.board(edit) if edit else POWER_BOARD))
        if records is not None:
            records(params)
        return _run(self.registry, gid, self.root, params)


POWER_GATES = ("pcb.charge_temperature", "pcb.charge_limits", "pcb.fault_current",
               "pcb.discharge_cutoff")


class ThePowerRulesReadTheirRecords(_PowerCase):
    """Records held to the board and read strictly; the baseline's values."""

    def test_the_formula_gives_iecs_k_factors(self):
        """k = K sqrt(ln((limit + beta) / (start + beta))): IEC 60364-5-54's table."""
        for start, limit, k in ((70, 160, 115.0), (90, 250, 143.1), (70, 140, 102.8),
                                (90, 160, 99.9)):
            with self.subTest(start=start, limit=limit):
                got = math.sqrt(self.power.adiabatic_s(1.0, 1.0, start, limit))
                self.assertAlmostEqual(got, k, delta=0.05)
        # references/battery.md's worked point: 0.03 mm^2, 25 -> 85 C, 10 A.
        self.assertAlmostEqual(self.power.adiabatic_s(10.0, 0.03, 25, 85), 0.0956, places=4)

    def test_the_baseline_passes_each_at_its_value(self):
        """The design's numbers, recomputed: 5.34..43.47 C against 0..45 C; 4.232 V
        of 4.25 V; 1.45 x 1 A against a 1 A trip; 2.85 V against 2.5 V."""
        for gid, measured, units, said in (
                ("pcb.charge_temperature", 1.53, "K", "charging allowed 5.34..43.47 C"),
                ("pcb.charge_limits", 0.9958, "ratio", "holds 4.232 V of the cell's 4.25 V"),
                ("pcb.fault_current", 1.45, "ratio", "trips by 1 A, against 1.45 x 1 A"),
                ("pcb.discharge_cutoff", 2.85, "V", "cuts it off at 2.85 V per cell")):
            with self.subTest(gate=gid):
                verdict = self.run_gate(gid)
                self.assertEqual(verdict.outcome, "pass", verdict.render())
                self.assertEqual((verdict.measured, verdict.units), (measured, units))
                self.assertIn(said, verdict.detail)

    def test_a_charger_with_no_safety_timer_passes(self):
        """The branch failed every charger with no timer; IEC 62368-1 M.4.2 asks
        for none. The baseline's MCP73833-AMI has its timer disabled."""
        self.assertIs(_baseline()["pcb_protectors"]["charger"]["functions"]["safety_timer"],
                      False)
        self.assertEqual(self.run_gate("pcb.charge_temperature").outcome, "pass")

    def test_a_record_for_another_design_errors(self):
        def fuse_value(p):
            p["pcb_protectors"]["fuse"]["value"] = "PTC_OTHER"

        def charger_ref(p):
            p["pcb_protectors"]["charger"]["ref"] = "U9"

        def battery_pin(p):
            p["pcb_batteries"]["main"]["terminals"]["pos"] = "7"
        for gid, records, said in (
                ("pcb.fault_current", fuse_value, "'s F1 is PTC_FIXTURE_0A5, the record's "
                                                  "PTC_OTHER"),
                ("pcb.charge_temperature", charger_ref, "has no U9"),
                ("pcb.discharge_cutoff", battery_pin, "has no pad 7")):
            with self.subTest(gate=gid):
                verdict = self.run_gate(gid, records=records)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn("is for another design", verdict.error)
                self.assertIn(said, verdict.error)

    def test_a_record_read_loosely_errors(self):
        """An unknown key names the nearest known one; a list of records, a
        typical-only threshold, an empty source and a flag spelled as a word are
        refused."""
        def misspelt(p):
            p["pcb_protectors"]["charger"]["functions"]["charge_temp"]["ntc"]["r25_tol"] = 1

        def as_list(p):
            p["pcb_batteries"] = list(p["pcb_batteries"].values())

        def typical(p):
            p["pcb_protectors"]["charger"]["functions"]["charge_temp"]["sense"]["cold_v"] = 1.23

        def no_source(p):
            p["pcb_protectors"]["protector"]["source"] = " "

        def worded(p):
            p["pcb_protectors"]["charger"]["functions"]["safety_timer"] = "false"
        for gid, records, said in (
                ("pcb.charge_temperature", misspelt, "unknown key 'r25_tol' (did you mean "
                                                     "'r25_tol_pct'?)"),
                ("pcb.fault_current", as_list, "pcb_batteries is a list"),
                ("pcb.charge_temperature", typical, "is one number, 1.23: give the datasheet "
                                                    "[min, max]"),
                ("pcb.discharge_cutoff", no_source, "pcb_protectors.protector's source is "
                                                    "empty"),
                ("pcb.charge_limits", worded, "give true or false")):
            with self.subTest(gate=gid, said=said):
                verdict = self.run_gate(gid, records=records)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn(said, verdict.error)

    def test_a_part_not_fitted_is_not_counted(self):
        """dnp on the NTC, the charger, the cutoff, its RC resistor or the fuse."""
        for gid, ref, said in (("pcb.charge_temperature", "TH1", "TH1 ("),
                               ("pcb.charge_temperature", "U1", "U1 ("),
                               ("pcb.discharge_cutoff", "U2", "U2 ("),
                               ("pcb.discharge_cutoff", "R5", "R5 ("),
                               ("pcb.fault_current", "F1", "F1 (")):
            for attr in K.NOT_FITTED:
                with self.subTest(gate=gid, ref=ref, attr=attr):
                    def edit(tree):
                        K.child(_footprint(tree, ref), "attr").append(attr)
                    verdict = self.run_gate(gid, edit)
                    self.assertEqual(verdict.outcome, "fail", verdict.render())
                    self.assertIn(said, verdict.detail)
                    self.assertIn("marked not fitted", verdict.detail)

    def test_a_board_the_guard_would_fail_errors_and_a_newer_one_skips(self):
        """A record may name a board pcb.board_readable never reads, so each rule
        makes the guard's checks itself: older than KiCad 6 errors, and newer
        than the reader was checked through skips, in the guard's own words."""
        older = self.board(lambda t: K.child(t, "version").__setitem__(1, "20171130"), "old")
        newer = self.board(lambda t: K.child(t, "version").__setitem__(1, "20991231"), "new")
        for gid in POWER_GATES:
            with self.subTest(gate=gid, board="older than KiCad 6"):
                verdict = self.run_gate(gid, board=older)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn("not one this reader can vouch for", verdict.error)
                self.assertIn("older than", verdict.error)
            with self.subTest(gate=gid, board="newer than the reader"):
                verdict = self.run_gate(gid, board=newer)
                self.assertEqual(verdict.outcome, "skipped", verdict.render())
                self.assertIn("this reader was checked through board format 20241229 "
                              "(KiCad 9.0); the file is format 20991231",
                              verdict.skip_reason)

    def test_no_battery_skips_and_says_so(self):
        for gid in POWER_GATES:
            with self.subTest(gate=gid):
                verdict = self.run_gate(gid, records=lambda p: p.pop("pcb_batteries"))
                self.assertEqual(verdict.outcome, "skipped", verdict.render())
                self.assertIn("no pcb_batteries", verdict.skip_reason)

    def test_a_board_value_is_read_as_a_resistor_is_labelled(self):
        """``1M`` is a megohm on a resistor (it is a milliohm to SPICE, which is
        why pcb.py's spice_number is not reused); ``4k7`` is RKM; a value with a
        tolerance in it is refused, never guessed."""
        r = self.helpers.resistance
        for text, ohms in (("2k", 2000.0), ("4k7", 4700.0), ("1M", 1e6), ("330", 330.0),
                           ("330R", 330.0), ("4R7", 4.7), ("5.1k", 5100.0), ("1M5", 1.5e6),
                           ("100 ohm", 100.0), ("10K", 10000.0)):
            with self.subTest(value=text):
                self.assertAlmostEqual(r(text), ohms)
        for text in ("10k 1%", "PTC_FIXTURE_0A5", "", "k"):
            with self.subTest(value=text):
                self.assertIsNone(r(text))


class ThePowerBaselineHasItsSchematic(_PowerCase):
    """The power board and its schematic travel together. The known-good reads
    TH1's symbol, a known-bad copy that swaps a part carries the schematic with
    the part swapped there too, and KiCad says each schematic is its board's.
    (What slipped through P5: the board travelled alone, so the known-good never
    reached the defeat check, and ``ntc_swapped``'s copy under the control's out
    dir had no file beside it: the gate asked for one that nothing in its run
    wrote, an opaque read, so its qualification was never current
    (``tests.test_cache.OutDirReads``).)"""

    #: The fixtures that state their schematic, and the part each swaps there
    #: (None: the schematic as it is). The others move a net and write none.
    WITH_SCHEMATIC = {"ntc_swapped": ("TH1", "NTC 100k B4250"),
                      "fuse_trips_late": ("F1", "PTC_FIXTURE_1A0"), "lfp_cell": None}

    def _fixture(self, name: str) -> str:
        bad = load_path(os.path.join(PACK, "selftest", "bad_boards.py"))
        ctx = gates_mod.GateContext(
            root=self.root, ledger=Ledger(meta=ProjectMeta(name="pcb")), model=None,
            params={}, out_dir=os.path.join(self.root, "out"), tier=1,
            log=lambda _m: None, extra={})
        return getattr(bad, name)(ctx).params["pcb_batteries"]["main"]["board"]

    @staticmethod
    def _values(path: str) -> dict:
        """{ref: Value} of every part a board or a schematic places (a ``#``
        ref, a power flag, places none)."""
        tree = K.load_tree(path)
        found = {}
        for node in K.children(tree, "footprint") + K.children(tree, "symbol"):
            props = {str(p[1]): str(p[2]) for p in K.children(node, "property") if len(p) >= 3}
            if not props.get("Reference", "#").startswith("#"):
                found[props["Reference"]] = props.get("Value")
        return found

    def test_the_known_good_reads_its_ntcs_symbol(self):
        verdict = self.run_gate("pcb.charge_temperature")
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertNotIn("was not read", verdict.detail)
        self.assertNotIn("places no TH1", verdict.detail)
        sch = POWER_BOARD[:-len(".kicad_pcb")] + ".kicad_sch"
        self.assertEqual(self._values(sch), self._values(POWER_BOARD))

    def test_a_copy_that_swaps_a_part_carries_the_schematic_swapped(self):
        base = self._values(POWER_BOARD)
        for name in ("ntc_swapped", "fuse_trips_late", "lfp_cell", "cutoff_drives_nothing",
                     "ground_beside_vbus", "shared_cc"):
            with self.subTest(fixture=name):
                board = self._fixture(name)
                sch = board[:-len(".kicad_pcb")] + ".kicad_sch"
                if name not in self.WITH_SCHEMATIC:
                    self.assertFalse(os.path.exists(sch))
                    continue
                want = dict(base)
                if self.WITH_SCHEMATIC[name]:
                    ref, value = self.WITH_SCHEMATIC[name]
                    want[ref] = value
                self.assertEqual(self._values(board), want)
                self.assertEqual(self._values(sch), want)

    @unittest.skipIf(SYMBOLS is None or shutil.which("kicad-cli") is None,
                     "needs KiCad's stock symbols and the real kicad-cli")
    def test_kicad_says_each_schematic_is_its_boards(self):
        """pcb.parity passes the baseline and every copy that carries a
        schematic, and pcb.erc finds nothing but the libraries no table beside
        the board names (make_power_schematic.py says why)."""
        registry = _registry()
        boards = {"baseline": POWER_BOARD,
                  **{name: self._fixture(name) for name in self.WITH_SCHEMATIC}}
        for name, src in boards.items():
            with self.subTest(board=name):
                root = os.path.join(self.root, "kicad", name)
                os.makedirs(root)
                for ext in (".kicad_pcb", ".kicad_sch"):
                    shutil.copyfile(src[:-len(".kicad_pcb")] + ext,
                                    os.path.join(root, "power_board" + ext))
                for table in ("sym-lib-table", "fp-lib-table"):
                    shutil.copyfile(os.path.join(ASSETS, table), os.path.join(root, table))
                params = dict(_baseline(), pcb_board=os.path.join(root, "power_board.kicad_pcb"))
                parity = _run(registry, "pcb.parity", root, params)
                self.assertEqual(parity.outcome, "pass", parity.render())
                erc = _run(registry, "pcb.erc", root, params)
                if erc.outcome != "pass":
                    self.assertEqual(erc.outcome, "fail", erc.render())
                    self.assertRegex(erc.detail, r"(\d+) finding\(s\): \1x lib_symbol_issues "
                                                 r"\(warning\) \(")


class ChargingStopsOutsideTheWindow(_PowerCase):
    """pcb.charge_temperature: the window at the corners, wherever the stop lives."""

    GID = "pcb.charge_temperature"

    @staticmethod
    def _ntc(p) -> dict:
        return p["pcb_protectors"]["charger"]["functions"]["charge_temp"]["ntc"]

    def test_headroom_is_a_difference_in_k_a_claim_compares(self):
        """Reported in K, not C: a claim of headroom stated in K is compared with
        it (the critique's grafted point), and one stated in C is not."""
        from nopekit import claims as claims_mod
        from nopekit.models import Acceptance, Claim, Comparator
        verdict = self.run_gate(self.GID)
        self.assertEqual(verdict.units, "K")
        for units, limit, state in (("K", 1.0, "holds"), ("K", 2.0, "fails"),
                                    ("C", 1.0, "not-compared")):
            claim = Claim(id="CT1", statement="headroom", tags=["charge-temperature"],
                          acceptance=Acceptance(quantity="charge temperature headroom",
                                                comparator=Comparator.GE, limit=limit,
                                                units=units))
            with self.subTest(units=units, limit=limit):
                self.assertEqual(claims_mod.cross_check(claim, verdict).state, state)

    def test_the_b_pair_is_flagged_when_extrapolated(self):
        """The cold edge, 5.34 C, is below the record's B25/85 pair: the detail
        says so, and that the B equation reads a cold trip warm there."""
        verdict = self.run_gate(self.GID)
        self.assertIn("the cold edge, 5.34 C, is below the B pair 25/85 C, where the B "
                      "equation extrapolates and reads a cold trip warm", verdict.detail)
        self.assertNotIn("the hot edge", verdict.detail)

    def test_the_rt_table_comes_first(self):
        """The maker's table, when given, is read in place of the B equation (here
        a table the B equation itself wrote, so the edges agree), and nothing is
        flagged as extrapolated. A table that stops short of a trip errors."""
        beta = 4250.0

        def table(lo, hi, step):
            return [[t, round(10000 * math.exp(beta * (1 / (t + 273.15) - 1 / 298.15)), 3)]
                    for t in range(lo, hi + 1, step)]

        def with_table(p):
            self._ntc(p)["rt_table_c_ohm"] = table(-40, 125, 5)
        verdict = self.run_gate(self.GID, records=with_table)
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertIn("the maker's R/T table", verdict.detail)
        self.assertNotIn("extrapolates", verdict.detail)
        self.assertAlmostEqual(verdict.measured, 1.53, delta=0.05)

        def short_table(p):
            self._ntc(p)["rt_table_c_ohm"] = table(20, 60, 5)
        verdict = self.run_gate(self.GID, records=short_table)
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("lies outside the NTC's R/T table, 20..60 C: give rows that bracket it",
                      verdict.error)

    def test_a_table_states_its_b_tolerance(self):
        """A maker's table spreads away from 25 C as its B does, so a table with
        no beta_tol_pct errors, as the B equation's record does. The same B4250
        table at R25 8 %: B 3 % charges the cell to 45.52 C (-0.52 K); B 0 %,
        stated for an exact table, reads 0.14 K. (What slipped through the first
        cut: an omitted beta_tol_pct read as 0 on a table, and so passed.)"""
        beta = 4250.0
        rows = [[t, round(10000 * math.exp(beta * (1 / (t + 273.15) - 1 / 298.15)), 3)]
                for t in range(-40, 126, 5)]

        def table(beta_tol):
            def records(p):
                ntc = self._ntc(p)
                for k in ("beta_k", "beta_pair_c", "beta_tol_pct"):
                    ntc.pop(k)
                ntc.update(rt_table_c_ohm=rows, r25_tol_pct=8)
                if beta_tol is not None:
                    ntc["beta_tol_pct"] = beta_tol
            return records
        for beta_tol, outcome, headroom in ((3, "fail", -0.52), (0, "pass", 0.14)):
            with self.subTest(beta_tol_pct=beta_tol):
                verdict = self.run_gate(self.GID, records=table(beta_tol))
                self.assertEqual(verdict.outcome, outcome, verdict.render())
                self.assertEqual(verdict.measured, headroom)
                self.assertIn("the maker's R/T table", verdict.detail)
        for name, records in (("table", table(None)),
                              ("B equation", lambda p: self._ntc(p).pop("beta_tol_pct"))):
            with self.subTest(omitted_on=name):
                verdict = self.run_gate(self.GID, records=records)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn("charge_temp.ntc has no beta_tol_pct: the B constant's "
                              "tolerance", verdict.error)

    def test_the_ntc_s_grade_is_read(self):
        """thermal-analytic's grade case, grafted: the same network built from a
        coarser grade of the same NTC. Its nominal curve, and so every check
        made at nominal, does not move; at the corners the headroom shrinks from
        1.53 K (1 %) to 0.17 K (R25 5 %, B 3 %), and a 10 % part charges the cell
        past its 45 C."""
        def grade(r25, beta):
            def records(p):
                self._ntc(p).update(r25_tol_pct=r25, beta_tol_pct=beta)
            return records
        for r25, beta, outcome, headroom in ((1, 1, "pass", 1.53), (5, 3, "pass", 0.17),
                                             (10, 3, "fail", -0.97)):
            with self.subTest(r25_tol_pct=r25, beta_tol_pct=beta):
                verdict = self.run_gate(self.GID, records=grade(r25, beta))
                self.assertEqual(verdict.outcome, outcome, verdict.render())
                self.assertEqual(verdict.measured, headroom)
                self.assertIn("on the hot side", verdict.detail)

    def test_a_fixed_resistor_where_the_ntc_goes_fails(self):
        """The datasheets' own way to switch THERM off: a 10 kOhm resistor where
        the NTC goes. Its value is an NTC's to a BOM; only the symbol tells, so the
        schematic beside the board is read. A custom thermistor symbol passes."""
        board = self.board(stem="with_sch")
        for lib_id, outcome in (("Device:R", "fail"), ("Device:R_Small", "fail"),
                                ("Device:Thermistor_NTC", "pass"),
                                ("Project_Lib:NTC_0603", "pass")):
            with self.subTest(lib_id=lib_id):
                with open(board[:-len(".kicad_pcb")] + ".kicad_sch", "w",
                          encoding="utf-8") as fh:
                    fh.write(_ONE_SYMBOL_SCH.format(lib_id=lib_id))
                verdict = self.run_gate(self.GID, board=board)
                self.assertEqual(verdict.outcome, outcome, verdict.render())
                if outcome == "fail":
                    self.assertIsNone(verdict.measured)
                    self.assertIn(f"TH1 is a fixed resistor ({lib_id})", verdict.detail)
                else:
                    self.assertNotIn("was not read", verdict.detail)

    def test_an_ntc_off_the_therm_net_or_tied_off_senses_nothing(self):
        """The branch's ntc_unwired, kept as a test: TH1's THERM pad on a net of
        its own; THERM tied to the charger's own VSS; TH1's far end on a net
        nothing else reaches."""
        def therm_on_gnd(tree):
            K.child(_pad(tree, "U1", "8"), "net")[1:] = [_net_code(tree, "GND"), K.Q("GND")]
        for name, edit, said in (
                ("NTC unwired", lambda t: _move_pad_net(t, "TH1", "1", "TH_OPEN"),
                 "reaches no pad of TH1"),
                ("THERM on VSS", therm_on_gnd, "tied to its own pad(s) 5"),
                ("far end open", lambda t: _move_pad_net(t, "TH1", "2", "TH_FAR"),
                 "which no other part reaches")):
            with self.subTest(case=name):
                verdict = self.run_gate(self.GID, edit)
                self.assertEqual(verdict.outcome, "fail", verdict.render())
                self.assertIsNone(verdict.measured)
                self.assertIn(said, verdict.detail)

    def test_an_undeclared_part_on_the_sense_net_errors(self):
        """A resistor across the NTC moves every trip, and its value is not the
        record's: it must be named, or declared with why it carries nothing."""
        def extra(tree):
            K.child(_pad(tree, "C3", "1"), "net")[1:] = [_net_code(tree, "THERM"),
                                                         K.Q("THERM")]
        path = self.board(extra)
        verdict = self.run_gate(self.GID, board=path)
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("C3 on the sense network (THERM) is not in the record", verdict.error)

        def declared(p):
            p["pcb_protectors"]["charger"]["functions"]["charge_temp"]["network"] = {
                "others": [{"ref": "C3", "value": "10uF", "why": "a filter capacitor"}]}
        self.assertEqual(self.run_gate(self.GID, records=declared, board=path).outcome, "pass")

    def test_a_pack_ntc_on_the_battery_s_therm_pin_counts(self):
        """A pack with its NTC inside: the charger's THERM reaches it through the
        connector's thermistor pin. Here J10 stands for the pack's connector (top
        on 5, return on 1, thermistor on 8, moved onto THERM), and the NTC is the
        pack's record. The flex to J11 joins THERM to J11's position 8 too: a
        mating's connector is a conductor, not a part on the sense network."""
        def records(p):
            bat = p["pcb_batteries"]["main"]
            bat["terminals"] = {"ref": "J10", "value": "FFC_8P", "pos": "5", "neg": "1",
                                "therm": "8"}
            bat["ntc"] = {"r25_ohm": 10000, "r25_tol_pct": 1, "beta_k": 4250,
                          "beta_pair_c": [25, 85], "beta_tol_pct": 1}
            self._ntc(p).clear()
            self._ntc(p)["battery_therm"] = True

        def therm_pin(tree):
            # TH1 off the board's THERM net, and J10's position 8 on it: only
            # the pack's NTC is there.
            _move_pad_net(tree, "TH1", "1", "TH_GONE")
            K.child(_pad(tree, "J10", "8"), "net")[1:] = [_net_code(tree, "THERM"),
                                                          K.Q("THERM")]
        verdict = self.run_gate(self.GID, therm_pin, records)
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertEqual(verdict.measured, 1.53)
        self.assertIn("on the pack's NTC", verdict.detail)

    def test_a_divider_reads_its_pullup_off_the_board(self):
        """The ratio kind: TS on a divider, the NTC low, the trip as fractions of
        the reference. R3 (3k) stands for the pull-up here, moved onto THERM.

        By hand: R = R_pu f / (1 - f) at the corners of f and R_pu. Cold, 3k
        +1 % at 0.82 is 13803 ohm, the highest trip; the NTC at R25 -1 % and B
        -1 % (its lowest curve below 25 C) reaches it at 18.14 C, the lowest
        temperature any corner still charges at. Hot, 3k -1 % at 0.40 is 1980
        ohm, the lowest trip; at R25 +1 % and B -1 % (its highest curve above
        25 C) it is reached at 63.92 C. So 18.14..63.92 C against 0..45 C:
        -18.92 K, on the hot side. (What slipped through the first cut of this
        test: it took either outcome and pinned no value, so (1 - f) / f, or
        the corners swapped, stayed green.)"""
        def pull_up(tree):
            K.child(_pad(tree, "R3", "1"), "net")[1:] = [_net_code(tree, "THERM"),
                                                         K.Q("THERM")]
            K.child(_pad(tree, "R3", "2"), "net")[1:] = [_net_code(tree, "VBUS"),
                                                         K.Q("VBUS")]

        def ratio(cold, hot, tol=1):
            def records(p):
                ct = p["pcb_protectors"]["charger"]["functions"]["charge_temp"]
                ct["sense"] = {"pin": "8", "kind": "ratio", "pullup_ref": "R3",
                               "pullup_tol_pct": tol, "cold_frac": cold, "hot_frac": hot}
                if tol == 0:
                    self._ntc(p).update(r25_tol_pct=0, beta_tol_pct=0)
            return records
        board = self.board(pull_up)
        verdict = self.run_gate(self.GID, records=ratio([0.80, 0.82], [0.40, 0.42]),
                                board=board)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertEqual(verdict.measured, -18.92)
        self.assertIn("charging allowed 18.14..63.92 C", verdict.detail)
        self.assertIn("-18.92 K of headroom on the hot side", verdict.detail)

        # A known boundary: every tolerance 0, so every corner is the nominal
        # curve, and each fraction is the one the 10k B4250 NTC under 3k gives
        # at 1 C and at 44 C, R / (R + R_pu). The edges land on those two
        # temperatures, 1 K inside each side of the cell's window.
        def frac(t_c):
            r = 10000 * math.exp(4250 * (1 / (t_c + 273.15) - 1 / 298.15))
            return r / (r + 3000)
        f1, f44 = frac(1.0), frac(44.0)
        verdict = self.run_gate(self.GID, records=ratio([f1, f1], [f44, f44], tol=0),
                                board=board)
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertEqual(verdict.measured, 1.0)
        self.assertIn("charging allowed 1.00..44.00 C", verdict.detail)

    def test_firmware_alone_skips_and_a_fail_beside_it_still_fails(self):
        """Firmware is named, never counted: a cell whose only stop is firmware
        skips; with a board element that fails beside it, the rule fails."""
        def firmware_only(p):
            p["pcb_protectors"]["charger"]["functions"].pop("charge_temp")
            p["pcb_protectors"]["mcu"] = {"battery": "main", "kind": "gauge",
                                          "where": "firmware",
                                          "functions": {"charge_temp": {}},
                                          "source": "a register window, set at boot"}
        verdict = self.run_gate(self.GID, records=firmware_only)
        self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("only firmware stops charging", verdict.skip_reason)
        self.assertIn("mcu (firmware)", verdict.skip_reason)

        def beside(p):
            p["pcb_protectors"]["mcu"] = {"battery": "main", "kind": "gauge",
                                          "where": "firmware",
                                          "functions": {"charge_temp": {}},
                                          "source": "a register window, set at boot"}
        verdict = self.run_gate(self.GID, lambda t: _move_pad_net(t, "TH1", "1", "TH_OPEN"),
                                beside)
        self.assertEqual(verdict.outcome, "fail", verdict.render())

    def test_a_cell_held_by_firmware_never_hides_another_cell_s_fail(self):
        """Two cells: a second one on J10 whose charger and window are firmware's.
        Beside the baseline's cell it skips; beside a coarser NTC (R25 10 %, B
        3 %) that charges the first cell past 45 C, the rule fails at that
        cell's headroom. (What
        slipped through the first cut: the skip came before the other cell was
        judged.)"""
        def second(grade):
            def records(p):
                p["pcb_batteries"]["second"] = {
                    "board": p["pcb_batteries"]["main"]["board"],
                    "terminals": {"ref": "J10", "value": "FFC_8P", "pos": "5", "neg": "1"},
                    "chemistry": "li-polymer", "series": 1, "parallel": 1,
                    "cell": {"charge_temp_c": [0, 45]}, "source": "a second cell"}
                p["pcb_protectors"]["mcu"] = {"battery": "second", "kind": "charger",
                                              "where": "firmware",
                                              "functions": {"charge_temp": {}},
                                              "source": "a PMIC's register window"}
                self._ntc(p).update(r25_tol_pct=grade[0], beta_tol_pct=grade[1])
            return records
        verdict = self.run_gate(self.GID, records=second((1, 1)))
        self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("second: mcu (firmware)", verdict.skip_reason)
        verdict = self.run_gate(self.GID, records=second((10, 3)))
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertEqual(verdict.measured, -0.97)
        self.assertIn("firmware only, not judged: second: mcu (firmware)", verdict.detail)

    def test_the_most_protective_element_sets_each_edge(self):
        """A pack element with a narrower window sets the hot edge; the board's
        charger still sets the cold one."""
        def pack_window(p):
            p["pcb_protectors"]["pack_pcm"]["functions"]["charge_temp"] = {
                "window_c": {"cold_c": [-5, -2], "hot_c": [38, 41]}}
        verdict = self.run_gate(self.GID, records=pack_window)
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertEqual(verdict.measured, 4.0)
        self.assertIn("charging allowed 5.34..41.00 C", verdict.detail)
        self.assertIn("hot edge pack_pcm (in-pack), on its datasheet's word", verdict.detail)


class TheChargerIsHeldToTheCell(_PowerCase):
    """pcb.charge_limits: the charger's worst corner against the cell, and a
    single-fault overcharge stop beside it."""

    GID = "pcb.charge_limits"

    def test_a_small_prog_resistor_charges_a_small_cell_too_hard(self):
        """A 400 mAh cell at 1 C beside PROG 1k: 1100 V / 990 ohm is 1.111 A."""
        def small_cell(p):
            p["pcb_batteries"]["main"]["cell"]["charge_a_max"] = 0.4
        verdict = self.run_gate(self.GID, records=small_cell)
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertIn(f"0.3704 A of its 0.4 A ({1100 / 2970 / 0.4:.4f})", verdict.detail)
        verdict = self.run_gate(self.GID, lambda t: _set_value(t, "R3", "1k"), small_cell)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertEqual(verdict.measured, round(1100 / 990 / 0.4, 4))
        self.assertIn("current set by R3 1k", verdict.detail)

    def test_an_hv_variant_stales_the_record_and_then_fails(self):
        """A variant letter swap is another design: the record's value no longer
        binds. With the record moved too, 4.382 V is past the cell's 4.25 V."""
        hv = "MCP73833-NVI/MF"
        verdict = self.run_gate(self.GID, lambda t: _set_value(t, "U1", hv))
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("is for another design", verdict.error)

        def moved(p):
            c = p["pcb_protectors"]["charger"]
            c["value"] = hv
            c["functions"]["charge_v"]["v_reg_v"] = [4.318, 4.382]
        verdict = self.run_gate(self.GID, lambda t: _set_value(t, "U1", hv), moved)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertEqual(verdict.measured, round(4.382 / 4.25, 4))

    def test_nothing_independent_of_the_charger_fails(self):
        """No pack PCM and no protector overcharge: the charger alone holds the
        cell, and a single fault in it overcharges. And U2's CO driving nothing
        leaves it uncounted."""
        def charger_alone(p):
            p["pcb_protectors"].pop("pack_pcm")
            p["pcb_protectors"]["protector"]["functions"].pop("overcharge_v")
        verdict = self.run_gate(self.GID, records=charger_alone)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertIsNone(verdict.measured)
        self.assertIn("nothing independent of the charger stops an overcharge", verdict.detail)

        def pcm_gone(p):
            p["pcb_protectors"].pop("pack_pcm")
        verdict = self.run_gate(self.GID, lambda t: _move_pad_net(t, "U2", "5", "CO_OPEN"),
                                pcm_gone)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertIn("it drives nothing", verdict.detail)

    def test_a_firmware_overcharge_stop_is_named_and_skipped_never_counted(self):
        """Only firmware stands beside the charger: the rule cannot see it, so it
        skips, naming it, as every battery rule treats firmware. Beside a board
        element that does not count, the rule still fails."""
        def firmware(p):
            p["pcb_protectors"].pop("pack_pcm")
            p["pcb_protectors"]["protector"]["functions"].pop("overcharge_v")
            p["pcb_protectors"]["mcu"] = {"battery": "main", "kind": "gauge",
                                          "where": "firmware",
                                          "functions": {"overcharge_v": {
                                              "v_max": 4.3, "per_cell": True}},
                                          "source": "an ADC check that stops the charger"}
        verdict = self.run_gate(self.GID, records=firmware)
        self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("main: the overcharge stop mcu (firmware)", verdict.skip_reason)

        def beside(p):
            p["pcb_protectors"].pop("pack_pcm")
            p["pcb_protectors"]["mcu"] = {"battery": "main", "kind": "gauge",
                                          "where": "firmware",
                                          "functions": {"overcharge_v": {
                                              "v_max": 4.3, "per_cell": True}},
                                          "source": "an ADC check that stops the charger"}
        verdict = self.run_gate(self.GID, lambda t: _move_pad_net(t, "U2", "5", "CO_OPEN"),
                                beside)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertIn("mcu (firmware) is firmware, which this rule cannot see",
                      verdict.detail)

    def test_the_charger_s_own_stop_moves_nothing(self):
        """The charger's own battery OVP is never independent of the charger, so
        it could never count, and recording it moves no verdict: beside a
        firmware-only stop the rule still skips, and alone it fails as no stop
        does. Either way it is named. (What slipped through the first cut: it
        was listed with the elements that failed to count, and the firmware
        skip turned into a FAIL.)"""
        def stops(firmware):
            def records(p):
                p["pcb_protectors"].pop("pack_pcm")
                p["pcb_protectors"]["protector"]["functions"].pop("overcharge_v")
                p["pcb_protectors"]["charger"]["functions"]["overcharge_v"] = {
                    "v_max": 4.4, "per_cell": True}
                if firmware:
                    p["pcb_protectors"]["mcu"] = {
                        "battery": "main", "kind": "gauge", "where": "firmware",
                        "functions": {"overcharge_v": {"v_max": 4.3, "per_cell": True}},
                        "source": "an ADC check that stops the charger"}
            return records
        verdict = self.run_gate(self.GID, records=stops(True))
        self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("main: the overcharge stop mcu (firmware)", verdict.skip_reason)
        self.assertIn("U1 MCP73833-AMI/UN is the charger itself", verdict.skip_reason)
        verdict = self.run_gate(self.GID, records=stops(False))
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertIsNone(verdict.measured)
        self.assertIn("nothing independent of the charger stops an overcharge", verdict.detail)
        self.assertIn("U1 MCP73833-AMI/UN is the charger itself", verdict.detail)

    def test_a_two_cell_pack_needs_a_per_cell_stop(self):
        """Two cells in series on J10 (top on pin 5, tap on 7, return on 1): a
        pack-level overcharge bounds no single cell, so only a per-cell element
        counts."""
        def two_cell(per_cell):
            def records(p):
                p["pcb_batteries"]["main"].update(series=2)
                p["pcb_batteries"]["main"]["terminals"] = {
                    "ref": "J10", "value": "FFC_8P", "pos": "5", "neg": "1", "taps": ["7"]}
                for pid in ("protector", "fuse"):
                    p["pcb_protectors"].pop(pid)
                p["pcb_conductors"] = {}
                c = p["pcb_protectors"]["charger"]
                c["functions"].pop("charge_temp")
                c["functions"]["charge_v"] = {"v_reg_v": [8.336, 8.464], "per_cell": False}
                p["pcb_protectors"]["pack_pcm"]["functions"]["overcharge_v"]["per_cell"] = per_cell
            return records
        verdict = self.run_gate(self.GID, records=two_cell(False))
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertIn("holds the pack, not each cell", verdict.detail)
        verdict = self.run_gate(self.GID, records=two_cell(True))
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertEqual(verdict.measured, round(8.464 / 2 / 4.25, 4))

    def test_a_charger_on_a_primary_cell_fails(self):
        def primary(p):
            p["pcb_batteries"]["main"]["chemistry"] = "li-primary"
        verdict = self.run_gate(self.GID, records=primary)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertIsNone(verdict.measured)
        self.assertIn("charges a li-primary cell, which must never be charged", verdict.detail)

    def test_firmware_set_charging_skips_and_a_typo_errors(self):
        def firmware(p):
            p["pcb_protectors"]["charger"]["functions"]["charge_v"] = {"per_cell": False,
                                                                       "set_by": "firmware"}
        verdict = self.run_gate(self.GID, records=firmware)
        self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("U1 MCP73833-AMI/UN's charge voltage", verdict.skip_reason)

        def typo(p):
            p["pcb_batteries"]["main"]["cell"]["upper_charge_v"] = 42.5
        verdict = self.run_gate(self.GID, records=typo)
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("42.5 V per cell, outside 1.5-4.6 V", verdict.error)

        def both(p):
            p["pcb_protectors"]["charger"]["functions"]["charge_v"]["set_by"] = "firmware"
        verdict = self.run_gate(self.GID, records=both)
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("charge_v is set_by firmware and gives v_reg_v: give one", verdict.error)

    def test_a_firmware_function_never_hides_one_the_board_sets(self):
        """Each charge function is judged on its own. The lfp_cell control's
        LiFePO4 cell with the charger's current set by firmware still fails on
        the voltage the record and the board give, 4.232 / 3.65; a firmware
        voltage beside a PROG resistor that drives a 200 mA cell at 0.3704 A
        fails on the current. The firmware function is named in both. (What
        slipped through the first cut: a firmware set_by on either function
        returned before the other was read, and both read Skipped.)"""
        def lfp_firmware_current(p):
            p["pcb_batteries"]["main"]["chemistry"] = "lifepo4"
            p["pcb_batteries"]["main"]["cell"]["upper_charge_v"] = 3.65
            p["pcb_protectors"]["charger"]["functions"]["charge_a"] = {"set_by": "firmware"}

        def small_cell_firmware_voltage(p):
            p["pcb_batteries"]["main"]["cell"]["charge_a_max"] = 0.2
            p["pcb_protectors"]["charger"]["functions"]["charge_v"] = {"per_cell": False,
                                                                       "set_by": "firmware"}
        for records, measured, held, named in (
                (lfp_firmware_current, round(4.232 / 3.65, 4),
                 "holds 4.232 V of the cell's 3.65 V (1.1595)", "charge current"),
                (small_cell_firmware_voltage, round(1100 / 2970 / 0.2, 4),
                 "holds 0.3704 A of the cell's 0.2 A (1.8519)", "charge voltage")):
            with self.subTest(firmware=named):
                verdict = self.run_gate(self.GID, records=records)
                self.assertEqual(verdict.outcome, "fail", verdict.render())
                self.assertEqual(verdict.measured, measured)
                self.assertIn(held, verdict.detail)
                self.assertIn(f"set by firmware, not judged: main: U1 MCP73833-AMI/UN's "
                              f"{named}", verdict.detail)

    def test_a_real_overcharge_fails_at_its_ratio_never_as_a_typo(self):
        """The typo bound is the cell record's. A 2S charger on the 1S cell (a
        cell-count mistake) and a PROG of "10" typed for "10k" on the board are
        real overcharges: each fails at its ratio, 8.464 / 4.25 and 1100 V /
        9.9 ohm over 3.35 A. On a charger only a typed value below the bound's
        floor is refused, the one typo that would read as a pass. (What slipped
        through the first cut: the whole bound was held on the charger's worst
        corner per cell, and both overcharges read "a typo?".)"""
        def two_s(p):
            p["pcb_protectors"]["charger"]["functions"]["charge_v"]["v_reg_v"] = [8.336, 8.464]
        verdict = self.run_gate(self.GID, records=two_s)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertEqual(verdict.measured, round(8.464 / 4.25, 4))
        self.assertIn("holds 8.464 V of the cell's 4.25 V", verdict.detail)
        verdict = self.run_gate(self.GID, lambda t: _set_value(t, "R3", "10"))
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertEqual(verdict.measured, round(1100 / 9.9 / 3.35, 4))
        self.assertIn("current set by R3 10", verdict.detail)

        def low_v(p):
            p["pcb_protectors"]["charger"]["functions"]["charge_v"]["v_reg_v"] = [0.4168,
                                                                                  0.4232]

        def low_a(p):
            p["pcb_protectors"]["charger"]["functions"]["charge_a"] = {"a_max": 0.0004}
        for records, said in ((low_v, "v_reg_v's max is 0.4232 V, below 1.5 V"),
                              (low_a, "a_max is 0.0004 A, below 0.001 A")):
            with self.subTest(said=said):
                verdict = self.run_gate(self.GID, records=records)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn(said, verdict.error)

    def test_a_divider_is_read_at_its_corners(self):
        """An adjustable charger: Vfb (1 + Rtop / Rbot), the resistors off the
        board at their tolerance. R4 (2.7k) over R1 (5.1k) stand in."""
        def divider(p):
            p["pcb_protectors"]["charger"]["functions"]["charge_v"] = {
                "divider": {"vfb_v": [2.7, 2.73], "top_ref": "R4", "bottom_ref": "R1",
                            "tol_pct": 1}, "per_cell": False}
        verdict = self.run_gate(self.GID, records=divider)
        v = 2.73 * (1 + 2700 * 1.01 / (5100 * 0.99))
        self.assertEqual(verdict.measured, round(v / 4.25, 4))
        self.assertIn("voltage set by R4 2.7k over R1 5.1k", verdict.detail)


class EveryConductorIsGuarded(_PowerCase):
    """pcb.fault_current: interrupt, overload band and short circuit, any
    protector topology."""

    GID = "pcb.fault_current"

    def test_a_thin_conductor_fails_the_short_circuit_point(self):
        """The branch's thin_conductor, kept as a test: the wire's area derived so
        that IEC 60949 puts it at its limit in the PTC's trip time / 1.15."""
        p0 = _baseline()
        cond = p0["pcb_conductors"]["wire_out"]
        amps, t_max = p0["pcb_protectors"]["fuse"]["functions"]["trip"]["trip_max_s"][0]
        area = round(self.power.area_for_s(t_max / 1.15, amps, cond["start_c"],
                                           cond["limit_c"]), 5)

        def thin(p):
            p["pcb_conductors"]["wire_out"]["area_mm2"] = area
        verdict = self.run_gate(self.GID, records=thin)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertAlmostEqual(verdict.measured, 1 / 1.15, places=3)
        self.assertIn("trips by 100 ms at 8 A", verdict.detail)

    def test_a_trip_point_the_cell_cannot_reach_fails(self):
        """A small cell whose least fault current, 5 A, never reaches the PTC's
        only guaranteed point, 8 A."""
        def weak(p):
            p["pcb_batteries"]["main"]["cell"]["short_a"] = [5.0, 40.0]
        verdict = self.run_gate(self.GID, records=weak)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertIsNone(verdict.measured)
        self.assertIn("nothing guarantees a trip at the current main can drive (5 A at "
                      "the least", verdict.detail)

    def test_a_low_interrupt_rating_fails(self):
        def low(p):
            p["pcb_protectors"]["fuse"]["functions"]["trip"]["interrupt_a"] = 30.0
        verdict = self.run_gate(self.GID, records=low)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertEqual(verdict.measured, 0.75)
        self.assertIn("interrupts 30 A of 40 A", verdict.detail)

    def test_a_fuse_beside_the_path_is_not_between(self):
        def edit(tree):
            K.child(_pad(tree, "J6", "1"), "net")[1:] = [_net_code(tree, "CELL_P"),
                                                         K.Q("CELL_P")]
        verdict = self.run_gate(self.GID, edit)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertIsNone(verdict.measured)
        self.assertIn("F1 is not between main and J6 pin 1", verdict.detail)

    def test_low_side_fets_guard_and_an_undeclared_tap_is_named(self):
        """Protector FETs in the negative lead (the branch read them FAIL). U2's VSS
        and C1 sit on the cell's negative ahead of Q1: a sound design may, so they
        are refused until declared, never failed."""
        def low_side(ahead):
            def records(p):
                trip = {"kind": "fet", "terminal": "neg",
                        "switch": {"ref": "Q1", "value": "FDS6890A", "path": ["1", "3"]},
                        "trip_a": 1.2, "trip_max_s": [[8.0, 0.01]]}
                if ahead:
                    trip["ahead"] = [{"ref": "U2", "value": "PROT_1S_FIXTURE",
                                      "why": "the protector's own supply"},
                                     {"ref": "C1", "value": "100nF",
                                      "why": "its supply capacitor"}]
                p["pcb_protectors"]["protector"]["functions"]["trip"] = trip
                p["pcb_conductors"]["wire_out"]["protected_by"] = ["protector"]
            return records
        verdict = self.run_gate(self.GID, records=low_side(False))
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("C1, U2 on main's neg net (CELL_N) taps the cell ahead of Q1",
                      verdict.error)
        verdict = self.run_gate(self.GID, records=low_side(True))
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertEqual(verdict.measured, round(1.45 / 1.2, 4))

    def test_the_best_protector_guards_each_band(self):
        """Two protectors on one conductor: the PTC covers the overload band, the
        low-side FETs the short circuit; each band takes the better one."""
        def both(p):
            p["pcb_protectors"]["protector"]["functions"]["trip"] = {
                "kind": "fet", "terminal": "neg",
                "switch": {"ref": "Q1", "value": "FDS6890A", "path": ["1", "3"]},
                "trip_a": 4.0, "trip_max_s": [[8.0, 0.001]],
                "ahead": [{"ref": "U2", "value": "PROT_1S_FIXTURE", "why": "its supply"},
                          {"ref": "C1", "value": "100nF", "why": "its supply capacitor"}]}
            p["pcb_conductors"]["wire_out"]["protected_by"] = ["fuse", "protector"]
        verdict = self.run_gate(self.GID, records=both)
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertEqual(verdict.measured, 1.45)

    def test_a_track_is_rated_by_ipc_2221(self):
        """A track conductor: its continuous rating is IPC-2221's at the stated
        rise on its narrowest stretch, in the project's copper."""
        def edit(tree):
            tree.append(["segment", ["start", "33", "8"], ["end", "40", "8"],
                         ["width", "0.3"], ["layer", K.Q("F.Cu")],
                         ["net", _net_code(tree, "VSYS")],
                         ["uuid", K.Q("00000000-0000-4000-8000-0000000000c1")]])

        def track(p):
            p["pcb_conductors"]["wire_out"] = {
                "battery": "main", "board": p["pcb_batteries"]["main"]["board"],
                "kind": "track", "net": "VSYS", "continuous_rise_c": 20.0,
                "start_c": 40.0, "limit_c": 130.0, "protected_by": ["fuse"],
                "source": "a 0.3 mm outer track, 20 C rise; FR-4 to 130 C"}
        verdict = self.run_gate(self.GID, records=track, board=self.board(edit))
        amps = (K.K_EXTERNAL * 20.0 ** K.EXP_DT
                * ((0.3 / K.MM_PER_MIL) * K.MIL_PER_OZ) ** K.EXP_AREA)
        area = 0.3 * K.MIL_PER_OZ * K.MM_PER_MIL
        short = self.power.adiabatic_s(8.0, area, 40.0, 130.0) / 0.1
        least = round(min(2.0, 1.45 * amps / 1.0, short), 4)
        self.assertEqual(verdict.measured, least)
        self.assertEqual(verdict.outcome, "pass" if least >= 1.0 else "fail")
        self.assertIn("VSYS's 0.3 mm track on F.Cu", verdict.detail)


class TheCutoffMustOpenThePath(_PowerCase):
    """pcb.discharge_cutoff: a cutoff counts only when it senses the cell and
    drives a switch in its path."""

    GID = "pcb.discharge_cutoff"

    def test_a_cutoff_that_senses_nothing_leaves_the_pcm(self):
        """The branch's cutoff_unwired, kept as a test: R5's VSYS pad on a net of
        its own, so U2 senses nothing the cell drives."""
        verdict = self.run_gate(self.GID, lambda t: _move_pad_net(t, "R5", "1", "R5_OPEN"))
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertEqual(verdict.measured, 2.3)
        self.assertIn("U2 does not sense the cell through F1, R5", verdict.detail)

    def test_a_sense_pin_on_the_return_or_a_wrong_chain_never_counts(self):
        """In the branch's review: the series part was inferred, the cell's own
        connector among the candidates, and a VSS pin read as a sense pin."""
        def sense(**s):
            def records(p):
                for fn in ("overdischarge_v", "overcharge_v"):
                    p["pcb_protectors"]["protector"]["functions"][fn]["sense"] = s
            return records
        for name, records, said in (
                ("VSS named", sense(pin="3"), "pad 3 is on the cell's return"),
                ("through the connector", sense(pin="2", through=["BT1", "R5"]),
                 "BT1 is the battery's own connector"),
                ("through the capacitor", sense(pin="2", through=["C1"]),
                 "C1's pads sit on"),
                ("no chain", sense(pin="2"), "(through names no chain)")):
            with self.subTest(case=name):
                verdict = self.run_gate(self.GID, records=records)
                self.assertEqual(verdict.outcome, "fail", verdict.render())
                self.assertEqual(verdict.measured, 2.3)
                self.assertIn(said, verdict.detail)

    def test_a_cutoff_across_a_mating_counts(self):
        """The cutoff on a daughterboard: a copy of the board whose BT1 meets the
        main board's BT1 pin for pin. Without the mating it senses another net."""
        main = self.board(stem="main")
        daughter = self.board(stem="daughter")

        def split(mated):
            def records(p):
                for pid in ("protector",):
                    p["pcb_protectors"][pid]["board"] = daughter
                p["pcb_matings"] = ({"link": {"a": {"board": main, "ref": "BT1",
                                                    "value": "CELL"},
                                              "b": {"board": daughter, "ref": "BT1",
                                                    "value": "CELL"},
                                              "map": "straight", "source": "a soldered link"}}
                                    if mated else {})
            return records
        verdict = self.run_gate(self.GID, records=split(True), board=main)
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertEqual(verdict.measured, 2.85)
        verdict = self.run_gate(self.GID, records=split(False), board=main)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertEqual(verdict.measured, 2.3)

    def test_a_reversed_mating_joins_pin_n_to_the_last_but_n(self):
        """``reversed``: an 8-way flex whose two ends face opposite ways joins
        position 1 to 8. Joined so, J10's GND meets J11's SIG_D."""
        d = self.helpers.Design(_ctx(self.root, _power_params()))
        rec = {"flex": {"a": {"board": POWER_BOARD, "ref": "J10", "value": "FFC_8P"},
                        "b": {"board": POWER_BOARD, "ref": "J11", "value": "FFC_8P"},
                        "map": "reversed", "source": "flipped"}}
        d.matings(rec)
        self.assertTrue(d.same(d.node(POWER_BOARD, "GND"), d.node(POWER_BOARD, "SIG_D")))
        self.assertFalse(d.same(d.node(POWER_BOARD, "GND"), d.node(POWER_BOARD, "VSYS")))

    def test_two_cells_count_only_a_per_cell_cutoff(self):
        def two_cell(per_cell):
            def records(p):
                p["pcb_batteries"]["main"].update(series=2)
                p["pcb_batteries"]["main"]["terminals"] = {
                    "ref": "J10", "value": "FFC_8P", "pos": "5", "neg": "1", "taps": ["7"]}
                p["pcb_protectors"].pop("protector")
                p["pcb_protectors"]["pack_pcm"]["functions"]["overdischarge_v"].update(
                    per_cell=per_cell, v_min=2.8)
            return records
        verdict = self.run_gate(self.GID, records=two_cell(False))
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertIn("holds the pack, not each cell", verdict.detail)
        verdict = self.run_gate(self.GID, records=two_cell(True))
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertEqual(verdict.measured, 2.8)

    def test_firmware_alone_skips_and_a_primary_cell_is_not_judged(self):
        def firmware(p):
            p["pcb_protectors"].pop("protector")
            p["pcb_protectors"].pop("pack_pcm")
            p["pcb_protectors"]["mcu"] = {"battery": "main", "kind": "gauge",
                                          "where": "firmware",
                                          "functions": {"overdischarge_v": {
                                              "v_min": 3.0, "per_cell": True}},
                                          "source": "an ADC check before sleep"}
        verdict = self.run_gate(self.GID, records=firmware)
        self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("only firmware cuts the cell off", verdict.skip_reason)

        def primary(p):
            p["pcb_batteries"]["main"]["chemistry"] = "alkaline"
        verdict = self.run_gate(self.GID, records=primary)
        self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("main (alkaline) not judged", verdict.skip_reason)


# --------------------------------------------------------------------------- #
# 0.3.0: the readers. A newer board skips, nets read by name, the land fit is
# anchored past the full search, a fab service lists what it builds, and a
# series pair is stated per ref.
# --------------------------------------------------------------------------- #
def _by_name(text: str, version: str = "20260206") -> str:
    """The board as KiCad 10 writes it from format 20251028 (its 10.0 source,
    ``pcb_io_kicad_sexpr.cpp``: every pad, track, via and zone writes ``(net
    "<name>")``, and the board writes no net table): the table dropped, every
    code replaced by its name, a zone's ``net_name`` dropped. No KiCad 10 is
    installed where this pack was written, so this is the source's form, not a
    saved file: the reason the reader's ceiling stays at KiCad 9."""
    tree = K.parse(text)
    codes = {str(n[1]): str(n[2]) for n in K.children(tree, "net") if len(n) >= 3}
    tree[:] = [x for x in tree if not (isinstance(x, list) and K.head(x) == "net")]

    def walk(node):
        for x in node:
            if isinstance(x, list):
                if K.head(x) == "net" and len(x) >= 2 and not isinstance(x[1], K.Q):
                    x[1:] = [K.Q(codes[str(x[1])])]
                walk(x)
    walk(tree)
    for zone in K.children(tree, "zone"):
        zone[:] = [x for x in zone if not (isinstance(x, list) and K.head(x) == "net_name")]
    K.child(tree, "version")[1] = version
    return K.dump(tree) + "\n"


def _with_zone(text: str) -> str:
    """The baseline with a GND pour on B.Cu, as KiCad 9 writes a zone's net:
    by code, and by name in ``net_name``."""
    tree = K.parse(text)
    tree.append(["zone", ["net", _net_code(tree, "GND")], ["net_name", K.Q("GND")],
                 ["layer", K.Q("B.Cu")], ["uuid", K.Q("00000000-0000-4000-8000-0000000000bb")]])
    return K.dump(tree) + "\n"


def _version(text: str, version: str) -> str:
    return text.replace("(version 20241229)", f"(version {version})", 1)


def _ctx(root: str, params: dict, tier: int = 0) -> gates_mod.GateContext:
    return gates_mod.GateContext(
        root=root, ledger=Ledger(meta=ProjectMeta(name="pcb")), model=None,
        params=dict(params), out_dir=os.path.join(root, "out"), tier=tier,
        log=lambda _m: None, extra={})


class ANewerBoardIsSkippedNeverFailed(unittest.TestCase):
    """A board newer than the reader was checked through is a limit of the
    reader: the guard skips, the gates that need it are not established, and
    the power rules skip with the same words. Older than KiCad 6, and a track
    on a code the table does not define, still fail. (What slipped through:
    every board KiCad 10 saved read FAIL, as a defective design.)"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nopekit-pcb-newer-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.registry = _registry()

    def _board(self, text: str, stem: str = "b") -> str:
        return _board_copy(self.root, text, stem=stem)

    def test_the_guard_skips_and_its_dependents_are_not_established(self):
        board = self._board(_by_name(_board_text()))
        verdicts = gates_mod.run_all(
            self.registry, _ctx(self.root, dict(_baseline(), pcb_board=board)), max_tier=0,
            only=["pcb.pin_names", "pcb.net_width", "pcb.land_pattern"])
        by_id = {v.gate: v for v in verdicts}
        guard = by_id["pcb.board_readable"]
        self.assertEqual(guard.outcome, "skipped", guard.render())
        self.assertIn("this reader was checked through board format 20241229 (KiCad 9.0); "
                      "the file is format 20260206", guard.skip_reason)
        self.assertIn("not a defect in the board", guard.skip_reason)
        for gid in ("pcb.pin_names", "pcb.net_width", "pcb.land_pattern"):
            with self.subTest(gate=gid):
                self.assertEqual(by_id[gid].outcome, "skipped", by_id[gid].render())
                self.assertEqual(by_id[gid].skip_reason,
                                 "prerequisite not established: pcb.board_readable (skipped)")

    def test_older_than_kicad_6_and_an_orphaned_code_still_fail(self):
        tree = K.load_tree(BOARD)
        code = _net_code(tree, "VBUS")
        tree.remove(next(n for n in K.children(tree, "net") if str(n[1]) == code))
        for name, text, said in (
                ("KiCad 5", _version(_board_text(), "20171130"), "older than 20211014 (KiCad 6)"),
                ("orphaned code", K.dump(tree) + "\n",
                 f"name net code(s) [{code}] that the net table does not define")):
            with self.subTest(board=name):
                verdict = _run(self.registry, "pcb.board_readable", self.root,
                               dict(_baseline(), pcb_board=self._board(text, stem="old")))
                self.assertEqual(verdict.outcome, "fail", verdict.render())
                self.assertIn(said, verdict.detail)

    def test_the_orphan_check_is_only_for_formats_that_write_codes(self):
        """KiCad 10 writes no net table from 20251028; a check that read it would
        call every track orphaned. Before it, an orphan is still found."""
        tree = K.load_tree(BOARD)
        code = _net_code(tree, "VBUS")
        tree.remove(next(n for n in K.children(tree, "net") if str(n[1]) == code))
        K.child(tree, "version")[1] = str(K.NET_CODES_DROPPED - 1)
        _v, problems = K.board_problems(tree)
        self.assertTrue(any("does not define" in p for p in problems), problems)
        K.child(tree, "version")[1] = str(K.NET_CODES_DROPPED)
        _v, problems = K.board_problems(tree)
        self.assertEqual(problems, [K.reader_ceiling(K.NET_CODES_DROPPED)])
        self.assertEqual(K.board_problems(K.parse(_by_name(_board_text())))[1],
                         [K.reader_ceiling(20260206)])

    def test_the_ceiling_stays_at_kicad_9_until_a_kicad_10_board_is_committed(self):
        """Only KiCad 9.0.9 was installed where this reader was written, so no
        board KiCad 10 saved could be checked against it: an accepted gap
        (PACK.md). Raise the ceiling with such a board as a fixture, never on the
        10.0 source alone."""
        self.assertEqual(K.MAX_BOARD_VERSION, 20241229)
        self.assertEqual(K.reader_ceiling(20241229), "")
        self.assertEqual(K.reader_ceiling(None), "")


class NetsAreReadByNameAsByCode(unittest.TestCase):
    """The baseline written as KiCad 10 writes nets (by name, no table) reads the
    same pads, tracks, zones and pin-name conflicts as the baseline itself."""

    def test_the_same_board_either_way(self):
        coded_text = _with_zone(_board_text())
        coded = K.board_from_tree(K.parse(coded_text))
        named = K.board_from_tree(K.parse(_by_name(coded_text)))
        self.assertEqual(named.nets, {}, "a KiCad 10 board has no net table")
        self.assertEqual(coded.net_names, ["DM", "DP", "GND", "VBUS"])
        self.assertEqual(named.net_names, coded.net_names)
        self.assertEqual([(f.ref, [(p.number, p.net, p.pinfunction) for p in f.pads])
                          for f in named.footprints],
                         [(f.ref, [(p.number, p.net, p.pinfunction) for p in f.pads])
                          for f in coded.footprints])
        self.assertEqual([(t.net, t.layer, t.width_mm, t.shape) for t in named.tracks],
                         [(t.net, t.layer, t.width_mm, t.shape) for t in coded.tracks])
        self.assertEqual(named.zone_nets, ["GND"])
        self.assertEqual(coded.zone_nets, ["GND"])
        self.assertEqual(K.pad_nets_by_id(K.parse(_by_name(coded_text))),
                         K.pad_nets_by_id(K.parse(coded_text)))
        self.assertTrue(all(t.net for t in named.tracks), "every baseline track has a net")


#: KiCad 9.0.9's ``Connector_USB.pretty/USB_C_Receptacle_GCT_USB4105-xx-A_16P_
#: TopMnt_Horizontal.kicad_mod`` (file version 20221018): every numbered pad,
#: its centre and size as the file writes them. A1 and B12, A4 and B9, A9 and B4,
#: A12 and B1 are stacked. The two unnumbered NPTH pegs are left out, as the
#: gate leaves them.
USBC_PADS = [
    ("A1", -3.2, -3.68, 0.6, 1.15), ("A4", -2.4, -3.68, 0.6, 1.15),
    ("A5", -1.25, -3.68, 0.3, 1.15), ("A6", -0.25, -3.68, 0.3, 1.15),
    ("A7", 0.25, -3.68, 0.3, 1.15), ("A8", 1.25, -3.68, 0.3, 1.15),
    ("A9", 2.4, -3.68, 0.6, 1.15), ("A12", 3.2, -3.68, 0.6, 1.15),
    ("B1", 3.2, -3.68, 0.6, 1.15), ("B4", 2.4, -3.68, 0.6, 1.15),
    ("B5", 1.75, -3.68, 0.3, 1.15), ("B6", 0.75, -3.68, 0.3, 1.15),
    ("B7", -0.75, -3.68, 0.3, 1.15), ("B8", -1.75, -3.68, 0.3, 1.15),
    ("B9", -2.4, -3.68, 0.6, 1.15), ("B12", -3.2, -3.68, 0.6, 1.15),
    ("S1", -4.32, -3.105, 1.0, 2.1), ("S1", -4.32, 1.075, 1.0, 1.8),
    ("S1", 4.32, -3.105, 1.0, 2.1), ("S1", 4.32, 1.075, 1.0, 1.8),
]

#: KiCad 9.0.9's ``Connector:USB_C_Receptacle_USB2.0_16P``: each pin's name.
USBC_NAMES = {"A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND",
              "A4": "VBUS", "A9": "VBUS", "B4": "VBUS", "B9": "VBUS",
              "A5": "CC1", "B5": "CC2", "A6": "D+", "B6": "D+", "A7": "D-", "B7": "D-",
              "A8": "SBU1", "B8": "SBU2", "S1": "SHIELD"}

#: KiCad 9.0.9's ``Power_Protection:IP4252CZ8``, a four-channel EMI filter
#: array: each channel's two ends carry one name.
IP4252_NAMES = {"1": "CH1", "2": "CH2", "3": "CH3", "4": "CH4",
                "5": "CH4", "6": "CH3", "7": "CH2", "8": "CH1", "9": "GND"}

FOOTPRINTS = next((d for d in (os.environ.get("KICAD9_FOOTPRINT_DIR", ""),
                               "/usr/share/kicad/footprints")
                   if d and os.path.isdir(os.path.join(d, "Connector_USB.pretty"))), None)


def _library_pins(lib: str, name: str) -> dict:
    with open(os.path.join(SYMBOLS, f"{lib}.kicad_sym"), encoding="utf-8") as fh:
        tree = K.parse(fh.read())
    sym = next(s for s in K.children(tree, "symbol") if str(s[1]) == name)
    pins = {}

    def walk(node):
        for x in node:
            if isinstance(x, list):
                if K.head(x) == "pin":
                    pins[str(K.value(x, "number"))] = str(K.value(x, "name"))
                walk(x)
    walk(sym)
    return pins


class TheStockTablesAreKiCads(unittest.TestCase):
    """The tables above, read again from KiCad's own libraries where they are
    installed, so a library that moves a pad or renames a pin is caught."""

    @unittest.skipIf(SYMBOLS is None, "needs KiCad's stock symbols")
    def test_the_pin_names(self):
        self.assertEqual(_library_pins("Connector", "USB_C_Receptacle_USB2.0_16P"), USBC_NAMES)
        self.assertEqual(_library_pins("Power_Protection", "IP4252CZ8"), IP4252_NAMES)

    @unittest.skipIf(FOOTPRINTS is None, "needs KiCad's stock footprints")
    def test_the_usb_c_pads(self):
        with open(os.path.join(FOOTPRINTS, "Connector_USB.pretty",
                               "USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"
                               ".kicad_mod"), encoding="utf-8") as fh:
            fp = K.parse(fh.read())
        pads = [(str(p[1]), float(K.child(p, "at")[1]), float(K.child(p, "at")[2]),
                 float(K.child(p, "size")[1]), float(K.child(p, "size")[2]))
                for p in K.children(fp, "pad") if str(p[1])]
        self.assertEqual(pads, USBC_PADS)


def _usbc_board(edit=None) -> list:
    """The receptacle's land as ``_land_fit`` takes it: ``[(name, LandPad)]``,
    ``edit({number: [x, y]})`` applied to the centres first."""
    centres = [[x, y] for _n, x, y, _w, _h in USBC_PADS]
    if edit is not None:
        edit(USBC_PADS, centres)
    return [(USBC_NAMES[n], K.LandPad(n, c[0], c[1], w, h))
            for (n, _x, _y, w, h), c in zip(USBC_PADS, centres)]


def _usbc_drawing(fit_globals) -> list:
    """The receptacle's drawing as a vendor prints it: turned 90 degrees, its
    origin at the mating face, y up, each pad by function. Its numbers differ
    from the footprint's in the order it lists a repeated function, which the
    fit must not care about."""
    pads = []
    for i, (n, x, y, w, h) in enumerate(USBC_PADS):
        # Turned 90 degrees (x, y) -> (-y, x), moved, then y up for the record.
        vx, vy = -y + 1.0, x - 2.0
        pads.append({"number": f"P{len(USBC_PADS) - i}", "function": USBC_NAMES[n],
                     "x": round(vx, 6), "y": round(-vy, 6), "w": h, "h": w})
    vendor = fit_globals["_drawing"]({"view": "top", "y_axis": "up", "pads": pads})
    assert isinstance(vendor, list), vendor
    return vendor


class TheLandFitIsAnchoredPastTheFullSearch(unittest.TestCase):
    """A part that repeats many functions had more pairings than the full search
    tries, and errored. Past that count the fit is anchored on the pads of a
    function of their own; pads are still never paired by number."""

    def setUp(self):
        self.registry = _registry()
        self.globals = self.registry.get("pcb.land_pattern")[1].__globals__
        self.fit = self.globals["_land_fit"]

    def test_a_usb_c_16p_receptacle_fits_where_it_errored(self):
        groups = {}
        for n, *_rest in USBC_PADS:
            groups[USBC_NAMES[n]] = groups.get(USBC_NAMES[n], 0) + 1
        tries = math.prod(math.factorial(c) for c in groups.values())
        self.assertEqual(tries, 55296)
        self.assertGreater(tries, self.globals["MAX_ASSIGNMENTS"], "the full search errored")
        fit = self.fit("J10", _usbc_board(), _usbc_drawing(self.globals))
        self.assertIsInstance(fit, dict, fit)
        self.assertEqual(fit["search"], "anchored on 4 pad(s) of a function of their own, "
                                        "76 assignment(s) of the rest")
        self.assertLess(fit["worst"]["residual_mm"], 1e-6)
        self.assertAlmostEqual(abs(fit["rotation_deg"]), 90.0, places=6)
        self.assertTrue(all(p["function"] == USBC_NAMES[p["board_pad"]] for p in fit["pairs"]))

    def test_a_usb_c_land_with_its_cc_pads_mirrored_fails(self):
        def mirrored(pads, centres):
            for c in centres:
                c[0] = -c[0]

        def cc_swapped(pads, centres):
            at = {n: i for i, (n, *_r) in enumerate(pads)}
            centres[at["A5"]][0], centres[at["B5"]][0] = (centres[at["B5"]][0],
                                                          centres[at["A5"]][0])
        for name, edit in (("the land drawn mirrored", mirrored), ("CC1 and CC2 swapped",
                                                                  cc_swapped)):
            with self.subTest(land=name):
                fit = self.fit("J10", _usbc_board(edit), _usbc_drawing(self.globals))
                self.assertIsInstance(fit, dict, fit)
                self.assertTrue(fit["search"].startswith("anchored"), fit["search"])
                w = fit["worst"]
                self.assertGreater(w["residual_mm"], w["limit_mm"], fit)

    def test_the_anchored_search_agrees_with_the_full_search(self):
        """Eight pads, two of a function of their own and three pairs: 8
        assignments in full, 6 anchored. Under a cap of 7 the anchored search
        runs, and reads as the full one does on the land and on its mirror."""
        names = ["A", "B", "G", "G", "V", "V", "D", "D"]
        at = [(-1.5, -1.0), (-0.5, -1.0), (0.5, -1.0), (1.5, -1.0),
              (1.5, 1.0), (0.5, 1.0), (-0.5, 1.0), (-1.5, 1.0)]
        board = [(f, K.LandPad(str(i + 1), x, y, 0.4, 0.8))
                 for i, (f, (x, y)) in enumerate(zip(names, at))]
        drawing = [{"number": str(i + 1), "function": f, "x": -y + 0.3, "y": x - 0.2,
                    "w": 0.8, "h": 0.4} for i, (f, (x, y)) in enumerate(zip(names, at))]
        mirror = [(f, K.LandPad(p.number, -p.x, p.y, p.w, p.h)) for f, p in board]
        for land, pads in (("good", board), ("mirrored", mirror)):
            with self.subTest(land=land):
                full = self.fit("U9", pads, drawing)
                with mock.patch.dict(self.globals, {"MAX_ASSIGNMENTS": 7}):
                    anchored = self.fit("U9", pads, drawing)
                self.assertEqual(full["search"], "every assignment (8)")
                self.assertTrue(anchored["search"].startswith("anchored on 2 pad(s)"),
                                anchored["search"])
                passed = [f["worst"]["residual_mm"] <= f["worst"]["limit_mm"]
                          for f in (full, anchored)]
                self.assertEqual(passed, [land == "good"] * 2, (full, anchored))
        self.assertAlmostEqual(full["worst"]["residual_mm"], anchored["worst"]["residual_mm"],
                               places=6)

    def test_too_few_anchors_still_errors(self):
        """Past the full search with fewer than two pads of a function of their
        own, nothing fixes the fit: the error stands, and says to split one."""
        board = [("G", K.LandPad(str(i), float(i), 0.0, 0.4, 0.8)) for i in range(8)]
        drawing = [{"number": str(i), "function": "G", "x": float(i), "y": 0.0,
                    "w": 0.4, "h": 0.8} for i in range(8)]
        why = self.fit("U9", board, drawing)
        self.assertIsInstance(why, str)
        self.assertIn("fewer than two of its functions sit on one pad alone", why)
        self.assertIn("split a function the part repeats", why)

    def test_pairing_by_number_is_for_registration_only(self):
        """The hook pairs by number when asked (a keep-out drawn around a
        module's numbered pads). On the baseline's U1 against its drawing
        renumbered 1<->3 and 4<->6, functions kept, pairing by function fits and
        pairing by number misfits. Anything else is refused."""
        pads, _unnumbered = K.footprint_land(TREE, "U1")
        names = {p.number: p.pinfunction for fp in K.board_from_tree(TREE).footprints
                 if fp.ref == "U1" for p in fp.pads}
        board = [(names[p.number], p) for p in pads]
        with open(os.path.join(ASSETS, "esda6v1bc6_land.json"), encoding="utf-8") as fh:
            record = json.load(fh)
        swap = {"1": "3", "3": "1", "4": "6", "6": "4"}
        for pad in record["pads"]:
            pad["number"] = swap.get(pad["number"], pad["number"])
        drawing = self.globals["_drawing"](record)
        by_function = self.fit("U1", board, drawing)
        by_number = self.fit("U1", board, drawing, pair_by="number")
        self.assertEqual((by_function["paired_by"], by_number["paired_by"]),
                         ("function", "number"))
        self.assertLessEqual(by_function["worst"]["residual_mm"],
                             by_function["worst"]["limit_mm"], by_function)
        self.assertGreater(by_number["worst"]["residual_mm"], by_number["worst"]["limit_mm"],
                           by_number)
        with self.assertRaises(ValueError):
            self.fit("U1", board, drawing, pair_by="position")

    def test_the_gate_pairs_by_function_never_by_number(self):
        """The baseline's drawing renumbered 1<->3 and 4<->6, its functions kept:
        the gate passes it (a pairing by number would put pad 1 on pad 3)."""
        with open(os.path.join(ASSETS, "esda6v1bc6_land.json"), encoding="utf-8") as fh:
            record = json.load(fh)
        swap = {"1": "3", "3": "1", "4": "6", "6": "4"}
        for pad in record["pads"]:
            pad["number"] = swap.get(pad["number"], pad["number"])
        root = tempfile.mkdtemp(prefix="nopekit-pcb-land-")
        self.addCleanup(shutil.rmtree, root, True)
        drawing = os.path.join(root, "renumbered.json")
        with open(drawing, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        board = _board_copy(root, _board_text())
        verdict = _run(self.registry, "pcb.land_pattern", root,
                       dict(_baseline(), pcb_board=board, pcb_land_drawings={"U1": drawing}))
        self.assertEqual(verdict.outcome, "pass", verdict.render())

    def test_a_mirrored_land_still_fails_through_the_gate(self):
        """The known-bad control's change, made here: U1's pads 1<->3 and 4<->6
        swapped, numbers and names kept."""
        tree = K.load_tree(BOARD)
        pads = {str(p[1]): p for p in K.children(_footprint(tree, "U1"), "pad")}
        for a, b in (("1", "3"), ("4", "6")):
            at_a, at_b = K.child(pads[a], "at"), K.child(pads[b], "at")
            at_a[1:3], at_b[1:3] = list(at_b[1:3]), list(at_a[1:3])
        root = tempfile.mkdtemp(prefix="nopekit-pcb-land-")
        self.addCleanup(shutil.rmtree, root, True)
        board = _board_copy(root, K.dump(tree) + "\n")
        verdict = _run(self.registry, "pcb.land_pattern", root, dict(
            _baseline(), pcb_board=board,
            pcb_land_drawings={"U1": os.path.join(ASSETS, "esda6v1bc6_land.json")}))
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertEqual(verdict.measured, 1.9, verdict.render())


#: The vias pcbnew 9.0.9 wrote for a blind via (F.Cu to In1.Cu), a buried one
#: (In1.Cu to In2.Cu, which it also writes ``blind``) and a microvia (measured:
#: ``pcbnew.SaveBoard`` on the baseline made four-layer).
VIAS_9 = {
    "blind": '(via blind (at 5 12) (size 0.45) (drill 0.2) (layers "F.Cu" "In1.Cu") (net 4) '
             '(uuid "1ecca1b2-cac7-4fca-9c08-bbb6c2daf091"))',
    "buried": '(via blind (at 7 12) (size 0.45) (drill 0.2) (layers "In1.Cu" "In2.Cu") (net 0) '
              '(uuid "15a587ce-5e1e-4fba-9963-c10ce6ca9441"))',
    "micro": '(via micro (at 9 12) (size 0.45) (drill 0.2) (layers "F.Cu" "In1.Cu") (net 0) '
             '(uuid "1603216d-a635-4af1-9dc7-fffb45567255"))',
}


def _stackup(*materials: str) -> str:
    """The baseline with a stated two-layer stackup, its one core in
    ``materials`` (more than one: KiCad's sublayers, ``addsublayer``), in the
    form KiCad 9.0.9's demos write it."""
    core = ['(layer "dielectric 1" (type "core") (thickness 1.51) '
            f'(material "{materials[0]}") (epsilon_r 4.5) (loss_tangent 0.02)']
    for m in materials[1:]:
        core.append(f' addsublayer (thickness 0.05) (material "{m}") (epsilon_r 3.5) '
                    f'(loss_tangent 0.01)')
    stack = ('(stackup (layer "F.SilkS" (type "Top Silk Screen")) '
             '(layer "F.Mask" (type "Top Solder Mask") (color "Green") (thickness 0.01) '
             '(material "Liquid Ink")) '
             '(layer "F.Cu" (type "copper") (thickness 0.035)) '
             + "".join(core) + ') '
             '(layer "B.Cu" (type "copper") (thickness 0.035)) '
             '(copper_finish "None") (dielectric_constraints no))')
    text = _board_text()
    anchor = "\t(setup\n"
    assert text.count(anchor) == 1
    return text.replace(anchor, anchor + "\t\t" + stack + "\n", 1)


def _with_via(kind: str) -> str:
    text = _board_text()
    anchor = "\t(segment\n"
    i = text.index(anchor)
    return text[:i] + "\t" + VIAS_9[kind] + "\n" + text[i:]


@unittest.skipIf(os.name == "nt", "the stand-in kicad-cli is a #! script")
class AFabServiceListsWhatItBuilds(unittest.TestCase):
    """A board whose stackup names a dielectric, or that holds a via kind, its
    service does not list skips, naming the gap, before kicad-cli runs. Several
    services and none named skips, naming them."""

    setUp = TheFabCountsOnlyItsOwnFindings.setUp
    _fab = TheFabCountsOnlyItsOwnFindings._fab

    def _fab_file(self, services: list, name: str = "fab.json") -> str:
        with open(os.path.join(PACK, "gates", "fab", "jlcpcb.json"), encoding="utf-8") as fh:
            standard = json.load(fh)["services"][0]
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"fab": "Fixture Fab", "source": "a fixture", "read": "2026-11-24",
                       "services": [dict(standard, **s) for s in services]}, fh)
        return path

    def test_the_baseline_reads_as_kicads_default_stackup(self):
        verdict = self._fab([])
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertTrue(verdict.detail.startswith("JLCPCB standard (1-2 layers, 1 oz"),
                        verdict.detail)
        self.assertIn("; the board: dielectric FR4 (KiCad's default: the board states no "
                      "stackup), 1 through via(s)", verdict.detail)

    def test_a_stated_fr4_stackup_runs(self):
        for spelled in ("FR4", "FR-4", "fr4"):
            with self.subTest(material=spelled):
                board = _board_copy(self.dir, _stackup(spelled), stem="fr4")
                verdict = self._fab([], board=board)
                self.assertEqual(verdict.outcome, "pass", verdict.render())
                self.assertIn(f"; the board: dielectric {spelled}, 1 through via(s)",
                              verdict.detail)

    def test_a_dielectric_the_service_does_not_list_skips(self):
        for name, materials, gap in (("polyimide", ("Polyimide",), "dielectric Polyimide"),
                                     ("a polyimide sublayer", ("FR4", "Polyimide"),
                                      "dielectric Polyimide"),
                                     ("a high-Tg laminate", ("FR408-HR",), "dielectric FR408-HR")):
            with self.subTest(board=name):
                board = _board_copy(self.dir, _stackup(*materials), stem="flex")
                verdict = self._fab([], board=board, runs=0)
                self.assertEqual(verdict.outcome, "skipped", verdict.render())
                self.assertIn(f"the board has {gap}, which JLCPCB's standard service does "
                              f"not list (it builds FR4; through vias)", verdict.skip_reason)
                self.assertNotIn("dielectric FR4,", verdict.skip_reason)

    def test_a_default_stackup_is_said_as_the_default_in_the_skip(self):
        """A board that states no stackup is read as KiCad's FR4: the skip says
        so, never that the board has FR4 (the review of P4)."""
        fab = self._fab_file([{"name": "flex", "materials": ["Polyimide"]}])
        self.params.update(pcb_fab=fab, pcb_fab_service="flex")
        verdict = self._fab([], runs=0)
        self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("the board has dielectric FR4 (KiCad's default: the board states no "
                      "stackup), which Fixture Fab's flex service does not list",
                      verdict.skip_reason)

    def test_a_board_newer_than_the_reader_skips_before_the_guard_reads_it(self):
        """The guard reads the stackup and vias with the pack's reader, so a file
        newer than it was checked through skips as at pcb.board_readable, and
        kicad-cli never runs (the review of P4: the guard read it on trust)."""
        for name, text in (("KiCad 10, a polyimide stackup", _by_name(_stackup("Polyimide"))),
                           ("KiCad 10, no stackup", _by_name(_board_text()))):
            with self.subTest(board=name):
                board = _board_copy(self.dir, text, stem="k10")
                verdict = self._fab([], board=board, runs=0)
                self.assertEqual(verdict.outcome, "skipped", verdict.render())
                self.assertTrue(verdict.skip_reason.startswith(
                    "this reader was checked through board format 20241229 (KiCad 9.0); "
                    "the file is format 20260206: the service guard reads the board's "
                    "stackup and vias with this pack's reader"), verdict.skip_reason)
                self.assertNotIn("the board has", verdict.skip_reason)

    def test_a_via_kind_the_service_does_not_list_skips(self):
        for kind in ("blind", "buried", "micro"):
            with self.subTest(via=kind):
                board = _board_copy(self.dir, _with_via(kind), stem=kind)
                self.assertEqual(K.via_kinds(K.load_tree(board))[kind], 1)
                verdict = self._fab([], board=board, runs=0)
                self.assertEqual(verdict.outcome, "skipped", verdict.render())
                self.assertIn(f"the board has 1 {kind} via(s), which JLCPCB's standard "
                              f"service does not list", verdict.skip_reason)

    def test_a_service_that_lists_them_judges_the_board(self):
        fab = self._fab_file([{"name": "hdi", "materials": ["FR4", "Polyimide"],
                               "via_types": ["through", "blind", "buried", "micro"]}])
        self.params["pcb_fab"] = fab
        self.params["pcb_fab_service"] = "hdi"
        board = _board_copy(self.dir, _stackup("Polyimide"), stem="flex")
        verdict = self._fab([], board=board)
        self.assertEqual(verdict.outcome, "pass", verdict.render())
        self.assertTrue(verdict.detail.startswith("Fixture Fab hdi ("), verdict.detail)

    def test_several_services_and_none_named_skips_naming_them(self):
        fab = self._fab_file([{"name": "standard"},
                              {"name": "flex", "materials": ["Polyimide"]}])
        self.params["pcb_fab"] = fab
        del self.params["pcb_fab_service"]
        verdict = self._fab([], runs=0)
        self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("Fixture Fab publishes limits for several services (standard, flex) "
                      "and there is no pcb_fab_service", verdict.skip_reason)
        self.params["pcb_fab_service"] = "standard"
        self.assertEqual(self._fab([]).outcome, "pass")

    def test_one_service_needs_no_name(self):
        del self.params["pcb_fab_service"]
        self.assertEqual(self._fab([]).outcome, "pass")

    def test_a_service_the_file_lacks_or_a_file_with_none_errors(self):
        self.params["pcb_fab_service"] = "rigid-flex"
        verdict = self._fab([], runs=0)
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("the fab file has no service 'rigid-flex': it lists standard",
                      verdict.error)
        with open(os.path.join(PACK, "gates", "fab", "jlcpcb.json"), encoding="utf-8") as fh:
            service = json.load(fh)["services"][0]
        old = os.path.join(self.dir, "old.json")
        with open(old, "w", encoding="utf-8") as fh:
            json.dump({"fab": "Old Fab", "common": service["common"],
                       "tables": service["tables"]}, fh)
        self.params.update(pcb_fab=old, pcb_fab_service="standard")
        verdict = self._fab([], runs=0)
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("the fab file lists no services (it holds common and tables at the "
                      "top, the shape before services", verdict.error)


class AFabFileIsReadStrictly(unittest.TestCase):
    """A service is held to its shape: a word where a list belongs, a via kind
    KiCad does not write, a missing key or a name given twice is refused."""

    def test_each_bad_shape_is_refused(self):
        with open(os.path.join(PACK, "gates", "fab", "jlcpcb.json"), encoding="utf-8") as fh:
            good = json.load(fh)
        svc = good["services"][0]
        self.assertEqual(K.fab_services(good), good["services"])
        for name, fab, said in (
                ("materials as a word", {"services": [dict(svc, materials="FR4")]},
                 "materials must list the dielectrics"),
                ("a via kind KiCad does not write", {"services": [dict(svc, via_types=["laser"])]},
                 "via_types ['laser'] must list some of"),
                ("no tables", {"services": [{k: v for k, v in svc.items() if k != "tables"}]},
                 "services[0] has no tables"),
                ("a name twice", {"services": [svc, svc]}, "names a service twice"),
                ("no services", {"fab": "X"}, "the fab file lists no services:")):
            with self.subTest(case=name):
                with self.assertRaises(ValueError) as caught:
                    K.fab_services(fab)
                self.assertIn(said, str(caught.exception))


def _with_filter(text: str, refs=("U3",), names=None) -> str:
    """The baseline with KiCad's IP4252CZ8 placed as each of ``refs``, each
    channel's two ends on two nets (``<ref>_K<n>`` in, ``<ref>_K<n>F`` out) and
    its pad 9 on GND, every pad carrying the stock symbol's pin name (``names``
    overrides some): an EMI filter on four key lines. Only the tier-0 reader
    reads it."""
    tree = K.parse(text)
    nets = K.children(tree, "net")
    code = max(int(n[1]) for n in nets)
    added = []

    def net(name: str) -> str:
        nonlocal code
        for n in nets + added:
            if str(n[2]) == name:
                return str(n[1])
        code += 1
        added.append(["net", str(code), K.Q(name)])
        return str(code)
    for ref in refs:
        pads = []
        for number, stock in IP4252_NAMES.items():
            name = (names or {}).get(number, stock)
            if stock == "GND":
                where = "GND"
            else:
                ch = stock[2:]
                where = f"{ref}_K{ch}" if int(number) <= 4 else f"{ref}_K{ch}F"
            pads.append(["pad", K.Q(number), "smd", "rect", ["at", "0", "0"],
                         ["size", "0.2", "0.4"], ["layers", K.Q("F.Cu")],
                         ["net", net(where), K.Q(where)], ["pinfunction", K.Q(name)],
                         ["pintype", K.Q("passive")]])
        tree.append(["footprint",
                     K.Q("Package_SON:Nexperia_HUSON-8_USON-8-1EP_1.35x1.7mm_P0.4mm_EP0.4x1.2mm"),
                     ["layer", K.Q("F.Cu")], ["at", "20", "12"],
                     ["property", K.Q("Reference"), K.Q(ref)],
                     ["property", K.Q("Value"), K.Q("IP4252CZ8")],
                     ["attr", "smd"], *pads])
    at = tree.index(nets[-1]) + 1
    tree[at:at] = added
    return K.dump(tree) + "\n"


class ASeriesPairIsStatedPerRef(unittest.TestCase):
    """KiCad's IP4252CZ8 names both ends of each channel alike (CH1 on pins 1
    and 8). With input and output on two nets it reads four shorts, until its
    pairs are stated for its ref; a pair stated on another ref clears nothing,
    and a pair the part does not give one name is a record for another
    design."""

    PAIRS = [["1", "8"], ["2", "7"], ["3", "6"], ["4", "5"]]
    SOURCE = "Nexperia IP4252CZ8 datasheet: channel n runs from pin n to pin 9-n"

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nopekit-pcb-series-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.registry = _registry()

    def _names(self, series, refs=("U3",), names=None):
        board = _board_copy(self.root, _with_filter(_board_text(), refs, names))
        return _run(self.registry, "pcb.pin_names", self.root,
                    dict(_baseline(), pcb_board=board, pcb_pin_names_series=series))

    def test_unstated_it_fails_and_stated_it_passes(self):
        verdict = self._names({})
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 4), verdict.render())
        self.assertIn("U3 CH1: U3_K1 on 1, U3_K1F on 8", verdict.detail)
        verdict = self._names({"U3": {"pairs": self.PAIRS, "source": self.SOURCE}})
        self.assertEqual((verdict.outcome, verdict.measured), ("pass", 0), verdict.render())
        self.assertIn("4 stated series pair(s) carry two nets, as pcb_pin_names_series "
                      "says: U3 CH1 (1/8), U3 CH2 (2/7), U3 CH3 (3/6), U3 CH4 (4/5)",
                      verdict.detail)
        self.assertTrue(verdict.detail.startswith("every other repeated pin name carries "
                                                  "one net"), verdict.detail)

    def test_the_title_names_the_exception(self):
        """A stated pair passes with two nets on one name, so a title that said
        "one net" said more than the gate checked (the review of P4)."""
        title = self.registry.get("pcb.pin_names")[0].title
        self.assertTrue(title.endswith("carry one net, except stated series pairs"), title)

    def test_a_pair_stated_on_another_ref_still_fails(self):
        verdict = self._names({"U4": {"pairs": self.PAIRS, "source": self.SOURCE}},
                              refs=("U3", "U4"))
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 4), verdict.render())
        self.assertIn("U3 CH1", verdict.detail)
        self.assertNotIn("U4 CH1:", verdict.detail)

    def test_a_record_for_another_design_errors(self):
        for name, series, said in (
                ("pins named apart", {"U3": {"pairs": [["1", "2"]], "source": self.SOURCE}},
                 "U3 pins 1 and 2 are named CH1 and CH2, not one name"),
                ("a ref the board lacks", {"U9": {"pairs": self.PAIRS, "source": self.SOURCE}},
                 "the board has no U9"),
                ("a pad the part lacks", {"U3": {"pairs": [["1", "10"]], "source": self.SOURCE}},
                 "U3 has no pad 10"),
                ("a name on more pins than the pair",
                 {"U3": {"pairs": [["1", "8"]], "source": self.SOURCE}},
                 "U3 names pins 1, 7, 8 CH1, and a series pair is the only two pins of "
                 "its name")):
            with self.subTest(case=name):
                three = {"7": "CH1"} if name == "a name on more pins than the pair" else None
                verdict = self._names(series, names=three)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn("the record is for another design", verdict.error)
                self.assertIn(said, verdict.error)

    def test_a_record_read_loosely_errors(self):
        for name, series, said in (
                ("no source", {"U3": {"pairs": self.PAIRS, "source": ""}}, "source is empty"),
                ("a misspelled key", {"U3": {"pair": self.PAIRS, "source": self.SOURCE}},
                 "unknown key(s) ['pair']"),
                ("not pairs", {"U3": {"pairs": [["1", "8", "2"]], "source": self.SOURCE}},
                 "a list of [pin, pin]"),
                ("not a map", [["1", "8"]], "is not {ref:")):
            with self.subTest(case=name):
                verdict = self._names(series)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn(said, verdict.error)



# --------------------------------------------------------------------------- #
# 0.3.0 P6: the ways a connector meets wrong, and the USB-C receptacle.
# --------------------------------------------------------------------------- #
def _to_net(tree, ref: str, number: str, name: str) -> None:
    """Pad ``number`` of ``ref`` moved onto the board's existing net ``name``."""
    K.child(_pad(tree, ref, number), "net")[1:] = [_net_code(tree, name), K.Q(name)]


def _swap(tree, ref: str, a: str, b: str) -> None:
    na, nb = K.child(_pad(tree, ref, a), "net"), K.child(_pad(tree, ref, b), "net")
    na[1:], nb[1:] = list(nb[1:]), list(na[1:])


#: A TRRS-like link on the two FFC housings: tip, ring 1, ring 2, sleeve on
#: the positions it names, the same on both ends.
def _link(p, mapping: dict, modes: list) -> None:
    p["pcb_matings"]["flex"].update(map=mapping, modes=modes)


class AMisMatingIsJudgedByTheModesStated(_PowerCase):
    """pcb.mismate on the power baseline: every stated wrong way, the nominal
    map, the coverage of every connector that carries a source, and a battery
    whose pigtail can be reversed. The FFC housings sit on one board, so a
    wrong cable makes a loop through their shared nets: the rule follows it, as
    the copper does."""

    GID = "pcb.mismate"

    def evidence(self, verdict) -> dict:
        with open(os.path.join(self.root, verdict.evidence[0]), encoding="utf-8") as fh:
            return json.load(fh)

    def test_the_baseline_passes_and_lists_the_signals_vbus_meets(self):
        verdict = self.run_gate(self.GID)
        self.assertEqual((verdict.outcome, verdict.measured, verdict.units),
                         ("pass", 0, "hazards"), verdict.render())
        self.assertIn("6 wrong way(s) of 4 mating(s)", verdict.detail)
        ev = self.evidence(verdict)
        self.assertEqual({(j["source"], j["meets"]) for j in ev["source_meets_signal"]},
                         {("J1's VBUS", "SIG_B"), ("J1's VBUS", "SIG_C")})
        self.assertEqual(ev["hazards"], [])

    def test_ground_beside_vbus_is_one_hazard_however_many_ways_make_it(self):
        """GND at 7 and VBUS at 5: the cable two positions off either way puts
        them together, two ways and one hazard."""
        verdict = self.run_gate(self.GID, lambda t: [_swap(t, r, "1", "7") for r in ("J10", "J11")])
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())
        self.assertIn("flex, narrower 6 at (0, 2): J10 pad 5 (VBUS) meets J11 pad 7 (GND)",
                      verdict.detail)
        self.assertEqual(self.evidence(verdict)["hazards"][0]["ways"],
                         ["flex, narrower 6 at (0, 2)", "flex, narrower 6 at (2, 0)"])

    def test_a_receptacle_no_record_names_still_brings_vbus(self):
        def records(p):
            p.pop("pcb_usbc")
        verdict = self.run_gate(self.GID, lambda t: [_swap(t, r, "1", "7") for r in ("J10", "J11")],
                                records)
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())

    def test_the_nominal_map_is_judged_first(self):
        """A regenerated J11 whose pins 1 and 5 trade nets: the straight cable,
        seated right, joins J10's GND to J11's VBUS. No mode is needed."""
        def records(p):
            p["pcb_matings"]["flex"]["modes"] = []
        verdict = self.run_gate(self.GID, lambda t: _swap(t, "J11", "1", "5"), records)
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())
        self.assertIn("flex's own map: J11 pad 1 (VBUS) meets J10 pad 1 (GND)", verdict.detail)

    def test_each_positional_mode(self):
        """Each positional mode on the single-row FFC housings. ``rows: 2`` is
        refused there: a column is two numbers only on KiCad's zigzag
        numbering, which a single row is not (review of P6: it shifted J10 by
        two numbers, a column it does not have, and failed on that).
        APositionalModeReadsThePadCentres has the two-row arithmetic."""
        for modes, outcome, said in (
                (["reversed"], "pass", "1 wrong way(s)"),
                (["rotate"], "pass", "1 wrong way(s)"),
                (["flipped_type"], "pass", "1 wrong way(s)"),
                ([{"offset": 2}], "pass", "4 wrong way(s)"),
                ([{"offset": 4}], "fail", "J10 off by -4 positions: J11 pad 5 (VBUS) meets "
                                          "J10 pad 1 (GND)"),
                ([{"offset": 2, "rows": 2}], "error", "offset with rows 2 moves the plug by "
                                                      "columns of KiCad's zigzag numbering")):
            with self.subTest(modes=modes):
                verdict = self.run_gate(self.GID, records=lambda p: _link(p, "straight", modes))
                self.assertEqual(verdict.outcome, outcome, verdict.render())
                self.assertIn(said, verdict.error if outcome == "error" else verdict.detail)

    def test_a_footprint_with_a_pad_left_out_is_refused(self):
        """J10 with its pad 4 taken off: the 6-way cable counts positions by
        number, and the left-out position is not on the board, so the rule
        refuses, naming the gap (review of P6: the positions skipped it, and
        a reversed 1, 2, 3, 5 met 2 with 3 where it meets the empty slot)."""
        def edit(t):
            fp = _footprint(t, "J10")
            fp.remove(_pad(t, "J10", "4"))
        verdict = self.run_gate(self.GID, edit)
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("narrower counts positions by pad number, and J10 (FFC_8P) has no pad 4 "
                      "among 1..8", verdict.error)
        verdict = self.run_gate(self.GID, edit, lambda p: _link(p, "reversed", []))
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("map is reversed, which meets position i with n+1-i, and J10 (FFC_8P) "
                      "has no pad 4 among 1..8", verdict.error)
        # Straight meets each pad with the pad of its own number: the gap
        # meets nothing, as it does, and the mating is judged.
        verdict = self.run_gate(self.GID, edit, lambda p: _link(p, "straight", []))
        self.assertEqual(verdict.outcome, "pass", verdict.render())

    def test_a_short_through_its_own_fault_element_is_protected(self):
        """Q1 stated as a FET trip at the cell's negative, so GND lies beyond
        it, and the cell's + on J10 pad 8 bridged to J10 pad 1 (GND): the
        cell's loop runs through Q1, so the hazard is protected, with J1 on
        the board or not. Review of P6: J1's GND on the same net read the pair
        raw, and the same short failed with J1 fitted and passed without it.
        VBUS on GND, J1's own loop, stays raw beside Q1's protected GND."""
        def trip(p):
            p["pcb_protectors"]["protector"]["functions"]["trip"] = {
                "kind": "fet", "terminal": "neg", "trip_a": 5.0, "trip_max_s": [[10.0, 0.01]],
                "switch": {"ref": "Q1", "value": "FDS6890A", "path": ["1", "3"]}}
            _link(p, "straight", [{"bridges": [["8", "1"]]}])

        def no_j1(p):
            trip(p)
            p.pop("pcb_usbc")
            p["pcb_matings"].pop("usb_in")

        def cell_on_8(t):
            _to_net(t, "J10", "8", "CELL_P")

        def cell_on_8_no_j1(t):
            cell_on_8(t)
            K.child(_footprint(t, "J1"), "attr").append("dnp")
        said = ("1 protected, judged by pcb.fault_current: flex, a plug that bridges 8-1 on "
                "J10: J10 pad 8 (CELL_P) meets J10 pad 1 (GND)")
        for name, edit, records in (("J1 fitted", cell_on_8, trip),
                                    ("J1 not fitted", cell_on_8_no_j1, no_j1)):
            with self.subTest(case=name):
                verdict = self.run_gate(self.GID, edit, records)
                self.assertEqual((verdict.outcome, verdict.measured), ("pass", 0),
                                 verdict.render())
                self.assertIn(said, verdict.detail)

        def vbus_on_gnd(p):
            trip(p)
            _link(p, "straight", [{"bridges": [["5", "1"]]}])
        verdict = self.run_gate(self.GID, records=vbus_on_gnd)
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())
        self.assertIn("J10 pad 5 (VBUS) meets J10 pad 1 (GND)", verdict.detail)

    def test_a_plug_slid_home_passes_every_contact(self):
        """Tip on VBUS (5), ring 1 open (3), ring 2 on SIG_C (7), sleeve on GND
        (1): three contacts short of home, the tip's VBUS meets the jack's
        sleeve."""
        def records(p):
            _link(p, {"5": "5", "3": "3", "7": "7", "1": "1"},
                  [{"slide": ["5", "3", "7", "1"]}])
        verdict = self.run_gate(self.GID, records=records)
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())
        self.assertIn("its plug 3 contacts short of home in J10", verdict.detail)
        self.assertIn("J10 pad 1 (GND)", verdict.detail)
        self.assertIn("J11 pad 5 (VBUS)", verdict.detail)

    def test_a_plug_that_bridges_two_contacts(self):
        """A TRS plug in a TRRS jack: its sleeve spans ring 2 and sleeve. With
        VBUS on ring 2 that is VBUS on GND; on SIG_C it is a signal grounded,
        not judged. On the wire out, a bridge puts VSYS, beyond F1, on GND:
        protected, named, not failed."""
        mapping = {"2": "2", "4": "4", "5": "5", "1": "1"}
        verdict = self.run_gate(self.GID, records=lambda p: _link(
            p, mapping, [{"bridges": [["5", "1"]]}]))
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())
        self.assertIn("a plug that bridges 5-1 on J10", verdict.detail)
        verdict = self.run_gate(self.GID, records=lambda p: _link(
            p, mapping, [{"bridges": [["4", "1"]]}]))
        self.assertEqual((verdict.outcome, verdict.measured), ("pass", 0), verdict.render())

        def wire(p):
            p["pcb_matings"]["wire"]["modes"] = [{"bridges": [["1", "2"]]}]
        verdict = self.run_gate(self.GID, records=wire)
        self.assertEqual((verdict.outcome, verdict.measured), ("pass", 0), verdict.render())
        self.assertIn("1 protected, judged by pcb.fault_current: wire, a plug that bridges "
                      "1-2 on J6", verdict.detail)
        self.assertEqual([h["raw"] for h in self.evidence(verdict)["protected"]], [False])

    def test_a_reversed_pigtail(self):
        """F1 at the cell's positive terminal limits a reversed cell's current
        (protected); with no fuse nothing does (a hazard), and the pack's PCM
        does not count; a series element stated as the reverse block blocks it.
        F1's place stands in for that element here."""
        def rev(extra=None):
            def records(p):
                p["pcb_matings"]["cell"]["modes"] = ["reversed"]
                if extra:
                    extra(p)
            return records

        def no_fuse(p):
            p["pcb_protectors"].pop("fuse")

        def pcm_trip(p):
            no_fuse(p)
            p["pcb_protectors"]["pack_pcm"]["functions"]["trip"] = {
                "kind": "pcm", "terminal": "neg", "trip_a": 5.0, "trip_max_s": [[10.0, 0.01]]}

        def block(p):
            no_fuse(p)
            p["pcb_protectors"]["blocker"] = {
                "battery": "main", "kind": "blocking", "where": "board",
                "board": POWER_BOARD, "ref": "F1", "value": "PTC_FIXTURE_0A5",
                "functions": {"reverse_block": {"terminal": "pos", "path_pins": ["1", "2"]}},
                "source": "a series element in F1's place, stated as the reverse block"}
        for name, extra, outcome, said in (
                ("fuse", None, "pass", "nothing blocks it, and F1 (ptc) limits its current"),
                ("nothing", no_fuse, "fail", "nothing blocks it or limits its current"),
                ("pack pcm", pcm_trip, "fail", "is in the pack, before the pigtail that "
                                               "reverses"),
                ("blocked", block, "pass", "cell: main reversed in BT1 is blocked by F1 "
                                           "PTC_FIXTURE_0A5")):
            with self.subTest(case=name):
                verdict = self.run_gate(self.GID, records=rev(extra))
                self.assertEqual(verdict.outcome, outcome, verdict.render())
                self.assertIn(said, verdict.detail)

    def test_a_source_on_another_source(self):
        """SIG_C stated as a rail. In a wrong way, VBUS on any other source is a
        hazard; on the nominal map, one at VBUS's 5 V is one rail and one at
        3.3 V is not."""
        def rail(volts):
            def records(p):
                p["pcb_rails"] = {"aux": {"board": POWER_BOARD, "net": "SIG_C",
                                          "nominal_v": volts, "return": "GND",
                                          "source": "a regulator's output, for the test"}}
            return records
        verdict = self.run_gate(self.GID, records=rail(5.0))
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())
        self.assertIn("J10 pad 5 (VBUS) meets J11 pad 7 (SIG_C)", verdict.detail)
        for volts, outcome in ((5.0, "pass"), (3.3, "fail")):
            with self.subTest(nominal_v=volts):
                def records(p, volts=volts):
                    rail(volts)(p)
                    _link(p, {"5": "7"}, [])
                verdict = self.run_gate(self.GID, records=records)
                self.assertEqual(verdict.outcome, outcome, verdict.render())

    def test_every_connector_that_carries_a_source_is_named(self):
        verdict = self.run_gate(self.GID, records=lambda p: p["pcb_matings"].pop("wire"))
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("no pcb_matings record names J6 (WIRE_OUT) carries VSYS", verdict.error)

    def test_a_record_read_loosely_errors_and_none_skips(self):
        def no_modes(p):
            p["pcb_matings"]["flex"].pop("modes")

        def typo(p):
            p["pcb_matings"]["flex"]["modes"] = ["revresed"]

        def narrower_all(p):
            p["pcb_matings"]["flex"]["modes"] = [{"narrower": 8}]

        def battery_mode(p):
            p["pcb_matings"]["cell"]["modes"] = [{"narrower": 1}]

        def other_pin(p):
            p["pcb_matings"]["flex"]["map"] = {"9": "1"}

        def other_battery(p):
            p["pcb_matings"]["cell"]["a"] = dict(p["pcb_matings"]["wire"]["a"])
        for records, said in (
                (no_modes, "pcb_matings.flex has no modes"),
                (typo, "is 'revresed' (did you mean 'reversed'?)"),
                (narrower_all, "a narrower cable has fewer ways than J10's 8"),
                (battery_mode, "narrower is not judged on a mating whose far side is battery"),
                (other_pin, "J10 (FFC_8P) has no pad 9"),
                (other_battery, "battery main's terminals are on BT1, not J6")):
            with self.subTest(said=said):
                verdict = self.run_gate(self.GID, records=records)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn(said, verdict.error)
        verdict = self.run_gate(self.GID, records=lambda p: p.pop("pcb_matings"))
        self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("no pcb_matings", verdict.skip_reason)

        def no_source(p):
            for key in ("pcb_batteries", "pcb_protectors", "pcb_usbc"):
                p.pop(key)
            p["pcb_matings"] = {"hdr": {"a": {"board": BOARD, "ref": "J1", "value": "USB_IN"},
                                        "b": {"external": "a host's lead"}, "modes": [],
                                        "source": "a keyed lead"}}
        verdict = self.run_gate(self.GID, records=no_source)
        self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("no source", verdict.skip_reason)

    def test_the_power_rules_read_a_mating_as_mismate_does(self):
        """One record, one reading: a mode the reader refuses is refused by the
        power rules too, and a battery or external side joins no net."""
        def typo(p):
            p["pcb_matings"]["flex"]["modes"] = [{"narower": 6}]
        for gid in POWER_GATES:
            with self.subTest(gate=gid):
                verdict = self.run_gate(gid, records=typo)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn("pcb_matings.flex.modes[0]", verdict.error)
        self.assertEqual(self.run_gate("pcb.discharge_cutoff").measured, 2.85)


class AUsbCReceptacleIsHeldToItsRole(_PowerCase):
    """pcb.usbc_receptacle on the power baseline's J1, a sink, and on edits of
    it: CC per pin, the Rd and Rp bands of the Type-C specification's Tables
    4-24 and 4-25, a source's VBUS switch, data on both rows."""

    GID = "pcb.usbc_receptacle"

    def usbc(self, p) -> dict:
        return p["pcb_usbc"]["usb_in"]

    def test_the_table_is_the_specifications(self):
        """gates/usbc.json, held to Type-C R2.0 4.11.1 as read on 2026-11-24:
        Rd 5.1 kOhm +-20 % (+-10 % to read the advertisement); Rp 56k +-20 %,
        22k +-5 %, 10k +-5 % to 4.75-5.5 V, 36k +-20 %, 12k +-5 %, 4.7k +-5 % to
        3.3 V."""
        with open(os.path.join(PACK, "gates", "usbc.json"), encoding="utf-8") as fh:
            table = json.load(fh)
        self.assertEqual((table["rd"]["nominal_ohm"], table["rd"]["tol_pct"]),
                         (5100, {"default_power": 20, "reads_rp": 10}))
        got = {(v, cur): (row["ohm"], row["tol_pct"])
               for v, rail in table["rp"]["rails"].items()
               for cur, row in rail.items() if cur != "rail"}
        self.assertEqual(got, {("5", "default"): (56000, 20), ("5", "1.5A"): (22000, 5),
                               ("5", "3A"): (10000, 5), ("3.3", "default"): (36000, 20),
                               ("3.3", "1.5A"): (12000, 5), ("3.3", "3A"): (4700, 5)})
        self.assertEqual(table["read"], "2026-11-24")

    def test_the_baseline_passes(self):
        verdict = self.run_gate(self.GID)
        self.assertEqual((verdict.outcome, verdict.measured, verdict.units),
                         ("pass", 0, "violations"), verdict.render())
        # J1 is on power_board, which pcb.pin_names never reads (pcb_board is
        # esd_guard), so this rule checks its bussing too, and says so.
        self.assertIn("J1 (sink, USB 2.0): CC1 R1 5.1k and CC2 R2 5.1k to GND, at "
                      "5.049k..5.151k inside Rd 4.08k..6.12k (5.1k +-20 %, Table 4-25); "
                      "A6, B6, A7 and B7 each on a net, D+ apart from D-; VBUS (A4, A9, B4, "
                      "B9), GND (A1, A12, B1, B12), D+ (A6, B6), D- (A7, B7) each on one net",
                      verdict.detail)

    def test_a_sink_s_faults(self):
        for name, edit, said in (
                ("shared CC", lambda t: _to_net(t, "J1", "B5", "CC1"),
                 "J1 CC1 and CC2 share one net (CC1)"),
                ("B6 open", lambda t: _drop_net(t, "J1", "B6"), "J1 B6 (D+, pad B6) is open"),
                ("CC open", lambda t: _drop_net(t, "J1", "A5"), "J1 CC1 (pad A5) is open"),
                ("10k Rd", lambda t: _set_value(t, "R1", "10k"),
                 "J1 CC1's Rd R1 10k is 9.9k..10.1k at its tolerance, outside 4.08k..6.12k")):
            with self.subTest(case=name):
                verdict = self.run_gate(self.GID, edit)
                self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1),
                                 verdict.render())
                self.assertIn(said, verdict.detail)
        # B6 on D-'s net is two faults this rule now sees on a board
        # pcb.pin_names does not read: D+ on D-, and the D+ pair split.
        verdict = self.run_gate(self.GID, lambda t: _to_net(t, "J1", "B6", "USB_DM"))
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 2), verdict.render())
        self.assertIn("J1 D+ and D- share USB_DM", verdict.detail)
        self.assertIn("J1 D+ pins sit on 2 nets (A6 on USB_DP, B6 on USB_DM)", verdict.detail)

    def test_bussing_where_pin_names_cannot_see_it(self):
        """Review of P6: pcb.pin_names reads pcb_board alone, so J1 on
        power_board with B6 on SIG_D, or B9 on SIG_A, passed with no rule
        looking. This rule checks it there; on pcb_board, with the pads named
        alike, it is pcb.pin_names' (which fails it), and with the names
        stripped it is this rule's again."""
        for name, edit, said in (
                ("B6 on SIG_D", lambda t: _to_net(t, "J1", "B6", "SIG_D"),
                 "J1 D+ pins sit on 2 nets (A6 on USB_DP, B6 on SIG_D)"),
                ("B9 on SIG_A", lambda t: _to_net(t, "J1", "B9", "SIG_A"),
                 "J1 VBUS pins sit on 2 nets (A4 on VBUS, A9 on VBUS, B4 on VBUS, B9 on "
                 "SIG_A)")):
            with self.subTest(case=name):
                verdict = self.run_gate(self.GID, edit)
                self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1),
                                 verdict.render())
                self.assertIn(said, verdict.detail)
                path = self.board(edit)

                def on_pcb_board(p, path=path):
                    p["pcb_board"] = path
                verdict = self.run_gate(self.GID, records=on_pcb_board, board=path)
                self.assertEqual(verdict.outcome, "pass", verdict.render())
                self.assertIn("VBUS, GND and each data pair bussed: pcb.pin_names' check on "
                              "this board", verdict.detail)
                self.assertEqual(self.run_gate("pcb.pin_names", records=on_pcb_board,
                                               board=path).outcome, "fail")

                def unnamed(t, edit=edit):
                    edit(t)
                    for pad in K.children(_footprint(t, "J1"), "pad"):
                        pad[:] = [x for x in pad if not (isinstance(x, list)
                                                         and K.head(x) == "pinfunction")]
                path = self.board(unnamed, stem="unnamed")

                def unnamed_pcb_board(p, path=path):
                    p["pcb_board"] = path
                verdict = self.run_gate(self.GID, records=unnamed_pcb_board, board=path)
                self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1),
                                 verdict.render())
                self.assertIn(said, verdict.detail)

    def test_a_pull_up_on_a_sink(self):
        verdict = self.run_gate(self.GID, lambda t: _to_net(t, "R2", "2", "VBUS"))
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 2), verdict.render())
        self.assertIn("R2 5.1k runs from J1 CC2 to VBUS: a sink terminates CC only to GND",
                      verdict.detail)
        self.assertIn("J1 CC2 has no Rd to GND", verdict.detail)

    def test_a_sink_that_reads_the_advertisement_takes_the_narrow_band(self):
        """4.7k at 5 %: 4.465k..4.935k, inside +-20 % (4.08k..6.12k) and outside
        +-10 % (4.59k..5.61k)."""
        def edit(t):
            _set_value(t, "R1", "4.7k")
            _set_value(t, "R2", "4.7k")
        for reads, outcome in ((False, "pass"), (True, "fail")):
            with self.subTest(reads_rp=reads):
                def records(p, reads=reads):
                    self.usbc(p).update(reads_rp=reads, cc_resistor_tol_pct=5)
                verdict = self.run_gate(self.GID, edit, records)
                self.assertEqual(verdict.outcome, outcome, verdict.render())
                if reads:
                    self.assertIn("outside 4.59k..5.61k (5.1k +-10 %, Table 4-25)",
                                  verdict.detail)

    #: Q1's two FETs in series from V5 (S2) to VBUS (S1), gate 1 its control:
    #: a back-to-back switch something turns off.
    Q1_SWITCH = {"ref": "Q1", "value": "FDS6890A", "path": ["1", "3"], "control": "2"}
    #: F1, a PTC, standing in for the switch: a source wired so is on whenever
    #: its rail is.
    F1_SWITCH = {"ref": "F1", "value": "PTC_FIXTURE_0A5", "path": ["1", "2"]}

    def _source(self, t, through: str = "Q1") -> None:
        """J1 as a source: R1 and R2 (56k) from CC to a 5 V rail V5, and VBUS
        fed from V5 through Q1 (its gate 1 on PROT_DO), or through F1 alone."""
        _move_pad_net(t, "R1", "2", "V5")
        _to_net(t, "R2", "2", "V5")
        if through == "Q1":
            _to_net(t, "Q1", "1", "VBUS")
            _to_net(t, "Q1", "3", "V5")
        else:
            _to_net(t, "F1", "1", "VBUS")
            _to_net(t, "F1", "2", "V5")
        _set_value(t, "R1", "56k")
        _set_value(t, "R2", "56k")

    def _as_source(self, current="default", switch=Q1_SWITCH):
        def records(p):
            u = self.usbc(p)
            u.pop("reads_rp")
            u.update(role="source", rp={"current": current, "rail_v": 5, "rail": "V5"})
            if switch is not None:
                u["vbus_switch"] = dict(switch)
        return records

    def test_a_source(self):
        verdict = self.run_gate(self.GID, self._source, self._as_source())
        self.assertEqual((verdict.outcome, verdict.measured), ("pass", 0), verdict.render())
        self.assertIn("to the 5 V rail V5", verdict.detail)
        self.assertIn("VBUS switched by Q1 FDS6890A, its control pin 2 on PROT_DO",
                      verdict.detail)
        verdict = self.run_gate(self.GID, self._source, self._as_source("3A"))
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 2), verdict.render())
        self.assertIn("outside 9.5k..10.5k (10k +-5 %, Table 4-24)", verdict.detail)
        verdict = self.run_gate(self.GID, self._source, self._as_source(switch=None))
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())
        self.assertIn("J1 VBUS has no switch", verdict.detail)

    def test_a_switch_is_a_part_something_turns_off(self):
        """Review of P6: F1, a PTC, read "VBUS switched by F1", on a source
        whose VBUS is on whenever its rail is (4.4.2's fault). A two-pad part
        fails; a switch's control pin tied to GND, VBUS or its supply, or open,
        fails; a switch named with no control pin is refused."""
        verdict = self.run_gate(self.GID, lambda t: self._source(t, "F1"),
                                self._as_source(switch=self.F1_SWITCH))
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())
        self.assertIn("F1 PTC_FIXTURE_0A5 has 2 pads: a two-terminal part (a fuse, a PTC, a "
                      "resistor, a ferrite) cannot switch", verdict.detail)
        for name, net, said in (("GND", "GND", "is on GND, GND: tied there"),
                                ("VBUS", "VBUS", "is on VBUS, VBUS: tied there"),
                                ("its supply", "V5", "is on V5, its supply: tied there")):
            with self.subTest(control_on=name):
                def edit(t, net=net):
                    self._source(t)
                    _to_net(t, "Q1", "2", net)
                verdict = self.run_gate(self.GID, edit, self._as_source())
                self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1),
                                 verdict.render())
                self.assertIn(f"Q1's control pin 2 {said}", verdict.detail)

        def open_gate(t):
            self._source(t)
            _drop_net(t, "Q1", "2")
        verdict = self.run_gate(self.GID, open_gate, self._as_source())
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())
        self.assertIn("Q1's control pin 2 is open", verdict.detail)
        no_control = {k: v for k, v in self.Q1_SWITCH.items() if k != "control"}
        verdict = self.run_gate(self.GID, self._source, self._as_source(switch=no_control))
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("vbus_switch has no control", verdict.error)
        on_path = dict(self.Q1_SWITCH, control="3")
        verdict = self.run_gate(self.GID, self._source, self._as_source(switch=on_path))
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("vbus_switch.control is 3, one of its path pins", verdict.error)

    def test_an_rd_on_a_source(self):
        """The baseline's Rd left in place on a source: each runs to GND, so
        neither pin has an Rp."""
        def records(p):
            self._as_source()(p)
            self.usbc(p)["rp"]["rail"] = "VSYS"
            self.usbc(p).pop("vbus_switch")
        verdict = self.run_gate(self.GID, records=records)
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertIn("R1 5.1k runs from J1 CC1 to GND: a source terminates CC only to its "
                      "Rp rail", verdict.detail)
        self.assertIn("J1 CC1 has no Rp to the 5 V rail VSYS", verdict.detail)

    def test_a_controller_terminates_cc(self):
        """CC1 and CC2 on U2's pads 4 and 5 (a controller's CC pins stand in);
        R1 beside it is counted until others names it."""
        def edit(t):
            _to_net(t, "J1", "A5", "PROT_DO")
            _to_net(t, "J1", "B5", "PROT_CO")

        def records(p):
            u = self.usbc(p)
            for k in ("reads_rp", "cc_resistor_tol_pct"):
                u.pop(k)
            u["cc_by"] = {"ref": "U2", "value": "PROT_1S_FIXTURE",
                          "pins": {"CC1": "4", "CC2": "5"}}
        verdict = self.run_gate(self.GID, edit, records)
        self.assertEqual((verdict.outcome, verdict.measured), ("pass", 0), verdict.render())
        self.assertIn("CC on U2 PROT_1S_FIXTURE", verdict.detail)

        def beside(t):
            edit(t)
            _to_net(t, "R1", "1", "PROT_DO")
        verdict = self.run_gate(self.GID, beside, records)
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())
        self.assertIn("R1 5.1k runs from J1 CC1 to GND beside U2", verdict.detail)

        def declared(p):
            records(p)
            self.usbc(p)["others"] = [{"ref": "R1", "value": "5.1k",
                                       "why": "a dead-battery Rd the controller switches out"}]
        self.assertEqual(self.run_gate(self.GID, beside, declared).outcome, "pass")

    def test_a_resistor_beside_the_rd_named_in_others(self):
        """Review of P6: a sink that reads the advertisement through a series
        resistor into an ADC (R3 here, from CC1 to PROG) could not be stated to
        pass: others was refused without cc_by. Named in others it is kept out
        of the count; a resistor to GND named there is refused, since it is in
        parallel with the Rd."""
        def edit(t):
            _to_net(t, "R3", "2", "CC1")

        def reads(p):
            self.usbc(p).update(reads_rp=True)
        verdict = self.run_gate(self.GID, edit, reads)
        self.assertEqual((verdict.outcome, verdict.measured), ("fail", 1), verdict.render())
        self.assertIn("R3 3k runs from J1 CC1 to PROG: a sink terminates CC only to GND",
                      verdict.detail)

        def named(p):
            reads(p)
            self.usbc(p)["others"] = [{"ref": "R3", "value": "3k",
                                       "why": "a series resistor into the ADC that reads CC1"}]
        verdict = self.run_gate(self.GID, edit, named)
        self.assertEqual((verdict.outcome, verdict.measured), ("pass", 0), verdict.render())
        self.assertIn("inside Rd 4.59k..5.61k (5.1k +-10 %, Table 4-25); R3 beside it, named "
                      "in others with why (not counted)", verdict.detail)

        def parallel(p):
            reads(p)
            self.usbc(p)["others"] = [{"ref": "R1", "value": "5.1k", "why": "a second Rd"}]
        verdict = self.run_gate(self.GID, records=parallel)
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("others names R1, which runs from J1 CC1 to GND: a resistor to GND, VBUS "
                      "or the Rp rail terminates or pulls CC", verdict.error)

    def test_a_controller_is_no_receptacle(self):
        """Review of P6: KiCad 9.0.9's FUSB302BMPX names its pins CC1 and CC2,
        and pcbnew copies the names onto the pads, so the controller read as a
        receptacle no record named: this rule errored, with no record that
        could clear it, and pcb.mismate read its VBUS and GND as a
        receptacle's. The pin names count on a connector only."""
        helpers = self.helpers
        # FUSB302BMPX, Interface_USB.kicad_sym in KiCad 9.0.9: its pins by
        # number, on Package_DFN_QFN:WQFN-14-1EP_2.5x2.5mm_P0.5mm_EP1.45x1.45mm.
        names = {"1": "CC2", "2": "VBUS", "3": "VDD", "4": "VDD", "5": "INT_N", "6": "SCL",
                 "7": "SDA", "8": "GND", "9": "GND", "10": "CC1", "11": "CC1",
                 "12": "VCONN", "13": "VCONN", "14": "CC2", "15": "GND"}
        pads = [K.Pad(number=n, net="", pinfunction=f, pintype="") for n, f in names.items()]
        qfn = "Package_DFN_QFN:WQFN-14-1EP_2.5x2.5mm_P0.5mm_EP1.45x1.45mm"
        for ref, lib, found in (("U2", qfn, False), ("IC1", qfn, False),
                                ("J3", qfn, True), ("USB1", "Project:Recept_16P", True),
                                ("U9", "Connector_USB:Recept_16P", True),
                                ("U9", "Project:USB_C_Receptacle_16P", True)):
            with self.subTest(ref=ref, lib=lib):
                fp = K.Footprint(ref=ref, value="", lib_id=lib, pads=pads)
                self.assertEqual(helpers.typec_receptacle(fp), found)

        def controller(t):
            K.child(_pad(t, "U2", "4"), "pinfunction")[1] = K.Q("CC1")
            K.child(_pad(t, "U2", "5"), "pinfunction")[1] = K.Q("CC2")
        verdict = self.run_gate(self.GID, controller)
        self.assertEqual((verdict.outcome, verdict.measured), ("pass", 0), verdict.render())
        verdict = self.run_gate("pcb.mismate", controller)
        self.assertEqual((verdict.outcome, verdict.measured), ("pass", 0), verdict.render())

    def test_a_footprint_numbered_otherwise_takes_a_pins_map(self):
        def edit(t):
            _pad(t, "J1", "A5")[1] = K.Q("C1")
            _pad(t, "J1", "B5")[1] = K.Q("C2")
        verdict = self.run_gate(self.GID, edit)
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("has no pad A5 (CC1): name its pad in pins", verdict.error)

        def records(p):
            self.usbc(p)["pins"] = {"A5": "C1", "B5": "C2"}
        self.assertEqual(self.run_gate(self.GID, edit, records).outcome, "pass")

    def test_no_record_skips_naming_the_receptacle_and_an_unnamed_one_errors(self):
        verdict = self.run_gate(self.GID, records=lambda p: p.pop("pcb_usbc"))
        self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("no USB-C receptacle on esd_guard.kicad_pcb", verdict.skip_reason)

        def on_power(p):
            p.pop("pcb_usbc")
            p["pcb_board"] = POWER_BOARD
        verdict = self.run_gate(self.GID, records=on_power)
        self.assertEqual(verdict.outcome, "skipped", verdict.render())
        self.assertIn("J1 on power_board.kicad_pcb is a USB-C receptacle", verdict.skip_reason)
        other = self.board(stem="other")

        def two(p):
            p["pcb_board"] = POWER_BOARD
            self.usbc(p)["board"] = other
        verdict = self.run_gate(self.GID, records=two)
        self.assertEqual(verdict.outcome, "error", verdict.render())
        self.assertIn("J1 (USB4105-GF-A) on power_board.kicad_pcb is a USB-C receptacle that "
                      "pcb_usbc does not name", verdict.error)

    def test_a_record_read_loosely_errors(self):
        def sink_rp(p):
            self.usbc(p)["rp"] = {"current": "default", "rail_v": 5, "rail": "VBUS"}

        def no_reads(p):
            self.usbc(p).pop("reads_rp")

        def no_tol(p):
            self.usbc(p).pop("cc_resistor_tol_pct")

        def data_on_six(p):
            self.usbc(p)["pins"] = {"A6": "S1"}
        for records, said in (
                (sink_rp, "is a sink: it takes no rp"),
                (no_reads, "has no reads_rp"),
                (no_tol, "has no cc_resistor_tol_pct"),
                (lambda p: self.usbc(p).update(role="dfp"), "give one of sink, source, drp"),
                (lambda p: self.usbc(p).update(value="OTHER"), "is for another design")):
            with self.subTest(said=said):
                verdict = self.run_gate(self.GID, records=records)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn(said, verdict.error)

    @unittest.skipIf(FOOTPRINTS is None, "needs KiCad's stock footprints")
    def test_every_stock_receptacle_is_found_and_no_plug(self):
        """KiCad 9.0.9's 22 stock USB-C receptacles, each read as a receptacle by
        its name and pads; its three plugs, none."""
        helpers = self.helpers
        lib = os.path.join(FOOTPRINTS, "Connector_USB.pretty")
        found = {}
        for name in sorted(os.listdir(lib)):
            if not name.startswith("USB_C_"):
                continue
            with open(os.path.join(lib, name), encoding="utf-8") as fh:
                tree = K.parse(fh.read())
            pads = [K.Pad(number=str(p[1]), net="", pinfunction="", pintype="")
                    for p in K.children(tree, "pad") if len(p) > 1]
            fp = K.Footprint(ref="J1", value="", lib_id=f"Connector_USB:{name[:-10]}",
                             pads=pads)
            found[name[:-10]] = helpers.typec_receptacle(fp)
        self.assertEqual(sum(found.values()), 22, found)
        self.assertFalse(any(v for k, v in found.items() if "Plug" in k), found)


class _Drawn:
    """What ``Mating`` asks of a ``Design``, for footprints drawn here as pad
    centres alone: the positional arithmetic with no board file."""

    def __init__(self, **land):
        self.at = land
        self.fps = {ref: K.Footprint(ref=ref, value="H", lib_id="Connector:H",
                                     pads=[K.Pad(number=n, net=f"N{n}", pinfunction="",
                                                 pintype="") for n in at])
                    for ref, at in land.items()}

    def board(self, rec, label):
        return "drawn"

    def part(self, path, ref, value, label, need=()):
        return self.fps[str(ref)]

    def land(self, path, fp):
        return self.at[fp.ref]


def _zigzag(n: int, pitch: float = 2.54) -> dict:
    """A 2-row header on KiCad's zigzag numbering: odd pads on one row."""
    return {str(k): (((k - 1) // 2) * pitch, ((k - 1) % 2) * pitch) for k in range(1, n + 1)}


def _dip(n: int, pitch: float = 2.54, width: float = 7.62) -> dict:
    """A 2-row footprint on a DIP's counter-clockwise numbering: 1..n/2 down
    one row, the rest back up the other."""
    return {str(k): ((0.0, (k - 1) * pitch) if k <= n // 2 else (width, (n - k) * pitch))
            for k in range(1, n + 1)}


def _row(numbers, pitch: float = 2.54) -> dict:
    return {str(k): ((int(k) - 1) * pitch, 0.0) for k in numbers}


class APositionalModeReadsThePadCentres(unittest.TestCase):
    """Review of P6: the turned modes met position i with n+1-i from the pad
    numbers, offset with rows 2 shifted by two numbers, and a pad left out
    was skipped, on any footprint. A DIP-numbered header turned 180 degrees
    meets 1 with n/2+1, and a reversed 1, 2, 3, 5 meets 2 with the empty
    slot. Each is read on the pad centres now, or refused, naming why."""

    def setUp(self):
        self.P = load_path(os.path.join(PACK, "gates", "_power.py"))

    def mating(self, d, modes, mapping="straight"):
        return self.P.Mating(d, "m", {"a": {"ref": "J1", "value": "H"},
                                      "b": {"ref": "J2", "value": "H"},
                                      "map": mapping, "modes": modes, "source": "drawn"})

    def met(self, m, i=0) -> dict:
        """``{b pin: a pin}`` of the ``i``-th way its first mode meets."""
        _what, groups = m.instances(m.modes[0])[i]
        return {dict(g)["b"]: dict(g).get("a") for g in groups}

    def test_a_turned_header_meets_the_pad_its_centre_turns_onto(self):
        for name, land, want in (
                ("zigzag", _zigzag(8), {"1": "8", "2": "7", "3": "6", "4": "5"}),
                ("DIP", _dip(8), {"1": "5", "2": "6", "3": "7", "4": "8"}),
                ("one row", _row(range(1, 6)), {"1": "5", "2": "4", "3": "3"})):
            for mode in ("rotate", "reversed"):
                if name == "one row" and mode == "rotate":
                    continue                    # an odd count is refused for rotate
                with self.subTest(numbering=name, mode=mode):
                    m = self.mating(_Drawn(J1=land, J2=land), [mode])
                    got = self.met(m)
                    self.assertEqual({k: got[k] for k in want}, want)

    def test_offset_by_columns_needs_zigzag_numbering(self):
        m = self.mating(_Drawn(J1=_zigzag(8), J2=_zigzag(8)), [{"offset": 1, "rows": 2}])
        ways = {what: i for i, (what, _g) in enumerate(m.instances(m.modes[0]))}
        self.assertEqual(self.met(m, ways["J1 off by +1 column"])["1"], "3")
        with self.assertRaisesRegex(self.P.RecordError, "pad centres are not on it"):
            self.mating(_Drawn(J1=_dip(8), J2=_dip(8)), [{"offset": 1, "rows": 2}])

    def test_a_gap_or_a_pad_with_no_partner_is_refused(self):
        gapped = _row(["1", "2", "3", "5"])
        full = _row(range(1, 6))
        with self.assertRaisesRegex(self.P.RecordError, r"J1 \(H\) has no pad 4 among 1..5"):
            self.mating(_Drawn(J1=gapped, J2=gapped), ["reversed"])
        with self.assertRaisesRegex(self.P.RecordError, r"J1 \(H\) has no pad 4 among 1..5"):
            self.mating(_Drawn(J1=gapped, J2=gapped), [], mapping="reversed")
        with self.assertRaisesRegex(self.P.RecordError, r"J2 \(H\) has no pad 4 among 1..5"):
            self.mating(_Drawn(J1=full, J2=gapped), ["flipped_type"],
                        mapping={"1": "1", "2": "2", "3": "3", "5": "5"})
        with self.assertRaisesRegex(self.P.RecordError, "J1 has 4 positions and J2 6"):
            self.mating(_Drawn(J1=_row(range(1, 5)), J2=_row(range(1, 7))), [],
                        mapping="reversed")
        skewed = dict(_row(range(1, 5)), **{"4": (7.62, 0.5)})
        with self.assertRaisesRegex(self.P.RecordError, "turned 180 degrees puts pad 2 where "
                                                        "it has no numbered pad"):
            self.mating(_Drawn(J1=skewed, J2=skewed), ["reversed"])


def _drop_net(tree, ref: str, number: str) -> None:
    """Pad ``number`` of ``ref`` on no net: KiCad writes such a pad with no
    ``(net ...)``."""
    pad = _pad(tree, ref, number)
    pad[:] = [x for x in pad if not (isinstance(x, list) and K.head(x) == "net")]


# --------------------------------------------------------------------------- #
# general-packs P7: the boards in their enclosure
# --------------------------------------------------------------------------- #
#: Whether the fit can run here: kicad-cli exports the board, and CadQuery (in
#: the interpreter the gate starts its helper with) measures it.
FIT_TOOLS = (shutil.which("kicad-cli") is not None
             and importlib.util.find_spec("cadquery") is not None)
FIT_SKIP = "needs kicad-cli and CadQuery: the fit exports the board and measures it"

#: The claim a fit test reads its limit from: the baseline's own (``_claims``,
#: FIT1), at another limit when a test says so.
def _fit_claim(limit: float = 1.0, units: str = "mm", comparator: str = ">=") -> Claim:
    return Claim.from_dict({
        "id": "FIT1", "statement": "every part keeps its gap from the enclosure",
        "kind": "measurable", "tags": ["enclosure-fit"],
        "acceptance": {"quantity": "board fit in its enclosure", "comparator": comparator,
                       "limit": limit, "units": units}})


def _fit_ctx(out: str, params: dict, claim: Claim | None = None) -> gates_mod.GateContext:
    """The baseline's own project (paths resolve in the pack) with ``params``
    over its projection, and the claim tagged enclosure-fit as the ledger."""
    return gates_mod.GateContext(
        root=PACK, ledger=Ledger(meta=ProjectMeta(name="fit"),
                                 claims=[claim or _fit_claim()]),
        model=None, params=params, out_dir=out, tier=1, log=lambda _m: None, extra={})


#: Writes a STEP of solids for a test enclosure, in a child as the gate's own
#: geometry runs: a ``shell`` is a closed box of ``wall`` mm around the cavity
#: ``lo``-``hi``, a ``block`` is the box ``lo``-``hi``; each ``add`` box is joined
#: to it (a rib).
_STEP_SCRIPT = r"""
import json, sys
import cadquery as cq
spec = json.loads(sys.argv[1])
def box(lo, hi):
    return (cq.Workplane("XY").box(*(h - l for l, h in zip(lo, hi)), centered=False)
            .translate(tuple(lo)))
kind, lo, hi, wall = spec["solid"]
shape = box(lo, hi)
if kind == "shell":
    shape = box([v - wall for v in lo], [v + wall for v in hi]).cut(shape)
for add_lo, add_hi in spec.get("add", []):
    shape = shape.union(box(add_lo, add_hi))
cq.exporters.export(shape, spec["out"])
"""


def _enclosure_step(path: str, kind: str, lo, hi, wall: float = 2.0, add=()) -> str:
    """The STEP of ``_STEP_SCRIPT``'s solid at ``path``, written by CadQuery in a
    child run through ``_env.run``. (What slipped through P7: a bare
    ``subprocess.run`` here inherited this machine's HOME, git identity and agent
    variables, and ``test_meta.NoSubprocessOutsideRun`` went red.)"""
    proc = _env.run([sys.executable, "-c", _STEP_SCRIPT,
                     json.dumps({"solid": [kind, list(lo), list(hi), wall], "out": path,
                                 "add": [[list(a), list(b)] for a, b in add]})],
                    cwd=os.path.dirname(path), timeout=120)
    if proc.returncode != 0:
        raise AssertionError(f"CadQuery did not write {path}: {proc.stderr[-2000:]}")
    return path


def _variant_board(root: str, edit) -> str:
    """The baseline board with ``edit(tree)`` applied, as ``<root>/b.kicad_pcb``,
    its project file and its 3D models beside it (``${KIPRJMOD}/3d``)."""
    tree = K.load_tree(BOARD)
    edit(tree)
    path = _board_copy(root, K.dump(tree) + "\n")
    if not os.path.isdir(os.path.join(root, "3d")):
        shutil.copytree(os.path.join(ASSETS, "3d"), os.path.join(root, "3d"))
    return path


def _drop_model(ref: str):
    def edit(tree):
        fp = _footprint(tree, ref)
        fp[:] = [x for x in fp if not (isinstance(x, list) and K.head(x) == "model")]
    return edit


def _lose_model(ref: str):
    """``ref``'s model pointed at a file that is not there: kicad-cli leaves the
    part out of its export, with exit 0."""
    def edit(tree):
        for item in _footprint(tree, ref):
            if isinstance(item, list) and K.head(item) == "model":
                item[1] = K.Q("${KIPRJMOD}/3d/not-installed.step")
    return edit


class TheFitIsPureArithmeticWhereItCanBe(unittest.TestCase):
    """The placement's rotation and a declared height's box, without KiCad: the
    pieces the fit hands kicad-cli and OpenCascade."""

    def setUp(self):
        self.g = _registry().get("pcb.enclosure_fit")[1].__globals__

    def test_one_rotation_about_x_then_y_then_z(self):
        rot = self.g["_rotation"]
        zero = {"rot_x_deg": 0.0, "rot_y_deg": 0.0, "rot_z_deg": 0.0}
        self.assertEqual(rot(zero), [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        self.assertEqual(rot(dict(zero, rot_x_deg=180.0)),
                         [[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, -1.0]],
                         "face down: y and z reversed, written as whole numbers")
        # About x first, then z: the board's z (its pins) ends on +x, its x on +y.
        # In the other order its pins would end on -y.
        m = rot(dict(zero, rot_x_deg=90.0, rot_z_deg=90.0))
        self.assertEqual(m, [[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        m = rot(dict(zero, rot_x_deg=30.0, rot_y_deg=-50.0, rot_z_deg=125.0))
        for i in range(3):
            for j in range(3):
                dot = sum(m[k][i] * m[k][j] for k in range(3))
                self.assertAlmostEqual(dot, 1.0 if i == j else 0.0, places=12)

    def test_the_height_box_is_the_courtyards_in_the_footprints_frame(self):
        box = self.g["_courtyard_box"]
        self.assertEqual(box(_footprint(TREE, "U1")), (-2.05, -1.7, 2.05, 1.7))
        # Flipped to B.Cu, KiCad stores the courtyard with y negated: the box is
        # read back into the library frame, as footprint_land reads pads.
        tree = K.load_tree(BOARD)
        fp = _footprint(tree, "J1")
        front = box(fp)
        K.child(fp, "layer")[1] = K.Q("B.Cu")
        for item in fp:
            if isinstance(item, list) and K.value(item, "layer") == "F.CrtYd":
                K.child(item, "layer")[1] = K.Q("B.CrtYd")
                for key in ("start", "end"):
                    K.child(item, key)[2] = str(-float(K.child(item, key)[2]))
        self.assertEqual(box(fp), front)
        self.assertEqual(front, (-1.8, -1.8, 1.8, 9.4))
        # A part with no courtyard on its own side has no box.
        K.child(fp, "layer")[1] = K.Q("F.Cu")
        self.assertIsNone(box(fp))

    def test_an_arc_adds_the_extremes_it_passes(self):
        arc = self.g["_arc_points"]
        # A half circle of radius 1 over the top (y up in this arithmetic):
        # it passes (0, 1), not (0, -1).
        pts = arc((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0))
        ys = [round(p[1], 9) for p in pts]
        self.assertEqual((min(ys), max(ys)), (0.0, 1.0))
        pts = arc((1.0, 0.0), (0.0, -1.0), (-1.0, 0.0))
        ys = [round(p[1], 9) for p in pts]
        self.assertEqual((min(ys), max(ys)), (-1.0, 0.0))


class AContactRegionIsHeldToWhatItsPartCanReach(unittest.TestCase):
    """The geometry's answer as ``_fit.py`` writes it, judged without KiCad. In
    the review of P7, a region 0.1 mm short of the enclosure's whole box
    excused each header against the walls and the floor as well as the lid it
    named. A region may now take out material no further than twice the claim's
    limit past its part's own box.

    J1 as placed at z 5.8 in the baseline enclosure: its box, and the
    enclosure's, as the geometry measured them."""

    BOX = [[2.73, -12.89, 4.395], [5.27, -2.73, 15.935]]
    ENCLOSURE = [[-4.0, -19.0, -2.0], [34.0, 4.0, 18.5]]
    LID = [[1.0, -14.5, 16.0], [7.0, -1.0, 17.5]]

    def setUp(self):
        self.g = _registry().get("pcb.enclosure_fit")[1].__globals__

    def _refusal(self, regions: dict, material: dict, clearance=1.565, limit=1.0, fid="",
                 parts=None):
        contacts = self.g["_contacts"](
            {"J1": {f: {"reason": "seats on it", "region_mm": r} for f, r in regions.items()}},
            "pcb_enclosure_contacts")
        fit = self.g["_Fit"](fid, "b.kicad_pcb", "e.step", {}, set(), contacts, {})
        row = {"interference_mm3": 0.0, "clearance_mm": clearance, "bbox": self.BOX,
               "excused": {f: {"clearance_mm": 0.565, "material_bbox": m}
                           for f, m in material.items()}}
        if parts is None:
            parts = {"J1": {"enclosure": row, "boards": {}}}
        return self.g["_region_refusal"](fit, parts, self.ENCLOSURE, limit)

    def test_a_region_near_its_part_stands(self):
        self.assertIsNone(self._refusal({"lid": self.LID},
                                        {"lid": [[1.0, -14.5, 16.5], [7.0, -1.0, 17.5]]}))
        self.assertIsNone(self._refusal({"lid": self.LID}, {"lid": None}),
                          "a region in the air takes out nothing and excuses nothing")
        self.assertIsNone(self._refusal({"lid": self.LID}, {"lid": None}, parts={}),
                          "a part not in the export is named as missing, not judged here")

    def test_the_reviews_region_is_refused_naming_what_it_may_hold(self):
        region = [[-4.0, -19.0, -2.0], [34.0, 4.0, 18.4]]
        refused = self._refusal({"lid": region}, {"lid": region}, clearance=2.465, fid="main")
        self.assertIsNotNone(refused)
        error, detail = refused
        self.assertEqual(error, "main:J1 against lid: the region reaches more than 2 mm past "
                                "J1's own box (2 times the claim's limit). Draw it within "
                                "[[0.73, -14.89, 2.395], [7.27, -0.73, 17.935]]")
        self.assertLessEqual(len(error), 200, "a verdict's error keeps 200 characters")
        self.assertIn("the region takes out enclosure material from (-4, -19, -2) to "
                      "(34, 4, 18.4) mm", detail)
        self.assertIn("J1's own box, as placed, is (2.73, -12.89, 4.395) to "
                      "(5.27, -2.73, 15.935) mm, and a region may reach 2 mm past it", detail)

    def test_the_reach_is_twice_the_claims_limit(self):
        def at(top, limit=1.0):
            return self._refusal({"lid": self.LID},
                                 {"lid": [[1.0, -14.5, 16.5], [7.0, -1.0, top]]}, limit=limit)
        self.assertIsNone(at(17.935 + 0.0005), "within the printed digit")
        self.assertIsNotNone(at(17.935 + 0.01))
        self.assertIsNone(at(17.4, limit=1.0))
        self.assertIn("reaches more than 1 mm past J1's own box", (at(17.4, limit=0.5)
                                                                  or ("", ""))[0])

    def test_a_region_its_part_cannot_reach_excuses_nothing(self):
        error, _detail = self._refusal(
            {"floor": [[20.0, -17.0, -2.0], [30.0, 2.0, 0.5]]},
            {"floor": [[20.0, -17.0, -2.0], [30.0, 2.0, 0.0]]}) or ("", "")
        self.assertEqual(error, "J1 against floor: the region holds no material within 2 mm "
                                "of J1's own box (2 times the claim's limit), so it excuses "
                                "nothing. Remove it, or draw it where J1 touches")

    def test_regions_that_take_out_all_of_the_enclosure_are_refused(self):
        """Before the review of P7, such a part had no distance to the rest, and
        was left out of the verdict: judged against nothing."""
        halves = {"west": [[-50.0, -50.0, -50.0], [15.0, 50.0, 50.0]],
                  "east": [[15.0, -50.0, -50.0], [50.0, 50.0, 50.0]]}
        error, _detail = self._refusal(halves, {f: None for f in halves},
                                       clearance=None) or ("", "")
        self.assertIn("J1: its regions (east, west) take out all of the enclosure", error)
        error, _detail = self._refusal({"all": [[-50.0, -50.0, -50.0], [50.0, 50.0, 50.0]]},
                                       {"all": self.ENCLOSURE}, clearance=None) or ("", "")
        self.assertIn("J1 against all: its region holds the whole enclosure", error)

    def test_boards_share_an_enclosure_by_its_bytes(self):
        """The group is the enclosure file's bytes, not its path as written
        (review of P7: ./cad/enc.step was another enclosure than cad/enc.step)."""
        Fit, contacts = self.g["_Fit"], self.g["_contacts"]
        left = Fit("left", "l.kicad_pcb", "/p/cad/enc.step", {}, set(),
                   contacts({"board": {"right:board": {"reason": "the edges seat"}}}, "c"), {},
                   enclosure_id="a" * 64)
        right = Fit("right", "r.kicad_pcb", "/p/./cad/enc.step", {}, set(), {}, {},
                    enclosure_id="a" * 64)
        self.assertEqual(self.g["_board_contacts"]([left, right]),
                         {"left": {"board": {"right": ["board"]}},
                          "right": {"board": {"left": ["board"]}}})
        right.enclosure_id = "b" * 64
        with self.assertRaises(self.g["_FitError"]) as raised:
            self.g["_board_contacts"]([left, right])
        self.assertIn("right is in another enclosure", str(raised.exception))


class AFitRecordIsReadStrictly(unittest.TestCase):
    """Each refusal comes before any tool runs, so these need no KiCad: the gate
    function is called as the registry holds it."""

    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="nopekit-fit-records-")
        self.addCleanup(shutil.rmtree, self.out, True)
        self.fn = _registry().get("pcb.enclosure_fit")[1]

    def _verdict(self, claim=None, **params):
        return self.fn(_fit_ctx(self.out, dict(_baseline(), **params), claim))

    def test_the_old_limit_is_refused_naming_the_claim(self):
        """A limit is the claim's. Dropped silently, the old key would read as a
        number the project thinks was used."""
        v = self._verdict(pcb_enclosure_clearance_mm=1.0)
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("pcb_enclosure_clearance_mm is retired", v.error)
        self.assertIn("the claim tagged enclosure-fit", v.error)

    def test_a_claim_in_other_terms_is_refused(self):
        for claim in (_fit_claim(units="in"), _fit_claim(comparator="<=")):
            with self.subTest(claim=claim.acceptance):
                v = self._verdict(claim)
                self.assertEqual(v.outcome, "error", v.render())
                self.assertIn("(>=) in mm", v.error)

    def test_a_contact_names_a_feature_and_says_why(self):
        region = [[0, -17, 16], [8, 2, 19]]
        for name, contacts, said in (
                ("a ref alone", {"J1": {}}, "never a part against anything"),
                ("no reason", {"J1": {"lid": {"region_mm": region}}}, "has no reason"),
                ("no region", {"J1": {"lid": {"reason": "seats on it"}}}, "has no region_mm"),
                ("a misspelled key", {"J1": {"lid": {"reason": "x", "region": region}}},
                 "unknown key 'region' (did you mean 'region_mm'?)"),
                ("a box inside out", {"J1": {"lid": {"reason": "x",
                                                     "region_mm": [[8, 2, 19], [0, -17, 16]]}}},
                 "each low corner must be below its high one"),
                ("a part the board lacks", {"J9": {"lid": {"reason": "x", "region_mm": region}}},
                 "a contact names J9: the board has no such part"),
                ("another board, one fit", {"J1": {"lower:J2": {"reason": "mates"}}},
                 "needs pcb_enclosure_fits"),
                ("the body against the enclosure",
                 {"board": {"lid": {"reason": "x", "region_mm": region}}},
                 "the board body may already touch the enclosure")):
            with self.subTest(case=name):
                v = self._verdict(pcb_enclosure_contacts=contacts)
                self.assertEqual(v.outcome, "error", v.render())
                self.assertIn(said, v.error)

    def test_a_height_is_one_body_with_a_source(self):
        for name, heights, said in (
                ("a part with a model", {"U1": {"h": 1.0, "source": "datasheet"}},
                 "U1 names a 3D model"),
                ("no source", {"U1": {"h": 1.0, "source": " "}}, "source is empty"),
                ("no height", {"U1": {"h": 0, "source": "datasheet"}}, "a height is above 0 mm"),
                ("a flag", {"U1": {"h": True, "source": "datasheet"}}, "give a number")):
            with self.subTest(case=name):
                v = self._verdict(pcb_part_heights_mm=heights)
                self.assertEqual(v.outcome, "error", v.render())
                self.assertIn(said, v.error)

    def test_a_placement_states_where_and_turns_only_what_it_says(self):
        for name, place, said in (
                ("no z", {"x": 0, "y": 0}, "has no z"),
                ("the old flip word", {"x": 0, "y": 0, "z": 4, "flip": True},
                 "unknown key 'flip'"),
                ("a word for a number", {"x": 0, "y": 0, "z": "4"}, "give a number")):
            with self.subTest(case=name):
                v = self._verdict(pcb_board_placement=place)
                self.assertEqual(v.outcome, "error", v.render())
                self.assertIn(said, v.error)

    def test_each_fit_is_stated_once(self):
        fits = {"main": {"board": _baseline()["pcb_board"],
                         "enclosure_step": _baseline()["pcb_enclosure_step"],
                         "placement": {"x": 0, "y": 0, "z": 4}}}
        v = self._verdict(pcb_enclosure_fits=fits)
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("state each fit once", v.error)
        params = {k: val for k, val in _baseline().items()
                  if k not in ("pcb_enclosure_step", "pcb_board_placement",
                               "pcb_enclosure_bodiless")}
        for name, bad, said in (
                ("an id with a space", {"main board": fits["main"]}, "a fit's id is letters"),
                ("no placement", {"main": {k: x for k, x in fits["main"].items()
                                           if k != "placement"}}, "has no placement"),
                ("a list", [fits["main"]], "not an object keyed by fit id")):
            with self.subTest(case=name):
                v = self.fn(_fit_ctx(self.out, dict(params, pcb_enclosure_fits=bad)))
                self.assertEqual(v.outcome, "error", v.render())
                self.assertIn(said, v.error)

    def test_a_newer_board_is_skipped_in_the_guards_words(self):
        """Held to the reader's ceiling before any model is read (noted in the
        review of P4: this gate read a newer file's footprints on trust)."""
        root = tempfile.mkdtemp(prefix="nopekit-fit-newer-")
        self.addCleanup(shutil.rmtree, root, True)
        board = _board_copy(root, _version(_board_text(), "20260206"))
        v = self._verdict(pcb_board=board)
        self.assertEqual(v.outcome, "skipped", v.render())
        self.assertIn("this reader was checked through board format 20241229 (KiCad 9.0); "
                      "the file is format 20260206", v.skip_reason)
        self.assertIn("not a defect in the board", v.skip_reason)

    def test_a_part_with_neither_model_nor_height_still_skips_named(self):
        root = tempfile.mkdtemp(prefix="nopekit-fit-nomodel-")
        self.addCleanup(shutil.rmtree, root, True)
        v = self._verdict(pcb_board=_variant_board(root, _drop_model("U1")))
        self.assertEqual(v.outcome, "skipped", v.render())
        self.assertIn("1 placed part(s) name no 3D model: U1", v.skip_reason)
        self.assertIn("pcb_part_heights_mm", v.skip_reason)
        # Every choice is shown: a skip reason keeps 200 characters.
        self.assertTrue(v.skip_reason.endswith("list a part with no body in "
                                               "pcb_enclosure_bodiless"), v.skip_reason)


@unittest.skipUnless(FIT_TOOLS, FIT_SKIP)
class TheFitTakesItsLimitAndContactsFromTheRecords(unittest.TestCase):
    """The baseline board in the baseline enclosure, raised until its pins come
    near the lid: the claim's limit judges it, and a by-design contact excuses
    one part against one region of the enclosure, never against the rest.

    The baseline's pins are 2.365 mm under the lid at z 4. At z 5.8 they are
    0.565 mm under it; at z 7 they run 0.635 mm into it. At z 5.8, J1's box is
    x 2.73 to 5.27, y -12.89 to -2.73, z 4.395 to 15.935, and a region may take
    out material up to 2 mm past it (twice the claim's 1.0 mm): each lid region
    here is the lid over its header, to z 17.5 of the lid's 18.5."""

    LID_OVER_J1 = [[1.0, -14.5, 16.0], [7.0, -1.0, 17.5]]
    LID_OVER_J2 = [[23.0, -14.5, 16.0], [29.0, -1.0, 17.5]]
    FLOOR_UNDER_J2 = [[20.0, -17.0, -2.0], [30.0, 2.0, 0.5]]
    #: A rib hung from the lid beside J2's pins, and a region around it; the lid
    #: region over J2 for that enclosure starts at the lid's underside, so it
    #: holds none of the rib.
    RIB = [[26.9, -13.0, 12.0], [28.5, -3.0, 16.5]]
    RIB_BESIDE_J2 = [[26.5, -14.0, 11.0], [28.6, -2.0, 16.5]]
    LID_ONLY_OVER_J2 = [[23.0, -14.5, 16.5], [29.0, -1.0, 17.5]]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nopekit-fit-rib-")
        cls.ribbed = _enclosure_step(os.path.join(cls.tmp, "ribbed.step"), "shell",
                                     (-2.0, -17.0, 0.0), (32.0, 2.0, 16.5),
                                     add=[cls.RIB])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="nopekit-fit-contacts-")
        self.addCleanup(shutil.rmtree, self.out, True)
        self.registry = _registry()

    def _run(self, z: float = 4.0, contacts=None, claim=None, enclosure=None):
        params = dict(_baseline(), pcb_board_placement={"x": 0.0, "y": 0.0, "z": z})
        if contacts is not None:
            params["pcb_enclosure_contacts"] = contacts
        if enclosure is not None:
            params["pcb_enclosure_step"] = enclosure
        out = tempfile.mkdtemp(dir=self.out)
        return gates_mod.run_gate(*self.registry.get("pcb.enclosure_fit"),
                                  _fit_ctx(out, params, claim))

    def _contact(self, region, reason="seats against it by design"):
        return {"reason": reason, "region_mm": region}

    def test_the_limit_is_the_claims(self):
        """The same 2.365 mm against the claim at 2.0 and at 3.0: the value never
        moves with the limit, the verdict does."""
        at_2 = self._run(claim=_fit_claim(2.0))
        at_3 = self._run(claim=_fit_claim(3.0))
        self.assertEqual((at_2.outcome, at_2.measured, at_2.limit), ("pass", 2.365, 2.0),
                         at_2.render())
        self.assertEqual((at_3.outcome, at_3.measured, at_3.limit), ("fail", 2.365, 3.0),
                         at_3.render())
        self.assertIn("vs >= 3 mm (the claim tagged enclosure-fit)", at_3.detail)

    def test_a_contact_excuses_one_part_against_one_region_only(self):
        near = self._run(z=5.8)
        self.assertEqual((near.outcome, near.measured), ("fail", 0.565), near.render())
        one = self._run(z=5.8, contacts={"J1": {"lid_j1": self._contact(self.LID_OVER_J1)}})
        self.assertEqual((one.outcome, one.measured), ("fail", 0.565), one.render())
        self.assertIn("nearest J2 is 0.565 mm", one.detail)
        self.assertIn("declared contact(s): J1 to lid_j1 0.565 mm", one.detail)
        both = self._run(z=5.8, contacts={
            "J1": {"lid_j1": self._contact(self.LID_OVER_J1)},
            "J2": {"lid_j2": self._contact(self.LID_OVER_J2)}})
        # Each pin keeps its gap from the rest of the lid: 1.565 mm to the lid
        # above each region's top, z 17.5.
        self.assertEqual((both.outcome, both.measured), ("pass", 1.565), both.render())
        self.assertIn("J2 to lid_j2 0.565 mm", both.detail)

    def test_a_contact_never_excuses_its_part_against_another_feature(self):
        """J2 near two features: the lid over its pins and a rib beside them.
        A contact is (ref, feature), never a ref alone: excused at one, the
        other judges it."""
        j1 = {"lid_j1": self._contact(self.LID_OVER_J1)}
        lid = self._run(z=5.8, enclosure=self.ribbed, contacts={
            "J1": j1, "J2": {"lid_j2": self._contact(self.LID_ONLY_OVER_J2)}})
        # The rib judges it: 0.58 mm from the pins' faces (x 26.32) to its own (26.9).
        self.assertEqual((lid.outcome, lid.measured), ("fail", 0.58), lid.render())
        self.assertIn("nearest J2 is 0.58 mm from the enclosure", lid.detail)
        rib = self._run(z=5.8, enclosure=self.ribbed, contacts={
            "J1": j1, "J2": {"rib": self._contact(self.RIB_BESIDE_J2)}})
        self.assertEqual((rib.outcome, rib.measured), ("fail", 0.565), rib.render())
        self.assertIn("nearest J2 is 0.565 mm from the enclosure", rib.detail)
        both = self._run(z=5.8, enclosure=self.ribbed, contacts={
            "J1": j1, "J2": {"lid_j2": self._contact(self.LID_ONLY_OVER_J2),
                             "rib": self._contact(self.RIB_BESIDE_J2)}})
        self.assertEqual((both.outcome, both.measured), ("pass", 1.565), both.render())
        self.assertIn("J2 to lid_j2 0.565 mm, J2 to rib 0.58 mm", both.detail)

    def test_a_contact_never_excuses_a_collision(self):
        v = self._run(z=7.0, contacts={
            "J1": {"lid_j1": self._contact(self.LID_OVER_J1)},
            "J2": {"lid_j2": self._contact(self.LID_OVER_J2)}})
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("2 collision(s)", v.detail)
        self.assertIn("J1 shares 0.931 mm^3 with the enclosure", v.detail)

    def test_a_region_that_holds_the_whole_enclosure_is_refused(self):
        v = self._run(z=5.8, contacts={"J2": {"everything": self._contact(
            [[-50.0, -50.0, -50.0], [50.0, 50.0, 50.0]])}})
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("its region holds the whole enclosure", v.error)

    def test_a_region_its_part_cannot_reach_is_refused(self):
        """In the review of P7, the enclosure's whole box but its top 0.1 mm
        excused each header against the walls and the floor as well as the lid,
        and read pass at 2.465 mm against the 0.1 mm skin it left. And J2
        excused against the floor, 4.395 mm under its pin tails, excused it
        against nothing it comes near."""
        region = [[-4.0, -19.0, -2.0], [34.0, 4.0, 18.4]]
        v = self._run(z=5.8, contacts={"J1": {"lid": self._contact(region)},
                                       "J2": {"lid": self._contact(region)}})
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("J1 against lid: the region reaches more than 2 mm past J1's own box",
                      v.error)
        self.assertIn("Draw it within [[0.73, -14.89, 2.395], [7.27, -0.73, 17.935]]",
                      v.error)
        self.assertIn("the region takes out enclosure material from (-4, -19, -2) to "
                      "(34, 4, 18.4) mm", v.detail)
        floor = self._run(z=5.8, contacts={
            "J1": {"lid_j1": self._contact(self.LID_OVER_J1)},
            "J2": {"floor_j2": self._contact(self.FLOOR_UNDER_J2)}})
        self.assertEqual(floor.outcome, "error", floor.render())
        self.assertIn("J2 against floor_j2: the region holds no material within 2 mm of "
                      "J2's own box", floor.error)


@unittest.skipUnless(FIT_TOOLS, FIT_SKIP)
class BoardsSitInAnyOrientationAndBesideEachOther(unittest.TestCase):
    """Boards placed in a roomy box (cavity x -10..80, y -30..10, z 0..30): face
    down, on an edge, and two side by side, where each board is held to the
    claim against the other as against the enclosure."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nopekit-fit-boards-")
        cls.box = _enclosure_step(os.path.join(cls.tmp, "box.step"), "shell",
                                  (-10.0, -30.0, 0.0), (80.0, 10.0, 30.0))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def setUp(self):
        self.registry = _registry()

    def _run(self, params: dict):
        out = tempfile.mkdtemp(dir=self.tmp)
        base = {k: v for k, v in _baseline().items()
                if k not in ("pcb_enclosure_step", "pcb_board_placement",
                             "pcb_enclosure_bodiless")}
        return gates_mod.run_gate(*self.registry.get("pcb.enclosure_fit"),
                                  _fit_ctx(out, dict(base, **params)))

    def _one(self, place: dict):
        return self._run({"pcb_enclosure_step": self.box, "pcb_board_placement": place})

    def test_face_down(self):
        """rot_x 180 reverses y and z: the pins (z -1.405..10.135 on the board)
        hang down, their tips 2.0 mm above the floor at z 12.135."""
        v = self._one({"x": 10.0, "y": -20.0, "z": 12.135, "rot_x_deg": 180.0})
        self.assertEqual((v.outcome, v.measured), ("pass", 2.0), v.render())
        self.assertIn("is 2 mm from the enclosure", v.detail)
        self.assertIn("turned x 180 deg", v.detail)

    def test_on_an_edge_turned_about_x_then_z(self):
        """rot_x 90 then rot_z 90: the pins point +x, their tips 3.0 mm from the
        +x wall. Turned about z first, they would point -y, nowhere near it."""
        v = self._one({"x": 66.865, "y": -25.0, "z": 20.0, "rot_x_deg": 90.0,
                       "rot_z_deg": 90.0})
        self.assertEqual((v.outcome, v.measured), ("pass", 3.0), v.render())

    def _two(self, gap: float, contacts=None, right_enclosure=None):
        def fit(x):
            return {"board": _baseline()["pcb_board"], "enclosure_step": self.box,
                    "placement": {"x": x, "y": 0.0, "z": 4.0}}
        fits = {"left": fit(0.0), "right": fit(30.0 + gap)}
        if contacts:
            fits["left"]["contacts"] = contacts
        if right_enclosure is not None:
            fits["right"]["enclosure_step"] = right_enclosure
        return self._run({"pcb_enclosure_fits": fits})

    def test_two_boards_are_held_to_the_claim_against_each_other(self):
        """Side by side, the bodies ``gap`` apart: the nearest pair of shapes
        is the two bodies (each board's pins sit 2.73 mm in from its edge), and
        against the enclosure the pin tails keep 2.595 mm from the floor."""
        apart = self._two(2.0)
        self.assertEqual((apart.outcome, apart.measured), ("pass", 2.0), apart.render())
        self.assertIn("2 board(s)", apart.detail)
        close = self._two(0.5)
        self.assertEqual((close.outcome, close.measured), ("fail", 0.5), close.render())
        self.assertIn("nearest left:board is 0.5 mm from board right", close.detail)
        touching = self._two(-0.5)
        self.assertEqual(touching.outcome, "fail", touching.render())
        self.assertIn("left:board shares 11.3 mm^3 with board right (right:board 11.3 mm^3)",
                      touching.detail)

    def test_one_enclosure_however_its_path_is_spelled(self):
        """In the review of P7, the group was the path as written: the right
        board's enclosure spelled <tmp>/./box.step was another enclosure, each
        board was fitted against the box alone, and two boards that share
        11.3 mm^3 read pass at 2.595 mm. The group is now the file's bytes."""
        link = os.path.join(self.tmp, "link.step")
        if not os.path.lexists(link):
            os.symlink(self.box, link)
        copy = os.path.join(self.tmp, "copy.step")
        shutil.copyfile(self.box, copy)
        for name, spelling in (("./", os.path.join(self.tmp, ".", "box.step")),
                               ("from the project root", os.path.relpath(self.box, PACK)),
                               ("a link", link), ("a copy", copy)):
            with self.subTest(spelling=name):
                v = self._two(-0.5, right_enclosure=spelling)
                self.assertEqual(v.outcome, "fail", v.render())
                self.assertIn("left:board shares 11.3 mm^3 with board right", v.detail)
        excused = self._two(0.5, {"board": {"right:board": {"reason": "the edges seat "
                                                                      "against each other"}}},
                            right_enclosure=link)
        self.assertEqual((excused.outcome, excused.measured), ("pass", 2.595),
                         excused.render())
        # Another file is another enclosure, and the verdict says so.
        other = _enclosure_step(os.path.join(self.tmp, "other.step"), "shell",
                                (-10.0, -30.0, 0.0), (80.0, 10.0, 30.5))
        apart = self._two(-0.5, right_enclosure=other)
        self.assertEqual((apart.outcome, apart.measured), ("pass", 2.595), apart.render())
        self.assertIn("2 board(s) in 2 enclosure files, each board fitted against the "
                      "others in its own", apart.detail)

    def test_a_board_with_a_part_missing_is_judged_on_what_arrived(self):
        """In the review of P7, a pair of boards was measured under the first
        only, and a board whose part kicad-cli left out was dropped with its
        pair rows: with it first the collision read skip, with it second fail.
        A shape that arrived can only come nearer when a missing one is added,
        so its fail counts; its pass does not."""
        root = tempfile.mkdtemp(dir=self.tmp)
        lost = _variant_board(root, _lose_model("U1"))

        def fits(order, gap):
            recs = {"whole": {"board": _baseline()["pcb_board"], "enclosure_step": self.box,
                              "placement": {"x": 0.0, "y": 0.0, "z": 4.0}},
                    "broken": {"board": lost, "enclosure_step": self.box,
                               "placement": {"x": 30.0 + gap, "y": 0.0, "z": 4.0}}}
            return {"pcb_enclosure_fits": {k: recs[k] for k in order}}

        for order in (("broken", "whole"), ("whole", "broken")):
            with self.subTest(first=order[0]):
                v = self._run(fits(order, -0.5))
                self.assertEqual(v.outcome, "fail", v.render())
                self.assertIn("board shares 11.3 mm^3 with board", v.detail)
                self.assertIn("not judged: fit broken: kicad-cli left out U1: it did not "
                              "find their 3D models", v.detail)
        apart = self._run(fits(("broken", "whole"), 2.0))
        self.assertEqual(apart.outcome, "skipped", apart.render())
        self.assertIn("fit broken: kicad-cli left out U1", apart.skip_reason)
        # The advice is one the gate accepts, and is shown: a part that names a
        # model and a declared height is refused, and a skip reason keeps 200
        # characters (review of P7).
        self.assertTrue(apart.skip_reason.endswith(
            "or hide it and state a height in pcb_part_heights_mm"), apart.skip_reason)

    def test_a_contact_between_boards_is_one_shape_against_one_shape(self):
        excused = self._two(0.5, {"board": {"right:board": {"reason": "the edges seat "
                                                                      "against each other"}}})
        self.assertEqual((excused.outcome, excused.measured), ("pass", 2.595),
                         excused.render())
        self.assertIn("left:board to right:board 0.5 mm", excused.detail)
        # Excused against the other board's J1 only: its body still judges it.
        scoped = self._two(0.5, {"board": {"right:J1": {"reason": "a pin rests on it"}}})
        self.assertEqual((scoped.outcome, scoped.measured), ("fail", 0.5), scoped.render())
        collided = self._two(-0.5, {"board": {"right:board": {"reason": "seated"}}})
        self.assertEqual(collided.outcome, "fail", collided.render())
        self.assertIn("collision(s)", collided.detail)


@unittest.skipUnless(FIT_TOOLS, FIT_SKIP)
class APartWithNoModelIsFittedAtItsDeclaredHeight(unittest.TestCase):
    """U1 with its model removed, under a block 0.8 mm above where a 1.55 mm
    body would reach (its model's own height). The declared height decides the
    gap: no height skips, 1.55 mm fails at 0.8 mm, 1.0 mm passes at 1.35 mm."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nopekit-fit-heights-")
        # KiCad 9.0.9 seats a model at z 1.595 on this 1.6 mm board (measured).
        top = 1.595 + 1.55 + 0.8
        cls.block = _enclosure_step(os.path.join(cls.tmp, "block.step"), "block",
                                    (13.0, -9.5, top), (17.0, -6.2, top + 2.0))
        cls.board = _variant_board(cls.tmp, _drop_model("U1"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def _run(self, heights=None):
        params = dict(_baseline(), pcb_board=self.board, pcb_enclosure_step=self.block,
                      pcb_board_placement={"x": 0.0, "y": 0.0, "z": 0.0})
        if heights is not None:
            params["pcb_part_heights_mm"] = heights
        return gates_mod.run_gate(*_registry().get("pcb.enclosure_fit"),
                                  _fit_ctx(tempfile.mkdtemp(dir=self.tmp), params))

    def test_the_height_decides_the_gap(self):
        none = self._run()
        self.assertEqual(none.outcome, "skipped", none.render())
        self.assertIn("name no 3D model: U1", none.skip_reason)
        tall = self._run({"U1": {"h": 1.55, "source": "the part's seated height"}})
        self.assertEqual((tall.outcome, tall.measured), ("fail", 0.8), tall.render())
        self.assertIn("nearest U1 is 0.8 mm", tall.detail)
        low = self._run({"U1": {"h": 1.0, "source": "the part's seated height"}})
        self.assertEqual((low.outcome, low.measured), ("pass", 1.35), low.render())



# --------------------------------------------------------------------------- #
# general-packs P8: the antenna's keep-out, on every copper layer
# --------------------------------------------------------------------------- #
RADIO_BOARD = os.path.join(ASSETS, "radio_board.kicad_pcb")
KEEPOUT_TOOLS = (shutil.which("kicad-cli") is not None
                 and importlib.util.find_spec("pcbnew") is not None)
KEEPOUT_SKIP = ("needs kicad-cli and pcbnew: the gate refills the board's zones with "
                "pcbnew and judges each copy with kicad-cli")
PCBNEW_SKIP = "needs pcbnew: the test board is built with KiCad's own placement"

#: KiCad 9.0.9's ESP32-S3-WROOM-1 keep-out, in its library frame (y down):
#: ``RF_Module.pretty/ESP32-S3-WROOM-1.kicad_mod``'s rule area, as the library
#: writes it. A test reads the library itself where it is installed.
S3_KEEPOUT_LOCAL = [(-24.0, -6.75), (24.0, -6.75), (24.0, -27.75), (-24.0, -27.75)]

#: Builds a test board with KiCad's own pcbnew, in a child as the asset scripts
#: run: parts placed, turned and flipped by KiCad (so each footprint's rule area
#: is saved where KiCad puts it), pours, tracks and named rule areas (with any
#: cutouts, which KiCad saves as further polygons of the area), then filled.
_BUILD_SCRIPT = r"""
import json, os, sys
import pcbnew

def mm(v): return pcbnew.FromMM(v)
def xy(p): return pcbnew.VECTOR2I(mm(p[0]), mm(p[1]))

spec = json.load(open(sys.argv[1]))
out = spec["out"]
b = pcbnew.BOARD()
b.SetCopperLayerCount(spec.get("layers", 4))
nets = {}
def net(name):
    if name not in nets:
        n = pcbnew.NETINFO_ITEM(b, name); b.Add(n); nets[name] = n
    return nets[name]
for p in spec.get("parts", []):
    fp = pcbnew.FootprintLoad(os.path.join(spec["footprints"], p["lib"] + ".pretty"), p["name"])
    fp.SetFPID(pcbnew.LIB_ID(p["lib"], p["name"]))
    fp.SetReference(p["ref"]); fp.SetValue(p["value"])
    fp.SetPosition(xy(p["at"])); fp.SetOrientationDegrees(p.get("rot", 0.0))
    for pad in fp.Pads():
        if pad.GetNumber() in p.get("nets", {}):
            pad.SetNet(net(p["nets"][pad.GetNumber()]))
    b.Add(fp)
    if p.get("back"):
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
w, h = spec["outline"]
corners = [(0, 0), (w, 0), (w, h), (0, h)]
for a, c in zip(corners, corners[1:] + corners[:1]):
    e = pcbnew.PCB_SHAPE(b); e.SetShape(pcbnew.SHAPE_T_SEGMENT)
    e.SetStart(xy(a)); e.SetEnd(xy(c)); e.SetLayer(pcbnew.Edge_Cuts); e.SetWidth(mm(0.1)); b.Add(e)
for t in spec.get("tracks", []):
    s = pcbnew.PCB_TRACK(b); s.SetStart(xy(t["pts"][0])); s.SetEnd(xy(t["pts"][1]))
    s.SetWidth(mm(0.25)); s.SetLayer(b.GetLayerID(t["layer"])); s.SetNet(net(t["net"]))
    b.Add(s)
for z in spec.get("pours", []):
    zone = pcbnew.ZONE(b); zone.SetLayer(b.GetLayerID(z["layer"])); zone.SetNet(net(z["net"]))
    o = zone.Outline(); o.NewOutline()
    for p in z["pts"]: o.Append(mm(p[0]), mm(p[1]))
    b.Add(zone)
for a in spec.get("areas", []):
    zone = pcbnew.ZONE(b); zone.SetIsRuleArea(True)
    ls = pcbnew.LSET()
    for name in a["layers"]: ls.AddLayer(b.GetLayerID(name))
    zone.SetLayerSet(ls); zone.SetZoneName(a["name"])
    zone.SetDoNotAllowTracks(True); zone.SetDoNotAllowVias(True); zone.SetDoNotAllowPads(True)
    zone.SetDoNotAllowCopperPour(True); zone.SetDoNotAllowFootprints(False)
    o = zone.Outline(); o.NewOutline()
    for p in a["pts"]: o.Append(mm(p[0]), mm(p[1]))
    for i, hole in enumerate(a.get("holes", [])):
        o.NewHole()
        for p in hole: o.Append(mm(p[0]), mm(p[1]), -1, i)
    b.Add(zone)
pcbnew.SaveBoard(out, b)
b = pcbnew.LoadBoard(out)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
pcbnew.SaveBoard(out, b)
for ext in (".kicad_pro", ".kicad_prl"):
    side = out[:-len(".kicad_pcb")] + ext
    if os.path.exists(side): os.remove(side)
"""


def _build_board(root: str, name: str, spec: dict) -> str:
    out = os.path.join(root, name + ".kicad_pcb")
    script = os.path.join(root, "build_board.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(_BUILD_SCRIPT)
    path = os.path.join(root, name + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dict(spec, out=out, footprints=FOOTPRINTS), fh)
    subprocess.run([sys.executable, script, path], check=True, capture_output=True,
                   timeout=120, cwd=root)
    return out


def _keepout_gate():
    return _registry().get("pcb.antenna_keepout")


def _keepout_ctx(root: str, params: dict) -> gates_mod.GateContext:
    out = tempfile.mkdtemp(prefix="out-", dir=root)
    return gates_mod.GateContext(
        root=root, ledger=Ledger(meta=ProjectMeta(name="radio")), model=None,
        params=dict(params), out_dir=out, tier=1, log=lambda _m: None, extra={})


def _radio_record(board: str, **over) -> dict:
    rec = {"board": board, "ref": "U1", "value": "ESP32-S3-WROOM-1", "keepout": "footprint",
           "source": "the stock footprint's rule area"}
    rec.update(over)
    return {"pcb_antennas": {"radio": rec}}


def _radio_copy(root: str, edit=None, stem: str = "radio_board") -> str:
    """The baseline's radio board with ``edit(tree)`` applied, written under
    ``root`` (in a directory of its own, so each copy may take its own project
    file)."""
    tree = K.load_tree(RADIO_BOARD)
    if edit is not None:
        edit(tree)
    where = tempfile.mkdtemp(prefix="board-", dir=root)
    path = os.path.join(where, stem + ".kicad_pcb")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(K.dump(tree) + "\n")
    return path


def _keepout_evidence(v) -> dict:
    with open(next(e for e in v.evidence if e.endswith("antenna_keepout.json")),
              encoding="utf-8") as fh:
        return json.load(fh)


def _uid(name: str) -> K.Q:
    import uuid
    return K.Q(str(uuid.uuid5(uuid.NAMESPACE_URL, f"test:{name}")))


def _track(layer: str, a, b, net: str = "GND"):
    def edit(tree):
        tree.append(["segment", ["start", f"{a[0]:g}", f"{a[1]:g}"],
                     ["end", f"{b[0]:g}", f"{b[1]:g}"], ["width", "0.25"],
                     ["layer", K.Q(layer)], ["net", _net_code(tree, net)],
                     ["uuid", _uid(f"{layer}{a}{b}")]])
    return edit


def _via(at, net: str = "GND"):
    def edit(tree):
        tree.append(["via", ["at", f"{at[0]:g}", f"{at[1]:g}"], ["size", "0.6"], ["drill", "0.3"],
                     ["layers", K.Q("F.Cu"), K.Q("B.Cu")], ["net", _net_code(tree, net)],
                     ["uuid", _uid(f"via{at}")]])
    return edit


def _moved(ref: str, x: float, y: float):
    def edit(tree):
        at = K.child(_footprint(tree, ref), "at")
        at[1], at[2] = f"{x:g}", f"{y:g}"
    return edit


def _no_footprint_keepout(ref: str):
    def edit(tree):
        fp = _footprint(tree, ref)
        fp[:] = [c for c in fp if not (isinstance(c, list) and K.head(c) == "zone")]
    return edit


def _both(*edits):
    def edit(tree):
        for e in edits:
            e(tree)
    return edit


class TheKeepoutIsJudgedOnEveryLayer(unittest.TestCase):
    """KiCad's own DRC, on the radio board as stored and as refilled, with the
    footprint's keep-out copied to every copper layer. What slipped through:
    ``pcb.drc`` holds the stock ESP32-S3-WROOM-1's keep-out on F.Cu, In1.Cu and
    B.Cu only, and judges a pour by its stored fill."""

    @classmethod
    def setUpClass(cls):
        if not KEEPOUT_TOOLS:
            raise unittest.SkipTest(KEEPOUT_SKIP)
        cls.root = tempfile.mkdtemp(prefix="nopekit-keepout-")
        cls.spec, fn = _keepout_gate()
        cls.fn = staticmethod(fn)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, True)

    def run_on(self, board: str, **over):
        return gates_mod.run_gate(self.spec, self.fn,
                                  _keepout_ctx(self.root, _radio_record(board, **over)))

    def test_the_baseline_passes_and_its_footprint_area_names_three_layers(self):
        tree = K.load_tree(RADIO_BOARD)
        zone = K.children(_footprint(tree, "U1"), "zone")[0]
        self.assertEqual(sorted(str(x) for x in K.child(zone, "layers")[1:]),
                         ["B.Cu", "F.Cu", "In1.Cu"], "the stock area leaves In2.Cu out")
        v = self.run_on(RADIO_BOARD)
        self.assertEqual((v.outcome, v.measured, v.limit, v.units), ("pass", 0, 0, "findings"),
                         v.render())
        self.assertIn("U1 (footprint area) on radio_board.kicad_pcb, 4 copper layers", v.detail)
        ev = _keepout_evidence(v)
        self.assertEqual(ev["copper_layers"]["radio_board.kicad_pcb"],
                         ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"])
        self.assertEqual(ev["findings"], [])

    def test_a_pass_names_what_was_judged_and_never_says_all_copper(self):
        """KiCad's keep-out does not report a copper drawing: a filled polygon on
        In2.Cu, on GND, under the antenna (pcbnew reads it as copper on its net)
        passes. So the pass and the title name what KiCad reports, and say the
        drawings are not judged. What slipped through (review of P8): over this
        board the pass read "nothing on any copper layer in an antenna's
        keep-out", and the title "No copper on any layer"."""
        def drawn(tree):
            tree.append(["gr_poly", ["pts", ["xy", "1", "8"], ["xy", "5", "8"], ["xy", "5", "22"],
                                     ["xy", "1", "22"]],
                         ["stroke", ["width", "0.1"], ["type", "solid"]], ["fill", "yes"],
                         ["layer", K.Q("In2.Cu")], ["net", _net_code(tree, "GND")],
                         ["uuid", _uid("drawn ground")]])
        v = self.run_on(_radio_copy(self.root, drawn))
        self.assertEqual((v.outcome, v.measured), ("pass", 0), v.render())
        self.assertTrue(v.detail.startswith(
            "no track, via, other part's pad or zone fill on any copper layer in an antenna's "
            "keep-out, as stored and as refilled (copper drawings and text not judged): "), v.detail)
        for words in (v.detail, self.spec.title):
            self.assertNotIn("nothing on any copper layer", words.lower())
            self.assertNotIn("no copper", words.lower())
        self.assertTrue(self.spec.title.startswith(
            "No track, via, other part's pad or zone fill on any copper layer"), self.spec.title)

    def test_plain_drc_sees_nothing_in_the_keepout_of_the_known_bad(self):
        """Nothing cheaper catches it: kicad-cli's own DRC on the known-bad board
        reports what it reports on the baseline, item kind for item kind, and no
        item in a keep-out."""
        bad = gates_mod.run_fixture(self.spec, self.fn,
                                    packs_mod.baseline_context(PACK, out_dir=self.root),
                                    trace=None, out_dir=tempfile.mkdtemp(dir=self.root))
        board = bad.params["pcb_antennas"]["radio"]["board"]
        kinds = []
        for path in (RADIO_BOARD, board):
            report = os.path.join(self.root, "plain.json")
            subprocess.run(["kicad-cli", "pcb", "drc", "--severity-all", "--format", "json",
                            "-o", report, path], capture_output=True, timeout=120,
                           cwd=self.root, check=True)
            with open(report, encoding="utf-8") as fh:
                kinds.append(sorted(v["type"] for v in json.load(fh)["violations"]))
        self.assertEqual(kinds[0], kinds[1])
        self.assertNotIn("items_not_allowed", kinds[1])
        v = gates_mod.run_gate(self.spec, self.fn, bad)
        self.assertEqual((v.outcome, v.measured), ("fail", 1), v.render())
        [found] = _keepout_evidence(v)["findings"]
        self.assertEqual((found["ref"], found["copies"]), ("U1", ["refilled"]))
        self.assertTrue(found["item"].startswith("Zone [GND] on In2.Cu"), found)
        self.assertIn("refilled only; the stored fill holds none", v.detail)

    def test_each_kind_of_copper_is_found_on_any_layer(self):
        board = _radio_copy(self.root, _both(
            _track("In2.Cu", (1.0, 10.0), (5.0, 10.0)),
            _track("B.Cu", (1.0, 12.0), (5.0, 12.0)),
            _via((3.0, 20.0)),
            _moved("C1", 3.0, 26.0)))
        v = self.run_on(board)
        self.assertEqual((v.outcome, v.measured), ("fail", 5), v.render())
        found = _keepout_evidence(v)["findings"]
        words = sorted(f["item"].split(",")[0] for f in found)
        self.assertEqual(words, ["Pad 1 [3V3] of C1 on F.Cu", "Pad 2 [GND] of C1 on F.Cu",
                                 "Track [GND] on B.Cu", "Track [GND] on In2.Cu",
                                 "Via [GND] on F.Cu - B.Cu"])
        self.assertTrue(all(f["copies"] == ["stored", "refilled"] for f in found), found)

    def test_a_stored_fill_the_refill_would_take_out_still_counts(self):
        """The fab is sent the file's fill (kicad-cli 9 plots it as stored), so
        copper the file holds in the keep-out counts, refill or not, even outside
        its zone's outline, where KiCad's own DRC does not look."""
        def stale(tree):
            zone = next(z for z in K.children(tree, "zone")
                        if str(K.value(z, "layer", default="")) == "In2.Cu")
            zone.append(["filled_polygon", ["layer", K.Q("In2.Cu")],
                         ["pts", ["xy", "1", "10"], ["xy", "4", "10"], ["xy", "4", "12"],
                          ["xy", "1", "12"]]])
        v = self.run_on(_radio_copy(self.root, stale))
        self.assertEqual((v.outcome, v.measured), ("fail", 1), v.render())
        [found] = _keepout_evidence(v)["findings"]
        self.assertEqual(found["copies"], ["stored"])
        self.assertIn("stored only; a refill takes it out", v.detail)

    def test_the_project_cannot_hide_a_finding(self):
        """A project file that ignores ``items_not_allowed`` is set back on the
        staged copy, and KiCad does honour the project's word: the same staged
        board under the project's own file reports nothing."""
        board = _radio_copy(self.root, _track("In2.Cu", (1.0, 10.0), (5.0, 10.0)))
        with open(os.path.join(ASSETS, "esd_guard.kicad_pro"), encoding="utf-8") as fh:
            project = json.load(fh)
        project["board"]["design_settings"]["rule_severities"]["items_not_allowed"] = "ignore"
        pro = board[:-len(".kicad_pcb")] + ".kicad_pro"
        with open(pro, "w", encoding="utf-8") as fh:
            json.dump(project, fh)
        v = self.run_on(board)
        self.assertEqual((v.outcome, v.measured), ("fail", 1), v.render())
        stored = next(os.path.dirname(e) for e in v.evidence
                      if e.endswith(os.path.join("stored", "drc.json")))
        shutil.copyfile(pro, os.path.join(stored, "radio_board.kicad_pro"))
        report = os.path.join(stored, "hidden.json")
        subprocess.run(["kicad-cli", "pcb", "drc", "--severity-all", "--format", "json",
                        "-o", report, os.path.join(stored, "radio_board.kicad_pcb")],
                       capture_output=True, timeout=120, cwd=stored, check=True)
        with open(report, encoding="utf-8") as fh:
            types = {x["type"] for x in json.load(fh)["violations"]}
        self.assertNotIn("items_not_allowed", types, "the override is what finds it")

    def test_one_board_named_twice_is_judged_once(self):
        board = _radio_copy(self.root, _track("In2.Cu", (1.0, 10.0), (5.0, 10.0)))
        # The same file spelled two ways: relative with a "." in it, and absolute.
        spelled = os.path.join(os.path.relpath(os.path.dirname(board), self.root), ".",
                               os.path.basename(board))
        params = dict(_radio_record(board), pcb_board=spelled)
        v = gates_mod.run_gate(self.spec, self.fn, _keepout_ctx(self.root, params))
        self.assertEqual((v.outcome, v.measured), ("fail", 1), v.render())

    def test_a_fail_stands_beside_a_board_kicad_could_not_judge(self):
        """Two boards: KiCad finds a track on one and times out (or crashes) on
        the other. The fail stands, and the detail names the board not judged."""
        bad = _radio_copy(self.root, _track("In2.Cu", (1.0, 10.0), (5.0, 10.0)), stem="a")
        other = _radio_copy(self.root, stem="b")
        real = self.fn.__globals__["_antenna_runs"]
        Verdict = self.fn.__globals__["Verdict"]
        for said, stuck in (
                ("timed out", Verdict(gate=self.spec.id, passed=False, skipped=True,
                                      skip_reason="timed out")),
                ("crashed", Verdict(gate=self.spec.id, passed=False, error="crashed"))):
            def runs(ctx, gid, n, path, *rest, stuck=stuck):
                return stuck if path.endswith("b.kicad_pcb") else real(ctx, gid, n, path, *rest)
            params = {"pcb_antennas": {
                "a": dict(_radio_record(bad)["pcb_antennas"]["radio"]),
                "b": dict(_radio_record(other)["pcb_antennas"]["radio"])}}
            with self.subTest(other=said), \
                    mock.patch.dict(self.fn.__globals__, {"_antenna_runs": runs}):
                v = gates_mod.run_gate(self.spec, self.fn, _keepout_ctx(self.root, params))
                self.assertEqual((v.outcome, v.measured), ("fail", 1), v.render())
                self.assertIn(said, v.detail)

    def test_a_module_with_no_keepout_skips_by_name(self):
        bare = _radio_copy(self.root, _no_footprint_keepout("U1"))
        v = gates_mod.run_gate(self.spec, self.fn, _keepout_ctx(self.root, {"pcb_board": bare}))
        self.assertEqual(v.outcome, "skipped", v.render())
        self.assertIn("no antenna keep-out for U1 (RF_Module:ESP32-S3-WROOM-1) on "
                      "radio_board.kicad_pcb: the footprint has none", v.skip_reason)
        self.assertIn("(or \"none\", for an antenna off the board)", v.skip_reason)
        # Named "none", it is listed, and a board with nothing else skips.
        v = self.run_on(bare, keepout="none", source="a wire antenna on a pad")
        self.assertEqual(v.outcome, "skipped", v.render())
        self.assertIn("listed, not judged: U1 (no antenna on the board)", v.skip_reason)
        # A record that says "footprint" of a footprint with no area is false.
        v = self.run_on(bare)
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("carries no keep-out rule area", v.error)


class TheKeepoutRecordsAreReadStrictly(unittest.TestCase):
    """A record is held to its board's part, and to its own keys; none of this
    needs KiCad: each refusal comes before the board is staged."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nopekit-keepout-rec-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.spec, self.fn = _keepout_gate()

    def call(self, params: dict):
        return self.fn(_keepout_ctx(self.root, params))

    def test_a_record_for_another_design_is_an_error(self):
        v = self.call(_radio_record(RADIO_BOARD, value="ESP32-C3-MINI-1"))
        self.assertIn("is for another design: radio_board.kicad_pcb's U1 is ESP32-S3-WROOM-1, "
                      "the record's ESP32-C3-MINI-1", v.error)
        v = self.call(_radio_record(RADIO_BOARD, ref="U9"))
        self.assertIn("is for another design: radio_board.kicad_pcb has no U9", v.error)

    def test_an_unknown_key_or_form_is_refused(self):
        v = self.call(_radio_record(RADIO_BOARD, keepout_layers="all"))
        self.assertIn("unknown key 'keepout_layers'", v.error)
        v = self.call(_radio_record(RADIO_BOARD, keepout="all"))
        self.assertIn("give \"footprint\", \"none\", {\"drawing\": <path>} or "
                      "{\"rule_area\": <name>}", v.error)
        v = self.call(_radio_record(RADIO_BOARD, keepout={"drawing": "a.json",
                                                          "rule_area": "x"}))
        self.assertIn("give one", v.error)
        v = self.call({"pcb_antennas": [{"ref": "U1"}]})
        self.assertIn("is a list", v.error)
        v = self.call(_radio_record(RADIO_BOARD, source=" "))
        self.assertIn("source is empty", v.error)

    def test_a_rule_area_must_be_on_the_board(self):
        v = self.call(_radio_record(RADIO_BOARD, keepout={"rule_area": "AE1 clearance"}))
        self.assertIn("radio_board.kicad_pcb has no rule area named 'AE1 clearance'", v.error)

    def test_a_newer_board_skips_with_the_guards_words(self):
        board = _radio_copy(self.root, lambda t: K.child(t, "version").__setitem__(1, "20260206"))
        v = self.call(_radio_record(board))
        self.assertTrue(v.skipped, v)
        self.assertIn("this reader was checked through board format 20241229 (KiCad 9.0); the "
                      "file is format 20260206", v.skip_reason)

    def test_no_board_and_no_record_skips(self):
        v = self.call({})
        self.assertTrue(v.skipped, v)
        self.assertIn("no pcb_board and no pcb_antennas", v.skip_reason)
        v = self.call({"pcb_board": BOARD})
        self.assertTrue(v.skipped, v)
        self.assertIn("no radio module on esd_guard.kicad_pcb", v.skip_reason)


def _s3_drawing(turn_deg: float = 30.0, shift=(5.0, -3.0), y_axis: str = "up",
                pads=("1", "14", "27", "40"), mirror: bool = False) -> dict:
    """The ESP32-S3-WROOM-1's pads and keep-out as a vendor's figure would give
    them: KiCad's own land and the library's keep-out, carried into another
    frame (turned, moved, y up as printed). ``mirror`` reads its y the wrong
    way, as a drawing transcribed with the wrong ``y_axis`` does."""
    land, _ = K.footprint_land(K.load_tree(RADIO_BOARD), "U1")
    by = {p.number: p for p in land}
    c, s = math.cos(math.radians(turn_deg)), math.sin(math.radians(turn_deg))

    def frame(x, y):
        x, y = x * c - y * s + shift[0], x * s + y * c + shift[1]
        flip = (y_axis == "up") != mirror
        return round(x, 4), round(-y if flip else y, 4)
    out = []
    for n in pads:
        x, y = frame(by[n].x, by[n].y)
        out.append({"number": n, "function": f"P{n}", "x": x, "y": y, "w": by[n].w,
                    "h": by[n].h})
    return {"part": "ESP32-S3-WROOM-1", "source": "test: KiCad's land, carried",
            "view": "top", "y_axis": y_axis, "pads": out,
            "keepout": {"polygon": [list(frame(x, y)) for x, y in S3_KEEPOUT_LOCAL]}}


class AVendorDrawingRegistersByItsPads(unittest.TestCase):
    """``{"drawing": path}``: the drawing fitted on the footprint's numbered pads
    with ``_land_fit(..., pair_by="number")``, then carried onto the board. The
    library's own keep-out, given as a drawing in another frame, lands on the
    area KiCad saved for the footprint. Arithmetic only: no KiCad needed."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nopekit-keepout-drawing-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.g = _keepout_gate()[1].__globals__
        self.tree = K.load_tree(RADIO_BOARD)

    def area(self, drawing: dict, tree=None, ref: str = "U1"):
        tree = tree or self.tree
        path = os.path.join(self.root, "drawing.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(drawing, fh)
        ctx = _keepout_ctx(self.root, {})
        return self.g["_drawing_area"](ctx, tree, _footprint(tree, ref), ref, path, "rec")

    def stored(self, tree=None, ref: str = "U1"):
        zone = K.children(_footprint(tree or self.tree, ref), "zone")[0]
        return self.g["_polygon_points"](zone)

    def test_the_library_keepout_lands_where_kicad_saved_it(self):
        _poly, fit = self.area(_s3_drawing())
        self.assertEqual(fit["paired_by"], "number")
        self.assertLess(fit["rms_mm"], 1e-3)
        for got, want in zip(fit["polygon_board_mm"], self.stored()):
            self.assertAlmostEqual(got[0], want[0], places=3)
            self.assertAlmostEqual(got[1], want[1], places=3)

    def test_the_library_frame_reaches_the_board_as_kicad_places_it(self):
        if FOOTPRINTS is None:
            self.skipTest("needs KiCad's footprint library, to read the stock area")
        lib = K.load_tree(os.path.join(FOOTPRINTS, "RF_Module.pretty",
                                       "ESP32-S3-WROOM-1.kicad_mod"))
        local = self.g["_polygon_points"](K.children(lib, "zone")[0])
        self.assertEqual([tuple(p) for p in local], S3_KEEPOUT_LOCAL)
        place = self.g["_footprint_to_board"](_footprint(self.tree, "U1"))
        for (x, y), want in zip(S3_KEEPOUT_LOCAL, self.stored()):
            got = place((x, y))
            self.assertAlmostEqual(got[0], want[0], places=6)
            self.assertAlmostEqual(got[1], want[1], places=6)

    def test_a_drawing_that_does_not_register_is_refused(self):
        P = self.g["P"]
        cases = {
            "mirrored": (_s3_drawing(mirror=True), "does not register on U1"),
            "on one line": (_s3_drawing(pads=("1", "7", "14")), "sit on one line"),
            "two pads": (_s3_drawing(pads=("1", "27")), "gives 2 pad(s)"),
            "a repeated pad": (_s3_drawing(pads=("1", "14", "27", "41")),
                               "which U1's footprint repeats"),
            "no keep-out": (dict(_s3_drawing(), keepout=None), "has no keepout"),
            "a short polygon": (dict(_s3_drawing(), keepout={"polygon": [[0, 0], [1, 1]]}),
                                "three or more"),
        }
        for name, (drawing, said) in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(P.RecordError) as caught:
                    self.area(drawing)
                self.assertIn(said, str(caught.exception))


class EachRadioIsReadByItsOwnKeepout(unittest.TestCase):
    """A BLE, a Wi-Fi, a sub-GHz and a LoRa module from KiCad's RF_Module on one
    four-layer board, each placed by KiCad, the Wi-Fi module turned 37 degrees
    and flipped to B.Cu, with a track on In2.Cu under each antenna and a ground
    pour on In1.Cu everywhere. The BLE module's footprint (RN4871) carries an
    every-layer antenna area and two F.Cu areas beside pads: only the antenna's
    is read, so the pour under the pads is not a finding. The sub-GHz module's
    area (IQRF) is on F.Cu alone, so the pour runs under its antenna, and is."""

    @classmethod
    def setUpClass(cls):
        if not KEEPOUT_TOOLS or FOOTPRINTS is None:
            raise unittest.SkipTest(KEEPOUT_SKIP)
        cls.root = tempfile.mkdtemp(prefix="nopekit-keepout-radios-")
        cls.spec, fn = _keepout_gate()
        cls.fn = staticmethod(fn)
        cls.g = fn.__globals__
        parts = [
            {"lib": "RF_Module", "name": "Microchip_RN4871", "ref": "U1", "value": "RN4871",
             "at": [20, 60]},
            {"lib": "RF_Module", "name": "ESP32-S3-WROOM-1", "ref": "U2",
             "value": "ESP32-S3-WROOM-1", "at": [100, 45], "rot": 37, "back": True,
             "nets": {"1": "GND", "40": "GND", "41": "GND"}},
            {"lib": "RF_Module", "name": "IQRF_TRx2DA_KON-SIM-01", "ref": "U3",
             "value": "TR-72DA", "at": [30, 12]},
            {"lib": "RF_Module", "name": "HOPERF_RFM9XW_SMD", "ref": "U4", "value": "RFM95W",
             "at": [45, 45]},
        ]
        cls.spec_board = {"layers": 4, "outline": [140, 80], "parts": parts,
                          "pours": [{"layer": "In1.Cu", "net": "GND",
                                     "pts": [[0.5, 0.5], [139.5, 0.5], [139.5, 79.5],
                                             [0.5, 79.5]]}]}
        probe = _build_board(cls.root, "probe", cls.spec_board)
        u2 = _footprint(K.load_tree(probe), "U2")
        cls.u2_centre = cls.g["_footprint_to_board"](u2)((0.0, -17.25))
        x, y = cls.u2_centre
        cls.board = _build_board(cls.root, "radios", dict(cls.spec_board, tracks=[
            {"layer": "In2.Cu", "net": "GND", "pts": [[17, 53.5], [23, 53.5]]},
            {"layer": "In2.Cu", "net": "GND", "pts": [[25, 29], [35, 29]]},
            {"layer": "In2.Cu", "net": "GND", "pts": [[x - 2, y], [x + 2, y]]}]))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, True)

    def run_on(self, params: dict):
        return gates_mod.run_gate(self.spec, self.fn, _keepout_ctx(self.root, params))

    def findings(self, v) -> list:
        return sorted((f["ref"], f["item"].split(",")[0]) for f in _keepout_evidence(v)["findings"])

    WANT = [("U1", "Track [GND] on In2.Cu"), ("U2", "Track [GND] on In2.Cu"),
            ("U3", "Track [GND] on In2.Cu"), ("U3", "Zone [GND] on In1.Cu")]

    def test_each_module_is_found_and_judged(self):
        none = {"pcb_antennas": {"lora": {"ref": "U4", "value": "RFM95W", "keepout": "none",
                                          "source": "a wire antenna on its ANT pad"}}}
        v = self.run_on(dict(none, pcb_board=self.board))
        self.assertEqual((v.outcome, v.measured), ("fail", 4), v.render())
        self.assertEqual(self.findings(v), self.WANT)
        self.assertIn("listed, not judged: U4 (no antenna on the board)", v.detail)
        # Without the record the LoRa module is not judged, and the fails stand.
        v = self.run_on({"pcb_board": self.board})
        self.assertEqual((v.outcome, v.measured), ("fail", 4), v.render())
        self.assertIn("not judged: no antenna keep-out for U4 (RF_Module:HOPERF_RFM9XW_SMD)",
                      v.detail)

    def test_a_drawing_on_a_turned_flipped_module_lands_on_its_area(self):
        """U2's own keep-out given as a vendor drawing: registered on a module
        KiCad turned 37 degrees and flipped, it covers what the footprint's area
        covers, and finds the same track."""
        tree = K.load_tree(self.board)
        land, _ = K.footprint_land(tree, "U2")
        by = {p.number: p for p in land}
        drawing = {"part": "ESP32-S3-WROOM-1", "source": "test", "view": "top",
                   "y_axis": "down",
                   "pads": [{"number": n, "function": n, "x": by[n].x, "y": by[n].y,
                             "w": by[n].w, "h": by[n].h} for n in ("1", "14", "27", "40")],
                   "keepout": {"polygon": [list(p) for p in S3_KEEPOUT_LOCAL]}}
        path = os.path.join(self.root, "u2.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(drawing, fh)
        v = self.run_on({"pcb_board": self.board, "pcb_antennas": {
            "wifi": {"ref": "U2", "value": "ESP32-S3-WROOM-1", "keepout": {"drawing": path},
                     "source": "test"},
            "lora": {"ref": "U4", "value": "RFM95W", "keepout": "none", "source": "test"}}})
        self.assertEqual(self.findings(v), self.WANT, v.render())
        ev = _keepout_evidence(v)
        got = next(j for j in ev["judged"] if j["ref"] == "U2")["polygons_board_mm"][0]
        want = self.g["_polygon_points"](K.children(_footprint(tree, "U2"), "zone")[0])
        for a, b in zip(got, want):
            self.assertAlmostEqual(a[0], b[0], places=3)
            self.assertAlmostEqual(a[1], b[1], places=3)

    def test_a_designers_rule_area_is_read_the_same_way(self):
        """A chip antenna's clearance the designer drew on F.Cu alone, named:
        copied to every layer, it finds the In1.Cu pour under it."""
        board = _build_board(self.root, "chip", dict(self.spec_board, areas=[
            {"name": "AE1 clearance", "layers": ["F.Cu"],
             "pts": [[60, 5], [75, 5], [75, 15], [60, 15]]}]))
        v = self.run_on({"pcb_board": board, "pcb_antennas": {
            "chip": {"ref": "U4", "value": "RFM95W", "keepout": {"rule_area": "AE1 clearance"},
                     "source": "test: the antenna's datasheet clearance"}}})
        self.assertIn(("U4", "Zone [GND] on In1.Cu"), self.findings(v), v.render())
        self.assertIn("U4 (rule area 'AE1 clearance')", v.detail)


class ACutoutInAKeepoutStaysACutout(unittest.TestCase):
    """A chip antenna (KiCad's stock Johanson 2450AT18) on a two-layer board,
    its clearance a rule area the designer drew on F.Cu and B.Cu with a cutout
    where the feed runs. KiCad saves the cutout as the area's second polygon and
    reads it back as a hole. A track in the cutout is not a finding; a track in
    the area outside it is. What slipped through (review of P8): each polygon
    became a solid area of its own, so the feed in the cutout read as a finding,
    and the evidence showed the outline only."""

    @classmethod
    def setUpClass(cls):
        if not KEEPOUT_TOOLS or FOOTPRINTS is None:
            raise unittest.SkipTest(KEEPOUT_SKIP)
        cls.root = tempfile.mkdtemp(prefix="nopekit-keepout-cutout-")
        cls.spec, fn = _keepout_gate()
        cls.fn = staticmethod(fn)
        cls.board = _build_board(cls.root, "chip", {
            "layers": 2, "outline": [100, 40],
            "parts": [{"lib": "RF_Antenna", "name": "Johanson_2450AT18x100", "ref": "AE1",
                       "value": "2450AT18A100", "at": [75, 12], "nets": {"1": "RF"}}],
            "areas": [{"name": "AE1 clearance", "layers": ["F.Cu", "B.Cu"],
                       "pts": [[60, 5], [90, 5], [90, 25], [60, 25]],
                       "holes": [[[70, 10], [80, 10], [80, 20], [70, 20]]]}],
            "tracks": [{"layer": "F.Cu", "net": "RF", "pts": [[72, 17], [78, 17]]},
                       {"layer": "B.Cu", "net": "GND", "pts": [[62, 22], [68, 22]]}]})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, True)

    def test_the_board_holds_the_cutout_as_kicad_saves_one(self):
        zone = next(z for z in K.children(K.load_tree(self.board), "zone")
                    if str(K.value(z, "name", default="")) == "AE1 clearance")
        self.assertEqual(len(K.children(zone, "polygon")), 2, "the outline, then the cutout")

    def test_a_track_in_the_cutout_is_not_a_finding(self):
        v = gates_mod.run_gate(self.spec, self.fn, _keepout_ctx(self.root, {
            "pcb_board": self.board, "pcb_antennas": {"chip": {
                "ref": "AE1", "value": "2450AT18A100", "keepout": {"rule_area": "AE1 clearance"},
                "source": "test: the antenna's datasheet clearance, the feed cut out"}}}))
        self.assertEqual((v.outcome, v.measured), ("fail", 1), v.render())
        ev = _keepout_evidence(v)
        self.assertEqual([(f["ref"], f["item"].split(",")[0]) for f in ev["findings"]],
                         [("AE1", "Track [GND] on B.Cu")])
        self.assertEqual(ev["own_items_left_out"], [], "AE1's pads sit in the cutout")
        [judged] = ev["judged"]
        self.assertEqual(judged["polygons_board_mm"], [[[60, 5], [90, 5], [90, 25], [60, 25]]])
        self.assertEqual(judged["holes_board_mm"], [[[[70, 10], [80, 10], [80, 20], [70, 20]]]])
        self.assertIn("AE1 (rule area 'AE1 clearance', 1 cutout)", v.detail)
        # KiCad's own DRC on the board reads the cutout the same way: the area's
        # one item is the track outside it.
        report = os.path.join(self.root, "plain.json")
        subprocess.run(["kicad-cli", "pcb", "drc", "--severity-all", "--format", "json",
                        "-o", report, self.board], capture_output=True, timeout=120,
                       cwd=self.root, check=True)
        with open(report, encoding="utf-8") as fh:
            plain = [i["description"].split(",")[0] for x in json.load(fh)["violations"]
                     if x["type"] == "items_not_allowed" for i in x["items"]]
        self.assertEqual(plain, ["Track [GND] on B.Cu"])


class AHoleWithNoCopperIsListedNotCounted(unittest.TestCase):
    """KiCad's stock ESP32-S3-WROOM-1 on a four-layer board, with two of KiCad's
    stock M2 mounting holes in its keep-out: H1 as the library draws it (a
    non-plated hole, its pad no wider than its drill: no copper, as pcbnew's
    ``PAD::FlashLayer`` says), and H2 with its pad widened to 3.0 mm (a copper
    ring on every layer). KiCad's keep-out reports both ("NPTH pad of H1"). H1 is
    listed and not counted: the rule is copper, and a screw in it is the metal
    lens's. H2 counts. What slipped through (review of P8): H1 counted, and a
    hole for a plastic standoff under the antenna failed a sound board."""

    @classmethod
    def setUpClass(cls):
        if not KEEPOUT_TOOLS or FOOTPRINTS is None:
            raise unittest.SkipTest(KEEPOUT_SKIP)
        cls.root = tempfile.mkdtemp(prefix="nopekit-keepout-holes-")
        cls.spec, fn = _keepout_gate()
        cls.fn = staticmethod(fn)
        hole = {"lib": "MountingHole", "name": "MountingHole_2.2mm_M2", "value": "MountingHole"}
        built = _build_board(cls.root, "holes", {
            "layers": 4, "outline": [60, 40],
            "parts": [{"lib": "RF_Module", "name": "ESP32-S3-WROOM-1", "ref": "U1",
                       "value": "ESP32-S3-WROOM-1", "at": [30, 25]},
                      dict(hole, ref="H1", at=[30, 6]), dict(hole, ref="H2", at=[40, 6])]})
        tree = K.load_tree(built)
        pad = _pad(tree, "H2", "")
        cls.stock = [str(x) for x in K.child(pad, "size")[1:]], str(K.child(pad, "drill")[1])
        K.child(pad, "size")[1:] = ["3", "3"]
        cls.both = os.path.join(cls.root, "both.kicad_pcb")
        with open(cls.both, "w", encoding="utf-8") as fh:
            fh.write(K.dump(tree) + "\n")
        tree[:] = [c for c in tree if not (isinstance(c, list) and K.head(c) == "footprint"
                                           and K.footprint_ref(c)[0] == "H2")]
        cls.bare = os.path.join(cls.root, "bare.kicad_pcb")
        with open(cls.bare, "w", encoding="utf-8") as fh:
            fh.write(K.dump(tree) + "\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, True)

    def run_on(self, board: str):
        return gates_mod.run_gate(self.spec, self.fn, _keepout_ctx(self.root, {"pcb_board": board}))

    def test_the_stock_hole_has_no_ring(self):
        self.assertEqual(self.stock, (["2.2", "2.2"], "2.2"))

    def test_a_hole_with_no_copper_passes_and_is_named(self):
        v = self.run_on(self.bare)
        self.assertEqual((v.outcome, v.measured), ("pass", 0), v.render())
        self.assertIn("holes with no copper in a keep-out, not counted: NPTH pad of H1 (U1) (a "
                      "metal screw in one is lenses.md's)", v.detail)
        ev = _keepout_evidence(v)
        self.assertEqual([(h["ref"], h["item"]) for h in ev["holes_no_copper_left_out"]],
                         [("U1", "NPTH pad of H1")])

    def test_a_hole_with_a_copper_ring_counts(self):
        v = self.run_on(self.both)
        self.assertEqual((v.outcome, v.measured), ("fail", 1), v.render())
        ev = _keepout_evidence(v)
        self.assertEqual([(f["ref"], f["item"]) for f in ev["findings"]], [("U1", "NPTH pad of H2")])
        self.assertEqual([h["item"] for h in ev["holes_no_copper_left_out"]], ["NPTH pad of H1"])


class TheFootprintsAntennaAreaIsChosen(unittest.TestCase):
    """Which of a stock footprint's rule areas is its antenna's, read from KiCad
    9.0.9's own library: no KiCad run needed, only its footprints."""

    def setUp(self):
        if FOOTPRINTS is None:
            self.skipTest("needs KiCad's footprint library")
        self.g = _keepout_gate()[1].__globals__
        self.copper = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]

    def areas(self, name: str) -> list:
        fp = K.load_tree(os.path.join(FOOTPRINTS, "RF_Module.pretty", name + ".kicad_mod"))
        return [sorted(self.g["_zone_layers"](z, self.copper))
                for z in self.g["_footprint_areas"](fp, self.copper)]

    def test_the_every_layer_area_alone_beside_pad_areas(self):
        self.assertEqual(self.areas("Microchip_RN4871"), [sorted(self.copper)])

    def test_a_partial_area_is_read_and_an_area_that_allows_copper_is_not(self):
        self.assertEqual(self.areas("ESP32-S3-WROOM-1"), [["B.Cu", "F.Cu", "In1.Cu"]])
        self.assertEqual(self.areas("NINA-B111"), [])
        self.assertEqual(self.areas("TD1208"), [])
        self.assertEqual(self.areas("HOPERF_RFM9XW_SMD"), [])


class AFindingIsTiedToItsAreaAndItsPart(unittest.TestCase):
    """How a DRC report is read (``_antenna_findings``): an item counts when its
    violation names one of the gate's areas; the area's own part's items are its
    own copper, listed apart; a hole KiCad gives no copper (its id among the
    refill's ``bare_holes``) is listed apart too; an item another area names
    (the project's own keep-outs, which ``pcb.drc`` judges) is not this gate's."""

    def test_the_report_is_split_by_area_and_owner(self):
        read = _keepout_gate()[1].__globals__["_antenna_findings"]

        def item(words, uid):
            return {"description": words, "uuid": uid}
        doc = {"violations": [
            {"type": "items_not_allowed",
             "description": "Items not allowed (keepout area 'nopekit_antenna_U1')",
             "items": [item("Pad 41 [GND] of U1 on F.Cu", "a"),
                       item("Pad 1 [3V3] of U10 on F.Cu", "b"),
                       item("Track [GND] on In2.Cu, length 3.0000 mm", "c"),
                       item("NPTH pad of H1", "g"), item("NPTH pad of H2", "h")]},
            {"type": "items_not_allowed", "description": "Items not allowed (keepout area)",
             "items": [item("Track [GND] on F.Cu, length 1.0000 mm", "d")]},
            {"type": "items_not_allowed",
             "description": "Items not allowed (keepout area 'nopekit_antenna_U2')",
             "items": [item("Pad 2 [GND] of U1 on F.Cu", "e")]},
            {"type": "clearance", "description": "Clearance violation (keepout area "
                                                 "'nopekit_antenna_U1')",
             "items": [item("Via [GND] on F.Cu - B.Cu", "f")]},
        ]}
        owners = {"nopekit_antenna_U1": "U1", "nopekit_antenna_U2": "U2"}
        found, own, holes = read(doc, owners, {"g"})
        self.assertEqual([(r[1], r[2]) for r in found],
                         [("U1", "Pad 1 [3V3] of U10 on F.Cu"),
                          ("U1", "Track [GND] on In2.Cu, length 3.0000 mm"),
                          ("U1", "NPTH pad of H2"),
                          ("U2", "Pad 2 [GND] of U1 on F.Cu")])
        self.assertEqual([(r[1], r[2]) for r in own], [("U1", "Pad 41 [GND] of U1 on F.Cu")])
        self.assertEqual([(r[1], r[2]) for r in holes], [("U1", "NPTH pad of H1")])
        # With no list from the refill, no hole is set apart: every item counts.
        found, _own, holes = read(doc, owners)
        self.assertIn(("U1", "NPTH pad of H1"), [(r[1], r[2]) for r in found])
        self.assertEqual(holes, [])


class TheKeepoutsToolsAreHeldToTheirWord(unittest.TestCase):
    """A refill that crashes, and a DRC whose exit code and report disagree,
    are errors. Stand-ins, so no KiCad is needed."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nopekit-keepout-tools-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.spec, self.fn = _keepout_gate()

    def helper(self, body: str) -> str:
        path = os.path.join(self.root, "fill.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def test_a_crash_in_the_refill_is_an_error(self):
        crash = self.helper("import os, signal\nos.kill(os.getpid(), signal.SIGSEGV)\n")
        with mock.patch.dict(self.fn.__globals__, {"KEEPOUT_FILL_HELPER": crash}):
            v = self.fn(_keepout_ctx(self.root, _radio_record(RADIO_BOARD)))
        self.assertIn("the zone refill (KiCad's pcbnew, in a child) failed (exit -11)", v.error)

    def test_an_exit_code_that_disagrees_with_its_report_is_an_error(self):
        copy = self.helper("import json, shutil, sys\nshutil.copyfile(sys.argv[1], sys.argv[2])\n"
                           "print(json.dumps({'zones': 4, 'filled': True}))\n")
        bin_dir = os.path.join(self.root, "bin")
        os.makedirs(bin_dir)
        _stand_in(bin_dir, "kicad-cli",
                  "import json, sys\nout = sys.argv[sys.argv.index('-o') + 1]\n"
                  "json.dump({'violations': [{'type': 'items_not_allowed', 'description': "
                  "\"Items not allowed (keepout area 'nopekit_antenna_U1')\", 'items': "
                  "[{'description': 'Track [GND] on In2.Cu', 'uuid': 'x'}]}]}, open(out, 'w'))\n"
                  "sys.exit(0)\n")
        with mock.patch.dict(self.fn.__globals__, {"KEEPOUT_FILL_HELPER": copy}), \
                mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]}):
            v = self.fn(_keepout_ctx(self.root, _radio_record(RADIO_BOARD)))
        self.assertIn("kicad-cli exited 0 on the stored copy but its report lists 1 item(s)",
                      v.error)


# --------------------------------------------------------------------------- #
# general-packs P9: the proposers. Every unit of a multi-unit symbol, the stock
# libraries where KiCad puts them, and a draft of the power records.
# --------------------------------------------------------------------------- #
WRITER = os.path.join(PACK, "proposers", "schematic_from_board.py")
DRAFTER = os.path.join(PACK, "proposers", "battery_record_draft.py")

#: The power board's parts on KiCad's stock symbols, as ``make_power_board.py``
#: names them: Q1, an FDS6890A, is a two-unit symbol (one FET each). The
#: statements make KiCad's ERC find nothing on the written schematic: U1's status
#: pins, J1's SBU pins and the FFC housings' two open positions are open on
#: purpose, and four nets are fed from off this board.
POWER_SYMBOLS = {
    "symbols": {"J1": "Connector:USB_C_Receptacle_USB2.0_16P", "R*": "Device:R",
                "C*": "Device:C", "U1": "Battery_Management:MCP73833-xxx-UN",
                "TH1": "Device:Thermistor_NTC", "F1": "Device:Polyfuse",
                "BT1": "Connector_Generic:Conn_01x02", "U2": "Battery_Management:AP9101CK",
                "Q1": "Transistor_FET:FDS6890A", "J6": "Connector_Generic:Conn_01x02",
                "J1?": "Connector_Generic:Conn_01x08"},
    "no_connect": {"U1": ["3", "4", "7"], "J1": ["A8", "B8"], "J10": ["3", "6"],
                   "J11": ["3", "6"]},
    "power_flags": ["VBUS", "GND", "PROT_VDD", "CELL_N"]}


def _write(board: str, symbols: dict, cwd: str, name: str, env: dict | None = None):
    """``(process, out dir)`` of the schematic writer on ``board``."""
    out = os.path.join(cwd, name)
    os.makedirs(out)
    path = os.path.join(cwd, f"{name}.symbols.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(symbols, fh)
    proc = _env.run([sys.executable, WRITER, "--board", board, "--symbols", path], cwd=cwd,
                    env={"NOPEKIT_PROPOSAL_DIR": out, **(env or {})})
    return proc, out


def _placed(sch: list, ref: str) -> list:
    """The placed symbols of ``ref`` in a written schematic."""
    return [s for s in K.children(sch, "symbol")
            if any(len(p) > 2 and p[1] == "Reference" and p[2] == ref
                   for p in K.children(s, "property"))]


#: A child that prints each symbol's unit map as ``_schematic.unit_pins`` reads
#: it: run apart, as ``_DERIVED`` is, since the module edits sys.path. The last
#: entry is a symbol drawn here, its De Morgan body (style 2) with pin 1 moved.
_UNITS = r"""
import json, sys
sys.dont_write_bytecode = True
proposers, symbols = sys.argv[1:3]
sys.path.insert(0, proposers)
import _schematic as W
K = W.K
out = {}
for lib_id in sys.argv[3:]:
    lib, _, name = lib_id.partition(":")
    out[lib_id] = W.unit_pins(W.library_symbol(symbols, lib, name))
drawn = K.parse('(symbol "X" (symbol "X_1_1" (pin input line (at -5.08 0 0) (length 2.54) '
                '(name "A") (number "1"))) (symbol "X_1_2" (pin input line (at 5.08 2.54 '
                '180) (length 2.54) (name "A") (number "1"))))')
out["drawn"] = W.unit_pins(drawn)
print(json.dumps(out))
"""


@unittest.skipIf(SYMBOLS is None, "needs KiCad's stock symbols")
class AStockSymbolIsReadUnitByUnit(unittest.TestCase):
    """``unit_pins`` on KiCad's own multi-unit symbols: a dual op-amp with its
    power unit (LM358, which extends LM2904), a quad gate with its supply
    (74HC00, which extends 74LS00 and draws a De Morgan body), a dual MOSFET, and
    a resistor, one unit. The pins every unit draws are in each; a De Morgan
    body never moves a pin. (What slipped through: the writer refused every one
    of the first three, exit 2.)"""

    def test_each_unit_holds_its_own_pins(self):
        proc = _env.run([sys.executable, "-c", _UNITS, os.path.join(PACK, "proposers"),
                         SYMBOLS, "Amplifier_Operational:LM358", "74xx:74HC00",
                         "Transistor_FET:FDS6890A", "Device:R"], cwd=REPO)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = {k: {u: sorted(p, key=int) for u, p in v.items()}
               for k, v in json.loads(proc.stdout).items()}
        self.assertEqual(got["Amplifier_Operational:LM358"],
                         {"1": ["1", "2", "3"], "2": ["5", "6", "7"], "3": ["4", "8"]})
        self.assertEqual(got["74xx:74HC00"],
                         {"1": ["1", "2", "3"], "2": ["4", "5", "6"], "3": ["8", "9", "10"],
                          "4": ["11", "12", "13"], "5": ["7", "14"]})
        self.assertEqual(got["Transistor_FET:FDS6890A"],
                         {"1": ["1", "2", "7", "8"], "2": ["3", "4", "5", "6"]})
        self.assertEqual(got["Device:R"], {"1": ["1", "2"]})
        self.assertEqual(json.loads(proc.stdout)["drawn"], {"1": {"1": [-5.08, 0.0]}})


@unittest.skipIf(SYMBOLS is None or shutil.which("kicad-cli") is None,
                 "needs KiCad's stock symbols and the real kicad-cli")
class AMultiUnitPartIsPlacedWhole(unittest.TestCase):
    """The writer on the power board, whose Q1 is a two-unit FDS6890A: both
    units placed under Q1, KiCad's ERC finds nothing at every severity, and
    parity finds no difference. With a unit taken out after writing, ERC names
    the unit and parity its pins: so both judge the writer here. The same board
    with a dual op-amp's three units mapped on Q1's eight pads still matches the
    board pin for pin. (What slipped through: the writer refused Q1, exit 2, so
    this board, and any with a dual op-amp, a quad gate or a dual MOSFET, had no
    schematic to judge.)"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nopekit-pcb-units-")
        self.addCleanup(shutil.rmtree, self.root, True)
        with open(POWER_BOARD, encoding="utf-8") as fh:
            self.board = _board_copy(self.root, fh.read(), stem="p")

    def _adopt(self, out: str) -> None:
        for name in ("p.kicad_sch", "sym-lib-table"):
            shutil.copyfile(os.path.join(out, name), os.path.join(self.root, name))

    def _judge(self) -> dict:
        registry = _registry()
        params = dict(_baseline(), pcb_board=self.board)
        return {gid: _run(registry, gid, self.root, params) for gid in ("pcb.erc", "pcb.parity")}

    def test_every_unit_is_placed_and_kicad_agrees(self):
        proc, out = _write(self.board, POWER_SYMBOLS, self.root, "w")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("1 multi-unit part(s), every unit placed", proc.stdout)
        with open(os.path.join(out, "p.schematic.json"), encoding="utf-8") as fh:
            summary = json.load(fh)
        self.assertEqual(summary["units"], {"Q1": {"1": ["1", "2", "7", "8"],
                                                   "2": ["3", "4", "5", "6"]}})
        with open(os.path.join(out, "p.kicad_sch"), encoding="utf-8") as fh:
            sch = K.parse(fh.read())
        q1 = _placed(sch, "Q1")
        self.assertEqual(sorted(str(K.value(s, "unit")) for s in q1), ["1", "2"])
        self.assertEqual(len({str(K.value(s, "uuid")) for s in q1}), 2)
        self._adopt(out)
        for gid, verdict in self._judge().items():
            with self.subTest(gate=gid):
                self.assertEqual(verdict.outcome, "pass", verdict.render())
        # Unit 2 taken out: both rules see it.
        path = os.path.join(self.root, "p.kicad_sch")
        tree = K.load_tree(path)
        tree[:] = [x for x in tree if not (isinstance(x, list) and K.head(x) == "symbol"
                                           and x in _placed(tree, "Q1")
                                           and str(K.value(x, "unit")) == "2")]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(K.dump(tree) + "\n")
        found = self._judge()
        self.assertEqual(found["pcb.erc"].outcome, "fail", found["pcb.erc"].render())
        self.assertIn("missing_unit: Symbol Q1", found["pcb.erc"].detail)
        self.assertEqual(found["pcb.parity"].outcome, "fail", found["pcb.parity"].render())
        self.assertEqual(found["pcb.parity"].measured, 4)     # unit 2's pads 3 to 6

    def test_a_dual_op_amp_takes_its_three_units(self):
        symbols = copy.deepcopy(POWER_SYMBOLS)
        symbols["symbols"]["Q1"] = "Amplifier_Operational:LM358"
        proc, out = _write(self.board, symbols, self.root, "w")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(os.path.join(out, "p.schematic.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["units"]["Q1"],
                             {"1": ["1", "2", "3"], "2": ["5", "6", "7"], "3": ["4", "8"]})
        self._adopt(out)
        verdict = self._judge()["pcb.parity"]
        self.assertEqual(verdict.outcome, "pass", verdict.render())


@unittest.skipIf(SYMBOLS is None, "needs KiCad's stock symbols")
class TheStockLibrariesAreNamedAsKiCadNamesThem(unittest.TestCase):
    """Where the stock symbols come from, and the variable the sym-lib-table
    names them by: KICAD10_SYMBOL_DIR before KICAD9_SYMBOL_DIR, each written as
    itself; a fixed path by the release ``kicad-cli version`` gives, or KiCad 9's
    name, said so, when none answers. (What slipped through: only
    KICAD9_SYMBOL_DIR and the Linux path were read, and every table said
    ``${KICAD9_SYMBOL_DIR}``, so a KiCad 10 install, macOS and Windows read no
    symbol.)"""

    UNSET = {v: None for v in ("KICAD10_SYMBOL_DIR", "KICAD9_SYMBOL_DIR", "KICAD_SYMBOL_DIR")}

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nopekit-pcb-stock-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.board = _board_copy(self.root, _board_text())

    def _named(self, name: str, env: dict) -> tuple[set[str], dict]:
        proc, out = _write(self.board, _esd_symbols(), self.root, name, env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(os.path.join(out, "sym-lib-table"), encoding="utf-8") as fh:
            uris = set(re.findall(r'\(uri "\$\{(\w+)\}/', fh.read()))
        with open(os.path.join(out, "b.schematic.json"), encoding="utf-8") as fh:
            return uris, json.load(fh)["stock_variables"]

    def test_the_variable_it_was_found_through(self):
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty)
        for name, env, want in (
                ("ten", {**self.UNSET, "KICAD10_SYMBOL_DIR": SYMBOLS}, "KICAD10_SYMBOL_DIR"),
                ("nine", {**self.UNSET, "KICAD10_SYMBOL_DIR": empty,
                          "KICAD9_SYMBOL_DIR": SYMBOLS}, "KICAD9_SYMBOL_DIR")):
            with self.subTest(env=want):
                uris, said = self._named(name, env)
                self.assertEqual(uris, {want})
                self.assertEqual(said, {want: "set in the environment"})

    @unittest.skipUnless(SYMBOLS == "/usr/share/kicad/symbols",
                         "needs the stock symbols at the Linux package's fixed path")
    def test_a_fixed_path_takes_the_release_kicad_cli_gives(self):
        ten = os.path.join(self.root, "ten-bin")
        none = os.path.join(self.root, "no-bin")
        os.makedirs(ten)
        os.makedirs(none)
        _stand_in(ten, "kicad-cli", "print('10.0.1')\n")
        uris, said = self._named("cli", {**self.UNSET, "PATH": ten})
        self.assertEqual((uris, said), ({"KICAD10_SYMBOL_DIR"},
                                        {"KICAD10_SYMBOL_DIR": "kicad-cli version 10.0.1"}))
        uris, said = self._named("bare", {**self.UNSET, "PATH": none})
        self.assertEqual(uris, {"KICAD9_SYMBOL_DIR"})
        self.assertIn("not read from KiCad", said["KICAD9_SYMBOL_DIR"])

    def test_the_search_order_and_a_windows_release_folder(self):
        """The search order, and a Windows install's release folder read as its
        variable's major: pure functions of their input (no such folder here)."""
        proc = _env.run([sys.executable, "-c", _STOCK, os.path.join(PACK, "proposers")],
                        cwd=REPO)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout)
        self.assertEqual(got["dirs"], [["/k10", "KICAD10_SYMBOL_DIR"],
                                       ["/k9", "KICAD9_SYMBOL_DIR"],
                                       *[[p, None] for p in got["paths"]]])
        self.assertEqual(got["paths"][0], "/usr/share/kicad/symbols")
        self.assertEqual(got["windows"], ["KICAD10_SYMBOL_DIR", "the release folder of "
                                          r"C:\Program Files\KiCad\10.0\share\kicad\symbols"])


#: A child that prints the writer's search order for a given environment, and the
#: variable it names a Windows install's symbol folder by.
_STOCK = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import schematic_from_board as S
print(json.dumps({
    "dirs": S.stock_dirs({"KICAD9_SYMBOL_DIR": "/k9", "KICAD10_SYMBOL_DIR": "/k10"}),
    "paths": list(S.STOCK_PATHS),
    "windows": S.stock_variable(r"C:\Program Files\KiCad\10.0\share\kicad\symbols")}))
"""


def _draft(root: str, *boards: str):
    """``(process, records, summary)`` of the record draft on ``boards`` (paths
    from ``root``), run from ``root``; the two dicts are None when it wrote
    nothing."""
    out = tempfile.mkdtemp(prefix="draft-", dir=root)
    argv = [a for b in boards for a in ("--board", b)]
    proc = _env.run([sys.executable, DRAFTER, *argv], cwd=root,
                    env={"NOPEKIT_PROPOSAL_DIR": out})
    names = sorted(os.listdir(out))
    if not names:
        return proc, None, None
    stem = os.path.basename(boards[0])[:-len(".kicad_pcb")]
    assert names == [f"{stem}.power_draft.json", f"{stem}.power_records.json"], names
    with open(os.path.join(out, names[1]), encoding="utf-8") as fh:
        records = json.load(fh)
    with open(os.path.join(out, names[0]), encoding="utf-8") as fh:
        summary = json.load(fh)
    return proc, records, summary


def _is_fill(value) -> bool:
    if isinstance(value, list):
        return bool(value) and all(_is_fill(v) for v in value)
    return isinstance(value, str) and value.startswith("FILL: ")


def _filled(draft, base):
    """``draft`` with each FILL value replaced by ``base``'s value at its path, and
    dropped where ``base`` has none; every other value is the draft's own."""
    if not isinstance(draft, dict):
        return draft
    out = {}
    for key, value in draft.items():
        if isinstance(base, dict) and key in base:
            out[key] = base[key] if _is_fill(value) else _filled(value, base[key])
        elif not _is_fill(value):
            out[key] = value
    return out


def _part(kind: str, rec: dict):
    """What a draft record and a baseline record of one part share."""
    if kind == "pcb_batteries":
        return rec["terminals"]["ref"]
    if kind == "pcb_matings":
        return rec["a"]["ref"]
    if kind == "pcb_conductors":
        return rec["ref"], tuple(rec["pins"])
    return rec.get("ref")


#: The six rules that read the power records.
DRAFT_GATES = POWER_GATES + ("pcb.mismate", "pcb.usbc_receptacle")


class ADraftSettlesNothing(_PowerCase):
    """``pcb.battery_record_draft`` on the power board: every power rule refuses
    the draft as written, naming a drafted source, and with each source written,
    naming a FILL value; nothing in it is a number or a flag; with every FILL
    stated and the drafted sources kept, every rule still refuses; and with each
    FILL and each source taken from the baseline's own records, and nothing else,
    the draft reads as the baseline does, so the refs, pads, ids and shapes it
    wrote are the board's and the rules'. (What it answers: the records are the
    heaviest part of first use, and a draft that guessed one value would read as
    a fact once adopted. What slipped through, in review of P9: the drafted
    source was a sentence, which ``_power.source`` takes, since it checks that a
    source says something and not what; so with every number stated the draft
    passed all six rules, its source the proposer's words.)"""

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.root, "power_board.kicad_pcb")
        shutil.copyfile(POWER_BOARD, self.path)
        self.proc, self.records, self.summary = _draft(self.root, "power_board.kicad_pcb")
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)

    def _params(self, records: dict) -> dict:
        params = _power_params(self.path)
        params.update(records)
        return params

    @staticmethod
    def _sources(records: dict, source) -> dict:
        """``records`` with each record's source replaced by ``source(kind, id,
        record)``."""
        out = copy.deepcopy(records)
        for kind, recs in out.items():
            for rid, rec in recs.items():
                if "source" in rec:
                    rec["source"] = source(kind, rid, records[kind][rid])
        return out

    def test_every_power_rule_refuses_the_draft_as_written(self):
        """As written, each rule refuses a drafted source (a list: a source must
        be a sentence); with every source written, each refuses a FILL value."""
        for records, said in (
                (self.records, "source is empty: say where each number comes from"),
                (self._sources(self.records, lambda k, r, _rec: f"{k}.{r}: a person's words"),
                 "FILL: ")):
            params = self._params(records)
            for gid in DRAFT_GATES:
                with self.subTest(gate=gid, said=said):
                    verdict = _run(self.registry, gid, self.root, params)
                    self.assertEqual(verdict.outcome, "error", verdict.render())
                    self.assertIn(said, verdict.error)

    def test_a_board_with_no_cell_skips_or_refuses(self):
        """The same board with its cell, charger, protector and fuse taken off:
        the four battery rules skip (no pcb_batteries), and the mating and USB-C
        rules refuse their FILL values. None passes."""
        def strip(tree):
            tree[:] = [x for x in tree if not (isinstance(x, list) and K.head(x) == "footprint"
                                               and K.footprint_ref(x)[0] in
                                               ("BT1", "U1", "U2", "F1"))]
        board = self.board(strip, stem="no_cell")
        proc, records, _summary = _draft(self.root, os.path.basename(board))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual({k: sorted(v) for k, v in records.items() if v},
                         {"pcb_matings": ["j1", "j10", "j11"], "pcb_usbc": ["j1"]})
        params = _power_params(board)
        params.update(records)
        for gid in DRAFT_GATES:
            with self.subTest(gate=gid):
                verdict = _run(self.registry, gid, self.root, params)
                self.assertIn(verdict.outcome, ("skipped", "error"), verdict.render())
                if gid in POWER_GATES:
                    self.assertIn("no pcb_batteries", verdict.skip_reason)

    def test_no_number_and_no_flag_is_in_it(self):
        fills, leaves = [], []

        def walk(node, path):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{path}.{k}" if path else k)
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")
            else:
                leaves.append((path, node))
                if isinstance(node, str) and node.startswith("FILL: "):
                    fills.append(path)
        walk(self.records, "")
        self.assertEqual([(p, v) for p, v in leaves if not isinstance(v, str)], [])
        self.assertEqual(self.summary["fill"], fills)
        # 64 datasheet numbers, flags and words; U1's and U2's kind (charger,
        # protector, bms or gauge is the person's word: in review of P9 the
        # draft wrote it); and the 11 records' sources.
        self.assertEqual(len(fills), 77)
        self.assertEqual(sorted(self.records), sorted(DRAFT_RECORDS))
        sources = 0
        for kind, recs in self.records.items():
            for rid, rec in recs.items():
                if "source" in rec:
                    sources += 1
                    with self.subTest(record=f"{kind}.{rid}"):
                        self.assertIsInstance(rec["source"], list)
                        self.assertEqual(len(rec["source"]), 1)
                        self.assertTrue(rec["source"][0].startswith(
                            "FILL: DRAFT by pcb.battery_record_draft"), rec["source"])
        self.assertEqual(sources, 11)
        for pid, rec in self.records["pcb_protectors"].items():
            with self.subTest(kind=pid):
                self.assertTrue(_is_fill(rec["kind"]), rec["kind"])
        self.assertIn("charger when U1", self.records["pcb_protectors"]["u1"]["kind"])
        self.assertEqual(self.summary["keep_or_delete"], [])

    def _from_baseline(self) -> tuple[dict, dict]:
        """``(the baseline's params, the draft with each FILL taken from them)``:
        each record's source the baseline's, a person's words, which is what
        lets the record pass."""
        base = _power_params(self.path)
        filled, dropped = {}, []
        for kind in DRAFT_RECORDS:
            by_part = {_part(kind, rec): rec for rec in base[kind].values()}
            filled[kind] = {}
            for rid, rec in self.records[kind].items():
                if _part(kind, rec) in by_part:
                    filled[kind][rid] = _filled(rec, by_part[_part(kind, rec)])
                else:
                    dropped.append(f"{kind}.{rid}")
        # J11 is the far end of the baseline's one board-to-board mating.
        self.assertEqual(dropped, ["pcb_matings.j11"])
        self.assertEqual(base["pcb_matings"]["flex"]["b"]["ref"], "J11")
        self.assertEqual([p for p in self._fill_paths(filled)], [])
        return base, filled

    def test_stated_numbers_beside_a_drafted_source_pass_nothing(self):
        """Every FILL stated from the baseline, and each source left as drafted:
        every rule refuses the source. Writing it is the person's act that lets
        the record pass."""
        _base, filled = self._from_baseline()
        kept = self._sources(filled, lambda k, r, _rec: self.records[k][r]["source"])
        self.assertEqual(sorted({p.rsplit(".", 1)[-1] for p in self._fill_paths(kept)}),
                         ["source[0]"])
        params = self._params(kept)
        for gid in DRAFT_GATES:
            with self.subTest(gate=gid):
                verdict = _run(self.registry, gid, self.root, params)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn("source is empty", verdict.error)

    def test_filled_from_the_baseline_it_reads_as_the_baseline(self):
        base, filled = self._from_baseline()
        for gid in DRAFT_GATES:
            with self.subTest(gate=gid):
                want = _run(self.registry, gid, self.root, base)
                got = _run(self.registry, gid, self.root, self._params(filled))
                self.assertEqual(want.outcome, "pass", want.render())
                self.assertEqual((got.outcome, got.measured, got.units),
                                 (want.outcome, want.measured, want.units), got.render())

    @staticmethod
    def _fill_paths(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from ADraftSettlesNothing._fill_paths(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from ADraftSettlesNothing._fill_paths(v, f"{path}[{i}]")
        elif _is_fill(node):
            yield path

    def test_it_finds_each_part_by_its_evidence(self):
        found = {row["record"]: row["found_by"] for row in self.summary["candidates"]}
        self.assertEqual(found["pcb_batteries.bt1"], "ref BT1")
        self.assertEqual(found["pcb_protectors.u1"], "pins VBAT (9, 10), PROG (6), THERM (8)")
        self.assertEqual(found["pcb_protectors.u2"], "pins DO (4) and CO (5)")
        self.assertEqual(found["pcb_protectors.f1"],
                         "library Fuse, pad 1 on the battery's CELL_P")
        charger = self.records["pcb_protectors"]["u1"]["functions"]
        self.assertEqual(charger["charge_a"]["prog"]["ref"], "R3")
        self.assertEqual(charger["charge_temp"]["sense"]["pin"], "8")
        self.assertEqual(charger["charge_temp"]["ntc"]["ref"], "TH1")
        cut = self.records["pcb_protectors"]["u2"]["functions"]
        self.assertEqual({k: (f["acts"]["pin"], f["acts"]["switch"]["ref"],
                              f["acts"]["switch"]["control"]) for k, f in cut.items()},
                         {"overcharge_v": ("5", "Q1", "4"), "overdischarge_v": ("4", "Q1", "2")})
        self.assertEqual(self.records["pcb_conductors"]["j6_vsys"]["protected_by"], ["f1"])
        self.assertIn("in-pack", " ".join(self.summary["cannot_see"]))

    def test_the_copies_are_the_gates(self):
        """The coverage rule's connector test and the USB-C receptacle test are
        copied into the proposer (a proposer imports no gate): held equal here,
        constant by constant and on every footprint of both baseline boards."""
        proc = _env.run([sys.executable, "-c", _COPIES, os.path.join(PACK, "proposers"),
                         POWER_BOARD, BOARD], cwd=REPO)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout)
        power, helpers = self.power, self.helpers
        self.assertEqual(got["connector_libs"], list(power.CONNECTOR_LIBS))
        self.assertEqual(got["connector_ref"], power._CONNECTOR_REF.pattern)
        self.assertEqual(got["typec_vbus"], list(helpers.TYPEC_VBUS))
        self.assertEqual(got["typec_data"], list(helpers.TYPEC_DP + helpers.TYPEC_DM))
        self.assertEqual(got["typec_name"], helpers._TYPEC_NAME.pattern)
        self.assertEqual(got["typec_ref"], helpers._CONNECTOR_REF.pattern)
        for path in (POWER_BOARD, BOARD):
            board = K.load_board(path)
            self.assertEqual(got["found"][path],
                             {fp.ref: helpers.typec_receptacle(fp) for fp in board.footprints})
        self.assertTrue(got["found"][POWER_BOARD]["J1"])

    def test_a_refusal_writes_nothing(self):
        with open(os.path.join(self.root, "not_a.kicad_pcb"), "w", encoding="utf-8") as fh:
            fh.write("(kicad_sch (version 20250114))\n")
        shutil.copyfile(BOARD, os.path.join(self.root, "esd.kicad_pcb"))
        for boards, said in ((("gone.kicad_pcb",), "no KiCad board at gone.kicad_pcb"),
                             (("power_board.kicad_pcb", "power_board.kicad_pcb"),
                              "is named twice"),
                             (("not_a.kicad_pcb",), "is not a board the power rules read"),
                             (("esd.kicad_pcb",), "nothing to draft on esd.kicad_pcb")):
            with self.subTest(said=said):
                proc, records, _summary = _draft(self.root, *boards)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn(said, proc.stderr)
                self.assertIsNone(records)


def _without(*refs: str):
    """A board edit: the parts ``refs`` taken off."""
    def edit(tree):
        tree[:] = [x for x in tree if not (isinstance(x, list) and K.head(x) == "footprint"
                                           and K.footprint_ref(x)[0] in refs)]
    return edit


def _join_net(tree, gone: str, into: str) -> None:
    """Every pad on the net ``gone`` moved onto the net ``into``."""
    code = _net_code(tree, into)
    for fp in K.children(tree, "footprint"):
        for pad in K.children(fp, "pad"):
            net = K.child(pad, "net")
            if net is not None and len(net) > 2 and str(net[2]) == gone:
                net[1:] = [code, K.Q(into)]


def _pin_names(tree, ref: str, names: dict) -> None:
    """``ref``'s pads renamed, ``{pad number: pin name}``."""
    for number, name in names.items():
        K.child(_pad(tree, ref, number), "pinfunction")[1] = K.Q(name)


#: Q1's pads named as a regulator's, on the power board with the cell's parts
#: taken off: its drains (5 and 6, both on FET_D) the output, a source the input,
#: the other source the ground, and J6's pin 1 moved onto FET_D.
def _q1_as_regulator(tree) -> None:
    _without("BT1", "U1", "U2", "F1")(tree)
    _pin_names(tree, "Q1", {"5": "VOUT", "6": "VOUT", "1": "VIN", "3": "GND"})
    _to_net(tree, "J6", "1", "FET_D")


class ADraftDraftsWhatTheCopperShows(_PowerCase):
    """What the review of P9 found the draft writing that the board does not
    show, each on the power board edited. A cell's return drives no conductor; a
    VBUS pad on no net puts no mating on a netless pad; two boards of one name
    keep each its own records; a regulator's output may span pins, one on two
    nets is named, and each rail is listed to keep or delete; and a data line's
    DO and CO are no protector's."""

    def test_a_cells_return_drives_nothing(self):
        """BT1's - on GND (no low-side switch): its pads carry no + or - name,
        and U1's and U2's VSS on GND show it is the return, so no connector's
        GND is drafted as a fault conductor. (What slipped through: j1_gnd,
        j6_gnd, j10_gnd and j11_gnd, each with its numbers to state.)"""
        board = self.board(lambda t: _join_net(t, "CELL_N", "GND"), stem="low")
        proc, records, summary = _draft(self.root, os.path.basename(board))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(sorted(records["pcb_conductors"]), ["j6_vsys"])
        self.assertEqual(sorted(records["pcb_matings"]), ["bt1", "j1", "j10", "j11", "j6"])
        self.assertIn("the copper shows its return on GND, where U1's VSS and U2's VSS sit",
                      records["pcb_batteries"]["bt1"]["terminals"]["neg"])
        self.assertTrue(_is_fill(records["pcb_batteries"]["bt1"]["terminals"]["neg"]))
        self.assertEqual(summary["keep_or_delete"], [])

    def test_a_return_nothing_shows_is_kept_and_said(self):
        """With the charger and the protector off too, nothing shows which pad
        is the -: both nets are kept, and keep_or_delete names what was drafted
        on them, since a conductor left out is one no rule asks for."""
        def edit(tree):
            _join_net(tree, "CELL_N", "GND")
            _without("U1", "U2")(tree)
        board = self.board(edit, stem="bare")
        proc, records, summary = _draft(self.root, os.path.basename(board))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(sorted(records["pcb_conductors"]),
                         ["j10_gnd", "j11_gnd", "j1_gnd", "j6_gnd", "j6_vsys"])
        self.assertEqual(len(summary["keep_or_delete"]), 1, summary["keep_or_delete"])
        said = summary["keep_or_delete"][0]
        self.assertIn("which of BT1's pads is the cell's -", said)
        self.assertIn("CELL_P, GND", said)
        for cid in records["pcb_conductors"]:
            self.assertIn(f"pcb_conductors.{cid}", said)

    def test_a_vbus_pad_on_no_net_drafts_no_mating(self):
        """J1's A4 on no net, and J6 off every source (pin 1 on VSYS, which no
        cell drives here, pin 2 on no net): J6 carries nothing, and no mating
        says it carries an empty name. (What slipped through: ``A | B - {""}``
        kept the empty name, so J6 and every connector with a netless pad was
        drafted a mating that "carries , ...".)"""
        def edit(tree):
            _without("BT1", "U1", "U2", "F1")(tree)
            _drop_net(tree, "J1", "A4")
            _drop_net(tree, "J6", "2")
        board = self.board(edit, stem="open_vbus")
        proc, records, summary = _draft(self.root, os.path.basename(board))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(sorted(records["pcb_matings"]), ["j1", "j10", "j11"])
        for row in summary["candidates"]:
            self.assertNotIn("carries ,", row["found_by"])

    def test_a_data_lines_outputs_are_no_cutoff_pair(self):
        """U2 with DI and CI beside its DO and CO, as an addressable LED or a line
        driver names its data and clock pins: it is drafted as no protector.
        (What slipped through, found while the survey for CUTOFF_PAIRS was
        written in review of P9: every such part was drafted as one.)"""
        board = self.board(lambda t: _pin_names(t, "U2", {"1": "DI", "3": "CI"}),
                           stem="led")
        proc, records, _summary = _draft(self.root, os.path.basename(board))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(sorted(records["pcb_protectors"]), ["f1", "u1"])

    def test_two_boards_of_one_name_keep_their_own_records(self):
        """a/main.kicad_pcb and b/main.kicad_pcb, two copies of the power board:
        each id starts with its folder, so neither board's records replace the
        other's. (What slipped through: both took the prefix main, and the
        second board's battery, protectors, receptacle and matings replaced the
        first's with no word.)"""
        text = K.dump(K.load_tree(POWER_BOARD)) + "\n"
        for side in ("a", "b"):
            os.makedirs(os.path.join(self.root, side))
            with open(os.path.join(self.root, side, "main.kicad_pcb"), "w",
                      encoding="utf-8") as fh:
                fh.write(text)
        proc, records, _summary = _draft(self.root, "a/main.kicad_pcb", "b/main.kicad_pcb")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual({k: v["board"] for k, v in records["pcb_batteries"].items()},
                         {"a_main_bt1": "a/main.kicad_pcb", "b_main_bt1": "b/main.kicad_pcb"})
        self.assertEqual(len(records["pcb_protectors"]), 6)
        self.assertEqual(len(records["pcb_matings"]), 10)
        self.assertEqual(sorted(records["pcb_usbc"]), ["a_main_j1", "b_main_j1"])
        self.assertIn("one of a_main_bt1, b_main_bt1",
                      records["pcb_protectors"]["a_main_u1"]["battery"])

    def test_the_prefixes_grow_until_each_is_its_own(self):
        proc = _env.run([sys.executable, "-c", _PREFIXES, os.path.join(PACK, "proposers")],
                        cwd=REPO)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), [
            ["power", "main"], ["a_main", "b_main"], ["main", "sub_main"],
            ["a_x_main", "b_x_main"], ["power_board", "power_board_2"]])

    def test_a_regulators_output_may_span_pins(self):
        """Q1 named as a regulator, its output on two pads of one net: a rail
        on FET_D, returning on GND, listed to keep or delete with J6, the mating
        drafted only for it. (What slipped through: one pad of each name was
        required, so a regulator whose output spans pins was skipped, and every
        rail read as sure as a battery's records.)"""
        board = self.board(_q1_as_regulator, stem="reg")
        proc, records, summary = _draft(self.root, os.path.basename(board))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rail = records["pcb_rails"]["q1_fet_d"]
        self.assertEqual((rail["net"], rail["return"]), ("FET_D", "GND"))
        self.assertTrue(_is_fill(rail["nominal_v"]))
        found = {row["record"]: row["found_by"] for row in summary["candidates"]}
        self.assertEqual(found["pcb_rails.q1_fet_d"], "pins VOUT (5, 6) and VIN (1)")
        self.assertEqual(len(summary["keep_or_delete"]), 1)
        self.assertIn("pcb_rails.q1_fet_d: Q1 (FDS6890A) was taken for a regulator by its "
                      "pin names alone", summary["keep_or_delete"][0])
        self.assertIn("the matings drafted only for FET_D (pcb_matings.j6)",
                      summary["keep_or_delete"][0])

    def test_a_regulator_on_two_nets_is_named(self):
        """The same, with pad 6 moved to a net of its own: no rail, and
        cannot_see names Q1 and both nets. (What slipped through: such a part
        was skipped and nothing said so.)"""
        def edit(tree):
            _q1_as_regulator(tree)
            _move_pad_net(tree, "Q1", "6", "OTHER")
        board = self.board(edit, stem="reg2")
        proc, records, summary = _draft(self.root, os.path.basename(board))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(records["pcb_rails"], {})
        self.assertIn("Q1 (FDS6890A) on reg2.kicad_pcb, a regulator by its pins VOUT (5, 6) "
                      "and VIN (1): its VOUT pins sit on FET_D, OTHER, so no pcb_rails record "
                      "is drafted for it", " ".join(summary["cannot_see"]))


#: A child that prints the proposer's id prefixes for sets of board paths.
_PREFIXES = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import battery_record_draft as D
print(json.dumps([D.prefixes(rels) for rels in (
    ["power.kicad_pcb", "main.kicad_pcb"], ["a/main.kicad_pcb", "b/main.kicad_pcb"],
    ["main.kicad_pcb", "sub/main.kicad_pcb"], ["a/x/main.kicad_pcb", "b/x/main.kicad_pcb"],
    ["power-board.kicad_pcb", "Power_Board.kicad_pcb"])]))
"""


DRAFT_RECORDS = ("pcb_batteries", "pcb_protectors", "pcb_conductors", "pcb_matings",
                 "pcb_usbc", "pcb_rails")

#: A child that prints the proposer's copies of the gates' connector and USB-C
#: tests, and its receptacle verdict on each footprint of each board named.
_COPIES = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import battery_record_draft as D
out = {"connector_libs": list(D.CONNECTOR_LIBS), "connector_ref": D._CONNECTOR_REF.pattern,
       "typec_vbus": list(D.TYPEC_VBUS), "typec_data": list(D.TYPEC_DATA),
       "typec_name": D._TYPEC_NAME.pattern, "typec_ref": D._TYPEC_CONNECTOR_REF.pattern,
       "found": {}}
for path in sys.argv[2:]:
    board = D.K.load_board(path)
    out["found"][path] = {fp.ref: D.typec_receptacle(fp) for fp in board.footprints}
print(json.dumps(out))
"""


if __name__ == "__main__":
    unittest.main()
