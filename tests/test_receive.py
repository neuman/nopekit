# SPDX-License-Identifier: Apache-2.0
"""The hostile tests for `nopekit validate` and Eddy's pre-receive hook (milestone 0).

Every rule in `receive.RULES` (E01-E44) and every note in `receive.NOTES`
(N01-N04) has exactly one ``test_E##_*`` / ``test_N##_*`` method below that
pushes the thing the rule refuses and asserts the rule's id and the table's
words — `TheContractIsTheRuleTable` holds that bijection, and holds the doc's
table (`docs/EDDY_CONTRACT.md`) equal to the tuples, so code, doc and tests are
one list (CLAUDE.md's `INVARIANT_CLASSES` idiom).

Two modes. **In-process**: a work clone pushes its history to a bare server
with no hook, and `receive.validate_push(bare, [(old, new, ref)])` is asked
with ``old`` the tip the test recorded before the push — ``exclude`` makes the
object scan and the chain the same as inside a hook. **Through git**: a shim
installed as ``<bare>/hooks/pre-receive`` execs ``server/pre-receive`` under
this interpreter, and a real ``git push`` must be declined with the rule's
line prefixed ``remote:`` and ``ls-remote`` unmoved. Trees that no checkout can
make (a case twin, a gitlink, a symlink beside a record, a ``.GIT`` part, a
newline in a name) are built with ``git mktree -z`` and ``git commit-tree``;
constants are patched in process (`mock.patch.object`), never a 51 MiB fixture.
Every process goes through ``_env.run`` / ``_env.git`` / ``_env.nopekit``
(`test_meta.NoSubprocessOutsideRun`).

What these tests were written against, so the next reader knows what each
scenario is for: a validator that walked ``old..new`` from the main object
store saw no pushed object inside the hook; one that diffed each commit against
its parent let a working commit smuggle anything (`AWorkingCommitSmugglesNothing`
keeps the parent diff beside the refusal as its own "what slipped through");
one that used ``merge-base --is-ancestor`` called a merge whose first parent
was a foreign chain a fast-forward (E04's third case asserts that ancestry);
and a hook that returned 0 on its own crash accepted whatever it could not read
(`TheHookFailsClosed`). What the hook cannot see — a forged PASS at the honest
rho, a resealed round record with true digests — is pinned as accepted in
`TheStateFileIsTheTreesOwn.test_what_the_hook_cannot_see`, the milestone-0
limit, with what closes it in the docstring there.

Not an invariant class (brief §1.8): ``skipTest`` where git is absent is
allowed, as `test_vcs._require_git` does, and no end-to-end push belongs in the
fast tier — `TheContractIsTheRuleTable` alone is pure.
"""
from __future__ import annotations

import contextlib
import hashlib
import inspect
import io
import json
import os
import re
import shutil
import stat
import sys
import unicodedata
import unittest
from typing import Any, Mapping, Sequence
from unittest import mock

import _env
import _physical
import _projects
from nopekit import derivation, receive, rounds, store, vcs, verdicts

ZERO = "0" * 40

#: Where the brief keeps the hook and the contract.
SERVER_DIR = os.path.join(_env.REPO, "server")
HOOK_SCRIPT = os.path.join(SERVER_DIR, "pre-receive")
CONTRACT = os.path.join(_env.REPO, "docs", "EDDY_CONTRACT.md")

#: `spec/packs.json` with nothing pinned: the shape a bracket candidate carries.
NO_PINS = '{"schema": 1, "packs": {}}\n'

#: A pin's well-formed members, for the tests that vary one of them.
PIN_REPO = "https://packs.example.invalid/mesh.git"
PIN_COMMIT = "a" * 40


def trailers(kind: str = "candidate", *, name: str | None = "D.2", schema: str | None = "1",
             agent: str | None = "designer", model: str | None = "test-model",
             extra: Sequence[str] = ()) -> list[str]:
    """The trailer lines of a claiming commit; ``None`` leaves a key out, ``""``
    writes the key with an empty value (``Nope-Model:``)."""
    pairs = [("Nope-Schema", schema), ("Nope-Kind", kind), ("Nope-Name", name),
             ("Nope-Agent", agent), ("Nope-Model", model)]
    lines = [f"{key}: {value}" if value else f"{key}:" for key, value in pairs
             if value is not None]
    return lines + list(extra)


def evidence_trailers(**kw: Any) -> list[str]:
    kw.setdefault("name", None)
    return trailers("evidence", **kw)


def message(subject: str, lines: Sequence[str] = (), middle: str = "") -> str:
    """A commit message: the subject, an optional middle paragraph, and the
    trailer block as its own last paragraph."""
    parts = [subject]
    if middle:
        parts.append(middle)
    if lines:
        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


def _normal(text: str) -> str:
    """A table cell or a rule's message with its backticks gone and its
    whitespace collapsed, so the doc and the tuple compare on their words."""
    return re.sub(r"\s+", " ", str(text).replace("`", "")).strip()


# --------------------------------------------------------------------------- #
# the pure class (the one in the fast tier)
# --------------------------------------------------------------------------- #
class TheContractIsTheRuleTable(unittest.TestCase):
    """`receive.RULES` and `NOTES` are the rule table: the doc carries the same
    rows, every id has exactly one hostile test below, the ids are in
    evaluation order, `OUTPUT_PATTERNS` is derived from the three ignore
    blocks and nothing else, `EMPTY_TREE` is hashlib's, and ``server/`` names
    no plugin root. Pure: no git, no project, ~0.1 s."""

    @staticmethod
    def _doc_rows() -> tuple[list[tuple[str, str, str, str]], list[tuple[str, str]]]:
        """The rows of the doc's two tables — the one headed ``id | applies to
        | refuses | message`` and the one headed ``id | message`` — and of no
        other table that names a rule id (section 7 lists E40-E44 in a table
        of its own)."""
        with open(CONTRACT, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        rules: list[tuple[str, str, str, str]] = []
        notes: list[tuple[str, str]] = []
        table: str | None = None
        for line in lines:
            stripped = line.strip()
            if not stripped.startswith("|"):
                table = None
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if cells == ["id", "applies to", "refuses", "message"]:
                table = "rules"
                continue
            if cells == ["id", "message"]:
                table = "notes"
                continue
            if table is None or all(set(c) <= set("-: ") for c in cells):
                continue
            if table == "rules":
                if len(cells) != 4:
                    raise AssertionError(f"{CONTRACT}: a rule row with {len(cells)} cells: {line}")
                rules.append((cells[0], cells[1], cells[2], cells[3]))
            else:
                if len(cells) != 2:
                    raise AssertionError(f"{CONTRACT}: a note row with {len(cells)} cells: {line}")
                notes.append((cells[0], cells[1]))
        if not rules or not notes:
            raise AssertionError(f"{CONTRACT}: the rule table or the notes table is missing")
        return rules, notes

    def test_the_doc_table_is_the_rule_table(self):
        rules, notes = self._doc_rows()
        self.assertEqual([r[0] for r in rules], [r.id for r in receive.RULES],
                         "the doc's rule ids are not RULES' ids, in order")
        for row, rule in zip(rules, receive.RULES):
            with self.subTest(rule=rule.id):
                self.assertEqual(_normal(row[1]), _normal(rule.applies))
                self.assertEqual(_normal(row[2]), _normal(rule.refuses))
                self.assertEqual(_normal(row[3]), _normal(rule.message))
        self.assertEqual([n[0] for n in notes], [n.id for n in receive.NOTES])
        for row, note in zip(notes, receive.NOTES):
            with self.subTest(note=note.id):
                self.assertEqual(_normal(row[1]), _normal(note.message))

    def test_every_rule_has_exactly_one_hostile_test(self):
        ids = [r.id for r in receive.RULES] + [n.id for n in receive.NOTES]
        found: dict[str, list[str]] = {}
        module = sys.modules[__name__]
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if not issubclass(cls, unittest.TestCase) or cls.__module__ != __name__:
                continue
            for method in dir(cls):
                hit = re.match(r"^test_([EN]\d\d)_", method)
                if hit:
                    found.setdefault(hit.group(1), []).append(f"{name}.{method}")
        problems = [f"{rid}: {found.get(rid) or 'no hostile test'}" for rid in ids
                    if len(found.get(rid, ())) != 1]
        problems += [f"{rid}: tests {methods} but no such rule" for rid, methods in found.items()
                     if rid not in ids]
        self.assertEqual(problems, [], "one test_E##_/test_N##_ method per id, no more, no less")

    def test_rules_are_in_evaluation_order(self):
        self.assertEqual([r.id for r in receive.RULES], [f"E{i:02d}" for i in range(1, 45)])
        self.assertEqual([n.id for n in receive.NOTES], [f"N{i:02d}" for i in range(1, 5)])
        for rule in receive.RULES + receive.NOTES:
            with self.subTest(rule=rule.id):
                self.assertTrue(_normal(rule.message).startswith(rule.id + " "),
                                f"{rule.id}'s message does not open with its id")
                self.assertNotIn("\n", rule.message, "a message is one line")

    @staticmethod
    def _block_lines(block: str) -> list[str]:
        return [line.strip() for line in block.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
                and "nopekit:" not in line]

    @staticmethod
    def _norm_pattern(pattern: str) -> str:
        return pattern.replace("**/", "").lstrip("/")

    def test_output_patterns_are_the_ignore_blocks(self):
        from nopekit import site
        expected: list[str] = []
        for base, block in ((".nopekit/", store._NOPEKIT_IGNORE_BLOCK),
                            ("", store._ROOT_IGNORE_BLOCK),
                            ("site/", site._BUILD_IGNORE)):
            expected += [self._norm_pattern(base + line.lstrip("/"))
                         for line in self._block_lines(block)]
        self.assertTrue(expected, "the ignore blocks hold no pattern: the derivation is empty")
        patterns = [self._norm_pattern(p) for p in receive.OUTPUT_PATTERNS]
        for line in expected:
            with self.subTest(line=line):
                self.assertIn(line, patterns, f"{line!r} is in an ignore block, not in "
                                              f"OUTPUT_PATTERNS {receive.OUTPUT_PATTERNS}")
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, expected, f"{pattern!r} is in OUTPUT_PATTERNS and in "
                                                 f"no ignore block")

    def test_the_empty_tree_is_hashlibs(self):
        self.assertEqual(receive.EMPTY_TREE["sha1"], hashlib.sha1(b"tree 0\0").hexdigest())
        self.assertEqual(receive.EMPTY_TREE["sha256"], hashlib.sha256(b"tree 0\0").hexdigest())
        self.assertEqual(set(receive.EMPTY_TREE), {"sha1", "sha256"})

    def test_the_paths_are_their_homes(self):
        """Every ``.nopekit/`` path `receive` judges is spelled from the module
        that writes it, and the diff's evidence set is the scan's exempt
        subtrees (`rounds.ROUND_NAMED_BY_NOPEKIT`) — one list, not two
        spellings. `SEALED_DIRS` is `store.SEALED_KINDS` plus ``results``,
        which `store` seals on a branch of its own. `TRACE_NAME` matches the
        names `derivation` writes and nothing beside them."""
        self.assertEqual(receive.PROJECT_REL, f"{store.NOPEKIT_DIR}/{store.PROJECT_NAME}")
        self.assertEqual(receive.VERDICTS_PREFIX, f"{store.NOPEKIT_DIR}/{store.VERDICTS_NAME}/")
        self.assertEqual(receive.TRACE_PREFIX, f"{store.NOPEKIT_DIR}/{derivation.TRACE_DIR}/")
        self.assertEqual(receive.EVIDENCE_ADDS, (receive.VERDICTS_PREFIX, receive.TRACE_PREFIX))
        self.assertEqual(receive.EVIDENCE_ADDS, tuple(rounds.ROUND_NAMED_BY_NOPEKIT))
        self.assertEqual(receive.STATE_REL, f"{store.NOPEKIT_DIR}/{rounds.ROUND_NAME}")
        self.assertEqual(set(receive.NOPEKIT_KEPT),
                         {store.PROJECT_NAME, ".gitignore", rounds.ROUND_NAME,
                          f"{store.VERDICTS_NAME}/", f"{derivation.TRACE_DIR}/"})
        self.assertEqual(receive.SEALED_DIRS, ("results",) + tuple(store.SEALED_KINDS))
        self.assertIn("results", store.RECORD_DIRS)
        key, seal = "0123456789abcdef" * 4, "fedcba9876543210" * 4
        self.assertTrue(receive.TRACE_NAME.match(f"{key[:16]}.json"))
        self.assertTrue(receive.TRACE_NAME.match(f"{key[:16]}.amber.{seal[:16]}.json"))
        for stray in ("readme.json", f"{key[:15]}.json", f"{key[:16]}.amber.json",
                      f"{key[:16]}.amber.{seal[:16]}.txt", f"{key[:17]}.json"):
            with self.subTest(name=stray):
                self.assertIsNone(receive.TRACE_NAME.match(stray))
        # The depth bound sits well above the deepest file nopekit writes (an
        # amber file, 8 levels), and the scanner reads brackets outside strings.
        self.assertGreaterEqual(receive.MAX_JSON_DEPTH, 16)
        deep = b"[" * (receive.MAX_JSON_DEPTH + 1)
        self.assertEqual(receive.nesting_problem(deep),
                         f"not strict JSON (nested deeper than {receive.MAX_JSON_DEPTH} levels)")
        self.assertIsNone(receive.nesting_problem(b"[" * receive.MAX_JSON_DEPTH))
        self.assertIsNone(receive.nesting_problem(b'{"a": "' + deep + b'"}'))
        self.assertIsNone(receive.nesting_problem(b'{"a": "\\"' + deep + b'"}'))

    def test_no_plugin_root_in_server(self):
        self.assertTrue(os.path.isfile(HOOK_SCRIPT), f"{HOOK_SCRIPT} is missing")
        self.assertTrue(os.access(HOOK_SCRIPT, os.X_OK), f"{HOOK_SCRIPT} is not executable")
        with open(HOOK_SCRIPT, "rb") as fh:
            self.assertTrue(fh.read(2) == b"#!", f"{HOOK_SCRIPT} has no shebang")
        for dirpath, _dirs, files in os.walk(SERVER_DIR):
            for name in files:
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8", errors="replace") as fh:
                    self.assertNotIn("CLAUDE_PLUGIN_ROOT", fh.read(),
                                     f"{path} names a plugin root; the hook runs where "
                                     f"Eddy's image put it, under no plugin host")
        docs = [CONTRACT, os.path.join(_env.REPO, "docs", "SPINE_CONTRACT.md"),
                os.path.join(_env.REPO, "CLAUDE.md"), HOOK_SCRIPT]
        for doc in docs:
            with open(doc, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for token in set(re.findall(r"(?<![\w/.-])server/[A-Za-z0-9_./-]*[A-Za-z0-9_-]", text)):
                with self.subTest(doc=os.path.relpath(doc, _env.REPO), path=token):
                    self.assertTrue(os.path.exists(os.path.join(_env.REPO, token)),
                                    f"{doc} names {token}, which does not exist")


# --------------------------------------------------------------------------- #
# the fixtures every hostile class shares
# --------------------------------------------------------------------------- #
class _Pushes(_env.EnvCase):
    """A work clone of the bracket (a candidate on its tip), a bare server, pushes
    with and without the hook, in-process validation, trees no checkout can
    make, and the forgeries of a verdict entry."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not on PATH")   # not an invariant class (brief §1.8)

    # -- git ---------------------------------------------------------------- #
    def _git(self, args: Sequence[Any], cwd: str, **kw: Any) -> str:
        kw.setdefault("identity", True)
        proc = _env.git(args, cwd=cwd, **kw)
        self.assertEqual(proc.returncode, 0,
                         f"git {' '.join(map(str, args))} in {cwd}: {proc.stderr}")
        return proc.stdout

    def _sha(self, repo: str, rev: str = "HEAD") -> str:
        return self._git(["rev-parse", "--verify", rev + "^{commit}"], repo).strip()

    def _tree_of(self, repo: str, rev: str = "HEAD") -> str:
        return self._git(["rev-parse", "--verify", rev + "^{tree}"], repo).strip()

    def _blob(self, repo: str, data: bytes | str) -> str:
        """The oid of ``data`` written into ``repo``'s object store."""
        path = os.path.join(self.tmp(), "blob")
        with open(path, "wb") as fh:
            fh.write(data.encode("utf-8") if isinstance(data, str) else data)
        return self._git(["hash-object", "-w", "--", path], repo).strip()

    def _write(self, root: str, rel: str, data: bytes | str) -> str:
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def _read_json(self, root: str, rel: str) -> Any:
        with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
            return json.load(fh)

    def _bytes(self, root: str, rel: str) -> bytes:
        with open(os.path.join(root, *rel.split("/")), "rb") as fh:
            return fh.read()

    def _commit(self, work: str, files: Mapping[str, bytes | str | None] | None = None, *,
                subject: str = "work", lines: Sequence[str] = (), middle: str = "",
                amend: bool = False, text: str | None = None, raw: bytes | None = None,
                encoding: str | None = None) -> str:
        """Write ``files`` (``None`` removes one), stage them with ``-f`` (an
        output the project ignores must still land in the tree), and commit
        with ``text`` or `message` verbatim (``--cleanup=verbatim``: a trailer
        value with an escape byte, a key with an empty value, a duplicate key
        all reach git exactly as written) — or ``raw`` bytes, with
        ``encoding`` as ``i18n.commitEncoding`` (a Latin-1 commit with its
        ``encoding`` header). Returns the new sha."""
        for rel, data in (files or {}).items():
            if data is None:
                self._git(["rm", "-q", "-r", "-f", "--", rel], work)
            else:
                self._write(work, rel, data)
                self._git(["add", "-f", "--", rel], work)
        path = os.path.join(self.tmp(), "message")
        if raw is not None:
            with open(path, "wb") as fh:
                fh.write(raw)
        else:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text if text is not None else message(subject, lines, middle))
        args = ["commit", "-q", "--allow-empty", "--cleanup=verbatim", "-F", path]
        if encoding:
            args = ["-c", f"i18n.commitEncoding={encoding}"] + args
        if amend:
            args.append("--amend")
        self._git(args, work)
        return self._sha(work)

    def _raw_tree(self, repo: str, entries: Sequence[tuple[str, bytes, str]]) -> str:
        """A tree object from raw ``(mode, name bytes, oid)`` entries, in git's
        format, written with ``hash-object -w -t tree``: the one way to make a
        name no text pipe carries (a byte that is not UTF-8)."""
        rows = sorted((name + (b"/" if mode == "40000" else b""), mode, name, oid)
                      for mode, name, oid in entries)
        body = b"".join(mode.encode("ascii") + b" " + name + b"\0" + bytes.fromhex(oid)
                        for _key, mode, name, oid in rows)
        path = os.path.join(self.tmp(), "tree")
        with open(path, "wb") as fh:
            fh.write(body)
        return self._git(["hash-object", "-w", "-t", "tree", "--", path], repo).strip()

    def _branch(self, work: str, name: str, start: str = "HEAD") -> None:
        """A clean branch ``name`` at ``start``: every edit discarded, every
        untracked file (a `check`'s new entry) removed, ignored files kept."""
        self._git(["reset", "-q", "--hard"], work)
        self._git(["clean", "-qfd"], work)
        self._git(["checkout", "-q", "-B", name, start], work)

    def _ls_tree(self, repo: str, tree: str) -> dict[str, tuple[str, str, str]]:
        """``{name: (mode, type, oid)}`` of one tree level, read with ``-z``."""
        out = self._git(["ls-tree", "-z", tree], repo)
        found: dict[str, tuple[str, str, str]] = {}
        for record in out.split("\0"):
            if not record:
                continue
            meta, _sep, name = record.partition("\t")
            mode, kind, oid = meta.split()
            found[name] = (mode, kind, oid)
        return found

    def _tree(self, repo: str, base: str | None,
              changes: Mapping[str, tuple[str, str] | None]) -> str:
        """Tree ``base`` with ``changes`` applied — ``path -> (mode, oid)`` or
        ``None`` to drop it — written with ``git mktree -z``, which makes any
        name: a case twin, a ``.GIT`` part, a newline, a symlink (``120000``),
        a gitlink (``160000``), a blob where a directory was."""
        entries = self._ls_tree(repo, base) if base else {}
        nested: dict[str, dict[str, tuple[str, str] | None]] = {}
        for path, value in changes.items():
            first, sep, rest = path.partition("/")
            if sep:
                nested.setdefault(first, {})[rest] = value
            elif value is None:
                entries.pop(path, None)
            else:
                mode, oid = value
                kind = {"040000": "tree", "40000": "tree", "160000": "commit"}.get(mode, "blob")
                entries[path] = (mode, kind, oid)
        for name, sub in nested.items():
            held = entries.get(name)
            sub_base = held[2] if held and held[1] == "tree" else None
            entries[name] = ("040000", "tree", self._tree(repo, sub_base, sub))
        lines = [f"{mode} {kind} {oid}\t{name}" for name, (mode, kind, oid) in entries.items()]
        return self._git(["mktree", "-z"], repo, input="\0".join(lines) + "\0").strip()

    def _commit_tree(self, repo: str, tree: str, parents: Sequence[str], text: str) -> str:
        args = ["commit-tree", tree]
        for parent in parents:
            args += ["-p", parent]
        return self._git(args, repo, input=text).strip()

    def _tree_commit(self, work: str, changes: Mapping[str, tuple[str, str] | None], *,
                     lines: Sequence[str], subject: str = "a tree no checkout makes",
                     parent: str = "HEAD") -> str:
        """A commit on ``parent`` whose tree is ``parent``'s with ``changes``;
        HEAD does not move — the sha is pushed by name."""
        base = self._sha(work, parent)
        tree = self._tree(work, self._tree_of(work, base), changes)
        return self._commit_tree(work, tree, [base], message(subject, lines))

    # -- the work clone and the bare server --------------------------------- #
    def _work(self, name: str = "D.1", *, physical: bool = False) -> str:
        """The bracket as a clone holds it, with ``spec/packs.json`` committed
        and the five candidate trailers on its tip (``Nope-Name: name``).
        ``physical``: `_physical.project`'s copy, with C5's test written, for
        the sealed-results rows."""
        dest = os.path.join(self.tmp(), "work")
        if physical:
            work = _physical.project(dest, git=True)
        else:
            work = _projects.bracket_copy(dest, git=True, migrated=True)
        self._write(work, "spec/packs.json", NO_PINS)
        launch = os.path.join(work, ".vscode", "launch.json")
        if os.path.exists(launch):
            # `/.vscode/launch.json` is an output the root ignore block names,
            # so a tree holding it reads E28; the bracket tracks one on purpose
            # (`store._ROOT_IGNORE_BLOCK`'s comment) and a line never does. The
            # copy's own commit ignored it; a tracked one is untracked here.
            tracked = _env.git(["ls-files", "--error-unmatch", "--", ".vscode/launch.json"],
                               cwd=work).returncode == 0
            if tracked:
                self._git(["rm", "-q", "--cached", "--", ".vscode/launch.json"], work)
            os.remove(launch)
        self._git(["add", "-A"], work)
        self._commit(work, subject="a copy of examples/bracket", lines=trailers(name=name),
                     amend=True)
        return work

    def _bare(self, work: str) -> str:
        bare = os.path.join(self.tmp(), "server.git")
        self._git(["init", "-q", "--bare", bare], self.tmp())
        self._git(["remote", "add", "origin", bare], work)
        return bare

    def _tip(self, bare: str, ref: str) -> str:
        proc = _env.git(["rev-parse", "--verify", "--quiet", ref], cwd=bare)
        return proc.stdout.strip() if proc.returncode == 0 else ZERO

    def _push(self, work: str, *refspecs: str) -> Any:
        """``git push origin <refspecs>``; never asserts (a refused push is an answer)."""
        return _env.git(["push", "origin", *refspecs], cwd=work, identity=True)

    def _land(self, work: str, bare: str, refspec: str) -> tuple[str, str, str]:
        """Push ``refspec`` (``<rev>:<ref>``; ``:<ref>`` deletes; a leading
        ``+`` forces) with no hook installed and return ``(old, new, ref)`` as
        the hook would have been handed them."""
        ref = refspec.split(":", 1)[1]
        old = self._tip(bare, ref)
        proc = self._push(work, refspec)
        self.assertEqual(proc.returncode, 0, f"push {refspec}: {proc.stderr}")
        return old, self._tip(bare, ref), ref

    def _ls_remote(self, work: str) -> dict[str, str]:
        out = self._git(["ls-remote", "origin"], work)
        found: dict[str, str] = {}
        for line in out.splitlines():
            sha, _sep, ref = line.partition("\t")
            if ref:
                found[ref.strip()] = sha.strip()
        return found

    def _hook(self, bare: str, *, script: str = HOOK_SCRIPT, env_lines: Sequence[str] = ()) -> str:
        """Install the two-line shim Eddy's image installs, exec-ing ``script``
        under this interpreter; ``env_lines`` are shell lines run first (a
        PATH with a stand-in git, a PYTHONPATH to nowhere)."""
        shim = os.path.join(bare, "hooks", "pre-receive")
        os.makedirs(os.path.dirname(shim), exist_ok=True)
        with open(shim, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("#!/bin/sh\n")
            for line in env_lines:
                fh.write(line + "\n")
            fh.write(f'exec "{sys.executable}" "{script}" "$@"\n')
        os.chmod(shim, 0o755)
        return shim

    def _spec_branch(self, bare: str, sha: str) -> str:
        """Write ``refs/heads/spec`` as Eddy's internal path would on accepting
        ``sha``: the four-entry spec tree from `git mktree`, a commit on it,
        `update-ref`. Returns the spec tree's id."""
        lines: list[str] = []
        out = self._git(["ls-tree", "-z", sha, "--", "claims", "requirements", "milestones",
                         "spec/packs.json"], bare)
        for record in out.split("\0"):
            if not record:
                continue
            meta, _sep, name = record.partition("\t")
            mode, kind, oid = meta.split()
            lines.append(f"{mode} {kind} {oid}\t{os.path.basename(name)}")
        tree = self._git(["mktree", "-z"], bare, input="\0".join(lines) + "\0").strip()
        parents = [] if self._tip(bare, "refs/heads/spec") == ZERO else ["refs/heads/spec"]
        commit = self._commit_tree(bare, tree, parents, f"accept-gates for {sha[:12]}\n")
        self._git(["update-ref", "refs/heads/spec", commit], bare)
        return tree

    # -- in-process validation ---------------------------------------------- #
    def _report(self, repo: str, *updates: tuple[str, str, str], **kw: Any) -> Any:
        return receive.validate_push(repo, list(updates), **kw)

    @staticmethod
    def _lines(report: Any) -> list[str]:
        lines: list[str] = []
        for ref in report.refs:
            lines += [receive.format_line(r) for r in ref.refusals]
            lines += [receive.format_line(n) for n in ref.notes]
        if report.unanswered:
            lines.append(report.unanswered)
        return lines

    def _validate(self, repo: str, ref: str, old: str, new: str) -> list[str]:
        """The lines `validate_push` prints for one update; git must have answered."""
        report = self._report(repo, (old, new, ref))
        self.assertEqual(report.unanswered, "", "git did not answer: " + report.unanswered)
        return self._lines(report)

    def _line(self, work: str, bare: str, rev: str, name: str) -> list[str]:
        """Land ``rev`` as a new ``cand/<name>`` with no hook and validate it."""
        old, new, ref = self._land(work, bare, f"{rev}:refs/heads/cand/{name}")
        return self._validate(bare, ref, old, new)

    def _has(self, lines: Sequence[str], *needles: str) -> list[str]:
        hits = [line for line in lines if all(n in line for n in needles)]
        shown = "\n  ".join(lines) or "(no lines)"
        self.assertTrue(hits, f"no line holds all of {needles!r}; the lines were:\n  {shown}")
        return hits

    def _has_no(self, lines: Sequence[str], *needles: str) -> None:
        hits = [line for line in lines if all(n in line for n in needles)]
        self.assertEqual(hits, [], f"a line holds {needles!r}, which it must not")

    def _cli(self, *args: Any) -> tuple[int, str, str]:
        """``nopekit <args>`` in process: (exit code, stdout, stderr)."""
        from nopekit import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main([str(a) for a in args])
            except SystemExit as exc:       # argparse's exit on a bad argument
                code = exc.code if isinstance(exc.code, int) else 2
        return code, out.getvalue(), err.getvalue()

    # -- nopekit in the clone ----------------------------------------------- #
    def _nopekit(self, work: str, *args: str, code: int | None = None) -> Any:
        proc = _env.nopekit(list(args), cwd=work, identity=True)
        if code is not None:
            self.assertEqual(proc.returncode, code,
                             f"nopekit {' '.join(args)}: {proc.stdout[-2000:]}{proc.stderr[-2000:]}")
        return proc

    def _loosen(self, work: str, limit: float = 1.0, cid: str = "C1") -> None:
        """Edit ``cid``'s acceptance limit as a person would (the bracket's C1
        fails at 0.5 and passes at 1.0, so the edit files a new entry)."""
        rel = f"claims/{cid}.json"
        doc = self._read_json(work, rel)
        doc["acceptance"]["limit"] = limit
        self._write(work, rel, json.dumps(doc, indent=2) + "\n")

    def _untracked(self, work: str, prefix: str) -> list[str]:
        out = self._git(["ls-files", "--others", "--exclude-standard", "-z", "--", prefix], work)
        return sorted(p for p in out.split("\0") if p)

    def _new_entry(self, work: str) -> str:
        """Loosen C1's limit and run `check`: the one new verdict entry's path."""
        self._loosen(work)
        self._nopekit(work, "check")
        added = self._untracked(work, ".nopekit/verdicts/")
        self.assertEqual(len(added), 1, f"check filed {added}, not one new entry")
        return added[0]

    # -- verdict entries and their forgeries -------------------------------- #
    def _entries(self, work: str, gate: str) -> dict[str, dict]:
        directory = os.path.join(work, ".nopekit", "verdicts", gate)
        return {name: self._read_json(work, f".nopekit/verdicts/{gate}/{name}")
                for name in sorted(os.listdir(directory)) if name.endswith(".json")}

    def _fail_entry(self, work: str, gate: str = "bracket.deflection") -> tuple[str, dict]:
        """The bracket's committed FAIL entry (C1 at 0.5 mm): ``(rel path, doc)``."""
        for name, doc in self._entries(work, gate).items():
            if not name.startswith("control-") and doc["verdict"]["passed"] is False:
                return f".nopekit/verdicts/{gate}/{name}", doc
        raise AssertionError(f"{gate} has no FAIL entry in the bracket's cache")

    def _control_entry(self, work: str, gate: str = "bracket.deflection") -> tuple[str, dict]:
        for name, doc in self._entries(work, gate).items():
            if name.startswith("control-"):
                return f".nopekit/verdicts/{gate}/{name}", doc
        raise AssertionError(f"{gate} has no control entry in the bracket's cache")

    def _trace_file(self, work: str) -> str:
        names = sorted(os.listdir(os.path.join(work, ".nopekit", "trace")))
        plain = [n for n in names if ".amber." not in n and n.endswith(".json")]
        self.assertTrue(plain, f"the bracket's trace holds {names}, no trace file")
        return f".nopekit/trace/{plain[0]}"

    @staticmethod
    def _entry_bytes(doc: Mapping[str, Any], *, reseal: bool = True) -> tuple[str, bytes]:
        """``(name, bytes)`` of an entry as `verdicts.write_entry` would file it:
        the body in field order, ``digest`` over it (unless ``reseal`` is off),
        the name from its rho and outcome."""
        body = {key: doc[key] for key in verdicts._ENTRY_FIELDS if key != "digest"}
        digest = verdicts._digest_of(body) if reseal else doc["digest"]
        name = f"{body['rho'][:16]}-{verdicts.out8(body['verdict'])}.json"
        return name, verdicts._dump({**body, "digest": digest})

    def _write_entry(self, work: str, gate: str, doc: Mapping[str, Any], *,
                     name: str | None = None, reseal: bool = True) -> str:
        own, data = self._entry_bytes(doc, reseal=reseal)
        rel = f".nopekit/verdicts/{gate}/{name or own}"
        self._write(work, rel, data)
        return rel

    @staticmethod
    def _control_bytes(doc: Mapping[str, Any]) -> bytes:
        fields = verdicts._CONTROL_FIELDS if "bad_extra" in doc else verdicts._LEGACY_CONTROL_FIELDS
        body = {key: doc[key] for key in fields if key != "digest"}
        return verdicts._dump({**body, "digest": verdicts._digest_of(body)})

    @staticmethod
    def _round_bytes(doc: Mapping[str, Any], number: int | None = None) -> bytes:
        return rounds.render(rounds.sealed(doc, doc["round"] if number is None else number))

    def _forge_round(self, work: str) -> None:
        """`test_round.forge`'s record: every claim checked, ready, resealed."""
        doc = self._read_json(work, rounds.ROUND_REL)
        doc["claims"] = {cid: "checked" for cid in doc["claims"]}
        doc["requirements"] = {rid: "checked" for rid in doc["requirements"]}
        doc["readiness"] = "ready"
        doc["milestones"] = {m: "ready" for m in doc["milestones"]}
        self._write(work, rounds.ROUND_REL, self._round_bytes(doc))


# --------------------------------------------------------------------------- #
# refs
# --------------------------------------------------------------------------- #
class TheRefLayoutIsClosed(_Pushes):
    """E01-E07, and what `draft/*` takes: the five refs Eddy keeps, a line that
    only grows along its first parent, a line name that is a plain id, a
    bounded walk, and git not answering as exit 2."""

    def test_E01_a_ref_outside_the_layout(self):
        work = self._work()
        bare = self._bare(work)
        old, new, _ref = self._land(work, bare, "HEAD:refs/heads/main")
        for ref in ("refs/heads/feature/x", "refs/tags/v1", "refs/notes/commits", "refs/pull/1/head"):
            with self.subTest(ref=ref):
                lines = self._validate(bare, ref, ZERO, new)
                self._has(lines, f"E01 {ref}: not a ref Eddy keeps; push to cand/<name> (a line), "
                                 "draft/<name> (scratch) or main")

    def test_E02_spec_and_published_take_no_push(self):
        work = self._work()
        bare = self._bare(work)
        _old, new, _ref = self._land(work, bare, "HEAD:refs/heads/main")
        lines = self._validate(bare, "refs/heads/spec", ZERO, new)
        self._has(lines, "E02 spec: written only by Eddy when the gates are accepted; push a "
                         "candidate to cand/<name> and accept it there")
        lines = self._validate(bare, "refs/heads/published", ZERO, new)
        self._has(lines, "E02 published: moved only by a publish act; push a candidate to "
                         "cand/<name>")

    def test_E03_a_line_and_main_are_never_deleted(self):
        work = self._work()
        bare = self._bare(work)
        _old, tip, _ref = self._land(work, bare, "HEAD:refs/heads/cand/x")
        self._land(work, bare, "HEAD:refs/heads/main")
        lines = self._validate(bare, "refs/heads/main", tip, ZERO)
        self._has(lines, "E03 main: never deleted; it carries the README and the Pages workflow")
        lines = self._validate(bare, "refs/heads/cand/x", tip, ZERO)
        self._has(lines, "E03 cand/x: a line is never deleted; leave it and push a new line")
        # The deletion of a ref that does not exist arrives as two zero ids
        # (git 2.34.1 hands a hook that line and warns "Deleting a non-existent
        # ref"): a deletion still, so E03 on a line or main, nothing on a draft.
        lines = self._validate(bare, "refs/heads/cand/never", ZERO, ZERO)
        self._has(lines, "E03 cand/never: a line is never deleted; leave it and push a new line")
        lines = self._validate(bare, "refs/heads/main", ZERO, ZERO)
        self._has(lines, "E03 main: never deleted; it carries the README and the Pages workflow")
        self.assertEqual(self._validate(bare, "refs/heads/draft/never", ZERO, ZERO), [])
        # Through git: the hook refuses the deletion and the ref stays.
        self._hook(bare)
        before = self._ls_remote(work)
        proc = self._push(work, ":refs/heads/cand/x")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("remote: E03 cand/x: a line is never deleted", proc.stderr)
        self.assertIn("(pre-receive hook declined)", proc.stderr)
        self.assertEqual(self._ls_remote(work), before)

    def test_E04_a_line_takes_first_parent_fast_forwards_only(self):
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        second = self._commit(work, {"inputs/note.txt": "n\n"}, subject="second",
                              lines=trailers(name="D.2"))
        self._land(work, bare, "HEAD:refs/heads/cand/x")
        expect = "is not a fast-forward of"
        tail = "along the first parent; a line only grows — rebase onto cand/x or push a new cand/<name>"
        # 1. a force-push of a rewound tip
        old, new, ref = self._land(work, bare, f"+{base}:refs/heads/cand/x")
        self.assertEqual((old, new), (second, base))
        lines = self._validate(bare, ref, old, new)
        self._has(lines, f"E04 cand/x: {base[:12]} {expect} {second[:12]} {tail}")
        # 2. a rebased line: the same change, another sha
        self._branch(work, "rebased", base)
        rebased = self._commit(work, {"inputs/note.txt": "n\n"}, subject="second, reworded",
                               lines=trailers(name="D.2"))
        self.assertNotEqual(rebased, second)
        old, new, ref = self._land(work, bare, f"+{rebased}:refs/heads/cand/x")
        lines = self._validate(bare, ref, second, rebased)
        self._has(lines, f"E04 cand/x: {rebased[:12]} {expect} {second[:12]} {tail}")
        # 3. a merge whose first parent is a fresh chain and whose second parent
        #    is the old tip: `merge-base --is-ancestor old M` says yes — the
        #    recorded slip — and the first-parent chain says no.
        self._branch(work, "fresh", base)
        self._commit(work, {"inputs/other.txt": "o\n"}, subject="fresh", lines=trailers(name="D.3"))
        self._git(["merge", "-q", "--no-ff", "-m", "merge the old tip", second], work)
        merge = self._sha(work)
        self.assertEqual(_env.git(["merge-base", "--is-ancestor", second, merge], cwd=work)
                         .returncode, 0, "the slip is real: git calls the old tip an ancestor")
        self.assertEqual(self._sha(work, merge + "^2"), second)
        old, new, ref = self._land(work, bare, f"+{merge}:refs/heads/cand/x")
        lines = self._validate(bare, ref, second, merge)
        self._has(lines, f"E04 cand/x: {merge[:12]} {expect} {second[:12]} {tail}")

    def test_E05_a_line_name_is_a_plain_id(self):
        work = self._work()
        bare = self._bare(work)
        _old, tip, _ref = self._land(work, bare, "HEAD:refs/heads/cand/D")
        shape = "a line's name is [A-Za-z0-9][A-Za-z0-9._-]{0,63}; rename the ref"
        lines = self._validate(bare, "refs/heads/cand/-x", ZERO, tip)
        self._has(lines, f"E05 cand/-x: {shape}")
        long = "a" * 65
        lines = self._validate(bare, f"refs/heads/cand/{long}", ZERO, tip)
        self._has(lines, f"E05 cand/{long}: {shape}")
        lines = self._validate(bare, "refs/heads/cand/d", ZERO, tip)
        self._has(lines, "E05 cand/d: differs only in case from cand/D; pick another name")
        lines = self._validate(bare, "refs/heads/cand/D.2_ok-9", ZERO, tip)
        self._has_no(lines, "E05 ")
        # Two case twins created in ONE push: the repository's tips are the
        # state before the push, so neither saw the other and both landed
        # (review of milestone 0); the later one is refused against the earlier.
        report = self._report(bare, (ZERO, tip, "refs/heads/cand/Twin"),
                              (ZERO, tip, "refs/heads/cand/twin"))
        self.assertEqual(report.unanswered, "")
        self.assertEqual([r.refusals for r in report.refs][0], ())
        self._has(self._lines(report), "E05 cand/twin: differs only in case from cand/Twin; pick "
                                       "another name")
        self._hook(bare)
        before = self._ls_remote(work)
        proc = self._push(work, "HEAD:refs/heads/cand/Twin", "HEAD:refs/heads/cand/twin")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("remote: E05 cand/twin: differs only in case from cand/Twin; pick another "
                      "name", proc.stderr)
        self.assertIn("(pre-receive hook declined)", proc.stderr)
        self.assertEqual(self._ls_remote(work), before)

    def test_E06_the_walk_is_bounded(self):
        work = self._work()
        bare = self._bare(work)
        for i in range(3):
            self._commit(work, subject=f"working {i}")
        self._commit(work, subject="tip", lines=trailers(name="D.2"))
        _old, tip, ref = self._land(work, bare, "HEAD:refs/heads/cand/x")
        with mock.patch.object(receive, "WALK_LIMIT", 3):
            lines = self._validate(bare, ref, ZERO, tip)
        self._has(lines, "E06 cand/x: more than 3 first-parent commits to validate; start the "
                         "line from a candidate commit nearer its tip")

    def test_E07_git_not_answering_refuses(self):
        work = self._work()
        bare = self._bare(work)
        old, new, ref = self._land(work, bare, "HEAD:refs/heads/cand/x")
        with mock.patch.object(vcs, "first_parents", return_value=None):
            report = self._report(bare, (ZERO, new, ref))
            code, out, err = self._cli("validate", bare, ref, ZERO, new)
        self.assertTrue(report.unanswered.startswith("E07 push: git did not answer (first_parents:"),
                        report.unanswered)
        self.assertIn("nothing landed — push again, and if it repeats the server's keeper "
                      "reads the hook's log", report.unanswered)
        self.assertEqual(code, 2, out + err)
        self.assertIn("E07 push: git did not answer (first_parents:", out + err)
        # Without the patch the same push is answered.
        self.assertEqual(self._report(bare, (ZERO, new, ref)).unanswered, "")

    def test_draft_takes_anything_but_a_secret(self):
        work = self._work()
        bare = self._bare(work)
        self._hook(bare)
        base = self._sha(work)
        scratch = self._commit(work, {"claims": None}, subject="scratch: the claims gone")
        proc = self._push(work, f"{scratch}:refs/heads/draft/x")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._ls_remote(work).get("refs/heads/draft/x"), scratch)
        proc = self._push(work, f"+{base}:refs/heads/draft/x")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._ls_remote(work).get("refs/heads/draft/x"), base)
        proc = self._push(work, ":refs/heads/draft/x")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("refs/heads/draft/x", self._ls_remote(work))
        # Deleting a draft that never existed: git hands the hook two zero ids
        # and no quarantine, and the push is an accepted no-op — it read "both
        # ids are zero; git hands a hook no such line", exit 2, in review.
        proc = self._push(work, ":refs/heads/draft/never-existed")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("remote: E", proc.stderr)
        self.assertNotIn("declined", proc.stderr)
        secret = self._commit(work, {"generators/.env": "KEY=1\n"}, subject="wires a proposer")
        proc = self._push(work, f"{secret}:refs/heads/draft/x")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("remote: E10 push: generators/.env looks like a secret (.env); nothing "
                      "pushed is ever removed, so rename it or keep it out of the push", proc.stderr)
        self.assertIn("(pre-receive hook declined)", proc.stderr)
        self.assertNotIn("refs/heads/draft/x", self._ls_remote(work))


# --------------------------------------------------------------------------- #
# the object scan
# --------------------------------------------------------------------------- #
class TheObjectScan(_Pushes):
    """E08-E11: every new object is bounded and named before any blob is read,
    on every ref kind, working commits and second parents included — and the
    cache subtree nopekit names is exempt from the name scan."""

    def test_E08_a_blob_over_the_cap_on_every_ref(self):
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        self._land(work, bare, "HEAD:refs/heads/main")      # the bracket's objects are old
        tail = "nothing pushed is ever removed, so keep it out of the push or shrink it"
        # A working commit under a clean candidate on a line; another scan on a draft.
        self._commit(work, {"inputs/scan.bin": b"\x01" * 1025}, subject="a scan")
        tip = self._commit(work, subject="clean", lines=trailers(name="D.2"))
        self._branch(work, "draft", base)
        scratch = self._commit(work, {"inputs/scan.bin": b"\x02" * 1025}, subject="another scan")
        with mock.patch.object(receive, "MAX_BLOB_BYTES", 1024):
            for rev, ref in ((tip, "refs/heads/cand/x"), (scratch, "refs/heads/draft/y")):
                with self.subTest(ref=ref):
                    oid = self._git(["rev-parse", f"{rev}:inputs/scan.bin"], work).strip()
                    old, new, ref = self._land(work, bare, f"{rev}:{ref}")
                    lines = self._validate(bare, ref, old, new)
                    self._has(lines, f"E08 push: inputs/scan.bin ({oid[:12]}) is ", "over the",
                              "a pushed file may be; " + tail)
        lines = self._validate(bare, "refs/heads/cand/x", ZERO, tip)
        self._has_no(lines, "E08 ")

    def test_E09_a_push_total_is_capped(self):
        work = self._work()
        bare = self._bare(work)
        self._land(work, bare, "HEAD:refs/heads/main")      # the bracket's objects are old
        each = 3 * 1024 * 1024 // 2                          # 1.5 MiB: three make exactly 4.5
        files = {f"inputs/big{i}.bin": bytes([i]) * each for i in range(3)}
        self._commit(work, files, subject="three scans", lines=trailers(name="D.2"))
        old, new, ref = self._land(work, bare, "HEAD:refs/heads/cand/x")
        with mock.patch.object(receive, "MAX_PUSH_BYTES", 3 * 1024 * 1024):
            lines = self._validate(bare, ref, old, new)
        self._has(lines, "E09 push: 4.5 MiB of new files, over the 3", "MiB one push may carry; "
                                                                      "push in smaller steps")
        self._has_no(lines, "E08 ")
        self._has_no(self._validate(bare, ref, old, new), "E09 ")

    def test_E10_a_secret_name_even_when_deleted_again_and_an_old_blob_at_a_new_name(self):
        work = self._work()
        bare = self._bare(work)
        tail = "nothing pushed is ever removed, so rename it or keep it out of the push"
        self._commit(work, {".env.d/key.pem": "-----BEGIN KEY-----\n"}, subject="adds a key")
        self._commit(work, {".env.d/key.pem": None}, subject="removes it again")
        self._commit(work, subject="clean", lines=trailers(name="D.2"))
        old, new, ref = self._land(work, bare, "HEAD:refs/heads/cand/x")
        lines = self._validate(bare, ref, old, new)
        self._has(lines, "E10 push: .env.d/key.pem looks like a secret (", f"); {tail}")
        # An old blob at a new name: README's bytes placed at .aws/credentials by
        # a new tree — no new blob, so only the tree scan sees it.
        readme = self._git(["rev-parse", "HEAD:README.md"], work).strip()
        sha = self._tree_commit(work, {".aws/credentials": ("100644", readme)},
                                lines=trailers(name="D.3"))
        lines = self._line(work, bare, sha, "y")
        self._has(lines, f"E10 push: .aws/credentials looks like a secret (*credentials*); {tail}")
        # Under the cache subtrees the exemption holds only for the paths
        # nopekit writes there: a key file under .nopekit/trace/ and an AWS
        # credentials file under .nopekit/verdicts/ landed on draft/* and main
        # in review, where no tree rule runs (E29 holds the shape on a claiming
        # commit alone) and nothing pushed is ever removed.
        for i, target in enumerate(("refs/heads/draft/cache", "refs/heads/main")):
            with self.subTest(ref=target):
                # A commit of its own per ref: one already on a ref holds no new
                # object, and the scan reads new objects only.
                cache = self._commit(work, {".nopekit/trace/id_rsa": f"-----BEGIN KEY {i}-----\n",
                                            ".nopekit/verdicts/aws/credentials": f"[default {i}]\n"},
                                     subject=f"a key under the cache {i}")
                old, new, ref = self._land(work, bare, f"{cache}:{target}")
                lines = self._validate(bare, ref, old, new)
                self._has(lines, f"E10 push: .nopekit/trace/id_rsa looks like a secret (id_rsa*); "
                                 f"{tail}")
                self._has(lines, f"E10 push: .nopekit/verdicts/aws/credentials looks like a secret "
                                 f"(*credentials*); {tail}")

    def test_E11_a_missing_object_refuses(self):
        work = self._work()
        bare = self._bare(work)
        self._commit(work, {"inputs/x.txt": "x\n"}, subject="x", lines=trailers(name="D.2"))
        old, new, ref = self._land(work, bare, "HEAD:refs/heads/cand/x")
        real = vcs.object_sizes
        dropped: list[str] = []

        def dropping(repo, oids, **kw):
            found = real(repo, oids, **kw)
            if found and not dropped:
                dropped.append(sorted(found)[0])
                found.pop(dropped[0])
            return found

        with mock.patch.object(vcs, "object_sizes", dropping):
            lines = self._validate(bare, ref, old, new)
        self.assertTrue(dropped, "the scan never asked object_sizes")
        self._has(lines, f"E11 push: object {dropped[0][:12]} is in neither the push nor the "
                         "repository; push again")

    def test_the_exempt_cache_subtree_raises_no_name_refusal(self):
        work = self._work()
        bare = self._bare(work)
        rel, doc = self._fail_entry(work)
        copied = self._write_entry(work, "cad.key", doc)
        self._commit(work, {copied: self._bytes(work, copied)},
                     subject="an evaluator called cad.key", lines=trailers(name="D.2"))
        old, new, ref = self._land(work, bare, "HEAD:refs/heads/cand/x")
        lines = self._validate(bare, ref, old, new)
        self._has_no(lines, "E10 ")
        # The copy is refused for what it is — another gate's entry under this
        # directory — never for its name.
        self._has(lines, "E38 ", copied, "is not the directory's")


# --------------------------------------------------------------------------- #
# trailers
# --------------------------------------------------------------------------- #
class TrailersAreAClosedSchema(_Pushes):
    """E12-E24: the trailer schema is closed, spelled exactly, given once, read
    the same way by git and by the raw scan, carries its schema and kind, and
    a push ends on a claiming commit whose name is given once per line."""

    def test_E12_a_control_byte_is_refused(self):
        work = self._work()
        bare = self._bare(work)
        sha = self._commit(work, subject="red", lines=trailers(model="\x1b[31mred"))
        # A \x02 in a value broke `vcs.trailer_lines`'s framing before E12 was
        # asked, and the commit read E07 "git did not answer … push again"
        # (review of milestone 0): the raw bytes are judged first, and such a
        # message is never handed to git's trailer reader.
        framed = self._commit(work, subject="framed", lines=trailers(name="D.3",
                                                                      model="test-model\x02x"))
        # A Latin-1 commit (an `encoding` header, \xe9 in a value) read E16 with
        # advice that could not fix it, because git re-encodes a trailer to
        # UTF-8 and the raw-line scan compared bytes: the message is UTF-8 or
        # it is refused as not.
        raw = message("latin-1", trailers(name="D.4", model="mod\xe9le")).encode("latin-1")
        at = raw.index(b"\xe9")
        latin = self._commit(work, raw=raw, encoding="ISO-8859-1")
        self.assertIn("encoding ISO-8859-1",
                      self._git(["cat-file", "commit", latin], work).split("\n\n")[0])
        lines = self._line(work, bare, "HEAD", "x")
        self._has(lines, f"E12 {sha[:12]}: the commit message holds a control byte (0x1b); "
                         "rewrite the message")
        self._has(lines, f"E12 {framed[:12]}: the commit message holds a control byte (0x02); "
                         "rewrite the message")
        self._has(lines, f"E12 {latin[:12]}: the commit message is not UTF-8 (byte 0xe9 at offset "
                         f"{at}); commit it as UTF-8")
        self._has_no(lines, f"E16 {latin[:12]}")
        self._has_no(lines, "E07 ")
        # Nothing of a refused message is read: the candidates it would have
        # claimed are not classified, so no E13..E23 is said of them either.
        for refused in (sha, framed, latin):
            self.assertEqual([line for line in lines if refused[:12] in line
                              and not line.startswith("E12 ")], [])

    def test_E13_an_unknown_key_is_refused_with_a_suggestion(self):
        work = self._work()
        bare = self._bare(work)
        self._hook(bare)
        sha = self._commit(work, subject="typo", lines=trailers(extra=["Nope-Kinds: candidate"]))
        before = self._ls_remote(work)
        proc = self._push(work, "HEAD:refs/heads/cand/x")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"remote: E13 {sha[:12]}: Nope-Kinds is not a trailer the schema knows "
                      "(did you mean Nope-Kind?); the keys are Nope-Schema, Nope-Kind, Nope-Name, "
                      "Nope-Trigger, Nope-Agent, Nope-Model", proc.stderr)
        self.assertIn("(pre-receive hook declined)", proc.stderr)
        self.assertEqual(self._ls_remote(work), before)

    def test_E14_a_case_variant_is_refused(self):
        work = self._work()
        bare = self._bare(work)
        sha = self._commit(work, subject="case", lines=[
            "Nope-Schema: 1", "nope-kind: candidate", "Nope-Name: D.2",
            "Nope-Agent: designer", "Nope-Model: m"])
        lines = self._line(work, bare, "HEAD", "x")
        self._has(lines, f"E14 {sha[:12]}: nope-kind is spelled Nope-Kind; trailer keys are "
                         "spelled exactly")

    def test_E15_a_key_given_twice(self):
        work = self._work()
        bare = self._bare(work)
        sha = self._commit(work, subject="twice", lines=trailers(extra=["Nope-Kind: evidence"]))
        lines = self._line(work, bare, "HEAD", "x")
        self._has(lines, f"E15 {sha[:12]}: Nope-Kind is given twice; give each trailer once")

    def test_E16_a_nope_line_outside_the_block(self):
        work = self._work()
        bare = self._bare(work)
        outside = self._commit(work, subject="a stray line in the middle",
                               lines=trailers(name="D.2"), middle="Nope-Kind: candidate")
        mixed = self._commit(work, text="a mixed last paragraph\n\nNope-Kind: candidate\n"
                                        "and a sentence of prose beside it\n")
        self.assertEqual(self._git(["log", "-1", "--format=%(trailers:only)", mixed], work).strip(),
                         "", "git reads the mixed paragraph as prose: the premise of the second case")
        # The block rule is git's, not a paragraph rule of this module's: a
        # last paragraph mixing the Nope- lines with a Signed-off-by and a
        # prose line is a block to git (its quarter-of-the-lines rule beside a
        # trailer git itself generates), and then to the validator too. Held
        # against git's own reading, so a git whose heuristic differs moves
        # this case with it (the contract's limit 12: drift is loud, not silent).
        signed = self._commit(work, text=message("signed off", trailers(name="D.3") + [
            "Signed-off-by: Someone <someone@example.invalid>", "this line is prose"]))
        block = self._git(["log", "-1", "--format=%(trailers:only,unfold)", signed], work)
        lines = self._line(work, bare, "HEAD", "x")
        words = ('"Nope-Kind: candidate" sits outside the trailer block, where git reads it as '
                 "prose; put every Nope- line in the last paragraph with nothing else in it")
        self._has(lines, f"E16 {outside[:12]}: {words}")
        self._has(lines, f"E16 {mixed[:12]}: {words}")
        if "Nope-Kind: candidate" in block.splitlines():
            self.assertEqual([line for line in lines if signed[:12] in line
                              and line.startswith("E")], [],
                             "git read the block, and the validator refused it")
        else:
            self._has(lines, f"E16 {signed[:12]}: {words}")

    def test_E17_schema_missing_or_newer(self):
        work = self._work()
        bare = self._bare(work)
        missing = self._commit(work, subject="no schema", lines=trailers(schema=None))
        newer = self._commit(work, subject="schema 2", lines=trailers(name="D.3", schema="2"))
        # Not 1, in three spellings `str.isdigit` takes for a number: a
        # superscript two (`int` raised, and the hook crashed), an Arabic-Indic
        # one (`int` read 1, and the commit landed) — review of milestone 0 —
        # and a zero-padded 1.
        others = {said: self._commit(work, subject=f"schema {said}",
                                     lines=trailers(name=f"D.{i + 4}", schema=said))
                  for i, said in enumerate(("²", "١", "01"))}
        lines = self._line(work, bare, "HEAD", "x")
        self._has(lines, f"E17 {missing[:12]}: Nope-Schema is missing; a commit that claims a kind "
                         "carries Nope-Schema: 1")
        self._has(lines, f"E17 {newer[:12]}: Nope-Schema: 2 is newer than this server reads (1); "
                         "push with Nope-Schema: 1")
        for said, sha in others.items():
            with self.subTest(said=said):
                self._has(lines, f'E17 {sha[:12]}: Nope-Schema: "{said}" is not 1; a commit that '
                                 "claims a kind carries Nope-Schema: 1")

    def test_E18_a_kind_missing_or_outside_the_set(self):
        work = self._work()
        bare = self._bare(work)
        alone = self._commit(work, text="a name alone\n\nNope-Name: D.2\n")
        cased = self._commit(work, subject="Candidate", lines=trailers("Candidate", name="D.3"))
        lines = self._line(work, bare, "HEAD", "x")
        self._has(lines, f"E18 {alone[:12]}: Nope-Name without Nope-Kind; a commit that claims "
                         "nothing carries no Nope- trailer, one that claims carries Nope-Kind: "
                         "candidate or evidence")
        self._has(lines, f'E18 {cased[:12]}: Nope-Kind: "Candidate" is not one of candidate, '
                         "evidence, act")

    def test_E19_an_act_never_arrives_by_push(self):
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        tree = self._tree_of(work)
        act = self._commit(work, text=message("accept", [
            "Nope-Schema: 1", "Nope-Kind: act", "Nope-Act: accept-gates", "Nope-By: someone",
            "Nope-Role: admin", f"Nope-Candidate: {base}", f"Nope-Spec: {tree}"]))
        lines = self._line(work, bare, "HEAD", "x")
        self._has(lines, f"E19 {act[:12]}: Nope-Kind: act is never pushed; an act is recorded by "
                         "Eddy on a signed-in person's click")

    def test_E20_a_required_key_is_missing(self):
        work = self._work()
        bare = self._bare(work)
        unnamed = self._commit(work, subject="no name", lines=trailers(name=None))
        unmodelled = self._commit(work, subject="no model", lines=evidence_trailers(model=None))
        lines = self._line(work, bare, "HEAD", "x")
        self._has(lines, f"E20 {unnamed[:12]} (candidate): Nope-Name is missing; a candidate "
                         "commit carries Nope-Schema, Nope-Kind, Nope-Name, Nope-Agent, Nope-Model")
        # The table spells both kinds' sentences out; the first draft
        # substituted `a {kind}` and printed "a evidence commit".
        self._has(lines, f"E20 {unmodelled[:12]} (evidence): Nope-Model is missing; an evidence "
                         "commit carries Nope-Schema, Nope-Kind, Nope-Agent, Nope-Model")

    def test_E21_a_value_out_of_shape(self):
        work = self._work()
        bare = self._bare(work)
        cases = [
            (trailers(name="D.2", agent="autopilot"),
             '(candidate D.2): Nope-Agent: "autopilot" is not one of designer, reviewer'),
            (trailers(name="../D"),
             'Nope-Name: "../D" is not [A-Za-z0-9][A-Za-z0-9._-]{0,63}'),
            (trailers(name="D.4", model=""),
             "(candidate D.4): Nope-Model is empty or over 200 characters"),
            (trailers(name="D.5", extra=["Nope-Trigger: fem x -51c"]),
             '(candidate D.5): Nope-Trigger: "fem x -51c" is not <gate id>/<16 hex>-<8 hex>'),
            (trailers(name="D.6", extra=["Nope-By: me"]),
             "(candidate D.6): Nope-By belongs to an act; drop it"),
            (trailers(name="D.7", model="m" * 300),
             "(candidate D.7): Nope-Model is empty or over 200 characters"),
        ]
        shas = [self._commit(work, subject=f"case {i}", lines=lines) for i, (lines, _w) in
                enumerate(cases)]
        lines = self._line(work, bare, "HEAD", "x")
        for sha, (_t, words) in zip(shas, cases):
            with self.subTest(words=words):
                self._has(lines, f"E21 {sha[:12]} ", words)

    def test_E22_a_trigger_names_no_entry(self):
        work = self._work()
        bare = self._bare(work)
        rel, _doc = self._fail_entry(work)
        held = rel[len(".nopekit/verdicts/"):-len(".json")]
        named = self._commit(work, subject="names an entry the tree holds",
                             lines=trailers(name="D.2", extra=[f"Nope-Trigger: {held}"]))
        absent = "bracket.deflection/0000000000000000-00000000"
        unnamed = self._commit(work, subject="names none",
                               lines=trailers(name="D.3", extra=[f"Nope-Trigger: {absent}"]))
        lines = self._line(work, bare, "HEAD", "x")
        self._has(lines, f"E22 {unnamed[:12]} (candidate D.3): Nope-Trigger names {absent}, which "
                         "this tree does not hold under .nopekit/verdicts/; name an entry the "
                         "tree holds")
        self._has_no(lines, f"E22 {named[:12]}")

    def test_E23_a_name_is_given_once_per_line(self):
        work = self._work()
        bare = self._bare(work)
        first = self._commit(work, subject="D.2", lines=trailers(name="D.2"))
        again = self._commit(work, subject="D.2 again", lines=trailers(name="D.2"))
        lines = self._line(work, bare, "HEAD", "x")
        self._has(lines, f"E23 {again[:12]} (candidate D.2): Nope-Name D.2 is already the name of "
                         f"{first[:12]} on this line; a name is given once per line")
        self._has_no(lines, f"E23 {first[:12]}")
        self._branch(work, "evidence", first)
        other = self._commit(work, subject="evidence named otherwise",
                             lines=evidence_trailers(name="D.3"))
        lines = self._line(work, bare, "HEAD", "y")
        # `where` carries the name once known — `(evidence D.3)` — so the
        # kind and the message are held, not the parenthesis whole.
        self._has(lines, f"E23 {other[:12]} (evidence", "): Nope-Name D.3 is not its candidate's "
                                                        "(D.2); drop it or match it")

    def test_E24_the_tip_claims_a_kind(self):
        work = self._work()
        bare = self._bare(work)
        self._commit(work, subject="D.2", lines=trailers(name="D.2"))
        working = self._commit(work, subject="just work")
        lines = self._line(work, bare, "HEAD", "x")
        self._has(lines, f"E24 cand/x: the tip {working[:12]} is a working commit; a push ends on "
                         "a candidate or an evidence commit")
        self._commit(work, subject="D.3", lines=trailers(name="D.3"))
        old, new, ref = self._land(work, bare, "HEAD:refs/heads/cand/x")
        self._has_no(self._validate(bare, ref, old, new), "E24 ")
        lines = self._line(work, bare, working, "y")
        self._has(lines, f"E24 cand/y: the tip {working[:12]} is a working commit; a push ends on "
                         "a candidate or an evidence commit")
        # A tip that is not a commit at all: a tag object pushed as a line's
        # value. Git refuses the ref write itself after the hook ("trying to
        # write non-commit object", on every branch), and the hook said E07
        # "push again" of it in review — a deterministic refusal presented as
        # transient. On a line it is E24 in the hook's own words; on a draft
        # the scan runs and git's word is the last.
        self._git(["tag", "-a", "v1", "-m", "a tag"], work)
        tag = self._git(["rev-parse", "refs/tags/v1"], work).strip()
        self.assertEqual(self._git(["cat-file", "-t", tag], work).strip(), "tag")
        self._git(["push", "-q", "origin", "refs/tags/v1"], work)
        lines = self._validate(bare, "refs/heads/cand/t", ZERO, tag)
        self._has(lines, f"E24 cand/t: the tip {tag[:12]} is a tag, not a commit; a push ends on a "
                         "candidate or an evidence commit")
        self._has_no(lines, "E07 ")
        self.assertEqual(self._validate(bare, "refs/heads/draft/t", ZERO, tag), [])
        self._git(["tag", "-a", "v2", "-m", "another tag"], work)
        tag2 = self._git(["rev-parse", "refs/tags/v2"], work).strip()
        self._hook(bare)
        before = self._ls_remote(work)
        proc = self._push(work, "refs/tags/v2:refs/heads/cand/t2")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"remote: E24 cand/t2: the tip {tag2[:12]} is a tag, not a commit", proc.stderr)
        self.assertIn("(pre-receive hook declined)", proc.stderr)
        self.assertEqual(self._ls_remote(work), before)


# --------------------------------------------------------------------------- #
# the claiming tree
# --------------------------------------------------------------------------- #
class TheTreeIsPlain(_Pushes):
    """E25-E32: a tree Eddy reads holds files and directories only, one name
    per casefold and normalisation, no forbidden part, no attribute that
    makes a checkout differ, no ignored output, only entries under the cache,
    only so many files, a nopekit project, and records the strict reader
    accepts."""

    def test_E25_a_link_or_a_gitlink(self):
        work = self._work()
        bare = self._bare(work)
        link = self._blob(work, "../../etc/hostname")
        sha = self._tree_commit(work, {"claims/C1.json": ("120000", link),
                                       "generators/cadlib": ("160000", self._sha(work))},
                                lines=trailers(name="D.2"))
        lines = self._line(work, bare, sha, "x")
        where = f"E25 {sha[:12]} (candidate D.2): "
        self._has(lines, where + "claims/C1.json is a symbolic link; a tree Eddy reads holds files "
                                 "and directories only")
        self._has(lines, where + "generators/cadlib is a gitlink (a nested repository); vendor its "
                                 "files or leave them out")

    def test_E26_collisions_and_forbidden_parts(self):
        work = self._work()
        bare = self._bare(work)
        c1 = self._git(["rev-parse", "HEAD:claims/C1.json"], work).strip()
        blob = self._blob(work, "{}\n")
        nfc, nfd = unicodedata.normalize("NFC", "café"), unicodedata.normalize("NFD", "café")
        self.assertNotEqual(nfc, nfd)
        cases = [
            ({"claims/c1.json": ("100644", c1)}, ["claims/C1.json", "claims/c1.json",
                                                  "differ only in case; one file on a "
                                                  "case-insensitive checkout — rename one"]),
            ({f"inputs/{nfc}.json": ("100644", blob), f"inputs/{nfd}.json": ("100644", blob)},
             ["differ only in Unicode normalisation; rename one"]),
            ({"x/.GIT/y.json": ("100644", blob)},
             ["has a path part that is .git, ., .. or empty, or a control byte; rename it"]),
            ({"x/bad\nname.json": ("100644", blob)},
             ["has a path part that is .git, ., .. or empty, or a control byte; rename it"]),
        ]
        for i, (changes, words) in enumerate(cases):
            with self.subTest(case=i):
                sha = self._tree_commit(work, changes, lines=trailers(name=f"D.{i + 2}"))
                lines = self._line(work, bare, sha, f"line{i}")
                self._has(lines, f"E26 {sha[:12]} (candidate D.{i + 2}): ", *words)
        # A name holding a byte that is not UTF-8 — 0x9b, an 8-bit CSI — under
        # .nopekit/: `os.fsdecode` makes it U+DC9B, which `util.printable`
        # matched nowhere, and a stdout with the same error handler wrote the
        # byte BACK to the pusher's terminal (review of milestone 0). The line
        # names it as the escaped byte, in process and through the hook.
        base = self._sha(work)
        held = self._ls_tree(work, f"{base}:.nopekit")
        entries = [(mode.lstrip("0"), name.encode("utf-8"), oid)
                   for name, (mode, _kind, oid) in held.items()]
        entries.append(("100644", b"notes\x9b31m.txt", blob))
        tree = self._tree(work, self._tree_of(work, base),
                          {".nopekit": ("040000", self._raw_tree(work, entries))})
        sha = self._commit_tree(work, tree, [base], message("a C1 control in a name",
                                                            trailers(name="D.6")))
        lines = self._line(work, bare, sha, "c1")
        self._has(lines, f"E28 {sha[:12]} (candidate D.6): .nopekit/notes\\x9b31m.txt is not a path "
                         "nopekit keeps under .nopekit/")
        for line in lines:
            self.assertNotIn("\udc9b", line)
            self.assertNotIn("\x9b", line)
        # Through the hook, as a commit of its own (one already on a line's
        # chain validates nothing but the tip rule and the scan): the client
        # sees the escaped byte as text, never a replacement character.
        again = self._commit_tree(work, tree, [base], message("the same tree, again",
                                                              trailers(name="D.7")))
        self._hook(bare)
        proc = self._push(work, f"{again}:refs/heads/cand/c1hook")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"remote: E28 {again[:12]} (candidate D.7): .nopekit/notes\\x9b31m.txt is not "
                      "a path nopekit keeps", proc.stderr)
        self.assertNotIn("�", proc.stderr, "a raw byte reached the client's terminal")

    def test_E27_attributes_that_change_a_checkout(self):
        work = self._work()
        bare = self._bare(work)
        own = self._bytes(work, ".gitattributes").decode("utf-8")
        base = self._sha(work)
        self._has_no(self._line(work, bare, base, "base"), "E27 ")
        cases = [
            ({".gitattributes": own + "inputs/* export-ignore\n"},
             ".gitattributes sets export-ignore on inputs/*;"),
            ({".gitattributes": own + "*.json eol=crlf\n"}, ".gitattributes sets eol=crlf on *.json;"),
            ({"model/.gitattributes": "* filter=lfs\n"}, "model/.gitattributes sets filter"),
        ]
        for i, (files, words) in enumerate(cases):
            with self.subTest(words=words):
                self._branch(work, f"attr{i}", base)
                sha = self._commit(work, files, subject="attributes", lines=trailers(name=f"D.{i + 2}"))
                lines = self._line(work, bare, sha, f"attr{i}")
                self._has(lines, f"E27 {sha[:12]} (candidate D.{i + 2}): {words}",
                          "a checkout must be the tree — export-ignore, export-subst, filter, "
                          "ident, eol=crlf and working-tree-encoding are refused")
                if "filter" in words:
                    self._has(lines, f"E27 {sha[:12]}", " on *;")

    def test_E28_an_output_or_an_unkept_nopekit_path(self):
        work = self._work()
        bare = self._bare(work)
        outputs = [".nopekit/cache/last_outcomes.json", ".nopekit/ledger.json", "REPORT.md",
                   "site/data/build.json", "x/__pycache__/y.pyc"]
        unkept = [".nopekit/packs/mesh/pack.json", ".nopekit/notes.txt"]
        sha = self._commit(work, {rel: "{}\n" for rel in outputs + unkept},
                           subject="outputs committed", lines=trailers(name="D.2"))
        lines = self._line(work, bare, "HEAD", "x")
        where = f"E28 {sha[:12]} (candidate D.2): "
        for rel in outputs:
            with self.subTest(path=rel):
                self._has(lines, where + f"{rel} is an output nopekit ignores; committed, it would "
                                         "be read as truth on every clone — take it out of the commit")
        for rel in unkept:
            with self.subTest(path=rel):
                self._has(lines, where + f"{rel} is not a path nopekit keeps under .nopekit/ "
                                         "(project.json, .gitignore, round.json, verdicts/, trace/); "
                                         "pin a pack in spec/packs.json instead of committing it")

    def test_E29_a_path_under_verdicts_or_trace_that_is_not_an_entry(self):
        work = self._work()
        bare = self._bare(work)
        valid = "0000000000000000-00000000.json"
        files = {".nopekit/verdicts/bracket.deflection/notes.json": "{}\n",
                 f".nopekit/verdicts/a:b/{valid}": "{}\n",
                 f".nopekit/verdicts/x/y/{valid}": "{}\n",
                 ".nopekit/trace/readme.json": "{}\n"}
        sha = self._commit(work, files, subject="stray files", lines=trailers(name="D.2"))
        lines = self._line(work, bare, "HEAD", "x")
        where = f"E29 {sha[:12]} (candidate D.2): "
        entry = ("is not a verdict entry name (<gate>/<16 hex>-<8 hex>.json or "
                 "<gate>/control-<16 hex>-<8 hex>.json); take it out")
        self._has(lines, where + ".nopekit/verdicts/bracket.deflection/notes.json " + entry)
        self._has(lines, where + f".nopekit/verdicts/x/y/{valid} " + entry)
        self._has(lines, where, "a:b", "cannot name a directory in the verdict cache")
        self._has(lines, where + ".nopekit/trace/readme.json is not a trace file name (<16 hex>.json "
                                 "or <16 hex>.amber.<16 hex>.json); take it out")

    def test_E30_the_hook_reads_only_so_much(self):
        work = self._work()
        bare = self._bare(work)
        sha = self._sha(work)
        old, new, ref = self._land(work, bare, "HEAD:refs/heads/cand/x")
        with mock.patch.object(receive, "MATERIALISE_MAX_FILES", 10):
            lines = self._validate(bare, ref, old, new)
        hits = self._has(lines, f"E30 {sha[:12]} (candidate D.1): ",
                         "files under the records, .nopekit/verdicts/ and .nopekit/trace/, over "
                         "the 10 one tree may ask the hook to read; start a new line from a "
                         "slimmer candidate")
        self.assertRegex(hits[0], r"\(candidate D\.1\): 4\d files under")
        self._has_no(self._validate(bare, ref, old, new), "E30 ")

    def test_E31_not_a_nopekit_project(self):
        work = self._work()
        bare = self._bare(work)
        sha = self._commit(work, {".nopekit/project.json": None}, subject="the marker gone",
                           lines=trailers(name="D.2"))
        lines = self._line(work, bare, "HEAD", "x")
        self._has(lines, f"E31 {sha[:12]} (candidate D.2): not a nopekit project "
                         "(.nopekit/project.json is missing); run nopekit init and commit what it "
                         "writes")

    def test_E32_a_record_the_reader_refuses_and_a_stray_file(self):
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        doc = self._read_json(work, "claims/C1.json")
        doc["acceptance"]["limt"] = doc["acceptance"].pop("limit")
        typo = self._commit(work, {"claims/C1.json": json.dumps(doc, indent=2) + "\n"},
                            subject="a typo", lines=trailers(name="D.2"))
        lines = self._line(work, bare, typo, "typo")
        self._has(lines, f"E32 {typo[:12]} (candidate D.2): claims/C1.json: unknown key \"limt\"",
                  'did you mean "limit"')
        self._branch(work, "stray", base)
        stray = self._commit(work, {"claims/notes/x.txt": "notes\n"}, subject="a stray file",
                             lines=trailers(name="D.3"))
        lines = self._line(work, bare, stray, "stray")
        self._has(lines, f"E32 {stray[:12]} (candidate D.3): claims/notes/x.txt is not a record; "
                         "claims/, requirements/ and milestones/ hold <id>.json files only — move it")
        blob = self._blob(work, "x\n")
        asfile = self._tree_commit(work, {"claims": ("100644", blob)}, lines=trailers(name="D.4"),
                                   parent=base)
        lines = self._line(work, bare, asfile, "asfile")
        self._has(lines, f"E32 {asfile[:12]} (candidate D.4): claims: expected a directory of "
                         "claims records, found a file")

    def test_a_deeply_nested_json_file_is_named_not_a_crash(self):
        """A JSON file nested past `MAX_JSON_DEPTH` is refused under its own
        rule's words before any reader opens it. What slipped through (review
        of milestone 0): ``[`` × 100 000 in the spec, a claim, an entry or the
        state file raised ``RecursionError`` through every reader — the hook
        turned it into "nopekit validate crashed", and `nopekit validate`
        printed a traceback with exit 1 instead of naming the file."""
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        deep = "[" * 100_000
        depth = receive.MAX_JSON_DEPTH
        entry = f".nopekit/verdicts/bracket.deflection/{'0' * 16}-{'0' * 8}.json"
        cases = [
            ("spec/packs.json", f"E34 {{where}}: spec/packs.json: not strict JSON (nested deeper "
                                f"than {depth} levels)"),
            ("claims/C9.json", f"E32 {{where}}: claims/C9.json: not strict JSON (nested deeper than "
                               f"{depth} levels)"),
            (entry, f"E38 {{where}}: {entry}: not strict JSON (nested deeper than {depth} levels); "
                    f"file the entry nopekit check writes"),
            (rounds.ROUND_REL, f"E41 {{where}}: .nopekit/round.json reads unreadable (not strict JSON "
                               f"(nested deeper than {depth} levels)); run nopekit round and commit "
                               f"what it writes"),
        ]
        for i, (rel, expected) in enumerate(cases):
            with self.subTest(path=rel):
                self._branch(work, f"deep{i}", base)
                sha = self._commit(work, {rel: deep}, subject="nested", lines=trailers(name=f"D.{i + 2}"))
                old, new, ref = self._land(work, bare, f"{sha}:refs/heads/cand/deep{i}")
                lines = self._validate(bare, ref, old, new)
                line = expected.format(where=f"{sha[:12]} (candidate D.{i + 2})")
                self._has(lines, line)
                code, out, err = self._cli("validate", bare, ref, old, new)
                self.assertEqual(code, 1, out + err)
                self.assertIn(line, out)
                self.assertNotIn("Traceback", out + err)
                self.assertNotIn("RecursionError", out + err)


# --------------------------------------------------------------------------- #
# sealed kinds
# --------------------------------------------------------------------------- #
class SealedKindsAreAppendOnly(_Pushes):
    """E33: a results file (two entries a person recorded in a pty) shortened,
    deleted, or rewritten under its seal — the last one store's own refusal,
    which the hook passes on as E32, the boundary this test pins."""

    def test_E33_a_shortened_or_deleted_chain(self):
        work = self._work(physical=True)
        _physical.tty(work, "claim", "physical", "C5", "pass", "--detail", "no cracking",
                      "--evidence", _physical.EVIDENCE, answer="C5", code=0)
        _physical.tty(work, "claim", "physical", "C5", "fail", "--detail", "hairline cracks",
                      answer="C5", code=0)
        recorded = _physical.results(work, "C5")["results"]
        self.assertEqual([e["passed"] for e in recorded], [True, False])
        self._nopekit(work, "round")
        self._git(["add", "-A"], work)
        base = self._commit(work, subject="with two results", lines=trailers(name="D.1"), amend=True)
        bare = self._bare(work)
        self._has_no(self._line(work, bare, base, "base"), "E33 ")
        # 1. the last entry (the fail) dropped, the seal of the rest intact
        shortened = {"results": recorded[:1]}
        store.read_record  # the strict reader is what the hook runs on the copy
        self._branch(work, "short", base)
        sha = self._commit(work, {"results/C5.json": json.dumps(shortened, indent=2) + "\n"},
                           subject="drops the fail", lines=trailers(name="D.2"))
        lines = self._line(work, bare, sha, "short")
        hits = self._has(lines, f"E33 {sha[:12]} (candidate D.2): results/C5.json drops or rewrites "
                                "entry ", "a sealed record is appended, never shortened — restore it "
                                "from the previous candidate and append")
        self.assertRegex(hits[0], r"drops or rewrites entry \d+;")
        # 2. the file deleted
        self._branch(work, "gone", base)
        sha = self._commit(work, {"results/C5.json": None}, subject="deletes the results",
                           lines=trailers(name="D.3"))
        lines = self._line(work, bare, sha, "gone")
        self._has(lines, f"E33 {sha[:12]} (candidate D.3): results/C5.json is deleted; a sealed "
                         "record is never removed")
        # 3. a fail flipped under its seal: the strict reader refuses the file,
        #    and that refusal is E32 in store's words, before any diff rule.
        flipped = json.loads(json.dumps({"results": recorded}))
        flipped["results"][1]["passed"] = True
        self._branch(work, "flip", base)
        sha = self._commit(work, {"results/C5.json": json.dumps(flipped, indent=2) + "\n"},
                           subject="flips the fail", lines=trailers(name="D.4"))
        lines = self._line(work, bare, sha, "flip")
        self._has(lines, f"E32 {sha[:12]} (candidate D.4): results/C5.json")
        self._has_no(lines, f"E33 {sha[:12]}")


# --------------------------------------------------------------------------- #
# the spec tree
# --------------------------------------------------------------------------- #
class TheSpecTreeDecidesAcceptance(_Pushes):
    """E34, E35, N01 and the tree-id arithmetic: `spec/` holds `packs.json`
    alone in its exact shape, the pins and the live list agree, a candidate
    whose spec tree is on no commit of `spec` lands and waits, and
    `receive.tree_id` is `git mktree`'s."""

    def _pins(self, packs: Mapping[str, Any], *, schema: Any = 1) -> str:
        doc: dict[str, Any] = {}
        if schema is not None:
            doc["schema"] = schema
        doc["packs"] = packs
        return json.dumps(doc, indent=2) + "\n"

    def test_E34_the_spec_is_well_shaped(self):
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        cases: list[tuple[dict, list[str]]] = [
            ({"spec/packs.json": None},
             ["spec/packs.json is missing; the spec pins its packs — commit "
              '{"schema": 1, "packs": {}} when there are none']),
            ({"spec/claims/C9.json": "{}\n"},
             ["spec/claims/C9.json is not spec/packs.json; claims/, requirements/ and "
              "milestones/ live at the root and spec/ holds packs.json alone"]),
            ({"spec/packs.json": self._pins({"mesh": {"name": "mesh"}}, schema=None)},
             ['spec/packs.json: "schema" is missing; the file is {"schema": 1, "packs": {}} with '
              'a pin per pack',
              'spec/packs.json: packs.mesh: unknown key "name"',
              "spec/packs.json: packs.mesh.repo is missing",
              "spec/packs.json: packs.mesh.commit is missing"]),
            ({"spec/packs.json": self._pins({"mesh": {"repo": "file:///tmp/x", "commit": PIN_COMMIT}})},
             ["spec/packs.json: packs.mesh.repo must be an https:// URL with a host name"]),
            ({"spec/packs.json": self._pins({"mesh": {"repo": "ssh://git@host/x", "commit": PIN_COMMIT}})},
             ["spec/packs.json: packs.mesh.repo must be an https:// URL with a host name"]),
            ({"spec/packs.json": self._pins({"mesh": {"repo": PIN_REPO, "commit": "abcdef0"}})},
             ["spec/packs.json: packs.mesh.commit must be 40 or 64 hex characters"]),
        ]
        for i, (files, words) in enumerate(cases):
            with self.subTest(case=i):
                self._branch(work, f"spec{i}", base)
                sha = self._commit(work, files, subject="the spec", lines=trailers(name=f"D.{i + 2}"))
                lines = self._line(work, bare, sha, f"spec{i}")
                # One line per reader's sentence, each held whole: the reader's
                # words are the rule's third form, not "a line that says E34".
                for sentence in words:
                    self._has(lines, f"E34 {sha[:12]} (candidate D.{i + 2}): {sentence}")

    def test_E35_pins_and_the_live_list_agree(self):
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        pinned = self._pins({"aero-openfoam": {"repo": PIN_REPO, "commit": PIN_COMMIT}})
        sha = self._commit(work, {"spec/packs.json": pinned}, subject="pins a pack",
                           lines=trailers(name="D.2"))
        lines = self._line(work, bare, sha, "pinned")
        self._has(lines, f"E35 {sha[:12]} (candidate D.2): spec/packs.json pins aero-openfoam, "
                         "which .nopekit/project.json's packs does not list; add it with nopekit "
                         "packs add or drop the pin")
        self._has_no(lines, "E34 ")
        self._branch(work, "listed", base)
        project = self._read_json(work, ".nopekit/project.json")
        project["packs"] = ["my-pack"]
        sha = self._commit(work, {".nopekit/project.json": json.dumps(project, indent=2) + "\n"},
                           subject="lists a pack", lines=trailers(name="D.3"))
        lines = self._line(work, bare, sha, "listed")
        self._has(lines, f"E35 {sha[:12]} (candidate D.3): .nopekit/project.json lists my-pack, "
                         "which is neither bundled nor pinned in spec/packs.json; pin it")

    def test_N01_a_candidate_whose_gates_are_not_accepted_lands_and_waits(self):
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        old, new, ref = self._land(work, bare, "HEAD:refs/heads/cand/x")
        accepted = self._spec_branch(bare, base)
        report = self._report(bare, (old, new, ref))
        self.assertEqual(report.unanswered, "")
        self.assertEqual([r.refusals for r in report.refs], [()])
        self.assertIs(report.refs[0].accepted_spec, True)
        self.assertEqual(report.refs[0].spec_tree, accepted)
        self._has_no(self._lines(report), "N01 ")
        # The limit loosened and `round` rerun: a conforming candidate whose
        # spec tree is on no commit of spec.
        self._loosen(work)
        self._nopekit(work, "round")
        self._git(["add", "-A"], work)
        sha = self._commit(work, subject="loosens C1", lines=trailers(name="D.2"))
        old, new, ref = self._land(work, bare, "HEAD:refs/heads/cand/x")
        report = self._report(bare, (old, new, ref))
        self.assertEqual(report.unanswered, "")
        self.assertEqual([r.refusals for r in report.refs], [()])
        self.assertIs(report.refs[0].accepted_spec, False)
        self.assertNotEqual(report.refs[0].spec_tree, accepted)
        self._has(self._lines(report), f"N01 {sha[:12]} (candidate D.2): awaits acceptance; its "
                                       f"gates (spec tree {report.refs[0].spec_tree[:12]}) are on no "
                                       "commit of spec")
        code, out, err = self._cli("validate", bare, ref, old, new)
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"N01 {sha[:12]} (candidate D.2): awaits acceptance", out)
        code, out, err = self._cli("validate", bare, ref, old, new, "--json")
        self.assertEqual(code, 0, out + err)
        doc = json.loads(out)
        self.assertIs(doc["ok"], True)
        self.assertIs(doc["refs"][0]["accepted_spec"], False)
        self.assertEqual([n["rule"] for n in doc["refs"][0]["notes"]], ["N01"])

    def test_the_tree_id_is_gits(self):
        work = self._work()
        sha = self._sha(work)
        out = self._git(["ls-tree", "-z", sha, "--", "claims", "requirements", "milestones",
                         "spec/packs.json"], work)
        entries: list[tuple[str, str, str]] = []
        mktree_lines: list[str] = []
        for record in out.split("\0"):
            if not record:
                continue
            meta, _sep, name = record.partition("\t")
            mode, kind, oid = meta.split()
            name = os.path.basename(name)
            entries.append((mode.lstrip("0") if kind == "tree" else mode, name, oid))
            mktree_lines.append(f"{mode} {kind} {oid}\t{name}")
        self.assertEqual(sorted(n for _m, n, _o in entries),
                         ["claims", "milestones", "packs.json", "requirements"])
        from_git = self._git(["mktree", "-z"], work, input="\0".join(mktree_lines) + "\0").strip()
        self.assertEqual(receive.tree_id(entries, "sha1"), from_git)
        self.assertEqual(receive.tree_id(reversed(entries), "sha1"), from_git,
                         "the entry order is git's, whatever order they are given in")
        derived, problems = receive.spec_tree_id(work, sha)
        self.assertEqual((derived, problems), (from_git, []))
        # The same four entries moved under a real spec/ directory.
        self._branch(work, "moved", sha)
        for name in ("claims", "requirements", "milestones"):
            self._git(["mv", name, f"spec/{name}"], work)
        self._git(["commit", "-q", "-m", "the records under spec/"], work)
        self.assertEqual(self._git(["rev-parse", "HEAD:spec"], work).strip(), from_git)
        self.assertEqual(receive.EMPTY_TREE["sha1"], vcs.empty_tree(work))
        self.assertEqual(receive.tree_id([], "sha1"), receive.EMPTY_TREE["sha1"])


# --------------------------------------------------------------------------- #
# the diff against the previous claiming commit
# --------------------------------------------------------------------------- #
class ACandidateMayChangeAnythingButTheCache(_Pushes):
    """E37-E39, N02-N04: a candidate may change anything but may not rewrite
    or remove a verdict entry or a trace file, and may not add an entry its
    reader refuses or whose rho does not follow; what the tree can see of an
    entry's reads is a note, never a refusal."""

    def test_E37_an_entry_is_never_rewritten_or_removed(self):
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        rel, doc = self._fail_entry(work)
        trace = self._trace_file(work)
        # 1. one byte of a committed entry rewritten, its digest recomputed
        doc = json.loads(json.dumps(doc))
        doc["verdict"]["detail"] = doc["verdict"]["detail"] + " "
        name, data = self._entry_bytes(doc)
        self.assertEqual(rel.rsplit("/", 1)[1], name, "the detail is not in the outcome: same name")
        sha = self._commit(work, {rel: data}, subject="rewrites an entry", lines=trailers(name="D.2"))
        lines = self._line(work, bare, sha, "rewrite")
        self._has(lines, f"E37 {sha[:12]} (candidate D.2): {rel} is modified; a verdict entry is "
                         "written once — file a new entry at its new rho")
        # 2. a candidate deleting the FAIL entry and re-rounding at a looser limit
        self._branch(work, "withdraw", base)
        os.remove(os.path.join(work, *rel.split("/")))
        self._loosen(work)
        self._nopekit(work, "round")
        self._git(["add", "-A"], work)
        sha = self._commit(work, subject="withdraws the fail", lines=trailers(name="D.3"))
        self.assertFalse(os.path.exists(os.path.join(work, *rel.split("/"))))
        lines = self._line(work, bare, sha, "withdraw")
        self._has(lines, f"E37 {sha[:12]} (candidate D.3): {rel} is deleted; evidence is added, "
                         "never withdrawn")
        # 3. an evidence commit deleting it
        self._branch(work, "evidence", base)
        sha = self._commit(work, {rel: None}, subject="evidence withdraws", lines=evidence_trailers())
        lines = self._line(work, bare, sha, "evidence")
        self._has(lines, f"E37 {sha[:12]} (evidence): {rel} is deleted; evidence is added, never "
                         "withdrawn")
        # 4. a trace file modified
        self._branch(work, "trace", base)
        with open(os.path.join(work, *trace.split("/")), "rb") as fh:
            raw = fh.read()
        sha = self._commit(work, {trace: raw + b"\n"}, subject="touches the trace",
                           lines=trailers(name="D.4"))
        lines = self._line(work, bare, sha, "trace")
        self._has(lines, f"E37 {sha[:12]} (candidate D.4): {trace} is modified; a verdict entry is "
                         "written once — file a new entry at its new rho")

    def test_E38_a_malformed_entry_is_refused_in_the_loaders_words(self):
        work = self._work()
        bare = self._bare(work)
        rel, doc = self._fail_entry(work)
        gate = "bracket.deflection"
        files: dict[str, bytes | str] = {}
        # 1. a hand-written PASS whose digest does not seal its body
        passing = json.loads(json.dumps(doc))
        passing["verdict"].update({"passed": True, "measured": 0.4})
        unsealed_name, unsealed = self._entry_bytes(passing, reseal=False)
        files[f".nopekit/verdicts/{gate}/{unsealed_name}"] = unsealed
        # 2. keys out of order, digest correct for the body
        body = {key: doc[key] for key in verdicts._ENTRY_FIELDS if key != "digest"}
        shuffled = {"digest": verdicts._digest_of(body), **body}
        other_rho = hashlib.sha256(b"shuffled").hexdigest()
        shuffled["rho"] = other_rho
        files[f".nopekit/verdicts/{gate}/{other_rho[:16]}-{verdicts.out8(doc['verdict'])}.json"] = \
            verdicts._dump(shuffled)
        # 3. a control whose admitted says paired without a walk — at the name
        #    its outcome digest gives it (the walk's tally is in out8), so it
        #    is an added file beside the real one, never a rewrite (E37)
        crel, control = self._control_entry(work)
        self.assertEqual(control["admitted"], "paired")
        self.assertIsNotNone(control["mutation"])
        control = json.loads(json.dumps(control))
        control["mutation"] = None
        fields = (verdicts._CONTROL_FIELDS if "bad_extra" in control
                  else verdicts._LEGACY_CONTROL_FIELDS)
        unwalked = verdicts.ControlEntry(**{key: control[key] for key in fields
                                            if key not in ("schema", "kind")})
        crel = f".nopekit/verdicts/{gate}/{unwalked.name}.json"
        self.assertFalse(os.path.exists(os.path.join(work, *crel.split("/"))))
        files[crel] = self._control_bytes(control)
        # 4. an entry copied from another gate's directory
        copied = f".nopekit/verdicts/bracket.bearing/{rel.rsplit('/', 1)[1]}"
        files[copied] = self._entry_bytes(doc)[1]
        # 5. a trace file that is not a JSON object
        trace = ".nopekit/trace/0123456789abcdef.json"
        files[trace] = "[]\n"
        sha = self._commit(work, files, subject="five forgeries", lines=trailers(name="D.2"))
        lines = self._line(work, bare, sha, "x")
        where = f"E38 {sha[:12]} (candidate D.2): "
        tail = "; file the entry nopekit check writes"
        self._has(lines, where + f".nopekit/verdicts/{gate}/{unsealed_name}: hand-edited entry: its "
                                 "digest does not match its content" + tail)
        self._has(lines, where + f".nopekit/verdicts/{gate}/{other_rho[:16]}-", ": keys are [", tail)
        self._has(lines, where + crel + ": mutation is null where the walk applies and both "
                                        "controls held: an entry missing its walk is not a "
                                        "qualification" + tail)
        self._has(lines, where + copied + ": gate 'bracket.deflection' is not the directory's "
                                          "'bracket.bearing'" + tail)
        self._has(lines, where + trace + ": ", "expected a JSON object, found list", tail)

    def test_E39_rho_must_follow_from_what_the_entry_says_it_read(self):
        work = self._work()
        bare = self._bare(work)
        _rel, doc = self._fail_entry(work)
        forged = json.loads(json.dumps(doc))
        forged["rho"] = hashlib.sha256(b"a fresh address").hexdigest()
        added = self._write_entry(work, "bracket.deflection", forged)
        loaded, why = verdicts._load_entry(os.path.join(work, *added.split("/")), "bracket.deflection")
        self.assertEqual(why, "", "nopekit's own reader accepts the entry: only rho is wrong")
        self._git(["add", "-A"], work)
        sha = self._commit(work, subject="a fresh rho", lines=trailers(name="D.2"))
        lines = self._line(work, bare, sha, "x")
        self._has(lines, f"E39 {sha[:12]} (candidate D.2): {added}: rho does not follow from its "
                         "own gate, spine, code and reads; file the entry nopekit check writes")

    def _accepted(self, work: str, bare: str, sha: str, name: str) -> list[str]:
        """Validate ``sha`` as ``cand/<name>``, in process and through the CLI
        (exit 0, ``--json`` ``ok: true``); the lines printed."""
        old, new, ref = self._land(work, bare, f"{sha}:refs/heads/cand/{name}")
        report = self._report(bare, (old, new, ref))
        self.assertEqual(report.unanswered, "")
        self.assertEqual([r.refusals for r in report.refs], [()], self._lines(report))
        code, out, err = self._cli("validate", bare, ref, old, new, "--json")
        self.assertEqual(code, 0, out + err)
        self.assertIs(json.loads(out)["ok"], True)
        code, out, err = self._cli("validate", bare, ref, old, new)
        self.assertEqual(code, 0, out + err)
        lines = self._lines(report)
        for line in lines:
            self.assertIn(line, out, "the CLI prints every note")
        return lines

    def test_N02_an_entry_under_another_spine_is_a_note(self):
        work = self._work()
        bare = self._bare(work)
        _rel, doc = self._fail_entry(work)
        foreign = json.loads(json.dumps(doc))
        foreign["spine"] = "0" * 64
        foreign["rho"] = verdicts.rho(foreign["gate"], foreign["spine"], foreign["code"]["digest"],
                                      foreign["reads"])
        self._write_entry(work, "bracket.deflection", foreign)
        self._nopekit(work, "round")
        self._git(["add", "-A"], work)
        sha = self._commit(work, subject="another spine's entry", lines=trailers(name="D.1"), amend=True)
        lines = self._accepted(work, bare, sha, "x")
        self._has(lines, f"N02 {sha[:12]} (candidate D.1): ", " added entries were written under "
                                                              "another spine (000000000000); they read "
                                                              "Stale under this nopekit")

    def test_N03_a_tree_visible_disagreement_is_a_note(self):
        work = self._work()
        bare = self._bare(work)
        rel, doc = self._fail_entry(work)
        was = doc["reads"]["ledger"]["acceptance:C1"]
        self._loosen(work)
        self._nopekit(work, "round")
        self._git(["add", "-A"], work)
        self.assertTrue(os.path.exists(os.path.join(work, *rel.split("/"))),
                        "the older entry stays beside the new one")
        sha = self._commit(work, subject="both entries", lines=trailers(name="D.1"), amend=True)
        lines = self._accepted(work, bare, sha, "x")
        hits = self._has(lines, f"N03 {sha[:12]} (candidate D.1): {rel} read acceptance:C1 at "
                                f"{was[:12]}; this tree holds ", ", so it arrives Stale")
        self.assertNotIn(f"this tree holds {was[:12]}", hits[0])

    def test_N04_two_outcomes_at_one_rho_is_a_note(self):
        work = self._work()
        bare = self._bare(work)
        _rel, doc = self._fail_entry(work)
        twin = json.loads(json.dumps(doc))
        twin["verdict"].update({"passed": True, "measured": 0.4})
        added = self._write_entry(work, "bracket.deflection", twin)
        self.assertEqual(added.rsplit("/", 1)[1][:16], doc["rho"][:16])
        self._nopekit(work, "round")
        self._git(["add", "-A"], work)
        sha = self._commit(work, subject="two outcomes", lines=trailers(name="D.1"), amend=True)
        lines = self._accepted(work, bare, sha, "x")
        self._has(lines, f"N04 {sha[:12]} (candidate D.1): .nopekit/verdicts/bracket.deflection/ "
                         f"holds two outcomes at rho {doc['rho'][:12]}; the resolver reads that as an "
                         "error and the page shows the contradiction, not either outcome")


class AnEvidenceCommitAddsOnlyEvidence(_Pushes):
    """E36: an evidence commit adds under the cache and the trace and rewrites
    the state file, nothing else — judged against the previous claiming
    commit's tree."""

    def test_E36_an_evidence_commit_touches_only_evidence(self):
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        tail = ("; an evidence commit adds under .nopekit/verdicts/ and .nopekit/trace/ and "
                "rewrites .nopekit/round.json, nothing else — put the change in a candidate commit")
        self._new_entry(work)
        self._git(["add", "-A"], work)
        sha = self._commit(work, subject="evidence with a limit edit", lines=evidence_trailers())
        lines = self._line(work, bare, sha, "limit")
        self._has(lines, f"E36 {sha[:12]} (evidence): changes claims/C1.json (M)" + tail)
        self._has_no(lines, f"E36 {sha[:12]} (evidence): changes .nopekit/verdicts/")
        self._branch(work, "model", base)
        _projects.set_thickness(work, 8.0)
        sha = self._commit(work, {"model/bracket.py": self._bytes(work, "model/bracket.py")},
                           subject="evidence with a model edit", lines=evidence_trailers())
        lines = self._line(work, bare, sha, "model")
        self._has(lines, f"E36 {sha[:12]} (evidence): changes model/bracket.py (M)" + tail)
        self._branch(work, "site", base)
        sha = self._commit(work, {"site/data/page.json": "{}\n"}, subject="evidence with a page",
                           lines=evidence_trailers())
        lines = self._line(work, bare, sha, "site")
        self._has(lines, f"E36 {sha[:12]} (evidence): changes site/data/page.json (A)" + tail)


class AWorkingCommitSmugglesNothing(_Pushes):
    """A claiming commit is judged by the difference between its tree and the
    previous claiming commit's — so a working commit between them can smuggle
    nothing, and the refusal lands on the claiming commit that carried it."""

    def _smuggle(self, work: str) -> tuple[str, str, str, str]:
        """``(candidate, working, evidence, rewritten entry)``: the fixture's
        candidate, a working commit that edits C1's limit and rewrites one
        byte of a committed entry (digest recomputed), and an evidence commit
        adding the entry `round` then files with the regenerated state file."""
        candidate = self._sha(work)
        rel, doc = self._fail_entry(work)
        doc = json.loads(json.dumps(doc))
        doc["verdict"]["detail"] += " "
        _name, data = self._entry_bytes(doc)
        self._loosen(work)
        working = self._commit(work, {rel: data, "claims/C1.json": self._bytes(work, "claims/C1.json")},
                               subject="just work")
        self._nopekit(work, "round")
        added = self._untracked(work, ".nopekit/verdicts/")
        self.assertEqual(len(added), 1, added)
        self._git(["add", "-A"], work)
        evidence = self._commit(work, subject="the recheck", lines=evidence_trailers())
        return candidate, working, evidence, rel

    def test_the_smuggle_lands_in_the_next_diff(self):
        work = self._work()
        bare = self._bare(work)
        self._hook(bare)
        candidate, working, evidence, rel = self._smuggle(work)
        before = self._ls_remote(work)
        proc = self._push(work, f"{evidence}:refs/heads/cand/x")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"remote: E36 {evidence[:12]} (evidence): changes claims/C1.json (M);", proc.stderr)
        self.assertIn(f"remote: E37 {evidence[:12]} (evidence): {rel} is modified;", proc.stderr)
        self.assertNotIn(working[:12], proc.stderr, "a working commit is never judged")
        self.assertNotIn(f"E36 {candidate[:12]}", proc.stderr)
        self.assertIn("(pre-receive hook declined)", proc.stderr)
        self.assertEqual(self._ls_remote(work), before)

    def test_the_previous_claiming_commit_is_found_past_old(self):
        work = self._work()
        bare = self._bare(work)
        self._commit(work, subject="work 1")
        doc = self._read_json(work, "claims/C2.json")
        doc["rationale"] = str(doc.get("rationale", "")) + " (edited in a working commit)"
        self._commit(work, {"claims/C2.json": json.dumps(doc, indent=2) + "\n"}, subject="work 2")
        self._commit(work, subject="work 3")
        # The hook off: a line whose tip is a working commit lands by hand.
        old, landed, ref = self._land(work, bare, "HEAD:refs/heads/cand/x")
        evidence = self._commit(work, subject="the recheck", lines=evidence_trailers())
        old, new, ref = self._land(work, bare, "HEAD:refs/heads/cand/x")
        self.assertEqual(old, landed)
        lines = self._validate(bare, ref, old, new)
        self._has(lines, f"E36 {evidence[:12]} (evidence): changes claims/C2.json (M); an evidence "
                         "commit adds under .nopekit/verdicts/ and .nopekit/trace/ and rewrites "
                         ".nopekit/round.json, nothing else — put the change in a candidate commit")

    def test_a_parent_diff_would_have_let_it_through(self):
        work = self._work()
        bare = self._bare(work)
        candidate, working, evidence, rel = self._smuggle(work)
        old, new, ref = self._land(work, bare, f"{evidence}:refs/heads/cand/x")
        lines = self._validate(bare, ref, old, new)
        self._has(lines, f"E36 {evidence[:12]} (evidence): changes claims/C1.json (M)")
        self._has(lines, f"E37 {evidence[:12]} (evidence): {rel} is modified")
        parent_diff = vcs.tree_diff(bare, vcs.tree_of(bare, working), vcs.tree_of(bare, evidence))
        self.assertIsNotNone(parent_diff)
        parent_paths = {change.path for change in parent_diff}
        self.assertNotIn("claims/C1.json", parent_paths)
        self.assertNotIn(rel, parent_paths)
        self.assertFalse([p for p in parent_paths if p.startswith("claims/")],
                         f"the parent diff names a record: {parent_paths}")
        claiming_diff = vcs.tree_diff(bare, vcs.tree_of(bare, candidate), vcs.tree_of(bare, evidence))
        self.assertIn("claims/C1.json", {c.path for c in claiming_diff})


# --------------------------------------------------------------------------- #
# the state file
# --------------------------------------------------------------------------- #
class TheStateFileIsTheTreesOwn(_Pushes):
    """E40-E44: `.nopekit/round.json` is present, reads `ok`, was computed from
    this tree's records and verdict cache, is the bytes `round` renders, and
    its counter climbs along the line — and what the hook cannot see is pinned
    as accepted, so the limit is a test and not a surprise."""

    def test_E40_a_missing_state_file(self):
        work = self._work()
        bare = self._bare(work)
        sha = self._commit(work, {rounds.ROUND_REL: None}, subject="no record",
                           lines=trailers(name="D.2"))
        lines = self._line(work, bare, sha, "x")
        self._has(lines, f"E40 {sha[:12]} (candidate D.2): .nopekit/round.json is missing; run "
                         "nopekit round and commit it")

    def test_E41_edited_unreadable_newer(self):
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        doc = self._read_json(work, rounds.ROUND_REL)
        edited = json.loads(json.dumps(doc))
        edited["claims"]["C1"] = "checked"
        conflict = ("<<<<<<< HEAD\n" + rounds.render(doc).decode("utf-8") + "=======\n"
                    + rounds.render(edited).decode("utf-8") + ">>>>>>> theirs\n")
        newer = json.loads(json.dumps(doc))
        newer["schema"] = 2
        cases = [
            (rounds.render(edited), "reads edited (its self-digest does not match its content); run "
                                    "nopekit round and commit what it writes"),
            (conflict.encode("utf-8"), "reads unreadable ("),
            (self._round_bytes(newer), "was written by a newer nopekit (schema 2); this server reads 1"),
            # A round that is not an integer >= 1 is a record `rounds.read`
            # says `ok` of and gives no round for (`Previous.round` None): held
            # here, since E43 renders `round: 0` byte for byte and E44 compares
            # nothing against a counter it never learned — a line started at
            # round 0 grew with round -7 in review of milestone 0.
            (self._round_bytes(doc, 0), "reads unreadable (round 0 is not an integer >= 1); run "
                                        "nopekit round and commit what it writes"),
            (self._round_bytes(doc, "8"), "reads unreadable (round '8' is not an integer >= 1); run "
                                          "nopekit round and commit what it writes"),
        ]
        for i, (data, words) in enumerate(cases):
            with self.subTest(words=words):
                self._branch(work, f"state{i}", base)
                sha = self._commit(work, {rounds.ROUND_REL: data}, subject="the record",
                                   lines=trailers(name=f"D.{i + 2}"))
                lines = self._line(work, bare, sha, f"state{i}")
                self._has(lines, f"E41 {sha[:12]} (candidate D.{i + 2}): .nopekit/round.json {words}")

    def test_E42_computed_from_other_records_or_verdicts(self):
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        held = self._read_json(work, rounds.ROUND_REL)["computed_from"]
        claim = self._read_json(work, "claims/C1.json")
        claim["rationale"] = str(claim["rationale"]) + " (edited after the round)"
        sha = self._commit(work, {"claims/C1.json": json.dumps(claim, indent=2) + "\n"},
                           subject="a rationale edit", lines=trailers(name="D.2"))
        lines = self._line(work, bare, sha, "records")
        hits = self._has(lines, f"E42 {sha[:12]} (candidate D.2): .nopekit/round.json was computed "
                                f"from other records (computed_from.records {held['records'][:12]}, "
                                "this tree's records digest ", "); run nopekit round and commit it")
        self.assertNotIn(f"records digest {held['records'][:12]}", hits[0])
        self._has_no(lines, f"E42 {sha[:12]}", "verdict cache")
        self._branch(work, "verdicts", base)
        _rel, doc = self._fail_entry(work)
        foreign = json.loads(json.dumps(doc))
        foreign["spine"] = "1" * 64
        foreign["rho"] = verdicts.rho(foreign["gate"], foreign["spine"], foreign["code"]["digest"],
                                      foreign["reads"])
        self._write_entry(work, "bracket.deflection", foreign)
        self._git(["add", "-A"], work)
        sha = self._commit(work, subject="an entry without a round", lines=trailers(name="D.3"))
        lines = self._line(work, bare, sha, "verdicts")
        self._has(lines, f"E42 {sha[:12]} (candidate D.3): .nopekit/round.json was computed from "
                         f"another verdict cache (computed_from.verdicts {held['verdicts'][:12]}, ",
                  "); run nopekit round and commit it")
        self._has_no(lines, f"E42 {sha[:12]}", "other records")

    def test_E43_not_the_rendered_bytes(self):
        work = self._work()
        bare = self._bare(work)
        doc = self._read_json(work, rounds.ROUND_REL)
        reindented = (json.dumps(doc, indent=4, ensure_ascii=False) + "\n").encode("utf-8")
        self.assertNotEqual(reindented, rounds.render(doc))
        self.assertEqual(rounds.self_digest(doc), doc["self"], "the seal holds: only the bytes differ")
        sha = self._commit(work, {rounds.ROUND_REL: reindented}, subject="reindented",
                           lines=trailers(name="D.2"))
        lines = self._line(work, bare, sha, "x")
        self._has(lines, f"E43 {sha[:12]} (candidate D.2): .nopekit/round.json is not the bytes "
                         "nopekit round renders (indent, key order or newline differ); run nopekit "
                         "round and commit what it writes")
        self._has_no(lines, f"E41 {sha[:12]}")

    def test_E44_the_counter_is_monotone(self):
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        doc = self._read_json(work, rounds.ROUND_REL)
        was = doc["round"]
        self.assertGreaterEqual(was, 1)
        sha = self._commit(work, {rounds.ROUND_REL: self._round_bytes(doc, 1)}, subject="round 1",
                           lines=evidence_trailers())
        lines = self._line(work, bare, sha, "x")
        self._has(lines, f"E44 {sha[:12]} (evidence): .nopekit/round.json says round 1, not above "
                         f"the previous candidate's {was}; run nopekit round and commit what it writes")
        self._has_no(lines, f"E36 {sha[:12]}")
        self._has_no(lines, f"E42 {sha[:12]}")
        self._has_no(lines, f"E43 {sha[:12]}")
        # The counter is held on a line from its first claiming commit, the
        # number taken from that commit's own file: a line whose candidate says
        # 3 (resealed, its digests true, so it lands) refuses an evidence commit
        # saying 2. And a counter below 1 is E41's, never a gap in E44's: the
        # line that started at `round: 0` and grew with `round: -7` in review.
        # The line forks from nothing: a root commit on the bracket's tree, so no
        # claiming commit sits below it and the previous tree is the empty one.
        tree3 = self._tree(work, self._tree_of(work, base),
                           {rounds.ROUND_REL: ("100644", self._blob(work, self._round_bytes(doc, 3)))})
        three = self._commit_tree(work, tree3, [], message("round 3", trailers(name="D.2")))
        tree2 = self._tree(work, tree3,
                           {rounds.ROUND_REL: ("100644", self._blob(work, self._round_bytes(doc, 2)))})
        two = self._commit_tree(work, tree2, [three], message("round 2", evidence_trailers()))
        lines = self._line(work, bare, two, "orphan")
        self._has(lines, f"E44 {two[:12]} (evidence): .nopekit/round.json says round 2, not above "
                         "the previous candidate's 3; run nopekit round and commit what it writes")
        self.assertEqual([line for line in lines if three[:12] in line and line.startswith("E")],
                         [], "the first claiming commit of a line is held to no earlier round")
        tree_below = self._tree(work, tree3, {rounds.ROUND_REL: ("100644",
                                                                self._blob(work, self._round_bytes(doc, -7)))})
        below = self._commit_tree(work, tree_below, [three], message("round -7", evidence_trailers()))
        lines = self._line(work, bare, below, "zero")
        self._has(lines, f"E41 {below[:12]} (evidence): .nopekit/round.json reads unreadable (round "
                         "-7 is not an integer >= 1); run nopekit round and commit what it writes")
        self._has_no(lines, "E44 ")

    def test_what_the_hook_cannot_see(self):
        """The milestone-0 limit, pinned (brief §11): a forged round record
        whose digests are true, and a forged Fresh PASS at the honest rho,
        are both ACCEPTED. The hook proves a record is this tree's own and an
        entry is well-formed, named, sealed and rho-consistent; it cannot
        prove an outcome was produced by the tree, because that needs the
        model and the gate modules executed. What closes it: `export`'s
        re-execution at the boundary that spends (invariant 12) and, in
        milestone 1, a renderer that runs `check --force` at tier 0 in a
        sandbox; until then the UI labels every token "run by the agent"."""
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        # 1. the forged record, every claim checked and ready, resealed
        self._forge_round(work)
        sha = self._commit(work, subject="a forged record", lines=trailers(name="D.2"))
        lines = self._line(work, bare, sha, "record")
        self.assertEqual([line for line in lines if line.startswith("E")], [],
                         "the limit moved: the hook now refuses a resealed record with true digests")
        # 2. the forged PASS at the honest rho: the FAIL's reads, the outcome
        #    flipped, digest and name recomputed, the FAIL never committed (a
        #    line of its own, whose first candidate is this one: on a line
        #    nothing is deleted, so a FAIL once landed is E37 to withdraw),
        #    and `round` serving the forgery as Fresh.
        self._branch(work, "pass", base)
        rel, doc = self._fail_entry(work)
        forged = json.loads(json.dumps(doc))
        forged["verdict"].update({"passed": True, "measured": 0.4})
        os.remove(os.path.join(work, *rel.split("/")))
        added = self._write_entry(work, "bracket.deflection", forged)
        proc = self._nopekit(work, "round")
        self.assertTrue(os.path.exists(os.path.join(work, *added.split("/"))))
        self.assertFalse(os.path.exists(os.path.join(work, *rel.split("/"))),
                         "the tier-0 sweep re-ran the gate: the premise of the forgery is gone")
        self.assertEqual(self._read_json(work, rounds.ROUND_REL)["claims"]["C1"], "checked",
                         f"the resolver did not serve the forgery as Fresh: {proc.stdout}")
        self._git(["add", "-A"], work)
        sha = self._commit(work, subject="a forged pass", lines=trailers(name="D.3"), amend=True)
        self.assertEqual(self._git(["rev-list", "--count", sha], work).strip(), "1")
        lines = self._line(work, bare, sha, "pass")
        self.assertEqual([line for line in lines if line.startswith("E")], [],
                         "the limit moved: the hook now refuses a forged PASS at the honest rho")


# --------------------------------------------------------------------------- #
# through git
# --------------------------------------------------------------------------- #
class TheHookRefusesThroughGit(_Pushes):
    """The real hook, a real push: the rule's line reaches the client prefixed
    ``remote:``, the push is declined, and nothing moves."""

    def test_the_lines_reach_the_client(self):
        work = self._work()
        bare = self._bare(work)
        self._hook(bare)
        sha = self._commit(work, subject="typo", lines=trailers(extra=["Nope-Kinds: candidate"]))
        before = self._ls_remote(work)
        proc = self._push(work, "HEAD:refs/heads/cand/x")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"remote: E13 {sha[:12]}: Nope-Kinds is not a trailer the schema knows "
                      "(did you mean Nope-Kind?)", proc.stderr)
        self.assertIn("(pre-receive hook declined)", proc.stderr)
        self.assertNotIn("accepted:", proc.stderr + proc.stdout)
        self.assertEqual(self._ls_remote(work), before)
        self.assertNotIn("refs/heads/cand/x", before)


class TheHookFailsClosed(_Pushes):
    """A crash, an import failure, no quarantine and a hung git each decline
    the push: a non-zero exit is the only thing the hook promises on its own
    failure, and exit 0 is never the answer to a question it could not ask."""

    def _copy_of_the_script(self, *, planted: str = "") -> str:
        """A copy of ``server/pre-receive`` under a directory of its own (so
        its ``../src`` does not exist), with ``planted`` inserted before the
        import of nopekit."""
        where = os.path.join(self.tmp(), "server")
        os.makedirs(where)
        with open(HOOK_SCRIPT, encoding="utf-8") as fh:
            lines = fh.read().splitlines(keepends=True)
        if planted:
            hit = [i for i, line in enumerate(lines) if "from nopekit.cli import main" in line]
            self.assertEqual(len(hit), 1, "the script imports nopekit.cli exactly once")
            indent = lines[hit[0]][:len(lines[hit[0]]) - len(lines[hit[0]].lstrip())]
            lines.insert(hit[0], f"{indent}{planted}\n")
        path = os.path.join(where, "pre-receive")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(lines))
        os.chmod(path, 0o755)
        return path

    def test_a_crash_an_import_failure_and_no_quarantine_each_decline(self):
        work = self._work()
        bare = self._bare(work)
        before = self._ls_remote(work)
        nowhere = self.tmp()
        probe = _env.run([sys.executable, "-c", "import nopekit"], cwd=nowhere,
                         env={"PYTHONPATH": nowhere})
        if probe.returncode == 0:
            self.skipTest("nopekit is installed for this interpreter: an import cannot fail here")
        # 1. an import failure: PYTHONPATH to nowhere, the script away from its src
        self._hook(bare, script=self._copy_of_the_script(),
                   env_lines=[f'PYTHONPATH="{nowhere}"', "export PYTHONPATH"])
        proc = self._push(work, "HEAD:refs/heads/cand/x")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("remote: nopekit validate crashed (ModuleNotFoundError: ", proc.stderr)
        self.assertIn("the push is refused", proc.stderr)
        self.assertIn("(pre-receive hook declined)", proc.stderr)
        # 2. a crash before the import
        self._hook(bare, script=self._copy_of_the_script(planted='raise RuntimeError("planted")'))
        proc = self._push(work, "HEAD:refs/heads/cand/x")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("remote: nopekit validate crashed (RuntimeError: planted); the push is "
                      "refused", proc.stderr)
        self.assertIn("(pre-receive hook declined)", proc.stderr)
        self.assertEqual(self._ls_remote(work), before)
        # 3. run outside a hook: no GIT_OBJECT_DIRECTORY
        proc = _env.run([sys.executable, HOOK_SCRIPT], cwd=bare,
                        input=f"{ZERO} {self._sha(work)} refs/heads/cand/x\n")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("no GIT_OBJECT_DIRECTORY, so this is not a pre-receive hook", proc.stderr)
        # 4. the delete-only carve-out (git builds no quarantine for a push that
        #    unpacks nothing) opens only with GIT_DIR set, as git sets it for
        #    every hook: a delete-only line with neither variable is refused, and
        #    with GIT_DIR alone it is judged against the main store.
        proc = _env.run([sys.executable, HOOK_SCRIPT], cwd=bare,
                        input=f"{ZERO} {ZERO} refs/heads/draft/x\n")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("no GIT_OBJECT_DIRECTORY, so this is not a pre-receive hook", proc.stderr)
        proc = _env.run([sys.executable, HOOK_SCRIPT], cwd=bare, env={"GIT_DIR": bare},
                        input=f"{ZERO} {ZERO} refs/heads/draft/x\n")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc = _env.run([sys.executable, HOOK_SCRIPT], cwd=bare, env={"GIT_DIR": bare},
                        input=f"{ZERO} {ZERO} refs/heads/cand/x\n")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("E03 cand/x: a line is never deleted", proc.stdout + proc.stderr)
        self.assertEqual(self._ls_remote(work), before)

    def test_a_hung_git_declines(self):
        work = self._work()
        bare = self._bare(work)
        fake = self.tmp()
        path = os.path.join(fake, "git")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"#!{sys.executable}\nimport time\ntime.sleep({vcs.VCS_TIMEOUT_S + 5})\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self._hook(bare, env_lines=[f'PATH="{fake}:$PATH"', "export PATH"])
        before = self._ls_remote(work)
        proc = self._push(work, "HEAD:refs/heads/cand/x")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("remote: E07 push: git did not answer (", proc.stderr)
        self.assertIn("(pre-receive hook declined)", proc.stderr)
        self.assertEqual(self._ls_remote(work), before)


# --------------------------------------------------------------------------- #
# the validator writes nothing
# --------------------------------------------------------------------------- #
_WRITES: list[str] = []
_WATCHING = [False]


def _audit(event: str, args: tuple) -> None:
    """Every write the process makes while `_WATCHING`: an `open` for writing,
    a directory made, a path removed or renamed. Two things are not writes:
    ``os.devnull`` opened read-write (`subprocess.DEVNULL` for a child's
    stdin), and a removal named relative to a ``dir_fd`` (`shutil.rmtree`
    unlinking inside the directory it is removing, which the "scratch is gone"
    check below covers by its root)."""
    if not _WATCHING[0]:
        return
    try:
        if event == "open":
            path, mode, flags = args
            if isinstance(path, int):
                return
            path = os.fsdecode(path)
            if path == os.devnull:
                return
            writing = (mode is not None and any(c in str(mode) for c in "wax+")) or (
                mode is None and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)))
            if writing:
                _WRITES.append(path)
        elif event in ("os.mkdir", "os.rename", "os.remove", "os.unlink", "os.rmdir",
                       "shutil.rmtree", "os.link", "os.symlink", "os.replace", "shutil.move",
                       "shutil.copyfile", "os.truncate"):
            path = os.fsdecode(args[0])
            if os.path.isabs(path):
                _WRITES.append(path)
    except Exception:    # noqa: BLE001 — an audit hook must never raise into the program
        pass


class TheValidatorWritesNothing(_Pushes):
    """`validate_push` writes only under a scratch directory outside the
    repository, removed in a ``finally``: on a refused push and an accepted
    one, against the bare server and the work clone; the clone's index is
    untouched; and a refused push through the hook moves no ref."""

    def _watched(self, fn: Any) -> list[str]:
        del _WRITES[:]
        if not getattr(sys, "_nopekit_receive_audit", False):
            sys.addaudithook(_audit)
            sys._nopekit_receive_audit = True       # type: ignore[attr-defined]
        _WATCHING[0] = True
        try:
            fn()
        finally:
            _WATCHING[0] = False
        return list(_WRITES)

    def test_every_write_is_scratch_and_gone(self):
        work = self._work()
        bare = self._bare(work)
        base = self._sha(work)
        refused = self._commit(work, {rounds.ROUND_REL: None}, subject="refused",
                               lines=trailers(name="D.2"))
        old, new, ref = self._land(work, bare, f"{refused}:refs/heads/cand/refused")
        # The accepted push is a commit of its own on a fresh branch: `base`
        # is on cand/refused's chain by now, so a new ref at it would validate
        # nothing but the tip rule and the scan and materialise nothing —
        # which made the accepted sub-case vacuous in review (zero writes
        # passed every assertion below).
        self._branch(work, "accepted", base)
        accepted = self._commit(work, {"notes/note.txt": "n\n"}, subject="accepted",
                                lines=trailers(name="D.3"))
        self._land(work, bare, f"{accepted}:refs/heads/cand/accepted")
        self._nopekit(work, "status")               # writes the clone's index (ignored by git)
        ledger = os.path.join(work, ".nopekit", "ledger.json")
        self.assertTrue(os.path.isfile(ledger), "the clone's index is there to be left alone")
        with open(ledger, "rb") as fh:
            index_was = (fh.read(), os.stat(ledger).st_mtime_ns)
        for repo, (o, n, r), ok in ((bare, (old, new, ref), False),
                                    (work, (ZERO, refused, ref), False),
                                    (bare, (ZERO, accepted, "refs/heads/cand/accepted"), True),
                                    (work, (ZERO, accepted, "refs/heads/cand/accepted"), True)):
            with self.subTest(repo=os.path.basename(repo), new=n[:12]):
                reports: list[Any] = []
                writes = self._watched(lambda: reports.append(self._report(repo, (o, n, r))))
                self.assertEqual(reports[0].unanswered, "")
                self.assertIs(reports[0].accepted, ok, self._lines(reports[0]))
                scratch = {w for w in writes if "nopekit-validate-" in w}
                outside = sorted(set(writes) - scratch)
                self.assertEqual(outside, [], f"validate_push wrote outside its scratch: {outside}")
                roots = {w[:w.index("nopekit-validate-") + len("nopekit-validate-")] for w in scratch}
                self.assertTrue(roots, "validate_push materialised nothing: the audit hook saw no "
                                       "scratch write, so nothing below could fail")
                for root in roots:
                    parent = os.path.dirname(root)
                    left = [name for name in os.listdir(parent)
                            if os.path.join(parent, name).startswith(root)]
                    self.assertEqual(left, [], f"scratch left behind: {left}")
        with open(ledger, "rb") as fh:
            self.assertEqual((fh.read(), os.stat(ledger).st_mtime_ns), index_was,
                             "the clone's index moved under validate_push")
        # Through the hook: a refused push moves no ref. A commit of its own —
        # one already on a line's chain would validate nothing but the tip
        # rule and the scan (brief §3's edge case), and land.
        self._hook(bare)
        self._branch(work, "hooked", base)
        again = self._commit(work, {rounds.ROUND_REL: None}, subject="refused again",
                             lines=trailers(name="D.3"))
        before = self._ls_remote(work)
        proc = self._push(work, f"{again}:refs/heads/cand/hooked")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"remote: E40 {again[:12]} (candidate D.3): .nopekit/round.json is missing",
                      proc.stderr)
        self.assertIn("(pre-receive hook declined)", proc.stderr)
        self.assertEqual(self._ls_remote(work), before)


# --------------------------------------------------------------------------- #
# the green path
# --------------------------------------------------------------------------- #
class AnHonestLineLands(_Pushes):
    """The bracket, its spec tree on `spec`, the candidate trailers on its tip,
    pushed through the real hook to a new line: accepted, with no line of its
    own on the client; then a candidate that waits, then an evidence commit
    (a new amber file from `model trace` and the regenerated state file):
    accepted. And `nopekit validate` says the same of the clone."""

    def test_an_honest_line_lands_and_evidence_follows(self):
        work = self._work(name="bracket")
        bare = self._bare(work)
        base = self._sha(work)
        self._land(work, bare, "HEAD:refs/heads/main")
        self._spec_branch(bare, base)
        self._hook(bare)
        proc = self._push(work, "HEAD:refs/heads/cand/bracket")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("remote: E", proc.stderr + proc.stdout)
        self.assertNotIn("accepted:", proc.stderr + proc.stdout, "withheld under --stdin")
        self.assertNotIn("N01 ", proc.stderr, "its spec tree is on spec")
        self.assertEqual(self._ls_remote(work).get("refs/heads/cand/bracket"), base)
        # A second candidate: the limit loosened, `round` rerun — lands and waits.
        self._loosen(work)
        self._nopekit(work, "round")
        self._git(["add", "-A"], work)
        second = self._commit(work, subject="loosens C1", lines=trailers(name="D.2"))
        proc = self._push(work, "HEAD:refs/heads/cand/bracket")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("remote: E", proc.stderr + proc.stdout)
        self.assertIn(f"remote: N01 {second[:12]} (candidate D.2): awaits acceptance", proc.stderr)
        # That candidate ADDED a verdict entry (the loosened limit's new rho), so
        # the accept path for an added entry — its reader, its rho, the notes —
        # ran through the real hook here, not only in process. (An EVIDENCE
        # commit carrying a new entry needs a gate tier 0 skips, which the
        # bracket has none of: a limit edit in an evidence commit is E36.)
        added_entries = self._git(["diff", "--name-only", f"{base}..{second}", "--",
                                   ".nopekit/verdicts/"], work).split()
        self.assertEqual(len(added_entries), 1, added_entries)
        self.assertTrue(verdicts._ENTRY_NAME.match(os.path.basename(added_entries[0])))
        # Evidence: the trace's amber file for the new acceptance, and the
        # state file regenerated with the trace's digest, nothing else.
        self._nopekit(work, "model", "trace", code=0)
        added = self._untracked(work, ".nopekit/trace/")
        self.assertEqual(len(added), 1, f"model trace wrote {added}, not one amber file")
        self._nopekit(work, "round")
        self._git(["add", "-A"], work)
        changed = self._git(["diff", "--cached", "--name-only"], work).split()
        self.assertEqual(sorted(changed), sorted([added[0], rounds.ROUND_REL]))
        evidence = self._commit(work, subject="the trace drawn", lines=evidence_trailers())
        proc = self._push(work, "HEAD:refs/heads/cand/bracket")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("remote: E", proc.stderr + proc.stdout)
        self.assertEqual(self._ls_remote(work).get("refs/heads/cand/bracket"), evidence)
        # The agent's pre-flight on the clone itself.
        proc = self._nopekit(work, "validate", ".", "refs/heads/cand/bracket", "0", "HEAD", "--json",
                             code=0)
        doc = json.loads(proc.stdout)
        self.assertIs(doc["ok"], True)
        self.assertEqual(doc["unanswered"], "")
        kinds = [(c["kind"], c["name"]) for c in doc["refs"][0]["commits"]]
        self.assertIn(("candidate", "bracket"), kinds)
        self.assertIn(("candidate", "D.2"), kinds)
        self.assertIn("evidence", [k for k, _n in kinds])
        proc = self._nopekit(work, "validate", ".", "refs/heads/cand/bracket", "0", "HEAD", code=0)
        self.assertIn(f"accepted: cand/bracket at {evidence[:12]}", proc.stdout)
        self.assertNotIn("\nE", "\n" + proc.stdout)


if __name__ == "__main__":
    unittest.main()
