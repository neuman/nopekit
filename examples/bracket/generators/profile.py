# SPDX-License-Identifier: Apache-2.0
"""The bracket's print package: what `nopekit export print-v1` hands whoever
prints it (P2.5b-D23).

`side_profile(ctx)` writes two files into `ctx.out_dir`:

* `bracket-profile.svg` — the plate seen from above (its outline and the bolt
  holes) and from the side (its thickness), at 1 svg unit per mm;
* `print-settings.txt` — what the slicer is told that the drawing cannot say:
  the material.

It reads the design ONLY through `ctx.params` — the projection, traced — so the
article this export builds is exactly what is read here: `arm_length`, `width`,
`thickness`, `hole_d`, `n_bolts`, `edge_margin` and `material`. Never `load_n`,
`bed_xy` or `safety_factor`: a change to the rated load does not change the
object printed, so it asks for no new print, and the rebuild prediction says so.
The material IS read, and written down: a print in PLA is another object than
one in PETG (critique 1 of the P2.5b design — a package that carried a value its
generator never read would be a change that moved no article).

Standard library only, like the model: the reference project must run on a
machine with nothing installed. Deterministic: two exports of one design are
byte-identical (no clock, no randomness, fixed number formats).
"""
from __future__ import annotations

import os


def _mm(value: float) -> str:
    return f"{float(value):.2f}"


def side_profile(ctx) -> None:
    arm = float(ctx.params["arm_length"])
    width = float(ctx.params["width"])
    thickness = float(ctx.params["thickness"])
    hole = float(ctx.params["hole_d"])
    bolts = int(ctx.params["n_bolts"])
    margin = float(ctx.params["edge_margin"])
    material = str(ctx.params["material"])

    plate = arm + margin + hole                       # the model's own footprint rule
    gap = 10.0                                         # mm between the two views
    cx = margin + hole / 2.0                           # hole centres, from the wall edge
    pitch = width / (bolts + 1) if bolts > 0 else 0.0
    holes = "".join(
        f'  <circle cx="{_mm(cx)}" cy="{_mm(pitch * (i + 1))}" r="{_mm(hole / 2.0)}" '
        f'fill="none" stroke="black" stroke-width="0.3"/>\n' for i in range(bolts))
    height = width + gap + thickness
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_mm(plate)}mm" '
        f'height="{_mm(height)}mm" viewBox="0 0 {_mm(plate)} {_mm(height)}">\n'
        f'  <title>wall bracket: plan, then side profile</title>\n'
        f'  <rect x="0" y="0" width="{_mm(plate)}" height="{_mm(width)}" fill="none" '
        f'stroke="black" stroke-width="0.3"/>\n'
        f"{holes}"
        f'  <rect x="0" y="{_mm(width + gap)}" width="{_mm(plate)}" '
        f'height="{_mm(thickness)}" fill="none" stroke="black" stroke-width="0.3"/>\n'
        f"</svg>\n")
    with open(os.path.join(ctx.out_dir, "bracket-profile.svg"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(svg)
    with open(os.path.join(ctx.out_dir, "print-settings.txt"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(f"material: {material}\n"
                 f"print the plate flat on the bed; bracket-profile.svg has its outline\n")
