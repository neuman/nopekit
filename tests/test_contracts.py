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

The checkers, each a PURE function of text inputs so each violation test plants
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
    SiteMetaKeysAreDocumented   ... and every key of its `meta`, in SITE_CONTRACT's
                                ### `meta` table
    ClosedRowsResolve           a PLAN §3 row marked closed names tests that exist
    ClosedRowsStayClosed        the rows Phase 1 closed stay marked closed
    DocumentedNamesExist        the reverse: every name SPINE_CONTRACT writes in a
                                module's section — a `name(` call form, a fenced
                                def / class / CONSTANT, a member of a `class`
                                block — exists in that module, and every member
                                PACK_FORMAT's `class` blocks list exists
    RemovedNamesAreGone         no document an agent reads names what Phase 1
                                removed (the run history, the record-mutating
                                commands, the old recorders)

**Both directions, from the Phase 1 commit.** During the phase the check ran one
way, code ⊆ docs: the unit that owned a contract document wrote the other units'
surfaces into it before their code merged, so a documented name with no code yet
had to stay green. What that left open is R-14's own stated failure — a contract
describing a spine that no longer exists. Phase 1 deleted the run history, three
commands, a flag and the CLI's recorders, and a code ⊆ docs check cannot see a
paragraph that goes on documenting any of them: SPINE_CONTRACT still told its
reader that the legacy save wrote "no `last_run`" and that `_ParamReads` had
missed bulk reads, and SITE_CONTRACT still explained the absence of a
`meta.last_run` — each a name a fresh context window would grep for and act on.
`DocumentedNamesExist` and `RemovedNamesAreGone` close that direction.

"Appears" is deliberately stronger than a substring anywhere in the file. A
field called `id`, `note` or `kind` appears in every contract document by
accident, so a whole-file search would pass a record nobody documented. A name
counts only in CODE FORM (a fenced block or an inline code span) inside its own
module's section; a field counts only inside its own record's `class` block.

Run:  PYTHONPATH=src python3 -m unittest tests.test_contracts -v
"""
from __future__ import annotations

import ast
import builtins
import contextlib
import dataclasses
import glob
import importlib
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


#: The §3 rows Phase 1 closed, and the checkpoints each carries `closed` before.
#: `ClosedRowsResolve` checks a closed row's tests exist; nothing there notices a
#: row that stops SAYING closed — the cheapest way to make a deleted proof go
#: green is to strike the word. So the Phase 1 closures are pinned: a later phase
#: adds its own `closed` marks and never removes these. *Rejected:* pinning every
#: Ph cell verbatim (every honest later closure would turn this red).
PHASE_1_CLOSURES: dict[str, tuple[str, ...]] = {
    # closed in every phase they list
    "M2.1a": ("1.0", "1.1"), "M2.1b": ("1.0",), "M2.1c": ("1.2",),
    "M2.1e": ("1.1", "1.2"), "M5.1": ("1",), "M11.1": ("1.2",), "M11.2": ("1.2",),
    "M11.4": ("1.2", "1.3"), "M11.5": ("1.2",), "M11.7": ("1.2",), "M11.9": ("1.2",),
    "M11.11": ("1.2",),
    # partial: only their Phase 1 checkpoints are closed
    "M0.1": ("1",), "M2.1d": ("1.1", "1.2"), "M2.2b": ("1.2",), "M3.L": ("1.2",),
    "M3.C": ("1.2",), "M9.2": ("1.3",), "M11.3": ("1.2",), "M11.6": ("1.2",),
    "M13.3": ("1.2",), "M13.7": ("1.2",), "M14.3": ("1.2",), "M14.6": ("1",),
    "M18.3": ("1",), "M18.5": ("1",),
}


def closed_phases(ph: str) -> set[str]:
    """The phases a Ph cell marks `closed` (`closed 1.2, 2.4` -> {"1.2"}),
    by `row_is_closed`'s rule: parenthesised notes dropped first."""
    text = re.sub(r"\([^)]*\)", " ", ph)
    return {m.group(0) for m in re.finditer(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", text)
            if re.search(r"closed\s+$", text[:m.start()])}


def closure_problems(plan: str, pinned: dict[str, tuple[str, ...]] = PHASE_1_CLOSURES
                     ) -> list[str]:
    """Pinned closures a §3 row no longer carries (or a pinned row that is gone)."""
    rows = {row["id"]: row for row in gap_map_rows(plan)}
    problems: list[str] = []
    for row_id, phases in pinned.items():
        row = rows.get(row_id)
        if row is None:
            problems.append(f"{row_id} was closed by Phase 1 and is no longer a §3 row")
            continue
        lost = [p for p in phases if p not in closed_phases(row["ph"])]
        if lost:
            problems.append(f"{row_id} was closed at {', '.join(lost)} by Phase 1 and "
                            f"its Ph cell no longer says so: {row['ph']!r}")
    return problems


# -- the reverse direction: what the contracts name exists (Phase 1 commit) -- #
#: A call form inside an inline code span — `name(`, `store.load(`,
#: `ctx.param(`. A word followed by `(s)` is English ("control(s)"), not a call.
CALL_FORM = re.compile(r"(?<![\w.])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\((?!s\))")
#: What a fenced line DEFINES at column 0 of a module's section: `def name(`,
#: `class Name`, `CONSTANT = ...` or `CONSTANT: type` — one per `;` statement.
_TOP_NAME = re.compile(r"^\s*(?:(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(|class\s+([A-Za-z_]\w*)"
                       r"|([A-Z][A-Z0-9_]*)\s*(?::(?!:)|=(?!=)))")
#: A member a `class` block lists: `name: type` or `name -> type` (a property) at
#: the start of a statement, and every `def name(`. Comments are cut first.
_MEMBER = re.compile(r"(?:^|;)\s*([A-Za-z_]\w*)\s*(?::(?!:)|->)")
_MEMBER_DEF = re.compile(r"\bdef\s+([A-Za-z_]\w*)\s*\(")
_CLASS_HEAD = re.compile(r"^(\s*)class\s+([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*:?(.*)$")
PACKAGE = "atompipe"


@dataclasses.dataclass
class ModuleIndex:
    """What one spine module binds, read from its AST (never by importing it).

    `names` maps every top-level binding — a def, a class, an assignment, an
    import, including those under a top-level `if`/`try` — to the node that
    binds it; `params` is every parameter name of every function and method,
    so a doc may write `run_all`'s hook as `before(spec, fn)`."""

    names: dict[str, ast.AST]
    classes: dict[str, ast.ClassDef]
    params: frozenset[str]


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for elt in target.elts for n in _target_names(elt)]
    return []


def _bind(body: list[ast.stmt], out: dict[str, ast.AST]) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = node
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out[alias.asname or alias.name.split(".")[0]] = node
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                out[alias.asname or alias.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                out.update((n, node) for n in _target_names(target))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            out.update((n, node) for n in _target_names(node.target))
        elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            for block in ("body", "orelse", "finalbody"):
                _bind(getattr(node, block, []) or [], out)
            for handler in getattr(node, "handlers", []) or []:
                _bind(handler.body, out)


def module_index(source: str, filename: str = "<module>") -> ModuleIndex:
    tree = ast.parse(source, filename=filename)
    names: dict[str, ast.AST] = {}
    _bind(tree.body, names)
    classes = {n: node for n, node in names.items() if isinstance(node, ast.ClassDef)}
    params = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            params.update(arg.arg for arg in a.posonlyargs + a.args + a.kwonlyargs)
            params.update(arg.arg for arg in (a.vararg, a.kwarg) if arg is not None)
    return ModuleIndex(names=names, classes=classes, params=frozenset(params))


def _class_members(node: ast.ClassDef) -> set[str]:
    """What a class body binds, plus every `self.<x> = ...` in its methods —
    `Registry.pack_dirs` is set in `__init__`, and is as much a member as a field."""
    members: dict[str, ast.AST] = {}
    _bind(node.body, members)
    out = set(members)
    for sub in ast.walk(node):
        targets: list[ast.expr] = []
        if isinstance(sub, ast.Assign):
            targets = list(sub.targets)
        elif isinstance(sub, (ast.AnnAssign, ast.AugAssign)):
            targets = [sub.target]
        for target in targets:
            for elt in (target.elts if isinstance(target, (ast.Tuple, ast.List)) else [target]):
                if (isinstance(elt, ast.Attribute) and isinstance(elt.value, ast.Name)
                        and elt.value.id == "self"):
                    out.add(elt.attr)
    return out


def _dotted(expr: ast.expr) -> str:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        head = _dotted(expr.value)
        return f"{head}.{expr.attr}" if head else ""
    if isinstance(expr, ast.Subscript):          # Generic[T], NamedTuple[...] and the like
        return _dotted(expr.value)
    return ""


def _import_target(node: ast.AST, name: str) -> tuple[str, str] | None:
    """`(module path, attribute or "")` a name an import binds refers to; None when
    the node is no import. `from . import store` -> ("atompipe.store", "")."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            if (alias.asname or alias.name.split(".")[0]) == name:
                return (alias.name if alias.asname else alias.name.split(".")[0], "")
    if isinstance(node, ast.ImportFrom):
        base = node.module or ""
        if node.level:
            base = PACKAGE + (f".{base}" if base else "")
        for alias in node.names:
            if (alias.asname or alias.name) == name:
                return (base, alias.name)
    return None


def _stdlib_has(module: str, attrs: list[str]) -> bool:
    """Does `module.<attrs...>` exist? Only ever a standard-library module: the
    spine imports nothing else (CI's AST walk)."""
    try:
        obj = importlib.import_module(module)
    except ImportError:
        return False
    path = module
    for attr in attrs:
        path = f"{path}.{attr}"
        if hasattr(obj, attr):
            obj = getattr(obj, attr)
            continue
        try:
            obj = importlib.import_module(path)
        except ImportError:
            return False
    return True


def resolves_in(parts: list[str], stem: str, indexes: dict[str, ModuleIndex],
                _depth: int = 0) -> bool:
    """Does the dotted name `parts` resolve in spine module `stem`'s namespace —
    its bindings, then a spine module's qualified name, then the builtins?"""
    if _depth > 12:                               # a cycle of re-exports: say no
        return False
    index = indexes[stem]
    head, rest = parts[0], list(parts[1:])
    if head in index.classes:
        return not rest or has_member(stem, head, rest[0], indexes, _depth + 1)
    node = index.names.get(head)
    if node is not None:
        target = _import_target(node, head)
        if target is None:
            return True       # a def or an assignment: it exists; what it holds is not read
        module, attr = target
        chain = ([attr] if attr else []) + rest
        spine = module[len(PACKAGE) + 1:] if module.startswith(PACKAGE + ".") else ""
        if module == PACKAGE and attr in indexes:  # from atompipe import store
            return not rest or resolves_in(rest, attr, indexes, _depth + 1)
        if spine in indexes:
            return not chain or resolves_in(chain, spine, indexes, _depth + 1)
        return _stdlib_has(module, chain)
    if head in indexes:                           # `store.load(` written in another section
        return not rest or resolves_in(rest, head, indexes, _depth + 1)
    if hasattr(builtins, head):
        obj = getattr(builtins, head)
        for attr in rest:
            if not hasattr(obj, attr):
                return False
            obj = getattr(obj, attr)
        return True
    return False


def has_member(stem: str, cls: str, member: str, indexes: dict[str, ModuleIndex],
               _depth: int = 0) -> bool:
    """Does class `cls` of module `stem` define or inherit `member`? Bases are
    followed through the spine, the standard library and the builtins."""
    node = indexes[stem].classes.get(cls)
    if node is None or _depth > 12:
        return False
    if member in _class_members(node) or hasattr(object, member):
        return True
    return any(base and resolves_in(base.split(".") + [member], stem, indexes, _depth + 1)
               for base in map(_dotted, node.bases))


def call_form_resolves(token: str, stem: str, indexes: dict[str, ModuleIndex]) -> bool:
    """A call form written in `stem`'s section names something that exists: a name
    in the module's namespace (bindings, a spine module's qualified name, a
    builtin); a parameter of one of its functions, called (`loader(abspath)`); or
    a member of a class it defines, bare (`self_modified()`) or reached through an
    instance (`ctx.param(`)."""
    parts = token.split(".")
    if resolves_in(parts, stem, indexes):
        return True
    if len(parts) == 1 and parts[0] in indexes[stem].params:
        return True
    return any(has_member(stem, cls, parts[-1], indexes) for cls in indexes[stem].classes)


def _class_chunks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """`(class name, [the head's remainder] + body lines)` for every `class` line
    in `lines`, the body read the way `class_block` reads one."""
    chunks: list[tuple[str, list[str]]] = []
    i = 0
    while i < len(lines):
        match = _CLASS_HEAD.match(lines[i])
        if not match:
            i += 1
            continue
        indent, name, remainder = len(match.group(1)), match.group(2), match.group(3)
        body = [remainder]
        i += 1
        while i < len(lines) and (not lines[i].strip()
                                  or len(lines[i]) - len(lines[i].lstrip()) > indent):
            body.append(lines[i])
            i += 1
        chunks.append((name, body))
    return chunks


def listed_members(body: list[str]) -> list[str]:
    """The members a `class` block lists (`_MEMBER`, `_MEMBER_DEF`), comments cut."""
    out: list[str] = []
    for line in body:
        code = line.split("#", 1)[0]
        out += [m.group(1) for m in _MEMBER.finditer(code)]
        out += [m.group(1) for m in _MEMBER_DEF.finditer(code)]
    return out


def documented_names(section: str) -> dict[str, list]:
    """What a module's SPINE_CONTRACT section names, by kind: `calls` (tokens of
    inline code spans), `defined` (fenced column-0 names) and `members`
    (`(class, member)` from fenced `class` blocks)."""
    calls: list[str] = []
    defined: list[str] = []
    fenced: list[str] = []
    for line, in_fence in _lines_outside_fences(section):
        if in_fence:
            if not _FENCE.match(line):
                fenced.append(line)
                if not line[:1].isspace():
                    for stmt in line.split("#", 1)[0].split(";"):
                        match = _TOP_NAME.match(stmt)
                        if match:
                            defined.append(next(g for g in match.groups() if g))
            continue
        for span in re.findall(r"`([^`\n]+)`", line):
            calls += CALL_FORM.findall(span)
    members = [(cls, m) for cls, body in _class_chunks(fenced) for m in listed_members(body)]
    return {"calls": calls, "defined": defined, "members": members}


def spine_indexes(sources: dict[str, str]) -> dict[str, ModuleIndex]:
    return {name[:-3]: module_index(src, name) for name, src in sources.items()
            if name.endswith(".py") and name not in NOT_SPINE_MODULES}


def documented_name_problems(sources: dict[str, str], contract: str) -> list[str]:
    """Names SPINE_CONTRACT writes in a module's section that the module lacks.

    The reverse of `spine_problems`: that one finds code the contract does not
    describe, this one a contract describing code that is gone. A section that
    is missing is `spine_problems`' report, not this one's."""
    indexes = spine_indexes(sources)
    problems: list[str] = []
    for stem in sorted(indexes):
        filename = f"{stem}.py"
        section = md_section(contract, r"^###\s+`" + re.escape(filename) + "`")
        if section is None:
            continue
        names = documented_names(section)
        for token in dict.fromkeys(names["calls"]):
            if not call_form_resolves(token, stem, indexes):
                problems.append(f"{filename}: `{token}(` is written in its SPINE_CONTRACT "
                                f"section and names nothing {filename} has")
        index = indexes[stem]
        for name in dict.fromkeys(names["defined"]):
            if name not in index.names:
                problems.append(f"{filename}: its SPINE_CONTRACT section defines `{name}`, "
                                f"which {filename} does not")
        for cls, member in dict.fromkeys(names["members"]):
            if cls in index.classes and not has_member(stem, cls, member, indexes):
                problems.append(f"{filename}: SPINE_CONTRACT's `class {cls}` block lists "
                                f"`{member}`, which {cls} does not have")
    return problems


def pack_member_problems(sources: dict[str, str], pack_format: str,
                         surface=PACK_SURFACE) -> list[str]:
    """Members PACK_FORMAT's `class` blocks list that the class does not have —
    `pack_surface_problems` turned round."""
    indexes = spine_indexes(sources)
    problems: list[str] = []
    for where, name, _groups in surface:
        stem = where[:-3]
        block = class_block(pack_format, name)
        if block is None or stem not in indexes:
            continue                               # the forward checker reports these
        lines = [line for line in block.splitlines() if line.strip()]
        head = _CLASS_HEAD.match(lines[0]) if lines else None
        body = ([head.group(3)] if head else []) + lines[1:]
        for member in dict.fromkeys(listed_members(body)):
            if not has_member(stem, name, member, indexes):
                problems.append(f"PACK_FORMAT's `class {name}` block lists `{member}`, "
                                f"which {where}'s {name} does not have")
    return problems


# -- RemovedNamesAreGone -------------------------------------------------- #
#: What Phase 1 removed, each with what replaced it. A document that still names
#: one sends a reader after an API, a record or a command that is not there —
#: argparse answers `invalid choice`, the import fails, the key is never written.
#: *Rejected:* scanning only code spans (prose that says "run claim add" misleads
#: exactly as much); a list derived from a diff (the names are few, and each needs
#: its replacement said).
REMOVED_NAMES: tuple[tuple[str, str], ...] = (
    ("RunMeta", "the run record went at 1.2: git and the verdict cache are the history"),
    ("record_run", "the run history went at 1.2 (S-89)"),
    ("load_runs", "the run history went at 1.2 (S-31)"),
    ("runs_dir", "the run history went at 1.2"),
    ("RUNS_NAME", "the run history went at 1.2"),
    ("last_run", "the ledger's sweep record went at 1.2: staleness is per gate, and the "
                 "last full check is .atompipe/cache/last_check.json"),
    ("_ParamReads", "the CLI's flat read recorder went at 1.2: verdicts.ParamTrace"),
    ("_staleness", "the global staleness rule went at 1.2: verdicts.freshness and resolve"),
    ("sync_params", "the parameter sync went at 1.3: modelio.param_view reads the model"),
    ("undocumented_params", "went in the review of 1.3: it judged the model alone while "
                            "status, the report and the page judged the records alone; "
                            "modelio.undefended_params over param_view is the one nag list"),
    ("claim add", "went at 1.3 (A-8): a claim is the file claims/<id>.json"),
    ("claim edit", "went at 1.3 (A-8): edit claims/<id>.json"),
    ("packs remove", "went at 1.3 (A-8): delete the name from packs in .atompipe/project.json"),
    ("--set-entry", "went at 1.3 (A-8): \"model_entry\" in .atompipe/project.json"),
    ("gate --selftest", "never parsed: the command is `gate selftest`"),
    ("decide --when", "went at 1.3 (S-44): it backdated a decision; the edge stamps the time"),
)

#: The documents an agent reads for how atompipe works: the contract documents, the
#: skills, README and CLAUDE.md (spec §3.16), and — because a removed name misleads
#: from them as much — the rest of what `test_docs_commands` reads for commands.
#: Never `docs/*.md`: the plan documents quote the removed names as the history
#: they record, and the user's untracked draft lives there.
REMOVED_NAME_DOCS = (
    "README.md", "CLAUDE.md", "CONTRIBUTING.md", "METHOD.md",
    "docs/SPINE_CONTRACT.md", "docs/PACK_FORMAT.md", "docs/SITE_CONTRACT.md",
    "docs/EXTENSION_PROTOCOL.md",
)
REMOVED_NAME_GLOBS = ("skills/*/SKILL.md", "packs/*/PACK.md", "packs/*/references/*.md")


def _removed_pattern(name: str) -> re.Pattern:
    """An identifier as a whole identifier (`test_staleness` is not `_staleness`);
    a command or flag as whole words, any whitespace between them (a line break
    included), never a prefix (`claim edited` is not `claim edit`)."""
    if re.fullmatch(r"[A-Za-z_]\w*", name):
        return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])")
    words = r"\s+".join(re.escape(w) for w in name.split())
    return re.compile(r"(?<![\w-])" + words + r"(?![\w-])")


def removed_name_problems(docs: dict[str, str],
                          names: tuple[tuple[str, str], ...] = REMOVED_NAMES) -> list[str]:
    """`<path>:<line>: names <name> — <why>` for every removed name a document writes."""
    problems: list[str] = []
    for path in sorted(docs):
        text = docs[path]
        for name, why in names:
            for match in _removed_pattern(name).finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{path}:{line}: names `{name}`, which is gone — {why}")
    return problems


def site_meta_problems(meta_keys, site_contract: str) -> list[str]:
    """`state.json` `meta` keys missing from SITE_CONTRACT's ### `meta` table.

    Looked for in that subsection only: `name`, `summary` and `created` are in
    every document by accident, so "somewhere" proves nothing."""
    section = md_section(site_contract, r"^###\s+`meta`")
    if section is None:
        return ["SITE_CONTRACT has no ### `meta` section"]
    code = code_text(section)
    return [f"state.json meta key `{key}` is not in SITE_CONTRACT's ### `meta` section"
            for key in sorted(meta_keys) if not mentions(code, key)]


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


def _removed_name_docs() -> dict[str, str]:
    """The documents `RemovedNamesAreGone` reads, by repo-relative path."""
    paths = list(REMOVED_NAME_DOCS)
    for pattern in REMOVED_NAME_GLOBS:
        paths += sorted(os.path.relpath(p, REPO).replace(os.sep, "/")
                        for p in glob.glob(os.path.join(REPO, pattern)))
    return {path: _read(os.path.join(REPO, path)) for path in paths}


def _plant_in_section(contract: str, heading: str, text: str) -> str:
    """`contract` with `text` inserted right under the first line matching `heading`."""
    match = re.search(heading, contract, re.M)
    assert match, heading
    end = contract.index("\n", match.end()) + 1
    return contract[:end] + text + contract[end:]


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


class ClosedRowsStayClosed(unittest.TestCase):
    """The rows Phase 1 closed keep saying so (`PHASE_1_CLOSURES`)."""

    def test_every_phase_1_closure_is_still_marked(self):
        problems = closure_problems(_read(PLAN))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_pin_is_not_vacuous(self):
        """Twelve rows closed outright and fourteen in part: a pin that lost its
        rows would hold nothing."""
        full = [r for r in gap_map_rows(_read(PLAN)) if row_is_closed(r["ph"])]
        self.assertGreaterEqual(len(full), 12, [r["id"] for r in full])
        self.assertGreaterEqual(len(PHASE_1_CLOSURES), 26)

    def test_a_struck_closure_is_caught(self):
        plan = _read(PLAN)
        row = next(line for line in plan.splitlines() if line.startswith("| M2.1b |"))
        self.assertIn("| closed 1.0 |", row, "the planted edit has nothing to strike")
        struck = plan.replace(row, row.replace("| closed 1.0 |", "| 1.0 |"))
        self.assertEqual(closure_problems(struck),
                         ["M2.1b was closed at 1.0 by Phase 1 and its Ph cell no "
                          "longer says so: '1.0'"])

    def test_a_partial_closure_keeps_only_its_phase(self):
        self.assertEqual(closed_phases("closed 1.2, 2.4"), {"1.2"})
        self.assertEqual(closed_phases("closed 1 (statement), 2, 2.5 (the line), 5"), {"1"})
        self.assertEqual(closed_phases("closed 1.1, closed 1.2, 2.3, 5"), {"1.1", "1.2"})
        self.assertEqual(closed_phases("1.2"), set())


class DocumentedNamesExist(unittest.TestCase):
    """The reverse direction (spec §3.16): what SPINE_CONTRACT writes in a module's
    section, and what PACK_FORMAT's `class` blocks list, exists in the code.

    Three kinds of name, each with its planted violator: a call form in an inline
    code span (`` `store.load(` ``) resolves to an attribute of the module or of a
    class it defines — a builtin, or a parameter called by name, counts; a name a
    fenced line defines at column 0 (`def`, `class`, `CONSTANT =`) is a binding of
    the module; a member a `class` block lists is a member of that class, its
    bases' included. Each is read from the AST, never by importing the module, so
    a planted edit to the SOURCE is a violator too — the real direction of the
    drift, code moving under a document that stays."""

    def test_spine_contract_names_only_what_exists(self):
        problems = documented_name_problems(_spine_sources(), _read(SPINE_CONTRACT))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_pack_format_lists_only_members_that_exist(self):
        sources = {name: _read(os.path.join(SPINE_SRC, name))
                   for name in ("models.py", "gates.py")}
        problems = pack_member_problems(sources, _read(PACK_FORMAT))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_walk_is_not_vacuous(self):
        """Measured when this landed: 77 call forms, 298 fenced names and 320
        members across the module sections. The floors sit well below, and far
        above zero: a section parser that stopped seeing fences reads green."""
        contract = _read(SPINE_CONTRACT)
        counts = {"calls": 0, "defined": 0, "members": 0}
        for name in _spine_sources():
            if name in NOT_SPINE_MODULES:
                continue
            section = md_section(contract, r"^###\s+`" + re.escape(name) + "`") or ""
            for kind, found in documented_names(section).items():
                counts[kind] += len(found)
        self.assertGreaterEqual(counts["calls"], 40, counts)
        self.assertGreaterEqual(counts["defined"], 150, counts)
        self.assertGreaterEqual(counts["members"], 150, counts)

    def test_a_planted_call_form_is_caught(self):
        contract = _plant_in_section(_read(SPINE_CONTRACT), r"^###\s+`store\.py`",
                                     "It rebuilds with `store.rebuild_u31(root)`.\n")
        self.assertEqual(documented_name_problems(_spine_sources(), contract),
                         ["store.py: `store.rebuild_u31(` is written in its SPINE_CONTRACT "
                          "section and names nothing store.py has"])

    def test_a_planted_fenced_definition_is_caught(self):
        contract = _plant_in_section(
            _read(SPINE_CONTRACT), r"^###\s+`store\.py`",
            "```python\ndef rebuild_u31(root) -> None\nREBUILT_U31 = 1\n```\n")
        self.assertEqual(documented_name_problems(_spine_sources(), contract),
                         ["store.py: its SPINE_CONTRACT section defines `rebuild_u31`, "
                          "which store.py does not",
                          "store.py: its SPINE_CONTRACT section defines `REBUILT_U31`, "
                          "which store.py does not"])

    def test_a_planted_class_member_is_caught(self):
        contract = _plant_in_section(
            _read(SPINE_CONTRACT), r"^###\s+`models\.py`",
            "```python\nclass Claim(Record):\n    reviewed_u31: bool; statement: str\n```\n")
        self.assertEqual(documented_name_problems(_spine_sources(), contract),
                         ["models.py: SPINE_CONTRACT's `class Claim` block lists "
                          "`reviewed_u31`, which Claim does not have"])

    def test_a_function_removed_from_the_code_is_caught(self):
        """The drift this class exists for: the code moves and the page stays."""
        sources = _spine_sources()
        self.assertIn("def records_digest(", sources["store.py"])
        sources["store.py"] = sources["store.py"].replace(
            "def records_digest(", "def records_digest_gone_u31(")
        problems = documented_name_problems(sources, _read(SPINE_CONTRACT))
        self.assertTrue(problems, "a documented function deleted from the code went unseen")
        self.assertTrue(all("records_digest" in p for p in problems), problems)
        self.assertIn("store.py: its SPINE_CONTRACT section defines `records_digest`, "
                      "which store.py does not", problems)

    def test_a_field_removed_from_verdict_is_caught(self):
        sources = {name: _read(os.path.join(SPINE_SRC, name))
                   for name in ("models.py", "gates.py")}
        self.assertIn("    cpu_s: float = 0.0\n", sources["models.py"])
        sources["models.py"] = sources["models.py"].replace(
            "    cpu_s: float = 0.0\n", "    cpu_gone_u31: float = 0.0\n")
        self.assertEqual(pack_member_problems(sources, _read(PACK_FORMAT)),
                         ["PACK_FORMAT's `class Verdict` block lists `cpu_s`, which "
                          "models.py's Verdict does not have"])

    def test_what_resolves_and_what_does_not(self):
        """Each rule once, on a planted module: the positive halves keep the check
        from reading every English word as a stale name, the negative halves keep
        the rules from reading anything as resolved."""
        sources = {
            "planted.py": ("import json\nfrom . import store\n"
                           "from .models import Claim\n"
                           "class Ctx(dict):\n    def param(self, name): pass\n"
                           "    def __init__(self):\n        self.memo = {}\n"
                           "def run_all(registry, *, before=None): pass\n"),
            "store.py": "def load(root): pass\n",
            "models.py": "class Record:\n    def to_dict(self): pass\n"
                         "class Claim(Record):\n    statement: str = ''\n",
        }
        indexes = spine_indexes(sources)
        for token in ("json.dumps", "store.load", "Claim.to_dict", "Claim.statement",
                      "models.Claim", "dict", "isinstance", "before", "ctx.param",
                      "param", "p.get", "Ctx.memo", "run_all"):
            self.assertTrue(call_form_resolves(token, "planted", indexes), token)
        for token in ("json.nope", "store.nope", "Claim.nope", "nope", "ctx.nope",
                      "models.Nope", "registry_u31"):
            self.assertFalse(call_form_resolves(token, "planted", indexes), token)
        self.assertEqual(CALL_FORM.findall("<k> control(s) pending; run `load(root)`"),
                         ["load"])


class RemovedNamesAreGone(unittest.TestCase):
    """No document an agent reads names what Phase 1 removed (spec §3.16)."""

    def test_no_document_names_a_removed_name(self):
        problems = removed_name_problems(_removed_name_docs())
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_documents_are_read(self):
        """The spec's set is in the list and every file in it exists: a list that
        read nothing would find nothing."""
        docs = _removed_name_docs()
        for path in ("README.md", "CLAUDE.md", "docs/SPINE_CONTRACT.md",
                     "docs/PACK_FORMAT.md", "docs/SITE_CONTRACT.md",
                     "skills/atompipe/SKILL.md", "skills/pack-authoring/SKILL.md"):
            self.assertIn(path, docs)
            self.assertGreater(len(docs[path]), 500, path)
        self.assertGreaterEqual(len(docs), 15)

    def test_every_planted_removed_name_is_caught(self):
        for name, _why in REMOVED_NAMES:
            for text in (f"intro\nRun `atompipe {name} x`.\n", f"intro\nthe {name} here\n"):
                problems = removed_name_problems({"doc.md": text})
                self.assertEqual(len(problems), 1, (name, text, problems))
                self.assertTrue(problems[0].startswith(f"doc.md:2: names `{name}`"),
                                problems)

    def test_a_command_broken_across_lines_is_caught(self):
        problems = removed_name_problems({"doc.md": "then run atompipe claim\n  add C9\n"})
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith("doc.md:1: names `claim add`"), problems)

    def test_near_misses_are_not_removed_names(self):
        text = ("`tests/test_staleness.py`; a claim edited by hand; `atompipe claim "
                "physical C5 pass`; `atompipe packs add fdm-print`; `last_runs`; "
                "`atompipe decide --title x`; `atompipe gate selftest`; "
                "`--set-entry-point`; `records_run`\n")
        self.assertEqual(removed_name_problems({"doc.md": text}), [])


class SiteMetaKeysAreDocumented(unittest.TestCase):
    """Every key of `state.json`'s `meta`, in SITE_CONTRACT's ### `meta` table.

    The top-level check (SiteStateKeysAreDocumented) passes a `meta` whatever it
    holds, and `meta` is where the page's staleness lives: `stale`,
    `stale_reason`, `records_digest`. The same real build, borrowed."""

    setUp = SiteStateKeysAreDocumented.setUp

    def _meta_keys(self) -> list[str]:
        with open(os.path.join(self.root, "site", "data", "state.json"),
                  encoding="utf-8") as fh:
            return sorted(json.load(fh)["meta"])

    def test_every_meta_key_is_documented(self):
        keys = self._meta_keys()
        self.assertIn("records_digest", keys, "the build wrote no real meta")
        problems = site_meta_problems(keys, _read(SITE_CONTRACT))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_a_planted_meta_key_is_caught(self):
        problems = site_meta_problems(self._meta_keys() + ["planted_meta_u31"],
                                      _read(SITE_CONTRACT))
        self.assertEqual(problems, ["state.json meta key `planted_meta_u31` is not in "
                                    "SITE_CONTRACT's ### `meta` section"])


if __name__ == "__main__":
    unittest.main()
