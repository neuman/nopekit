#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The reference project's failure, read back from its JUnit report and compared.

    python tests/oracle/bracket_signature.py <junit.xml> <expected.json> --exit-code N

`examples/bracket` fails on purpose: at thickness 7.0 the tip sags 0.70 mm
against a 0.5 mm limit, and C7 has no gate at all, so `atompipe check` exits 1
in every correct build. What slipped through (S-83): CI ran `check || true`,
and `|| true` cannot tell that one intended failure from a crash (exit 2), from
a second failure, or from the intended failure quietly going away — all four
read green. So CI now hands this script the exit code it captured and the
report `check --junit` wrote, and the pair must equal one pinned signature,
`tests/expected_bracket.json` (PLAN G4). A phase that changes the signature on
purpose changes that file in the same commit and says why.

The signature, derived from the XML:

    {"exit_code": N,
     "gates":           {"fail": [ids], "error": [ids], "not-admitted": [ids],
                         "skipped": [ids]},
     "claims.critical": {"fail": {id: failure type}, "error": {id: error type},
                         "skipped": {id: skip message}},
     ...one entry per testsuite, claims suites keyed by claim}

A passing testcase is childless and appears nowhere. A category or a suite the
expectation leaves out means "none", never "anything": `claims.critical` pins
no `error` key, and a claim that errors is still a difference. The root's
`exit_code` property must also equal `--exit-code`: a report that disagrees
with the process it came from is itself the drift the JUnit renderer exists to
prevent.

Exit codes: 0 the signature matches; 1 it differs (each difference named, then
the derived signature, so an intended change can be pinned by copying it);
2 there is nothing to compare — no report (what a crash leaves, since `--junit`
removes the old file before the run), or a report or expectation that does not
parse. 2 is never 0: a missing report must not read as a match.

Standard library only: CI installs nothing, and this runs there. It lives under
`tests/oracle/` with no `__init__.py` and no `test` prefix, so unittest
discovery never collects it; `tests/test_ci_config.py` runs it.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

MATCHES, DIFFERS, UNREADABLE = 0, 1, 2


def derive(root: Any, exit_code: int) -> dict[str, Any]:
    """The signature of the report whose root element is ``root``."""
    shape: dict[str, Any] = {"exit_code": exit_code}
    for suite in root.iter("testsuite"):
        name = suite.get("name") or ""
        keyed = name.startswith("claims.")
        categories = shape.setdefault(name, {})
        for case in suite.findall("testcase"):       # its own, not a nested suite's
            case_id = case.get("name") or ""
            for child in case:
                if child.tag == "failure":
                    category, why = "fail", child.get("type") or ""
                elif child.tag == "error":
                    why = child.get("type") or "error"
                    category = "not-admitted" if why == "not-admitted" and not keyed else "error"
                elif child.tag == "skipped":
                    category, why = "skipped", child.get("message") or ""
                else:
                    continue                    # <properties>, <system-out>: not outcomes
                if keyed:
                    categories.setdefault(category, {})[case_id] = why
                else:
                    categories.setdefault(category, []).append(case_id)
    return shape


#: The categories the pinned file always spells out, empty or not, in its order.
#: Every other suite and category is printed only when it holds something.
_PINNED_FORM = {
    "gates": ("fail", "error", "not-admitted", "skipped"),
    "claims.critical": ("fail", "skipped"),
}


def pinnable(derived: dict[str, Any]) -> dict[str, Any]:
    """``derived`` in the pinned file's own shape and order, ready to copy in."""
    out: dict[str, Any] = {"exit_code": derived.get("exit_code")}
    for suite, form in _PINNED_FORM.items():
        got = derived.get(suite) or {}
        keyed = suite.startswith("claims.")
        block: dict[str, Any] = {}
        for category in list(form) + sorted(set(got) - set(form)):
            value = got.get(category, {} if keyed else [])
            block[category] = dict(sorted(value.items())) if keyed else sorted(value)
        out[suite] = block
    for suite in sorted(set(derived) - set(out)):
        if derived[suite]:
            out[suite] = derived[suite]
    return out


def _exit_property(root: Any) -> str | None:
    for props in root.findall("properties"):
        for prop in props.findall("property"):
            if prop.get("name") == "exit_code":
                return prop.get("value")
    return None


def differences(derived: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """Every way ``derived`` differs from ``expected``; empty when they agree."""
    out: list[str] = []
    if derived.get("exit_code") != expected.get("exit_code"):
        out.append(f"exit_code: expected {expected.get('exit_code')!r}, "
                   f"the process exited {derived.get('exit_code')!r}")
    for suite in sorted((set(derived) | set(expected)) - {"exit_code"}):
        got, want = derived.get(suite) or {}, expected.get(suite) or {}
        if not isinstance(want, dict):
            out.append(f"{suite}: the expectation is not an object: {want!r}")
            continue
        for category in sorted(set(got) | set(want)):
            out += _compare(f"{suite}.{category}", got.get(category), want.get(category))
    return out


def _compare(label: str, got: Any, want: Any) -> list[str]:
    if got is None:
        got = {} if isinstance(want, dict) else []
    if want is None:
        want = {} if isinstance(got, dict) else []
    if isinstance(got, dict) != isinstance(want, dict):
        return [f"{label}: expected {want!r}, the report gives {got!r}"]
    out: list[str] = []
    if isinstance(want, dict):
        for key in sorted(set(got) | set(want)):
            if key not in got:
                out.append(f"{label}: {key} expected ({want[key]!r}), not in the report")
            elif key not in want:
                out.append(f"{label}: {key} ({got[key]!r}) in the report, not expected")
            elif got[key] != want[key]:
                out.append(f"{label}: {key} expected {want[key]!r}, the report gives {got[key]!r}")
        return out
    for item in sorted(set(want) - set(got)):
        out.append(f"{label}: {item} expected, not in the report")
    for item in sorted(set(got) - set(want)):
        out.append(f"{label}: {item} in the report, not expected")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bracket_signature.py",
        description="Compare a `check --junit` report and its exit code with the "
                    "pinned signature. 0 same, 1 differs, 2 nothing to compare.")
    parser.add_argument("junit", help="the report `atompipe check --junit` wrote")
    parser.add_argument("expected", help="the pinned signature, tests/expected_bracket.json")
    parser.add_argument("--exit-code", type=int, required=True,
                        help="the exit code `atompipe check` returned")
    args = parser.parse_args(argv)

    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(args.junit).getroot()
    except FileNotFoundError:
        print(f"bracket signature: no report at {args.junit} (the command exited "
              f"{args.exit_code}). `--junit` removes the old report before the run, so "
              f"this is what a run that ended early leaves — nothing to compare, and "
              f"never a match", file=sys.stderr)
        return UNREADABLE
    except (OSError, ET.ParseError) as exc:
        print(f"bracket signature: {args.junit} does not parse: {exc}", file=sys.stderr)
        return UNREADABLE
    try:
        with open(args.expected, "r", encoding="utf-8") as fh:
            expected = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"bracket signature: {args.expected} does not load: {exc}", file=sys.stderr)
        return UNREADABLE
    if not isinstance(expected, dict):
        print(f"bracket signature: {args.expected} is not a JSON object", file=sys.stderr)
        return UNREADABLE

    derived = derive(root, args.exit_code)
    found = differences(derived, expected)
    recorded = _exit_property(root)
    if recorded != str(args.exit_code):
        found.insert(0, f"exit_code: the report records {recorded!r}, the process exited "
                        f"{args.exit_code} — the file and the job disagree")
    if not found:
        print(f"bracket signature: matches {args.expected} (exit {args.exit_code})")
        return MATCHES
    print(f"bracket signature: DIFFERS from {args.expected}")
    for line in found:
        print(f"  {line}")
    print("derived signature (pin it only if the change is intended, and say why):")
    print(json.dumps(pinnable(derived), indent=2))
    return DIFFERS


if __name__ == "__main__":
    sys.exit(main())
