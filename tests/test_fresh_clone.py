# SPDX-License-Identifier: Apache-2.0
"""The fresh-clone transcript, replayed at every checkpoint (PLAN G6).

`docs/plan/phase-1.md` opens with what a human sees on a fresh clone of the
bracket: the first check, an edit, what `status` says about it, the evidence it
leaves, the selftest where CLAUDE.md says to run it, and JUnit. `tests/_transcript.py`
holds that transcript as data; this file copies the bracket into a new git
repository — where it is the repository root, as it is for a user who cloned it
— and replays every step whose checkpoint has landed, in order, through `_env`.

How the copy is made, and what slipped through on the way:

* **Plain `git ls-files` is the wrong list.** It misses a checkpoint's new files
  that are not yet committed, and it lists files the working tree has deleted
  (`runs/`, when 1.3 removes the run history). The copy takes `git ls-files
  --cached --others --exclude-standard examples/bracket`, filtered to paths that
  exist: what a commit of this working tree would hold.
* **verify.sh `--dir` copies the tree without its `.git`** (tests:H11), so there
  is no git to ask. Where the enclosing repository does not track the bracket —
  none at all, or an unrelated one the copy landed inside — the file list comes
  from a walk that leaves out exactly what a clone would not have: bytecode and
  the run's own outputs. `BracketFreshClone` replays through both lists, so the
  fallback is exercised on every run, not only inside the phase gate.
* **Bytecode is never suppressed.** `PYTHONDONTWRITEBYTECODE` is never set, here
  or by `_env` (which strips it): a user's shell does not set it, and the
  transcript's last step must see the `__pycache__/` a real run writes.
* **A replay needs an identity to commit, and a runner has none.** The copy's
  commit is the one place a test asks `_env.git` for `identity=True`.

**Line endings** (1.3). Every digest in a verdict entry is over bytes, so a clone
whose checkout rewrote line endings would read every committed entry stale and
dirty its own tree on the first check — the Windows default, `core.autocrlf=true`.
`LineEndings` clones the fresh copy that way and asks for LF on disk, Fresh
entries, a clean tree after `check` and a mesh byte for byte, then takes the
bracket's `.gitattributes` block away to show it is what holds;
`RepoLineEndings` holds the repository's own `.gitattributes` (bundled pack files
are read by gates in every project) to the same rules, through `git check-attr`.

The harness is itself a checker, so it gets what every checker here gets: a
planted input it must refuse. `TranscriptMatchers` holds every expectation to
the plan's own transcript (positive) and to a mutation of it (negative), and
`TranscriptIsWellFormed` stops a step from quietly leaving the table or being
replayed before the checkpoint that can make it true.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_fresh_clone.py -v
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import unittest

import _env
import _transcript
from _transcript import Result

#: The bracket, relative to the repository root, spelled with `/` as git spells it.
BRACKET_REL = "examples/bracket"
BRACKET = os.path.join(_env.REPO, *BRACKET_REL.split("/"))

#: What the walk leaves out, each because a clone would not carry it. Directories
#: named `__pycache__` at any depth and `*.pyc` files: bytecode a previous run
#: wrote, which would hide the bytecode the replay's own run writes (the
#: `no-bytecode-shown` step) and is ignored by the repository's `.gitignore`.
#: `.atompipe/out`, `.atompipe/cache`, `.atompipe/obs`: a run's scratch,
#: evidence, cache and observations — outputs, never sources, and a previous
#: run's cache copied in would make the "first" check a second one.
#: `.atompipe/ledger.json` and `.atompipe/ledger.legacy.json` (from 1.3): the
#: generated index of the records and the ledger they were migrated from, both
#: ignored — a checkout's outputs, which a clone of the migrated bracket never
#: holds (what slipped through: the U32 checkout's index and legacy ledger rode
#: into every `--dir` "fresh clone", so its first command read a stranger's
#: index before rewriting it).
#: *Rejected:* parsing the `.gitignore` files — a second, partial
#: implementation of git's ignore rules, wrong in the cases that matter;
#: *rejected:* `shutil.copytree` of everything, which is how a developer's
#: `.atompipe/out` would ride into a "fresh" clone.
WALK_PRUNE_NAMES = frozenset({"__pycache__"})
WALK_PRUNE_PATHS = frozenset({".atompipe/out", ".atompipe/cache", ".atompipe/obs"})
WALK_SKIP_PATHS = frozenset({".atompipe/ledger.json", ".atompipe/ledger.legacy.json"})
WALK_SKIP_SUFFIXES = (".pyc",)

#: The message of the copy's single commit.
COMMIT_MESSAGE = "a fresh clone of examples/bracket"


# --------------------------------------------------------------------------- #
# The copy
# --------------------------------------------------------------------------- #
def git_listing(repo: str = _env.REPO, rel: str = BRACKET_REL) -> list[str] | None:
    """The files under ``rel`` a commit of ``repo``'s working tree would hold,
    relative to ``rel``; None when no repository here tracks ``rel``.

    `--cached --others --exclude-standard`, filtered to paths that exist: the
    index alone misses new files a checkpoint has not committed yet and lists
    the ones the working tree deleted.
    """
    tracked = _env.git(["ls-files", "-z", "--cached", "--", rel], cwd=repo)
    if tracked.returncode != 0 or not tracked.stdout.strip("\0"):
        return None
    listed = _env.git(["ls-files", "-z", "--cached", "--others", "--exclude-standard",
                       "--", rel], cwd=repo)
    if listed.returncode != 0:
        raise AssertionError(f"git ls-files in {repo} failed: {listed.stderr.strip()}")
    prefix = rel.rstrip("/") + "/"
    out: set[str] = set()
    for path in listed.stdout.split("\0"):
        if not path.startswith(prefix):
            continue
        if os.path.exists(os.path.join(repo, *path.split("/"))):
            out.add(path[len(prefix):])
    return sorted(out)


def walk_listing(root: str) -> list[str]:
    """Every file under ``root`` a clone would carry, relative to it, `/`-separated:
    no `__pycache__`, no `*.pyc`, none of `.atompipe/{out,cache,obs}`, and not
    the ignored index or legacy ledger."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(d for d in dirnames
                             if d not in WALK_PRUNE_NAMES
                             and rel_dir + d not in WALK_PRUNE_PATHS)
        for filename in sorted(filenames):
            if filename.endswith(WALK_SKIP_SUFFIXES) or rel_dir + filename in WALK_SKIP_PATHS:
                continue
            out.append(rel_dir + filename)
    return sorted(out)


def bracket_listing(*, walk: bool | None = None) -> tuple[str, list[str]]:
    """``(source, files)`` for the bracket: ``"git"`` where a repository here
    tracks it, else ``"walk"``. ``walk=True`` forces the walk; ``False`` forces
    git and fails where there is none."""
    if not walk:
        listed = git_listing()
        if listed is not None:
            return "git", listed
        if walk is False:
            raise AssertionError(f"no git repository here tracks {BRACKET_REL}")
    return "walk", walk_listing(BRACKET)


def fresh_clone(dest: str, *, walk: bool | None = None) -> str:
    """Copy the bracket into ``dest`` (made here), `git init` it and commit
    everything with a test identity. Returns the listing's source."""
    source, files = bracket_listing(walk=walk)
    if not files:
        raise AssertionError(f"the {source} listing of {BRACKET_REL} is empty")
    for rel in files:
        target = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(os.path.join(BRACKET, *rel.split("/")), target)
    for argv, identity in ((["-c", "init.defaultBranch=main", "init", "-q"], False),
                           (["add", "-A"], False),
                           (["commit", "-q", "-m", COMMIT_MESSAGE], True)):
        proc = _env.git(argv, cwd=dest, identity=identity)
        if proc.returncode != 0:
            raise AssertionError(f"git {' '.join(argv)} in the copy failed: "
                                 f"{proc.stderr.strip()}")
    return source


def committed_entries(project: str) -> list[str]:
    """The cache entries the copy's commit holds, project-relative."""
    proc = _env.git(["ls-files", "-z", "--", _transcript.VERDICTS_DIR], cwd=project)
    if proc.returncode != 0:
        raise AssertionError(f"git ls-files in the copy failed: {proc.stderr.strip()}")
    return sorted(p for p in proc.stdout.split("\0") if p)


# --------------------------------------------------------------------------- #
# The replay
# --------------------------------------------------------------------------- #
def _tail(text: str, n: int = 30) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:]) if lines else "(empty)"


class _Replay:
    """Executes `_transcript.STEPS` against one copy, in order, through `_env`."""

    def __init__(self, case: _env.EnvCase, project: str, spines: list,
                 current: _transcript.Tag | None = None):
        self.case = case
        self.project = project
        self.spines = spines
        self.current = _transcript.CURRENT if current is None else current
        self.home = case.tmp()             # one HOME for the whole session, as a user has
        self.last: Result | None = None
        self.snapshots: dict[str, set[str]] = {}
        self._spine_current: bool | None = None

    def spine_is_current(self) -> bool:
        if self._spine_current is None:
            self._spine_current = _transcript.spine_is_current(self.spines)
        return self._spine_current

    def _entries(self) -> set[str]:
        root = os.path.join(self.project, *_transcript.VERDICTS_DIR.split("/"))
        found: set[str] = set()
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                full = os.path.join(dirpath, filename)
                found.add(os.path.relpath(full, self.project).replace(os.sep, "/"))
        return found

    def act(self, action: _transcript.Action) -> Result | None:
        if isinstance(action, _transcript.Atompipe):
            cwd = self.project if action.where == "project" else self.case.tmp()
            proc = _env.atompipe(list(action.argv), cwd=cwd, home=self.home)
            self.last = Result(str(action), proc.returncode, proc.stdout, proc.stderr, cwd)
            return self.last
        if isinstance(action, _transcript.Git):
            proc = _env.git(list(action.argv), cwd=self.project, home=self.home)
            if proc.returncode != 0:
                raise AssertionError(f"{action} failed in the copy: {proc.stderr.strip()}")
            self.last = Result(str(action), proc.returncode, proc.stdout, proc.stderr,
                               self.project)
            return self.last
        if isinstance(action, _transcript.SameRun):
            if self.last is None:
                raise AssertionError("(same run) with no command before it")
            return self.last
        if isinstance(action, _transcript.Edit):
            path = os.path.join(self.project, *action.path.split("/"))
            with open(path, "r", encoding="utf-8", newline="") as fh:
                text = fh.read()
            if text.count(action.old) != 1:
                raise AssertionError(f"{action}: found {text.count(action.old)} times, "
                                     f"expected once")
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text.replace(action.old, action.new))
            return None
        if isinstance(action, _transcript.Restore):
            if action.since not in self.snapshots:
                raise AssertionError(f"{action}: step {action.since} was not replayed")
            proc = _env.git(["checkout", "--", action.path], cwd=self.project, home=self.home)
            if proc.returncode != 0:
                raise AssertionError(f"{action}: {proc.stderr.strip()}")
            for rel in sorted(self._entries() - self.snapshots[action.since]):
                os.remove(os.path.join(self.project, *rel.split("/")))
            return None
        if isinstance(action, _transcript.Seq):
            result = None
            for sub in action.actions:
                result = self.act(sub) or result
            return result
        raise AssertionError(f"unknown transcript action {action!r}")

    def run(self) -> list[str]:
        """Replay every due step; returns the ids replayed. Each step's failures
        are reported under its own subTest, and the replay goes on: a later
        step's result says whether an early failure was the only one."""
        replayed: list[str] = []
        for step in _transcript.STEPS:
            if not _transcript.due(step, self.current):
                continue
            self.snapshots[step.id] = self._entries()
            result = self.act(step.action)
            replayed.append(step.id)
            for expect in step.expect:
                if not _transcript.reached(expect, step, self.current):
                    continue
                # G6's allowance, as a plain conditional: a starred expectation holds
                # only on the spine that wrote the committed cache.
                if expect.starred and not self.spine_is_current():
                    continue
                with self.case.subTest(step=step.id, expect=expect.kind):
                    why = _transcript.problem(expect, result)
                    if why is not None:
                        shown = (f"$ {result.command}   (exit {result.returncode})\n"
                                 f"--- stdout\n{_tail(result.stdout)}\n"
                                 f"--- stderr\n{_tail(result.stderr)}"
                                 if result is not None else "")
                        self.case.fail(f"transcript step {step.id} ({step.action}): "
                                       f"{why}\n{shown}")
        return replayed


class BracketFreshClone(_env.EnvCase):
    """The transcript's due steps, replayed on a fresh clone of the bracket."""

    def _replay(self, *, walk: bool | None) -> tuple[str, list[str]]:
        project = os.path.join(self.tmp(), "bracket")
        os.makedirs(project)
        source = fresh_clone(project, walk=walk)
        spines = _transcript.read_spines(project, committed_entries(project))
        replayed = _Replay(self, project, spines).run()
        due = [s.id for s in _transcript.STEPS if _transcript.due(s)]
        self.assertEqual(replayed, due)
        self.assertTrue(replayed, "no transcript step is due: CURRENT is before every tag")
        return source, replayed

    def test_replays_the_transcript(self):
        """The copy a user would get: `git ls-files` where the bracket is tracked,
        the walk where it is not (verify.sh `--dir`)."""
        self._replay(walk=None)

    def test_replays_from_a_tree_without_git(self):
        """The walk fallback, forced, so it is exercised in a checkout too — the
        phase gate's `--dir` copy is the only other place it runs."""
        source, _ = self._replay(walk=True)
        self.assertEqual(source, "walk")


# --------------------------------------------------------------------------- #
# Line endings: digests are over bytes (PLAN P1.3, SF PD-17)
# --------------------------------------------------------------------------- #
#: A mesh git's NUL-byte heuristic takes for text: an ASCII STL with CRLF line
#: ends, as a Windows exporter writes one. Under `* text=auto` alone its CRLFs
#: would be rewritten on the way into the index, and a gate that digested the
#: mesh would never read Fresh again.
ASCII_STL_CRLF = (b"solid part\r\n  facet normal 0 0 1\r\n    outer loop\r\n"
                  b"      vertex 0 0 0\r\n      vertex 1 0 0\r\n      vertex 0 1 0\r\n"
                  b"    endloop\r\n  endfacet\r\nendsolid part\r\n")

#: The binary kinds the repository and every project pin `-text` (store's
#: `.gitattributes` block, and the repo root's).
BINARY_KINDS = ("*.stl", "*.step", "*.glb", "*.png", "*.jpg")


def autocrlf_clone(origin: str, dest: str) -> str:
    """A clone of ``origin`` as a Windows user with ``core.autocrlf=true`` gets it:
    set for the clone command, so the checkout is converted, and in the clone's
    own config, so every later `git status` there judges the tree the same way."""
    url = pathlib.Path(os.path.abspath(origin)).as_uri()
    proc = _env.git(["-c", "core.autocrlf=true", "clone", "-q",
                     "--config", "core.autocrlf=true", url, dest],
                    cwd=os.path.dirname(os.path.abspath(dest)))
    if proc.returncode != 0:
        raise AssertionError(f"git clone {url} with core.autocrlf=true failed: "
                             f"{proc.stderr.strip()}")
    return dest


def _bytes(root: str, rel: str) -> bytes:
    with open(os.path.join(root, *rel.split("/")), "rb") as fh:
        return fh.read()


def _commit(project: str, message: str) -> None:
    for argv, identity in ((["add", "-A"], False), (["commit", "-q", "-m", message], True)):
        proc = _env.git(argv, cwd=project, identity=identity)
        if proc.returncode != 0:
            raise AssertionError(f"git {' '.join(argv)} in {project}: {proc.stderr.strip()}")


def _states(case: unittest.TestCase, project: str) -> dict[str, str]:
    """``{gate: freshness state}`` from `status --json` in ``project``."""
    proc = _env.atompipe(["status", "--json"], cwd=project)
    case.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
    freshness = json.loads(proc.stdout).get("freshness") or {}
    case.assertTrue(freshness, "status --json names no gate")
    return {gate: row.get("state") for gate, row in freshness.items()}


class LineEndings(_env.EnvCase):
    """A Windows clone (`core.autocrlf=true`) of the bracket reads its committed
    cache as a Linux clone does: LF on disk, every entry Fresh, a clean tree after
    `check`, and a mesh byte for byte — because of the bracket's own
    `.gitattributes` block, which the last test takes away to show it is what
    holds (a check that cannot fail is a logger)."""

    def _origin(self) -> str:
        origin = os.path.join(self.tmp(), "origin")
        os.makedirs(origin)
        fresh_clone(origin)
        return origin

    def test_an_autocrlf_clone_checks_out_lf_and_every_entry_is_fresh(self):
        origin = self._origin()
        clone = autocrlf_clone(origin, os.path.join(self.tmp(), "windows"))
        listed = _env.git(["ls-files", "-z"], cwd=clone)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        committed = sorted(p for p in listed.stdout.split("\0") if p)
        self.assertIn("gates/structural.py", committed)
        moved = [rel for rel in committed if _bytes(clone, rel) != _bytes(origin, rel)]
        self.assertEqual(moved, [], "an autocrlf=true checkout rewrote committed bytes")
        self.assertNotIn(b"\r\n", _bytes(clone, "model/bracket.py"))

        states = _states(self, clone)
        self.assertEqual({g: s for g, s in states.items() if s != "fresh"}, {},
                         "a committed entry is not Fresh in the autocrlf clone")
        check = _env.atompipe(["check"], cwd=clone)
        self.assertEqual(check.returncode, 1, check.stdout[-2000:] + check.stderr[-2000:])
        self.assertRegex(check.stdout, r"(?m)^6 gates: 0 executed, 6 cached — ")
        porcelain = _env.git(["status", "--porcelain", "--untracked-files=all"], cwd=clone)
        self.assertEqual((porcelain.returncode, porcelain.stdout), (0, ""),
                         "check dirtied the autocrlf clone")

    def test_a_mesh_round_trips_byte_identical(self):
        origin = self._origin()
        for rel in ("inputs/cad/part.stl", "inputs/cad/part.dat"):
            path = os.path.join(origin, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(ASCII_STL_CRLF)
        _commit(origin, "a mesh, and the same bytes under a name no rule pins")
        clone = autocrlf_clone(origin, os.path.join(self.tmp(), "windows"))
        self.assertEqual(_bytes(clone, "inputs/cad/part.stl"), ASCII_STL_CRLF)
        # The control: the same bytes where no `-text` rule reaches are rewritten,
        # so the identity above is the attribute's doing, not git leaving bytes be.
        self.assertNotEqual(_bytes(clone, "inputs/cad/part.dat"), ASCII_STL_CRLF)

    def test_without_the_attributes_the_clone_goes_crlf_and_stale(self):
        origin = self._origin()
        proc = _env.git(["rm", "-q", ".gitattributes"], cwd=origin)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        _commit(origin, "the bracket without its line-ending block")
        clone = autocrlf_clone(origin, os.path.join(self.tmp(), "windows"))
        self.assertIn(b"\r\n", _bytes(clone, "gates/structural.py"),
                      "autocrlf=true checked out LF with no attribute asking for it: "
                      "this control no longer shows what the block prevents")
        self.assertNotEqual(_states(self, clone).get("bracket.deflection"), "fresh")


def attribute_problems(text: str, case: _env.EnvCase) -> list[str]:
    """What is wrong with ``text`` as a `.gitattributes`, asked of git itself
    (`git check-attr` in a scratch repository), never parsed here: a pattern
    list written the way the plan abbreviates it — several patterns, one
    attribute — is valid-looking text that pins only its first pattern."""
    repo = case.tmp()
    proc = _env.git(["init", "-q"], cwd=repo)
    if proc.returncode != 0:
        raise AssertionError(f"git init: {proc.stderr.strip()}")
    with open(os.path.join(repo, ".gitattributes"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    names = ["src/atompipe/verdicts.py", "examples/bracket/claims/C1.json",
             *(f"part{kind[1:]}" for kind in BINARY_KINDS)]
    proc = _env.git(["check-attr", "text", "eol", "--", *names], cwd=repo)
    if proc.returncode != 0:
        raise AssertionError(f"git check-attr: {proc.stderr.strip()}")
    got: dict[tuple[str, str], str] = {}
    for line in proc.stdout.splitlines():
        path, attr, value = (part.strip() for part in line.rsplit(":", 2))
        got[(path, attr)] = value
    problems = []
    for name in names:
        binary = name.startswith("part.")
        want_text = "unset" if binary else "auto"
        if got.get((name, "text")) != want_text:
            problems.append(f"{name}: text is {got.get((name, 'text'))!r}, want {want_text}")
        if not binary and got.get((name, "eol")) != "lf":
            problems.append(f"{name}: eol is {got.get((name, 'eol'))!r}, want lf")
    return problems


class RepoLineEndings(_env.EnvCase):
    """The repository's own `.gitattributes`: every pack gate's entries in every
    project digest bundled pack files, so this checkout's line endings are part
    of every project's cache (SF PD-17)."""

    def test_root_gitattributes_pins_lf_and_binary_kinds(self):
        with open(os.path.join(_env.REPO, ".gitattributes"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(attribute_problems(text, self), [])
        # Its negative controls: the plan's one-line abbreviation, and no eol.
        abbreviated = "* text=auto eol=lf\n" + " ".join(BINARY_KINDS) + " -text\n"
        self.assertTrue(attribute_problems(abbreviated, self),
                        "several patterns on one line pinned every kind")
        no_eol = text.replace("* text=auto eol=lf", "* text=auto")
        self.assertNotEqual(no_eol, text)
        self.assertTrue(attribute_problems(no_eol, self), "a missing eol=lf was accepted")


# --------------------------------------------------------------------------- #
# The copy's own controls
# --------------------------------------------------------------------------- #
class CopyListsWhatACloneHolds(_env.EnvCase):
    def _write(self, root: str, rel: str, text: str = "x\n") -> None:
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_the_walk_leaves_out_bytecode_and_outputs(self):
        root = self.tmp()
        kept = ["model/bracket.py", ".atompipe/project.json", ".atompipe/.gitignore",
                ".atompipe/runs/0001-aaaaaaaa.json", "selftest/bad_configs.py",
                "outputs/kept.txt", "model/out/kept.txt", ".atompipe/verdicts/g/x.json",
                "claims/C1.json", "ledger.json", "model/ledger.legacy.json"]
        dropped = ["model/__pycache__/bracket.cpython-312.pyc", "gates/stray.pyc",
                   ".atompipe/out/junit.xml", ".atompipe/cache/last_check.json",
                   ".atompipe/obs/g.jsonl", "selftest/__pycache__/deep/x.txt",
                   ".atompipe/ledger.json", ".atompipe/ledger.legacy.json"]
        for rel in kept + dropped:
            self._write(root, rel)
        self.assertEqual(walk_listing(root), sorted(kept))

    def test_git_listing_drops_deleted_and_keeps_new_files(self):
        """The index lists a deleted file and misses an untracked one; the
        listing does neither, and honours the ignores."""
        repo = self.tmp()
        rel = "examples/bracket"
        for name in ("keep.py", "deleted.json", ".gitignore"):
            self._write(repo, f"{rel}/{name}", "out/\n" if name == ".gitignore" else "x\n")
        for argv, identity in ((["init", "-q"], False), (["add", "-A"], False),
                               (["commit", "-q", "-m", "planted"], True)):
            proc = _env.git(argv, cwd=repo, identity=identity)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        os.remove(os.path.join(repo, *f"{rel}/deleted.json".split("/")))
        self._write(repo, f"{rel}/new.json")
        self._write(repo, f"{rel}/out/ignored.xml")
        self.assertEqual(git_listing(repo, rel), [".gitignore", "keep.py", "new.json"])

    def test_git_listing_is_none_where_nothing_tracks_the_bracket(self):
        """No repository at all, and a repository that does not track it: both
        take the walk, never an empty copy."""
        bare = self.tmp()
        self._write(bare, "examples/bracket/keep.py")
        self.assertIsNone(git_listing(bare, "examples/bracket"))
        proc = _env.git(["init", "-q"], cwd=bare)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(git_listing(bare, "examples/bracket"))


# --------------------------------------------------------------------------- #
# The transcript's own controls
# --------------------------------------------------------------------------- #
#: The step ids of spec §4 (U10), in order. A step leaves the transcript, or moves,
#: only by an edit to this list in the same commit — where a reviewer sees it.
#: What this guards: the cheapest way to make a failing step green is to delete
#: it or push its tag later (R-6, R-7).
SPEC_STEP_IDS = (
    "first-check-exit", "first-check-shape", "first-check-cached", "clean-after-check",
    "edit-bed-xy", "status-stale", "check-after-edit", "porcelain-after-edit",
    "why-thickness", "gate-show-last-selftest", "pack-mode-selftest", "check-junit",
    "revert", "no-bytecode-shown",
)

#: Each step's tag in the spec's table. A tag moves later only here, visibly.
SPEC_TAGS = {
    "first-check-exit": (1, 1), "first-check-shape": (1, 2), "first-check-cached": (1, 3),
    "clean-after-check": (1, 3), "edit-bed-xy": (1, 2), "status-stale": (1, 2),
    "check-after-edit": (1, 2), "porcelain-after-edit": (1, 3), "why-thickness": (1, 3),
    "gate-show-last-selftest": (1, 2), "pack-mode-selftest": (1, 1), "check-junit": (1, 1),
    "revert": (1, 2), "no-bytecode-shown": (1, 3),
}


class TranscriptIsWellFormed(unittest.TestCase):
    def test_the_steps_are_the_specs(self):
        self.assertEqual(tuple(s.id for s in _transcript.STEPS), SPEC_STEP_IDS)
        self.assertEqual({s.id: tuple(s.tag) for s in _transcript.STEPS}, SPEC_TAGS)

    def test_current_is_a_checkpoint(self):
        self.assertIn(tuple(_transcript.CURRENT), _transcript.CHECKPOINTS)
        for step in _transcript.STEPS:
            self.assertIn(tuple(step.tag), _transcript.CHECKPOINTS, step.id)
            for expect in step.expect:
                if expect.at is not None:
                    self.assertIn(tuple(expect.at), _transcript.CHECKPOINTS, step.id)
                    self.assertGreater(tuple(expect.at), tuple(step.tag), step.id)

    def test_starred_expectations_wait_for_committed_entries(self):
        """A starred expectation reads the committed cache, which exists from
        1.3; due earlier, it would be judged against a spine with no entries."""
        for step in _transcript.STEPS:
            for expect in step.expect:
                if expect.starred:
                    at = expect.at if expect.at is not None else step.tag
                    self.assertEqual(tuple(at), (1, 3), step.id)

    def test_same_run_reads_a_command_that_ran(self):
        """`(same run)` reads the last command replayed before it, so that command
        must be an `atompipe` run due no later than the step reading it."""
        command = None
        for step in _transcript.STEPS:
            if isinstance(step.action, _transcript.SameRun):
                self.assertIsNotNone(command, step.id)
                self.assertIsInstance(command.action, _transcript.Atompipe, step.id)
                self.assertLessEqual(tuple(command.tag), tuple(step.tag), step.id)
            else:
                command = step

    def test_every_edit_applies_once_to_the_tracked_bracket(self):
        """An edit whose text moved in the model would fail only when its
        checkpoint lands; this fails the day the model moves."""
        for step in _transcript.STEPS:
            actions = (step.action.actions if isinstance(step.action, _transcript.Seq)
                       else (step.action,))
            for action in actions:
                if not isinstance(action, (_transcript.Edit, _transcript.Restore)):
                    continue
                path = os.path.join(BRACKET, *action.path.split("/"))
                self.assertTrue(os.path.isfile(path), f"{step.id}: {action.path}")
                if isinstance(action, _transcript.Edit):
                    with open(path, "r", encoding="utf-8") as fh:
                        self.assertEqual(fh.read().count(action.old), 1, step.id)
                else:
                    self.assertIn(action.since, SPEC_STEP_IDS, step.id)

    def test_no_expectation_names_the_in_repo_prefix(self):
        """In the copy the bracket is the repository root: porcelain prints
        `model/bracket.py`, never `examples/bracket/model/bracket.py`."""
        for step in _transcript.STEPS:
            for expect in step.expect:
                values = expect.value if isinstance(expect.value, tuple) else (expect.value,)
                for value in values:
                    self.assertNotIn("examples/bracket", str(value).replace("\\", ""),
                                     step.id)

    def test_every_shape_compiles_and_is_anchored(self):
        for name, pattern in _transcript.SHAPES.items():
            with self.subTest(shape=name):
                self.assertTrue(pattern.pattern.startswith("^") or
                                pattern.pattern.startswith("(?:^"), name)
                self.assertTrue(pattern.pattern.endswith("$") or
                                pattern.pattern.endswith("$)"), name)


#: The plan's transcript (docs/plan/phase-1.md), as each command prints it once
#: Phase 1 has landed — the positive control for every expectation. Built here,
#: never read from the plan file: the plan is a document people edit.
_CACHED_FAIL = f"{'[FAIL] bracket.deflection : 0.700 mm at 15 N (limit 0.5 mm)':<77} cached"
_BLOCKING = (
    "BLOCKING — 2 critical claim(s) must not be spent against:\n"
    "[FAIL ] C1 Tip sags no more than 0.5 mm at rated load — bracket.deflection : "
    "0.700 mm at 15 N (limit 0.5 mm)\n"
    "[gap  ] C7 First mode is clear of the pump that sits on the shelf — no gate covers it\n")
_PLAN_OUTPUT = {
    "first-check-exit": (1, f"{_CACHED_FAIL}\n"
                            "6 gates: 0 executed, 6 cached — 5 ok, 1 FAIL — tier 0\n"
                            + _BLOCKING),
    "clean-after-check": (0, ""),
    "status-stale": (0, "bracket — 7 claims, 6 gates\n"
                        "stale: bracket.bed_fit — config.bed_xy 220.0 -> 250.0   "
                        "(5 checks current)\n"
                        "last check: 2026-09-27T14:02:11Z (3m ago)\n"),
    "check-after-edit": (1, "[ok  ] bracket.bed_fit : 74 x 30 x 7 mm vs 234 mm usable "
                            "(250 bed - 2x8 brim)\n"
                            f"{_CACHED_FAIL}\n"
                            "6 gates: 1 executed, 5 cached — 5 ok, 1 FAIL — tier 0\n"
                            + _BLOCKING),
    "porcelain-after-edit": (0, " M model/bracket.py\n"
                                "?? .atompipe/verdicts/bracket.bed_fit/"
                                "0123456789abcdef-89abcdef.json\n"),
    "why-thickness": (0, "param thickness = 7.0 mm   (model/bracket.py Config.thickness)\n"
                         "REJECTED (1)\n"
                         "  4.0 mm — 3.75 mm deflection, 7.5x the limit   "
                         "(model/bracket.py PARAMS)\n"),
    "gate-show-last-selftest": (0, "bracket.deflection — tip deflection at rated load\n"
                                   "  last verdict: [FAIL] bracket.deflection : 0.700 mm\n"
                                   "  last selftest: [ok  ] fired at this version "
                                   "(control 0123456789ab)\n"),
    "pack-mode-selftest": (0, "[ok  ] beam-analytic (bundled) : 6 fired\n"
                              "54 control(s) in 7.5s: 43 fired, 0 BROKEN, "
                              "11 skipped (tooling)\n"),
    "check-junit": (1, f"{_CACHED_FAIL}\n"),
    "revert": (0, ""),
    "no-bytecode-shown": (0, ""),
}

_JUNIT_OK = ('<?xml version="1.0" encoding="UTF-8"?>\n<testsuites name="atompipe check">'
             '<testsuite name="gates"/></testsuites>\n')


def _mutations(expect: _transcript.Expect, result: Result, cwd: str) -> list[tuple[str, Result]]:
    """Ways to break ``result`` that ``expect`` must notice."""
    lines = result.stdout.splitlines()
    kind = expect.kind
    if kind == "exit":
        return [("exit code moved", result._replace(returncode=result.returncode + 1))]
    if kind in ("line", "last-line"):
        out = []
        hit = [i for i, text in enumerate(lines) if re.fullmatch(expect.value, text)]
        # the matching line with one character dropped from its end, and with a
        # word appended — "cached" removed, a count changed, text trailing
        for label, edit in (("last character dropped", lambda text: text[:-1]),
                            ("text appended", lambda text: text + " (extra)")):
            mutated = [edit(text) if i in hit else text for i, text in enumerate(lines)]
            out.append((label, result._replace(stdout="\n".join(mutated) + "\n")))
        if kind == "last-line":
            out.append(("a line added after it",
                        result._replace(stdout=result.stdout + "one more line\n")))
        return out
    if kind == "in-order":
        return [("lines reversed", result._replace(stdout="\n".join(reversed(lines)) + "\n")),
                ("a line removed", result._replace(stdout="\n".join(lines[1:]) + "\n"))]
    if kind == "exactly":
        out = [("a line added", result._replace(
            stdout=result.stdout + "?? .atompipe/verdicts/stray.json\n"))]
        if lines:
            out.append(("a line removed", result._replace(stdout="\n".join(lines[1:]) + "\n")))
            out.append(("a line doubled", result._replace(stdout=result.stdout + lines[0] + "\n")))
        return out
    if kind == "no-line-containing":
        return [("the text appears", result._replace(
            stdout=result.stdout + f"?? model/{expect.value}\n"))]
    if kind == "junit":
        path, _root = expect.value
        return [("not written", result._replace(cwd=os.path.join(cwd, "elsewhere"))),
                ("wrong root", result._replace(cwd=_junit_dir(cwd, "wrong-root",
                                                              path, "<testsuite/>"))),
                ("does not parse", result._replace(cwd=_junit_dir(cwd, "broken", path,
                                                                  "<testsuites>")))]
    raise AssertionError(f"no mutation for expectation kind {kind!r}")


def _junit_dir(base: str, name: str, path: str, text: str) -> str:
    root = os.path.join(base, name)
    full = os.path.join(root, *path.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)
    return root


class TranscriptMatchers(_env.EnvCase):
    """Every expectation accepts the plan's own transcript and refuses a mutation
    of it — at every checkpoint, not only the ones that have landed."""

    def _results(self) -> dict[str, Result]:
        cwd = self.tmp()
        _junit_dir(cwd, ".", _transcript.JUNIT_PATH, _JUNIT_OK)
        results: dict[str, Result] = {}
        last = None
        for step in _transcript.STEPS:
            if isinstance(step.action, _transcript.SameRun):
                results[step.id] = last
                continue
            if step.id in _PLAN_OUTPUT:
                code, stdout = _PLAN_OUTPUT[step.id]
                last = Result(str(step.action), code, stdout, "", cwd)
                results[step.id] = last
        return results

    def test_every_expectation_accepts_the_plan_transcript(self):
        results = self._results()
        for step in _transcript.STEPS:
            for expect in step.expect:
                with self.subTest(step=step.id, expect=expect.kind):
                    self.assertIn(step.id, results, "no plan output for this step")
                    self.assertIsNone(_transcript.problem(expect, results[step.id]))

    def test_every_expectation_refuses_a_mutation(self):
        results = self._results()
        base = self.tmp()
        checked = 0
        for step in _transcript.STEPS:
            for expect in step.expect:
                for label, mutated in _mutations(expect, results[step.id], base):
                    with self.subTest(step=step.id, expect=expect.kind, mutation=label):
                        self.assertIsNotNone(_transcript.problem(expect, mutated),
                                             f"{step.id}: {expect} accepted {label}")
                        checked += 1
        self.assertGreater(checked, 0)

    def test_no_command_is_a_problem(self):
        self.assertIsNotNone(_transcript.problem(_transcript.exit_code(0), None))

    def test_the_spine_rule_reads_both_entry_kinds(self):
        self.assertEqual(_transcript.entry_spine({"spine": "a" * 64}), "a" * 64)
        self.assertEqual(_transcript.entry_spine(
            {"kind": "control", "spine": "wrong", "static_parts": {"spine": "b" * 64}}), "b" * 64)
        self.assertIsNone(_transcript.entry_spine({"kind": "control"}))
        self.assertIsNone(_transcript.entry_spine({"spine": 3}))

    def test_a_committed_entry_that_does_not_parse_is_an_error(self):
        """Not a mismatch: a mismatch would quietly stop the starred
        expectations from being asserted."""
        project = self.tmp()
        rel = ".atompipe/verdicts/g/0123456789abcdef-01234567.json"
        path = os.path.join(project, *rel.split("/"))
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with self.assertRaises(AssertionError):
            _transcript.read_spines(project, [rel])
        self.assertEqual(_transcript.read_spines(project, []), [])


if __name__ == "__main__":
    unittest.main()
