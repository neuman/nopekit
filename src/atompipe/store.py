# SPDX-License-Identifier: Apache-2.0
"""atompipe.store — where a project lives on disk, and the rules for touching it.

The whole project state is `<root>/.atompipe/ledger.json` plus an append-only run
history in `<root>/.atompipe/runs/`. Everything else under `.atompipe/` is scratch.

Three decisions are baked in here and are worth the words:

1. **One ledger file.** An agent reads it once and holds the entire shape of the
   project. Sharding it across `claims/*.json` would have made every read a
   directory walk and every diff a merge conflict.

2. **`init` refuses to run twice.** The ledger carries the only copy of *why*
   every number is what it is — the rejected alternatives especially. Clobbering
   it is unrecoverable in a way that losing generated CAD is not. So a second
   `init` is a user error, not an overwrite.

3. **Runs are files, not ledger rows.** `record_run` appends; nothing ever
   rewrites history. The ledger keeps only the LATEST verdict per gate (see
   `Ledger.upsert_verdict`), which is what staleness is judged against; the
   history is how you answer "when did this last pass?" without bloating the
   file an agent has to read on every command.

Nothing in this module stamps its own time. Run timestamps come in on `RunMeta`
(contract rule 3): a store that reached for the clock could not be tested, and a
run history that disagreed with the ledger about *when* would be worse than none.
"""
from __future__ import annotations

import os
import re
from typing import Any, Iterable

from .models import ArtifactKind, Ledger, ProjectMeta, RunMeta, Verdict
from .util import AtompipeError, atomic_write_json, ensure_dir, read_json, short_hash

# --------------------------------------------------------------------------- #
# layout constants
# --------------------------------------------------------------------------- #
ATOMPIPE_DIR = ".atompipe"
LEDGER_NAME = "ledger.json"
RUNS_NAME = "runs"
OUT_NAME = "out"
PACKS_NAME = "packs"                 # project-local packs, searched first by packs.py
PROJECTION_NAME = "model.json"       # modelio.write_projection lands here

#: The records layout's project file (Phase 1.3), written LAST by `init` and by
#: the legacy migration, as the commit marker. Named here ahead of that layout
#: because `find_root` must recognise a project in either shape from 1.1 on.
PROJECT_NAME = "project.json"

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

INPUTS_NAME = "inputs"
DOCS_NAME = "docs"
MODEL_NAME = "model"

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

#: run files are `0001-<hash>.json`; the counter sorts, the hash identifies.
_RUN_FILE_RE = re.compile(r"^(\d{4,})-([0-9a-f]+)\.json$")

_GITIGNORE = """\
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

_INPUTS_README = """\
# inputs/ — the evidence this design is built on

Put real files here. Hand sketches, photos of the thing you'd buy instead, CAD
you already have, datasheet PDFs, caliper readings, log files. Intake is not only
a conversation: the numbers that matter usually arrive as a photo of a napkin.

Use `atompipe ingest <path>` rather than copying by hand — it files the artifact
in the right bucket, hashes it, and registers it in the ledger so a changed input
can mark a claim STALE. Then use `atompipe extract` to record what you actually
read out of it:

    atompipe ingest inputs/sketches/hull.png --desc "midship section, dimensioned"
    atompipe extract hull-png --what "hull beam at midship reads 148 mm" \\
                              --grounds beam_mm

**An artifact nobody extracted from is decoration.** A file sitting in this
directory grounds nothing, proves nothing, and will not stop anyone from
re-litigating a number two months from now. `atompipe inputs --unextracted`
lists the decorations.

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
    """`<root>/.atompipe/ledger.json` — the whole project state, one file."""
    return os.path.join(atompipe_dir(root), LEDGER_NAME)


def runs_dir(root: str) -> str:
    """`<root>/.atompipe/runs/` — append-only gate-sweep history. Tracked in git."""
    return os.path.join(atompipe_dir(root), RUNS_NAME)


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
        "runs": runs_dir(root),
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
# the ledger
# --------------------------------------------------------------------------- #
def load(root: str) -> Ledger:
    """Read `ledger.json`. A missing file is an empty project, not an error.

    A *corrupt* file, however, IS an error and must never be swallowed: returning
    an empty Ledger for an unparseable file would look like it worked, and the
    very next `save` would write that emptiness over the only copy of the
    project's rejected alternatives. Hand-edited ledgers are the expected cause,
    so these raise AtompipeError naming the file, not a traceback.
    """
    path = ledger_path(root)
    if not os.path.exists(path):
        return Ledger()

    try:
        data = read_json(path, default=None)
    except Exception as exc:                        # unreadable / bad JSON
        raise AtompipeError(f"{path}: cannot be read as JSON ({exc})") from exc

    if data is None:
        # The file exists but produced nothing. Either it is empty or `read_json`
        # returned its default for a parse failure. Both mean "do not proceed".
        raise AtompipeError(
            f"{path}: exists but is empty or unparseable JSON — refusing to treat "
            f"it as a new project. Restore it from git, or delete it if you truly "
            f"want to start over."
        )
    if not isinstance(data, dict):
        raise AtompipeError(
            f"{path}: expected a JSON object at the top level, "
            f"got {type(data).__name__}"
        )

    try:
        return Ledger.from_dict(data)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        # Unknown keys are dropped by the models, so getting here means a VALUE is
        # wrong: a comparator that is not a comparator, a tier that is not an int.
        raise AtompipeError(f"{path}: not a valid atompipe ledger ({exc})") from exc


def save(root: str, ledger: Ledger) -> None:
    """Write the ledger atomically (temp file + `os.replace`).

    Atomic because the ledger is read and rewritten by nearly every command: a
    crash mid-write against a plain `open(...,'w')` truncates first and leaves a
    zero-byte project. `atomic_write_json` sorts keys so the git diff of a run
    shows what actually changed rather than dict ordering churn.

    Refuses to write into a directory that was never `init`-ed, because the
    failure mode is silent: a stray `.atompipe/ledger.json` one level up from
    where the user thinks they are.
    """
    dot = atompipe_dir(root)
    if not os.path.isdir(dot):
        raise AtompipeError(
            f"no {ATOMPIPE_DIR}/ directory at {os.path.abspath(root)} — "
            f"run `atompipe init` before saving"
        )
    atomic_write_json(ledger_path(root), ledger.to_dict())


def init(root: str, meta: ProjectMeta) -> Ledger:
    """Create the full project layout and the first ledger. Refuses to clobber.

    Refusal is the point. A ledger holds provenance that exists nowhere else —
    `the wider trace was tried first and the router could not close that net` is not
    recoverable from the CAD, the git history, or anyone's memory. So a second
    `init` on a live project raises instead of "re-initialising" it.

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
                f"project. Refusing to overwrite its ledger; the rejected "
                f"alternatives it records cannot be regenerated."
            )
    if os.path.lexists(dot) and not os.path.isdir(dot):
        raise AtompipeError(
            f"{dot} exists and is not a directory — move it aside before "
            f"`atompipe init`; nothing was written"
        )

    ensure_dir(root)
    ensure_dir(dot)
    ensure_dir(runs_dir(root))
    ensure_dir(out_dir(root))
    ensure_dir(inputs_dir(root))
    for bucket in INPUT_BUCKETS:
        ensure_dir(os.path.join(inputs_dir(root), bucket))
    ensure_dir(docs_dir(root))
    ensure_dir(model_dir(root))

    paths = project_paths(root)
    # Written whole, as before. Now that `.atompipe/` may already exist, a
    # `.gitignore` in it is either a crashed `init`'s (this same text) or a
    # hand-made one beside a pack home's `packs/`, and the latter's lines are
    # replaced: the writer that keeps user lines in a marked block arrives with
    # the records layout (1.3), and a project whose `out/` is tracked is the
    # worse failure of the two.
    with open(paths["gitignore"], "w", encoding="utf-8") as fh:
        fh.write(_GITIGNORE)
    with open(paths["inputs_readme"], "w", encoding="utf-8") as fh:
        fh.write(_INPUTS_README)

    ledger = Ledger(meta=meta)
    save(root, ledger)
    return ledger


# --------------------------------------------------------------------------- #
# run history
# --------------------------------------------------------------------------- #
def _as_dict(obj: Any) -> dict[str, Any]:
    """Accept a model object or an already-serialised dict. Callers vary."""
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise TypeError(f"cannot serialise {type(obj).__name__} for the run history")


def _next_index(directory: str) -> int:
    """One past the highest counter already on disk (MAX, not COUNT).

    Counting files would reuse a number after someone deletes an old run, and two
    different sweeps sharing a filename is how a history stops being a history.
    """
    highest = 0
    if os.path.isdir(directory):
        for name in os.listdir(directory):
            m = _RUN_FILE_RE.match(name)
            if m:
                highest = max(highest, int(m.group(1)))
    return highest + 1


def record_run(root: str, verdicts: Iterable[Verdict], run_meta: RunMeta) -> str:
    """Append one run to `.atompipe/runs/` and return the file path.

    The filename is `NNNN-<hash>.json`: a zero-padded counter so `ls` sorts
    chronologically, and a short hash of the run's identity (when + model hash +
    inputs hash + tier + the gates that ran) so two runs are visibly different
    files even at a glance.

    The name deliberately does NOT embed a timestamp this function generates.
    `run_meta.when` is supplied by the caller and is the single authority on when
    a sweep happened; a filename minted from `datetime.now()` would eventually
    disagree with it (timezones, a sweep that spans midnight, a replayed run) and
    the disagreement would be invisible.

    Append-only: an existing file is never rewritten, and if a name is somehow
    taken (two sweeps racing) the counter advances until it is free.
    """
    directory = runs_dir(root)
    if not os.path.isdir(atompipe_dir(root)):
        raise AtompipeError(
            f"no {ATOMPIPE_DIR}/ directory at {os.path.abspath(root)} — "
            f"run `atompipe init` before recording a run"
        )
    ensure_dir(directory)

    meta = _as_dict(run_meta)
    rows = [_as_dict(v) for v in (verdicts or [])]

    # Identity seed: what was run, on what, when. Not the duration — a rerun of
    # the identical sweep should be recognisable as such.
    seed = "|".join([
        str(meta.get("when", "")),
        str(meta.get("model_hash", "")),
        str(meta.get("inputs_hash", "")),
        str(meta.get("tier", "")),
        ",".join(str(r.get("gate", "")) for r in rows),
    ])
    digest = short_hash(seed, 8)

    index = _next_index(directory)
    while True:
        path = os.path.join(directory, f"{index:04d}-{digest}.json")
        if not os.path.exists(path):
            break
        index += 1

    atomic_write_json(path, {"index": index, "meta": meta, "verdicts": rows})
    return path


def load_runs(root: str, limit: int = 20) -> list[dict]:
    """Read the run history, NEWEST FIRST. `limit <= 0` means all of it.

    Each returned dict is the stored record plus a `path` key added here (derived
    at read time, not duplicated into the file — the file's own name is already
    the truth about where it lives).

    A record that will not parse is returned with an `error` key and empty
    `verdicts` rather than being dropped. Silently skipping it would make a
    corrupted history look like a short one, and "the gate passed last Tuesday"
    is exactly the kind of claim that must not quietly evaporate.
    """
    directory = runs_dir(root)
    if not os.path.isdir(directory):
        return []

    names = sorted(
        (n for n in os.listdir(directory) if _RUN_FILE_RE.match(n)),
        key=lambda n: int(_RUN_FILE_RE.match(n).group(1)),   # type: ignore[union-attr]
        reverse=True,
    )
    if limit and limit > 0:
        names = names[:limit]

    out: list[dict] = []
    for name in names:
        path = os.path.join(directory, name)
        index = int(_RUN_FILE_RE.match(name).group(1))       # type: ignore[union-attr]
        try:
            data = read_json(path, default=None)
        except Exception as exc:                             # noqa: BLE001 - reported, not raised
            data = None
            err = str(exc)
        else:
            err = "unreadable or not a JSON object" if not isinstance(data, dict) else ""
        if err:
            out.append({"index": index, "path": path, "meta": {},
                        "verdicts": [], "error": err})
            continue
        record = dict(data)                                  # type: ignore[arg-type]
        record.setdefault("index", index)
        record.setdefault("meta", {})
        record.setdefault("verdicts", [])
        record["path"] = path
        out.append(record)
    return out


__all__ = [
    "ATOMPIPE_DIR", "LEDGER_NAME", "INPUT_BUCKETS", "BUCKET_FOR_KIND",
    "find_root", "require_root",
    "atompipe_dir", "ledger_path", "runs_dir", "out_dir", "inputs_dir",
    "docs_dir", "model_dir", "project_paths",
    "load", "save", "init",
    "record_run", "load_runs",
]
