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
from nopekit.models import Ledger, ProjectMeta

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
        rules = K.dru_rules("(version 1)\n# a comment (with a paren\n"
                            "(rule edge (layer outer) (condition \"A.NetName == 'GND' && "
                            "(A.Type == 'Pad' || B.Type == 'Pad')\") "
                            "(constraint edge_clearance (min 0.05mm)))\n"
                            "(rule plated (condition \"A.isPlated()\") "
                            "(constraint hole_size (min 0.3mm)))\n"
                            "(rule open (constraint track_width (min 0.1mm)))\n")
        probes = K.probe_rules(rules)
        self.assertEqual([(p["kind"], p["what"], p["reads_b"]) for p in probes],
                         [("rule", "edge", False), ("rule", "plated", False),
                          ("rule", "open", False), ("property", "NetName", False),
                          ("property", "Type", False)])
        self.assertIn("(layer outer)", probes[0]["text"])
        # A rule that reads B is probed on the terms that read A alone.
        self.assertEqual(probes[0]["a_side"], "A.NetName == 'GND'")
        self.assertIn("(condition \"A.NetName == 'GND'\")", probes[0]["text"])
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
    """The baseline's params, every power record pointed at ``board``."""
    params = copy.deepcopy(_baseline())
    for key in ("pcb_batteries", "pcb_protectors", "pcb_conductors", "pcb_matings"):
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
        the reference. R3 (3k) stands for the pull-up here, moved onto THERM."""
        def pull_up(tree):
            K.child(_pad(tree, "R3", "1"), "net")[1:] = [_net_code(tree, "THERM"),
                                                         K.Q("THERM")]
            K.child(_pad(tree, "R3", "2"), "net")[1:] = [_net_code(tree, "VBUS"),
                                                         K.Q("VBUS")]

        def ratio(p):
            ct = p["pcb_protectors"]["charger"]["functions"]["charge_temp"]
            ct["sense"] = {"pin": "8", "kind": "ratio", "pullup_ref": "R3",
                           "pullup_tol_pct": 1, "cold_frac": [0.80, 0.82],
                           "hot_frac": [0.40, 0.42]}
        verdict = self.run_gate(self.GID, pull_up, ratio)
        self.assertIn(verdict.outcome, ("pass", "fail"), verdict.render())
        self.assertIsNotNone(verdict.measured)
        # R = R_pu f / (1 - f) at the corners: 3k at 0.80 to 0.82 is 11.9..13.8 kOhm
        # cold and 2.0..2.2 kOhm hot; both edges then lie inside -40..150 C.
        self.assertIn("charging allowed", verdict.detail)

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


if __name__ == "__main__":
    unittest.main()
