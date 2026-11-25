# SPDX-License-Identifier: Apache-2.0
"""Write ``kicad10/``, the baseline as KiCad 10 saves it, and a second board that
holds what the baseline does not (inner layers, a stated stackup, every via kind,
a filled pour and a keep-out). Not a gate and not run by any test: a record of
how the committed files were made, so a reader can make them again and diff.

Run it under KiCad 10's own Python and kicad-cli, from the repository root:

    docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:$PWD" -w "$PWD" \\
        kicad/kicad:10.0 python3 packs/pcb-kicad/selftest/assets/make_kicad10.py

Made with the kicad/kicad:10.0 image's KiCad 10.0.8 (``kicad-cli version``).

**Why these files exist.** The pack's reader (``gates/_kicad.py``) was written
where only KiCad 9.0.9 was installed, so its ceiling (``MAX_BOARD_VERSION``) stood
at KiCad 9's board format and every board KiCad 10 saved was skipped. KiCad 10
is the current release: in the holdout run on outside designs, two of the four
were KiCad 10 boards, and every pcb-kicad gate skipped or errored on them. The
ceiling moves only on a file KiCad 10 itself wrote, read by the reader the same
way as the KiCad 9 file it came from (``tests/test_pcb_kicad.py``,
``AKiCad10BoardReadsAsItsKiCad9Original``):

- ``kicad10/esd_guard.*``: the baseline's board, schematic, project file and
  custom rules, each upgraded by ``kicad-cli pcb upgrade`` / ``sch upgrade``
  (board format 20260206, schematic 20260306). Its library tables name the
  pack's own copies one directory up (``${KIPRJMOD}/../lib``), so the gates must
  stage a library the table names outside the board's own directory but inside
  the project. Under kicad-cli 10 it passes DRC, ERC and parity at every
  severity; under kicad-cli 9 every one of them is a skip that names the release
  (kicad-cli 9.0.9 exits 3, "Failed to load board", on a 20260206 file).
- ``kicad10/reader.kicad_pcb``: the baseline turned to four copper layers with
  a stated stackup (FR4 prepreg, an FR408-HR core), one blind via (F.Cu to
  In1.Cu), one buried (In1.Cu to In2.Cu) and one microvia (F.Cu to In1.Cu)
  beside its through via, a GND pour on In1.Cu filled by KiCad 10's own zone
  filler, and a keep-out rule area on every copper layer. Nothing judges it; it
  is a reader fixture only. The inner layers and the stackup are written into a
  KiCad 9 copy first (pcbnew 10.0.8 does not rebuild a stackup when a script
  changes the copper layer count: measured, it kept the two-layer stackup), and
  KiCad 10 then loads that copy, adds the rest and saves it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "kicad10")
sys.dont_write_bytecode = True        # never leave a __pycache__ in the pack it describes
sys.path.insert(0, HERE)
import make_libraries as L                                         # noqa: E402

#: The baseline's own files, upgraded as they are.
PROJECT = (".kicad_pcb", ".kicad_sch", ".kicad_pro", ".kicad_dru")

#: The two inner copper layers, in KiCad 9's layer numbering (F.Cu 0, B.Cu 2,
#: In1.Cu 4, In2.Cu 6), and the four-layer stackup, written into the KiCad 9 copy.
INNER = '\t\t(4 "In1.Cu" signal)\n\t\t(6 "In2.Cu" signal)\n'
STACKUP = """\t\t(stackup
\t\t\t(layer "F.SilkS" (type "Top Silk Screen"))
\t\t\t(layer "F.Paste" (type "Top Solder Paste"))
\t\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))
\t\t\t(layer "F.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 1" (type "prepreg") (thickness 0.1) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
\t\t\t(layer "In1.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 2" (type "core") (thickness 1.24) (material "FR408-HR") (epsilon_r 3.7) (loss_tangent 0.01))
\t\t\t(layer "In2.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 3" (type "prepreg") (thickness 0.1) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
\t\t\t(layer "B.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))
\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))
\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))
\t\t\t(copper_finish "None")
\t\t\t(dielectric_constraints no)
\t\t)
"""

#: (kind, (x, y) mm, top layer, bottom layer, size mm, drill mm), all on GND,
#: in the strip below the VBUS track where the baseline has no copper.
VIAS = [
    (pcbnew.VIATYPE_BLIND, (10.0, 13.5), pcbnew.F_Cu, pcbnew.In1_Cu, 0.6, 0.3),
    (pcbnew.VIATYPE_BURIED, (13.0, 13.5), pcbnew.In1_Cu, pcbnew.In2_Cu, 0.6, 0.3),
    (pcbnew.VIATYPE_MICROVIA, (16.0, 13.5), pcbnew.F_Cu, pcbnew.In1_Cu, 0.3, 0.1),
]

#: The GND pour on In1.Cu and the keep-out, as outlines in mm.
POUR = [(1.0, 1.0), (29.0, 1.0), (29.0, 14.0), (1.0, 14.0)]
KEEPOUT = [(20.0, 12.5), (24.0, 12.5), (24.0, 14.5), (20.0, 14.5)]


def mm(v: float) -> int:
    return pcbnew.FromMM(v)


def _outline(zone, points) -> None:
    outline = zone.Outline()
    outline.NewOutline()
    for x, y in points:
        outline.Append(mm(x), mm(y))


def _tidy() -> None:
    """kicad-cli leaves local settings and backups beside what it saved: per-user
    state, never part of the fixture."""
    for name in os.listdir(OUT):
        if name.endswith((".kicad_prl", ".bak", "-backups", ".lck")) or name.startswith("_autosave"):
            path = os.path.join(OUT, name)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)


def project() -> None:
    os.makedirs(OUT, exist_ok=True)
    for ext in PROJECT:
        shutil.copyfile(os.path.join(HERE, "esd_guard" + ext), os.path.join(OUT, "esd_guard" + ext))
    for kind, ext in (("pcb", ".kicad_pcb"), ("sch", ".kicad_sch")):
        subprocess.run(["kicad-cli", kind, "upgrade", os.path.join(OUT, "esd_guard" + ext)],
                       check=True)
    L.write_tables("${KIPRJMOD}/../lib", OUT)


def reader() -> None:
    with open(os.path.join(HERE, "esd_guard.kicad_pcb"), encoding="utf-8") as fh:
        text = fh.read()
    for anchor, add in (('\t\t(0 "F.Cu" signal)\n', INNER), ("\t(setup\n", STACKUP)):
        if text.count(anchor) != 1:
            raise SystemExit(f"the baseline no longer has one {anchor.strip()!r}")
        text = text.replace(anchor, anchor + add, 1)
    with tempfile.TemporaryDirectory() as tmp:
        four = os.path.join(tmp, "four.kicad_pcb")
        with open(four, "w", encoding="utf-8") as fh:
            fh.write(text)
        board = pcbnew.LoadBoard(four)
    gnd = board.FindNet("GND")
    for kind, (x, y), top, bottom, size, drill in VIAS:
        via = pcbnew.PCB_VIA(board)
        via.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        via.SetViaType(kind)
        via.SetLayerPair(top, bottom)
        via.SetWidth(mm(size))
        via.SetDrill(mm(drill))
        via.SetNet(gnd)
        board.Add(via)
    pour = pcbnew.ZONE(board)
    pour.SetLayer(pcbnew.In1_Cu)
    pour.SetNet(gnd)
    _outline(pour, POUR)
    board.Add(pour)
    keepout = pcbnew.ZONE(board)
    keepout.SetIsRuleArea(True)
    keepout.SetDoNotAllowTracks(True)
    keepout.SetDoNotAllowVias(True)
    keepout.SetDoNotAllowZoneFills(True)
    keepout.SetLayerSet(pcbnew.LSET.AllCuMask(4))
    _outline(keepout, KEEPOUT)
    board.Add(keepout)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(os.path.join(OUT, "reader.kicad_pcb"), board)
    # SaveBoard writes a project file beside the board; a reader fixture has none.
    os.remove(os.path.join(OUT, "reader.kicad_pro"))


def main() -> int:
    project()
    reader()
    _tidy()
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
