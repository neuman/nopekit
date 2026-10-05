# SPDX-License-Identifier: Apache-2.0
"""Digests from bytes, behind a stat cache that must not serve a stale one (S-22, S-45).

Staleness used to hash the digest RECORDED when a file was ingested. A limit file
edited under a project gate left every key unchanged, and the claim read PASS
where a re-run failed 19.6 g against 1 g (S-22); evidence tampered with after
ingest left `doctor` saying "staleness unchanged" (S-45). `util.FileDigests`
hashes the bytes on disk and keeps a stat cache so it need not re-read them every
time — and a cache is exactly where a stale digest would hide, so every test here
is an attempt to make it serve one.

The constructions are deliberate. A real same-tick edit cannot be produced on
demand (whether two writes share a timestamp tick depends on the kernel's clock
granularity), so the tests build the STATE such an edit leaves — a cache entry
whose key equals the file's current stat while its digest is of other bytes —
and set the cache file's mtime to place it inside or outside the racy window.
Each one carries its control: the same entry one tick outside the window IS
served, which proves the cache is consulted and that the timestamp rule, not
something else, is what re-hashed it.

Run:  PYTHONPATH=src python3 -m unittest tests.test_digests -v
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import unittest
from unittest import mock

from atompipe.util import FileDigests

import _env

#: A file mtime far past any window: 2020-01-01T00:00:00Z.
OLD_NS = 1_577_836_800 * 10**9

#: A cache write time far in the future: 2100-01-01T00:00:00Z. Past every file's
#: mtime and ctime, so nothing is racy and only the stat key can catch an edit.
FUTURE_NS = 4_102_444_800 * 10**9

POISON = "0" * 64


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _key(st: os.stat_result) -> list[int]:
    return [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_dev]


def _touched(st: os.stat_result) -> int:
    """The last time the file's stat moved: what the racy rule compares."""
    return max(st.st_mtime_ns, st.st_ctime_ns)


class FileDigestsAreHonest(_env.EnvCase):
    def setUp(self):
        self.dir = self.tmp()
        self.cache = os.path.join(self.dir, ".atompipe", "cache", "digests.json")

    # -- helpers ------------------------------------------------------------ #
    def _rows(self) -> dict:
        with open(self.cache, encoding="utf-8") as fh:
            return json.load(fh)["files"]

    def _plant(self, path: str, *, written: int, sha: str | None = None,
               key: os.stat_result | None = None) -> None:
        """Rewrite ``path``'s cache row (its digest, its recorded stat, or both)
        and date the cache file's write at ``written``."""
        with open(self.cache, encoding="utf-8") as fh:
            data = json.load(fh)
        row = data["files"][os.path.abspath(path)]
        if key is not None:
            row[:5] = _key(key)
        if sha is not None:
            row[5] = sha
        with open(self.cache, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.utime(self.cache, ns=(written, written))

    def _cached(self, name: str, data: bytes, *, mtime_ns: int = OLD_NS) -> str:
        """A file with an old mtime, digested and saved into the cache."""
        path = _write(os.path.join(self.dir, name), data)
        os.utime(path, ns=(mtime_ns, mtime_ns))
        digests = FileDigests(self.cache)
        self.assertEqual(digests.digest(path), _sha(data))
        self.assertTrue(digests.save())
        return path

    def _tick_past(self, path: str) -> None:
        """Wait until the filesystem clock has moved past ``path``'s last touch.

        A condition, not a sleep: files written next get a strictly later stamp
        however coarse this kernel's timestamps are.
        """
        target = _touched(os.stat(path))
        probe = os.path.join(self.dir, ".tick")
        for _ in range(1000):
            _write(probe, b"")
            if os.stat(probe).st_mtime_ns > target:
                return
            time.sleep(0.002)
        self.fail("the filesystem clock did not advance in 2 s")

    # -- the key ------------------------------------------------------------ #
    def test_a_same_size_edit_with_the_mtime_restored_and_backdated_is_caught(self):
        """S-22's shape: the bytes change, the size and mtime do not, and the edit
        is years outside any racy window — so only ctime and the inode can see it."""
        old, new = b"limit_g = 1.0\n", b"limit_g = 9.0\n"
        path = self._cached("limits.txt", old)
        before = os.stat(path)

        # Control: with the key unchanged the cache IS served — a planted digest
        # comes back — so what follows is the key at work, not an uncached read.
        self._plant(path, sha=POISON, written=FUTURE_NS)
        self.assertEqual(FileDigests(self.cache).digest(path), POISON)

        # The edit comes after the recorded stat, in a later tick. What slipped
        # through while writing this test: run within one coarse timestamp tick
        # of `_cached`'s own utime, the edit left even ctime as recorded and the
        # test failed about one run in four — that case is the racy rule's, and
        # the same-tick tests below own it.
        self._tick_past(path)
        with open(path, "r+b") as fh:                      # in place: same inode
            fh.write(new)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.stat(path)
        self.assertEqual((after.st_size, after.st_mtime_ns),
                         (before.st_size, before.st_mtime_ns),
                         "the edit must be invisible to size and mtime for this to test anything")
        self.assertEqual(FileDigests(self.cache).digest(path), _sha(new),
                         "a same-size edit with the mtime restored served the old digest")

    def test_a_same_size_replace_with_the_mtime_restored_is_caught(self):
        """How editors save: write a new file, rename it over the old one."""
        old, new = b"mass_g = 12.5\n", b"mass_g = 99.5\n"
        path = self._cached("mass.txt", old)
        before = os.stat(path)
        self._plant(path, sha=POISON, written=FUTURE_NS)
        self.assertEqual(FileDigests(self.cache).digest(path), POISON)

        staged = _write(path + ".new", new)
        os.utime(staged, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(staged, path)
        after = os.stat(path)
        self.assertEqual((after.st_size, after.st_mtime_ns), (before.st_size, before.st_mtime_ns))
        self.assertEqual(FileDigests(self.cache).digest(path), _sha(new))

    # -- the racy-clean rule ------------------------------------------------ #
    def test_an_edit_in_the_same_tick_as_the_cache_write_is_rehashed(self):
        """In the tick the cache is written, a same-size edit keeps size, mtime and
        ctime: the key matches and the bytes do not. Git's rule, `>=`, not `>`."""
        old, new = b"bed_xy = 220\n", b"bed_xy = 250\n"
        path = self._cached("config.txt", old)
        with open(path, "r+b") as fh:
            fh.write(new)
        st = os.stat(path)
        # The state that edit leaves: the recorded stat IS the file's stat, the
        # recorded digest is the old bytes', and the cache was written in the tick
        # the file was last touched.
        self._plant(path, key=st, written=_touched(st))
        self.assertEqual(FileDigests(self.cache).digest(path), _sha(new),
                         "an entry written in the tick of the edit was trusted")

        # Control: the same entry written one tick later is trusted — the rule,
        # not the key, is what re-hashed it above.
        self._plant(path, key=st, written=_touched(st) + 1)
        self.assertEqual(FileDigests(self.cache).digest(path), _sha(old))

    def test_a_poisoned_cache_inside_the_racy_window_is_rehashed(self):
        """A cache written BEFORE the file's last touch — restored from a backup,
        copied between checkouts — holds a digest for a key that matches."""
        data = b"load_n = 15\n"
        path = self._cached("load.txt", data, mtime_ns=OLD_NS + 10 * 10**9)
        self._plant(path, sha=POISON, written=OLD_NS)
        self.assertEqual(FileDigests(self.cache).digest(path), _sha(data),
                         "a digest cached before the file last moved was served")

        self._plant(path, sha=POISON, written=FUTURE_NS)            # control
        self.assertEqual(FileDigests(self.cache).digest(path), POISON)

    def test_a_backdated_edit_in_the_cache_tick_is_rehashed(self):
        """The ctime half of the rule. An edit whose mtime was restored to the past,
        landing in the tick the cache was written, has an old mtime and a ctime of
        exactly that tick; judged by mtime alone it is clean."""
        old, new = b"n_bolts = 2\n", b"n_bolts = 3\n"
        path = self._cached("bolts.txt", old)
        with open(path, "r+b") as fh:
            fh.write(new)
        os.utime(path, ns=(OLD_NS, OLD_NS))
        st = os.stat(path)
        self.assertLess(st.st_mtime_ns, st.st_ctime_ns)
        self._plant(path, key=st, written=st.st_ctime_ns)
        self.assertEqual(FileDigests(self.cache).digest(path), _sha(new),
                         "judged by mtime alone: a restored mtime hid an edit in the cache's tick")

        self._plant(path, key=st, written=st.st_ctime_ns + 1)       # control
        self.assertEqual(FileDigests(self.cache).digest(path), _sha(old))

    def test_a_racy_entry_is_not_laundered_by_an_idle_save(self):
        """A process that loads a racy entry and never asks for that file must not
        write it back: the next load would judge it against the NEW write time and
        trust it. Git smudges such entries on index write for the same reason."""
        data = b"material = petg\n"
        path = self._cached("material.txt", data)
        self._plant(path, sha=POISON, written=OLD_NS)               # racy: mtime == written

        idle = FileDigests(self.cache)
        other = _write(os.path.join(self.dir, "other.txt"), b"x\n")
        idle.digest(other)                                          # something to save
        self.assertTrue(idle.save())
        self.assertNotIn(os.path.abspath(path), self._rows(),
                         "a racy entry nobody re-checked was saved again")
        self.assertIn(os.path.abspath(other), self._rows())

        os.utime(self.cache, ns=(FUTURE_NS, FUTURE_NS))              # however late the next write
        self.assertEqual(FileDigests(self.cache).digest(path), _sha(data))

    def test_nothing_is_trusted_before_a_reference_and_the_cache_saves_work(self):
        """With no cache file there is no write time to judge by, so nothing hashed
        in this process is trusted until the first save. After it, a clean file is
        read once — the speed-up the cache exists for."""
        data = b"thickness = 7.0\n"
        expected = _sha(data)                  # before the spy: it counts every sha256
        path = _write(os.path.join(self.dir, "thickness.txt"), data)
        os.utime(path, ns=(OLD_NS, OLD_NS))
        self._tick_past(path)

        with mock.patch.object(hashlib, "sha256", wraps=hashlib.sha256) as hashed:
            loose = FileDigests()
            self.assertEqual(loose.digest(path), expected)
            self.assertEqual(loose.digest(path), expected)
            self.assertEqual(hashed.call_count, 2, "trusted an entry with no reference write")
            self.assertFalse(loose.save(), "an uncached FileDigests wrote a file")

            hashed.reset_mock()
            cached = FileDigests(self.cache)
            cached.digest(path)
            self.assertTrue(cached.save())
            cached.digest(path)
            FileDigests(self.cache).digest(path)
            self.assertEqual(hashed.call_count, 1,
                             "a clean file was re-read after the cache recorded it")

    # -- no bytes, no digest ------------------------------------------------ #
    def test_a_missing_file_is_none(self):
        digests = FileDigests(self.cache)
        self.assertIsNone(digests.digest(os.path.join(self.dir, "absent.txt")))

        path = self._cached("gone.txt", b"here\n")
        os.remove(path)
        self.assertIsNone(FileDigests(self.cache).digest(path),
                          "a deleted file answered with its cached digest")

        self.assertIsNone(digests.digest(self.dir), "a directory has no bytes")
        self.assertIsNone(digests.digest(os.path.join(self.dir, "nul\x00byte")))
        if hasattr(os, "mkfifo"):
            fifo = os.path.join(self.dir, "pipe")
            os.mkfifo(fifo)
            self.assertIsNone(digests.digest(fifo), "a FIFO is not a file")

    def test_the_digest_is_the_sha256_of_the_bytes(self):
        data = b"\x00\x01binary\r\nno newline translation\r\n" * 1000
        path = _write(os.path.join(self.dir, "part.stl"), data)
        self.assertEqual(FileDigests().digest(path), _sha(data))

    # -- the cache is a speed-up, never a failure --------------------------- #
    def test_a_corrupt_cache_is_ignored_and_rebuilt(self):
        data = b"k = 1\n"
        path = _write(os.path.join(self.dir, "k.txt"), data)
        sha_bad = {"schema": 1, "files": {os.path.abspath(path): [1, 2, 3, 4, 5, "zz"]}}
        for garbage in (b"\xff\xfe not json", b"[]", b'{"schema": 99, "files": {}}',
                        json.dumps(sha_bad).encode(), b'{"schema": 1, "files": {"x": NaN}}'):
            with self.subTest(garbage=garbage[:30]):
                _write(self.cache, garbage)
                digests = FileDigests(self.cache)
                self.assertEqual(digests.digest(path), _sha(data))
                self.assertTrue(digests.save())
                self.assertIn(os.path.abspath(path), self._rows())

    def test_save_is_best_effort(self):
        blocker = _write(os.path.join(self.dir, "a-file"), b"")
        digests = FileDigests(os.path.join(blocker, "cache", "digests.json"))
        path = _write(os.path.join(self.dir, "x.txt"), b"x\n")
        self.assertEqual(digests.digest(path), _sha(b"x\n"))
        self.assertFalse(digests.save(), "a cache under a regular file cannot be written")
        self.assertEqual(digests.digest(path), _sha(b"x\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
