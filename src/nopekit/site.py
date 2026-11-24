# SPDX-License-Identifier: Apache-2.0
"""nopekit.site — the ledger, rendered and addressable.

The site has two jobs and the second one is why it exists:

1. **Explain the thing** to someone who did not build it.
2. **Debug the thing** — spin it, pull it apart, and see the latest gate results
   *anchored to the geometry they are about*.

A readiness report says ``cad.clash: 1 interfering pair — carriage /
guide_roller, 0.41 mm^3``. That is a sentence somebody has to go and act on.
The same verdict carrying two :class:`~nopekit.models.Locator` records lights
both parts up in the viewer, and the reader is looking at the problem a second
later. Everything in this module exists to keep that link intact from the gate
that measured the overlap to the pixel that turns red.

Five rules are mechanical here, not advisory:

1. **The site never computes truth.** It renders the ledger. Nothing in this
   module runs a gate, re-derives a measurement, or decides whether a claim
   passes — :mod:`nopekit.claims` and :mod:`nopekit.report` do that, and
   :func:`state` calls *them* rather than reimplementing the judgement. A page
   that reached its own verdict would be a second opinion published next to the
   first, and a reader has no way to tell which one is the design's actual
   status. If a number on the page is wrong, the ledger is wrong (rule 1 of the
   method: generated files are outputs, not sources).

2. **:func:`build` does not run gates.** It renders what
   :func:`nopekit.verdicts.resolve` makes of the verdict cache — which never
   runs a gate either — and stamps each verdict with its own age. A build that
   helpfully re-ran the cheap gates and not the expensive ones would publish a
   mixed-age picture under one timestamp — the tier-0 numbers from ten seconds
   ago beside the tier-2 numbers from last Tuesday, with nothing on the page
   saying which is which. Age is per gate for the same reason, and so is
   staleness: each verdict is current or not by its OWN inputs.

   What slipped through (S-28): this module kept its own copy of the staleness
   rule and of ``_flat_params``, "byte-for-byte" the CLI's, synced by a
   comment, and judged the whole page by one hash of the projection against
   the last sweep's. A page handed no resolution now asks the resolver itself;
   there is no generous default that lists recorded entries as current, which
   would serve every cached PASS whether or not its control was ever
   demonstrated (invariant 7 by omission).

3. **A locator that cannot be drawn is REPORTED, never dropped.** A gate that
   thinks it is drawing and is not looks exactly like a gate that found nothing,
   and the failure mode is silent on both ends: the pack author sees a passing
   import and the reader sees an unannotated verdict. :func:`locator_problems`
   names every locator addressing a view that does not exist or a node the view
   does not declare, and the problem list rides in ``state.json`` as well as in
   the build summary, so it is visible to the page and to ``curl`` alike.

4. **It degrades all the way down.** A project with no geometry — a chemical
   process, a supply chain, a bracket with nothing exported yet — still gets
   claims, verdicts, evidence, provenance and the readiness sentence. 3D is one
   view kind among six, not the point. A viewgen with nothing to draw returns
   ``None`` and that is a normal outcome, not an error.

5. **Extensibility lives in the data, not in shipped code.** A pack emits views
   in kinds the renderer already knows. Nothing here loads pack JavaScript: the
   one artifact whose entire job is to be trusted when a gate says something is
   wrong must not depend on code nobody reviewed.

The structure deliberately mirrors :mod:`nopekit.gates` — :class:`ViewContext`
for :class:`~nopekit.gates.GateContext`, :class:`ViewRegistry` for
:class:`~nopekit.gates.Registry`, :func:`viewgen` for
:func:`~nopekit.gates.gate`, :func:`run_all_viewgens` for
:func:`~nopekit.gates.run_all`. A pack author who has written a gate should
have to learn nothing to write a viewgen. The one asymmetry is deliberate and
is called out at :meth:`ViewRegistry.register`: a viewgen declares no negative
control, because it settles nothing and there is no claim to falsify.

Time policy (contract rule 3): nothing here reads the clock. ``now`` arrives
from the CLI edge as an ISO string; a verdict's ``when`` is the resolver's — the
obs run that last hit or wrote its entry, else the entry's commit time — and an
*absent* ``now`` or ``when`` produces ``age_s: null`` rather than a zero. A zero
age renders as "just now", which is precisely the lie a staleness display exists
to prevent.

No build step, ever. ``site/`` is plain HTML, CSS and ES modules;
``nopekit site build`` writes JSON and ``nopekit site serve`` is
``python3 -m http.server``. A project site that needs npm is a project site that
rots, and the spine is standard-library only for exactly the same reason.
"""
from __future__ import annotations

import contextlib
import csv
import dataclasses
import fnmatch
import hashlib
import importlib.util
import json
import math
import os
import posixpath
import re
import shutil
import struct
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, NamedTuple

from . import claims as claim_logic
from . import decisions as decision_logic
from . import derivation as trace_logic
from . import graph as graph_logic
from . import modelio
from . import page as page_logic
from . import prose as prose_logic
from . import report as report_logic
from . import store
from . import vcs
from . import verdicts as verdict_logic
from .gates import availability as _gate_availability
from .models import Acceptance, Ledger, Locator, Verdict, View, ViewKind
from .util import (
    NopekitError,
    atomic_write_json,
    atomic_write_text,
    ensure_dir,
    rel,
    sha256_text,
    short_hash,
    write_json_if_changed,
)

__all__ = [
    "SITE_DIR",
    "DATA_DIR",
    "ASSETS_DIR",
    "VENDOR_DIR",
    "VIEWS_DIR",
    "STATE_NAME",
    "ViewContext",
    "ViewSpec",
    "ViewRegistry",
    "VIEW_REGISTRY",
    "viewgen",
    "active_view_registry",
    "use_view_registry",
    "load_viewgens",
    "view_tool_rows",
    "availability",
    "run_viewgen",
    "run_all_viewgens",
    "derive_explode",
    "locator_problems",
    "scaffold",
    "build",
    "state",
    "resolution_digest",
    "resolution_moved",
    "clean_assets",
    "vendor_urls",
    # architecture phase 5: the front page, the clock file, the watcher
    "PAGE_NAME", "MAP_NAME", "BUILD_NAME", "CONTENT_DIR", "EVIDENCE_DIR", "SHELLS",
    "OUTLINE_MARKER", "OUTLINE_MARKER_RE", "AGED_SECONDS", "WATCH_DIRS",
    "scaffolded", "outline_of", "ensure_site_ignore", "prose_pool", "baked_pool", "ages",
    "write_build_json",
    "watch_snapshot", "refresh_renderer",
    # the two pages: the engineering and marketing pages
    "PAGES", "VIEWS_MARKER", "VIEWS_MARKER_RE", "OLD_LAYOUT_WARNING", "outline_file",
    "outline_problem", "old_layout", "page_urls", "pages_line",
    # issue #15: viewgens bound to a verdict entry
    "BoundEntry", "BoundContext", "FIGURE_SCRATCH", "FIGURE_RECIPE_SALT", "BOUND_KINDS",
    "CHART_FORBIDDEN_KEYS", "CHART_NUMBER_KEYS", "CAPTION_STATUS_PHRASES",
]


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #
#: The site lives at ``<root>/site/``. Not hidden and not under ``.nopekit/``:
#: users edit the pages (`SHELLS`), and a directory people are invited to hack on does
#: not belong in the tool's private state.
SITE_DIR = "site"

#: Generated JSON, relative to ``site/``. Hand-editing anything under here is
#: editing an output (method rule 1); ``build`` overwrites it without asking.
DATA_DIR = "data"

#: Generated binaries a viewgen wrote: GLB, PNG, SVG, CSV. Relative to ``site/``.
ASSETS_DIR = "assets"

#: Where ``nopekit site vendor`` puts three.js for offline use. Gitignored by
#: default — it is a third-party copy, it is large, and the site works without it.
VENDOR_DIR = "vendor"

#: Per-view payloads too big to inline, under ``site/data/views/<id>.json``.
VIEWS_DIR = posixpath.join(DATA_DIR, "views")

#: The technical record's document (`technical.html` reads it whole).
STATE_NAME = "state.json"

#: The front page's two documents (architecture phase 5, SPEC 28-30): the
#: outline around the map, and the map. Baked by `page`, tracked.
PAGE_NAME = "page.json"
MAP_NAME = "map.json"

#: The clock (SPEC 32): the build time, each verdict's age and the live flag.
#: Ignored by git through `site/.gitignore`, so a rebuild that judged nothing new
#: changes no tracked byte; the page works without it.
BUILD_NAME = "build.json"

#: The agent's prose and figures (SPEC 30), under ``site/``: tracked, stamped by
#: `nopekit site stamp`, and not a record.
CONTENT_DIR = "content"

#: Evidence the evaluators wrote, copied under ``site/`` so a published page can
#: link it (`page.evidence_plan`).
EVIDENCE_DIR = "assets/evidence"

#: The shells: the project's own files, written by `site init` (and every one
#: but the root `index.html` by `site build` when absent), never refreshed.
#: PETGasus ships its own `technical.html`, a renamed older shell with its own
#: title: a refresh would erase it. *Rejected:* refreshing `technical.html` as a
#: renderer file.
#:
#: The two pages: the root `index.html` is a redirect to `engineering/`, the
#: engineering page (the outline, SPEC 28) is `engineering/index.html` and the
#: marketing page `marketing/index.html`, each a directory's index so the
#: addresses `/engineering` and `/marketing` work on every static host. A
#: project whose root page predates them keeps it: `build` writes the two pages
#: beside it and never writes the root (`old_layout`). What slipped through the
#: plan: the build's shell loop had no `ensure_dir`, so a nested shell would
#: have raised on every existing project; and a page left out of this tuple is
#: a renderer file, overwritten on every build. *Rejected:* flat
#: `engineering.html` / `marketing.html` (the standard library's server, and
#: `site serve`, answer `/engineering` with a 404); the engineering page at `/`
#: with `/engineering` a redirect to it (the address the person asked for would
#: be an alias); a router in one page (a reload at `/marketing` 404s on a static
#: host with no rewrite rules).
SHELLS = ("index.html", "engineering/index.html", "marketing/index.html", "technical.html")

#: The two pages, ``(name, path under site/)``, in the menu's order, the
#: engineering page first: the default the root redirects to.
PAGES: tuple[tuple[str, str], ...] = (("engineering", "engineering/"),
                                      ("marketing", "marketing/"))

#: The outline marker the new shell carries (SPEC 28): `site build` warns when
#: the engineering page (`outline_file`) lacks it, or carries an older outline.
#: A meta tag, so a page written by hand can carry it too.
OUTLINE_MARKER = '<meta name="nopekit-outline" content="1">'
OUTLINE_MARKER_RE = r'<meta\s+name=["\']nopekit-outline["\']\s+content=["\'](\d+)["\']'

#: The marker the root redirect carries (the two pages): a root `index.html`
#: without it is a front page from before them, and the build, `site status` and
#: `doctor` say so (`old_layout`). A meta tag for the outline marker's reason.
VIEWS_MARKER = '<meta name="nopekit-views" content="1">'
VIEWS_MARKER_RE = r'<meta\s+name=["\']nopekit-views["\']\s+content=["\'](\d+)["\']'

#: A verdict this old is *aged* on the page (`build.json`'s ``aged``), and an
#: oldest result past it beside one four times newer raises the mixed-age
#: banner. Moved from the template's format.js (SPEC 32: the browser computes no
#: status): a day, because a check run once a day is the slowest loop a designer
#: keeps up with, and a result older than that is from another working session.
#: *Rejected:* an hour (every lunch break would age the page); a week (a result
#: from last Tuesday would read as fresh as this morning's).
AGED_SECONDS = 24 * 3600

#: What `nopekit site serve --watch` polls (SPEC 33): the records, the project
#: file, the model and the project's evaluators, views and generators, the packs
#: copied in, the verdict cache, the trace, and the page's own text and
#: authored pins. `__pycache__` and `*.pyc` are left out. And the evidence
#: manifests (issue #15): a `check --force` that solves an entry again writes
#: a new manifest and no new entry file, and that is the run a bound figure
#: can now be drawn from, so the watcher must wake on it.
WATCH_DIRS: tuple[str, ...] = (*store.RECORD_DIRS, ".nopekit/project.json", "model", "gates",
                               "selftest", "views", "generators", ".nopekit/packs",
                               ".nopekit/verdicts", ".nopekit/trace", ".nopekit/cache/evidence",
                               "site/content", "site/annotations.json", "site/explode.json")

#: Where a bound viewgen writes (issue #15, B.1.3), relative to the project:
#: ``<gate>/<entry>/<view>/`` under ``.nopekit/out/``, which git ignores. The
#: spine checks what lands here and only then publishes it under
#: ``site/assets/figures/`` (`page.FIGURES_DIR`). *Rejected:* ``site/`` (a file
#: is published before it is checked); ``.nopekit/cache/`` (a gate never
#: writes there, and scratch a viewgen fills is not a cache).
FIGURE_SCRATCH = ".nopekit/out/figures"

#: The salt of a bound viewgen's recipe digest (`_recipe_digest`), the memo's
#: key: a new salt draws every figure again, which is what a change to how a
#: figure is checked or published needs.
FIGURE_RECIPE_SALT = "nopekit-figure-v1"

#: The kinds a bound viewgen draws: a still (``image``, a PNG), a chart (its
#: series as its payload) and a 3D field (``model3d`` or ``field``, a GLB or
#: a self-contained glTF: `SOLID_SUFFIXES`).
#: *Rejected:* ``table`` (rows are a gate's evidence already, linked as such).
BOUND_KINDS = (ViewKind.IMAGE, ViewKind.CHART, ViewKind.MODEL3D, ViewKind.FIELD)

#: A bound chart may hold none of these keys, anywhere (B.2.3): a chart's
#: limit and its current point are the claim's, set by nopekit from the floor
#: (P1.4). What slipped through before this: `_chart_margins` took a chart's
#: margin from the view's own limit, so a pack limit beside the claim's was a
#: second limit on the page. *Rejected:* stripping such a key silently (a pack
#: bug would read as a working chart).
#: ``markers`` too, since an unbound view's ``markers`` became its point
#: (review of P2, `page.CHART_CURRENT_KEYS`).
CHART_FORBIDDEN_KEYS = frozenset({"limit", "limits", "current", "marker", "now", "markers"})

#: The parts of a bound chart whose every number must be one a verified
#: evidence file of the entry holds (a JSON leaf or a CSV cell): a chart draws
#: what the run wrote, never a number its viewgen computed. *Rejected:* every
#: number of the payload (axis bounds and tick steps are presentation).
CHART_NUMBER_KEYS = ("series", "band", "estimates", "rule")

#: Phrases a bound figure's caption may not hold: `page.CAPTION_STATUS_PHRASES`,
#: where the build's refusal and the page's checker (G7) read one rule.
CAPTION_STATUS_PHRASES = page_logic.CAPTION_STATUS_PHRASES

#: The bytes a PNG and a GLB start with (B.2.3's format check, stdlib).
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
GLB_MAGIC = b"glTF"

#: The 3D files a bound figure may write: a GLB, or a glTF 2.0 JSON whose
#: every buffer and image is a ``data:`` URI (`_solid_problem`). three.js's
#: GLTFLoader reads both. Issue #15, P2.4, under the author's rule of
#: 2026-10-21 (use the trusted tool's output, build as little as possible):
#: ParaView 6.2 writes glTF and no GLB (``ExportView`` with ``InlineData``),
#: and its faceted export passes the Khronos validator as it is (measured;
#: `tests/test_cfd_views.py`), so the page takes ParaView's file unchanged.
#: What a self-contained glTF closes: one that names a ``.bin`` or a texture
#: beside it would make the page fetch a file the spine never saw, hashed or
#: published. *Rejected:* a GLB repacker in the pack (the design's
#: ``_gltf.py``: a codec of our own between ParaView and three.js); trimesh
#: to convert (a second tool that rewrites the colours ParaView wrote, for
#: 25% fewer bytes the 2 MiB cap did not need: 940 KB for the baseline's 8000
#: cells); checking accessors here (the Khronos validator does that, in the
#: tests, where node is).
SOLID_SUFFIXES = (".glb", ".gltf")

#: The line `ensure_site_ignore` adds once a bound viewgen is registered: the
#: live 3D files of entries no export re-ran (`page.LIVE_DIR`) are on one
#: computer only (B.2.4).
_LIVE_IGNORE = (
    "# `site build` writes the 3D view of a result no export re-ran here. It is\n"
    "# on this computer only, so git ignores it.\n"
    "assets/live/\n"
)

#: The scaffold source, shipped inside the package (see pyproject package-data).
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_template")

#: A view payload larger than this is written to ``data/views/<id>.json`` instead
#: of being inlined in ``state.json``. The threshold exists because ``state.json``
#: is meant to be read whole — by the page in one fetch, and by an agent with
#: `curl` and no browser. One field-sample table of 40,000 rows inlined would
#: make the file that answers "what is the status of this project?" unreadable
#: for the 99% of readers who did not want the table.
VIEW_INLINE_MAX = 8192

#: The suffix of a chart view's spec split out of ``state.json`` (P2.5):
#: ``data/views/<id>~chart.json``, beside its payload's ``<id>.json``, named
#: in its row as ``chart_url``. ``~`` is no character of a view id
#: (`_ID_OK`), so no view's payload file can be another view's chart file.
#: *Rejected:* ``.chart.json`` (the view ``a.chart``'s payload would be the
#: view ``a``'s chart); a folder of its own (a second folder to clean).
CHART_FILE_SUFFIX = "~chart.json"

#: Characters allowed in a view id. Ids become filenames (``data/views/<id>.json``)
#: and query fragments, and a locator addresses a view by this string, so a slash
#: or a space in one is a path escape and a broken anchor at the same time.
_ID_OK = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
)

#: The lines `ensure_site_ignore` appends to an older `site/.gitignore`
#: (architecture phase 5, SPEC 32): the clock file is an output that changes on
#: every build, so a tracked copy would dirty `git status` after every build.
_BUILD_IGNORE = (
    "# `site build` writes the build time and each result's age here. It changes on\n"
    "# every build, so git ignores it. The page works without it.\n"
    "data/build.json\n"
)

#: Written by :func:`scaffold` when the site has no ``.gitignore`` of its own.
#: ``data/`` and ``assets/`` are deliberately NOT ignored: they are the published
#: site, and a repo that ignores them publishes an empty page. ``vendor/`` is,
#: and from phase 5 ``data/build.json``.
_GITIGNORE = (
    "# `nopekit site vendor` writes three.js here. It is a third-party copy,\n"
    "# it is large, and the site loads the same version from a CDN without it —\n"
    "# so it is not ours to commit. Delete the vendor/ line (and only that line)\n"
    "# if you want an offline, self-contained archive of the project in git.\n"
    "vendor/\n"
    "\n" + _BUILD_IGNORE
)


def _noop_log(_message: str) -> None:
    """Default ``ViewContext.log``: swallow it.

    Same reasoning as ``gates._noop_log``: a viewgen must be runnable from a
    test or a pack's own ``__main__`` with no CLI attached, and defaulting to
    ``print`` would spray a ``--json`` CLI with prose.
    """


# --------------------------------------------------------------------------- #
# the context a viewgen is handed
# --------------------------------------------------------------------------- #
@dataclass
class ViewContext:
    """Everything a viewgen is allowed to see. One argument, mirroring GateContext.

    The field list is deliberately the same shape as
    :class:`~nopekit.gates.GateContext` — ``root``, ``ledger``, ``model``,
    ``params``, a place to write, ``log``, ``extra`` — because a pack author
    writing ``views/assembly.py`` has already written ``gates/clash.py`` and
    should not have to learn a second API to draw what the first one measured.
    Where gates get ``out_dir`` (evidence), viewgens get ``assets_dir``
    (published output); where gates get ``tier`` (how expensive a sweep this
    is), viewgens get nothing equivalent, because drawing is not tiered.

    Every field has a default so a test can build a context with the one thing
    it cares about.

    ``written`` is not part of the constructor: it is the record of what
    :meth:`write_asset` actually wrote, and :func:`build` reads it to decide
    what in ``site/assets/`` is still live. A viewgen cannot seed it, because a
    viewgen claiming to have written a file it did not write would make
    :func:`clean_assets` preserve a phantom.
    """

    root: str = ""
    ledger: Ledger = field(default_factory=Ledger)
    model: Any | None = None
    params: dict[str, Any] = field(default_factory=dict)
    assets_dir: str = ""
    log: Callable[[str], None] = _noop_log
    extra: dict[str, Any] = field(default_factory=dict)

    #: Site-relative paths this context has written, in order. Populated only by
    #: :meth:`write_asset`; see :func:`clean_assets` for why it must be complete.
    written: list[str] = field(default_factory=list, init=False)

    # -- parameter access -------------------------------------------------- #
    def param(self, name: str, default: Any = None) -> Any:
        """Read one projected parameter, or ``default``.

        Byte-for-byte the behaviour of ``GateContext.param``, dotted spelling
        included: half the callers are looking at ``model.json``, which is
        ``{"config": {...}, "derived": {...}}``, while ``params`` here is
        flattened. The two contexts must agree — a pack author who learned
        ``ctx.param("config.thickness")`` in a gate and got ``None`` in a
        viewgen would file it as a spine bug, and would be right.
        """
        if name in self.params:
            return self.params[name]
        tail = name.rsplit(".", 1)[-1]
        if tail in self.params:
            return self.params[tail]
        return default

    # -- assets ------------------------------------------------------------ #
    def _assets_root(self) -> str:
        """Absolute directory assets land in. Derived, so a test needs only ``root``."""
        if self.assets_dir:
            return self.assets_dir
        return os.path.join(self.root or os.curdir, SITE_DIR, ASSETS_DIR)

    def asset_path(self, name: str) -> str:
        """Filesystem path an asset called ``name`` would have. Creates nothing.

        For a viewgen that must hand a real path to an exporter which insists on
        opening the file itself (trimesh, matplotlib, a solver's writer). Pair it
        with :meth:`write_asset` for anything you can produce in memory, because
        only ``write_asset`` records the file — see there for why that matters.
        """
        return os.path.join(self._assets_root(), _safe_asset_name(name))

    def write_asset(self, name: str, data: bytes | str) -> str:
        """Write one asset and return its path **relative to ``site/``**.

        The return value is what goes in ``View.src``: ``"assets/assembly.glb"``.
        Relative because the page is served from ``site/`` and the whole
        directory is copied, published, zipped and moved around; an absolute
        path baked into ``state.json`` describes one machine's filesystem and
        breaks on every other one.

        **Never write into ``site/assets/`` by hand.** ``build`` must be able to
        delete stale assets — a renamed view otherwise leaves a 12 MB GLB behind
        that is indistinguishable from a current one — and it can only delete
        what it knows is no longer referenced, which means it can only trust a
        list of what *was* written. A viewgen that writes the file itself is
        invisible to that list: its output either survives forever or, worse,
        gets deleted the first time somebody makes the cleaner more aggressive.

        The write is atomic (temp file in the same directory, then
        ``os.replace``). A viewer handed a half-written GLB does not raise; it
        renders a broken box, which reads as a modelling problem rather than as
        a crashed export.
        """
        safe = _safe_asset_name(name)
        target = os.path.join(self._assets_root(), safe)
        ensure_dir(os.path.dirname(target))
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        # Written only when the bytes differ (architecture phase 5, SPEC 32): a
        # rebuild that drew the same picture changes no tracked byte and no
        # mtime. The path is recorded either way, or the cleaner would delete it.
        try:
            with open(target, "rb") as fh:
                same = fh.read() == payload
        except OSError:
            same = False
        if not same:
            _atomic_write_bytes(target, payload)

        site_relative = posixpath.join(ASSETS_DIR, safe)
        if site_relative not in self.written:
            self.written.append(site_relative)
        return site_relative


def _safe_asset_name(name: str) -> str:
    """Validate an asset name and return it normalised to a posix sub-path.

    Subdirectories are allowed (``"charts/sweep.csv"``); escaping ``assets/`` is
    not. Viewgens are third-party pack code — a pack is an ordinary directory
    anybody can drop in — so ``write_asset("../../.nopekit/ledger.json", ...)``
    is a real reach, and it is the ledger it would land on. The check is here
    rather than at the call site because both public entry points (
    :meth:`ViewContext.write_asset` and :meth:`ViewContext.asset_path`) need the
    same answer and a check that exists twice is a check that will exist once.
    """
    raw = str(name or "").strip().replace("\\", "/")
    if not raw:
        raise NopekitError(
            "write_asset() needs a name — an asset with no filename cannot be "
            "referenced by a view, cleaned, or published"
        )
    if raw.startswith("/") or os.path.isabs(raw) or (len(raw) > 1 and raw[1] == ":"):
        raise NopekitError(
            f"asset name {name!r} is an absolute path. Assets are written under "
            f"site/{ASSETS_DIR}/ and referenced relative to site/, so that the "
            f"whole directory can be copied and published somewhere else."
        )
    normalised = posixpath.normpath(raw)
    if normalised == ".." or normalised.startswith("../"):
        raise NopekitError(
            f"asset name {name!r} escapes site/{ASSETS_DIR}/. Everything a viewgen "
            f"writes has to live inside the assets directory: it is published as "
            f"part of the site, and `nopekit site build` deletes stale files in "
            f"there — which it can only do safely if nothing outside is reachable."
        )
    return normalised


def _atomic_write_bytes(path: str, payload: bytes) -> None:
    """``util.atomic_write_text`` for bytes: temp file in the same dir, then replace.

    ``util`` has the text version only, and a GLB is not text — round-tripping
    binary through a str encoding to reuse it would corrupt the file quietly,
    which is the one failure mode worse than not being atomic at all. Same
    directory so ``os.replace`` stays on one filesystem and therefore stays
    atomic; ``/tmp`` is frequently a different mount and the rename would
    degrade to a copy.
    """
    directory = os.path.dirname(path) or os.curdir
    ensure_dir(directory)
    handle, temp = tempfile.mkstemp(dir=directory, prefix=".nopekit-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


# --------------------------------------------------------------------------- #
# the context a BOUND viewgen is handed (issue #15, B.1.3)
# --------------------------------------------------------------------------- #
@dataclass
class BoundEntry:
    """The verdict entry a bound viewgen draws: ``gate``, ``name``
    (``<rho16>-<out8>``), ``fresh`` (the resolver's word for it), ``files``
    ``{portable path: absolute path}`` holding ONLY the evidence files whose
    bytes are the ones the recorded run of this entry cited (its manifest,
    `verdicts.evidence_manifest`), and ``withheld`` ``{portable path:
    reason}`` for every other evidence path. A viewgen reads ``files`` and
    nothing else of the run: a file under the same name with other bytes is
    another run's, and a picture of it would be published as this result's."""

    gate: str = ""
    name: str = ""
    fresh: bool = False
    files: dict[str, str] = field(default_factory=dict)
    withheld: dict[str, str] = field(default_factory=dict)

    def path(self, basename: str) -> str | None:
        """The verified file whose base name is ``basename``, or None (none,
        or withheld)."""
        for portable in sorted(self.files):
            if posixpath.basename(portable) == basename:
                return self.files[portable]
        return None


@dataclass
class BoundContext(ViewContext):
    """What a bound viewgen (``evidence_of``) is handed: a `ViewContext` with no
    model, no parameters and no claim's acceptance, plus ``entry`` (a
    `BoundEntry`). It writes into scratch git ignores
    (``.nopekit/out/figures/<gate>/<entry>/<view>/``), never into ``site/``:
    the spine validates what it wrote (`_figure_refusal`) and only then
    publishes it (`_publish_figure`).

    * ``bind({path: sha256})`` declares extra inputs (a solved field the
      verified summary lists with its digest). Each digest must occur in a
      verified evidence file of the entry, and each file must hash to it; else
      it returns None and says why. After the call the spine hashes every
      bound input and every verified evidence file again, and discards the
      picture if one moved.
    * ``write_asset(name, bytes)`` writes a PNG or a 3D file (a GLB, or a
      glTF that holds its buffers inside it) into the scratch; over
      `page.EVIDENCE_MAX_BYTES` it raises and writes nothing.
    * ``write_live(name, bytes)`` the same, for a file published only to the
      ignored live folder.
    * ``note(text)`` says something in the build's warnings.

    What it closes (issue #15): the model and the parameters are the design
    NOW, which may be another design than the served entry's; a picture drawn
    from them would be published as that entry's evidence. A claim's limit
    handed to a viewgen is how a colour range gets keyed to a limit, so it is
    withheld (`_figure_refusal` refuses one that turns up anyway).
    *Rejected:* `write_asset` straight into ``site/`` (a half-validated or
    oversize file would be published until the check ran, and an input that
    moved during the draw would not be seen)."""

    entry: BoundEntry = field(default_factory=BoundEntry)
    scratch: str = ""
    view_id: str = ""
    #: ``{name: (absolute path, live)}`` of what `write_asset` and `write_live`
    #: wrote, in order. Only these are published.
    outputs: dict[str, tuple[str, bool]] = field(default_factory=dict, init=False)
    #: ``{absolute path: sha256}`` of every input `bind` accepted.
    bound: dict[str, str] = field(default_factory=dict, init=False)
    #: What the viewgen said (`note`), and why a write or a bind was refused.
    notes: list[str] = field(default_factory=list, init=False)
    refused: list[str] = field(default_factory=list, init=False)
    #: ``{portable path: sha256}`` of ``entry.files``, set by the spine.
    digests: dict[str, str] = field(default_factory=dict, init=False)

    def note(self, text: str) -> None:
        """Say ``text`` in the build's warnings, under this view's id."""
        text = " ".join(str(text or "").split())
        if text:
            self.notes.append(text)

    def asset_path(self, name: str) -> str:
        """Refused: a bound viewgen's files reach the site only through
        `write_asset`, where the spine sees and checks them."""
        raise NopekitError(f"view {self.view_id}: a bound viewgen writes its files with "
                           f"write_asset(name, bytes); a path into site/ would skip the checks "
                           f"that publish it")

    def write_asset(self, name: str, data: bytes | str) -> str:
        """Write one output into the scratch folder and return its name (what
        ``View.src`` names). Over the cap it raises and writes nothing."""
        return self._write(name, data, live=False)

    def write_live(self, name: str, data: bytes | str) -> str:
        """`write_asset` for a file published only under ``assets/live/``."""
        return self._write(name, data, live=True)

    def _write(self, name: str, data: bytes | str, *, live: bool) -> str:
        safe = _safe_asset_name(name)
        if "/" in safe:
            raise NopekitError(f"view {self.view_id}: {name!r} names a folder; a bound "
                               f"figure's files are plain names in its entry's folder")
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        if len(payload) > page_logic.EVIDENCE_MAX_BYTES:
            said = (f"{safe} is {len(payload)} bytes, over the 2 MiB cap; nothing was "
                    f"written")
            self.refused.append(said)
            raise NopekitError(f"view {self.view_id}: {said}")
        target = os.path.join(self.scratch, safe)
        _atomic_write_bytes(target, payload)
        self.outputs[safe] = (target, live)
        return safe

    def bind(self, inputs: Mapping[str, str]) -> dict[str, str] | None:
        """``{path: absolute path}`` for every input of ``inputs`` (``{path:
        sha256}``), or None when one does not hold, with the reason noted. A
        path is ``<out>/...`` (under ``.nopekit/out/``) or relative to the
        project, and must stay inside it."""
        if not isinstance(inputs, Mapping):
            raise TypeError("bind() takes {path: sha256}")
        text = b"\n".join(_file_bytes(p) or b"" for p in self.entry.files.values())
        out: dict[str, str] = {}
        for path, want in inputs.items():
            why = ""
            absolute = self._input_path(str(path))
            want = str(want or "").strip().lower()
            if absolute is None:
                why = "it is outside the project"
            elif not (len(want) == 64 and all(c in "0123456789abcdef" for c in want)):
                why = "its digest is not a sha256"
            elif want.encode("ascii") not in text:
                why = "no evidence file of this result holds its digest"
            else:
                found = page_logic._sha256_of(absolute)
                if found is None:
                    why = "it is not on this computer"
                elif found[0] != want:
                    why = (f"the file on this computer is from another run: `nopekit check "
                           f"--force --only {self.entry.gate}` solves it again")
            if why:
                self.refused.append(f"input {path} is not bound: {why}")
                self.note(f"input {path} is not bound: {why}")
                return None
            out[str(path)] = absolute
            self.bound[absolute] = want
        return out

    def _input_path(self, text: str) -> str | None:
        root = os.path.abspath(self.root or os.curdir)
        if text.startswith("<out>/"):
            base = store.out_dir(root)
            parts = text[len("<out>/"):].replace("\\", "/").split("/")
        elif text.startswith(("<", "~")) or os.path.isabs(text):
            return None
        else:
            base = root
            parts = text.replace("\\", "/").split("/")
        parts = [p for p in parts if p not in ("", ".")]
        if not parts or ".." in parts:
            return None
        candidate = os.path.join(base, *parts)
        real, top = os.path.realpath(candidate), os.path.realpath(root)
        return candidate if real.startswith(top + os.sep) else None

    def moved(self) -> list[str]:
        """The bound inputs and verified evidence files whose bytes are not
        the ones they were bound at, now."""
        out = []
        for absolute, want in sorted(self.bound.items()):
            found = page_logic._sha256_of(absolute)
            if found is None or found[0] != want:
                out.append(absolute)
        for portable, absolute in sorted(self.entry.files.items()):
            found = page_logic._sha256_of(absolute)
            if found is None or found[0] != self.digests.get(portable):
                out.append(portable)
        return out


def _file_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# the registration record
# --------------------------------------------------------------------------- #
@dataclass
class ViewSpec:
    """Registration record for one viewgen. The callable itself is not serialised.

    Mirrors :class:`~nopekit.models.GateSpec`, minus everything that only makes
    sense for something that can fail: no tier (drawing is not swept), no
    ``settles`` (a view settles nothing), no negative control (see
    :meth:`ViewRegistry.register`).

    ``gates`` is the list of gate ids whose locators are expected to address this
    view. It is a *declaration for the page* — "show me the gates that touch
    this view" without scanning every verdict — and not a constraint: a verdict
    from a gate that is not listed still draws, because a pack that grew a new
    gate must not silently stop rendering.

    ``evidence_of`` (issue #15) binds the viewgen to one gate: it draws that
    gate's served verdict entry, from the files the entry's run cited, through a
    `BoundContext`, and its figure is published under the entry's folder.
    ``recipe`` names the files beside the module (the scene, the scales) whose
    bytes, with the module's, key the memo (`_recipe_digest`). On ``ViewSpec``,
    never on ``models.View``: the binding is a property of the drawing code,
    and a field on ``View`` would be a spine edit and a strict-reader change
    for one use (C2).
    """

    id: str
    kind: ViewKind = ViewKind.IMAGE
    title: str = ""
    description: str = ""
    requires_python: tuple = ()
    requires_tools: tuple = ()
    pack: str = ""
    order: int = 100
    gates: tuple = ()
    evidence_of: str = ""
    recipe: tuple = ()


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #
def _own_copy(spec: ViewSpec) -> ViewSpec:
    """A private copy the caller cannot edit afterwards.

    Same reasoning as ``gates._own_copy``: registration audits the record (id
    usable, kind known), and storing the caller's object would make that audit a
    formality — a pack could register ``id="assembly"`` and rewrite the id a line
    later, after which every locator aimed at the view it registered addresses
    nothing. The tuple fields are re-tupled for the same reason.
    """
    return dataclasses.replace(
        spec,
        kind=ViewKind(spec.kind),
        requires_python=tuple(spec.requires_python or ()),
        requires_tools=tuple(spec.requires_tools or ()),
        gates=tuple(spec.gates or ()),
        evidence_of=str(spec.evidence_of or ""),
        recipe=tuple(str(r) for r in (spec.recipe or ())),
    )


class ViewRegistry:
    """The viewgens known to this process, in declaration order.

    Order is preserved for the same reason :class:`~nopekit.gates.Registry`
    preserves it — it is the order the pack's author wrote them — but display
    order is ``ViewSpec.order`` and the page sorts on that. Registration order is
    the tie-break, so two views that both took the default 100 render in the
    order their pack declared rather than alphabetically.
    """

    def __init__(self) -> None:
        self._views: dict[str, tuple[ViewSpec, Callable[[ViewContext], Any]]] = {}
        self._tool_checks: dict[str, tuple[str, Callable[[str], Any]]] = {}

    # -- registration ------------------------------------------------------ #
    def register(
        self,
        spec: ViewSpec,
        fn: Callable[[ViewContext], Any],
        *,
        replace: bool = False,
    ) -> None:
        """Register one viewgen, or raise ``NopekitError`` saying what is wrong.

        **This registry deliberately does not demand a negative control**, and
        the asymmetry with :meth:`nopekit.gates.Registry.register` is worth
        stating because a reader who knows that code will look for it. A gate is
        refused without one because a validator nobody has watched fail is a
        logger, and a green tick from a logger launders assumption into proof. A
        viewgen proves nothing: it draws. There is no claim to falsify, no
        acceptance to invert, and a known-bad fixture for a picture would only
        demonstrate that the picture is different. The honesty machinery belongs
        on the thing that makes a claim, and putting a ceremonial version of it
        here would teach pack authors that the control is paperwork.

        What IS refused, and why each one produces a wrong page rather than an
        error:

        * an empty or unsafe id — the id is a filename (``data/views/<id>.json``),
          a URL fragment, and the string every ``Locator.view`` matches against
        * an unknown kind — the renderer knows six kinds and nothing else; a
          seventh renders as a blank panel with no message
        * a duplicate id from a different function — two views claiming one name
          means every locator aimed at that name lights up whichever one
          registered last, which is a confident highlight on the wrong geometry.
          That is worse than no highlight: it sends someone to inspect a part
          that is fine, and after the second time they stop trusting the overlay.

        Re-registering the *same* function under the same id is a no-op, because
        importing a pack's view module twice in one process is routine.
        """
        if not isinstance(spec, ViewSpec):       # a bug in the caller, not the user
            raise TypeError(f"register() needs a ViewSpec, got {type(spec).__name__}")
        if not callable(fn):
            raise TypeError(f"viewgen {spec.id!r} is not callable ({type(fn).__name__})")

        view_id = (spec.id or "").strip()
        _require_view_id(view_id)
        spec = dataclasses.replace(spec, id=view_id, kind=_coerce_kind(view_id, spec.kind))
        _require_binding(spec)

        existing = self._views.get(view_id)
        if existing is not None and not replace:
            old_spec, old_fn = existing
            if old_fn is fn:
                self._views[view_id] = (_own_copy(spec), fn)      # idempotent re-import
                return
            where_old = old_spec.pack or getattr(old_fn, "__module__", "?")
            where_new = spec.pack or getattr(fn, "__module__", "?")
            raise NopekitError(
                f"view id {view_id!r} is already registered by {where_old} and "
                f"{where_new} wants it too. Two views cannot share an id: every "
                f"Locator addresses a view by this string, so the second one "
                f"silently steals the first one's highlights. Rename one."
            )

        self._views[view_id] = (_own_copy(spec), fn)

    def unregister(self, view_id: str) -> bool:
        """Drop a viewgen. Returns whether it was there. Mostly for tests."""
        return self._views.pop(view_id, None) is not None

    def clear(self) -> None:
        """Empty the registry. Tests and isolated pack validation use a scratch one."""
        self._views.clear()
        self._tool_checks.clear()

    # -- the tools the views draw with ------------------------------------- #
    def add_tool_check(self, tool: str, fn: Callable[[str], Any], *, pack: str = "") -> None:
        """Register a view module's check of one tool it draws with
        (`TOOL_CHECKS_NAME`). A second check of the same tool from the same
        function is a no-op; from another, the first stays (``doctor`` shows
        one row a tool)."""
        self._tool_checks.setdefault(str(tool), (pack, fn))

    def tool_checks(self) -> dict[str, tuple[str, Callable[[str], Any]]]:
        """``{tool: (pack, check)}``, in registration order."""
        return dict(self._tool_checks)

    def view_tools(self) -> list[str]:
        """Every tool a view draws with: each viewgen's ``requires_tools``,
        then each tool a module checks, in order, once each."""
        out: list[str] = []
        for spec in self.specs():
            out.extend(t for t in spec.requires_tools if t and t not in out)
        out.extend(t for t in self._tool_checks if t not in out)
        return out

    # -- lookup ------------------------------------------------------------ #
    def get(self, view_id: str) -> tuple[ViewSpec, Callable[[ViewContext], Any]] | None:
        """``(spec, fn)`` for one id, or None. Never raises on an unknown id."""
        return self._views.get(view_id)

    def specs(self) -> list[ViewSpec]:
        """Every spec, in registration order."""
        return [spec for spec, _ in self._views.values()]

    def ids(self) -> list[str]:
        """Every view id, in registration order."""
        return list(self._views)

    def pairs(self) -> list[tuple[ViewSpec, Callable[[ViewContext], Any]]]:
        """Every ``(spec, fn)``, in registration order."""
        return list(self._views.values())

    # -- dunders ----------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self._views)

    def __contains__(self, view_id: object) -> bool:
        return view_id in self._views

    def __iter__(self):
        return iter(self._views.values())

    def __repr__(self) -> str:          # pragma: no cover - diagnostics only
        return f"ViewRegistry({len(self._views)} views: {', '.join(list(self._views)[:6])})"


def _coerce_kind(view_id: str, kind: Any) -> ViewKind:
    """``ViewKind`` or ``NopekitError`` naming the kinds the renderer knows.

    A bare ``ValueError: 'hologram' is not a valid ViewKind`` is a Python error
    about an enum; a pack author reading it learns nothing about what the site
    can draw. The refusal has to carry the vocabulary, because the whole point of
    the fixed kind list is that extensibility lives in the DATA — the answer is
    always "pick the kind that carries your payload", never "add a renderer".
    """
    try:
        return ViewKind(kind)
    except ValueError as exc:
        known = ", ".join(k.value for k in ViewKind)
        raise NopekitError(
            f"view {view_id!r} declares kind {kind!r}, which the site cannot render. "
            f"Known kinds: {known}. A pack gets visualisation for free by emitting "
            f"one of these; it does not get to ship its own renderer, because the "
            f"one artifact whose job is to be trusted cannot depend on code nobody "
            f"reviewed."
        ) from exc


def _require_binding(spec: ViewSpec) -> None:
    """Refuse a bound viewgen (``evidence_of``) the build could not publish: a
    kind outside `BOUND_KINDS`, or a ``recipe`` path that leaves its pack or
    project (it is read for the memo's key). Checked at registration, so a
    pack whose figure could never be shown fails to load instead of drawing
    nothing on every build."""
    if not spec.evidence_of and not spec.recipe:
        return
    if not str(spec.evidence_of or "").strip():
        raise NopekitError(f"view {spec.id!r} names a recipe and no evidence_of: a recipe "
                           f"keys a bound figure, and a figure is bound to one gate's result")
    if ViewKind(spec.kind) not in BOUND_KINDS:
        raise NopekitError(
            f"view {spec.id!r} is bound to {spec.evidence_of} as a {ViewKind(spec.kind).value}; "
            f"a bound viewgen draws {', '.join(k.value for k in BOUND_KINDS)}")
    for rel in spec.recipe or ():
        parts = str(rel).replace("\\", "/").split("/")
        if not rel or os.path.isabs(str(rel)) or ".." in parts or str(rel).startswith("/"):
            raise NopekitError(f"view {spec.id!r}: recipe file {rel!r} must be a path inside "
                               f"its pack or project")


def _require_view_id(view_id: str) -> None:
    """Refuse a view id that cannot be a filename, a fragment and a locator target.

    Checked at registration because every later use is somewhere it is too late
    to complain: ``data/views/../../x.json`` is a path escape at write time, and
    an id with a space in it is a locator that never matches at read time — and
    a locator that never matches is silent by construction.
    """
    if not view_id:
        raise NopekitError(
            "a view needs an id: it is the string every Locator addresses, the "
            "name of its payload file, and the anchor the page links to"
        )
    bad = sorted({ch for ch in view_id if ch not in _ID_OK})
    if bad:
        raise NopekitError(
            f"view id {view_id!r} contains {', '.join(repr(c) for c in bad)}. Use "
            f"letters, digits, '-', '_' and '.': the id becomes a filename under "
            f"site/{VIEWS_DIR}/ and a URL fragment, and a locator matches it as a "
            f"literal string."
        )


#: The process-wide default registry. Pack view modules decorate against this on
#: import; a loader may pass its own for isolated validation.
VIEW_REGISTRY = ViewRegistry()

#: Stack of registries that ``@viewgen`` targets when a module does not name one.
#: Empty means VIEW_REGISTRY. A stack, not a slot, for the same reason as in
#: ``gates``: validating one pack while another is loading is a real sequence.
_ACTIVE_VIEW_REGISTRIES: list[ViewRegistry] = []


def active_view_registry() -> ViewRegistry:
    """The registry ``@viewgen`` decorates into right now.

    A pack's view module cannot name the registry it belongs in — it is imported
    by a loader that chose one, and the module was written before that choice
    existed. So the choice is ambient for the duration of the import.
    """
    return _ACTIVE_VIEW_REGISTRIES[-1] if _ACTIVE_VIEW_REGISTRIES else VIEW_REGISTRY


@contextlib.contextmanager
def use_view_registry(registry: ViewRegistry):
    """Make ``registry`` the target of every ``@viewgen`` decorated inside the block.

    The same seam ``gates.use_registry`` provides, and it exists for the same
    measured failure: a loader that hands a private registry to an import and
    gets an empty one back, with the views in the global registry instead.
    Because a loader reports what it added by diffing the registry it was given,
    the symptom is an empty list rather than an error — a pack that looks like it
    ships no views at all. Silent under-reporting is the worst failure available
    here, so the seam exists rather than being left to convention.
    """
    _ACTIVE_VIEW_REGISTRIES.append(registry)
    try:
        yield registry
    finally:
        # Remove OUR entry, not the top one: a module that misbehaved and left
        # something on the stack would otherwise silently redirect every later
        # registration in the process.
        try:
            _ACTIVE_VIEW_REGISTRIES.remove(registry)
        except ValueError:                       # pragma: no cover - defensive
            pass


# --------------------------------------------------------------------------- #
# loading views/*.py
# --------------------------------------------------------------------------- #
def _shown(path: str, root: str | None) -> str:
    """``path`` as a message names it: relative to the project, or whole when
    there is none (`pack validate` loads a pack with no project)."""
    return rel(path, root) if root else path


def load_viewgens(directory: str, registry: ViewRegistry, *,
                  pack: str, root: str | None) -> list[ViewSpec]:
    """Import `<directory>/*.py` into a ViewRegistry; return the specs it added.

    Deliberately the same shape as `gates.load_project_gates`, because a viewgen is a
    gate's twin: same directory convention, same `sys.path` handling, same
    `PACK`/`PACK_DIR` globals, same "an exception here is the pack author's
    problem and must not print as a spine traceback". A pack author who has
    written `gates/clash.py` writes `views/assembly.py` with nothing new to
    learn, which is the whole reason `site.py` mirrors `gates.py` field for
    field.

    Two details carry weight:

    * **Module names are salted with a hash of the file's absolute path**, so a
      pack's `views/assembly.py` and a project's `views/assembly.py` are
      different modules. Without the salt the second one to load silently reuses
      the first one's code while every message names the second file.
    * **Already-imported modules are re-registered from `fn.view_spec`**, not
      re-executed. Python will not run a module twice, so a second build in one
      process (a test, an agent that builds after every edit, `site status`
      after `site build`) would decorate nothing and find an EMPTY registry —
      reporting "this project has no views" about a project with six. The
      decorator attaches `view_spec` to the function precisely so a loader can
      recover the registration without re-running the module.

    Registering the same function under the same id twice is a no-op in
    `ViewRegistry`, so the walk is safe on a freshly executed module too, and
    the two paths stay one path rather than two that can drift.
    """
    if not os.path.isdir(directory):
        return []
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        raise NopekitError(f"cannot read {_shown(directory, root)}: {exc}") from exc
    files = [os.path.join(directory, name) for name in names
             if name.endswith(".py") and not name.startswith("_")]
    if not files:
        return []

    where = f"pack {pack!r}: " if pack else ""
    before = set(registry.ids())
    # The directory ABOVE views/ — the pack root, or the project root. On
    # sys.path so a viewgen can `import _geom` and share the helper the gate
    # next door already uses, and set as PACK_DIR so pack-relative fixtures
    # resolve the same way they do for gates.
    base = os.path.dirname(directory)
    sys.path.insert(0, base)
    try:
        with use_view_registry(registry):
            for path in files:
                module = _import_viewgen_module(
                    path, pack=pack, base=base, root=root, where=where)
                # The attribute walk, always — see the docstring. On a fresh
                # import it re-registers what the decorator just registered
                # (a no-op); on a reused one it is the only thing that registers
                # anything at all.
                for value in list(vars(module).values()):
                    spec = getattr(value, "view_spec", None)
                    if not isinstance(spec, ViewSpec) or not callable(value):
                        continue
                    try:
                        registry.register(spec, value)
                    except NopekitError as exc:
                        raise NopekitError(
                            f"{where}{_shown(path, root)}: {exc}") from exc
                checks = getattr(module, TOOL_CHECKS_NAME, None)
                if checks is None:
                    continue
                if not isinstance(checks, Mapping) or not all(
                        isinstance(t, str) and t and callable(fn) for t, fn in checks.items()):
                    raise NopekitError(
                        f"{where}{_shown(path, root)}: {TOOL_CHECKS_NAME} must map each "
                        f"tool's name to a function of the project root that returns "
                        f"(ok, words)")
                for tool, fn in checks.items():
                    registry.add_tool_check(tool, fn, pack=pack)
    finally:
        try:
            sys.path.remove(base)       # removes OUR insertion (the first match)
        except ValueError:              # pragma: no cover - a viewgen mangled sys.path
            pass
    return [spec for spec in registry.specs() if spec.id not in before]


def _import_viewgen_module(path: str, *, pack: str, base: str,
                           root: str | None, where: str) -> Any:
    """Import one `views/*.py`, or raise an NopekitError naming the file."""
    stem = os.path.splitext(os.path.basename(path))[0]
    module_name = f"nopekit_views_{stem}_{short_hash(os.path.abspath(path), 8)}"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing                 # ordinary import semantics; see load_viewgens

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:         # pragma: no cover - defensive
        raise NopekitError(f"{where}{_shown(path, root)}: no import machinery accepted this file")
    module = importlib.util.module_from_spec(spec)
    # Set BEFORE execution, which `module_from_spec` makes possible by handing
    # the module over before the loader runs it. `@viewgen` reads `PACK` off the
    # defining module at decoration time so twenty decorators do not each retype
    # the pack name — a mistyped one orphans a view in the site's grouping with
    # nothing reporting it. `PACK_DIR` is the pack's own directory, for a viewgen
    # that has to find a template or a material table shipped beside it; for a
    # project's `views/` it is the project root, which is where those things are.
    module.PACK = pack
    module.PACK_DIR = base
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except NopekitError as exc:
        # A registry refusal (duplicate view id, unknown kind) is already phrased
        # for a human. Keep the phrasing and add the location.
        sys.modules.pop(module_name, None)
        raise NopekitError(f"{where}{_shown(path, root)}: {exc}") from exc
    except KeyboardInterrupt:
        sys.modules.pop(module_name, None)
        raise
    except BaseException as exc:
        # BaseException on purpose, exactly as `packs.load_gates` does it: a view
        # module whose exporter guards its own absence with `sys.exit()` at
        # import time must not be able to end the command with that exit code.
        sys.modules.pop(module_name, None)
        raise NopekitError(
            f"{where}{_shown(path, root)} failed to import: {type(exc).__name__}: {exc}"
        ) from exc
    return module


# --------------------------------------------------------------------------- #
# the decorator
# --------------------------------------------------------------------------- #
def viewgen(
    *,
    id: str,
    kind: ViewKind | str,
    title: str = "",
    description: str = "",
    requires_python: Iterable[str] = (),
    requires_tools: Iterable[str] = (),
    pack: str = "",
    order: int = 100,
    gates: Iterable[str] = (),
    registry: ViewRegistry | None = None,
    evidence_of: str = "",
    recipe: Iterable[str] = (),
) -> Callable[[Callable[[ViewContext], Any]], Callable[[ViewContext], Any]]:
    """Declare a viewgen: build its :class:`ViewSpec` and register it.

    Returns the function **unchanged** — no wrapper, no signature change —
    exactly as :func:`nopekit.gates.gate` does, so a viewgen stays an ordinary
    function of one argument and its own unit test exercises the thing that
    ships rather than the registry's normalisation of it::

        from pack.views.assembly import assembly
        view = assembly(ctx)        # no registry, no CLI

    The spec is attached as ``fn.view_spec`` so a loader walking a module's
    attributes can find it without consulting a registry.

    Three fields are derived rather than restated (method rule 2):

    * ``pack`` falls back to the defining module's ``PACK`` global, which is what
      a pack loader sets. Typing the pack name into twenty decorators is twenty
      chances to typo it, and a mistyped pack name orphans a view in the site's
      grouping with nothing reporting it.
    * ``title`` / ``description`` fall back to the docstring's first line / rest,
      so a viewgen whose docstring already says what it draws does not say it
      twice and let the two drift.

    ``requires_python`` / ``requires_tools`` are declared the way a gate declares
    them, and for the same reason: a viewgen whose exporter is not installed is
    reported as *unavailable*, with the missing module named, rather than
    vanishing. A view that is absent because trimesh is missing looks exactly
    like a view the project never had.

    Registration failures raise ``NopekitError`` at IMPORT time, which is the
    point — a pack with a duplicate view id fails to load rather than loading
    with one view quietly shadowing another.
    """

    def decorate(fn: Callable[[ViewContext], Any]) -> Callable[[ViewContext], Any]:
        doc = (fn.__doc__ or "").strip()
        doc_title, _, doc_rest = doc.partition("\n")

        module = sys.modules.get(getattr(fn, "__module__", "") or "")
        module_pack = getattr(module, "PACK", "") if module is not None else ""

        spec = ViewSpec(
            id=id,
            kind=_coerce_kind(id, kind),
            title=title or doc_title.strip(),
            description=description or doc_rest.strip(),
            requires_python=tuple(str(m) for m in (requires_python or ())),
            requires_tools=tuple(str(t) for t in (requires_tools or ())),
            pack=pack or (str(module_pack) if module_pack else ""),
            order=int(order),
            gates=tuple(str(g) for g in (gates or ())),
            evidence_of=str(evidence_of or ""),
            recipe=tuple(str(r) for r in (recipe or ())),
        )
        # Resolved at DECORATION time, not when `viewgen()` was called: the
        # ambient registry is whatever loader is importing this module right now.
        target = registry if registry is not None else active_view_registry()
        target.register(spec, fn)
        fn.view_spec = spec              # type: ignore[attr-defined]
        return fn

    return decorate


# --------------------------------------------------------------------------- #
# availability
# --------------------------------------------------------------------------- #
def availability(spec: ViewSpec) -> tuple[bool, str]:
    """Can this viewgen run here? ``(ok, reason)`` — the reason is user-facing.

    Delegates to :func:`nopekit.gates.availability`, which needs only
    ``requires_tools`` and ``requires_python`` and which :class:`ViewSpec`
    provides under the same names. That is deliberate rather than lazy: the
    question "is trimesh importable?" has exactly one right answer, and the
    gates implementation is the one that learned the hard cases — a parent
    package that raises on import (absent and broken are equally unusable, but
    the message must say which), and a solver binding whose "driver not
    installed" guard is a bare ``sys.exit()`` at module scope, which uncaught
    ends the whole command. A second copy here would be a second copy that has
    not learned any of that yet.
    """
    return _gate_availability(spec)      # type: ignore[arg-type]


#: A view module may define ``TOOL_CHECKS = {"<tool>": check}``: ``check(root)``
#: returns ``(ok, words)``, the words after ``"<tool>: "`` on ``doctor``'s row
#: for that tool. For a tool whose presence on PATH says too little (the CFD
#: pack checks that ``pvbatch62`` is the ParaView build it checked, and that
#: its renderer control passed). A tool with no check reads found or not on
#: PATH. *Rejected:* a ``ViewSpec`` field (a check is per tool, and a pack
#: checks its tool before any viewgen needs it); doctor rows from the pack
#: manifest's ``requires_tools`` alone (that list mixes gate and view tools,
#: and ``doctor`` already shows the gates').
TOOL_CHECKS_NAME = "TOOL_CHECKS"

#: What a missing view tool changes, said on its row.
NO_STATUS_WORDS = "No status depends on them."


def view_tool_rows(registry: ViewRegistry, root: str, *,
                   skip: Iterable[str] = ()) -> list[tuple[bool, str]]:
    """``[(ok, "<tool>: <words>")]`` for every tool a view draws with, less
    ``skip`` (the gates' tools, which have rows of their own): ``doctor``'s
    rows. A check that raises reads as a problem of the check, never as the
    tool found."""
    skipped = set(skip)
    rows: list[tuple[bool, str]] = []
    checks = registry.tool_checks()
    for tool in registry.view_tools():
        if tool in skipped:
            continue
        users = [s.id for s in registry.specs() if tool in s.requires_tools]
        if tool in checks:
            pack, fn = checks[tool]
            try:
                ok, words = fn(root)
            except Exception as exc:              # noqa: BLE001 - pack code
                rows.append((False, f"{tool}: {pack or 'the'} check of it raised "
                                    f"{type(exc).__name__}: {exc}"))
                continue
            rows.append((bool(ok), f"{tool}: {' '.join(str(words).split())}"))
            continue
        found = shutil.which(tool)
        rows.append((bool(found), f"{tool}: found at {found}" if found else
                     f"{tool}: not on PATH. These views are not drawn: "
                     f"{', '.join(users)}. {NO_STATUS_WORDS}"))
    return rows


# --------------------------------------------------------------------------- #
# running one viewgen
# --------------------------------------------------------------------------- #
#: Note prefixes :func:`run_viewgen` emits, so :func:`run_all_viewgens` can
#: classify an outcome without re-deriving it. The contract's return type is
#: ``(View | None, str)`` — a human-facing note — and three different reasons for
#: "no view" must stay distinguishable downstream: unavailable is a missing
#: dependency (install it), empty is nothing to draw (normal), error is a broken
#: viewgen (fix it). Classifying by sniffing free text at a distance is fragile,
#: so the prefixes are constants shared by the producer and the consumer.
_NOTE_UNAVAILABLE = "unavailable: "
_NOTE_ERROR = "error: "
_NOTE_EMPTY = "nothing to draw"


def _note_status(view: View | None, note: str) -> str:
    """``ok`` | ``empty`` | ``unavailable`` | ``error`` for one viewgen's outcome."""
    if view is not None:
        return "ok"
    if note.startswith(_NOTE_UNAVAILABLE):
        return "unavailable"
    if note.startswith(_NOTE_ERROR):
        return "error"
    return "empty"


def _stamp_view(view: View, spec: ViewSpec) -> tuple[View, str]:
    """Overwrite the identity fields of a view from its spec. Returns ``(view, note)``.

    Same rule as ``gates._stamp``, and it exists because of the same copy-paste:
    a viewgen written by duplicating the one next to it keeps the previous
    view's id in its ``View(id=...)`` literal. A view filed under the wrong id
    is worse than a missing one — it overwrites its neighbour in the id-keyed
    view list, and every locator aimed at either id now points at one arbitrary
    picture. The spec is the only authority on identity, so the spec wins.

    ``kind`` is forced too, and a disagreement is reported rather than
    reconciled: the registration is what the site's grouping and the pack's
    documentation were written against, and a viewgen that returns a different
    kind is a bug in one of the two places, not a preference.
    """
    note = ""
    if view.kind != spec.kind:
        note = (f"declared kind {spec.kind.value!r} but returned "
                f"{ViewKind(view.kind).value!r}; the declaration wins")
    return (
        dataclasses.replace(
            view,
            id=spec.id,
            kind=spec.kind,
            title=view.title or spec.title,
            description=view.description or spec.description,
            pack=view.pack or spec.pack,
            # The declaration wins unless the viewgen deliberately moved off
            # the default: display order is a property of where a pack wants its
            # view to sit, which is decided at registration, but a viewgen that
            # computes an order at run time (a sweep view that belongs next to
            # the gate it plots) has said something the spec could not know.
            order=view.order if view.order != 100 else int(spec.order),
            # Union, not replacement: the spec's declaration is what the pack
            # documented, the view's list is what the run actually found, and
            # dropping either makes "show me the gates that touch this view"
            # quietly incomplete.
            gates=sorted({*(view.gates or []), *(spec.gates or ())}),
        ),
        note,
    )


def run_viewgen(
    spec: ViewSpec,
    fn: Callable[[ViewContext], Any],
    ctx: ViewContext,
) -> tuple[View | None, str]:
    """Run one viewgen. Returns ``(view or None, note)``; never raises.

    Four outcomes, and keeping them four instead of two is the job:

    * **a view** — it ran and produced something to render (note usually empty)
    * **None, "nothing to draw"** — it ran and had nothing. A CAD viewgen in a
      project with no geometry is not a failure and must not be one: the site
      degrades all the way down to claims and verdicts, and a project that never
      exports a mesh is a normal project, not a broken one.
    * **None, "unavailable: ..."** — its exporter is not installed. Reported,
      never silent: a view missing because trimesh is absent looks identical to
      a view the project never had, and the second costs nobody an afternoon.
    * **None, "error: ..."** — it crashed. A crashed viewgen is a bug in the
      viewgen; it does not stop the build, because the other views and the whole
      claims/verdicts half of the page are still true and still worth publishing.

    ``SystemExit`` and ``GeneratorExit`` are caught alongside ``Exception`` for
    the reason ``gates.run_gate`` spells out at length: pack code that reaches
    ``sys.exit()`` — an exporter's import guard, a stray ``argparse`` call —
    unwinds past an ``except Exception`` handler and out of the command, which
    then exits having written nothing and reported nothing. ``KeyboardInterrupt``
    is deliberately still allowed through: Ctrl-C during a slow mesh export must
    stop the build.

    A viewgen returning something that is not a ``View`` and not ``None`` is an
    error, not a view. There is no coercion here (unlike ``gates._normalise``,
    which accepts four shapes) because there is no natural shorthand for a
    picture: a ``dict`` that looks like a view is a view someone forgot to
    construct, and guessing at it produces a panel that renders wrong rather
    than a message that says what to fix.
    """
    ok, reason = availability(spec)
    if not ok:
        return None, _NOTE_UNAVAILABLE + reason

    try:
        result = fn(ctx)
    except (SystemExit, GeneratorExit) as exc:    # BaseException: see docstring
        what = (f"viewgen called sys.exit({exc.code!r})"
                if isinstance(exc, SystemExit) else "viewgen raised GeneratorExit")
        return None, _NOTE_ERROR + f"{what} — a viewgen must return a View or None"
    except Exception as exc:                      # noqa: BLE001 - deliberate
        tail = traceback.format_exc().strip().splitlines()[-1]
        return None, _NOTE_ERROR + f"{type(exc).__name__}: {exc} ({tail})"

    if result is None:
        return None, _NOTE_EMPTY
    if not isinstance(result, View):
        return None, _NOTE_ERROR + (
            f"viewgen returned {type(result).__name__}; expected a View or None. "
            f"Returning None is the correct way to say there is nothing to draw."
        )

    view, note = _stamp_view(result, spec)
    return view, note


def _selected_views(registry: ViewRegistry, only: str | Iterable[str] | None) -> list[ViewSpec]:
    """Resolve ``only`` to a concrete, ordered spec list. Unmatched patterns raise.

    ``only`` accepts an exact id, a pack name, or an fnmatch pattern
    (``cad.*``) — the same vocabulary as ``nopekit check --only``, because a
    user who learned one should not have to learn the other. Matching nothing
    raises, exactly as it does there: a typo'd ``--only assmebly`` that quietly
    built zero views and reported a clean build is the same lie as a validator
    that exits 0. An empty registry is allowed through — there is nothing to
    typo against.
    """
    all_specs = registry.specs()
    if only is None:
        return all_specs

    patterns = [only] if isinstance(only, str) else [str(o) for o in only]
    patterns = [p.strip() for p in patterns if str(p).strip()]
    if not patterns:
        return all_specs

    chosen: set[str] = set()
    unmatched: list[str] = []
    for pattern in patterns:
        hits = [s for s in all_specs
                if s.id == pattern
                or (s.pack and s.pack == pattern)
                or fnmatch.fnmatchcase(s.id, pattern)]
        if not hits:
            unmatched.append(pattern)
        chosen.update(s.id for s in hits)

    if unmatched and all_specs:
        known = ", ".join(s.id for s in all_specs[:12])
        more = "" if len(all_specs) <= 12 else f" (+{len(all_specs) - 12} more)"
        raise NopekitError(
            f"no view matches {', '.join(repr(u) for u in unmatched)} — building zero "
            f"views and calling it a build is the failure this tool exists to prevent. "
            f"Known views: {known}{more}"
        )
    return [s for s in all_specs if s.id in chosen]


def run_all_viewgens(
    registry: ViewRegistry,
    ctx: ViewContext,
    *,
    only: str | Iterable[str] | None = None,
) -> tuple[list, list]:
    """Run every selected viewgen. Returns ``(views, notes)``.

    ``views`` are the ones that produced something, sorted by
    ``(spec.order, registration order)`` — display order, resolved once here so
    the page does not have to and so ``state.json`` reads top-to-bottom the way
    the site does.

    ``notes`` carries **one record per viewgen that ran**, including the ones
    that produced nothing::

        {"view": "assembly", "status": "unavailable", "pack": "cad",
         "note": "unavailable: requires python trimesh (not importable)"}

    Every viewgen gets a row, not just the failures, because "which views does
    this project have, and why is that one missing?" is a question the build
    summary has to be able to answer without the reader re-running anything.
    ``status`` is one of ``ok`` / ``empty`` / ``unavailable`` / ``error``.

    A bound viewgen (``evidence_of``) is not run here: it needs a
    `BoundContext` of its gate's served entry, and `build` runs it in its own
    pass (`_bound_pass`), after the resolution.

    A viewgen that crashes does not stop the build: the rest of the site —
    every other view, and the entire claims-and-verdicts half of the page — is
    still true, and withholding a true report because one picture failed to draw
    helps nobody. The crash is in ``notes`` with its exception, and
    :func:`build` lifts it into the summary's warnings so it is not merely
    *available* but *said*.
    """
    selected = _selected_views(registry, only)
    order_index = {spec.id: i for i, spec in enumerate(registry.specs())}

    views: list[View] = []
    notes: list[dict[str, Any]] = []
    for spec in selected:
        if spec.evidence_of:
            continue
        entry = registry.get(spec.id)
        if entry is None:            # concurrent unregister; nothing else can do this
            raise NopekitError(f"view {spec.id!r} disappeared from the registry mid-build")
        live, fn = entry
        ctx.log(f"view {live.id}")
        view, note = run_viewgen(live, fn, ctx)
        notes.append({
            "view": live.id,
            "pack": live.pack,
            "kind": ViewKind(live.kind).value,
            "status": _note_status(view, note),
            "note": note,
        })
        if view is not None:
            views.append(view)

    views.sort(key=lambda v: (int(v.order), order_index.get(v.id, 0), v.id))
    return views, notes


# --------------------------------------------------------------------------- #
# explode, derived
# --------------------------------------------------------------------------- #
#: Clearance factor on the thickness extent. 1.15 rather than 1.0 so parts
#: actually separate instead of sitting face-to-face, where an exploded view
#: reads as an unexploded one with a suspicious seam.
_STEP_CLEARANCE = 1.15

#: Floor on the step, in the GLB's own units (mm for every CAD exporter the packs
#: use). A 0.8 mm sheet-metal stack derives a 0.92 mm step, which on screen is
#: indistinguishable from not exploding at all — the geometry is thin, but the
#: reader's eye is not.
_STEP_MIN = 14.0

#: Decimal places kept in the manifest. Offsets are display positions, not
#: measurements: six more digits would add nothing a viewer can show and would
#: churn the diff of a generated file on every rebuild.
_ROUND = 4

#: Separator between a mover name and a node's role within it. A double
#: underscore, not a single one, because single underscores are ordinary inside
#: part names — ``guide_roller`` is one part, ``guide_roller__axle`` is a
#: feature of ``guide_roller`` — and a grouper that split on the first ``_``
#: would shatter every multi-word part into a mover of its own.
MOVER_SEPARATOR = "__"


def derive_explode(bounds: dict, *, overrides: dict | None = None) -> dict:
    """Derive an explode manifest from node bounding boxes in the GLB's own space.

    ``bounds`` is ``{node_name: (min_xyz, max_xyz)}``. Returns::

        {"axes": {"thick": 2, "height": 1, "width": 0},
         "extent": [...], "center": [...], "step": 18.4,
         "movers": {"lid": {"offset": [0, 0, 27.6], "nodes": [...], "rank": 2}}}

    Everything here is **derived, never asked for** (method rule 2), and the
    reason is specific rather than aesthetic:

    * **The thickness axis is the smallest global extent.** A stack is thin in
      the direction it stacks; that is what makes it a stack.
    * **Stack order is each mover's centroid along that axis, sorted.** A stack
      order is something the geometry already knows. Transcribing it into a
      hand-authored layer table is how it goes stale — the table is written once,
      a part moves to the other side of the board six revisions later, and the
      exploded view keeps showing the old order with total confidence. Nothing
      catches it, because nothing is comparing the table to the model.
    * **``step`` is ``max(thickness_extent × 1.15, 14)``** so parts clear each
      other at any scale, with a floor for geometry too thin to separate visibly.
    * **Movers left or right of centre fan outward on the widest axis**,
      proportionally to how far off centre they sit — a mover at the edge of the
      assembly fans a full step, one on the centreline does not move sideways at
      all. This is what makes a mirrored or two-handed assembly readable instead
      of collapsing both halves onto one column of parts. The magnitude is
      ``step`` again rather than a second tuned constant: the clearance that
      separates layers is the clearance that separates halves.

    Movers group by the node-name prefix before ``__`` (see
    :data:`MOVER_SEPARATOR`), so ``lid__boss_a`` and ``lid__boss_b`` travel with
    ``lid``. A node with no separator is its own mover. **Node names are an
    interface**: whatever a ``model3d`` viewgen names its nodes is what every
    gate in that domain must use in its locators, and that scheme belongs in the
    pack's ``PACK.md``.

    ``overrides`` merges **per mover**, so correcting one part does not mean
    transcribing the other twenty — which is the practical reason hand-authored
    manifests rot: the cost of fixing one offset is retyping the whole table, so
    nobody does it twice. Accepted in either shape::

        {"movers": {"lid": {"offset": [0, 0, 40]}}, "step": 22}
        {"lid": {"offset": [0, 0, 40]}}

    ``nodes`` cannot be overridden: node membership comes from the geometry, and
    an override that edits it is transcribing the thing this function exists to
    stop transcribing. An override naming a mover that does not exist is
    reported under ``stale_overrides`` rather than dropped — it means the part
    was renamed or deleted and a hand-edit outlived it.
    """
    boxes = {str(name): _box(name, box) for name, box in (bounds or {}).items()}

    if not boxes:
        # A model3d view with no nodes is a degenerate but legal input (an empty
        # assembly, a viewgen that exported only a ground plane). Returning a
        # manifest of zeros beats raising: the caller has already produced a GLB,
        # and refusing to describe it would turn an empty picture into a failed
        # build.
        manifest: dict[str, Any] = {
            "axes": {"thick": 2, "height": 1, "width": 0},
            "extent": [0.0, 0.0, 0.0],
            "center": [0.0, 0.0, 0.0],
            "step": _STEP_MIN,
            "movers": {},
        }
        return _apply_explode_overrides(manifest, overrides)

    lows = [min(box[0][i] for box in boxes.values()) for i in range(3)]
    highs = [max(box[1][i] for box in boxes.values()) for i in range(3)]
    extent = [highs[i] - lows[i] for i in range(3)]
    center = [(highs[i] + lows[i]) / 2.0 for i in range(3)]

    # Ascending extent, index as the tie-break so a perfect cube resolves the
    # same way on every machine. A manifest that depends on dict iteration order
    # is a manifest that differs between two runs of the same build, and the
    # diff of a generated file is the only thing telling a reviewer what moved.
    by_extent = sorted(range(3), key=lambda i: (extent[i], i))
    thick = by_extent[0]
    rest = by_extent[1:]
    width = max(rest, key=lambda i: (extent[i], -i))
    height = next(i for i in rest if i != width)

    groups: dict[str, list[str]] = {}
    for node in boxes:
        groups.setdefault(mover_name(node), []).append(node)

    centroids: dict[str, list[float]] = {}
    for mover, nodes in groups.items():
        # The centre of the mover's combined bounding box, not the mean of its
        # nodes' centres: a mover with one large body and nine tiny bosses would
        # otherwise be centroided onto the bosses, and it would sort into the
        # wrong layer. We have bounding boxes, not masses, so this is the
        # honest definition of "where this part is".
        lo = [min(boxes[n][0][i] for n in nodes) for i in range(3)]
        hi = [max(boxes[n][1][i] for n in nodes) for i in range(3)]
        centroids[mover] = [(hi[i] + lo[i]) / 2.0 for i in range(3)]

    step = max(extent[thick] * _STEP_CLEARANCE, _STEP_MIN)
    ordered = sorted(groups, key=lambda m: (centroids[m][thick], m))
    n = len(ordered)
    span = extent[width]

    movers: dict[str, Any] = {}
    for rank, mover in enumerate(ordered):
        offset = [0.0, 0.0, 0.0]
        # Centred on the assembly rather than counted up from the bottom, so an
        # exploded view stays inside the same camera frame as the assembled one.
        offset[thick] = (rank - (n - 1) / 2.0) * step
        if span > 0:
            fan = 2.0 * (centroids[mover][width] - center[width]) / span
            offset[width] += max(-1.0, min(1.0, fan)) * step
        movers[mover] = {
            "offset": [round(v, _ROUND) for v in offset],
            "nodes": sorted(groups[mover]),
            # Rank rides along so a viewer can stage the explode one layer at a
            # time without re-deriving the sort from the offsets it was given.
            "rank": rank,
        }

    manifest = {
        "axes": {"thick": thick, "height": height, "width": width},
        "extent": [round(v, _ROUND) for v in extent],
        "center": [round(v, _ROUND) for v in center],
        "step": round(step, _ROUND),
        "movers": movers,
    }
    return _apply_explode_overrides(manifest, overrides)


def mover_name(node: str) -> str:
    """The mover a node belongs to: its name before ``__``, or the whole name."""
    name = str(node)
    return name.split(MOVER_SEPARATOR, 1)[0] if MOVER_SEPARATOR in name else name


def _box(name: Any, box: Any) -> tuple[list[float], list[float]]:
    """Validate one ``(min_xyz, max_xyz)`` pair, naming the node when it is wrong."""
    try:
        low, high = box
        low = [float(v) for v in low]
        high = [float(v) for v in high]
    except (TypeError, ValueError) as exc:
        raise NopekitError(
            f"bounds for node {name!r} are not a (min_xyz, max_xyz) pair of three "
            f"numbers each: {box!r}"
        ) from exc
    if len(low) != 3 or len(high) != 3:
        raise NopekitError(
            f"bounds for node {name!r} need three coordinates per corner, got "
            f"{len(low)} and {len(high)}"
        )
    # Swapped corners are repaired rather than rejected: some exporters hand back
    # a box with a negative extent on a mirrored part, and a negative extent
    # would make that axis sort as the *smallest* one — quietly electing the
    # wrong thickness axis and exploding the whole assembly sideways.
    return ([min(a, b) for a, b in zip(low, high)],
            [max(a, b) for a, b in zip(low, high)])


def _apply_explode_overrides(manifest: dict, overrides: dict | None) -> dict:
    """Merge ``overrides`` into a derived manifest, per mover. See :func:`derive_explode`."""
    if not overrides:
        return manifest
    if not isinstance(overrides, dict):
        raise NopekitError(
            f"explode overrides must be a dict, got {type(overrides).__name__}"
        )

    # Either shape is accepted, and the rule is unambiguous: a "movers" key means
    # the manifest-shaped form, anything else is the movers map itself. Being
    # forgiving here costs three lines; being wrong costs a silently ignored
    # site/explode.json, which is the failure people debug for an hour.
    if "movers" in overrides:
        top = {k: v for k, v in overrides.items() if k != "movers"}
        per_mover = overrides.get("movers") or {}
    else:
        top = {}
        per_mover = overrides

    for key in ("step", "axes", "center", "extent"):
        if key in top:
            manifest[key] = top[key]

    stale: list[str] = []
    movers = manifest["movers"]
    for name, patch in (per_mover or {}).items():
        if name not in movers:
            stale.append(str(name))
            continue
        if not isinstance(patch, dict):
            raise NopekitError(
                f"explode override for mover {name!r} must be a dict of fields to "
                f"replace, got {type(patch).__name__}"
            )
        merged = dict(movers[name])
        for key, value in patch.items():
            if key == "nodes":
                # Node membership is geometry. An override that edits it is
                # transcribing exactly what this module derives, and it will be
                # the copy that goes stale.
                continue
            if key == "offset":
                value = _offset(name, value)
            merged[key] = value
        movers[name] = merged

    if stale:
        # Never silent: an override for a part that no longer exists means a
        # hand-edit has outlived its geometry, and the person who wrote it is
        # still looking at a viewer that ignores them.
        manifest["stale_overrides"] = sorted(stale)
    return manifest


def _offset(name: str, value: Any) -> list[float]:
    """Validate an overridden offset. A two-element offset breaks the viewer silently."""
    try:
        out = [float(v) for v in value]
    except (TypeError, ValueError) as exc:
        raise NopekitError(
            f"explode override for {name!r}: offset must be three numbers, got {value!r}"
        ) from exc
    if len(out) != 3:
        raise NopekitError(
            f"explode override for {name!r}: offset needs three components (x, y, z), "
            f"got {len(out)}"
        )
    return [round(v, _ROUND) for v in out]


# --------------------------------------------------------------------------- #
# locators: the debugging link, and its failure modes
# --------------------------------------------------------------------------- #
def _view_targets(view: View) -> tuple[set[str] | None, str]:
    """What this view declares as addressable, and what those things are called.

    ``None`` means the view publishes no namespace, so a target cannot be
    checked — an image with no hotspot list, a diagram whose element ids live
    inside its SVG. Unverifiable is NOT reported as a problem: a false "unknown
    node" on every locator would train readers to ignore the problem list, and
    then the real ones go past unread too.
    """
    kind = ViewKind(view.kind)
    meta = view.meta or {}
    data = view.data or {}

    if kind in (ViewKind.MODEL3D, ViewKind.FIELD):
        names = list(meta.get("nodes") or [])
        explode = meta.get("explode")
        if isinstance(explode, dict):
            # A locator may legitimately address a mover ("lid") as well as a
            # node ("lid__boss_a"): the mover is what the viewer isolates.
            for mover, record in (explode.get("movers") or {}).items():
                names.append(str(mover))
                names.extend(str(n) for n in (record or {}).get("nodes") or [])
        return (({str(n) for n in names}) or None), "node"

    if kind is ViewKind.IMAGE:
        spots = data.get("hotspots") or []
        ids = {str(h.get("id")) for h in spots if isinstance(h, dict) and h.get("id")}
        return (ids or None), "hotspot"

    if kind is ViewKind.CHART:
        series = data.get("series")
        if isinstance(series, dict):
            return ({str(k) for k in series} or None), "series"
        if isinstance(series, list):
            keys = {str(s.get("key") or s.get("name")) for s in series
                    if isinstance(s, dict) and (s.get("key") or s.get("name"))}
            return (keys or None), "series"
        return None, "series"

    if kind is ViewKind.TABLE:
        rows = data.get("rows") or []
        ids = {str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")}
        return (ids or None), "row"

    elements = data.get("elements") or meta.get("elements") or []
    return ({str(e) for e in elements} or None), "element"


def locator_problems(views: Iterable[View], verdicts: Iterable[Verdict]) -> list[dict]:
    """Every locator that cannot be drawn, with the gate that emitted it.

    **A locator is never dropped for being unusable.** It stays in
    ``state.json`` and the problem is published beside it, because the two
    failure modes this catches are both invisible from the inside:

    * a locator naming a view that does not exist — the gate believes it is
      drawing, the page draws nothing, and *a gate that thinks it is drawing and
      is not looks exactly like a gate that found nothing*. The pack author sees
      a clean import; the reader sees an unannotated verdict.
    * a locator naming a node the view does not declare — usually the node
      naming scheme moved on one side of the interface. ``PACK.md`` publishes
      that scheme precisely because it is an interface between a viewgen and
      every gate in its domain, and interfaces drift.

    Returns one record per bad locator::

        {"gate": "cad.clash", "view": "assembly", "target": "carriage",
         "severity": "unknown-target",
         "problem": "view 'assembly' declares no node 'carriage'"}
    """
    by_id = {v.id: v for v in views}
    namespaces = {vid: _view_targets(view) for vid, view in by_id.items()}

    out: list[dict] = []
    for verdict in verdicts:
        for locator in (verdict.locators or []):
            if not isinstance(locator, Locator):
                locator = Locator.from_dict(locator)         # tolerate a raw dict
            view_id = (locator.view or "").strip()
            if not view_id:
                out.append({
                    "gate": verdict.gate, "view": "", "target": locator.target,
                    "severity": "missing-view",
                    "problem": "locator names no view, so nothing can be highlighted",
                })
                continue
            if view_id not in by_id:
                known = ", ".join(sorted(by_id)[:6]) or "(no views were built)"
                out.append({
                    "gate": verdict.gate, "view": view_id, "target": locator.target,
                    "severity": "missing-view",
                    "problem": f"names view {view_id!r}, which no view generates. "
                               f"Built views: {known}",
                })
                continue
            target = (locator.target or "").strip()
            if not target:
                continue            # the whole view; always addressable
            names, label = namespaces[view_id]
            if names is not None and target not in names:
                out.append({
                    "gate": verdict.gate, "view": view_id, "target": target,
                    "severity": "unknown-target",
                    "problem": f"view {view_id!r} declares no {label} {target!r}",
                })
    return out


# --------------------------------------------------------------------------- #
# the editor's launch entry
# --------------------------------------------------------------------------- #
#: Where an editor looks for "how do I run this project": VS Code's Run and Debug.
#: What slipped through: a groundspace built end to end had a site and no way to
#: open it short of knowing `nopekit site serve` existed; the person looking at it
#: in their editor found nothing to press. *Rejected:* `.claude/launch.json` (the
#: projects this was asked for keep theirs in `.vscode/`), and serving `site/` with
#: `python3 -m http.server` (it loses `site serve`'s no-store and `.js` MIME fixes,
#: which are the difference between a current page and a blank one).
LAUNCH_PATH = os.path.join(".vscode", "launch.json")

#: The line `nopekit site serve` prints once its socket is listening, and the
#: pattern the launch entry waits for. Kept beside each other so one cannot move
#: without the other: `tests/test_launch.py` runs the real server and matches its
#: first line with this pattern.
SERVE_READY_LINE = "serving {served} at {url}"
SERVE_READY_RE = r"serving \S+ at (https?://\S+)"


def launch_config(name: str, *, src_root: str | None = None) -> dict:
    """The `.vscode/launch.json` a groundspace ships: build the site, serve it on a
    free port, and let the editor open the browser when the server says it is up.

    Shaped after VS Code's `node-terminal` launch with a `serverReadyAction`, because
    that runs any shell command with no extension and opens the browser on the URL
    the server printed. Each choice, and what it is for:

    * `site build` first, `;` not `&&`: the page should show the ledger as it is
      now, and a model that does not build should still open on the last page that
      did (the page says how old its data is).
    * `-p 0`: another groundspace's site may already hold 8000; the bound port is
      what the server prints and what the pattern captures.
    * `--no-browser`: the editor opens it; two windows otherwise.
    * the command finds `nopekit` on PATH, else runs it as a module from
      `src_root` (the nopekit this file was written by), so the entry works for an
      installed nopekit and for one run from a checkout. The fallback path is
      machine-specific; `nopekit site init` rewrites nothing, so delete the file
      and run `nopekit site build` to regenerate it on another machine.
    """
    a = "$A"
    find = ('command -v nopekit >/dev/null 2>&1 && A=nopekit || A="python3 -m nopekit"; ')
    command = f"{find}{a} site build; {a} site serve -p 0 --no-browser"
    env = {"NO_COLOR": "1"}
    if src_root:
        env["PYTHONPATH"] = src_root

    def one(browser: str, action: str) -> dict:
        return {
            "name": f"{name} site — {browser}",
            "type": "node-terminal",
            "request": "launch",
            "command": command,
            "cwd": "${workspaceFolder}",
            "env": dict(env),
            "serverReadyAction": {
                "pattern": SERVE_READY_RE,
                "uriFormat": "%s",
                "action": action,
                "webRoot": "${workspaceFolder}/" + SITE_DIR,
                "killOnServerStop": True,
            },
        }

    return {"version": "0.2.0",
            "configurations": [one("Chrome", "debugWithChrome"), one("Edge", "debugWithEdge")]}


def ensure_launch(root: str, name: str) -> str | None:
    """Write `<root>/.vscode/launch.json` when there is none. Returns the path
    written (relative), or None when a file was already there.

    Never edits an existing one: it is the person's (often JSON with comments, which
    this cannot round-trip), and a launch entry silently rewritten is the editor's
    version of a hand-edited generated file.
    """
    path = os.path.join(root, LAUNCH_PATH)
    if os.path.exists(path):
        return None
    src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ensure_dir(os.path.dirname(path))
    atomic_write_text(path, json.dumps(launch_config(name, src_root=src_root),
                                       indent=2, ensure_ascii=False) + "\n")
    return LAUNCH_PATH.replace(os.sep, "/")


# --------------------------------------------------------------------------- #
# scaffolding
# --------------------------------------------------------------------------- #
def scaffold(root: str, *, force: bool = False) -> list:
    """Copy the site template into ``<root>/site/``. Returns the paths written.

    **Refuses to overwrite an existing ``site/index.html`` unless ``force``.**
    That file is the shell, and the contract says it plainly: *hackable, yours,
    never regenerated after init*. Someone who has spent an afternoon on their
    project's page must not lose it to a command they ran to get the new
    renderer, and a scaffold that silently rewrote it would be the same class of
    mistake as hand-editing a generated file — in the opposite direction.

    The other shells (`SHELLS`: the engineering and marketing pages, from the
    two pages, and ``technical.html``) are written when absent and kept when
    present. Everything else in the template (``app.js``, ``style.css``, and
    anything a later template adds) IS refreshed. Those are the renderer: they are ours,
    they get fixes, and a project that could never receive one would be stuck
    with the bugs of the day it was created. ``force`` replaces every shell, the four of them.

    ``data/`` and ``assets/`` are created empty so a freshly scaffolded site
    serves without a build, and ``.gitignore`` is written only when absent —
    ignoring ``vendor/`` and deliberately not ignoring the generated JSON, which
    IS the published site.
    """
    site = os.path.join(root, SITE_DIR)
    index = os.path.join(site, "index.html")
    if os.path.exists(index) and not force:
        # The refusal names every shell `force` replaces, and leads with `site
        # build`. What slipped through (review of SPEC 40): it still said
        # "--force to replace it" after `force` came to replace the four
        # shells, so a person who wanted a renderer fix read it as losing only
        # the root redirect and lost their engineering and marketing pages.
        shells = ", ".join(posixpath.join(SITE_DIR, s) for s in SHELLS)
        raise NopekitError(
            f"{posixpath.join(SITE_DIR, 'index.html')} already exists: this project "
            f"has a site, and its shells are yours. `nopekit site build` refreshes "
            f"the renderer and the data and writes a missing page, without touching "
            f"a shell. `--force` replaces the four shells ({shells}); your edits to "
            f"them are not recoverable afterwards."
        )
    if not os.path.isdir(TEMPLATE_DIR):             # pragma: no cover - packaging
        raise NopekitError(
            f"the site template is missing from the installed package "
            f"({TEMPLATE_DIR}). This is a packaging fault, not a project fault: "
            f"reinstall nopekit."
        )

    written: list[str] = []
    for source, relative in _template_files():
        target = os.path.join(site, *relative.split("/"))
        # `technical.html` is a shell too (architecture phase 5), and from the
        # two pages `engineering/index.html` and `marketing/index.html`: each
        # written when absent, or with `force`; a project's own copy is never
        # replaced. The root `index.html` (the redirect) is written here always:
        # the refusal above has already kept a project's own.
        if relative in SHELLS and relative != "index.html" and os.path.exists(target) \
                and not force:
            continue
        ensure_dir(os.path.dirname(target))
        shutil.copyfile(source, target)
        written.append(posixpath.join(SITE_DIR, relative))

    for directory in (DATA_DIR, VIEWS_DIR, ASSETS_DIR, CONTENT_DIR):
        ensure_dir(os.path.join(site, directory.replace("/", os.sep)))

    gitignore = os.path.join(site, ".gitignore")
    if not os.path.exists(gitignore):
        atomic_write_text(gitignore, _GITIGNORE)
        written.append(posixpath.join(SITE_DIR, ".gitignore"))
    elif ensure_site_ignore(root):
        written.append(posixpath.join(SITE_DIR, ".gitignore"))

    return sorted(written)


def scaffolded(root: str) -> bool:
    """Whether ``root`` has a site: ``site/index.html`` is a file. The one
    predicate `round`, `status`, `doctor`, `site build` and `site serve` ask
    (architecture phase 5): a project that tracks only ``site/content/`` (the
    bracket) has text for a page and no page, so it has no site. From the two
    pages the root is a redirect `site init` always writes, and a project from
    before them has its own front page there, so the root alone still decides.
    *Rejected:* the root or ``engineering/index.html`` (invariant 8 says
    `round` writes what `site build` writes when ``site/index.html`` exists:
    widening the predicate would widen `round`'s writes)."""
    return os.path.isfile(os.path.join(root, SITE_DIR, "index.html"))


def outline_file(root: str) -> str:
    """The page that carries the outline, relative to ``root``: the engineering
    page (``site/engineering/index.html``) when it exists, else the root
    ``site/index.html`` (a project from before the two pages, not yet built
    again). What every outline reader reads and names."""
    engineering = posixpath.join(SITE_DIR, "engineering", "index.html")
    if os.path.isfile(os.path.join(root, *engineering.split("/"))):
        return engineering
    return posixpath.join(SITE_DIR, "index.html")


def _marker(path: str, pattern: str) -> int | None:
    import re
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            found = re.search(pattern, fh.read())
    except OSError:
        return None
    return int(found.group(1)) if found else None


def outline_of(root: str) -> int | None:
    """The outline the engineering page carries (`OUTLINE_MARKER`, read from
    `outline_file`), or None when it carries none (a shell older than the
    outline, or the project's own)."""
    return _marker(os.path.join(root, *outline_file(root).split("/")), OUTLINE_MARKER_RE)


def outline_problem(root: str) -> str:
    """The warning for an engineering page older than the outline, or ``""``:
    one sentence for `site build`, `site status` and `doctor`, naming the file
    it read (`outline_file`). The way out writes only that page: delete it and
    build, which writes an absent shell; `site init --force` would replace all
    four shells, the person's `technical.html` too."""
    marker = outline_of(root)
    if marker is not None and marker >= page_logic.OUTLINE:
        return ""
    shown = outline_file(root)
    if shown == posixpath.join(SITE_DIR, "index.html"):
        how = "run `nopekit site build`, which writes engineering/ with the outline"
    else:
        how = f"keep a copy of {shown}, delete it, then run `nopekit site build`"
    return (f"{shown} predates the page outline (no nopekit-outline marker): "
            f"data/{PAGE_NAME} and data/{MAP_NAME} are written, and no page shows them. "
            f"To show them, {how}.")


def old_layout(root: str) -> bool:
    """Whether the root ``site/index.html`` is a front page from before the two
    pages: it exists and does not carry `VIEWS_MARKER`. Its address then opens
    that page, not the engineering page; nothing here replaces it."""
    path = os.path.join(root, SITE_DIR, "index.html")
    return os.path.isfile(path) and _marker(path, VIEWS_MARKER_RE) is None


#: The old-layout warning, one sentence set for `site build`, `site status` and
#: `doctor`. It says to move the page aside and run `site init`, never `--force`:
#: `init` without it writes the absent root and keeps every shell that exists,
#: where `--force` replaces the engineering, marketing and technical pages too
#: (the critics' review of the plan: the first wording destroyed the work it
#: was warning about). It says what `site build` does, never what it did:
#: `site status` and `doctor` print it before any build too. What slipped
#: through (review of SPEC 40): "The site build wrote engineering/ and
#: marketing/ beside it", printed by both on a project that had just upgraded,
#: where neither directory existed.
OLD_LAYOUT_WARNING = (f"{SITE_DIR}/index.html is a front page from before the engineering and "
                      f"marketing pages. `nopekit site build` writes engineering/ and "
                      f"marketing/ beside it, when absent. To make the address / open the engineering page, move "
                      f"{SITE_DIR}/index.html aside, then run nopekit site init.")


def page_urls(url: str) -> dict:
    """``{name: address}`` of the two pages under a served root ``url`` (which
    ends in ``/``): what `site serve` prints after its ready line and puts in
    its JSON."""
    base = url if url.endswith("/") else url + "/"
    return {name: base + path for name, path in PAGES}


def pages_line(url: str) -> str:
    """`site serve`'s line after the ready line: the two pages' addresses. It
    never matches `SERVE_READY_RE` (the editor's launch entry opens the first
    address that matches), so the ready line stays the one the editor reads."""
    found = page_urls(url)
    return "pages: " + " and ".join(f"{found[name]} ({name})" for name, _path in PAGES)


def ensure_site_ignore(root: str, *, live: bool = False) -> bool:
    """Append the clock file's ignore lines to ``site/.gitignore`` when no line
    equals ``data/build.json`` or ``/data/build.json``, creating the file with
    only those lines when there is none; True when it wrote. Every other byte
    stays as it was. What slipped through (review of phase 5): a missing
    ``site/.gitignore`` (the scaffolded one said to delete it for an offline
    archive) was left missing, so ``build.json``, which changes on every
    build, dirtied ``git status`` after every rebuild.

    With ``live`` (a bound viewgen is registered, issue #15), the live 3D
    folder's lines too (`_LIVE_IGNORE`), when no line equals ``assets/live/``
    or ``/assets/live/``. Only then: a project that draws no bound figure
    keeps its ignore file as it was. *Rejected:* the line in every project's
    file (every existing project's tracked file would move for a folder it
    never has)."""
    path = os.path.join(root, SITE_DIR, ".gitignore")
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
    except FileNotFoundError:
        if not os.path.isdir(os.path.join(root, SITE_DIR)):
            return False
        text = ""
    lines = {line.strip() for line in text.splitlines()}
    blocks = []
    if not lines & {"data/build.json", "/data/build.json"}:
        blocks.append(_BUILD_IGNORE)
    if live and not lines & {"assets/live/", "/assets/live/", "assets/live", "/assets/live"}:
        blocks.append(_LIVE_IGNORE)
    if not blocks:
        return False
    body = text if not text or text.endswith("\n") else text + "\n"
    for block in blocks:
        body += ("\n" if body.strip() else "") + block
    atomic_write_text(path, body)
    return True


def _evidence_plan(root: str, view: Any, resolution: Any, registry: Any) -> list:
    """`page.evidence_plan` with git's ignore rules: a path in the project that
    git ignores and does not track is never published (`vcs.ignored`); and
    with the evidence manifests `check` wrote (`verdicts.evidence_manifest`),
    so a file is copied only as the bytes its entry's run cited (issue #15)."""
    asked: dict[str, bool] = {}

    def ignored(relpath: str) -> bool:
        if relpath not in asked:
            found = vcs.ignored(root, [relpath])
            asked[relpath] = bool(found) and relpath in found
        return asked[relpath]

    def manifest(gate_id: str, entry_name: str) -> Any:
        return verdict_logic.evidence_manifest(root, gate_id, entry_name)

    return page_logic.evidence_plan(root, view, resolution, registry, ignored=ignored,
                                    manifest=manifest)


def _kept_json(site: str, keep: Iterable[str]) -> list:
    """The JSON documents among ``keep`` (paths under ``site/assets/``, as
    `clean_assets` takes them), read; one that does not parse is left out.

    A bound figure's files (``assets/figures/``, ``assets/live/``) are left out
    too (issue #15, B.1.4): a sidecar's colour range, a tick, a chart's series
    are a pack's constants or a run's raw numbers, and none of them is a
    result a sentence may quote. *Rejected:* pooling them (a colour bound of
    0.02 m/s would vouch for "0.02 m/s" in prose about anything)."""
    docs: list[Any] = []
    seen: set[str] = set()
    for entry in keep:
        text = str(entry).replace("\\", "/").strip()
        if not text.endswith(".json"):
            continue
        if not text.startswith(ASSETS_DIR + "/"):
            text = posixpath.join(ASSETS_DIR, text)
        text = posixpath.normpath(text)
        if text.startswith((page_logic.FIGURES_DIR + "/", page_logic.LIVE_DIR + "/")):
            continue
        if text in seen:
            continue
        seen.add(text)
        try:
            with open(os.path.join(site, *text.split("/")), "r", encoding="utf-8") as fh:
                docs.append(json.load(fh))
        except (OSError, ValueError):
            continue
    return docs


def prose_pool(docs: Iterable[Any], assets: Iterable[Any] = ()) -> Any:
    """The number pool prose is matched against (`prose.number_pool`): the
    baked documents (``state.json`` with its view files, ``map.json``,
    ``page.json`` without its prose) and the asset JSON the page keeps."""
    return prose_logic.number_pool([*docs, *assets])


def baked_pool(root: str, map_doc: Mapping[str, Any], page_doc: Mapping[str, Any]) -> Any:
    """`prose_pool` over a built site as it is on disk, for a reader that
    checks a baked page's number marks (`page.status_problems`): its
    ``state.json``, its view files, ``map.json``, ``page.json`` with every
    prose piece left out, and every JSON file under ``assets/`` (after a
    build, exactly the ones it kept)."""
    site = os.path.join(root, SITE_DIR)
    docs: list[Any] = []
    try:
        with open(os.path.join(site, DATA_DIR, STATE_NAME), "r", encoding="utf-8") as fh:
            docs.append(json.load(fh))
    except (OSError, ValueError):
        pass
    views_dir = os.path.join(site, VIEWS_DIR.replace("/", os.sep))
    if os.path.isdir(views_dir):
        for name in sorted(os.listdir(views_dir)):
            if name.endswith(".json"):
                try:
                    with open(os.path.join(views_dir, name), "r", encoding="utf-8") as fh:
                        docs.append(json.load(fh))
                except (OSError, ValueError):
                    continue
    bare = json.loads(json.dumps(page_doc))
    for section in bare.get("sections") or ():
        section["prose"] = None
    for subject in page_logic.PROSE_SUBJECTS:
        if subject == "next":
            if isinstance(bare.get("next"), dict):
                bare["next"]["prose"] = None
        else:
            bare[subject] = None
    assets: list[str] = []
    for directory, _dirs, names in os.walk(os.path.join(site, ASSETS_DIR)):
        for name in sorted(names):
            if name.endswith(".json"):
                assets.append(os.path.relpath(os.path.join(directory, name), site)
                              .replace(os.sep, "/"))
    return prose_pool([*docs, map_doc, bare], _kept_json(site, assets))


def ages(resolution: Any, now: str) -> dict:
    """``{gate: {when, age_s, aged}}`` over the resolution's rows: ``when`` the
    resolver's (the obs run that last hit or wrote the entry, else its commit
    time), ``age_s`` `_age_seconds` (null when unknown, never 0), ``aged`` at
    `AGED_SECONDS` or more. For `build.json` only: the clock never reaches a
    tracked file."""
    out: dict[str, dict] = {}
    for gid in sorted((getattr(resolution, "rows", None) or {}), key=store._natural):
        row = resolution.rows[gid]
        when = str(getattr(row, "when", "") or "")
        age = _age_seconds(now, when)
        out[gid] = {"when": when, "age_s": age,
                    "aged": bool(age is not None and age >= AGED_SECONDS)}
    return out


def _mixed_age(verdicts: Mapping[str, dict]) -> dict:
    """The mixed-age banner's decision, baked (SPEC 32): shown when at least two
    results have an age, the oldest is aged, and it is more than four times the
    newest — the template's `mixedAgeBanner` rule, moved here."""
    found = [(gid, row["age_s"]) for gid, row in verdicts.items()
             if isinstance(row.get("age_s"), (int, float))]
    if len(found) < 2:
        return {"shown": False, "oldest_s": None, "newest_s": None, "gates": []}
    oldest = max(age for _gid, age in found)
    newest = min(age for _gid, age in found)
    shown = oldest >= AGED_SECONDS and oldest > newest * 4
    return {"shown": bool(shown), "oldest_s": oldest, "newest_s": newest,
            "gates": [gid for gid, age in found if age >= AGED_SECONDS] if shown else []}


def write_build_json(root: str, *, now: str, id: str, ok: bool = True, error: str = "",
                     live: bool = False, verdicts: Mapping[str, dict] | None = None,
                     mixed_age: Mapping[str, Any] | None = None,
                     tracing: bool = False,
                     live_figures: Mapping[str, str] | None = None) -> str:
    """Write ``site/data/build.json`` (ignored, SPEC 32-33): the build time, the
    live flag the page polls by, an ``id`` over the tracked baked bytes (the
    page reloads its data when it moves), ok and error, each verdict's age, and
    ``tracing`` — a trace holds its lock now (architecture phase 3b), so the
    live page can say its links are being measured. And ``live_figures``
    (issue #15, B.2.4): ``{figure id: site path}`` of each live 3D file on this
    computer, never in a tracked file, so a static host never shows a 3D
    button that leads nowhere. Returns its site path."""
    verdicts = dict(verdicts or {})
    doc = {"schema": 1, "built": now, "live": bool(live), "id": id, "ok": bool(ok),
           "error": str(error or ""), "verdicts": verdicts,
           "mixed_age": dict(mixed_age) if mixed_age is not None else _mixed_age(verdicts),
           "tracing": bool(tracing),
           "live_figures": {k: live_figures[k] for k in sorted(live_figures or {})}}
    path = os.path.join(root, SITE_DIR, DATA_DIR, BUILD_NAME)
    ensure_dir(os.path.dirname(path))
    atomic_write_json(path, doc)
    return posixpath.join(SITE_DIR, DATA_DIR, BUILD_NAME)


def _baked_id(site: str) -> str:
    """The seal over ``{relpath: sha256}`` of the tracked baked files: the
    three documents and every ``data/views/*.json``."""
    import hashlib
    files: dict[str, str] = {}
    data = os.path.join(site, DATA_DIR)
    names = [STATE_NAME, MAP_NAME, PAGE_NAME]
    views = os.path.join(site, VIEWS_DIR.replace("/", os.sep))
    if os.path.isdir(views):
        names += [posixpath.join("views", n) for n in sorted(os.listdir(views))
                  if n.endswith(".json")]
    for name in names:
        path = os.path.join(data, *name.split("/"))
        try:
            with open(path, "rb") as fh:
                files[name] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            continue
    return hashlib.sha256(("nopekit-build-v1\n" + json.dumps(files, sort_keys=True))
                          .encode("utf-8")).hexdigest()[:16]


def watch_snapshot(root: str) -> dict:
    """``{relpath: (size, mtime_ns)}`` over `WATCH_DIRS` — what `site serve
    --watch` compares between polls. ``__pycache__`` and ``*.pyc`` are left
    out; a missing path is simply absent."""
    out: dict[str, tuple] = {}
    for rel in WATCH_DIRS:
        path = os.path.join(root, *rel.split("/"))
        if os.path.isfile(path):
            st = os.stat(path)
            out[rel] = (st.st_size, st.st_mtime_ns)
            continue
        if not os.path.isdir(path):
            continue
        for directory, dirnames, filenames in os.walk(path):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for name in filenames:
                if name.endswith(".pyc"):
                    continue
                full = os.path.join(directory, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                out[os.path.relpath(full, root).replace(os.sep, "/")] = (st.st_size,
                                                                        st.st_mtime_ns)
    return out


def refresh_renderer(root: str) -> list:
    """Bring every renderer file under ``<root>/site/`` up to the installed
    template — everything but the `SHELLS`, which are yours — and
    return the site paths it rewrote (``[]`` when each already matched).

    ``scaffold``'s docstring always said the renderer "IS refreshed"; nothing
    did it after ``site init``, which refuses while ``index.html`` exists. What
    slipped through (review of the P2.1 design): a page scaffolded before P2.1
    kept a ``format.js`` that read ``blocked`` as "its tooling is missing" in a
    missing tool's tone and took READY from ``readiness.ready`` — so once P2.1
    filed a crash under ``blocked``, that page read a crash exactly like a
    missing tool, and a project waiting for an article READY. ``site build`` is
    the command every such project runs, so it refreshes the renderer, byte for
    byte, before it writes the data the renderer reads. *Rejected:* a
    ``--refresh`` flag on ``site init`` (a step nobody knows to take); refusing
    the build when the renderer differs (the fix is mechanical, and the shell
    stays untouched either way)."""
    site = os.path.join(root, SITE_DIR)
    out: list[str] = []
    for source, relative in _template_files():
        if relative in SHELLS:
            continue
        target = os.path.join(site, *relative.split("/"))
        with open(source, "rb") as fh:
            want = fh.read()
        try:
            with open(target, "rb") as fh:
                if fh.read() == want:
                    continue
        except OSError:
            pass
        ensure_dir(os.path.dirname(target))
        _atomic_write_bytes(target, want)
        out.append(posixpath.join(SITE_DIR, relative))
    return sorted(out)


def _template_files() -> list[tuple[str, str]]:
    """``(absolute source, site-relative destination)`` for every template file.

    Walked rather than enumerated. A hardcoded list of three filenames is how a
    template's new file silently fails to ship — the same way a pack-root helper
    module once failed to make it into the wheel and every gate in that pack died
    on import. Dotfiles and ``__pycache__`` are skipped: the template is data,
    and Python never executes it from here.
    """
    out: list[tuple[str, str]] = []
    for directory, dirnames, filenames in os.walk(TEMPLATE_DIR):
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and d != "__pycache__")
        for name in sorted(filenames):
            if name.startswith(".") or name.endswith(".pyc"):
                continue
            source = os.path.join(directory, name)
            relative = os.path.relpath(source, TEMPLATE_DIR).replace(os.sep, "/")
            out.append((source, relative))
    return out


# --------------------------------------------------------------------------- #
# bound viewgens: drawn from one verdict entry, checked, published (issue #15)
# --------------------------------------------------------------------------- #
def _recipe_digest(spec: ViewSpec, fn: Callable[..., Any]) -> tuple[str, str]:
    """``(digest, problem)``: sha256 over `FIGURE_RECIPE_SALT`, the viewgen
    module's bytes and each ``recipe`` file's bytes (resolved against the
    module's ``PACK_DIR``, the pack or the project). The memo's key with the
    gate, the entry and the view (B.2.1): a scene edited draws again; a tool
    installed elsewhere does not (C6: every locked build draws the same
    bytes). ``problem`` names a recipe file that is not there."""
    module = sys.modules.get(getattr(fn, "__module__", "") or "")
    path = str(getattr(module, "__file__", "") or "")
    base = str(getattr(module, "PACK_DIR", "") or (os.path.dirname(os.path.dirname(path))
                                                   if path else ""))
    digest = hashlib.sha256(f"{FIGURE_RECIPE_SALT}\n{spec.id}\n".encode("utf-8"))
    for label, target in (("module", path), *((rel, os.path.join(base, *rel.split("/")))
                                               for rel in spec.recipe)):
        payload = _file_bytes(target) if target else None
        if payload is None:
            if label == "module" and not path:
                continue
            return "", f"its recipe file {label} is not there"
        digest.update(f"{label}\n".encode("utf-8"))
        digest.update(hashlib.sha256(payload).hexdigest().encode("ascii"))
    return digest.hexdigest(), ""


def _exported_entries(ledger: Ledger, gate_id: str) -> set[str]:
    """The entry names of ``gate_id`` that an export re-ran (``exports/*.json``
    ``reran`` rows): the only entries whose GLB is committed (B.2.4)."""
    out: set[str] = set()
    for record in getattr(ledger, "exports", None) or ():
        for row in getattr(record, "reran", None) or ():
            if isinstance(row, Mapping) and row.get("gate") == gate_id and row.get("out8"):
                out.add(f"{str(row.get('rho') or '')[:verdict_logic.RHO_CHARS]}-{row['out8']}")
    return out


def _bound_entry(root: str, gate_id: str, entry: Any, fresh: bool) -> tuple[BoundEntry, dict]:
    """The `BoundEntry` of ``entry`` and ``{portable path: sha256}`` of its
    verified files: an evidence path of the entry's verdict is handed over only
    when its bytes are the manifest's (`verdicts.evidence_manifest`), the
    rule `page.evidence_plan` copies by."""
    found = verdict_logic.evidence_manifest(root, gate_id, entry.name)
    bound = BoundEntry(gate=gate_id, name=entry.name, fresh=bool(fresh))
    digests: dict[str, str] = {}
    for text in (entry.verdict or {}).get("evidence") or ():
        text = str(text)
        source, outside, why = page_logic.evidence_source(root, text)
        if outside or source is None or not os.path.isfile(source):
            bound.withheld[text] = why or ("it is outside the project" if outside
                                           else "it is not on this computer")
            continue
        sha = page_logic._sha256_of(source)
        want = found.get(text) if isinstance(found, Mapping) else None
        if found is None:
            bound.withheld[text] = page_logic._NO_RECORDED_RUN
        elif sha is None or not isinstance(want, Mapping) or want.get("sha256") != sha[0]:
            bound.withheld[text] = page_logic._ANOTHER_RUN.format(gate=gate_id)
        else:
            bound.files[text] = source
            digests[text] = sha[0]
    return bound, digests


def _numbers(node: Any) -> list[float]:
    """Every finite number under ``node`` (bools are not numbers)."""
    out: list[float] = []
    if isinstance(node, bool):
        return out
    if isinstance(node, (int, float)):
        if math.isfinite(node):
            out.append(float(node))
    elif isinstance(node, Mapping):
        for value in node.values():
            out += _numbers(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            out += _numbers(value)
    return out


def _evidence_numbers(files: Iterable[str]) -> set[float]:
    """Every number a verified evidence file holds: a JSON file's leaves, a
    CSV file's cells. Another kind of file holds none."""
    out: set[float] = set()
    for path in files:
        payload = _file_bytes(path)
        if payload is None:
            continue
        lower = path.lower()
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if lower.endswith(".json"):
            try:
                out.update(_numbers(json.loads(text)))
            except ValueError:
                continue
        elif lower.endswith(".csv"):
            for row in csv.reader(text.splitlines()):
                for cell in row:
                    try:
                        value = float(cell)
                    except ValueError:
                        continue
                    if math.isfinite(value):
                        out.add(value)
    return out


def _keys(node: Any) -> set[str]:
    """Every key of every object under ``node``."""
    out: set[str] = set()
    if isinstance(node, Mapping):
        for key, value in node.items():
            out.add(str(key))
            out |= _keys(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            out |= _keys(value)
    return out


def _caption_status(caption: str) -> str:
    """`page.caption_status`: the first status word or phrase ``caption``
    holds, or ``""``."""
    return page_logic.caption_status(caption)


def _png_ok(payload: bytes) -> bool:
    """A PNG signature, then an IHDR chunk of 13 bytes whose width and height
    are not zero: enough to know the file is the picture it says it is. The
    pixels are the pack's (its renderer control holds them, Phase 2)."""
    if len(payload) < 33 or not payload.startswith(PNG_SIGNATURE):
        return False
    length, kind = struct.unpack(">I4s", payload[8:16])
    width, height = struct.unpack(">II", payload[16:24])
    return length == 13 and kind == b"IHDR" and width > 0 and height > 0


def _solid_problem(name: str, payload: bytes) -> str:
    """Why ``payload`` is not the 3D file its ``name`` says, or ``""``. A
    ``.glb`` starts with `GLB_MAGIC`. A ``.gltf`` is a JSON object whose
    ``asset.version`` is ``2.0`` and whose every buffer and image holds its
    bytes as a ``data:`` URI (`SOLID_SUFFIXES`): the page then fetches this
    one file and nothing else. The geometry is the tool's; the Khronos
    validator holds it in the tests."""
    if name.lower().endswith(".glb"):
        return "" if payload.startswith(GLB_MAGIC) else f"{name} is not a GLB"
    try:
        doc = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return f"{name} is not a glTF: it is not JSON"
    asset = doc.get("asset") if isinstance(doc, dict) else None
    if not isinstance(asset, dict) or asset.get("version") != "2.0":
        return f"{name} is not a glTF 2.0"
    for group in ("buffers", "images"):
        rows = doc.get(group) or []
        if not isinstance(rows, list):
            return f"{name} is not a glTF: its {group} is not a list"
        for row in rows:
            uri = row.get("uri") if isinstance(row, dict) else None
            if uri is not None and not (isinstance(uri, str) and uri.startswith("data:")):
                return (f"{name} names a file outside it ({str(uri)[:60]!r}); a glTF on the "
                        f"page holds its bytes inside it")
    return ""


def _figure_refusal(spec: ViewSpec, view: View, ctx: BoundContext,
                    limits: Iterable[Any]) -> str:
    """Why the spine refuses to publish what a bound viewgen returned (B.2.3),
    or ``""``. Each refusal is a warning and publishes nothing; the page keeps
    the figure it had (`page.figure_for`)."""
    if ctx.refused:
        return ctx.refused[0]
    for name, (path, _live) in ctx.outputs.items():
        size = os.path.getsize(path) if os.path.isfile(path) else -1
        if size > page_logic.EVIDENCE_MAX_BYTES:
            return f"{name} is over the 2 MiB cap"
    kind = ViewKind(spec.kind)
    pngs = [n for n in ctx.outputs if n.lower().endswith(".png")]
    glbs = [n for n in ctx.outputs if n.lower().endswith(SOLID_SUFFIXES)]
    others = [n for n in ctx.outputs if n not in pngs and n not in glbs]
    if others:
        return (f"{others[0]} is not a PNG, a GLB or a glTF; a bound figure writes nothing "
                f"else")
    if len(pngs) > 1 or len(glbs) > 1:
        return "a bound figure writes at most one PNG and one 3D file (a GLB or a glTF)"
    for name in pngs:
        if not _png_ok(_file_bytes(ctx.outputs[name][0]) or b""):
            return f"{name} is not a PNG"
        if ctx.outputs[name][1]:
            return f"{name} is a still written with write_live; only a 3D file is live"
    for name in glbs:
        problem = _solid_problem(name, _file_bytes(ctx.outputs[name][0]) or b"")
        if problem:
            return problem
    if kind is ViewKind.CHART:
        if view.src or pngs:
            return "a chart names no file; its series are its payload"
        if not isinstance(view.data, dict) or not view.data:
            return "a chart with no series"
        planted = sorted(_keys(view.data) & CHART_FORBIDDEN_KEYS)
        if planted:
            return (f"its chart holds {planted[0]!r}: a chart's limit and its point are the "
                    f"claim's, set by nopekit")
        held = _evidence_numbers(ctx.entry.files.values())
        for key in CHART_NUMBER_KEYS:
            for number in _numbers(view.data.get(key)):
                if float(json.loads(json.dumps(number))) not in held:
                    return (f"the chart holds {number!r}, which no evidence file of this "
                            f"result holds")
    else:
        if view.data:
            return "a bound picture carries no payload; its numbers are its evidence's"
        want = pngs if kind is ViewKind.IMAGE else glbs
        if view.src not in want:
            return (f"its src {view.src!r} names no "
                    f"{'PNG' if kind is ViewKind.IMAGE else '3D file'} it wrote")
    moved = ctx.moved()
    if moved:
        return f"the inputs moved while the picture was drawn ({moved[0]})"
    meta = view.meta if isinstance(view.meta, Mapping) else {}
    scale = meta.get("scale")
    if scale is not None and not isinstance(scale, Mapping):
        return "its colour scale is not an object"
    shape = page_logic.scales_problem(scale, meta.get("scales"), meta.get("light"))
    if shape:
        return shape
    # Every scale the figure states, the one ``scale`` and each of
    # ``scales`` (design P2), is held to the same rule: no end equals a limit
    # and no source is keyed to one. What it closes before it could slip
    # through: a second ramp on the hero, its top at the claim's limit, under
    # a check that read ``scale`` alone.
    for each in [scale or {}, *(meta.get("scales") or ())]:
        ends = [each.get(end) for end in ("lo", "hi")]
        for end in ends:
            if isinstance(end, bool) or not isinstance(end, (int, float)):
                continue
            for limit in limits:
                if isinstance(limit, bool) or not isinstance(limit, (int, float)):
                    continue
                if math.isclose(float(end), float(limit), rel_tol=1e-9, abs_tol=0.0):
                    return f"a colour range taken from a limit ({end!r})"
        for source in each.get("source") or ():
            if isinstance(source, Mapping) and "limit" in str(source.get("key") or "").lower():
                return f"a colour range taken from a limit ({source.get('key')!r})"
    word = _caption_status(str(meta.get("caption") or ""))
    if word:
        return f"a caption may not say a status ({word!r})"
    return ""


def _json_safe(value: Any, default: Any) -> Any:
    """``value`` through a JSON round trip, or ``default`` when it is not JSON."""
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        return default


def _put_figure_file(site: str, folder: str, view_id: str, payload: bytes, ext: str,
                     written: list[str]) -> dict:
    """Write ``payload`` as ``<folder>/<view>.<sha12>.<ext>`` (when its bytes
    differ) and return its sidecar row."""
    sha = hashlib.sha256(payload).hexdigest()
    name = f"{view_id}.{sha[:12]}.{ext}"
    target = os.path.join(site, *folder.split("/"), name)
    if _file_bytes(target) != payload:
        _atomic_write_bytes(target, payload)
        written.append(posixpath.join(SITE_DIR, folder, name))
    return {"name": name, "sha256": sha, "bytes": len(payload)}


def _publish_figure(site: str, spec: ViewSpec, view: View, ctx: BoundContext, *,
                    recipe: str, exported: bool, written: list[str]) -> dict:
    """Publish what `_figure_refusal` passed into ``assets/figures/<gate>/
    <entry>/``, with its sidecar, and a 3D file (a GLB or a glTF) of an entry no
    export re-ran (or one written with `write_live`) into the ignored
    ``assets/live/<gate>/<entry>/``.
    Returns the sidecar. Every file is written only when its bytes differ."""
    gate_id, entry = ctx.entry.gate, ctx.entry.name
    folder = page_logic.figure_folder(gate_id, entry)
    files: dict[str, dict] = {}
    for name, (path, live) in ctx.outputs.items():
        payload = _file_bytes(path) or b""
        if name.lower().endswith(".png"):
            files["image"] = _put_figure_file(site, folder, spec.id, payload, "png", written)
        else:
            committed = exported and not live
            where = folder if committed else page_logic.live_folder(gate_id, entry)
            files["field"] = {**_put_figure_file(site, where, spec.id, payload,
                                                 _solid_suffix(name),
                                                 written if committed else []),
                              "live": not committed}
    if ViewKind(spec.kind) is ViewKind.CHART:
        payload = (json.dumps(view.data, sort_keys=True, indent=1, ensure_ascii=False,
                              allow_nan=False) + "\n").encode("utf-8")
        files["chart"] = _put_figure_file(site, folder, spec.id, payload, "chart.json",
                                          written)
    meta = view.meta if isinstance(view.meta, Mapping) else {}
    schema = page_logic.sidecar_schema(meta)
    sidecar = {
        "schema": schema, "view": spec.id, "gate": gate_id, "entry": entry,
        "kind": ViewKind(spec.kind).value, "recipe": recipe, "files": files,
        "title": str(view.title or spec.title or ""),
        "caption": " ".join(str(meta.get("caption") or "").split()),
        "alt": " ".join(str(meta.get("alt") or "").split()),
        "scale": _json_safe(meta.get("scale") or {}, {}),
        "marks": _json_safe(meta.get("marks") or [], []),
        "tool": _json_safe(meta.get("tool") or {}, {}),
    }
    if schema >= 2:
        # Schema 2 (design P2): written only for a figure that states its
        # scales or its light, so a one-scale figure's sidecar keeps its bytes.
        sidecar["scales"] = _json_safe(list(meta.get("scales") or []), [])
        sidecar["light"] = str(meta.get("light") or "")
    target = os.path.join(site, *folder.split("/"), spec.id + page_logic.SIDECAR_SUFFIX)
    ensure_dir(os.path.dirname(target))
    if write_json_if_changed(target, sidecar):
        written.append(posixpath.join(SITE_DIR, folder, spec.id + page_logic.SIDECAR_SUFFIX))
    return sidecar


def _solid_suffix(name: str) -> str:
    """``gltf`` for a ``.gltf`` name, else ``glb``: a published 3D file keeps
    the format the viewgen wrote (`SOLID_SUFFIXES`)."""
    return "gltf" if name.lower().endswith(".gltf") else "glb"


def _promote_live(site: str, sidecar: dict, written: list[str]) -> dict | None:
    """The sidecar of an entry an export has re-ran since its 3D file was drawn
    live: the live file copied into the committed folder (``live`` false), or
    None when the live file is not here with its bytes (draw it again)."""
    field_row = (sidecar.get("files") or {}).get("field") or {}
    gate_id, entry, view_id = sidecar["gate"], sidecar["entry"], sidecar["view"]
    source = os.path.join(site, *page_logic.live_folder(gate_id, entry).split("/"),
                          str(field_row.get("name") or ""))
    payload = _file_bytes(source)
    if payload is None or hashlib.sha256(payload).hexdigest() != field_row.get("sha256"):
        return None
    folder = page_logic.figure_folder(gate_id, entry)
    row = {**_put_figure_file(site, folder, view_id, payload,
                              _solid_suffix(str(field_row.get("name") or "")), written),
           "live": False}
    promoted = {**sidecar, "files": {**sidecar["files"], "field": row}}
    target = os.path.join(site, *folder.split("/"), view_id + page_logic.SIDECAR_SUFFIX)
    if write_json_if_changed(target, promoted):
        written.append(posixpath.join(SITE_DIR, folder, view_id + page_logic.SIDECAR_SUFFIX))
    return promoted


def _gate_limits(ledger: Ledger, registry: Any, gate_id: str, entry: Any) -> list:
    """Every limit a colour range may not be taken from: each ``limit`` and
    ``limit_hi`` of the claims ``gate_id`` covers (`claims.effective_gates`),
    and the entry's verdict limit."""
    out: list[Any] = []
    covering = claim_logic.effective_gates(ledger, registry) if registry is not None else {}
    for claim in ledger.claims:
        if gate_id in (covering.get(claim.id) or ()):
            acc = getattr(claim, "acceptance", None)
            out += [getattr(acc, "limit", None), getattr(acc, "limit_hi", None)]
    if entry is not None:
        out.append((entry.verdict or {}).get("limit"))
    return [v for v in out if v is not None]


class _Bound(NamedTuple):
    """What the bound pass hands `build`: the views, one note row per bound
    viewgen, the files the cleaner keeps, the live files (``{figure id: site
    path}``, for ``build.json`` only), each view's extra ``state.json`` keys
    (``binding``, ``data_url``, ``field_src``), what it wrote, and one
    `page.BoundFigure` per bound viewgen for ``page.json`` (``found``)."""

    views: list
    notes: list
    keep: list
    live: dict
    rows: dict
    written: list
    found: list


def _bound_pass(root: str, site: str, ledger: Ledger, registry: Any, resolution: Any,
                view_registry: ViewRegistry, warnings: list[str]) -> _Bound:
    """Each bound viewgen, in registry order (B.2.1, step 4): no served entry
    draws nothing; a valid sidecar under the served entry at the current recipe
    is the memo, and the viewgen is not called; else, when it is available, it
    draws from a `BoundContext` and the spine checks (`_figure_refusal`) and
    publishes (`_publish_figure`). Then the figure the page shows is read from
    the disk (`page.figure_for`): current, stale, unseen, older or none. A viewgen that
    is unavailable, crashes, returns nothing or is refused moves no status (no
    view reaches the resolution) and deletes no picture: the cleaner keeps the
    figure it resolved to."""
    rows = getattr(resolution, "rows", None) or {}
    unseen = claim_logic.unseen_gates(resolution)
    stripped = Ledger.from_dict(ledger.to_dict())
    stripped = dataclasses.replace(stripped, claims=[
        dataclasses.replace(c, acceptance=Acceptance()) for c in stripped.claims])
    out = _Bound([], [], [], {}, {}, [], [])
    for spec, fn in view_registry.pairs():
        if not spec.evidence_of:
            continue
        gate_id = spec.evidence_of
        row = rows.get(gate_id)
        entry = getattr(row, "entry", None)
        served = str(getattr(entry, "name", "") or "")
        fresh = bool(getattr(row, "fresh", False))
        status, note = "none", ""
        recipe, recipe_problem = _recipe_digest(spec, fn)
        exported = served in _exported_entries(ledger, gate_id) if served else False
        if not served:
            note = f"no result of {gate_id} is recorded: run `nopekit check`"
        else:
            found, _why = page_logic.read_sidecar(root, gate_id, served, spec.id)
            memo = found is not None and not recipe_problem and found.get("recipe") == recipe
            field_row = ((found or {}).get("files") or {}).get("field") or {}
            if memo and field_row.get("live") is True:
                live_file = os.path.join(site, *page_logic.live_folder(gate_id, served)
                                         .split("/"), str(field_row.get("name") or ""))
                if exported:
                    memo = _promote_live(site, found, out.written) is not None
                elif not os.path.isfile(live_file) and availability(spec)[0]:
                    memo = False
            if memo:
                status = "memo"
            elif recipe_problem:
                status, note = "error", _NOTE_ERROR + recipe_problem
            else:
                ok, reason = availability(spec)
                if not ok:
                    status, note = "unavailable", _NOTE_UNAVAILABLE + reason
                else:
                    scratch = os.path.join(root, *FIGURE_SCRATCH.split("/"),
                                           page_logic._component(gate_id),
                                           page_logic._component(served),
                                           page_logic._component(spec.id))
                    shutil.rmtree(scratch, ignore_errors=True)
                    ensure_dir(scratch)
                    bound, digests = _bound_entry(root, gate_id, entry, fresh)
                    ctx = BoundContext(root=root, ledger=stripped, model=None, params={},
                                       assets_dir=scratch, log=_noop_log, extra={},
                                       entry=bound, scratch=scratch, view_id=spec.id)
                    ctx.digests = digests
                    drawn, note = run_viewgen(spec, fn, ctx)
                    status = _note_status(drawn, note)
                    for said in ctx.notes:
                        warnings.append(f"view {spec.id}: {said}")
                    if drawn is not None:
                        refusal = _figure_refusal(spec, drawn, ctx, _gate_limits(
                            ledger, registry, gate_id, entry))
                        if refusal:
                            status, note = "refused", refusal
                        else:
                            _publish_figure(site, spec, drawn, ctx, recipe=recipe,
                                            exported=exported, written=out.written)
            if (status in ("unavailable", "error", "refused", "empty") and found is not None
                    and found.get("recipe") != recipe):
                note += ("; " if note else "") + ("the picture of this result was drawn by an "
                                                  "older scene and is kept: build where its "
                                                  "tools are to draw it again")
        figure = page_logic.figure_for(root, gate_id, spec.id, served, fresh,
                                       gate_id in unseen)
        warnings.extend(figure.problems)
        sidecar = figure.sidecar
        if sidecar is not None and sidecar.get("kind") != ViewKind(spec.kind).value:
            warnings.append(f"view {spec.id}: the picture under {gate_id}/{figure.entry} is a "
                            f"{sidecar.get('kind')}, not a {ViewKind(spec.kind).value}; it is "
                            f"not shown")
            sidecar = None
        if status in ("unavailable", "error", "refused"):
            warnings.append(f"view {spec.id}: {note}")
        out.notes.append({"view": spec.id, "pack": spec.pack, "kind": ViewKind(spec.kind).value,
                          "status": status, "note": note, "gate": gate_id, "entry": served,
                          "currency": figure.currency if sidecar is not None else "none"})
        # What page.json bakes (P1.4): the figure as shown, ``none`` when the
        # disk's sidecar is refused here for its kind.
        out.found.append(page_logic.BoundFigure(
            view=spec.id, kind=ViewKind(spec.kind).value, gate=gate_id,
            title=str(spec.title or ""),
            provenance=" ".join(str(spec.description or "").split()),
            figure=figure if sidecar is not None else figure._replace(
                entry="", currency="none", sidecar=None)))
        if sidecar is None:
            continue
        folder = figure.folder
        files = sidecar["files"]
        out.keep.append(posixpath.join(folder, spec.id + page_logic.SIDECAR_SUFFIX))
        src, extra = "", {}
        solid = ViewKind(spec.kind) in (ViewKind.MODEL3D, ViewKind.FIELD)
        if "image" in files:
            still = posixpath.join(folder, files["image"]["name"])
            out.keep.append(still)
            # A 3D view's `src` is its GLB, never its still: with the GLB
            # live (on this computer only), the row names no file at all.
            src = "" if solid else still
        if "field" in files:
            name = files["field"]["name"]
            if files["field"].get("live") is not True:
                committed = posixpath.join(folder, name)
                out.keep.append(committed)
                if solid:
                    src = committed
                else:
                    extra["field_src"] = committed
            elif figure.entry == served:
                live = posixpath.join(page_logic.live_folder(gate_id, served), name)
                found_live = page_logic._sha256_of(os.path.join(site, *live.split("/")))
                if found_live is not None and found_live[0] == files["field"]["sha256"]:
                    out.keep.append(live)
                    out.live[f"{gate_id}/{served}/{spec.id}"] = live
        if "chart" in files:
            extra["data_url"] = posixpath.join(folder, files["chart"]["name"])
            out.keep.append(extra["data_url"])
        extra["binding"] = {"gate": gate_id, "entry": figure.entry,
                            "currency": figure.currency,
                            "currency_word": page_logic.figure_currency_word(figure.currency)}
        meta = {key: sidecar[key] for key in ("caption", "alt", "scale", "marks", "scales",
                                              "light")
                if sidecar.get(key)}
        shown, _note = _stamp_view(View(id=spec.id, kind=spec.kind,
                                        title=str(sidecar.get("title") or spec.title),
                                        src=src, description=spec.description,
                                        gates=[gate_id], data={}, meta=meta, pack=spec.pack,
                                        order=spec.order), spec)
        out.views.append(shown)
        out.rows[spec.id] = extra
    return out


# --------------------------------------------------------------------------- #
# building
# --------------------------------------------------------------------------- #
def build(
    root: str,
    ledger: Ledger,
    registry: Any,
    view_registry: ViewRegistry,
    *,
    model: Any | None = None,
    projection: dict | None = None,
    now: str = "",
    resolution: Any = None,
    params: Any = None,
    graph: Any = None,
    live: bool = False,
    traced: Any = None,
) -> dict:
    """Run the viewgens, write ``site/data/`` and ``site/assets/``, return a summary.

    **This function does not run gates**, and the omission is the point. It
    renders ``resolution`` — the caller's ``verdicts.resolve`` of the verdict
    cache. A build that re-ran the cheap gates on the way past would publish a
    page whose tier-0 numbers are ten seconds old and whose tier-2 numbers are
    a week old, and the reader has no way to tell them apart. If the results
    are stale, the fix is to run ``nopekit check``, and the page's job is to
    make the staleness impossible to miss, not to paper over it.

    Handed no ``resolution``, it resolves for itself (:func:`state`), against
    the projection it builds the views from: ``projection``, or — when the
    caller passed neither it nor ``model`` — the live model, loaded here. A
    model that does not load is not an error for the page; the resolver reads
    every verdict that read it as not current (S-21), and the build warns.
    ``params`` as for :func:`state`; handed none, they are the view of the model
    the build already holds. ``graph`` is the caller's `graph.build` over the
    same resolution (`round` hands its own, so the map and the round record
    share one graph); with none, `graph.of` builds it over the current
    design's trace. ``traced`` is the caller's `derivation.TraceRead`; with
    none, the build reads it (architecture phase 3b). ``live`` marks
    ``data/build.json`` live, so a page served by `site serve --watch` polls it.

    What it writes (architecture phase 5, SPEC 28-33), in this order: the
    renderer (`refresh_renderer`, never a shell), ``technical.html`` when absent,
    the clock's ignore line; the views and ``state.json`` (the technical
    record, clock-free); ``map.json`` and ``page.json`` (`page`), refused
    whole when `page.status_problems` finds a token the resolution does not
    say (invariant 14); the evidence copies; ``build.json`` last. Every tracked
    file is written only when its bytes differ, so a rebuild that judged
    nothing new changes no tracked byte and no mtime.

    ``registry`` is the *gate* registry; ``view_registry`` holds the viewgens.
    Both are parameters so a build can run against a scratch registry in a test.

    Returns a summary dict: what was written, what was removed, every viewgen's
    outcome, and — never merely available but actually said — ``warnings``,
    which is the list a CLI must print. The ledger is NOT written back: the site
    is an output and the records are the source (method rule 1).
    """
    site = os.path.join(root, SITE_DIR)
    if not scaffolded(root):
        raise NopekitError(
            f"no {SITE_DIR}/index.html at {os.path.abspath(root)}: run `nopekit site init` "
            f"first ({SITE_DIR}/{CONTENT_DIR}/ holds the page's text, not the page)")
    data_dir = os.path.join(site, DATA_DIR)
    views_dir = os.path.join(site, VIEWS_DIR.replace("/", os.sep))
    assets_dir = os.path.join(site, ASSETS_DIR)
    for directory in (data_dir, views_dir, assets_dir):
        ensure_dir(directory)
    refreshed = refresh_renderer(root)
    pages_written: list[str] = []
    for source, relative in _template_files():
        target = os.path.join(site, *relative.split("/"))
        # Every absent shell but the root: an existing project gains the two
        # pages here (`site init` refuses while its root page exists), and its
        # root page stays its own. A shell written here is listed apart from
        # the renderer (``pages``). What slipped through (review of SPEC 40):
        # it was appended to ``refreshed``, so the build said it "refreshed
        # the renderer" with a page the project never had, written new, among
        # the renderer's files, and a person read their own shell as replaced.
        if relative in SHELLS and relative != "index.html" and not os.path.exists(target):
            ensure_dir(os.path.dirname(target))
            shutil.copyfile(source, target)
            pages_written.append(posixpath.join(SITE_DIR, relative))
    bound_registered = any(spec.evidence_of for spec in view_registry.specs())
    ignore_written = ensure_site_ignore(root, live=bound_registered)

    warnings: list[str] = []
    model_error = ""
    if projection is None and model is None:
        model, projection, model_error = _live_projection(root, ledger)
    if model_error:
        warnings.append(f"the model does not load, so no verdict that reads it is "
                        f"current: {model_error}")
    flat, conflicts = modelio.flat_params(projection)
    for conflict in conflicts:
        warnings.append(f"model and build() disagree on {conflict} — views read the "
                        f"config value")

    # The resolution and the evidence plan first (issue #15, B.2.1): a bound
    # viewgen draws its gate's SERVED entry, so the resolver must have said
    # which one before any viewgen runs. Safe to move: the resolution reads
    # the ledger the caller handed in, never a generated view (F5).
    if resolution is None:
        resolution = _resolve(root, ledger, registry, projection=projection, model=model,
                              model_error=model_error, now=now)
    plan = _evidence_plan(root, verdict_logic.view(ledger, resolution), resolution, registry)
    for item in plan:
        if item.reason and item.action == "keep":
            warnings.append(f"evidence {item.path} of {item.gate} is the copy an earlier "
                            f"build made, not the file on this computer: {item.reason}")
        elif item.reason:
            warnings.append(f"evidence {item.path} of {item.gate} is not on the page: "
                            f"{item.reason}")

    ctx = ViewContext(
        root=root,
        # A DEFENSIVE COPY, exactly as `cli._context` hands gates one. Viewgens
        # are third-party code, and handing over the live object would let a
        # viewgen append a Verdict or clear a claim, which `state()` would then
        # publish as the project's status a few lines later.
        ledger=Ledger.from_dict(ledger.to_dict()),
        model=model,
        params=flat,
        assets_dir=assets_dir,
        log=_noop_log,
        extra={},
    )

    generated, notes = run_all_viewgens(view_registry, ctx)
    held = _kept_from_last_build(site, view_registry, notes, warnings)
    for note in notes:
        if note["status"] in ("error", "unavailable"):
            kept_word = "kept from the last build: " if note["view"] in held else ""
            warnings.append(f"view {note['view']}: {kept_word}{note['note']}")
    figures = _bound_pass(root, site, ledger, registry, resolution, view_registry, warnings)
    notes.extend(figures.notes)
    if held or figures.views:
        order_index = {spec.id: i for i, spec in enumerate(view_registry.specs())}
        generated = sorted([*generated, *held.values(), *figures.views],
                           key=lambda v: (int(v.order), order_index.get(v.id, 0), v.id))

    merged, conflicts_views = _merge_views(ledger.views, generated)
    warnings.extend(conflicts_views)

    # `state` reads views off the ledger, so the generated ones are grafted onto
    # an in-memory copy. A `replace`, not a dict round trip: a claim's results
    # and attributions live in memory only (P2.5a, `FORBIDDEN_KEYS`).
    for_state = dataclasses.replace(ledger, views=list(merged))
    if params is None:
        params = _param_views(ledger, model, model_error, resolution, registry)
    view = verdict_logic.view(for_state, resolution)
    composed = _composed(view, registry, resolution)
    # -- the trace and amber (architecture phase 3b), before `state.json`,
    # which carries them inside its resolution digest ----------------------- #
    tokens = graph_logic.tokens(view, resolution, registry, composed=composed)
    if traced is None:
        try:
            traced = trace_logic.trace_for(root, model, projection,
                                           trace_logic.anchors_for(root, resolution))
        except (NopekitError, OSError, ValueError, TypeError):
            traced = trace_logic.TraceRead("unrecorded", None, None, (), ())
    built = graph if graph is not None else graph_logic.of(
        root, view, resolution, registry, projection=projection, model=model, views=params,
        bases={}, trace=traced.trace)
    amber = trace_logic.amber_now(root, view, registry, built, tokens.claims, traced)
    payload = state(root, for_state, registry, resolution=resolution, params=params,
                    judged=(view, composed),
                    trace=trace_logic.state_payload(traced, built, amber))
    # A bound view's row (issue #15): the chart file it draws (``data_url``),
    # its committed 3D file (``field_src``) and which result it is
    # (``binding``). Views are outside the resolution digest
    # (`_UNJUDGED_KEYS`), so no row moves what the page judged.
    for row in payload["views"]:
        row.update(figures.rows.get(str(row.get("id") or ""), {}))
    # Every chart view's Vega-Lite spec (issue #15, P2.5): the technical
    # record draws it as the engineering page's cards do, and the hand-drawn
    # `lib/chart.js` is gone. A bound view's series are its chart file's.
    anchored: dict[str, list] = {}
    for verdict_row in sorted(payload["verdicts"], key=lambda r: r.get("order", 0)):
        for locator in verdict_row.get("locators") or ():
            if isinstance(locator, dict) and locator.get("view"):
                anchored.setdefault(str(locator["view"]), []).append((locator, verdict_row))
    for row in payload["views"]:
        if str(row.get("kind") or "") == "chart":
            source = (page_logic._chart_payload(root, str(row.get("data_url") or ""))
                      if row.get("binding") else row.get("data"))
            row["chart"] = page_logic.view_chart(row, source,
                                                 anchored.get(str(row.get("id") or ""), ()))
    stale = bool(payload["meta"]["stale"])
    stale_reason = str(payload["meta"]["stale_reason"])

    # -- the front page (page.py): the map, the outline, the floor ---------- #
    lay = page_logic.layout(view, built, projection=projection, params=params,
                            registry=registry, resolution=resolution)
    warnings.extend(lay.warnings)
    records = str(payload["meta"].get("records_digest") or "")
    judged = str(payload["meta"].get("resolution_digest") or "")
    map_doc = page_logic.bake_map(view, lay, built, tokens, composed, projection=projection,
                                  params=params, registry=registry, payload=payload,
                                  name=str(view.meta.name or ""), records_digest=records,
                                  resolution_digest=judged, amber=amber, trace=traced)
    page_doc = page_logic.bake_page(root, view, lay, built, tokens, composed,
                                    registry=registry, payload=payload, plan=plan,
                                    records_digest=records, resolution_digest=judged,
                                    amber=amber, figures=figures.found, warnings=warnings)
    # The figures (issue #15, B.5) are held against the figures the bound pass
    # resolved and the files it keeps: a page that links a file the cleaner
    # removes below, or says another currency, is refused whole.
    problems_found = page_logic.status_problems(
        map_doc, page_doc, view, composed, tokens, amber=amber,
        feeds=page_logic.live_feeds(lay, built, tokens),
        figures=page_logic.figure_facts(root, resolution, payload, view, composed, registry,
                                        kept=figures.keep, shown=figures.found))
    if problems_found:
        raise NopekitError(
            f"the page would say more than the resolution, so nothing under "
            f"{SITE_DIR}/{DATA_DIR}/ was written: {problems_found[0]}. This is a defect in "
            f"nopekit; report it.")

    # The figures the bound pass published (issue #15), before the page that
    # links them, as the evidence below.
    written: list[str] = list(figures.written)
    # Evidence first: a copy the page links must be on disk before the page is.
    copied = kept = 0
    keep_evidence: list[str] = []
    for item in plan:
        if item.action == "copy" and item.source and item.dest:
            target = os.path.join(site, *item.dest.split("/"))
            with open(item.source, "rb") as fh:
                payload_bytes = fh.read()
            if hashlib.sha256(payload_bytes).hexdigest() != item.sha256:
                # Planned at the bytes the entry's run cited, read now as
                # others: a run between the plan and the copy. The page links
                # the copy an earlier build made, if any; nothing is written.
                warnings.append(f"evidence {item.path} of {item.gate} moved while the page "
                                f"was built, so it was not copied; build again")
                if os.path.isfile(target):
                    kept += 1
                    keep_evidence.append(item.dest)
                continue
            try:
                with open(target, "rb") as fh:
                    same = fh.read() == payload_bytes
            except OSError:
                same = False
            if not same:
                _atomic_write_bytes(target, payload_bytes)
                written.append(posixpath.join(SITE_DIR, item.dest))
            copied += 1
            keep_evidence.append(item.dest)
        elif item.action == "keep" and item.dest:
            kept += 1
            keep_evidence.append(item.dest)
    not_shown = sum(1 for item in plan if item.action == "none")

    # Split the payloads too big to inline BEFORE writing state.json, so the file
    # the page fetches stays the size of a project summary rather than the size
    # of its largest table.
    kept_view_files: list[str] = []
    inline_views: list[Any] = []
    for row in payload["views"]:
        view_id = str(row.get("id") or "")
        inline = row.get("data") or {}
        chart = row.get("chart") or {}
        if not inline and not chart.get("spec"):
            continue
        try:
            _require_view_id(view_id)
        except NopekitError as exc:
            warnings.append(f"view {view_id!r} kept inline: {exc}")
            continue
        # A chart's spec holds its series once more (and its points twice), so
        # it is split on its own size, into `<id>~chart.json` (P2.5): a bound
        # chart has no inline data, and its spec alone can be the size of a
        # settling history. *Rejected:* the spec inline beside a split payload
        # (state.json would carry the largest chart twice over).
        if chart.get("spec") and _rough_size(chart) > VIEW_INLINE_MAX:
            relative = posixpath.join(VIEWS_DIR, view_id + CHART_FILE_SUFFIX)
            if write_json_if_changed(os.path.join(site, relative.replace("/", os.sep)),
                                     chart):
                written.append(posixpath.join(SITE_DIR, relative))
            row["chart"] = {}
            row["chart_url"] = relative
            kept_view_files.append(view_id + CHART_FILE_SUFFIX)
        if not inline or _rough_size(inline) <= VIEW_INLINE_MAX:
            continue
        relative = posixpath.join(VIEWS_DIR, f"{view_id}.json")
        if write_json_if_changed(os.path.join(site, relative.replace("/", os.sep)), inline):
            written.append(posixpath.join(SITE_DIR, relative))
        inline_views.append(inline)
        row["data"] = {}
        row["data_url"] = relative
        kept_view_files.append(f"{view_id}.json")
    # What each view row is as written, its view file included, sealed into
    # page.json: the next build keeps a row only at these bytes
    # (`_kept_from_last_build`), and `site status` reads any other as edited.
    page_doc["meta"]["views"] = {str(row.get("id") or ""): view_digest(
        row, _views_file_payload(site, row)) for row in payload["views"]}

    # Keep every asset a view still points at, not merely the ones written this
    # run, and every evidence copy the plan links. A viewgen that is unavailable
    # or crashed is among them through the row its id had in the last build
    # (`_kept_from_last_build`). What slipped through (issue #15): this comment
    # said so before the code did; such a viewgen returned no view, its `src`
    # left this list, and the cleaner deleted the picture it drew last time.
    # And every file of each bound figure the page shows (issue #15, B.2.4): the
    # one the view resolved to, current, stale or older, and the live 3D file
    # of the served entry. An older folder no view resolves to is not kept.
    keep = list(ctx.written) + keep_evidence + list(figures.keep)
    for row in payload["views"]:
        src = str(row.get("src") or "")
        if src.startswith(ASSETS_DIR + "/"):
            keep.append(src)

    # The prose last among the bakes: its numbers are matched against every
    # number the other documents hold (SPEC 30), never against itself, and
    # only against assets this build keeps: a file the cleaner removes below
    # never vouches for a number. What slipped through (review of phase 5): the
    # pool read every JSON under assets/ before the cleaner ran, so an old
    # evidence copy matched a number once, and the next build, with nothing
    # new judged, marked it and rewrote page.json.
    pool = prose_pool([payload, *inline_views, map_doc, page_doc],
                      _kept_json(site, keep))
    prose_rows = page_logic.bake_prose(root, page_doc, lay, tokens, pool)
    for row in prose_rows:
        where = row["file"]
        if row["subject"] is None:
            warnings.extend(row["problems"])
            continue
        if row["stamp"] == "stale":
            warnings.append(f"{where}: written before the latest results; read it again, then "
                            f"`nopekit site stamp {row['subject']}`")
        elif row["stamp"] == "unstamped":
            warnings.append(f"{where}: not stamped; read it again, then "
                            f"`nopekit site stamp {row['subject']}`")
        for number in row["unmatched"][:8]:
            warnings.append(f"{where}: {number} matches no number the site bakes; the page "
                            f"marks it")
        if len(row["unmatched"]) > 8:
            warnings.append(f"{where}: and {len(row['unmatched']) - 8} more")
        for problem in row["register"] + row["problems"]:
            warnings.append(f"{where}: {problem}")

    map_doc["self"] = page_logic.seal(map_doc)
    page_doc["self"] = page_logic.seal(page_doc)
    for name, doc in ((STATE_NAME, payload), (MAP_NAME, map_doc), (PAGE_NAME, page_doc)):
        relative = posixpath.join(DATA_DIR, name)
        if write_json_if_changed(os.path.join(data_dir, name), doc):
            written.append(posixpath.join(SITE_DIR, relative))

    removed = [posixpath.join(SITE_DIR, p) for p in
               clean_assets(root, keep) + _clean_view_files(views_dir, kept_view_files)]

    problems = payload.get("locator_problems") or []
    for problem in problems:
        warnings.append(f"locator {problem['gate']} -> {problem['view']}"
                        f"{'/' + problem['target'] if problem['target'] else ''}: "
                        f"{problem['problem']}")

    marker = outline_of(root)
    older = outline_problem(root)
    if older:
        warnings.append(older)
    if old_layout(root):
        warnings.append(OLD_LAYOUT_WARNING)
    # What the build says that is no fault (``notices``): printed, never
    # counted as a warning by `round` or `status`.
    notices: list[str] = []
    # The marketing page's story (the two pages): a warning, never a refusal,
    # because the page omits the section and still says nothing false. No test
    # holds a build of a project without story.md to zero warnings. It says
    # "has no story" only when there is neither story.md nor intro.md: with
    # intro.md the page shows it as the story (the addendum's rule: story,
    # else intro, else none), and the build says that instead. What slipped
    # through (review of SPEC 40): "the marketing page has no story" on every
    # build and every round of a project with intro.md, whose page had one.
    if page_doc.get("story") is None and page_doc.get("intro") is None:
        warnings.append(f"{SITE_DIR}/{CONTENT_DIR}/story.md is missing: the marketing page "
                        f"has no story")
    elif page_doc.get("story") is None:
        notices.append(f"{SITE_DIR}/{CONTENT_DIR}/story.md is missing: the marketing page "
                         f"shows intro.md as its story until story.md is written")

    verdict_ages = ages(resolution, now)
    build_json = write_build_json(root, now=now, id=_baked_id(site), ok=True, live=live,
                                  verdicts=verdict_ages,
                                  tracing=trace_logic.live_holder(root) is not None,
                                  live_figures=figures.live)
    stamps = [r["stamp"] for r in prose_rows if r["subject"] is not None]
    return {
        "root": root,
        "site": SITE_DIR,
        "state": posixpath.join(SITE_DIR, DATA_DIR, STATE_NAME),
        "page": posixpath.join(SITE_DIR, DATA_DIR, PAGE_NAME),
        "map_file": posixpath.join(SITE_DIR, DATA_DIR, MAP_NAME),
        "build_json": build_json,
        "built": now,
        "stale": stale,
        "stale_reason": stale_reason,
        "views": [{"id": v["id"], "kind": v["kind"], "src": v.get("src", ""),
                   "pack": v.get("pack", ""), "data_url": v.get("data_url", "")}
                  for v in payload["views"]],
        "viewgens": notes,
        "wrote": sorted(set(written + refreshed + pages_written
                            + ([posixpath.join(SITE_DIR, ".gitignore")] if ignore_written
                               else [])
                            + [posixpath.join(SITE_DIR, p) for p in ctx.written])),
        "refreshed": refreshed,
        "pages": pages_written,
        "notices": notices,
        "removed": sorted(removed),
        "locator_problems": problems,
        "counts": {
            "views": len(payload["views"]),
            "claims": len(payload["claims"]),
            "verdicts": len(payload["verdicts"]),
            "locator_problems": len(problems),
            # A bound figure's files count as assets a viewgen drew (issue
            # #15), its sidecars not: what the page shows, not how it is bound.
            "assets": len(ctx.written) + sum(
                1 for p in figures.keep if not p.endswith(page_logic.SIDECAR_SUFFIX)),
        },
        "map": {"lines": len(lay.lines), "boards": len(lay.boards), "zones": len(lay.zones),
                "stations": len(lay.stations), "traced": built.trace is not None,
                "layers": len(lay.layers), "feeds": len(lay.feeds),
                "round": getattr(built, "trace_round", None),
                "crossings": dict(lay.crossings)},
        "prose": [{"file": r["file"], "subject": r["subject"], "stamp": r["stamp"],
                   "unmatched": r["unmatched"], "register": r["register"],
                   "problems": r["problems"]} for r in prose_rows],
        "prose_counts": {"current": stamps.count("current"), "stale": stamps.count("stale"),
                         "unstamped": stamps.count("unstamped"),
                         "unmatched": sum(len(r["unmatched"]) for r in prose_rows)},
        "outline": {"marker": marker, "current": page_logic.OUTLINE},
        "evidence": {"copied": copied, "kept": kept, "missing": not_shown},
        "warnings": warnings,
    }


def view_digest(row: Mapping[str, Any], data: Any) -> str:
    """sha256 of a view's ``state.json`` row and the payload of its view file
    (``data/views/<id>.json``; None when it has none), canonical JSON: what
    ``page.json``'s ``meta.views`` holds per view. ``""`` for a row JSON cannot
    write (the build refuses such a row before here)."""
    try:
        text = json.dumps({"row": row, "data": data}, sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(("nopekit-view-v1\n" + text).encode("utf-8")).hexdigest()


def _views_file_payload(site: str, row: Mapping[str, Any]) -> Any:
    """The payload of ``row``'s view file under ``data/views/`` (`VIEWS_DIR`),
    as the build split it out; None for a row with no such file (inline data,
    or a bound view's chart under ``assets/figures/``); the string
    ``"unreadable"`` when the file is missing or does not parse, which no
    payload the build writes (a dict) equals. A row whose chart spec was split
    out (``chart_url``) gives ``{"data": that, "chart": the spec}``."""
    view_id = str(row.get("id") or "")
    data = None
    if view_id and row.get("data_url") == posixpath.join(VIEWS_DIR, f"{view_id}.json"):
        data = _view_file(site, row["data_url"])
    if not row.get("chart_url"):
        return data
    # A chart's spec split out (P2.5) is sealed with its row too: a number
    # edited into it by hand reads *edited by hand*, as one in the payload does,
    # and a row that names another file reads so as well.
    chart = (_view_file(site, row["chart_url"]) if view_id and row["chart_url"] ==
             posixpath.join(VIEWS_DIR, view_id + CHART_FILE_SUFFIX) else "unreadable")
    return {"data": data, "chart": chart}


def _view_file(site: str, rel: str) -> Any:
    """The JSON of the site file ``rel``, or ``"unreadable"``."""
    try:
        with open(os.path.join(site, *rel.split("/")), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return "unreadable"


def view_problems(site: str, state_doc: Mapping[str, Any],
                  page_doc: Mapping[str, Any]) -> list[str]:
    """Each view row of ``state_doc`` (with its view file) whose digest is not
    the one ``page_doc``'s ``meta.views`` holds, as a sentence; [] when the page
    holds no digests (a page built before them). The page's own seal is the
    caller's to check: a resealed page that names the edited bytes is the
    forged-page class (SPINE_CONTRACT's limits)."""
    digests = (page_doc.get("meta") or {}).get("views")
    if not isinstance(digests, dict):
        return []
    out = []
    for row in state_doc.get("views") or ():
        if not isinstance(row, dict):
            continue
        view_id = str(row.get("id") or "")
        if digests.get(view_id) != view_digest(row, _views_file_payload(site, row)):
            out.append(f"view {view_id}: its row in {DATA_DIR}/{STATE_NAME} or its file "
                       f"{VIEWS_DIR}/{view_id}.json is not what the build wrote")
    return out


def _kept_from_last_build(site: str, registry: ViewRegistry,
                          notes: Iterable[Mapping[str, Any]],
                          warnings: list[str] | None = None) -> dict[str, View]:
    """``{view id: View}`` for each viewgen of ``notes`` that is unavailable or
    crashed this build: the row its id had in the last ``data/state.json``,
    with its payload read back from its ``data_url`` file, as a view again
    (its identity forced from its spec, `_stamp_view`).

    A view is page furniture, never a truth input: no view reaches the
    resolution (`build` resolves against the ledger it was handed), so a row
    kept here moves no status. The row is kept only while every file it names
    is on disk and its payload parses; otherwise the view leaves the page, as
    before, and the page links no file that is gone. Nothing machine-specific
    is added to the row, so a clone without the tool bakes the same
    ``state.json``. What slipped through (issue #15): an unavailable viewgen
    returned no view, so a build on a computer without its tool removed the
    view from the page and deleted its committed picture.
    *Rejected:* keeping every file under ``assets/`` that the last state named
    (a view whose viewgen was removed or returned nothing would never go);
    keeping the file and dropping the row (the cleaner's job is to remove what
    no page links, and a picture nobody links is that).

    A row is kept only at the bytes the last build wrote: its digest, with its
    view file, must be the one the last ``page.json`` holds under its intact
    seal (``meta.views``, `view_digest`). What slipped through (review of P1):
    a number added by hand to ``data/views/<id>.json`` was read back here and
    baked into the new page, where it vouched for a prose number, and the
    build resealed the page, so `site status` no longer said *edited by hand*.
    *Rejected:* leaving a kept row's numbers out of the prose pool (a clone
    without the tool would then mark numbers the page built with the tool
    matched, and the two computers would bake two pages from one set of
    records). What it cannot close: a hand edit that also writes the digest
    into ``page.json`` and seals it again (the forged-page class,
    SPINE_CONTRACT's limits).
    """
    wanted = {str(n.get("view") or "") for n in notes
              if n.get("status") in ("unavailable", "error")}
    if not wanted:
        return {}
    try:
        with open(os.path.join(site, DATA_DIR, STATE_NAME), "r", encoding="utf-8") as fh:
            last = json.load(fh)
    except (OSError, ValueError):
        return {}
    try:
        with open(os.path.join(site, DATA_DIR, PAGE_NAME), "r", encoding="utf-8") as fh:
            last_page = json.load(fh)
    except (OSError, ValueError):
        last_page = None
    digests = ((last_page.get("meta") or {}).get("views")
               if isinstance(last_page, dict) and last_page.get("self") == page_logic.seal(
                   last_page) else None)
    if not isinstance(digests, dict):
        digests = {}
    rows = last.get("views") if isinstance(last, dict) else None
    held: dict[str, View] = {}
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict) or row.get("id") not in wanted or row["id"] in held:
            continue
        found = registry.get(row["id"])
        if found is None:
            continue
        spec = found[0]
        named = [str(row.get(key) or "") for key in ("src", "data_url", "chart_url")]
        if not all(_site_file(site, rel) for rel in named if rel):
            continue
        if digests.get(row["id"]) != view_digest(row, _views_file_payload(site, row)):
            if warnings is not None:
                warnings.append(f"view {row['id']}: its row in {DATA_DIR}/{STATE_NAME} or "
                                f"its file {VIEWS_DIR}/{row['id']}.json is not what the last "
                                f"build wrote, so it is not kept")
            continue
        body = {k: v for k, v in row.items() if k != "data_url"}
        if row.get("data_url"):
            if row["data_url"] != posixpath.join(VIEWS_DIR, f"{row['id']}.json"):
                continue
            try:
                with open(os.path.join(site, *row["data_url"].split("/")), "r",
                          encoding="utf-8") as fh:
                    body["data"] = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(body["data"], dict):
                continue
        try:
            view = View.from_dict(body)
        except (TypeError, ValueError):
            continue
        held[spec.id], _note = _stamp_view(view, spec)
    return held


def _site_file(site: str, rel: str) -> bool:
    """Whether ``rel`` is a plain relative path to a file under ``site``."""
    text = str(rel).replace("\\", "/")
    if (not text or text.startswith("/") or ":" in text
            or ".." in text.split("/")):
        return False
    return os.path.isfile(os.path.join(site, *text.split("/")))


def _merge_views(declared: Iterable[View], generated: Iterable[View]) -> tuple[list, list]:
    """Ledger-declared views plus generated ones. Returns ``(views, warnings)``.

    A project can declare a view by hand in the ledger (a photograph of the
    prototype, a diagram somebody drew) as well as generate one. On an id
    collision the **generated** view wins, because it was produced from the model
    in this build and the declared one is a record of a previous opinion — but
    the collision is reported, since the losing view silently disappearing from
    the page is exactly the kind of absence nobody investigates.
    """
    by_id: dict[str, View] = {}
    warnings: list[str] = []
    # `run_all_viewgens` already resolved display order among the generated
    # views, registration order included; re-sorting on (order, id) here would
    # throw that tie-break away and re-alphabetise a pack's panels. So the
    # generated sequence is preserved as its own key and the declared views
    # follow it.
    rank: dict[str, int] = {}
    for view in generated or []:
        rank.setdefault(view.id, len(rank))
    for view in declared or []:
        by_id[view.id] = view
        rank.setdefault(view.id, len(rank) + 1000)
    for view in generated or []:
        if view.id in by_id:
            warnings.append(
                f"view {view.id!r} is declared in the ledger and generated by a "
                f"viewgen; the generated one is published"
            )
        by_id[view.id] = view
    ordered = sorted(by_id.values(), key=lambda v: (int(v.order), rank[v.id], v.id))
    return ordered, warnings


def _rough_size(payload: Any) -> int:
    """Cheap byte estimate for an inline view payload, for the split decision.

    ``repr`` rather than ``json.dumps``: this only has to be right to within a
    factor of two to decide which side of an 8 KB threshold something is on, and
    serialising every table twice to find out is a real cost on a project with
    forty of them.
    """
    return len(repr(payload))


def _clean_view_files(views_dir: str, keep: Iterable[str]) -> list[str]:
    """Delete ``data/views/*.json`` that no longer belongs to a view. Returns removals.

    Same reasoning as :func:`clean_assets`: a renamed view otherwise leaves its
    old payload on disk, where it is indistinguishable from a current one to
    anything that finds it by URL.
    """
    keeping = set(keep)
    removed: list[str] = []
    if not os.path.isdir(views_dir):
        return removed
    for name in sorted(os.listdir(views_dir)):
        path = os.path.join(views_dir, name)
        if not name.endswith(".json") or not os.path.isfile(path):
            continue
        if name in keeping:
            continue
        os.unlink(path)
        removed.append(posixpath.join(VIEWS_DIR, name))
    return removed


def clean_assets(root: str, keep: Iterable[str]) -> list:
    """Delete files under ``site/assets/`` that nothing references. Returns removals.

    ``keep`` is site-relative (``"assets/assembly.glb"``) or assets-relative
    (``"assembly.glb"``); both spellings are accepted because the first is what
    :meth:`ViewContext.write_asset` returns and the second is what a caller
    holding a bare filename has.

    Stale assets are not harmless. A renamed view leaves a 12 MB GLB behind that
    no page links to but every deploy copies, and — the part that actually costs
    something — a reader who reaches the old URL cannot tell the abandoned
    geometry from the current geometry. It renders perfectly. This is why
    ``write_asset`` exists at all: the cleaner can only be trusted to delete what
    it can prove is unreferenced, which means every asset has to arrive through a
    path that records it.

    Symlinks are removed as links and never followed, and directories are left
    alone except for pruning the empty ones. Nothing outside ``site/assets/`` is
    reachable from here by construction.
    """
    assets = os.path.join(root, SITE_DIR, ASSETS_DIR)
    if not os.path.isdir(assets):
        return []

    keeping: set[str] = set()
    for entry in keep or []:
        text = str(entry).replace("\\", "/").strip()
        if not text:
            continue
        if text.startswith(ASSETS_DIR + "/"):
            text = text[len(ASSETS_DIR) + 1:]
        keeping.add(posixpath.normpath(text))

    removed: list[str] = []
    # Bottom-up so a directory is visited after its children and can be pruned
    # in the same pass once it is empty.
    for directory, _dirnames, filenames in os.walk(assets, topdown=False):
        for name in sorted(filenames):
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, assets).replace(os.sep, "/")
            if relative in keeping:
                continue
            os.unlink(path)
            removed.append(posixpath.join(ASSETS_DIR, relative))
        if directory != assets and not os.listdir(directory):
            os.rmdir(directory)
    return sorted(removed)


# --------------------------------------------------------------------------- #
# the state the page reads
# --------------------------------------------------------------------------- #

#: `ProjectMeta` fields `state.json`'s `meta` never carries (architecture phase
#: 6): `people` lists the names and email addresses a round may commit as, and a
#: page deployed to Pages would publish them to anyone. *Rejected:* baking them
#: (nothing on the page reads them; git's own history already names each
#: commit's author to whoever can read the repository).
_UNPUBLISHED_META = frozenset({"people"})

def state(
    root: str,
    ledger: Ledger,
    registry: Any,
    *,
    now: str = "",
    stale: bool | None = None,
    resolution: Any = None,
    params: Any = None,
    judged: tuple | None = None,
    trace: Mapping[str, Any] | None = None,
) -> dict:
    """Everything the page shows, in one inspectable JSON-safe dict.

    One file, read once, with nothing behind a second request: claims with
    resolved status, verdicts with their locators and ages, views, parameters
    with their rationale **and their rejected alternatives**, inputs with what
    was extracted from them, capability gaps, decisions, and the readiness
    sentence. That completeness is a feature for humans and a requirement for
    agents — ``curl .../data/state.json`` has to be enough to answer "what is the
    status of this project?" without a browser, and an agent that has to render
    HTML to read a verdict will not read the verdict.

    The judgements are **borrowed, not recomputed**. Which verdict each gate
    has, and whether it is current, is ``resolution``'s — the one resolver's
    (:func:`nopekit.verdicts.resolve`, R-5); claim statuses come from
    :mod:`nopekit.claims` given the resolver's ``stale_gates``, the headline
    sentence, the words and the coverage logic from :mod:`nopekit.report`. Those
    are private helpers in ``report`` and reaching for them is deliberate: a
    second implementation would give the page and the readiness document two
    opinions about the same ledger, and the page is the one more people will
    read. One of the two would eventually be wrong and nothing would be
    comparing them.

    ``resolution`` — the caller's, laid over ``ledger`` as its verdicts. With
    none, this resolves for itself, against the live model (loaded here when
    the ledger names one). *Rejected:* a default that lists the recorded entries
    with nothing stale — a caller that forgot the keyword would serve every
    cached PASS as current and never ask whether its control was demonstrated,
    and nothing on the page would say so (judges 2 and 3, SF PD-07).

    ``stale=True`` is the all-stale override: every claim that would pass reads
    STALE, every row not current. ``None`` and ``False`` are no override at all
    — a caller cannot declare the cache current; only the resolver says what is.

    ``now`` is kept for callers and ignored (architecture phase 5, SPEC 32): the
    document is clock-free, and the build time and each verdict's age go to
    ``data/build.json`` (`ages`, `write_build_json`). ``judged`` is the
    caller's ``(view, composed)``, so a build composes once.

    ``params`` — the caller's :func:`nopekit.modelio.param_view` (the CLI's,
    which also names what a broken model's text states). With none, the view of
    the live model, loaded here as for ``resolution``. Never ``ledger.params``:
    what slipped through (review, checkpoint 1.3) — this read the records, which
    from 1.3 hold only what the model cannot (a source, a grounding), so the
    migrated bracket's page showed no parameter at all, and a record holding
    only ``"source"`` was shown at value ``null`` and flagged undefended while
    ``doctor`` said every parameter carried a rationale.
    """
    if resolution is None or params is None:
        model, projection, model_error = _live_projection(root, ledger)
        if resolution is None:
            resolution = _resolve(root, ledger, registry, projection=projection,
                                  model=model, model_error=model_error, now=now)
        if params is None:
            params = _param_views(ledger, model, model_error, resolution, registry)
    everything = stale is True
    stale_gates = frozenset(resolution.stale_gates)
    # The page's ledger IS the resolution laid over the records: every judgement
    # below — statuses, coverage, PARTIAL, the headline, locator problems — reads
    # these verdicts, so none of them can disagree with the verdict rows.
    if judged is not None:
        view, composed = judged
    else:
        view = verdict_logic.view(ledger, resolution)
        composed = _composed(view, registry, resolution, everything)
    assets = page_logic.evidence_assets(_evidence_plan(root, view, resolution, registry))
    board_of = {cid: board.id for board in page_logic.boards_of(view)
                for cid in board.members}
    resolved = {cid: c.status for cid, c in composed.items()}
    summary = claim_logic.summarise(view, registry, stale=everything,
                                    stale_gates=stale_gates)
    cover = report_logic._coverage(view, registry)
    stale_reasons = {gid: (resolution.rows[gid].stale_reason if gid in resolution.rows
                           else "") for gid in stale_gates}
    unseen = claim_logic.unseen_gates(resolution)

    from . import packs as pack_logic             # a reader's module: not at import
    found_proposers = (pack_logic.proposers(root)
                       if any(a.proposer for a in view.inputs) else {})
    input_bases = {a.id: pack_logic.proposer_basis(found_proposers, a.proposer)
                   for a in view.inputs if a.proposer}
    verdict_rows: list[dict] = []
    bases = report_logic._bases(registry)
    for verdict in view.verdicts:
        how = resolution.rows.get(verdict.gate)
        row = verdict.to_dict()
        # SPEC 38: where the evaluator's verdict logic comes from — the
        # registry's token, `undeclared` with no spec; shown, never a status.
        row["basis"] = bases.get(verdict.gate, "undeclared")
        if how is not None and how.cached and how.entry is not None:
            # A cached row shows its entry as recorded: the tier and pack the
            # gate RAN under. The resolver re-stamps a stale verdict with the
            # gate's current spec so coverage is judged against the claims the
            # gate covers now, and tier and pack come along with the claims. For
            # a Fresh entry the two always agree — both are in its code digest
            # (SPEC_FIELDS_IN_RHO) — so this moves nothing that counts; for a
            # stale one the page shows what was measured, beside why it is not
            # current. The claims stay the resolver's: the claim rows are judged
            # from them, and a verdict row naming other claims would be a second
            # opinion about coverage.
            recorded = how.entry.to_verdict()
            row["tier"], row["pack"] = int(recorded.tier), recorded.pack
        # How the resolver reached this verdict, said on the row it describes:
        # `cached` — it is a cache entry's, as recorded; `fresh` — and it is
        # current and counts; `stale_reason` — why it is not current (the
        # resolver's words: "config.bed_xy 220.0 -> 250.0", "control not
        # demonstrated at this version — run nopekit check"). A row the page
        # showed without them would look exactly as current as one that is.
        row["cached"] = bool(how is not None and how.cached)
        row["fresh"] = bool(how is not None and how.fresh) and not everything
        reason = how.stale_reason if how is not None else ""
        if verdict.gate in unseen and not everything:
            # Issue #15, D1: in its own words, a count and one example — the
            # whole channel list (often 2 KB of solver paths) is `doctor`'s.
            reason = report_logic.unseen_words("meta", resolution, {verdict.gate})
        row["stale_reason"] = reason or (_MARKED_STALE if everything else "")
        # Which of the two: the technical page's flag reads the resolver's word
        # for each (review of D1: every unseen row there read "≈ invalidated"),
        # and the engineering page's Failing cause text, for a fail of one.
        row["unseen"] = bool(verdict.gate in unseen and not everything)
        # No clock here (architecture phase 5, SPEC 32): a verdict's `when` and
        # age go to `data/build.json` (`ages`), which git ignores, so a rebuild
        # that judged nothing new changes no tracked byte. What slipped through
        # before: every build rewrote `state.json` with new ages, so a check that
        # moved nothing still dirtied `git status` with the published page.
        # `ok` is the only predicate that means "it ran, it did not crash, and it
        # said yes". The page must never key a green tick off `passed`: a skipped
        # gate carries passed=False today, but a pack that sets passed=True next
        # to skipped=True would walk straight into the proof column.
        row["ok"] = verdict.ok
        # From `Verdict.outcome`, the one definition (R-5) — never the flags.
        # What slipped through (P2.1 design): this read `passed`, so a junk
        # truthy pass flag (`"yes"`) rendered `pass` on a row whose `ok` was
        # False, and a skipped-and-errored verdict rendered by its first flag.
        # An unqualified evaluator's row reads `unqualified` (P2.3-D17), never
        # `errored`: its verdict says `error` only so that it is never ok (R-2),
        # and the page painted a crash's word on something that crashed nothing.
        # A pass outside its operating context reads `outside-context` (P2.4-D22:
        # never counted `unqualified` — outside its context the evaluator is
        # still qualified; what does not hold is this pass's inputs). What
        # slipped through (review of P2.4): the CLI split it out and the page
        # painted `? UNQUALIFIED` beside the row text "outside operating context".
        row["status"] = ("outside-context" if claim_logic.outside_context(verdict) else
                         "unqualified" if getattr(verdict, "unqualified", "") else
                         {"error": "errored", "skipped": "skipped", "pass": "pass",
                          "fail": "fail"}[verdict.outcome])
        if getattr(verdict, "unqualified", ""):
            # The reason in the table's words (`report.HUMAN`), the token kept
            # for a machine reader; the page owns neither.
            # `text` is the row's sentence, lead and all (`report.unqualified_text`):
            # the page rendered `error`, R-2's fallback, which carries the token
            # (review of P2.3: `unqualified: qualification:not-yet|0` on every
            # bracket row of a clone without its cache).
            row["qualification"] = {
                "token": verdict.unqualified,
                "reason": report_logic.qualification_reason(verdict.unqualified),
                "text": report_logic.unqualified_text(verdict.unqualified)}
        # The margin (D-17, P2.4-D8): `claims.margin`, the one function, or
        # why there is none — on every row, never computed by the page (the
        # site renders the ledger; it never computes truth). `margin` null and
        # `margin_why` "" are the two that do not apply to each other.
        found = claim_logic.margin(verdict)
        row["margin"] = (None if found.fraction is None
                         else float(f"{found.fraction:.6g}"))
        row["margin_why"] = found.why
        row["views"] = sorted({(loc.view or "") for loc in (verdict.locators or [])
                               if (loc.view or "")})
        if not verdict.locators and not verdict.ok:
            # Said out loud rather than left as an empty list: an unlocatable
            # failure is a normal, honest outcome (a gate attaches a locator only
            # when it genuinely knows the position, because a confident highlight
            # on the wrong part is worse than none) and the page has to be able
            # to say so instead of looking like the overlay is broken.
            row["unanchored"] = True
        # The evidence as the page links it (`page.evidence_plan`): the published
        # copy's href, aligned with `evidence`, or "" where it is not on the page.
        row["evidence_assets"] = list(assets.get(verdict.gate) or
                                      ["" for _ in verdict.evidence or ()])
        verdict_rows.append(row)
    # Reading order, baked (SPEC 32): errored, fail, unqualified, outside its
    # context, skipped, pass, then the natural gate id — what the template's
    # VERDICT_ORDER and anchored-verdict ranking computed in the browser.
    for i, row in enumerate(sorted(verdict_rows, key=lambda r: (
            _ROW_RANK.get(str(r.get("status")), len(_ROW_RANK)),
            store._natural(str(r.get("gate")))))):
        row["order"] = i

    claim_rows: list[dict] = []
    needs = {spec.id: list(getattr(spec, "needs", None) or ())
             for spec in report_logic._specs(registry)}
    # In severity order (P2.1-D16): what slipped through (P2.0 F-5), the page
    # listed claims in record order, a skip above the crash it should sit under.
    for claim in report_logic.in_severity(view, composed):
        found = composed[claim.id]
        status = found.status
        unproven = report_logic._unproven_for(claim.id, cover, view)
        # The claim as every JSON channel shows it (`report.claim_json`, P2.5a-D20):
        # its record fields, its terminal and authority, and `physical_result`
        # carrying the judge's `counts` and `why` — the page keys its ok tone on
        # those, never on `passed` (critique 3 of the P2.5a design).
        row = report_logic.claim_json(claim, composed=found, exports=view.exports)
        row["status"] = str(status)
        # A recorded result's outcome as a word key (architecture phase 5,
        # template side): the page shows "pass" or "fail" from `outcome` and its
        # tone from `counts`, and reads no `passed` flag (SPEC 32: the template
        # scan refuses a `.passed` read, which a verdict row's flag and a
        # result's field share).
        if isinstance(row.get("physical_result"), dict):
            row["physical_result"]["outcome"] = (
                "pass" if row["physical_result"].get("passed") is True else "fail")
        # The words beside the kept enum (P2.1-D12): `key`, `word`, `cause`,
        # `reason`, `errored` — all `report.HUMAN`'s, so the page owns none.
        row.update(report_logic.status_view(found, view, claim,
                                            stale_reasons=stale_reasons))
        row["acceptance_render"] = claim.acceptance.render()
        row["gates"] = list(cover.get(claim.id) or claim.gates or [])
        row["verdicts"] = [v.gate for v in report_logic._claim_verdicts(view, claim)]
        row["evidence"] = sorted({e for v in report_logic._claim_verdicts(view, claim)
                                  for e in (v.evidence or [])})
        # Each covering gate that produced no pass that counts, with its reason
        # led by the fact (`unrun`, `errored: …`, `skipped: …`, `unqualified:
        # …`). PARTIAL went with P2.1 (GLOSSARY §3: under the composition no
        # Checked claim has one); `disagree` is what is left of it — a claim the
        # resolver calls `pass` while one of these exists — and the page paints
        # it loud, never as a lesser success (D18; review of the P2.1 design:
        # with `partial` gone and nothing in its place, a resolver regression
        # would have reached the page as a clean Checked chip).
        row["unproven"] = [{"gate": gid, "why": why} for gid, why in unproven]
        row["disagree"] = bool(report_logic._disagreement(view, claim, found, cover))
        # Which values are the claim's (P2.4, critique 10 of its design): the
        # evaluators whose value is compared with its acceptance condition, and
        # the passes whose value is not (another quantity or units) — computed
        # here by `claims`, so the page reads which value to show and never
        # chooses one. What slipped through: the page headlined the first
        # passing verdict, a guard's `8.57 L/h` on failing C1.
        row["compared"] = claim_logic.compared_gates(claim, view.verdicts)
        row["not_compared"] = [gid for gid, _why in claim_logic.not_compared(
            claim, view.verdicts, needs)]
        # The CLAIM's limit, which the page shows beside the claim's value — never
        # the verdict's: a compared pair is the one kind whose two limits can
        # part. What slipped through (review of P2.4): the summary drew the
        # headline verdict's own limit, so Failing C3 read `0.195 MPa / 15 MPa`
        # against a claim of `<= 0.1 MPa` — S-35's slip, on the page.
        row["limit_text"] = report_logic.limit_words(claim.acceptance)
        # The board the claim sits on (SPEC 17): the record's, or `other`.
        row["board"] = board_of.get(claim.id, page_logic.OTHER)
        claim_rows.append(row)

    # The one producer's statuses, with D18's disagreeing claims named
    # (`report.requirement_statuses`): the same `cover` as `row["disagree"]`.
    served = report_logic.requirement_statuses(view, composed, cover)
    views = [_chart_margins(v.to_dict()) for v in view.views]
    problems = locator_problems(view.views, view.verdicts)

    payload = {
        "meta": {
            # Every ProjectMeta field but `people` (architecture phase 6): the
            # page is published as committed, and the names and addresses a
            # round may commit as are the project's, not the page's to publish.
            **{key: value for key, value in view.meta.to_dict().items()
               if key not in _UNPUBLISHED_META},
            "stale": everything or bool(stale_gates),
            "stale_reason": (_MARKED_STALE if everything
                             else _stale_reason(resolution, stale_gates)),
            # Review of issue #15's D1: every gate not current is not current
            # only for a channel nopekit cannot see. The page's notice reads it
            # (not its Stale claims: a Failing claim's unseen fail read "Some
            # results are older than the design"), and the technical page's
            # banner. `stale_check` is the command that reaches the stale gates
            # — a plain `nopekit check` is tier 0 — which `site status` and the
            # banner print (each said a plain `nopekit check` for a tier-2
            # solver).
            "stale_unseen": bool(not everything and stale_gates
                                 and set(stale_gates) <= set(unseen)),
            "stale_check": ("nopekit check" + report_logic._tier_flag(registry, stale_gates)
                            if everything or stale_gates else ""),
            # Which records this page was built from: `store.records_digest`,
            # over the record files' paths and bytes and `project.json`. The
            # page's own staleness (`cli._site_state`) compares it with the
            # records now. What it replaced compared mtimes with `ledger.json`,
            # which from 1.3 is a generated index every command rewrites — a page
            # of unchanged records read stale after any `status` (cli:H16). The
            # records are half of what the page renders; `resolution_digest`, set
            # below, is the other half.
            "records_digest": store.records_digest(root),
            # Stated on the artifact itself, because someone will find this file
            # on its own and wonder whether editing it does anything.
            "generated": "nopekit site build — an output of the ledger and its verdict "
                         "cache, not a source. If a number here is wrong, the ledger is "
                         "wrong.",
        },
        "readiness": {
            # markdown=False: the page styles its own emphasis, and `**not
            # ready**` rendered literally into HTML reads as a typo in the one
            # sentence that has to be believed.
            "verdict": report_logic._verdict_sentence(
                view, composed, registry, stale=everything, markdown=False),
            # Kept as they were (P2.1-D12): `counts` by enum value, `ready` —
            # nothing stops `check` — and `n_gaps`, gap records. What the page
            # reads from P2.1: `all_required_checked` for its READY headline
            # (GLOSSARY §4), and `tally` for its count strip, in HUMAN's words
            # with a crash counted apart (`N skipped (k errored)`).
            "counts": summary["by_status"],
            "by_key": summary["counts"],
            "errored": summary["errored"],
            "tally": _tally(composed),
            "kinds": summary["by_kind"],
            "ready": summary["ready"],
            "all_required_checked": summary["all_required_checked"],
            "unresolved": summary["unresolved_ids"],
            "blocking": summary["blocking_ids"],
            "n_claims": summary["n_claims"],
            "n_critical": summary["n_critical"],
            "n_gates": summary["n_gates"],
            "n_gaps": summary["n_gaps"],
            # P2.5b (S-60's field half): *ready* per milestone, the one
            # predicate's (`claims.unresolved` through `summarise`), as last
            # evaluated — the boundary's is `export <m> --dry-run`'s. The page
            # renders it in P5; until then it is here for an agent to read.
            "milestones": summary.get("milestones") or {},
        },
        # Every status's words, keyed by the enum value a claim row's `status`
        # holds: the page's chip labels and hints, so the site never owns a word
        # (D-16). An `errored` entry for a crash's Skipped.
        "words": report_logic.words_table(),
        # Each verdict row's outcome word, the same way: `HUMAN`'s.
        "outcome_words": report_logic.outcome_words(),
        # The rest of what the page says that is the ledger's word, not the
        # page's: `invalidated`, each gap record's state and each verdict chip's
        # title (`report.page_phrases`).
        "phrases": report_logic.page_phrases(),
        "claims": claim_rows,
        # SPEC decision 12: each requirement, its status from the one producer
        # over the compositions above, worded by `report.requirement_json`. The
        # page draws none until the map (phase 5); `resolution_digest` covers
        # them, so a moved requirement status marks the page behind.
        "requirements": [report_logic.requirement_json(r, served[r.id])
                         for r in view.requirements],
        "verdicts": verdict_rows,
        "views": views,
        "locator_problems": problems,
        "params": [_param_row(param, view) for param in params],
        # SPEC 38: an input's basis is its proposer's, looked up (`""` with no
        # proposer, `undeclared` when no discoverable pack declares it).
        "inputs": [{**artifact.to_dict(), "extracted": artifact.extracted,
                    "basis": input_bases.get(artifact.id, "")}
                   for artifact in view.inputs],
        "gaps": [need.to_dict() for need in report_logic._needs(view, registry)],
        "decisions": [decision.to_dict() for decision in view.decisions],
        # SPEC 14: every rejected alternative as tokens — its type, its return
        # condition's state now, its preference's standing — `summarise`'s.
        "rejections": summary["rejections"],
        # The rebuild prediction (P2.5a-D16): `claims.rebuild`, the one producer.
        "rebuild": [found.to_dict() for found in claim_logic.rebuild(view)],
    }
    # What this page judged, so `cli._site_state` can ask whether a rebuild now
    # would judge the same (`resolution_digest`). Set last, over the finished
    # document, and by this function, so the build's digest and the reader's are
    # one computation over one producer's output and cannot drift apart.
    # Architecture phase 3b: the trace's state and amber
    # (`derivation.state_payload`), inside the resolution digest so an amber
    # change makes the page stale; null and empty when the caller has none.
    payload["trace"] = dict((trace or {}).get("trace") or {}) or None
    payload["amber"] = dict((trace or {}).get("amber") or {})
    payload["meta"]["resolution_digest"] = resolution_digest(payload)
    return payload


#: The rank `state` orders verdict rows by (SPEC 32): a crash first, louder
#: than a fail (invariant 2), then a fail, an unqualified evaluator, a pass
#: outside its context, a skip, a pass.
_ROW_RANK = {"errored": 0, "fail": 1, "unqualified": 2, "outside-context": 3, "skipped": 4,
             "pass": 5}


def _composed(view: Any, registry: Any, resolution: Any, everything: bool = False) -> dict:
    """The compositions `state` and the front page share — one
    `claims.compositions` call per build."""
    return claim_logic.compositions(view, registry=registry, stale=everything,
                                    stale_gates=frozenset(resolution.stale_gates),
                                    unseen_gates=claim_logic.unseen_gates(resolution))


def _chart_margins(row: dict) -> dict:
    """A chart view's current points with ``margin_text`` (SPEC 32): the
    distance from each to the view's own limit, in the site's words — what the
    template's ``marginText`` subtracted in the browser. Other views as they
    are."""
    if str(row.get("kind") or "") != "chart":
        return row
    data = row.get("data")
    if not isinstance(data, dict):
        return row
    meta = row.get("meta") or {}
    raw_limits = data.get("limit", data.get("limits", meta.get("limit")))
    limits = raw_limits if isinstance(raw_limits, list) else (
        [] if raw_limits is None else [raw_limits])
    limit = None
    for item in limits:
        if isinstance(item, dict):
            value, axis = item.get("value", item.get("limit")), item.get("axis")
            comparator = str(item.get("comparator") or "<=")
        else:
            value, axis, comparator = item, "y", "<="
        if axis != "x" and _finite(value):
            limit = (float(value), comparator)
            break
    key = next((k for k in page_logic.CHART_CURRENT_KEYS if k in data), None)
    if limit is None or key is None:
        return row
    # The y axis as the chart reads it (`page._view_axis`): the payload's, else
    # the meta's, where the three analytic packs put it.
    units = str(page_logic._view_axis(data, meta if isinstance(meta, dict) else {},
                                      "y").get("units") or "")
    said = report_logic.HUMAN["site"]["chart_margin"]

    def one(item: Any) -> Any:
        point = dict(item) if isinstance(item, dict) else {"value": item}
        y = point.get("y", point.get("value"))
        if not _finite(y):
            return item
        delta = float(y) - limit[0]
        inside = delta < 0 if limit[1].startswith("<") else delta > 0
        text = f"{page_logic._num(abs(delta))}{' ' + units if units else ''}"
        point["margin_text"] = said["inside" if inside else "past"].format(delta=text)
        return point

    current = data[key]
    data = dict(data)
    data[key] = [one(c) for c in current] if isinstance(current, list) else one(current)
    return {**row, "data": data}


def _finite(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value == value and value not in (float("inf"), float("-inf")))


# --------------------------------------------------------------------------- #
# what a page judged
# --------------------------------------------------------------------------- #
#: What ``resolution_digest`` leaves out of a ``state`` document: the two
#: digests, each verdict row's ``evidence_assets``, and the views with their
#: ``locator_problems``. (Until phase 5 it also left out the clock — the
#: build's ``built`` stamp and each row's ``when`` and ``age_s`` — which the
#: document no longer holds: they are in ``data/build.json``. Phase 3a's
#: ``judgement_digest`` is this function, renamed: "judgement" now names
#: nothing on the page, and the glossary's *judgment* is an expert's.) Everything else is judged, including keys a
#: later ``state`` adds: a new clock-like key makes pages read stale after every
#: check, which someone sees the same day; an unwatched judgement is the defect
#: this digest exists for (review, ``repro_site``). *Rejected:* an allow-list of
#: judged keys, which fails silent in exactly that direction; judging ``when``,
#: which a check that changed nothing moves (a cache hit is an obs run), so every
#: sweep would send the reader to rebuild a page whose every verdict still
#: stands (cli:H16's complaint, in a new place). The views are page furniture,
#: drawn by viewgens that a reader asking "is the page current?" never runs: a
#: model edit that moves no verdict leaves the judgement current and the drawing
#: old, and ``site build`` redraws it. Not a claim — no verdict rests on a view.
_UNJUDGED_META = frozenset({"records_digest", "resolution_digest"})
#: `evidence_assets` says where a copy is on the page, which moves with what is
#: on disk, never with the judgement.
_UNJUDGED_ROW = frozenset({"evidence_assets"})
_UNJUDGED_KEYS = frozenset({"views", "locator_problems"})


def _tally(composed: Any) -> list[dict]:
    """The page's count strip: `report.count_bits`, one item per status word
    present, Checked first then severity order, each `{key, status, n, errored,
    label}` — `label` the terminal count line's own bit (`7 skipped (6
    errored)`), so the strip and the line are one producer. What slipped through
    (review of the P2.1 design): the strip printed literal labels per enum
    value, so a crash counted under the skip with no `(k errored)`, and "with no
    gate" and "capability gap" stayed on the page after every other channel
    moved."""
    return report_logic.count_bits(composed)


def resolution_digest(payload: Any) -> str:
    """sha256 over what a ``state`` document judged: its claim statuses and
    PARTIAL markers, the readiness sentence and counts, every verdict row with
    how the resolver reached it (``cached``, ``fresh``, ``stale_reason``), the
    parameters with the gates that read them, the gaps and the records it shows
    — everything but ``_UNJUDGED_*``.

    Why it exists: a page renders the records AND the resolver's judgement of
    the verdict cache against the live model, and ``meta.records_digest`` sees
    only the first. A ``check`` that FAILed C1 moved no record, so ``site
    status``, ``status`` and ``doctor`` read a page showing C1 PASS as "current
    with the records" (review, ``repro_site``). Before checkpoint 1.3 the check
    rewrote ``ledger.json`` and the mtime rule caught it. *Rejected:* a
    fingerprint of the files the resolution read (``verdicts.watched_paths``) —
    a second opinion about whether the resolution moved, blind to what is not a
    watched file (``controls.json`` admitting a control, a remembered crash, a
    solver installed or removed); this asks the one resolver (R-5) and compares
    its answer. Never a judgement itself: the page computes nothing from it.
    """
    meta = {key: value for key, value in (payload.get("meta") or {}).items()
            if key not in _UNJUDGED_META}
    rows = [{key: value for key, value in dict(row).items() if key not in _UNJUDGED_ROW}
            for row in payload.get("verdicts") or ()]
    judged = {key: value for key, value in payload.items()
              if key not in _UNJUDGED_KEYS and key not in ("meta", "verdicts")}
    judged["meta"], judged["verdicts"] = meta, rows
    # allow_nan: a digest is not a file, and `atomic_write_json` already refuses
    # the page a NaN names (S-47); refusing here too would turn "is the page
    # current?" into a crash of `status`, the command run when something is wrong.
    text = json.dumps(judged, sort_keys=True, ensure_ascii=False, allow_nan=True,
                      separators=(",", ":"))
    return sha256_text("nopekit-resolution-v1\n" + text)


def resolution_moved(shown: Any, now: Any) -> list[str]:
    """What a page shows that a rebuild would not, as short facts: each claim
    whose status moved (``C1 pass -> fail``, ``C9 (none) -> pending`` for one
    the page lacks), else each verdict row whose outcome or measurement moved
    (``g.one pass 0.41 mm -> pass 0.29 mm``; ``… (stale)`` on a row that does
    not count), else each parameter whose value moved (``thickness 7 mm -> 8
    mm``) or, its value standing, the first other field that did (``thickness
    rationale``), in the documents' order. ``[]`` when none moved — the
    readiness, a stale reason's words: the caller then says only THAT it moved.
    ``shown`` is the page's ``state.json``, ``now`` a ``state`` document built
    now; both are read, never judged again.

    The parameter tier arrived with the parameter view (review, checkpoint
    1.3): the page's ``params`` are the model's values and rationales now, so a
    docstring edit moves what the page shows and no verdict, and a reason that
    named nothing sent the reader looking for a verdict that had not moved."""
    def claims_of(doc: Any) -> dict[str, str]:
        return {str(row.get("id")): str(row.get("status") or "")
                for row in (doc.get("claims") or ())}

    def row_of(row: Any) -> str:
        said = [str(row.get("status") or "")]
        measured = row.get("measured")
        if isinstance(measured, (int, float)) and not isinstance(measured, bool):
            said.append(f"{measured:g}" + (f" {row['units']}" if row.get("units") else ""))
        if row.get("stale_reason"):
            said.append("(stale)")
        return " ".join(said)

    def gates_of(doc: Any) -> dict[str, str]:
        return {str(row.get("gate")): row_of(row) for row in (doc.get("verdicts") or ())}

    moved: list[str] = []
    for before, after in ((claims_of(shown), claims_of(now)), (gates_of(shown), gates_of(now))):
        for key in [*after, *(k for k in before if k not in after)]:
            if before.get(key) != after.get(key):
                moved.append(f"{key} {before.get(key) or '(none)'} -> "
                             f"{after.get(key) or '(none)'}")
        if moved:
            return moved

    def params_of(doc: Any) -> dict[str, dict]:
        return {str(row.get("name")): dict(row) for row in (doc.get("params") or ())}

    def value_of(row: dict | None) -> str:
        if row is None:
            return "(none)"
        value = row.get("value")
        said = "(no value)" if value is None else f"{value:g}" if (
            isinstance(value, (int, float)) and not isinstance(value, bool)) else str(value)
        return said + (f" {row['units']}" if row.get("units") and value is not None else "")

    before, after = params_of(shown), params_of(now)
    for key in [*after, *(k for k in before if k not in after)]:
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if old is None or new is None or old.get("value") != new.get("value"):
            moved.append(f"{key} {value_of(old)} -> {value_of(new)}")
        else:
            # In the row's own key order (``ParamView.to_dict``'s, then the
            # flags), so an emptied rationale reads as ``rationale``, not as the
            # ``defended`` flag it also moved.
            field = next(name for name in [*new, *(k for k in old if k not in new)]
                         if old.get(name) != new.get(name))
            moved.append(f"{key} {field}")
    return moved


def _param_views(ledger: Ledger, model: Any, model_error: str, resolution: Any,
                 registry: Any) -> list:
    """``modelio.param_view`` of ``model``, each view's ``gates`` read off the
    resolution's read sets for the gates ``registry`` registers (all of them
    with no registry) — what the page shows when its caller handed none."""
    registered = ({spec.id for spec in report_logic._specs(registry)}
                  if registry is not None else None)
    reads = {gate: paths for gate, paths in (getattr(resolution, "read_sets", None)
                                              or {}).items()
             if registered is None or gate in registered}
    return modelio.param_view(ledger, model, model_error=model_error, read_sets=reads)


def _param_row(param: Any, ledger: Ledger) -> dict:
    """One parameter as ``modelio.ParamView.to_dict`` spells it — the model's
    value where it lives, its rationale and losers from both homes, the gates
    that read it — and a flag for the provenance it lacks.

    ``defended`` is ``modelio.undefended_params``'s answer, the one ``doctor``,
    ``status`` and the report print: false for a number the model holds that no
    home defends, which the next agent will change; ``None`` where the model
    holds no number (``model_error`` says why, or the record outlived its field)
    — there is nothing to defend, and "undefended" would be a claim about text
    nobody read. The page shows the flag next to the number rather than in a
    list somewhere else.

    ``changed_in`` is derived from the decisions (``decisions.changed_in``, as
    ``why`` derives it) — the page's "last moved in". The record row it
    replaced carried the key, and the strict reader keeps it out of records.
    """
    row = param.to_dict()
    row["changed_in"] = decision_logic.changed_in(ledger, param.name)
    row["defended"] = (None if not param.home
                       else not modelio.undefended_params([param]))
    row["derived"] = bool(param.derived_from)
    return row


# --------------------------------------------------------------------------- #
# the resolution, and ages
# --------------------------------------------------------------------------- #
#: A row's (and ``meta``'s) stale reason under the caller's ``stale=True``.
#: Said as what it is — the caller's word, not the resolver's finding — so a
#: reader does not go looking for an input that moved. *Rejected:* the old
#: "supplied by the caller", which named who and not what; and an empty reason,
#: which leaves a not-current row looking exactly like a current one.
_MARKED_STALE = "marked stale by the caller"


def _live_projection(root: str, ledger: Ledger) -> tuple[Any, dict | None, str]:
    """``(model, projection, model_error)`` for the model the ledger names —
    ``(None, None, "")`` when it names none, which is a normal early state.

    Loaded here only when the caller handed the page neither a resolution nor a
    projection. *Rejected:* the projection recorded in ``.nopekit/model.json``,
    which this module used to fall back on — it is the model as the last
    command saw it, so a model edited since would resolve every entry against
    the old values and read current. A model that does not load returns its
    error instead, and the resolver reads every verdict that read it as not
    current (S-21): never raising here, because a page that cannot be built for
    a broken model hides the one fact the reader most needs.
    """
    entry = (ledger.meta.model_entry or "").strip()
    if not entry:
        return None, None, ""
    try:
        model = modelio.load_model(root, entry)
        return model, modelio.project(model), ""
    except NopekitError as exc:
        return None, None, str(exc)


def _resolve(root: str, ledger: Ledger, registry: Any, *, projection: dict | None = None,
             model: Any = None, model_error: str = "", now: str = "") -> Any:
    """What the page renders when its caller did not say: the ONE resolver's
    answer (R-5). ``projection``/``model`` as :func:`build` built them; with
    neither, the live model is loaded (:func:`_live_projection`)."""
    if projection is None and model is None and not model_error:
        model, projection, model_error = _live_projection(root, ledger)
    return verdict_logic.resolve(root, registry, projection, ledger,
                                 model_error=model_error, now=now, model=model)


def _stale_reason(resolution: Any, stale_gates: frozenset) -> str:
    """``meta.stale_reason``: the stale gates by name, each with the resolver's
    reason, in the resolution's order — at most ``verdicts.MAX_STALE_REASONS``,
    then ``(+n more)``, the cap ``status`` puts on one gate's reasons, so the
    banner stays a line. ``""`` when nothing is stale.

    What it replaced: ``model 3211… -> 9a0c…; inputs e3b0… -> 5f1d…`` — one pair
    of hashes for the whole project, which said THAT something moved and never
    which result it touched, and was the same sentence for a comment edit and a
    changed load case.
    """
    unseen = claim_logic.unseen_gates(resolution)
    named = [(gid, resolution.rows[gid].stale_reason)
             for gid in resolution.rows if gid in stale_gates and gid not in unseen]
    named += [(gid, "") for gid in sorted(stale_gates - unseen - {gid for gid, _ in named})]
    shown = [f"{gid}: {why}" if why else gid
             for gid, why in named[:verdict_logic.MAX_STALE_REASONS]]
    more = len(named) - len(shown)
    text = "; ".join(shown) + (f" (+{more} more)" if more > 0 else "")
    if unseen:
        # Issue #15, D1: the gates not current only for a channel nopekit
        # cannot see, in one clause of their own — a count and one example.
        # What slipped through: each gate's whole channel list (~2 KB of
        # solver paths) under "verdicts are STALE", for an untouched design.
        clause = report_logic.unseen_words("meta", resolution, unseen)
        text = f"{text}; {clause}" if text else clause
    return text


def _parse_iso(text: str) -> datetime | None:
    """Parse an nopekit timestamp, or None. Never raises, never guesses.

    Tolerant of the ``Z`` suffix `utcnow_iso` writes and of the offset forms a
    human might paste in. An unparseable timestamp yields None, which becomes a
    null age — a wrong age is worse than an absent one, because the page renders
    it with the same confidence as a right one.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_seconds(now: str, when: str) -> float | None:
    """Seconds between ``when`` and ``now``, or None when either is unknown.

    Clamped at zero: a clock skew that produced a negative age would render as a
    result from the future, which reads as a bug in the page rather than a bug in
    the clock and sends the reader to the wrong place.
    """
    start, end = _parse_iso(when), _parse_iso(now)
    if start is None or end is None:
        return None
    if start.tzinfo is None or end.tzinfo is None:      # mixed naive/aware
        start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
    return round(max(0.0, (end - start).total_seconds()), 3)


# --------------------------------------------------------------------------- #
# vendoring
# --------------------------------------------------------------------------- #
#: Pinned exactly, never a range and never "latest". An unpinned CDN URL is a
#: build step somebody else controls: the page that rendered a verdict correctly
#: this morning can render it wrong this afternoon, with no commit in between and
#: nothing to bisect. The site is the artifact whose job is to be trusted when a
#: gate says something is wrong.
THREE_VERSION = "0.169.0"

#: jsdelivr's npm mirror, which serves the published package tree unmodified —
#: so the vendored copy and the CDN copy are the same files at the same relative
#: paths, and `nopekit site vendor` changes where the modules come from without
#: changing what they are.
_THREE_BASE = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}"

#: Vega, Vega-Lite and vega-embed, which draw a bound figure's chart from the
#: spec the spine bakes (`page.chart_spec`; issue #15, rule 0.1 of its design:
#: draw with a trusted library and build as little as possible). Pinned exactly,
#: as three.js is, and each at least two weeks old when it was chosen
#: (2026-10-21): vega 6.2.0 (2025-09-16), vega-lite 6.4.1 (2025-09-23),
#: vega-embed 7.1.0 (2025-10-09). The UMD builds, each one self-contained file
#: (``vega.min.js`` 512 KB, ``vega-lite.min.js`` 249 KB, ``vega-embed.min.js``
#: 60 KB), loaded by `lib/figures.js` in that order, from ``vendor/vega/`` when
#: it is there, else from the CDN; `lib/figures.js` holds the same three URLs
#: and a test holds the two lists equal. *Rejected:* the ESM builds through the
#: import map (``vega.module.js`` imports 27 bare package names and
#: ``vega-lite``'s ``build/index.js`` four more, each an import-map line; and
#: jsdelivr's ``+esm`` of vega-embed 7.1.0 resolves vega-lite to 6.4.3, a
#: version nobody pinned); an import-map entry in the page shells (they are
#: the project's own and never regenerated, so a site made before this change
#: would never get the line, while ``lib/`` is refreshed by every build).
VEGA_VERSIONS = {"vega": "6.2.0", "vega-lite": "6.4.1", "vega-embed": "7.1.0"}


def vendor_urls() -> dict:
    """``{site-relative path: URL}`` for ``nopekit site vendor``. Offline three.js.

    The layout under ``vendor/`` **mirrors the package's own**, and that is not
    tidiness: ``jsm/loaders/GLTFLoader.js`` contains
    ``import { toTrianglesDrawMode } from '../utils/BufferGeometryUtils.js'``,
    so flattening the three files into one directory produces a vendored site
    that 404s on a relative import the CDN copy resolves fine. The breakage
    appears only offline, which is the one situation vendoring exists for and the
    one nobody tests before archiving a project.

    ``BufferGeometryUtils.js`` is in the list for the same reason — it is not
    imported by the page, it is imported by the loader, and a vendor step that
    copies only what the page names leaves a dangling dependency.

    The import map in each page (``engineering/index.html``,
    ``marketing/index.html``, ``technical.html``) is what chooses between these
    files and the CDN; this function only says what to fetch and where to put it. The mapping
    the template declares is::

        "three"      -> vendor/three.module.js       (or the CDN build)
        "three/addons/" -> vendor/jsm/               (or the CDN examples/jsm/)

    so the bare specifier inside every addon resolves either way without
    rewriting a single file.
    """
    return {
        posixpath.join(VENDOR_DIR, "three.module.js"):
            f"{_THREE_BASE}/build/three.module.js",
        posixpath.join(VENDOR_DIR, "jsm/loaders/GLTFLoader.js"):
            f"{_THREE_BASE}/examples/jsm/loaders/GLTFLoader.js",
        posixpath.join(VENDOR_DIR, "jsm/controls/OrbitControls.js"):
            f"{_THREE_BASE}/examples/jsm/controls/OrbitControls.js",
        posixpath.join(VENDOR_DIR, "jsm/utils/BufferGeometryUtils.js"):
            f"{_THREE_BASE}/examples/jsm/utils/BufferGeometryUtils.js",
        # The two pages: the viewer decodes a GLB written with
        # EXT_meshopt_compression (a large model, compressed). The decoder
        # imports nothing, so it sits where the addon path names it, and a
        # vendored site loads such a model offline too. *Rejected:* the CDN
        # copy alone (the one situation vendoring exists for would 404 on it).
        posixpath.join(VENDOR_DIR, "jsm/libs/meshopt_decoder.module.js"):
            f"{_THREE_BASE}/examples/jsm/libs/meshopt_decoder.module.js",
        # Issue #15: the chart library (`VEGA_VERSIONS`), under vendor/vega/,
        # so an offline site draws its charts too and does not fall back to
        # the table of numbers.
        **{posixpath.join(VENDOR_DIR, "vega", f"{name}.min.js"):
           f"https://cdn.jsdelivr.net/npm/{name}@{version}/build/{name}.min.js"
           for name, version in VEGA_VERSIONS.items()},
    }
