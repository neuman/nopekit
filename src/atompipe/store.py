# SPDX-License-Identifier: Apache-2.0
"""atompipe.store — where a project lives on disk, and the rules for touching it.

A project is its RECORD FILES — one file per record, `claims/C1.json`,
`params/<name>.json`, `decisions/<slug>.json`, `needs/<id>.json`,
`inputs/<id>.json`, `results/<claim-id>.json`, `views/<id>.json` — plus
`.atompipe/project.json` for the project's own metadata. The verdict cache,
`.atompipe/verdicts/`, is evidence and is committed too (`verdicts.py` owns it).
`.atompipe/ledger.json` is a GENERATED index of the records: an output, rebuilt
by the commands and never read back for truth. Everything else under
`.atompipe/` is this checkout's: scratch (`out/`), memory (`cache/`), and what
runs cost (`obs/`) — all ignored.

Four decisions are baked in here and are worth the words:

1. **Records are files; the one-file read survives as a generated index.** This
   module used to keep one `ledger.json`, so an agent read the whole project in
   one read, and sharding it was rejected because every diff would be a merge
   conflict. That argument inverts once a branch is a candidate design (PLAN
   D-06): the one file every command rewrote is the file every branch conflicts
   on, and a candidate that moved one number could not be merged beside one that
   moved another. So the records are files — an edit to C1 conflicts only with
   another edit to C1 — and the one-read benefit is kept by `build_index`, whose
   output is `.atompipe/ledger.json`: never hand-edited, ignored by git, and
   rebuilt from the records whenever they move (invariant 8: the index never
   disagrees with them). *Rejected:* keeping `ledger.json` as the source and
   generating the files from it (two homes, and the conflict stays); sharding by
   kind (`claims.json`: the same conflict per kind).

2. **The reader of a record file is strict.** An unknown key is refused with a
   suggestion, never dropped. What slipped through: `Record.from_dict` drops
   unknown keys, so a hand-edited `"rejectd"` vanished on load and the next save
   wrote the ledger back without it — the rejected alternatives erased from disk
   by a command that reads like a query (S-40). `from_dict` stays lenient for
   dicts in memory; a FILE a human edits goes through `read_record`.

3. **`init` refuses to run twice.** The records carry the only copy of *why*
   every number is what it is — the rejected alternatives especially. Clobbering
   them is unrecoverable in a way that losing generated CAD is not. So a second
   `init` is a user error, not an overwrite.

4. **No run history.** There used to be one: every `check` and every `gate
   selftest` appended a tracked `.atompipe/runs/NNNN-<hash>.json`, so the
   verification suite dirtied the tree it verified (S-89), and it filed
   `<gate>#selftest` rows in the same series as the sweep's rows, which no
   latency reader filtered — a median over both is the cost of neither (S-31).
   Git and the verdict cache are the history now: an entry is written once, when
   a gate's inputs are new, and never rewritten. What each run cost is
   `.atompipe/obs/`, gate runs and control runs apart; when the last full check
   ran is `.atompipe/cache/last_check.json`. Both are untracked.

A project `init` made before the records layout keeps everything in a legacy
`.atompipe/ledger.json`. `migrate_legacy` turns it into record files once — a
pure function of that file and of what the model states, resumable after a
crash, refusing anything it does not understand before it writes a byte — and
`load` reads a legacy project through the same function, in memory.

Nothing in this module stamps its own time: `init` gets `meta.created` and the
migration gets `when` from its caller (contract rule 3). A store that reached
for the clock could not be tested, and one that disagreed with the CLI about
*when* would be worse than none. Nor does it take the build lock: only the CLI
edge does (`FileLock` is not re-entrant).
"""
from __future__ import annotations

import dataclasses
import difflib
import enum
import functools
import hashlib
import json
import math
import os
import re
import sys
import types
import typing
from typing import Any, Callable, NamedTuple

from .models import (
    ALWAYS_WRITTEN,
    FORBIDDEN_KEYS,
    RECORD_KINDS,
    ArtifactKind,
    Claim,
    Decision,
    InputArtifact,
    Ledger,
    Need,
    NeedStatus,
    Param,
    PhysicalResult,
    ProjectMeta,
    View,
)
from .util import AtompipeError, FileDigests, atomic_write_json, atomic_write_text, ensure_dir

# --------------------------------------------------------------------------- #
# layout constants
# --------------------------------------------------------------------------- #
ATOMPIPE_DIR = ".atompipe"
#: The generated index on a records project; the whole project on a legacy one.
LEDGER_NAME = "ledger.json"
OUT_NAME = "out"
PACKS_NAME = "packs"                 # project-local packs, searched first by packs.py
PROJECTION_NAME = "model.json"       # modelio.write_projection lands here

#: The records layout's project file (Phase 1.3), written LAST by `init` and by
#: the legacy migration, as the commit marker. Named here ahead of that layout
#: because `find_root` must recognise a project in either shape from 1.1 on.
PROJECT_NAME = "project.json"

#: `project.json`'s own schema. The legacy ledger is schema 1 by convention (it
#: never said); a `project.json` newer than this spine is refused, never read
#: with its unknown parts dropped. Rejected: no schema (a spine older than a
#: layout would read it half-understood — the S-40 shape for a whole file).
PROJECT_SCHEMA = 2

#: What the legacy ledger becomes when it migrates: renamed, never deleted, and
#: ignored. It is the only copy of the dropped verdicts and `last_run` if anyone
#: ever wants them, and git holds it anyway. Rejected: deleting it (a migration
#: that destroys its input cannot be audited); leaving it as `ledger.json` (the
#: index is written there).
LEGACY_LEDGER_NAME = "ledger.legacy.json"

#: The record directories, each under the project root, in `models.RECORD_KINDS`
#: order (a test holds the two equal). NOT hidden: a human edits these, and
#: burying them in `.atompipe/` guarantees nobody does.
RECORD_DIRS: tuple[str, ...] = (
    "claims", "params", "decisions", "needs", "inputs", "results", "views",
)

#: Under `.atompipe/`: the tracked verdict cache, and the untracked memory and
#: cost directories (`verdicts.py` writes all three).
VERDICTS_NAME = "verdicts"
CACHE_NAME = "cache"
OBS_NAME = "obs"

#: The index's first key, so whoever opens `.atompipe/ledger.json` — a human in
#: an editor, an agent in one read — is told on line two that editing it does
#: nothing. No timestamp: the index must be a pure function of the records, or
#: every command would rewrite it (and invariant 8 could not compare it).
INDEX_BANNER = (
    "generated by atompipe: an output of claims/ params/ decisions/ needs/ inputs/ "
    "results/ views/ .atompipe/project.json; edit those, never this"
)

#: `init`'s ignore file at 1e09113. It ALLOWED the ledger (`!ledger.json`) and
#: the run history (`!runs/`), which were the project then. Kept byte for byte:
#: `ensure_ignore_blocks` replaces a file that begins with it by the marked
#: block, and a template that drifted by one byte would read as a user's lines.
_GITIGNORE_1E09113 = """\
# Generated files are outputs, not sources.
#
# out/ is gate scratch and evidence: meshes, plots, solver working directories.
# All of it is rebuildable from the model plus inputs/, and committing it turns
# every check run into a thousand-line diff.
out/
*.tmp
*.lock

# ...but the ledger and the run history ARE the project. They carry the rejected
# alternatives and the proof that a gate once passed; neither can be regenerated.
# These lines are redundant against the patterns above and deliberately so —
# they state the intent where the next person will look for it.
!ledger.json
!runs/
"""

#: `init`'s ignore file at checkpoint 1.2: `cache/` and `obs/` added (cli:H2,
#: S-76 — 1.2 began writing both and nothing ignored them, so the first `check`
#: in a clean clone dirtied `git status`), `!runs/` gone with the run history.
_GITIGNORE_1_2 = """\
# Generated files are outputs, not sources.
#
# out/ is gate scratch and evidence: meshes, plots, solver working directories.
# All of it is rebuildable from the model plus inputs/, and committing it turns
# every check run into a thousand-line diff.
out/
*.tmp
*.lock

# cache/ and obs/ are this checkout's memory, not the project's: file digests,
# the last check's summary, remembered crashes, what each run cost. The verdict
# cache (verdicts/) is evidence and stays tracked.
cache/
obs/

# ...but the ledger IS the project. It carries the rejected alternatives, which
# cannot be regenerated. This line is redundant against the patterns above and
# deliberately so — it states the intent where the next person will look for it.
!ledger.json
"""

#: The two texts an earlier `init` wrote to `.atompipe/.gitignore`, oldest first.
#: A file that BEGINS with one is `init`'s, not the user's, and the prefix is
#: replaced by the block; whatever follows is the user's and is kept. Both allow
#: `ledger.json`, which is the generated index now: left in place, `!ledger.json`
#: re-adds it on the next `git add -A`, undoing D-06. Rejected: recognising any
#: file that merely contains `!ledger.json` as a template (a user's lines around
#: it would be replaced as if they were ours).
LEGACY_GITIGNORE_TEMPLATES: tuple[str, ...] = (_GITIGNORE_1E09113, _GITIGNORE_1_2)

INPUTS_NAME = "inputs"
DOCS_NAME = "docs"
MODEL_NAME = "model"


#: What makes a directory a project: one of these FILES under `.atompipe/`, never
#: the directory itself. What slipped through (S-64): the marker was any
#: `.atompipe/` directory, and `~/.atompipe/` is where user packs live
#: (`packs.search_paths`). On a pack author's machine every directory under `~`
#: was an empty, unnamed project — `status` in a scratch directory reported
#: "(unnamed) v0.1" — so pack mode, the Stop hook's fast exit and `/start`'s
#: `init` never saw "no project". `ledger.json` stays a marker for every project
#: `init` made before `project.json` existed. Rejected: keeping the directory and
#: special-casing `~` (a project's `.atompipe/packs/` checked out on its own is
#: the same bug in another place); `.atompipe/.gitignore` (`init` writes it
#: before the ledger, so a crashed `init` would count as a project); any
#: non-empty `.atompipe/` (`packs/` is what makes the user-pack home non-empty).
_MARKERS: tuple[str, ...] = (PROJECT_NAME, LEDGER_NAME)

#: The repository boundary: the walk up stops at the first directory holding a
#: `.git` entry — a directory in a clone, a `gitdir:` FILE in a worktree or a
#: submodule. What slipped through (S-64): with no boundary, a worktree nested
#: inside a project resolved to the TRUNK's `.atompipe/`, so a command run in the
#: worktree read and wrote the trunk's ledger. The marker is checked FIRST at
#: each level (cli:H9): a project that is its own git root — the fresh-clone copy
#: of the bracket, any standalone project — must still find itself. Rejected:
#: stopping before the marker check (the case just named); asking git
#: (`git rev-parse --show-toplevel` is a subprocess on every command, and a
#: machine without git would have no projects); `.hg`/`.svn` as boundaries too
#: (Subversion before 1.7 put `.svn/` in EVERY directory, so a project in such a
#: checkout could not be found from its own `model/`).
_GIT_ENTRY = ".git"

#: The buckets under `inputs/`. These directory names are load-bearing: they are
#: also the directory HINT that `artifacts.kind_for` uses when an extension is
#: ambiguous (a .png in `references/` is a teardown photo, not a sketch).
INPUT_BUCKETS: tuple[str, ...] = (
    "sketches",
    "references",
    "cad",
    "screenshots",
    "datasheets",
    "specs",
    "measurements",
    "data",
)

#: Where each ingested artifact kind gets copied. STANDARD shares `specs/`
#: (a published code IS a requirements document) and OTHER falls into `data/`
#: rather than inventing a `misc/` bucket nobody would ever look in. LINK has no
#: file at all, so it has no bucket.
BUCKET_FOR_KIND: dict[ArtifactKind, str] = {
    ArtifactKind.SKETCH: "sketches",
    ArtifactKind.REFERENCE: "references",
    ArtifactKind.CAD: "cad",
    ArtifactKind.SCREENSHOT: "screenshots",
    ArtifactKind.DATASHEET: "datasheets",
    ArtifactKind.SPEC: "specs",
    ArtifactKind.STANDARD: "specs",
    ArtifactKind.MEASUREMENT: "measurements",
    ArtifactKind.DATA: "data",
    ArtifactKind.OTHER: "data",
    ArtifactKind.LINK: "",
}


_INPUTS_README = """\
# inputs/ — the evidence this design is built on

Put real files here. Hand sketches, photos of the thing you'd buy instead, CAD
you already have, datasheet PDFs, caliper readings, log files. Intake is not only
a conversation: the numbers that matter usually arrive as a photo of a napkin.

Use `atompipe ingest <path>` rather than copying by hand — it files the bytes in
the right bucket, hashes them, and writes the artifact's record,
`inputs/<id>.json`, pinning that hash so an edited input shows up as drift. Then
use `atompipe extract` to record what you actually read out of it:

    atompipe ingest inputs/sketches/hull.png --desc "midship section, dimensioned"
    atompipe extract hull-png --what "hull beam at midship reads 148 mm" \\
                              --grounds beam_mm

**An artifact nobody extracted from is decoration.** A file sitting in this
directory grounds nothing, proves nothing, and will not stop anyone from
re-litigating a number two months from now. `atompipe inputs --unextracted`
lists the decorations.

## Records here, bytes in the buckets

Every top-level `inputs/*.json` is a **record** — one ingested artifact: where
its bytes are, their pinned sha256, and what was read out of them. It is read
strictly, so a stray evidence file saved as `inputs/loads.json` is refused
rather than mistaken for one. **Evidence bytes live in the bucket
subdirectories below**, never at the top level: a JSON log is evidence and goes
in `data/` or `measurements/`.

## Buckets

- `sketches/`      hand drawings, napkin diagrams, whiteboard photos
- `references/`    photos of existing products, teardowns, prior art
- `cad/`           STL, STEP, 3MF, f3d, KiCad, gerbers
- `screenshots/`   a UI, a config screen, a vendor page, another tool's output
- `datasheets/`    component and material datasheets (usually PDF)
- `specs/`         written specs, briefs, RFQs, requirements — and published
                   standards (IPC, ASTM, NEC); a standard is a requirements doc
- `measurements/`  calipers, scales, meters: numbers off the real world
- `data/`          CSV, logs, test results, and anything that fits nowhere else

The directory an artifact sits in is a hint the pipeline reads, so a photo in
`references/` is treated as prior art and the same photo in `sketches/` is
treated as your own drawing. File accordingly.

Empty buckets do not survive a `git clone` (git does not track empty
directories). `atompipe ingest` recreates whichever one it needs.
"""


# --------------------------------------------------------------------------- #
# locating a project
# --------------------------------------------------------------------------- #
def _marker_in(directory: str) -> str | None:
    """The marker file that makes `directory` a project, or None.

    `isfile`, not `exists`: a `.atompipe` that is a regular file, or a
    `project.json` that is a directory, marks nothing, because the alternative is
    a confusing failure three calls later in `load`.
    """
    dot = os.path.join(directory, ATOMPIPE_DIR)
    for name in _MARKERS:
        path = os.path.join(dot, name)
        if os.path.isfile(path):
            return path
    return None


def _walk_up(start: str | None) -> tuple[str | None, str | None]:
    """`(root, boundary)`: the project found walking up from `start`, else None
    and the git root the walk stopped at (None when it reached the filesystem
    root). One walk for both callers, so the message `require_root` prints can
    never describe a different search from the one `find_root` ran."""
    cur = os.path.abspath(start or os.getcwd())
    if os.path.isfile(cur):            # tolerate being handed a file path
        cur = os.path.dirname(cur)
    while True:
        if _marker_in(cur) is not None:                       # the marker FIRST ...
            return cur, None
        if os.path.lexists(os.path.join(cur, _GIT_ENTRY)):    # ... then the boundary
            return None, cur
        parent = os.path.dirname(cur)
        if parent == cur:              # hit "/" (or a drive root)
            return None, None
        cur = parent


def find_root(start: str | None = None) -> str | None:
    """Walk UP from `start` (default: cwd) to the directory holding a project marker.

    Same contract as git's discovery of `.git`: you can run `atompipe check` from
    `model/` or from a deep `inputs/cad/` subdirectory and hit the same project.
    A project is a directory holding `.atompipe/project.json`, or a legacy
    `.atompipe/ledger.json` — never merely a `.atompipe/` directory, which is
    also what the user-pack home `~/.atompipe/packs/` looks like (S-64).

    At each level the marker is checked first; then a `.git` entry there (a
    clone's directory, or a worktree's or submodule's `gitdir:` file) ends the
    search with None. A project is found only at or below the root of the
    repository it sits in, so a worktree nested inside a project is its own
    world, not the trunk's. Returns the directory CONTAINING `.atompipe/`, or
    None if there is no project between `start` and that boundary (or the
    filesystem root).
    """
    return _walk_up(start)[0]


def require_root(start: str | None = None) -> str:
    """`find_root` or a message the user can act on.

    Every CLI command except `init` goes through here, so this is the one place
    that has to explain what is missing. "no such file or directory: ledger.json"
    is not that explanation, and neither is "run `atompipe init`" to a user
    standing in a worktree whose trunk IS a project: the message names both
    markers and the git root the search stopped at, which is the reason the
    trunk above did not count.
    """
    root, boundary = _walk_up(start)
    if root is None:
        where = os.path.abspath(start or os.getcwd())
        stopped = (f"here at {boundary}" if boundary is not None
                   else "and none was found on the way up")
        raise AtompipeError(
            f"no atompipe project found in {where} or above it (looking for "
            f"{ATOMPIPE_DIR}/{PROJECT_NAME}, or a legacy {ATOMPIPE_DIR}/{LEDGER_NAME}; "
            f"the search stops at the first directory holding {_GIT_ENTRY}, "
            f"{stopped}) — run `atompipe init` first"
        )
    return root


# --------------------------------------------------------------------------- #
# well-known paths
# --------------------------------------------------------------------------- #
def atompipe_dir(root: str) -> str:
    """`<root>/.atompipe` — the machine's half of the project."""
    return os.path.join(root, ATOMPIPE_DIR)


def ledger_path(root: str) -> str:
    """`<root>/.atompipe/ledger.json` — the GENERATED index on a records project
    (an output: `write_index` writes it, nothing reads it for truth); the whole
    project on a legacy one, until it migrates."""
    return os.path.join(atompipe_dir(root), LEDGER_NAME)


def out_dir(root: str) -> str:
    """`<root>/.atompipe/out/` — gate scratch and evidence. Git-ignored by `init`.

    Gates write meshes, plots and solver working directories here. It is a build
    artifact: deleting it must never lose anything a verdict depends on for its
    *truth*, only for its *illustration*.
    """
    return os.path.join(atompipe_dir(root), OUT_NAME)


def inputs_dir(root: str) -> str:
    """`<root>/inputs/` — NOT hidden, because humans drop files in here by hand.

    Everything else the pipeline owns lives under the dot-directory; this one is
    the user's. Burying evidence intake in `.atompipe/` guarantees nobody uses it.
    """
    return os.path.join(root, INPUTS_NAME)


def docs_dir(root: str) -> str:
    """`<root>/docs/` — generated readiness report and decision log land here."""
    return os.path.join(root, DOCS_NAME)


def model_dir(root: str) -> str:
    """`<root>/model/` — the parametric model, the single source of truth."""
    return os.path.join(root, MODEL_NAME)


def project_paths(root: str) -> dict[str, str]:
    """Every well-known path in one call, so no other module hardcodes a join.

    The layout is this module's responsibility alone. When `report.py` wants
    `docs/readiness.md` it asks here; that way moving the layout is one edit
    instead of a grep across the spine. Paths are returned whether or not they
    exist yet — this is the map, not an inventory.
    """
    dot = atompipe_dir(root)
    paths = {
        "root": root,
        "atompipe": dot,
        "ledger": ledger_path(root),
        "project": os.path.join(dot, PROJECT_NAME),
        "legacy_ledger": os.path.join(dot, LEGACY_LEDGER_NAME),
        "out": out_dir(root),
        "packs": os.path.join(dot, PACKS_NAME),
        "projection": os.path.join(dot, PROJECTION_NAME),
        "gitignore": os.path.join(dot, ".gitignore"),
        "inputs": inputs_dir(root),
        "inputs_readme": os.path.join(inputs_dir(root), "README.md"),
        "docs": docs_dir(root),
        "model": model_dir(root),
        "readiness": os.path.join(docs_dir(root), "readiness.md"),
        "decisions": os.path.join(docs_dir(root), "decisions.md"),
    }
    for bucket in INPUT_BUCKETS:
        paths[f"inputs_{bucket}"] = os.path.join(inputs_dir(root), bucket)
    return paths


# --------------------------------------------------------------------------- #
# records: shared pieces
# --------------------------------------------------------------------------- #
#: The one record kind that names itself by something other than `id`.
_PARAM_ID = "name"

#: Paths the migration and `init` write, relative to the root, in posix form.
_PROJECT_REL = f"{ATOMPIPE_DIR}/{PROJECT_NAME}"
_LEDGER_REL = f"{ATOMPIPE_DIR}/{LEDGER_NAME}"
_LEGACY_REL = f"{ATOMPIPE_DIR}/{LEGACY_LEDGER_NAME}"

#: The stat cache `verdicts` keeps for file digests; the index reads it (never
#: writes it) so a large CAD input is not re-hashed by every command.
_DIGESTS_NAME = "digests.json"

#: Characters a record id may not hold, because the id IS a file name: a path
#: separator would write outside the record directory, `:` is not a file-name
#: character on Windows, and a control character is invisible in every listing.
_BAD_ID = re.compile(r'[/\\:\x00-\x1f]')

#: Appended to a refusal of a top-level `inputs/*.json` (Q1.9). A stray evidence
#: file there is the likeliest cause of such a refusal, and "unknown key" alone
#: sends the user to fix a file that was never meant to be a record.
_INPUTS_HINT = (
    " — every top-level inputs/*.json is a record (one ingested artifact); if this "
    "file is evidence, move it into a bucket (inputs/data/, inputs/measurements/, …) "
    "and register it with `atompipe ingest`"
)


def _id_field(cls: type) -> str:
    return _PARAM_ID if cls is Param else "id"


def _kind_class(kind: str) -> type:
    """The record class for a record directory name; a bug if it is not one."""
    try:
        return RECORD_KINDS[kind]
    except KeyError:
        raise ValueError(f"not a record kind: {kind!r} (one of {', '.join(RECORD_DIRS)})") from None


def _natural(text: str) -> tuple:
    """Sort key: C2 before C10, and G2 before G10 — the order a person numbered
    them in, which is the order the legacy ledger kept. Ties break on the raw
    text, so the order is total and never depends on a directory listing."""
    parts = re.split(r"(\d+)", text)
    return tuple(int(p) if i % 2 else p for i, p in enumerate(parts)), text


def _check_id(rid: Any, label: str) -> str:
    """`rid` if it can name a file in a record directory, else refuse."""
    if (not isinstance(rid, str) or not rid.strip() or rid != rid.strip()
            or rid.startswith(".") or _BAD_ID.search(rid)):
        raise AtompipeError(
            f"{label}: {rid!r} cannot be a record id — the id is the file name, so it "
            f"must be non-empty, must not start with '.', and must not contain '/', "
            f"'\\', ':' or surrounding spaces")
    return rid


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _write_if_changed(path: str, data: bytes) -> bool:
    """Write `data` to `path` atomically unless the file already holds exactly it.

    Byte-for-byte, so a second migration, a second `init` step or an unchanged
    index touches nothing — not even an mtime a stat cache would notice."""
    try:
        if _read_bytes(path) == data:
            return False
    except OSError:
        pass
    atomic_write_text(path, data.decode("utf-8"))
    return True


def _dumps(obj: Any, label: str) -> bytes:
    """A record file's bytes: indent 2, UTF-8 as itself, strict JSON, one newline.

    `ensure_ascii=False` because a human edits these: `Ø 5 mm` must read as
    itself, not `\\u00d8`. No `sort_keys`: the writer's order is the dataclass
    order, which is the order a person reads a claim in (statement first)."""
    try:
        text = json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False)
    except ValueError as exc:
        raise AtompipeError(
            f"{label}: refusing to write NaN or Infinity; a number that could not be "
            f"measured is not a number ({exc})") from None
    except TypeError as exc:
        raise AtompipeError(f"{label}: not JSON ({exc})") from None
    return (text + "\n").encode("utf-8")


# --------------------------------------------------------------------------- #
# the writer
# --------------------------------------------------------------------------- #
def _at_default(f: dataclasses.Field, value: Any) -> bool:
    if f.default is not dataclasses.MISSING:
        return value == f.default
    if f.default_factory is not dataclasses.MISSING:          # type: ignore[misc]
        default = f.default_factory()                          # type: ignore[misc]
        return value == default or (isinstance(default, list)
                                    and isinstance(value, tuple) and not value)
    return False                                               # required: always written


def _encode_value(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _encode(value)
    if isinstance(value, dict):
        return {k: _encode_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_value(v) for v in value]
    return value


def _encode(obj: Any, *, drop: str | None = None) -> dict[str, Any]:
    """`obj` as its record file holds it: dataclass field order, `drop` (the
    id: the stem is the id) omitted, every field at its default omitted except
    `ALWAYS_WRITTEN`, every forbidden key omitted. A nested record equal to its
    whole default is omitted entirely, because it is a field at its default.

    Omitting defaults is what makes a record say only what was decided, and it
    is also what makes the bytes stable: a spine that adds a field with a
    default does not rewrite every record on disk (R-2)."""
    name = type(obj).__name__
    forbidden = FORBIDDEN_KEYS.get(name, {})
    out: dict[str, Any] = {}
    for f in dataclasses.fields(obj):
        if f.name == drop or f.name in forbidden:
            continue
        value = getattr(obj, f.name)
        if (name, f.name) not in ALWAYS_WRITTEN and _at_default(f, value):
            continue
        out[f.name] = _encode_value(value)
    return out


def _record_bytes(kind: str, record: Any, label: str) -> bytes:
    if kind == "results":
        return _dumps({"results": [_encode(r) for r in record]}, label)
    return _dumps(_encode(record, drop=_id_field(type(record))), label)


def _refuse_case_variant(directory: str, rid: str, label: str) -> None:
    """Two ids that differ only in case are ONE file on a case-insensitive
    filesystem (macOS, Windows): writing the second would overwrite the first
    there and create a sibling here, and the two checkouts would disagree about
    how many claims the project has."""
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if (name.endswith(".json") and name[:-5] != rid
                and name[:-5].casefold() == rid.casefold()):
            raise AtompipeError(
                f"{label}: {os.path.basename(directory)}/{name} already holds that id in "
                f"another case — two ids differing only in case are one file on a "
                f"case-insensitive filesystem; use {name[:-5]!r} or another id")


def write_record(root: str, kind: str, record: Any, *, record_id: str | None = None
                 ) -> str | None:
    """Write one record to `<root>/<kind>/<id>.json`; the path, or None if unchanged.

    `kind` is a record directory (`RECORD_DIRS`). The bytes are the dataclass
    fields in order, the id omitted (the stem is the id), every default omitted
    but `ALWAYS_WRITTEN`, every `FORBIDDEN_KEYS` key omitted, indent 2, UTF-8,
    one trailing newline — so the same record always writes the same bytes, and
    the file is rewritten (atomically) only when they differ.

    `results` is the one kind whose record is a LIST: pass the claim's
    `PhysicalResult`s, oldest first, with `record_id=<claim id>`; the file is
    `{"results": [...]}`. Every other kind takes its record and reads the id off
    it (`name` for a `Param`).
    """
    cls = _kind_class(kind)
    if kind == "results":
        if record_id is None:
            raise ValueError("write_record(root, 'results', ...) needs record_id=<the claim id>")
        record = list(record)
        for item in record:
            if not isinstance(item, PhysicalResult):
                raise TypeError(f"results hold PhysicalResult, not {type(item).__name__}")
        rid = record_id
    else:
        if not isinstance(record, cls):
            raise TypeError(f"write_record({kind!r}) takes a {cls.__name__}, "
                            f"not {type(record).__name__}")
        rid = getattr(record, _id_field(cls))
        if record_id is not None and record_id != rid:
            raise ValueError(f"record_id {record_id!r} is not the record's own id {rid!r}")
    label = f"{kind}/{rid}.json" if isinstance(rid, str) else kind
    _check_id(rid, label)
    directory = os.path.join(root, kind)
    _refuse_case_variant(directory, rid, label)
    path = os.path.join(directory, rid + ".json")
    return path if _write_if_changed(path, _record_bytes(kind, record, label)) else None


# --------------------------------------------------------------------------- #
# the strict reader
# --------------------------------------------------------------------------- #
class _NonFinite:
    """A NaN or ±Infinity the JSON held, kept as a marker until its path is known,
    so the refusal can say WHICH value could not be measured."""

    __slots__ = ("token",)

    def __init__(self, token: str) -> None:
        self.token = token


def _loads_strict(text: str, label: str) -> Any:
    """`json.loads` that refuses a key written twice in one object — json keeps
    the LAST silently, so whichever copy a human edited might be the one lost —
    and marks every non-finite number (including a float literal like 1e999
    that overflows to infinity)."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise AtompipeError(
                    f'{label}: the key "{key}" appears twice in one object — JSON keeps '
                    f"only the last one silently; keep the one you mean and delete the other")
            out[key] = value
        return out

    def number(token: str) -> Any:
        value = float(token)
        return value if math.isfinite(value) else _NonFinite(token)

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=_NonFinite,
                          parse_float=number)
    except json.JSONDecodeError as exc:
        raise AtompipeError(f"{label}: not valid JSON ({exc.msg} at line {exc.lineno} "
                            f"column {exc.colno})") from None


def _join(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else str(key)


def _find_nonfinite(value: Any, path: str = "") -> tuple[str, str] | None:
    if isinstance(value, _NonFinite):
        return path, value.token
    if isinstance(value, dict):
        items: Any = value.items()
    elif isinstance(value, list):
        items = enumerate(value)
    else:
        return None
    for key, item in items:
        found = _find_nonfinite(item, _join(path, key))
        if found is not None:
            return found
    return None


def _refuse_nonfinite(value: Any, label: str, prefix: str = "") -> None:
    found = _find_nonfinite(value)
    if found is not None:
        where, token = found
        raise AtompipeError(
            f"{label}: {prefix}{where or 'the value'} is {token} — a number that could not "
            f"be measured is not a number; write a finite value, or null")


@functools.lru_cache(maxsize=None)
def _nested_fields(cls: type) -> dict[str, tuple[type | None, bool]]:
    """Field name -> `(record class it nests, is a list of them)`, read off the
    annotations, so the reader follows `models.py` and never keeps its own copy
    of which field holds which record (rule 2)."""
    hints = typing.get_type_hints(cls)
    return {f.name: _nested_type(hints.get(f.name)) for f in dataclasses.fields(cls)}


def _nested_type(tp: Any) -> tuple[type | None, bool]:
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        return tp, False
    origin = typing.get_origin(tp)
    if origin is list:
        args = typing.get_args(tp)
        if args and isinstance(args[0], type) and dataclasses.is_dataclass(args[0]):
            return args[0], True
        return None, False
    if origin is typing.Union or origin is getattr(types, "UnionType", None):
        for arg in typing.get_args(tp):
            if arg is not type(None):
                found = _nested_type(arg)
                if found[0] is not None:
                    return found
    return None, False


def _refuse_anywhere(value: Any, label: str, where: str, prefix: str) -> None:
    """The `FORBIDDEN_KEYS["*"]` keys at any depth of a free-form value (a
    view's `data`, a result's `evidence`)."""
    if isinstance(value, dict):
        for key, item in value.items():
            here = _join(where, key)
            if key in FORBIDDEN_KEYS["*"]:
                raise AtompipeError(
                    f'{label}: {prefix}"{key}" at {here} — {key} is '
                    f'{FORBIDDEN_KEYS["*"][key]}; delete the key')
            _refuse_anywhere(item, label, here, prefix)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _refuse_anywhere(item, label, _join(where, i), prefix)


def _check_keys(data: dict, cls: type, label: str, path: str = "", *, legacy: bool = False,
                model_entry: str = "", prefix: str = "", hint: str = "") -> None:
    """Refuse every key of `data` (a `cls` record, at `path`) the reader does not
    know, recursing into nested records.

    `legacy` reads a legacy ledger's record, where the derived fields were fields
    (`Claim.gates`, `Param.value`, …) and are dropped by the migration rather
    than refused. `prefix` names the record inside a file that holds several."""
    names = [f.name for f in dataclasses.fields(cls)]
    forbidden = {} if legacy else FORBIDDEN_KEYS.get(cls.__name__, {})
    nested = _nested_fields(cls)
    for key, value in data.items():
        where = _join(path, key)
        at = "" if where == key else f" at {where}"
        if key in FORBIDDEN_KEYS["*"]:
            raise AtompipeError(
                f'{label}: {prefix}"{key}"{at} — {key} is {FORBIDDEN_KEYS["*"][key]}; '
                f"delete the key")
        if key in forbidden:
            owner = forbidden[key].format(model=model_entry or "meta.model_entry")
            raise AtompipeError(
                f'{label}: {prefix}"{key}"{at} does not belong in this record — {owner}; '
                f"delete the key")
        if key not in names:
            allowed = [n for n in names if n not in forbidden]
            close = difflib.get_close_matches(key, allowed, n=1, cutoff=0.6)
            guess = f' (did you mean "{close[0]}"?)' if close else ""
            raise AtompipeError(
                f'{label}: {prefix}unknown key "{key}"{at}{guess} — a key the reader does '
                f"not know is refused, never dropped: a dropped key is erased by the next "
                f"write (S-40){hint}")
        sub, many = nested.get(key, (None, False))
        if sub is None:
            _refuse_anywhere(value, label, where, prefix)
            continue
        if value is None:
            continue
        if many:
            if not isinstance(value, list):
                raise AtompipeError(f"{label}: {prefix}{where} must be a list of objects")
            for i, item in enumerate(value):
                if not isinstance(item, dict):
                    raise AtompipeError(f"{label}: {prefix}{_join(where, i)} must be an object")
                _check_keys(item, sub, label, _join(where, i), legacy=legacy,
                            model_entry=model_entry, prefix=prefix,
                            hint=hint if legacy else "")
        else:
            if not isinstance(value, dict):
                raise AtompipeError(f"{label}: {prefix}{where} must be an object")
            _check_keys(value, sub, label, where, legacy=legacy, model_entry=model_entry,
                        prefix=prefix, hint=hint if legacy else "")


def _parse_record(label: str, kind: str, stem: str, raw: bytes, *, model_entry: str = ""
                  ) -> Any:
    """The strict reader, over bytes: `read_record`'s body, and the migration's
    read-back of its own plan (so a plan and the files it writes cannot load
    differently)."""
    cls = _kind_class(kind)
    hint = _INPUTS_HINT if kind == "inputs" else ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AtompipeError(f"{label}: not UTF-8 text ({exc.reason}){hint}") from None
    if not text.strip():
        raise AtompipeError(f"{label}: the file is empty — a record is a JSON object; "
                            f"restore it from git, or delete the file")
    data = _loads_strict(text, label)
    _refuse_nonfinite(data, label)
    if not isinstance(data, dict):
        raise AtompipeError(f"{label}: expected a JSON object, found "
                            f"{type(data).__name__}{hint}")
    _check_id(stem, label)

    if kind == "results":
        for key in data:
            if key in FORBIDDEN_KEYS["*"]:
                _refuse_anywhere(data, label, "", "")
            if key != "results":
                raise AtompipeError(
                    f'{label}: unknown key "{key}" (did you mean "results"?) — a results '
                    f'file is {{"results": [...]}}, oldest first')
        items = data.get("results")
        if not isinstance(items, list):
            raise AtompipeError(f'{label}: expected {{"results": [...]}} — "results" must '
                                f"be a list of physical results")
        out: list[PhysicalResult] = []
        for i, item in enumerate(items):
            where = _join("results", i)
            if not isinstance(item, dict):
                raise AtompipeError(f"{label}: {where} must be an object")
            _check_keys(item, PhysicalResult, label, where)
            if not isinstance(item.get("passed"), bool):
                raise AtompipeError(
                    f'{label}: {where}.passed must be true or false — a result nobody wrote '
                    f"as a bool is not a pass")
            out.append(PhysicalResult.from_dict(item))
        return out

    _check_keys(data, cls, label, model_entry=model_entry, hint=hint)
    idf = _id_field(cls)
    if idf in data and data[idf] != stem:
        raise AtompipeError(
            f'{label}: "{idf}" says {data[idf]!r} but the file is named for {stem!r} — the '
            f"file name is the {idf}; rename the file, or delete the key")
    body = dict(data)
    body[idf] = stem
    # A field a record file may never hold but the dataclass requires (`Param.value`:
    # the model owns it) reads as None — unknown here, filled by whoever owns it.
    for f in dataclasses.fields(cls):
        if (f.name in FORBIDDEN_KEYS.get(cls.__name__, {}) and f.default is dataclasses.MISSING
                and f.default_factory is dataclasses.MISSING):          # type: ignore[misc]
            body[f.name] = None
    try:
        return cls.from_dict(body)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise AtompipeError(f"{label}: not a valid {kind} record ({exc})") from None


def _label(path: str) -> str:
    """`claims/C1.json`: the record's name inside the project, which is what a
    person looks for — never a temp-dir path three screens wide."""
    full = os.path.abspath(path)
    return f"{os.path.basename(os.path.dirname(full))}/{os.path.basename(full)}"


def read_record(path: str, kind: str, *, model_entry: str = "") -> Any:
    """Read one record file STRICTLY; the record (for `results`, its list).

    Refused, each with a message naming the file, the key and — where there is
    one — the suggestion: an unknown key at any depth (`difflib`); a key written
    twice; NaN or ±Infinity; an empty file; anything but an object; an `id`
    (`name` for a param) that disagrees with the file's stem; a `FORBIDDEN_KEYS`
    key, naming the home that owns the fact (`model_entry` fills the model's
    name); `independence` anywhere. A top-level `inputs/*.json` that fails is
    most likely stray evidence, and the message says where evidence goes.

    `Record.from_dict` stays lenient; this is the reader for FILES a human edits,
    where a dropped key is a lost fact (S-40).
    """
    stem = os.path.basename(path)
    stem = stem[:-5] if stem.endswith(".json") else stem
    label = _label(path)
    try:
        raw = _read_bytes(path)
    except OSError as exc:
        raise AtompipeError(f"{label}: cannot be read ({exc.strerror or exc})") from None
    return _parse_record(label, kind, stem, raw, model_entry=model_entry)


def _record_files(root: str, kind: str) -> list[tuple[str, str]]:
    """`(stem, path)` for every record file of `kind`, in natural order.

    A record file is a top-level `<stem>.json` that is not a dot-file (an
    editor's swap file, `atomic_write_text`'s temp file). `views/*.py` are
    viewgens and never records; `inputs/<bucket>/` holds evidence bytes. Two
    stems differing only in case are refused, naming both (see
    `_refuse_case_variant`)."""
    directory = os.path.join(root, kind)
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return []
    except NotADirectoryError:
        raise AtompipeError(f"{kind}: expected a directory of {kind} records, found a "
                            f"file") from None
    stems = [name[:-5] for name in names
             if name.endswith(".json") and not name.startswith(".")
             and os.path.isfile(os.path.join(directory, name))]
    seen: dict[str, str] = {}
    for stem in sorted(stems):
        key = stem.casefold()
        if key in seen:
            raise AtompipeError(
                f"{kind}/{seen[key]}.json and {kind}/{stem}.json name ids that differ only "
                f"in case — they are one file on a case-insensitive filesystem; rename one")
        seen[key] = stem
    return [(stem, os.path.join(directory, stem + ".json"))
            for stem in sorted(stems, key=_natural)]


def _input_size(root: str, path: str) -> int:
    """`InputArtifact.bytes`, computed from the file: its size now, 0 if absent."""
    if not path:
        return 0
    full = path if os.path.isabs(path) else os.path.join(root, *path.split("/"))
    try:
        return os.path.getsize(full) if os.path.isfile(full) else 0
    except OSError:
        return 0


def _assemble(root: str, meta: ProjectMeta, parsed: dict[str, list[tuple[str, Any]]]
              ) -> Ledger:
    """The in-memory Ledger from parsed records: each kind in natural id order,
    decisions newest first, each claim's `physical_result` the LAST of its
    results, each input's `bytes` measured, `verdicts` empty (they live in the
    verdict cache). One assembler for files on disk and for a migration's plan,
    so the two cannot disagree about what a record means."""
    ordered = {kind: [rec for _stem, rec in sorted(parsed.get(kind, []),
                                                    key=lambda pair: _natural(pair[0]))]
               for kind in RECORD_DIRS}
    results = dict(parsed.get("results", []))
    claims = ordered["claims"]
    for claim in claims:
        items = results.get(claim.id) or []
        claim.physical_result = items[-1] if items else None
    for artifact in ordered["inputs"]:
        artifact.bytes = _input_size(root, artifact.path)
    # Newest first, as `decisions.render_log` prints storage order and `add` used
    # to prepend. The legacy ledger's order was the only order; a directory has
    # none, so `when` gives it, then the id. Rejected: file-name order (a log
    # sorted by slug is in no order anyone reads).
    decisions = sorted(ordered["decisions"], key=lambda d: _natural(d.id))
    decisions.sort(key=lambda d: d.when or "", reverse=True)
    return Ledger(meta=meta, claims=claims, params=ordered["params"],
                  inputs=ordered["inputs"], needs=ordered["needs"], decisions=decisions,
                  verdicts=[], views=ordered["views"])


# --------------------------------------------------------------------------- #
# project.json
# --------------------------------------------------------------------------- #
def _project_path(root: str) -> str:
    return os.path.join(atompipe_dir(root), PROJECT_NAME)


def _project_bytes(meta: ProjectMeta) -> bytes:
    """`{"schema": 2, ...every ProjectMeta field...}`. Every field, defaults too:
    it is one small file whose `model_entry` a human looks for, and an absent key
    there reads as a project with no model."""
    return _dumps({"schema": PROJECT_SCHEMA, **meta.to_dict()}, _PROJECT_REL)


def _parse_project(raw: bytes, label: str) -> ProjectMeta:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AtompipeError(f"{label}: not UTF-8 text ({exc.reason})") from None
    if not text.strip():
        raise AtompipeError(f"{label}: the file is empty — restore it from git")
    data = _loads_strict(text, label)
    _refuse_nonfinite(data, label)
    if not isinstance(data, dict):
        raise AtompipeError(f"{label}: expected a JSON object, found {type(data).__name__}")
    if "schema" not in data:
        raise AtompipeError(f'{label}: no "schema" — add "schema": {PROJECT_SCHEMA}')
    schema = data["schema"]
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise AtompipeError(f'{label}: "schema" must be an integer, not {schema!r}')
    if schema > PROJECT_SCHEMA:
        raise AtompipeError(
            f"{label}: schema {schema} is newer than this atompipe reads (schema "
            f"{PROJECT_SCHEMA}) — upgrade atompipe; a layout read half-understood loses "
            f"whatever this version does not know")
    if schema != PROJECT_SCHEMA:
        raise AtompipeError(f"{label}: schema {schema} is not a records layout this "
                            f"atompipe knows (it reads schema {PROJECT_SCHEMA})")
    body = {k: v for k, v in data.items() if k != "schema"}
    _check_keys(body, ProjectMeta, label)
    try:
        return ProjectMeta.from_dict(body)
    except (TypeError, ValueError) as exc:
        raise AtompipeError(f"{label}: not a valid project file ({exc})") from None


def read_project(root: str) -> ProjectMeta:
    """`.atompipe/project.json` as a `ProjectMeta`, read strictly.

    Refuses a missing `schema`, a schema newer than `PROJECT_SCHEMA` (a newer
    layout read by an older spine would be read half-understood), and every key
    `read_record` would refuse."""
    path = _project_path(root)
    try:
        raw = _read_bytes(path)
    except OSError as exc:
        raise AtompipeError(f"{_PROJECT_REL}: cannot be read ({exc.strerror or exc})") from None
    return _parse_project(raw, _PROJECT_REL)


def write_project(root: str, meta: ProjectMeta) -> str | None:
    """Write `.atompipe/project.json`; the path, or None when the bytes are unchanged."""
    path = _project_path(root)
    return path if _write_if_changed(path, _project_bytes(meta)) else None


def is_legacy(root: str) -> bool:
    """True when the project keeps its records in a legacy `.atompipe/ledger.json`:
    that file exists and `.atompipe/project.json` does not. `project.json` is
    written LAST by the migration, so a migration that crashed half way is still
    legacy — and `migrate_legacy` completes it."""
    dot = atompipe_dir(root)
    return (not os.path.isfile(os.path.join(dot, PROJECT_NAME))
            and os.path.isfile(os.path.join(dot, LEDGER_NAME)))


# --------------------------------------------------------------------------- #
# load and save
# --------------------------------------------------------------------------- #
def _load_records(root: str) -> Ledger:
    meta = read_project(root)
    parsed = {kind: [(stem, read_record(path, kind, model_entry=meta.model_entry))
                     for stem, path in _record_files(root, kind)]
              for kind in RECORD_DIRS}
    return _assemble(root, meta, parsed)


def load(root: str, *,
         model_prose: Callable[[str, str], Any] | None = None) -> Ledger:
    """The project, in memory: its record files, or its legacy ledger migrated IN
    MEMORY (nothing is written). `verdicts` is always empty: they live in the
    verdict cache, and `verdicts.resolve` is what reads them.

    A project with neither `project.json` nor a legacy ledger is an empty
    project, not an error. A record that does not read — a typo'd key, a NaN, a
    half-migrated file that disagrees with its legacy ledger — IS an error and
    is never swallowed: an empty Ledger for a broken file would look like it
    worked, and the next save would write that emptiness over the project.
    `.atompipe/ledger.json` on a records project is the generated index and is
    never read here.

    `model_prose` is `migrate_legacy`'s, and matters only on a legacy project:
    the in-memory plan is the migration's, and the migration's plan depends on
    what the model states (the params rule). A caller that will migrate, or
    whose project `check` will, passes the reader `check` migrates with — the
    spine passes `modelio.static_param_prose` at every call, and
    `tests/test_records.EveryReaderMigratesAsCheckDoes` walks the spine's AST
    for a call that does not. What slipped through: this called
    `migrate_legacy` with none (lossless: every param keeps its rationale)
    while `check` passed the static reader, so the two planned different
    files from the same bytes — on a model stating `D` and `d`, every read
    command refused "params/D.json and params/d.json … rename one" while
    `check` wrote neither file; after a killed migration, every read command
    blamed a `params/thickness.json` byte for byte what `check` would write.
    *Rejected:* a default reader registered by `modelio` at import (the plan
    would depend on which module a process happened to import first); a
    meta-only fast path (the refusals are the point: a reader must refuse
    exactly where `check` refuses, and nowhere else).
    """
    if os.path.isfile(_project_path(root)):
        return _load_records(root)
    if os.path.isfile(ledger_path(root)):
        return migrate_legacy(root, apply=False, when="", model_prose=model_prose).ledger
    return Ledger()


def _param_body(param: Param) -> dict[str, Any]:
    return _encode(param, drop=_PARAM_ID)


def save(root: str, ledger: Ledger) -> None:
    """Write a whole Ledger in the layout the project is in. For tests and the
    migration's callers: from checkpoint 1.3 no command writes a whole ledger
    (a command writes exactly the one record it was asked to, spec §3.15).

    * **A records project** (`project.json` present): `project.json`, then one
      file per record — a record no longer in `ledger` has its file removed, a
      param with nothing to hold writes none — then the index, so it never
      disagrees with what was just written. A claim's `physical_result` is
      APPENDED to `results/<id>.json` when it is not already the last result
      there, and a result is never removed: results are append-only (D-11), and a
      whole-ledger save that truncated them would lose a refutation.
    * **A legacy project**: the legacy `ledger.json`, with `verdicts` empty (they
      live in the verdict cache, PD-31) and no `last_run`. It does not migrate:
      migrating is the CLI's decision, made once, under the lock.

    Refuses to write into a directory that was never `init`-ed, because the
    failure mode is silent: a stray `.atompipe/` one level up from where the user
    thinks they are.
    """
    dot = atompipe_dir(root)
    if not os.path.isdir(dot):
        raise AtompipeError(
            f"no {ATOMPIPE_DIR}/ directory at {os.path.abspath(root)} — "
            f"run `atompipe init` before saving"
        )
    if not os.path.isfile(_project_path(root)):
        atomic_write_json(ledger_path(root), dataclasses.replace(ledger, verdicts=[]).to_dict())
        return

    write_project(root, ledger.meta)
    for kind, records in (("claims", ledger.claims), ("params", ledger.params),
                          ("decisions", ledger.decisions), ("needs", ledger.needs),
                          ("inputs", ledger.inputs), ("views", ledger.views)):
        keep: set[str] = set()
        for record in records:
            if kind == "params" and not _param_body(record):
                continue
            write_record(root, kind, record)
            keep.add(getattr(record, _id_field(type(record))))
        for stem, path in _record_files(root, kind):
            if stem not in keep:
                os.remove(path)
    for claim in ledger.claims:
        if claim.physical_result is None:
            continue
        path = os.path.join(root, "results", claim.id + ".json")
        existing = read_record(path, "results") if os.path.isfile(path) else []
        if not existing or existing[-1] != claim.physical_result:
            write_record(root, "results", [*existing, claim.physical_result],
                         record_id=claim.id)
    write_index(root)


# --------------------------------------------------------------------------- #
# the index: .atompipe/ledger.json, an output
# --------------------------------------------------------------------------- #
def _walk_files(base: str, rel: str, _seen: frozenset[str] = frozenset()) -> list[str]:
    """Posix paths (relative to the project root) of every file under `base`,
    sorted, skipping dot-entries and `__pycache__`. `os.listdir` and a sort, not
    `os.walk`: the order must never be the filesystem's. A directory symlinked
    back into its own ancestry is walked once, not forever."""
    out: list[str] = []
    real = os.path.realpath(base)
    if real in _seen:
        return out
    seen = _seen | {real}
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        if name.startswith(".") or name == "__pycache__":
            continue
        path = os.path.join(base, name)
        here = f"{rel}/{name}"
        if os.path.isdir(path):
            out.extend(_walk_files(path, here, seen))
        elif os.path.isfile(path):
            out.append(here)
    return out


def _posix_rel(root: str, path: str) -> str:
    if os.path.isabs(path):
        try:
            path = os.path.relpath(path, root)
        except ValueError:                         # another drive: not in the project
            return path.replace("\\", "/")
    path = path.replace("\\", "/")
    return path[2:] if path.startswith("./") else path


def records_digest(root: str) -> str:
    """sha256 over every record file's path and bytes, plus `project.json` — on a
    legacy project, over its `ledger.json`. It moves when a record moves and
    never when the index is rewritten; the site compares it to say "the page was
    built from other records" (mtimes said so for a ledger nobody changed)."""
    entries: list[tuple[str, str]] = []
    if os.path.isfile(_project_path(root)):
        entries.append((_PROJECT_REL, _project_path(root)))
        for kind in RECORD_DIRS:
            entries.extend((f"{kind}/{stem}.json", path) for stem, path in _record_files(root, kind))
    elif os.path.isfile(ledger_path(root)):
        entries.append((_LEDGER_REL, ledger_path(root)))
    digest = hashlib.sha256(b"atompipe-records-v1\x00")
    for rel, path in sorted(entries):
        digest.update(rel.encode("utf-8") + b"\x00"
                      + hashlib.sha256(_read_bytes(path)).hexdigest().encode("ascii") + b"\n")
    return digest.hexdigest()


def _row(record: Any) -> dict[str, Any]:
    idf = _id_field(type(record))
    return {idf: getattr(record, idf), **_encode(record, drop=idf)}


def build_index(root: str, *, digests: FileDigests | None = None) -> dict[str, Any]:
    """The index, `.atompipe/ledger.json`'s content: a PURE function of the record
    files and of the bytes of the inputs they name. No clock, no model, no
    registry, no listing order.

    Keys, in this order: `generated` (`INDEX_BANNER`), `schema`,
    `records_digest`, `meta`, `claims`, `params`, `decisions`, `needs`, `inputs`,
    `results`, `views`, `unregistered_inputs`, `problems`. Each record row is its
    file's content with its id; each input row adds `sha256` (the digest of the
    bytes NOW), `pinned` (the record's sha256), `drift` (the two differ) and
    `exists` (null for an input with no path). `unregistered_inputs` are evidence
    bytes under `inputs/` that no record names — dropped by hand, never ingested.
    `problems` are missing bytes, drift and dangling links.

    It never holds a status, a coverage or a verdict: those depend on the
    registry and the verdict cache, live in `.atompipe/cache/last_check.json`,
    and an index that carried them could disagree with them. What slipped
    through without the computed digest (S-45): evidence was hashed at ingest and
    never again, so tampered bytes still read as unchanged.
    """
    if not os.path.isfile(_project_path(root)):
        raise AtompipeError(
            f"{os.path.abspath(root)} has no {_PROJECT_REL} — a legacy project has no "
            f"index; its {_LEDGER_REL} is still the records until it migrates")
    meta = read_project(root)
    parsed = {kind: [(stem, read_record(path, kind, model_entry=meta.model_entry))
                     for stem, path in _record_files(root, kind)]
              for kind in RECORD_DIRS}
    results = sorted(parsed["results"], key=lambda pair: _natural(pair[0]))
    ledger = _assemble(root, meta, parsed)
    if digests is None:
        digests = FileDigests(os.path.join(atompipe_dir(root), CACHE_NAME, _DIGESTS_NAME))

    problems: list[str] = []
    inputs: list[dict[str, Any]] = []
    named: set[str] = set()
    for artifact in ledger.inputs:
        row = _row(artifact)
        label = f"inputs/{artifact.id}.json"
        pinned = artifact.sha256 or ""
        if artifact.path:
            rel = _posix_rel(root, artifact.path)
            named.add(rel)
            full = os.path.join(root, *rel.split("/")) if not os.path.isabs(rel) else rel
            computed = digests.digest(full)
            exists: bool | None = computed is not None
            drift = bool(pinned) and computed is not None and computed != pinned
            if computed is None:
                problems.append(f"{label}: {rel} is missing — the record names bytes that "
                                f"are not there")
            elif drift:
                problems.append(f"{label}: {rel} changed since it was ingested (pinned "
                                f"{pinned[:12]}, now {computed[:12]}) — re-ingest it if the "
                                f"change is meant, restore it if not")
        else:
            computed, exists, drift = None, None, False
        row["sha256"] = computed
        row["pinned"] = pinned
        row["drift"] = drift
        row["exists"] = exists
        inputs.append(row)

    claim_ids = {c.id for c in ledger.claims}
    input_ids = {a.id for a in ledger.inputs}
    for claim in ledger.claims:
        for aid in claim.grounded_by:
            if aid not in input_ids:
                problems.append(f"claims/{claim.id}.json: grounded_by names {aid!r}, which "
                                f"has no inputs/{aid}.json")
    for param in ledger.params:
        for aid in param.grounded_by:
            if aid not in input_ids:
                problems.append(f"params/{param.name}.json: grounded_by names {aid!r}, which "
                                f"has no inputs/{aid}.json")
    for need in ledger.needs:
        for cid in need.claim_ids:
            if cid not in claim_ids:
                problems.append(f"needs/{need.id}.json: names claim {cid!r}, which has no "
                                f"claims/{cid}.json")
    for decision in ledger.decisions:
        for cid in decision.claims_changed:
            if cid not in claim_ids:
                problems.append(f"decisions/{decision.id}.json: claims_changed names {cid!r}, "
                                f"which has no claims/{cid}.json")
    for cid, _items in results:
        if cid not in claim_ids:
            problems.append(f"results/{cid}.json: no claims/{cid}.json holds that claim")

    unregistered = [rel for rel in _walk_files(inputs_dir(root), INPUTS_NAME)
                    if rel != f"{INPUTS_NAME}/README.md"
                    and not (rel.count("/") == 1 and rel.endswith(".json"))
                    and rel not in named]

    return {
        "generated": INDEX_BANNER,
        "schema": PROJECT_SCHEMA,
        "records_digest": records_digest(root),
        "meta": meta.to_dict(),
        "claims": [_row(c) for c in ledger.claims],
        "params": [_row(p) for p in ledger.params],
        "decisions": [_row(d) for d in ledger.decisions],
        "needs": [_row(n) for n in ledger.needs],
        "inputs": inputs,
        "results": {cid: [_encode(r) for r in items] for cid, items in results},
        "views": [_row(v) for v in ledger.views],
        "unregistered_inputs": unregistered,
        "problems": sorted(problems),
    }


def write_index(root: str) -> bool:
    """Rebuild `.atompipe/ledger.json`; True when its bytes changed.

    Only on a records project: on a legacy one `ledger.json` IS the records, and
    writing an index there would destroy them. Best-effort on the filesystem —
    a read-only checkout warns and returns False, and the records stay the
    truth — but a record that does not read is an error, as it is everywhere."""
    if not os.path.isfile(_project_path(root)):
        return False
    data = _dumps(build_index(root), _LEDGER_REL)
    path = ledger_path(root)
    try:
        if _read_bytes(path) == data:
            return False
    except OSError:
        pass
    try:
        atomic_write_text(path, data.decode("utf-8"))
    except OSError as exc:
        sys.stderr.write(f"atompipe: warning: could not write the index {_LEDGER_REL} "
                         f"({exc.strerror or exc}); the records are unchanged and are "
                         f"still the truth\n")
        return False
    return True


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 60 else text[:57] + "..."


def _keyed(section: Any, idf: str) -> dict[str, Any] | None:
    if not isinstance(section, list) or not all(isinstance(r, dict) for r in section):
        return None
    return {str(row.get(idf)): row for row in section}


def _diff(old: Any, new: Any, where: str, out: list[str]) -> None:
    if isinstance(old, dict) and isinstance(new, dict):
        for key in list(new) + [k for k in old if k not in new]:
            here = _join(where, key)
            if key not in old:
                out.append(f"{here}: in the records, missing from the index")
            elif key not in new:
                out.append(f"{here}: in the index, not in the records")
            else:
                _diff(old[key], new[key], here, out)
    elif old != new or isinstance(old, bool) != isinstance(new, bool):
        out.append(f"{where}: the index says {_short(old)}, the records say {_short(new)}")


def agree(root: str) -> list[str]:
    """Every way `.atompipe/ledger.json` disagrees with the records; [] when it
    agrees, or when there is no index to disagree (a legacy project, whose
    `ledger.json` is the records; a project no command has indexed yet).

    Record sections compare by id, so `claims.C1.acceptance.limit` names the
    field, and an injected `C99` reads as "in the index, not in the records".
    Invariant 8: the index never disagrees with the records — this is how a test
    (and `doctor`) asks."""
    if not os.path.isfile(_project_path(root)):
        return []
    path = ledger_path(root)
    try:
        raw = _read_bytes(path)
    except FileNotFoundError:
        return []
    except OSError as exc:
        return [f"{_LEDGER_REL}: cannot be read ({exc.strerror or exc})"]
    try:
        old = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return [f"{_LEDGER_REL}: not the index — not valid JSON ({exc})"]
    new = build_index(root)
    if not isinstance(old, dict):
        return [f"{_LEDGER_REL}: not the index — expected an object"]
    out: list[str] = []
    for key in list(new) + [k for k in old if k not in new]:
        if key not in old:
            out.append(f"{key}: in the records, missing from the index")
            continue
        if key not in new:
            out.append(f"{key}: in the index, not in the records")
            continue
        idf = _PARAM_ID if key == "params" else "id"
        if key in ("claims", "params", "decisions", "needs", "inputs", "views"):
            a, b = _keyed(old[key], idf), _keyed(new[key], idf)
            if a is not None and b is not None:
                _diff(a, b, key, out)
                continue
        _diff(old[key], new[key], key, out)
    return out


# --------------------------------------------------------------------------- #
# ignore and attribute blocks
# --------------------------------------------------------------------------- #
_BEGIN = "# atompipe:begin"
_END = "# atompipe:end"

#: `.atompipe/.gitignore`'s block: a DENY-list, never an allow-list —
#: `.atompipe/packs/` is source and `model.json` is reviewed, and a `*` then
#: `!` scheme would make every new kind of evidence arrive ignored. Why each line:
#: the index and the pre-migration ledger are outputs (D-06); `obs/` and `cache/`
#: are this checkout's cost and memory (S-76, cli:H2); `out/` is scratch;
#: `export/` is where P2.5's export writes; `runs/` is the run history that went
#: (a leftover one stays out of `git add -A`); `*.tmp`/`*.lock` are the atomic
#: writer's and the build lock's. `verdicts/` is evidence and stays tracked.
_ATOMPIPE_IGNORE_BLOCK = f"""\
{_BEGIN}
# Written by atompipe, which rewrites everything between these two markers.
# Outputs, not sources: ledger.json is the generated index of the records,
# ledger.legacy.json the ledger they migrated from, and the rest this checkout's
# scratch and memory. verdicts/ is evidence and stays tracked.
ledger.json
ledger.legacy.json
obs/
cache/
out/
export/
runs/
*.tmp
*.lock
{_END}
"""

#: The project root's `.gitignore` block. Importing `model/`, `gates/` and
#: `selftest/` writes bytecode beside them, and a project outside this repository
#: has no root ignore file to inherit one from.
_ROOT_IGNORE_BLOCK = f"""\
{_BEGIN}
# Written by atompipe: bytecode from importing model/, gates/ and selftest/.
__pycache__/
*.py[cod]
{_END}
"""

#: The project root's `.gitattributes` block. Every digest is over BYTES, so a
#: Windows clone with `core.autocrlf=true` would check every source out CRLF,
#: stale every verdict entry and dirty the tree; the binary kinds must never be
#: normalised at all. One pattern per line: gitattributes reads `a b -text` as
#: the pattern `a` with an attribute named `b`.
_ATTRIBUTES_BLOCK = f"""\
{_BEGIN}
# Written by atompipe: digests are over bytes, so line endings must not move.
* text=auto eol=lf
*.stl -text
*.step -text
*.glb -text
*.png -text
*.jpg -text
{_END}
"""

#: Lines outside `.atompipe/.gitignore`'s block that are removed with a notice:
#: each would re-add an output the block ignores — `!ledger.json` the index,
#: undoing D-06 on the next `git add -A`.
_ALLOW_LINES = ("!ledger.json", "!runs/")


def _block_patterns(block: str) -> set[str]:
    return {line.strip() for line in block.splitlines()
            if line.strip() and not line.lstrip().startswith("#")}


def _with_block(text: str, block: str, templates: tuple[str, ...],
                allow_lines: tuple[str, ...]) -> tuple[str, list[str]]:
    """`text` with `block` at the top and the user's lines after it; plus the
    allow lines it removed (for the notice).

    A text that BEGINS with a template loses that prefix. Any earlier block is
    removed wherever it sits (an unterminated one keeps its lines, which are then
    judged as the user's). User lines equal to a block pattern are dropped — the
    block holds them — and so are `allow_lines`. A paragraph left holding only
    comments after that explained only lines the block now owns, and goes with
    them (the bracket's appended `cache/`/`obs/` and the comment above them).
    Every other user line is kept, in order."""
    text = text.replace("\r\n", "\n")
    for template in templates:
        if text.startswith(template):
            text = text[len(template):]
            break
    lines = text.split("\n")
    user: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == _BEGIN:
            end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() == _END), None)
            if end is not None:
                i = end + 1
                continue
            i += 1                   # an unterminated block: its lines are judged as user lines
            continue
        user.append(lines[i])
        i += 1

    patterns = _block_patterns(block)
    removed: list[str] = []
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in user + [""]:
        if line.strip():
            current.append(line)        # as written: a gitignore's `\ ` keeps a trailing space
            continue
        if current:
            paragraphs.append(current)
            current = []
    kept: list[list[str]] = []
    for paragraph in paragraphs:
        survivors: list[str] = []
        dropped = False
        for line in paragraph:
            stripped = line.strip()
            if stripped in allow_lines:
                removed.append(stripped)
                dropped = True
            elif stripped in patterns:
                dropped = True
            else:
                survivors.append(line)
        if dropped and all(line.lstrip().startswith("#") for line in survivors):
            continue
        if survivors:
            kept.append(survivors)
    tail = "\n\n".join("\n".join(p) for p in kept)
    return block + ("\n" + tail + "\n" if tail else ""), removed


def ensure_ignore_blocks(root: str) -> list[str]:
    """Make sure the three marked blocks are in place; the files changed, as
    root-relative posix paths. Idempotent: a second call changes nothing.

    * `.atompipe/.gitignore`: the deny-list block. A file that begins with a
      `LEGACY_GITIGNORE_TEMPLATES` text has that prefix replaced by the block; a
      line elsewhere equal to `!ledger.json` or `!runs/` is removed with a notice
      on stderr (it would re-add the index, undoing D-06); a line duplicating the
      block goes; every other user line is kept, in order, after the block.
    * the project root's `.gitignore`: `__pycache__/`, `*.py[cod]`.
    * the project root's `.gitattributes`: `* text=auto eol=lf` and the binary
      kinds `-text`.

    What slipped through (S-76): `init` wrote an ignore file that allowed the
    ledger, and nothing ignored `cache/`, so a check in a clean clone dirtied
    `git status`; a hand-kept list there would have to be re-merged by every
    user on every upgrade, which is why the spine owns a marked block instead.
    """
    changed: list[str] = []
    for rel, block, templates, allow in (
            (f"{ATOMPIPE_DIR}/.gitignore", _ATOMPIPE_IGNORE_BLOCK,
             LEGACY_GITIGNORE_TEMPLATES, _ALLOW_LINES),
            (".gitignore", _ROOT_IGNORE_BLOCK, (), ()),
            (".gitattributes", _ATTRIBUTES_BLOCK, (), ())):
        path = os.path.join(root, *rel.split("/"))
        try:
            raw = _read_bytes(path)
        except FileNotFoundError:
            raw = b""
        except OSError as exc:
            raise AtompipeError(f"{rel}: cannot be read ({exc.strerror or exc})") from None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AtompipeError(f"{rel}: not UTF-8 text ({exc.reason})") from None
        new, removed = _with_block(text, block, templates, allow)
        for line in removed:
            sys.stderr.write(
                f"atompipe: removed `{line}` from {rel} — it would re-add an output the "
                f"block ignores ({line[1:]} is generated now, never a source)\n")
        if _write_if_changed(path, new.encode("utf-8")):
            changed.append(rel)
    return changed


# --------------------------------------------------------------------------- #
# the legacy migration
# --------------------------------------------------------------------------- #
class MigrationPlan(NamedTuple):
    """What `migrate_legacy` did, or would do.

    `ledger` is the project as the migrated records read (verdicts empty) — the
    same Ledger `load` returns before and after, so a command answers the same
    whether or not it could write. `files` maps each root-relative posix path
    the migration writes to its bytes, `.atompipe/project.json` included; empty
    for a project already migrated. `notice` is the one stderr paragraph the CLI
    prints ("" when there is nothing to say)."""

    ledger: Ledger
    files: dict[str, bytes]
    notice: str


#: The sections of a legacy ledger that hold records, with the kind each becomes
#: and the singular a refusal names a record by.
_LEGACY_SECTIONS: tuple[tuple[str, str, type], ...] = (
    ("claims", "claim", Claim),
    ("params", "param", Param),
    ("decisions", "decision", Decision),
    ("needs", "need", Need),
    ("inputs", "input", InputArtifact),
    ("views", "view", View),
)

#: Top-level legacy keys that are dropped whole, unread (D-09): verdicts live in
#: the cache now and are re-earned by the first check; `last_run` went with the
#: run history. Their CONTENTS are not checked — a misspelled key inside a record
#: that is dropped anyway loses nothing, and refusing a migration over it would
#: be friction with no fact to save.
_LEGACY_DROPPED = ("verdicts", "last_run")


def _read_legacy(root: str) -> dict[str, Any]:
    """The legacy ledger's JSON, every key checked before anything reads it."""
    label = _LEDGER_REL
    try:
        raw = _read_bytes(ledger_path(root))
    except OSError as exc:
        raise AtompipeError(f"{label}: cannot be read ({exc.strerror or exc})") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AtompipeError(f"{label}: not UTF-8 text ({exc.reason})") from None
    if not text.strip():
        raise AtompipeError(
            f"{label}: exists but is empty — refusing to treat it as a new project. "
            f"Restore it from git, or delete it if you truly want to start over.")
    data = _loads_strict(text, label)
    if not isinstance(data, dict):
        raise AtompipeError(f"{label}: expected a JSON object at the top level, got "
                            f"{type(data).__name__}")
    if "generated" in data:
        raise AtompipeError(
            f"{label} is the generated index, but {_PROJECT_REL} is missing — restore it "
            f"(`git checkout -- {_PROJECT_REL}`); the index is an output and is never "
            f"read back as the records")
    known = [f.name for f in dataclasses.fields(Ledger)] + list(_LEGACY_DROPPED)
    for key in data:
        if key in FORBIDDEN_KEYS["*"]:
            raise AtompipeError(f'{label}: "{key}" — {key} is {FORBIDDEN_KEYS["*"][key]}; '
                                f"delete the key — nothing was written")
        if key not in known:
            close = difflib.get_close_matches(key, known, n=1, cutoff=0.6)
            guess = f' (did you mean "{close[0]}"?)' if close else ""
            raise AtompipeError(
                f'{label}: unknown top-level key "{key}"{guess} — today\'s spine would '
                f"drop it on load and erase it on the next save (S-40); fix it in the "
                f"file and run the command again — nothing was written")
    for key in _LEGACY_DROPPED:
        if key in data and data[key] is not None and not isinstance(
                data[key], list if key == "verdicts" else dict):
            raise AtompipeError(f"{label}: {key} is not a {'list' if key == 'verdicts' else 'object'}")

    meta = data.get("meta")
    if meta is not None:
        if not isinstance(meta, dict):
            raise AtompipeError(f"{label}: meta must be an object")
        _refuse_nonfinite(meta, label, "meta.")
        _check_keys(meta, ProjectMeta, label, legacy=True, prefix="meta: ",
                    hint=" — fix it in the file and run the command again; nothing was written")
    for section, singular, cls in _LEGACY_SECTIONS:
        items = data.get(section)
        if items is None:
            continue
        if not isinstance(items, list):
            raise AtompipeError(f"{label}: {section} must be a list of records")
        idf = _id_field(cls)
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise AtompipeError(f"{label}: {section}[{i}] must be an object")
            name = item.get(idf)
            prefix = (f'{singular} "{name}": ' if isinstance(name, str) and name
                      else f"{section}[{i}]: ")
            _refuse_nonfinite(item, label, prefix)
            _check_keys(item, cls, label, legacy=True, prefix=prefix,
                        hint=" — fix it in the file and run the command again; nothing "
                             "was written")
    return data


def _ids_problems(ledger: Ledger) -> None:
    """Refuse ids that cannot be file names, and ids that repeat, before any file
    is planned: two claims named C1 would be one file, and which one survived
    would depend on the write order. Ids that differ only in CASE are refused
    once the files are planned (`_case_collisions`): a param `D` beside a param
    `d` is two model fields, and only matters if both hold something to write."""
    label = _LEDGER_REL
    for section, singular, cls in _LEGACY_SECTIONS:
        idf = _id_field(cls)
        seen: set[str] = set()
        for record in getattr(ledger, section):
            rid = getattr(record, idf)
            _check_id(rid, f"{label}: {singular} {rid!r}")
            if rid in seen:
                raise AtompipeError(
                    f"{label}: {section} names {rid!r} twice — one record per id, because "
                    f"the id is the file name; rename one and run the command again")
            seen.add(rid)


def _case_collisions(files: dict[str, bytes]) -> None:
    """Two planned files whose names differ only in case are one file on a
    case-insensitive filesystem; refuse before either is written."""
    seen: dict[str, str] = {}
    for rel in sorted(files):
        key = rel.casefold()
        if key in seen:
            raise AtompipeError(
                f"{_LEDGER_REL}: {seen[key]} and {rel} would name ids that differ only in "
                f"case — one file on a case-insensitive filesystem; rename one and run the "
                f"command again — nothing was written")
        seen[key] = rel


def _need_is_derived(need: Need, claims: dict[str, Claim]) -> bool:
    """True when `need` holds nothing `gap` would not derive again by itself: one
    claim, OPEN, no class, no candidates, nothing chosen, no note, and the
    quantity `gap` names (the claim's acceptance quantity, else its statement).

    Such a need is not a record (needs/ is SPARSE: only an enriched Need). This
    repeats `claims._gap_quantity`'s rule because the store cannot import
    `claims`; if the two ever drift, the need reads as enriched and is KEPT —
    the direction that loses nothing."""
    if (need.claim_class or need.candidates or need.chosen or need.note
            or need.status != NeedStatus.OPEN or len(need.claim_ids) != 1):
        return False
    claim = claims.get(need.claim_ids[0])
    if claim is None:
        return False
    derived = (claim.acceptance.quantity or "").strip() or (claim.statement or "").strip()
    return (need.quantity or "").strip() in ("", derived)


def _stated(root: str, entry: str, model_prose: Callable[[str, str], Any] | None
            ) -> dict[str, dict[str, str]]:
    """What the model states per param, or {} — lossless — when there is no
    reader, no entry, or the reader cannot parse it."""
    if model_prose is None or not entry:
        return {}
    try:
        stated = model_prose(root, entry)
    except (AtompipeError, OSError, SyntaxError, ValueError, UnicodeDecodeError):
        return {}
    return stated if isinstance(stated, dict) else {}


def _plan_files(root: str, data: dict[str, Any],
                model_prose: Callable[[str, str], Any] | None) -> dict[str, bytes]:
    """The record files a legacy ledger migrates to: a pure function of the
    checked legacy JSON and of what the model states."""
    label = _LEDGER_REL
    body = {k: v for k, v in data.items() if k not in _LEGACY_DROPPED}
    try:
        ledger = Ledger.from_dict(body)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise AtompipeError(f"{label}: not a valid atompipe ledger ({exc}) — nothing was "
                            f"written") from None
    _ids_problems(ledger)

    files: dict[str, bytes] = {}
    for claim in ledger.claims:
        files[f"claims/{claim.id}.json"] = _record_bytes("claims", claim, label)
        if claim.physical_result is not None:
            files[f"results/{claim.id}.json"] = _record_bytes(
                "results", [claim.physical_result], label)

    # The params rule (spec §3.15). The model owns value, derived_from, units and
    # rationale; a param record keeps `source`, `grounded_by`, `tags`, `rejected`,
    # and `rationale`/`units` only where the model states none. It loses nothing:
    # the legacy `sync_params` already overwrote a record's rationale with the
    # model's whenever the model stated one, so a hand-written rationale survives
    # in a legacy ledger only where the model is silent — and those are kept.
    # Rejected: dropping units/rationale whenever `meta.model_entry` is set
    # (erases a hand-written rationale the model lacks); keeping everything and
    # hand-deleting the duplicates (a generated output edited by hand, and one
    # duplicate per param in every user project).
    stated = _stated(root, ledger.meta.model_entry, model_prose)
    for param in ledger.params:
        said = stated.get(param.name) or {}
        drop: dict[str, str] = {}
        if isinstance(said, dict) and str(said.get("rationale") or "").strip():
            drop["rationale"] = ""
        if isinstance(said, dict) and str(said.get("units") or "").strip():
            drop["units"] = ""
        sparse = dataclasses.replace(param, **drop)
        if _param_body(sparse):
            files[f"params/{param.name}.json"] = _record_bytes("params", sparse, label)

    by_id = {c.id: c for c in ledger.claims}
    for need in ledger.needs:
        if not _need_is_derived(need, by_id):
            files[f"needs/{need.id}.json"] = _record_bytes("needs", need, label)
    for kind, records in (("decisions", ledger.decisions), ("inputs", ledger.inputs),
                          ("views", ledger.views)):
        for record in records:
            files[f"{kind}/{record.id}.json"] = _record_bytes(kind, record, label)
    files[_PROJECT_REL] = _project_bytes(ledger.meta)
    _case_collisions(files)
    return files


def _ledger_from_files(root: str, files: dict[str, bytes]) -> Ledger:
    """The plan read back through the strict reader: the Ledger `load` will
    return once the files are on disk."""
    meta = _parse_project(files[_PROJECT_REL], _PROJECT_REL)
    parsed: dict[str, list[tuple[str, Any]]] = {kind: [] for kind in RECORD_DIRS}
    for rel, raw in files.items():
        if rel == _PROJECT_REL:
            continue
        kind, name = rel.split("/", 1)
        parsed[kind].append((name[:-5], _parse_record(rel, kind, name[:-5], raw,
                                                      model_entry=meta.model_entry)))
    return _assemble(root, meta, parsed)


def _refuse_disagreeing_records(root: str, files: dict[str, bytes]) -> None:
    """A migration that crashed left record files and no `project.json`. Each is
    re-derived; identical ones are completed, and anything else — a record
    edited since, a record the ledger does not hold — is refused, naming both
    sides, because completing the migration would silently pick one."""
    problems: list[str] = []
    for kind in RECORD_DIRS:
        for stem, path in _record_files(root, kind):
            rel = f"{kind}/{stem}.json"
            want = files.get(rel)
            if want is None:
                problems.append(f"{rel} is not in {_LEDGER_REL}"
                                + (_INPUTS_HINT if kind == "inputs" else ""))
            elif _read_bytes(path) != want:
                problems.append(f"{rel} differs from what {_LEDGER_REL} migrates to")
    if problems:
        raise AtompipeError(
            f"{_LEDGER_REL} has not finished migrating (there is no {_PROJECT_REL} yet), "
            f"and record files already here disagree with it: {'; '.join(problems)}. "
            f"Move those files aside, or make {_LEDGER_REL} say the same, then run the "
            f"command again — nothing was written.")


def _finish_rename(root: str) -> None:
    """Complete a migration that committed (`project.json` written) but crashed
    before renaming the legacy ledger: a `ledger.json` that is not an index and
    no `ledger.legacy.json` yet. The rename is what keeps the legacy file from
    being overwritten by the first index — never deleted, as the table says."""
    legacy, kept = ledger_path(root), os.path.join(atompipe_dir(root), LEGACY_LEDGER_NAME)
    if not os.path.isfile(legacy) or os.path.lexists(kept):
        return
    try:
        data = json.loads(_read_bytes(legacy).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return
    if isinstance(data, dict) and "generated" not in data:
        os.replace(legacy, kept)


def _counts(files: dict[str, bytes]) -> str:
    counts: dict[str, int] = {}
    for rel in files:
        if rel != _PROJECT_REL:
            kind = rel.split("/", 1)[0]
            counts[kind] = counts.get(kind, 0) + 1
    return ", ".join(f"{counts[k]} {k}/" for k in RECORD_DIRS if counts.get(k)) or "no records"


def migrate_legacy(root: str, *, apply: bool, when: str,
                   model_prose: Callable[[str, str], Any] | None = None) -> MigrationPlan:
    """Turn a legacy `.atompipe/ledger.json` into record files — or, with
    `apply=False`, say what it would write and write nothing.

    A pure function of the legacy file and of `model_prose(root, entry)`'s
    output (`{param: {"rationale", "units"}}`, what the model STATES, read
    statically — the CLI passes `modelio.static_param_prose`; the store never
    imports or runs a model). With `model_prose=None`, no entry, or an entry
    that does not parse, nothing is dropped. Every plan of one project — `load`'s
    in memory, `doctor`'s, `check --no-record`'s and `check`'s own — must be made
    with the same reader, or the refusals below fire on one path and not the
    other (see `load`).

    Before a byte is written it refuses: an unknown key at any level (`rejectd`,
    with a suggestion — S-40: today's spine drops it and the next save erases
    it), `independence` anywhere, a NaN in a record, an id that cannot be a file
    name or collides, a generated index with no `project.json`, and record files
    left by a crashed migration that disagree with this plan. Then, with
    `apply`: the record files, then `ensure_ignore_blocks`, then `project.json`
    LAST (the commit marker: a crash before it leaves a legacy project that the
    next run completes), then `ledger.json` renamed to `ledger.legacy.json`,
    never deleted. `physical_result` moves to `results/<id>.json`; verdicts
    and `last_run` are dropped (D-09; claims read PENDING until the first
    check); `Claim.gates`, `Param.value`/`derived_from`/`gates`/`changed_in` and
    `InputArtifact.bytes` are derived and written nowhere; a need `gap` would
    derive by itself, and a param left with nothing, write no file.

    On a project already migrated it returns the loaded records, no files, no
    notice, and changes no byte. It never takes the build lock (§0.6), and
    `when` — the CLI's one clock stamp — appears only in the notice.
    """
    if os.path.isfile(_project_path(root)):
        ledger = _load_records(root)
        if apply:
            _finish_rename(root)
        return MigrationPlan(ledger, {}, "")
    if not os.path.isfile(ledger_path(root)):
        return MigrationPlan(Ledger(), {}, "")

    files = _plan_files(root, _read_legacy(root), model_prose)
    try:
        ledger = _ledger_from_files(root, files)
    except AtompipeError as exc:
        # A value the legacy reader let through that no record file may hold (a
        # result whose `passed` is not a bool): name the file the user must edit.
        raise AtompipeError(f"{_LEDGER_REL} cannot migrate — the record it would write "
                            f"does not read back: {exc} — fix it in {_LEDGER_REL}; "
                            f"nothing was written") from None
    _refuse_disagreeing_records(root, files)
    if not apply:
        return MigrationPlan(ledger, files, (
            f"atompipe: this project keeps its records in the legacy {_LEDGER_REL}; it is "
            f"read in memory and will migrate to record files ({_counts(files)}) on the "
            f"next check"))

    legacy = ledger_path(root)
    kept = os.path.join(atompipe_dir(root), LEGACY_LEDGER_NAME)
    if os.path.lexists(kept) and (not os.path.isfile(kept)
                                  or _read_bytes(kept) != _read_bytes(legacy)):
        raise AtompipeError(
            f"{_LEGACY_REL} already exists and is not {_LEDGER_REL} — the migration keeps "
            f"the legacy ledger under that name; move the old copy aside and run the "
            f"command again — nothing was written")
    for rel in sorted(files, key=lambda r: (r.split("/", 1)[0] != "claims", r)):
        if rel != _PROJECT_REL:
            _write_if_changed(os.path.join(root, *rel.split("/")), files[rel])
    for kind in RECORD_DIRS:                  # a migrated project has init's layout
        ensure_dir(os.path.join(root, kind))
    ensure_ignore_blocks(root)
    _write_if_changed(_project_path(root), files[_PROJECT_REL])
    os.replace(legacy, kept)
    at = f" ({when})" if when else ""
    return MigrationPlan(ledger, files, (
        f"atompipe: migrated {_LEDGER_REL} to record files{at}: {_counts(files)}. The old "
        f"file is kept, ignored, as {_LEGACY_REL}; {_LEDGER_REL} is now a generated index "
        f"of the records — edit the records, never it. If git tracks the old file, stop "
        f"tracking it:\n  git rm --cached {_LEDGER_REL}"))


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #
def init(root: str, meta: ProjectMeta) -> Ledger:
    """Create the records layout. Refuses to clobber; never writes a `ledger.json`.

    Writes the empty record directories, the input buckets and their README,
    `docs/` and `model/`, the three marked blocks (`ensure_ignore_blocks`), and
    `.atompipe/project.json` LAST — the marker `find_root` looks for, so an
    `init` that crashed half way is not a project. No `ledger.json`: that name
    is the generated index, and a new project written in the legacy layout was
    born needing a migration, with a spurious `git rm` notice on its first
    command.

    Refusal is the point. The records hold provenance that exists nowhere else —
    `the wider trace was tried first and the router could not close that net` is
    not recoverable from the CAD, the git history, or anyone's memory. So a
    second `init` on a live project raises instead of "re-initialising" it.

    `meta` arrives fully formed (including `created`, which the CLI stamps) —
    see contract rule 3 on why this function does not look at the clock.

    It refuses when a MARKER exists, not when `.atompipe/` does: a directory
    holding only `.atompipe/packs/` is not a project (S-64, cli:H9), and `init`
    there used to be refused as if it were one. Its packs are left in place.
    """
    dot = atompipe_dir(root)
    for name in _MARKERS:
        marker = os.path.join(dot, name)
        # `lexists`, not `find_root`'s `isfile`: a `ledger.json` that is a
        # directory marks no project, but writing over it would still crash or
        # clobber, and refusing is never the direction that loses a ledger.
        if os.path.lexists(marker):
            raise AtompipeError(
                f"{marker} already exists — this directory is already an atompipe "
                f"project. Refusing to overwrite its records; the rejected "
                f"alternatives they hold cannot be regenerated."
            )
    if os.path.lexists(dot) and not os.path.isdir(dot):
        raise AtompipeError(
            f"{dot} exists and is not a directory — move it aside before "
            f"`atompipe init`; nothing was written"
        )

    ensure_dir(root)
    ensure_dir(dot)
    ensure_dir(out_dir(root))
    for kind in RECORD_DIRS:
        ensure_dir(os.path.join(root, kind))
    for bucket in INPUT_BUCKETS:
        ensure_dir(os.path.join(inputs_dir(root), bucket))
    ensure_dir(docs_dir(root))
    ensure_dir(model_dir(root))

    readme = project_paths(root)["inputs_readme"]
    if not os.path.exists(readme):
        # Only when missing: a directory that was not a project may still hold
        # a README a person wrote, and init refuses to clobber what it did not make.
        atomic_write_text(readme, _INPUTS_README)
    ensure_ignore_blocks(root)
    write_project(root, meta)
    return Ledger(meta=meta)


__all__ = [
    "ATOMPIPE_DIR", "LEDGER_NAME", "PROJECT_NAME", "LEGACY_LEDGER_NAME", "PROJECT_SCHEMA",
    "RECORD_DIRS", "VERDICTS_NAME", "CACHE_NAME", "OBS_NAME", "INDEX_BANNER",
    "LEGACY_GITIGNORE_TEMPLATES", "INPUT_BUCKETS", "BUCKET_FOR_KIND",
    "find_root", "require_root",
    "atompipe_dir", "ledger_path", "out_dir", "inputs_dir",
    "docs_dir", "model_dir", "project_paths",
    "read_project", "write_project", "read_record", "write_record",
    "load", "save", "is_legacy",
    "build_index", "write_index", "agree", "records_digest",
    "MigrationPlan", "migrate_legacy", "ensure_ignore_blocks", "init",
]
