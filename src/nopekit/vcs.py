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
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Iterable, Sequence

__all__ = [
    "VCS_TIMEOUT_S",
    "is_repo",
    "git_head",
    "ls_files",
    "ident",
    "show",
    "history",
    "model_dirty",
    "commit_times",
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
#:   foreign ceiling can stop discovery short of the project's ``.git``);
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
    "GIT_PREFIX", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES",
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


def _git(root: str | os.PathLike[str], args: Sequence[str], *,
         literal_pathspecs: bool = False) -> bytes | None:
    """``git -C root <args>``'s stdout, or ``None`` for any failure at all.

    The one place a process starts. ``--literal-pathspecs`` for calls that take
    paths: a file literally named ``*.stl`` or ``:(top)x`` is a path, not a
    pattern that quietly matches something else.
    """
    try:
        argv = ["git", "-C", os.fspath(root)]
        if literal_pathspecs:
            argv.append("--literal-pathspecs")
        argv.extend(os.fspath(a) for a in args)
        proc = subprocess.run(
            argv, env=_environment(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=VCS_TIMEOUT_S, check=False)
    except Exception:  # noqa: BLE001 — never raises: see the module docstring
        # FileNotFoundError (no git), PermissionError (a noexec PATH entry),
        # TimeoutExpired, ValueError (a NUL byte in a path), TypeError (a path
        # that is not one). Each means "git did not answer", which is the answer.
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


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


def ident(root: str | os.PathLike[str]) -> str | None:
    """``Name <email>`` — the author identity a commit in ``root`` would carry,
    timestamp dropped; ``None`` when none is configured.

    ``user.useConfigOnly=true`` is the point: without it git invents
    ``user@host.domain`` from the machine, and a signature that names a hostname
    nobody chose is a signature nobody made (D-12; P2.5's signer and P3's
    preference records refuse on ``None``).
    """
    line = _text(_git(root, ["-c", "user.useConfigOnly=true", "var", "GIT_AUTHOR_IDENT"]))
    if not line:
        return None
    # "Name <email> 1790560187 -0700": the identity ends at the last '>'. What
    # follows is git's clock, and a clock does not belong in an identity.
    end = line.rfind(">")
    who = line[:end + 1].strip() if end >= 0 else ""
    return who if "<" in who else None


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
