# SPDX-License-Identifier: Apache-2.0
"""What ships carries no prior of the scenarios the private bench scores.

PLAN-v0.14 (top): the agents the bench measures run nopekit from a release
bundle and must share no priors with the agents that build it — an agent that has
read the test answers the test, not the tool — so shipped text carrying a scored
scenario's terms contaminates it. What slipped through (review of P2.5a): the
walkthrough's persona and its own example of an expert-judgment claim were copied
verbatim into the shipped skill — "C6 is a gap until <the persona> records its
owner", and the claim itself as the judgment example — and the persona into the
site contract and two docstrings. Neither was in any shipped file before.

* **ShippedTextCarriesNoScenarioPrior** — every file the bundle ships (git's
  files, less PLAN-v0.14's strip list) is read; a term is matched as a word (a
  token as written, or lower-cased, or two lower-cased tokens in a row); a hit
  reads ``<path>: PRIOR[i]``.

The terms are held as SHA-256 digests, never as words: this file states no
persona, scenario or example, as PLAN-v0.14 asks of "a repo test born from a bench
finding". Adding one is ``hashlib.sha256(term.encode()).hexdigest()`` — the
persona's name as written (case kept, so the thermal pack's ``SAM``, an acronym,
is no hit), every phrase lower-cased. A scenario term already in shipped text
before this test (an anecdote METHOD or README recounts) is not listed: that is
A-13's question, put to the user, not this file's to settle. *Rejected:* the
words in plain text (a builder reading this file is told nothing new, but every
grep of the repository then finds the persona in it); scanning the bundle
itself (the bench's, outside this repository — this is the repository's own
half, run with every suite).

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_priors.py -v
"""
from __future__ import annotations

import hashlib
import os
import re
import unittest
from typing import Iterable

import _env

#: The terms, as digests (see the module docstring). Each from the walkthrough
#: spec the bench scores against: its persona's name (case kept); its own
#: expert-judgment example; the names of its milestones; its parameter and its
#: candidate; the place its physical result failed. And two canaries, a word and
#: a phrase, which the planted rows below write — the scanner must find both.
PRIOR_DIGESTS: tuple[str, ...] = (
    "4ecde249d747d51d869ae689c44cc1e6191b581b8315edac97990fdc4dce40d7",
    "6636b52e455d1a2ea833d09d3bef9f5da871463883ae69b500d9e87a9fa4af3f",
    "a054b91b11528999319d924681813e0418f0859a86d37fb42b8aebc594d6c5f1",
    "1c599bbe71201071dc27f6e854f416821b06e66c3a5f0a85036c102abe26827e",
    "c939d07c656e6a95f4ffa52b51b6e4fa8c31bf53256ad4f2e95d5b1b74117063",
    "044bbbb4b21d2382eb57e8c8db9d02f1670adc431daddd07c6481ab551cee293",
    "97560c48a607abae80363bb79720742fc5e2ea4227e4de13b11a0f58cfb32c40",
    "69f0ba06e2c49cc8d2071164c37ad79bda23526757daa7b1b6224d4af0d4e929",
    "52360086bee5253fcd2750f84c3cc4a7db2d5fad950f7a3c78e4e48a86889543",
    hashlib.sha256(b"canary phrase").hexdigest(),
)
_INDEX = {digest: i for i, digest in enumerate(PRIOR_DIGESTS)}

#: PLAN-v0.14's strip list: what the release bundle leaves out, so what this
#: scan leaves out too. The paper is any PDF.
STRIPPED_PREFIXES = ("docs/PLAN", "docs/plan/", "tests/", "evals/")
STRIPPED_FILES = ("docs/BENCHMARK_ENGINE.md", "docs/GLOSSARY.md", "docs/ORIGINS.md")

#: A scan that reads nothing passes every check: the floor it must read. Why
#: these: on 2026-10-04 the shipped files were 187 text files, 4.6 MB; half of
#: either is a scan that lost a directory. *Rejected:* no floor (an empty file
#: list is the logger NoLeakedProvenance's floor exists to catch).
MIN_FILES = 90
MIN_BYTES = 2_000_000

_TOKEN = re.compile(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*")


def _digest(term: str) -> str:
    return hashlib.sha256(term.encode("utf-8")).hexdigest()


def _shipped(rel: str) -> bool:
    return not (rel.startswith(STRIPPED_PREFIXES) or rel in STRIPPED_FILES
                or rel.lower().endswith(".pdf"))


def _files(root: str) -> list[str]:
    """The repository's files as git lists them (tracked and untracked, ignores
    honoured), or every file under ``root`` when it is not a checkout."""
    proc = _env.git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                    cwd=root)
    if proc.returncode == 0:
        return sorted({name for name in proc.stdout.split("\0") if name})
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for name in filenames:
            out.append(os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/"))
    return sorted(out)


def scan(root: str, files: Iterable[str] | None = None) -> tuple[list[str], int, int]:
    """``(hits, files read, bytes read)`` over the shipped files under ``root``."""
    hits: list[str] = []
    count = size = 0
    for rel in (files if files is not None else _files(root)):
        if not _shipped(rel):
            continue
        path = os.path.join(root, *rel.split("/"))
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        if b"\0" in data[:8192]:
            continue
        count += 1
        size += len(data)
        tokens = _TOKEN.findall(data.decode("utf-8", "replace"))
        lowered = [token.lower() for token in tokens]
        grams = set(tokens) | set(lowered) | {f"{a} {b}" for a, b in zip(lowered, lowered[1:])}
        found = sorted({_INDEX[d] for d in map(_digest, grams) if d in _INDEX})
        hits += [f"{rel}: PRIOR[{i}]" for i in found]
    return hits, count, size


class ShippedTextCarriesNoScenarioPrior(unittest.TestCase):
    """(review of P2.5a; PLAN-v0.14, top) No shipped file names a term of a
    scenario the private bench scores."""

    def test_the_shipped_files_are_clean(self):
        hits, count, size = scan(_env.REPO)
        self.assertEqual(hits, [])
        self.assertGreaterEqual(count, MIN_FILES)
        self.assertGreaterEqual(size, MIN_BYTES)

    def test_planted_terms_are_found(self):
        """Planted: the canaries, as a word and as a phrase, in a shipped path and
        in a stripped one — only the shipped one is a hit."""
        root = tempfile_dir(self)
        files = {"skills/x/SKILL.md": "a prior-scan-canary here\n",
                 "README.md": "a Canary\nphrase across a line\n",
                 "tests/test_x.py": "prior-scan-canary\n",
                 "docs/GLOSSARY.md": "canary phrase\n"}
        for rel, text in files.items():
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        hits, count, _size = scan(root, sorted(files))
        self.assertEqual(hits, ["README.md: PRIOR[9]", "skills/x/SKILL.md: PRIOR[8]"])
        self.assertEqual(count, 2)


class TheSkillPromisesNoCheckpoint(unittest.TestCase):
    """(review of P2.5a) The shipped skill told an agent a fail keeps counting
    "until a later checkpoint records a pass on a new build" — the development
    plan's word in a human channel (GLOSSARY §7: an agent repeats the skill to a
    person), promising a supersession that has not landed. The skills name no
    checkpoint; planted: the sentence back."""

    WORD = re.compile(r"\bcheckpoints?\b", re.IGNORECASE)

    def _skills(self) -> dict[str, str]:
        base = os.path.join(_env.REPO, "skills")
        out = {}
        for dirpath, _dirs, filenames in os.walk(base):
            for name in filenames:
                if name.endswith(".md"):
                    path = os.path.join(dirpath, name)
                    with open(path, encoding="utf-8") as fh:
                        out[os.path.relpath(path, _env.REPO)] = fh.read()
        return out

    def test_no_skill_names_a_checkpoint(self):
        found = self._skills()
        self.assertGreaterEqual(len(found), 2)
        self.assertEqual([rel for rel, text in found.items() if self.WORD.search(text)], [])

    def test_the_sentence_back_is_caught(self):
        planted = "A fail keeps counting until a later checkpoint records a pass."
        self.assertTrue(self.WORD.search(planted))


def tempfile_dir(case: unittest.TestCase) -> str:
    import tempfile
    path = tempfile.mkdtemp(prefix="nopekit-priors-")
    case.addCleanup(_env._rmtree, path)
    return path


if __name__ == "__main__":
    unittest.main(verbosity=2)
