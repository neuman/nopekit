# SPDX-License-Identifier: Apache-2.0
"""atompipe.modelio — the model contract: one parametric model, one truth.

Rule 1 of the method is that a single parametric model is the only source of
truth and everything else is generated from it. This module is where that rule
stops being a slogan and becomes an interface:

    model/<thing>.py
        CONFIG: <dataclass instance>        # every input, and nothing else
        def build(config) -> dict           # every derived quantity, pure
        PARAMS: list[Param] = [...]         # optional, the provenance

Nothing downstream — no gate, no report, no generator — is allowed to hold an
input of its own. It asks for the PROJECTION instead: a flat, JSON-safe,
diffable view of the config plus everything `build()` resolved, which is also
what staleness is judged against (`model_hash`).

Three things here exist because of specific failures, not because a loader is
traditional:

* **No silent coercion.** A projection value that is not JSON-safe raises
  `AtompipeError` naming the exact field. The tempting alternative — `default=
  str` on `json.dumps` — turns a `Vector3` into `"<Vector3 object at 0x7f...>"`,
  which hashes differently on every run and makes a model and its projection
  drift apart without a single error message. Drift between two representations
  of the same thing is exactly what a cross-representation check is written to
  catch, and catching it at this boundary is cheaper than catching it downstream.

* **The import error is the product.** A traceback from inside a user's model is
  the single most common thing anyone hits here — a typo, a missing third-party
  dependency, a divide by zero in `build()`. `load_model` and `project` catch it
  and re-raise as `AtompipeError` carrying the real exception text AND the line
  in the user's file, because "ZeroDivisionError" with no location is a worse
  message than the traceback it replaced.

* **Determinism is assertable.** `check_determinism` builds twice and diffs.
  Byte-identical output makes a sound acceptance gate for a large refactor:
  if the result is bit-for-bit the same, the refactor changed
  nothing, and that is a proof no amount of reading the diff can give you. A
  model that cannot pass this one has a clock, a `set`, or a `random` in it, and
  every hash-based staleness check downstream is already lying.

* **The code that runs is the code on disk, and it is written down.**
  `load_source_module` is the one loader for every Python file the spine
  executes on a project's behalf — gate modules, fixtures, helpers loaded by
  path, and (through `load_model`) the model and its siblings. It compiles the
  bytes it read, never a `.pyc`; it records every file under the owning roots
  that ran, as `(path, sha256)`, on the module (`__atompipe_code__`); and it
  serves a module from `sys.modules` only while every recorded file still has
  the recorded digest. What slipped through without it (S-26): only the model
  entry was fresh-compiled, so a same-size, same-second edit to a gate module
  or a pack helper ran the stale bytecode — the source said 8.0 and the verdict
  came from 7.0 — and an in-process reload served the old module with no pyc
  involved at all. A per-gate verdict keyed on the file's bytes would then have
  filed the old behaviour under the new digest. `verdicts` digests these
  closures; this module never imports `verdicts` or `gates`.

Time policy (contract rule 3): nothing here reads the clock. `build()` is
supposed to be pure, and a model that stamps its own timestamp fails
`check_determinism` on purpose.
"""
from __future__ import annotations

import ast
import contextlib
import difflib
import functools
import hashlib
import inspect

import dataclasses
import importlib.machinery
import importlib.util
import json
import math
import os
import re
import site
import sys
import sysconfig
import traceback
import types
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping

from . import store
from .models import Param, Rejected, _enc
from .util import AtompipeError, atomic_write_json, rel, short_hash

__all__ = [
    "LoadedModel",
    "load_model",
    "project",
    "flat_params",
    "model_hash",
    "write_projection",
    "params_from_model",
    "undocumented_params",
    "check_determinism",
    "CodeClosure",
    "load_source_module",
    "load_path",
    "is_code",
    "code_closure",
    "clear_caches",
    "static_param_prose",
    "ParamView",
    "param_view",
]

#: Loaded model modules are registered in `sys.modules` under this prefix rather
#: than under their own stem. A project that names its model `model/types.py` or
#: `model/email.py` — and people do, because the directory is theirs — would
#: otherwise shadow a stdlib module for the rest of the process, and the failure
#: lands somewhere else entirely, minutes later, in code that never heard of the
#: model. The prefix costs nothing and makes that impossible.
_MODULE_PREFIX = "atompipe_model_"

#: This file, so user tracebacks can be trimmed of spine frames.
_THIS_FILE = os.path.abspath(__file__)

#: The installed spine package. Never part of a project's code closure (its
#: modules reach a verdict through the spine digest or `spine_extras`), even when
#: a project root happens to contain it — a test tree under the repository does.
_PKG_DIR = os.path.dirname(_THIS_FILE)

#: Where a module's recorded closure and its gate registrations live: on the
#: module object itself. A module served from `sys.modules` fires no import
#: event, so a closure recorded anywhere else is lost to the second importer —
#: `packs.load_gates` reuses cached modules, and every later registry (validate,
#: pack mode, a test's fresh `Registry`) would otherwise see a module with no
#: code at all.
_CODE_ATTR = "__atompipe_code__"
_GATES_ATTR = "__atompipe_gates__"

#: Module names for helpers loaded by path are salted with a digest of the
#: absolute path. fdm-print loaded its helpers under a fixed name
#: (`atompipe_pack_fdm_print__fold`), so a second copy of the pack in one
#: process — `test_pack_keys`' `fdm-twin`, a project-local shadow, a user pack —
#: silently ran the FIRST copy's helper while every message named the second
#: (packs:H4). 12 hex chars, like every other short digest here: two paths
#: colliding is not a failure mode worth a longer name. Rejected: the stem alone
#: (that is the bug), and a counter (a name that depends on load order is a
#: name two processes disagree about).
_PATH_PREFIX = "atompipe_path_"

#: The modules `verdicts.SPINE_MODULES` digests into every verdict. Mirrored
#: here, not imported: `verdicts` imports this module, so the dependency cannot
#: run the other way. `test_codeload` holds the two equal. An `atompipe.*` import
#: OUTSIDE this set is recorded per module as a `spine_extras` name, because the
#: spine digest does not cover it — fdm-print and cad-solid import `atompipe.site`
#: for the node separator their locators carry (packs:H4).
_SPINE_MODULE_FILES = frozenset({"models.py", "gates.py", "modelio.py", "verdicts.py"})

#: Top-level names that are never a project's code and never a third-party
#: instrument. `sys.stdlib_module_names` is 3.10+, the CI floor.
_STDLIB = frozenset(sys.stdlib_module_names) | frozenset(sys.builtin_module_names)

#: The prefix of a fallback that came from computed source, as opposed to an
#: import that resolved to something that is not Python source.
_FALLBACK_COMPUTED = "computed source at "


# --------------------------------------------------------------------------- #
# the loaded model
# --------------------------------------------------------------------------- #
@dataclass
class LoadedModel:
    """A model module that has been imported, with its config resolved.

    This type deliberately does NOT live in `models.py`: it holds a live module
    object and a live dataclass instance, neither of which can cross the JSON
    boundary. Everything that IS persisted about a model — the params, the
    projection, the hash — is a plain dict or a `models.Param` by the time it
    leaves this module.

    `entry` is stored repo-relative (`model/bracket.py`) to match
    `ProjectMeta.model_entry`, so a record written on one machine still resolves
    on another.
    """

    module: Any
    config: Any
    entry: str
    params: list[Param] = field(default_factory=list)

    @property
    def file(self) -> str:
        """Absolute path of the file that was executed.

        Derived from the module rather than stored a second time (rule 2): two
        fields that must agree about the same path are two fields that will
        eventually disagree.
        """
        return os.path.abspath(getattr(self.module, "__file__", "") or "")

    @property
    def directory(self) -> str:
        """Directory the model was loaded from — where its sibling files live."""
        path = self.file
        return os.path.dirname(path) if path else ""


# --------------------------------------------------------------------------- #
# error rendering:  the user's traceback, in one line
# --------------------------------------------------------------------------- #
def _user_location(exc: BaseException) -> str:
    """`  at model/bracket.py:87 in build` for the deepest frame OUTSIDE the spine.

    The deepest non-spine frame is where the user's code actually broke. Keeping
    just that line (plus the source text) is the whole difference between an
    error a user can fix and one they have to reproduce under a debugger; the
    twelve frames of importlib machinery above it teach nobody anything.
    """
    if isinstance(exc, SyntaxError) and exc.filename:
        # A SyntaxError never reaches a frame in the user's file — the module
        # body was never executed — so its location lives on the exception.
        where = f"\n  at {exc.filename}:{exc.lineno or 0}"
        return f"{where}\n    {exc.text.strip()}" if exc.text else where

    frames = traceback.extract_tb(exc.__traceback__)
    outside = [f for f in frames if os.path.abspath(f.filename or "") != _THIS_FILE]
    if not outside:
        return ""
    frame = outside[-1]
    where = f"\n  at {frame.filename}:{frame.lineno} in {frame.name}"
    return f"{where}\n    {frame.line.strip()}" if frame.line else where


def _explain(exc: BaseException) -> str:
    """`ZeroDivisionError: float division by zero` + where it happened.

    `ModuleNotFoundError` gets an extra sentence because it is both the most
    common failure and the one most often misread as an atompipe bug: the SPINE
    is standard-library only, a user's MODEL is not required to be, and the
    thing to do about it is `pip install`, not file an issue.
    """
    text = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, ModuleNotFoundError) and exc.name:
        text += (
            f"\n  the model imports {exc.name!r}, which is not installed in this "
            f"interpreter ({sys.executable}) — install it, or keep the model "
            f"standard-library only so the project runs on a fresh machine"
        )
    return text + _user_location(exc)


# --------------------------------------------------------------------------- #
# JSON safety:  the boundary where drift is caught
# --------------------------------------------------------------------------- #
def _json_problem(path: str, value: Any) -> str:
    """The message for one non-JSON-safe projection value. Names the field.

    Every branch here says what to do in `build()`, because the answer is always
    "return a different value", never "atompipe will handle it".
    """
    kind = type(value).__name__
    if isinstance(value, (set, frozenset)):
        # Worse than merely unserialisable: a set's iteration order depends on
        # string hash randomisation, so it differs between PROCESSES. Coercing
        # one to a list would give a projection that hashes differently on every
        # run and marks every gate result stale for no reason.
        return (
            f"{path} is a {kind}; JSON has no set, and a set's iteration order "
            f"changes between processes (PYTHONHASHSEED), so the model hash "
            f"would differ on every run — return sorted(...) from build()"
        )
    if callable(value):
        return (
            f"{path} is a {kind} (a callable); build() returns DATA, not "
            f"behaviour — call it and return the result"
        )
    return (
        f"{path} is a {kind}, which is not JSON-safe. Convert it in build() "
        f"(float(), str(), list(), or .to_dict()) — the spine will not coerce "
        f"it for you, because a coerced value and the model it came from drift "
        f"apart with no error to notice"
    )


def _check_json_safe(value: Any, path: str, seen: set[int] | None = None) -> None:
    """Walk `value`, raising AtompipeError at the first field JSON cannot hold.

    Runs AFTER `models._enc`, so dataclasses, enums and tuples are already
    primitives; anything still exotic at this point is something the model
    genuinely invented and genuinely has to fix.

    Three rejections here are stricter than `json.dumps` and each one is
    deliberate:

    * **NaN / Infinity.** `json.dumps` happily emits the bare tokens `NaN` and
      `Infinity`, which are not JSON (RFC 8259) and which every non-Python
      reader — jq, a browser, a fab house's importer — rejects. Worse, a NaN
      defeats `Acceptance.holds` silently: `nan <= limit` is False and
      `nan > limit` is also False, so a claim backed by a NaN can neither pass
      nor fail, it just quietly never proves anything. That is a logger, not a
      gate.
    * **Non-string dict keys.** `json.dumps({1: "a"})` returns `{"1": "a"}` with
      no complaint, so the value that comes back from disk is no longer the
      value that went in. That is the definition of drift.
    * **Cycles.** `models._enc` normally hits a self-referencing structure
      first and dies of recursion, which `_safe` translates; this guard is the
      backstop for anyone walking a structure `_enc` never touched.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return                                    # bool is an int; both are fine
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise AtompipeError(
                f"{path} is {value!r}. NaN and Infinity are not JSON, and a NaN "
                f"silently defeats every claim comparator (nan <= x and nan > x "
                f"are both False), so a gate reading it can never fail or pass "
                f"— guard the division in build() and return None instead"
            )
        return

    if isinstance(value, (dict, list)):
        seen = set() if seen is None else seen
        if id(value) in seen:
            raise AtompipeError(
                f"{path} contains a reference back to a container that already "
                f"encloses it; a cyclic structure cannot be serialised"
            )
        seen = seen | {id(value)}
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise AtompipeError(
                        f"{path} has a non-string key {key!r} "
                        f"({type(key).__name__}); JSON object keys are strings "
                        f"and json.dumps would stringify it silently, so the "
                        f"projection would stop round-tripping — use str keys"
                    )
                _check_json_safe(item, f"{path}.{key}", seen)
        else:
            for index, item in enumerate(value):
                _check_json_safe(item, f"{path}[{index}]", seen)
        return

    raise AtompipeError(_json_problem(path, value))


def _safe(value: Any, path: str) -> Any:
    """`models._enc` then validate. The only way a value enters a projection.

    The RecursionError catch is not paranoia: `_enc` (and `dataclasses.asdict`
    underneath it) walks a structure blindly, so a dict that contains itself —
    or a parent/child pair of dataclasses pointing at each other, which is how
    most people's first assembly tree gets written — exhausts the stack *before*
    the validator ever sees a field. Left alone that surfaces as a thousand-line
    traceback through `_enc`, naming nothing the user can act on.
    """
    try:
        encoded = _enc(value)
        _check_json_safe(encoded, path)
    except RecursionError as exc:
        raise AtompipeError(
            f"{path} could not be encoded: it nests too deeply, or a value "
            f"refers back to a container that encloses it. build() returns a "
            f"tree, not a graph — replace the back-reference with a name or an id"
        ) from exc
    return encoded


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #
def _resolve_entry(root: str, entry: str | None) -> str:
    """Absolute path of the model file, from an explicit entry or the ledger.

    Deliberately does NOT guess. If the ledger has no `model_entry`, this lists
    what it can see under `model/` and asks the user to pick — a loader that
    guesses "the only .py file in model/" works right up until there are two,
    and then silently analyses the wrong one.
    """
    if not entry:
        meta = store.load(root).meta
        entry = (meta.model_entry or "").strip()
    if not entry:
        candidates = _model_candidates(root)
        hint = (
            f" — found {', '.join(candidates)}" if candidates
            else " — write one (a dataclass CONFIG plus build(config) -> dict)"
        )
        raise AtompipeError(
            f"no model entry recorded for this project: set "
            f"meta.model_entry in {rel(store.ledger_path(root), root)} to the "
            f"model file{hint}"
        )

    path = entry if os.path.isabs(entry) else os.path.join(root, entry)
    path = os.path.abspath(path)

    if os.path.isdir(path):
        # A model that grew into a package: `model/hull/` with an __init__.py.
        # A model routinely grows into a package of many modules; this must keep working.
        package_init = os.path.join(path, "__init__.py")
        if not os.path.isfile(package_init):
            raise AtompipeError(
                f"model entry {entry!r} is a directory with no __init__.py; "
                f"point meta.model_entry at a .py file, or add "
                f"{rel(package_init, root)}"
            )
        return package_init

    if not os.path.isfile(path):
        raise AtompipeError(
            f"model entry {entry!r} does not exist (looked in "
            f"{rel(path, root)}); fix meta.model_entry or create the file"
        )
    return path


def _model_candidates(root: str) -> list[str]:
    """Plausible model files under `model/`, for the "you have no entry" message."""
    directory = store.model_dir(root)
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    return [
        rel(os.path.join(directory, n), root)
        for n in names
        if n.endswith(".py") and not n.startswith("_")
    ][:8]


# --------------------------------------------------------------------------- #
# the recording loader:  fresh bytes, a recorded closure, a content-keyed cache
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CodeClosure:
    """The code one module ran, as recorded while it ran. Stored on the module.

    * `files` — `(absolute path, sha256 of the bytes)` for every Python file
      that is code (`_code_root`: under the owning roots, or beside them and
      not installed) that this module executed or depends on: itself,
      every helper imported by name, through a namespace package or by path,
      every cached helper it picked up from `sys.modules` (by the helper's own
      recorded closure), and every local module a function body imports lazily
      (read statically). Sorted by path.
    * `fallback` — empty, or why the recording gave up on precision and took
      every `*.py` under the owning root instead: computed source compiled or
      executed from a frame in code, or an import of code that resolved to
      something that is not Python source.
    * `third_party` — the top-level names the files import, read from their
      source (module and function level), that are not code — installed
      (`_installed`), or not importable at all — not the standard library and
      not `atompipe`. Static on purpose: a list built from
      import events depends on what an earlier gate happened to import first,
      and two identical runs would disagree about it (packs:H3).
    * `spine_extras` — the `atompipe.*` modules the files import that the
      spine digest does not cover, digested per module by `verdicts`.

    A closure whose files no longer all have their recorded digest is stale, and
    `load_source_module` re-executes the module rather than serve it.
    """

    files: tuple[tuple[str, str], ...] = ()
    fallback: str = ""
    third_party: tuple[str, ...] = ()
    spine_extras: tuple[str, ...] = ()


def _norm(path: str) -> str:
    """The comparison form of a path: absolute and case-folded where the OS folds."""
    return os.path.normcase(os.path.abspath(path))


def _under(path: str, directory: str) -> bool:
    """Is normalised `path` inside normalised `directory` (or equal to it)?"""
    if path == directory:
        return True
    return path.startswith(directory if directory.endswith(os.sep) else directory + os.sep)


#: The interpreter's own trees: never a project's code, even under a root.
_EXCLUDED: tuple[str, ...] | None = None


def _excluded_dirs() -> tuple[str, ...]:
    """Interpreter prefixes, the stdlib and site dirs, user site, the spine package.

    User site is the one that bites in practice: trimesh and numpy live in
    `~/.local` on the machine this was written on, which is under no prefix, and
    a project root that is a home directory would otherwise "own" them. Computed
    once; `site.getsitepackages` is missing from some embedded interpreters, so
    every source is optional.
    """
    global _EXCLUDED
    if _EXCLUDED is None:
        found = {sys.prefix, sys.base_prefix, sys.exec_prefix,
                 getattr(sys, "base_exec_prefix", ""), _PKG_DIR}
        with contextlib.suppress(Exception):
            paths = sysconfig.get_paths()
            found.update(paths.get(k) or "" for k in ("stdlib", "platstdlib", "purelib", "platlib"))
        with contextlib.suppress(Exception):
            found.update(site.getsitepackages())
        with contextlib.suppress(Exception):
            found.add(site.getusersitepackages())
        _EXCLUDED = tuple(sorted({_norm(p) for p in found if p}))
    return _EXCLUDED


def _owning_root(path: str, roots: Iterable[str]) -> str | None:
    """The (normalised) root that owns `path`, or None when it is under no root.

    The deepest root containing the path wins. An interpreter tree wins over a
    root only when it sits INSIDE that root — a virtualenv under the project, a
    test tree under the repository that holds the spine — so a pack installed
    inside site-packages still owns its own files. None does not yet mean "not
    code": `_code_root` decides that.
    """
    target = _norm(path)
    best = ""
    for root in roots:
        if _under(target, root) and len(root) > len(best):
            best = root
    if not best:
        return None
    for excluded in _excluded_dirs():
        if excluded != best and _under(excluded, best) and _under(target, excluded):
            return None
    return best


#: Directory names that make a tree installed third-party code wherever it sits:
#: pip, venv, conda and Debian's python all install into one of these, and a
#: tree put on `sys.path` by hand keeps its name (ROS's
#: `/opt/ros/<distro>/lib/python3.x/site-packages` on PYTHONPATH is under none of
#: this interpreter's prefixes). Matched as a whole path component, and only for
#: a file under no root, so a project's own tree never changes owner by it.
#: Rejected: distribution metadata as the test — an editable install has
#: metadata too, and its source is somebody's working copy, the very case that
#: slipped through; and no name rule at all, which files a second interpreter's
#: site-packages as the project's code, fresh-compiled and digested file by
#: file on every load.
_SITE_DIR_NAMES = frozenset({"site-packages", "dist-packages"})


def _installed(target: str) -> bool:
    """Is normalised `target` installed third-party code: under one of the
    interpreter's trees (`_excluded_dirs`), the spine included, or under a
    directory named like a site dir (`_SITE_DIR_NAMES`)?"""
    if any(_under(target, excluded) for excluded in _excluded_dirs()):
        return True
    return any(part in _SITE_DIR_NAMES for part in target.split(os.sep)[:-1])


def _code_root(path: str, roots: Iterable[str]) -> str | None:
    """The (normalised) root that owns `path` as CODE, or None when it is an
    instrument: installed third-party code, provenance and never rho (Q1.3).

    A root that owns it (`_owning_root`) first. A Python file under no root is
    still code unless it is installed (`_installed`), and owns itself: its
    directory is its root, for a message and for a fallback walk. What slipped
    through with the roots alone (review round 1, the `mono` probe): a
    monorepo's `shared/beamlib.py`, beside the project and put on `sys.path` by
    a gate module, was declined by the recording finder, so the stock loader ran
    it from its `__pycache__`, the closure left it out, and the static pass
    listed `beamlib` as third-party — an instrument, "unknown". After its
    allowable went 5.0 -> 0.1 a plain `check` served the PASS as cached and
    current, where `--force` FAILed it 0.700 vs 0.1; and a same-second edit ran
    the old bytecode, S-26 again. Rejected: making such a gate opaque (never
    Fresh, so re-run on every check, and its import still reads the pyc).
    """
    root = _owning_root(path, roots)
    if root is not None:
        return root
    target = _norm(path)
    if _installed(target):
        return None
    return os.path.dirname(target)


def _roots(roots: Iterable[str] | None, path: str) -> tuple[str, ...]:
    """Normalise the caller's roots; a loader with none owns the file's own directory."""
    found = [_norm(r) for r in (roots or ()) if r]
    if not found:
        found = [_norm(os.path.dirname(os.path.abspath(path)))]
    return tuple(dict.fromkeys(found))


def _display(path: str, root: str) -> str:
    """`gates/mesh.py`: a path relative to its root, in posix form, for a message."""
    try:
        return os.path.relpath(path, root).replace(os.sep, "/")
    except ValueError:                            # another drive on Windows
        return path


#: Reads code bytes through the import system's own reader, so the read is
#: attributed to import machinery — which is what it is — by a trace window's
#: audit hook. Hashing a helper for its closure is the loader's read, not a
#: gate's input; filed as a gate read, it would put every helper digest into a
#: gate's ρ a second time, keyed on whether the helper was cached.
_READER = importlib.machinery.SourceFileLoader("atompipe_modelio_reader", _THIS_FILE)


def _read(path: str) -> bytes | None:
    try:
        return _READER.get_data(path)
    except OSError:
        return None


def _file_sha(path: str, memo: dict[str, str | None] | None = None) -> str | None:
    """sha256 of the bytes at `path` now, or None when it cannot be read."""
    key = _norm(path)
    if memo is not None and key in memo:
        return memo[key]
    data = _read(path)
    digest = hashlib.sha256(data).hexdigest() if data is not None else None
    if memo is not None:
        memo[key] = digest
    return digest


def _module_file(module: Any) -> str | None:
    """A module's absolute `__file__`, or None (builtins, namespace packages)."""
    try:
        where = vars(module).get("__file__")
    except TypeError:
        return None
    return os.path.abspath(where) if isinstance(where, str) and where else None


def _own_closure(module: Any) -> CodeClosure | None:
    """The closure this loader recorded on `module`, read without `__getattr__`.

    A package with a PEP 562 `__getattr__` (the spine's own `atompipe` is one)
    would otherwise be asked for a name it does not have, and may import
    something to answer.
    """
    try:
        found = vars(module).get(_CODE_ATTR)
    except TypeError:
        return None
    return found if isinstance(found, CodeClosure) else None


def _closure_current(closure: CodeClosure, *, roots: Iterable[str] = (),
                     memo: dict[str, str | None] | None = None) -> bool:
    """Does every recorded file still have its recorded digest?

    A fallback closure is also stale when a new `*.py` appears under a root it
    walked: "every file under the directory" is a statement about the listing,
    not only about the files that were there.
    """
    for path, digest in closure.files:
        if not digest or _file_sha(path, memo) != digest:
            return False
    if closure.fallback:
        recorded = {_norm(p) for p, _ in closure.files}
        for root in roots:
            if not any(_under(p, root) for p in recorded):
                continue
            if any(_norm(p) not in recorded for p in _py_files_under(root)):
                return False
    return True


def _py_files_under(root: str) -> Iterator[str]:
    """Every `*.py` under `root`, sorted, for a fallback closure.

    Skips `__pycache__`, dot-directories (`.git`, `.venv`, `.atompipe`), any
    interpreter tree inside the root, and any directory holding a `pyvenv.cfg`:
    a virtualenv is somebody else's code however it is named, and walking one
    would make every fallback closure thousands of files long.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        kept = []
        for name in sorted(dirnames):
            full = os.path.join(dirpath, name)
            if name == "__pycache__" or name.startswith("."):
                continue
            if os.path.isfile(os.path.join(full, "pyvenv.cfg")):
                continue
            if _owning_root(full, (root,)) is None:
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


class _Recording:
    """One module execution's closure while it is being built.

    Nested recordings are the rule, not the exception: a helper imported while a
    gate module runs gets its own recording (so it carries its own closure for
    the next importer), and folds into its importer's when it finishes.
    """

    __slots__ = ("name", "path", "roots", "memo", "files", "sources", "foreign",
                 "fallback", "fallback_root", "third_party", "spine_extras")

    def __init__(self, name: str, path: str, roots: tuple[str, ...],
                 memo: dict[str, str | None] | None = None) -> None:
        self.name = name
        self.path = path
        self.roots = roots
        self.memo: dict[str, str | None] = {} if memo is None else memo
        self.files: dict[str, str] = {}
        self.sources: dict[str, bytes] = {}       # files THIS recording compiled
        self.foreign: dict[str, bytes] = {}       # local files read, not compiled here
        self.fallback = ""
        self.fallback_root = ""
        self.third_party: set[str] = set()
        self.spine_extras: set[str] = set()

    def note(self, path: str, digest: str) -> None:
        """Record one file. Two different digests for one path mean two versions
        of it ran inside one closure; "" never matches a file, so the closure is
        stale at the next look rather than quietly keeping either."""
        known = self.files.get(path)
        self.files[path] = digest if known in (None, digest) else ""

    def compiled(self, path: str, data: bytes) -> None:
        digest = hashlib.sha256(data).hexdigest()
        self.memo[_norm(path)] = digest
        self.sources[path] = data
        self.note(path, digest)

    def read_foreign(self, path: str) -> None:
        """A local file this recording did not compile: digest what is on disk
        now, and queue its source for the static pass."""
        if path in self.files or path in self.foreign:
            return
        data = _read(path)
        if data is None:
            self.note(path, "")
            return
        self.memo[_norm(path)] = digest = hashlib.sha256(data).hexdigest()
        self.note(path, digest)
        self.foreign[path] = data

    def give_up(self, why: str, root: str) -> None:
        """Take every `*.py` under `root`. The first reason is the one named; a
        reason absorbed from a helper does not stop this module's own root being
        walked, because the helper's walk covered the helper's root."""
        if not self.fallback:
            self.fallback = why
        if not self.fallback_root:
            self.fallback_root = root

    def unmappable(self, name: str, path: str, root: str) -> None:
        """An import under the roots that resolved to something that is not Python
        source (an extension module, a sourceless `.pyc`): its own bytes go in,
        and so does every `*.py` beside it, because what it runs is invisible."""
        digest = _file_sha(path, self.memo)
        self.note(path, digest or "")
        self.give_up(f"import {name} resolves to {_display(path, root)}, which is not "
                     f"Python source", root)

    def absorb(self, closure: CodeClosure) -> None:
        for path, digest in closure.files:
            self.note(path, digest)
        if closure.fallback and not self.fallback:
            self.fallback = closure.fallback      # its walk is already in its files
        self.third_party.update(closure.third_party)
        self.spine_extras.update(closure.spine_extras)

    def closure(self) -> CodeClosure:
        return CodeClosure(
            files=tuple(sorted(self.files.items())),
            fallback=self.fallback,
            third_party=tuple(sorted(self.third_party)),
            spine_extras=tuple(sorted(self.spine_extras)),
        )


#: The recordings in progress, innermost last. Process-global, like the import
#: system it shadows; loads happen on the CLI's one thread (see `use_registry`
#: in gates.py for the same reasoning), and a lock here could deadlock against
#: the import system's per-module locks.
_STACK: list[_Recording] = []


class _RecordingFinder:
    """At `sys.meta_path[0]` while a recording runs: fresh-loads imports that are code.

    It asks the stock `PathFinder` where a name lives — the same answer the
    import system would reach, from the same `sys.path` — and when that is a
    Python source file that is code (`_code_root`: under the recording's roots,
    or beside them and not installed), hands back the spec with a `_FreshLoader`
    in place of the stock one. So a helper imported by name is compiled from its
    bytes (no pyc) and recorded, exactly like the module that imported it.
    Everything else — the standard library, installed third-party packages,
    namespace packages (fluids-analytic's `gates`, whose `__file__` is None) —
    is declined and resolves exactly as it would have.
    """

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        try:
            return _fresh_spec(fullname, path)
        except Exception:                         # noqa: BLE001 - never break an import
            return None

    def invalidate_caches(self) -> None:          # the PathFinder it asks has its own
        pass


_FINDER = _RecordingFinder()


def _fresh_spec(fullname: str, path: Any) -> Any:
    if not _STACK:
        return None
    top = fullname.partition(".")[0]
    if top in _STDLIB or top == "atompipe":
        return None
    spec = importlib.machinery.PathFinder.find_spec(fullname, path)
    if spec is None or spec.origin is None or not spec.has_location:
        return None                               # not found, or a namespace package
    recording = _STACK[-1]
    origin = os.path.abspath(spec.origin)
    root = _code_root(origin, recording.roots)
    if root is None:
        return None                               # installed: an instrument
    loader = spec.loader
    if isinstance(loader, importlib.machinery.SourceFileLoader):
        if not isinstance(loader, _FreshLoader):
            spec.loader = _FreshLoader(fullname, spec.origin)
        return spec
    recording.unmappable(fullname, origin, root)
    return None


#: One audit hook per process, installed on the first recording. There is no
#: way to remove an audit hook, so it routes only while a recording runs and
#: returns at once otherwise.
_HOOKED = False


def _audit(event: str, args: tuple) -> None:
    """Notice computed source compiled or executed from a frame in code (`_code_root`).

    The calling frame is the whole test. `@dataclass` and `namedtuple` compile
    and exec generated source on every class they build, from a frame in the
    standard library; keyed on the event alone, every pack that defines a
    dataclass would fall back to a whole-directory digest (fdm-print's fold,
    cad-solid, openmodelica — core:§0). `exec(compile(src, ...))` in a gate
    module's body comes from a frame in the gate module, and that is the case
    whose code the recording cannot see.

    Never raises: an exception from an audit hook aborts the operation that
    raised the event, and this one sees every compile in the process.
    """
    if event != "exec" and event != "compile":
        return
    if not _STACK:
        return
    try:
        recording = _STACK[-1]
        if recording.fallback_root:
            return
        frame = sys._getframe(1)
        filename = frame.f_code.co_filename
        # `<frozen importlib._bootstrap>` and `<string>` are not files; made
        # absolute they would land under whatever the working directory is.
        if not filename or not os.path.isabs(filename):
            return
        root = _code_root(filename, recording.roots)
        if root is None:
            return
        recording.give_up(f"{_FALLBACK_COMPUTED}{_display(filename, root)}:{frame.f_lineno}", root)
    except Exception:                             # noqa: BLE001 - see docstring
        return


@contextlib.contextmanager
def _recording(recording: _Recording) -> Iterator[_Recording]:
    """Push `recording`, with the finder at `sys.meta_path[0]` and the hook live."""
    global _HOOKED
    if not _HOOKED:
        sys.addaudithook(_audit)
        _HOOKED = True
    inserted = _FINDER not in sys.meta_path
    if inserted:
        sys.meta_path.insert(0, _FINDER)
    _STACK.append(recording)
    try:
        yield recording
    finally:
        # Pop OUR entry, not the top one: a module that broke the stack must not
        # leave the next import attributed to a load that already finished.
        for index in range(len(_STACK) - 1, -1, -1):
            if _STACK[index] is recording:
                del _STACK[index]
                break
        if inserted and not _STACK:
            with contextlib.suppress(ValueError):
                sys.meta_path.remove(_FINDER)


class _FreshLoader(importlib.machinery.SourceFileLoader):
    """A source loader that never consults or writes `__pycache__`, and records.

    This is not an optimisation, it is a correctness fix, and it was found by
    smoke-testing the obvious thing: load the model, edit `thickness: float =
    7.0` to `8.0`, load it again — and get the OLD value back, with no error and
    no warning.

    Python validates a cached `.pyc` against the source's mtime (whole seconds)
    and its size in bytes. `7.0` and `8.0` are the same size, and a tier-0 loop
    that promises to run in seconds means the re-run happens inside the same
    second. Both checks pass, the stale bytecode is executed, and `atompipe
    check` confidently reports yesterday's geometry as today's. That is the
    exact failure this whole module exists to prevent — the model changed and
    the projection did not — arriving through the back door.

    Overriding `get_code` to compile the source every time costs a millisecond
    on a file a human wrote and closes it. It compiles the very bytes it read
    and records their digest, so the closure names the code that ran, not the
    code on disk a moment later. `source_to_code` still compiles with
    `dont_inherit=True`, so the spine's own `from __future__ import annotations`
    does not silently leak into a model that never asked for it.

    This used to be the model ENTRY's loader only, with the residual written
    here: sibling modules went through the normal import machinery and could be
    served from a `__pycache__` left behind by running the model by hand. The
    same hole ran every gate module and pack helper (S-26). Now every module the
    spine loads for a project, and every import under its roots while one runs,
    comes through here.
    """

    def __init__(self, fullname: str, path: str, *, roots: tuple[str, ...] | None = None,
                 memo: dict[str, str | None] | None = None) -> None:
        super().__init__(fullname, path)
        self.roots = roots
        self.memo = memo

    def get_code(self, fullname: str) -> Any:
        data = self.get_data(self.path)
        if _STACK and _STACK[-1].path == os.path.abspath(self.path):
            _STACK[-1].compiled(os.path.abspath(self.path), data)
        return self.source_to_code(data, self.path)

    def exec_module(self, module: Any) -> None:
        path = os.path.abspath(self.path)
        parent = _STACK[-1] if _STACK else None
        roots = self.roots or (parent.roots if parent is not None else _roots((), path))
        memo = self.memo if self.memo is not None else (parent.memo if parent is not None else None)
        recording = _Recording(module.__name__, path, roots, memo)
        with _recording(recording):
            try:
                super().exec_module(module)
            except BaseException:
                # What failed still decided the importer's behaviour (a local
                # `try: import helper / except ImportError:` takes the other
                # branch), so its files stay in the importer's closure: an edit
                # that fixes it must make the importer stale.
                if parent is not None:
                    for failed_path, digest in recording.files.items():
                        parent.note(failed_path, digest)
                raise
            closure = _seal(module, recording)
        if parent is not None:
            parent.absorb(closure)


def _seal(module: Any, recording: _Recording) -> CodeClosure:
    """Finish a recording after its module ran, and store the closure on it."""
    _walk_globals(module, recording)
    _static_pass(recording)
    if recording.fallback_root:
        known = {_norm(p) for p in recording.files}
        for path in _py_files_under(recording.fallback_root):
            if _norm(path) not in known:
                recording.note(path, _file_sha(path, recording.memo) or "")
    closure = recording.closure()
    setattr(module, _CODE_ATTR, closure)
    return closure


def _module_of(value: Any, own: str) -> Any:
    """The module a global belongs to: itself when it is one, else by `__module__`."""
    if isinstance(value, types.ModuleType):
        return value
    try:
        name = getattr(value, "__module__", None)
    except Exception:                             # noqa: BLE001 - a proxy that refuses
        return None
    if not isinstance(name, str) or name == own:
        return None
    return sys.modules.get(name)


def _walk_globals(module: Any, recording: _Recording) -> None:
    """Attribute the helpers a module holds but did not import during its run.

    A helper already in `sys.modules` fires no import event at all, so the
    recording finder never sees it: fdm-print's shared fold, `fdm_print_parts`
    imported under two names, fluids-analytic's `from gates._fluids_analytic
    import …` in its second gate module. After the module runs, every global
    that IS a module, or whose `__module__` names one, is looked up; a module
    that is code (`_code_root`: under the roots, or beside them and not
    installed) contributes its own recorded closure. One some other
    machinery loaded (no closure — `spec_from_file_location` by hand) is
    digested as it is on disk now, read statically, and walked in turn: the
    bytes it ran may predate that digest, which is why the bundled by-path
    loaders move onto `load_path`.
    """
    seen = {id(module)}
    queue = [module]
    while queue:
        current = queue.pop()
        try:
            values = list(vars(current).values())
        except TypeError:
            continue
        own = getattr(current, "__name__", "")
        for value in values:
            target = _module_of(value, own)
            if target is None or id(target) in seen:
                continue
            seen.add(id(target))
            where = _module_file(target)
            if where is None:
                continue                          # builtin, or a namespace package
            root = _code_root(where, recording.roots)
            if root is None:
                continue                          # installed: an instrument
            closure = _own_closure(target)
            if closure is not None:
                recording.absorb(closure)
                continue
            if not where.endswith(tuple(importlib.machinery.SOURCE_SUFFIXES)):
                recording.unmappable(getattr(target, "__name__", "?"), where, root)
                continue
            recording.read_foreign(where)
            queue.append(target)


#: `(path, sha256) -> (imports, attributes read off a bare `import atompipe`)`.
#: A helper shared by seven gate modules is parsed once.
_STATIC: dict[tuple[str, str], tuple[tuple, frozenset]] = {}


def _static_imports(path: str, data: bytes) -> tuple[tuple, frozenset]:
    """`((level, module, names, line), …)` for every import in the file, at any depth."""
    key = (_norm(path), hashlib.sha256(data).hexdigest())
    cached = _STATIC.get(key)
    if cached is not None:
        return cached
    try:
        tree = ast.parse(data, filename=path)
    except (SyntaxError, ValueError):
        _STATIC[key] = ((), frozenset())
        return _STATIC[key]
    found: list[tuple] = []
    bare: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((0, alias.name, (), node.lineno))
                if alias.name == "atompipe" or (alias.name.startswith("atompipe.")
                                                and alias.asname is None):
                    bare.add(alias.asname or "atompipe")
        elif isinstance(node, ast.ImportFrom):
            found.append((node.level or 0, node.module or "",
                          tuple(a.name for a in node.names), node.lineno))
    attributes = frozenset(
        node.attr for node in ast.walk(tree)
        if bare and isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) and node.value.id in bare
    )
    _STATIC[key] = (tuple(found), attributes)
    return _STATIC[key]


def _spine_names(module: str, names: tuple[str, ...]) -> set[str]:
    """The `atompipe` modules one import statement reaches."""
    parts = module.split(".")
    if len(parts) > 1:
        return {f"atompipe.{parts[1]}"}
    if not names:
        return {"atompipe"}                       # `import atompipe`: see the attributes
    found: set[str] = set()
    for name in names:
        found |= _package_attribute(name)
    return found


def _package_attribute(name: str) -> set[str]:
    """What `atompipe.<name>` resolves to, without importing it.

    A submodule is itself. Anything else is answered by the package's
    `__getattr__` — from `models` (a spine module) or an alias (`AtompipeError`
    lives in `util`) — so the package module is in it too.
    """
    if name != "*" and (os.path.isfile(os.path.join(_PKG_DIR, f"{name}.py"))
                        or os.path.isfile(os.path.join(_PKG_DIR, name, "__init__.py"))):
        return {f"atompipe.{name}"}
    found = {"atompipe"}
    package = sys.modules.get("atompipe")
    aliases = vars(package).get("_ALIASES", {}) if package is not None else {}
    if isinstance(aliases, dict) and isinstance(aliases.get(name), str):
        found.add(f"atompipe.{aliases[name]}")
    return found


def _is_spine(module: str) -> bool:
    parts = module.split(".")
    return len(parts) > 1 and f"{parts[1]}.py" in _SPINE_MODULE_FILES


def _resolve_local(dotted: str, search: list[str], roots: tuple[str, ...]
                   ) -> tuple[bool, list[tuple[str, str, str]]]:
    """Where `dotted` lives, without importing anything.

    Returns `(local, found)`: whether its top-level name resolves to code at all
    (`_code_root`: under the roots, or beside them and not installed), and
    `(fullname, path, root)` for each file on the way down. Namespace
    packages resolve and contribute no file. `PathFinder.find_spec` is used rather
    than `importlib.util.find_spec`, which imports every parent package to answer
    for a dotted name.
    """
    found: list[tuple[str, str, str]] = []
    parts = dotted.split(".")
    locations: list[str] = list(search)
    local = False
    for depth in range(len(parts)):
        fullname = ".".join(parts[:depth + 1])
        try:
            spec = importlib.machinery.PathFinder.find_spec(fullname, locations)
        except (ImportError, ValueError, OSError, AttributeError):
            break
        if spec is None:
            break
        if spec.origin is not None and spec.has_location:
            origin = os.path.abspath(spec.origin)
            root = _code_root(origin, roots)
            if root is None:
                break
            found.append((fullname, origin, root))
        else:
            under = [loc for loc in (spec.submodule_search_locations or ())
                     if _code_root(loc, roots) is not None]
            if not under:
                break
        if depth == 0:
            local = True
        locations = list(spec.submodule_search_locations or ())
        if not locations:
            break
    return local, found


def _static_pass(recording: _Recording) -> None:
    """Everything the recording can learn from source without running it.

    * Every local import, at any depth, is resolved to its file. The recording
      finder already saw the ones that ran; this adds the ones that did not — an
      import inside a function body (`build()` or a gate importing a helper
      lazily), and a module-level import of a cached module whose name the file
      did not keep in its globals. Zero bundled hits today: every lazy import in
      the packs is third-party (R-4).
    * Every other top-level name that is not the standard library or `atompipe`
      is a third-party name: installed, or not importable at all. A name that
      resolves on `sys.path` to code beside the roots (`_code_root`) is local,
      like one under them. `atompipe.*` names outside the spine digest are
      `spine_extras`.

    A local file some module in `sys.modules` already ran contributes that
    module's closure; any other is digested as it is on disk and read in turn.
    """
    by_file: dict[str, Any] | None = None
    pending = list(recording.sources.items()) + list(recording.foreign.items())
    # Where a bare name can resolve locally: the roots, every directory the
    # closure already holds a file in, and any `sys.path` entry under the roots.
    # What slipped through with the roots alone: beam, thermal and openmodelica
    # fixtures put `gates/` on `sys.path` and `import _thermal_physics` — the
    # helper's file was recorded, and its name was listed as third-party too.
    shared = list(recording.roots)
    shared += sorted({os.path.dirname(p) for p in recording.files})
    shared += [entry for entry in sys.path
               if isinstance(entry, str) and entry and _owning_root(entry, recording.roots)]
    # Then the rest of `sys.path`, in its own order, after the file's own
    # directory, so a local name still resolves where it did. What slipped
    # through without it (review round 1, `mono`): a helper beside the project,
    # imported only inside a gate function from a `shared/` the module put on
    # `sys.path`, resolved nowhere and was listed as third-party — an instrument.
    rest = [entry for entry in sys.path
            if isinstance(entry, str) and entry and entry not in shared]
    done: set[str] = set()
    while pending:
        path, data = pending.pop()
        if path in done:
            continue
        done.add(path)
        imports, attributes = _static_imports(path, data)
        search = list(dict.fromkeys(shared + [os.path.dirname(path)] + rest))
        for level, module, names, _line in imports:
            if level:
                base = os.path.dirname(path)
                for _ in range(level - 1):
                    base = os.path.dirname(base)
                targets = [module] if module else []
                targets += [f"{module}.{n}" if module else n for n in names if n != "*"]
                lookup = [base]
            else:
                top = module.partition(".")[0]
                if top == "atompipe":
                    recording.spine_extras.update(
                        m for m in _spine_names(module, names) if not _is_spine(m))
                    continue
                if not top or top in _STDLIB:
                    continue
                local, _found = _resolve_local(top, search, recording.roots)
                if not local:
                    recording.third_party.add(top)
                    continue
                targets = [module] + [f"{module}.{n}" for n in names if n != "*"]
                lookup = search
            for target in targets:
                _local, found = _resolve_local(target, lookup, recording.roots)
                for fullname, origin, root in found:
                    if origin in recording.files:
                        continue
                    if not origin.endswith(tuple(importlib.machinery.SOURCE_SUFFIXES)):
                        recording.unmappable(fullname, origin, root)
                        continue
                    if by_file is None:
                        # One file can be in sys.modules twice (fdm-print loads
                        # `fdm_print_parts` by name and by path); the copy this
                        # loader ran carries the closure, so it wins.
                        by_file = {}
                        for loaded in list(sys.modules.values()):
                            where = _module_file(loaded)
                            if where is not None and (_norm(where) not in by_file
                                                      or _own_closure(loaded) is not None):
                                by_file[_norm(where)] = loaded
                    loaded = by_file.get(_norm(origin))
                    closure = _own_closure(loaded) if loaded is not None else None
                    if closure is not None:
                        recording.absorb(closure)
                        continue
                    recording.read_foreign(origin)
                    if origin in recording.foreign:
                        pending.append((origin, recording.foreign[origin]))
        for attribute in attributes:
            recording.spine_extras.update(
                m for m in _package_attribute(attribute) if not _is_spine(m))


def _purge(previous: CodeClosure | None, roots: tuple[str, ...], *, keep: str,
           memo: dict[str, str | None], everything: bool = False) -> None:
    """Drop from `sys.modules` every module that would serve stale code to this load.

    Two kinds. A module this loader ran — under the roots, beside them and not
    installed (`_code_root`: a monorepo's `shared/` helper, imported by name, is
    served to the next importer exactly like one under the roots), or in the
    previous version's closure — whose own closure no longer matches the disk:
    the next importer must re-run it, not pick up the old object (a helper
    edited after its first importer loaded is otherwise served to the second
    one — S-26 in-process, no pyc involved). And a module some other machinery loaded
    whose file the previous closure recorded at a digest it no longer has.
    `everything` drops the whole previous closure: the name now belongs to a
    different file, and none of the old one's helpers are this one's.
    """
    recorded = {_norm(p): digest for p, digest in (previous.files if previous else ())}
    for name, loaded in list(sys.modules.items()):
        if name == keep or not isinstance(loaded, types.ModuleType):
            continue
        where = _module_file(loaded)
        if where is None:
            continue
        key = _norm(where)
        closure = _own_closure(loaded)
        if closure is not None:
            if key not in recorded and _code_root(where, roots) is None:
                continue
            if (everything and key in recorded) or not _closure_current(closure, memo=memo):
                sys.modules.pop(name, None)
        elif key in recorded and (everything or _file_sha(where, memo) != recorded[key]):
            sys.modules.pop(name, None)


def _registered(registry: Any) -> list[tuple[str, Any]]:
    """`(gate id, fn)` for everything `registry` holds, via `ids()` and `get()`."""
    pairs = []
    for gate_id in list(registry.ids()):
        entry = registry.get(gate_id)
        if entry is not None:
            pairs.append((gate_id, entry[1]))
    return pairs


def _signature(fn: Any) -> tuple[Any, Any]:
    return getattr(fn, "__module__", None), getattr(fn, "__qualname__", None)


def _drop_stale_gates(registry: Any, name: str, previous: Any) -> None:
    """Unregister what an earlier run of module `name` put into `registry`.

    Before a re-execution, and before a re-adoption: the new functions are
    different objects under the same ids, and `Registry.register` rightly refuses
    a second function for an id it holds. A gate is this module's when its
    function is one the previous run recorded, or was defined in the module.
    """
    old = vars(previous).get(_GATES_ATTR) if previous is not None else None
    old_fns = {id(fn) for _spec, fn in (old or ())}
    for gate_id, fn in _registered(registry):
        if id(fn) in old_fns or getattr(fn, "__module__", None) == name:
            registry.unregister(gate_id)


def _readopt(registry: Any, module: Any, name: str) -> None:
    """Put a cached module's recorded `(spec, fn)` pairs into `registry`.

    A module served from the cache does not run, so its `@gate` decorators do
    not fire, and a fresh `Registry` — one per command, one per in-process test —
    would come back empty: a pack that looks like it ships no gates, which is
    the silent under-reporting `use_registry` exists to prevent. Stale entries
    go first: any function from this module that is not a current one, or an
    older version of a current one (same module and qualname).
    """
    pairs = vars(module).get(_GATES_ATTR) or ()
    current = {id(fn) for _spec, fn in pairs}
    versions = {_signature(fn) for _spec, fn in pairs}
    for gate_id, fn in _registered(registry):
        if id(fn) in current:
            continue
        if getattr(fn, "__module__", None) == name or _signature(fn) in versions:
            registry.unregister(gate_id)
    for spec, fn in pairs:
        entry = registry.get(spec.id)
        if entry is not None and entry[1] is fn:
            continue
        registry.register(spec, fn)


def _reusable(cached: Any, path: str, roots: tuple[str, ...], registry: Any,
              attrs: Mapping[str, Any] | None, memo: dict[str, str | None]) -> bool:
    """May `load_source_module` hand back the module already in `sys.modules`?

    Only the module this loader ran from this very file, with the globals the
    caller seeds (a pack's `PACK_DIR`) unchanged, with its gate registrations
    recorded when a registry is asking for them, and with every file of its
    recorded closure still at its recorded digest. Anything else runs again.
    """
    if not isinstance(cached, types.ModuleType):
        return False
    where = _module_file(cached)
    if where is None or _norm(where) != _norm(path):
        return False
    closure = _own_closure(cached)
    if closure is None:
        return False
    namespace = vars(cached)
    if registry is not None and namespace.get(_GATES_ATTR) is None:
        return False
    for key, value in (attrs or {}).items():
        if key not in namespace or namespace[key] != value:
            return False
    return _closure_current(closure, roots=roots, memo=memo)


def load_source_module(path: str, *, name: str, roots: Iterable[str],
                       registry: Any = None,
                       attrs: Mapping[str, Any] | None = None) -> types.ModuleType:
    """Execute `path` as module `name`, fresh and recorded; or serve it unchanged.

    The one loader for Python the spine runs on a project's behalf: pack gate
    modules, project gate modules, fixtures, the known-good module and helpers
    loaded by path (`load_path`). Four promises:

    * **Fresh bytes.** The source is read and those bytes compiled; no `.pyc` is
      read or written, for the module or for any import of code while it runs
      (S-26). Code is `_code_root`'s: a file under `roots`, or beside them and
      not installed — a monorepo's `shared/` helper is the gate's code, never a
      third-party instrument.
    * **A recorded closure.** Every file of code that ran — or that a function
      body imports lazily, read statically — is stored on the module as
      `__atompipe_code__` (a `CodeClosure`; `code_closure` reads it). Computed
      source compiled or executed from a frame in code, or an import of code
      that is not Python source, gives up precision for every `*.py` under the
      owning root, and says so in `fallback`.
    * **A content-keyed cache.** The module already in `sys.modules` under `name`
      is returned only if this loader ran it from this file and every recorded
      file still has its recorded digest. Otherwise every stale module it
      depended on is purged from `sys.modules` and it runs again. In-process
      callers — a test's second `cli.main`, `pack validate` after an edit — then
      never run old code under a new digest.
    * **Gates follow the module.** With `registry`, the `(spec, fn)` pairs the
      module registered into it while running are recorded as
      `__atompipe_gates__`; a cache hit re-adopts them into the `registry` the
      caller passes now, dropping stale ids first, so a fresh `Registry` is never
      handed back empty. `registry` must be the one `@gate` decorates into
      during the load — the caller wraps this call in `gates.use_registry(...)`;
      this module never imports `gates`.

    `attrs` are set on the module before it runs (a pack's `PACK` and
    `PACK_DIR`, which a gate module may read at import time) and are part of the
    cache key. `__file__` is `os.path.abspath(path)`, never a realpath:
    fluids-analytic's control finds its gate module by scanning `sys.modules`
    for exactly that (packs:H14).

    Raises whatever the module raised, after removing it from `sys.modules` and
    unregistering what it managed to register: a module that failed to import
    has registered nothing. The caller words the error; this is not the place
    that knows whether the file was a pack's or the project's.

    `sys.path` is the caller's: a pack loader puts its directory there for the
    duration, exactly as before.
    """
    abspath = os.path.abspath(path)
    owned = _roots(roots, abspath)
    for running in _STACK:
        if running.name == name:
            # A module loading itself while it runs: import semantics, the
            # partially initialised module, rather than infinite recursion.
            partial = sys.modules.get(name)
            if partial is not None:
                return partial
    memo: dict[str, str | None] = {}
    cached = sys.modules.get(name)
    if cached is not None and _reusable(cached, abspath, owned, registry, attrs, memo):
        if _STACK:
            # Served, not run: it is still the caller's code (a helper loaded by
            # path from a gate module's body).
            _STACK[-1].absorb(_own_closure(cached))
        if registry is not None:
            _readopt(registry, cached, name)
        return cached

    previous = _own_closure(cached) if cached is not None else None
    moved = cached is not None and _norm(_module_file(cached) or "") != _norm(abspath)
    _purge(previous, owned, keep=name, memo=memo, everything=moved)
    if registry is not None:
        _drop_stale_gates(registry, name, cached)

    loader = _FreshLoader(name, abspath, roots=owned, memo=memo)
    spec = importlib.util.spec_from_file_location(name, abspath, loader=loader)
    if spec is None:                              # pragma: no cover - defensive
        raise ImportError(f"cannot load {abspath}: no import machinery accepted it")
    module = importlib.util.module_from_spec(spec)
    for key, value in (attrs or {}).items():
        setattr(module, key, value)
    before = set(registry.ids()) if registry is not None else set()
    # Registered BEFORE execution: a module that imports itself, a dataclass
    # that resolves its own annotations, and `@gate` reading the module's `PACK`
    # all look the module up by name while the body is still running.
    sys.modules[name] = module
    try:
        loader.exec_module(module)
    except BaseException:
        if sys.modules.get(name) is module:
            del sys.modules[name]
        if registry is not None:
            for gate_id in list(registry.ids()):
                if gate_id not in before:
                    registry.unregister(gate_id)
        raise
    if registry is not None:
        added = []
        for gate_id in registry.ids():
            if gate_id not in before:
                entry = registry.get(gate_id)
                if entry is not None:
                    added.append((entry[0], entry[1]))
        setattr(module, _GATES_ATTR, tuple(added))
    return module


def load_path(path: str) -> types.ModuleType:
    """Load a helper by its path, under a name salted with that path.

    For a helper a gate module and its fixture must share, where the directory
    is on `sys.path` only while gates load (fdm-print's fold and process model).
    Two copies of one pack in a process get two names, so neither runs the
    other's helper (packs:H4); two gate modules loading one path get one module,
    content-keyed like everything `load_source_module` serves. Called while a
    module is being loaded, the helper joins that module's closure whether it
    runs or is served from the cache.
    """
    abspath = os.path.abspath(path)
    stem = re.sub(r"\W", "_", os.path.splitext(os.path.basename(abspath))[0]) or "module"
    salt = hashlib.sha256(os.fsencode(abspath)).hexdigest()[:12]
    parent = _STACK[-1] if _STACK else None
    roots = (parent.roots if parent is not None and _owning_root(abspath, parent.roots)
             else _roots((), abspath))
    return load_source_module(abspath, name=f"{_PATH_PREFIX}{stem}_{salt}", roots=roots)


def is_code(path: str, roots: Iterable[str] = ()) -> bool:
    """Is the Python file at `path` code — under `roots`, or beside them and not
    installed (`_code_root`) — rather than an installed instrument?

    The one test the recording finder applies to an import, for a caller that
    must decide before anything runs whether a module it was named is code to
    load fresh and record (`gates.load_fixture`'s `module:function` form) or an
    instrument the stock import system serves. Not `_roots`: with no roots it
    makes the file's own directory one, and a root owns what is under it — a
    module in site-packages would own itself and read as code.
    """
    owned = tuple(dict.fromkeys(_norm(r) for r in roots if r))
    return _code_root(path, owned) is not None


def code_closure(obj: Any) -> CodeClosure | None:
    """The recorded closure of a module, or of the module that defines `obj`.

    None for anything this loader did not run — a gate registered from Python
    in a test, a lambda — which `verdicts.code_digest` then digests by its
    defining file, or reports as opaque; never as a digest of nothing.
    """
    if isinstance(obj, types.ModuleType):
        return _own_closure(obj)
    target = getattr(obj, "__func__", obj)        # a bound method's function
    try:
        name = getattr(target, "__module__", None)
    except Exception:                             # noqa: BLE001 - a proxy that refuses
        return None
    module = sys.modules.get(name) if isinstance(name, str) else None
    return _own_closure(module) if module is not None else None


# --------------------------------------------------------------------------- #
# module-level memos:  emptied before every run of the code that holds them
# --------------------------------------------------------------------------- #
#: The type `functools.lru_cache` and `functools.cache` wrap a function in —
#: taken from a wrapper rather than named, because its name is private.
_LRU_WRAPPER = type(functools.lru_cache(maxsize=None)(lambda: None))

#: How far `clear_caches` follows `__wrapped__` from a module global to a memo
#: under it: a decorator stacked over a cache (`@traced @functools.cache`) hides
#: the wrapper behind one level, and nobody stacks eight. Rejected: no limit — a
#: hand-built `__wrapped__` that points back at itself would loop.
_UNWRAP_DEPTH = 8


def _home_module(obj: Any) -> Any:
    """The module `obj` is, or the one that defines it (`code_closure`'s lookup)."""
    if isinstance(obj, types.ModuleType):
        return obj
    target = getattr(obj, "__func__", obj)
    try:
        name = getattr(target, "__module__", None)
    except Exception:                             # noqa: BLE001 - a proxy that refuses
        return None
    return sys.modules.get(name) if isinstance(name, str) else None


def _closure_modules(obj: Any) -> list[types.ModuleType]:
    """Every loaded module whose code `obj` runs: its own, and each module whose
    file is in its recorded closure — in `sys.modules`, or held only by
    reference (a helper some other machinery executed and never registered).
    A module this loader did not run counts only outside the interpreter's
    trees and the spine: a gate registered from Python in a test is keyed by its
    defining file, and a `functools.partial` would otherwise hand over
    `functools` itself."""
    home = _home_module(obj)
    if home is None:
        return []
    closure = _own_closure(home)
    if closure is None:
        where = _module_file(home)
        if where is None or any(_under(_norm(where), d) for d in _excluded_dirs()):
            return []
        return [home]
    files = {_norm(path) for path, _sha in closure.files}
    found: dict[int, types.ModuleType] = {id(home): home}
    for loaded in list(sys.modules.values()):
        if isinstance(loaded, types.ModuleType) and id(loaded) not in found:
            where = _module_file(loaded)
            if where is not None and _norm(where) in files:
                found[id(loaded)] = loaded
    queue = list(found.values())
    while queue:
        for value in list(vars(queue.pop()).values()):
            if isinstance(value, types.ModuleType) and id(value) not in found:
                where = _module_file(value)
                if where is not None and _norm(where) in files:
                    found[id(value)] = value
                    queue.append(value)
    return list(found.values())


def _memo_clearer(value: Any) -> Any:
    """`value.cache_clear` when `value` — or what it wraps, `_UNWRAP_DEPTH` deep
    — is a memo that knows how to empty itself, else None. functools' wrapper by
    its type; a plain function carrying a callable `cache_clear` of its own
    (cachetools' `cached` copies functools' protocol). Read through `vars`,
    never `getattr`, on anything else: a module global may be a proxy whose
    attribute lookup runs code."""
    seen: set[int] = set()
    for _depth in range(_UNWRAP_DEPTH):
        if value is None or id(value) in seen:
            return None
        seen.add(id(value))
        if isinstance(value, _LRU_WRAPPER):
            return value.cache_clear
        if not isinstance(value, types.FunctionType):
            return None
        own = vars(value)
        if callable(own.get("cache_clear")):
            return own["cache_clear"]
        value = own.get("__wrapped__")
    return None


def _memos_in(module: types.ModuleType) -> list[tuple[str, Any]]:
    """`(dotted name, cache_clear)` for every memo `module` holds at module
    level: a global, or an attribute of a class the module defines — a cached
    staticmethod, classmethod, method or property getter, which every instance
    shares."""
    name = getattr(module, "__name__", "?")
    found: list[tuple[str, Any]] = []
    for key, value in list(vars(module).items()):
        clear = _memo_clearer(value)
        if clear is not None:
            found.append((f"{name}.{key}", clear))
            continue
        if not isinstance(value, type) or vars(value).get("__module__") != name:
            continue
        for attr, member in list(vars(value).items()):
            if isinstance(member, (staticmethod, classmethod)):
                member = member.__func__
            elif isinstance(member, property):
                member = member.fget
            clear = _memo_clearer(member)
            if clear is not None:
                found.append((f"{name}.{key}.{attr}", clear))
    return found


def clear_caches(obj: Any) -> tuple[str, ...]:
    """Empty every functools memo held at module level by the code `obj` runs
    (`_closure_modules`); return the dotted names emptied, sorted.

    `gates.run_gate` calls it on the gate function before the gate runs, and
    `gates._build_control` and `verdicts._known_good` on the fixture and on
    `known_good.context` before theirs, so every run reads its files itself,
    inside its own trace window. What slipped through (review round 2): S-27
    was closed for `ctx.extra` only. A `functools.lru_cache` around a file read
    is the same cross-gate channel — the first caller opens the file, every
    later one gets the value and opens nothing, so no audit event puts the file
    in its read set. And the admission control runs its gate FIRST, in the same
    process: even a lone memoised gate was a hit on its real run. `probe.lru`'s
    entry keyed no file while its control named `data/limit2.txt`; after the file
    was zeroed a plain `check` served the cached PASS as current and a forced run
    FAILed it. Emptying the memo costs a re-read per run; a memo that survives
    is an input nothing keys.

    Only memos that can empty themselves are reached. Any other module-global
    memo — a dict filled from a function body, a `global` rebound from one, a
    mutable default argument — cannot be emptied from outside, and
    `atompipe doctor`'s `memos` row names each one. *Rejected:* re-executing
    the module before every run (it re-registers its gates, and a module whose
    import is expensive — trimesh, a solver binding — pays that per gate);
    clearing every `cache_clear` found by attribute lookup (a proxy's
    `__getattr__` would run code the gate never called).
    """
    cleared: list[str] = []
    for module in _closure_modules(obj):
        for name, clear in _memos_in(module):
            clear()
            cleared.append(name)
    return tuple(sorted(set(cleared)))


# --------------------------------------------------------------------------- #
# loading the model
# --------------------------------------------------------------------------- #
def _import_file(path: str, roots: Iterable[str] | None = None) -> Any:
    """Execute the model file as a module, with its own directory importable.

    The directory goes on `sys.path` so a model can split itself across several
    files (`import geometry` next to `bracket.py`) without becoming a package
    first. It is removed again afterwards, and that removal is not politeness:
    a `model/` directory containing `types.py` or `json.py` on `sys.path` will
    shadow the standard library for every later import in the process,
    including the spine's own. The window is kept as short as the import itself.

    The model always runs again — every load is a fresh `CONFIG` — and while it
    runs it is recorded like any other module: siblings imported under `roots`
    come through the fresh loader, and a sibling whose file changed since the
    last load is purged first, so `import geometry` cannot hand back the old
    module (S-26, which the entry-only fresh loader left open for siblings).

    The consequence, which is worth knowing when you split a model: import your
    sibling modules at MODULE level, not lazily inside `build()`. By the time
    `build()` runs, the path is back to normal — module-level imports are
    already cached in `sys.modules`, a first lazy import is not. (Its file is
    still in the model's closure, read statically; the bytes it runs are the
    stock import system's.)

    Only paths this function actually ADDED are removed, so a model that
    legitimately extends `sys.path` for itself keeps its addition.
    """
    directory = os.path.dirname(path)
    is_package = os.path.basename(path) == "__init__.py"

    # For a package, the PARENT is what makes `import hull.parts` resolve; the
    # package directory itself is what makes flat sibling imports resolve. Both,
    # in that order, so the package name wins over a same-named submodule.
    additions = [directory]
    if is_package:
        additions.insert(0, os.path.dirname(directory))

    stem = os.path.basename(directory) if is_package else os.path.splitext(os.path.basename(path))[0]
    name = _MODULE_PREFIX + (stem or "model")

    owned = _roots(roots, path)
    memo: dict[str, str | None] = {}
    loader = _FreshLoader(name, path, roots=owned, memo=memo)   # never a stale .pyc
    spec = importlib.util.spec_from_file_location(
        name,
        path,
        loader=loader,
        submodule_search_locations=[directory] if is_package else None,
    )
    if spec is None or spec.loader is None:       # a .py we cannot make a spec for
        raise AtompipeError(
            f"cannot import {path}: python does not recognise it as a module "
            f"(is it a .py file?)"
        )

    module = importlib.util.module_from_spec(spec)
    added = [d for d in additions if d and d not in sys.path]
    for d in reversed(added):
        sys.path.insert(0, d)
    # Registered BEFORE execution: a module that imports itself, a dataclass
    # that resolves its own annotations, and pickling all look the module up by
    # name while the body is still running.
    previous = sys.modules.get(name)
    moved = previous is not None and _norm(_module_file(previous) or "") != _norm(path)
    _purge(_own_closure(previous) if previous is not None else None, owned,
           keep=name, memo=memo, everything=moved)
    sys.modules[name] = module
    try:
        loader.exec_module(module)
    except BaseException as exc:                  # noqa: BLE001 - re-raised below
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise AtompipeError(f"model {path} failed to import\n  {_explain(exc)}") from exc
    finally:
        for d in added:
            try:
                sys.path.remove(d)
            except ValueError:                    # the model removed it itself
                pass
    return module


def _resolve_config(module: Any, path: str) -> Any:
    """Find the config: `CONFIG` first, then `Config()`.

    Both spellings are accepted because both are honest. `CONFIG = Config()` is
    what a model writes once it has non-default values it wants to keep; a bare
    `Config` dataclass with defaults is what a model starts as. Refusing the
    second would make the first five minutes of a project feel like paperwork.

    What is NOT accepted is a plain dict or a namespace. `params_from_model`
    reads field order, defaults and types out of `dataclasses.fields`, and a
    dict has none of those — the provenance of a number would have nowhere to
    live.
    """
    config = getattr(module, "CONFIG", None)

    if config is None or isinstance(config, type):
        # `CONFIG = Config` (the class, not an instance) is a common slip and
        # means exactly what `Config` alone means, so treat them the same.
        factory = config if isinstance(config, type) else getattr(module, "Config", None)
        if isinstance(factory, type):
            try:
                config = factory()
            except Exception as exc:              # noqa: BLE001 - user's __init__
                raise AtompipeError(
                    f"{path} defines Config but Config() could not be "
                    f"constructed with no arguments; give every field a default "
                    f"or add `CONFIG = Config(...)`\n  {_explain(exc)}"
                ) from exc

    if config is None:
        raise AtompipeError(
            f"{path} exposes no model config: expected a module-level `CONFIG` "
            f"(a dataclass instance) or a `Config` dataclass constructible with "
            f"no arguments"
        )

    if not dataclasses.is_dataclass(config) or isinstance(config, type):
        raise AtompipeError(
            f"CONFIG in {path} is a {type(config).__name__}, not a dataclass "
            f"instance. The config carries the field order, defaults and types "
            f"that every parameter's provenance hangs off — use "
            f"`@dataclass class Config: ...` and `CONFIG = Config()`"
        )
    return config


def load_model(root: str, entry: str | None = None) -> LoadedModel:
    """Import the project's model and resolve its config and params.

    `entry` defaults to `ledger.meta.model_entry`; pass it explicitly to load a
    model that is not the project's own (a fixture, a candidate revision) with
    no ledger read at all — that saves the tier-0 loop a file read it does not
    need (rule 10).

    Everything a user can get wrong here — a missing file, a syntax error, an
    uninstalled import, a config that is a dict — raises `AtompipeError` with
    the real message and the line it came from. That is the whole job: this is
    the first spine function a new project touches, and it is the one that most
    often has something to say.
    """
    path = _resolve_entry(root, entry)
    # The project root owns the model's code, so a sibling under `model/` and a
    # helper the model imports from elsewhere in the project are both recorded;
    # the model's own directory is a root too, for an entry that lives outside
    # the project, and so that computed source in the model falls back to the
    # model's directory rather than every `*.py` in the project.
    module = _import_file(path, roots=(root, os.path.dirname(path)))
    relative = rel(path, root)

    config = _resolve_config(module, relative)

    builder = getattr(module, "build", None)
    if not callable(builder):
        raise AtompipeError(
            f"{relative} has no `build(config) -> dict`. The model is the only "
            f"source of truth, and build() is how everything downstream — "
            f"gates, reports, generators — reads it; without it the model is a "
            f"pile of constants nobody can check"
        )

    model = LoadedModel(module=module, config=config, entry=relative)
    model.params = params_from_model(model)
    return model


# --------------------------------------------------------------------------- #
# projection
# --------------------------------------------------------------------------- #
def project(model: LoadedModel) -> dict[str, Any]:
    """Run `build()` and return `{"config": {...}, "derived": {...}}`, JSON-safe.

    This is the ONLY view of a model the rest of the spine gets. A gate is
    handed the projection, not the module, which is what stops a gate from
    recomputing a section modulus slightly differently from the model and
    "proving" a claim about a part that does not exist (rule 2).

    `config` is the dataclass fields verbatim: the inputs. `derived` is whatever
    `build()` returned: the consequences. The split is what lets a report say
    "you changed `thickness`" instead of "137 numbers changed".

    Every value passes through `models._enc` and is then VALIDATED. A value JSON
    cannot hold raises `AtompipeError` naming the field — see `_check_json_safe`
    for why each rejection is stricter than `json.dumps` is.
    """
    builder = getattr(model.module, "build", None)
    if not callable(builder):                     # re-checked: callers construct
        raise AtompipeError(f"{model.entry} has no callable build(config)")

    try:
        derived = builder(model.config)
    except AtompipeError:
        raise                                     # the model used our error type
    except Exception as exc:                      # noqa: BLE001 - user's build()
        raise AtompipeError(
            f"{model.entry}: build(config) raised\n  {_explain(exc)}"
        ) from exc

    if not isinstance(derived, dict):
        raise AtompipeError(
            f"{model.entry}: build(config) returned {type(derived).__name__}, "
            f"expected a dict of derived values. Every gate reads this dict by "
            f"key name; a tuple or an object gives them nothing to ask for"
        )

    return {
        "config": _safe(model.config, "config"),
        "derived": _safe(derived, "derived"),
    }


def flat_params(projection: dict | None) -> tuple[dict[str, Any], list[str]]:
    """Flatten a projection to `{name: value}` for `GateContext.params`, with conflicts.

    Derived values first, then config over the top, so an INPUT always wins a
    name collision. `build()` returning a key that shares a config field's name
    is common and harmless when the values agree (the reference model echoes
    `material` straight back); when they do NOT agree, one of the two numbers a
    gate could read is not the model's input, and which one it got would depend
    on dict ordering. So the input wins, and the disagreement is returned to be
    reported rather than resolved silently — that is rule 6, cross-representation
    agreement, applied at the cheapest place it can be applied.

    The one copy (S-28). `cli` flattened for `GateContext.params` and `site` for
    `ViewContext.params`, each with its own copy of these lines, kept
    "byte-for-byte" in sync by a comment — and a viewgen and a gate that read one
    name must get one number, or the picture is of a different design from the
    one that was measured. It lives here because the projection does, and
    because a verdict's inputs are digested from exactly this dict: a second
    copy that drifted would key a verdict on values no gate read.

    Equality is Python's: `7.0` against `7`, or `True` against `1`, is not a
    conflict. `tests/test_codeload.FlatParamsIsTheOldShape` pins the output —
    order and types included — to what the two copies produced.
    """
    if not projection:
        return {}, []
    config = dict(projection.get("config") or {})
    derived = dict(projection.get("derived") or {})
    conflicts = [
        f"{name}: config {config[name]!r} vs build() {derived[name]!r}"
        for name in sorted(set(config) & set(derived))
        if config[name] != derived[name]
    ]
    flat = dict(derived)
    flat.update(config)
    return flat, conflicts


def _canonical(projection: dict[str, Any]) -> str:
    """The one byte-level representation of a projection.

    `sort_keys` because a dict's insertion order is not part of the model's
    meaning and must not change its hash. `allow_nan=False` so a NaN that
    somehow skipped validation fails loudly instead of hashing as the token
    `NaN`. Compact separators because nothing reads this string — the readable
    copy is what `write_projection` puts on disk.
    """
    try:
        return json.dumps(
            projection,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise AtompipeError(
            f"projection is not JSON-serialisable ({exc}); build it with "
            f"modelio.project(), which validates every field and names the one "
            f"that is wrong"
        ) from exc


def model_hash(projection: dict[str, Any]) -> str:
    """Stable short hash of a projection: a display id, and nothing else.

    Staleness is the quiet failure this was built to prevent: gates pass, someone
    edits `thickness`, and the report keeps showing yesterday's green. Until 1.2
    it was recorded with each sweep and compared against the next — one hash
    deciding every gate, which a model that failed to import made compare equal
    (S-21). Staleness is per gate now (`verdicts.freshness`); this hash survives
    as a display id (`check --json`'s `model_hash`, `doctor`, `status --json`).

    Stable means stable across PROCESSES, not just within one — which is why
    `project()` rejects sets (hash-randomised iteration order) and why this
    sorts keys. 12 hex chars: enough to distinguish revisions, short enough to
    be compared by eye.
    """
    return short_hash(_canonical(projection))


def write_projection(root: str, projection: dict[str, Any]) -> str:
    """Write `.atompipe/model.json` and return its path.

    This file is the diffable view of the model: sorted keys, indent 2, one
    value per line, so `git diff` after a parameter change reads as "deflection
    0.70 -> 0.47" rather than as one enormous line. It is generated output —
    deleting it loses nothing — but it is the artefact that makes a design
    change reviewable by someone who does not read Python.

    Written EXACTLY as given, with no hash or timestamp folded in: a file that
    contains its own hash cannot be checked against `model_hash(read_json(...))`
    by anyone, and a generated file with a timestamp in it changes on every
    build and teaches every reviewer to ignore its diff.
    """
    path = store.project_paths(root)["projection"]
    atomic_write_json(path, projection)
    return path


# --------------------------------------------------------------------------- #
# parameters and their provenance
# --------------------------------------------------------------------------- #
#: How a refusal spells one rejected alternative in a PARAMS dict item.
_LOSER = '{"value": ..., "why": ...}'


def _unknown_key(entry: str, where: str, key: Any, known: Iterable[str]) -> AtompipeError:
    """The refusal for a key a PARAMS dict item does not know, with a suggestion."""
    names = sorted(known)
    close = (difflib.get_close_matches(key, names, n=1, cutoff=0.6)
             if isinstance(key, str) else [])
    hint = f" — did you mean {close[0]!r}?" if close else ""
    return AtompipeError(
        f"{entry}: {where} has an unknown key {key!r}{hint} It takes "
        f"{', '.join(names)}. A key it does not know used to be dropped without a "
        f"word, and whatever it carried — the units, the loser and why it lost — "
        f"with it"
    )


def _strict_item(item: dict, entry: str, where: str) -> None:
    """Refuse a PARAMS dict item whose keys `Param.from_dict` would drop.

    What slipped through (the model-side cousin of S-40): `Param.from_dict` keeps
    only the keys it knows, so `{"name": "thickness", "unit": "mm"}` loaded as a
    parameter with no units, and a rejection spelt `{"value": ..., "whi": ...}`
    crashed on a TypeError from inside the dataclass. Both now stop the load
    with the key, the entry and the nearest real key. Measured first (R-4,
    R-10): the bracket's PARAMS is the only bundled one, and it loads.
    """
    param_keys = {f.name for f in dataclasses.fields(Param)}
    rejected_keys = {f.name for f in dataclasses.fields(Rejected)}
    for key in item:
        if key not in param_keys:
            raise _unknown_key(entry, where, key, param_keys)
    rejected = item.get("rejected")
    if rejected is None:
        return
    if isinstance(rejected, (str, bytes, dict)) or not isinstance(rejected, (list, tuple)):
        raise AtompipeError(
            f"{entry}: {where} `rejected` is a {type(rejected).__name__}; it takes a "
            f"list of {_LOSER} items, one per alternative that lost"
        )
    for index, loser in enumerate(rejected):
        if isinstance(loser, Rejected):
            continue
        if not isinstance(loser, dict):
            raise AtompipeError(
                f"{entry}: {where} rejected[{index}] is a {type(loser).__name__}; each "
                f"alternative that lost is a {_LOSER} item — "
                f"a loser with no reason is re-proposed by the next reader"
            )
        for key in loser:
            if key not in rejected_keys:
                raise _unknown_key(entry, f"{where} rejected[{index}]", key, rejected_keys)
        for key in ("value", "why"):
            if key not in loser:
                raise AtompipeError(
                    f"{entry}: {where} rejected[{index}] has no {key!r}: an alternative "
                    f"that lost is what lost AND why"
                )


def _explicit_params(module: Any, entry: str) -> list[Param]:
    """Read the model's optional `PARAMS`: tolerant of its shape, strict on its keys.

    Dicts are accepted alongside `Param` instances because a model that
    generates its parameter table (from a CSV of stock sizes, say) naturally
    produces dicts, and a model with zero dependencies (the bracket) writes
    dicts so it still runs without atompipe on the path. `Param.from_dict` is
    the contract's own reader, but it is lenient — so a dict item's keys are
    checked first (`_strict_item`).
    """
    raw = getattr(module, "PARAMS", None)
    if raw is None:
        return []
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise AtompipeError(
            f"{entry}: PARAMS must be a list of Param records, got "
            f"{type(raw).__name__}"
        )

    params: list[Param] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(raw):
        if isinstance(item, dict):
            label = item.get("name")
            _strict_item(item, entry, f"PARAMS[{index}]" + (
                f" ({label!r})" if isinstance(label, str) and label.strip() else ""))
            # `value` is a required field on Param but a POINTLESS one to write
            # here: for anything that is also a config field the dataclass owns
            # the value and this one is overwritten below. Defaulting it to None
            # is what lets a model write the useful half — the units and the
            # rationale — without restating a number it would only get wrong.
            item = Param.from_dict({"value": None, **item})
        if not isinstance(item, Param):
            raise AtompipeError(
                f"{entry}: PARAMS[{index}] is a {type(item).__name__}; every "
                f"entry must be an atompipe Param (or a dict of one)"
            )
        name = (item.name or "").strip()
        if not name:
            raise AtompipeError(
                f"{entry}: PARAMS[{index}] has no name; a parameter's name is "
                f"how a claim, a gate and a decision all refer to the same number"
            )
        if name in seen:
            # Silently keeping the last one loses whichever rationale was
            # written first, and the loss is invisible in the report.
            raise AtompipeError(
                f"{entry}: PARAMS declares {name!r} twice (entries "
                f"{seen[name]} and {index}); merge them"
            )
        seen[name] = index
        params.append(item)
    return params


def _config_source(model: LoadedModel) -> str:
    """Absolute path of the file defining the model's config class.

    Falls back through the module's own `__file__` and finally the recorded entry,
    because every one of these can legitimately be missing (a class built by a
    decorator, a module loaded from a zip) and losing prose must never be fatal.
    """
    for getter in (lambda: inspect.getfile(type(model.config)),
                   lambda: getattr(model.module, "__file__", "") or "",
                   lambda: model.entry):
        try:
            path = getter()
        except (TypeError, OSError):
            continue
        if path and os.path.isfile(path):
            return path
    return model.entry


def field_docstrings(entry: str, class_name: str) -> dict[str, str]:
    """Attribute docstrings from the model's config dataclass, by AST.

    Python has no runtime access to the string literal that follows a field:

        @dataclass
        class Config:
            thickness: float = 7.0
            '''mm. Deflection goes as 1/t^3, so thickness is the cheap lever.'''

    ...but that is exactly where a careful author writes the rationale, because it
    is the idiomatic place and it sits against the value it explains. Parsing it
    out means rule 3 (every constant carries its provenance) costs nothing beyond
    normal Python, and — more importantly — it avoids the trap of demanding a
    parallel `PARAMS` table that repeats every field name. That table would be a
    second source of truth for the field list, which is precisely what rule 2
    forbids, and it would drift the first time somebody added a field.

    So: `PARAMS` stays available for what a docstring cannot carry (rejected
    alternatives, units as data, gate bindings), and the docstring carries the
    prose — why THIS value; what lost goes in `PARAMS` (the bracket's docstring
    quoted a loser's margin as "~5x" where the model said 7.5x, S-42: a number in
    prose is a number nobody recomputes). An explicit `PARAMS` rationale wins
    over a docstring when both exist.

    Returns {} on any parse failure — a model that cannot be parsed can still be
    imported and run, and losing prose is not a reason to refuse to load.
    """
    try:
        with open(entry, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=entry)
    except (OSError, SyntaxError):
        return {}

    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            out.update(_attribute_docstrings(node))
    return out


def _attribute_docstrings(node: ast.ClassDef) -> dict[str, str]:
    """`{field: docstring}` for one class body, whitespace collapsed.

    The one reading of "the string after a field", shared by `field_docstrings`
    (the loaded model) and `static_param_prose` (the model's text, never run),
    so the two cannot normalise the same docstring two ways.
    """
    out: dict[str, str] = {}
    body = node.body
    for i, stmt in enumerate(body):
        # A field is `name: type` or `name: type = default`; its docstring is
        # the bare string expression immediately after it.
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        if i + 1 >= len(body):
            continue
        nxt = body[i + 1]
        if (isinstance(nxt, ast.Expr) and isinstance(nxt.value, ast.Constant)
                and isinstance(nxt.value.value, str)):
            text = " ".join(nxt.value.value.split())
            if text:
                out[stmt.target.id] = text
    return out


def params_from_model(model: LoadedModel) -> list[Param]:
    """Merge the model's `PARAMS` provenance with its dataclass fields.

    The split of duties is the point:

    * the **dataclass field** owns the VALUE — always, even when `PARAMS` states
      one. A literal repeated in `PARAMS` is a second source of truth, and the
      two WILL drift (that is rule 2, and it is not a hypothetical: a config
      says 7.0, a params table says 6.0, and the report defends a number the
      part was never built with). The field wins, silently and by design.
    * **`PARAMS`** owns everything a field cannot express: units, the rationale,
      the rejected alternatives, the source, which gates protect it.

    Order is the model's own field order, then any `PARAMS` entry that matches
    no field — those are kept, because a model legitimately documents constants
    that live outside the config (a material property, a fastener standard).

    Fields with no `PARAMS` entry still become `Param`s, with empty units and an
    empty rationale, and `undocumented_params` is how the report nags about
    them. A number with no rationale is a number nobody can defend.
    """
    declared = {p.name: p for p in _explicit_params(model.module, model.entry)}
    # Resolve from the CLASS, not from `model.entry`. `entry` is the relative path
    # the ledger stores, and a model that has grown into a package defines its
    # config in whichever module it likes — the docstrings live with the class, so
    # ask the class where it lives.
    docs = field_docstrings(_config_source(model), type(model.config).__name__)
    used: set[str] = set()
    merged: list[Param] = []

    for f in dataclasses.fields(model.config):
        value = _safe(getattr(model.config, f.name), f"config.{f.name}")
        param = declared.get(f.name)
        if param is None:
            merged.append(Param(name=f.name, value=value,
                                rationale=docs.get(f.name, "")))
            continue
        used.add(f.name)
        # An explicit PARAMS rationale wins; the docstring fills the gap when it
        # is silent, so the two can be used together without either being a
        # partial duplicate of the other.
        if not param.rationale and f.name in docs:
            param = dataclasses.replace(param, rationale=docs[f.name])
        # Copy, so repeated calls cannot mutate the model module's own PARAMS
        # list — a loader that edits the thing it loaded is a loader that gives
        # a different answer the second time it is called.
        merged.append(dataclasses.replace(param, value=value))

    for name, param in declared.items():
        if name not in used:
            merged.append(
                dataclasses.replace(param, value=_safe(param.value, f"PARAMS.{name}"))
            )
    return merged


# --------------------------------------------------------------------------- #
# what the model states, read and never run  (the migration's params rule)
# --------------------------------------------------------------------------- #
def static_param_prose(root: str, entry: str | None) -> dict[str, dict[str, str]]:
    """`{name: {"rationale": str, "units": str}}`: what the model's TEXT states.

    The 1.3 migration keeps a param record's `rationale` and `units` only where
    the model states none (spec §3.15, the params rule), and it has to ask "does
    the model state one?" without running the model: the migration is a pure
    function of the legacy ledger and this, it runs on a project whose model may
    not import at all, and `store` must never execute user code. So this PARSES
    the entry file — `ast.parse`, never an import — and reads two things:

    * the attribute docstrings of the config class, normalised exactly as
      `field_docstrings` normalises them (one helper, `_attribute_docstrings`);
    * `rationale` and `units` string constants from the `PARAMS` items that are
      dict literals or `Param(...)` calls (keywords, or positions in `Param`'s
      field order).

    A name appears only when something is stated, with `""` for the half that is
    not. An empty or missing entry, a directory without `__init__.py`, or a file
    that does not parse states nothing — `{}` — and the migration is then
    lossless.

    The one property that matters: it never says the model states something the
    running model does not. Saying too MUCH drops a hand-written rationale the
    model lacks — the lossy migration this rule exists to prevent; saying too
    little keeps a duplicate, which costs a line. So every reading it cannot be
    sure of reads as "not stated": a non-constant (`units=UNITS`), a `**spread`
    in an item, a `PARAMS` built by a comprehension, an item added inside an
    `if`. `tests/test_param_view.StaticProse` holds it equal to the loaded
    bracket, field for field, and a subset of a model that computes its units.

    Which class's docstrings: the one the loader would resolve as the config —
    the class `CONFIG = X(...)` (or `CONFIG = X`) names, else `Config`, which is
    `_resolve_config`'s rule. The spec says "every class in the entry file";
    that over-claims for a model with a second dataclass sharing a field name
    (a `Fixture.c` with a docstring beside an undocumented `Config.c` would
    read as "the model states c's rationale", and the migration would drop the
    record's — the loss itself). Every class is still the fallback when the
    config cannot be named from the text (`CONFIG = make_config()`); a config
    imported from another file states nothing here.
    """
    path = _static_entry(root, entry)
    if path is None:
        return {}
    try:
        with open(path, "rb") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError, ValueError):
        return {}
    stated = {name: {"rationale": text, "units": ""}
              for name, text in _static_docstrings(tree).items()}
    for name, row in _static_params(tree).items():
        target = stated.setdefault(name, {"rationale": "", "units": ""})
        # An explicit PARAMS rationale wins over the docstring, as it does in
        # `params_from_model`; units have no other home.
        for key in ("rationale", "units"):
            if row.get(key):
                target[key] = row[key]
    return {name: row for name, row in stated.items() if row["rationale"] or row["units"]}


def _static_entry(root: str, entry: str | None) -> str | None:
    """The entry's file, as `_resolve_entry` finds it — minus every fallback that
    reads the ledger or lists `model/`: the migration calls this while the ledger
    is the thing being migrated."""
    entry = entry.strip() if isinstance(entry, str) else ""
    if not entry:
        return None
    path = os.path.abspath(entry if os.path.isabs(entry) else os.path.join(root, entry))
    if os.path.isdir(path):
        path = os.path.join(path, "__init__.py")
    return path if os.path.isfile(path) else None


def _static_docstrings(tree: ast.Module) -> dict[str, str]:
    """The config class's attribute docstrings, read from the module's text."""
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    name, external = _static_config_class(tree)
    if name is not None:
        chosen = [node for node in classes if node.name == name]
        if chosen:
            out: dict[str, str] = {}
            for node in chosen:              # the order `field_docstrings` merges in
                out.update(_attribute_docstrings(node))
            return out
        external = external or name in _imported_names(tree)
    if external:
        return {}
    out = {}
    for node in classes:
        for field_name, text in _attribute_docstrings(node).items():
            out.setdefault(field_name, text)
    return out


def _static_config_class(tree: ast.Module) -> tuple[str | None, bool]:
    """`(class name, defined elsewhere)` for the config, read as `_resolve_config` reads it.

    The LAST module-level binding of `CONFIG` is the one the loader sees. No
    `CONFIG` at all means `Config`. `CONFIG = mod.Config()` names a class in
    another module: `(None, True)`. Anything else (`CONFIG = make()` of a def,
    a subscript) cannot be named from the text: `(None, False)`, or the name of a
    def, which the caller finds is no class here.
    """
    value: ast.expr | None = None
    bound = False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, rhs = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, rhs = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "CONFIG" for t in targets):
            value, bound = rhs, True
    if not bound:
        return "Config", False
    if isinstance(value, ast.Call):
        value = value.func
    if isinstance(value, ast.Name):
        return value.id, False
    if isinstance(value, ast.Attribute):
        return None, True
    return None, False


def _imported_names(tree: ast.Module) -> set[str]:
    """Names a module-level import binds (`import a.b` binds `a`)."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _static_params(tree: ast.Module) -> dict[str, dict[str, str]]:
    """`{name: {"rationale", "units"}}` from the module-level `PARAMS` literal.

    Followed in statement order the way the module would run it: an assignment
    of a list or tuple literal replaces the items, `PARAMS += [...]`,
    `PARAMS.append(item)` and `PARAMS.extend([...])` add to them, and any other
    rebinding forgets them (whatever it builds is not readable here). The first
    item naming a param is the one read; the loader refuses a second.
    """
    items: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node.value is None or not any(
                    isinstance(t, ast.Name) and t.id == "PARAMS" for t in targets):
                continue
            items = list(_literal_items(node.value) or ())
        elif (isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name)
              and node.target.id == "PARAMS"):
            more = _literal_items(node.value) if isinstance(node.op, ast.Add) else None
            items = items + list(more) if more is not None else []
        elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
              and isinstance(node.value.func, ast.Attribute)
              and isinstance(node.value.func.value, ast.Name)
              and node.value.func.value.id == "PARAMS"
              and len(node.value.args) == 1 and not node.value.keywords):
            method, arg = node.value.func.attr, node.value.args[0]
            if method == "append":
                items.append(arg)
            elif method == "extend":
                items += _literal_items(arg) or []
    out: dict[str, dict[str, str]] = {}
    for item in items:
        read = _static_item(item)
        if read is not None and read[0] not in out:
            out[read[0]] = read[1]
    return out


def _literal_items(node: ast.expr) -> list[ast.expr] | None:
    """The elements of a list or tuple literal, or None for anything else."""
    if isinstance(node, (ast.List, ast.Tuple)) and not any(
            isinstance(e, ast.Starred) for e in node.elts):
        return list(node.elts)
    return None


def _static_item(node: ast.expr) -> tuple[str, dict[str, str]] | None:
    """`(name, {"rationale", "units"})` from one dict literal or `Param(...)` call."""
    given: dict[str, ast.expr] = {}
    if isinstance(node, ast.Dict):
        if any(key is None for key in node.keys):       # a **spread may override
            return None
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                given[key.value] = value
    elif isinstance(node, ast.Call) and _called(node.func) == "Param":
        if any(isinstance(a, ast.Starred) for a in node.args) or any(
                k.arg is None for k in node.keywords):
            return None
        order = [f.name for f in dataclasses.fields(Param)]
        for position, arg in enumerate(node.args[:len(order)]):
            given[order[position]] = arg
        for keyword in node.keywords:
            given[str(keyword.arg)] = keyword.value
    else:
        return None
    name = _constant_str(given.get("name"))
    if not name:
        return None
    return name, {"rationale": _constant_str(given.get("rationale")),
                  "units": _constant_str(given.get("units"))}


def _called(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _constant_str(node: ast.expr | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


# --------------------------------------------------------------------------- #
# the parameter as a reader sees it: the model's number, both homes' losers
# --------------------------------------------------------------------------- #
#: Where a param record lives, relative to the project root (phase-1's layout).
#: Spelt here rather than asked of `store`, because it is a DISPLAY tag — the
#: origin printed after a rejection — and it names the file a human opens to edit
#: it whether or not the project has migrated yet.
_RECORD_DIR = "params"

#: The reason a view carries when the caller loaded no model and gave no reason.
#: A view without a model must never pass for one with it; an empty
#: `model_error` next to `value=None` would read as "the model says None".
_NO_MODEL = "no model was loaded"


@dataclass(frozen=True)
class ParamView:
    """One parameter as every reader shows it, assembled from its two homes.

    The model owns `value`, `units`, `rationale` and `derived_from`, and its own
    `PARAMS` losers. The param record (`params/<name>.json`) owns `source`,
    `grounded_by` and `tags`, may carry losers of its own, and carries `units`
    or `rationale` only where the model states none (spec §3.15). `rejected` is
    the union, each row `(Rejected, origin)`, origin the place it lives —
    `"model/bracket.py PARAMS"` or `"params/<name>.json"` — the model's first,
    a loser both homes state shown once, as the model's.

    `home` is where the value lives (`"model/bracket.py Config.thickness"`, or
    `"<entry> PARAMS"` for a constant only PARAMS declares); `""` means the
    model does not hold this parameter — either it did not load (`model_error`
    says why) or the record outlived its field (an orphan). `value` is `None`
    in both cases and never a copy from anywhere else: S-39 was `why` quoting
    the ledger's 7 after the model said 8.0. `record` is the record's path, or
    `""` when no record exists.

    Not persisted; `to_dict` is the JSON shape for readers that write one
    (`last_check.json`'s `params`).
    """

    name: str
    value: Any = None
    units: str = ""
    rationale: str = ""
    derived_from: tuple[str, ...] = ()
    rejected: tuple[tuple[Rejected, str], ...] = ()
    source: str = ""
    grounded_by: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    home: str = ""
    record: str = ""
    model_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "value": self.value, "units": self.units,
            "rationale": self.rationale, "derived_from": list(self.derived_from),
            "rejected": [{"value": item.value, "why": item.why,
                          "evidence": item.evidence, "origin": origin}
                         for item, origin in self.rejected],
            "source": self.source, "grounded_by": list(self.grounded_by),
            "tags": list(self.tags), "home": self.home, "record": self.record,
            "model_error": self.model_error,
        }


def param_view(ledger, model: LoadedModel | None, *,
               model_error: str = "") -> list[ParamView]:
    """Every parameter, value from the model, provenance from the record.

    `ledger` supplies the records (`ledger.params`); nothing but its names,
    `source`, `grounded_by`, `tags`, `rejected`, and — where the model states
    none — `units` and `rationale` is read from them. Their `value`,
    `derived_from`, `gates` and `changed_in` are never read: the model owns the
    first two, the last two are derived (`why` takes read sets; `decisions.
    changed_in`).

    Order: the model's (field order, then PARAMS-only constants), then records
    the model does not define, in record order — each with `value=None` and no
    `home`, which is what `orphan_params` lists.

    `model=None` (it did not load, or the project has none): one view per
    record, `value=None`, `model_error` set — to `model_error`, else
    `"no model was loaded"`. No number is shown where the model should answer.

    Replaces the mutating parameter sync (`sync_params`, deleted at checkpoint
    1.3 with its last caller), which copied the model into the ledger on every
    check and kept the record's `rejected` whole: a loser added to PARAMS after
    the param existed never reached anything a reader saw (S-38), and `why`
    quoted the copy's value after the model moved (S-39). Nothing is copied
    here, so there is nothing to fall behind.
    """
    records: dict[str, Param] = {}
    for record in list(getattr(ledger, "params", None) or ()):
        records.setdefault(record.name, record)         # first wins, as Ledger.param
    if model is None:
        error = " ".join(str(model_error or "").split()) or _NO_MODEL
        return [_record_view(record, model_error=error) for record in records.values()]

    declared = model.params or params_from_model(model)
    fields = {f.name for f in dataclasses.fields(model.config)}
    config_home = (f"{_entry_relative(model, _config_source(model))} "
                   f"{type(model.config).__name__}")
    params_home = f"{model.entry} PARAMS"
    views: list[ParamView] = []
    for param in declared:
        record = records.get(param.name)
        views.append(ParamView(
            name=param.name,
            value=param.value,
            units=param.units or (record.units if record else "") or "",
            rationale=param.rationale or (record.rationale if record else "") or "",
            derived_from=tuple(param.derived_from or ()),
            rejected=_union([(item, params_home) for item in param.rejected or ()]
                            + _record_losers(record)),
            # The record's provenance; a PARAMS entry fills only what no record says.
            source=(record.source if record else "") or param.source or "",
            grounded_by=tuple((record.grounded_by if record else None)
                              or param.grounded_by or ()),
            tags=tuple((record.tags if record else None) or param.tags or ()),
            home=f"{config_home}.{param.name}" if param.name in fields else params_home,
            record=_record_path(param.name) if record else "",
        ))
    live = {param.name for param in declared}
    views += [_record_view(record) for name, record in records.items() if name not in live]
    return views


def _record_path(name: str) -> str:
    return f"{_RECORD_DIR}/{name}.json"


def _record_losers(record: Param | None) -> list[tuple[Rejected, str]]:
    if record is None:
        return []
    origin = _record_path(record.name)
    out = []
    for item in record.rejected or ():
        if isinstance(item, dict):
            item = Rejected(value=str(item.get("value", "")), why=str(item.get("why", "")),
                            evidence=str(item.get("evidence", "")))
        if isinstance(item, Rejected):
            out.append((item, origin))
    return out


def _union(rows: list[tuple[Rejected, str]]) -> tuple[tuple[Rejected, str], ...]:
    """Rows in order, each loser once. Case-folded on (value, why), the rule
    `decisions` already uses: a record that repeats the model's loser is the same
    loser, and the first home to state it — the model — is the one shown."""
    seen: set[tuple[str, str]] = set()
    out = []
    for item, origin in rows:
        key = (" ".join(str(item.value).split()).casefold(),
               " ".join(str(item.why).split()).casefold())
        if key not in seen:
            seen.add(key)
            out.append((item, origin))
    return tuple(out)


def _record_view(record: Param, *, model_error: str = "") -> ParamView:
    """A record alone: its own provenance, no number, no home."""
    return ParamView(
        name=record.name, value=None, units=record.units or "",
        rationale=record.rationale or "", rejected=_union(_record_losers(record)),
        source=record.source or "", grounded_by=tuple(record.grounded_by or ()),
        tags=tuple(record.tags or ()), home="", record=_record_path(record.name),
        model_error=model_error)


def _entry_relative(model: LoadedModel, path: str) -> str:
    """`path` relative to the project root the model was loaded from, posix.

    The root is not on `LoadedModel`, but `entry` is `file` relative to it, so
    it is recovered by walking up one directory per entry component. The
    config class usually lives in the entry itself; a model that grew into a
    package names the module that defines it.
    """
    if not path or not os.path.isabs(path) or _norm(path) == _norm(model.file):
        return model.entry
    root = model.file
    for _part in model.entry.replace("\\", "/").split("/"):
        root = os.path.dirname(root)
    try:
        return os.path.relpath(path, root).replace(os.sep, "/")
    except ValueError:                             # another drive, on Windows
        return model.entry


def orphan_params(ledger, model: LoadedModel) -> list[str]:
    """Param records (`ledger.params`) the model no longer defines, in record order.

    Either the model dropped a parameter and the record should be retired with a
    decision entry, or the parameter was renamed and its provenance is now
    stranded — pointing at nothing while every `grounded_by` still references it.
    Both are worth a line in the report rather than a silent deletion.

    It reads the records and the model and nothing else: never a merged copy.
    The parameter sync used to keep an orphan in `ledger.params` so this could
    find it there; from 1.3 the records are the files under `params/`, and a
    param the model owns entirely has no record at all, so an orphan is exactly
    a record with no field behind it — the views `param_view` returns with no
    `home`. `model` and `doctor` both list them from here.
    """
    live = {p.name for p in (model.params or params_from_model(model))}
    return [p.name for p in ledger.params if p.name not in live]

def undocumented_params(model: LoadedModel) -> list[str]:
    """Names of parameters carrying no rationale. The report's nag list.

    The test is the rationale, not the paperwork: a `Param` that exists in
    `PARAMS` with `rationale=""` is exactly as undefended as a field nobody
    mentioned. "It was 7 when it worked" is not a rationale, but at least it is
    a sentence someone can argue with; an empty string is a number that will be
    re-litigated by every fresh reader forever, which is the cost `Rejected`
    exists to eliminate.

    Returned in model field order, so the list reads like the config file.
    """
    return [p.name for p in (model.params or params_from_model(model))
            if not (p.rationale or "").strip()]


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def _first_difference(a: Any, b: Any, path: str) -> str | None:
    """First field where two projections disagree, or None. Depth-first, sorted.

    Reporting the FIELD rather than "the projections differ" is the difference
    between a five-minute fix (`mass_g` moved in the 14th decimal: a set got
    iterated) and an afternoon of bisecting a build function.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                return f"{path}.{key}: absent in the first run, present later"
            if key not in b:
                return f"{path}.{key}: present in the first run, absent later"
            found = _first_difference(a[key], b[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} then {len(b)}"
        for index, (x, y) in enumerate(zip(a, b)):
            found = _first_difference(x, y, f"{path}[{index}]")
            if found:
                return found
        return None
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} {a!r} then {type(b).__name__} {b!r}"
    if a != b:
        return f"{path}: {a!r} then {b!r}"
    return None


def check_determinism(model: LoadedModel, runs: int = 2) -> tuple[bool, str]:
    """Build the model `runs` times and prove the projection does not move.

    Byte-identical output makes a sound acceptance gate for a
    4,250-line refactor: if every exported mesh hashes the same before and
    after, the refactor changed nothing, and no amount of reading the diff gives
    you that. The same property one level up is this function.

    It is also a precondition for everything hash-based downstream. If `build()`
    is not deterministic then `model_hash` is noise, every run looks stale, and
    "the model changed" stops meaning anything. The usual culprits, in the order
    they show up: a `set` in the output (hash-randomised between processes), a
    timestamp, a `random`, a dict keyed by object identity, and a `build()` that
    MUTATES the config it was handed — which this catches precisely because the
    same config instance is reused across runs rather than being deep-copied.

    Returns `(ok, detail)` where `detail` is one line naming the first field
    that moved, so a verdict can carry it. Never raises for a non-deterministic
    model: that is a finding, not a crash. It DOES raise `AtompipeError` if
    `build()` itself fails, because that is a different problem with a different
    fix.
    """
    if runs < 2:
        # A programming error, not a user error: one run cannot disagree with
        # itself, and silently returning True would be a gate that cannot fail.
        raise ValueError(f"check_determinism needs at least 2 runs, got {runs}")

    first = project(model)
    first_text = _canonical(first)
    digest = short_hash(first_text)

    for run in range(2, runs + 1):
        current = project(model)
        if _canonical(current) == first_text:
            continue
        where = _first_difference(first, current, "projection") or "(unlocatable)"
        return False, (
            f"build() is not deterministic: run {run} differs from run 1 at {where}"
        )

    return True, f"{runs} builds identical (model hash {digest})"
