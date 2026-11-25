# SPDX-License-Identifier: Apache-2.0
"""Draws README's cad-solid picture from the pack's own known-good assembly.

    python3 docs/img/packs/render_cad_solid.py [--out docs/img/packs/cad-solid-enclosure-baseline.png]

The picture is ``packs/cad-solid/selftest/baseline.json``'s four solids (housing,
cover, carriage, guide roller), read from the STL files the baseline names and
drawn where the baseline places them, with only the cover lifted. Nothing is
modelled here: the geometry is the selftest's, and the pack's gates pass on it.

What slipped through: README showed an exploded render of the parent device
(docs/ORIGINS.md), drawn by a script that is not in this repository. Two
reviewers read it as that device. A picture made here from the pack's own
baseline shows what the pack checks and nothing of anyone's design, and anyone
can draw it again.

Needs trimesh, NumPy and matplotlib (``pip install trimesh numpy matplotlib``).
Rejected: pyrender and VTK (an OpenGL context that a headless machine often lacks,
for three flat-faced parts and a roller); KiCad's or ParaView's renderers (not
installed with this pack's own dependencies).
"""
from __future__ import annotations

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
PACK = os.path.join(REPO, "packs", "cad-solid")
DEFAULT_OUT = os.path.join(HERE, "cad-solid-enclosure-baseline.png")

#: How far each part is lifted out of place, mm, along +z. Only the cover: lifted
#: 40 mm (more than the housing's 34 mm depth), it clears the rim and shows the
#: cavity. The carriage and the roller stay where the baseline puts them, seen
#: through the housing, so the picture shows the fit the gates judge (the 0.4 mm
#: running clearance per side, the roller on the floor). Rejected: lifting every
#: part (the carriage then floats above an empty tray, and the fit is gone).
LIFT_MM = {"cover": 40.0}

#: Colour and opacity per part. The housing and the cover are drawn translucent so
#: the parts inside show; the moving parts are opaque. Rejected: one colour for
#: every part (the carriage disappears against the housing floor).
STYLE = {"housing": ("#7d8fa3", 0.22), "cover": ("#9fb4c9", 0.30),
         "carriage": ("#e07b39", 1.0), "guide_roller": ("#3d4b5c", 1.0)}

#: The label of each part, in words (the selftest's names, with no underscore), and
#: where it sits: the corner of the part's box it is written at ("lo" or "hi" per
#: axis, or "mid"), then an offset in mm. Placed by hand once, at this view: a label
#: at each box's +x corner put the roller's name on the carriage.
LABEL = {"housing": ("housing", ("hi", "lo", "mid"), (4.0, 0.0, 0.0), "left"),
         "cover": ("cover (lifted)", ("hi", "lo", "mid"), (4.0, 0.0, 0.0), "left"),
         "carriage": ("carriage", ("mid", "lo", "hi"), (0.0, 0.0, 3.0), "center"),
         "guide_roller": ("guide roller", ("lo", "mid", "hi"), (-2.0, 0.0, 4.0), "right")}

#: An edge is drawn where its two faces meet at more than this angle, degrees, so a
#: box shows its corners and not the diagonals of its triangles, and the roller
#: (48 sides, 7.5 degrees apart) shows its two end rings only.
FEATURE_DEG = 20.0

#: One light from above and in front; each face is shaded by its normal against it,
#: between AMBIENT and 1. 0.45 keeps a face turned away from the light readable.
LIGHT = (-0.35, -0.55, 0.76)
AMBIENT = 0.45


def _meshes():
    import trimesh
    with open(os.path.join(PACK, "selftest", "baseline.json"), encoding="utf-8") as fh:
        baseline = json.load(fh)
    out = {}
    for name, rel in baseline["meshes"].items():
        mesh = trimesh.load(os.path.join(PACK, rel), force="mesh")
        mesh.apply_translation((0.0, 0.0, LIFT_MM.get(name, 0.0)))
        out[name] = mesh
    return out


def _shade(hex_colour: str, normals, alpha: float):
    import numpy as np
    from matplotlib.colors import to_rgb
    light = np.asarray(LIGHT) / np.linalg.norm(LIGHT)
    k = AMBIENT + (1.0 - AMBIENT) * np.clip(normals @ light, 0.0, 1.0)
    rgb = np.outer(k, to_rgb(hex_colour))
    return np.column_stack([rgb, np.full(len(k), alpha)])


def draw(out_path: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection

    meshes = _meshes()
    faces, colours, edges = [], [], []
    for name, mesh in meshes.items():
        colour, alpha = STYLE[name]
        faces.extend(mesh.vertices[mesh.faces])
        colours.extend(_shade(colour, mesh.face_normals, alpha))
        sharp = mesh.face_adjacency_angles > np.radians(FEATURE_DEG)
        edges.extend(mesh.vertices[mesh.face_adjacency_edges[sharp]])
    fig = plt.figure(figsize=(7.2, 4.8), dpi=150)
    ax = fig.add_subplot(projection="3d")
    # One collection for every face of every part, so matplotlib orders the faces
    # by depth across parts; one collection per part is ordered as a whole, and
    # the housing then drew over the carriage inside it.
    ax.add_collection3d(Poly3DCollection(faces, facecolors=colours, edgecolors="none"))
    ax.add_collection3d(Line3DCollection(edges, colors=[(0.12, 0.15, 0.2, 0.7)],
                                         linewidths=0.6))
    lo = np.min([m.bounds[0] for m in meshes.values()], axis=0)
    hi = np.max([m.bounds[1] for m in meshes.values()], axis=0)
    ax.set_xlim(lo[0], hi[0])
    ax.set_ylim(lo[1], hi[1])
    ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect(hi - lo)
    ax.view_init(elev=24, azim=-58)
    ax.set_axis_off()
    for name, mesh in meshes.items():
        words, corner, offset, align = LABEL[name]
        at = [{"lo": lo_, "hi": hi_, "mid": (lo_ + hi_) / 2}[c] + d
              for c, lo_, hi_, d in zip(corner, mesh.bounds[0], mesh.bounds[1], offset)]
        ax.text(*at, words, fontsize=9, color="#1f2933", ha=align)
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    fig.savefig(out_path, facecolor="white", bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return out_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=DEFAULT_OUT, help="the PNG to write")
    args = parser.parse_args(argv)
    print(draw(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
