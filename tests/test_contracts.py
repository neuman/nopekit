# SPDX-License-Identifier: Apache-2.0
"""Contracts move with the code (PLAN R-14): the documents an agent reads first
must describe the spine that exists.

What slipped through without this: `docs/SPINE_CONTRACT.md` had no heading at all
for `models.py` — the module it tells every reader to read first — or for
`site.py`, and 42 names in the other modules' `__all__` were in no section. The
state.json example in SITE_CONTRACT never listed `locator_problems`, and
PACK_FORMAT never listed the fields a gate can set on a `Verdict`. Each of those
is a fresh context window reading a map of a smaller spine than the one it is
about to edit, and nothing turned red when the map fell behind (the drift behind
S-10, S-11 and S-41).

Five checkers, each a PURE function of text inputs so each violation test plants
a string instead of editing the tree:

    SpineModulesAreDocumented   every src/atompipe/*.py has its ### `<module>.py`
                                heading, and every name in its __all__ is in
                                that section, in code form
    RecordFieldsAreDocumented   every field of the record kinds sits in its own
                                `class <Kind>` block in SPINE_CONTRACT
    PackSurfaceIsDocumented     GateSpec / NegativeControl / Verdict fields and
                                properties, GateContext fields and methods, each
                                in its own `class` block in PACK_FORMAT
    SiteStateKeysAreDocumented  a real `site init` + `site build` on a copy of
                                the bracket; every top-level state.json key and
                                every file under site/data/ is in SITE_CONTRACT
    ClosedRowsResolve           a PLAN §3 row marked closed names tests that exist

**One direction only during Phase 1: code ⊆ docs.** Inside a phase the unit that
owns a contract document writes the other units' surfaces into it before their
code merges, so a documented name with no code yet must stay green. The reverse
direction — a contract describing a spine that no longer exists — lands with the
Phase 1 commit, as `DocumentedNamesExist` and `RemovedNamesAreGone`.

"Appears" is deliberately stronger than a substring anywhere in the file. A
field called `id`, `note` or `kind` appears in every contract document by
accident, so a whole-file search would pass a record nobody documented. A name
counts only in CODE FORM (a fenced block or an inline code span) inside its own
module's section; a field counts only inside its own record's `class` block.

Run:  PYTHONPATH=src python3 -m unittest tests.test_contracts -v
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest

from atompipe import cli as cli_mod
from atompipe import gates as gates_mod

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPINE_SRC = os.path.join(REPO, "src", "atompipe")
TESTS_DIR = os.path.join(REPO, "tests")
DOCS_DIR = os.path.join(REPO, "docs")
SPINE_CONTRACT = os.path.join(DOCS_DIR, "SPINE_CONTRACT.md")
PACK_FORMAT = os.path.join(DOCS_DIR, "PACK_FORMAT.md")
SITE_CONTRACT = os.path.join(DOCS_DIR, "SITE_CONTRACT.md")
PLAN = os.path.join(DOCS_DIR, "PLAN.md")
BRACKET = os.path.join(REPO, "examples", "bracket")

#: Modules that carry no public surface of their own: the package marker and the
#: `python -m atompipe` shim. Everything else under src/atompipe/ is a spine
#: module and owes the contract a heading.
NOT_SPINE_MODULES = frozenset({"__init__.py", "__main__.py"})

#: The record kinds (PLAN R-14). These are the shapes Phase 1.3
#: turns into files a human edits by hand, so their fields are the ones a reader
#: of the contract must be able to find. `Verdict`, `GateSpec` and
#: `NegativeControl` are checked against PACK_FORMAT instead — the pack author is
#: their reader — so they have exactly one documented home.
RECORD_KINDS = (
    "Claim", "Acceptance", "Param", "Rejected", "Decision", "Need",
    "InputArtifact", "Extraction", "PhysicalResult", "View", "ProjectMeta",
)

#: What PACK_FORMAT owes a pack author, class by class: `(where the class is
#: defined, class name, member groups)`. GateContext's FIELDS are checked as well
#: as its methods — stronger than R-14's list, because a gate reads `ctx.params`
#: and `ctx.out_dir` as often as it calls `ctx.param()`, and an undocumented
#: field is exactly as invisible as an undocumented method.
PACK_SURFACE = (
    ("models.py", "GateSpec", ("fields",)),
    ("models.py", "NegativeControl", ("fields",)),
    ("models.py", "Verdict", ("fields", "properties")),
    ("gates.py", "GateContext", ("fields", "methods")),
)

#: The gap map at 7ecf953 holds 74 rows. The floor sits below that so a later
#: phase may merge or retire rows without touching this file, and far enough above
#: zero that a parser which stopped seeing a table (a renamed `Ph` header, an
#: unescaped pipe) fails here instead of reporting "no closed rows, all green".
#: Rejected: pinning 74 exactly (every honest edit to the map would turn this red),
#: and no floor (the checker could go vacuous and nobody would know).
MIN_GAP_MAP_ROWS = 60

_WORD = r"(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])"
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADING = re.compile(r"^(#{1,6})\s")


# --------------------------------------------------------------------------- #
# markdown, read the way the checkers need it
# --------------------------------------------------------------------------- #
def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _lines_outside_fences(md: str):
    """Yield `(line, in_fence)` for every line, tracking fenced blocks.

    A heading inside a code block is not a heading: SPINE_CONTRACT's fences hold
    Python, and a column-0 `# comment` there would otherwise end a section early
    and hide every name after it.
    """
    fence = ""
    for line in md.splitlines():
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)
            if not fence:
                fence = marker[0] * len(marker)
                yield line, True
                continue
            if line.strip().startswith(fence):
                fence = ""
                yield line, True
                continue
        yield line, bool(fence)


def md_section(md: str, heading: str) -> str | None:
    """The text under the first heading matching the regex `heading`, up to the
    next heading of the same or a higher level. None when no heading matches —
    never "", which a caller could read as a section that documents nothing."""
    out: list[str] | None = None
    level = 0
    for line, in_fence in _lines_outside_fences(md):
        match = None if in_fence else _HEADING.match(line)
        if out is None:
            if match and re.match(heading, line):
                out, level = [], len(match.group(1))
            continue
        if match and len(match.group(1)) <= level:
            break
        out.append(line)
    return None if out is None else "\n".join(out)


def code_text(md: str) -> str:
    """Only the parts of `md` that are code: fenced blocks and inline spans.

    A name that appears only in prose ("the build is ...") is not documented;
    it is a coincidence of English. Everything this file checks is a code name,
    so it is looked for where code is written.
    """
    out: list[str] = []
    for line, in_fence in _lines_outside_fences(md):
        if in_fence:
            if not _FENCE.match(line):
                out.append(line)
        else:
            out.extend(re.findall(r"`([^`\n]+)`", line))
    return "\n".join(out)


def class_block(md: str, name: str) -> str | None:
    """Every `class <name>` chunk in the fenced code of `md`, joined; else None.

    A chunk is the `class` line and the lines indented under it, the way Python
    reads a class body — so the fields listed for `Claim` cannot be satisfied by
    the fields listed for `Decision` two lines further down. A one-line
    `class Tier(IntEnum): INSTANT=0 ...` is a chunk of one line.
    """
    head = re.compile(r"^(\s*)class\s+" + re.escape(name) + r"(?![A-Za-z0-9_])")
    fenced = [line for line, in_fence in _lines_outside_fences(md)
              if in_fence and not _FENCE.match(line)]
    chunks: list[str] = []
    i = 0
    while i < len(fenced):
        match = head.match(fenced[i])
        if not match:
            i += 1
            continue
        indent = len(match.group(1))
        chunk = [fenced[i]]
        i += 1
        while i < len(fenced):
            line = fenced[i]
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                break
            chunk.append(line)
            i += 1
        chunks.append("\n".join(chunk))
    return "\n".join(chunks) if chunks else None


def mentions(text: str, name: str) -> bool:
    """`name` as a whole identifier in `text` (so `set_pack` never satisfies `pack`)."""
    return re.search(_WORD.format(re.escape(name)), text) is not None


# --------------------------------------------------------------------------- #
# python source, read without importing it
# --------------------------------------------------------------------------- #
def public_names(source: str, filename: str = "<module>") -> tuple[list[str], str]:
    """`(names, problem)`: the module's `__all__`, else its public top-level defs.

    Read from the AST, never by importing: the checker has to see the module
    exactly as a reader of its text would. An `__all__` that is not a literal
    list of strings is a PROBLEM rather than a fallback, because falling back
    would quietly check a different, smaller list than the one the module
    exports.
    """
    tree = ast.parse(source, filename=filename)
    names: list[str] | None = None
    for node in tree.body:
        targets: list[ast.expr] = []
        value = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets, value = [node.target], node.value
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        try:
            literal = ast.literal_eval(value) if value is not None else None
        except ValueError:
            literal = None
        if not isinstance(literal, (list, tuple)) or not all(
                isinstance(x, str) for x in literal):
            return [], (f"{filename}: __all__ is not a literal list of strings, so "
                        f"what it exports cannot be read without running it")
        names = (names or []) + list(literal) if isinstance(node, ast.AugAssign) \
            else list(literal)
    if names is not None:
        return names, ""
    defs = [node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not node.name.startswith("_")]
    return defs, ""


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    out = set()
    for deco in node.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        if isinstance(target, ast.Name):
            out.add(target.id)
        elif isinstance(target, ast.Attribute):
            out.add(target.attr)
    return out


def class_members(source: str, name: str) -> dict[str, list[str]] | None:
    """Public `fields`, `properties` and `methods` of top-level class `name`.

    Fields are the annotated assignments of the class body (the dataclass
    fields), minus `ClassVar`s; properties are defs decorated `property` or
    `cached_property`; methods are every other public def. None when the class
    is absent — a renamed class must fail the checker loudly, not shrink it.
    """
    tree = ast.parse(source)
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == name):
            continue
        fields: list[str] = []
        properties: list[str] = []
        methods: list[str] = []
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                if "ClassVar" in ast.unparse(item.annotation):
                    continue
                if not item.target.id.startswith("_"):
                    fields.append(item.target.id)
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if item.name.startswith("_"):
                    continue
                if _decorator_names(item) & {"property", "cached_property"}:
                    properties.append(item.name)
                else:
                    methods.append(item.name)
        return {"fields": fields, "properties": properties, "methods": methods}
    return None


# --------------------------------------------------------------------------- #
# the checkers — each a pure function of text
# --------------------------------------------------------------------------- #
def spine_problems(modules: dict[str, str], contract: str) -> list[str]:
    """Undocumented spine modules and names. `modules` maps `<name>.py` to source."""
    problems: list[str] = []
    for filename in sorted(modules):
        if filename in NOT_SPINE_MODULES:
            continue
        section = md_section(contract, r"^###\s+`" + re.escape(filename) + "`")
        if section is None:
            problems.append(f"SPINE_CONTRACT has no ### `{filename}` heading")
            continue
        names, problem = public_names(modules[filename], filename)
        if problem:
            problems.append(problem)
            continue
        code = code_text(section)
        for name in names:
            if not mentions(code, name):
                problems.append(f"{filename}: `{name}` is public and not in its "
                                f"SPINE_CONTRACT section (in code form)")
    return problems


def _surface_problems(source: str, where: str, doc: str, doc_name: str,
                      name: str, groups: tuple[str, ...]) -> list[str]:
    members = class_members(source, name)
    if members is None:
        return [f"{where} defines no class {name}: if it was renamed, rename it in "
                f"this checker too — a class the checker cannot find is a class it "
                f"stops checking"]
    block = class_block(doc, name)
    if block is None:
        return [f"{doc_name} has no `class {name}` block in a code fence"]
    return [f"{name}.{member} ({group[:-1] if group != 'properties' else 'property'}) "
            f"is not in {doc_name}'s `class {name}` block"
            for group in groups for member in members[group]
            if not mentions(block, member)]


def record_problems(models_source: str, contract: str,
                    kinds: tuple[str, ...] = RECORD_KINDS) -> list[str]:
    """Record-kind fields missing from their `class` block in SPINE_CONTRACT."""
    problems: list[str] = []
    for kind in kinds:
        problems += _surface_problems(models_source, "models.py", contract,
                                      "SPINE_CONTRACT", kind, ("fields",))
    return problems


def pack_surface_problems(sources: dict[str, str], pack_format: str,
                          surface=PACK_SURFACE) -> list[str]:
    """The pack-facing surface missing from its `class` block in PACK_FORMAT."""
    problems: list[str] = []
    for where, name, groups in surface:
        problems += _surface_problems(sources[where], where, pack_format,
                                      "PACK_FORMAT", name, groups)
    return problems


def site_problems(state_keys, data_files, site_contract: str) -> list[str]:
    """Top-level state.json keys and site/data/ files missing from SITE_CONTRACT.

    Keys are looked for in the code of the `state.json` section; files anywhere
    in the document's code, where a file under `views/` may be written as the
    template the build uses, `views/<id>.json`.
    """
    problems: list[str] = []
    section = md_section(site_contract, r"^##\s+`state\.json`")
    if section is None:
        problems.append("SITE_CONTRACT has no ## `state.json` section")
    else:
        code = code_text(section)
        for key in sorted(state_keys):
            if not mentions(code, key):
                problems.append(f"state.json key `{key}` is not in SITE_CONTRACT's "
                                f"`state.json` section")
    code = code_text(site_contract)
    for path in sorted(data_files):
        spellings = [path]
        if path.startswith("views/"):
            spellings.append("views/<id>.json")
        if not any(re.search(r"(?<![\w.-])" + re.escape(s) + r"(?![\w.-])", code)
                   for s in spellings):
            problems.append(f"site/data/{path} is written by `site build` and not "
                            f"named in SITE_CONTRACT")
    return problems


# -- PLAN §3: closed rows ----------------------------------------------------- #
#: The Phase 1 token rule: a backticked token is a test reference iff
#: it matches one of these. Anything else in a Proof cell (`gate list --json`,
#: `why thickness`, `C:`) is prose about the proof, not its name.
TEST_TOKEN = re.compile(r"^test_\w+(\.\w+)*$|^[A-Z]\w*(\.(\w+|\*))*$")


def _cells(row: str) -> list[str]:
    """Split a markdown table row on unescaped pipes (`bench e2\\|e5` is one cell)."""
    body = row.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", body)]


def gap_map_rows(plan: str) -> list[dict]:
    """Every row of PLAN §3's tables as `{"id", "ph", "proof", "has_columns"}`.

    Columns are found from each table's own header, because the §18 table has
    five columns where the others have six: an index fixed at 4 would read the
    Proof cell there as the Ph cell.
    """
    section = md_section(plan, r"^##\s+3\.\s")
    if section is None:
        return []
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            header = None
            continue
        cells = _cells(line)
        if header is None:
            header = [c.strip("* ") for c in cells]
            continue
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        row = dict(zip(header, cells))
        rows.append({"id": row.get("#", ""), "ph": row.get("Ph", ""),
                     "proof": row.get("Proof", ""),
                     "has_columns": "Ph" in header and "Proof" in header})
    return rows


def row_is_closed(ph: str) -> bool:
    """True iff the Ph cell lists at least one phase and marks EVERY one closed.

    `closed 1.2` is closed; `closed 1.2, 2.2` is not (P2 still owes its half);
    `—` lists no phase and is not closed. Parenthesised notes are dropped first,
    so `2 (no-field test)` reads as phase 2.
    """
    text = re.sub(r"\([^)]*\)", " ", ph)
    phases = list(re.finditer(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", text))
    return bool(phases) and all(re.search(r"closed\s+$", text[:m.start()])
                                for m in phases)


def proof_refs(cell: str) -> list[str]:
    """The backticked tokens of a Proof cell that name tests (TEST_TOKEN)."""
    return [tok for tok in re.findall(r"`([^`\n]+)`", cell) if TEST_TOKEN.match(tok)]


def tests_index(sources: dict[str, str]) -> dict[str, dict]:
    """`{module stem: {"classes": {name: (methods, bases)}, "functions": set}}`."""
    index: dict[str, dict] = {}
    for filename, source in sources.items():
        tree = ast.parse(source, filename=filename)
        classes: dict[str, tuple[set[str], list[str]]] = {}
        functions: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                methods = {item.name for item in node.body
                           if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
                bases = [b.id if isinstance(b, ast.Name) else
                         b.attr if isinstance(b, ast.Attribute) else ""
                         for b in node.bases]
                classes[node.name] = (methods, bases)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.add(node.name)
        stem = os.path.splitext(os.path.basename(filename))[0]
        index[stem] = {"classes": classes, "functions": functions}
    return index


def _class_has(index: dict, module: str | None, cls: str, member: str,
               _seen: frozenset = frozenset()) -> bool:
    """`cls` (in `module`, or in any module) defines or inherits `member` or
    `test_<member>` — `RenderersAgree.table` names `RenderersAgree.test_table`."""
    for stem, mod in index.items():
        if module is not None and stem != module:
            continue
        found = mod["classes"].get(cls)
        if found is None or (stem, cls) in _seen:
            continue
        methods, bases = found
        if member == "*" or member in methods or f"test_{member}" in methods:
            return True
        if any(base and _class_has(index, None, base, member, _seen | {(stem, cls)})
               for base in bases):
            return True
    return False


def resolves(token: str, index: dict) -> bool:
    """Does a test reference name a module, class or method under tests/?"""
    parts = token.split(".")
    if parts[0].startswith("test_"):
        if len(parts) == 1:
            return parts[0] in index or any(
                parts[0] in mod["functions"]
                or any(parts[0] in methods for methods, _ in mod["classes"].values())
                for mod in index.values())
        module, rest = parts[0], parts[1:]
        if module not in index:
            return False
        if len(rest) == 1 and rest[0] in index[module]["functions"]:
            return True
        if rest[0] not in index[module]["classes"]:
            return False
        return len(rest) == 1 or (len(rest) == 2 and
                                  _class_has(index, module, rest[0], rest[1]))
    cls, rest = parts[0], parts[1:]
    if not rest:
        return any(cls in mod["classes"] for mod in index.values())
    return len(rest) == 1 and _class_has(index, None, cls, rest[0])


def closed_row_problems(plan: str, index: dict) -> list[str]:
    """Closed §3 rows whose Proof names a test that does not exist."""
    problems: list[str] = []
    for row in gap_map_rows(plan):
        if not row_is_closed(row["ph"]):
            continue
        refs = proof_refs(row["proof"])
        if not refs:
            problems.append(f"{row['id']} is closed and its Proof names no test")
        for ref in refs:
            if not resolves(ref, index):
                problems.append(f"{row['id']} is closed and its Proof names "
                                f"`{ref}`, which is no module, class or method "
                                f"under tests/")
    return problems


# --------------------------------------------------------------------------- #
# the tree, read once
# --------------------------------------------------------------------------- #
def _spine_sources() -> dict[str, str]:
    return {name: _read(os.path.join(SPINE_SRC, name))
            for name in sorted(os.listdir(SPINE_SRC)) if name.endswith(".py")}


def _test_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(TESTS_DIR):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                path = os.path.join(dirpath, name)
                out[os.path.relpath(path, TESTS_DIR)] = _read(path)
    return out


def _plant_field(source: str, cls: str, line: str) -> str:
    """`source` with `line` added to the body of top-level class `cls`, after
    its first annotated field — a real edit to the real file's text."""
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    first = next(item for item in node.body if isinstance(item, ast.AnnAssign))
    lines = source.splitlines()
    indent = " " * first.col_offset
    lines.insert(first.end_lineno, indent + line)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
class SpineModulesAreDocumented(unittest.TestCase):
    """Every spine module has its heading; every public name is in its section.

    `test_every_…` is the check on the tree; every other test plants a violator
    (PLAN's `V:`) or pins a rule that keeps the check from going soft."""

    def test_every_module_and_public_name_is_documented(self):
        problems = spine_problems(_spine_sources(), _read(SPINE_CONTRACT))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_walk_is_not_vacuous(self):
        """A glob that matched nothing would document everything."""
        sources = _spine_sources()
        spine = [name for name in sources if name not in NOT_SPINE_MODULES]
        # 12 spine modules at 7ecf953; Phase 1 only adds (verdicts.py, vcs.py).
        self.assertGreaterEqual(len(spine), 12, spine)
        for name in ("models.py", "gates.py", "site.py", "cli.py"):
            self.assertIn(name, spine)
            names, problem = public_names(sources[name], name)
            self.assertEqual(problem, "")
            self.assertTrue(names, f"{name} exports nothing the checker can see")

    def test_a_planted_undocumented_name_is_caught(self):
        modules = {"planted.py": '__all__ = ["documented_fn", "undocumented_fn"]\n'}
        contract = "### `planted.py`  (no deps)\n```python\ndef documented_fn()\n```\n"
        problems = spine_problems(modules, contract)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("undocumented_fn", problems[0])

    def test_a_planted_module_without_a_heading_is_caught(self):
        sources = {**_spine_sources(), "planted.py": "def planted_fn():\n    pass\n"}
        problems = spine_problems(sources, _read(SPINE_CONTRACT))
        self.assertIn("SPINE_CONTRACT has no ### `planted.py` heading", problems)

    def test_a_name_in_prose_only_is_not_documented(self):
        """`build` is an English word; saying it is not documenting `build()`."""
        modules = {"planted.py": '__all__ = ["build"]\n'}
        contract = "### `planted.py`\nWe build things here.\n"
        self.assertEqual(len(spine_problems(modules, contract)), 1)

    def test_a_name_under_another_modules_heading_is_not_documented(self):
        modules = {"a.py": '__all__ = ["shared_name"]\n', "b.py": "__all__ = []\n"}
        contract = ("### `a.py`\nnothing here\n### `b.py`\n"
                    "```python\ndef shared_name()\n```\n")
        problems = spine_problems(modules, contract)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("a.py", problems[0])

    def test_a_heading_inside_a_fence_does_not_end_the_section(self):
        modules = {"planted.py": '__all__ = ["late_name"]\n'}
        contract = ("### `planted.py`\n```python\n# a comment, not a heading\n"
                    "def late_name()\n```\n")
        self.assertEqual(spine_problems(modules, contract), [])

    def test_an_unreadable_all_is_a_problem_not_a_fallback(self):
        modules = {"planted.py": '__all__ = ["a"] + OTHER\ndef a():\n    pass\n'}
        contract = "### `planted.py`\n`a`\n"
        problems = spine_problems(modules, contract)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("not a literal", problems[0])

    def test_without_all_the_public_defs_are_checked(self):
        modules = {"planted.py": ("def shown():\n    pass\n"
                                  "def _private():\n    pass\n"
                                  "class Hidden:\n    pass\n")}
        contract = "### `planted.py`\n`shown`\n"
        problems = spine_problems(modules, contract)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("Hidden", problems[0])


class RecordFieldsAreDocumented(unittest.TestCase):
    """Every field of every record kind, inside that record's `class` block."""

    def test_every_record_field_is_documented(self):
        problems = record_problems(_read(os.path.join(SPINE_SRC, "models.py")),
                                   _read(SPINE_CONTRACT))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_a_planted_record_field_is_caught(self):
        planted = _plant_field(_read(os.path.join(SPINE_SRC, "models.py")),
                               "Claim", "planted_field_u01: str = ''")
        problems = record_problems(planted, _read(SPINE_CONTRACT))
        self.assertEqual(problems,
                         ["Claim.planted_field_u01 (field) is not in "
                          "SPINE_CONTRACT's `class Claim` block"])

    def test_a_field_documented_under_another_class_does_not_count(self):
        source = "class Claim:\n    note: str = ''\n"
        doc = ("```python\nclass Claim(Record):\n    id: str\n"
               "class Decision(Record):\n    note: str\n```\n")
        self.assertEqual(record_problems(source, doc, kinds=("Claim",)),
                         ["Claim.note (field) is not in SPINE_CONTRACT's "
                          "`class Claim` block"])

    def test_a_renamed_record_fails_loudly(self):
        problems = record_problems("class Other:\n    x: int = 0\n", "",
                                   kinds=("Claim",))
        self.assertEqual(len(problems), 1)
        self.assertIn("defines no class Claim", problems[0])


class PackSurfaceIsDocumented(unittest.TestCase):
    """What a pack author can set, read and call, in PACK_FORMAT."""

    def _sources(self) -> dict[str, str]:
        return {"models.py": _read(os.path.join(SPINE_SRC, "models.py")),
                "gates.py": _read(os.path.join(SPINE_SRC, "gates.py"))}

    def test_every_field_property_and_method_is_documented(self):
        problems = pack_surface_problems(self._sources(), _read(PACK_FORMAT))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_a_planted_gatespec_field_is_caught(self):
        sources = self._sources()
        sources["models.py"] = _plant_field(sources["models.py"], "GateSpec",
                                            "planted_field_u01: str = ''")
        problems = pack_surface_problems(sources, _read(PACK_FORMAT))
        self.assertEqual(problems, ["GateSpec.planted_field_u01 (field) is not in "
                                    "PACK_FORMAT's `class GateSpec` block"])

    def test_a_planted_verdict_property_is_caught(self):
        sources = self._sources()
        sources["models.py"] = _plant_field(
            sources["models.py"], "Verdict",
            "@property\n    def planted_prop_u01(self) -> bool:\n        return True")
        problems = pack_surface_problems(sources, _read(PACK_FORMAT))
        self.assertEqual(problems, ["Verdict.planted_prop_u01 (property) is not in "
                                    "PACK_FORMAT's `class Verdict` block"])

    def test_a_planted_gatecontext_method_is_caught(self):
        sources = self._sources()
        sources["gates.py"] = _plant_field(
            sources["gates.py"], "GateContext",
            "def planted_method_u01(self) -> None:\n        return None")
        problems = pack_surface_problems(sources, _read(PACK_FORMAT))
        self.assertEqual(problems, ["GateContext.planted_method_u01 (method) is not "
                                    "in PACK_FORMAT's `class GateContext` block"])

    def test_private_members_are_not_surface(self):
        source = ("class GateContext:\n    _hidden: int = 0\n"
                  "    def _exact(self):\n        pass\n")
        self.assertEqual(pack_surface_problems(
            {"gates.py": source}, "```\nclass GateContext:\n```\n",
            surface=(("gates.py", "GateContext", ("fields", "methods")),)), [])


class SiteStateKeysAreDocumented(unittest.TestCase):
    """`site init` + `site build` on a copy of the bracket, in-process.

    In-process through `cli.main`, so what is checked is exactly what the
    command writes, with `gates.REGISTRY` swapped for a fresh one — the CLI loads
    into the module-level registry, and a registry another test filled would
    lend this build gates the bracket does not have.
    """

    def setUp(self) -> None:
        tmp = tempfile.mkdtemp(prefix="atompipe-contracts-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.root = os.path.join(tmp, "bracket")
        shutil.copytree(BRACKET, self.root,
                        ignore=shutil.ignore_patterns("__pycache__", "site"))
        saved = gates_mod.REGISTRY
        gates_mod.REGISTRY = gates_mod.Registry()
        self.addCleanup(setattr, gates_mod, "REGISTRY", saved)

        for argv in (["site", "init", "-C", self.root],
                     ["site", "build", "-C", self.root]):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli_mod.main(argv)
            self.assertEqual(code, 0, f"{argv}: {out.getvalue()}{err.getvalue()}")

        data = os.path.join(self.root, "site", "data")
        with open(os.path.join(data, "state.json"), encoding="utf-8") as fh:
            self.state_keys = sorted(json.load(fh))
        self.data_files = sorted(
            os.path.relpath(os.path.join(dirpath, name), data).replace(os.sep, "/")
            for dirpath, _dirs, names in os.walk(data) for name in names)

    def test_every_state_key_and_data_file_is_documented(self):
        self.assertIn("claims", self.state_keys, "the build wrote no real state")
        self.assertIn("state.json", self.data_files)
        problems = site_problems(self.state_keys, self.data_files,
                                 _read(SITE_CONTRACT))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_a_planted_state_key_is_caught(self):
        problems = site_problems(self.state_keys + ["planted_key_u01"],
                                 self.data_files, _read(SITE_CONTRACT))
        self.assertEqual(problems, ["state.json key `planted_key_u01` is not in "
                                    "SITE_CONTRACT's `state.json` section"])

    def test_a_planted_data_file_is_caught(self):
        problems = site_problems(self.state_keys,
                                 self.data_files + ["planted_u01.json"],
                                 _read(SITE_CONTRACT))
        self.assertEqual(problems, ["site/data/planted_u01.json is written by "
                                    "`site build` and not named in SITE_CONTRACT"])

    def test_a_split_view_payload_is_covered_by_the_template(self):
        self.assertEqual(site_problems([], ["views/assembly.json"],
                                       _read(SITE_CONTRACT)), [])


class ClosedRowsResolve(unittest.TestCase):
    """A §3 row marked closed names tests that exist (PLAN §3's header rule)."""

    _PLANTED_ROW = "| M99.9 | planted | planted | planted | {ph} | {proof} |"

    def _plan_with(self, ph: str, proof: str) -> str:
        plan = _read(PLAN)
        anchor = plan.index("\n| M0.1 |")
        return (plan[:anchor] + "\n" + self._PLANTED_ROW.format(ph=ph, proof=proof)
                + plan[anchor:])

    def test_every_closed_row_names_tests_that_exist(self):
        problems = closed_row_problems(_read(PLAN), tests_index(_test_sources()))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_parser_sees_the_whole_gap_map(self):
        """Every `| M…` line in §3 is parsed, with a Ph and a Proof column."""
        plan = _read(PLAN)
        rows = gap_map_rows(plan)
        section = md_section(plan, r"^##\s+3\.\s") or ""
        ids = [_cells(line)[0] for line in section.splitlines()
               if re.match(r"^\|\s*M\d", line)]
        self.assertGreaterEqual(len(rows), MIN_GAP_MAP_ROWS)
        self.assertEqual([row["id"] for row in rows], ids)
        self.assertTrue(all(row["has_columns"] for row in rows),
                        [row["id"] for row in rows if not row["has_columns"]])

    def test_a_planted_closed_row_naming_a_missing_test_is_caught(self):
        plan = self._plan_with("closed 1.0", "`test_no_such_module_u01.Nope`")
        problems = closed_row_problems(plan, tests_index(_test_sources()))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("M99.9", problems[0])
        self.assertIn("test_no_such_module_u01.Nope", problems[0])

    def test_a_planted_closed_row_naming_no_test_is_caught(self):
        plan = self._plan_with("closed 1.1", "the CI step")
        problems = closed_row_problems(plan, tests_index(_test_sources()))
        self.assertEqual(problems, ["M99.9 is closed and its Proof names no test"])

    def test_a_closed_row_naming_real_tests_resolves(self):
        """The positive half: without it the V above could pass on a checker
        that refuses every row."""
        plan = self._plan_with(
            "closed 1.0, closed 1.1",
            "`test_invariants.SkipIsNotPass`; `test_packs`; `ErrorIsNotPass.*`;"
            " `SkipIsNotPass.verdict_ok_is_false_when_skipped`; `gate list --json`")
        self.assertEqual(closed_row_problems(plan, tests_index(_test_sources())), [])

    def test_only_rows_closed_in_every_phase_are_checked(self):
        index = tests_index(_test_sources())
        for ph in ("closed 1.2, 2.2", "1.2", "—", "2 (no-field test), closed 5"):
            plan = self._plan_with(ph, "`test_no_such_module_u01`")
            self.assertEqual(closed_row_problems(plan, index), [], ph)

    def test_the_token_rule(self):
        self.assertTrue(row_is_closed("closed 2 (no-field test), closed 5"))
        self.assertFalse(row_is_closed("closed 1.2, 2.2"))
        self.assertFalse(row_is_closed("—"))
        self.assertEqual(proof_refs("`test_a.B`; `C:` x; `why thickness`;"
                                    " `Goalposts.*`; `executed`;"
                                    " `Objectives.test_weight_is_refused…`"),
                         ["test_a.B", "Goalposts.*"])
        index = tests_index({
            "test_x.py": ("class Base:\n    def test_inherited(self): pass\n"
                          "class Child(Base):\n    def test_table(self): pass\n"
                          "def test_free(): pass\n")})
        for token in ("test_x", "test_x.Child", "test_x.Child.test_table",
                      "Child.table", "Child.inherited", "Child.*", "test_free",
                      "test_x.test_free", "test_table"):
            self.assertTrue(resolves(token, index), token)
        for token in ("test_y", "test_x.Nope", "Child.nope", "Nope",
                      "test_x.Child.nope", "test_nope"):
            self.assertFalse(resolves(token, index), token)


if __name__ == "__main__":
    unittest.main()
