# SPDX-License-Identifier: Apache-2.0
"""The one mesh loader of the gates that read a part's mesh: ``gates/mesh.py`` and
``gates/slicer.py``, both of which declare trimesh and numpy.

Loaded by path (``nopekit.modelio.load_path``), as ``_process_model.py`` is, so two
gate modules get one module and so one loader function for the sweep's memo.
"""
from __future__ import annotations

import os


def parse_mesh(path: str):
    """``(mesh, defect, empty)`` for the file at ``path``, or None when there is none.

    The loader ``gates/mesh.py``'s ``_read_mesh`` and ``gates/slicer.py`` hand the
    spine's memo, so it runs once per file per sweep and every gate that asks
    after that gets the same answer. It lives here, not in a gate module, so both
    hand the memo one function: the slicer gate first carried a parser of its
    own, a second loader identity, and the sweep parsed every part twice. Not in
    ``fdm_print_parts.py``: every gate in the pack loads that file, and a trimesh
    import there is read as an import of ``fdm.bed_fit``, which declares none.
    ``defect`` is decided here, with the parse, because it is a property of the
    file and both gates want it. ``empty`` is the reason when the file parsed to
    no faces at all; ``mesh`` and ``defect`` are None then.

    Module-level on purpose: the memo is keyed on the loader's identity, and a
    function defined inside a gate body is a new object on every call, so it
    would never hit. A missing file comes back as None rather than raising: its
    absence is what the gate decides on, and asking through the memo is what
    records the path as this gate's input either way.
    """
    if not os.path.isfile(path):
        return None
    import trimesh                                    # noqa: PLC0415 - lazy on purpose

    loaded = trimesh.load_mesh(path, process=True)
    if isinstance(loaded, trimesh.Scene):
        try:
            loaded = loaded.dump(concatenate=True)
        except TypeError:                              # older trimesh
            loaded = trimesh.util.concatenate(loaded.dump())
    if getattr(loaded, "faces", None) is None or len(loaded.faces) == 0:
        return None, None, f"{os.path.basename(path)} loaded with no faces"
    return loaded, solid_defect(loaded, os.path.basename(path)), None


def solid_defect(mesh, name: str) -> str | None:
    """A one-line description of why this mesh is not a solid, or None.

    **Why this FAILS the gate rather than skipping it.** Every number both mesh
    gates produce is read off ``face_normals``, and a face normal only means
    "outward" if the winding says so. Flip the winding and the signed volume goes
    negative, every overhang angle comes out mirrored, and a part covered in 70
    deg overhangs reports ``worst face 0.0 deg`` — a confident green about a
    quantity that was never measured. That is not "the gate could not run", which
    is what a skip means; it is a defect in the artifact the project handed over,
    and it is exactly the kind of finding a readiness report exists to carry.

    The pack's own fixture builder has asserted this on the solids it generates
    since the day it was written, with a comment explaining that mirrored normals
    would make a control "work" for the wrong reason. The project's mesh gets the
    same check, for the same reason.

    ``is_winding_consistent`` is deliberately NOT part of the test: an STL is a
    soup of independent triangles, and a perfectly good solid comes back from the
    round trip with that flag false while ``is_watertight`` and a positive signed
    volume both hold. Testing it would fail honest meshes, and a gate that fails
    honest input gets switched off.
    """
    import numpy as np                                 # noqa: PLC0415

    if not mesh.is_watertight:
        edges = np.sort(mesh.edges, axis=1)
        _, counts = np.unique(edges, axis=0, return_counts=True)
        open_edges = int((counts != 2).sum())
        return (f"{name} is not watertight: {open_edges} edge(s) are not shared by "
                f"exactly two faces, so it has holes or self-intersects. Inside and "
                f"outside are undefined on such a mesh, which makes every face "
                f"normal — and so every overhang and bridge number below — "
                f"meaningless. Repair the mesh or re-export it as a solid")
    if mesh.volume <= 0:
        return (f"{name} has non-positive signed volume ({mesh.volume:.1f} mm^3): its "
                f"faces are wound inside out. Every overhang angle would come out "
                f"mirrored, so a part that is nothing but steep overhangs reports "
                f"0.0 deg and passes. Flip the normals (trimesh: fix_normals) and "
                f"re-export")
    return None
