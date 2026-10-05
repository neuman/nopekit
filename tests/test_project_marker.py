# SPDX-License-Identifier: Apache-2.0
"""A project is where its marker is, and the search stops at the repository (S-64).

`store.find_root` used to accept any `.nopekit/` DIRECTORY as a project and walk
up to the filesystem root to find one. Two things slipped through that:

* `~/.nopekit/` is where user packs live (`packs.search_paths`). On a pack
  author's machine it made every directory under `~` a project — an empty,
  unnamed one — so `status` in a scratch directory reported "(unnamed) v0.1",
  `init` there was the only command that behaved, and pack mode, the Stop hook's
  fast exit and `/start`'s `init` could never see "no project".
* The walk had no repository boundary. A worktree nested inside a project
  resolved to the TRUNK's `.nopekit/`, so a command run in the worktree read and
  wrote the trunk's ledger.

The marker is now a FILE — `.nopekit/project.json`, or a legacy
`.nopekit/ledger.json` — and the walk stops at the first directory holding a
`.git` entry, checked AFTER the marker at the same level so a project that is
its own git root (the fresh-clone copy of the bracket, any standalone project)
still finds itself (cli:H9, tests:H16).

Every "resolves to None" assertion here depends on nothing above the temp
directory being a project; `setUp` checks that first and says so, so a machine
with a stray marker above `/tmp` fails as the machine, not as the code.

Run:  PYTHONPATH=src python3 -m unittest tests.test_project_marker -v
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from nopekit import store
from nopekit.models import ProjectMeta
from nopekit.util import NopekitError

import _env

#: The two marker spellings, as the files a project holds. Built from the
#: store's own names where it has them, so a rename moves the test with it.
LEGACY_MARKER = os.path.join(store.NOPEKIT_DIR, store.LEDGER_NAME)
PROJECT_MARKER = os.path.join(store.NOPEKIT_DIR, "project.json")
MARKERS = (PROJECT_MARKER, LEGACY_MARKER)


def _write(path: str, text: str = "{}\n") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _mkdir(*parts: str) -> str:
    path = os.path.join(*parts)
    os.makedirs(path, exist_ok=True)
    return path


def _user_pack(home: str, name: str = "mypack") -> str:
    """A user pack, planted where `packs.search_paths` looks for one: the shape of
    `~/.nopekit/` on the machine of anyone who writes packs."""
    pack_dir = _mkdir(home, store.NOPEKIT_DIR, "packs", name)
    _write(os.path.join(pack_dir, "pack.json"), json.dumps({"name": name}) + "\n")
    return pack_dir


def _meta(name: str) -> ProjectMeta:
    # `created` is stamped by the CLI in real use (store never reads the clock).
    return ProjectMeta(name=name, created="2026-01-01T00:00:00Z")


class ProjectMarker(_env.EnvCase):
    def setUp(self):
        base = self.tmp()
        above = os.path.dirname(base)
        found = store.find_root(above)
        self.assertIsNone(
            found,
            f"{found} above the temp directory is an nopekit project on this machine; "
            f"every 'resolves to None' assertion below would measure it, not the code")
        self.base = base

    # ---------------------------------------------------------------- S-64 (V)
    def test_the_user_pack_home_does_not_make_home_a_project(self):
        """V (S-64): `$HOME/.nopekit/packs/` does not make `$HOME/x` a project."""
        home = _mkdir(self.base, "home")
        pack_dir = _user_pack(home)
        x = _mkdir(home, "x")
        with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}):
            # The precondition: this IS the user-pack home as the spine sees it.
            self.assertEqual(
                os.path.abspath(os.path.expanduser(os.path.join("~", store.NOPEKIT_DIR,
                                                                "packs"))),
                os.path.dirname(pack_dir))
            self.assertIsNone(store.find_root(x))
            self.assertIsNone(store.find_root(home))
            self.assertIsNone(store.find_root(pack_dir))
        # End to end: the CLI in `$HOME/x` says there is no project, rather than
        # reporting on an empty, unnamed one at `$HOME`.
        proc = _env.nopekit(["status"], cwd=x, home=home)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("no nopekit project", proc.stderr)
        self.assertNotIn("(unnamed)", proc.stdout)

    # ------------------------------------------------------ the git boundary
    def test_a_nested_worktree_resolves_to_none_not_trunk(self):
        """A worktree (`.git` is a FILE) or a nested clone (`.git` is a directory)
        inside a project is its own world: the walk stops there instead of
        resolving to the trunk's project."""
        for i, marker in enumerate(MARKERS):
            for kind in ("file", "dir"):
                with self.subTest(marker=marker, git=kind):
                    trunk = _mkdir(self.base, f"trunk-{kind}-{i}")
                    _write(os.path.join(trunk, marker))
                    wt = _mkdir(trunk, "wt")
                    if kind == "file":
                        _write(os.path.join(wt, ".git"), "gitdir: /elsewhere/.git/worktrees/wt\n")
                    else:
                        _mkdir(wt, ".git")
                    deep = _mkdir(wt, "model", "parts")
                    self.assertIsNone(store.find_root(deep))
                    self.assertIsNone(store.find_root(wt))
                    # The positive half: the trunk still finds itself from below.
                    self.assertEqual(store.find_root(_mkdir(trunk, "model")), trunk)

    def test_a_project_that_is_its_own_git_root_is_found(self):
        """The marker is checked BEFORE the `.git` stop at each level, or a project
        that is also a repository root — the fresh-clone copy of the bracket, any
        standalone project — would never find itself (cli:H9)."""
        for i, marker in enumerate(MARKERS):
            for kind in ("file", "dir"):
                with self.subTest(marker=marker, git=kind):
                    root = _mkdir(self.base, f"own-{kind}-{i}")
                    _write(os.path.join(root, marker))
                    if kind == "file":
                        _write(os.path.join(root, ".git"), "gitdir: /elsewhere/.git\n")
                    else:
                        _mkdir(root, ".git")
                    self.assertEqual(store.find_root(root), root)
                    self.assertEqual(store.find_root(_mkdir(root, "inputs", "cad")), root)

    def test_a_project_below_a_repository_root_is_found(self):
        """The layout of this repository: `examples/bracket` is a project inside a
        repository whose root is not one. From inside the project it is found; from
        between the two, the walk stops at the repository root with None."""
        repo = _mkdir(self.base, "repo")
        _mkdir(repo, ".git")
        project = _mkdir(repo, "examples", "bracket")
        _write(os.path.join(project, LEGACY_MARKER))
        self.assertEqual(store.find_root(_mkdir(project, "model")), project)
        self.assertIsNone(store.find_root(os.path.join(repo, "examples")))
        self.assertIsNone(store.find_root(repo))

    # ------------------------------------------------------------ the marker
    def test_only_a_marker_file_makes_a_project(self):
        """A `.nopekit/` holding anything but a marker is not a project: scratch
        (`out/`), a user-pack home (`packs/`), or a `.nopekit` that is a file."""
        cases = {
            "empty .nopekit/": lambda d: _mkdir(d, store.NOPEKIT_DIR),
            "only packs/": lambda d: _user_pack(d),
            "only out/ and runs/": lambda d: (_mkdir(d, store.NOPEKIT_DIR, "out"),
                                              _mkdir(d, store.NOPEKIT_DIR, "runs")),
            ".nopekit is a file": lambda d: _write(os.path.join(d, store.NOPEKIT_DIR)),
            "project.json is a directory": lambda d: _mkdir(d, PROJECT_MARKER),
        }
        for i, (label, plant) in enumerate(cases.items()):
            with self.subTest(label):
                d = _mkdir(self.base, f"unmarked-{i}")
                plant(d)
                self.assertIsNone(store.find_root(_mkdir(d, "sub")))
        for i, marker in enumerate(MARKERS):
            with self.subTest(marker=marker):
                d = _mkdir(self.base, f"marked-{i}")
                _write(os.path.join(d, marker))
                self.assertEqual(store.find_root(_mkdir(d, "sub")), d)
                # tolerate being handed a file path, as before
                self.assertEqual(store.find_root(os.path.join(d, marker)), d)

    def test_require_root_names_both_markers_and_the_git_boundary(self):
        """The one place that explains a missing project says what counts as one
        and where the search stops, so a user in a worktree is not told to `init`
        without being told why the trunk above did not count."""
        repo = _mkdir(self.base, "boundary")
        _mkdir(repo, ".git")
        where = _mkdir(repo, "somewhere")
        with self.assertRaises(NopekitError) as caught:
            store.require_root(where)
        message = str(caught.exception)
        for needle in (where, "project.json", "ledger.json", ".git", repo, "nopekit init"):
            self.assertIn(needle, message)

    # ------------------------------------------------------------------- init
    def test_init_succeeds_beside_user_packs_only(self):
        """`init` refuses only when a MARKER exists: a directory holding only
        `.nopekit/packs/` is not a project, and its packs survive the init."""
        d = _mkdir(self.base, "packs-only")
        pack_dir = _user_pack(d)
        with open(os.path.join(pack_dir, "pack.json"), "rb") as fh:
            before = fh.read()
        store.init(d, _meta("packs-only"))
        self.assertEqual(store.find_root(d), d)
        with open(os.path.join(pack_dir, "pack.json"), "rb") as fh:
            self.assertEqual(fh.read(), before, "init touched a pack it found in place")

    def test_init_still_refuses_a_marker(self):
        """The negative control for the one above: loosening the refusal from
        "`.nopekit/` exists" to "a marker exists" must not let `init` clobber a
        project, in either spelling."""
        # Directory names carry no marker spelling, so the message assertion
        # below can only be satisfied by the message, not by the path in it.
        for i, marker in enumerate(MARKERS):
            with self.subTest(marker=marker):
                d = _mkdir(self.base, f"refuse-{i}")
                path = _write(os.path.join(d, marker), '{"sentinel": true}\n')
                with self.assertRaises(NopekitError) as caught:
                    store.init(d, _meta("refuse"))
                self.assertIn(os.path.basename(marker), str(caught.exception))
                with open(path, "r", encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), '{"sentinel": true}\n')
        d = _mkdir(self.base, "twice")
        store.init(d, _meta("twice"))
        with self.assertRaises(NopekitError):
            store.init(d, _meta("twice"))
        # A `.nopekit` that is a FILE marks nothing, but `init` over it must
        # still refuse before writing anything, not half-build a layout.
        d = _mkdir(self.base, "dot-is-a-file")
        _write(os.path.join(d, store.NOPEKIT_DIR), "not a directory\n")
        with self.assertRaises(NopekitError):
            store.init(d, _meta("dot-is-a-file"))
        self.assertEqual(os.listdir(d), [store.NOPEKIT_DIR])


if __name__ == "__main__":
    unittest.main()
