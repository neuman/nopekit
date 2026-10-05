# SPDX-License-Identifier: Apache-2.0
"""Known-bad fixtures for openmodelica. One physical change each.

`nopekit gate selftest` requires every gate to FAIL here. A gate that passes its
own fixture is reported as broken, and a green verdict from it means nothing.

---------------------------------------------------------------------------
SEALED FIXTURES
---------------------------------------------------------------------------
A control must fire in EVERY project, not just a friendly one. Layering the
known-bad values over the host project's projection looks safe — the override
wins on every key it states — but gates resolve synonym families and derived
quantities, so a key the fixture never mentions can still arrive from the
project and neutralise the control.

It was observed live in a sibling pack: a fixture raised a hull's centre of
gravity to make it unstable, the host project happened to state a waterplane
inertia (an honest thing to state, better than the pack's own fallback), and the
gate PASSED ITS OWN KNOWN-BAD FIXTURE. A control whose severity depends on the
host project's numbers passes in some repositories and fails in others, which is
the same as having none.

So the base here is the pack's OWN ``selftest/baseline.json`` and nothing is
inherited from ``ctx.params``. ``ctx.ledger`` is replaced with an empty one for
the same reason: ``modelica.claims_addressable`` and ``modelica.result_claim``
both read claims out of it, and a host project's claim list would otherwise
change what the control is measuring.

**Paths are made absolute against THIS directory.** A fixture that handed a gate
``selftest/assets/model`` would resolve it against the host project's root, find
nothing, and the gate would SKIP — and a skipped control is not a control. This
is the file-based equivalent of the inheritance hole above and it has exactly the
same symptom: green in the pack's own CI, silent in every real repository.

---------------------------------------------------------------------------
AND SEALED IS NOT THE SAME AS HARD-CODED
---------------------------------------------------------------------------
Sealing fixes WHICH model the control is computed against. It says nothing about
whether the perturbation is BIG ENOUGH to cross the threshold it is tested
against. A fixture that writes a literal (``+20 K``, ``x1.5``) is only guaranteed
to cross the limits this baseline happens to ship with; lift it into a project
with looser limits and the control quietly stops falsifying anything while still
reporting that it did.

So every numeric fixture below computes its perturbation FROM THE LIMIT OR
TOLERANCE THE GATE WILL JUDGE IT BY, using the pack's own reduction and
interpolation functions so a fixture cannot be severe by an arithmetic the gate
does not share.

``MARGIN`` is how far past the limit each fixture must land.

---------------------------------------------------------------------------
WHAT IS GENERATED AND WHAT IS SHIPPED
---------------------------------------------------------------------------
The three tier-2 fixtures are SHIPPED .mo files under ``assets/bad/``, because
the thing they must be bad in the eyes of is a compiler, and a compiler's opinion
of a generated file is not reproducible by a reader. They are real Modelica and
omc genuinely rejects all three — which is why those controls will fire wherever
omc is installed even though they skip on a machine without it.

The five tier-0 fixtures are GENERATED from the pack's own assets at fixture
time, because they must be derived from the limits in ``baseline.json`` (see
above) and a checked-in copy would silently stop matching the moment a limit
moved. They are written under the CALLER's ``out_dir``
(``<out_dir>/openmodelica/known-bad/``), and stay there after a run to be opened
and diffed against the originals.

Never into this directory. What slipped through: they used to go to
``selftest/.generated/`` whatever ``out_dir`` the caller gave, falling back to one
fixed ``$TMPDIR/nopekit-openmodelica`` shared by every user of the machine — so
a wheel install wrote into site-packages, the repository's own suite rewrote four
files inside the tree it was testing on every run, and two users' runs could
collide in one directory (packs:H16). Rejected: keeping the in-pack directory
because a reader can find it there — a fixture that writes into the pack it is
proving makes the proof depend on whether that directory is writable.
"""
from __future__ import annotations

import copy
import csv
import dataclasses
import json
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PACK_DIR = os.path.dirname(_HERE)
_GATES_DIR = os.path.join(_PACK_DIR, "gates")
if _GATES_DIR not in sys.path:
    sys.path.insert(0, _GATES_DIR)

import _modelica as M                                              # noqa: E402

from nopekit.models import Ledger                                 # noqa: E402

#: How far past its limit or tolerance each fixture must drive the quantity it
#: perturbs, as a fraction of that limit. 5%, and the 5 is doing real work: these
#: are ABSOLUTE-SCALE quantities (kelvin, absolute pressure, a time in seconds
#: from the start of a run), where the 15% a ratio-based pack can use is
#: physically enormous — 15% of a 345 K limit is 52 K, a fixture nobody would
#: believe. 5% is still four orders of magnitude outside any solver tolerance
#: (1e-6) and far outside CSV round-trip rounding, which is what a margin has to
#: beat. 0.1% was tried and rejected: the mirror comparison's own allowance is a
#: mixed absolute+relative band, and a fixture inside a thousandth of it is a
#: fixture whose firing depends on the last digit of a float.
MARGIN = 0.05

#: Absolute floor on that perturbation, for a limit of zero. Without it a claim
#: with ``limit = 0`` gets a perturbation of 0 and the control silently becomes a
#: no-op — the one arithmetic accident that turns a sealed, derived fixture back
#: into a decoration.
MARGIN_FLOOR = 1e-6

_BASELINE_PATH = os.path.join(_HERE, "baseline.json")

#: Keys in the baseline whose value is a path (or a list of paths) into the
#: pack's own assets. Listed explicitly rather than sniffed, so adding a key that
#: happens to look like a path cannot silently change what a control points at.
_PATH_KEYS = ("modelica_result_csv", "modelica_mirror_csv")
_PATH_LIST_KEYS = ("modelica_sources",)


def _baseline() -> dict:
    """The pack's plausible-good projection, documentation keys stripped and
    every asset path made absolute against this pack."""
    try:
        with open(_BASELINE_PATH, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return {}
    params = {k: v for k, v in loaded.items() if not k.startswith("_")}
    for key in _PATH_KEYS:
        if key in params:
            params[key] = os.path.join(_PACK_DIR, str(params[key]))
    for key in _PATH_LIST_KEYS:
        if key in params:
            params[key] = [os.path.join(_PACK_DIR, str(p)) for p in params[key]]
    return params


def _with(ctx, **overrides):
    """A ctx whose params are the BASELINE plus these overrides, and whose ledger
    is empty so no host claim can change what the control measures."""
    params = _baseline()
    params.update(overrides)
    return dataclasses.replace(ctx, params=params, ledger=Ledger())


#: Where under the caller's ``out_dir`` the generated known-bad files go. Named
#: for the pack, so a project that runs several packs' controls into one
#: ``out_dir`` cannot have two packs' fixtures collide.
_GENERATED = ("openmodelica", "known-bad")


def _generated(ctx, *parts: str) -> str:
    """A path for a generated known-bad file, under ``ctx.out_dir``; its directory
    is created.

    Through ``ctx.out_path``, the spine's one answer to "where does this run
    write". See the module docstring for why it is never this pack's directory.
    """
    return ctx.out_path(*_GENERATED, *parts)


def _read_rows(path: str) -> tuple[list[str], list[list[str]]]:
    with open(path, "r", encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.reader(handle) if row and any(c.strip() for c in row)]
    if not rows:
        raise OSError(f"{path} has no rows")
    return [c.strip().strip('"') for c in rows[0]], rows[1:]


def _write_rows(path: str, header: list[str], rows: list[list[str]]) -> str:
    """Write an OMC-shaped result table: quoted header, bare numeric rows.

    Written by hand rather than with ``csv.writer`` because the header must come
    out exactly as omc writes it — ``"time","tank.T"`` — and a writer set to
    QUOTE_NONE escapes the quote characters the caller supplied, while one set to
    QUOTE_ALL quotes the numbers. Neither is the format the gates read.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(",".join(f'"{name}"' for name in header) + "\n")
        for row in rows:
            handle.write(",".join(str(cell) for cell in row) + "\n")
    return path


def _first_binding(params: dict) -> dict | None:
    """The first claim binding that carries a comparator and a limit of its own."""
    entries = params.get("modelica_claim_variables") or []
    if isinstance(entries, dict):
        entries = [{"claim": k, **v} if isinstance(v, dict) else {"claim": k, "variable": v}
                   for k, v in entries.items()]
    for entry in entries:
        if isinstance(entry, dict) and entry.get("variable") and entry.get("limit") is not None:
            return entry
    return None


# --------------------------------------------------------------------------- #
# tier 0
# --------------------------------------------------------------------------- #
def stripped_declarations(ctx):
    """modelica.source_hygiene — the parameters lost their descriptions and units.

    Copies the pack's own .mo sources and, using the SCANNER's own character
    spans rather than a regex, does exactly two things to every ``parameter``
    declaration: deletes its description string, and renames its ``unit``
    modification to ``displayUnit``. The units on the package's local SI type
    aliases go the same way, so a declaration cannot inherit one either.

    ``displayUnit`` rather than deletion is the point of the fixture. It is still
    legal Modelica, it still simulates, it still produces identical numbers — and
    it carries no unit, so nothing in the toolchain will ever object when someone
    adds a temperature to a length. That is the real shape of this defect: not a
    model that breaks, a model that quietly stops being checkable.

    Values, equations, structure, file names and line count are untouched.
    """
    base = _baseline()
    sources = [str(p) for p in (base.get("modelica_sources") or [])]
    files = M.gather_mo_files(sources)
    if not files:
        return _with(ctx)

    # Emptied first: the gate walks this directory, and a .mo file an earlier run
    # left there (a source since renamed) would be read as part of the model.
    out_dir = _generated(ctx, "stripped")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    written: list[str] = []
    for path in files:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        model = M.scan_text(text, path)
        edits: list[tuple[int, int, str]] = []
        for component in model.parameters:
            if component.description_span:
                edits.append((*component.description_span, ""))
            if component.unit_span:
                edits.append((*component.unit_span, "displayUnit"))
        for _name, (alias_file, span) in model.alias_unit_spans.items():
            if alias_file == path:
                edits.append((*span, "displayUnit"))
        # Right to left, so an earlier edit cannot move a later span.
        seen: set[tuple[int, int]] = set()
        for start, end, replacement in sorted(set(edits), key=lambda e: -e[0]):
            if (start, end) in seen:
                continue
            seen.add((start, end))
            text = text[:start] + replacement + text[end:]
        target = os.path.join(out_dir, os.path.basename(path))
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)
        written.append(target)
    return _with(ctx, modelica_sources=[out_dir])


def renamed_variable(ctx):
    """modelica.claims_addressable — a claim still names the old variable.

    Suffixes ``_v2`` onto the first bound variable, which is what the claim would
    still say three revisions after somebody renamed it in the model. The model,
    the result file and every other binding are bit-identical; the claim is
    simply pointing at a name that no longer exists, and nothing in a normal run
    would ever say so — the extraction gate skips for a missing column, the skip
    resolves the claim to BLOCKED, and BLOCKED reads as 'waiting on tooling'.
    """
    base = _baseline()
    entries = copy.deepcopy(base.get("modelica_claim_variables") or [])
    if isinstance(entries, dict):
        first = next(iter(entries), None)
        if first is None:
            return _with(ctx)
        value = entries[first]
        if isinstance(value, dict):
            value["variable"] = f"{value.get('variable', 'x')}_v2"
        else:
            entries[first] = f"{value}_v2"
    else:
        if not entries:
            return _with(ctx)
        entry = entries[0]
        if isinstance(entry, dict):
            entry["variable"] = f"{entry.get('variable', 'x')}_v2"
        else:
            entries[0] = f"{entry}_v2"
    return _with(ctx, modelica_claim_variables=entries)


def run_truncated(ctx):
    """modelica.solution_valid — the run stopped at half its stopTime.

    Every sample past the midpoint of the declared start..stop span is dropped.
    One physically meaningful change: the run did not finish. Every number that
    remains is bit-identical to the good file, the header is identical, the time
    column is still monotonic and nothing is NaN — which is the whole problem.
    A final value read off this file is a real float produced by a real solver
    from a run that stopped mid-transient, and there is nothing in the number to
    say so.

    The cut is computed from the baseline's own start and stop times, so it lands
    at half of whatever run the project recorded.
    """
    base = _baseline()
    path = base.get("modelica_result_csv")
    if not path or not os.path.isfile(str(path)):
        return _with(ctx)
    start = float(base.get("modelica_start_time_s") or 0.0)
    stop = float(base.get("modelica_stop_time_s") or 0.0)
    cut = start + (stop - start) / 2.0

    header, rows = _read_rows(str(path))
    kept = []
    for row in rows:
        try:
            when = float(row[0])
        except (TypeError, ValueError, IndexError):
            continue
        if when <= cut + 1e-9:
            kept.append(row)
    if len(kept) < 2:
        kept = rows[:max(2, len(rows) // 2)]
    target = _generated(ctx, "truncated_res.csv")
    _write_rows(target, header, kept)
    return _with(ctx, modelica_result_csv=target)


def nan_injected(ctx):
    """modelica.solution_valid — one cell of the run is NaN.

    The pack's SECOND fixture for this gate, shipped and exercised even though
    only one fixture can be declared on a gate. It is the other half of the
    validity guard's job: ``run_truncated`` is a run that stopped, this is a run
    that diverged, and the two have nothing in common except that any number read
    off either is worthless.

    A single cell, in the middle row of the first bound variable's column, set to
    ``nan``. The run still reaches stopTime, the time column is still monotonic,
    every other cell is untouched. In a real model this is what an unguarded
    division, a ``sqrt`` of a negative, or a Newton iteration that gave up looks
    like in the output — one poisoned column and a result file that a length
    check or a file-exists check accepts without comment.
    """
    base = _baseline()
    path = base.get("modelica_result_csv")
    if not path or not os.path.isfile(str(path)):
        return _with(ctx)
    binding = _first_binding(base)
    variable = (binding or {}).get("variable", "")

    header, rows = _read_rows(str(path))
    column = header.index(variable) if variable in header else min(1, len(header) - 1)
    if rows:
        row = list(rows[len(rows) // 2])
        while len(row) <= column:
            row.append("0")
        row[column] = "nan"
        rows[len(rows) // 2] = row
    target = _generated(ctx, "nan_res.csv")
    _write_rows(target, header, rows)
    return _with(ctx, modelica_result_csv=target)


def claim_exceeded(ctx):
    """modelica.result_claim — the claimed variable finishes past its limit.

    Shifts the first bound variable's whole column by the amount that puts its
    own reducer (``max``, ``final``, whatever the binding declares) just past the
    limit that binding will be judged against — MARGIN beyond it, computed with
    ``_modelica.reduce_series``, the same function the gate reduces with. A
    fixture that used its own arithmetic could be severe in a way the gate does
    not measure, and would then be testing itself.

    Only that one column moves, so the file is still a valid result table: the
    time column, the sample count, the header and every other variable are
    identical. This is a run that completed cleanly and a design that is wrong,
    which is exactly the case this gate has to separate from all the others.
    """
    base = _baseline()
    path = base.get("modelica_result_csv")
    binding = _first_binding(base)
    if not path or not os.path.isfile(str(path)) or binding is None:
        return _with(ctx)

    variable = str(binding["variable"])
    limit = float(binding["limit"])
    comparator = str(binding.get("comparator") or "<=")
    reducer = str(binding.get("reducer") or "final")
    at = binding.get("time", binding.get("at"))

    try:
        series = M.result_series(str(path), [variable])
        current, _how = M.reduce_series(series["time"], series[variable], reducer,
                                        None if at is None else float(at))
    except (M.ModelicaError, KeyError):
        return _with(ctx)

    overshoot = max(MARGIN * abs(limit), MARGIN_FLOOR)
    if comparator in (">=", ">"):
        target_value = limit - overshoot
    else:
        target_value = limit + overshoot
    shift = target_value - current

    header, rows = _read_rows(str(path))
    if variable not in header:
        return _with(ctx)
    column = header.index(variable)
    moved = []
    for row in rows:
        row = list(row)
        try:
            row[column] = repr(float(row[column]) + shift)
        except (TypeError, ValueError, IndexError):
            pass
        moved.append(row)
    target = _generated(ctx, "claim_exceeded_res.csv")
    _write_rows(target, header, moved)
    return _with(ctx, modelica_result_csv=target)


def mirror_drifted(ctx):
    """modelica.mirror_agrees — the mirror has drifted past the tolerance.

    Offsets the mirror's first variable, at every sample, by ``(1 + MARGIN)``
    times the band that variable will be judged in (``abs_tol + rel_tol *
    |mirror|``, computed per sample from the baseline's own tolerances). The
    offset therefore scales with whatever tolerance a project declares, and the
    control cannot be defused by loosening one.

    Nothing else changes: same times, same second variable, same Modelica result.
    This is a second implementation that has drifted one revision away from the
    model it is supposed to mirror — the failure the gate exists for, and the one
    nothing else in a project ever looks at, because both halves individually
    still produce numbers that look right.
    """
    base = _baseline()
    path = base.get("modelica_mirror_csv")
    if not path or not os.path.isfile(str(path)):
        return _with(ctx)

    spec = base.get("modelica_mirror_variables") or {}
    default_rel = float(base.get("modelica_mirror_rel_tol") or 1e-3)
    default_abs = float(base.get("modelica_mirror_abs_tol") or 0.0)

    header, rows = _read_rows(str(path))
    variables = [name for name in header if name != "time"]
    if not variables:
        return _with(ctx)
    variable = variables[0]
    entry = spec.get(variable) if isinstance(spec, dict) else None
    rel_tol = float((entry or {}).get("rel_tol", default_rel)) if isinstance(entry, dict) \
        else default_rel
    abs_tol = float((entry or {}).get("abs_tol", default_abs)) if isinstance(entry, dict) \
        else default_abs

    column = header.index(variable)
    drifted = []
    for row in rows:
        row = list(row)
        try:
            value = float(row[column])
        except (TypeError, ValueError, IndexError):
            drifted.append(row)
            continue
        band = abs_tol + rel_tol * abs(value)
        offset = max((1.0 + MARGIN) * band, MARGIN_FLOOR)
        row[column] = repr(value + offset)
        drifted.append(row)
    target = _generated(ctx, "drifted_mirror.csv")
    _write_rows(target, header, drifted)
    return _with(ctx, modelica_mirror_csv=target)


# --------------------------------------------------------------------------- #
# tier 2 — shipped .mo files omc genuinely rejects
# --------------------------------------------------------------------------- #
def _bad_asset(filename: str) -> str:
    return os.path.join(_PACK_DIR, "selftest", "assets", "bad", filename)


def unbalanced_model(ctx):
    """modelica.checks — one unknown too many.

    ``assets/bad/UnbalancedTank.mo``: the pack's own tank with ``qWall`` declared
    and never given an equation. checkModel reports 2 equations and 3 variables
    and the gate must fail on the inequality.
    """
    return _with(ctx,
                 modelica_sources=[_bad_asset("UnbalancedTank.mo")],
                 modelica_class="UnbalancedTank.Run",
                 modelica_load_libraries=[])


def will_not_compile(ctx):
    """modelica.compiles — a call to a function that does not exist.

    ``assets/bad/WillNotCompile.mo``: the wall loss routed through ``wallLoss()``,
    defined nowhere. The file parses and the balance is right; buildModel returns
    no executable.
    """
    return _with(ctx,
                 modelica_sources=[_bad_asset("WillNotCompile.mo")],
                 modelica_class="WillNotCompile.Run",
                 modelica_load_libraries=[])


def assert_fires(ctx):
    """modelica.simulates — the run stops on an assert at 3% of stopTime.

    ``assets/bad/AssertFires.mo``: a ceiling 43 K below the tank's own steady
    state, so ``assert(T < T_max)`` fires at t = 738 s of a 25000 s run. It
    compiles, it checks balanced, it initialises, and it writes a result file
    full of real numbers from a run that did not happen.
    """
    return _with(ctx,
                 modelica_sources=[_bad_asset("AssertFires.mo")],
                 modelica_class="AssertFires.Run",
                 modelica_load_libraries=[])
