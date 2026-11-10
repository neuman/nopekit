# SPDX-License-Identifier: Apache-2.0
"""nopekit.vcs — the only git edge.

Every question the spine asks git goes through this module, and nothing else in
``src/`` starts a git process (``test_meta.NoGitOutsideVcs`` refuses one). Phase 1
asks four things: which files a pack's ``selftest/`` holds (the control entry's
static walk), when an entry file was committed (a verdict's age on the page), the
commit a report was made at, and — from P2.5 on — who is signing. P3 adds
candidate worktrees here too, so the rules below are written once.

What each rule is for:

* **Argv form, ``-C root``.** No shell, so a path with a space or a quote is one
  argument, and every call names the repository it is asking about instead of
  inheriting the process's working directory.
* **A clean environment.** A git hook exports ``GIT_DIR``, ``GIT_INDEX_FILE`` and
  ``GIT_WORK_TREE`` pointing at the OUTER repository; an nopekit command run from
  a pre-commit hook would otherwise answer every question about the project with
  the hook's repository — its HEAD, its index, its files (the same trap
  ``tests/_env.py`` closes for the suite's own children). ``_STRIPPED`` lists the
  variables that pick a repository, an index, an object store or a config
  injection; everything else a user set on purpose (``GIT_CONFIG_GLOBAL``,
  ``GIT_AUTHOR_NAME``, ``GIT_SSH_COMMAND``) is theirs and stays.
* **A timeout** (``VCS_TIMEOUT_S``), because a hung ``core.fsmonitor`` daemon or a
  credential prompt would otherwise hang ``check`` with no output at all.
* **Never raising.** Every function answers ``None`` (or ``{}``, or ``False``)
  on any failure: no git on PATH, not a repository, a shallow or broken clone, a
  timeout, output it cannot parse. What slipped through while mapping Phase 1
  (tests:H11): ``verify.sh --dir`` checks a COPY of the bracket that has no
  ``.git``, and ``test_site`` runs ``doctor`` in-process in a bare temp
  directory — a git edge that raised there would have made "not a repository"
  a crash instead of a fact. Callers degrade: the selftest walk falls back to a
  filesystem walk, an age to ``null``, a commit to "not a git repository".

Only paths and hashes come back, never git's prose: ``LC_ALL=C`` keeps the
output parseable on a localized machine, and ``-z`` keeps a file name with a
newline or a non-ASCII character one exact name instead of a C-quoted string.

It imports nothing from nopekit (spec §3.1 allows ``util`` and no other), so
anything in the spine may ask git without an import cycle. It never reads the
wall clock (spec §0.6): a commit time is git's record, converted, not a reading
of now.

The writing half (architecture phase 6)
---------------------------------------
``round --commit`` (and the Stop hook's ``round --hook``) commits a round, and
``init`` starts a repository when the project sits in none; both go through
the functions below, so this module stays the only git edge. They keep every
rule above — argv form, ``-C root``, the same clean environment — and add a
contract of their own, because a write is not a question:

* **Explicit literal paths, always.** ``add`` and ``commit`` take a list of
  paths relative to ``root``, handed over on stdin (``--pathspec-from-file=-
  --pathspec-file-nul``: no argv limit, and a name with a newline stays one
  name) under ``--literal-pathspecs``. Never ``-A`` over the repository, never
  a directory pathspec: a hook that ran ``git add -A`` would commit a person's
  unrelated work as "round N" (designed against in phase 6). ``commit`` uses
  ``--only``, so what the person staged outside the list stays staged and
  uncommitted.
* **Never pushes.** ``push``, ``merge`` and ``tag`` come with candidates and
  merge (phase 4), under this same section.
* **Never writes config.** The only ``-c`` is ``user.useConfigOnly=true`` on
  the commit, so git never invents an identity from the host name — the rule
  ``ident`` keeps.
* **Never bypasses a hook.** No ``--no-verify``, no ``core.hooksPath``: a
  refusing hook is an answer, quoted back. ``GIT_EDITOR=:`` so no hook ever
  opens an editor no one can see.
* **A timeout of its own** (``VCS_WRITE_TIMEOUT_S``), ending in SIGTERM before
  SIGKILL: git removes its ``index.lock`` on SIGTERM, and a lock left behind
  would fail the person's next commit.
* **Never raises.** Each returns a ``Wrote``; ``why`` is the one place git's
  prose comes back — its first stderr line — and it is shown to a person,
  never parsed.

Three reads serve the writes (``changed_paths``, ``staged_paths``,
``operation``); they follow the read half's rules.

Reading a push (Eddy milestone 0)
---------------------------------
``nopekit validate`` and Eddy's pre-receive hook judge a push by what git
holds — refs, commits, trailers, trees, blobs, the objects new to the push —
so the read half gains the reads under "reading a push" below. They ask about
a repository that may be BARE: ``-C <bare>`` with ``GIT_DIR`` stripped finds
it, because a bare repository is its own git directory (verified from inside a
real pre-receive hook on git 2.34.1). None of them writes: ``empty_tree``
hashes with ``hash-object`` and no ``-w``, and ``cat-file``, ``ls-tree``,
``diff-tree``, ``rev-list``, ``for-each-ref``, ``show-ref``, ``log`` and
``rev-parse`` are questions. Each keeps the read half's rules — argv form,
the clean environment, the timeout, never raising, ``None`` on any failure —
and adds two of its own: every object id is validated against ``_HEX_OID``
BEFORE it reaches argv, so a bad sha answers ``None`` without starting git and
can never be read as an option or a revision expression; and output is ``-z``
or length-prefixed (``cat-file --batch``'s ``<oid> <type> <size>`` header),
never a line git might quote or truncate — except ``rev-list --objects``,
which has no ``-z`` on 2.34 and stops a name at its first newline; its name
is a label for a message and never a path a rule judges.

**The quarantine carve-out.** Inside pre-receive the pushed objects are
visible only through ``GIT_OBJECT_DIRECTORY`` (the quarantine) and
``GIT_ALTERNATE_OBJECT_DIRECTORIES`` (the main store), and ``_STRIPPED``
removes both, with ``GIT_QUARANTINE_PATH``: with them stripped, ``git -C
<bare> cat-file -t <new>`` answers "could not get object info" (verified by a
real push on git 2.34.1). So every read of a push takes an explicit
``objects: ObjectDirs | None``, and ``_ask`` sets exactly those two variables
on the STRIPPED environment for that one call. Nothing in the spine reads the
variables: the hook script reads them and hands them to the CLI as flags.
Rejected: loosening ``_STRIPPED`` for the two — every other caller would then
answer for the OUTER repository when run from a hook, the trap this docstring
opens with, reopened for the one caller that must not fall into it; and
reading the variables inside ``_ask`` when ``objects`` is ``None`` — an
environment read in the spine that no test could tell from the leak, and
``tests/_env`` strips them anyway, so no in-process row would ever have seen
the quarantine.
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Iterable, NamedTuple, Sequence

__all__ = [
    "VCS_TIMEOUT_S",
    "is_repo",
    "git_head",
    "ls_files",
    "ignored",
    "ident",
    "committer_ident",
    "show",
    "history",
    "model_dirty",
    "commit_times",
    # the writing half (architecture phase 6)
    "VCS_WRITE_TIMEOUT_S",
    "Wrote",
    "in_repository",
    "init_repo",
    "changed_paths",
    "staged_paths",
    "operation",
    "add",
    "commit",
    # reading a push (Eddy milestone 0): nopekit validate and the pre-receive hook
    "ObjectDirs",
    "TreeEntry",
    "Change",
    "CommitObject",
    "object_format",
    "commit_exists",
    "first_parents",
    "line_commits",
    "ref_tips",
    "ref_exists",
    "ref_trees",
    "commit_objects",
    "trailer_lines",
    "tree_of",
    "tree_entries",
    "tree_listing",
    "tree_diff",
    "blobs",
    "object_sizes",
    "new_objects",
    "tree_objects",
    "empty_tree",
]


#: Seconds before a git call is abandoned and answered ``None``. Why 10: far above
#: an honest answer — measured 2026-09-27, ``ls-files --cached --others`` and a
#: whole-history ``log --name-only`` over this repository take 0.01-0.02 s warm,
#: and ``git ls-files`` on a cold, large repository (an index the page cache has
#: never seen) takes seconds — so only a hang reaches it. Rejected: unbounded (a
#: hung fsmonitor hook or credential helper hangs ``check`` with no output, and
#: the user cannot tell nopekit from git); 1-2 s (a network filesystem or a cold
#: cache legitimately takes that long, and a timeout is not a crash — it is read
#: as "not a repository", which quietly swaps the selftest walk for the
#: filesystem walk).
VCS_TIMEOUT_S = 10

#: Removed from the environment of every git call. Two sources, unioned:
#: the spec's list (§4 U13), and git's own ``git rev-parse --local-env-vars``
#: (git 2.43) — the variables git itself clears when it runs a sub-git in ANOTHER
#: repository (a submodule), which is exactly this module's situation. The union
#: is what ``VcsIsTheOnlyGitEdge`` checks against the installed git's list, so a
#: git that grows a new repository-local variable turns a test red instead of
#: leaking. Why each group:
#:
#: * which repository: GIT_DIR, GIT_WORK_TREE, GIT_IMPLICIT_WORK_TREE,
#:   GIT_COMMON_DIR, GIT_PREFIX, GIT_NAMESPACE, GIT_CEILING_DIRECTORIES (a
#:   foreign ceiling can stop discovery short of the project's ``.git``),
#:   GIT_INTERNAL_SUPER_PREFIX (git 2.34 lists it in ``--local-env-vars``: the
#:   superproject's prefix a submodule sub-git is handed — what slipped
#:   through: the list above was drawn from git 2.43, which had dropped it, and
#:   the clean-environment test was red on a 2.34 machine for exactly that one
#:   name until Eddy milestone 0 added it);
#: * which index and objects: GIT_INDEX_FILE, GIT_OBJECT_DIRECTORY,
#:   GIT_ALTERNATE_OBJECT_DIRECTORIES, GIT_GRAFT_FILE, GIT_SHALLOW_FILE,
#:   GIT_NO_REPLACE_OBJECTS, GIT_REPLACE_REF_BASE, GIT_QUARANTINE_PATH (a
#:   pre-receive hook sets it with the object directories);
#: * injected config: GIT_CONFIG_PARAMETERS (how ``git -c`` reaches a hook's
#:   children), GIT_CONFIG_COUNT (its newer spelling — with the count gone, git
#:   reads no GIT_CONFIG_KEY_n/VALUE_n), GIT_CONFIG (a legacy single-file
#:   override).
#:
#: Rejected: stripping every ``GIT_*`` as ``tests/_env.py`` does. The suite wants a
#: machine-free child; the spine wants the USER's git — their ``GIT_CONFIG_GLOBAL``,
#: their ``GIT_AUTHOR_NAME`` (``ident`` must agree with the commit ``git commit``
#: would make), their ``GIT_SSH_COMMAND`` once P3 fetches. Also rejected: asking
#: the installed git for its list at runtime (a second process on every call, to
#: learn a list git changes a few times a decade and that a test already pins).
_STRIPPED = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_COMMON_DIR",
    "GIT_PREFIX", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES", "GIT_INTERNAL_SUPER_PREFIX",
    "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE", "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE", "GIT_QUARANTINE_PATH",
    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_CONFIG",
})

#: Set on every git call. ``GIT_OPTIONAL_LOCKS=0``: a read-only question must
#: never take ``.git/index.lock`` — ``git status`` refreshes the index
#: opportunistically, and a lock held by nopekit fails the user's concurrent
#: ``git commit`` with "index.lock exists". ``GIT_TERMINAL_PROMPT=0``: git never
#: asks (stdin is closed as well, so a question fails instead of hanging).
#: ``LC_ALL=C``: the output this module parses is git's, not a translation of it.
_SET = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}

_HEX_OID = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")

#: A commit header in ``commit_times``'s log: \x01<committer epoch>\x02<decorations>.
#: Neither control byte occurs in a decimal time or a ref name. A file name that
#: BEGINS with \x01 is legal and absurd; it reads as a header that does not parse,
#: and the whole answer becomes unknown (``{}``) rather than a wrong time.
_LOG_FORMAT = "%x01%ct%x02%D"


def _environment() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED}
    env.update(_SET)
    return env


def _ask(root: str | os.PathLike[str], args: Sequence[str], *,
         literal_pathspecs: bool = False,
         stdin_bytes: bytes | None = None,
         objects: ObjectDirs | None = None,
         timeout: float | None = None) -> tuple[int | None, bytes, bytes]:
    """``git -C root <args>``: ``(returncode, stdout, stderr)``; ``(None, b"",
    b"timeout")`` when it did not finish and ``(None, b"", b"")`` when it did
    not start. The one place a read-only git process starts.

    ``--literal-pathspecs`` for calls that take paths: a file literally named
    ``*.stl`` or ``:(top)x`` is a path, not a pattern that quietly matches
    something else.

    Eddy milestone 0: ``stdin_bytes`` is written to git's stdin and the pipe
    closed (``cat-file --batch``'s oids, ``hash-object --stdin``'s bytes);
    with none, stdin stays ``DEVNULL``, so a question never waits on input.
    ``objects`` sets ``GIT_OBJECT_DIRECTORY`` and
    ``GIT_ALTERNATE_OBJECT_DIRECTORIES`` on the STRIPPED environment, for this
    call only (the quarantine carve-out in the module docstring). ``timeout``
    replaces ``VCS_TIMEOUT_S`` for this call.
    """
    limit = VCS_TIMEOUT_S if timeout is None else timeout
    try:
        argv = ["git", "-C", os.fspath(root)]
        if literal_pathspecs:
            argv.append("--literal-pathspecs")
        argv.extend(os.fspath(a) for a in args)
        env = _environment()
        if objects is not None:
            env["GIT_OBJECT_DIRECTORY"] = os.fspath(objects.directory)
            env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = os.fspath(objects.alternates)
        feed = ({"input": bytes(stdin_bytes)} if stdin_bytes is not None
                else {"stdin": subprocess.DEVNULL})
        proc = subprocess.run(
            argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=limit, check=False, **feed)
    except subprocess.TimeoutExpired:
        return None, b"", b"timeout"
    except Exception:  # noqa: BLE001 — never raises: see the module docstring
        # FileNotFoundError (no git), PermissionError (a noexec PATH entry),
        # ValueError (a NUL byte in a path), TypeError (a path that is not
        # one, an ObjectDirs member that is not a path). Each means "git did
        # not answer", which is the answer.
        return None, b"", b""
    return proc.returncode, proc.stdout, proc.stderr


def _git(root: str | os.PathLike[str], args: Sequence[str], *,
         literal_pathspecs: bool = False,
         stdin_bytes: bytes | None = None,
         objects: ObjectDirs | None = None,
         timeout: float | None = None) -> bytes | None:
    """``git -C root <args>``'s stdout, or ``None`` for any failure at all."""
    code, out, _err = _ask(root, args, literal_pathspecs=literal_pathspecs,
                           stdin_bytes=stdin_bytes, objects=objects, timeout=timeout)
    return out if code == 0 else None


def _text(out: bytes | None) -> str | None:
    if out is None:
        return None
    try:
        return out.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None


def is_repo(path: str | os.PathLike[str]) -> bool:
    """``path`` is inside a git work tree. ``False`` for anything else, including
    no git at all: the caller's question is "can I ask git about this?"."""
    return _text(_git(path, ["rev-parse", "--is-inside-work-tree"])) == "true"


def git_head(root: str | os.PathLike[str]) -> str | None:
    """The full object id of ``HEAD`` for the repository holding ``root``;
    ``None`` outside a repository, before the first commit, or on any failure."""
    oid = _text(_git(root, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"]))
    return oid if oid and _HEX_OID.match(oid) else None


def ls_files(root: str | os.PathLike[str], relpaths: Iterable[str], *,
             others: bool = True) -> list[str] | None:
    """The files git knows under ``relpaths`` (all of ``root`` when empty), as
    POSIX paths relative to ``root``, sorted and unique; ``None`` on failure.

    With ``others`` (the default): tracked files plus untracked files that no
    ignore rule excludes (``--cached --others --exclude-standard``) — the files
    the author would commit, which is what the control entry's selftest walk
    digests. A pack's ``selftest/`` file created but not yet added
    still counts: a control input the author is editing is an input.

    What git reports is passed through, not re-checked against the disk: a
    tracked file deleted from the working tree is still listed (its digest reads
    missing, which is a change), and an untracked nested repository appears once
    as ``name/``. Duplicates — one path per stage while a merge is unresolved —
    are collapsed.
    """
    paths = _paths(relpaths)
    if paths is None:
        return None
    args = ["ls-files", "-z"]
    if others:
        args += ["--cached", "--others", "--exclude-standard"]
    args += ["--", *paths]
    out = _git(root, args, literal_pathspecs=True)
    if out is None:
        return None
    return sorted({os.fsdecode(name) for name in out.split(b"\0") if name})


def ignored(root: str | os.PathLike[str], relpaths: Iterable[str]) -> list[str] | None:
    """The files under ``relpaths`` that git ignores and does not track, as
    POSIX paths relative to ``root``, sorted; ``None`` on failure or outside a
    repository. A tracked file is never in it, whatever an ignore rule says
    (it is already public), and a path with no file is never in it either.
    `site build` asks it before it publishes evidence a verdict names by a
    path in the project (architecture phase 5: a git-ignored ``.env`` named as
    evidence was published)."""
    paths = _paths(relpaths)
    if not paths:
        return None if paths is None else []
    out = _git(root, ["ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--",
                      *paths], literal_pathspecs=True)
    if out is None:
        return None
    return sorted({os.fsdecode(name) for name in out.split(b"\0") if name})


def _ident(root: str | os.PathLike[str], var: str) -> str | None:
    line = _text(_git(root, ["-c", "user.useConfigOnly=true", "var", var]))
    if not line:
        return None
    # "Name <email> 1790560187 -0700": the identity ends at the last '>'. What
    # follows is git's clock, and a clock does not belong in an identity.
    end = line.rfind(">")
    who = line[:end + 1].strip() if end >= 0 else ""
    return who if "<" in who else None


def ident(root: str | os.PathLike[str]) -> str | None:
    """``Name <email>`` — the author identity a commit in ``root`` would carry,
    timestamp dropped; ``None`` when none is configured.

    ``user.useConfigOnly=true`` is the point: without it git invents
    ``user@host.domain`` from the machine, and a signature that names a hostname
    nobody chose is a signature nobody made (D-12; P2.5's signer and P3's
    preference records refuse on ``None``).
    """
    return _ident(root, "GIT_AUTHOR_IDENT")


def committer_ident(root: str | os.PathLike[str]) -> str | None:
    """``Name <email>`` — the COMMITTER a commit in ``root`` would carry
    (``committer.name``/``committer.email`` config or ``GIT_COMMITTER_*`` over
    ``user.*``), timestamp dropped; ``None`` when none is configured.

    For the round's identity gate (review of architecture phase 6): the gate
    read the author alone, and a CI runner or wrapper that exported
    ``GIT_COMMITTER_*`` put a committer on a round's commit that the project's
    ``people`` never named, with the paragraph naming only the author."""
    return _ident(root, "GIT_COMMITTER_IDENT")


def show(root: str | os.PathLike[str], relpath: str, rev: str = "HEAD") -> bytes | None:
    """The bytes commit ``rev`` (``HEAD``, or a full object id from
    :func:`history`) holds at ``relpath`` (``git show <rev>:<path>``), or
    ``None`` — outside a repository, before a first commit, or a path the
    commit does not hold. Read-only.

    For the strict results reader's refusal (P2.5a, critique 6 of its design):
    "git checkout -- results/C1.json" discards whatever the commit does not
    hold, and a fail recorded since is exactly that — so the refusal compares
    the two and names each fail the restore would drop. A path with a ``:`` or
    a leading ``-``, or a ``rev`` that is neither ``HEAD`` nor an object id, is
    refused as no answer rather than handed to git as syntax."""
    rel = _normal(os.fspath(relpath))
    if not rel or rel.startswith("-") or ":" in rel:
        return None
    if rev != "HEAD" and not _HEX_OID.match(str(rev)):
        return None
    return _git(root, ["show", f"{rev}:./{rel}"])


def history(root: str | os.PathLike[str], relpath: str, limit: int) -> list[str]:
    """The full object ids of the commits that changed ``relpath``, newest
    first, at most ``limit`` — ``[]`` outside a repository, for a path no commit
    holds, or on any failure. Read-only.

    For the results reader's refusal (review of P2.5a): a hand edit that was
    COMMITTED leaves ``HEAD`` holding the same broken bytes, so "git checkout
    -- results/C2.json" changed nothing and every command refused again; the
    refusal walks these back to the newest version that verifies. Pinned
    against the user's log config as ``commit_times`` is (``log.follow`` would
    walk a rename into another claim's file)."""
    rel = _normal(os.fspath(relpath))
    if not rel or rel.startswith("-") or limit < 1:
        return []
    out = _text(_git(root, ["-c", "log.follow=false", "-c", "log.showSignature=false",
                            "log", f"-n{int(limit)}", "--no-renames", "--format=%H", "--",
                            rel], literal_pathspecs=True))
    if not out:
        return []
    return [line for line in out.splitlines() if _HEX_OID.match(line)]


def model_dirty(root: str | os.PathLike[str], relpaths: Iterable[str]) -> bool | None:
    """Whether any of ``relpaths`` (the model's files) differs from the last
    commit — staged, unstaged or untracked — or ``None`` outside a repository.
    Read-only (``git status --porcelain``). For ``claim physical``'s prompt
    (P2.5a, R4 of its design): the moment a person attests that the object in
    hand was built from this design is the moment to see that the design moved
    since the last commit."""
    paths = _paths(relpaths)
    if not paths:
        return None if paths is None else False
    out = _git(root, ["status", "--porcelain", "-z", "--untracked-files=all", "--", *paths],
               literal_pathspecs=True)
    if out is None:
        return None
    return bool(out.strip(b"\0"))


def _iso(epoch: int) -> str:
    # The shape util.utcnow_iso stamps, so an age computed from an obs `when`
    # and one computed from a commit time read the same.
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _paths(relpaths: Iterable[str] | str | os.PathLike[str]) -> list[str] | None:
    """``relpaths`` as a list of strings; a lone path is one path, never its
    characters (``ls_files(root, "selftest")`` would otherwise ask about ``s``,
    ``e``, ``l``...). ``None`` when it is not paths at all."""
    if isinstance(relpaths, (str, bytes, os.PathLike)):
        relpaths = [relpaths]
    try:
        return [os.fsdecode(p) for p in relpaths]
    except TypeError:
        return None


def _normal(relpath: str) -> str:
    path = relpath.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.rstrip("/")
    return "" if path == "." else path


def commit_times(root: str | os.PathLike[str], relpaths: Iterable[str]) -> dict[str, str]:
    """``{relpath: "2026-09-27T14:02:11Z"}`` — when the newest commit touching
    each path (a file, or anything under a directory) was committed.

    A path appears only when git knows its time: untracked, outside ``root``, or
    any failure → absent, and the caller renders the age as unknown, never 0.

    One ``git log`` walk for all paths, newest first. Committer time, not author
    time: a rebased entry reached this history when it was committed here.

    **Shallow clones.** CI checks out one commit, whose diff against nothing
    "adds" every file. Attributing a file to that boundary commit would date an
    entry committed a year ago to the clone's tip — younger than it is, the
    flattering direction on a page where age fades a verdict. So a path whose
    newest touching commit is a shallow boundary (git decorates it ``grafted``)
    has no time.
    """
    paths = _paths(relpaths)
    if not paths:
        return {}
    wanted = {p: _normal(p) for p in paths}
    # Pinned against the user's log config, each of which changes what this
    # parses: log.follow (renames under one path), log.showSignature (gpg lines
    # in the output), log.showRoot (the root commit's files) — `--root` restores
    # it. "" is not a pathspec git accepts; "." is the same question.
    args = ["-c", "log.follow=false", "-c", "log.showSignature=false",
            "log", "-z", "--root", "--no-renames", "--relative", "--name-only",
            "--decorate-refs-exclude=*", f"--format={_LOG_FORMAT}",
            "--", *sorted({path or "." for path in wanted.values()})]
    out = _git(root, args, literal_pathspecs=True)
    if out is None:
        return {}

    # file -> (newest real commit time, newest boundary commit time)
    newest: dict[str, list[int]] = {}
    when, grafted, after_header = 0, False, False
    for token in out.split(b"\0"):
        if token.startswith(b"\x01"):
            head, _, decorations = token[1:].partition(b"\x02")
            try:
                when = int(head)
            except ValueError:
                return {}
            grafted = b"grafted" in decorations.split(b", ")
            after_header = True
            continue
        if after_header and token.startswith(b"\n"):
            token = token[1:]          # git separates a header from its names with "\n"
        after_header = False
        if not token:
            continue
        slot = newest.setdefault(os.fsdecode(token), [-1, -1])
        index = 1 if grafted else 0
        slot[index] = max(slot[index], when)

    result: dict[str, str] = {}
    for original, path in wanted.items():
        real, boundary = -1, -1
        for name, (r, b) in newest.items():
            if not path or name == path or name.startswith(path + "/"):
                real, boundary = max(real, r), max(boundary, b)
        if real >= 0 and real >= boundary:
            result[original] = _iso(real)
    return result


# --------------------------------------------------------------------------- #
# reading a push (Eddy milestone 0): nopekit validate and the pre-receive hook
# --------------------------------------------------------------------------- #
class ObjectDirs(NamedTuple):
    """The object stores a pre-receive hook is handed, passed to a read
    explicitly (the carve-out in the module docstring): ``directory`` is
    ``GIT_OBJECT_DIRECTORY`` as the hook received it — the quarantine the
    pushed objects sit in until the hook accepts — and ``alternates`` is
    ``GIT_ALTERNATE_OBJECT_DIRECTORIES``, the main store, in git's
    ``os.pathsep``-joined form; ``""`` for none."""

    directory: str
    alternates: str = ""


class TreeEntry(NamedTuple):
    """One ``ls-tree -l`` record: ``mode`` (``100644``, ``100755``, ``120000``
    a symlink, ``040000`` a tree, ``160000`` a gitlink), ``type`` (``blob``,
    ``tree``, ``commit``), ``oid``, ``size`` (``None`` for a tree or a gitlink,
    which ``-l`` prints as ``-``) and ``path`` relative to the tree asked
    about, exact: ``-z`` keeps a name with a newline one name."""

    mode: str
    type: str
    oid: str
    size: int | None
    path: str


class Change(NamedTuple):
    """One ``diff-tree -r --no-renames`` record: ``status`` ``A``, ``M``,
    ``D`` or ``T`` (a type change — a file that became a symlink or a
    gitlink); modes and oids are git's raw ones, ``000000`` and all zeros on
    the side that has no entry."""

    status: str
    path: str
    old_mode: str
    new_mode: str
    old_oid: str
    new_oid: str


class CommitObject(NamedTuple):
    """A commit as ``cat-file --batch`` serves it: its ``tree``, its
    ``parents`` in order (the first is the first parent — the one a line is
    made of), and ``message``, the raw bytes after the first blank line,
    undecoded: a control byte or an encoding a trailer parser would smooth
    over is still there for the validator to refuse."""

    sha: str
    tree: str
    parents: tuple[str, ...]
    message: bytes


def _oid(value: object) -> str | None:
    """``value`` as a full object id, or ``None``. Checked BEFORE argv: a sha
    is the one input a pusher chooses that these reads hand to git, and
    ``-C``, ``--not`` or ``^{commit}`` beside an unchecked one is an option
    or a revision expression git would honour."""
    return value if isinstance(value, str) and _HEX_OID.match(value) else None


def _oids(values: object) -> list[str] | None:
    """Every entry of ``values`` as a full object id, in order, or ``None``
    when any is not (a lone string is its characters, which fail)."""
    try:
        items = list(values)
    except TypeError:
        return None
    out: list[str] = []
    for value in items:
        oid = _oid(value)
        if oid is None:
            return None
        out.append(oid)
    return out


def _count(limit: object) -> int | None:
    """``limit`` as a non-negative ``int`` for ``--max-count``, or ``None``."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        return None
    return limit


def _ref_arg(ref: object) -> str | None:
    """A ref or a ref prefix fit for argv: a string that cannot be read as an
    option. Shape beyond that is the caller's rule, not this edge's."""
    return ref if isinstance(ref, str) and ref and not ref.startswith("-") else None


def _stdin_lines(oids: Sequence[str]) -> bytes:
    return b"".join(oid.encode("ascii") + b"\n" for oid in oids)


def _batch(out: bytes) -> list[tuple[str, str, bytes]] | None:
    """``cat-file --batch`` output as ``[(oid, type, content)]`` — the
    length-prefixed form, so a blob holding ``"\\n<oid> blob 3\\n"`` is one
    blob, not two. A ``<oid> missing`` line is ``(oid, "missing", b"")``;
    anything that does not parse makes the whole answer ``None``."""
    items: list[tuple[str, str, bytes]] = []
    pos, end = 0, len(out)
    while pos < end:
        newline = out.find(b"\n", pos)
        if newline < 0:
            return None
        header = out[pos:newline].split(b" ")
        pos = newline + 1
        try:
            oid = _oid(header[0].decode("ascii"))
        except UnicodeDecodeError:
            return None
        if oid is None:
            return None
        if len(header) == 2 and header[1] in (b"missing", b"ambiguous"):
            items.append((oid, "missing", b""))
            continue
        if len(header) != 3:
            return None
        try:
            kind = header[1].decode("ascii")
            size = int(header[2])
        except (UnicodeDecodeError, ValueError):
            return None
        if size < 0 or pos + size >= end or out[pos + size:pos + size + 1] != b"\n":
            return None
        items.append((oid, kind, out[pos:pos + size]))
        pos += size + 1
    return items


def _cat_file(root: str | os.PathLike[str], oids: Sequence[str], want: str, *,
              objects: ObjectDirs | None) -> dict[str, bytes] | None:
    """``{oid: content}`` for ``oids`` through one ``cat-file --batch``; a
    ``missing`` line or an object of another type than ``want`` makes the
    whole answer ``None`` — the caller asked for blobs (or commits, or trees)
    and was handed something else, which is not a partial answer but a
    wrong one."""
    out = _git(root, ["cat-file", "--batch"], stdin_bytes=_stdin_lines(oids), objects=objects)
    if out is None:
        return None
    items = _batch(out)
    if items is None:
        return None
    found: dict[str, bytes] = {}
    for oid, kind, content in items:
        if kind != want:
            return None
        found[oid] = content
    return found


def _parse_commit(sha: str, raw: bytes) -> CommitObject | None:
    head, sep, message = raw.partition(b"\n\n")
    if not sep:
        return None
    tree: str | None = None
    parents: list[str] = []
    for line in head.split(b"\n"):
        # A continuation line (gpgsig's, mergetag's) starts with a space and
        # is no header; a header this does not know is passed over.
        try:
            if line.startswith(b"tree "):
                if tree is not None:
                    return None
                tree = _oid(line[5:].decode("ascii"))
                if tree is None:
                    return None
            elif line.startswith(b"parent "):
                parent = _oid(line[7:].decode("ascii"))
                if parent is None:
                    return None
                parents.append(parent)
        except UnicodeDecodeError:
            return None
    if tree is None:
        return None
    return CommitObject(sha, tree, tuple(parents), message)


def _parse_tree(raw: bytes, hash_bytes: int) -> list[tuple[str, str, str]] | None:
    """A raw tree object: ``<mode> <name>\\0<raw oid>`` repeated, the oid
    ``hash_bytes`` long (20 for sha1, 32 for sha256)."""
    entries: list[tuple[str, str, str]] = []
    pos, end = 0, len(raw)
    while pos < end:
        space = raw.find(b" ", pos)
        nul = raw.find(b"\0", space + 1) if space >= 0 else -1
        if space < 0 or nul < 0 or nul + 1 + hash_bytes > end:
            return None
        try:
            mode = raw[pos:space].decode("ascii")
        except UnicodeDecodeError:
            return None
        if not mode or not mode.isdigit():
            return None
        name = os.fsdecode(raw[space + 1:nul])
        oid = raw[nul + 1:nul + 1 + hash_bytes].hex()
        entries.append((mode, name, oid))
        pos = nul + 1 + hash_bytes
    return entries


def _ls_tree(out: bytes) -> list[TreeEntry] | None:
    """``ls-tree -z -l`` records: ``<mode> <type> <oid> <size>\\t<path>\\0``,
    the size right-aligned and ``-`` for a tree or a gitlink."""
    entries: list[TreeEntry] = []
    for record in out.split(b"\0"):
        if not record:
            continue
        meta, sep, path = record.partition(b"\t")
        fields = meta.split()
        if not sep or len(fields) != 4:
            return None
        try:
            mode, kind, oid_text, size_text = (f.decode("ascii") for f in fields)
        except UnicodeDecodeError:
            return None
        oid = _oid(oid_text)
        if oid is None or kind not in ("blob", "tree", "commit"):
            return None
        size: int | None = None
        if size_text != "-":
            try:
                size = int(size_text)
            except ValueError:
                return None
            if size < 0:
                return None
        entries.append(TreeEntry(mode, kind, oid, size, os.fsdecode(path)))
    return entries


def _hex_lines(out: bytes | None) -> list[str] | None:
    """Every line of ``out`` as an object id; ``None`` when one is not."""
    if out is None:
        return None
    found: list[str] = []
    for line in out.split(b"\n"):
        if not line:
            continue
        try:
            oid = _oid(line.decode("ascii"))
        except UnicodeDecodeError:
            return None
        if oid is None:
            return None
        found.append(oid)
    return found


def object_format(root: str | os.PathLike[str], *,
                  objects: ObjectDirs | None = None) -> str | None:
    """``"sha1"`` or ``"sha256"`` — the repository's hash (``rev-parse
    --show-object-format``), which decides the empty tree's id and the width
    of every oid a tree holds; ``None`` outside a repository."""
    text = _text(_git(root, ["rev-parse", "--show-object-format"], objects=objects))
    return text if text in ("sha1", "sha256") else None


def commit_exists(root: str | os.PathLike[str], sha: str, *,
                  objects: ObjectDirs | None = None) -> bool | None:
    """Whether ``sha`` names a commit the repository (or the quarantine) holds:
    ``True`` on exit 0 with the same id echoed back, ``False`` on exit 1 (no
    such object, or one that is not a commit), ``None`` otherwise — a bad
    sha included, without starting git."""
    oid = _oid(sha)
    if oid is None:
        return None
    code, out, _err = _ask(root, ["rev-parse", "--verify", "--quiet", f"{oid}^{{commit}}"],
                           objects=objects)
    if code == 0:
        return True if _text(out) == oid else None
    return False if code == 1 else None


def first_parents(root: str | os.PathLike[str], tip: str, *, limit: int,
                  objects: ObjectDirs | None = None) -> list[str] | None:
    """The first-parent chain from ``tip`` (``rev-list --first-parent``),
    newest first with ``tip`` itself at row 0, at most ``limit + 1`` long, so
    the caller reads ``len > limit`` as "over the bound" without a second
    walk; ``None`` on any failure. The chain is what a line IS under Eddy: a
    merge whose second parent is the old tip is not on it, which is how E04
    refuses what ``merge-base --is-ancestor`` would have called a
    fast-forward."""
    oid, count = _oid(tip), _count(limit)
    if oid is None or count is None:
        return None
    return _hex_lines(_git(root, ["rev-list", "--first-parent", f"--max-count={count + 1}", oid],
                           objects=objects))


def line_commits(root: str | os.PathLike[str], tips: Sequence[str], *, limit: int,
                 objects: ObjectDirs | None = None) -> list[str] | None:
    """The union of the first-parent chains of every ``tips`` entry, at most
    ``limit`` commits in all, in one ``rev-list --first-parent`` walk; ``[]``
    for no tips, without starting git."""
    oids, count = _oids(tips), _count(limit)
    if oids is None or count is None:
        return None
    if not oids:
        return []
    return _hex_lines(_git(root, ["rev-list", "--first-parent", f"--max-count={count}", *oids],
                           objects=objects))


def ref_tips(root: str | os.PathLike[str], prefix: str, *,
             objects: ObjectDirs | None = None) -> dict[str, str] | None:
    """``{refname: oid}`` for every ref under ``prefix`` (``refs/heads/cand/``,
    ``refs/``): ``for-each-ref`` with a NUL between name and id, so each pair
    is read exactly; ``{}`` when none; ``None`` on failure or a ``prefix``
    that could be read as an option."""
    pattern = _ref_arg(prefix)
    if pattern is None:
        return None
    out = _git(root, ["for-each-ref", "--format=%(refname)%00%(objectname)", pattern],
               objects=objects)
    if out is None:
        return None
    tips: dict[str, str] = {}
    for line in out.split(b"\n"):
        if not line:
            continue
        name, sep, oid_bytes = line.partition(b"\0")
        try:
            oid = _oid(oid_bytes.decode("ascii"))
        except UnicodeDecodeError:
            return None
        if not sep or not name or oid is None:
            return None
        tips[os.fsdecode(name)] = oid
    return tips


def ref_exists(root: str | os.PathLike[str], ref: str) -> bool | None:
    """Whether the full ``ref`` (``refs/heads/spec``) exists: ``show-ref
    --verify --quiet``'s exit 0 / 1; ``None`` otherwise."""
    name = _ref_arg(ref)
    if name is None:
        return None
    code, _out, _err = _ask(root, ["show-ref", "--verify", "--quiet", name])
    if code == 0:
        return True
    return False if code == 1 else None


def ref_trees(root: str | os.PathLike[str], ref: str, *, limit: int,
              objects: ObjectDirs | None = None) -> list[str] | None:
    """The tree id of every commit on ``ref``'s first-parent chain, newest
    first, at most ``limit`` (``log --first-parent --format=%T``); ``[]``
    when `ref_exists` says the ref is absent (an empty ``spec`` branch is a
    valid start); ``None`` on failure. Pinned against ``log.showSignature``
    as every ``log`` read here is: a gpg line would read as no tree."""
    name, count = _ref_arg(ref), _count(limit)
    if name is None or count is None:
        return None
    present = ref_exists(root, name)
    if present is None:
        return None
    if not present:
        return []
    return _hex_lines(_git(root, ["-c", "log.showSignature=false", "log", "--first-parent",
                                  "--format=%T", f"--max-count={count}", name, "--"],
                           objects=objects))


def commit_objects(root: str | os.PathLike[str], shas: Sequence[str], *,
                   objects: ObjectDirs | None = None) -> dict[str, CommitObject] | None:
    """``{sha: CommitObject}`` for ``shas`` through one ``cat-file --batch``
    (the shas on stdin, one per line); a ``missing`` line, an object that is
    not a commit, or a commit that does not parse makes the whole answer
    ``None``. ``{}`` for no shas, without starting git."""
    oids = _oids(shas)
    if oids is None:
        return None
    if not oids:
        return {}
    raw = _cat_file(root, oids, "commit", objects=objects)
    if raw is None:
        return None
    found: dict[str, CommitObject] = {}
    for sha, content in raw.items():
        parsed = _parse_commit(sha, content)
        if parsed is None:
            return None
        found[sha] = parsed
    return found


#: ``trailer_lines``'s log format: \x01<sha>\n<git's trailer block>\x02 per
#: commit. The two control bytes are refused in any message the validator
#: trusts (E12 reads the raw message first), and a block holding either here
#: does not parse — the whole answer is ``None``, never a trailer misread.
_TRAILER_FORMAT = "%x01%H%x0a%(trailers:only,unfold)%x02"


def trailer_lines(root: str | os.PathLike[str], shas: Sequence[str], *,
                  objects: ObjectDirs | None = None) -> dict[str, list[tuple[str, str]]] | None:
    """``{sha: [(key, value), ...]}`` — git's OWN reading of each commit's
    trailer block (``%(trailers:only,unfold)``: the last paragraph when every
    line of it is a trailer, folded continuation lines joined), each line
    split at its first ``:`` and both halves stripped; ``[]`` for a commit
    with no block; ``None`` when a block does not parse or git did not
    answer. The validator compares this with its own scan of the raw message,
    so a ``Nope-`` line git reads as prose is refused rather than read two
    ways. ``{}`` for no shas, without starting git."""
    oids = _oids(shas)
    if oids is None:
        return None
    if not oids:
        return {}
    out = _git(root, ["-c", "log.showSignature=false", "log", "--no-walk=unsorted", "--stdin",
                      f"--format={_TRAILER_FORMAT}"],
               stdin_bytes=_stdin_lines(oids), objects=objects)
    if out is None or not out.startswith(b"\x01"):
        return None
    found: dict[str, list[tuple[str, str]]] = {}
    for chunk in out.split(b"\x01")[1:]:
        if not chunk.endswith(b"\x02\n") or chunk.count(b"\x02") != 1:
            return None
        head, sep, block = chunk[:-2].partition(b"\n")
        try:
            sha = _oid(head.decode("ascii"))
        except UnicodeDecodeError:
            return None
        if not sep or sha is None:
            return None
        lines: list[tuple[str, str]] = []
        for line in block.split(b"\n"):
            if not line:
                continue
            key, colon, value = line.partition(b":")
            if not colon:
                return None
            lines.append((os.fsdecode(key).strip(), os.fsdecode(value).strip()))
        found[sha] = lines
    return found


def tree_of(root: str | os.PathLike[str], sha: str, *,
            objects: ObjectDirs | None = None) -> str | None:
    """The tree id of commit ``sha`` (``rev-parse --verify --quiet
    <sha>^{tree}``); ``None`` for no such commit or any failure."""
    oid = _oid(sha)
    if oid is None:
        return None
    return _oid(_text(_git(root, ["rev-parse", "--verify", "--quiet", f"{oid}^{{tree}}"],
                           objects=objects)))


def tree_entries(root: str | os.PathLike[str], tree: str, *,
                 objects: ObjectDirs | None = None) -> list[TreeEntry] | None:
    """The top-level entries of ``tree`` (``ls-tree -z -l``, not recursive):
    its files, its subtrees (size ``None``) and its gitlinks."""
    oid = _oid(tree)
    if oid is None:
        return None
    out = _git(root, ["ls-tree", "-z", "-l", oid], objects=objects)
    return None if out is None else _ls_tree(out)


def tree_listing(root: str | os.PathLike[str], tree: str, *,
                 objects: ObjectDirs | None = None) -> list[TreeEntry] | None:
    """Every blob, symlink and gitlink under ``tree`` (``ls-tree -r -z -l``),
    with mode, oid, size and full path; subtrees themselves are not rows."""
    oid = _oid(tree)
    if oid is None:
        return None
    out = _git(root, ["ls-tree", "-r", "-z", "-l", oid], objects=objects)
    return None if out is None else _ls_tree(out)


def tree_diff(root: str | os.PathLike[str], a: str, b: str, *,
              objects: ObjectDirs | None = None) -> list[Change] | None:
    """What changed from tree ``a`` to tree ``b`` (``diff-tree -r -z
    --no-renames``), as raw ``:<oldmode> <newmode> <oldoid> <newoid> <S>``
    records: never a rename or a copy, so a file moved reads as ``D`` and
    ``A`` and a rule on one path sees it. ``a`` may be the empty tree, which
    makes every entry of ``b`` an ``A``."""
    old, new = _oid(a), _oid(b)
    if old is None or new is None:
        return None
    out = _git(root, ["diff-tree", "-r", "-z", "--no-renames", old, new], objects=objects)
    if out is None:
        return None
    tokens = out.split(b"\0")
    if tokens and not tokens[-1]:
        tokens.pop()
    if len(tokens) % 2:
        return None
    changes: list[Change] = []
    for meta, path in zip(tokens[::2], tokens[1::2]):
        fields = meta.split(b" ")
        if len(fields) != 5 or not fields[0].startswith(b":"):
            return None
        try:
            old_mode, new_mode, old_oid, new_oid, status = (f.decode("ascii") for f in fields)
        except UnicodeDecodeError:
            return None
        old_mode = old_mode[1:]
        if (_oid(old_oid) is None or _oid(new_oid) is None or len(status) != 1
                or not old_mode.isdigit() or not new_mode.isdigit()):
            return None
        changes.append(Change(status, os.fsdecode(path), old_mode, new_mode, old_oid, new_oid))
    return changes


def blobs(root: str | os.PathLike[str], oids: Sequence[str], *,
          objects: ObjectDirs | None = None) -> dict[str, bytes] | None:
    """``{oid: bytes}`` for ``oids`` through one ``cat-file --batch``; a
    ``missing`` line or an object that is not a blob makes the whole answer
    ``None``. ``{}`` for no oids, without starting git."""
    wanted = _oids(oids)
    if wanted is None:
        return None
    if not wanted:
        return {}
    return _cat_file(root, wanted, "blob", objects=objects)


def object_sizes(root: str | os.PathLike[str], oids: Sequence[str], *,
                 objects: ObjectDirs | None = None) -> dict[str, tuple[str, int]] | None:
    """``{oid: (type, size)}`` through one ``cat-file --batch-check`` — the
    sizes without the bytes, so the object scan hashes nothing it has not
    yet bounded. A ``missing`` line is left out: the caller names it (E11).
    ``{}`` for no oids, without starting git."""
    wanted = _oids(oids)
    if wanted is None:
        return None
    if not wanted:
        return {}
    out = _git(root, ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
               stdin_bytes=_stdin_lines(wanted), objects=objects)
    if out is None:
        return None
    sizes: dict[str, tuple[str, int]] = {}
    for line in out.split(b"\n"):
        if not line:
            continue
        fields = line.split(b" ")
        try:
            oid = _oid(fields[0].decode("ascii"))
            if oid is None:
                return None
            if len(fields) == 2 and fields[1] in (b"missing", b"ambiguous"):
                continue
            if len(fields) != 3:
                return None
            kind, size = fields[1].decode("ascii"), int(fields[2])
        except (UnicodeDecodeError, ValueError):
            return None
        if size < 0:
            return None
        sizes[oid] = (kind, size)
    return sizes


def new_objects(root: str | os.PathLike[str], new: str, *, exclude: Sequence[str],
                objects: ObjectDirs | None = None) -> list[tuple[str, str]] | None:
    """``[(oid, path)]`` for every object — commit, tree, blob — reachable
    from ``new`` and from none of ``exclude`` (``rev-list --objects <new>
    --not <exclude>...``): the push's new objects when ``exclude`` is every
    other ref tip. ``path`` is the name rev-list printed, ``""`` for a commit
    and for the root tree, and a LABEL: rev-list prints a blob once, at the
    first name it saw it under, stops a name at its first newline (no ``-z``
    on 2.34), and names only new objects — an old blob at a new name is seen
    through `tree_objects`, never here. Why ``exclude`` and not ``--all``:
    inside pre-receive ``--all`` is the pre-push ref state, but outside a hook
    (the CLI before pushing, every in-process test) the ref already points at
    ``new`` and ``--all`` lists nothing."""
    oid, others = _oid(new), _oids(exclude)
    if oid is None or others is None:
        return None
    args = ["rev-list", "--objects", oid]
    if others:
        args += ["--not", *others]
    out = _git(root, args, objects=objects)
    if out is None:
        return None
    found: list[tuple[str, str]] = []
    for line in out.split(b"\n"):
        if not line:
            continue
        head, _sep, name = line.partition(b" ")
        try:
            item = _oid(head.decode("ascii"))
        except UnicodeDecodeError:
            return None
        if item is None:
            return None
        found.append((item, os.fsdecode(name)))
    return found


def tree_objects(root: str | os.PathLike[str], oids: Sequence[str], *,
                 objects: ObjectDirs | None = None) -> dict[str, list[tuple[str, str, str]]] | None:
    """``{tree oid: [(mode, name, oid)]}`` for ``oids`` through one ``cat-file
    --batch``, each raw tree parsed here (``<mode> <name>\\0<raw oid>``; the
    oid's width from the repository's hash). For the name scan: every entry
    of every NEW tree, so a blob the repository already held, placed at
    ``.aws/credentials`` by a new tree, is seen at that name. A ``missing``
    line or an object that is not a tree makes the whole answer ``None``;
    ``{}`` for no oids, without starting git."""
    wanted = _oids(oids)
    if wanted is None:
        return None
    if not wanted:
        return {}
    raw = _cat_file(root, wanted, "tree", objects=objects)
    if raw is None:
        return None
    trees: dict[str, list[tuple[str, str, str]]] = {}
    for oid, content in raw.items():
        entries = _parse_tree(content, len(oid) // 2)
        if entries is None:
            return None
        trees[oid] = entries
    return trees


def empty_tree(root: str | os.PathLike[str]) -> str | None:
    """The empty tree's id for the repository's hash (``hash-object -t tree
    --stdin`` fed nothing; no ``-w``, so nothing is written): the previous
    tree of a line's first claiming commit."""
    return _oid(_text(_git(root, ["hash-object", "-t", "tree", "--stdin"], stdin_bytes=b"")))


# --------------------------------------------------------------------------- #
# the writing half (architecture phase 6)
# --------------------------------------------------------------------------- #
#: Seconds before a writing git call is abandoned. Why 120: the Stop hook's
#: budget is 300 s (``hooks/hooks.json``); a round's check and site build usually
#: take well under 60 s, and a commit hook that runs a linter or a test file takes
#: tens of seconds. Rejected: 10 s, the read timeout (an ordinary pre-commit hook
#: exceeds it, and the round would read "did not finish" on every turn); 290 s (a
#: hung gpg pinentry would spend the hook's whole budget, and Claude Code would
#: kill the hook mid-commit, leaving ``index.lock`` and printing no message).
VCS_WRITE_TIMEOUT_S = 120

#: Seconds between SIGTERM and SIGKILL when a writing call times out: git's
#: sigchain handler removes ``index.lock`` on SIGTERM, which takes milliseconds.
_TERM_GRACE_S = 5


class Wrote(NamedTuple):
    """What a writing call did. ``ok``; ``oid``, the new commit's full id
    (``commit`` only); ``why`` — ``""``, ``"no-git"`` (git could not be
    started), ``"timeout"``, ``"inside a repository"`` (``init_repo``) or
    ``"refused: <git's first stderr line>"``."""

    ok: bool
    oid: str | None
    why: str


def _git_write(root: str | os.PathLike[str], args: Sequence[str], *,
               stdin_bytes: bytes | None = None,
               timeout: float | None = None) -> tuple[int | None, bytes, str]:
    """``git -C root <args>``: ``(returncode, stdout, first stderr line)`` —
    ``(None, b"", "no-git")`` when git did not start and ``(None, b"",
    "timeout")`` when it did not finish. The one place a writing git process
    starts: the read half's environment plus ``GIT_EDITOR=:``. Never raises."""
    limit = VCS_WRITE_TIMEOUT_S if timeout is None else timeout
    env = _environment()
    env["GIT_EDITOR"] = ":"
    try:
        argv = ["git", "-C", os.fspath(root), *(os.fspath(a) for a in args)]
        proc = subprocess.Popen(
            argv, env=env,
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception:  # noqa: BLE001 — never raises: see the module docstring
        return None, b"", "no-git"
    try:
        out, err = proc.communicate(stdin_bytes, timeout=limit)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.communicate(timeout=_TERM_GRACE_S)
        except Exception:  # noqa: BLE001
            proc.kill()
            try:
                proc.communicate(timeout=_TERM_GRACE_S)
            except Exception:  # noqa: BLE001
                pass
        return None, b"", "timeout"
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        return None, b"", "no-git"
    lines = [line.strip() for line in err.decode("utf-8", "replace").splitlines()
             if line.strip()]
    # git's own "fatal:"/"error:" line is the useful one; a hook's output comes
    # first and is what a refusing hook says, so the first line is kept.
    return proc.returncode, out, (lines[0] if lines else "")


def _refused(code: int | None, first: str) -> str:
    if code is None:
        return first
    return "refused: " + (first or f"git exited {code}")


def _pathspec_stdin(relpaths: Iterable[str]) -> bytes | None:
    paths = _paths(relpaths)
    if not paths:
        return None
    try:
        return b"".join(os.fsencode(p) + b"\0" for p in paths)
    except (TypeError, ValueError):
        return None


#: git's own words, under ``LC_ALL=C``, for "no repository holds this
#: directory" — the one refusal of ``rev-parse`` that is an answer rather than
#: a failure to answer. ``... (or any of the parent directories)`` and ``...
#: (or any parent up to mount point /x)`` both begin so.
_NOT_A_REPOSITORY = "not a git repository"
_NO_GIT = "git is not on PATH"


def in_repository(path: str | os.PathLike[str]) -> tuple[bool | None, str]:
    """Whether ``path`` sits in a git repository: ``(True, "")``, ``(False,
    "")`` — a POSITIVE "no": git said "not a git repository" and no ``.git``
    entry stands in ``path`` or above it — or ``(None, why)`` when git did not
    answer (not on PATH, a timeout, a refusal such as "dubious ownership", or a
    ``.git`` git would not read). Read-only.

    For ``init`` and `init_repo` (review of architecture phase 6): both asked
    `is_repo`, which answers ``False`` on ANY failure, so under the
    ``safe.directory`` refusal common on WSL ``/mnt/c``, shared drives and
    container volumes (``GIT_TEST_ASSUME_DIFFERENT_OWNER=1`` simulates it), or a
    10 s timeout on a cold large repository, ``init`` in a subdirectory of an
    existing repository started a nested one: the project was hidden from the
    repository it sat in, and every round committed into the nested one. A
    repository is started only on this function's ``False``. The ``.git``
    walk is the second witness: a ceiling or a filesystem boundary that stops
    git's discovery short of a repository is not "no repository"."""
    code, out_bytes, err_bytes = _ask(path, ["rev-parse", "--is-inside-work-tree"])
    if code is None:
        return None, (f"git did not answer in {VCS_TIMEOUT_S} s" if err_bytes == b"timeout"
                      else _NO_GIT)
    out = _text(out_bytes)
    if code == 0 and out in ("true", "false"):
        # "false": inside a .git directory or a bare repository — a repository
        # holds it all the same.
        return True, ""
    err = err_bytes.decode("utf-8", "replace")
    first = next((line.strip() for line in err.splitlines() if line.strip()), "")
    if code == 0 or _NOT_A_REPOSITORY not in err:
        return None, first or f"git exited {code}"
    here = os.path.abspath(os.fspath(path))
    while True:
        if os.path.lexists(os.path.join(here, ".git")):
            return None, f"git does not read the repository at {here}"
        parent = os.path.dirname(here)
        if parent == here:
            return False, ""
        here = parent


def init_repo(root: str | os.PathLike[str]) -> Wrote:
    """``git init -q`` in ``root``. Refused (``ok`` False) unless
    `in_repository` says positively that no repository holds ``root``:
    ``why`` ``"inside a repository"``, or ``"git did not answer: <why>"``.
    `init` changes nothing about git inside a repository, and a nested
    ``.git`` would hide the project from the repository it sits in."""
    inside, why = in_repository(root)
    if inside:
        return Wrote(False, None, "inside a repository")
    if inside is None:
        return Wrote(False, None, "no-git" if why == _NO_GIT else f"git did not answer: {why}")
    code, _out, first = _git_write(root, ["init", "-q"])
    if code == 0:
        return Wrote(True, None, "")
    return Wrote(False, None, _refused(code, first))


def _nul_list(out: bytes | None) -> list[str] | None:
    if out is None:
        return None
    return [os.fsdecode(name) for name in out.split(b"\0") if name]


def changed_paths(root: str | os.PathLike[str]) -> list[str] | None:
    """Every path under ``root`` that differs from ``HEAD`` — modified or
    deleted in the work tree, staged, or untracked and not ignored — relative
    to ``root``, POSIX, sorted and unique (an untracked nested repository
    keeps git's trailing ``/``); ``None`` outside a repository or on any
    failure. Read-only. Asked only about ``.``: a change elsewhere in the
    repository is never listed, so a project inside a larger repository (the
    bracket, a monorepo) sees its own subtree.

    Three questions, unioned: ``diff`` (the work tree against the index — it
    compares content, so a file whose mtime alone moved is not listed, where
    ``ls-files --modified`` would name it), ``diff --cached`` (the index against
    ``HEAD``; an unborn branch compares against nothing) and ``ls-files
    --others --exclude-standard``. Rejected: ``status --porcelain``, which
    prints paths relative to the repository's top level whatever ``-C`` says."""
    found: set[str] = set()
    for args in (["diff", "--name-only", "-z", "--no-renames", "--relative", "--", "."],
                 ["diff", "--cached", "--name-only", "-z", "--no-renames", "--relative",
                  "--", "."],
                 ["ls-files", "-z", "--others", "--exclude-standard", "--", "."]):
        names = _nul_list(_git(root, ["-c", "core.quotepath=false", *args]))
        if names is None:
            return None
        # An untracked nested repository is listed as ``name/``, and that slash
        # is kept: `rounds.stage_plan` names a name ending in ``/``, never stages
        # it. What slipped through (review of architecture phase 6): `_normal`
        # stripped it, so ``generators/cadlib/`` read as a file, was staged, and a
        # round committed a 160000 gitlink — a submodule pointer nobody chose.
        found.update(_normal(name) + ("/" if name.endswith("/") and _normal(name) else "")
                     for name in names)
    found.discard("")
    return sorted(found)


def staged_paths(root: str | os.PathLike[str]) -> list[str] | None:
    """The paths under ``root`` whose index entry differs from ``HEAD``,
    relative to ``root``, sorted; ``None`` on failure. Read-only."""
    names = _nul_list(_git(root, ["diff", "--cached", "--name-only", "-z", "--no-renames",
                                  "--relative", "--", "."]))
    if names is None:
        return None
    return sorted({_normal(name) for name in names} - {""})


#: The in-progress operations a round never commits through, by the file git
#: keeps while each runs (``git rev-parse --git-path <name>``).
_OPERATIONS = (("MERGE_HEAD", "merge"), ("rebase-merge", "rebase"), ("rebase-apply", "rebase"),
               ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"),
               ("BISECT_LOG", "bisect"))


def operation(root: str | os.PathLike[str]) -> str | None:
    """What the repository holding ``root`` is in the middle of: ``""`` (none),
    ``"merge"``, ``"rebase"``, ``"cherry-pick"``, ``"revert"``, ``"bisect"``,
    or ``"detached"`` (HEAD names no branch); ``None`` outside a repository.
    Read-only. A commit made through any of them would land inside the
    person's operation — a merge commit nobody wrote, a rebase step that is a
    round. An unborn branch still has a symbolic HEAD, so a new repository
    reads ``""``."""
    if not is_repo(root):
        return None
    for name, word in _OPERATIONS:
        where = _text(_git(root, ["rev-parse", "--git-path", name]))
        if where and os.path.exists(os.path.join(os.fspath(root), where)):
            return word
    if _git(root, ["symbolic-ref", "-q", "HEAD"]) is None:
        return "detached"
    return ""


def add(root: str | os.PathLike[str], relpaths: Iterable[str]) -> Wrote:
    """Stage exactly ``relpaths`` (relative to ``root``): new, modified and
    deleted alike (``add --all`` limited to the list). Literal pathspecs on
    stdin, never a directory, never the repository."""
    data = _pathspec_stdin(relpaths)
    if data is None:
        return Wrote(False, None, "refused: no paths to stage")
    code, _out, first = _git_write(root, ["--literal-pathspecs", "add", "--all",
                                          "--pathspec-from-file=-", "--pathspec-file-nul"],
                                   stdin_bytes=data)
    if code == 0:
        return Wrote(True, None, "")
    return Wrote(False, None, _refused(code, first))


def commit(root: str | os.PathLike[str], relpaths: Iterable[str], subject: str,
           body: str) -> Wrote:
    """Commit exactly ``relpaths`` as they are in the work tree (``--only``),
    with ``subject`` and ``body`` as two paragraphs, under git's configured
    identity (``user.useConfigOnly=true``: none configured is a refusal, never
    an invented one). The commit hooks run; a refusing one is ``why``. ``oid``
    is ``HEAD`` after the commit."""
    data = _pathspec_stdin(relpaths)
    if data is None:
        return Wrote(False, None, "refused: no paths to commit")
    args = ["-c", "user.useConfigOnly=true", "--literal-pathspecs", "commit", "--quiet",
            "--cleanup=whitespace", "--only", "--pathspec-from-file=-",
            "--pathspec-file-nul", "-m", subject]
    if body:
        args += ["-m", body]
    code, _out, first = _git_write(root, args, stdin_bytes=data)
    if code == 0:
        return Wrote(True, git_head(root), "")
    return Wrote(False, None, _refused(code, first))
