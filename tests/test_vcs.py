# SPDX-License-Identifier: Apache-2.0
"""`vcs` is the only git edge, and it answers for the project it is asked about.

A git hook exports `GIT_DIR`, `GIT_INDEX_FILE` and `GIT_WORK_TREE` pointing at the
repository that ran the hook. An nopekit command started from one would, through
a naive `subprocess.run(["git", ...])`, report that repository's HEAD, index and
files as the project's — and a control entry's selftest walk, a verdict's age and
a signer's identity would all be facts about somewhere else. Each test below
plants the trap, shows it is real (a git that inherits the variables DOES answer
for the foreign repository), and then asks `vcs`.

The other half of the contract is that `vcs` never raises: no git, no repository,
a hung git — each is an answer (`None`, `{}`, `False`), because `verify.sh --dir`
checks a copy of the bracket with no `.git` and `test_site` runs `doctor` in a
bare temp directory (tests:H11).

Every repository here is built in a temp directory through `_env.git`; nothing
reads this checkout's own `.git`, which a `verify.sh --dir` copy does not have.
`vcs` runs in THIS process, so `setUp` pins what it inherits: no global or system
git config, no user excludes, no inherited `GIT_*` — the dev box's `~/.gitconfig`
must not decide what `ls_files` ignores.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_vcs.py -v
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import pathlib
import shutil
import stat
import sys
import time
import unittest
from unittest import mock

from nopekit import vcs

import _env

#: The variables spec §4 U13 says the git edge strips. The test also plants every
#: variable the installed git lists in `git rev-parse --local-env-vars` — git's own
#: list of what picks a repository — so a newer git that adds one turns this red.
SPEC_STRIPPED = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES", "GIT_CONFIG_PARAMETERS",
)

#: What the git edge sets on every call.
SPEC_SET = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


class VcsIsTheOnlyGitEdge(_env.EnvCase):
    def setUp(self):
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for name in list(os.environ):
            if name.startswith(("GIT_", "CLAUDE")) or name in ("EMAIL", "XDG_CONFIG_HOME"):
                del os.environ[name]
        home = self.tmp()
        os.environ.update({"HOME": home, "XDG_CONFIG_HOME": home,
                           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})

    # -- helpers ------------------------------------------------------------ #
    def _require_git(self) -> None:
        # Not an invariant class, so this skip is allowed; every CI runner and
        # every checkout of this repository has git. The no-git and hung-git
        # tests below run without one.
        if shutil.which("git") is None:
            self.skipTest("git is not on PATH")

    def _git(self, args, cwd, **kw):
        proc = _env.git(args, cwd=cwd, **kw)
        self.assertEqual(proc.returncode, 0, f"git {args}: {proc.stderr}")
        return proc.stdout

    def _repo(self, files: dict[str, str], *, when: str = "2021-01-01T00:00:00Z") -> str:
        self._require_git()
        repo = self.tmp()
        self._git(["init", "-q"], repo)
        if files:
            self._commit(repo, files, when=when)
        return repo

    def _commit(self, repo: str, files: dict[str, str], *, when: str) -> str:
        for rel, text in files.items():
            _write(os.path.join(repo, rel), text)
        self._git(["add", "--", *files], repo)
        self._git(["commit", "-qm", f"at {when}"], repo, identity=True,
                  env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})
        return self._git(["rev-parse", "HEAD"], repo).strip()

    def _not_a_repo(self) -> str:
        path = self.tmp()
        if shutil.which("git") is not None:
            self.assertNotEqual(
                _env.git(["rev-parse", "--git-dir"], cwd=path).returncode, 0,
                f"{path} is inside a git repository: this machine's temp directory "
                f"is, so a non-repository cannot be built here")
        return path

    def _fake_git(self, body: str) -> str:
        """A directory holding an executable ``git`` that runs ``body`` (Python)."""
        bindir = self.tmp()
        path = os.path.join(bindir, "git")
        _write(path, f"#!{sys.executable}\n{body}\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return bindir

    # -- the spec's V: a foreign repository in the environment -------------- #
    def test_a_foreign_repository_in_the_environment_is_ignored(self):
        project = self._repo({"p.txt": "p\n"})
        self._git(["config", "user.name", "Project Person"], project)
        self._git(["config", "user.email", "project@nopekit.invalid"], project)
        project_head = self._git(["rev-parse", "HEAD"], project).strip()
        foreign = self._repo({"f.txt": "f\n", "g.txt": "g\n"}, when="2019-05-05T00:00:00Z")
        foreign_head = self._git(["rev-parse", "HEAD"], foreign).strip()
        plain = self._not_a_repo()

        planted = {
            "GIT_DIR": os.path.join(foreign, ".git"),
            "GIT_INDEX_FILE": os.path.join(foreign, ".git", "index"),
            "GIT_WORK_TREE": foreign,
            "GIT_CONFIG_PARAMETERS": "'user.name'='Foreign Hook' 'user.email'='hook@foreign.invalid'",
        }
        # The trap is real: a git that inherits these answers for the foreign
        # repository, even asked from inside the project, even from no repository.
        self.assertEqual(self._git(["rev-parse", "HEAD"], project, env=planted).strip(),
                         foreign_head)
        self.assertEqual(self._git(["rev-parse", "HEAD"], plain, env=planted).strip(),
                         foreign_head)
        self.assertIn("Foreign Hook",
                      self._git(["var", "GIT_AUTHOR_IDENT"], project, env=planted))

        with mock.patch.dict(os.environ, planted):
            self.assertEqual(vcs.git_head(project), project_head)
            self.assertTrue(vcs.is_repo(project))
            self.assertEqual(vcs.ls_files(project, []), ["p.txt"])
            self.assertEqual(vcs.ls_files(project, [], others=False), ["p.txt"])
            self.assertEqual(vcs.commit_times(project, ["p.txt", "f.txt"]),
                             {"p.txt": "2021-01-01T00:00:00Z"})
            self.assertEqual(vcs.ident(project), "Project Person <project@nopekit.invalid>")

            self.assertIsNone(vcs.git_head(plain), "a directory with no repository "
                              "answered with the inherited GIT_DIR's HEAD")
            self.assertFalse(vcs.is_repo(plain))
            self.assertIsNone(vcs.ls_files(plain, []))
            self.assertEqual(vcs.commit_times(plain, ["f.txt"]), {})

    def test_the_call_is_argv_with_a_clean_environment(self):
        """What the child actually receives, recorded by a stand-in git."""
        where = self.tmp()
        record = os.path.join(where, "record.json")
        bindir = self._fake_git(
            "import json, os, sys\n"
            f"json.dump({{'argv': sys.argv[1:], 'env': dict(os.environ)}}, open({record!r}, 'w'))\n"
            "print('0' * 40)")
        local = []
        if shutil.which("git") is not None:
            local = self._git(["rev-parse", "--local-env-vars"], where).split()
            self.assertIn("GIT_DIR", local, "git no longer lists its local variables")
        planted = {name: "planted" for name in (*SPEC_STRIPPED, *local)}
        planted.update(PATH=bindir, GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_AUTHOR_NAME="A User", LC_ALL="xx_XX.UTF-8")
        with mock.patch.dict(os.environ, planted):
            head = vcs.git_head(where)

        self.assertTrue(os.path.isfile(record), "the stand-in git never ran — is the "
                        "temp directory mounted noexec?")
        self.assertEqual(head, "0" * 40, "vcs did not take the stand-in's answer")
        with open(record, encoding="utf-8") as fh:
            seen = json.load(fh)
        self.assertEqual(seen["argv"][:2], ["-C", where], "not argv form with -C root")
        leaked = sorted(name for name in (*SPEC_STRIPPED, *local) if name in seen["env"])
        self.assertEqual(leaked, [], "a repository-selecting variable reached git")
        for name, value in SPEC_SET.items():
            self.assertEqual(seen["env"].get(name), value, name)
        # What the user set on purpose is theirs: their config file, their name.
        self.assertEqual(seen["env"].get("GIT_CONFIG_GLOBAL"), os.devnull)
        self.assertEqual(seen["env"].get("GIT_AUTHOR_NAME"), "A User")

    # -- never raises ------------------------------------------------------- #
    def test_a_non_repository_is_none(self):
        plain = self._not_a_repo()
        _write(os.path.join(plain, "a.txt"), "a\n")
        self.assertFalse(vcs.is_repo(plain))
        self.assertIsNone(vcs.git_head(plain))
        self.assertIsNone(vcs.ls_files(plain, []))
        self.assertIsNone(vcs.ls_files(plain, ["a.txt"], others=False))
        self.assertEqual(vcs.commit_times(plain, ["a.txt"]), {})
        missing = os.path.join(plain, "no-such-dir")
        self.assertIsNone(vcs.git_head(missing))
        self.assertIsNone(vcs.ls_files(missing, []))
        self.assertIsNone(vcs.ident(missing))

    def test_a_forced_timeout_is_none(self):
        """A hung git (an fsmonitor daemon, a credential prompt) costs the timeout
        and reads as no answer — never a hang, never an exception."""
        project = self.tmp()
        bindir = self._fake_git("import time\ntime.sleep(60)")
        calls = {
            "git_head": lambda: vcs.git_head(project),
            "is_repo": lambda: vcs.is_repo(project),
            "ls_files": lambda: vcs.ls_files(project, []),
            "ident": lambda: vcs.ident(project),
            "commit_times": lambda: vcs.commit_times(project, ["x"]),
        }
        with mock.patch.dict(os.environ, {"PATH": bindir}), \
                mock.patch.object(vcs, "VCS_TIMEOUT_S", 0.5):
            for name, call in calls.items():
                with self.subTest(call=name):
                    started = time.perf_counter()
                    answer = call()
                    elapsed = time.perf_counter() - started
                    self.assertIn(answer, (None, {}, False))
                    # Waited for the timeout — so the stand-in did run and hang,
                    # rather than failing to start — and gave up long before it.
                    self.assertGreaterEqual(elapsed, 0.45, f"{name} returned in {elapsed:.2f}s")
                    self.assertLess(elapsed, 20, f"{name} waited {elapsed:.1f}s")

    def test_no_git_at_all_is_none(self):
        empty = self.tmp()
        with mock.patch.dict(os.environ, {"PATH": empty}):
            self.assertFalse(vcs.is_repo(empty))
            self.assertIsNone(vcs.git_head(empty))
            self.assertIsNone(vcs.ls_files(empty, []))
            self.assertIsNone(vcs.ident(empty))
            self.assertEqual(vcs.commit_times(empty, ["x"]), {})

    # -- each answer -------------------------------------------------------- #
    def test_git_head_and_is_repo(self):
        repo = self._repo({"a.txt": "a\n", "sub/b.txt": "b\n"})
        head = self._git(["rev-parse", "HEAD"], repo).strip()
        self.assertEqual(vcs.git_head(repo), head)
        self.assertEqual(vcs.git_head(os.path.join(repo, "sub")), head)
        self.assertTrue(vcs.is_repo(os.path.join(repo, "sub")))
        self.assertFalse(vcs.is_repo(os.path.join(repo, ".git")), "the git dir is not a work tree")

        unborn = self._repo({})
        _write(os.path.join(unborn, "new.txt"), "n\n")
        self.assertTrue(vcs.is_repo(unborn))
        self.assertIsNone(vcs.git_head(unborn), "no commit yet, so no HEAD")
        self.assertEqual(vcs.ls_files(unborn, []), ["new.txt"])

    def test_ls_files_is_tracked_plus_untracked_not_ignored(self):
        odd = "we ird\nname-é.json"       # a newline and a non-ASCII byte: -z keeps it exact
        repo = self._repo({
            ".gitignore": "*.log\nbuild/\n",
            "selftest/a.json": "{}\n",
            "selftest/sub/b.py": "x = 1\n",
            f"selftest/{odd}": "{}\n",
            "model/m.py": "m = 1\n",
        })
        _write(os.path.join(repo, "selftest", "new.json"), "{}\n")        # untracked
        _write(os.path.join(repo, "selftest", "run.log"), "noise\n")      # ignored
        _write(os.path.join(repo, "selftest", "build", "y.bin"), "y\n")   # ignored dir

        tracked = ["selftest/a.json", f"selftest/{odd}", "selftest/sub/b.py"]
        self.assertEqual(vcs.ls_files(repo, ["selftest"]),
                         sorted(tracked + ["selftest/new.json"]))
        self.assertEqual(vcs.ls_files(repo, ["selftest"], others=False), sorted(tracked))
        self.assertEqual(vcs.ls_files(os.path.join(repo, "selftest"), []),
                         sorted(["a.json", odd, "new.json", "sub/b.py"]),
                         "paths are relative to the root asked about")
        self.assertIn(".gitignore", vcs.ls_files(repo, []))
        self.assertEqual(vcs.ls_files(repo, "selftest"), vcs.ls_files(repo, ["selftest"]),
                         "a lone path was read as its characters")
        # A pathspec is a path, never a pattern: a glob that would match two
        # files matches none, because no file is literally named that.
        self.assertEqual(vcs.ls_files(repo, ["selftest/*.json"]), [])

    def test_commit_times_are_the_newest_commit_touching_each_path(self):
        repo = self._repo({"a.txt": "1\n", "d/x.txt": "x\n"}, when="2020-01-01T00:00:00Z")
        self._commit(repo, {"d/y.txt": "y\n"}, when="2021-06-01T00:00:00Z")
        self._commit(repo, {"a.txt": "2\n"}, when="2022-03-04T05:06:07Z")
        _write(os.path.join(repo, "untracked.txt"), "u\n")

        self.assertEqual(
            vcs.commit_times(repo, ["a.txt", "d", "d/x.txt", "./d/y.txt",
                                    "untracked.txt", "missing.txt"]),
            {"a.txt": "2022-03-04T05:06:07Z", "d": "2021-06-01T00:00:00Z",
             "d/x.txt": "2020-01-01T00:00:00Z", "./d/y.txt": "2021-06-01T00:00:00Z"})
        self.assertEqual(vcs.commit_times(repo, ["."]), {".": "2022-03-04T05:06:07Z"})
        self.assertEqual(vcs.commit_times(os.path.join(repo, "d"), ["x.txt", "y.txt"]),
                         {"x.txt": "2020-01-01T00:00:00Z", "y.txt": "2021-06-01T00:00:00Z"})
        self.assertEqual(vcs.commit_times(repo, []), {})
        self.assertEqual(vcs.commit_times(repo, "a.txt"), {"a.txt": "2022-03-04T05:06:07Z"})

    def test_a_shallow_boundary_has_no_time(self):
        """CI checks out one commit, whose diff against nothing 'adds' every file.
        Dating an old entry to the clone's tip would make it look younger than it
        is; a path last touched at the boundary has no time instead."""
        src = self._repo({"a.txt": "1\n", "b.txt": "b\n"}, when="2020-01-01T00:00:00Z")
        self._commit(src, {"a.txt": "2\n"}, when="2022-02-02T00:00:00Z")
        full = vcs.commit_times(src, ["a.txt", "b.txt"])
        self.assertEqual(full, {"a.txt": "2022-02-02T00:00:00Z", "b.txt": "2020-01-01T00:00:00Z"})

        url = pathlib.Path(src).as_uri()
        depth1 = os.path.join(self.tmp(), "depth1")
        self._git(["clone", "--quiet", "--depth", "1", url, depth1], os.path.dirname(depth1))
        self.assertEqual(vcs.commit_times(depth1, ["a.txt", "b.txt"]), {},
                         "a shallow tip dated files it only appears to add")

        depth2 = os.path.join(self.tmp(), "depth2")
        self._git(["clone", "--quiet", "--depth", "2", url, depth2], os.path.dirname(depth2))
        self.assertEqual(vcs.commit_times(depth2, ["a.txt", "b.txt"]),
                         {"a.txt": "2022-02-02T00:00:00Z"},
                         "a.txt's change is a real diff; b.txt is only at the boundary")

    def test_ident_drops_the_timestamp_and_never_invents_one(self):
        repo = self._repo({"a.txt": "a\n"})
        self.assertIsNone(vcs.ident(repo), "no identity is configured anywhere, and "
                          "useConfigOnly must stop git inventing user@host")
        self._git(["config", "user.name", "Local Person"], repo)
        # A name with no email: without useConfigOnly git fills the email in from
        # the login and host name (on a machine whose host name has a dot) and
        # answers with an address nobody chose.
        self.assertIsNone(vcs.ident(repo), "an email git made up from the host name")
        self._git(["config", "user.email", "local@nopekit.invalid"], repo)
        self.assertEqual(vcs.ident(repo), "Local Person <local@nopekit.invalid>")
        # The user's own environment is their git: `git commit` would use it too.
        with mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": "Env Person",
                                          "GIT_AUTHOR_EMAIL": "env@nopekit.invalid"}):
            self.assertEqual(vcs.ident(repo), "Env Person <env@nopekit.invalid>")


class TheWritingHalf(_env.EnvCase):
    """Architecture phase 6: the writing half — `init_repo`, `add`, `commit` and
    the reads that serve them. Explicit literal paths only, `--only` so what the
    person staged stays theirs, never a push, a config write or a bypassed hook,
    and never a raise: no git, a hung git and a refusal are each an answer."""

    setUp = VcsIsTheOnlyGitEdge.setUp
    _require_git = VcsIsTheOnlyGitEdge._require_git
    _git = VcsIsTheOnlyGitEdge._git
    _repo = VcsIsTheOnlyGitEdge._repo
    _commit = VcsIsTheOnlyGitEdge._commit
    _not_a_repo = VcsIsTheOnlyGitEdge._not_a_repo
    _fake_git = VcsIsTheOnlyGitEdge._fake_git

    def _identity(self):
        os.environ.update(_env.IDENTITY)

    def test_add_takes_a_literal_path(self):
        repo = self._repo({"keep.txt": "k\n"})
        _write(os.path.join(repo, "a.json"), "{}\n")
        _write(os.path.join(repo, "*.json"), "{}\n")
        self.assertEqual(vcs.add(repo, ["*.json"]), vcs.Wrote(True, None, ""))
        self.assertEqual(vcs.staged_paths(repo), ["*.json"])

    def test_commit_only_leaves_what_the_person_staged(self):
        repo = self._repo({"x.txt": "x\n", "other.txt": "o\n"})
        self._identity()
        _write(os.path.join(repo, "other.txt"), "the person's staged edit\n")
        self._git(["add", "other.txt"], repo)
        _write(os.path.join(repo, "x.txt"), "x2\n")
        _write(os.path.join(repo, "new.txt"), "n\n")
        os.makedirs(os.path.join(repo, "gone"), exist_ok=True)
        self.assertTrue(vcs.add(repo, ["x.txt", "new.txt"]).ok)
        made = vcs.commit(repo, ["x.txt", "new.txt"], "round 1: x", "the body")
        self.assertTrue(made.ok, made.why)
        self.assertEqual(made.oid, vcs.git_head(repo))
        shown = self._git(["show", "--name-only", "--format=%s%n%b", "HEAD"], repo)
        self.assertIn("round 1: x", shown)
        self.assertIn("the body", shown)
        self.assertEqual(sorted(line for line in shown.splitlines()
                                if line.endswith(".txt")), ["new.txt", "x.txt"])
        self.assertEqual(vcs.staged_paths(repo), ["other.txt"])

    def test_the_argv_never_pushes_bypasses_or_configures(self):
        where = self.tmp()
        log = os.path.join(where, "argv.jsonl")
        bindir = self._fake_git(
            "import json, sys\n"
            f"open({log!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], "
            "'stdin': sys.stdin.read() if not sys.stdin.isatty() else ''}) + '\\n')\n"
            # `init_repo` starts one only on git's own "no repository here".
            "if 'rev-parse' in sys.argv:\n"
            "    sys.stderr.write('fatal: not a git repository (or any of the parent "
            "directories): .git\\n')\n"
            "    sys.exit(128)\n")
        with mock.patch.dict(os.environ, {"PATH": bindir}):
            self.assertTrue(vcs.add(where, ["claims/C1.json", "a b/c.json"]).ok)
            self.assertTrue(vcs.commit(where, ["claims/C1.json"], "round 1: s", "b").ok)
            vcs.init_repo(where)
        with open(log, encoding="utf-8") as fh:
            calls = [json.loads(line) for line in fh]
        writes = [c for c in calls if {"add", "commit", "init"} & set(c["argv"])]
        self.assertEqual(len(writes), 3, calls)
        for call in writes:
            argv = call["argv"]
            self.assertEqual(argv[:2], ["-C", where])
            for banned in ("--no-verify", "-n", "push", "config", "-A", "core.hooksPath"):
                self.assertNotIn(banned, argv)
            if "--all" in argv:
                self.assertIn("--pathspec-from-file=-", argv)
            if "add" in argv or "commit" in argv:
                self.assertIn("--literal-pathspecs", argv)
                self.assertIn("--pathspec-file-nul", argv)
        commit = next(c for c in writes if "commit" in c["argv"])
        self.assertEqual(commit["argv"][2:4], ["-c", "user.useConfigOnly=true"])
        self.assertIn("--only", commit["argv"])
        self.assertEqual(commit["stdin"], "claims/C1.json\0")
        add = next(c for c in writes if "add" in c["argv"])
        self.assertEqual(add["stdin"], "claims/C1.json\0a b/c.json\0")

    def test_a_hung_git_is_a_timeout(self):
        project = self.tmp()
        bindir = self._fake_git("import time\ntime.sleep(60)")
        with mock.patch.dict(os.environ, {"PATH": bindir}), \
                mock.patch.object(vcs, "VCS_WRITE_TIMEOUT_S", 0.5):
            started = time.perf_counter()
            found = vcs.commit(project, ["x"], "s", "b")
            elapsed = time.perf_counter() - started
        self.assertEqual(found, vcs.Wrote(False, None, "timeout"))
        self.assertGreaterEqual(elapsed, 0.45)
        self.assertLess(elapsed, 20)

    def test_no_git_is_an_answer(self):
        empty = self.tmp()
        with mock.patch.dict(os.environ, {"PATH": empty}):
            self.assertEqual(vcs.add(empty, ["x"]), vcs.Wrote(False, None, "no-git"))
            self.assertEqual(vcs.commit(empty, ["x"], "s", "b").why, "no-git")
            self.assertEqual(vcs.init_repo(empty).why, "no-git")
            self.assertIsNone(vcs.changed_paths(empty))
            self.assertIsNone(vcs.operation(empty))

    def test_init_repo_inside_a_repository_is_refused(self):
        repo = self._repo({"a.txt": "a\n"})
        inner = os.path.join(repo, "sub")
        os.makedirs(inner)
        self.assertEqual(vcs.init_repo(inner), vcs.Wrote(False, None, "inside a repository"))
        self.assertFalse(os.path.exists(os.path.join(inner, ".git")))
        fresh = self._not_a_repo()
        self.assertEqual(vcs.init_repo(fresh), vcs.Wrote(True, None, ""))
        self.assertTrue(vcs.is_repo(fresh))
        self.assertEqual(vcs.operation(fresh), "")

    def test_a_repository_is_started_only_on_gits_no(self):
        """Review of phase 6: `init_repo` and `init` asked `is_repo`, which reads
        every failure as "no", so git's "dubious ownership" refusal (WSL's
        /mnt/c, shared drives, container volumes) started a nested repository
        inside an existing one. `in_repository` tells "no" from "no answer"."""
        repo = self._repo({"a.txt": "a\n"})
        inner = os.path.join(repo, "sub")
        os.makedirs(inner)
        self.assertEqual(vcs.in_repository(inner), (True, ""))
        self.assertEqual(vcs.in_repository(self._not_a_repo()), (False, ""))
        with mock.patch.dict(os.environ, {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}):
            self.assertFalse(vcs.is_repo(inner), "the refusal this guards against")
            inside, why = vcs.in_repository(inner)
            self.assertIsNone(inside)
            self.assertIn("dubious ownership", why)
            made = vcs.init_repo(inner)
        self.assertFalse(made.ok)
        self.assertTrue(made.why.startswith("git did not answer: "), made.why)
        self.assertFalse(os.path.exists(os.path.join(inner, ".git")), "a nested repository")
        # A `.git` git will not read stands above: that is no "no" either.
        broken = self._not_a_repo()
        _write(os.path.join(broken, ".git"), "gitdir: /nonexistent/nopekit-test\n")
        below = os.path.join(broken, "proj")
        os.makedirs(below)
        inside, why = vcs.in_repository(below)
        self.assertIsNone(inside, why)
        self.assertFalse(vcs.init_repo(below).ok)
        self.assertFalse(os.path.exists(os.path.join(below, ".git")))
        # A hung git is no answer.
        bindir = self._fake_git("import time\ntime.sleep(60)")
        with mock.patch.dict(os.environ, {"PATH": bindir}), \
                mock.patch.object(vcs, "VCS_TIMEOUT_S", 0.5):
            self.assertIsNone(vcs.in_repository(self.tmp())[0])

    def test_the_committer_is_read_on_its_own(self):
        repo = self._repo({"a.txt": "a\n"})
        os.environ.update(_env.IDENTITY)
        self.assertEqual(vcs.committer_ident(repo), "nopekit tests <tests@nopekit.invalid>")
        os.environ.update({"GIT_COMMITTER_NAME": "Mallory",
                           "GIT_COMMITTER_EMAIL": "m@evil.example"})
        self.assertEqual(vcs.committer_ident(repo), "Mallory <m@evil.example>")
        self.assertEqual(vcs.ident(repo), "nopekit tests <tests@nopekit.invalid>")

    def test_changed_paths_are_the_subtree_relative_to_it(self):
        repo = self._repo({"top.txt": "t\n", "sub/kept.txt": "k\n", "sub/gone.txt": "g\n",
                           "sub/mod.txt": "m\n"})
        sub = os.path.join(repo, "sub")
        _write(os.path.join(repo, "top.txt"), "t2\n")
        _write(os.path.join(sub, "mod.txt"), "m2\n")
        os.remove(os.path.join(sub, "gone.txt"))
        _write(os.path.join(sub, "staged.txt"), "s\n")
        self._git(["add", "sub/staged.txt"], repo)
        _write(os.path.join(sub, "new.txt"), "n\n")
        _write(os.path.join(repo, ".gitignore"), "*.log\n")
        _write(os.path.join(sub, "noise.log"), "ignored\n")
        # An mtime alone is not a change.
        os.utime(os.path.join(sub, "kept.txt"), (1, 1))
        self.assertEqual(vcs.changed_paths(sub),
                         ["gone.txt", "mod.txt", "new.txt", "staged.txt"])
        self.assertEqual(vcs.staged_paths(sub), ["staged.txt"])
        self.assertIsNone(vcs.changed_paths(self._not_a_repo()))

    def test_operation_names_each(self):
        repo = self._repo({"a.txt": "a\n"})
        self.assertEqual(vcs.operation(repo), "")
        head = vcs.git_head(repo)
        for name, word in (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"),
                           ("REVERT_HEAD", "revert"), ("BISECT_LOG", "bisect"),
                           ("rebase-merge", "rebase"), ("rebase-apply", "rebase")):
            with self.subTest(name=name):
                where = os.path.join(repo, ".git", name)
                if name.startswith("rebase"):
                    os.makedirs(where)
                else:
                    _write(where, head + "\n")
                self.assertEqual(vcs.operation(os.path.join(repo)), word)
                if os.path.isdir(where):
                    os.rmdir(where)
                else:
                    os.remove(where)
        self._git(["checkout", "-q", "--detach"], repo)
        self.assertEqual(vcs.operation(repo), "detached")
        self.assertIsNone(vcs.operation(self._not_a_repo()))


#: The variables a pre-receive hook exports for the quarantine (git 2.34.1,
#: observed from inside a real hook): the pushed objects' directory, the main
#: store as its alternate, and the quarantine path itself. `vcs._STRIPPED`
#: removes all three; `objects=` puts back exactly the first two.
QUARANTINE_VARS = ("GIT_QUARANTINE_PATH", "GIT_OBJECT_DIRECTORY",
                   "GIT_ALTERNATE_OBJECT_DIRECTORIES")

#: The sha1 empty tree, recomputed from git's object header rather than
#: copied: `hashlib.sha1(b"tree 0\0").hexdigest()`.
EMPTY_TREE_SHA1 = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class TheQuarantineIsAnExplicitPassthrough(_env.EnvCase):
    """Eddy milestone 0: inside pre-receive the pushed objects are visible only
    through `GIT_OBJECT_DIRECTORY` and `GIT_ALTERNATE_OBJECT_DIRECTORIES`, and
    `vcs` strips both. The carve-out is an argument, never an environment read:
    `objects=` sets exactly those two on the stripped environment for that one
    call, at the values passed, and `GIT_QUARANTINE_PATH` still never reaches
    git. Without it nothing a hook exported reaches git, as before."""

    setUp = VcsIsTheOnlyGitEdge.setUp
    _git = VcsIsTheOnlyGitEdge._git
    _fake_git = VcsIsTheOnlyGitEdge._fake_git

    def test_objects_sets_exactly_two_variables(self):
        where = self.tmp()
        record = os.path.join(where, "record.jsonl")
        bindir = self._fake_git(
            "import json, os, sys\n"
            f"open({record!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], "
            "'env': dict(os.environ), "
            "'stdin': sys.stdin.read() if not sys.stdin.isatty() else ''}) + '\\n')\n"
            "print('sha1')")
        local = []
        if shutil.which("git") is not None:
            local = self._git(["rev-parse", "--local-env-vars"], where).split()
            self.assertIn("GIT_OBJECT_DIRECTORY", local, "git no longer lists the quarantine's")
        names = sorted({*QUARANTINE_VARS, *local})
        planted = {name: "planted" for name in names}
        planted["PATH"] = bindir
        # What a hook receives: the quarantine under the repository's objects/,
        # and the main store as its alternate.
        quarantine = vcs.ObjectDirs(directory=os.path.join(where, "objects", "incoming-1"),
                                    alternates=os.path.join(where, "objects"))
        with mock.patch.dict(os.environ, planted):
            self.assertEqual(vcs.object_format(where), "sha1")
            self.assertEqual(vcs.object_format(where, objects=quarantine), "sha1")
            # A read that feeds stdin takes the carve-out through the same door.
            self.assertIsNone(vcs.object_sizes(where, ["0" * 40], objects=quarantine),
                              "the stand-in's answer is not batch-check output")
        with open(record, encoding="utf-8") as fh:
            calls = [json.loads(line) for line in fh]
        self.assertEqual(len(calls), 3, calls)
        without, with_objects, with_stdin = calls
        self.assertEqual(without["argv"], ["-C", where, "rev-parse", "--show-object-format"])
        self.assertEqual(sorted(n for n in names if n in without["env"]), [],
                         "a planted quarantine variable reached git with no objects= given")
        for call in (with_objects, with_stdin):
            reached = {n: call["env"][n] for n in names if n in call["env"]}
            self.assertEqual(reached, {"GIT_OBJECT_DIRECTORY": quarantine.directory,
                                       "GIT_ALTERNATE_OBJECT_DIRECTORIES": quarantine.alternates},
                             "objects= sets exactly the two, at the values passed")
            self.assertNotIn("GIT_QUARANTINE_PATH", call["env"])
            for name, value in SPEC_SET.items():
                self.assertEqual(call["env"].get(name), value, name)
        self.assertEqual(with_stdin["argv"][2:4],
                         ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"])
        self.assertEqual(with_stdin["stdin"], "0" * 40 + "\n", "the oids go on stdin")

    def test_the_variables_are_named_only_where_stripped_or_set(self):
        """The hook script hands the quarantine to the CLI as flags; the spine
        reads no `GIT_*` variable to find it — an environment read here would be
        indistinguishable from the leak `_STRIPPED` closes. In `vcs.py` the
        three names occur as string constants in exactly two places: the strip
        list, and `_ask`'s two assignments onto the stripped environment."""
        with open(vcs.__file__, encoding="utf-8") as fh:
            module = ast.parse(fh.read())
        owners: set[str] = set()
        for node in module.body:
            lines = sorted(n.lineno for n in ast.walk(node)
                           if isinstance(n, ast.Constant) and n.value in QUARANTINE_VARS)
            if not lines:
                continue
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                owners.add(node.targets[0].id)
            else:
                owners.add(getattr(node, "name", f"line {lines[0]}"))
        self.assertEqual(owners, {"_STRIPPED", "_ask"})


class TheReceiveReads(_env.EnvCase):
    """Eddy milestone 0: the reads `nopekit validate` and the pre-receive hook
    ask. Each on a repository built through `_env.git`: `-z` and
    length-prefixed output read exactly (a file name holding a newline stays
    one name; a blob that looks like a batch header is one blob), git's own
    trailer reading, the first-parent chain and not the merge's other side,
    `None` outside a repository, on a `missing` line, on the wrong object
    type, on a hung git — and on a bad sha without a process started."""

    setUp = VcsIsTheOnlyGitEdge.setUp
    _require_git = VcsIsTheOnlyGitEdge._require_git
    _git = VcsIsTheOnlyGitEdge._git
    _repo = VcsIsTheOnlyGitEdge._repo
    _commit = VcsIsTheOnlyGitEdge._commit
    _not_a_repo = VcsIsTheOnlyGitEdge._not_a_repo
    _fake_git = VcsIsTheOnlyGitEdge._fake_git

    ODD = "we ird\nname.json"           # a newline and a space: -z keeps it one name
    MISSING = "0" * 39 + "1"

    def _claim(self, repo: str, files: dict[str, str], message: str,
               trailers: tuple[str, ...] = ()) -> str:
        for rel, text in files.items():
            _write(os.path.join(repo, rel), text)
        if files:
            self._git(["add", "--", *files], repo)
        args = ["commit", "-q", "--allow-empty", "-m", message]
        for trailer in trailers:
            args += ["--trailer", trailer]
        self._git(args, repo, identity=True,
                  env={"GIT_AUTHOR_DATE": "2021-01-01T00:00:00Z",
                       "GIT_COMMITTER_DATE": "2021-01-01T00:00:00Z"})
        return self._git(["rev-parse", "HEAD"], repo).strip()

    def _line(self) -> tuple[str, dict[str, str]]:
        """A -> B -> C on the line, D on a side branch from A, M = merge(C, D):
        M's first parent is C, and D is on no first-parent chain of M."""
        repo = self._repo({"a.txt": "a\n", "d/b.txt": "b\n", self.ODD: "{}\n"})
        a = self._git(["rev-parse", "HEAD"], repo).strip()
        branch = self._git(["symbolic-ref", "--short", "HEAD"], repo).strip()
        b = self._claim(repo, {"a.txt": "a2\n"}, "second\n\nNope-Kind: candidate in the middle\n\nprose last",
                        ())
        c = self._claim(repo, {"c.txt": "c\n"}, "third",
                        ("Nope-Schema: 1", "Nope-Kind: candidate", "Nope-Name: D.2",
                         "Signed-off-by: x <x@nopekit.invalid>"))
        self._git(["checkout", "-q", "-b", "side", a], repo)
        # side.txt holds d/b.txt's bytes: one blob at two paths.
        d = self._claim(repo, {"side.txt": "b\n"}, "side work", ("Nope-Kind: evidence",))
        self._git(["checkout", "-q", branch], repo)
        self._git(["merge", "-q", "--no-ff", "-m", "merge side", "side"], repo, identity=True)
        m = self._git(["rev-parse", "HEAD"], repo).strip()
        self._git(["update-ref", "refs/heads/cand/x", m], repo)
        return repo, {"a": a, "b": b, "c": c, "d": d, "m": m}

    def test_the_repository_and_its_refs(self):
        repo, shas = self._line()
        self.assertEqual(vcs.object_format(repo), "sha1")
        self.assertEqual(vcs.empty_tree(repo), EMPTY_TREE_SHA1)
        self.assertEqual(hashlib.sha1(b"tree 0\0").hexdigest(), EMPTY_TREE_SHA1)
        self.assertIs(vcs.commit_exists(repo, shas["m"]), True)
        self.assertIs(vcs.commit_exists(repo, self.MISSING), False)
        tree = vcs.tree_of(repo, shas["m"])
        self.assertIs(vcs.commit_exists(repo, tree), False, "a tree is not a commit")
        tips = vcs.ref_tips(repo, "refs/heads/cand/")
        self.assertEqual(tips, {"refs/heads/cand/x": shas["m"]})
        self.assertIn("refs/heads/side", vcs.ref_tips(repo, "refs/"))
        self.assertEqual(vcs.ref_tips(repo, "refs/none/"), {})
        self.assertIsNone(vcs.ref_tips(repo, "--all"), "a prefix is never an option")
        self.assertIs(vcs.ref_exists(repo, "refs/heads/cand/x"), True)
        self.assertIs(vcs.ref_exists(repo, "refs/heads/spec"), False)
        self.assertIsNone(vcs.ref_exists(repo, "-q"))
        trees = vcs.ref_trees(repo, "refs/heads/cand/x", limit=10)
        self.assertEqual(trees[0], tree)
        self.assertEqual(len(trees), 4, "M, C, B, A along the first parent; never D")
        self.assertEqual(vcs.ref_trees(repo, "refs/heads/cand/x", limit=2), trees[:2])
        self.assertEqual(vcs.ref_trees(repo, "refs/heads/spec", limit=10), [],
                         "an absent spec branch is a valid start")

    def test_the_chain_is_the_first_parent(self):
        repo, shas = self._line()
        chain = vcs.first_parents(repo, shas["m"], limit=10)
        self.assertEqual(chain, [shas["m"], shas["c"], shas["b"], shas["a"]])
        self.assertNotIn(shas["d"], chain)
        over = vcs.first_parents(repo, shas["m"], limit=2)
        self.assertEqual(len(over), 3, "limit + 1 rows, so len > limit says over the bound")
        self.assertEqual(vcs.first_parents(repo, shas["a"], limit=0), [shas["a"]])
        union = vcs.line_commits(repo, [shas["m"], shas["d"]], limit=100)
        self.assertEqual(sorted(union), sorted(shas.values()))
        self.assertEqual(vcs.line_commits(repo, [], limit=100), [])
        commits = vcs.commit_objects(repo, [shas["m"], shas["a"], shas["c"]])
        self.assertEqual(set(commits), {shas["m"], shas["a"], shas["c"]})
        self.assertEqual(commits[shas["m"]].parents, (shas["c"], shas["d"]))
        self.assertEqual(commits[shas["a"]].parents, ())
        self.assertEqual(commits[shas["m"]].tree, vcs.tree_of(repo, shas["m"]))
        self.assertEqual(commits[shas["c"]].message,
                         b"third\n\nNope-Schema: 1\nNope-Kind: candidate\nNope-Name: D.2\n"
                         b"Signed-off-by: x <x@nopekit.invalid>\n")
        self.assertIsInstance(commits[shas["c"]].message, bytes)
        self.assertEqual(vcs.commit_objects(repo, []), {})

    def test_trailers_are_gits_reading(self):
        repo, shas = self._line()
        read = vcs.trailer_lines(repo, [shas["a"], shas["b"], shas["c"], shas["d"]])
        self.assertEqual(read[shas["a"]], [])
        self.assertEqual(read[shas["b"]], [], "a Nope- line in a middle paragraph is prose to git; "
                         "the validator's raw scan refuses it, this read never invents it")
        self.assertEqual(read[shas["c"]], [("Nope-Schema", "1"), ("Nope-Kind", "candidate"),
                                           ("Nope-Name", "D.2"),
                                           ("Signed-off-by", "x <x@nopekit.invalid>")])
        self.assertEqual(read[shas["d"]], [("Nope-Kind", "evidence")])
        self.assertEqual(vcs.trailer_lines(repo, []), {})
        self.assertIsNone(vcs.trailer_lines(repo, [self.MISSING]), "a bad object is no answer")

    def test_trees_blobs_and_diffs_are_exact(self):
        repo, shas = self._line()
        tree = vcs.tree_of(repo, shas["m"])
        self.assertEqual(tree, self._git(["rev-parse", "HEAD^{tree}"], repo).strip())
        top = vcs.tree_entries(repo, tree)
        self.assertEqual([e.path for e in top], ["a.txt", "c.txt", "d", "side.txt", self.ODD])
        sub = next(e for e in top if e.path == "d")
        self.assertEqual((sub.mode, sub.type, sub.size), ("040000", "tree", None))
        listing = vcs.tree_listing(repo, tree)
        self.assertEqual([e.path for e in listing],
                         ["a.txt", "c.txt", "d/b.txt", "side.txt", self.ODD])
        self.assertTrue(all(e.type == "blob" and e.mode == "100644" for e in listing))
        self.assertEqual([e.size for e in listing], [3, 2, 2, 2, 3])

        # A tree no checkout makes: a symlink, a gitlink, and a file that became a link.
        target = self._git(["hash-object", "-w", "--stdin"], repo, input="a.txt").strip()
        self._git(["update-index", "--add", "--cacheinfo", f"120000,{target},link"], repo)
        self._git(["update-index", "--add", "--cacheinfo", f"160000,{shas['a']},sub"], repo)
        self._git(["update-index", "--add", "--cacheinfo", f"120000,{target},a.txt"], repo)
        self._git(["update-index", "--force-remove", "c.txt"], repo)
        odd_oid = next(e.oid for e in listing if e.path == self.ODD)
        _write(os.path.join(repo, self.ODD), "{}\n\n")
        self._git(["update-index", "--", self.ODD], repo)
        made = self._git(["write-tree"], repo).strip()
        by_path = {e.path: e for e in vcs.tree_entries(repo, made)}
        self.assertEqual((by_path["link"].mode, by_path["link"].type, by_path["link"].size),
                         ("120000", "blob", 5))
        self.assertEqual((by_path["sub"].mode, by_path["sub"].type, by_path["sub"].oid,
                          by_path["sub"].size), ("160000", "commit", shas["a"], None))
        self.assertEqual([e.path for e in vcs.tree_listing(repo, made)],
                         ["a.txt", "d/b.txt", "link", "side.txt", "sub", self.ODD],
                         "a recursive listing has the gitlink and no subtree row")
        changes = {c.path: c for c in vcs.tree_diff(repo, tree, made)}
        self.assertEqual({p: c.status for p, c in changes.items()},
                         {"a.txt": "T", "c.txt": "D", "link": "A", "sub": "A", self.ODD: "M"})
        self.assertEqual((changes["a.txt"].old_mode, changes["a.txt"].new_mode), ("100644", "120000"))
        self.assertEqual((changes["c.txt"].new_mode, changes["c.txt"].new_oid), ("000000", "0" * 40))
        self.assertEqual(changes[self.ODD].old_oid, odd_oid)
        self.assertEqual(vcs.tree_diff(repo, made, made), [])
        self.assertEqual({c.status for c in vcs.tree_diff(repo, EMPTY_TREE_SHA1, tree)}, {"A"})
        self.assertEqual(len(vcs.tree_diff(repo, EMPTY_TREE_SHA1, tree)), 5)

        found = vcs.blobs(repo, [e.oid for e in listing])
        self.assertEqual(found[odd_oid], b"{}\n")
        self.assertEqual(len(found), 4, "one blob serves two paths; it is read once")
        sizes = vcs.object_sizes(repo, [tree, shas["m"], odd_oid, self.MISSING])
        self.assertEqual(sizes[odd_oid], ("blob", 3))
        self.assertEqual(sizes[shas["m"]][0], "commit")
        self.assertEqual(sizes[tree][0], "tree")
        self.assertNotIn(self.MISSING, sizes, "a missing object is left out, named by the caller")
        raw = vcs.tree_objects(repo, [made])
        self.assertEqual([(m, n) for m, n, _o in raw[made]],
                         [("120000", "a.txt"), ("40000", "d"), ("120000", "link"),
                          ("100644", "side.txt"), ("160000", "sub"), ("100644", self.ODD)],
                         "modes as the tree stores them (40000, not 040000), names exact")
        self.assertEqual(dict((n, o) for _m, n, o in raw[made])["sub"], shas["a"])
        self.assertEqual(vcs.blobs(repo, []), {})
        self.assertEqual(vcs.object_sizes(repo, []), {})
        self.assertEqual(vcs.tree_objects(repo, []), {})

    def test_new_objects_are_the_pushs_own(self):
        repo, shas = self._line()
        new = vcs.new_objects(repo, shas["m"], exclude=[shas["a"]])
        oids = {oid for oid, _p in new}
        for sha in ("m", "c", "b", "d"):
            self.assertIn(shas[sha], oids, f"{sha} is reachable from M and not from A")
        self.assertNotIn(shas["a"], oids)
        a_tree = vcs.tree_of(repo, shas["a"])
        self.assertNotIn(a_tree, oids)
        by_oid = dict(new)
        self.assertEqual(by_oid[shas["m"]], "", "a commit has no path")
        self.assertEqual(by_oid[vcs.tree_of(repo, shas["m"])], "", "the root tree's path is empty")
        c_blob = next(e.oid for e in vcs.tree_listing(repo, vcs.tree_of(repo, shas["m"]))
                      if e.path == "c.txt")
        self.assertEqual(by_oid[c_blob], "c.txt")
        whole = vcs.new_objects(repo, shas["a"], exclude=[])
        self.assertIn(shas["a"], dict(whole))
        odd_oid = next(e.oid for e in vcs.tree_listing(repo, a_tree) if e.path == self.ODD)
        self.assertEqual(dict(whole)[odd_oid], "we ird",
                         "rev-list stops a name at its first newline (no -z on 2.34): "
                         "the name is a label, never a path a rule judges")
        self.assertEqual(vcs.new_objects(repo, shas["m"], exclude=[shas["m"]]), [])

    def test_batch_output_is_read_by_length(self):
        """A blob whose bytes look like a `cat-file --batch` header is one blob:
        the header's size is what is read, never the next newline."""
        repo = self._repo({"a.txt": "a\n"})
        header_like = f"\n{EMPTY_TREE_SHA1} blob 3\nabc\n"
        oid = self._git(["hash-object", "-w", "--stdin"], repo, input=header_like).strip()
        self.assertEqual(vcs.blobs(repo, [oid]), {oid: header_like.encode()})
        self.assertEqual(vcs.object_sizes(repo, [oid]), {oid: ("blob", len(header_like))})

    def test_a_missing_object_or_the_wrong_type_is_none(self):
        repo, shas = self._line()
        tree = vcs.tree_of(repo, shas["m"])
        blob = vcs.tree_listing(repo, tree)[0].oid
        self.assertIsNone(vcs.blobs(repo, [blob, self.MISSING]), "one missing, no answer")
        self.assertIsNone(vcs.blobs(repo, [tree]), "a tree is not a blob")
        self.assertIsNone(vcs.commit_objects(repo, [shas["m"], self.MISSING]))
        self.assertIsNone(vcs.commit_objects(repo, [blob]))
        self.assertIsNone(vcs.tree_objects(repo, [tree, self.MISSING]))
        self.assertIsNone(vcs.tree_objects(repo, [blob]))
        self.assertIsNone(vcs.tree_of(repo, self.MISSING))
        self.assertIsNone(vcs.tree_of(repo, blob), "a blob has no tree")
        self.assertIsNone(vcs.tree_entries(repo, self.MISSING))
        self.assertIsNone(vcs.tree_listing(repo, blob))
        self.assertIsNone(vcs.tree_diff(repo, tree, self.MISSING))
        self.assertIsNone(vcs.first_parents(repo, self.MISSING, limit=5))
        self.assertIsNone(vcs.line_commits(repo, [shas["m"], self.MISSING], limit=5))
        self.assertIsNone(vcs.new_objects(repo, self.MISSING, exclude=[]))
        self.assertIsNone(vcs.new_objects(repo, shas["m"], exclude=[self.MISSING]))

    def test_none_outside_a_repository(self):
        plain = self._not_a_repo()
        sha = "a" * 40
        calls = {
            "object_format": lambda: vcs.object_format(plain),
            "commit_exists": lambda: vcs.commit_exists(plain, sha),
            "first_parents": lambda: vcs.first_parents(plain, sha, limit=1),
            "line_commits": lambda: vcs.line_commits(plain, [sha], limit=1),
            "ref_tips": lambda: vcs.ref_tips(plain, "refs/"),
            "ref_exists": lambda: vcs.ref_exists(plain, "refs/heads/x"),
            "ref_trees": lambda: vcs.ref_trees(plain, "refs/heads/x", limit=1),
            "commit_objects": lambda: vcs.commit_objects(plain, [sha]),
            "trailer_lines": lambda: vcs.trailer_lines(plain, [sha]),
            "tree_of": lambda: vcs.tree_of(plain, sha),
            "tree_entries": lambda: vcs.tree_entries(plain, sha),
            "tree_listing": lambda: vcs.tree_listing(plain, sha),
            "tree_diff": lambda: vcs.tree_diff(plain, sha, sha),
            "blobs": lambda: vcs.blobs(plain, [sha]),
            "object_sizes": lambda: vcs.object_sizes(plain, [sha]),
            "new_objects": lambda: vcs.new_objects(plain, sha, exclude=[]),
            "tree_objects": lambda: vcs.tree_objects(plain, [sha]),
        }
        for name, call in calls.items():
            with self.subTest(call=name):
                self.assertIsNone(call())
        # `hash-object` is git's one question that needs no repository: it
        # answers sha1's empty tree anywhere. The validator asks `object_format`
        # first, which is None here, so no push is judged on it.
        self.assertEqual(vcs.empty_tree(plain), EMPTY_TREE_SHA1)

    def test_a_hung_git_is_none_and_the_timeout_keyword_is_this_calls(self):
        project = self.tmp()
        bindir = self._fake_git("import time\ntime.sleep(60)")
        sha = "a" * 40
        calls = {
            "object_format": lambda: vcs.object_format(project),
            "commit_exists": lambda: vcs.commit_exists(project, sha),
            "first_parents": lambda: vcs.first_parents(project, sha, limit=1),
            "ref_tips": lambda: vcs.ref_tips(project, "refs/"),
            "ref_trees": lambda: vcs.ref_trees(project, "refs/heads/x", limit=1),
            "blobs": lambda: vcs.blobs(project, [sha]),
            "trailer_lines": lambda: vcs.trailer_lines(project, [sha]),
            "empty_tree": lambda: vcs.empty_tree(project),
        }
        with mock.patch.dict(os.environ, {"PATH": bindir}), \
                mock.patch.object(vcs, "VCS_TIMEOUT_S", 0.3):
            for name, call in calls.items():
                with self.subTest(call=name):
                    started = time.perf_counter()
                    self.assertIsNone(call())
                    elapsed = time.perf_counter() - started
                    self.assertGreaterEqual(elapsed, 0.25, f"{name} returned in {elapsed:.2f}s")
                    self.assertLess(elapsed, 20, f"{name} waited {elapsed:.1f}s")
        # `timeout=` is for this call: the module's 10 s stays, the call waits 0.3.
        with mock.patch.dict(os.environ, {"PATH": bindir}):
            started = time.perf_counter()
            self.assertEqual(vcs._ask(project, ["rev-parse", "HEAD"], timeout=0.3),
                             (None, b"", b"timeout"))
            self.assertLess(time.perf_counter() - started, 5)

    def test_a_bad_sha_never_reaches_git(self):
        """`_HEX_OID` before argv: `HEAD`, a short id, a path, an option — each
        answers None with no process started, so a pusher's string is never a
        revision expression git evaluates."""
        repo = self.tmp()
        good = "a" * 40
        bad = ("HEAD", "a" * 39, "A" * 40, "../x", "-q", "", good + "^{commit}", "0" * 41)
        with mock.patch.object(vcs, "_ask", side_effect=AssertionError("git was started")):
            for sha in bad:
                with self.subTest(sha=sha):
                    self.assertIsNone(vcs.commit_exists(repo, sha))
                    self.assertIsNone(vcs.first_parents(repo, sha, limit=1))
                    self.assertIsNone(vcs.line_commits(repo, [good, sha], limit=1))
                    self.assertIsNone(vcs.commit_objects(repo, [sha]))
                    self.assertIsNone(vcs.trailer_lines(repo, [good, sha]))
                    self.assertIsNone(vcs.tree_of(repo, sha))
                    self.assertIsNone(vcs.tree_entries(repo, sha))
                    self.assertIsNone(vcs.tree_listing(repo, sha))
                    self.assertIsNone(vcs.tree_diff(repo, good, sha))
                    self.assertIsNone(vcs.tree_diff(repo, sha, good))
                    self.assertIsNone(vcs.blobs(repo, [sha]))
                    self.assertIsNone(vcs.object_sizes(repo, [sha]))
                    self.assertIsNone(vcs.new_objects(repo, sha, exclude=[]))
                    self.assertIsNone(vcs.new_objects(repo, good, exclude=[sha]))
                    self.assertIsNone(vcs.tree_objects(repo, [sha]))
            # A lone string is its characters, which are not shas; a limit
            # that is not a count is no count.
            self.assertIsNone(vcs.blobs(repo, good))
            self.assertIsNone(vcs.first_parents(repo, good, limit=-1))
            self.assertIsNone(vcs.line_commits(repo, [good], limit="10"))
            self.assertIsNone(vcs.ref_trees(repo, "refs/heads/x", limit=True))
            self.assertIsNone(vcs.ref_tips(repo, ""))
            self.assertIsNone(vcs.ref_exists(repo, "--verify"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
