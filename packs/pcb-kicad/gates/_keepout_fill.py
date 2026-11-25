# SPDX-License-Identifier: Apache-2.0
"""``pcb.antenna_keepout``'s refill, run by the gate in a child process:

    python3 _keepout_fill.py <board.kicad_pcb> <refilled.kicad_pcb>

It loads the board with KiCad's own ``pcbnew`` (the project file beside it gives
the net classes' clearances, and the board file its zones' settings), refills
every zone with ``pcbnew.ZONE_FILLER``, as KiCad's "Fill All Zones" does, and
saves the copy. It prints one JSON line: ``{"zones": <count>, "filled": <bool>,
"bare_holes": [<pad uuid>, ...]}``.

**The holes with no copper.** ``bare_holes`` lists each non-plated pad that
KiCad flashes on no copper layer (``PAD::FlashLayer``): a hole no wider than its
drill, such as the stock ``MountingHole_2.2mm_M2``. KiCad's keep-out reports one
in an area that forbids pads ("NPTH pad of H1"), and the gate lists it apart: it
holds no copper. KiCad decides, not this pack's reading of the pad: an NPTH pad
wider than its drill carries a copper ring, and counts. (What slipped through,
review of P8: a mounting hole for a plastic standoff under the antenna read as
copper in the keep-out, and a sound board failed. Measured with pcbnew 9.0.9: the
stock hole flashes no layer; the same hole with a 3.0 mm pad flashes all four.)

**Why a refill at all.** ``kicad-cli pcb drc`` 9.0 judges a zone by the fill the
file holds. It has no option to refill first (``kicad-cli pcb drc --help``,
9.0.9), and neither has its gerber export. So a pour whose outline was moved into
an antenna's keep-out, and not refilled since, reads clean on the file, and the
next refill (KiCad's, or a fab tool's) pours copper there. (Measured on the
pack's own radio board with KiCad 9.0.9: the In2.Cu pour's outline moved under the
antenna, the stored fill left alone, DRC reads nothing; this refill pours it.)

**In a child, not in nopekit's process.** A crash here must read as the gate
erroring, never as the check run dying. Measured with pcbnew 9.0.9: a zone fill
of a board made in memory, before it was saved and loaded again, killed the
interpreter with SIGSEGV. The board here is always loaded from its file. It also
keeps KiCad's imports out of the gate's trace.

The interpreter is the one running nopekit (``sys.executable``), so it needs
``pcbnew`` importable there: Linux's distribution KiCad installs it for the system
Python; on macOS and Windows nopekit runs under KiCad's own Python
(``references/installing.md``).
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: _keepout_fill.py <board.kicad_pcb> <refilled.kicad_pcb>", file=sys.stderr)
        return 2
    src, dst = argv
    import pcbnew
    board = pcbnew.LoadBoard(src)
    copper = [layer for layer in range(pcbnew.PCB_LAYER_ID_COUNT)
              if pcbnew.IsCopperLayer(layer) and board.IsLayerEnabled(layer)]
    bare = sorted(pad.m_Uuid.AsString() for pad in board.GetPads()
                  if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH
                  and not any(pad.FlashLayer(layer) for layer in copper))
    zones = board.Zones()
    filled = bool(pcbnew.ZONE_FILLER(board).Fill(zones))
    pcbnew.SaveBoard(dst, board)
    print(json.dumps({"zones": len(zones), "filled": filled, "bare_holes": bare}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
