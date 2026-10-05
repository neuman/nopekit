# SPDX-License-Identifier: Apache-2.0
"""Bundled domain packs.

This file exists only so the packs ship inside the wheel. Packs themselves are
ordinary directories — no build step, no registration — and `pyproject.toml` maps
this directory onto the importable name `nopekit.bundled` so that
`pip install nopekit` gets the domains as well as the spine. Without it a pip
user got a working spine and zero gates, which is a spine that cannot do anything.

Do not import from here. `nopekit.packs.search_paths()` locates these on disk.
"""
