# SPDX-License-Identifier: Apache-2.0
"""Rebuild ``ansur1988_hand.csv`` from the two public ANSUR 1988 files, byte for byte.

    python3 derive_hand_table.py <dir> [--out PATH] [--check]

``<dir>`` holds ``ansurMen.csv`` and ``ansurWomen.csv`` as
https://www.openlab.psu.edu/ansur/ serves them (the links point at
``https://tools.openlab.psu.edu/publicData/``). Each file must hash to the sha256
that ``PROVENANCE.json`` beside this script records, or the script refuses, names
the file and both hashes, and writes nothing. A file that differs is another
survey, or this one edited, and a table built from it would carry this
provenance while holding other numbers.

What it does, and nothing else:

1. Reads each file as UTF-8 with its byte-order mark (``utf-8-sig``).
2. Keeps seven measurements of each subject, by the column names that
   ``PROVENANCE.json`` maps (``columns``), and its ``SUBJECT_NUMBER``. Every value is
   an integer in millimetres in the public files; it is written as it is read.
3. Writes the women first, then the men, each in the order of its file, with
   ``sex`` as ``F`` or ``M``, one header line, ``\\n`` line ends.

``--check`` builds the table in memory and compares it with the shipped file:
exit 0 when the bytes are equal, 1 when they differ. Standard library only, so
the table can be rebuilt on any machine that has Python.

What slipped through before a script existed (the design round of this pack):
a table typed or pasted from a percentile page cannot be traced to a subject,
and a percentile table cannot answer the question this pack asks, which is how
many people reach every control at once. The gate counts subjects. So the
subjects themselves ship, and this script is the proof of where each row came
from.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROVENANCE = os.path.join(HERE, "PROVENANCE.json")
TABLE = os.path.join(HERE, "ansur1988_hand.csv")

#: The order of the shipped columns. ``sex`` and ``subject`` first, then the
#: seven measurements in the order ``PROVENANCE.json`` lists them.
LEAD = ("sex", "subject")


def _provenance() -> dict:
    with open(PROVENANCE, encoding="utf-8") as fh:
        return json.load(fh)


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def derive(source_dir: str, provenance: dict | None = None) -> bytes:
    """The table's bytes from the two public files in ``source_dir``.

    Raises ``ValueError`` naming the file and both hashes when a file does not
    hash to the recorded sha256, and naming the column when a mapped column is
    missing or a value is not a positive integer."""
    prov = provenance if provenance is not None else _provenance()
    columns = prov["columns"]
    names = list(columns)
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([*LEAD, *names])
    for source in prov["sources"]:
        path = os.path.join(source_dir, source["file"])
        got = _sha256(path)
        if got != source["sha256"]:
            raise ValueError(f"{source['file']}: sha256 {got} is not the recorded "
                             f"{source['sha256']}; this is not the file the table was "
                             f"derived from, and nothing was written")
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        for name in names:
            column = columns[name]["source_column"]
            if rows and column not in rows[0]:
                raise ValueError(f"{source['file']}: no column {column!r} (for {name})")
        for row in rows:
            values = []
            for name in names:
                raw = row[columns[name]["source_column"]].strip()
                if not raw.isdigit() or int(raw) <= 0:
                    raise ValueError(f"{source['file']}: subject {row['SUBJECT_NUMBER']}: "
                                     f"{name} is {raw!r}, not a positive integer in mm")
                values.append(int(raw))
            writer.writerow([source["sex"], int(row["SUBJECT_NUMBER"]), *values])
    return out.getvalue().encode("utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source_dir", help="the directory that holds ansurMen.csv and "
                                           "ansurWomen.csv")
    parser.add_argument("--out", default=TABLE, help="where to write the table "
                                                     "(default: beside this script)")
    parser.add_argument("--check", action="store_true",
                        help="compare with the shipped table; write nothing")
    args = parser.parse_args(argv)
    try:
        data = derive(args.source_dir)
    except (OSError, ValueError, KeyError) as exc:
        print(f"derive_hand_table: {exc}", file=sys.stderr)
        return 2
    if args.check:
        with open(TABLE, "rb") as fh:
            same = fh.read() == data
        print("the shipped table is the derived one" if same
              else "the shipped table differs from the derived one")
        return 0 if same else 1
    with open(args.out, "wb") as fh:
        fh.write(data)
    print(f"wrote {args.out}: sha256 {hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
