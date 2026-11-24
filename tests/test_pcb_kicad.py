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
        fab_dir = os.path.join(PACK, "gates", "fab")
        for name in sorted(os.listdir(fab_dir)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(fab_dir, name), encoding="utf-8") as fh:
                fab = json.load(fh)
            for row in fab["tables"]:
                with self.subTest(fab=name, row=row["name"]):
                    rules = list(fab.get("common", [])) + list(row["rules"])
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
POWER_BOARD = os.path.join(ASSETS, "lipo_charger.kicad_pcb")
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


class ThePowerRulesReadTheirRecords(unittest.TestCase):
    """The three LiPo rules: IEC 60949's arithmetic, and records held to the board."""

    def setUp(self):
        self.registry = _registry()
        self.power = load_path(os.path.join(PACK, "gates", "power.py"))
        params = _baseline()
        for key in ("pcb_cell", "pcb_charger", "pcb_fuse", "pcb_conductor"):
            params[key] = dict(params[key], board=POWER_BOARD)
        params["pcb_cutoffs"] = [dict(r, board=POWER_BOARD) for r in params["pcb_cutoffs"]]
        self.params = params
        self.root = tempfile.mkdtemp(prefix="nopekit-pcb-power-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_the_formula_gives_iecs_k_factors(self):
        """k = K sqrt(ln((limit + beta) / (start + beta))): IEC 60364-5-54's table."""
        for start, limit, k in ((70, 160, 115.0), (90, 250, 143.1), (70, 140, 102.8),
                                (90, 160, 99.9)):
            with self.subTest(start=start, limit=limit):
                got = math.sqrt(self.power.adiabatic_s(1.0, 1.0, start, limit))
                self.assertAlmostEqual(got, k, delta=0.05)
        # references/power.md's worked point: 0.03 mm^2, 25 -> 85 C, 10 A.
        self.assertAlmostEqual(self.power.adiabatic_s(10.0, 0.03, 25, 85), 0.0956, places=4)

    def _run(self, gid: str, **records):
        params = dict(self.params, **records)
        return _run(self.registry, gid, self.root, params)

    def test_a_record_for_another_design_errors(self):
        for gid, key, change, said in (
                ("pcb.fuse_conductor", "pcb_fuse", {"value": "MF-MSMF050"},
                 "board's F1 is MF-MSMF075"),
                ("pcb.charge_protection", "pcb_charger", {"ref": "U9"}, "the board has no U9"),
                ("pcb.discharge_cutoff", "pcb_cell", {"pin": "7"}, "has no pad 7")):
            with self.subTest(gate=gid):
                verdict = self._run(gid, **{key: dict(self.params[key], **change)})
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn("record is for another design", verdict.error)
                self.assertIn(said, verdict.error)

    def _board(self, edit) -> str:
        tree = K.load_tree(POWER_BOARD)
        edit(tree)
        path = os.path.join(self.root, "edited.kicad_pcb")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(K.dump(tree) + "\n")
        return path

    def _repoint(self, path: str) -> dict:
        out = {k: dict(self.params[k], board=path)
               for k in ("pcb_cell", "pcb_charger", "pcb_fuse", "pcb_conductor")}
        out["pcb_cutoffs"] = [dict(r, board=path) for r in self.params["pcb_cutoffs"]]
        return out

    def test_therm_tied_to_ground_is_no_thermistor(self):
        def edit(tree):
            gnd = _net_code(tree, "GND")
            K.child(_pad(tree, "U1", "8"), "net")[1:] = [gnd, K.Q("GND")]
        verdict = self._run("pcb.charge_protection", **self._repoint(self._board(edit)))
        self.assertEqual(verdict.outcome, "fail", verdict.render())

    def test_a_fuse_beside_the_path_is_not_between(self):
        def edit(tree):
            K.child(_pad(tree, "J3", "1"), "net")[1:] = [_net_code(tree, "VCELL"), K.Q("VCELL")]
        verdict = self._run("pcb.fuse_conductor", **self._repoint(self._board(edit)))
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertIn("F1 is not between the cell and J3", verdict.detail)

    def test_a_cutoff_sensing_the_cells_return_never_counts(self):
        """In review: the series part was inferred, the cell's own connector J2
        (VCELL, GND) among the candidates, so a cutoff whose VDD sat on ground read
        as sensing the cell "through J2", and so did a record naming a VSS pin."""
        cut = self.params["pcb_cutoffs"][0]

        def vdd_on_gnd(tree):
            K.child(_pad(tree, "U2", "2"), "net")[1:] = [_net_code(tree, "GND"), K.Q("GND")]
        moved = self._repoint(self._board(vdd_on_gnd))
        for name, cutoffs in (
                ("VSS pin named", [dict(cut, sense_pin="3")]),
                ("series part is the cell", [dict(cut, series_ref="J2")]),
                ("series part is the cap", [dict(cut, series_ref="C1")]),
                ("no series part named", [dict(cut, series_ref=None)]),
                ("VDD on ground", [dict(c, series_ref=None) for c in moved["pcb_cutoffs"]])):
            with self.subTest(case=name):
                params = moved if name == "VDD on ground" else {}
                verdict = self._run("pcb.discharge_cutoff",
                                    **dict(params, pcb_cutoffs=cutoffs))
                self.assertEqual(verdict.outcome, "fail", verdict.render())
                self.assertIn("nothing cuts the cell off", verdict.detail)

    def test_a_part_not_fitted_is_not_counted(self):
        """In review: dnp on TH1, U2 or R1 left each rule passing."""
        for gid, ref in (("pcb.charge_protection", "TH1"), ("pcb.charge_protection", "U1"),
                         ("pcb.discharge_cutoff", "U2"), ("pcb.discharge_cutoff", "R1"),
                         ("pcb.fuse_conductor", "F1")):
            for attr in K.NOT_FITTED:
                with self.subTest(gate=gid, ref=ref, attr=attr):
                    def edit(tree):
                        K.child(_footprint(tree, ref), "attr").append(attr)
                    verdict = self._run(gid, **self._repoint(self._board(edit)))
                    self.assertEqual(verdict.outcome, "fail", verdict.render())
                    self.assertIn(f"{ref} (", verdict.detail)
                    self.assertIn("marked not fitted", verdict.detail)

    def test_an_ntc_with_one_end_open_senses_nothing(self):
        """In review: TH1's pad 2 on no net read as "two nets", a pass."""
        for name, edit in (
                ("no net", lambda t: _pad(t, "TH1", "2").remove(K.child(_pad(t, "TH1", "2"),
                                                                       "net"))),
                ("a net of its own", lambda t: _move_pad_net(t, "TH1", "2", "TH_FAR"))):
            with self.subTest(case=name):
                verdict = self._run("pcb.charge_protection", **self._repoint(self._board(edit)))
                self.assertEqual(verdict.outcome, "fail", verdict.render())
                self.assertIn("TH1's other pad", verdict.detail)

    def test_a_record_read_loosely_errors(self):
        """In review: "false" as a string read as a timer, a negative area squared
        away, and a cutoff with an empty source counted."""
        for gid, key, change, said in (
                ("pcb.charge_protection", "pcb_charger", {"safety_timer": "false"},
                 "give true or false"),
                ("pcb.fuse_conductor", "pcb_conductor", {"area_mm2": -0.081},
                 "area_mm2 must be above 0"),
                ("pcb.fuse_conductor", "pcb_fuse", {"trip_max_s": [[8.0, 0.0]]},
                 "must be above 0"),
                ("pcb.discharge_cutoff", "pcb_cutoffs", {"source": ""},
                 "pcb_cutoffs[0]'s source is empty"),
                ("pcb.discharge_cutoff", "pcb_cutoffs", {"series_ref": "R9"},
                 "the board has no R9")):
            with self.subTest(gate=gid, change=change):
                rec = self.params[key]
                rec = ([dict(rec[0], **change)] if isinstance(rec, list)
                       else dict(rec, **change))
                verdict = self._run(gid, **{key: rec})
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn(said, verdict.error)

    def test_a_board_the_guard_would_fail_errors(self):
        """A record may name a board pcb.board_readable never reads (pcb_board is
        another board), so each rule makes the guard's checks itself."""
        def newer(tree):
            K.child(tree, "version")[1] = "20991231"
        records = self._repoint(self._board(newer))
        for gid in ("pcb.charge_protection", "pcb.fuse_conductor", "pcb.discharge_cutoff"):
            with self.subTest(gate=gid):
                verdict = self._run(gid, **records)
                self.assertEqual(verdict.outcome, "error", verdict.render())
                self.assertIn("not one this reader can vouch for", verdict.error)
                self.assertIn("newer than", verdict.error)

    def test_a_pcm_alone_below_the_minimum_fails_and_says_so(self):
        cell = dict(self.params["pcb_cell"], min_v=3.0, pcm_cutoff_min_v=2.5)
        verdict = self._run("pcb.discharge_cutoff", pcb_cell=cell, pcb_cutoffs=[])
        self.assertEqual(verdict.outcome, "fail", verdict.render())
        self.assertEqual(verdict.measured, 2.5)
        self.assertIn("the cell's PCM opens at 2.5 V, below its 3 V minimum; no part on "
                      "the board cuts the cell off", verdict.detail)


if __name__ == "__main__":
    unittest.main()
