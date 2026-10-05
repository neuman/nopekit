#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""R-8: Phase 1 moves where facts live, not what they are — measured against an older spine.

    python3 tests/oracle/r8_statuses.py --base <ref> [--toward-unresolved] [--self-test] [--keep]

**Phase 2's mode, ``--toward-unresolved``** (PLAN R-8: "P2 uses a weaker oracle:
statuses may move only in the blocking direction, and the phase commit lists every
changed claim with its reason"; PLAN-v0.14 §1.4: *blocking* is GLOSSARY's
*unresolved*). A claim's status may move only if its rank does not fall — 0
resolved (``pass``, ``verified``), 1 unresolved but not stopping ``check``
(``asserted``, ``unverified``), 2 stopping it — AND the new side's cause (``status
--json`` ``statuses[id].cause``) is one the claim holds on the new side: a covering
verdict that errored (``errored``), was refused (``unqualified``), skipped
(``skipped``) or failed (``failed``); a covering gate never run (``unrun``) or
invalidated (``invalidated``); an assumption's owner (``no-owner``,
``owner-unattributed``, ``no-reason``); from P2.4, a covering pass outside its
evaluator's operating context (``outside-context``, or ``fallback`` — rank 1 —
where an owned fallback carries it) and a covering pass whose value settles the
claim's quantity, in its units, and misses its acceptance condition
(``acceptance``, re-typed here: ``_misses``). Exit codes may go 0 -> 1, never 1 -> 0; a
BLOCKING list may only grow. Every move is printed with its cause — the list the
phase commit carries. The rule is written here AND in
``test_status_table.StatusesMoveOnlyTowardUnresolved``, never shared: an oracle that
imports the module under test relaxes with it (P2.1-D20, D-25's argument).

Phase 1 moves every verdict into a per-gate cache and every record into its own
file. None of that may change what a claim IS: a full sweep of the same project
must give the same claim statuses under the spine at ``<ref>`` and under this
checkout's (PLAN R-8). A refactor that moved a status by accident — a verdict
lost between two homes, a resolver that forgot a rung — would otherwise read as
"the new spine is stricter" or "the new spine is kinder", and nobody would know
which half of the change did it. Kleene composition waits for Phase 2 (D-01)
precisely so this comparison stays exact.

**The corpus** is built once, with ``tests/_projects.py``, and each spine sweeps
its own fresh copy of every project: the bracket at thickness 7.0 (C1 fails on
purpose) and at 8.0 (it passes), and each of the seven bundled pack baselines
wrapped as a **legacy** project — the layout the old spine reads natively and the
new one reads or migrates, so the same bytes go to both — save the bracket's
evaluators and controls, which each spine takes from its own tree, as it takes
its bundled packs (``BRACKET_CODE``, P2.4).

**Per project and per spine:** ``check --tier 3 --json``, then ``status --json``.
Compared:

* the sweep's claim statuses — the old spine's ``status --json`` claims, read
  right after its sweep, against the new spine's ``.atompipe/cache/last_check.json``
  ``statuses`` (the sweep's own record; a spine that keeps none is read through
  ``status``, as the old one is);
* the ``check`` exit codes;
* the BLOCKING lists, claim by claim with their statuses.

A difference fails the oracle unless it is one of the two enumerated in advance
(R-8), every one of which is expected to be absent from the bundled corpus: a
covering gate that returned a non-bool pass value (refused by the new spine,
counted by the old), and a covering gate that fails admission. An exit-code or
BLOCKING difference is permitted only when every claim difference behind it is.

**``status``-level differences** are reported too — what a human would see if
they typed ``status`` after the sweep — and each must be a claim the new spine
reads STALE while every stale gate reads ``opaque inputs``: an entry whose gate
read through a channel the tracer cannot see (omc's subprocess) is never Fresh,
by design (spec §3.4), so it reads stale between sweeps. Anything else fails.

**Its own negative control** (``--self-test``): an oracle that cannot fail is a
logger. It runs the corpus twice on the OLD spine, which must compare identical
— status level included — and then compares the old spine on the bracket at 7.0
with the NEW spine on a copy planted at 8.0, which flips C1 and must be
reported. With ``--toward-unresolved`` a third half runs the pair REVERSED on the
bracket at 7.0 — the new spine as the base, the old as the change — so every move
the new spine made toward unresolved is now one toward resolved, and must be
reported. ``--self-test`` exits 0 only when every half holds.

Exit codes: 0 — no difference outside the permitted set (with ``--self-test``:
both halves held); 1 — a difference (each named), or a self-test half that did
not hold; 2 — nothing to compare: ``<ref>`` is not a commit here, a worktree
could not be made, or a spine crashed (exit 2, or JSON that does not parse). 2 is
never 0: a comparison that did not happen must not read as "identical".

Every subprocess goes through ``tests/_env.run`` (``test_meta`` holds all of
``tests/`` to that): children get a temp HOME, no user packs, no agent
variables, and ``PYTHONPATH`` with the spine under test first. ``<ref>`` is
materialised with ``git worktree add --detach`` into a temp directory and removed
afterwards. Standard library only. It lives under ``tests/oracle/`` with no
``test`` prefix, so unittest discovery never collects it: it needs history (a
``verify.sh --dir`` copy has none) and a minute of solver time.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from typing import Any, NamedTuple

SAME, DIFFERS, UNUSABLE = 0, 1, 2

_HERE = os.path.dirname(os.path.abspath(__file__))
_TESTS = os.path.dirname(_HERE)


def _load_env() -> Any:
    """``tests/_env.py`` by path, registered as ``_env``: ``_projects`` imports it
    by name, and two copies of it would be two ideas of what a child's
    environment is."""
    spec = importlib.util.spec_from_file_location("_env", os.path.join(_TESTS, "_env.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["_env"] = module
    spec.loader.exec_module(module)
    return module


_env = _load_env()
for _path in (_TESTS, _env.SRC):          # _projects, test_fresh_clone; the new spine
    if _path not in sys.path:
        sys.path.insert(0, _path)
import _projects  # noqa: E402

#: The two differences R-8 enumerates in advance, as the new spine words them on
#: a covering gate's row. None is expected on the bundled corpus; a permitted
#: difference is still printed, with its reason.
PERMITTED = (
    ("fails admission", re.compile(r"^not admitted: ")),
    ("returned a non-bool pass value",
     re.compile(r"^gate reported passed=.* a verdict must say True or False")),
)

#: The prefix of the one staleness reason permitted right after a full sweep.
OPAQUE = "opaque inputs"

#: The thickness the self-test plants on the new side: the model docstring's own
#: fix (0.47 mm against 0.5), so C1 flips from FAIL to PASS and nothing else about
#: the bracket's claims needs to move.
PLANTED_THICKNESS = 8.0


class Unusable(Exception):
    """Nothing to compare (exit 2)."""


class Side(NamedTuple):
    """One spine's sweep of one project."""
    exit: int
    sweep: dict          # claim id -> status, as the sweep recorded it
    sweep_source: str    # "last_check.json" or "status --json"
    blocking: dict       # claim id -> status
    status: dict         # claim id -> status, from `status --json`
    stale: dict          # gate id -> reasons, for every gate `status` reads stale
    rows: dict           # gate id -> check --json row
    seconds: float
    causes: dict = {}    # claim id -> cause, from `status --json` `statuses` (P2.1 on)
    never: tuple = ()    # gates `status --json` reads never run, with no refusal
    acceptances: dict = {}  # claim id -> its acceptance record, as swept (P2.4)


class Difference(NamedTuple):
    where: str           # "status", "exit", "blocking", "status-level"
    what: str
    permitted: str       # "" when not permitted


# --------------------------------------------------------------------------- #
# the spines
# --------------------------------------------------------------------------- #
def materialise(ref: str, work: str) -> tuple[str, str]:
    """``(sha, tree)``: ``<ref>`` checked out, detached, in a temp worktree."""
    proc = _env.git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=_env.REPO)
    if proc.returncode != 0:
        raise Unusable(f"--base {ref}: not a commit in {_env.REPO}")
    sha = proc.stdout.strip()
    tree = os.path.join(work, "base")
    proc = _env.git(["worktree", "add", "--detach", tree, sha], cwd=_env.REPO)
    if proc.returncode != 0:
        raise Unusable(f"git worktree add {sha}: {proc.stderr.strip()}")
    if not os.path.isfile(os.path.join(tree, "src", "atompipe", "__init__.py")):
        unmaterialise(tree)
        raise Unusable(f"{ref} has no src/atompipe: not an atompipe spine")
    return sha, tree


def unmaterialise(tree: str) -> None:
    _env.git(["worktree", "remove", "--force", tree], cwd=_env.REPO)
    _env.git(["worktree", "prune"], cwd=_env.REPO)


def _pythonpath(spine_src: str) -> str:
    """``spine_src`` first, then the parent's entries minus this checkout's src —
    so the old spine is never shadowed by the new one, and a probe's
    ``sitecustomize`` on the parent's path still reaches the child."""
    mine = os.path.normcase(os.path.normpath(_env.SRC))
    rest = [e for e in _env._pythonpath().split(os.pathsep)
            if e and os.path.normcase(os.path.normpath(e)) != mine]
    return os.pathsep.join([spine_src, *rest])


def _json(proc: Any, what: str) -> dict:
    if proc.returncode not in (0, 1):
        raise Unusable(f"{what} exited {proc.returncode}:\n{proc.stdout[-2000:]}"
                       f"\n{proc.stderr[-2000:]}")
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise Unusable(f"{what}: stdout is not one JSON document ({exc}):\n"
                       f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    if not isinstance(data, dict):
        raise Unusable(f"{what}: stdout is a {type(data).__name__}, not an object")
    return data


#: The bracket's directories that call the spine's API — its evaluators and
#: their controls — taken from the swept spine's own tree (P2.4). What slipped
#: through: P2.4's bracket reads its goalpost (`ctx.acceptance`) and names each
#: comparator (`Verdict(comparator=…)`), so the old spine, handed this
#: checkout's bracket, errored on every evaluator — C2, C3, C4 "blocked ->
#: pass", all of it the old spine crashing on code it never had. A bundled pack
#: already travels with its spine; the bracket's code now does too. The design
#: (`model/`) and the records stay the same bytes on both sides.
#: *Rejected:* the base tree's whole bracket (its claims would differ from the
#: new side's, and R-8 compares one project's statuses); skipping the bracket
#: (the corpus's one project with a failing claim).
BRACKET_CODE = ("gates", "selftest")


def _own_bracket_code(spine_src: str, dest: str) -> None:
    """Overlay ``dest``'s bracket code with the files of the spine's own tree."""
    code = os.path.join(os.path.dirname(os.path.abspath(spine_src)), "examples", "bracket")
    if not (os.path.isfile(os.path.join(dest, "gates", "structural.py"))
            and os.path.isfile(os.path.join(code, "gates", "structural.py"))):
        return
    for part in BRACKET_CODE:
        theirs = os.path.join(code, part)
        for here, _dirs, files in os.walk(theirs):
            if "__pycache__" in here.split(os.sep):
                continue
            for name in files:
                if name.endswith(".pyc"):
                    continue
                src = os.path.join(here, name)
                target = os.path.join(dest, part, os.path.relpath(src, theirs))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(src, target)


def sweep(spine_src: str, pristine: str, dest: str, *, thickness: float | None = None) -> Side:
    """A fresh copy of ``pristine`` at ``dest``, swept by the spine at ``spine_src``
    — with the bracket's code from that spine's own tree (``BRACKET_CODE``)."""
    shutil.copytree(pristine, dest, symlinks=True)
    _own_bracket_code(spine_src, dest)
    if thickness is not None:
        _projects.set_thickness(dest, thickness)
    env = {"PYTHONPATH": _pythonpath(spine_src)}
    started = time.monotonic()
    check = _env.run([sys.executable, "-m", "atompipe", "check", "--tier", "3", "--json"],
                     cwd=dest, env=env)
    seconds = time.monotonic() - started
    checked = _json(check, f"check in {dest} ({spine_src})")
    status = _json(_env.run([sys.executable, "-m", "atompipe", "status", "--json"],
                            cwd=dest, env=env), f"status in {dest} ({spine_src})")
    last = os.path.join(dest, ".atompipe", "cache", "last_check.json")
    if os.path.isfile(last):
        with open(last, encoding="utf-8") as fh:
            swept, source = dict(json.load(fh)["statuses"]), "last_check.json"
    else:
        swept, source = dict(status["claims"]), "status --json"
    stale = {gate: list(state.get("reasons") or ())
             for gate, state in (status.get("freshness") or {}).items()
             if gate in set(status.get("stale_gates") or ())}
    causes = {cid: str((view or {}).get("cause", ""))
              for cid, view in (status.get("statuses") or {}).items()}
    never = tuple(sorted(gate for gate, state in (status.get("freshness") or {}).items()
                         if state.get("state") == "never"
                         and state.get("admission") != "not-admitted"))
    return Side(exit=check.returncode, sweep=swept, sweep_source=source,
                blocking={b["claim"]: b["status"] for b in checked["blocking"]},
                status=dict(status["claims"]), stale=stale,
                rows={row["gate"]: row for row in checked["verdicts"]}, seconds=seconds,
                causes=causes, never=never, acceptances=claim_acceptances(dest))


# --------------------------------------------------------------------------- #
# the corpus
# --------------------------------------------------------------------------- #
def build_corpus(root: str) -> dict[str, str]:
    """``{name: pristine project}``, each built once; every sweep copies it."""
    corpus: dict[str, str] = {}
    for thickness in (7.0, 8.0):
        name = f"bracket@{thickness}"
        corpus[name] = _projects.bracket_copy(os.path.join(root, name), thickness=thickness)
    for pack in sorted(os.listdir(_projects.PACKS)):
        if os.path.isfile(os.path.join(_projects.PACKS, pack, "pack.json")):
            corpus[pack] = _projects.wrap_pack_baseline(pack, os.path.join(root, pack))
    return corpus


def claim_kinds(project: str) -> dict[str, str]:
    """``{claim id: kind}``, read as ``claim_tags`` reads the records."""
    ledger = os.path.join(project, ".atompipe", "ledger.json")
    rows: list = []
    if os.path.isfile(ledger):
        with open(ledger, encoding="utf-8") as fh:
            rows = list(json.load(fh).get("claims") or ())
    else:
        directory = os.path.join(project, "claims")
        for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else ():
            if name.endswith(".json"):
                with open(os.path.join(directory, name), encoding="utf-8") as fh:
                    rows.append({"id": name[:-5], **json.load(fh)})
    return {str(row["id"]): str(row.get("kind") or "measurable") for row in rows}


def claim_acceptances(project: str) -> dict[str, dict]:
    """``{claim id: acceptance record}`` for every claim that carries one, read
    from the records as ``claim_kinds`` reads them (P2.4: the goalpost a
    covering pass is compared with, ``_misses``)."""
    ledger = os.path.join(project, ".atompipe", "ledger.json")
    rows: list = []
    if os.path.isfile(ledger):
        with open(ledger, encoding="utf-8") as fh:
            rows = list(json.load(fh).get("claims") or ())
    else:
        directory = os.path.join(project, "claims")
        for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else ():
            if name.endswith(".json"):
                with open(os.path.join(directory, name), encoding="utf-8") as fh:
                    rows.append({"id": name[:-5], **json.load(fh)})
    return {str(row["id"]): dict(row["acceptance"]) for row in rows
            if isinstance(row.get("acceptance"), dict)}


def claim_tags(project: str) -> dict[str, set]:
    """``{claim id: {id and tags}}`` — what a gate's ``claims`` list binds to
    (``claims.covers``: the id, or any tag) — read from the project's legacy
    ledger or, in the records layout, its ``claims/*.json``."""
    ledger = os.path.join(project, ".atompipe", "ledger.json")
    rows: list = []
    if os.path.isfile(ledger):
        with open(ledger, encoding="utf-8") as fh:
            rows = list(json.load(fh).get("claims") or ())
    else:
        directory = os.path.join(project, "claims")
        for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else ():
            if name.endswith(".json"):
                with open(os.path.join(directory, name), encoding="utf-8") as fh:
                    rows.append({"id": name[:-5], **json.load(fh)})
    return {str(row["id"]): {str(row["id"]), *map(str, row.get("tags") or ())} for row in rows}


# --------------------------------------------------------------------------- #
# the comparison
# --------------------------------------------------------------------------- #
def _permitted(claim: str, binds: set, new: Side) -> str:
    """Why a difference on ``claim`` is one R-8 permits, or ``""``."""
    for gate, row in sorted(new.rows.items()):
        if not binds & set(row.get("claims") or ()):
            continue
        for reason, pattern in PERMITTED:
            if pattern.search(str(row.get("error") or "")):
                return f"{gate} {reason}"
    return ""


def _outcome(row: dict) -> str:
    """A ``check --json`` row's outcome. The new spine writes ``outcome``; an
    older one only the flags, read in ``Verdict.outcome``'s order."""
    if row.get("outcome"):
        return str(row["outcome"])
    if row.get("error"):
        return "error"
    if row.get("skipped"):
        return "skipped"
    return "pass" if row.get("passed") is True else "fail"


def _covering(binds: set, side: Side) -> str:
    return ", ".join(f"{gate} {_outcome(row)}" for gate, row in sorted(side.rows.items())
                     if binds & set(row.get("claims") or ())) or "no covering gate"


#: GLOSSARY §3's rank of resolved: 0 Checked, 1 unresolved but not stopping
#: `check` (Pending build, Assumed), 2 stopping it. Typed here, never imported.
RANK = {"pass": 0, "verified": 0, "asserted": 1, "unverified": 1}

#: The causes an assumption may move to (an owner nobody recorded, P2.1-D8).
OWNER_CAUSES = ("no-owner", "owner-unattributed", "no-reason")

#: GLOSSARY's comparators, typed here (never `models.Comparator`: an oracle that
#: imports the module under test relaxes with it, P2.1-D20). P2.4.
COMPARE = {"<=": lambda v, lo, hi: v <= lo, "<": lambda v, lo, hi: v < lo,
           ">=": lambda v, lo, hi: v >= lo, ">": lambda v, lo, hi: v > lo,
           "==": lambda v, lo, hi: v == lo, "!=": lambda v, lo, hi: v != lo,
           "between": lambda v, lo, hi: lo <= v <= (lo if hi is None else hi)}


def _real(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _words(text: Any) -> str:
    """A quantity as P2.4 matches one: casefolded, `-` and `_` as spaces."""
    return " ".join(str(text or "").casefold().replace("-", " ").replace("_", " ").split())


def _misses(row: dict, acceptance: dict) -> bool:
    """A covering pass whose value is evidence about the claim's goalpost —
    it ``settles`` the claim's quantity, in its units — and does not meet it:
    the fact a move to ``acceptance`` (Failing) must hold (P2.4-D9)."""
    quantity = _words(acceptance.get("quantity"))
    limit = acceptance.get("limit")
    compare = COMPARE.get(str(acceptance.get("comparator") or "<="))
    if not quantity or _words(row.get("settles")) != quantity or compare is None:
        return False
    if str(row.get("units") or "").strip() != str(acceptance.get("units") or "").strip():
        return False
    if not (_real(limit) and _real(row.get("measured"))):
        return False
    return not compare(float(row["measured"]), float(limit), acceptance.get("limit_hi"))


def _held(claim: str, binds: set, kind: str, side: Side, *, stale: bool) -> set[str]:
    """The causes ``claim`` holds on ``side``: what its covering verdicts did, a
    covering gate never run, an invalidated one (status level), an assumption."""
    held: set[str] = set()
    for gate, row in side.rows.items():
        if not binds & set(row.get("claims") or ()):
            continue
        outcome = _outcome(row)
        if outcome == "error":
            held.add("unqualified" if row.get("unqualified") else "errored")
        elif outcome == "skipped":
            held.add("skipped")
        elif outcome == "fail":
            held.add("failed")
        elif outcome == "outside-context":
            # P2.4: a pass outside its evaluator's operating context — Gap, or
            # Assumed where an owned fallback carries it (rank 1, never 0).
            held.update(("outside-context", "fallback"))
        elif outcome == "pass" and _misses(row, side.acceptances.get(claim) or {}):
            held.add("acceptance")
        if stale and gate in side.stale:
            held.add("invalidated")
        if gate in side.never:
            held.add("unrun")
    if kind == "assumption":
        held.update(OWNER_CAUSES)
    if kind == "physical":
        # P2.1's review: a typed pass no article binds reads Pending build,
        # `physical-pass` (a move from verified, toward unresolved).
        held.update(("physical-fail", "no-article", "physical-pass"))
    return held


def _toward(claim: str, a: Any, b: Any, binds: set, kind: str, new: Side, *,
            stale: bool = False) -> str:
    """Why ``claim``'s move ``a -> b`` is one P2's oracle permits, or ``""``."""
    if a is None or b is None:
        return ""
    if RANK.get(str(b), 2) < RANK.get(str(a), 2):
        return ""
    cause = new.causes.get(claim, "")
    if cause and cause in _held(claim, binds, kind, new, stale=stale):
        return f"toward unresolved ({cause})"
    return ""


def compare(old: Side, new: Side, tags: dict[str, set], *, toward: bool = False,
            kinds: dict[str, str] | None = None) -> list[Difference]:
    out: list[Difference] = []
    kinds = kinds or {}
    claims = sorted(set(old.sweep) | set(new.sweep) | set(tags))
    permitted_claims = []
    for claim in claims:
        a, b = old.sweep.get(claim), new.sweep.get(claim)
        if a != b:
            binds = tags.get(claim, {claim})
            why = (_toward(claim, a, b, binds, kinds.get(claim, "measurable"), new)
                   if toward else _permitted(claim, binds, new))
            out.append(Difference("status", f"{claim}: {a} -> {b}   (old: {_covering(binds, old)}; "
                                            f"new: {_covering(binds, new)})", why))
            permitted_claims.append(bool(why))
    behind = bool(permitted_claims) and all(permitted_claims)
    if old.exit != new.exit:
        stricter = old.exit == 0 and new.exit == 1
        out.append(Difference("exit", f"check exit {old.exit} -> {new.exit}",
                              "every claim difference behind it is permitted"
                              if behind and (stricter or not toward) else ""))
    if old.blocking != new.blocking:
        grows = set(old.blocking) <= set(new.blocking)
        out.append(Difference("blocking", f"{sorted(old.blocking.items())} -> "
                                          f"{sorted(new.blocking.items())}",
                              "every claim difference behind it is permitted"
                              if behind and (grows or not toward) else ""))
    # Right after a full sweep the only gates allowed to read stale are the
    # opaque ones; one stale for any other reason makes every status-level
    # difference a failure, whichever claim it is on.
    opaque_only = bool(new.stale) and all(
        reasons and all(r.startswith(OPAQUE) for r in reasons) for reasons in new.stale.values())
    for claim in claims:
        a, b = old.status.get(claim), new.status.get(claim)
        if a != b:
            gates_ = ", ".join(f"{g} ({'; '.join(r)})" for g, r in sorted(new.stale.items()))
            why = f"{OPAQUE} STALE" if b == "stale" and opaque_only else ""
            if not why and toward:
                why = _toward(claim, a, b, tags.get(claim, {claim}),
                              kinds.get(claim, "measurable"), new, stale=True)
            out.append(Difference("status-level", f"{claim}: {a} -> {b}   "
                                                   f"(stale: {gates_ or 'none'})", why))
    return out


def run_pair(label: str, old_src: str, new_src: str, pristine: str, work: str, *,
             new_thickness: float | None = None, toward: bool = False
             ) -> tuple[list[Difference], Side, Side]:
    old = sweep(old_src, pristine, os.path.join(work, label, "old"))
    new = sweep(new_src, pristine, os.path.join(work, label, "new"), thickness=new_thickness)
    return compare(old, new, claim_tags(pristine), toward=toward,
                   kinds=claim_kinds(pristine)), old, new


def report(name: str, diffs: list[Difference], old: Side, new: Side) -> None:
    """Print one project's line, then each difference with its verdict."""
    head = (f"  {name:<18} exit {old.exit}/{new.exit}  claims {len(new.sweep)}  "
            f"({old.seconds:.1f}s / {new.seconds:.1f}s; right side's sweep read from "
            f"{new.sweep_source})")
    print(head + ("  same" if not diffs else ""))
    for d in diffs:
        tag = f"permitted: {d.permitted}" if d.permitted else "DIFFERS"
        print(f"      {d.where:<12} {d.what}   [{tag}]")


class Tally(NamedTuple):
    """Differences over the corpus: the sweep's (statuses, exit codes, BLOCKING)
    and ``status``'s, each split into failing and permitted."""
    failing: int
    permitted: int
    status_failing: int
    status_permitted: int

    def summary(self) -> str:
        return (f"{self.failing} failing and {self.permitted} permitted sweep "
                f"difference(s); {self.status_failing} failing and {self.status_permitted} "
                f"permitted status-level difference(s)")


def run_corpus(old_src: str, new_src: str, corpus: dict[str, str], work: str, *,
               toward: bool = False) -> Tally:
    counts = [0, 0, 0, 0]
    for name, pristine in corpus.items():
        diffs, old, new = run_pair(name, old_src, new_src, pristine, work, toward=toward)
        report(name, diffs, old, new)
        for d in diffs:
            counts[(2 if d.where == "status-level" else 0) + (1 if d.permitted else 0)] += 1
    return Tally(*counts)


def self_test(old_src: str, new_src: str, corpus: dict[str, str], work: str, *,
              toward: bool = False) -> int:
    print("self-test 1/2: the old spine against itself must compare identical")
    tally = run_corpus(old_src, old_src, corpus, os.path.join(work, "old-old"))
    first = not any(tally)
    print(f"  -> {'held' if first else 'DID NOT HOLD'}: {tally.summary()}; every one must be 0")

    print(f"self-test 2/2: a planted difference must be reported "
          f"(new side: bracket@7.0 edited to thickness {PLANTED_THICKNESS})")
    diffs, old, new = run_pair("planted", old_src, new_src, corpus["bracket@7.0"],
                               os.path.join(work, "planted"), new_thickness=PLANTED_THICKNESS,
                               toward=toward)
    report("bracket@7.0*", diffs, old, new)
    flipped = [d for d in diffs if d.where == "status" and d.what.startswith("C1:")
               and not d.permitted]
    second = bool(flipped)
    print(f"  -> {'held' if second else 'DID NOT HOLD'}: C1 "
          f"{'reported' if second else 'NOT reported'} as a failing difference")
    third = True
    if toward:
        print("self-test 3/3: the pair reversed — the new spine as the base — must report "
              "every move it made toward unresolved as one toward resolved")
        diffs, old, new = run_pair("reversed", new_src, old_src, corpus["bracket@7.0"],
                                   os.path.join(work, "reversed"), toward=True)
        report("bracket@7.0~", diffs, old, new)
        backward = [d for d in diffs if d.where == "status" and not d.permitted]
        third = bool(backward)
        print(f"  -> {'held' if third else 'DID NOT HOLD'}: {len(backward)} move(s) toward "
              f"resolved reported as failing")
    ok = first and second and third
    print(f"R-8 SELF-TEST: {'PASSED' if ok else 'FAILED'}")
    return SAME if ok else DIFFERS


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True, help="the older spine: a commit-ish in this repository")
    ap.add_argument("--self-test", action="store_true",
                    help="run the oracle's own negative control instead of the comparison")
    ap.add_argument("--toward-unresolved", action="store_true",
                    help="Phase 2's oracle: statuses may move only toward GLOSSARY's "
                         "unresolved, each for a cause the claim holds")
    ap.add_argument("--keep", action="store_true", help="keep the temp directory, and say where")
    args = ap.parse_args(argv)

    work = tempfile.mkdtemp(prefix="atompipe-r8-")
    tree = ""
    try:
        sha, tree = materialise(args.base, work)
        old_src = os.path.join(tree, "src")
        print(f"R-8 oracle: base {args.base} ({sha[:12]}) vs this checkout ({_env.SRC})")
        corpus = build_corpus(os.path.join(work, "corpus"))
        print(f"corpus: {', '.join(corpus)} ({len(corpus)} projects); check --tier 3, then status")
        toward = bool(args.toward_unresolved)
        if args.self_test:
            return self_test(old_src, _env.SRC, corpus, work, toward=toward)
        tally = run_corpus(old_src, _env.SRC, corpus, os.path.join(work, "old-new"),
                           toward=toward)
        failing = tally.failing + tally.status_failing
        print(f"R-8: {'IDENTICAL' if not failing else 'DIFFERS'} — {len(corpus)} projects; "
              f"{tally.summary()}")
        return SAME if not failing else DIFFERS
    except Unusable as exc:
        print(f"R-8: NOTHING TO COMPARE — {exc}", file=sys.stderr)
        return UNUSABLE
    finally:
        if tree:
            unmaterialise(tree)
        if args.keep:
            print(f"(kept {work})", file=sys.stderr)
        else:
            _env._rmtree(work)


if __name__ == "__main__":
    sys.exit(main())
