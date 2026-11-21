#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Eddy, milestone 1: a thing's own git server, in one standard-library process.

A thing is one git repository, and Eddy keeps it: a bare repository under
Eddy's own data directory, independent of GitHub or any other host, created
with ``server/pre-receive`` installed as its rule (``docs/EDDY_CONTRACT.md``,
section 14). This process serves three things over HTTP and nothing else:

* **git** — smart HTTP through ``git http-backend``, the CGI git ships, so a
  ``git push`` reaches the same pre-receive hook a push over SSH or a local
  path reaches, and a refusal comes back as ``remote:`` lines. Dumb HTTP is
  refused: it bypasses ``receive-pack`` and so the hook.
* **the screens** — one page (``static/``) drawn from data routes: section
  11's read-only routes over `vcs` reads (lines, history, commits, trees,
  blobs, raw bytes, diffs), the family tree, a candidate's boxes, and the
  bundled packs. Every route answers JSON but ``raw`` and ``box``.
* **activity** — the change events ``server/post-receive`` appends, as a
  page, as data and as a server-sent event stream.

What it never does: it computes no status (what a page says of a commit is
what ``nopekit validate`` prints for it, and a token is a baked one or none:
until the renderer lands every line reads *not yet rendered*); it executes
nothing from a tree; and it writes no ref itself — every ref moves through
``git receive-pack`` and the hook. Acts, the renderer and the UI are
later milestones. A candidate's engineering and marketing views are drawn
by the page from the baked files that candidate's own commit holds
(``site/data/``, written by ``nopekit site build``): Eddy renders them and
decides nothing in them.

The data directory (``--root``, default ``$EDDY_ROOT`` or ``~/.eddy``)::

    repos/<thing>.git     each thing's bare repository, created under the hook
    events/<thing>.jsonl  one JSON line per ref a push moved (post-receive)
    tokens.json           who may push: a login and a token's SHA-256, mode 0600

Every git route and every page needs a token (``Authorization: Bearer
<token>``, or HTTP basic with the token as the password). The login a push is
recorded under is the token's, never the user name typed beside it, and it
reaches the hook as ``NOPE_PUSHER``. ``--public-read`` opens clone, fetch and
the pages to anyone who can reach the port; a push always needs a token.
Plain HTTP carries the token in the clear: the default address is loopback,
and anything wider belongs behind TLS.

Run as a script, never ``-m`` (``server/pre-receive``'s lesson: ``-m`` puts
the cwd first on ``sys.path``)::

    python3 server/eddy.py token neuman        # prints a token, once
    python3 server/eddy.py new owl             # a bare repository under the hook
    python3 server/eddy.py serve               # http://127.0.0.1:8765/

or in the image (``server/Dockerfile``). Not the spine: this file starts git
itself, because ``git http-backend`` streams a push for minutes and `vcs`'s
ten-second, never-raise contract is not for that; every read the browser
makes still goes through `vcs`.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import difflib
import fnmatch
import hashlib
import hmac
import json
import os
import posixpath
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKOUT = os.path.dirname(HERE)
SRC = os.path.join(CHECKOUT, "src")
if __name__ == "__main__":
    # As in server/pre-receive: drop every empty or relative entry (the cwd) and
    # this directory, and put this checkout's src first, before importing it.
    sys.path[:] = [SRC] + [e for e in sys.path if e and os.path.isabs(e) and e not in (HERE, SRC)]

from nopekit import receive, vcs  # noqa: E402
from nopekit.util import NopekitError, printable  # noqa: E402

PRE_RECEIVE = os.path.join(HERE, "pre-receive")
POST_RECEIVE = os.path.join(HERE, "post-receive")
STATIC_DIR = os.path.join(HERE, "static")

# --------------------------------------------------------------------------- #
# constants, each with why this value and what was rejected
# --------------------------------------------------------------------------- #
#: The port. The build plan's (``git push http://localhost:8765/owl.git``).
#: Rejected: 8000 and 8080, the defaults of ``python3 -m http.server`` and of
#: most dev servers a maker already runs, so the first start would collide.
DEFAULT_PORT = 8765

#: A thing's name: the URL's ``<thing>.git`` and the directory's. Lower case,
#: no dot. Rejected: `receive.LINE_NAME`'s shape — upper case makes ``Owl``
#: and ``owl`` one directory on a case-insensitive volume (Docker Desktop on a
#: Mac), and a dot makes ``owl.git.git`` and an ambiguous suffix.
THING_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: A login: the word a token records and the hook receives as ``--by``. The
#: line name's closed shape, so it is one printable word in every line it
#: reaches. Rejected: an e-mail address (an ``@`` and a ``+`` in a log line,
#: and a person's address in every event row).
LOGIN = receive.LINE_NAME

#: A token: ``eddy_`` and 32 random bytes, url-safe. The prefix makes a leaked
#: token findable by a secret scanner. Rejected: 16 bytes (enough against
#: guessing, but tokens are long-lived and this costs nothing); a token kept in
#: plain text (a copy of the data directory would be every key it holds).
TOKEN_PREFIX = "eddy_"
TOKEN_BYTES = 32

#: Seconds before a ``git http-backend`` run is killed. A push's walk is
#: bounded by its git calls (``vcs.VCS_TIMEOUT_S`` each, two to four per ref
#: plus a few per claiming commit, ``docs/EDDY_CONTRACT.md`` section 10), and a
#: first push of a real project with a long line is the slow case. Rejected:
#: none (a hung hook holds a thread forever); 120 s (the walk of a long line,
#: each claiming commit's tree read whole, is minutes on a slow disk).
BACKEND_TIMEOUT_S = 900

#: The largest request body taken: the validator's per-push cap
#: (`receive.MAX_PUSH_BYTES`) and 64 MiB for the pack's own framing. A pack
#: is compressed, so an honest push under the validator's cap fits. Rejected:
#: no cap (a client can stream until the disk fills before the hook runs).
MAX_REQUEST_BYTES = receive.MAX_PUSH_BYTES + 64 * 1024 * 1024

#: Commits one history page lists. Rejected: the whole line (a page that
#: grows with the line forever, and one ``cat-file`` per page view of it).
HISTORY_LIMIT = 200

#: The largest side of a text diff the diff page draws, and the bytes one page
#: fetches for diffs in all. A larger file reads *changed* with its sizes.
#: Rejected: `receive.MAX_BLOB_BYTES` (50 MiB of diff in one HTML page).
DIFF_TEXT_MAX = 256 * 1024
DIFF_TOTAL_MAX = 8 * 1024 * 1024

#: Events the activity page shows, newest first. Rejected: all of them (the
#: log grows with every push; the stream and the file keep the rest).
ACTIVITY_LIMIT = 200

#: The event stream: how often it looks for new lines, how often it sends a
#: comment so a proxy keeps the connection, and how long one stream lives
#: before the browser's EventSource reconnects (with ``Last-Event-ID``).
#: Rejected: inotify (Linux only, and a dependency of a kind the spine refuses).
SSE_POLL_S = 1.0
SSE_HEARTBEAT_S = 15.0
SSE_MAX_S = 3600.0

#: Validator reports a commit page keeps, keyed by everything a report reads
#: (the commit, its line, every tip and the spec branch). Rejected: none (a
#: commit page of a long line re-walks it on every view).
VALIDATE_CACHE = 64

#: Seconds for each git call that creates a repository. Rejected:
#: `vcs.VCS_TIMEOUT_S` (``git init`` on a network volume can take longer).
SETUP_TIMEOUT_S = 30

#: The extension tables of section 11. Text the contract names, and any blob
#: that decodes as UTF-8 with no NUL; HTML, SVG and XML only ever as text, so a
#: page Eddy serves never runs a tree's markup or script.
TEXT_SUFFIXES = (".py", ".json", ".md", ".log", ".txt", ".jsonl")
AS_TEXT_SUFFIXES = (".html", ".htm", ".xhtml", ".svg", ".xml")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
AUDIO_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
               ".m4a": "audio/mp4", ".flac": "audio/flac"}
VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}
MODEL_TYPES = {".stl": "model/stl", ".glb": "model/gltf-binary"}
OTHER_TYPES = {".json": "application/json", ".pdf": "application/pdf"}

#: The app's policy: no inline script; scripts from Eddy's own ``/static/``
#: and three.js from the CDN the site already uses
#: (``site_template/engineering/index.html``) for a candidate's 3D model.
#: Inline style attributes are allowed: the app sets bar widths and grid
#: places in markup it builds from escaped data. Frames are Eddy's own boxes.
PAGE_CSP = ("default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "media-src 'self'; script-src 'self' https://cdn.jsdelivr.net; connect-src 'self'; "
            "frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
#: A raw blob is never a page: sandboxed, nothing loads, nothing runs.
RAW_CSP = "default-src 'none'; sandbox"

STATIC_FILES = {"index.html": "text/html; charset=utf-8", "app.css": "text/css; charset=utf-8",
                "app.js": "text/javascript; charset=utf-8", "made-with-nopekit.png": "image/png"}

REPOS_DIR = "repos"
EVENTS_DIR = "events"
TOKENS_FILE = "tokens.json"

#: The repository configuration every thing is created with (section 10): git's
#: own refusal of malformed objects, and receive-pack over HTTP. Never
#: ``receive.denyDeletes`` or ``receive.denyNonFastForwards``: ``draft/*``
#: takes both, and the hook holds them for lines and ``main`` (E03, E04).
REPO_CONFIG = (("receive.fsckObjects", "true"), ("transfer.fsckObjects", "true"),
               ("http.receivepack", "true"))

_GIT_ROUTE = re.compile(r"^/([a-z0-9][a-z0-9-]{0,63})\.git(/info/refs|/git-upload-pack|"
                        r"/git-receive-pack)$")
_BROWSE_ROUTE = re.compile(r"^/r/([^/]+)(?:/(.*))?$")
_HEX = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_HOST = re.compile(r"^[A-Za-z0-9.\-]+(:[0-9]{1,5})?$|^\[[0-9A-Fa-f:.]+\](:[0-9]{1,5})?$")


class EddyError(Exception):
    """A refusal Eddy states in one line: a name out of shape, a thing that
    exists, a tokens file that does not parse."""


class Unanswered(Exception):
    """A `vcs` read answered ``None``: the page says git did not answer and
    shows nothing it would have had to guess (the validator's fail-closed rule,
    on the reading side)."""


# --------------------------------------------------------------------------- #
# the data directory: things, tokens, events
# --------------------------------------------------------------------------- #
def default_root() -> str:
    return os.path.abspath(os.environ.get("EDDY_ROOT") or os.path.expanduser("~/.eddy"))


def repo_path(root: str, thing: str) -> str:
    return os.path.join(root, REPOS_DIR, f"{thing}.git")


def events_path(root: str, thing: str) -> str:
    return os.path.join(root, EVENTS_DIR, f"{thing}.jsonl")


def things(root: str) -> list[str]:
    """Every thing under ``root``, sorted: a ``<name>.git`` directory whose name
    is in shape. Anything else under ``repos/`` is not a thing and is not served."""
    try:
        names = os.listdir(os.path.join(root, REPOS_DIR))
    except FileNotFoundError:
        return []
    found = []
    for entry in names:
        name = entry[:-4] if entry.endswith(".git") else ""
        if THING_NAME.match(name) and os.path.isdir(os.path.join(root, REPOS_DIR, entry)):
            found.append(name)
    return sorted(found)


def _base_env(root: str) -> dict[str, str]:
    """The environment every git process Eddy starts runs in: no system or
    user config but the data directory's (``HOME`` is the root, which holds no
    ``.gitconfig`` unless the keeper put one there), C locale, no prompt."""
    return {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": root,
            "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def _setup_git(root: str, args: list[str]) -> None:
    try:
        proc = subprocess.run(["git", *args], env=_base_env(root), stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=SETUP_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EddyError(f"git {args[0]} did not run: {exc}") from None
    if proc.returncode != 0:
        said = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise EddyError(f"git {args[-1] if args[0] == '-C' else args[0]} failed: "
                        f"{said[0] if said else proc.returncode}")


def _write_if_changed(path: str, text: str, mode: int) -> bool:
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text and (os.stat(path).st_mode & 0o777) == mode:
                return False
    except FileNotFoundError:
        pass
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return True


def hook_shims(root: str, thing: str, python: str) -> dict[str, str]:
    """The two shims a thing's ``hooks/`` holds, as section 10 installs them:
    two lines of ``sh`` each, exec-ing this checkout's scripts under ``python``
    (an absolute path, so the hook runs the interpreter the server runs)."""
    py = shlex.quote(python)
    return {
        "pre-receive": f"#!/bin/sh\nexec {py} {shlex.quote(PRE_RECEIVE)} \"$@\"\n",
        "post-receive": (f"#!/bin/sh\nexec {py} {shlex.quote(POST_RECEIVE)} "
                         f"{shlex.quote(events_path(root, thing))} \"$@\"\n"),
    }


def install_hooks(root: str, thing: str, python: str | None = None) -> list[str]:
    """Write the shims (when their bytes differ) and drop git's ``*.sample``
    files; returns the hooks written. Run at creation and at every start, so a
    data directory moved to a new checkout or image points at its scripts."""
    hooks = os.path.join(repo_path(root, thing), "hooks")
    os.makedirs(hooks, exist_ok=True)
    wrote = []
    for name, text in hook_shims(root, thing, python or sys.executable).items():
        if _write_if_changed(os.path.join(hooks, name), text, 0o755):
            wrote.append(name)
    for entry in os.listdir(hooks):
        if entry.endswith(".sample"):
            os.remove(os.path.join(hooks, entry))
    return wrote


def create_thing(root: str, thing: str, *, python: str | None = None) -> str:
    """A new bare repository for ``thing``, created under the hook: ``main``
    as its HEAD, section 10's configuration, both shims. Returns its path."""
    if not isinstance(thing, str) or not THING_NAME.match(thing):
        raise EddyError(f"{thing!r} is not a thing's name: lower-case letters, digits and "
                        f"'-', starting with a letter or digit, at most 64")
    path = repo_path(root, thing)
    if os.path.exists(path):
        raise EddyError(f"{thing} already exists at {path}")
    os.makedirs(os.path.join(root, REPOS_DIR), exist_ok=True)
    os.makedirs(os.path.join(root, EVENTS_DIR), exist_ok=True)
    _setup_git(root, ["init", "--quiet", "--bare", "--initial-branch=main", path])
    for key, value in REPO_CONFIG:
        _setup_git(root, ["-C", path, "config", key, value])
    install_hooks(root, thing, python)
    return path


def _tokens_path(root: str) -> str:
    return os.path.join(root, TOKENS_FILE)


def read_tokens(root: str) -> list[dict[str, str]]:
    """The tokens file's entries, read strictly: ``{"schema": 1, "tokens":
    [{"login", "id", "sha256"}]}``; ``[]`` when there is none."""
    try:
        with open(_tokens_path(root), encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise EddyError(f"{_tokens_path(root)} does not parse: {exc}") from None
    entries = doc.get("tokens") if isinstance(doc, dict) and doc.get("schema") == 1 else None
    if not isinstance(entries, list):
        raise EddyError(f"{_tokens_path(root)} is not a schema-1 tokens file")
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {"login", "id", "sha256"}
                or not LOGIN.match(str(entry["login"]))
                or not re.match(r"^[0-9a-f]{64}$", str(entry["sha256"]))):
            raise EddyError(f"{_tokens_path(root)} holds an entry out of shape: {entry!r}")
    return entries


def _write_tokens(root: str, entries: list[dict[str, str]]) -> None:
    os.makedirs(root, exist_ok=True)
    path = _tokens_path(root)
    tmp = f"{path}.tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"schema": 1, "tokens": entries}, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def add_token(root: str, login: str) -> str:
    """A new token for ``login``; the token itself is returned once and only
    its SHA-256 is kept."""
    if not isinstance(login, str) or not LOGIN.match(login):
        raise EddyError(f"{login!r} is not a login: letters, digits, '.', '_' and '-', "
                        f"starting with a letter or digit, at most 64")
    entries = read_tokens(root)
    token = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    digest = _digest(token)
    entries.append({"login": login, "id": digest[:8], "sha256": digest})
    _write_tokens(root, entries)
    return token


def revoke_token(root: str, token_id: str) -> int:
    """Drop every entry whose id is ``token_id``; returns how many."""
    entries = read_tokens(root)
    kept = [e for e in entries if e["id"] != token_id]
    if len(kept) != len(entries):
        _write_tokens(root, kept)
    return len(entries) - len(kept)


def login_for(root: str, token: str) -> str | None:
    """The login ``token`` was issued to, or ``None``. Every entry is compared,
    in constant time, so the answer's timing says nothing about which matched."""
    if not token:
        return None
    digest = _digest(token)
    found = None
    for entry in read_tokens(root):
        if hmac.compare_digest(entry["sha256"], digest) and found is None:
            found = entry["login"]
    return found


def read_events(root: str, thing: str) -> list[dict[str, Any]]:
    """Every event row of ``thing``, oldest first; a line that does not parse
    is kept as ``{"unreadable": <line number>}`` rather than dropped."""
    try:
        with open(events_path(root, thing), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return []
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
            rows.append(row if isinstance(row, dict) else {"unreadable": number})
        except ValueError:
            rows.append({"unreadable": number})
    return rows


# --------------------------------------------------------------------------- #
# reading one thing: every question through vcs, and receive's own words
# --------------------------------------------------------------------------- #
def _zero(sha: str) -> str:
    return "0" * len(sha)


def _subject(message: bytes) -> str:
    first = message.decode("utf-8", "replace").strip().splitlines()
    return printable(first[0]) if first else ""


def _blob_kind(path: str) -> str:
    """``image``, ``audio``, ``video``, ``model``, ``text`` (by suffix or by
    decoding) or ``as-text`` (markup shown as its source), from the name."""
    ext = posixpath.splitext(path.lower())[1]
    if ext in IMAGE_TYPES:
        return "image"
    if ext in AUDIO_TYPES:
        return "audio"
    if ext in VIDEO_TYPES:
        return "video"
    if ext in MODEL_TYPES:
        return "model"
    if ext in AS_TEXT_SUFFIXES:
        return "as-text"
    return "text" if ext in TEXT_SUFFIXES else "maybe-text"


def _content_type(path: str, data: bytes) -> str:
    ext = posixpath.splitext(path.lower())[1]
    for table in (IMAGE_TYPES, AUDIO_TYPES, VIDEO_TYPES, MODEL_TYPES, OTHER_TYPES):
        if ext in table:
            return table[ext]
    if ext in AS_TEXT_SUFFIXES or _as_text(data) is not None:
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def _as_text(data: bytes) -> str | None:
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _is_spec_path(path: str) -> bool:
    return path.split("/", 1)[0] in receive.SPEC_ENTRIES or path == receive.PACKS_REL


def _is_part(path: str) -> bool:
    return fnmatch.fnmatchcase(posixpath.basename(path), "print-*.stl")


class Thing:
    """One thing's repository, read for one request: every answer is cached
    on the instance, which lives as long as the request does."""

    def __init__(self, eddy: "Eddy", name: str) -> None:
        self.eddy = eddy
        self.name = name
        self.repo = repo_path(eddy.root, name)
        self._tips: dict[str, str] | None = None
        self._spec: set[str] | None = None
        self._on_lines: set[str] | None = None
        self._chains: dict[str, list[str]] = {}

    # -- answers ------------------------------------------------------------ #
    @staticmethod
    def _need(value: Any, what: str) -> Any:
        if value is None:
            raise Unanswered(f"git did not answer ({what})")
        return value

    def tips(self) -> dict[str, str]:
        """Every ``refs/heads/*`` tip."""
        if self._tips is None:
            self._tips = self._need(vcs.ref_tips(self.repo, "refs/heads/"), "for-each-ref")
        return self._tips

    def line_tips(self) -> dict[str, str]:
        return {ref[len(receive.CAND_PREFIX):]: sha for ref, sha in self.tips().items()
                if ref.startswith(receive.CAND_PREFIX)}

    def spec_trees(self) -> set[str]:
        """The tree of every commit on ``spec``: a spec tree is accepted when
        it is one of these (section 5)."""
        if self._spec is None:
            self._spec = set(self._need(vcs.ref_trees(self.repo, receive.SPEC,
                                                      limit=receive.WALK_LIMIT), "spec"))
        return self._spec

    def on_lines(self) -> set[str]:
        """Every commit on a line's first-parent chain. A kind is read only
        there: on ``draft/*`` kinds are never read, and on ``main`` a claiming
        commit means nothing (section 2)."""
        if self._on_lines is None:
            tips = list(self.line_tips().values())
            self._on_lines = set(self._need(
                vcs.line_commits(self.repo, tips, limit=receive.WALK_LIMIT), "rev-list"))
        return self._on_lines

    def chain(self, line: str) -> list[str]:
        if line not in self._chains:
            tip = self.line_tips()[line]
            self._chains[line] = self._need(
                vcs.first_parents(self.repo, tip, limit=receive.WALK_LIMIT), "rev-list")
        return self._chains[line]

    def line_of(self, sha: str) -> str | None:
        """The line whose first-parent chain holds ``sha``; the first by name
        when two lines share it (a fork shares its base)."""
        if sha not in self.on_lines():
            return None
        for line in sorted(self.line_tips()):
            if sha in self.chain(line):
                return line
        return None

    def commits(self, shas: list[str]) -> dict[str, vcs.CommitObject]:
        return self._need(vcs.commit_objects(self.repo, shas), "cat-file commit")

    def nope_pairs(self, shas: list[str]) -> dict[str, dict[str, str]]:
        """Each commit's ``Nope-*`` trailers as git itself reads the block,
        exact spelling, first occurrence."""
        lines = self._need(vcs.trailer_lines(self.repo, shas), "trailers")
        found: dict[str, dict[str, str]] = {}
        for sha, pairs in lines.items():
            kept: dict[str, str] = {}
            for key, value in pairs:
                if key.startswith("Nope-") and key not in kept:
                    kept[key] = value
            found[sha] = kept
        return found

    def acceptance(self, sha: str) -> dict[str, Any]:
        """The spec tree of ``sha`` and whether a commit of ``spec`` holds it,
        or why it has none (E34's words)."""
        try:
            tree, problems = receive.spec_tree_id(self.repo, sha)
        except NopekitError as exc:
            raise Unanswered(str(exc)) from None
        if tree is None:
            return {"spec_tree": None, "accepted": None, "spec_problems": problems}
        return {"spec_tree": tree, "accepted": tree in self.spec_trees()}

    def head(self, sha: str) -> dict[str, Any]:
        """What the browser says of a commit above its tree, blob or diff: its
        kind and ``Nope-Name`` when it is on a line, and its spec's acceptance
        when it claims; a working commit, or a commit on no line, says so."""
        line = self.line_of(sha)
        info: dict[str, Any] = {"sha": sha, "line": line}
        if line is None:
            info.update(kind="", name="")
            return info
        pairs = self.nope_pairs([sha]).get(sha, {})
        kind = pairs.get("Nope-Kind", "")
        info.update(kind=kind or "working", name=pairs.get("Nope-Name", ""),
                    trigger=pairs.get("Nope-Trigger", ""))
        if kind in ("candidate", "evidence"):
            info.update(self.acceptance(sha))
        return info

    # -- resolving a URL's ``<sha>[/<path>]`` ---------------------------- #
    def resolve(self, rest: str) -> tuple[str, str, str | None] | None:
        """``(sha, path, ref)`` for a URL tail: a full commit id then a path,
        or a branch name (``cand/A``, ``main``) then a path — the longest name
        a ref has wins, as GitHub reads ``tree/<branch>/<path>``. ``None`` when
        neither names a commit."""
        first, _sep, more = rest.partition("/")
        if _HEX.match(first):
            exists = vcs.commit_exists(self.repo, first)
            if exists is None:
                raise Unanswered("git did not answer (rev-parse)")
            return (first, more, None) if exists else None
        parts = rest.split("/")
        tips = self.tips()
        for cut in range(len(parts), 0, -1):
            name = "/".join(parts[:cut])
            ref = "refs/heads/" + name
            if ref in tips:
                return tips[ref], "/".join(parts[cut:]), name
        return None

    def entry_at(self, sha: str, path: str) -> vcs.TreeEntry | None:
        """The entry at ``path`` in commit ``sha``'s tree (a tree entry for
        ``""``: the root), walking one ``ls-tree`` per directory."""
        tree = self._need(vcs.tree_of(self.repo, sha), "rev-parse tree")
        if not path:
            return vcs.TreeEntry("040000", "tree", tree, None, "")
        parts = path.split("/")
        if any(p in ("", ".", "..") for p in parts):
            return None
        current = tree
        for at, part in enumerate(parts):
            entries = self._need(vcs.tree_entries(self.repo, current), "ls-tree")
            match = next((e for e in entries if e.path == part), None)
            if match is None:
                return None
            if at == len(parts) - 1:
                return match._replace(path=path)
            if match.type != "tree":
                return None
            current = match.oid
        return None

    def path_oids(self, roots: dict[str, str], path: str) -> dict[str, str | None]:
        """``{sha: oid of path in its tree, or None}`` for every commit in
        ``roots`` (``sha -> root tree``), one ``cat-file --batch`` per
        directory level rather than one ``ls-tree`` per commit."""
        parts = path.split("/")
        current: dict[str, str | None] = dict(roots)
        for depth, part in enumerate(parts):
            last = depth == len(parts) - 1
            wanted = sorted({oid for oid in current.values() if oid})
            trees = self._need(vcs.tree_objects(self.repo, wanted), "cat-file tree")
            following: dict[str, str | None] = {}
            for sha, tree in current.items():
                hit = None
                for mode, name, oid in (trees.get(tree, ()) if tree else ()):
                    if name == part:
                        hit = (mode, oid)
                        break
                if hit and (last or hit[0].lstrip("0") == "40000"):
                    following[sha] = hit[1]
                else:
                    following[sha] = None
            current = following
        return current

    def validation(self, sha: str, line: str) -> dict[str, Any]:
        """What ``nopekit validate`` prints today for ``sha`` pushed as the tip
        of ``line`` from empty: the agent's pre-flight
        (``nopekit validate . refs/heads/cand/<name> 0 <sha>``), cached by
        everything the report reads."""
        ref = receive.CAND_PREFIX + line
        spec_tip = self.tips().get(receive.SPEC, "")
        key = (self.name, sha, ref, tuple(sorted(self.tips().items())), spec_tip)
        cached = self.eddy.cached_validation(key)
        if cached is not None:
            return cached
        report = receive.validate_push(self.repo, [(_zero(sha), sha, ref)])
        refusals: list[str] = []
        notes: list[str] = []
        for ref_report in report.refs:
            refusals += [receive.format_line(r) for r in ref_report.refusals]
            notes += [receive.format_line(n) for n in ref_report.notes]
        result = {"ref": ref, "refusals": refusals, "notes": notes,
                  "unanswered": printable(report.unanswered) if report.unanswered else "",
                  "accepted": report.accepted}
        self.eddy.keep_validation(key, result)
        return result


# --------------------------------------------------------------------------- #
# the views: each returns data; the HTML is drawn from that data alone
# --------------------------------------------------------------------------- #
class Redirect(Exception):
    def __init__(self, location: str) -> None:
        super().__init__(location)
        self.location = location


class NotFound(Exception):
    pass


def _href(thing: str, view: str, *parts: str) -> str:
    tail = "/".join(p for p in parts if p)
    url = f"/r/{urllib.parse.quote(thing)}/{view}"
    if tail:
        url += "/" + urllib.parse.quote(tail, safe="/.", errors="surrogateescape")
    return url


def view_things(eddy: "Eddy") -> dict[str, Any]:
    rows = []
    for name in things(eddy.root):
        thing = Thing(eddy, name)
        try:
            lines = len(thing.line_tips())
        except Unanswered:
            lines = None
        events = read_events(eddy.root, name)
        last = next((e for e in reversed(events) if "at" in e), None)
        rows.append({"thing": name, "lines": lines, "last_push": last.get("at") if last else ""})
    return {"view": "things", "things": rows}


def view_lines(thing: Thing, host: str) -> dict[str, Any]:
    tips = thing.tips()
    lines = thing.line_tips()
    shas = sorted(set(tips.values()))
    pairs = thing.nope_pairs(shas) if shas else {}
    rows = []
    for name in sorted(lines):
        sha = lines[name]
        nope = pairs.get(sha, {})
        row = {"line": name, "ref": receive.CAND_PREFIX + name, "tip": sha,
               "kind": nope.get("Nope-Kind", "") or "working", "name": nope.get("Nope-Name", ""),
               # Until milestone 2's renderer regenerates a line's state file
               # there is no token to show, and a guess would be a status the
               # server computed (section 11: "never a token").
               "state": "not yet rendered"}
        row.update(thing.acceptance(sha))
        rows.append(row)
    others = [{"ref": ref, "branch": ref[len("refs/heads/"):], "tip": sha}
              for ref, sha in sorted(tips.items()) if not ref.startswith(receive.CAND_PREFIX)]
    return {"view": "lines", "thing": thing.name, "lines": rows, "refs": others,
            "clone": f"http://{host}/{thing.name}.git" if host else f"/{thing.name}.git"}


def view_tree(thing: Thing, rest: str) -> dict[str, Any]:
    found = thing.resolve(rest)
    if found is None:
        raise NotFound(f"no commit or branch at {rest!r}")
    sha, path, ref = found
    path = path.strip("/")
    if ref is not None:
        raise Redirect(_href(thing.name, "tree", sha, path))
    entry = thing.entry_at(sha, path)
    if entry is None:
        raise NotFound(f"{path} is not in {sha[:12]}")
    if entry.type != "tree":
        raise Redirect(_href(thing.name, "blob", sha, path))
    entries = thing._need(vcs.tree_entries(thing.repo, entry.oid), "ls-tree")
    rows = [{"mode": e.mode, "type": e.type, "oid": e.oid, "size": e.size, "name": e.path}
            for e in entries]
    rows.sort(key=lambda r: (r["type"] != "tree", r["name"]))
    return {"view": "tree", "thing": thing.name, "sha": sha, "path": path,
            "commit": thing.head(sha), "entries": rows}


def view_blob(thing: Thing, rest: str) -> dict[str, Any]:
    found = thing.resolve(rest)
    if found is None:
        raise NotFound(f"no commit or branch at {rest!r}")
    sha, path, ref = found
    path = path.strip("/")
    if ref is not None:
        raise Redirect(_href(thing.name, "blob", sha, path))
    entry = thing.entry_at(sha, path) if path else None
    if entry is None:
        raise NotFound(f"{path} is not in {sha[:12]}")
    if entry.type == "tree":
        raise Redirect(_href(thing.name, "tree", sha, path))
    data: dict[str, Any] = {"view": "blob", "thing": thing.name, "sha": sha, "path": path,
                            "commit": thing.head(sha), "mode": entry.mode, "oid": entry.oid,
                            "size": entry.size, "raw": _href(thing.name, "raw", sha, path)}
    if entry.type == "commit":
        data["kind"] = "gitlink"
        return data
    if entry.mode == "120000":
        target = thing._need(vcs.blobs(thing.repo, [entry.oid]), "cat-file blob")[entry.oid]
        data.update(kind="symlink", target=printable(os.fsdecode(target)))
        return data
    kind = _blob_kind(path)
    if (entry.size or 0) > receive.MAX_BLOB_BYTES:
        data.update(kind="too-large", cap=receive.MAX_BLOB_BYTES)
        return data
    if kind in ("text", "as-text", "maybe-text"):
        raw = thing._need(vcs.blobs(thing.repo, [entry.oid]), "cat-file blob")[entry.oid]
        text = _as_text(raw)
        if text is None:
            data["kind"] = "binary"
        else:
            data.update(kind="text", text=text, markup=kind == "as-text")
        return data
    data["kind"] = kind
    return data


def raw_blob(thing: Thing, rest: str) -> tuple[bytes, str, str]:
    """``(bytes, content type, filename)`` for the raw route."""
    found = thing.resolve(rest)
    if found is None:
        raise NotFound(f"no commit or branch at {rest!r}")
    sha, path, ref = found
    path = path.strip("/")
    if ref is not None:
        raise Redirect(_href(thing.name, "raw", sha, path))
    entry = thing.entry_at(sha, path) if path else None
    if entry is None or entry.type != "blob":
        raise NotFound(f"{path} is not a file in {sha[:12]}")
    if (entry.size or 0) > receive.MAX_BLOB_BYTES:
        raise NotFound(f"{path} is over the {receive.MAX_BLOB_BYTES // (1024 * 1024)} MiB cap")
    data = thing._need(vcs.blobs(thing.repo, [entry.oid]), "cat-file blob")[entry.oid]
    line = thing.line_of(sha) or sha[:12]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{thing.name}-{line}-{posixpath.basename(path)}")
    return data, _content_type(path, data), safe


def view_diff(thing: Thing, rest: str) -> dict[str, Any]:
    left, sep, right = rest.partition("..")
    if not sep or not left or not right:
        raise NotFound("a diff is /diff/<a>..<b>[/<path>]")
    a = thing.resolve(left)
    b = thing.resolve(right)
    if a is None or a[1] or b is None:
        raise NotFound(f"{left!r} or {right!r} names no commit")
    path = b[1].strip("/")
    if a[2] is not None or b[2] is not None:
        raise Redirect(_href(thing.name, "diff", f"{a[0]}..{b[0]}", path))
    sha_a, sha_b = a[0], b[0]
    tree_a = thing._need(vcs.tree_of(thing.repo, sha_a), "rev-parse tree")
    tree_b = thing._need(vcs.tree_of(thing.repo, sha_b), "rev-parse tree")
    changes = thing._need(vcs.tree_diff(thing.repo, tree_a, tree_b), "diff-tree")
    if path:
        changes = [c for c in changes if c.path == path or c.path.startswith(path + "/")]
    zero = _zero(sha_a)
    oids = sorted({o for c in changes for o in (c.old_oid, c.new_oid) if o != zero})
    sizes = thing._need(vcs.object_sizes(thing.repo, oids), "cat-file sizes") if oids else {}
    fetch: list[str] = []
    budget = DIFF_TOTAL_MAX
    for c in changes:
        pair = [o for o in (c.old_oid, c.new_oid) if o != zero]
        if any(sizes.get(o, ("", DIFF_TEXT_MAX + 1))[1] > DIFF_TEXT_MAX for o in pair):
            continue
        cost = sum(sizes[o][1] for o in pair)
        if cost <= budget and all(sizes[o][0] == "blob" for o in pair):
            fetch += pair
            budget -= cost
    contents = thing._need(vcs.blobs(thing.repo, sorted(set(fetch))), "cat-file blob") \
        if fetch else {}
    files = []
    for c in changes:
        row: dict[str, Any] = {"path": c.path, "status": c.status, "old_oid": c.old_oid,
                               "new_oid": c.new_oid, "old_mode": c.old_mode,
                               "new_mode": c.new_mode, "spec": _is_spec_path(c.path),
                               "part": _is_part(c.path)}
        old = b"" if c.old_oid == zero else contents.get(c.old_oid)
        new = b"" if c.new_oid == zero else contents.get(c.new_oid)
        old_text = None if old is None else _as_text(old)
        new_text = None if new is None else _as_text(new)
        if old_text is not None and new_text is not None and "120000" not in (c.old_mode,
                                                                                c.new_mode):
            row["diff"] = list(difflib.unified_diff(
                old_text.splitlines(), new_text.splitlines(),
                fromfile=f"a/{c.path}", tofile=f"b/{c.path}", lineterm="", n=3))
        else:
            row["changed"] = {"old_size": sizes.get(c.old_oid, ("", None))[1],
                              "new_size": sizes.get(c.new_oid, ("", None))[1]}
        files.append(row)
    # The spec-tree diff first (section 11), then the rest in path order.
    files.sort(key=lambda r: (not r["spec"], r["path"]))
    parts = [{"path": r["path"], "status": r["status"]} for r in files if r["part"]]
    return {"view": "diff", "thing": thing.name, "a": sha_a, "b": sha_b, "path": path,
            "commit_a": thing.head(sha_a), "commit_b": thing.head(sha_b),
            "parts_changed": parts, "files": files}


def view_history(thing: Thing, rest: str) -> dict[str, Any]:
    if not rest:
        tips = thing.tips()
        if receive.MAIN in tips:
            raise Redirect(_href(thing.name, "history", "main"))
        lines = sorted(thing.line_tips())
        if lines:
            raise Redirect(_href(thing.name, "history", "cand/" + lines[0]))
        raise NotFound("nothing has been pushed yet")
    found = thing.resolve(rest)
    if found is None:
        raise NotFound(f"no commit or branch at {rest!r}")
    sha, path, ref = found
    path = path.strip("/")
    chain = thing._need(vcs.first_parents(thing.repo, sha, limit=HISTORY_LIMIT), "rev-list")
    more = len(chain) > HISTORY_LIMIT
    shown = chain[:HISTORY_LIMIT]
    objects = thing.commits(chain)
    on_line = (ref or "").startswith("cand/")
    pairs = thing.nope_pairs(shown) if on_line else {}
    touched: set[str] | None = None
    if path:
        oids = thing.path_oids({s: objects[s].tree for s in chain}, path)
        touched = set()
        for at, s in enumerate(shown):
            parent = chain[at + 1] if at + 1 < len(chain) else None
            if oids.get(s) != (oids.get(parent) if parent else None):
                touched.add(s)
    rows: list[dict[str, Any]] = []
    for s in shown:
        if touched is not None and s not in touched:
            continue
        nope = pairs.get(s, {})
        kind = nope.get("Nope-Kind", "") if on_line else ""
        row = {"sha": s, "kind": kind or ("working" if on_line else ""),
               "name": nope.get("Nope-Name", ""), "trigger": nope.get("Nope-Trigger", ""),
               "subject": _subject(objects[s].message),
               "parent": objects[s].parents[0] if objects[s].parents else ""}
        # Working commits fold (section 11): consecutive ones become one row.
        if on_line and row["kind"] == "working" and rows and "working" in rows[-1]:
            rows[-1]["working"].append(row)
        elif on_line and row["kind"] == "working":
            rows.append({"working": [row]})
        else:
            rows.append(row)
    return {"view": "history", "thing": thing.name, "ref": ref or "", "sha": sha, "path": path,
            "on_line": on_line, "rows": rows, "more": more}


def view_commit(thing: Thing, rest: str, *, validate: bool = False) -> dict[str, Any]:
    found = thing.resolve(rest)
    if found is None or found[1]:
        raise NotFound(f"no commit at {rest!r}")
    sha, _path, ref = found
    if ref is not None:
        raise Redirect(_href(thing.name, "commit", sha))
    obj = thing.commits([sha])[sha]
    trailers = thing._need(vcs.trailer_lines(thing.repo, [sha]), "trailers").get(sha, [])
    head = thing.head(sha)
    parent = obj.parents[0] if obj.parents else ""
    before = (thing._need(vcs.tree_of(thing.repo, parent), "rev-parse tree") if parent
              else thing._need(vcs.empty_tree(thing.repo), "empty tree"))
    changes = thing._need(vcs.tree_diff(thing.repo, before, obj.tree), "diff-tree")
    added = [c.path for c in changes if c.status == "A"]
    data: dict[str, Any] = {
        "view": "commit", "thing": thing.name, "sha": sha, "commit": head,
        "tree": obj.tree, "parents": list(obj.parents),
        "message": printable_block(obj.message),
        "trailers": [[printable(k), printable(v)] for k, v in trailers],
        "changed": len(changes),
        "verdicts_added": [p for p in added if p.startswith(receive.VERDICTS_PREFIX)],
        "trace_added": [p for p in added if p.startswith(receive.TRACE_PREFIX)],
    }
    # What `nopekit validate` prints for it today, on request: the walk re-reads
    # every claiming commit of the line, and a refusal is the agent's to read
    # in its terminal, not a status the screens show.
    if validate and head["line"] is not None:
        data["validate"] = thing.validation(sha, head["line"])
    return data


def printable_block(message: bytes) -> str:
    """A commit message for a page: decoded with replacement, every control
    character but newline and tab escaped as `printable` escapes it."""
    text = message.decode("utf-8", "replace")
    return "\n".join(printable(line) if line.strip() else "" for line in text.split("\n")).strip()


def view_activity(eddy: "Eddy", thing: Thing) -> dict[str, Any]:
    rows = read_events(eddy.root, thing.name)
    return {"view": "activity", "thing": thing.name, "total": len(rows),
            "events": list(reversed(rows[-ACTIVITY_LIMIT:]))}


# --------------------------------------------------------------------------- #
# what the screens read beyond section 11: the family tree, marks, boxes, packs
# --------------------------------------------------------------------------- #
#: Where the agent records each candidate's mark: one file at the tip of
#: ``main``. A mark changes after its candidate's commit is made (selected
#: today, not selected tomorrow), so it cannot live in that commit, and an
#: evidence commit may add nothing but evidence (E36). Rejected: a trailer
#: (schema 1 is frozen, and a trailer cannot change); a file read from each
#: line's tip (a change of mind would need a new candidate on every line).
MARKS_REL = "eddy/marks.json"
MARKS = ("selected", "proposal", "not-selected")

#: A box the agent supplies (the yellow outline): one folder per box under
#: ``site/boxes/`` holding ``box.json`` and its files. A custom box is one
#: self-contained HTML document Eddy runs in a sandboxed frame.
BOXES_DIR = "site/boxes/"
BOX_KINDS = ("image", "video", "custom")
BOX_VIEWS = ("engineering", "marketing", "both")
BOX_SECTIONS = ("choices", "implications", "claims", "story", "specs")
BOX_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
BOX_KEYS = {"schema", "kind", "title", "caption", "src", "thumbnail", "view", "section",
            "about", "aspect"}
#: The most boxes one candidate shows. Rejected: no cap (a page of hundreds of
#: live frames is one a phone cannot open).
MAX_BOXES = 24
#: What a box's own files are served as; anything else is refused. A box is the
#: one place a tree's HTML runs, and it runs sandboxed (`BOX_CSP`).
BOX_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
             ".css": "text/css; charset=utf-8", ".json": "application/json",
             ".svg": "image/svg+xml", **IMAGE_TYPES, **AUDIO_TYPES, **VIDEO_TYPES, **MODEL_TYPES}
#: A box's policy: a unique origin (``sandbox`` without ``allow-same-origin``),
#: scripts allowed, nothing from the network but its own folder. So a box
#: cannot read Eddy's pages, another box, or anything outside itself.
BOX_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline' 'self'; "
           "style-src 'unsafe-inline' 'self'; img-src 'self' data: blob:; "
           "media-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'")
#: Where a candidate keeps the picture the tree draws and a box falls back to.
THUMB_RELS = ("site/thumb.png", "site/thumb.jpg", "site/thumb.webp")
#: The baked page data the standard renderer reads (`nopekit site build`).
PAGE_REL = "site/data/page.json"


def _git_out(eddy_root: str, repo: str, args: list[str], stdin: bytes = b"") -> bytes | None:
    """A read `vcs` has no question for (a commit's time), with the server's
    own environment and `vcs`'s timeout; ``None`` on any failure."""
    try:
        proc = subprocess.run(["git", "-C", repo, *args], env=_base_env(eddy_root), input=stdin,
                              capture_output=True, timeout=vcs.VCS_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def read_marks(thing: Thing) -> dict[str, Any]:
    """``{"marks": {name: mark}, "problems": [...]}`` from `MARKS_REL` at the
    tip of ``main``, read strictly; no file is no marks, never a guess."""
    tip = thing.tips().get(receive.MAIN)
    if not tip:
        return {"marks": {}, "problems": [], "from": ""}
    entry = thing.entry_at(tip, MARKS_REL)
    if entry is None or entry.type != "blob":
        return {"marks": {}, "problems": [], "from": tip}
    raw = thing._need(vcs.blobs(thing.repo, [entry.oid]), "cat-file blob")[entry.oid]
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return {"marks": {}, "problems": [f"{MARKS_REL} does not parse: {exc}"], "from": tip}
    marks, problems = {}, []
    listed = doc.get("marks") if isinstance(doc, dict) and doc.get("schema") == 1 else None
    if not isinstance(listed, dict):
        return {"marks": {}, "from": tip,
                "problems": [f'{MARKS_REL} is not {{"schema": 1, "marks": {{"<name>": "<mark>"}}}}']}
    for name, mark in listed.items():
        if mark in MARKS:
            marks[str(name)] = mark
        else:
            problems.append(f"{MARKS_REL}: {printable(name)} is marked {printable(mark)!r}; "
                            f"a mark is one of {', '.join(MARKS)}")
    if sum(1 for m in marks.values() if m == "selected") > 1:
        problems.append(f"{MARKS_REL} marks more than one candidate selected")
    return {"marks": marks, "problems": problems, "from": tip}


def view_family(thing: Thing) -> dict[str, Any]:
    """The family tree: every candidate on every line, newest first, each with
    its parent (the nearest candidate below it on its line's chain), the
    working commits between them, its latest evidence commit (``head``: the
    tree the screens read), its mark, and whether it carries a thumbnail and
    baked page data. A commit two lines share (a fork's base) belongs to the
    line it sits nearest the tip of."""
    lines = thing.line_tips()
    chains = {line: thing.chain(line) for line in sorted(lines)}
    owner: dict[str, tuple[int, str]] = {}
    for line, chain in chains.items():
        for depth, sha in enumerate(chain):
            if sha not in owner or (depth, line) < owner[sha]:
                owner[sha] = (depth, line)
    shas = sorted(owner)
    if not shas:
        return {"view": "family", "thing": thing.name, "candidates": [], "lines": [],
                **read_marks(thing)}
    pairs = thing.nope_pairs(shas)
    objects = thing.commits(shas)
    times: dict[str, int] = {}
    out = _git_out(thing.eddy.root, thing.repo,
                   ["-c", "log.showSignature=false", "log", "--no-walk=unsorted", "--stdin",
                    "--format=%H %ct"], stdin="".join(s + "\n" for s in shas).encode("ascii"))
    for row in (out or b"").decode("ascii", "replace").split("\n"):
        sha, _sep, stamp = row.partition(" ")
        if stamp.isdigit():
            times[sha] = int(stamp)
    found: dict[str, dict[str, Any]] = {}
    for line, chain in chains.items():
        below: dict[str, Any] | None = None          # the nearest candidate under the walk
        work = 0
        for sha in reversed(chain):                  # oldest first
            kind = pairs.get(sha, {}).get("Nope-Kind", "")
            mine = owner[sha][1] == line
            if kind == "candidate":
                if sha in found:
                    below = found[sha]
                elif mine:
                    below = found[sha] = {
                        "sha": sha, "head": sha, "name": pairs[sha].get("Nope-Name", ""),
                        "line": line, "subject": _subject(objects[sha].message),
                        "time": times.get(sha, 0), "work": work, "evidence": 0,
                        "parent": below["sha"] if below else "",
                        "model": pairs[sha].get("Nope-Model", ""),
                    }
                work = 0
            elif kind == "evidence":
                if below is not None and mine and owner[below["sha"]][1] == line:
                    below["head"] = sha
                    below["evidence"] += 1
            else:
                work += 1
    cands = sorted(found.values(), key=lambda c: (-c["time"], c["name"]))
    heads = {c["head"]: objects[c["head"]].tree for c in cands}
    have_page = thing.path_oids(heads, PAGE_REL) if heads else {}
    thumbs = {rel: thing.path_oids(heads, rel) for rel in THUMB_RELS} if heads else {}
    marks = read_marks(thing)
    for c in cands:
        c["site"] = bool(have_page.get(c["head"]))
        c["thumb"] = next((rel for rel in THUMB_RELS if thumbs[rel].get(c["head"])), "")
        c["mark"] = marks["marks"].get(c["name"], "")
    return {"view": "family", "thing": thing.name, "candidates": cands,
            "lines": [{"line": line, "tip": lines[line]} for line in sorted(lines)], **marks}


def view_boxes(thing: Thing, rest: str) -> dict[str, Any]:
    """Every box of commit ``rest``: each ``site/boxes/<id>/box.json`` read
    strictly. A box out of shape is listed under ``problems`` and not shown."""
    found = thing.resolve(rest)
    if found is None or found[1]:
        raise NotFound(f"no commit at {rest!r}")
    sha = found[0]
    tree = thing._need(vcs.tree_of(thing.repo, sha), "rev-parse tree")
    listing = thing._need(vcs.tree_listing(thing.repo, tree), "ls-tree")
    files = {e.path: e for e in listing if e.path.startswith(BOXES_DIR) and e.type == "blob"}
    manifests = sorted(p for p in files if p.count("/") == 3 and p.endswith("/box.json"))
    raw = thing._need(vcs.blobs(thing.repo, [files[p].oid for p in manifests]), "cat-file blob") \
        if manifests else {}
    boxes, problems = [], []
    for path in manifests[:MAX_BOXES]:
        box_id = path.split("/")[2]
        folder = f"{BOXES_DIR}{box_id}/"
        why = _box_problem(box_id, raw[files[path].oid], folder, files)
        if isinstance(why, str):
            problems.append(f"{path}: {why}")
            continue
        doc = why
        base = _href(thing.name, "raw", sha, folder.rstrip("/"))
        box = {"id": box_id, "kind": doc["kind"], "title": doc.get("title", ""),
               "caption": doc.get("caption", ""), "view": doc.get("view", "both"),
               "section": doc["section"], "about": doc.get("about", ""),
               "aspect": doc.get("aspect", "4/3"),
               "thumbnail": f"{base}/{urllib.parse.quote(doc['thumbnail'])}"}
        if doc["kind"] == "custom":
            box["src"] = _href(thing.name, "box", sha, box_id, doc["src"])
        else:
            box["src"] = f"{base}/{urllib.parse.quote(doc['src'])}"
        boxes.append(box)
    if len(manifests) > MAX_BOXES:
        problems.append(f"{len(manifests)} boxes; the first {MAX_BOXES} are shown")
    return {"view": "boxes", "thing": thing.name, "sha": sha, "boxes": boxes,
            "problems": problems}


def _box_problem(box_id: str, raw: bytes, folder: str, files: dict[str, Any]) -> Any:
    """The manifest as a dict, or one line saying why it is not a box."""
    if not BOX_ID.match(box_id):
        return "a box's folder is lower-case letters, digits and '-', at most 40"
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return f"does not parse: {exc}"
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        return 'is not {"schema": 1, ...}'
    unknown = sorted(set(doc) - BOX_KEYS)
    if unknown:
        return f"unknown key {unknown[0]!r}; the keys are {', '.join(sorted(BOX_KEYS))}"
    if doc.get("kind") not in BOX_KINDS:
        return f"kind is one of {', '.join(BOX_KINDS)}"
    if doc.get("section") not in BOX_SECTIONS:
        return f"section is one of {', '.join(BOX_SECTIONS)}"
    if doc.get("view", "both") not in BOX_VIEWS:
        return f"view is one of {', '.join(BOX_VIEWS)}"
    if not re.match(r"^[0-9]{1,2}/[0-9]{1,2}$", str(doc.get("aspect", "4/3"))):
        return 'aspect is "<w>/<h>", such as "16/9"'
    for key in ("title", "caption", "about"):
        if not isinstance(doc.get(key, ""), str) or len(doc.get(key, "")) > 300:
            return f"{key} is text, at most 300 characters"
    for key in ("src", "thumbnail"):
        name = doc.get(key)
        if not isinstance(name, str) or "/" in name or name.startswith(".") or not name:
            return f"{key} names a file in the box's own folder"
        if folder + name not in files:
            return f"{key} names {name}, which the folder does not hold"
    ext = posixpath.splitext(doc["src"].lower())[1]
    wanted = {"image": IMAGE_TYPES, "video": VIDEO_TYPES, "custom": {".html": 1}}[doc["kind"]]
    if ext not in wanted:
        return f"a {doc['kind']} box's src ends in one of {', '.join(sorted(wanted))}"
    if posixpath.splitext(doc["thumbnail"].lower())[1] not in IMAGE_TYPES:
        return "thumbnail is a static image: " + ", ".join(sorted(IMAGE_TYPES))
    return doc


def box_file(thing: Thing, rest: str) -> tuple[bytes, str]:
    """``(bytes, content type)`` for one file of a box: ``<sha>/<id>/<name>``."""
    parts = rest.split("/")
    if len(parts) != 3 or not _HEX.match(parts[0]) or not BOX_ID.match(parts[1]):
        raise NotFound("a box file is /box/<sha>/<id>/<name>")
    sha, box_id, name = parts
    ctype = BOX_TYPES.get(posixpath.splitext(name.lower())[1])
    if ctype is None or name.startswith("."):
        raise NotFound(f"a box does not serve {name}")
    if thing.resolve(sha) is None:
        raise NotFound(f"no commit {sha[:12]}")
    entry = thing.entry_at(sha, f"{BOXES_DIR}{box_id}/{name}")
    if entry is None or entry.type != "blob" or entry.mode == "120000":
        raise NotFound(f"{name} is not in box {box_id}")
    if (entry.size or 0) > receive.MAX_BLOB_BYTES:
        raise NotFound(f"{name} is over the cap")
    return thing._need(vcs.blobs(thing.repo, [entry.oid]), "cat-file blob")[entry.oid], ctype


_PACKS: dict[str, Any] = {}


def read_packs() -> list[dict[str, Any]]:
    """The packs this checkout bundles, read without running any of them:
    ``pack.json``, and each gate's declaration parsed from its source (its id,
    title, tier, prerequisites and the known-bad input it is shown to fail)."""
    import ast
    root = os.path.join(CHECKOUT, "packs")
    if "list" in _PACKS:
        return _PACKS["list"]
    packs = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        meta_path = os.path.join(root, name, "pack.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            continue
        gates = []
        gdir = os.path.join(root, name, "gates")
        for fn in sorted(os.listdir(gdir)) if os.path.isdir(gdir) else []:
            if not fn.endswith(".py"):
                continue
            try:
                with open(os.path.join(gdir, fn), encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                for dec in getattr(node, "decorator_list", []) if isinstance(node, ast.FunctionDef) else []:
                    if not (isinstance(dec, ast.Call) and getattr(dec.func, "id", "") == "gate"):
                        continue
                    kw = {k.arg: k.value for k in dec.keywords}

                    def lit(value: Any, default: Any = "") -> Any:
                        try:
                            return ast.literal_eval(value) if value is not None else default
                        except (ValueError, SyntaxError):
                            return default
                    control = kw.get("negative_control")
                    ck = {k.arg: k.value for k in control.keywords} if isinstance(control, ast.Call) else {}
                    gates.append({"id": lit(kw.get("id")), "title": lit(kw.get("title")),
                                  "tier": str(getattr(kw.get("tier"), "attr", "")).lower(),
                                  "needs": lit(kw.get("needs"), []), "file": f"gates/{fn}",
                                  "fixture": lit(ck.get("fixture")), "note": lit(ck.get("note"))})
        files = []
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, name)):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                files.append([os.path.relpath(full, os.path.join(root, name)).replace(os.sep, "/"),
                              os.path.getsize(full)])
        packs.append({"name": name, "version": str(meta.get("version", "")),
                      "description": str(meta.get("description", "")),
                      "settles": list(meta.get("settles", [])), "gates": gates, "files": files,
                      "proposers": [x.get("id") for x in meta.get("provides_proposers", [])
                                    if isinstance(x, dict)]})
    _PACKS["list"] = packs
    return packs


def pack_file(name: str, rel: str) -> bytes:
    """A text file of a bundled pack, by its listed path only."""
    pack = next((p for p in read_packs() if p["name"] == name), None)
    if pack is None or rel not in {f[0] for f in pack["files"]}:
        raise NotFound(f"{rel} is not a file of pack {name}")
    with open(os.path.join(CHECKOUT, "packs", name, *rel.split("/")), "rb") as fh:
        return fh.read(receive.MAX_BLOB_BYTES)


# --------------------------------------------------------------------------- #
# the server
# --------------------------------------------------------------------------- #
class Eddy:
    """The data directory and the switches one server runs with."""

    def __init__(self, root: str, *, public_read: bool = False, quiet: bool = False) -> None:
        self.root = os.path.abspath(root)
        self.public_read = public_read
        self.quiet = quiet
        self._cache: dict[tuple, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def has_thing(self, name: str) -> bool:
        return bool(THING_NAME.match(name)) and os.path.isdir(repo_path(self.root, name))

    def cached_validation(self, key: tuple) -> dict[str, Any] | None:
        with self._lock:
            return self._cache.get(key)

    def keep_validation(self, key: tuple, value: dict[str, Any]) -> None:
        with self._lock:
            if len(self._cache) >= VALIDATE_CACHE:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = value

    def log(self, line: str) -> None:
        if not self.quiet:
            sys.stderr.write(printable(line) + "\n")
            sys.stderr.flush()


class EddyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], eddy: Eddy) -> None:
        self.eddy = eddy
        super().__init__(address, Handler)


def make_server(eddy: Eddy, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> EddyServer:
    return EddyServer((host, port), eddy)


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 so git's `Expect: 100-continue` is answered at once; every
    # response still says `Connection: close` and closes, so a streamed body
    # needs no length and no chunking.
    protocol_version = "HTTP/1.1"
    server_version = "Eddy/1"
    server: EddyServer

    # -- plumbing ------------------------------------------------------------ #
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — the base's name
        self.server.eddy.log(f"{self.address_string()} {format % args}")

    def _headers(self, status: int, ctype: str, *, length: int | None = None,
                 csp: str = PAGE_CSP, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", csp)
        self.send_header("Connection", "close")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True

    def _send(self, status: int, body: bytes, ctype: str, **kw: Any) -> None:
        self._headers(status, ctype, length=len(body), **kw)
        self.wfile.write(body)

    def _plain(self, status: int, text: str, **kw: Any) -> None:
        self._send(status, text.encode("utf-8"), "text/plain; charset=utf-8", **kw)

    def _json(self, status: int, data: Any) -> None:
        body = json.dumps(data, indent=1, ensure_ascii=False).encode("utf-8", "surrogateescape")
        self._send(status, body, "application/json; charset=utf-8",
                   extra={"Cache-Control": "no-store"})

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message, "status": status})

    def _token(self) -> str:
        scheme, _sep, value = self.headers.get("Authorization", "").partition(" ")
        if scheme.lower() == "bearer":
            return value.strip()
        if scheme.lower() == "basic":
            try:
                user, _colon, password = base64.b64decode(value.strip()).decode("utf-8").partition(":")
            except (ValueError, UnicodeDecodeError):
                return ""
            # The token is the password; a client that puts it in the user
            # name with an empty password (as some tools do) is read the same.
            return password or user
        return ""

    def _login(self, *, write: bool) -> str | None:
        """The token's login, ``""`` for an anonymous read under
        ``--public-read``, or ``None`` after sending 401."""
        try:
            login = login_for(self.server.eddy.root, self._token())
        except EddyError as exc:
            self.server.eddy.log(f"tokens: {exc}")
            self._plain(500, "the server's tokens file does not parse; its keeper must fix it\n")
            return None
        if login:
            return login
        if not write and self.server.eddy.public_read:
            return ""
        self._headers(401, "text/plain; charset=utf-8", length=len(_UNAUTHORIZED),
                      extra={"WWW-Authenticate": 'Basic realm="eddy", charset="UTF-8"'})
        self.wfile.write(_UNAUTHORIZED)
        return None

    def _host(self) -> str:
        host = self.headers.get("Host", "")
        return host if _HOST.match(host) else ""

    # -- dispatch ------------------------------------------------------------ #
    def do_GET(self) -> None:  # noqa: N802 — the base's name
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802 — the base's name
        self._dispatch()

    def _dispatch(self) -> None:
        split = urllib.parse.urlsplit(self.path)
        path, query = split.path, split.query
        try:
            git = _GIT_ROUTE.match(path)
            if git:
                self._git(git.group(1), git.group(2), query)
            elif path == "/healthz":
                self._plain(200, "ok\n")
            elif path == "/agent.md":
                self._agent_guide()
            elif path.startswith("/static/"):
                self._static(path[len("/static/"):])
            elif path == "/api/things":
                self._api_things()
            elif self.command != "GET":
                self._error(405, f"{self.command} is not a method this route takes")
            elif path in ("", "/"):
                if self._login(write=False) is not None:
                    self._static("index.html")
            elif path.startswith("/api/packs"):
                self._api_packs(path[len("/api/packs"):].strip("/"), query)
            else:
                self._browse(path, query)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001 — one line in the log, a 500 to the client
            self.server.eddy.log(f"error serving {printable(self.path)}: "
                                 f"{type(exc).__name__}: {exc}")
            try:
                self._error(500, "the server failed on this request; its log says why")
            except OSError:
                self.close_connection = True

    def _static(self, name: str) -> None:
        if name not in STATIC_FILES:
            return self._plain(404, "no such file\n")
        with open(os.path.join(STATIC_DIR, name), "rb") as fh:
            body = fh.read()
        self._send(200, body, STATIC_FILES[name], extra={"Cache-Control": "no-cache"})

    def _agent_guide(self) -> None:
        """The agent's way in: how to push to THIS server, with its address
        filled in. Open to anyone who can reach the port; it holds no secret."""
        with open(os.path.join(HERE, "AGENT.md"), encoding="utf-8") as fh:
            text = fh.read()
        host = self._host() or f"127.0.0.1:{DEFAULT_PORT}"
        body = text.replace("{{EDDY}}", f"http://{host}").replace("{{NOPEKIT}}", CHECKOUT)
        self._send(200, body.encode("utf-8"), "text/markdown; charset=utf-8")

    def _api_things(self) -> None:
        """``GET``: the things. ``POST`` with ``{"name": "<thing>"}``: create a
        thing under the hook (a token is needed)."""
        eddy = self.server.eddy
        if self.command == "GET":
            if self._login(write=False) is None:
                return
            return self._json(200, view_things(eddy))
        if self._login(write=True) is None:
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            doc = json.loads(self.rfile.read(min(length, 4096)) or b"{}")
            name = doc.get("name") if isinstance(doc, dict) else None
            path = create_thing(eddy.root, name)
        except (ValueError, EddyError) as exc:
            return self._error(400, str(exc))
        host = self._host()
        self._json(201, {"thing": name, "path": path,
                         "clone": f"http://{host}/{name}.git" if host else f"/{name}.git"})

    def _api_packs(self, rest: str, query: str) -> None:
        if self._login(write=False) is None:
            return
        if not rest:
            return self._json(200, {"packs": [{k: v for k, v in p.items() if k != "files"}
                                              for p in read_packs()]})
        name, _sep, tail = rest.partition("/")
        pack = next((p for p in read_packs() if p["name"] == name), None)
        if pack is None:
            return self._error(404, f"no pack named {name}")
        if tail == "file":
            rel = urllib.parse.parse_qs(query).get("path", [""])[0]
            try:
                data = pack_file(name, rel)
            except NotFound as exc:
                return self._error(404, str(exc))
            text = _as_text(data)
            return self._json(200, {"path": rel, "text": text, "size": len(data)})
        self._json(200, pack)

    def _browse(self, path: str, query: str = "") -> None:
        if self._login(write=False) is None:
            return
        eddy = self.server.eddy
        match = _BROWSE_ROUTE.match(path)
        if not match:
            return self._error(404, "no route here")
        name = urllib.parse.unquote(match.group(1))
        if not eddy.has_thing(name):
            return self._error(404, f"no thing named {name}")
        thing = Thing(eddy, name)
        rest = urllib.parse.unquote(match.group(2) or "", errors="surrogateescape")
        view, _sep, arg = rest.partition("/")
        try:
            if view in ("", "lines"):
                data = view_lines(thing, self._host())
            elif view == "family":
                data = view_family(thing)
            elif view == "boxes":
                data = view_boxes(thing, arg)
            elif view == "box":
                body, ctype = box_file(thing, arg)
                return self._send(200, body, ctype, csp=BOX_CSP,
                                  extra={"Cache-Control": "private, max-age=3600"})
            elif view == "tree":
                data = view_tree(thing, arg)
            elif view == "blob":
                data = view_blob(thing, arg)
            elif view == "raw":
                body, ctype, filename = raw_blob(thing, arg)
                return self._send(200, body, ctype, csp=RAW_CSP, extra={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Cache-Control": "private, max-age=3600"})
            elif view == "diff":
                data = view_diff(thing, arg)
            elif view == "history":
                data = view_history(thing, arg)
            elif view == "commit":
                data = view_commit(thing, arg, validate="validate" in urllib.parse.parse_qs(query))
            elif view == "activity":
                data = view_activity(eddy, thing)
            elif view == "events":
                return self._events(thing)
            else:
                return self._error(404, f"no view named {view}")
        except Redirect as moved:
            self._headers(302, "text/plain; charset=utf-8", length=0,
                          extra={"Location": moved.location})
            return None
        except NotFound as exc:
            return self._error(404, str(exc))
        except Unanswered as exc:
            return self._error(503, f"{exc}; nothing is shown rather than a guess")
        self._json(200, data)

    def _events(self, thing: Thing) -> None:
        """Server-sent events: each new row of the thing's event log as one
        ``data:`` line, its line number as the ``id`` (``Last-Event-ID``
        resumes after it); starts at the end of the log."""
        eddy = self.server.eddy
        try:
            seen = int(self.headers.get("Last-Event-ID", ""))
        except ValueError:
            seen = len(read_events(eddy.root, thing.name))
        self._headers(200, "text/event-stream; charset=utf-8",
                      extra={"Cache-Control": "no-cache"})
        self.wfile.write(b": connected\n\n")
        started = last_beat = time.monotonic()
        while time.monotonic() - started < SSE_MAX_S:
            rows = read_events(eddy.root, thing.name)
            for number in range(seen, len(rows)):
                payload = json.dumps(rows[number], sort_keys=True)
                self.wfile.write(f"id: {number + 1}\ndata: {payload}\n\n".encode("utf-8"))
            seen = max(seen, len(rows))
            now = time.monotonic()
            if now - last_beat >= SSE_HEARTBEAT_S:
                self.wfile.write(b": still here\n\n")
                last_beat = now
            time.sleep(SSE_POLL_S)

    # -- git ---------------------------------------------------------------- #
    def _body(self) -> Iterator[bytes]:
        """The request body, de-chunked when git streams it (a pack larger
        than ``http.postBuffer`` arrives with ``Transfer-Encoding: chunked``)."""
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            while True:
                size_line = self.rfile.readline(65537)
                size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline(65537) not in (b"\r\n", b"\n", b""):
                        pass
                    return
                left = size
                while left:
                    data = self.rfile.read(min(left, 65536))
                    if not data:
                        raise ConnectionResetError("the request ended inside a chunk")
                    left -= len(data)
                    yield data
                self.rfile.readline(3)
        else:
            left = int(self.headers.get("Content-Length") or 0)
            while left > 0:
                data = self.rfile.read(min(left, 65536))
                if not data:
                    raise ConnectionResetError("the request ended early")
                left -= len(data)
                yield data

    def _git(self, thing: str, route: str, query: str) -> None:
        eddy = self.server.eddy
        if route == "/info/refs":
            service = urllib.parse.parse_qs(query).get("service", [""])[0]
            if self.command != "GET":
                return self._plain(405, "info/refs takes GET\n")
        else:
            service = route[1:]
            if self.command != "POST":
                return self._plain(405, f"{service} takes POST\n")
        if service not in ("git-upload-pack", "git-receive-pack"):
            # Dumb HTTP would serve objects with no receive-pack, so no hook.
            return self._plain(403, "Eddy speaks git's smart HTTP only\n")
        write = service == "git-receive-pack"
        login = self._login(write=write)
        if login is None:
            return
        if not eddy.has_thing(thing):
            return self._plain(404, f"no thing named {thing}; its keeper creates it with "
                                    f"`python3 server/eddy.py new {thing}`\n")
        length = self.headers.get("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > MAX_REQUEST_BYTES):
            return self._plain(413, f"a push over {MAX_REQUEST_BYTES // (1024 * 1024)} MiB is "
                                    "refused before the hook; push in smaller steps\n")
        env = _base_env(eddy.root)
        env.update({
            "GIT_PROJECT_ROOT": os.path.join(eddy.root, REPOS_DIR),
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": f"/{thing}.git{route}",
            "REQUEST_METHOD": self.command,
            "QUERY_STRING": query,
            "REMOTE_ADDR": self.client_address[0],
            "SERVER_PROTOCOL": "HTTP/1.1",
            "GATEWAY_INTERFACE": "CGI/1.1",
        })
        for header, var in (("Content-Type", "CONTENT_TYPE"),
                            ("Content-Encoding", "HTTP_CONTENT_ENCODING"),
                            ("Git-Protocol", "HTTP_GIT_PROTOCOL")):
            if self.headers.get(header):
                env[var] = self.headers[header]
        if length is not None and "chunked" not in self.headers.get("Transfer-Encoding", "").lower():
            env["CONTENT_LENGTH"] = length
        if login:
            # The token's login, never the name typed beside it: what the
            # hook records as `--by` (NOPE_PUSHER, section 10).
            env["REMOTE_USER"] = login
            env["NOPE_PUSHER"] = login
        host = self._host()
        if host:
            env["EDDY_URL"] = f"http://{host}"
        try:
            proc = subprocess.Popen(["git", "http-backend"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                    cwd=os.path.join(eddy.root, REPOS_DIR))
        except OSError as exc:
            eddy.log(f"git http-backend did not start: {exc}")
            return self._plain(502, "git did not start on the server\n")
        timer = threading.Timer(BACKEND_TIMEOUT_S, proc.kill)
        timer.daemon = True
        timer.start()
        too_big = threading.Event()

        def feed() -> None:
            sent = 0
            try:
                for chunk in self._body():
                    sent += len(chunk)
                    if sent > MAX_REQUEST_BYTES:
                        too_big.set()
                        proc.kill()
                        return
                    proc.stdin.write(chunk)
            except (OSError, ValueError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        def drain() -> None:
            for line in proc.stderr:
                eddy.log(f"http-backend {thing}: {line.decode('utf-8', 'replace').rstrip()}")

        feeder = threading.Thread(target=feed, daemon=True)
        drainer = threading.Thread(target=drain, daemon=True)
        feeder.start()
        drainer.start()
        try:
            status, headers = 200, []
            while True:
                line = proc.stdout.readline(65537)
                if not line:
                    raise EOFError("git http-backend ended before its headers")
                line = line.rstrip(b"\r\n")
                if not line:
                    break
                key, _colon, value = line.decode("latin-1").partition(":")
                if key.lower() == "status":
                    status = int(value.strip().split(" ", 1)[0])
                else:
                    headers.append((key.strip(), value.strip()))
            self.send_response(status)
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            while True:
                chunk = proc.stdout.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (EOFError, ValueError) as exc:
            eddy.log(f"git http-backend {thing}: {exc}")
            if too_big.is_set():
                self._plain(413, "the push was over the request cap; nothing landed\n")
            else:
                self._plain(502, "git http-backend failed on the server; its log says why\n")
        except (BrokenPipeError, ConnectionResetError):
            proc.kill()
            self.close_connection = True
        finally:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            timer.cancel()
            feeder.join(timeout=5)
            drainer.join(timeout=5)
            for stream in (proc.stdout, proc.stderr):
                stream.close()
            if login:
                eddy.log(f"{service} {thing} by {login}: exit {proc.returncode}")


_UNAUTHORIZED = (b"a token is needed: Authorization: Bearer <token>, or HTTP basic with the "
                 b"token as the password\n")


# --------------------------------------------------------------------------- #
# the command line
# --------------------------------------------------------------------------- #
def _connect_lines(url: str, thing: str, token: str = "<token>") -> str:
    base = url.rstrip("/")
    return (f"  git remote add eddy {base}/{thing}.git\n"
            f"  git config http.{base}/.extraHeader \"Authorization: Bearer {token}\"\n"
            f"  git push eddy HEAD:refs/heads/draft/first")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eddy.py", description="Eddy: a thing's own git server")
    parser.add_argument("--root", default=None,
                        help="the data directory (default: $EDDY_ROOT or ~/.eddy)")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="serve git, the file browser and activity over HTTP")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--public-read", action="store_true",
                       help="let anyone who reaches the port clone and browse (never push)")
    new = sub.add_parser("new", help="create a thing: a bare repository under the hook")
    new.add_argument("thing")
    new.add_argument("--url", default=f"http://127.0.0.1:{DEFAULT_PORT}",
                     help="the address pushes reach, for the lines printed")
    token = sub.add_parser("token", help="issue a token for a login (printed once)")
    token.add_argument("login")
    sub.add_parser("tokens", help="list each token's login and id (never the token)")
    revoke = sub.add_parser("revoke", help="revoke a token by its id")
    revoke.add_argument("id")
    sub.add_parser("things", help="list the things")
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root) if args.root else default_root()
    try:
        if args.command == "serve":
            return _serve(root, args)
        if args.command == "new":
            path = create_thing(root, args.thing)
            print(f"created {args.thing} at {path}, under the hook\n"
                  f"push to it from a project:\n{_connect_lines(args.url, args.thing)}")
            if not read_tokens(root):
                print("no token exists yet: python3 server/eddy.py token <login>")
            return 0
        if args.command == "token":
            issued = add_token(root, args.login)
            print(f"token for {args.login} (shown once; Eddy keeps only its SHA-256):\n{issued}")
            return 0
        if args.command == "tokens":
            for entry in read_tokens(root):
                print(f"{entry['id']}  {entry['login']}")
            return 0
        if args.command == "revoke":
            n = revoke_token(root, args.id)
            print(f"revoked {n} token(s) with id {args.id}")
            return 0 if n else 1
        if args.command == "things":
            for name in things(root):
                print(name)
            return 0
    except EddyError as exc:
        sys.stderr.write(f"eddy: {exc}\n")
        return 2
    return 2


def _serve(root: str, args: argparse.Namespace) -> int:
    os.makedirs(os.path.join(root, REPOS_DIR), exist_ok=True)
    os.makedirs(os.path.join(root, EVENTS_DIR), exist_ok=True)
    read_tokens(root)                       # a tokens file that does not parse stops the start
    for name in things(root):
        wrote = install_hooks(root, name)
        if wrote:
            sys.stderr.write(f"eddy: {name}: rewrote {', '.join(wrote)} for this checkout\n")
    eddy = Eddy(root, public_read=args.public_read)
    httpd = make_server(eddy, args.host, args.port)
    host, port = httpd.server_address[:2]
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    sys.stderr.write(f"eddy: serving {root} at http://{shown}:{port}/\n")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        sys.stderr.write("eddy: listening beyond loopback over plain HTTP, where a token "
                         "travels in the clear; put TLS in front before anyone else can reach it\n")
    if not read_tokens(root):
        sys.stderr.write("eddy: no token yet; issue one with: python3 server/eddy.py token <login>\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
