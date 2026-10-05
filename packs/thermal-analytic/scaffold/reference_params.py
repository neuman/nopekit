# SPDX-License-Identifier: Apache-2.0
"""The starter projection for a thermal-analytic model — LOADED, not typed.

``PARAMS`` is ``selftest/baseline.json`` with its documentation keys stripped. It
is a view, not a copy, and that is the entire content of this file.

**Why it no longer holds its own numbers.** It used to: a hand-written dict of a
different installation, a different wall, a different collector. Two source files
then described "the reference set" and they drifted, exactly the way rule 1 says
they will. The drift was already shipping in output nobody re-derived — the
negative-control notes quoted "U_L 4.2 -> 12.6 in the reference set" while the
baseline's U_L was 4.5, `references/collector-example.md` opened by promising
every number in it came from this file, and nothing in ``src/nopekit`` had ever
read it. A scaffold that disagrees with the projection CI actually runs is worse
than no scaffold, because it is the file a new author copies from.

So there is one projection in this pack now, ``selftest/baseline.json``, and it
is the one the gates are verified against on every run. It carries a
``_description`` naming the installation and a ``_notes`` entry for every key
saying what the value is, its unit and why it is that number — read that file, not
this one, when you want to know what a key means.

Usage::

    from reference_params import PARAMS, NOTES, DESCRIPTION
    params = dict(PARAMS)              # then edit for your own design

Copy the keys your project owns and delete the rest. A gate whose keys are absent
SKIPs and its claim goes BLOCKED; that is designed behaviour, not a failure, and
it is better than a default invented on your model's behalf.
"""
from __future__ import annotations

import json
import os

_BASELINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "selftest", "baseline.json")

with open(_BASELINE, "r", encoding="utf-8") as _handle:
    _RAW: dict = json.load(_handle)

#: One line naming the installation every number below belongs to.
DESCRIPTION: str = _RAW.get("_description", "")

#: key -> what it is, its unit, and why it holds that value.
NOTES: dict = _RAW.get("_notes", {})

#: The projection itself: every key any gate in this pack reads, describing a
#: design that every gate passes.
PARAMS: dict = {k: v for k, v in _RAW.items() if not k.startswith("_")}


if __name__ == "__main__":                                  # pragma: no cover
    print(DESCRIPTION, "\n")
    for _key, _value in PARAMS.items():
        print(f"{_key:32} {_value!r:>28}   {NOTES.get(_key, '')[:90]}")
