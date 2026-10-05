# SPDX-License-Identifier: Apache-2.0
"""`python -m nopekit` — the same entry point as the `nopekit` script.

Two spellings of one command, and they must not diverge: `pyproject.toml` points
the console script at `nopekit.cli:main`, and this module calls the same
function. Anything more here (argument massaging, a second parser) would be a
second command surface that drifts from the first.

`raise SystemExit(main())` rather than `sys.exit` inside a `try` so the exit code
is exactly what `main` returned — 0, 1 for a blocking verdict, 2 for a user
error. That code is the product for `nopekit check` in CI.
"""
from __future__ import annotations

from .cli import main

raise SystemExit(main())
