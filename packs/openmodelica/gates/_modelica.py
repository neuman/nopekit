# SPDX-License-Identifier: Apache-2.0
"""Everything the openmodelica gates need that is not a gate: a Modelica source
scanner, an OMC result-file reader, and a subprocess driver for ``omc``.

It is deliberately a separate module from ``modelica.py`` for the same reason the
physics of an analytic pack is: a disputed number must be reproducible at a REPL
without building a ``GateContext``.

    >>> import _modelica as M
    >>> model = M.scan_sources(["selftest/assets/model"])
    >>> [c.name for c in model.class_list]
    ['ThermalTank', 'Tank', 'TankRun']

**Standard library only, at module scope and everywhere else.** The tier-2 gates
shell out to ``omc`` through :func:`run_mos`; nothing here imports OMPython, and
that is a decision with a reason (see ``references/driving-omc.md``): a ZMQ
session needs a matching client library, a live server process and a port, while
a script file and a CLI need a file and a CLI. The reference implementation this
pack was extracted from drove omc both ways and only the script form survived
being run on a machine that was not the author's.

Three things in here are worth knowing before you read a gate:

*The scanner is a scanner, not a compiler.* It knows class nesting, component
declarations, ``extends``, short class definitions and modification syntax. It
does NOT evaluate expressions, resolve libraries it has not been pointed at, or
understand ``redeclare``/``constrainedby`` semantics. Everything it cannot
resolve is reported as unresolved, never as absent — the difference between "this
variable does not exist" and "I could not see whether it exists" is the whole
credibility of :func:`resolve_variable`.

*The result reader streams.* A 24-hour run at 1 Hz with 200 variables is a
17-million-cell CSV, and a gate that loads it into a list of lists to find out
whether the last row reached stopTime has turned a two-second check into a
swap storm. :func:`result_scan` holds only the time column and a capped list of
problems; :func:`result_series` holds only the columns it was asked for.

*Nothing here ever writes a "default" number.* A missing file, an unreadable
column or an absent parameter comes back as ``None`` or raises
:class:`ModelicaError`, and the gate turns that into a SKIP with the reason in
it. A pack that invents a stopTime produces a green verdict for a question
nobody asked.
"""
from __future__ import annotations

import csv
import dataclasses
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence


class ModelicaError(Exception):
    """Something the caller supplied cannot be read. Always carries the path."""


# =========================================================================== #
# 1. the Modelica source scanner
# =========================================================================== #

#: Keywords that open a class definition. ``operator`` is here and is also a
#: prefix (``operator record``, ``operator function``); the scanner disambiguates
#: by looking at the token after it.
CLASS_KEYWORDS = frozenset({
    "model", "class", "block", "record", "connector", "package", "function",
    "type", "operator",
})

#: Words that may sit in front of a class keyword. A statement made only of these
#: plus a class keyword is a class header, not a component declaration — which is
#: how ``partial model Foo`` is told apart from ``Real partial``  (not legal, but
#: the test has to be positive rather than "not a known type").
CLASS_PREFIXES = frozenset({
    "encapsulated", "partial", "final", "replaceable", "expandable", "impure",
    "pure", "operator", "redeclare", "inner", "outer",
})

#: Type prefixes that may precede the type specifier of a component clause.
TYPE_PREFIXES = frozenset({
    "final", "inner", "outer", "replaceable", "each", "flow", "stream",
    "discrete", "parameter", "constant", "input", "output", "redeclare",
})

#: Words that end the declaration section of a class or change visibility.
#: Words that follow `end` when it closes a CONTROL BLOCK rather than a class.
#: Modelica spells both with the same keyword, and telling them apart is the whole
#: difference between a scanner that reads real models and one that reads only
#: models without conditionals.
BLOCK_END_WORDS = frozenset({"if", "when", "for", "while", "try"})

SECTION_WORDS = frozenset({"equation", "algorithm", "public", "protected",
                           "initial", "external"})

#: Built-in scalar types whose values are not physical quantities, so a missing
#: ``unit`` on one of them is not a defect. ``Real`` is deliberately absent: an
#: undeclared unit on a Real IS the defect ``modelica.source_hygiene`` exists to
#: find.
NON_PHYSICAL_BASE_TYPES = frozenset({"Integer", "Boolean", "String",
                                     "enumeration", "Clock", "ExternalObject"})

#: Namespaces whose types carry a unit in their own definition, so a declaration
#: using one is already unit-checked even though this scanner never opened the
#: library. Both the modern (``Modelica.Units.SI``) and legacy
#: (``Modelica.SIunits``) spellings, plus the near-universal ``SI`` import alias.
_SI_NAMESPACE_RE = re.compile(
    r"(?:^|\.)(?:SI|SIunits|NonSI)\.|(?:^|\.)Units\.SI\.")

_TOKEN_RE = re.compile(
    r'''(?P<string>"(?:[^"\\]|\\.)*")
      | (?P<qident>'(?:[^'\\]|\\.)*')
      | (?P<ident>[A-Za-z_][A-Za-z_0-9]*)
      | (?P<number>[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?)
      | (?P<punct>\S)''',
    re.VERBOSE,
)


@dataclass
class Token:
    kind: str
    text: str          # source spelling, quotes included for strings
    line: int
    start: int = 0     # character offset into the ORIGINAL file text
    end: int = 0

    @property
    def value(self) -> str:
        """A string token's contents, with the two escapes Modelica actually uses."""
        if self.kind != "string":
            return self.text
        body = self.text[1:-1]
        return body.replace('\\"', '"').replace("\\\\", "\\")


@dataclass
class Component:
    """One declared component: a parameter, a variable, a constant, a submodel."""

    name: str
    type_name: str
    prefixes: tuple[str, ...] = ()
    description: str = ""
    unit: str = ""                 # from a `unit="..."` modification, if any
    unit_source: str = ""          # "modifier" | "type alias X" | "SI type"
    file: str = ""
    line: int = 0
    owner: str = ""                # qualified name of the class declaring it
    #: character span of the description string literal in the original file, so a
    #: fixture can remove exactly it and nothing else
    description_span: tuple[int, int] | None = None
    #: character span of the ``unit`` keyword in this declaration's modification
    unit_span: tuple[int, int] | None = None

    @property
    def is_parameter(self) -> bool:
        return "parameter" in self.prefixes

    @property
    def is_constant(self) -> bool:
        return "constant" in self.prefixes

    @property
    def where(self) -> str:
        return f"{os.path.basename(self.file)}:{self.line}"


@dataclass
class ModelicaClass:
    name: str
    kind: str = "model"
    qualified: str = ""
    description: str = ""
    file: str = ""
    line: int = 0
    components: list[Component] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)
    nested: list["ModelicaClass"] = field(default_factory=list)
    in_declarations: bool = True

    def component(self, name: str) -> Component | None:
        return next((c for c in self.components if c.name == name), None)


@dataclass
class SourceModel:
    """Every class and component the scanner found across a set of files."""

    files: list[str] = field(default_factory=list)
    class_list: list[ModelicaClass] = field(default_factory=list)
    by_qualified: dict[str, ModelicaClass] = field(default_factory=dict)
    by_short: dict[str, list[ModelicaClass]] = field(default_factory=dict)
    #: alias name (short and qualified) -> (base type, unit). Populated from short
    #: class definitions such as `type Temperature = Real(unit = "K");`, which is
    #: how a package declares SI types without depending on the MSL.
    aliases: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: alias name -> (file, span of its ``unit`` keyword), for the same reason
    alias_unit_spans: dict[str, tuple[str, tuple[int, int]]] = field(default_factory=dict)
    unreadable: list[tuple[str, str]] = field(default_factory=list)

    @property
    def components(self) -> list[Component]:
        return [c for cls in self.class_list for c in cls.components]

    @property
    def parameters(self) -> list[Component]:
        return [c for c in self.components if c.is_parameter]

    def find_class(self, name: str) -> ModelicaClass | None:
        """Resolve a class name: exact qualified, then dotted suffix, then unique short."""
        if not name:
            return None
        hit = self.by_qualified.get(name)
        if hit is not None:
            return hit
        for qualified, cls in self.by_qualified.items():
            if qualified.endswith("." + name):
                return cls
        short = self.by_short.get(name.rsplit(".", 1)[-1]) or []
        return short[0] if len(short) == 1 else None


def strip_comments(text: str) -> str:
    """Remove // and /* */ comments, preserving string literals and line numbers.

    Line numbers survive because a multi-line block comment is replaced by its own
    newlines rather than deleted — a scanner that reports the wrong line for an
    undescribed parameter sends the reader to the wrong declaration, and in a file
    of two hundred parameters that is the same as reporting nothing.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif ch == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("\n" * text.count("\n", i, j))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    line = 1
    pos = 0
    for match in _TOKEN_RE.finditer(text):
        line += text.count("\n", pos, match.start())
        pos = match.start()
        kind = match.lastgroup or "punct"
        tokens.append(Token(kind=kind, text=match.group(), line=line,
                            start=match.start(), end=match.end()))
    return tokens


def _find_unit(tokens: Sequence[Token]) -> tuple[str, Token | None]:
    """``unit = "W/K"`` inside a modification: its value and the ``unit`` token.

    The token comes back as well as the value because ``selftest/bad_modelica.py``
    rewrites the pack's own sources to build its known-bad fixture, and a fixture
    that edits source with a regex edits the wrong thing the first time somebody
    writes ``displayUnit`` or puts a unit inside a nested modification.
    """
    for i in range(len(tokens) - 2):
        if (tokens[i].kind == "ident" and tokens[i].text == "unit"
                and tokens[i + 1].text == "=" and tokens[i + 2].kind == "string"):
            return tokens[i + 2].value, tokens[i]
    return "", None


def _balanced(tokens: Sequence[Token], start: int) -> tuple[list[Token], int]:
    """Tokens of the bracketed group at ``start``, and the index just past it."""
    openers, closers = "([{", ")]}"
    depth = 0
    i = start
    while i < len(tokens):
        text = tokens[i].text
        if text in openers:
            depth += 1
        elif text in closers:
            depth -= 1
            if depth == 0:
                return list(tokens[start:i + 1]), i + 1
        i += 1
    return list(tokens[start:]), len(tokens)


def _render(tokens: Iterable[Token]) -> str:
    return " ".join(t.text for t in tokens)


def _split_top_level(tokens: Sequence[Token], sep: str = ",") -> list[list[Token]]:
    parts: list[list[Token]] = [[]]
    depth = 0
    for token in tokens:
        if token.text in "([{":
            depth += 1
        elif token.text in ")]}":
            depth -= 1
        if depth == 0 and token.text == sep:
            parts.append([])
            continue
        parts[-1].append(token)
    return parts


def _drop_trailing_annotation(tokens: list[Token]) -> list[Token]:
    """Remove a trailing ``annotation(...)`` so the string comment is last again."""
    for i, token in enumerate(tokens):
        if token.kind == "ident" and token.text == "annotation":
            return tokens[:i]
    return tokens


class _Scanner:
    """Token state machine. One instance per file; results land in a SourceModel."""

    def __init__(self, model: SourceModel, path: str) -> None:
        self.model = model
        self.path = path
        self.stack: list[ModelicaClass] = []
        self.desync: list[str] = []
        #: `within A.B;` means every class in this file is nested inside package
        #: A.B. Discarding it registered each class at the top level unqualified,
        #: so in any multi-file package — which is how every real Modelica library
        #: is laid out — a claim naming `Plant.Tank.T` could not resolve against a
        #: file that declared `Tank` under `within Plant;`.
        self.within: str = ""

    def run(self, tokens: list[Token]) -> None:
        pending: list[Token] = []
        depth = 0
        i = 0
        n = len(tokens)
        while i < n:
            token = tokens[i]
            if token.kind == "punct":
                if token.text in "([{":
                    depth += 1
                elif token.text in ")]}":
                    depth = max(0, depth - 1)
                elif token.text == ";" and depth == 0:
                    self._statement(pending)
                    pending = []
                    i += 1
                    continue
                pending.append(token)
                i += 1
                continue

            if token.kind == "ident" and depth == 0:
                word = token.text
                if word == "end":
                    self._statement(pending)
                    pending = []
                    j = i + 1
                    # `end` closes EITHER a class (`end Tank;`) or a control block
                    # (`end if;`, `end when;`, `end for;`, `end while;`). Popping the
                    # class stack for both is the single worst bug this scanner can
                    # have, and it is silent: one ordinary if-equation closes the
                    # enclosing model, every later declaration is attributed to its
                    # parent package or dropped once the stack empties, and the gate
                    # then reports real variables as missing and invented ones as
                    # found. Conditional equations are core Modelica, so this hits a
                    # large fraction of real models.
                    closes_block = (j < n and tokens[j].kind == "ident"
                                    and tokens[j].text in BLOCK_END_WORDS)
                    closed_name = ""
                    if j < n and tokens[j].kind in ("ident", "qident"):
                        closed_name = tokens[j].text.strip("'")
                        j += 1
                    if j < n and tokens[j].text == ";":
                        j += 1
                    if not closes_block and self.stack:
                        # Modelica requires `end <name>;` to repeat the class name, so
                        # a mismatch means the scanner has lost sync. Popping anyway
                        # compounds the error; recording it lets `source_hygiene` say
                        # the file could not be read rather than quietly lying.
                        if closed_name and closed_name != self.stack[-1].name:
                            self.desync.append(
                                f"{self.path}: 'end {closed_name};' while inside "
                                f"'{self.stack[-1].name}'")
                        else:
                            self.stack.pop()
                    i = j
                    continue
                if word in SECTION_WORDS and not pending:
                    if word in ("equation", "algorithm", "external") and self.stack:
                        self.stack[-1].in_declarations = False
                    i += 1
                    continue
                if word in CLASS_KEYWORDS and self._is_class_header(pending):
                    consumed = self._open_class(tokens, i, word)
                    if consumed is not None:
                        pending = []
                        i = consumed
                        continue
                    # `operator` used as a prefix (operator record / operator function)
                    pending.append(token)
                    i += 1
                    continue
            pending.append(token)
            i += 1
        self._statement(pending)

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def _is_class_header(pending: list[Token]) -> bool:
        return all(t.kind == "ident" and t.text in CLASS_PREFIXES for t in pending)

    def _open_class(self, tokens: list[Token], i: int, word: str) -> int | None:
        """Push a new class and return the index after its header, or None."""
        n = len(tokens)
        j = i + 1
        if j >= n or tokens[j].kind not in ("ident", "qident"):
            return None
        if word == "operator" and tokens[j].text in ("record", "function"):
            return None                      # a prefix, not the class keyword
        name = tokens[j].text.strip("'")
        k = j + 1
        if k < n and tokens[k].text == "=":
            return None                      # short class definition; _statement handles it
        description = ""
        if k < n and tokens[k].kind == "string":
            description = tokens[k].value
            k += 1
        parent = self.stack[-1] if self.stack else None
        if parent:
            qualified = f"{parent.qualified}.{name}"
        else:
            qualified = f"{self.within}.{name}" if self.within else name
        cls = ModelicaClass(name=name, kind=word, qualified=qualified,
                            description=description, file=self.path, line=tokens[i].line)
        if parent is not None:
            parent.nested.append(cls)
        self.model.class_list.append(cls)
        self.model.by_qualified[qualified] = cls
        self.model.by_short.setdefault(name, []).append(cls)
        self.stack.append(cls)
        return k

    def _statement(self, pending: list[Token]) -> None:
        if not pending:
            return
        head = pending[0]
        if head.kind != "ident":
            return
        if head.text == "within":
            self.within = self._dotted(pending, 1)
            return
        if head.text in ("import", "annotation", "constrainedby"):
            return
        if head.text == "extends":
            base = self._dotted(pending, 1)
            if base and self.stack:
                self.stack[-1].extends.append(base)
            return
        idx = 0
        while idx < len(pending) and pending[idx].kind == "ident" \
                and pending[idx].text in CLASS_PREFIXES:
            idx += 1
        if idx < len(pending) and pending[idx].kind == "ident" \
                and pending[idx].text in CLASS_KEYWORDS:
            self._short_class(pending[idx:])
            return
        if self.stack and self.stack[-1].in_declarations:
            self._component_clause(pending)

    @staticmethod
    def _dotted(tokens: Sequence[Token], start: int) -> str:
        parts: list[str] = []
        i = start
        while i < len(tokens) and tokens[i].kind in ("ident", "qident"):
            parts.append(tokens[i].text)
            if i + 1 < len(tokens) and tokens[i + 1].text == ".":
                i += 2
                continue
            break
        return ".".join(parts)

    def _short_class(self, tokens: list[Token]) -> None:
        """``type Temperature = Real(unit = "K") "Absolute temperature";``

        This is how a self-contained package declares its SI types, and resolving
        it is what lets ``source_hygiene`` credit a declaration that carries no
        ``unit`` modifier of its own.
        """
        if len(tokens) < 4 or tokens[1].kind not in ("ident", "qident"):
            return
        name = tokens[1].text.strip("'")
        if tokens[2].text != "=":
            return
        base = self._dotted(tokens, 3)
        rest = tokens[3:]
        unit, unit_token = _find_unit(rest)
        if unit_token is not None:
            pass
        elif base in self.model.aliases:
            base, unit = self.model.aliases[base]
        elif _SI_NAMESPACE_RE.search(base):
            unit = "(from SI type)"
        parent = self.stack[-1] if self.stack else None
        qualified = f"{parent.qualified}.{name}" if parent else name
        self.model.aliases[name] = (base, unit)
        self.model.aliases[qualified] = (base, unit)
        if unit_token is not None:
            span = (self.path, (unit_token.start, unit_token.end))
            self.model.alias_unit_spans[name] = span
            self.model.alias_unit_spans[qualified] = span

    def _component_clause(self, tokens: list[Token]) -> None:
        n = len(tokens)
        idx = 0
        prefixes: list[str] = []
        while idx < n and tokens[idx].kind == "ident" and tokens[idx].text in TYPE_PREFIXES:
            prefixes.append(tokens[idx].text)
            idx += 1
        if idx >= n or tokens[idx].kind not in ("ident", "qident"):
            return
        type_parts = [tokens[idx].text]
        idx += 1
        while idx + 1 < n and tokens[idx].text == "." and tokens[idx + 1].kind in ("ident", "qident"):
            type_parts.append(tokens[idx + 1].text)
            idx += 2
        type_name = ".".join(type_parts)
        type_mod: list[Token] = []
        if idx < n and tokens[idx].text == "(":
            type_mod, idx = _balanced(tokens, idx)
        if idx < n and tokens[idx].text == "[":
            _span, idx = _balanced(tokens, idx)

        owner = self.stack[-1] if self.stack else None
        if owner is None:
            return
        for part in _split_top_level(tokens[idx:]):
            component = self._one_component(part, type_name, type_mod,
                                            tuple(prefixes), owner)
            if component is not None:
                owner.components.append(component)

    def _one_component(self, tokens: list[Token], type_name: str,
                       type_mod: Sequence[Token], prefixes: tuple[str, ...],
                       owner: ModelicaClass) -> Component | None:
        if not tokens or tokens[0].kind not in ("ident", "qident"):
            return None
        name = tokens[0].text.strip("'")
        idx = 1
        comp_mod: list[Token] = []
        if idx < len(tokens) and tokens[idx].text == "[":
            _span, idx = _balanced(tokens, idx)
        if idx < len(tokens) and tokens[idx].text == "(":
            comp_mod, idx = _balanced(tokens, idx)
        rest = _drop_trailing_annotation(list(tokens[idx:]))
        bound = bool(rest) and rest[0].text == "="
        value = rest[1:] if bound else rest

        description = ""
        description_span: tuple[int, int] | None = None
        if value and value[-1].kind == "string":
            # `parameter String name = "unnamed";` — the one string IS the value,
            # not a description. `= "unnamed" "The name"` has both. A declaration
            # with no binding at all (`Real T "Bulk temperature";`) reaches here
            # with value == [the string], and there the string IS the description.
            if len(value) > 1 or not bound:
                description = value[-1].value
                description_span = (value[-1].start, value[-1].end)

        unit, unit_token = _find_unit(comp_mod)
        if unit_token is None:
            unit, unit_token = _find_unit(type_mod)
        return Component(
            name=name, type_name=type_name, prefixes=prefixes,
            description=description, unit=unit,
            unit_source="modifier" if unit_token is not None else "",
            file=self.path, line=tokens[0].line, owner=owner.qualified,
            description_span=description_span,
            unit_span=(unit_token.start, unit_token.end) if unit_token is not None else None,
        )


def scan_text(text: str, path: str, model: SourceModel | None = None) -> SourceModel:
    model = model if model is not None else SourceModel()
    _Scanner(model, path).run(tokenize(strip_comments(text)))
    if path not in model.files:
        model.files.append(path)
    return model


def gather_mo_files(entries: Iterable[str], root: str = "") -> list[str]:
    """Every .mo file named by ``entries`` (files or directories), deduplicated.

    A directory containing ``package.mo`` at its top yields the whole tree, in
    the order ``package.mo`` first: that is how omc loads a structured package
    and loading the leaves first produces "class X not found" for a class that is
    in the very next file.

    An entry that is neither a file nor a directory yet is skipped — and that
    absence is an input of the calling gate: the spine records the
    ``os.path.isfile``/``isdir`` questions below, so the verdict goes stale when
    the file appears. What slipped through before it did (review round 1): with
    ``[model/A.mo, model/B.mo]`` named and only A present, ``source_hygiene``
    recorded ``{model/A.mo}`` and kept a Fresh PASS after B.mo appeared with an
    undocumented, unitless parameter.
    """
    out: list[str] = []
    for entry in entries:
        path = resolve_path(root, str(entry))
        if os.path.isfile(path):
            out.append(path)
            continue
        if not os.path.isdir(path):
            continue
        found: list[str] = []
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for filename in sorted(filenames):
                if filename.endswith(".mo"):
                    found.append(os.path.join(dirpath, filename))
        found.sort(key=lambda p: (os.path.basename(p) != "package.mo", p))
        out.extend(found)
    seen: set[str] = set()
    unique: list[str] = []
    for path in out:
        real = os.path.abspath(path)
        if real not in seen:
            seen.add(real)
            unique.append(real)
    return unique


def scan_sources(entries: Iterable[str], root: str = "") -> SourceModel:
    """Scan every .mo file under ``entries``. Unreadable files are recorded, not raised."""
    model = SourceModel()
    for path in gather_mo_files(entries, root):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as exc:
            model.unreadable.append((path, str(exc)))
            continue
        scan_text(text, path, model)
    return model


# -- unit / hygiene classification ------------------------------------------ #
def base_type(model: SourceModel, type_name: str, _depth: int = 0) -> str:
    """Follow local ``type`` aliases down to a builtin, or return the name itself."""
    if _depth > 8 or not type_name:
        return type_name
    if type_name in model.aliases:
        return base_type(model, model.aliases[type_name][0], _depth + 1)
    return type_name


def component_unit(model: SourceModel, component: Component) -> tuple[str, str]:
    """``(unit, where it came from)`` — ``("", "")`` when the declaration has none."""
    if component.unit:
        return component.unit, "modifier"
    name = component.type_name
    seen: set[str] = set()
    while name and name not in seen:
        seen.add(name)
        if _SI_NAMESPACE_RE.search(name):
            return "(carried by the SI type)", f"SI type {name}"
        alias = model.aliases.get(name)
        if alias is None:
            break
        if alias[1]:
            return alias[1], f"type alias {name}"
        name = alias[0]
    return "", ""


def classify_parameter(model: SourceModel, component: Component) -> str:
    """``'non-physical' | 'unit-ok' | 'unitless' | 'unresolved-type'``.

    ``unitless`` is the only one that counts as a defect, and it is reached only
    for a declaration whose base type really is ``Real``. A type this scanner
    cannot resolve — a component of a record, a class from a library nobody
    pointed it at — comes back ``unresolved-type`` and is counted separately,
    because reporting it as a unit defect would make the gate's headline number
    depend on how much of the library tree happened to be on the scan path.
    """
    unit, _source = component_unit(model, component)
    if unit:
        return "unit-ok"
    base = base_type(model, component.type_name)
    if base in NON_PHYSICAL_BASE_TYPES:
        return "non-physical"
    if base == "Real":
        return "unitless"
    return "unresolved-type"


# -- variable resolution ----------------------------------------------------- #
_SUBSCRIPT_RE = re.compile(r"\[[^\]]*\]")
_DER_RE = re.compile(r"^der\s*\(\s*(.+?)\s*\)$")


def normalise_variable(name: str) -> str:
    """``der(tank.T[2])`` -> ``tank.T``. The spellings a result header actually uses."""
    text = (name or "").strip()
    match = _DER_RE.match(text)
    if match:
        text = match.group(1).strip()
    return _SUBSCRIPT_RE.sub("", text).strip()


def _inherited_components(model: SourceModel, cls: ModelicaClass,
                          _depth: int = 0) -> list[Component]:
    out = list(cls.components)
    if _depth > 6:
        return out
    for base in cls.extends:
        parent = model.find_class(base)
        if parent is not None:
            out.extend(_inherited_components(model, parent, _depth + 1))
    return out


def resolve_variable(model: SourceModel, root_class: str, name: str) -> tuple[str, str]:
    """Can ``name`` be reached from ``root_class``? ``(state, explanation)``.

    States, and the distinction that matters:

    * ``found``        — every segment resolved to a declared component.
    * ``missing``      — a segment does not exist in a class this scanner CAN see.
      This is the drift the gate is looking for: a variable renamed in the model
      and left behind in a claim.
    * ``unresolved``   — the walk reached a component whose type is defined
      outside the scanned sources, so the remaining segments cannot be checked
      either way. Reported, never counted as a defect. A scanner that guessed
      here would fail a claim for naming a variable of a library component, which
      is the most common correct thing to do.
    """
    plain = normalise_variable(name)
    if not plain:
        return "missing", "empty variable name"
    segments = plain.split(".")
    cls = model.find_class(root_class) if root_class else None
    if cls is None:
        # No root class to walk from. Fall back to "is this name declared
        # anywhere", and say so: it is a weaker check and the verdict must not
        # present it as the strong one.
        declared = {c.name for c in model.components}
        if segments[-1] in declared or segments[0] in declared:
            return "unresolved", (f"no modelica_class to resolve against; "
                                  f"{segments[0]!r} is declared somewhere in the sources")
        return "missing", (f"no modelica_class to resolve against and no component "
                           f"named {segments[0]!r} anywhere in the scanned sources")

    trail = [cls.qualified]
    for index, segment in enumerate(segments):
        components = _inherited_components(model, cls)
        match = next((c for c in components if c.name == segment), None)
        if match is None:
            return "missing", (f"{cls.qualified} declares no component {segment!r} "
                               f"(walked {'.'.join(trail)})")
        if index == len(segments) - 1:
            return "found", f"{match.owner}.{match.name} at {match.where}"
        nested = model.find_class(match.type_name)
        if nested is None:
            return "unresolved", (f"{segment} is a {match.type_name}, which is not in "
                                  f"the scanned sources — the rest of {plain} cannot "
                                  f"be checked from here")
        cls = nested
        trail.append(segment)
    return "missing", "unreachable"


# =========================================================================== #
# 2. the OMC result file
# =========================================================================== #
@dataclass
class ResultScan:
    """What one streaming pass over a result CSV found."""

    path: str
    columns: list[str] = field(default_factory=list)
    rows: int = 0
    time: list[float] = field(default_factory=list)
    nonfinite: list[tuple[int, str, str]] = field(default_factory=list)
    unparseable: list[tuple[int, str, str]] = field(default_factory=list)
    backwards: list[tuple[int, float, float]] = field(default_factory=list)
    ragged: list[int] = field(default_factory=list)

    @property
    def first_time(self) -> float | None:
        return self.time[0] if self.time else None

    @property
    def last_time(self) -> float | None:
        return self.time[-1] if self.time else None


#: How many problems of each kind a scan keeps. A result file that is entirely NaN
#: produces one problem per cell; a verdict that tries to name seventeen million of
#: them is a verdict nobody reads and a process that runs out of memory proving it.
PROBLEM_CAP = 25

_NAN_SPELLINGS = {"nan", "-nan", "+nan", "1.#qnan", "-1.#ind", "1.#ind", "nan(ind)"}
_INF_SPELLINGS = {"inf", "+inf", "-inf", "infinity", "-infinity",
                  "1.#inf", "-1.#inf"}


def _parse_cell(raw: str) -> tuple[float | None, str]:
    """``(value, state)`` where state is ``'ok' | 'nonfinite' | 'bad'``.

    NaN and Inf are returned as floats AND flagged, because the two questions
    ("is there a NaN in here" and "what is the final value") are asked by
    different gates and collapsing them would make one of them lie.
    """
    text = (raw or "").strip().strip('"')
    if not text:
        return None, "bad"
    lowered = text.lower()
    if lowered in _NAN_SPELLINGS:
        return math.nan, "nonfinite"
    if lowered in _INF_SPELLINGS:
        return (-math.inf if lowered.startswith("-") else math.inf), "nonfinite"
    try:
        value = float(text)
    except ValueError:
        return None, "bad"
    if math.isnan(value) or math.isinf(value):
        return value, "nonfinite"
    return value, "ok"


def _open_result(path: str):
    if not os.path.isfile(path):
        raise ModelicaError(f"result file {path} does not exist")
    try:
        return open(path, "r", encoding="utf-8", errors="replace", newline="")
    except OSError as exc:
        raise ModelicaError(f"cannot read result file {path}: {exc}") from exc


def result_columns(path: str) -> list[str]:
    """The header row, unquoted. omc writes it quoted; csv strips that for us."""
    with _open_result(path) as handle:
        for row in csv.reader(handle):
            if not row:
                continue
            return [cell.strip().strip('"') for cell in row]
    raise ModelicaError(f"result file {path} is empty — no header row")


def result_scan(path: str) -> ResultScan:
    """One streaming pass: time column, non-finite cells, ragged rows, backsteps.

    Only the time column and a capped list of problems are retained, so this is
    O(1) in memory over the width and length of the file.
    """
    scan = ResultScan(path=path)
    with _open_result(path) as handle:
        reader = csv.reader(handle)
        for index, row in enumerate(reader):
            if not row or all(not cell.strip() for cell in row):
                continue
            if not scan.columns:
                scan.columns = [cell.strip().strip('"') for cell in row]
                continue
            scan.rows += 1
            if len(row) != len(scan.columns) and len(scan.ragged) < PROBLEM_CAP:
                scan.ragged.append(index + 1)
            for position, cell in enumerate(row):
                name = scan.columns[position] if position < len(scan.columns) \
                    else f"column{position + 1}"
                value, state = _parse_cell(cell)
                if state == "nonfinite" and len(scan.nonfinite) < PROBLEM_CAP:
                    scan.nonfinite.append((index + 1, name, cell.strip()))
                elif state == "bad" and len(scan.unparseable) < PROBLEM_CAP:
                    scan.unparseable.append((index + 1, name, cell.strip()[:32]))
                if position == 0 and state == "ok" and value is not None:
                    if scan.time and value < scan.time[-1] and len(scan.backwards) < PROBLEM_CAP:
                        scan.backwards.append((index + 1, scan.time[-1], value))
                    scan.time.append(value)
    if not scan.columns:
        raise ModelicaError(f"result file {path} is empty — no header row")
    return scan


def result_series(path: str, names: Sequence[str]) -> dict[str, list[float]]:
    """Only the named columns, plus ``time``. Missing names raise ``ModelicaError``.

    Non-finite cells come through as ``nan``/``inf`` floats rather than being
    dropped: ``modelica.solution_valid`` is what refuses a run containing them,
    and a reader that silently skipped them would let a claim be extracted from a
    diverged run with nothing in the verdict to say so.
    """
    wanted = list(dict.fromkeys(["time", *names]))
    columns = result_columns(path)
    index = {name: position for position, name in enumerate(columns)}
    missing = [name for name in wanted if name not in index]
    if missing:
        raise ModelicaError(
            f"{os.path.basename(path)} has no column(s) {', '.join(missing)}; it has "
            f"{len(columns)}: {', '.join(columns[:10])}"
            + ("..." if len(columns) > 10 else ""))
    out: dict[str, list[float]] = {name: [] for name in wanted}
    with _open_result(path) as handle:
        reader = csv.reader(handle)
        header_seen = False
        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue
            if not header_seen:
                header_seen = True
                continue
            for name in wanted:
                position = index[name]
                value, _state = _parse_cell(row[position]) if position < len(row) \
                    else (None, "bad")
                out[name].append(math.nan if value is None else value)
    return out


#: The reducers a project may name in ``modelica_claim_variables``. ``at`` is
#: handled separately because it takes an argument.
REDUCERS = ("final", "initial", "min", "max", "mean", "absmax", "range")


def reduce_series(times: Sequence[float], values: Sequence[float], reducer: str,
                  at: float | None = None) -> tuple[float, str]:
    """Collapse a series to one number. ``(value, how it was taken)``.

    ``at`` linearly interpolates between the bracketing samples, which is the only
    honest reading of a variable at a time the solver did not output. Extrapolation
    past either end raises instead: a value read off the end of a run that stopped
    early is exactly the number this pack exists to refuse.
    """
    if not values:
        raise ModelicaError("series is empty")
    name = (reducer or "final").strip().lower()
    if name == "at":
        if at is None:
            raise ModelicaError("reducer 'at' needs a time")
        return interpolate(times, values, at), f"value at t={at:g} s"
    if name == "final":
        return float(values[-1]), f"final value at t={times[-1]:g} s"
    if name == "initial":
        return float(values[0]), f"initial value at t={times[0]:g} s"
    if name == "min":
        return float(min(values)), "minimum over the run"
    if name == "max":
        return float(max(values)), "maximum over the run"
    if name == "absmax":
        return float(max(values, key=abs)), "largest magnitude over the run"
    if name == "mean":
        return float(sum(values) / len(values)), f"unweighted mean of {len(values)} samples"
    if name == "range":
        return float(max(values) - min(values)), "max minus min over the run"
    raise ModelicaError(
        f"unknown reducer {reducer!r}; use one of {', '.join(REDUCERS)} or "
        f"{{'reducer': 'at', 'time': <seconds>}}")


def interpolate(times: Sequence[float], values: Sequence[float], when: float) -> float:
    """Linear interpolation, refusing to extrapolate.

    omc emits duplicate time points at events (the same t appears twice with
    different values either side of the discontinuity). The bracketing search
    takes the LAST sample at or before ``when``, which is the post-event value —
    the one a reader asking "what is it at t" means.
    """
    if not times:
        raise ModelicaError("no time column to interpolate against")
    if when < times[0] - 1e-12 or when > times[-1] + 1e-12:
        raise ModelicaError(
            f"t={when:g} s is outside the run's {times[0]:g}..{times[-1]:g} s — "
            f"reading a value there would be extrapolation, not measurement")
    best = 0
    for i, t in enumerate(times):
        if t <= when + 1e-12:
            best = i
        else:
            break
    if best >= len(times) - 1:
        return float(values[-1])
    t0, t1 = times[best], times[best + 1]
    if t1 <= t0:
        return float(values[best])
    frac = (when - t0) / (t1 - t0)
    return float(values[best] + frac * (values[best + 1] - values[best]))


def read_table_csv(path: str) -> tuple[list[str], dict[str, list[float]]]:
    """A plain CSV of numbers with a header row — the mirror's format.

    Deliberately the same shape as an OMC result file (a ``time`` column plus one
    column per variable) so an independent implementation can be exported from a
    spreadsheet, a notebook or another tool with no adapter in between.
    """
    if not os.path.isfile(path):
        raise ModelicaError(f"file {path} does not exist")
    with _open_result(path) as handle:
        reader = csv.reader(handle)
        columns: list[str] = []
        data: dict[str, list[float]] = {}
        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue
            if not columns:
                columns = [cell.strip().strip('"') for cell in row]
                data = {name: [] for name in columns}
                continue
            for position, name in enumerate(columns):
                value, _state = _parse_cell(row[position]) if position < len(row) \
                    else (None, "bad")
                data[name].append(math.nan if value is None else value)
    if not columns:
        raise ModelicaError(f"{path} is empty — no header row")
    return columns, data


# =========================================================================== #
# 3. driving omc
# =========================================================================== #
#: The marker the generated .mos scripts print around each step's output. Chosen
#: to be something no Modelica model, error message or file path will contain, so
#: segmentation never eats a line of a real error string.
MARK = "@@NOPEKIT:"

BALANCE_RE = re.compile(r"has\s+(\d+)\s+equation\(s\)\s+and\s+(\d+)\s+variable\(s\)")
RESULT_FILE_RE = re.compile(r'resultFile\s*=\s*"((?:[^"\\]|\\.)*)"')
MESSAGES_RE = re.compile(r'messages\s*=\s*"((?:[^"\\]|\\.)*)"', re.S)

#: Fragments that mean the run did not merely end — it was stopped. Lowercased
#: substring match, because the surrounding text differs between OM versions and
#: between the C and the bundled runtimes.
FAILURE_MARKERS = (
    "simulation execution failed",
    "assert |",
    "assertion",
    "terminate",
    "division by zero",
    "solver failed",
    "integrator failed",
    "nonlinear system",
    "initialization failed",
    "model terminate",
)


@dataclass
class OmcRun:
    """One ``omc script.mos`` invocation."""

    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    script_path: str = ""
    log_path: str = ""
    timed_out: bool = False
    duration_s: float = 0.0
    launch_error: str = ""

    @property
    def ok(self) -> bool:
        return not self.timed_out and not self.launch_error and self.returncode == 0

    def segment(self, name: str) -> str:
        """Text the script printed between ``MARK+name`` and the next marker."""
        return segments(self.stdout).get(name, "")


def segments(stdout: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = ""
    buffer: list[str] = []
    for line in (stdout or "").splitlines():
        if line.startswith(MARK):
            if current:
                out[current] = "\n".join(buffer).strip()
            current = line[len(MARK):].strip()
            buffer = []
            continue
        if current:
            buffer.append(line)
    if current:
        out[current] = "\n".join(buffer).strip()
    return out


def mos_string(text: str) -> str:
    """Escape a Python string into a Modelica string literal."""
    return str(text).replace("\\", "\\\\").replace('"', '\\"')



def printed_value(segment: str) -> str:
    """The value a .mos script PRINTED, ignoring what the interpreter echoed.

    omc's script interpreter echoes the result of every statement, including an
    assignment, so a segment built as::

        built := buildModel(X);      # echoes {"/path/X","X_init.xml"}
        print(built[1] + "\n");      # prints /path/X

    arrives as TWO lines, the echoed tuple first and the printed value second.
    Taking the whole segment as the value yields a string that begins with a brace
    and is nobody's path: a real omc reported a 61 kB executable as "not on disk"
    because the gate compared the echoed tuple against the filesystem.

    The printed value is always last, because the print follows the statement that
    produced it. Returns the last non-empty line, stripped of surrounding quotes.
    """
    for line in reversed((segment or "").splitlines()):
        line = line.strip()
        if line:
            return line.strip('"')
    return ""

def mos_mark(name: str) -> str:
    return f'print("{MARK}{name}\\n");'


def mos_preamble(sources: Sequence[str], libraries: Sequence[str] = ()) -> list[str]:
    """loadModel for each library, loadFile for each source, with the error string.

    Ordering is load-libraries-then-sources and it is not cosmetic: a source that
    ``extends`` an MSL class fails to instantiate if the library is not in the
    symbol table yet, and the resulting error names the user's class, not the
    missing library.
    """
    lines: list[str] = []
    lines.append(mos_mark("libraries"))
    for library in libraries:
        lines.append(f'print("loadModel({library}) = " + String(loadModel({library})) + "\\n");')
    lines.append(mos_mark("load"))
    for path in sources:
        lines.append(
            f'print("loadFile({mos_string(os.path.basename(path))}) = " + '
            f'String(loadFile("{mos_string(path)}")) + "\\n");')
    lines.append(mos_mark("loaderr"))
    lines.append('print(getErrorString() + "\\n");')
    return lines


#: Where a script's sources are copied, under the directory omc runs in:
#: ``<work_dir>/.sources/<script name>/<entry index>/<entry basename>``.
#: The dot is load-bearing: :func:`gather_mo_files`, and so every gate in this
#: pack, skips dot-directories, so a work directory that sits inside a source
#: entry never offers the copies back as sources. One tree per script, so
#: ``check.mos``, ``build.mos`` and ``simulate.mos`` each still reproduce against
#: the copies they loaded after the next gate has run. One directory per entry,
#: because two entries can share a basename (``a/model``, ``b/model``).
#: *Rejected:* ``sources/`` without the dot — with an out directory inside a
#: source entry, every run would read the previous run's copies as the
#: project's own sources.
STAGE_DIR = ".sources"


def stage_sources(entries: Iterable[str], root: str, work_dir: str,
                  name: str) -> list[tuple[str, str]]:
    """Copy every source entry under ``work_dir``; ``[(original, copy)]`` per entry.

    omc is run from ``work_dir``, and that directory is the one place an omc
    behind a wrapper is sure to see: the wrapper has to mount it, because omc
    writes its build there. Anything else is the wrapper's guess. So the gates
    hand omc copies under ``work_dir`` and never a path into the project.

    What slipped through: the gates handed ``loadFile`` absolute paths into the
    project — or, under ``gate selftest``, into the pack's own
    ``selftest/assets``. The wrapper ``references/installing.md`` gave mounts
    ``$PWD``, ``$HOME`` and the temp directory; the test suite runs every child
    under a temp ``HOME``, so a checkout under the real home was invisible to
    omc. omc 1.22 answered ``loadFile(ThermalTank.mo) = false`` with an EMPTY
    error string, and all three tier-2 gates read FAIL — "the sources did not
    load" — on the pack's own good baseline, while their controls were counted
    as fired on the same invisibility. The suite was green only where the
    checkout happened to sit under ``/tmp``.

    A file entry is copied as itself; a directory entry as its whole tree under
    its own name, which omc requires of a ``package.mo``, with ``package.order``
    and ``Resources/`` beside it, because a ``modelica://`` URI resolves
    against the package's directory. Dot-directories are left out, as
    :func:`gather_mo_files` leaves them out, and so is ``work_dir`` itself when
    an entry contains it. The tree is rebuilt on every run: a copy left from
    the last one is a file the user deleted, still where ``package.mo``'s
    directory load would find it.

    *Rejected:* stating what the wrapper must mount (installing.md did) — the
    gates' verdicts then depend on a file outside the project that nothing
    checks, and a temp ``HOME`` is enough to break it. *Rejected:* symlinks into
    ``work_dir`` — a container resolves them in its own mount namespace, where
    the target does not exist. *Rejected:* hard links — they fail across
    filesystems (``/tmp`` often is another one) and an editor that saves by
    rename silently unlinks them. *Rejected:* copying only the ``.mo`` files —
    a model that reads a table through ``modelica://Pkg/Resources/...`` would
    fail at simulate time, a FAIL about a model that is fine. *Rejected:*
    probing omc's view first (``regularFileExists``) and skipping — honest, but
    an installed omc that cannot run here is still a gate that cannot run, and
    reading omc's error text instead is no better: 1.22 prints nothing at all
    for a file it cannot open. A model that opens a file by absolute path still
    needs that path mounted; nothing here can see such a read.
    """
    stage = os.path.join(work_dir, STAGE_DIR, name)
    if os.path.lexists(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    skip = {os.path.realpath(work_dir)}

    def leave_out(directory: str, names: list[str]) -> list[str]:
        out = []
        for entry_name in names:
            full = os.path.join(directory, entry_name)
            if entry_name.startswith(".") and os.path.isdir(full):
                out.append(entry_name)
            elif os.path.realpath(full) in skip:
                out.append(entry_name)
        return out

    placed: list[tuple[str, str]] = []
    for index, entry in enumerate(entries):
        original = os.path.abspath(resolve_path(root, str(entry)))
        copy = os.path.join(stage, str(index), os.path.basename(original) or "root")
        if os.path.isfile(original):
            os.makedirs(os.path.dirname(copy))
            shutil.copy2(original, copy)
        elif os.path.isdir(original) and os.path.realpath(original) not in skip:
            shutil.copytree(original, copy, ignore=leave_out,
                            ignore_dangling_symlinks=True)
        else:
            continue
        placed.append((original, copy))
    return placed


def staged_path(source: str, placed: Sequence[tuple[str, str]]) -> str:
    """Where :func:`stage_sources` put ``source`` (a path :func:`gather_mo_files`
    returned); ``source`` itself when no staged entry holds it."""
    for original, copy in placed:
        if source == original:
            return copy
        prefix = original.rstrip(os.sep) + os.sep
        if source.startswith(prefix):
            return os.path.join(copy, source[len(prefix):])
    return source


def unstage(text: str, placed: Sequence[tuple[str, str]]) -> str:
    """``text`` with every staged path put back to the user's.

    omc names the file it loaded in every error (``[<path>:3:1-3:1:writable]
    Error: ...``), and the file it loaded is a copy the next run overwrites. A
    verdict that cites the copy sends its reader to edit it, and the edit is
    gone on the next run. Longest copy first, so no copy's path is rewritten
    inside a longer one's.
    """
    text = text or ""
    for original, copy in sorted(placed, key=lambda pair: len(pair[1]), reverse=True):
        text = text.replace(copy, original)
    return text


def clear_objects(work_dir: str) -> list[str]:
    """Delete every ``*.o`` directly under ``work_dir``; return the names removed.

    omc's generated makefile marks the numbered ``<Class>_NN.c`` files ``.PHONY``,
    so make always rebuilds those, but it rebuilds the MAIN object
    ``<Class>.o`` only when ``<Class>.c`` has a later mtime. The main file is
    where omc writes the model GUID, fresh on every translation, and the
    executable refuses an ``_init.xml`` whose GUID it was not compiled with.

    What slipped through: on a WSL2 machine the wall clock stepped back by more
    than 0.1 s between ``modelica.compiles`` and ``modelica.simulates``, which
    build the same class in the same directory one after the other. The
    regenerated ``ThermalTank.TankRun.c`` was stamped EARLIER than the object
    the previous build had left, make kept that object, and the pack's own good
    baseline read FAIL with "the GUID ... from input data file ... does not
    match the GUID compiled in the model". It hit about one full test run in
    three, never on the same test twice, and looked like the unrelated
    two-processes-one-out_dir race until the mtimes were recorded. A stale
    object is a build-system artefact, not evidence about the model; a gate
    that fails on it sends someone to edit equations that are fine.

    Every run translates anyway, so deleting the objects costs one compile of
    the main file and nothing else. *Rejected:* ``MAKEFLAGS=-B`` in omc's
    environment, which works only while omc invokes GNU make with the inherited
    environment and no makefile resets it — the fix would live in someone
    else's build system. *Rejected:* a fresh work directory per run, which
    moves the evidence paths every run and still leaves the next build of the
    same class trusting whatever is on disk.
    """
    removed: list[str] = []
    try:
        names = sorted(os.listdir(work_dir))
    except OSError:
        return removed
    for fname in names:
        if not fname.endswith(".o"):
            continue
        path = os.path.join(work_dir, fname)
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed.append(fname)
        except OSError:
            # Left in place, make may trust it again; the GUID check at run time
            # still refuses the mismatch, so the worst case is today's behaviour.
            continue
    return removed


def run_mos(lines: Sequence[str], work_dir: str, name: str,
            timeout_s: float = 300.0, omc: str = "omc",
            staged: Sequence[tuple[str, str]] = ()) -> OmcRun:
    """Write ``lines`` to ``<work_dir>/<name>.mos`` and run ``omc`` on it.

    The script file and its captured output both stay on disk under ``out_dir``
    and are cited as evidence: a tier-2 verdict that a reader cannot reproduce by
    running one command is a verdict they have to take on trust, and this pack's
    whole argument is that nobody should have to.

    A timeout is NOT a failure. It is an absence of evidence and the caller turns
    it into a SKIP — a model that did not finish inside the budget has not been
    shown to be wrong, and filing it as a FAIL would send someone to edit
    equations when the honest fix is a bigger budget or a smaller model.

    Every object file already in ``work_dir`` is deleted first
    (:func:`clear_objects`), so each run builds from the source it just generated.

    ``staged`` is what :func:`stage_sources` copied for this script. The log
    names each copy beside its original and keeps omc's output as omc printed
    it; the ``OmcRun`` the gates read has every copy's path put back to the
    user's (:func:`unstage`).
    """
    import time as _time

    os.makedirs(work_dir, exist_ok=True)
    clear_objects(work_dir)
    script_path = os.path.join(work_dir, f"{name}.mos")
    log_path = os.path.join(work_dir, f"{name}.omc.log")
    body = "\n".join(lines) + "\n"
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(body)

    run = OmcRun(script_path=script_path, log_path=log_path)
    started = _time.perf_counter()
    try:
        completed = subprocess.run(          # noqa: S603 - argv form, no shell
            [omc, script_path],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        run.returncode = completed.returncode
        run.stdout = completed.stdout or ""
        run.stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        run.timed_out = True
        run.stdout = _as_text(exc.stdout)
        run.stderr = _as_text(exc.stderr)
    except (OSError, ValueError) as exc:
        run.launch_error = f"{type(exc).__name__}: {exc}"
    run.duration_s = _time.perf_counter() - started

    try:
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write(f"$ {omc} {script_path}\n(cwd {work_dir})\n")
            for original, copy in staged:
                handle.write(f"(staged {original} as {copy})\n")
            handle.write(f"--- script ---\n{body}")
            handle.write(f"--- returncode {run.returncode} "
                         f"timed_out={run.timed_out} {run.duration_s:.2f}s ---\n")
            handle.write(f"--- stdout ---\n{run.stdout}\n--- stderr ---\n{run.stderr}\n")
    except OSError:
        run.log_path = ""
    run.stdout = unstage(run.stdout, staged)
    run.stderr = unstage(run.stderr, staged)
    return run


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def unescape(text: str) -> str:
    """Undo the escaping omc applies inside an echoed record field."""
    return (text or "").replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def error_is_real(text: str) -> bool:
    """Does this getErrorString() content contain anything worse than a notification?

    omc returns warnings and notifications through the same channel as errors, and
    a gate that fails on "Notification: Automatically loaded package Modelica" is a
    gate that nobody leaves switched on.
    """
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        low = stripped.lower()
        if low.startswith(("notification:", "warning:", "[warning]", "[notification]")):
            continue
        if "error" in low or low.startswith("[error]"):
            return True
    return False


def first_errors(text: str, limit: int = 3) -> str:
    """The first few genuine error lines, flattened for a one-line detail."""
    picked: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if not stripped or low.startswith(("notification:", "warning:")):
            continue
        picked.append(stripped)
        if len(picked) >= limit:
            break
    return " | ".join(picked)


def resolve_path(root: str, path: str) -> str:
    """Absolute path for a project-supplied path, resolved against the project root."""
    text = str(path or "")
    expanded = os.path.expanduser(text)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    return os.path.normpath(os.path.join(root or os.curdir, expanded))
