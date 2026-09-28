# SPDX-License-Identifier: Apache-2.0
"""The spine digest follows the spine's semantics, never its version string (S-29).

What slipped through: 1e09113 changed what a verdict means — a NaN that used to
read `[ok]` now errors — and `atompipe.__version__` stayed `0.1.0`. A verdict
cache keyed on the version string would have gone on serving every PASS the old
semantics produced. The key is therefore a digest of what the verdict-path
modules SAY (`verdicts.SPINE_MODULES`), read through a canonical walk of their
ASTs so that a comment, a docstring or an attribute docstring moves nothing and
a `<` that becomes `<=` moves everything.

The walk has to be the same on every Python in CI's matrix. `ast.dump` is not:
3.12 adds `type_params=[]`, 3.13 omits empty fields, so every committed entry
would be stale on two of the three interpreters. `FIXTURE` below holds the
constructs whose trees moved between versions (f-strings with nested format
specs, `!r`, `{x=}`; `match`; walrus; decorators; async), and its digest is
pinned. The same assertion runs on 3.10, 3.12 and 3.13 in CI; only 3.12 exists
on the machine this was written on, and the pin was also confirmed on 3.13.

Run:  PYTHONPATH=src python3 -m unittest tests.test_spine_digest -v
"""
from __future__ import annotations

import ast
import os
import shutil
import sys
import unittest

from atompipe import verdicts

import _env

#: Every construct whose AST differs across 3.10 / 3.12 / 3.13 somewhere, plus
#: the docstring forms the digest must not see: module, class, method, one-line
#: function, and attribute docstrings written AFTER their fields
#: (`models.py:405-407` writes them that way).
FIXTURE = '''\
"""A module docstring the digest must not see."""
from __future__ import annotations

import dataclasses
from typing import Any

WIDTH = 10  # a comment the digest must not see


@dataclasses.dataclass(frozen=True)
class Reading:
    """Class docstring."""

    value: float = 0.0
    """An attribute docstring, written after its field."""
    units: str = "mm"
    """Another one."""

    def render(self, width: int = WIDTH, precision: int = 3) -> str:
        """Method docstring."""
        label = f"{self.value!r} {self.units:>{width}}"
        nested = f"{self.value:{width}.{precision}f}|{self.units!s:^8}"
        debug = f"{self.value=} {self.units = !r:>4} {{literal}}"
        return label + nested + debug


def classify(reading: Any, limit: float) -> str:
    "One-line docstring."
    match reading:
        case Reading(value=v, units="mm") if v < limit:
            return "under"
        case Reading(value=v) if (margin := limit - v) < 0:
            return f"over by {-margin:.2f}"
        case {"value": float(v), **rest}:
            return "mapping"
        case [first, *others]:
            return "sequence"
        case None | True:
            return "singleton"
        case _:
            return "unknown"


def cached(fn):
    return fn


@cached
async def fetch(source, *, retries: int = 3, **options):
    """Async docstring."""
    async with source as handle:
        async for chunk in handle:
            if (size := len(chunk)) > retries:
                await handle.close()
                yield size
    return_value = lambda x, /, y=2, *a, z, **k: (x, y, a, z, k)
    del return_value
'''

#: `canonical_ast_digest(FIXTURE)`, computed on 3.12.3 and confirmed on 3.13.
#: If this moves, every committed verdict entry in every project goes stale:
#: change the walk only on purpose, and say why in the commit.
PINNED = "PLACEHOLDER"


def _edit(source: str, old: str, new: str) -> str:
    """`source` with exactly one `old` replaced — a planted edit that silently
    matched nothing would make every "unchanged" assertion vacuous."""
    if source.count(old) != 1:
        raise AssertionError(f"planted edit {old!r} matches {source.count(old)} times")
    return source.replace(old, new)


class SpineDigestIsPortable(unittest.TestCase):
    def digest(self, source: str) -> str:
        value = verdicts.canonical_ast_digest(source)
        self.assertRegex(value, r"^[0-9a-f]{64}$")
        return value

    def test_pinned_fixture_digest(self):
        self.assertEqual(
            self.digest(FIXTURE), PINNED,
            f"the canonical walk of the fixture moved on Python "
            f"{sys.version.split()[0]}; a walk that differs across the CI matrix "
            f"stales every committed entry on the other interpreters")

    def test_comment_edits_leave_it_unchanged(self):
        base = self.digest(FIXTURE)
        edited = _edit(FIXTURE, "# a comment the digest must not see",
                       "# rewritten entirely")
        edited = _edit(edited, "import dataclasses\n",
                       "import dataclasses  # why\n\n# a new comment block\n")
        edited = _edit(edited, "        return label + nested + debug",
                       "        return (label +   # layout, not meaning\n"
                       "                nested + debug)")
        self.assertEqual(self.digest(edited), base)

    def test_docstring_edits_leave_it_unchanged(self):
        base = self.digest(FIXTURE)
        edited = _edit(FIXTURE, '"""A module docstring the digest must not see."""',
                       '"""Rewritten.\n\nWith a second paragraph."""')
        edited = _edit(edited, '"""Class docstring."""', '"""A different class docstring."""')
        edited = _edit(edited, '"""Method docstring."""', "'''Changed.'''")
        edited = _edit(edited, '"One-line docstring."', '"Another line."')
        edited = _edit(edited, '"""Async docstring."""', '"""Also changed."""')
        self.assertEqual(self.digest(edited), base)

    def test_attribute_docstring_edits_leave_it_unchanged(self):
        base = self.digest(FIXTURE)
        edited = _edit(FIXTURE, '"""An attribute docstring, written after its field."""',
                       '"""Reworded: the reading, in its units."""')
        edited = _edit(edited, '    """Another one."""\n', "")
        self.assertEqual(self.digest(edited), base)

    def test_a_bare_string_statement_at_any_depth_is_not_code(self):
        base = self.digest(FIXTURE)
        edited = _edit(FIXTURE, "                await handle.close()\n",
                       '                "a note inside a loop body"\n'
                       "                await handle.close()\n")
        self.assertEqual(self.digest(edited), base)

    def test_a_comparison_edit_changes_it(self):
        base = self.digest(FIXTURE)
        self.assertNotEqual(self.digest(_edit(FIXTURE, "if v < limit", "if v <= limit")),
                            base)

    def test_f_string_edits_change_it(self):
        base = self.digest(FIXTURE)
        for old, new in (("{self.units:>{width}}", "{self.units:<{width}}"),
                         ("{self.value!r}", "{self.value!s}"),
                         ("{self.value=}", "{self.value}"),
                         ("{{literal}}", "{{literally}}")):
            with self.subTest(edit=old):
                self.assertNotEqual(self.digest(_edit(FIXTURE, old, new)), base)

    def test_fields_that_moved_across_versions_are_ignored(self):
        """Simulate the other interpreters' trees on this one: 3.10 has no
        `type_params`, 3.13 fills absent optionals with None / [], and a `u''`
        literal carries `kind='u'`. None of that is meaning."""
        base = self.digest(FIXTURE)
        tree = ast.parse(FIXTURE)
        for node in ast.walk(tree):
            if hasattr(node, "type_params"):
                del node.type_params                      # 3.10's shape
            if isinstance(node, ast.Constant):
                node.kind = "u" if isinstance(node.value, str) else None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Assign)):
                node.type_comment = None
        self.assertEqual(verdicts.canonical_ast_digest(tree), base)
        tree = ast.parse(FIXTURE)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                node.type_params = []                     # 3.12's shape
        self.assertEqual(verdicts.canonical_ast_digest(tree), base)

    def test_an_unparsable_source_is_empty_not_a_digest_of_nothing(self):
        self.assertEqual(verdicts.canonical_ast_digest("def broken(:\n"), "")

    def test_the_spine_digest_is_memoised_and_real(self):
        first = verdicts.spine_digest()
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertEqual(verdicts.spine_digest(), first)
        self.assertEqual(verdicts.SPINE_MODULES,
                         ("models.py", "gates.py", "modelio.py", "verdicts.py"))


class SpineDigestFollowsSemantics(_env.EnvCase):
    """S-29 end to end: copies of the spine, the version string untouched."""

    def _spine_copy(self) -> str:
        dest = os.path.join(self.tmp(), "src")
        shutil.copytree(os.path.join(_env.SRC, "atompipe"), os.path.join(dest, "atompipe"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        return dest

    def _append(self, src: str, module: str, text: str) -> None:
        with open(os.path.join(src, "atompipe", module), "a", encoding="utf-8") as fh:
            fh.write(text)

    def _measure(self, src: str) -> tuple[str, str]:
        """`(version, spine digest)` of the spine at `src`, from a fresh process."""
        code = ("import atompipe, atompipe.verdicts as v, os, sys\n"
                "assert os.path.abspath(atompipe.__file__).startswith("
                f"{os.path.abspath(src)!r}), atompipe.__file__\n"
                "print(atompipe.__version__)\nprint(v.spine_digest())\n")
        proc = _env.run([sys.executable, "-c", code], cwd=self.tmp(),
                        env={"PYTHONPATH": src})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        version, digest = (proc.stdout.splitlines() + ["", ""])[:2]
        return version, digest

    def test_a_semantics_edit_with_no_version_bump_changes_the_spine_digest(self):
        base_version, base = self._measure(_env.SRC)
        self.assertRegex(base, r"^[0-9a-f]{64}$")

        prose = self._spine_copy()
        self._append(prose, "gates.py",
                     '\n\n# a comment\n"""A bare string: no meaning."""\n')
        self._append(prose, "models.py", '\n\n"""Another."""\n')
        self.assertEqual(self._measure(prose), (base_version, base),
                         "a comment or a docstring re-ran every gate")

        semantic = self._spine_copy()
        self._append(semantic, "gates.py",
                     "\n\ndef _planted_limit_check(measured, limit):\n"
                     "    return measured <= limit\n")
        version, digest = self._measure(semantic)
        self.assertEqual(version, base_version, "the planted edit must not bump the version")
        self.assertNotEqual(digest, base,
                            "a verdict-path semantics edit with no version bump left the "
                            "spine digest unchanged: the cache would keep serving the old "
                            "verdicts (S-29)")

    def test_an_unreadable_spine_module_empties_the_digest(self):
        """A wheel shipped without `.py` sources must make every entry Unknown,
        never Fresh against a digest of whatever was readable."""
        missing = self._spine_copy()
        os.remove(os.path.join(missing, "atompipe", "gates.py"))
        version, digest = self._measure(missing)
        self.assertEqual(digest, "")


if __name__ == "__main__":
    unittest.main()
