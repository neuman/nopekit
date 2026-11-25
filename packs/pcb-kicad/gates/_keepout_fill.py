# SPDX-License-Identifier: Apache-2.0
"""``pcb.antenna_keepout``'s refill, run by the gate in a child process:

    python3 _keepout_fill.py <board.kicad_pcb> <refilled.kicad_pcb>

It loads the board with KiCad's own ``pcbnew`` (the project file beside it gives
the net classes' clearances, and the board file its zones' settings), refills
every zone with ``pcbnew.ZONE_FILLER``, as KiCad's "Fill All Zones" does, and
saves the copy. It prints one JSON line: ``{"zones": <count>, "filled": <bool>}``.

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
    zones = board.Zones()
    filled = bool(pcbnew.ZONE_FILLER(board).Fill(zones))
    pcbnew.SaveBoard(dst, board)
    print(json.dumps({"zones": len(zones), "filled": filled}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
