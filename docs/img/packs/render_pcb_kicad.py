# SPDX-License-Identifier: Apache-2.0
"""Draws README's pcb-kicad pictures from the pack's own known-good board.

    python3 docs/img/packs/render_pcb_kicad.py [--java PATH] [--jar PATH]

Two pictures, both of ``packs/pcb-kicad/selftest/assets/esd_guard.kicad_pcb``
(a TVS array between two four-pin headers, the board every pcb-kicad gate passes):

* ``pcb-kicad-esd-baseline.png``: the board as it is committed, drawn by KiCad's
  own 3D renderer (``kicad-cli pcb render``) at an angle, with the pack's 3D
  models;
* ``pcb-kicad-autoroute-proposal.png``: what ``pcb.autoroute`` proposes for the
  same board with every track removed first (``--rip-up``), drawn from the top.
  It needs Freerouting and Java (``--jar``, ``--java``, or ``$FREEROUTING_JAR``
  and ``$JAVA``), as the proposer does; without them only the first picture is
  drawn, and the script says so.

What slipped through: README showed one of the parent device's routed boards
(docs/ORIGINS.md) as this pack's picture, twice, from a render that is not in this
repository. Two reviewers read it as that device. Pictures made here from the
pack's own baseline show what the pack checks and nothing of anyone's design.

Measured with KiCad 9.0.9 and Freerouting 2.2.4 (two passes, 18 tracks, nothing
unrouted). The board, its project, its rules and its library tables are copied
to a temporary directory first, so nothing is written into the pack.
Rejected: ``--quality high`` for the top view (its shading draws a grey shadow of
the headers past the board's edge in an orthographic view); a transparent
background (the shadow under the board reads as a smudge on a dark page); the
copper as an SVG plot (no tool here turns an SVG into the PNG README shows).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
ASSETS = os.path.join(REPO, "packs", "pcb-kicad", "selftest", "assets")
AUTOROUTE = os.path.join(REPO, "packs", "pcb-kicad", "proposers", "autoroute.py")
STEM = "esd_guard"

#: The files the board needs beside it: its project (``${KIPRJMOD}`` resolves to
#: the project's folder, and the 3D models are named under it), its rules and the
#: library tables.
BESIDE = (f"{STEM}.kicad_pro", f"{STEM}.kicad_dru", f"{STEM}.kicad_sch",
          "fp-lib-table", "sym-lib-table")

#: ``kicad-cli pcb render`` arguments per picture. The angled view turns the board
#: -45 degrees about x and 45 about z, KiCad's documented isometric example, in
#: perspective with its high-quality shading; the top view uses basic shading
#: (see the module docstring) and a zoom that fills the frame with the board.
BASELINE_VIEW = ("--rotate", "-45,0,45", "--perspective", "--quality", "high",
                 "--zoom", "0.9")
PROPOSAL_VIEW = ("--side", "top", "--quality", "basic", "--zoom", "1.3")
SIZE = ("-w", "960", "-h", "600")


def _stage(dest: str) -> str:
    shutil.copy2(os.path.join(ASSETS, f"{STEM}.kicad_pcb"), dest)
    for name in BESIDE:
        shutil.copy2(os.path.join(ASSETS, name), dest)
    shutil.copytree(os.path.join(ASSETS, "3d"), os.path.join(dest, "3d"))
    return os.path.join(dest, f"{STEM}.kicad_pcb")


def _render(board: str, out: str, view: tuple[str, ...]) -> None:
    subprocess.run(["kicad-cli", "pcb", "render", "-o", out, *SIZE, *view,
                    "--background", "opaque", board],
                   check=True, stdout=subprocess.DEVNULL)


def _routed(board: str, scratch: str, java: str | None, jar: str | None) -> str | None:
    """The proposer's routed copy of ``board``, staged with a project of its own
    name so its 3D models resolve; None when Freerouting is not given."""
    if not jar:
        return None
    proposals = os.path.join(scratch, "proposal")
    os.makedirs(proposals)
    argv = [sys.executable, AUTOROUTE, "--board", board, "--rip-up", "--jar", jar]
    if java:
        argv += ["--java", java]
    subprocess.run(argv, check=True, env=dict(os.environ, NOPEKIT_PROPOSAL_DIR=proposals))
    stem = f"{STEM}_routed"
    shutil.copy2(os.path.join(proposals, f"{STEM}.routed.kicad_pcb"),
                 os.path.join(os.path.dirname(board), f"{stem}.kicad_pcb"))
    shutil.copy2(os.path.join(os.path.dirname(board), f"{STEM}.kicad_pro"),
                 os.path.join(os.path.dirname(board), f"{stem}.kicad_pro"))
    return os.path.join(os.path.dirname(board), f"{stem}.kicad_pcb")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--java", default=os.environ.get("JAVA"))
    parser.add_argument("--jar", default=os.environ.get("FREEROUTING_JAR"))
    parser.add_argument("--out-dir", default=HERE)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="nopekit-readme-pcb-") as scratch:
        work = os.path.join(scratch, "board")
        os.makedirs(work)
        board = _stage(work)
        out = os.path.join(args.out_dir, "pcb-kicad-esd-baseline.png")
        _render(board, out, BASELINE_VIEW)
        print(out)
        routed = _routed(board, scratch, args.java, args.jar)
        if routed is None:
            print("pcb-kicad-autoroute-proposal.png not drawn: give --jar (Freerouting) "
                  "and --java, or set FREEROUTING_JAR and JAVA")
            return 0
        out = os.path.join(args.out_dir, "pcb-kicad-autoroute-proposal.png")
        _render(routed, out, PROPOSAL_VIEW)
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
