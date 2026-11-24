# SPDX-License-Identifier: Apache-2.0
"""The wheel ships every file that a bundled pack tracks.

``pyproject.toml`` maps ``packs/`` onto ``nopekit.bundled`` and names what ships
as package-data globs. A pack reads its baseline from its own directory, so a
file that no glob ships is a pack that is whole in a checkout and short of that
file in the wheel. What slipped through: the list named extensions, and none of
them was a KiCad board, an ngspice deck, a Modelica model or an OpenFOAM case
(whose files have no extension). So ``pip install nopekit`` shipped the
pcb-kicad, openmodelica, cfd-openfoam and aero-openfoam baselines without their
assets, and nothing said so (general-packs P2).

The check asks the question that setuptools answers, with the functions that
setuptools uses. ``build_py.find_data_files`` runs from the project root, expands
each pattern with ``glob(os.path.join(src_dir, pattern), recursive=True)``, keeps
the files, and drops what an ``exclude-package-data`` pattern matches with
``fnmatch``. ``fnmatch`` alone was rejected for the include side: its ``*``
matches a leading dot and glob's does not, so it reads a pack's ``.gitignore`` as
shipped under ``**/*`` when setuptools leaves it out. Measured on 2026-11-23 with
setuptools 68.1.2 (``pip wheel . --no-deps --no-build-isolation``): on the old
globs the wheel left out the same 39 tracked files that this check names, and on
the new globs it holds all 278 and nothing else under ``nopekit/bundled/``.
Measured again on 2026-11-24, in review of P2, after the OpenFOAM case globs were
anchored at the case root: the wheel holds the same 278. With a run left in
place in the spheroid case, ``**/0/*`` and ``**/system/*`` shipped its
``processor0/0/U``, ``processor1/system/decomposeParDict`` and
``postProcessing/forces/0/force.dat``, and the anchored globs ship none of them.

A glob ships nothing from a package that setuptools does not build, so the check
also reads ``[tool.setuptools] packages``: it must be a list that names
``nopekit.bundled``. What slipped through review of P2: the check read only the
globs, so a pyproject whose ``packages`` was ``["nopekit"]`` read green, and
setuptools 68.1.2 built from it a wheel with no file under ``nopekit/bundled/``.

What it does not see, and how each reads: with ``include-package-data``, a
``MANIFEST.in`` (there is none) can add files, and such a file reads unshipped
here; and a ``packages.find`` table finds what this check does not model, so it
is refused. Each is a false red, never a false green.

The list of files is ``git ls-files packs/``. Where git cannot answer (an
exported tree, ``git archive``, in which every file is committed), it is a walk
of ``packs/``. What slipped through review of P2: the class read git alone, so in
an exported tree, which the repository measures on (``tests/fast.py``), it went
red on ``fatal: not a git repository`` with no defect in the wheel.

``tomllib`` is in the standard library from Python 3.11. On 3.10 the class skips
and says so; CI's 3.12 and 3.13 legs walk it.
"""
from __future__ import annotations

import fnmatch
import glob
import os
import unittest

import _env
from nopekit import vcs

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None

PYPROJECT = os.path.join(_env.REPO, "pyproject.toml")
#: The package that the bundled packs ship as (``[tool.setuptools]``).
PACKAGE = "nopekit.bundled"


class Unbuilt(AssertionError):
    """``pyproject`` does not build :data:`PACKAGE`, so the wheel ships none of
    its files, whatever its globs say. An ``AssertionError``, so that unittest
    reads it as a failure of the check and not as an error in it."""


def bundled_spec(pyproject: str) -> tuple[str, list[str], list[str]]:
    """``(source directory, include globs, exclude globs)`` of :data:`PACKAGE`,
    read from ``pyproject``. Each list is what setuptools applies to the package:
    the entries under its own name, and those under ``""``, which apply to every
    package. Raises :class:`Unbuilt` when ``[tool.setuptools] packages`` is not
    a list that names :data:`PACKAGE`."""
    with open(pyproject, "rb") as fh:
        tool = tomllib.load(fh)["tool"]["setuptools"]
    packages = tool.get("packages")
    if not isinstance(packages, list):
        raise Unbuilt(
            f"{pyproject}: [tool.setuptools] packages is {packages!r}, not a list. A find "
            f"table scans only its `where`, so it never finds the mapped {PACKAGE!r}, and "
            f"this check does not model what it finds: list the packages by name")
    if PACKAGE not in packages:
        raise Unbuilt(
            f"{pyproject}: [tool.setuptools] packages {packages!r} does not name "
            f"{PACKAGE!r}, so setuptools does not build it and the wheel ships no pack "
            f"file, whatever the package-data globs say")

    def applied(table: dict) -> list[str]:
        return [*table.get("", []), *table.get(PACKAGE, [])]

    return (tool["package-dir"][PACKAGE], applied(tool.get("package-data", {})),
            applied(tool.get("exclude-package-data", {})))


def shipped(root: str, src_dir: str, include: list[str], exclude: list[str]) -> set[str]:
    """The ``root``-relative paths (``/``-separated) that setuptools ships from
    ``root/src_dir``. Globbed with ``root_dir=root``, so each path is relative as
    setuptools sees it, and nothing in ``root``'s own name is read as a
    wildcard."""
    found: list[str] = []
    for pattern in include:
        for rel in glob.glob(f"{src_dir}/{pattern}", root_dir=root, recursive=True):
            if os.path.isfile(os.path.join(root, rel)):
                found.append(rel.replace(os.sep, "/"))
    dropped: set[str] = set()
    for pattern in exclude:
        dropped.update(fnmatch.filter(found, f"{src_dir}/{pattern}"))
    return {rel for rel in found if rel not in dropped}


def unshipped(root: str, tracked: list[str], src_dir: str, include: list[str],
              exclude: list[str]) -> list[str]:
    """The paths in ``tracked`` that no glob ships from ``root``, sorted."""
    ships = shipped(root, src_dir, include, exclude)
    return sorted(rel for rel in tracked if rel not in ships)


def tracked(root: str, src_dir: str) -> list[str]:
    """The files under ``root/src_dir`` that git tracks (``git ls-files``),
    ``root``-relative and ``/``-separated, sorted. Where git cannot answer, or
    knows no file there (an exported tree, in which every file is committed),
    every file under it but those in ``__pycache__/`` and in a dot-directory
    (``.selftest-out/``, ``selftest/.generated/``), which a run in that tree
    writes and git ignores."""
    listed = vcs.ls_files(root, [src_dir], others=False)
    if listed:
        return listed
    found: list[str] = []
    for dirpath, dirs, files in os.walk(os.path.join(root, src_dir)):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
        found += [os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
                  for name in files]
    return sorted(found)


@unittest.skipIf(tomllib is None, "tomllib is in the standard library from Python 3.11: "
                 "not walked on this interpreter (CI's 3.12 and 3.13 legs walk it)")
class TheWheelShipsEveryPackFile(_env.EnvCase):
    """Every file that git tracks under ``packs/`` matches a bundled glob."""

    #: Name every unshipped path: unittest cuts a long list diff to its first.
    maxDiff = None

    def plant(self, paths: list[str], root: str | None = None) -> str:
        """An empty file at each of ``paths``, in ``root`` or a new temp tree."""
        root = root or self.tmp()
        for rel in paths:
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb"):
                pass
        return root

    def test_every_tracked_pack_file_matches_a_bundled_glob(self):
        src_dir, include, exclude = bundled_spec(PYPROJECT)
        files = tracked(_env.REPO, src_dir)
        self.assertTrue([rel for rel in files if rel.endswith("/pack.json")],
                        f"no pack found under {src_dir}/: the walk is vacuous")
        self.assertEqual(
            unshipped(_env.REPO, files, src_dir, include, exclude), [],
            f"these are tracked under {src_dir}/ and no package-data glob of {PACKAGE} "
            f"ships them, so a checkout has them and the wheel does not: "
            f"add a glob to pyproject.toml")

    def test_a_planted_unmatched_path_is_named(self):
        """V: the real globs over a copy of the tracked tree read clean; the same
        copy with one file that no glob ships names that file and nothing else."""
        src_dir, include, exclude = bundled_spec(PYPROJECT)
        files = tracked(_env.REPO, src_dir)
        root = self.plant(files)
        self.assertEqual(unshipped(root, files, src_dir, include, exclude), [])
        pack = sorted(rel for rel in files if rel.endswith("/pack.json"))[0]
        planted = f"{os.path.dirname(pack)}/selftest/assets/planted.not-a-pack-file"
        self.plant([planted], root)
        self.assertEqual(unshipped(root, [*files, planted], src_dir, include, exclude),
                         [planted])

    def test_a_star_never_reaches_a_dotfile(self):
        """V: under ``**/*`` a pack's ``.gitignore`` is unshipped, though
        ``fnmatch`` reads it as matched. That gap is why this check globs, and
        why ``**/.gitignore`` is a line of its own."""
        files = ["packs/p/a.py", "packs/p/.gitignore"]
        root = self.plant(files)
        self.assertTrue(fnmatch.fnmatch("packs/p/.gitignore", "packs/**/*"))
        self.assertEqual(unshipped(root, files, "packs", ["**/*"], []), ["packs/p/.gitignore"])
        self.assertEqual(unshipped(root, files, "packs", ["**/*", "**/.gitignore"], []), [])

    def test_a_case_ships_one_level_deep(self):
        """V: pyproject's OpenFOAM globs ship a case's dictionaries and fields,
        and nothing else a run left in place: not the mesh, a time directory, a
        processor directory's ``0/`` and ``system/`` or ``postProcessing/``'s
        time directories, which ``**/0/*`` and ``**/system/*`` shipped (review of
        P2). A case away from ``<pack>/selftest/assets/`` reads unshipped, so the
        check names it and it gets its own lines."""
        _, include, exclude = bundled_spec(PYPROJECT)
        case = "packs/p/selftest/assets/case"
        sources = [f"{case}/0/U", f"{case}/constant/transportProperties",
                   f"{case}/system/controlDict"]
        scratch = [f"{case}/constant/polyMesh/points", f"{case}/100/U",
                   f"{case}/processor0/0/U", f"{case}/processor1/system/decomposeParDict",
                   f"{case}/postProcessing/forces/0/force.dat"]
        elsewhere = ["packs/p/scaffold/case/0/U"]
        root = self.plant(sources + scratch + elsewhere)
        self.assertEqual(
            unshipped(root, sources + scratch + elsewhere, "packs", include, exclude),
            sorted(scratch + elsewhere))

    def test_an_excluded_file_is_named(self):
        """V: a file that ``exclude-package-data`` drops reads unshipped, though
        an include glob matches it; an entry under ``""`` applies too."""
        pyproject = os.path.join(self.tmp(), "pyproject.toml")
        with open(pyproject, "w", encoding="utf-8") as fh:
            fh.write('[tool.setuptools]\npackages = ["nopekit", "nopekit.bundled"]\n'
                     '[tool.setuptools.package-dir]\n"nopekit.bundled" = "packs"\n'
                     '[tool.setuptools.package-data]\n"" = ["**/*.md"]\n'
                     '"nopekit.bundled" = ["**/*.py"]\n'
                     '[tool.setuptools.exclude-package-data]\n'
                     '"nopekit.bundled" = ["**/scratch_*.py"]\n')
        src_dir, include, exclude = bundled_spec(pyproject)
        self.assertEqual((src_dir, include, exclude),
                         ("packs", ["**/*.md", "**/*.py"], ["**/scratch_*.py"]))
        files = ["packs/p/PACK.md", "packs/p/gates/g.py", "packs/p/gates/scratch_1.py"]
        root = self.plant(files)
        self.assertEqual(unshipped(root, files, src_dir, include, exclude),
                         ["packs/p/gates/scratch_1.py"])

    def test_a_package_not_built_is_refused(self):
        """V: pyproject's own text with ``packages`` cut to ``["nopekit"]``, or
        turned into a find table, is refused, though its globs still match every
        file. Each would build a wheel with no pack file in it."""
        with open(PYPROJECT, encoding="utf-8") as fh:
            text = fh.read()
        line = 'packages = ["nopekit", "nopekit.bundled"]\n'
        self.assertEqual(text.count(line), 1, "pyproject.toml's packages line moved")
        self.assertEqual(bundled_spec(PYPROJECT)[0], "packs")
        for cut, why in ((text.replace(line, 'packages = ["nopekit"]\n'), "does not name"),
                         (text.replace(line, 'packages = { find = { where = ["src"] } }\n'),
                          "not a list")):
            pyproject = os.path.join(self.tmp(), "pyproject.toml")
            with open(pyproject, "w", encoding="utf-8") as fh:
                fh.write(cut)
            with self.assertRaisesRegex(Unbuilt, why):
                bundled_spec(pyproject)

    def test_an_exported_tree_is_walked(self):
        """V: in a git repository the list is what git tracks, and an untracked
        file is not in it. A tree with no ``.git`` (an exported tree, in which
        every file is committed) lists every file, a ``.gitignore`` included,
        but none in ``__pycache__/`` or a dot-directory, which a run there
        writes."""
        files = ["packs/p/pack.json", "packs/p/.gitignore", "packs/p/gates/g.py"]
        scratch = ["packs/p/gates/__pycache__/g.cpython-312.pyc",
                   "packs/p/.selftest-out/report.txt",
                   "packs/p/selftest/.generated/bad.mo"]
        root = self.plant(files)
        self.assertEqual(_env.git(["init", "-q"], cwd=root).returncode, 0)
        self.assertEqual(_env.git(["add", "--", *files], cwd=root).returncode, 0)
        self.plant(["packs/p/untracked.txt"], root)
        self.assertEqual(tracked(root, "packs"), sorted(files))
        exported = self.plant(files + ["packs/p/untracked.txt"] + scratch)
        self.assertEqual(tracked(exported, "packs"), sorted(files + ["packs/p/untracked.txt"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
