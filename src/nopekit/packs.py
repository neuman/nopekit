# SPDX-License-Identifier: Apache-2.0
"""nopekit.packs — the plugin layer, and the discipline that keeps it cheap.

A pack teaches the spine one physical domain: gates that settle its claims,
generators, an adversarial lens list, sourcing reality. Packs are ordinary
directories with no build step and no registry service — drop one in and it is
found.

The whole module exists to protect one number: **how much context knowing about
a pack costs.** Three-tier progressive disclosure is the design:

    tier 1   pack.json        ~20 words     always affordable
    tier 2   PACK.md          ~150 lines    loaded when the domain is relevant
    tier 3   references/*.md  whatever      loaded for the specific task only

An agent must be able to know about forty packs while loading two. That is why
``discover`` reads ``pack.json`` and *nothing else*: no PACK.md, no imports, no
``gates/`` walk. The moment discovery imports Python, `nopekit packs list`
costs a second per pack and starts dragging third-party dependencies into a
tier-0 loop that promised to run in seconds — and a pack with a broken import
would take the whole listing down with it.

The second thing this module protects is blame. A pack is third-party code
running inside the spine's process. When it fails to import, the traceback is a
stranger's; what the user needs is a sentence naming the pack and the file. Every
import here is wrapped for exactly that reason: a broken pack must never look
like a spine bug.

Dependencies: models, util, store. ``gates`` is imported *lazily*, inside the
functions that need a Registry or run a gate, so that importing this module stays
free and so that a pack's gate file (which imports ``nopekit.gates``) can never
create a cycle at spine import time.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import sys
import tempfile
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from .models import GateSpec, KeyCollision, Ledger, Need, PackManifest, Tier
from .store import NOPEKIT_DIR, PACKS_NAME, find_root
from .util import NopekitError, read_json

if TYPE_CHECKING:                          # annotations only; see the module docstring
    from .gates import GateContext


__all__ = [
    "MANIFEST_NAME",
    "DOC_NAME",
    "GATES_DIR",
    "GENERATORS_DIR",
    "REFERENCES_DIR",
    "SELFTEST_DIR",
    "BASELINE_NAME",
    "origin_of",
    "LENSES_NAME",
    "SOURCING_NAME",
    "PACK_PATH_ENV",
    "BUNDLED_PACKS",
    "search_paths",
    "discover",
    "discover_dirs",
    "find",
    "read_manifest",
    "load_gates",
    "load_all_gates",
    "pack_doc",
    "reference_doc",
    "references",
    "validate",
    "Demonstration",
    "baseline_context",
    "demonstrate",
    "SealFinding",
    "seal_findings",
    "match",
    "score",
    "installed",
    "available",
    "key_scope",
    "key_vocabulary",
    "key_collisions",
]


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #
MANIFEST_NAME = "pack.json"          # tier 1
DOC_NAME = "PACK.md"                 # tier 2
REFERENCES_DIR = "references"        # tier 3
GATES_DIR = "gates"
GENERATORS_DIR = "generators"
SELFTEST_DIR = "selftest"
BASELINE_NAME = "baseline.json"
"""A plausible-good projection every gate in the pack passes, so its negative
controls can actually be exercised. See docs/PACK_FORMAT.md."""
LENSES_NAME = "lenses.md"
SOURCING_NAME = "sourcing.md"

#: Inside ``selftest/baseline.json``: one line per key saying what it is and its
#: unit. Required by the pack format, and the source of the MEANINGS two packs
#: are compared on when their key vocabularies overlap.
BASELINE_NOTES = "_notes"

#: Inside ``selftest/baseline.json``: ``{primary key: [other accepted spellings]}``.
#: The baseline itself carries only the spelling the pack teaches; a gate usually
#: accepts more (``part_bbox_mm`` also answers to ``bbox_mm``, ``bbox``,
#: ``footprint_mm``). Those fallbacks are where two packs collide without either
#: baseline showing it, so they are declared here rather than living only in the
#: gate source, where nothing can diff them.
BASELINE_ALIASES = "_aliases"

#: ``os.pathsep``-separated directories searched BEFORE everything else.
#: This is how CI tests a pack that is not installed anywhere, and how a
#: developer points the spine at a checkout without copying files around.
PACK_PATH_ENV = "NOPEKIT_PACK_PATH"

#: The packs that ship with the spine: ``<repo>/packs`` — two levels up from
#: ``src/nopekit/packs.py``. Computed from ``__file__`` at import time (a
#: string join, no I/O). In a wheel install this directory simply does not
#: exist, and ``search_paths`` drops it; the spine still works, it just has no
#: bundled domains. That is a deliberate degradation, not a failure: the spine
#: must never need a pack to start.
def _bundled_packs() -> str:
    """Where the packs that ship with the spine actually live.

    Two layouts, both real, checked in this order:

    * **a checkout** — ``<repo>/packs``, two levels up from ``src/nopekit/``.
      This is the contributor-facing home: ordinary directories, no build step.
    * **an installed wheel** — ``nopekit/bundled/``, where ``pyproject.toml``
      maps the same directory. Before that mapping existed, ``pip install
      nopekit`` produced a working spine and zero gates, which is a spine that
      cannot do anything; the checkout path happened to be the only one anybody
      tested.

    Returns the first that exists, else the checkout path (so an error message
    names somewhere a human recognises). A string join and at most two stats — no
    imports, no I/O beyond ``isdir``.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    checkout = os.path.join(os.path.dirname(os.path.dirname(here)), "packs")
    installed = os.path.join(here, "bundled")
    for candidate in (checkout, installed):
        if os.path.isdir(candidate):
            return candidate
    return checkout


BUNDLED_PACKS = _bundled_packs()

#: Accepted on the way IN to a lookup. Deliberately permissive about case and
#: dots (so ``find`` can be used on names the user typed), but it exists to keep
#: a path separator or a ``..`` out of a directory join: `nopekit packs show
#: ../../etc` must be an error message, not a file read. `validate` applies the
#: much stricter naming rule below to packs that want to be published.
_LOOKUP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: The rule for a pack that intends to be shared: lowercase, hyphenated.
#: ``cfd-openfoam``, ``fdm-print``, ``pcb-kicad``. Mixed case breaks on
#: case-insensitive filesystems in exactly one direction, which is the worst
#: kind of breakage — it works for the author forever.
_PUBLISH_NAME_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")

#: Minimum substantive (non-blank) lines for PACK.md to count as tier 2 at all.
#: PACK_FORMAT asks for ~150; this is the floor below which the file is a stub
#: pretending to be documentation.
MIN_DOC_LINES = 20

#: Description budget. One line, and short enough that forty of them fit in a
#: context window together. If it needs three sentences the pack is doing too
#: much and should be split — that judgement is in PACK_FORMAT.md, this is just
#: the number that enforces it.
MAX_DESCRIPTION_CHARS = 200


# --------------------------------------------------------------------------- #
# search paths
# --------------------------------------------------------------------------- #
def search_paths(root: str | None = None, *, existing_only: bool = True,
                 include_env: bool = True, include_user: bool = True) -> list[str]:
    """Directories searched for packs, **in precedence order — first wins**.

        1. ``$NOPEKIT_PACK_PATH`` entries (os.pathsep-separated)
        2. ``<root>/.nopekit/packs``   — this project's own packs
        3. ``~/.nopekit/packs``        — the user's packs
        4. the bundled ``packs/`` directory shipped with the spine

    ``include_env=False`` drops entry 1 and ``include_user=False`` drops entry 3.
    Both exist for the callers that must not be answered by the machine they
    happen to run on: those two entries belong to the machine, not to any
    checkout or project, and both outrank the bundled packs. What slipped
    through without them: a same-named pack in ``~/.nopekit/packs`` would have
    been the copy every in-process pack test and a repo-root selftest exercised,
    while each report named the bundled one (S-87; latent, never observed).
    Rejected: a single ``bundled_only`` flag — a project's own
    ``.nopekit/packs`` is part of the project, and pack mode inside a project
    must still see it.

    Precedence is the point. A project that has grown its own ``cfd-openfoam``
    pack through the extension protocol must shadow the bundled one, or the
    project's own solver settings — the ones it validated — silently stop being
    the ones that run. Shadowing happens at the *directory name* level, which is
    also the level ``find`` resolves at, so discovery and loading can never
    disagree about which copy is live.

    ``root=None`` means "find the project from the cwd" (``store.find_root``);
    when there is no project, the project-local entry is simply absent.

    Paths are absolute, de-duplicated (a path listed twice does not get searched
    twice) and, by default, filtered to those that exist — callers listing packs
    want somewhere to look, not a map. ``existing_only=False`` returns the full
    map, which is what `nopekit doctor` should print when a user asks why their
    pack is not being found.
    """
    candidates: list[str] = []

    env = os.environ.get(PACK_PATH_ENV, "") if include_env else ""
    for entry in env.split(os.pathsep):
        entry = entry.strip()
        if entry:
            candidates.append(os.path.abspath(os.path.expanduser(entry)))

    project = root if root is not None else find_root()
    if project:
        candidates.append(os.path.abspath(os.path.join(project, NOPEKIT_DIR, PACKS_NAME)))

    home = os.path.expanduser("~") if include_user else ""
    if home and home != "~":
        candidates.append(os.path.abspath(os.path.join(home, NOPEKIT_DIR, PACKS_NAME)))

    candidates.append(os.path.abspath(BUNDLED_PACKS))

    out: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        if existing_only and not os.path.isdir(path):
            continue
        out.append(path)
    return out


# --------------------------------------------------------------------------- #
# tier 1: discovery
# --------------------------------------------------------------------------- #

def origin_of(pack_dir: str, root: str | None = None) -> str:
    """Which search root a pack was resolved from: project, user, env or bundled.

    Surfaced everywhere a pack is listed, because a pack that is not the one you
    are editing looks exactly like a pack that is. A tester pulled a fix, watched
    the gate fail to appear, and lost ten minutes before discovering the live copy
    was the one inside the installed wheel — "packs list and doctor both showed me
    a stale pack with a straight face". The path was always available internally
    and never printed.
    """
    pack_dir = os.path.abspath(pack_dir)
    project = root if root is not None else find_root()
    if project:
        local = os.path.abspath(os.path.join(project, NOPEKIT_DIR, PACKS_NAME))
        if pack_dir.startswith(local + os.sep):
            return "project"
    home = os.path.expanduser("~")
    if home and home != "~":
        user = os.path.abspath(os.path.join(home, NOPEKIT_DIR, PACKS_NAME))
        if pack_dir.startswith(user + os.sep):
            return "user"
    if pack_dir.startswith(os.path.abspath(BUNDLED_PACKS) + os.sep):
        return "bundled"
    return "path"

def read_manifest(pack_dir: str) -> PackManifest:
    """Parse one pack's ``pack.json``. Tier 1, and the only file read here.

    Raises ``NopekitError`` for every way a *pack author* can get this wrong —
    missing file, malformed JSON (with the line and column, because "Expecting
    ',' delimiter" alone sends people hunting), a top-level array instead of an
    object, a bad ``max_tier``. These are user-fixable problems in someone
    else's directory; they must not surface as a spine traceback.

    Unknown keys are dropped by ``PackManifest.from_dict`` so a newer pack
    cannot break an older spine. That forgiveness is why ``validate`` reports
    unknown keys explicitly: a typo'd ``"settle"`` is silently ignored here and
    would otherwise cost a pack every gap match it should have won, with no
    symptom anywhere.
    """
    path = os.path.join(pack_dir, MANIFEST_NAME)
    if not os.path.isfile(path):
        raise NopekitError(f"{pack_dir} is not a pack: no {MANIFEST_NAME}")

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise NopekitError(
            f"{path} is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}"
        ) from exc
    except OSError as exc:
        raise NopekitError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise NopekitError(f"cannot read {path}: not UTF-8 text ({exc.reason})") from exc

    if not isinstance(data, dict):
        raise NopekitError(f"{path} must contain a JSON object, not {type(data).__name__}")

    try:
        manifest = PackManifest.from_dict(data)
    except (ValueError, TypeError) as exc:
        # Almost always max_tier: a string, or an int outside 0..3.
        raise NopekitError(f"{path} has a bad field value: {exc}") from exc

    if not (manifest.name or "").strip():
        raise NopekitError(f"{path} has no `name` — a pack without a name cannot be referenced")
    return manifest


def discover_dirs(root: str | None = None) -> list[tuple[str, PackManifest]]:
    """``(pack_dir, manifest)`` for every discoverable pack, in precedence order.

    ``discover`` throws the directory away; this keeps it, because the CLI wants
    to print *where* a pack came from ("fdm-print (bundled)" vs "fdm-print
    (project)") and re-walking the search paths to find that out is both slower
    and capable of disagreeing with the list it is annotating.

    Reads ``pack.json`` only — one small file per candidate directory, no Python
    imported, no PACK.md touched. That is the promise `nopekit packs list`
    makes and it is the reason forty packs stay affordable.

    A directory whose manifest will not parse is **skipped, not raised**: one
    broken pack in ``~/.nopekit/packs`` must not take down the listing of the
    other thirty-nine. It still claims its name for shadowing purposes, so a
    broken local pack does not silently hand the name to the bundled copy and
    run code the user did not mean to run. ``nopekit packs validate`` is where
    the broken one gets a full explanation.
    """
    found: list[tuple[str, PackManifest]] = []
    claimed: set[str] = set()          # directory names already resolved
    names: set[str] = set()            # declared manifest names already shown

    for base in search_paths(root):
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            # Searched-but-unreadable is not the user's problem to solve mid-list.
            continue
        for entry in entries:
            if entry.startswith(".") or entry.startswith("_"):
                continue
            key = os.path.normcase(entry)
            if key in claimed:
                continue
            pack_dir = os.path.join(base, entry)
            if not os.path.isfile(os.path.join(pack_dir, MANIFEST_NAME)):
                continue
            claimed.add(key)           # claimed even if it turns out to be broken
            try:
                manifest = read_manifest(pack_dir)
            except NopekitError:
                continue
            declared = os.path.normcase(manifest.name.strip())
            if declared in names:
                continue
            names.add(declared)
            found.append((pack_dir, manifest))
    return found


def discover(root: str | None = None) -> list[PackManifest]:
    """Every discoverable pack's tier-1 manifest, best-precedence first.

    This is the whole of what an agent needs to know that a pack *exists*: name,
    one-line description, the quantity vocabulary it settles, its cost tier. A
    few hundred bytes each. Loading the domain itself (tier 2) or its gates
    (Python) is a separate, explicit act.
    """
    return [manifest for _dir, manifest in discover_dirs(root)]


def find(name: str, root: str | None = None, *, include_env: bool = True,
         include_user: bool = True) -> str | None:
    """Absolute directory of pack ``name``, or None. First search path wins.

    A directory only counts as a pack if it contains ``pack.json`` — otherwise a
    stray ``packs/notes`` folder shadows a real pack by name and the failure
    shows up much later, as a missing gate.

    Raises ``NopekitError`` on a name containing a path separator or ``..``.
    This is the one place a user-supplied string is joined onto a filesystem
    path, so it is the one place that has to refuse ``../../etc``.

    ``include_env`` / ``include_user`` are passed to :func:`search_paths`.
    """
    clean = (name or "").strip()
    if not clean or not _LOOKUP_NAME_RE.match(clean) or ".." in clean:
        raise NopekitError(
            f"invalid pack name {name!r}: names are like `fdm-print` — "
            f"letters, digits, dots, dashes and underscores, no path separators"
        )
    for base in search_paths(root, include_env=include_env, include_user=include_user):
        candidate = os.path.join(base, clean)
        if os.path.isfile(os.path.join(candidate, MANIFEST_NAME)):
            return os.path.abspath(candidate)
    return None


def _require_dir(name: str, root: str | None = None, *, include_env: bool = True,
                 include_user: bool = True) -> str:
    """``find`` or an error that says where we looked.

    "pack not found" with no list of searched directories is the single most
    annoying error a plugin system can produce, because the fix is always "put it
    somewhere else" and the user cannot see where.
    """
    pack_dir = find(name, root, include_env=include_env, include_user=include_user)
    if pack_dir:
        return pack_dir
    looked = search_paths(root, existing_only=False, include_env=include_env,
                          include_user=include_user)
    where = "\n  ".join(looked) if looked else "(no search paths)"
    raise NopekitError(
        f"no pack named {name!r} — searched:\n  {where}\n"
        f"(set {PACK_PATH_ENV} to add a directory)"
    )


# --------------------------------------------------------------------------- #
# tier 2 / tier 3: documentation
# --------------------------------------------------------------------------- #
def _inside(parent: str, path: str) -> bool:
    """Is ``path`` inside ``parent``? False for anything that escapes, or cannot be compared.

    ``os.path.commonpath`` raises ValueError across drives, so a bad answer is a
    refusal rather than an exception: both callers are checking a string that
    arrived from a manifest or from a model's output, and "I could not prove
    this path is inside the pack" must never resolve to "read it".
    """
    try:
        return os.path.commonpath([parent, path]) == parent
    except ValueError:
        return False


def _read_text(path: str, what: str) -> str:
    """Read a UTF-8 text file, turning every failure into a user-facing error.

    Packs are other people's directories: an unreadable PACK.md is a permissions
    or encoding problem somebody can fix, never a spine bug.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise NopekitError(f"cannot read {what} at {path}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise NopekitError(f"cannot read {what} at {path}: not UTF-8 text ({exc.reason})") from exc


def pack_doc(name: str, root: str | None = None) -> str:
    """The pack's ``PACK.md`` — tier 2, loaded when the domain becomes relevant.

    ~150 lines: what the pack settles, what it explicitly *cannot* settle, its
    gates and their thresholds, units and frames, the physics in a paragraph.
    This is the document that lets an agent tell an absurd result from a
    plausible one, which is the difference between a gate and a number.

    Deliberately a separate call from ``discover``: reading this for every
    installed pack would cost more context than the entire rest of the project
    state, and forty domains' worth of prose is exactly what tiering exists to
    prevent.
    """
    pack_dir = _require_dir(name, root)
    path = os.path.join(pack_dir, DOC_NAME)
    if not os.path.isfile(path):
        raise NopekitError(f"pack {name!r} has no {DOC_NAME} (expected at {path})")
    return _read_text(path, f"pack {name!r} {DOC_NAME}")


def references(name: str, root: str | None = None) -> list[str]:
    """Reference names available for tier-3 loading, without loading any of them.

    The index is the cheap part: an agent reads the names, decides which single
    document the task at hand needs, and pays for that one. Returned without the
    ``.md`` suffix, sorted, so the value round-trips straight into
    ``reference_doc``.
    """
    pack_dir = _require_dir(name, root)
    ref_dir = os.path.join(pack_dir, REFERENCES_DIR)
    try:
        entries = os.listdir(ref_dir)
    except OSError:
        return []
    return sorted(e[:-3] for e in entries if e.endswith(".md") and not e.startswith("."))


def reference_doc(name: str, ref: str, root: str | None = None) -> str:
    """One tier-3 reference: ``<pack>/references/<ref>.md``.

    Tier 3 is where depth goes — a CFD pack's ``meshing.md`` may be nine hundred
    lines and cost nothing until somebody is actually meshing. Loaded one
    document at a time, on purpose.

    ``ref`` may be given with or without the ``.md`` suffix and may name a
    subdirectory, but the resolved path is checked to be *inside* the references
    directory: ``ref`` reaches this function from a model's output as often as
    from a human, and "the agent asked for a reference" must never be able to
    read ``../../.ssh/id_rsa``.

    A missing reference lists what does exist — an agent that guessed the name
    can then pick the right one without a second round trip.
    """
    pack_dir = _require_dir(name, root)
    wanted = (ref or "").strip()
    if not wanted:
        raise NopekitError(f"pack {name!r}: no reference name given")
    if wanted.endswith(".md"):
        wanted = wanted[:-3]

    ref_dir = os.path.abspath(os.path.join(pack_dir, REFERENCES_DIR))
    path = os.path.abspath(os.path.join(ref_dir, wanted + ".md"))
    if not _inside(ref_dir, path):
        raise NopekitError(
            f"pack {name!r}: reference {ref!r} resolves outside {REFERENCES_DIR}/ — refusing to read it"
        )
    if not os.path.isfile(path):
        have = references(name, root)
        listing = ", ".join(have) if have else "(none)"
        raise NopekitError(
            f"pack {name!r} has no reference {wanted!r}; available: {listing}"
        )
    return _read_text(path, f"pack {name!r} reference {wanted!r}")


# --------------------------------------------------------------------------- #
# gate loading
# --------------------------------------------------------------------------- #
def _module_name(pack: str, stem: str) -> str:
    """A unique, import-safe module name for one pack gate file.

    Two packs are allowed to both ship ``gates/geometry.py``. If both import as
    ``geometry``, the second one silently gets the first one's module object out
    of ``sys.modules`` and registers nothing — a gate that quietly does not exist
    is worse than one that crashes. Namespacing by pack makes that collision
    impossible.

    Underscores, not dots: a dotted name implies a parent package that does not
    exist, and some machinery (pickle, dataclasses' module lookup) goes looking
    for it.
    """
    safe_pack = re.sub(r"[^0-9A-Za-z]+", "_", pack).strip("_") or "pack"
    safe_stem = re.sub(r"[^0-9A-Za-z]+", "_", stem).strip("_") or "mod"
    return f"nopekit_pack_{safe_pack}__{safe_stem}"


def _gate_files(gates_dir: str) -> list[str]:
    """Importable gate modules in ``gates/``, sorted for a deterministic order.

    Leading-underscore files are shared helpers, not gate modules, and importing
    them twice (once directly, once as a helper) is how a registry ends up with
    duplicate registrations.

    A missing ``gates/`` is an empty list — a docs-and-sourcing pack is a real
    pack. Anything ELSE that stops the listing (no read permission, a file where
    the directory should be) raises, because ``load_gates`` reads an empty list
    as "this pack ships no gates" and returns success: the pack would load
    clean, register nothing, and every claim it covers would report UNCLAIMED
    with no error anywhere to explain why. "Cannot read the gates" and "there
    are no gates" must not produce the same answer.
    """
    try:
        entries = sorted(os.listdir(gates_dir))
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise NopekitError(
            f"cannot read {gates_dir}: {exc.strerror or exc} — refusing to treat an "
            f"unreadable gates directory as a pack that ships no gates"
        ) from exc
    return [
        os.path.join(gates_dir, e)
        for e in entries
        if e.endswith(".py") and not e.startswith("_") and os.path.isfile(os.path.join(gates_dir, e))
    ]


def _default_registry() -> Any:
    """``gates.REGISTRY`` — the pool ``@gate`` deposits into when nobody said otherwise.

    Needed because of how ``@gate`` binds its target: the decorator's signature
    is ``registry=REGISTRY``, a DEFAULT ARGUMENT evaluated when ``gates.py`` is
    defined. A pack's gate file — every published example included — decorates
    without naming a registry, so its gates land in the module-level default no
    matter which registry ``load_gates`` was handed. Discovered the hard way:
    the first smoke run of this module loaded a pack successfully and returned
    zero specs, with the gates sitting in a registry nobody had asked for.
    Monkeypatching the module attribute cannot fix it (the default is already
    bound), so ``load_gates`` reads both pools and migrates.

    Nothing is caught here, deliberately. This used to be wrapped in a bare
    ``except Exception: return None``, and that turned a spine bug into a SILENT
    EMPTY SWEEP: with no second pool to migrate from, ``load_gates`` returned an
    empty list, reported no error, and the readiness report rendered off an empty
    registry — every claim UNCLAIMED, no gate run, exit status fine. Packs are
    third-party code and their failures are caught everywhere in this module; the
    spine's own gate module is not third-party code, and a spine that cannot
    import it must say so loudly rather than proceed with nothing loaded.
    """
    from . import gates as _gates          # an ImportError here is ours; let it fly
    return _gates.REGISTRY


def _owned_by(spec: GateSpec, pack: str, module_names: set[str]) -> bool:
    """Did this spec come out of pack ``pack``'s gate files?

    Two independent signals, because either one can be absent: ``entry`` carries
    the importing module name (which ``_module_name`` made pack-unique), and
    ``pack`` carries the author's own declaration. Used for the re-load case,
    where a diff finds nothing because Python correctly declined to execute an
    already-imported module a second time.
    """
    entry_module = (spec.entry or "").split(":", 1)[0]
    return entry_module in module_names or (bool(spec.pack) and spec.pack == pack)


def load_gates(name: str, registry: Any, root: str | None = None, *,
               include_env: bool = True, include_user: bool = True) -> list[GateSpec]:
    """Import pack ``name``'s ``gates/*.py`` into ``registry``; return what it added.

    This is the tier boundary being crossed on purpose: discovery is free,
    loading gates runs a third party's Python inside our process. So:

    * The pack directory goes on ``sys.path[0]`` so a gate can ``import
      selftest.holed_mesh`` or share a ``_geom.py`` helper, and comes off again
      in a ``finally``. A leaked ``sys.path`` entry means the *next* pack's
      imports resolve against this pack's directory, which produces a wrong
      answer rather than an error.
    * Each file imports under a pack-namespaced module name (see
      ``_module_name``), so two packs may both ship ``gates/geometry.py``.
    * Each file loads through ``modelio.load_source_module``: compiled from the
      bytes on disk (never a ``.pyc``), its code closure recorded, and a module
      already in ``sys.modules`` REUSED only while every file it ran still hashes
      the same — otherwise it runs again. A reused module re-adopts the gates it
      registered into ``registry``, which is what lets one pack load into two
      registries without "gate already registered", and a fresh registry come
      back full. What slipped through when reuse was unconditional: an in-process
      edit to a gate module or its helper ran the old code under the new bytes
      (S-26).
    * ``PACK = "<name>"`` and ``PACK_DIR`` are set on the module object *before*
      execution, so a gate module can read them at import time (the contract
      documents those globals).
    * ``registry.pack_dirs[name]`` records the directory the gates came from.
    * Any exception becomes an ``NopekitError`` naming the pack and the file.
      A pack is somebody else's code; a stranger's ImportError with a spine
      traceback reads as a spine bug and gets reported as one.

    Returns the specs this pack contributes, stamped with ``pack=name`` where
    the author left the field blank, and guaranteed to be present in
    ``registry`` — including the ones ``@gate`` deposited in the module-level
    default instead (see ``_default_registry``). The stamp comes from diffing
    the registries around the import rather than from trusting the decorator,
    because a spec that lies about its pack makes a failing verdict
    unattributable, and an unattributable verdict is one nobody owns and nobody
    fixes.

    ``include_env`` / ``include_user`` are passed to :func:`search_paths`.
    """
    pack_dir = _require_dir(name, root, include_env=include_env, include_user=include_user)
    return _load_dir(name, pack_dir, registry)


def _load_dir(name: str, pack_dir: str, registry: Any) -> list[GateSpec]:
    """:func:`load_gates` after the name has been resolved to ``pack_dir``.

    Split out so :func:`demonstrate` can load the exact directory it was handed
    through the same code the real run uses: resolving by name there would let
    ``$NOPEKIT_PACK_PATH`` or ``~/.nopekit/packs`` answer for a copy nobody
    asked about (S-87).
    """
    gates_dir = os.path.join(pack_dir, GATES_DIR)
    files = _gate_files(gates_dir)
    if not files:
        # A docs/sourcing/lenses-only pack is legitimate. `validate` decides
        # whether a pack that *declares* gates and ships none is a problem;
        # loading is not the place to have that opinion.
        return []

    from . import gates as _gates          # local import: see _fresh_registry
    from . import modelio as _modelio      # local import: packs stays free to import

    # Where this pack's gates came from, on the registry that now holds them: a
    # verdict entry spells a path under the pack as `<pack:NAME>/...` so it reads
    # the same in every checkout, and only the load knows the directory.
    pack_dirs = getattr(registry, "pack_dirs", None)
    if isinstance(pack_dirs, dict):
        pack_dirs[name] = pack_dir

    default = _default_registry()
    pools: list[Any] = [registry]
    if default is not registry:
        pools.append(default)
    before = [(pool, {spec.id for spec in pool.specs()}) for pool in pools]

    module_names: set[str] = set()
    sys.path.insert(0, pack_dir)
    try:
        for path in files:
            stem = os.path.splitext(os.path.basename(path))[0]
            mod_name = _module_name(name, stem)
            module_names.add(mod_name)
            rel_path = os.path.join(GATES_DIR, os.path.basename(path))
            existing = sys.modules.get(mod_name)
            if existing is not None:
                # Module names are keyed on the pack NAME, so a second directory
                # carrying the same name would silently reuse — or now, replace —
                # the first one's code while every message said otherwise. That is
                # the shadowing case, and it gets an error rather than a quietly
                # wrong answer: only one copy can win at check time, so the user has
                # to be told there are two.
                seen_dir = getattr(existing, "PACK_DIR", None)
                if seen_dir and os.path.normcase(seen_dir) != os.path.normcase(pack_dir):
                    raise NopekitError(
                        f"pack {name!r} was already loaded in this process from {seen_dir}; "
                        f"refusing to load a second copy from {pack_dir} under the same name "
                        f"(one of them is shadowed — remove it or rename it)"
                    )
            # Fresh bytes, a recorded closure, and a content-keyed cache
            # (`modelio.load_source_module`). What slipped through with the stock
            # loader: a module already in `sys.modules` was reused however its file
            # had changed, and a stale `__pycache__` that validated by mtime and
            # size ran the old bytecode after a same-size edit inside one second —
            # the source said 8.0, the verdict came from 7.0 (S-26). A module still
            # current is served from the cache and re-adopts the gates it
            # registered into THIS registry; one that moved runs again. `PACK` and
            # `PACK_DIR` are set before it runs, readable at import time, and are
            # part of the cache key. `use_registry` makes `registry` the target of
            # the module's `@gate`s, which is what lets the load record them.
            try:
                with _gates.use_registry(registry):
                    _modelio.load_source_module(
                        path, name=mod_name, roots=[pack_dir], registry=registry,
                        attrs={"PACK": name, "PACK_DIR": pack_dir})
            except NopekitError as exc:
                # The registry rejecting a gate (no negative control) lands here
                # already phrased for a human; keep the phrasing, add the location.
                raise NopekitError(f"pack {name!r}: {rel_path}: {exc}") from exc
            except KeyboardInterrupt:
                # Ctrl-C is the user talking, not the pack failing. Dressing it up
                # as "pack X failed to import" blames a stranger's file for a
                # decision the user just made, and makes a slow pack load
                # un-interruptable in practice because the message reads as a bug
                # to go and fix. The loader already dropped the half-run module.
                raise
            except BaseException as exc:
                # BaseException on purpose: a gate module calling sys.exit() at
                # import time (a dependency's "tool missing" guard) must not be
                # able to take `nopekit check` down with its own exit code —
                # a pack that exits the process is a broken pack, and it is named
                # as one.
                raise NopekitError(
                    f"pack {name!r}: {rel_path} failed to import: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
    finally:
        try:
            sys.path.remove(pack_dir)     # removes OUR insertion (the first match)
        except ValueError:                # pragma: no cover - a gate mangled sys.path
            pass

    added: list[GateSpec] = []
    claimed: set[str] = set()
    for pool, pool_before in before:
        for spec_obj in pool.specs():
            if spec_obj.id in claimed:
                continue
            if spec_obj.id in pool_before and not _owned_by(spec_obj, name, module_names):
                continue
            claimed.add(spec_obj.id)
            # A blank pack is stamped THROUGH the registry. This used to assign
            # to the spec `get()` returned, which reached the stored record only
            # because `get()` handed the stored record out — the same door that
            # let `specs()[0].claims.append(...)` widen what a verdict settles.
            # Every exit now returns a copy, so an assignment here would stamp a
            # copy and throw the pack name away; `set_pack` is the one edit a
            # registry accepts, and no spec object is ever written to.
            setter = getattr(pool, "set_pack", None)
            if not (spec_obj.pack or "").strip() and callable(setter):
                setter(spec_obj.id, name)
            getter = getattr(pool, "get", None)
            entry = getter(spec_obj.id) if callable(getter) else None
            live = entry[0] if entry else spec_obj
            if not (live.pack or "").strip():
                # A pool that cannot be stamped: the caller still gets the pack
                # name on what this function returns, on a new object.
                live = dataclasses.replace(live, pack=name)
            if pool is not registry:
                fn = entry[1] if entry else None
                if fn is not None and not _current(fn):
                    # A function an earlier run of an edited module left in the
                    # default pool. The module ran again and this is not its gate
                    # any more; adopting it would sweep the old code.
                    claimed.discard(spec_obj.id)
                    continue
                _adopt(registry, live, fn, name)
            added.append(live)
    return added


def _current(fn: Any) -> bool:
    """Is ``fn`` still what its module says it is?

    The module ``fn`` came from, as ``sys.modules`` holds it now, still binds
    ``fn`` under its own name. False for a function left behind by an earlier
    run of a module that has since run again: same name, different object.
    """
    module = sys.modules.get(getattr(fn, "__module__", "") or "")
    name = getattr(fn, "__name__", "")
    return module is not None and bool(name) and vars(module).get(name) is fn


def _adopt(registry: Any, spec: GateSpec, fn: Any, pack: str) -> None:
    """Put a spec that landed in the default registry into the caller's registry.

    ``load_gates(name, registry)`` promises that ``registry`` holds the pack's
    gates afterwards. Without this the promise is quietly broken for every pack
    written the documented way, and the caller — `nopekit check`, or
    ``validate`` with its throwaway registry — sees an empty sweep and reports
    every claim UNCLAIMED. A readiness report that says "no gate covers this"
    because of a registry plumbing detail is precisely the kind of confident lie
    the ledger exists to prevent.
    """
    if fn is None:
        return
    getter = getattr(registry, "get", None)
    if callable(getter) and getter(spec.id):
        return                              # already there; registering again would raise
    try:
        registry.register(spec, fn)
    except NopekitError as exc:
        raise NopekitError(f"pack {pack!r}: cannot register gate {spec.id!r}: {exc}") from exc


def load_all_gates(names: Iterable[str], registry: Any, root: str | None = None) -> list[GateSpec]:
    """Load several packs in order; return every spec they added, de-duplicated.

    Order is the caller's (normally ``ledger.meta.packs``) and is preserved,
    because when two packs register the same gate id the first one wins and the
    user needs to be able to predict which that is.

    One broken pack fails the whole call. That is deliberate: a `check` run that
    quietly proceeded with two of three packs loaded would produce a readiness
    report whose UNCLAIMED section is an artefact of an import error rather than
    a statement about the design — which is precisely the kind of lie the
    readiness ledger exists to prevent.
    """
    out: list[GateSpec] = []
    seen: set[str] = set()
    for name in names or ():
        for spec in load_gates(name, registry, root):
            if spec.id in seen:
                continue
            seen.add(spec.id)
            out.append(spec)
    return out


# --------------------------------------------------------------------------- #
# validation:  what `nopekit pack validate` and CI run
# --------------------------------------------------------------------------- #
def _fresh_registry() -> Any:
    """A private ``Registry`` so validation cannot pollute the running one.

    Imported here rather than at module scope: ``packs`` depends on models/util/
    store only, and a gate module imports ``nopekit.gates``, so keeping the
    edge lazy keeps the import graph acyclic and keeps `nopekit packs list`
    from paying for gate machinery it never touches.
    """
    from . import gates as _gates          # local import: see docstring
    return _gates.Registry()


def _is_file_fixture(ref: str) -> bool:
    """True for ``selftest/holed_mesh.py``, false for ``pack.mod:make_brick``.

    The two fixture forms are distinguished by the colon, which is what
    ``gates.load_fixture`` keys on too. Anything without one is a path we can
    check for existence right now — and a negative control pointing at a file
    that does not exist is a gate whose ability to fail has never been
    demonstrated, dressed up to look like one that has.
    """
    return bool(ref) and ":" not in ref


def _declared_str_list(value: Any) -> list[str]:
    """Manifest list field as strings. Hand-edited JSON puts ints in string lists."""
    return [str(v) for v in value or []]


def _alias_problems(baseline: dict[str, Any]) -> list[str]:
    """Everything wrong with a baseline's ``_aliases`` map.

    The map is what lets ``nopekit doctor`` diff two packs' key vocabularies —
    a gate that accepts ``bbox_mm`` as a fallback for ``part_bbox_mm`` collides on
    the FALLBACK, and the baseline's own keys would never show it. An alias map
    that is malformed is therefore not cosmetic: it makes a collision invisible
    while looking like it was declared.
    """
    where = f"{SELFTEST_DIR}/{BASELINE_NAME}"
    aliases = baseline.get(BASELINE_ALIASES)
    if aliases is None:
        return []
    if not isinstance(aliases, dict):
        return [f"{where}: {BASELINE_ALIASES} must be an object of "
                f"{{primary key: [other accepted spellings]}}, not a "
                f"{type(aliases).__name__}"]

    stated = {key for key in baseline if not key.startswith("_")}
    problems: list[str] = []
    seen: dict[str, str] = {}
    for primary, spellings in aliases.items():
        if primary not in stated:
            problems.append(
                f"{where}: {BASELINE_ALIASES} names {primary!r}, which the baseline "
                f"itself does not state — declare the key (so every gate is proven "
                f"to read it) or drop the alias")
        if isinstance(spellings, (str, bytes)) or not isinstance(spellings, Iterable):
            problems.append(
                f"{where}: {BASELINE_ALIASES}[{primary!r}] must be a LIST of other "
                f"spellings, not a {type(spellings).__name__}")
            continue
        for spelling in spellings:
            alias = str(spelling)
            if alias in stated:
                problems.append(
                    f"{where}: {alias!r} is both a baseline key and an alias of "
                    f"{primary!r} — one spelling cannot be two quantities in one pack")
            if alias in seen and seen[alias] != primary:
                problems.append(
                    f"{where}: {alias!r} is an alias of both {seen[alias]!r} and "
                    f"{primary!r}; a gate resolving it would read whichever came first")
            seen[alias] = primary
    return problems


def validate(pack_dir: str, *, tier: int = Tier.BUILD,
             notes: list[str] | None = None) -> list[str]:
    """Every problem with a pack, as specific strings. Empty list = publishable.

    This is what ``nopekit pack validate`` prints and what CI gates a pull
    request on, so it is exhaustive on purpose and each message names the file,
    the id, and the fix. The checks, and why each one exists:

    * **manifest parses, has name + description, name matches the directory.**
      A pack whose ``name`` disagrees with its directory loads under one name and
      is referenced under another; the ledger then lists a pack that discovery
      cannot shadow correctly.
    * **unknown top-level keys.** ``from_dict`` drops them silently (by design —
      forward compatibility), so a typo'd ``"settle"`` costs the pack every gap
      match forever with no symptom. This is the only place it can be caught.
    * **description is one line and short.** Tier 1 is a budget, not a style.
    * **PACK.md exists and is substantive**, and says what the pack *cannot*
      settle — the section authors skip and readers need most.
    * **every ``provides_gates`` id actually registers**, and every registered
      gate is declared. A manifest that advertises a gate the code does not
      register sends `nopekit gap` looking for a capability that is not there.
    * **every gate has a negative control, and a file fixture that exists.**
      Rule 5: a gate that cannot demonstrate failure is a logger. A
      cable-routing validator once ran green for an entire revision while it
      returned a flag nobody read, with the cable geometrically inside a wall.
    * **``max_tier`` matches the gates, and at least one gate is tier 0.**
      Miscategorising cost breaks everyone's inner loop, in both directions: an
      understated tier drags a fifteen-minute solver into a loop that promised
      seconds, an overstated one scares people off a pack they could have run on
      every edit.
    * **gate ids are dotted**, so two packs cannot collide on ``geometry``.
    * **gate requirements appear in the manifest**, so tier 1 can tell the truth
      about what a pack will cost to run before anybody imports it.
    * **every gate at or below ``tier`` demonstrates** (:func:`demonstrate`): it
      passes the pack's own baseline, its control fires, the control still
      fires against an empty host, and it reads nothing of its host's
      ``ctx.params`` (:func:`seal_findings`). Everything above was static, and static
      certified a planted ``return True`` as publishable (S-09): a gate whose
      control is declared, exists and has never once fired reads exactly like one
      that works.

    Returns problems rather than raising: a validator that stops at the first
    problem turns one fix-and-rerun cycle into six. The one thing that does stop
    the run is a directory that is not a pack at all.

    ``notes``, when given, collects what is true but not wrong: a gate whose tools
    are absent here was not demonstrated, and a gate above ``tier`` was not run.
    Neither is ever a returned problem. What that separation protects: CI has no
    omc and no trimesh, so a validator that counted a tooling skip as a problem
    would be red on every runner, and one that stayed silent would let a skip
    pass for a demonstration (tests:H8).
    """
    problems: list[str] = []
    pack_dir = os.path.abspath(pack_dir)
    if not os.path.isdir(pack_dir):
        return [f"{pack_dir}: not a directory"]

    dirname = os.path.basename(pack_dir.rstrip(os.sep))
    manifest_path = os.path.join(pack_dir, MANIFEST_NAME)

    # -- tier 1: the manifest -------------------------------------------- #
    if not os.path.isfile(manifest_path):
        return [f"{MANIFEST_NAME} is missing — without it the pack is invisible to discovery"]

    raw: Any = None
    try:
        raw = read_json(manifest_path)
    except NopekitError as exc:
        return [f"{MANIFEST_NAME}: {exc}"]
    if raw is None:
        return [f"{MANIFEST_NAME} is empty"]
    if not isinstance(raw, dict):
        return [f"{MANIFEST_NAME}: must contain a JSON object, not {type(raw).__name__}"]

    known = {f.name for f in dataclasses.fields(PackManifest)}
    for key in sorted(set(raw) - known):
        problems.append(
            f"{MANIFEST_NAME}: unknown key {key!r} is silently ignored — "
            f"check the spelling against: {', '.join(sorted(known))}"
        )

    try:
        manifest = read_manifest(pack_dir)
    except NopekitError as exc:
        problems.append(str(exc))
        return problems

    name = manifest.name.strip()
    if name != dirname:
        problems.append(
            f"{MANIFEST_NAME}: name {name!r} does not match the directory name {dirname!r} — "
            f"discovery shadows by directory, so the two must agree"
        )
    if not _PUBLISH_NAME_RE.match(name):
        problems.append(
            f"{MANIFEST_NAME}: name {name!r} should be lowercase and hyphenated "
            f"(e.g. `cfd-openfoam`, `fdm-print`)"
        )

    description = (manifest.description or "").strip()
    if not description:
        problems.append(
            f"{MANIFEST_NAME}: description is empty — it is the one line every agent reads about this pack"
        )
    else:
        if "\n" in description.strip():
            problems.append(f"{MANIFEST_NAME}: description must be ONE line (it contains a newline)")
        if len(description) > MAX_DESCRIPTION_CHARS:
            problems.append(
                f"{MANIFEST_NAME}: description is {len(description)} chars "
                f"(budget {MAX_DESCRIPTION_CHARS}) — if it needs that much, the pack is doing "
                f"too much and should be split"
            )

    if not manifest.settles and not manifest.claim_classes:
        problems.append(
            f"{MANIFEST_NAME}: both `settles` and `claim_classes` are empty — "
            f"`nopekit gap` can never match this pack to a capability gap"
        )
    if not (manifest.origin or "").strip():
        problems.append(
            f"{MANIFEST_NAME}: `origin` is empty — say where this came from "
            f"(extracted from a shipped project / built by the extension protocol / ported from a paper); "
            f"the next reader calibrates their trust on that line"
        )

    # -- tier 2: PACK.md -------------------------------------------------- #
    doc_path = os.path.join(pack_dir, DOC_NAME)
    if not os.path.isfile(doc_path):
        problems.append(f"{DOC_NAME} is missing — tier 2 is what makes the domain usable by an agent")
    else:
        try:
            doc = _read_text(doc_path, DOC_NAME)
        except NopekitError as exc:
            doc = ""
            problems.append(str(exc))
        substantive = [ln for ln in doc.splitlines() if ln.strip()]
        if len(substantive) <= MIN_DOC_LINES:
            problems.append(
                f"{DOC_NAME} has only {len(substantive)} non-blank lines "
                f"(want more than {MIN_DOC_LINES}; PACK_FORMAT asks for ~150) — "
                f"a stub here means an agent works the domain blind"
            )
        if doc and "cannot" not in doc.lower():
            problems.append(
                f"{DOC_NAME} never says what this pack CANNOT settle — that section prevents "
                f"more damage than the gate list, because it names what a reader will otherwise assume"
            )

    if not os.path.isfile(os.path.join(pack_dir, LENSES_NAME)):
        problems.append(
            f"{LENSES_NAME} is missing — a pack ships its domain's adversarial review "
            f"dimensions (method rule 8: review moves the spec before anything is built)"
        )

    # -- the baseline projection ------------------------------------------ #
    # Without it a gate skips for want of a parameter, and a skipped negative
    # control never fires — so the gate ships UNPROVEN while the suite reads green.
    # That has already happened once in this repository: a gate declared a fixture
    # that was never written, and nothing caught it because the gate was skipping
    # for an unrelated missing dependency. The baseline is what makes `nopekit
    # gate selftest` mean something in CI.
    baseline = os.path.join(pack_dir, SELFTEST_DIR, BASELINE_NAME)
    baseline_usable = False
    if not os.path.isfile(baseline):
        problems.append(
            f"{SELFTEST_DIR}/{BASELINE_NAME} is missing — without a plausible "
            f"projection the gates skip, their negative controls never fire, and "
            f"nothing in this pack is proven"
        )
    else:
        try:
            with open(baseline, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict):
                problems.append(
                    f"{SELFTEST_DIR}/{BASELINE_NAME} must be a JSON object (a model "
                    f"projection), not a {type(loaded).__name__}"
                )
            elif not any(not key.startswith("_") for key in loaded):
                problems.append(
                    f"{SELFTEST_DIR}/{BASELINE_NAME} carries no parameters — only "
                    f"metadata keys"
                )
            else:
                baseline_usable = True
                problems.extend(_alias_problems(loaded))
        except (OSError, ValueError) as exc:
            problems.append(f"{SELFTEST_DIR}/{BASELINE_NAME} does not parse: {exc}")

    # -- generators ------------------------------------------------------- #
    gen_dir = os.path.join(pack_dir, GENERATORS_DIR)
    for gen in _declared_str_list(manifest.provides_generators):
        stem = gen.split(":", 1)[0].split(".")[-1]
        candidates = (
            os.path.join(gen_dir, f"{stem}.py"),
            os.path.join(gen_dir, f"{gen}.py"),
        )
        if not any(os.path.isfile(c) for c in candidates):
            problems.append(
                f"{MANIFEST_NAME}: provides_generators lists {gen!r} but no matching file in {GENERATORS_DIR}/"
            )

    # -- gates: the expensive half --------------------------------------- #
    gates_dir = os.path.join(pack_dir, GATES_DIR)
    declared_gates = _declared_str_list(manifest.provides_gates)
    gate_files = _gate_files(gates_dir)

    if declared_gates and not gate_files:
        problems.append(
            f"{MANIFEST_NAME}: provides_gates declares {len(declared_gates)} gate(s) but "
            f"{GATES_DIR}/ has no importable .py files"
        )

    specs: list[GateSpec] = []
    loaded = False
    if gate_files:
        # A genuine ImportError here is a spine bug (gates.py missing), not the
        # pack's fault, so it is deliberately NOT caught and turned into a
        # "problem" string — a validator that reports a broken spine as a broken
        # pack sends the user to fix the wrong file.
        registry = _fresh_registry()
        # Load through the same path the real run uses, from the PARENT directory,
        # so `find` resolves the pack exactly as it will at check time. Validating
        # through a different code path than the one that runs is how a pack passes
        # validation and then fails to load.
        parent = os.path.dirname(pack_dir)
        prev_env = os.environ.get(PACK_PATH_ENV)
        os.environ[PACK_PATH_ENV] = parent
        try:
            specs = load_gates(dirname, registry, root=None)
            loaded = True
        except NopekitError as exc:
            problems.append(str(exc))
        finally:
            if prev_env is None:
                os.environ.pop(PACK_PATH_ENV, None)
            else:
                os.environ[PACK_PATH_ENV] = prev_env

    registered = {spec.id for spec in specs}
    # Gates whose control cannot even be built, already a static problem below.
    # Their demonstration would only restate it ("control did not fire: fixture
    # ... does not exist"), and a derived message beside its root cause is one
    # more line to skim past. Not a fixture outside the pack: that one may exist
    # and run, and what it does when it runs is news.
    unusable_control: set[str] = set()
    # Only cross-check the manifest against the registry when the gates actually
    # loaded. After an import failure "nothing registered it" is true but
    # useless: it points the author at the @gate id when the real problem is the
    # traceback three lines up, and a derived message that outnumbers the root
    # cause is how a validator trains people to skim it.
    if loaded or not gate_files:
        for gid in declared_gates:
            if gid not in registered:
                problems.append(
                    f"{MANIFEST_NAME}: provides_gates lists {gid!r} but nothing registered it "
                    f"(check the @gate id in {GATES_DIR}/)"
                )
    for spec in specs:
        if declared_gates and spec.id not in declared_gates:
            problems.append(
                f"gate {spec.id!r} is registered but {MANIFEST_NAME} provides_gates does not list it — "
                f"tier 1 must describe what the pack actually provides"
            )

        if "." not in spec.id:
            problems.append(
                f"gate {spec.id!r}: ids are dotted and pack-prefixed (e.g. `fdm.overhang`) "
                f"so two packs cannot collide"
            )
        # `name` and `dirname` are both accepted here: when they disagree that is
        # already its own problem above, and repeating it once per gate buries the
        # one message that tells the author what to fix.
        if spec.pack and spec.pack not in (name, dirname):
            problems.append(
                f"gate {spec.id!r} declares pack={spec.pack!r} but lives in pack {name!r}"
            )
        if not spec.claims:
            problems.append(
                f"gate {spec.id!r} declares no `claims` — it can never be matched to a claim, "
                f"so it can never settle one (bind to claim TAGS, not just ids)"
            )
        if not (spec.settles or "").strip():
            problems.append(
                f"gate {spec.id!r} has an empty `settles` — that string is what turns "
                f"'no gate covers C2' into 'this gate might'"
            )
        # P2.2-D14: a pack's prerequisites are its own gates. Its controls and
        # baseline are sealed to the pack (invariant 5); a prerequisite in
        # another pack would make the isolation `demonstrate` checks depend on
        # that pack's version, and a missing pack would Skip this one's gates
        # in every project that installs it alone. A project's `gates/` may
        # name any id: there it is the project's choice, and `doctor` names one
        # nothing registers.
        for need in spec.needs or ():
            if need not in registered:
                problems.append(
                    f"{spec.id}: prerequisite {need} is not in this pack — a pack's "
                    f"prerequisites are its own gates (its controls and baseline are "
                    f"sealed to it); declare the edge in the project instead, or drop it")

        nc = spec.negative_control
        if nc is None:
            problems.append(
                f"gate {spec.id!r} has no negative_control — a gate that cannot demonstrate "
                f"failure is a logger, not a gate"
            )
        elif _is_file_fixture(nc.fixture):
            fixture_path = os.path.abspath(os.path.join(pack_dir, nc.fixture))
            if not _inside(pack_dir, fixture_path):
                problems.append(
                    f"gate {spec.id!r}: negative_control fixture {nc.fixture!r} points outside the pack"
                )
            elif not os.path.isfile(fixture_path):
                unusable_control.add(spec.id)
                problems.append(
                    f"gate {spec.id!r}: negative_control fixture {nc.fixture!r} does not exist — "
                    f"this gate's ability to fail has never been shown"
                )
        elif not nc.fixture.strip():
            unusable_control.add(spec.id)
            problems.append(f"gate {spec.id!r}: negative_control has an empty fixture")
        good_ref = (getattr(nc, "good", "") or "").strip() if nc is not None else ""
        if good_ref and _is_file_fixture(good_ref):
            # The known-good fixture (P2.3), checked as the known-bad one is:
            # inside the pack, and there.
            good_path = os.path.abspath(os.path.join(pack_dir, good_ref))
            if not _inside(pack_dir, good_path):
                problems.append(f"gate {spec.id!r}: negative_control good fixture "
                                f"{good_ref!r} points outside the pack")
            elif not os.path.isfile(good_path):
                unusable_control.add(spec.id)
                problems.append(f"gate {spec.id!r}: negative_control good fixture "
                                f"{good_ref!r} does not exist — this gate was never shown "
                                f"to pass its known-good control")

        for tool in spec.requires_tools:
            if tool not in manifest.requires_tools:
                problems.append(
                    f"{MANIFEST_NAME}: requires_tools does not list {tool!r}, needed by gate {spec.id!r} — "
                    f"tier 1 must tell the truth about what running this pack costs"
                )
        for mod in spec.requires_python:
            if mod not in manifest.requires_python:
                problems.append(
                    f"{MANIFEST_NAME}: requires_python does not list {mod!r}, needed by gate {spec.id!r}"
                )

    # file-based fixtures imply a selftest/ directory
    file_fixtures = [
        spec for spec in specs
        if spec.negative_control and _is_file_fixture(spec.negative_control.fixture)
    ]
    if file_fixtures and not os.path.isdir(os.path.join(pack_dir, SELFTEST_DIR)):
        ids = ", ".join(sorted(s.id for s in file_fixtures))
        problems.append(
            f"{SELFTEST_DIR}/ is missing but file fixtures are declared by: {ids}"
        )

    # tier consistency
    if specs:
        tiers = [int(spec.tier) for spec in specs]
        worst = max(tiers)
        declared_max = int(manifest.max_tier)
        if worst > declared_max:
            hot = ", ".join(sorted(s.id for s in specs if int(s.tier) > declared_max))
            problems.append(
                f"{MANIFEST_NAME}: max_tier is {declared_max} but gate(s) {hot} declare tier {worst} — "
                f"understating cost drags an expensive gate into someone's tier-0 inner loop"
            )
        elif worst < declared_max:
            problems.append(
                f"{MANIFEST_NAME}: max_tier is {declared_max} but the most expensive gate is tier {worst} — "
                f"overstating cost scares people off a pack they could run on every edit"
            )
        if min(tiers) > int(Tier.INSTANT):
            problems.append(
                f"no tier-0 gate: the cheapest gate here is tier {min(tiers)}. A pack of only "
                f"expensive gates has not finished its job — find the analytic bound first"
            )

    # -- demonstration: the gate on the gates, actually run --------------- #
    # Only once the gates loaded and the baseline is an object with parameters:
    # before that, every gate would "fail" for the one reason already stated.
    if loaded and specs and baseline_usable:
        from . import report as _report    # the one word table (D-16); not at import
        shown = demonstrate(pack_dir, tier=tier)
        # A demonstration line that restates the static problem: the control
        # that could not be built, in the table's words for either half.
        q = _report.HUMAN["qualification"]
        restating = (_CONTROL_PREFIX, *(f"{_report.unqualified_text('')}{q[half]} "
                                        f"{q['outcome']['errored']}"
                                        for half in ("known_bad", "known_good")))
        for line in shown.problems:
            gate_id, _, why = line.partition(": ")
            if gate_id in unusable_control and why.startswith(restating):
                continue
            problems.append(line)
        if notes is not None:
            notes.extend(f"not demonstrated here, its tools are absent: {entry}"
                         for entry in shown.skipped)
            notes.extend(shown.unchecked)
            above = sorted(spec.id for spec in specs if int(spec.tier) > int(tier))
            if above:
                notes.append(
                    f"not demonstrated at tier <= {int(tier)}: {len(above)} gate(s) above it "
                    f"({', '.join(above)})")

    return problems


# --------------------------------------------------------------------------- #
# demonstration:  the gate on the gates, as the spine's code
# --------------------------------------------------------------------------- #
#: Every demonstration problem about a gate's CONTROL starts with this, after
#: ``"<gate id>: "`` — how ``validate`` recognises the ones that restate a static
#: fixture problem it has already reported.
_CONTROL_PREFIX = "control "


@dataclasses.dataclass
class Demonstration:
    """What :func:`demonstrate` found in one pack.

    ``problems``  one line per defect, ``"<gate id>: <why>"``; a defect of the
                  pack as a whole (no baseline, gates that would not load) names
                  the file instead. Empty means every gate in scope was shown to
                  accept its pack's good design AND to refuse its known-bad one.
    ``skipped``   ``"<gate id> (<reason>)"`` for each gate in scope whose tools are
                  absent here. Not demonstrated, and never a problem — reported so
                  it cannot hide (S-12).
    ``ran``       how many gates in scope ran their control. Zero problems with
                  zero controls run is "nothing was tried", not "nothing failed",
                  and this is the field that tells the two apart.
    ``unchecked`` ``"<gate id>: isolation not checked — its prerequisite <id> is
                  skipped here (<why>)"`` for each prerequisite step 5 could not
                  run here, its tools absent. Not a problem, and never silent: a
                  skipped isolation check is as unrun as a skipped control.
                  What slipped through (review of P2.2): it was dropped without
                  a trace, so ``pack validate`` passed a non-isolated edge on a
                  machine without the guard's tool. Its own list, not
                  ``skipped``: that one is the gates whose own control did not
                  run, and ``gate selftest --pack`` counts it.
    """

    problems: list[str] = dataclasses.field(default_factory=list)
    skipped: list[str] = dataclasses.field(default_factory=list)
    ran: int = 0
    unchecked: list[str] = dataclasses.field(default_factory=list)
    qualifications: dict = dataclasses.field(default_factory=dict)
    """``{gate id: verdicts.QualificationFacts}`` for each gate in scope whose
    controls ran: what pack mode prints its lines and counts from (P2.3) — one
    producer, `verdicts._qualification` judging them. A gate whose tools are
    absent here is in ``skipped`` and not here."""


def baseline_context(pack_dir: str, *, out_dir: str) -> "GateContext":
    """The one sealed context a pack's gates are demonstrated against.

    * ``params`` — ``selftest/baseline.json`` exactly as it parses, ``_notes`` and
      ``_aliases`` included. That is how the suite's own oracle builds it
      (``tests/test_packs.py``, core:§5.12), and ``DemonstrateAgrees`` compares
      the two: stripping keys here would hold the copy to a different input.
    * ``root`` — the pack directory, so a relative asset path in the baseline
      resolves inside the pack.
    * ``ledger`` — EMPTY. What slipped through otherwise: openmodelica binds its
      baseline's variables to claims C1 and C2 and prefers the ledger's limit, so
      inside a project whose own C1 reads "≤ 0.5 mm" the pack judged 342.8 K
      against 0.5 and failed its own baseline (packs:H20). A pack's good design
      is good against the pack's limits, not the host's.
    * ``extra`` — empty. ``tier`` — EXTERNAL, the oracle's ``tier=3``: which gates
      run is the caller's ceiling, but a gate that reads ``ctx.tier`` to choose a
      cheaper path must take the path it takes under the oracle.
    * ``out_dir`` — the caller's, and required: every default lands inside
      somebody's tree, and a pack directory is somebody else's (packs:H16).

    Read fresh on every call, so a gate that mutates its params cannot hand the
    change to the next gate. Raises ``NopekitError`` when the baseline is
    missing, does not parse, or is not a JSON object.
    """
    from . import gates as _gates          # local import: see _fresh_registry

    pack_dir = os.path.abspath(pack_dir)
    path = os.path.join(pack_dir, SELFTEST_DIR, BASELINE_NAME)
    where = f"{SELFTEST_DIR}/{BASELINE_NAME}"
    if not os.path.isfile(path):
        raise NopekitError(
            f"{where} is missing — without a plausible projection the gates skip "
            f"and their negative controls never fire")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            params = json.load(handle)
    except (OSError, ValueError) as exc:
        raise NopekitError(f"{where} does not parse: {exc}") from exc
    if not isinstance(params, dict):
        raise NopekitError(
            f"{where} must be a JSON object (a model projection), not a "
            f"{type(params).__name__}")
    return _gates.GateContext(
        root=pack_dir, ledger=Ledger(), model=None, params=params,
        out_dir=out_dir, tier=int(Tier.EXTERNAL), extra={})


def _run_dir(base: str, gate_id: str, run: str) -> str:
    """``<base>/<gate id>/<run>``, emptied: one directory per gate per run, so no
    run can read a file another left behind (packs:H5) — including a previous
    demonstration into the same caller's ``out_dir``. Not created: ``ctx.out_path``
    makes it if the gate writes anything."""
    safe = re.sub(r"[^0-9A-Za-z._-]+", "_", gate_id).strip("._") or "gate"
    path = os.path.join(base, safe, run)
    shutil.rmtree(path, ignore_errors=True)
    return path


def _why(verdict: Any) -> str:
    """Everything a verdict says about itself, on one line: the error, then the
    detail or skip reason. Either alone can be useless — ``negative control
    unusable`` names no file, and a detail can omit the rule that tripped."""
    parts = [part for part in (verdict.error, verdict.detail or verdict.skip_reason) if part]
    return ": ".join(parts) or "no reason given"


def demonstrate(pack_dir: str, *, tier: int = Tier.BUILD,
                out_dir: str | None = None) -> Demonstration:
    """Run a pack's gates against their own good and known-bad inputs.

    The gate on the gates, as the spine's code rather than only a test's. Per
    gate at or below ``tier``, each run on a fresh :func:`baseline_context`:

    1. **the baseline must pass.** A gate that fails the pack's own good design
       makes its control meaningless — it was failing before the fixture touched
       anything. An always-False gate passed ``gate selftest`` for exactly that
       reason: only the reject half was ever tested (S-04).
    2. **the control must fire** (:func:`gates.selftest`). A gate that passes its
       known-bad input is a logger.
    3. **the seal probe**: the control must also fire against an empty host —
       ``params={}``, ``extra={}``, an empty ledger. A fixture that layers its
       known-bad values over the host's projection instead of stating everything
       its gate reads fires in the pack's CI and can be defused by any project
       that happens to state the key it forgot (invariant 5, SEALED).
    4. **the seal, read off the trace**: the control of step 2 must read nothing
       of its host's ``ctx.params`` (:func:`seal_findings`, one line per gate).
       Step 3 sees only what an EMPTY host changes; a fixture that layers the
       whole baseline over the host fires the same both ways and passed it.
    5. **isolation** (P2.2-D13): every prerequisite in the gate's ``needs``
       closure must pass the gate's own known-bad control. One that fails it
       pre-empts the control wherever both run, and the dependent's admission
       then shows nothing about the inputs it judges (:func:`_isolation_problems`;
       ``tests/test_packs.ControlsAreIsolated`` holds its own copy, D-25). A
       prerequisite whose tools are absent here is not checked, and not a
       problem — but it goes to ``unchecked``, so the unrun check is seen.

    A skip is honest only when :func:`gates.availability` says the gate's tools
    are absent; it goes to ``skipped``. A skip with the tools present is a
    problem: the gate decided for itself that the input does not apply (S-12).

    The pack is loaded ALONE, from ``pack_dir`` itself, into a fresh registry —
    no lookup by name, so neither ``$NOPEKIT_PACK_PATH`` nor
    ``~/.nopekit/packs`` can put another copy in its place (S-87).

    ``tier`` defaults to BUILD (1): tiers 0 and 1 call no external solver, so
    ``pack validate`` stays seconds long. Rejected: every tier — publishing a pack
    would wait on omc, and on a machine without it would demonstrate nothing
    more. ``gate selftest`` in pack mode passes EXTERNAL.

    ``out_dir`` None: a temp directory made here and removed before returning —
    nothing is persisted (Q1.8). Given: ``<out_dir>/<gate id>/{baseline,control,
    sealed}``, each emptied before its run and left afterwards for the caller.

    ``tests/test_packs.py`` keeps its own copy of these rules as an independent
    oracle and ``DemonstrateAgrees`` holds this one to it (D-25). Rejected: the
    tests delegating to this function — a test that calls the code it guards is
    relaxed by relaxing that code, with no test file touched.
    """
    from . import gates as _gates          # local import: see _fresh_registry
    from . import report as _report        # the one word table (D-16); not at import
    from . import verdicts as _verdicts    # local: the same edge, the same reason
    from .verdicts import GateTrace

    shown = Demonstration()
    pack_dir = os.path.abspath(pack_dir)
    if not os.path.isfile(os.path.join(pack_dir, MANIFEST_NAME)):
        shown.problems.append(f"{MANIFEST_NAME}: missing — {pack_dir} is not a pack")
        return shown

    # The directory name, as `find` would resolve it at check time and as
    # `validate` loads it; a manifest name that disagrees is validate's to report.
    name = os.path.basename(pack_dir.rstrip(os.sep))
    registry = _fresh_registry()
    try:
        files = _gate_files(os.path.join(pack_dir, GATES_DIR))
        if files:
            _load_dir(name, pack_dir, registry)
    except NopekitError as exc:
        shown.problems.append(f"{GATES_DIR}/: {exc}")
        return shown
    if files and not registry.specs():
        # Loading "succeeded" and nothing registered: in this process the modules
        # were already imported into a registry nobody can see. Zero gates must
        # not demonstrate as zero problems.
        shown.problems.append(
            f"{GATES_DIR}/: {len(files)} gate file(s) loaded and registered no gate — "
            f"nothing here could be demonstrated")
        return shown

    in_scope = [spec for spec in registry.specs() if int(spec.tier) <= int(tier)]
    if not in_scope:
        return shown

    base = out_dir if out_dir is not None else tempfile.mkdtemp(prefix="nopekit-demonstrate-")
    try:
        try:
            baseline_context(pack_dir, out_dir=base)
        except NopekitError as exc:
            shown.problems.append(str(exc))
            return shown

        walked = _walked_origin(pack_dir)
        for spec in in_scope:
            entry = registry.get(spec.id)
            if entry is None:                       # pragma: no cover - defensive
                shown.problems.append(f"{spec.id}: vanished from the registry")
                continue
            _spec, fn = entry
            nc = spec.negative_control
            expect = (getattr(nc, "expect", "fail") or "fail").strip().lower()

            # 1. the known-good control: the pack's baseline, or the `good`
            #    fixture the gate declares, built over it (P2.3-D4) — traced, so
            #    the fixture's seal (1b) and its channel (2b) are read off it
            good_trace = GateTrace(kind="control")
            good_ref = (getattr(nc, "good", "") or "").strip()
            good_ctx = baseline_context(pack_dir, out_dir=_run_dir(base, spec.id, "baseline"))
            good_line = ""
            if good_ref:
                try:
                    good_ctx = _gates.run_good_fixture(spec, fn, good_ctx, trace=good_trace,
                                                       out_dir=good_ctx.out_dir)
                except NopekitError as exc:
                    good_ctx = None
                    good_line = str(exc).splitlines()[0] if str(exc) else "unusable"
            if good_ctx is None:
                # Said once, by the judge, with the rest of its facts (`settle`).
                verdict = None
                outcome = "errored"
            else:
                verdict = _gates.run_gate(spec, fn, good_ctx, trace=good_trace)
                outcome = verdict.outcome
            good_extra = _verdicts._extra_keys(good_ctx) if good_ctx is not None else ()
            if outcome == "skipped":
                available, missing = _gates.availability(spec)
                if not available:
                    # Its control would skip for the same reason: selftest asks
                    # availability before it builds anything.
                    shown.skipped.append(f"{spec.id} ({missing or 'not available here'})")
                    continue
                good_line = verdict.skip_reason or "no reason given"
                if not good_ref:
                    shown.problems.append(
                        f"{spec.id}: skips its own baseline while its tools are present "
                        f"({good_line}) — a gate never shown to accept a good design is not "
                        f"shown to measure anything; state what it reads in "
                        f"{SELFTEST_DIR}/{BASELINE_NAME}")
            elif outcome == "error":
                good_line = (str(verdict.error).splitlines() or [""])[0]
                if not good_ref:
                    shown.problems.append(
                        f"{spec.id}: fails its own baseline — it errored: {_why(verdict)}")
            elif outcome == "fail" and good_ref:
                pass                    # the judge's, with the rest of its facts (`settle`)
            elif outcome == "fail":
                shown.problems.append(
                    f"{spec.id}: fails its own baseline: {_why(verdict)} — the baseline "
                    f"is not good or the gate is wrong, and its control proves nothing "
                    f"until one of them is fixed")
            known_good = {"pass": "pass", "fail": "fail", "skipped": "skipped"}.get(
                outcome, "errored")
            if known_good == "pass" and _gates.context_breach(
                    spec, good_ctx.params, read=good_trace.params) is not None:
                # P2.4-D19, judged here as `check` and `gate selftest` judge it
                # (`verdicts._good_half`): a known-good control outside the
                # evaluator's own operating context never showed it passing where
                # it claims to hold. What slipped through (review of P2.4): a pack
                # whose baseline sat outside a declared range validated
                # `publishable` while `gate selftest` and `check` held it unqualified.
                known_good = "outside"
            # 1b. the good fixture's seal: like the known-bad fixture's, it builds
            #     from the pack's own baseline, never from its host's params.
            if good_ref:
                unsealed_good = _seal_problem(_seal_finding(spec, good_trace))
                if unsealed_good:
                    shown.problems.append(unsealed_good.replace(
                        f"{_CONTROL_PREFIX}reads",
                        f"{_report.HUMAN['qualification']['known_good']} {_CONTROL_PREFIX}reads",
                        1))

            # 2. the known-bad input, over the pack's baseline — traced, so the
            #    seal detector (4.) reads what the control took from its host
            trace = GateTrace(kind="control")
            control = _gates.selftest(
                spec, fn, baseline_context(pack_dir, out_dir=_run_dir(base, spec.id, "control")),
                trace=trace)
            if control.outcome == "skipped":
                # gates.selftest turns a skip with the tools present into an
                # error, so a skip here is availability's — asked again anyway.
                available, missing = _gates.availability(spec)
                if not available:
                    shown.skipped.append(f"{spec.id} ({missing or 'not available here'})")
                    continue
            shown.ran += 1
            bad_extra = tuple(trace.handed_extra or ())
            if control.outcome == "pass":
                known_bad, bad_line = "fail", ""          # rejected, as declared
            elif control.outcome == "error":
                known_bad = ("skipped" if str(control.error).startswith("skipped on its own")
                             else "errored")
                bad_line = trace.gate_said or _verdicts._bad_line(trace, control)
            elif "PASSED its own known-bad" in (control.detail or ""):
                known_bad, bad_line = "pass", ""
            else:
                # It crashed on its fixture: the gate's own first line, never the
                # selftest's prose about it (review of P2.3).
                known_bad, bad_line = "errored", (trace.gate_said or "no reason given")
            facts = _verdicts.QualificationFacts(known_bad=known_bad, known_good=known_good,
                                                 bad_line=bad_line, good_line=good_line,
                                                 expect=expect)
            def settle(judged: Any) -> None:
                """The qualification, judged once, and its one problem — a known-good
                fact the baseline's own problem above already said excepted
                (invariant 6's words, which name the baseline)."""
                said = _unqualified_problem(spec.id, judged)
                kind = _verdicts.parse_token(_verdicts._qualification(judged))[0]
                if said and (good_ref or kind not in _BASELINE_SAID):
                    shown.problems.append(said)
                shown.qualifications[spec.id] = judged

            # After what the control DID, never before: the qualification's own
            # problem is the louder news.
            unsealed = _seal_problem(_seal_finding(spec, trace))
            if control.outcome != "pass":
                settle(facts)
                if unsealed:
                    shown.problems.append(unsealed)
                continue

            # 2b. channel parity (D-26, P2.3-D5): both controls hand the gate the
            #     same `ctx.extra` keys — or a gate that fails exactly when
            #     `extra` is not empty passes its baseline, fails its fixture, and
            #     has shown nothing.
            if known_good == "pass" and bad_extra != good_extra:
                facts = dataclasses.replace(facts, channels=(bad_extra, good_extra))

            # 3. the same known-bad input with nothing to inherit from
            bare = dataclasses.replace(
                baseline_context(pack_dir, out_dir=_run_dir(base, spec.id, "sealed")),
                params={}, extra={}, ledger=Ledger())
            sealed = _gates.selftest(spec, fn, bare)
            if sealed.outcome != "pass":
                how = "skips" if sealed.outcome == "skipped" else "does not fire"
                shown.problems.append(
                    f"{spec.id}: {_CONTROL_PREFIX}fires only with the baseline as host — "
                    f"against an empty host it {how} ({_why(sealed)}); the fixture "
                    f"inherits from the host instead of stating everything its gate "
                    f"reads, so installing this pack in another project can defuse it")

            # 4. the seal, read off step 2's trace: what the probe cannot see
            if unsealed:
                shown.problems.append(unsealed)

            # 5. isolation (P2.2-D13): every prerequisite in this gate's closure
            #    must PASS the known-bad control this gate just failed. If one
            #    fails it too, the guard pre-empts the control wherever both
            #    run: the dependent is pruned before its own judgement, and
            #    the control shows nothing about the inputs it actually judges.
            found, unchecked = _isolation_problems(pack_dir, registry, spec, fn,
                                                   _run_dir(base, spec.id, "isolation"))
            shown.problems.extend(found)
            shown.unchecked.extend(unchecked)

            # 6. the mutation pass, for a pack whose origin is the session's — a
            #    project-local, user or path pack (P2.3-D6) — on the known-good
            #    run, as `check` runs it there: every conclusive mutation must
            #    fail, and its controls must reach it as a check run does (no
            #    `ctx.extra` channel).
            if walked and known_good == "pass" and not facts.channels:
                if bad_extra or good_extra:
                    facts = dataclasses.replace(facts, check_channel=(bad_extra, good_extra))
                else:
                    try:
                        walk = _gates.mutation_walk(spec, fn, good_ctx, verdict,
                                                    trace=good_trace, roots=[pack_dir])
                    except _gates.MutationCannotRun as exc:
                        facts = dataclasses.replace(
                            facts, walk=f"could-not-run|{str(exc).splitlines()[0]}")
                    else:
                        facts = dataclasses.replace(
                            facts, walk=f"errored|{walk.flaky}" if walk.flaky else "",
                            mutation=(sum(1 for r in walk.results if r.outcome == "fail"),
                                      len(walk.results), len(walk.inconclusive)),
                            boundary=walk.boundary)
            settle(facts)
    finally:
        if out_dir is None:
            shutil.rmtree(base, ignore_errors=True)
    return shown


#: The known-good facts whose problem the baseline's own line already says
#: (step 1: "fails its own baseline", "skips its own baseline"), so the judge's
#: is not said twice. `known-good:outside` is not one: nothing above says it.
_BASELINE_SAID = ("known-good:fail", "known-good:errored", "known-good:skipped")


def _unqualified_problem(gate_id: str, facts: Any) -> str:
    """``"<gate>: unqualified: <the first fact that does not hold>[ — <what to
    do>]"`` for a judged qualification, or ``""`` when it holds — in the
    table's words alone (``report.qualification_reason``, the remedy from
    ``HUMAN["qualification"]["remedy"]``). The one problem a qualification
    adds to a pack's list, whatever fact failed. What slipped through (review
    of P2.3): each fact had its own string typed here — `control did not fire:
    … PASSED its own known-bad fixture … the gate is a logger` printed under
    the line that already said `known-bad pass → unqualified`, the
    channels:differ reason a second copy of the table's, `fails its known-good
    control …` and `its known-good control … is unusable` beside it — so a
    reworded table moved `check` and left every pack-mode problem saying the
    old thing (D-16)."""
    from . import report as _report        # the one word table (D-16); not at import
    from . import verdicts as _verdicts
    token = _verdicts._qualification(facts)
    if not token:
        return ""
    kind = _verdicts.parse_token(token)[0]
    remedy = _report.HUMAN["qualification"]["remedy"].get(kind, "")
    return f"{gate_id}: {_report.unqualified_text(token)}" + (f" — {remedy}" if remedy else "")


def _walked_origin(pack_dir: str) -> bool:
    """Does the mutation pass apply to this pack's gates — is it NOT one of the
    packs that ship with the spine (``BUNDLED_PACKS``)? The rule
    ``verdicts._mutation_applies`` reads for ``check`` (P2.3-D6)."""
    bundled = os.path.realpath(BUNDLED_PACKS)
    here = os.path.realpath(pack_dir)
    return not (here == bundled or here.startswith(bundled.rstrip(os.sep) + os.sep))


def _isolation_problems(pack_dir: str, registry: Any, spec: GateSpec, fn: Any,
                        out_dir: str) -> tuple[list[str], list[str]]:
    """``(problems, unchecked)``: ``"<gate>: control not isolated — …"`` for
    each prerequisite in ``spec``'s closure that does not PASS ``spec``'s
    known-bad control, built over the pack's baseline as step 2 built it, and
    ``"<gate>: isolation not checked — …"`` for each one whose tools are absent
    here (``Demonstration.unchecked``). Worded in GLOSSARY §2's words (critique
    of the P2.2 design: the first draft said "known-bad input", a §2 Never-say
    on a `pack validate` line), and the guard's verdict in §1's: its outcome
    word from ``report.HUMAN`` and one line — a crash's first, never its
    traceback (review of P2.2: the line printed the raw token ``error`` and a
    whole flattened stack, P2.1-D15)."""
    from . import gates as _gates          # local import: see _fresh_registry
    from . import report as _report        # the one word table (D-16); not at import

    closure = [s for s in _gates.plan(registry, [spec]) if s.id != spec.id]
    if not closure:
        return [], []
    out: list[str] = []
    unchecked: list[str] = []
    for need in closure:
        entry = registry.get(need.id)
        if entry is None:
            continue
        available, why = _gates.availability(need)
        if not available:
            unchecked.append(f"{spec.id}: isolation not checked — its prerequisite "
                             f"{need.id} is skipped here ({why or 'not available here'})")
            continue
        need_spec, need_fn = entry
        try:
            bad = _gates.run_fixture(spec, fn, baseline_context(pack_dir, out_dir=out_dir),
                                     trace=None, out_dir=out_dir)
        except NopekitError as exc:
            out.append(f"{spec.id}: control not isolated — its known-bad control could not be "
                       f"rebuilt to run {need.id} on: {exc}")
            return out, unchecked
        verdict = _gates.run_gate(need_spec, need_fn, bad)
        if verdict.outcome != "pass":
            q = _report.HUMAN["qualification"]
            word = _report.HUMAN["outcome"].get(verdict.outcome, verdict.outcome)
            out.append(f"{spec.id}: "
                       + q["isolation"].format(need=need.id, gate=spec.id, word=word,
                                               body=_one_line_of(verdict))
                       + q["isolation_fix"].format(gate=spec.id))
    return out, unchecked


def _one_line_of(verdict: Any) -> str:
    """What explains ``verdict`` in one line, by its outcome — a crash's first
    line, a skip's reason, a fail's detail — as ``Verdict.render`` reads it;
    never the traceback ``run_gate`` keeps in a crash's ``detail``."""
    if verdict.outcome == "error":
        return (str(verdict.error).splitlines() or [""])[0] or "no reason given"
    if verdict.outcome == "skipped":
        return verdict.skip_reason or verdict.detail or "no reason given"
    return verdict.detail or "no reason given"


# --------------------------------------------------------------------------- #
# the seal, read off the trace:  invariant 5 at runtime
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class SealFinding:
    """One pack control that read its HOST's ``ctx.params`` (invariant 5, SEALED).

    ``gate``        the gate whose control it is.
    ``fixture``     the negative-control reference, as the gate declares it.
    ``host_paths``  every host-param path the control read, sorted and dotted;
                    ``"(all params)"`` is the whole top level (``dict(ctx.params)``,
                    ``{**ctx.params}``, ``len``, ``bool`` ...). A key the host did
                    not have is a read too: in a host that states it, the value
                    arrives.
    """

    gate: str
    fixture: str
    host_paths: tuple[str, ...]


#: How many host paths a seal problem line names before ``(+n more)``. Why 3: the
#: same budget a stale reason gets (``verdicts.MAX_STALE_REASONS``), for the same
#: reason — one line an author reads to the end. Rejected: every path (an identity
#: fixture hands the gate the host itself, and a gate that probes synonym families
#: then reads dozens of absent keys through it); one (it cannot say whether the
#: fixture read one key or copied the whole projection — "(all params)" sorts
#: first, so it is always among the three when it happened).
_SEAL_PATHS_SHOWN = 3


def _host_path(path: tuple) -> str:
    """A host-read path, dotted as a stale reason spells a param."""
    return ".".join(str(part) for part in path) or "(all params)"


def _seal_finding(spec: GateSpec, trace: Any) -> SealFinding | None:
    """The finding in one control's trace, or ``None`` when it read nothing of the
    host's params.

    ``trace.host_reads`` is filled by the host view ``gates.selftest`` hands the
    fixture (``verdicts.ParamTrace(host=True)``), so it holds reads of the host's
    ``ctx.params`` and nothing else: the fixture's own, and the gate's through a
    context the fixture handed back unchanged. The spine's own ``_fixture_root``
    lookups (``extra["pack_dirs"]``, ``extra["pack_dir"]``) — the only host reads
    the P1 probe found on the bundled packs (packs:§2) — read the caller's
    untraced ``extra`` and never reach it.
    """
    paths = tuple(sorted({_host_path(path) for path in (getattr(trace, "host_reads", None) or {})}))
    if not paths:
        return None
    nc = spec.negative_control
    return SealFinding(gate=spec.id, fixture=(nc.fixture if nc else "") or "", host_paths=paths)


def _seal_problem(finding: SealFinding | None) -> str:
    """A finding as :func:`demonstrate` reports it, or ``""`` for none.

    ``"<gate id>: control reads the host's ctx.params: a, b, c (+n more) — ..."``:
    the ``control`` prefix (``_CONTROL_PREFIX``) files it with the control, so
    `gate selftest --pack` counts the control BROKEN rather than the baseline
    failed (``cli._read_back``).
    """
    if finding is None:
        return ""
    paths = ", ".join(finding.host_paths[:_SEAL_PATHS_SHOWN])
    more = len(finding.host_paths) - _SEAL_PATHS_SHOWN
    if more > 0:
        paths += f" (+{more} more)"
    return (f"{finding.gate}: {_CONTROL_PREFIX}reads the host's ctx.params: "
            f"{paths} — {finding.fixture or 'its fixture'} builds its "
            f"known-bad input over the host project's params instead of only the "
            f"pack's own {SELFTEST_DIR}/{BASELINE_NAME}, so a project that states a key "
            f"it does not can defuse it (invariant 5, SEALED)")


def seal_findings(registry: Any, host_ctx: "GateContext", *, tier: int = Tier.EXTERNAL,
                  out_dir: str | None = None) -> list[SealFinding]:
    """Every pack control in ``registry`` that reads its host's ``ctx.params``.

    Each pack gate at or below ``tier`` runs its control (:func:`gates.selftest`)
    with ``host_ctx`` as the host and a trace of its own; a control whose trace
    recorded any host-param read is a finding. A SEALED fixture builds its
    known-bad context from the pack's own ``selftest/baseline.json`` and reads
    nothing of the host (docs/PACK_FORMAT.md, "Fixtures must be SEALED").

    What slipped through without it: SEALED was checked only by outcome — the
    control run again over an empty host must still fire (the seal probe in
    :func:`demonstrate` and ``tests/test_packs.py``). A fixture that layers the
    pack's WHOLE baseline over the host (``{**ctx.params, **BASELINE, <bad>}``)
    fires identically over both, so the probe passed it, and `pack validate`
    certified it publishable — while any key the host states beyond the
    baseline (a synonym, a derived quantity: the waterplane inertia that defused
    a hull control) still reaches the gate. An identity fixture over a host that
    is itself bad "fires" for the host's reason; the trace names it anyway. The
    outcome of a control over one host cannot show what another host would do;
    what the control READ can.

    * **Pack gates only** (``spec.pack`` set). A project's fixture deriving from
      its own model is correct there; the rule is for packs, which ship to
      strangers (CLAUDE.md, invariant 5).
    * **Host reads only.** Pass a RICH host — a project's live context, or the
      pack's own baseline as ``ControlsAreSealed`` does: a read of a key the host
      lacks is recorded too, but a fixture that reads nothing of a host stating
      every key its gate reads cannot be reading anything a stranger's project
      could change. Reads of the host's ``extra`` and ``ledger`` are not
      host reads here: the trace does not tell a fixture's from its gate's on
      those channels, and cad-solid's ``_sealed`` merges ``extra`` by design
      (channel parity is P2.3, D-26).
    * A gate whose tools are absent is not run and reads nothing: ``selftest``
      asks :func:`gates.availability` before it builds anything.
    * ``out_dir`` None: a temp directory made here and removed before returning,
      so nothing lands in a pack (packs:H16). Given: ``<out_dir>/<gate id>/seal``,
      emptied before its run.

    Never raises for a pack's fault: ``selftest`` turns a broken fixture or a
    crashing gate into a verdict, and a control that crashed after reading the
    host has still read it. :func:`demonstrate` makes every finding a problem,
    so `pack validate` and `gate selftest --pack` refuse on it.
    """
    from . import gates as _gates          # local import: see _fresh_registry
    from .verdicts import GateTrace        # local: the same edge, the same reason

    in_scope = [spec for spec in registry.specs()
                if (spec.pack or "").strip() and int(spec.tier) <= int(tier)]
    findings: list[SealFinding] = []
    if not in_scope:
        return findings
    base = out_dir if out_dir is not None else tempfile.mkdtemp(prefix="nopekit-seal-")
    try:
        for spec in in_scope:
            entry = registry.get(spec.id)
            if entry is None:                       # pragma: no cover - defensive
                continue
            _spec, fn = entry
            trace = GateTrace(kind="control")
            _gates.selftest(spec, fn, host_ctx, trace=trace,
                            out_dir=_run_dir(base, spec.id, "seal"))
            finding = _seal_finding(spec, trace)
            if finding is not None:
                findings.append(finding)
    finally:
        if out_dir is None:
            shutil.rmtree(base, ignore_errors=True)
    return findings


# --------------------------------------------------------------------------- #
# gap matching:  "no gate covers C2"  ->  "these packs might"
# --------------------------------------------------------------------------- #
#: Words that carry no domain signal. Kept short on purpose: an aggressive
#: stoplist starts eating real vocabulary ("free" span, "max" deflection) and a
#: matcher that drops the discriminating word is worse than one that keeps a
#: little noise.
_STOPWORDS = frozenset({
    "a", "an", "and", "any", "are", "as", "at", "be", "by", "for", "from", "in",
    "is", "it", "its", "of", "on", "or", "per", "that", "the", "then", "this",
    "to", "under", "up", "was", "were", "with", "within",
})


def _normalise_token(token: str) -> str:
    """Lowercase, and a crude singular fold so ``overhangs`` matches ``overhang``.

    Deliberately crude — no stemmer, no wordlist, stdlib only. Domain vocabulary
    is mostly nouns, and the plural ``s`` is the one inflection that actually
    costs matches in practice ("bridge span" vs "bridge spans", "wall thickness"
    keeps its double s and is left alone).
    """
    token = token.lower()
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        token = token[:-1]
    return token


def _tokens(*texts: str) -> set[str]:
    """Lowercase alphanumeric tokens with stopwords and single characters dropped."""
    out: set[str] = set()
    for text in texts:
        for raw in re.split(r"[^0-9A-Za-z]+", text or ""):
            if not raw:
                continue
            token = _normalise_token(raw)
            if len(token) < 2 or token in _STOPWORDS:
                continue
            out.add(token)
    return out


def _phrase(text: str) -> str:
    """Whitespace/punctuation-insensitive form of a phrase, for exact matching.

    ``"Bed-fit"``, ``"bed fit"`` and ``"BED   FIT"`` all fold to ``"bed fit"``,
    so a manifest that hyphenates and a need that does not still match exactly.
    Word ORDER is kept: "span bridge" is not the phrase "bridge span", and
    pretending otherwise manufactures matches that are not there.
    """
    words = [_normalise_token(w) for w in re.split(r"[^0-9A-Za-z]+", text or "")]
    return " ".join(w for w in words if w)


def score(need: Need, manifest: PackManifest) -> float:
    """How well one pack's vocabulary covers one capability gap. 0 = no signal.

    An exact phrase hit ("righting moment" appearing verbatim in ``settles``)
    outranks any amount of loose token overlap, because a pack author who wrote
    the exact phrase a person used for the quantity has almost certainly covered
    it. Class agreement is next, then token overlap, weighted toward the
    quantity — ``claim_class`` is a coarse bucket ("structural") that many packs
    share, while the quantity is the discriminating string.

    Exposed separately from ``match`` so `nopekit gap --propose` can show *why*
    a pack was suggested. A ranked list with no visible reason is a list nobody
    trusts, and the user is about to be asked to install a solver on the strength
    of it.
    """
    settles = _declared_str_list(manifest.settles)
    classes = _declared_str_list(manifest.claim_classes)

    quantity = (need.quantity or "").strip()
    claim_class = (need.claim_class or "").strip()

    total = 0.0

    q_phrase = _phrase(quantity)
    if q_phrase:
        if any(_phrase(s) == q_phrase for s in settles):
            total += 8.0
        elif any(q_phrase and q_phrase in _phrase(s) for s in settles):
            total += 4.0            # "draft" inside "draft fraction at full load"

    c_phrase = _phrase(claim_class)
    if c_phrase and any(_phrase(c) == c_phrase for c in classes):
        total += 5.0

    q_tokens = _tokens(quantity)
    c_tokens = _tokens(claim_class)
    settles_tokens = _tokens(*settles)
    class_tokens = _tokens(*classes)

    total += 2.0 * len(q_tokens & settles_tokens)
    total += 1.0 * len(q_tokens & class_tokens)
    total += 2.0 * len(c_tokens & class_tokens)
    total += 1.0 * len(c_tokens & settles_tokens)

    return total


def match(need: Need, manifests: Sequence[PackManifest]) -> list[PackManifest]:
    """Packs that might close this gap, best first. Scored on vocabulary overlap.

    This is the function that turns "no gate covers C2" into something
    actionable. It is intentionally a dumb lexical matcher — lowercase, split on
    non-alphanumerics, drop stopwords, count overlap between the need's
    ``quantity``/``claim_class`` and the manifest's ``settles``/
    ``claim_classes``. No embeddings, no model call, no network: this runs inside
    a tier-0 loop and must cost microseconds, and a wrong-but-explainable
    suggestion the user can dismiss in one read beats a confident opaque one.

    Packs with no overlap at all are dropped rather than ranked last. A gap
    report that lists every installed pack in descending order of irrelevance
    teaches the reader to ignore it, and the honest answer to "nothing matches"
    is an empty list — which is the extension protocol's actual trigger.

    Ties break on name, so the output is stable across runs and diffs cleanly in
    a report. A pack with a broader vocabulary does score higher on a tie; that
    is intended, since it genuinely covers more ground.
    """
    scored: list[tuple[float, str, PackManifest]] = []
    for manifest in manifests or ():
        value = score(need, manifest)
        if value > 0:
            scored.append((value, manifest.name, manifest))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [manifest for _s, _n, manifest in scored]


# --------------------------------------------------------------------------- #
# installed vs available
# --------------------------------------------------------------------------- #
def installed(root: str, *, ledger: Ledger | None = None) -> list[str]:
    """Pack names this project has opted into — ``packs`` in
    ``.nopekit/project.json``, in order, read through ``store.load(root).meta``.

    Installed is a *project* fact, not a filesystem fact. A pack sitting in
    ``~/.nopekit/packs`` is available to every project on the machine; it only
    becomes part of this one when ``project.json`` says so (``nopekit packs
    add``, or an edit of that file — there is no ``packs remove``). Keeping the
    two concepts apart is what stops a gate sweep from silently changing because
    somebody cloned a pack into their home directory last week. On a legacy
    project ``store.load`` reads the same list out of its ``ledger.json``, in
    memory, so the answer does not change when the project migrates. The
    generated index is never read for it: it is an output.

    Order is preserved (it is gate-id precedence for ``load_all_gates``) and
    duplicates are dropped. Pass ``ledger`` when you already have one loaded —
    the CLI usually does, and re-reading the records to answer a listing
    question is a wasted walk on every command.
    """
    from . import store                      # local: keeps module import cost flat
    from . import modelio as _modelio        # local: the same edge, the same reason

    # With the reader `check` migrates with: on a legacy project `load` is the
    # migration's plan, and a plan made without it refuses what `check` does
    # not (store.load's docstring has the D/d case).
    led = ledger if ledger is not None else store.load(
        root, model_prose=_modelio.static_param_prose)
    out: list[str] = []
    seen: set[str] = set()
    for name in led.meta.packs or []:
        clean = str(name).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def available(root: str | None = None) -> list[str]:
    """Every pack name discovery can see from here, in precedence order.

    The counterpart to ``installed``: `nopekit packs` shows both, because the
    two most common pack questions are "why is this gate not running" (it is
    available but not installed) and "where did this gate come from" (it is
    installed and shadowed by a copy earlier on the search path).
    """
    return [manifest.name for manifest in discover(root)]


# --------------------------------------------------------------------------- #
# key vocabularies:  two packs, one word, two meanings
# --------------------------------------------------------------------------- #
# Packs share ONE flat namespace — the model's projection — and nothing stops two
# of them from wanting the same word for different things. It happened in the
# default set: `cad-solid` reads `bbox_mm` as the ASSEMBLY envelope, `fdm-print`
# reads it as ONE PART in print orientation. A project with both (every mechanical
# project has an assembly and printed parts) could satisfy exactly one of them —
# published as the assembly, the bed-fit gate measured a 480 mm boat against a
# 220 mm printer bed and FAILED; published as the part, the envelope gate skipped
# and its claim went unheld. Neither pack was wrong, and neither could see the
# other.
#
# `GateContext.param` fixes the *reading* — `cad.bbox_mm` and `fdm.bbox_mm` resolve
# before the bare key. What is below fixes the *finding out*: the spine knows which
# packs are installed and can diff what they read, so the collision is reported by
# `nopekit doctor` instead of discovered by a verdict about the wrong object.
def key_scope(manifest: PackManifest) -> str:
    """The prefix this pack's keys may carry: ``fdm-print`` -> ``fdm``.

    Taken from the manifest's ``key_scope`` when it states one, and otherwise
    DERIVED from the gate ids it declares, which are already dotted and
    pack-prefixed (``fdm.bed_fit``, ``fdm.overhang`` -> ``fdm``). Derived rather
    than duplicated on purpose (rule 2): a pack that renamed its gates and forgot
    a second declaration would publish a scope nothing answers to.

    A pack whose gate ids do not share one prefix has no scope, and scoping is
    simply off for it — better than picking one of two prefixes and being right
    half the time.
    """
    explicit = (getattr(manifest, "key_scope", "") or "").strip()
    if explicit:
        return explicit
    prefixes = {gid.split(".", 1)[0].strip()
                for gid in (manifest.provides_gates or []) if "." in gid}
    prefixes.discard("")
    return prefixes.pop() if len(prefixes) == 1 else ""


def _read_baseline(pack_dir: str) -> dict[str, Any]:
    """The pack's baseline projection, or ``{}``. Never raises.

    A broken baseline is already reported by ``validate``; a *diagnostic* that
    died on it would take out the one command people run when confused.
    """
    path = os.path.join(pack_dir, SELFTEST_DIR, BASELINE_NAME)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def key_vocabulary(name: str, root: str | None = None) -> dict[str, dict[str, str]]:
    """Every projection key this pack reads -> ``{"primary": …, "note": …}``.

    Two sources, and both are already required to exist for other reasons:

    * ``selftest/baseline.json`` — "every key any gate in the pack reads",
      carrying a ``_notes`` line per key. Those notes ARE the meanings compared
      when two packs overlap, which is why the pack format asks for them.
    * its ``_aliases`` map — the other spellings each key answers to. A gate that
      accepts ``bbox_mm`` as a fallback for ``part_bbox_mm`` collides on the
      fallback, and the baseline alone would never show it.

    Derived from the pack's own files rather than declared twice: a vocabulary
    maintained beside the gates is a vocabulary that goes stale silently, and a
    stale one makes this whole check a decoration.
    """
    pack_dir = find(name, root)
    if not pack_dir:
        return {}
    baseline = _read_baseline(pack_dir)
    notes_raw = baseline.get(BASELINE_NOTES)
    notes = {str(k): str(v) for k, v in notes_raw.items()} if isinstance(notes_raw, dict) else {}

    vocab: dict[str, dict[str, str]] = {}
    for key in baseline:
        if key.startswith("_"):
            continue
        vocab[key] = {"primary": key, "note": notes.get(key, "")}

    aliases = baseline.get(BASELINE_ALIASES)
    if isinstance(aliases, dict):
        for primary, spellings in aliases.items():
            if isinstance(spellings, (str, bytes)) or not isinstance(spellings, Iterable):
                continue
            for spelling in spellings:
                alias = str(spelling)
                if alias.startswith("_") or alias in vocab:
                    continue        # a key the pack states itself keeps its own meaning
                vocab[alias] = {"primary": str(primary),
                                "note": notes.get(str(primary), "")}
    return vocab


def _meaning_fingerprint(note: str) -> str:
    """A note reduced to what it SAYS, for comparing two packs' definitions.

    Letters, digits and single spaces. Two packs that wrote the same sentence with
    different punctuation mean the same thing; two that wrote different sentences
    are assumed to mean different things, which is the safe direction — this
    produces a WARNING naming both packs, and a spurious one costs a reader ten
    seconds while a missed one costs them the afternoon the friction log describes.
    """
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (note or "").lower()).split())


def key_collisions(names: Sequence[str], root: str | None = None, *,
                   projection_keys: Iterable[str] = ()) -> list[KeyCollision]:
    """Keys two or more of these packs read as DIFFERENT quantities.

    ``names`` is normally ``installed(root)`` — the packs this project actually
    opted into, because a collision between two packs nobody installed is not this
    project's problem and a doctor that says so is a doctor people stop reading.

    ``projection_keys`` is the project's own flat projection. A key it publishes
    bare marks the collision ``live``: one of these two packs is, right now,
    reading a number that was written for the other.

    Packs that describe a key identically are not reported. The comparison is on
    the ``_notes`` line each pack wrote for it — a declaration, not an inference —
    so the report says the packs *declare it differently*, which is exactly what
    is known.
    """
    vocabularies = {name: key_vocabulary(name, root) for name in names}
    scopes: dict[str, str] = {}
    for name in names:
        pack_dir = find(name, root)
        if not pack_dir:
            continue
        try:
            scopes[name] = key_scope(read_manifest(pack_dir))
        except NopekitError:
            scopes[name] = ""

    published = {str(k) for k in projection_keys or ()}
    by_key: dict[str, list[str]] = {}
    for name, vocab in vocabularies.items():
        for key in vocab:
            by_key.setdefault(key, []).append(name)

    out: list[KeyCollision] = []
    for key, owners in sorted(by_key.items()):
        if len(owners) < 2:
            continue
        meanings = {name: vocabularies[name][key]["note"] for name in owners}
        fingerprints = {_meaning_fingerprint(text) for text in meanings.values()}
        if len(fingerprints) == 1 and "" not in fingerprints:
            continue                    # the packs agree; one word, one quantity
        out.append(KeyCollision(
            key=key,
            packs=list(owners),
            meanings=meanings,
            scoped={name: f"{scopes[name]}.{key}" for name in owners if scopes.get(name)},
            primary={name: vocabularies[name][key]["primary"] for name in owners},
            live=key in published,
        ))
    return out
