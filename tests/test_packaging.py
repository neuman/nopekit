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

What it does not see: with ``include-package-data``, a ``MANIFEST.in`` (there is
none) can add files. Such a file reads unshipped here: a false red, never a
false green.

``tomllib`` is in the standard library from Python 3.11. On 3.10 the class skips
and says so; CI's 3.12 and 3.13 legs walk it.
"""
from __future__ import annotations

import fnmatch
import glob
import os
import unittest

import _env

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None

PYPROJECT = os.path.join(_env.REPO, "pyproject.toml")
#: The package that the bundled packs ship as (``[tool.setuptools]``).
PACKAGE = "nopekit.bundled"


def bundled_spec(pyproject: str) -> tuple[str, list[str], list[str]]:
    """``(source directory, include globs, exclude globs)`` of :data:`PACKAGE`,
    read from ``pyproject``. Each list is what setuptools applies to the package:
    the entries under its own name, and those under ``""``, which apply to every
    package."""
    with open(pyproject, "rb") as fh:
        tool = tomllib.load(fh)["tool"]["setuptools"]

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


@unittest.skipIf(tomllib is None, "tomllib is in the standard library from Python 3.11: "
                 "not walked on this interpreter (CI's 3.12 and 3.13 legs walk it)")
class TheWheelShipsEveryPackFile(_env.EnvCase):
    """Every file that git tracks under ``packs/`` matches a bundled glob."""

    #: Name every unshipped path: unittest cuts a long list diff to its first.
    maxDiff = None

    def tracked(self, src_dir: str) -> list[str]:
        proc = _env.git(["ls-files", "-z", "--", src_dir], cwd=_env.REPO)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return [rel for rel in proc.stdout.split("\0") if rel]

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
        tracked = self.tracked(src_dir)
        self.assertTrue([rel for rel in tracked if rel.endswith("/pack.json")],
                        f"git tracks no pack under {src_dir}/: the walk is vacuous")
        self.assertEqual(
            unshipped(_env.REPO, tracked, src_dir, include, exclude), [],
            f"git tracks these under {src_dir}/ and no package-data glob of {PACKAGE} "
            f"ships them, so a checkout has them and the wheel does not: "
            f"add a glob to pyproject.toml")

    def test_a_planted_unmatched_path_is_named(self):
        """V: the real globs over a copy of the tracked tree read clean; the same
        copy with one file that no glob ships names that file and nothing else."""
        src_dir, include, exclude = bundled_spec(PYPROJECT)
        tracked = self.tracked(src_dir)
        root = self.plant(tracked)
        self.assertEqual(unshipped(root, tracked, src_dir, include, exclude), [])
        pack = sorted(rel for rel in tracked if rel.endswith("/pack.json"))[0]
        planted = f"{os.path.dirname(pack)}/selftest/assets/planted.not-a-pack-file"
        self.plant([planted], root)
        self.assertEqual(unshipped(root, [*tracked, planted], src_dir, include, exclude),
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
        and not the mesh or a time directory of a run left in place, as its
        comment says."""
        _, include, exclude = bundled_spec(PYPROJECT)
        case = "packs/p/selftest/assets/case"
        sources = [f"{case}/0/U", f"{case}/constant/transportProperties",
                   f"{case}/system/controlDict"]
        scratch = [f"{case}/constant/polyMesh/points", f"{case}/100/U"]
        root = self.plant(sources + scratch)
        self.assertEqual(unshipped(root, sources + scratch, "packs", include, exclude),
                         sorted(scratch))

    def test_an_excluded_file_is_named(self):
        """V: a file that ``exclude-package-data`` drops reads unshipped, though
        an include glob matches it; an entry under ``""`` applies too."""
        pyproject = os.path.join(self.tmp(), "pyproject.toml")
        with open(pyproject, "w", encoding="utf-8") as fh:
            fh.write('[tool.setuptools.package-dir]\n"nopekit.bundled" = "packs"\n'
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
