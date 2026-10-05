# SPDX-License-Identifier: Apache-2.0
"""Every JSON file the spine writes is JSON (S-47).

Python's ``json`` writes ``NaN`` and ``Infinity`` as bare tokens by default. No
JSON parser accepts them: ``JSON.parse`` refuses the whole file. A could-not-measure
number reached ``state.json`` that way, the page refused to load, and the message
it showed advised ``atompipe site build`` — which wrote the same NaN again. The
fix is at the one writer every spine JSON file passes through,
``util.atomic_write_json``, and these tests hold it there.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from atompipe.util import AtompipeError, atomic_write_json


class StrictJson(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="atompipe-strict-json-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "state.json")

    def test_nan_write_raises_naming_the_path(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(AtompipeError) as caught:
                    atomic_write_json(self.path, {"verdicts": [{"measured": bad}]})
                message = str(caught.exception)
                self.assertIn(self.path, message)
                self.assertIn("NaN or Infinity", message)
                # Serialised before the file is touched: nothing written, no
                # temp file left behind next to it.
                self.assertEqual(os.listdir(self.dir), [])

    def test_a_refused_write_leaves_the_previous_file_intact(self):
        atomic_write_json(self.path, {"measured": 0.7})
        with self.assertRaises(AtompipeError):
            atomic_write_json(self.path, {"measured": float("nan")})
        with open(self.path, "r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"measured": 0.7})

    def test_finite_numbers_still_write(self):
        """The positive control: the strict writer refuses non-finite numbers,
        not numbers."""
        payload = {"measured": 0.6997, "limit": 0.5, "count": 3, "huge": 1e308, "none": None}
        atomic_write_json(self.path, payload)
        with open(self.path, "r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), payload)

    def test_an_unserialisable_value_keeps_its_own_message(self):
        """A NaN is named as a NaN; anything else that will not encode is still
        reported as not serialisable, so the message never sends a user hunting
        for a NaN that is not there."""
        with self.assertRaises(AtompipeError) as caught:
            atomic_write_json(self.path, {"x": object()})
        message = str(caught.exception)
        self.assertIn(self.path, message)
        self.assertIn("not JSON-serialisable", message)
        self.assertNotIn("NaN", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
