# SPDX-License-Identifier: Apache-2.0
"""Projects built for a test: the bracket copied, and a pack's baseline wrapped.

Not a test module (no ``test_`` prefix, so discovery never collects it). Three
builders, each written once so every later test that needs a planted project
gets the same one:

* :func:`bracket_copy` — the reference project, with an optional thickness edit
  and an optional repository around it. With ``migrated=True`` it is the bracket
  as a clone would hold it; by default it is the bracket as it stood before its
  own migration (below). The file list is ``test_fresh_clone``'s (U10): ``git
  ls-files --cached --others --exclude-standard`` where a repository tracks the
  bracket, the bytecode- and output-free walk where none does (verify.sh
  ``--dir`` copies the tree without its ``.git``). One list, so a copy here and
  the transcript's copy can never disagree about what a fresh clone contains.
* :func:`wrap_pack_baseline` — a project whose MODEL is a pack's
  ``selftest/baseline.json``: the pack's known-good design, run through
  ``check`` the way a user's project would run it, with one claim per gate so
  every verdict lands on a claim.
* :func:`write_known_good` — a planted project's ``selftest/known_good.py``,
  the design stated in full (P2.3: a project evaluator with no known-good
  control is unqualified, and its claim reads Gap).

What slipped through before these existed: the E4 rows for a multi-module pack
needed a wrapped fdm-print project from a unit in the same wave (judge J1), and
the R-8 oracle needs the SAME wrapped projects on the old spine and the new one.
A builder each test writes for itself drifts — one strips ``_notes``, one
forgets the assets — and two spines compared on two different projects prove
nothing about the spines.

The wrapped project is written in the **legacy** layout (one
``.atompipe/ledger.json``, the ``1e09113`` ignore file), by hand, from literals
in this file: never through ``store.init`` or ``Ledger.to_dict``. Those are the
spine's, and the spine this phase is changing; a fixture written by the code
under test changes shape with it, and "the old spine reads it natively" would
quietly stop being true at the commit that moves the layout.

**The bracket before its migration.** Checkpoint 1.3 (U32) migrated the tracked
bracket: records, ``project.json``, a committed verdict cache, no ledger, no run
history. Every test written before that commit copied the bracket expecting what
it then was — a legacy ledger for the migration to start from, a run history for
``doctor`` to warn about, no cache so the first ``check`` runs every gate and every
control — and the R-8 oracle needs a bracket the OLD spine reads natively. What
slipped through: the unit that migrated the bracket found forty-five failures
across eight test files for no reason but that their fixture had moved under
them, and none of those tests was wrong. So the default copy is the bracket's own sources (model, gates, selftest,
as they are now) around the bracket's legacy state, byte for byte as the last
commit before the migration tracked it, frozen in ``tests/bracket_legacy/``.
*Rejected:* ``migrated`` as the default, with every legacy caller naming
``legacy=True`` — some forty call sites in twelve files rewritten to keep meaning
what they already meant (R-6: an existing test stays byte-identical);
*rejected:* deriving the legacy ledger from the records through ``store`` or
``Ledger.to_dict`` — the spine under test writing its own fixture, the thing the
paragraph above refuses; *rejected:* reading it from git history — a verify.sh
``--dir`` copy has none.

Run:  (a helper; the tests that use it say how to run them)
"""
from __future__ import annotations

import json
import os
import pprint
import re
import shutil
from typing import Any

import _env
import test_fresh_clone
from atompipe import gates, packs

#: The bracket in the checkout this helper runs from.
BRACKET = test_fresh_clone.BRACKET

#: The bundled packs in this checkout.
PACKS = os.path.join(_env.REPO, "packs")

#: The bracket's legacy state — its ``.atompipe/.gitignore`` (as ``gitignore``: a
#: dot-file here would be an ignore file git applies to this directory), its
#: ``ledger.json`` and its run history — byte for byte as ``examples/bracket``
#: tracked them at 801cece, the last commit before its migration (U32). A copy
#: of these bytes, not a pointer to history: see the module docstring.
LEGACY_BRACKET = os.path.join(_env.REPO, "tests", "bracket_legacy")

#: Where each file of ``LEGACY_BRACKET`` goes in a legacy copy, as
#: ``(fixture path, project path)``, `/`-separated. Listed, not walked, so a
#: stray file dropped into the fixture directory never rides into a project.
LEGACY_BRACKET_FILES: tuple[tuple[str, str], ...] = (
    ("gitignore", ".atompipe/.gitignore"),
    ("ledger.json", ".atompipe/ledger.json"),
    ("runs/0001-7d24ce6c.json", ".atompipe/runs/0001-7d24ce6c.json"),
    ("runs/0002-6a57e5fa.json", ".atompipe/runs/0002-6a57e5fa.json"),
    ("runs/0003-e44e8096.json", ".atompipe/runs/0003-e44e8096.json"),
    ("runs/0004-05329ee2.json", ".atompipe/runs/0004-05329ee2.json"),
    ("runs/0005-25c288bb.json", ".atompipe/runs/0005-25c288bb.json"),
)

#: What the migration wrote into the bracket, which a legacy copy leaves out:
#: the project marker, the three marked ignore/attribute files, the records and
#: the verdict cache. A path under ``.atompipe/`` that is none of these (and not
#: the ignored index the walk may list) is refused rather than guessed at: the
#: day the bracket gains, say, ``.atompipe/packs/``, whoever added it decides
#: whether the legacy bracket had it. Spelled here, not read from
#: ``store.RECORD_DIRS``: a fixture that takes its shape from the spine under
#: test moves with it.
_MIGRATED_FILES = frozenset({".atompipe/project.json", ".atompipe/.gitignore",
                             ".gitignore", ".gitattributes",
                             ".atompipe/ledger.json", ".atompipe/ledger.legacy.json"})
_MIGRATED_DIRS = (".atompipe/verdicts/", "claims/", "params/", "decisions/", "needs/",
                  "results/")

#: The thickness default in `model/bracket.py`, as the model spells it. Exactly
#: one line may match: a second match (a comment quoting the line, a second
#: dataclass) would make "the thickness edit" ambiguous, and a copy edited in
#: two places is not the bracket at that thickness.
_THICKNESS_RE = re.compile(r"^(    thickness: float = )([0-9][0-9.]*)$", re.MULTILINE)

#: What a copy of a pack directory leaves out: bytecode, and dot-directories —
#: openmodelica once generated into ``selftest/.generated/``, and a stale
#: ``.generated`` copied into a project is a previous run's output passed off as
#: the pack. The same three rules as the control entry's static walk outside git
#: (spec §3.8), so a copied pack digests like the bundled one.
_PACK_PRUNE_NAMES = frozenset({"__pycache__"})
_PACK_SKIP_SUFFIXES = (".pyc", ".pyo", ".pyd")

#: The legacy ignore file, byte for byte as `store.init` wrote it at 1e09113 (and
#: as the bracket tracks it). The migration (1.3) recognises this text as a
#: template prefix; a wrapped project that carried a different one would test a
#: user's hand edit instead.
LEGACY_GITIGNORE = """\
# Generated files are outputs, not sources.
#
# out/ is gate scratch and evidence: meshes, plots, solver working directories.
# All of it is rebuildable from the model plus inputs/, and committing it turns
# every check run into a thousand-line diff.
out/
*.tmp
*.lock

# ...but the ledger and the run history ARE the project. They carry the rejected
# alternatives and the proof that a gate once passed; neither can be regenerated.
# These lines are redundant against the patterns above and deliberately so —
# they state the intent where the next person will look for it.
!ledger.json
!runs/
"""

#: `meta.created` of every wrapped project. Fixed, not the clock: two wraps of
#: one pack must be byte-identical, or a determinism test comparing two temp
#: projects would be comparing their creation times. The value is the bracket's
#: own era and means nothing else.
WRAP_CREATED = "2026-09-11T00:00:00Z"

#: `meta.spine_version` of a wrapped project: the version the legacy spine
#: stamps (the bracket's ledger carries the same).
WRAP_SPINE_VERSION = "0.1.0"

_WRAP_MODEL = '''\
# SPDX-License-Identifier: Apache-2.0
"""{pack}'s selftest baseline, wrapped as a project's model.

Written by tests/_projects.wrap_pack_baseline. `build()` returns the pack's
`selftest/baseline.json` minus its `_` keys (prose for a reader, not inputs for
a gate), so `check` hands every gate of the pack exactly the known-good design
its controls are measured against. `Config` has no fields: nothing here is a
parameter a human chose, only a design a pack author already validated.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

BASELINE = {baseline}


@dataclass
class Config:
    """No inputs: the design is the baseline."""


CONFIG = Config()


def build(config=None):
    """The baseline, a fresh copy per call, so no reader can edit the model."""
    return copy.deepcopy(BASELINE)
'''


# --------------------------------------------------------------------------- #
# the bracket
# --------------------------------------------------------------------------- #
def set_thickness(project: str, thickness: float) -> None:
    """Edit ``project``'s ``model/bracket.py`` thickness default in place.

    Refuses unless exactly one line matches, and writes ``repr(float(...))`` —
    ``8.0``, never ``8`` — so the edit is the one a person would type."""
    path = os.path.join(project, "model", "bracket.py")
    with open(path, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    found = _THICKNESS_RE.findall(text)
    if len(found) != 1:
        raise AssertionError(
            f"{path}: expected exactly one `thickness: float = <n>` line, found "
            f"{len(found)} — the thickness edit would be ambiguous")
    text = _THICKNESS_RE.sub(lambda m: m.group(1) + repr(float(thickness)), text)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _commit_all(project: str, message: str) -> None:
    """``git init`` + ``add -A`` + one commit, with the test identity (a runner has none)."""
    for argv, identity in ((["-c", "init.defaultBranch=main", "init", "-q"], False),
                           (["add", "-A"], False),
                           (["commit", "-q", "-m", message], True)):
        proc = _env.git(argv, cwd=project, identity=identity)
        if proc.returncode != 0:
            raise AssertionError(f"git {' '.join(argv)} in {project} failed: "
                                 f"{proc.stderr.strip()}")


def _migrated_path(rel: str) -> bool:
    """Whether ``rel`` (bracket-relative) is something the bracket's migration
    wrote, which a legacy copy leaves out. Refuses an ``.atompipe/`` path it does
    not know (see ``_MIGRATED_FILES``)."""
    if rel in _MIGRATED_FILES or rel.startswith(_MIGRATED_DIRS):
        return True
    if rel.startswith(".atompipe/"):
        raise AssertionError(
            f"examples/bracket/{rel}: bracket_copy does not know whether the legacy "
            "bracket had this file; say so in tests/_projects.py (_MIGRATED_FILES) "
            "or add it to tests/bracket_legacy/")
    return False


def bracket_copy(dest: str, *, thickness: float | None = None, git: bool = False,
                 migrated: bool = False) -> str:
    """Copy the bracket into ``dest`` (made if missing); return its absolute path.

    ``migrated=True``: the bracket as a clone holds it — records,
    ``.atompipe/project.json``, the committed verdict cache. Default: the bracket
    before its migration — the same model, gates and selftest, around the legacy
    ledger, ignore file and run history of ``LEGACY_BRACKET``, with no record, no
    marker block and no cache, so the first ``check`` migrates it and runs every
    gate and control (module docstring).

    ``thickness`` edits the model's default after the copy (:func:`set_thickness`);
    ``git`` then makes ``dest`` its own repository with one commit of everything,
    as a user who cloned the bracket has it — so a `git ls-files` walk in the copy
    sees what a clone's would. Without ``git``, ``dest`` sits in whatever
    repository encloses it (none, under the temp dir).
    """
    dest = os.path.abspath(dest)
    source, files = test_fresh_clone.bracket_listing()
    if not files:
        raise AssertionError(f"the {source} listing of the bracket is empty")
    os.makedirs(dest, exist_ok=True)
    copies = [(os.path.join(BRACKET, *rel.split("/")), rel) for rel in files
              if migrated or not _migrated_path(rel)]
    if not migrated:
        copies += [(os.path.join(LEGACY_BRACKET, *fixture.split("/")), rel)
                   for fixture, rel in LEGACY_BRACKET_FILES]
    for src, rel in copies:
        target = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)
    if thickness is not None:
        set_thickness(dest, thickness)
    if git:
        message = ("a copy of examples/bracket" if thickness is None
                   else f"a copy of examples/bracket at thickness {float(thickness)!r}")
        _commit_all(dest, message)
    return dest


# --------------------------------------------------------------------------- #
# a planted project's known-good design
# --------------------------------------------------------------------------- #
_KNOWN_GOOD_MODULE = '''\
# SPDX-License-Identifier: Apache-2.0
"""This planted project's known-good design: the one every control is one change away from.

Written by tests/_projects.write_known_good. Every value is stated here, as a
literal (D-27's pattern): nothing is read from the host it is handed, and it is
never ``return ctx`` — a known-good design that is the live one passes whatever
the live design is, until it does not (S-07, in the good direction). ``ROOT`` is
where the design's files live, or ``None`` when it has none of its own and keeps
the root it is handed; ``LIMITS`` are the claim limits it states, as a claim
record would carry them; ``CLAIMS`` are whole claim records it hands its gates as
they are.
"""
from __future__ import annotations

import copy
import dataclasses
import os

from atompipe.models import Acceptance, Claim, Ledger

PARAMS = {params}

ROOT = {root}

LIMITS = {limits}

CLAIMS = {claims}


def context(ctx):
    """``ctx`` with this design in place of whatever it carried: the params, the
    stated claims as its only ledger, no ``extra`` — and its own files."""
    ledger = Ledger(claims=[Claim(id=cid, statement=f"{{cid}}, as the known-good design states it",
                                  acceptance=Acceptance(limit=limit))
                            for cid, limit in LIMITS.items()]
                    + [Claim.from_dict(copy.deepcopy(row)) for row in CLAIMS])
    root = ctx.root if ROOT is None else ROOT
    return dataclasses.replace(ctx, params=copy.deepcopy(PARAMS), ledger=ledger, extra={{}},
                               root=root)
'''

#: Where :func:`write_known_good` puts the known-good design's own files,
#: project-relative. Under ``selftest/`` because that is where a project's
#: controls live, and a file there is part of every control's recorded version.
KNOWN_GOOD_FILES = "selftest/good"


def write_known_good(root: str, params: dict[str, Any], *,
                     files: dict[str, str | bytes] | None = None,
                     limits: dict[str, float] | None = None,
                     claims: list[dict] | None = None) -> str:
    """Give the project at ``root`` a ``selftest/known_good.py``; return its path.

    P2.3: a project evaluator is qualified only when it passes a known-good
    control as well as failing its known-bad one, and a project's known-good
    control is ``selftest/known_good.py``'s ``context(ctx)``. A synthetic
    project whose claims a test expects Checked must state one, or every such
    claim reads Gap (§7.3 of the P2.3 design). ``params`` is the design, stated
    in full and written as a literal — never derived from the host at run time.

    ``files`` (project-relative path -> text or bytes) are the design's own
    files: written under ``KNOWN_GOOD_FILES`` and the context's root pointed
    there, so a gate that reads ``data/x.txt`` reads the KNOWN-GOOD copy, and a
    test's edit to the live ``data/x.txt`` moves the real run alone. What would
    slip through without them: a known-good context that kept the project root
    read the live data files, so an edit meant to make the real run fail made
    the known-good control fail too, and the claim read Gap where the test
    meant Not met. ``limits`` (claim id -> limit) are the claim limits the
    design states, for a gate that reads its limit off a claim; ``claims`` are
    whole claim records (``Claim.to_dict``) handed as they are — the live
    records, for a gate whose check run reads one: a verdict that read a ledger
    value no qualification run read does not count (review of P2.3, ``br7``).
    """
    base = os.path.abspath(root)
    good_root = "None"
    if files is not None:
        for rel, body in files.items():
            target = os.path.join(base, *KNOWN_GOOD_FILES.split("/"), *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            mode, kw = ("wb", {}) if isinstance(body, bytes) else ("w", {"encoding": "utf-8",
                                                                       "newline": ""})
            with open(target, mode, **kw) as fh:
                fh.write(body)
        # abspath, never resolve(): the spine compares absolute paths, and a
        # symlinked temp directory resolved here would put the files outside it.
        good_root = ("os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "
                     + ", ".join(repr(part) for part in KNOWN_GOOD_FILES.split("/")) + ")")
        good_root = f"os.path.normpath({good_root})"
    path = os.path.join(base, "selftest", "known_good.py")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_KNOWN_GOOD_MODULE.format(
            params=pprint.pformat(params, indent=1, width=88, sort_dicts=True),
            root=good_root,
            limits=pprint.pformat(dict(limits or {}), indent=1, width=88, sort_dicts=True),
            claims=pprint.pformat(list(claims or []), indent=1, width=88, sort_dicts=True)))
    return path


def tree_files(root: str, rel: str) -> dict[str, bytes]:
    """Every file under ``<root>/<rel>``, as ``{project-relative path: bytes}`` —
    what :func:`write_known_good` copies as a design's files."""
    base = os.path.abspath(root)
    found: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(os.path.join(base, *rel.split("/"))):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                found[os.path.relpath(full, base).replace(os.sep, "/")] = fh.read()
    return found


# --------------------------------------------------------------------------- #
# a pack's baseline, wrapped
# --------------------------------------------------------------------------- #
def _copy_tree(src: str, dst: str) -> None:
    """``src`` into ``dst`` without bytecode or dot-directories (see _PACK_PRUNE_NAMES)."""
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in _PACK_PRUNE_NAMES and not d.startswith("."))
        rel = os.path.relpath(dirpath, src)
        here = dst if rel == os.curdir else os.path.join(dst, rel)
        os.makedirs(here, exist_ok=True)
        for name in sorted(filenames):
            if name.endswith(_PACK_SKIP_SUFFIXES):
                continue
            shutil.copy2(os.path.join(dirpath, name), os.path.join(here, name))


def pack_baseline(pack: str) -> dict[str, Any]:
    """``packs/<pack>/selftest/baseline.json`` as it parses, strictly: a baseline
    that is not a JSON object is a broken pack, not an empty design."""
    path = os.path.join(PACKS, pack, "selftest", "baseline.json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise AssertionError(f"{path} is a {type(data).__name__}, not a JSON object")
    return data


def design_of(baseline: dict[str, Any]) -> dict[str, Any]:
    """The baseline minus its ``_`` keys (``_description``, ``_notes``,
    ``_aliases``): what a wrapped model's ``build()`` returns."""
    return {k: v for k, v in baseline.items() if not str(k).startswith("_")}


def pack_gates(pack: str) -> list[Any]:
    """The bundled pack ``pack``'s GateSpecs, sorted by id.

    Loaded from the CHECKOUT's copy into a fresh ``Registry`` — never
    ``gates.REGISTRY`` — and resolved with neither ``$ATOMPIPE_PACK_PATH`` nor
    ``~/.atompipe/packs``: those belong to the machine, and a wrapped project's
    claims must name the gates of the pack it wraps, not of one a developer
    happens to have installed. Never from a project's ``copy_pack`` copy either:
    loading through the stock import system writes ``__pycache__`` beside the
    module, and a wrapped project that arrives with bytecode in it is a second
    run's project, not a fresh one (the control walk excludes bytecode, but a
    test that asks "after ``__pycache__`` exists" must be the one to create it).
    """
    registry = gates.Registry()
    packs.load_gates(pack, registry, root=_env.REPO, include_env=False, include_user=False)
    return sorted(registry.specs(), key=lambda spec: spec.id)


def _claim(n: int, spec: Any, pack: str) -> dict[str, Any]:
    """Claim ``G<n>``, critical and measurable, covered by ``spec`` through its tags.

    ``G``, not ``C``: openmodelica's baseline binds variables to claims C1 and
    C2 and prefers a ledger claim's limit when one of that id exists (packs:H20),
    so a wrapped project whose claims were C-numbered would judge the pack's
    design against the wrapper's limits instead of the pack's own.
    """
    return {
        "acceptance": {"comparator": "<=", "limit": None, "limit_hi": None,
                       "quantity": spec.settles or spec.title, "units": ""},
        "critical": True,
        "gates": [],
        "grounded_by": [],
        "id": f"G{n}",
        "kind": "measurable",
        "note": "",
        "physical_result": None,
        "rationale": f"{spec.id} must pass on {pack}'s own known-good design",
        "source": f"packs/{pack}/selftest/baseline.json",
        "statement": spec.title or spec.id,
        "tags": list(spec.claims),
    }


def _legacy_ledger(pack: str, specs: list[Any]) -> dict[str, Any]:
    return {
        "claims": [_claim(n, spec, pack) for n, spec in enumerate(specs, start=1)],
        "decisions": [],
        "inputs": [],
        "last_run": {"duration_s": 0.0, "inputs_hash": "", "model_hash": "",
                     "spine_version": "", "tier": 0, "when": ""},
        "meta": {"created": WRAP_CREATED, "model_entry": "model/wrap.py",
                 "name": f"wrapped-{pack}", "packs": [pack], "revision": "v0.1",
                 "spine_version": WRAP_SPINE_VERSION,
                 "summary": f"{pack}'s selftest baseline, wrapped as a project"},
        "needs": [],
        "params": [],
        "verdicts": [],
        "views": [],
    }


def wrap_pack_baseline(pack: str, dest: str, *, legacy: bool = True,
                       copy_pack: bool = False) -> str:
    """Make ``dest`` a project whose model is ``pack``'s baseline; return its path.

    * ``model/wrap.py`` — ``build()`` returns :func:`design_of` the baseline, as a
      literal in the file (so editing a design value is editing the model, as it
      is in a real project).
    * ``selftest/`` — the pack's, copied into the root, so a relative asset path
      in the baseline (``selftest/baseline_part.stl``,
      ``selftest/assets/results/…``) resolves against the project root as it
      resolves against the pack directory in pack mode.
    * ``meta.packs = [pack]``. With ``copy_pack`` the pack itself is copied to
      ``.atompipe/packs/<pack>`` too, which the search order puts ahead of the
      bundled one: a test may then edit the pack's code or fixtures without
      touching the checkout.
    * One critical measurable claim per gate, ``G1…Gn`` in gate-id order, with
      ``tags = spec.claims``, so each claim is covered by its gate (and by any
      other gate sharing a tag, exactly as a user's claim would be).
    * The **legacy** layout: ``.atompipe/ledger.json`` and ``.atompipe/.gitignore``
      (``LEGACY_GITIGNORE``), keys sorted, indent 2 — what the 1e09113 spine
      wrote, so that spine reads the project natively, this phase's spine
      migrates it, and the R-8 oracle compares the two on one fixture.

    ``legacy=False`` is refused. Before checkpoint 1.3 no spine read the records
    layout; since, a test that wants a wrapped project in it runs ``check`` on
    the legacy wrap and lets the spine migrate it (``test_records``'
    ``test_every_wrapped_pack_baseline_migrates``). A second writer of the
    records format here, beside ``store``'s, would be one more shape of record to
    keep in step — and the R-8 oracle needs the legacy bytes on both spines anyway.
    """
    if not legacy:
        raise NotImplementedError(
            "wrap_pack_baseline(legacy=False): a wrapped project is written legacy, "
            "the layout every spine reads; for the records layout, run `check` on "
            "it and let the spine under test migrate it")
    source = os.path.join(PACKS, pack)
    if not os.path.isfile(os.path.join(source, "pack.json")):
        raise AssertionError(f"no bundled pack {pack!r} at {source}")
    dest = os.path.abspath(dest)
    os.makedirs(dest, exist_ok=True)

    dot = os.path.join(dest, ".atompipe")
    os.makedirs(dot, exist_ok=True)
    if copy_pack:
        _copy_tree(source, os.path.join(dot, "packs", pack))
    _copy_tree(os.path.join(source, "selftest"), os.path.join(dest, "selftest"))

    design = design_of(pack_baseline(pack))
    os.makedirs(os.path.join(dest, "model"), exist_ok=True)
    with open(os.path.join(dest, "model", "wrap.py"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(_WRAP_MODEL.format(
            pack=pack, baseline=pprint.pformat(design, indent=1, width=88,
                                               sort_dicts=False)))

    ledger = _legacy_ledger(pack, pack_gates(pack))
    with open(os.path.join(dot, ".gitignore"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(LEGACY_GITIGNORE)
    with open(os.path.join(dot, "ledger.json"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(ledger, indent=2, sort_keys=True, ensure_ascii=False,
                            allow_nan=False) + "\n")
    return dest


# --------------------------------------------------------------------------- #
# a planted project: a model, gates, fixtures, claims and a known-good design
# --------------------------------------------------------------------------- #
#: The marker a planted project carries: the records layout's
#: ``.atompipe/project.json``, naming the model every planted project keeps at
#: ``model/m.py``. Spelled here as a literal, never written through ``store``
#: (the module docstring's rule: a fixture written by the spine under test moves
#: with it).
_PLANTED_MARKER = {"schema": 2, "name": "planted", "summary": "a planted project",
                   "created": WRAP_CREATED, "revision": "v0.1",
                   "model_entry": "model/m.py", "packs": [],
                   "spine_version": WRAP_SPINE_VERSION}


def plant_project(root: str, files: dict[str, str], *, claims: dict[str, dict],
                  known_good_params: dict[str, Any] | None = None,
                  known_good_claims: list[dict] | None = None) -> str:
    """Make ``root`` a project the commands run on; return its absolute path.

    ``files`` (project-relative path -> text) are its model (``model/m.py``),
    gates and fixtures; ``claims`` (id -> record, as a claim file holds it) are
    written one file each under ``claims/``; ``known_good_params`` and
    ``known_good_claims``, when given, are its ``selftest/known_good.py``
    (:func:`write_known_good`: the design stated as a literal, and the claim
    records its controls read their goalposts from — P2.4, a control never
    reads the live claim). P2.4's goalpost and operating-context tests plant
    their neutral projects here, so a CLI test and an in-process one
    (:class:`Planted`) read the same files.
    """
    base = os.path.abspath(root)
    os.makedirs(os.path.join(base, ".atompipe"), exist_ok=True)
    with open(os.path.join(base, ".atompipe", "project.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(json.dumps(_PLANTED_MARKER, indent=2) + "\n")
    for rel, text in files.items():
        target = os.path.join(base, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    write_claims(base, claims)
    if known_good_params is not None:
        write_known_good(base, known_good_params, claims=known_good_claims)
    return base


def write_claims(root: str, claims: dict[str, dict]) -> None:
    """``claims/<id>.json`` for each record of ``claims`` (id -> record), replaced."""
    for cid, record in claims.items():
        target = os.path.join(os.path.abspath(root), "claims", f"{cid}.json")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, indent=2, sort_keys=True) + "\n")


class Planted:
    """A planted project (:func:`plant_project`) loaded in THIS process: its
    gates in a fresh ``Registry`` (never ``gates.REGISTRY``), its model's
    projection, and a ledger of ``claims`` — the records, as ``Claim``\\s — that
    a test may edit between sweeps. One instance per project: a test that edits
    a gate's code builds a new project, since a module loaded here stays loaded."""

    def __init__(self, root: str, claims: dict[str, dict], *, now: str = WRAP_CREATED) -> None:
        from atompipe import modelio
        from atompipe.models import Claim, Ledger
        self.root = os.path.abspath(root)
        self.now = now
        self.registry = gates.Registry()
        gates.load_project_gates(self.root, self.registry)
        self.ledger = Ledger(claims=[Claim.from_dict({"id": cid, **record})
                                     for cid, record in claims.items()])
        self.projection = modelio.project(modelio.load_model(self.root, "model/m.py"))

    def ctx(self) -> Any:
        from atompipe import modelio, store
        flat, _conflicts = modelio.flat_params(self.projection)
        return gates.GateContext(root=self.root, ledger=self.ledger, model=None, params=flat,
                                 out_dir=store.out_dir(self.root), tier=0, extra={})

    def sweep(self, **kw: Any) -> Any:
        from atompipe import verdicts
        kw.setdefault("max_tier", 0)
        kw.setdefault("now", self.now)
        return verdicts.sweep(self.root, self.registry, self.ctx(),
                              projection=self.projection, ledger=self.ledger, **kw)

    def resolve(self) -> Any:
        from atompipe import verdicts
        return verdicts.resolve(self.root, self.registry, self.projection, self.ledger,
                                now=self.now)

    def composed(self, resolution: Any = None) -> dict:
        """``{claim id: Composed}`` as every reader composes it: the resolution's
        verdicts over the ledger, with the registry and its stale gates."""
        import dataclasses
        from atompipe import claims as claim_logic
        resolution = resolution if resolution is not None else self.resolve()
        view = dataclasses.replace(self.ledger, verdicts=list(resolution.verdicts))
        return claim_logic.compositions(view, registry=self.registry,
                                        stale_gates=resolution.stale_gates)

    def row(self, result: Any, gate_id: str) -> Any:
        found = [r for r in result.rows if r.verdict.gate == gate_id]
        assert len(found) == 1, (gate_id, [r.verdict.gate for r in result.rows])
        return found[0]
