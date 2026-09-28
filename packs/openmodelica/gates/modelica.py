# SPDX-License-Identifier: Apache-2.0
"""openmodelica — gates for a design whose physics is written as equations.

Five tier-0 gates that need nothing but the standard library, and three tier-2
gates that drive ``omc``. The split is the point of the pack: the tier-0 half
runs on every edit, on a laptop, with no compiler installed, and it is where
almost every real defect in a Modelica project is caught. The tier-2 half is the
compiler, and it is honest about being absent.

**What the tier-0 half actually reads.** Not the model — the model's *outputs*
and its *source text*. A result CSV, a mirror table, a set of .mo files. That is
a deliberate architecture: it means a project can record a run once, on a machine
that has OpenModelica, commit the result file, and keep gating claims against it
in CI and on every developer's laptop for as long as the model does not change.
``modelica.solution_valid`` is what stops that from becoming a lie — it refuses
a result file that did not finish, diverged, or never initialised, and it binds
to every claim tag in the pack so that when it trips, every number extracted from
that run reads as untrustworthy rather than as measured.

**Missing parameters SKIP; they never default.** A project that has not declared
``modelica_stop_time_s`` has not told this pack what the run was supposed to
reach, and inventing one would produce a green verdict for a question nobody
asked. Every gate names the keys it wanted in its skip reason.

**Claim binding.** Gates bind to the narrow quantity they measure. The one
deliberate exception is ``modelica.solution_valid``, which is this pack's
validity guard and binds broadly on purpose — see ``PACK.md`` for the tag
vocabulary a project should write its claims against.
"""
from __future__ import annotations

import fnmatch
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _modelica as M                                              # noqa: E402

from atompipe.gates import gate, GateContext                       # noqa: E402
from atompipe.models import Comparator, NegativeControl, Tier, Verdict   # noqa: E402

_MISSING = object()

#: Default ceiling on parameter declarations that carry no description or no
#: unit. ZERO, and the zero is the argument: a Modelica model is a system of
#: equations whose only defence against a unit slip is the declarations, and an
#: undescribed parameter is a number nobody can defend (method rule 3). A
#: tolerance of "a few" was rejected — in every real package this scanner was run
#: against, the count is either 0 or dozens, and a non-zero budget is a budget
#: that fills.
DEFAULT_MAX_BAD_PARAMETERS = 0

#: How far short of stopTime a run may stop and still count as having finished,
#: as a fraction of the run length. 1e-3 of the span, because omc writes the
#: final sample at exactly stopTime but a CSV round-trip through 17 significant
#: digits is not bit-exact and a solver that lands on 24999.9999997 s of a 25000 s
#: run finished. 0 was rejected for that reason; 1e-2 was rejected because a 1%
#: shortfall on a 24-hour run is fifteen minutes of missing transient.
DEFAULT_STOP_TIME_TOL_FRAC = 1e-3

#: Default relative tolerance for cross-representation agreement. 1e-3 is a
#: thousand times looser than a default solver tolerance (1e-6) and tight enough
#: that a sign error, a unit slip or a dropped term cannot hide under it. It is a
#: DEFAULT, not a recommendation: the right number comes from the effect being
#: claimed, and ``PACK.md`` says why a tolerance looser than the effect makes the
#: gate decorative.
DEFAULT_MIRROR_REL_TOL = 1e-3

#: Default wall-clock budget for a checkModel/buildModel invocation, seconds.
#: Generous because omc's first run on a machine loads and indexes the library
#: tree; a second run on the same model is usually a few seconds.
DEFAULT_OMC_TIMEOUT_S = 300.0

#: Default wall-clock budget for a compile-and-simulate, seconds. Separate from
#: the above because simulate() includes a C compilation of generated code, and a
#: budget that is right for checkModel stops a real simulation half way and
#: reports it as a failure of the model.
DEFAULT_SIMULATE_TIMEOUT_S = 900.0


# --------------------------------------------------------------------------- #
# parameter access
# --------------------------------------------------------------------------- #
def _need(ctx: GateContext, *keys: str) -> tuple[dict, list[str]]:
    values: dict = {}
    missing: list[str] = []
    for key in keys:
        value = ctx.param(key, _MISSING)
        if value is _MISSING or value is None:
            missing.append(key)
        else:
            values[key] = value
    return values, missing


def _skip(gate_id: str, reason: str) -> Verdict:
    """A visible refusal to run. The claim resolves BLOCKED, never PASS."""
    return Verdict(gate=gate_id, passed=False, skipped=True, skip_reason=reason)


def _skip_missing(gate_id: str, missing: list[str], hint: str = "") -> Verdict:
    return _skip(gate_id, f"model does not project {', '.join(missing)}"
                          + (f" — {hint}" if hint else ""))


def _write(ctx: GateContext, name: str, text: str) -> list[str]:
    """Best-effort evidence file. A read-only out_dir must not error a good gate."""
    try:
        path = ctx.out_path(name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return [path]
    except OSError:
        return []


def _as_float(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


# --------------------------------------------------------------------------- #
# the claim -> variable mapping, shared by claims_addressable and result_claim
# --------------------------------------------------------------------------- #
class ClaimBinding:
    """One row of ``modelica_claim_variables``, with its acceptance resolved.

    The acceptance comes from the LEDGER's claim when there is one, because the
    claim is the source of truth for what the design must achieve and a limit
    restated in the pack's own mapping is a duplicated constant waiting to drift
    (method rule 2). The mapping's own comparator/limit is the fallback for a
    project that has written the binding before it wrote the claim — and for this
    pack's own baseline, which has no ledger at all.
    """

    __slots__ = ("claim", "variable", "reducer", "at", "comparator", "limit",
                 "limit_hi", "units", "acceptance_from", "problem")

    def __init__(self, raw, ctx: GateContext, index: int) -> None:
        self.problem = ""
        self.claim = ""
        self.variable = ""
        self.reducer = "final"
        self.at: float | None = None
        self.comparator: Comparator | None = None
        self.limit: float | None = None
        self.limit_hi: float | None = None
        self.units = ""
        self.acceptance_from = ""

        if isinstance(raw, str):
            raw = {"variable": raw}
        if not isinstance(raw, dict):
            self.problem = f"entry {index} is a {type(raw).__name__}, not an object"
            return

        self.claim = str(raw.get("claim") or raw.get("claim_id") or "").strip()
        self.variable = str(raw.get("variable") or raw.get("var") or "").strip()
        if not self.variable:
            self.problem = f"entry {index} ({self.claim or 'unnamed'}) names no variable"
            return
        self.reducer = str(raw.get("reducer") or "final").strip().lower()
        self.at = _as_float(raw.get("time") if raw.get("time") is not None
                            else raw.get("at"), None)
        self.units = str(raw.get("units") or "")

        claim = ctx.ledger.claim(self.claim) if self.claim else None
        acceptance = getattr(claim, "acceptance", None) if claim is not None else None
        if acceptance is not None and acceptance.limit is not None:
            self.comparator = acceptance.comparator
            self.limit = float(acceptance.limit)
            self.limit_hi = (None if acceptance.limit_hi is None
                             else float(acceptance.limit_hi))
            self.units = acceptance.units or self.units
            self.acceptance_from = f"claim {self.claim}"
            return

        raw_limit = _as_float(raw.get("limit"), None)
        if raw_limit is None:
            self.acceptance_from = ""
            return
        try:
            self.comparator = Comparator(str(raw.get("comparator") or "<="))
        except ValueError:
            self.problem = (f"entry {index} ({self.claim or self.variable}) has "
                            f"comparator {raw.get('comparator')!r}, which is not one of "
                            f"{', '.join(c.value for c in Comparator)}")
            return
        self.limit = raw_limit
        self.limit_hi = _as_float(raw.get("limit_hi"), None)
        self.acceptance_from = "modelica_claim_variables"

    @property
    def label(self) -> str:
        return self.claim or self.variable

    @property
    def has_acceptance(self) -> bool:
        return self.comparator is not None and self.limit is not None


def _bindings(ctx: GateContext) -> tuple[list[ClaimBinding], list[str]]:
    """Parse ``modelica_claim_variables`` into bindings plus a list of complaints."""
    raw = ctx.param("modelica_claim_variables", None)
    entries: list = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, str):
                entries.append({"claim": key, "variable": value})
            elif isinstance(value, dict):
                entries.append({"claim": key, **value})
            else:
                entries.append(value)
    else:
        entries = _as_list(raw)

    bindings: list[ClaimBinding] = []
    problems: list[str] = []
    for index, entry in enumerate(entries, start=1):
        binding = ClaimBinding(entry, ctx, index)
        if binding.problem:
            problems.append(binding.problem)
            continue
        bindings.append(binding)
    return bindings, problems


_IDENTIFIER_RE = __import__("re").compile(r"^(?:der\()?[A-Za-z_][A-Za-z_0-9]*"
                                          r"(?:\.[A-Za-z_][A-Za-z_0-9]*|\[[^\]]*\])*\)?$")


def _ledger_variables(ctx: GateContext) -> list[tuple[str, str]]:
    """``(claim id, variable)`` for ledger claims whose acceptance names an identifier.

    A project may state the Modelica variable directly in a claim's
    ``acceptance.quantity`` ("tank.T") rather than in the pack's mapping. Picking
    those up is what makes the drift check cover claims nobody remembered to add
    to ``modelica_claim_variables`` — which is precisely the set most likely to
    have been left behind by a rename.
    """
    out: list[tuple[str, str]] = []
    for claim in getattr(ctx.ledger, "claims", []) or []:
        quantity = (getattr(getattr(claim, "acceptance", None), "quantity", "") or "").strip()
        if quantity and " " not in quantity and _IDENTIFIER_RE.match(quantity):
            out.append((claim.id, quantity))
    return out


# =========================================================================== #
# tier 0 — source hygiene
# =========================================================================== #
@gate(
    id="modelica.source_hygiene",
    title="Every parameter declaration carries a description, and a physical one a unit",
    claims=["modelica-source", "model-hygiene", "parameter-provenance"],
    tier=Tier.INSTANT,
    settles="parameter description and unit coverage",
    negative_control=NegativeControl(
        fixture="selftest/bad_modelica.py:stripped_declarations",
        note="the pack's own .mo sources rewritten so every parameter loses its "
             "description string and every `unit` modification becomes `displayUnit` "
             "— including the unit on the local SI type aliases, so a declaration "
             "cannot inherit one. Equations, values, structure and file layout are "
             "untouched: the model still simulates, it just cannot be unit-checked "
             "and cannot be defended",
    ),
)
def source_hygiene(ctx: GateContext) -> Verdict:
    """Scan the project's .mo files for parameters with no description or no unit.

    Both halves are the same defect seen from two sides. A ``parameter Real x =
    0.62;`` in an equation-based model is a number nobody can defend — method
    rule 3 — and it is also a unit error waiting to happen, because the only unit
    checking Modelica has is the one the declarations declare. The compiler will
    happily add a length to a temperature if neither carries a unit, and the
    result is a plausible number.

    A unit is credited from three places, in this order: an explicit ``unit=``
    modification on the declaration; a local ``type X = Real(unit="...")`` alias,
    resolved by following the alias chain; and a type from an SI namespace
    (``Modelica.Units.SI.*``, ``Modelica.SIunits.*``, an ``SI.`` import alias),
    which carries its unit in the library this scanner never opened.

    Non-physical parameters — Integer, Boolean, String, enumeration — are not
    asked for a unit. Neither are declarations whose type this scanner cannot
    resolve (a record, a library class): counting those as unit defects would
    make the headline number depend on how much of the library tree happened to
    be on the scan path, which is a gate whose answer changes when you move a
    file. They are counted and reported separately instead.

    A genuinely dimensionless parameter should be declared ``Real x(unit="1")``,
    which is the Modelica-correct spelling and costs nine characters.
    ``modelica_unitless_ok`` is the escape hatch (fnmatch patterns against the
    parameter name) and the verdict always says how many it excused, because an
    allowlist nobody can see is an allowlist that grows.
    """
    gid = "modelica.source_hygiene"
    values, missing = _need(ctx, "modelica_sources")
    if missing:
        return _skip_missing(gid, missing,
                             "a list of .mo files or directories to scan")

    entries = [str(e) for e in _as_list(values["modelica_sources"])]
    files = M.gather_mo_files(entries, ctx.root)
    if not files:
        return _skip(gid, f"modelica_sources names no readable .mo file "
                          f"(looked at {', '.join(entries[:3])} under {ctx.root or '.'})")

    model = M.scan_sources(entries, ctx.root)
    parameters = model.parameters
    if not parameters:
        return _skip(gid, f"scanned {len(files)} .mo file(s) and found no parameter "
                          f"declarations — either the sources are not Modelica or the "
                          f"model has no parameters to check")

    excuses = [str(p) for p in _as_list(ctx.param("modelica_unitless_ok", []))]
    limit = int(_as_float(ctx.param("modelica_max_bad_parameters",
                                    DEFAULT_MAX_BAD_PARAMETERS),
                          DEFAULT_MAX_BAD_PARAMETERS) or 0)

    undescribed: list[M.Component] = []
    unitless: list[M.Component] = []
    excused: list[M.Component] = []
    unresolved: list[M.Component] = []
    offenders: list[tuple[int, M.Component, str]] = []

    for component in parameters:
        faults: list[str] = []
        if not component.description.strip():
            undescribed.append(component)
            faults.append("no description")
        kind = M.classify_parameter(model, component)
        if kind == "unitless":
            if any(fnmatch.fnmatchcase(component.name, pattern) for pattern in excuses):
                excused.append(component)
            else:
                unitless.append(component)
                faults.append("no unit")
        elif kind == "unresolved-type":
            unresolved.append(component)
        if faults:
            offenders.append((len(faults), component, " and ".join(faults)))

    offenders.sort(key=lambda row: (-row[0], row[1].file, row[1].line))
    measured = len(offenders)

    report = [f"{len(files)} file(s), {len(model.class_list)} class(es), "
              f"{len(parameters)} parameter declaration(s)",
              f"undescribed: {len(undescribed)}   unitless: {len(unitless)}   "
              f"excused by modelica_unitless_ok: {len(excused)}   "
              f"type not resolvable: {len(unresolved)}", ""]
    for _count, component, fault in offenders:
        report.append(f"  {component.where:<28} {component.owner}.{component.name} "
                      f"({component.type_name}): {fault}")
    if unresolved:
        report.append("")
        report.append("not asked for a unit (type defined outside the scanned sources):")
        for component in unresolved[:40]:
            report.append(f"  {component.where:<28} {component.owner}.{component.name} "
                          f": {component.type_name}")
    evidence = _write(ctx, "modelica_source_hygiene.txt", "\n".join(report) + "\n")

    worst = ", ".join(f"{c.owner.split('.')[-1]}.{c.name} ({f})"
                      for _n, c, f in offenders[:3])
    return Verdict(
        gate=gid, passed=measured <= limit, measured=measured, limit=limit,
        units="parameters",
        detail=(f"{measured}/{len(parameters)} parameter(s) undefendable across "
                f"{len(files)} file(s): {len(undescribed)} with no description, "
                f"{len(unitless)} with no unit"
                + (f", {len(excused)} excused" if excused else "")
                + (f", {len(unresolved)} of unresolvable type (not counted)"
                   if unresolved else "")
                + (f"; worst: {worst}" if worst else "")),
        evidence=evidence,
    )


# =========================================================================== #
# tier 0 — claim/model drift
# =========================================================================== #
@gate(
    id="modelica.claims_addressable",
    title="Every Modelica variable a claim names still exists in the model or the result",
    claims=["claim-coverage", "model-drift", "modelica-source"],
    tier=Tier.INSTANT,
    settles="claim variable addressability",
    negative_control=NegativeControl(
        fixture="selftest/bad_modelica.py:renamed_variable",
        note="one claim's variable is given the name it would have after a rename "
             "the claim was not updated for (a '_v2' suffix). The model, the result "
             "file and every other binding are untouched — this is the state a "
             "project is in for the three revisions between renaming a variable and "
             "noticing that a claim stopped being checked",
    ),
)
def claims_addressable(ctx: GateContext) -> Verdict:
    """The drift check between what the project CLAIMS and what the model can SAY.

    A claim that names ``tank.outletTemperature`` after the variable was renamed
    to ``tank.T_out`` does not fail. It silently stops being checked: the
    extraction gate skips for a missing column, the skip resolves the claim to
    BLOCKED, and BLOCKED in a long report reads as "waiting on tooling" rather
    than "nobody has verified this since March".

    A variable is addressable if it is a column in the recorded result file, or
    if it resolves segment by segment through the model source from
    ``modelica_class`` — ``tank`` is a component of TankRun, ``T`` is a component
    of the Tank that ``tank`` is an instance of. Both sources are checked and the
    verdict says which one answered, because they fail differently: a variable
    missing from the source is a rename, a variable missing from the result but
    present in the source is usually a ``variableFilter`` that excluded it.

    The walk stops honestly. When it reaches a component whose type is defined
    outside the scanned sources — an MSL block, a library component — the
    remaining segments are reported UNRESOLVED and are not counted as missing.
    Guessing there would fail a claim for correctly naming a variable of a
    library component, which is the most common correct thing a project does.
    """
    gid = "modelica.claims_addressable"
    bindings, problems = _bindings(ctx)
    wanted: list[tuple[str, str]] = [(b.label, b.variable) for b in bindings]
    wanted.extend(_ledger_variables(ctx))
    if not wanted:
        return _skip(gid, "no claim names a Modelica variable — set "
                          "modelica_claim_variables (a list of {claim, variable, "
                          "reducer}) or write the variable name into a claim's "
                          "acceptance.quantity")

    result_path = ctx.param("modelica_result_csv", None)
    columns: list[str] = []
    column_error = ""
    if result_path:
        try:
            columns = M.result_columns(M.resolve_path(ctx.root, str(result_path)))
        except M.ModelicaError as exc:
            column_error = str(exc)
    column_set = set(columns)
    normalised_columns = {M.normalise_variable(c) for c in columns}

    sources = [str(e) for e in _as_list(ctx.param("modelica_sources", []))]
    model = M.scan_sources(sources, ctx.root) if sources else M.SourceModel()
    root_class = str(ctx.param("modelica_class", "") or "")

    if not columns and not model.class_list:
        return _skip(gid, "nothing to check against: modelica_result_csv is absent or "
                          "unreadable" + (f" ({column_error})" if column_error else "")
                          + " and modelica_sources names no readable .mo file")

    found: list[str] = []
    unresolved: list[str] = []
    dead: list[str] = []
    lines: list[str] = [f"root class: {root_class or '(none declared)'}",
                        f"result columns: {len(columns)}   "
                        f"scanned classes: {len(model.class_list)}", ""]
    for label, variable in wanted:
        if variable in column_set:
            found.append(variable)
            lines.append(f"  OK          {label:<18} {variable}  (result column)")
            continue
        if M.normalise_variable(variable) in normalised_columns:
            found.append(variable)
            lines.append(f"  OK          {label:<18} {variable}  (result column, "
                         f"der()/subscript normalised)")
            continue
        if not model.class_list:
            dead.append(f"{label}:{variable}")
            lines.append(f"  MISSING     {label:<18} {variable}  (not a result column; "
                         f"no sources scanned to check the model)")
            continue
        state, why = M.resolve_variable(model, root_class, variable)
        if state == "found":
            found.append(variable)
            lines.append(f"  OK          {label:<18} {variable}  (model source: {why})")
        elif state == "unresolved":
            unresolved.append(f"{label}:{variable}")
            lines.append(f"  UNRESOLVED  {label:<18} {variable}  ({why})")
        else:
            dead.append(f"{label}:{variable}")
            lines.append(f"  MISSING     {label:<18} {variable}  ({why})")

    for problem in problems:
        lines.append(f"  MALFORMED   {problem}")
    evidence = _write(ctx, "modelica_claim_variables.txt", "\n".join(lines) + "\n")

    measured = len(dead) + len(problems)
    return Verdict(
        gate=gid, passed=measured == 0, measured=measured, limit=0,
        units="variables",
        detail=(f"{len(found)}/{len(wanted)} claim variable(s) addressable "
                f"({len(columns)} result columns, {len(model.class_list)} scanned "
                f"classes)"
                + (f"; {len(unresolved)} unresolvable outside the scanned sources: "
                   f"{', '.join(unresolved[:2])}" if unresolved else "")
                + (f"; MISSING: {', '.join(dead[:3])}" if dead else "")
                + (f"; {len(problems)} malformed binding(s): {problems[0]}"
                   if problems else "")),
        evidence=evidence,
    )


# =========================================================================== #
# tier 0 — THE VALIDITY GUARD
# =========================================================================== #
@gate(
    id="modelica.solution_valid",
    title="The recorded run finished, stayed finite, and produced the variables claimed",
    claims=[
        # Deliberately broad. This is the pack's validity guard: when it trips,
        # every number anybody extracted from that run is untrustworthy, so it
        # must drag the whole domain down with it. See PACK.md, "Claim tags".
        "modelica", "simulation-result", "result-extraction", "equation-model",
        "cross-representation", "model-agreement", "mirror-agreement",
        "steady-state", "dynamic-response", "transient",
    ],
    tier=Tier.INSTANT,
    settles="simulation result validity",
    negative_control=NegativeControl(
        fixture="selftest/bad_modelica.py:run_truncated",
        note="the pack's own result file with every sample past half of stopTime "
             "removed: one physically meaningful change, the run did not finish. "
             "Every remaining number is bit-identical, which is the whole problem — "
             "a final value read off this file is a real number from a run that "
             "stopped mid-transient. selftest/bad_modelica.py:nan_injected is the "
             "same gate's second fixture, a single NaN in one cell",
    ),
)
def solution_valid(ctx: GateContext) -> Verdict:
    """Refuse a result file that cannot support the numbers read off it.

    This is the most important gate in the pack and the cheapest. Reading a final
    value off a simulation that stopped early, diverged, or never initialised is
    the silent failure of this entire domain: the number is a real float from a
    real file produced by a real solver, and nothing about it looks wrong. The
    run ended at 738 s of a 25000 s experiment because an assert fired, and the
    "steady-state temperature" somebody quoted is the temperature it happened to
    have on the way up.

    Six assertions, all of them cheap:

    * **the run reached stopTime** — within ``modelica_stop_time_tol_frac`` of the
      span. This is the one that catches an assert, a solver give-up and a
      killed process.
    * **no NaN or Inf anywhere**, in any column, at any time. A diverged state
      poisons everything downstream of it in the same run.
    * **every requested variable exists** — ``modelica_required_variables``. A
      ``variableFilter`` that silently excluded a variable produces a result file
      that is valid and useless.
    * **the time column is non-decreasing**. Non-DEcreasing, not increasing: omc
      writes duplicate time points at events, one sample either side of the
      discontinuity, and a gate demanding strict monotonicity fails every model
      with a switch in it.
    * **the rows are rectangular and numeric** — a truncated write or an
      interrupted run leaves a short final row.
    * **the state derivatives have settled**, when and only when the project asks
      for it with ``modelica_steady_state_tol``. This is the difference between
      "the run finished" and "the run finished having got somewhere", and it is
      what makes a claim about a steady-state value mean anything.

    ``measured`` is the time the run actually reached and ``limit`` is the
    stopTime it was supposed to reach, because that is the number a reader wants
    first. Everything else is in the detail and the evidence file.
    """
    gid = "modelica.solution_valid"
    values, missing = _need(ctx, "modelica_result_csv", "modelica_stop_time_s")
    if missing:
        return _skip_missing(gid, missing,
                             "the recorded run and the stopTime it was asked for; "
                             "without the stopTime there is nothing to compare the "
                             "run's end against and 'it finished' is unfalsifiable")

    path = M.resolve_path(ctx.root, str(values["modelica_result_csv"]))
    stop_time = _as_float(values["modelica_stop_time_s"])
    if stop_time is None:
        return _skip(gid, f"modelica_stop_time_s is {values['modelica_stop_time_s']!r}, "
                          f"not a number")
    start_time = _as_float(ctx.param("modelica_start_time_s", 0.0), 0.0) or 0.0
    span = abs(stop_time - start_time)
    tol_frac = _as_float(ctx.param("modelica_stop_time_tol_frac",
                                   DEFAULT_STOP_TIME_TOL_FRAC),
                         DEFAULT_STOP_TIME_TOL_FRAC) or DEFAULT_STOP_TIME_TOL_FRAC

    try:
        scan = M.result_scan(path)
    except M.ModelicaError as exc:
        return _skip(gid, f"{exc}")

    failures: list[str] = []
    notes: list[str] = []

    if not scan.columns or scan.columns[0] != "time":
        failures.append(f"first column is {scan.columns[0]!r}, not 'time' — this is not "
                        f"an OMC result table")
    if scan.rows < 2:
        failures.append(f"{scan.rows} data row(s): a run with fewer than two samples "
                        f"never integrated anything")

    last = scan.last_time
    if last is None:
        failures.append("the time column has no readable value at all")
    else:
        shortfall = stop_time - last
        if shortfall > max(span * tol_frac, 0.0):
            fraction = (last - start_time) / span if span else 0.0
            failures.append(f"stopped at t={last:g} s, {fraction * 100:.1f}% of the "
                            f"{start_time:g}..{stop_time:g} s run it was asked for "
                            f"({shortfall:g} s short)")
        first = scan.first_time
        if first is not None and abs(first - start_time) > max(span * tol_frac, 0.0):
            failures.append(f"first sample is at t={first:g} s, not the declared "
                            f"startTime {start_time:g} s")

    if scan.nonfinite:
        row, name, raw = scan.nonfinite[0]
        failures.append(f"{len(scan.nonfinite)}{'+' if len(scan.nonfinite) >= M.PROBLEM_CAP else ''} "
                        f"non-finite cell(s), first at line {row} in {name!r} ({raw})")
    if scan.unparseable:
        row, name, raw = scan.unparseable[0]
        failures.append(f"{len(scan.unparseable)} unparseable cell(s), first at line "
                        f"{row} in {name!r} ({raw!r})")
    if scan.ragged:
        failures.append(f"{len(scan.ragged)} row(s) have a different width from the "
                        f"header, first at line {scan.ragged[0]} — a truncated write")
    if scan.backwards:
        row, was, now = scan.backwards[0]
        failures.append(f"time goes backwards at line {row} ({was:g} -> {now:g} s)")

    required = [str(v) for v in _as_list(ctx.param("modelica_required_variables", []))]
    column_set = set(scan.columns)
    absent = [v for v in required if v not in column_set]
    if absent:
        failures.append(f"{len(absent)} requested variable(s) are not columns in this "
                        f"result: {', '.join(absent[:4])}")

    steady_tol = _as_float(ctx.param("modelica_steady_state_tol", None), None)
    steady_report: list[str] = []
    if steady_tol is not None:
        steady_vars = [str(v) for v in _as_list(ctx.param("modelica_steady_state_vars", []))]
        if not steady_vars:
            steady_vars = [c for c in scan.columns if c.startswith("der(")]
        if not steady_vars:
            failures.append("modelica_steady_state_tol was set but the result has no "
                            "der(...) columns and modelica_steady_state_vars is empty — "
                            "nothing was checked for settling")
        else:
            try:
                series = M.result_series(path, steady_vars)
            except M.ModelicaError as exc:
                failures.append(f"steady-state check: {exc}")
            else:
                worst_name, worst_value = "", 0.0
                for name in steady_vars:
                    tail = series.get(name) or []
                    if not tail:
                        continue
                    final = abs(tail[-1])
                    steady_report.append(f"  |{name}| final = {final:.6g} "
                                         f"vs tol {steady_tol:g}")
                    if final > abs(worst_value):
                        worst_name, worst_value = name, final
                if worst_name and abs(worst_value) > steady_tol:
                    failures.append(f"still transient at the end: |{worst_name}| = "
                                    f"{abs(worst_value):.4g} > modelica_steady_state_tol "
                                    f"{steady_tol:g} — the run was cut mid-change, not "
                                    f"settled")
                elif worst_name:
                    notes.append(f"settled (|{worst_name}| = {abs(worst_value):.3g} <= "
                                 f"{steady_tol:g})")
    else:
        notes.append("no steady-state check requested (set modelica_steady_state_tol)")

    report = [f"result file: {path}",
              f"columns: {len(scan.columns)}   rows: {scan.rows}",
              f"time: {scan.first_time} .. {scan.last_time} s "
              f"(asked for {start_time:g} .. {stop_time:g} s)",
              f"non-finite cells: {len(scan.nonfinite)}   unparseable: "
              f"{len(scan.unparseable)}   ragged rows: {len(scan.ragged)}   "
              f"backward time steps: {len(scan.backwards)}",
              f"required variables: {len(required)} requested, {len(absent)} absent", ""]
    report.extend(steady_report)
    if failures:
        report.append("")
        report.append("FAILURES")
        report.extend(f"  - {f}" for f in failures)
    evidence = _write(ctx, "modelica_solution_valid.txt", "\n".join(report) + "\n")

    reached = last if last is not None else start_time
    headline = (f"reached t={reached:g}/{stop_time:g} s over {scan.rows} samples x "
                f"{len(scan.columns)} variables")
    return Verdict(
        gate=gid, passed=not failures,
        measured=round(reached, 9), limit=round(stop_time, 9), units="s",
        detail=(f"{headline}; " + ("; ".join(failures[:3]) if failures
                                   else "; ".join(notes) or "all checks clean")),
        evidence=evidence,
    )


# =========================================================================== #
# tier 0 — turning a run into a settled claim
# =========================================================================== #
@gate(
    id="modelica.result_claim",
    title="A variable reduced out of the result meets its claim's acceptance",
    claims=["result-extraction", "simulation-result", "modelica"],
    tier=Tier.INSTANT,
    settles="simulated value against acceptance",
    negative_control=NegativeControl(
        fixture="selftest/bad_modelica.py:claim_exceeded",
        note="the pack's own result file with the first bound variable shifted so "
             "its reducer lands past the limit that binding will be judged by — the "
             "shift is computed FROM that limit, so the control still fires on a "
             "project whose limits are nothing like this pack's. Time, sample count "
             "and every other column are untouched: the run is valid and the design "
             "is wrong, which is the case this gate exists to separate from all the "
             "others",
    ),
)
def result_claim(ctx: GateContext) -> Verdict:
    """Extract a variable from the result and compare it against the acceptance.

    This is the bridge that turns a simulation into a settled claim, and it is
    deliberately the thinnest gate in the pack: read a column, reduce it to one
    number, compare. Everything that could make that number a lie is somebody
    else's job — ``modelica.solution_valid`` refuses the run, and
    ``modelica.claims_addressable`` refuses the name.

    Reducers: ``final``, ``initial``, ``min``, ``max``, ``mean``, ``absmax``,
    ``range``, or ``{"reducer": "at", "time": <seconds>}`` which linearly
    interpolates between the bracketing samples and REFUSES to extrapolate past
    the end of the run.

    The acceptance comes from the ledger's claim when the binding names one, so
    the limit lives in exactly one place (method rule 2). A binding that names no
    claim and carries no limit of its own is reported as unusable rather than
    quietly skipped — a mapping entry with no threshold is a report, not a gate.

    The verdict reports the WORST binding — the one furthest past its limit,
    normalised by the limit so a temperature and a power can be ranked against
    each other — with the full table in the evidence file.
    """
    gid = "modelica.result_claim"
    bindings, problems = _bindings(ctx)
    if not bindings:
        return _skip(gid, "modelica_claim_variables is empty or absent — it maps a "
                          "claim to (variable, reducer): "
                          "[{\"claim\": \"C1\", \"variable\": \"tank.T\", "
                          "\"reducer\": \"max\"}]"
                          + (f"; {problems[0]}" if problems else ""))
    usable = [b for b in bindings if b.has_acceptance]
    if not usable:
        return _skip(gid, f"none of the {len(bindings)} binding(s) has an acceptance: "
                          f"name a claim that carries one, or give the binding its own "
                          f"comparator and limit")

    values, missing = _need(ctx, "modelica_result_csv")
    if missing:
        return _skip_missing(gid, missing, "the recorded run to read the variables out of")
    path = M.resolve_path(ctx.root, str(values["modelica_result_csv"]))

    try:
        series = M.result_series(path, [b.variable for b in usable])
    except M.ModelicaError as exc:
        return _skip(gid, str(exc))
    times = series.get("time") or []
    if not times:
        return _skip(gid, f"{os.path.basename(path)} has no time samples to reduce over")

    rows: list[str] = [f"result file: {path}", f"{len(times)} samples", ""]
    failed: list[str] = []
    worst_margin = -math.inf
    worst: ClaimBinding | None = None
    worst_value = None
    unusable: list[str] = []

    for binding in usable:
        column = series.get(binding.variable) or []
        try:
            value, how = M.reduce_series(times, column, binding.reducer, binding.at)
        except M.ModelicaError as exc:
            unusable.append(f"{binding.label}: {exc}")
            rows.append(f"  UNUSABLE  {binding.label:<16} {binding.variable}: {exc}")
            continue
        if math.isnan(value) or math.isinf(value):
            unusable.append(f"{binding.label}: reduced to {value}")
            rows.append(f"  UNUSABLE  {binding.label:<16} {binding.variable}: "
                        f"reduced to {value} — see modelica.solution_valid")
            continue
        holds = binding.comparator.holds(value, binding.limit, binding.limit_hi)
        scale = max(abs(binding.limit), 1e-12)
        if binding.comparator in (Comparator.LE, Comparator.LT):
            margin = (value - binding.limit) / scale
        elif binding.comparator in (Comparator.GE, Comparator.GT):
            margin = (binding.limit - value) / scale
        else:
            margin = 0.0 if holds else abs(value - binding.limit) / scale
        if margin > worst_margin:
            worst_margin, worst, worst_value = margin, binding, value
        rows.append(f"  {'ok  ' if holds else 'FAIL'}      {binding.label:<16} "
                    f"{binding.variable} {how} = {value:.6g} {binding.units} "
                    f"{binding.comparator.value} {binding.limit:g} "
                    f"[{binding.acceptance_from}]")
        if not holds:
            failed.append(f"{binding.label} {binding.variable}={value:.6g} "
                          f"{binding.comparator.value} {binding.limit:g}")

    for problem in problems:
        rows.append(f"  MALFORMED {problem}")
    evidence = _write(ctx, "modelica_result_claims.txt", "\n".join(rows) + "\n")

    passed = not failed and not unusable and not problems
    detail = (f"{len(usable) - len(failed) - len(unusable)}/{len(usable)} bound "
              f"claim(s) met from {os.path.basename(path)}")
    if worst is not None:
        detail += (f"; worst {worst.label}: {worst.variable} = {worst_value:.6g} "
                   f"{worst.units} vs {worst.comparator.value} {worst.limit:g} "
                   f"({worst_margin * 100:+.1f}% of limit)")
    if failed:
        detail += f"; FAILED: {', '.join(failed[:2])}"
    if unusable:
        detail += f"; unusable: {unusable[0]}"
    if problems:
        detail += f"; malformed: {problems[0]}"

    return Verdict(
        gate=gid, passed=passed,
        measured=(None if worst_value is None else round(float(worst_value), 9)),
        limit=(None if worst is None else round(float(worst.limit), 9)),
        units=(worst.units if worst is not None else ""),
        detail=detail, evidence=evidence,
    )


# =========================================================================== #
# tier 0 — cross-representation agreement (method rule 6)
# =========================================================================== #
@gate(
    id="modelica.mirror_agrees",
    title="The Modelica result and an independent implementation still agree",
    claims=["cross-representation", "model-agreement", "mirror-agreement"],
    tier=Tier.INSTANT,
    settles="agreement between the model and its mirror",
    negative_control=NegativeControl(
        fixture="selftest/bad_modelica.py:mirror_drifted",
        note="the mirror's first variable is offset by just past the tolerance it "
             "will be judged against — the offset is computed FROM that tolerance, so "
             "the control fires whatever a project's tolerance is. Nothing else "
             "moves: this is a mirror that has drifted one revision away from the "
             "model, which is what the gate exists to catch and what nothing else "
             "in a project ever looks at",
    ),
)
def mirror_agrees(ctx: GateContext) -> Verdict:
    """Compare the Modelica result against an independent implementation.

    Method rule 6: the same fact usually lives in several places produced by
    different programs, and the check exists to catch frame errors, unit slips
    and regressions that are invisible until they are expensive.

    The failure this gate is built around is worth stating plainly, because it is
    common and it does not feel like a mistake while it is happening. A formal
    model exists but cannot be run in the environment that needs the numbers —
    no compiler on the CI box, no licence on the analyst's laptop, a notebook
    that has to render in a browser. So somebody writes a second implementation
    of the same equations, in Python or a spreadsheet, to get the numbers out.
    It works. It gets used. It gets extended. And from that day the two drift,
    with nothing watching, until the mirror is the thing everyone trusts and
    nobody has checked. **The mirror is not the risk. The mirror with no
    comparison gate is the risk.**

    The mirror is a CSV with a ``time`` column and one column per variable —
    deliberately the same shape as an OMC result file, so it can be exported from
    a spreadsheet, a notebook or another solver with no adapter in between. Each
    mirror sample is compared against the Modelica result interpolated at that
    time; a mirror time outside the run's span is refused rather than
    extrapolated.

    Agreement is ``|a - b| <= abs_tol + rel_tol * |mirror|`` per variable, which
    is the usual mixed criterion and the only one that works across a set of
    variables in different units and through a zero crossing. The verdict's
    ``measured`` is the worst ratio of actual error to allowed error: 1.0 means
    exactly at tolerance, so the number is comparable across variables and the
    reader can see how much room is left.
    """
    gid = "modelica.mirror_agrees"
    values, missing = _need(ctx, "modelica_result_csv", "modelica_mirror_csv")
    if missing:
        return _skip_missing(gid, missing,
                             "the Modelica result and the independent implementation "
                             "to compare it against (a CSV with a time column and one "
                             "column per variable)")
    result_path = M.resolve_path(ctx.root, str(values["modelica_result_csv"]))
    mirror_path = M.resolve_path(ctx.root, str(values["modelica_mirror_csv"]))

    try:
        mirror_columns, mirror = M.read_table_csv(mirror_path)
    except M.ModelicaError as exc:
        return _skip(gid, str(exc))
    if not mirror_columns or mirror_columns[0] != "time":
        return _skip(gid, f"{os.path.basename(mirror_path)}'s first column is "
                          f"{mirror_columns[0] if mirror_columns else '(none)'!r}, not "
                          f"'time' — the mirror uses the result-file shape so the two "
                          f"can be compared sample by sample")

    spec = ctx.param("modelica_mirror_variables", None)
    default_rel = _as_float(ctx.param("modelica_mirror_rel_tol",
                                      DEFAULT_MIRROR_REL_TOL),
                            DEFAULT_MIRROR_REL_TOL) or DEFAULT_MIRROR_REL_TOL
    default_abs = _as_float(ctx.param("modelica_mirror_abs_tol", 0.0), 0.0) or 0.0

    tolerances: dict[str, tuple[float, float]] = {}
    if isinstance(spec, dict):
        for name, entry in spec.items():
            if isinstance(entry, dict):
                tolerances[str(name)] = (
                    _as_float(entry.get("rel_tol"), default_rel) or 0.0,
                    _as_float(entry.get("abs_tol"), default_abs) or 0.0)
            else:
                tolerances[str(name)] = (default_rel, default_abs)
    else:
        for name in (_as_list(spec) or [c for c in mirror_columns if c != "time"]):
            tolerances[str(name)] = (default_rel, default_abs)
    tolerances.pop("time", None)
    if not tolerances:
        return _skip(gid, f"{os.path.basename(mirror_path)} has no variable columns "
                          f"besides time")

    absent = [n for n in tolerances if n not in mirror]
    if absent:
        return _skip(gid, f"modelica_mirror_variables names {', '.join(absent[:4])}, "
                          f"which {os.path.basename(mirror_path)} does not have "
                          f"(it has {', '.join(c for c in mirror_columns if c != 'time')})")

    try:
        series = M.result_series(result_path, list(tolerances))
    except M.ModelicaError as exc:
        return _skip(gid, f"the Modelica result cannot supply the mirror's variables: {exc}")
    times = series.get("time") or []
    if not times:
        return _skip(gid, f"{os.path.basename(result_path)} has no time samples")

    source = str(ctx.param("modelica_mirror_source", "") or "an independent implementation")
    rows: list[str] = [f"model  : {result_path}", f"mirror : {mirror_path}",
                       f"mirror is: {source}", ""]
    worst_ratio = 0.0
    worst_line = ""
    worst_variable = ""
    worst_rel = 0.0
    compared = 0
    refused: list[str] = []

    for name, (rel_tol, abs_tol) in tolerances.items():
        mirror_values = mirror.get(name) or []
        mirror_times = mirror.get("time") or []
        model_values = series.get(name) or []
        variable_worst = 0.0
        variable_line = ""
        for sample, when in enumerate(mirror_times):
            expected = mirror_values[sample] if sample < len(mirror_values) else math.nan
            if math.isnan(expected) or math.isnan(when):
                refused.append(f"{name}@{sample}: mirror value is not a number")
                continue
            try:
                actual = M.interpolate(times, model_values, when)
            except M.ModelicaError as exc:
                refused.append(f"{name}@t={when:g}: {exc}")
                continue
            if math.isnan(actual) or math.isinf(actual):
                refused.append(f"{name}@t={when:g}: the model result is {actual}")
                continue
            compared += 1
            allowed = abs_tol + rel_tol * abs(expected)
            error = abs(actual - expected)
            ratio = error / allowed if allowed > 0 else (math.inf if error else 0.0)
            relative = error / abs(expected) if expected else float("nan")
            if ratio > variable_worst:
                variable_worst = ratio
                variable_line = (f"  {name:<16} t={when:<10g} model={actual:.8g} "
                                 f"mirror={expected:.8g} |d|={error:.4g} "
                                 f"allowed={allowed:.4g} ratio={ratio:.3f}")
            if ratio > worst_ratio:
                worst_ratio, worst_line, worst_variable, worst_rel = \
                    ratio, variable_line, name, relative
        rows.append(variable_line or f"  {name:<16} (no comparable samples)")

    if not compared:
        return _skip(gid, "no mirror sample could be compared: "
                          + (refused[0] if refused else "the mirror has no rows"))

    rows.append("")
    rows.append(f"compared {compared} sample(s) across {len(tolerances)} variable(s)")
    if refused:
        rows.append(f"refused {len(refused)}:")
        rows.extend(f"  - {r}" for r in refused[:20])
    evidence = _write(ctx, "modelica_mirror_agreement.txt", "\n".join(rows) + "\n")

    passed = worst_ratio <= 1.0 and not refused
    return Verdict(
        gate=gid, passed=passed, measured=round(worst_ratio, 6), limit=1.0,
        units="x tolerance",
        detail=(f"{compared} sample(s), {len(tolerances)} variable(s) vs {source}; "
                f"worst {worst_variable or '-'} at {worst_ratio * 100:.1f}% of its "
                f"tolerance (relative error {worst_rel:.2e})"
                + (f"; {len(refused)} sample(s) refused: {refused[0]}" if refused else "")),
        evidence=evidence,
    )


# =========================================================================== #
# tier 2 — omc
# =========================================================================== #
def _omc_setup(ctx: GateContext, gid: str):
    """``(class, sources, libraries, timeout)`` or a SKIP verdict."""
    values, missing = _need(ctx, "modelica_class", "modelica_sources")
    if missing:
        return _skip_missing(gid, missing,
                             "the class to run and the .mo files that define it")
    class_name = str(values["modelica_class"]).strip()
    entries = [str(e) for e in _as_list(values["modelica_sources"])]
    sources = M.gather_mo_files(entries, ctx.root)
    if not sources:
        return _skip(gid, f"modelica_sources names no readable .mo file "
                          f"(looked at {', '.join(entries[:3])} under {ctx.root or '.'})")
    libraries = [str(x) for x in _as_list(ctx.param("modelica_load_libraries", []))]
    return class_name, sources, libraries


def _load_failed(run: M.OmcRun) -> str:
    """Why the sources did not load, or "". Checked before every tier-2 verdict."""
    loaded = run.segment("load")
    if "= false" in loaded:
        failed = [line for line in loaded.splitlines() if "= false" in line]
        return "; ".join(failed[:3])
    errors = run.segment("loaderr")
    if M.error_is_real(errors):
        return M.first_errors(errors)
    return ""


def _run_or_skip(ctx: GateContext, gid: str, lines, name: str, timeout: float):
    """Run omc; return the OmcRun, or a SKIP verdict when nothing was learned."""
    run = M.run_mos(lines, ctx.out_path("omc"), name, timeout_s=timeout)
    if run.launch_error:
        return _skip(gid, f"could not launch omc: {run.launch_error}")
    if run.timed_out:
        return _skip(gid, f"omc exceeded the {timeout:g}s budget "
                          f"(modelica_omc_timeout_s) and was killed — nothing was "
                          f"proven; raise the budget or reduce the model")
    if not run.stdout.strip() and run.returncode != 0:
        return _skip(gid, f"omc exited {run.returncode} printing nothing: "
                          f"{(run.stderr or '').strip()[:160] or 'no stderr either'}")
    return run


@gate(
    id="modelica.checks",
    title="checkModel reports a balanced system: equations == unknowns",
    claims=["equation-balance", "model-structure", "equation-model"],
    tier=Tier.SOLVE,
    settles="equation/unknown balance",
    requires_tools=["omc"],
    negative_control=NegativeControl(
        fixture="selftest/bad_modelica.py:unbalanced_model",
        note="selftest/assets/bad/UnbalancedTank.mo — the pack's own tank with one "
             "extra variable declared and never given an equation. checkModel reports "
             "2 equation(s) and 3 variable(s) and this gate must fail on the "
             "inequality. Nothing else about the model changes: it is legal Modelica "
             "and physically sensible, which is exactly why this defect survives "
             "review",
    ),
)
def checks(ctx: GateContext) -> Verdict:
    """Drive ``omc`` with a .mos script running ``checkModel(<class>)``.

    The classic structural error in an acausal language has no analogue in an
    imperative one: you do not write an assignment, you write a relation, and the
    system is solvable only if the number of equations equals the number of
    unknowns. Get it wrong and there is nothing to see in the source. A component
    connected on three ports instead of four, a variable declared for
    documentation and never constrained, a ``connect`` left out of a loop — each
    of them is a file that reads correctly and a model that has no unique
    solution.

    omc reports it in one line, ``Class X has N equation(s) and M variable(s)``,
    and this gate parses that line and FAILS on ``N != M`` with both counts in
    the verdict. It also surfaces ``getErrorString()``, because checkModel
    reports type errors, missing classes and connect problems there and a
    balanced model with three errors against it has not been checked.

    ``measured`` is the equation count and ``limit`` the variable count, so a
    reader sees the two numbers that matter without opening the evidence file.
    """
    gid = "modelica.checks"
    setup = _omc_setup(ctx, gid)
    if isinstance(setup, Verdict):
        return setup
    class_name, sources, libraries = setup
    timeout = _as_float(ctx.param("modelica_omc_timeout_s", DEFAULT_OMC_TIMEOUT_S),
                        DEFAULT_OMC_TIMEOUT_S) or DEFAULT_OMC_TIMEOUT_S

    lines = M.mos_preamble(sources, libraries)
    lines.append(M.mos_mark("check"))
    lines.append(f'print(checkModel({class_name}) + "\\n");')
    lines.append(M.mos_mark("checkerr"))
    lines.append('print(getErrorString() + "\\n");')
    lines.append(M.mos_mark("end"))

    run = _run_or_skip(ctx, gid, lines, "check", timeout)
    if isinstance(run, Verdict):
        return run
    evidence = [p for p in (run.script_path, run.log_path) if p]

    load_problem = _load_failed(run)
    if load_problem:
        return Verdict(gate=gid, passed=False,
                       detail=f"the sources did not load, so nothing was checked: "
                              f"{load_problem}",
                       evidence=evidence)

    check_text = run.segment("check")
    errors = run.segment("checkerr")
    match = M.BALANCE_RE.search(check_text) or M.BALANCE_RE.search(run.stdout)
    if match is None:
        return Verdict(
            gate=gid, passed=False,
            detail=(f"checkModel({class_name}) reported no equation/variable balance: "
                    f"{M.first_errors(errors) or (check_text.strip()[:180] or 'no output')}"),
            evidence=evidence)

    equations, variables = int(match.group(1)), int(match.group(2))
    balanced = equations == variables
    dirty = M.error_is_real(errors)
    return Verdict(
        gate=gid, passed=balanced and not dirty,
        measured=equations, limit=variables, units="equations",
        detail=(f"checkModel({class_name}): {equations} equation(s), "
                f"{variables} variable(s)"
                + ("" if balanced else
                   f" — {abs(equations - variables)} "
                   f"{'unknown' if variables > equations else 'equation'}(s) too many, "
                   f"the system has no unique solution")
                + (f"; errors: {M.first_errors(errors)}" if dirty else "")),
        evidence=evidence)


@gate(
    id="modelica.compiles",
    title="buildModel produces an executable with no errors",
    claims=["model-compiles", "model-structure", "equation-model"],
    tier=Tier.SOLVE,
    settles="model compiles to an executable",
    requires_tools=["omc"],
    negative_control=NegativeControl(
        fixture="selftest/bad_modelica.py:will_not_compile",
        note="selftest/assets/bad/WillNotCompile.mo — the pack's own tank routing its "
             "wall loss through wallLoss(), a function defined nowhere. The file "
             "parses, the balance is right and the physics is right; the frontend "
             "cannot instantiate the class and buildModel returns no executable. "
             "This is what a helper renamed out from under one caller looks like",
    ),
)
def compiles(ctx: GateContext) -> Verdict:
    """``buildModel(<class>)`` succeeds and leaves an executable behind.

    A balanced model is not a compilable one. Between checkModel and an
    executable sit the whole of flattening, alias elimination, index reduction,
    matching and the C code generation, and each of them has failure modes that
    checkModel cannot see: a structurally singular system, a higher-index DAE the
    reduction cannot handle, an initialisation system with no solution, a
    function whose external body is missing.

    The gate asserts three things and reports all of them: buildModel returned a
    non-empty executable path, the file is actually on disk, and
    ``getErrorString()`` carries no error afterwards. The third matters because
    omc will cheerfully build a model it has also complained about, and the
    complaint is usually the interesting half.

    Note what this does NOT settle. A clean compile is not a working model — it
    says the equations can be turned into code, nothing whatsoever about whether
    that code integrates, initialises or produces physically sensible numbers.
    That is ``modelica.simulates``, and then ``modelica.solution_valid``.
    """
    gid = "modelica.compiles"
    setup = _omc_setup(ctx, gid)
    if isinstance(setup, Verdict):
        return setup
    class_name, sources, libraries = setup
    timeout = _as_float(ctx.param("modelica_omc_timeout_s", DEFAULT_OMC_TIMEOUT_S),
                        DEFAULT_OMC_TIMEOUT_S) or DEFAULT_OMC_TIMEOUT_S

    lines = M.mos_preamble(sources, libraries)
    lines.append(M.mos_mark("build"))
    lines.append(f"built := buildModel({class_name});")
    lines.append('print(built[1] + "\\n");')
    lines.append(M.mos_mark("builderr"))
    lines.append('print(getErrorString() + "\\n");')
    lines.append(M.mos_mark("end"))

    run = _run_or_skip(ctx, gid, lines, "build", timeout)
    if isinstance(run, Verdict):
        return run
    evidence = [p for p in (run.script_path, run.log_path) if p]

    load_problem = _load_failed(run)
    if load_problem:
        return Verdict(gate=gid, passed=False,
                       detail=f"the sources did not load, so nothing was built: "
                              f"{load_problem}",
                       evidence=evidence)

    executable = M.printed_value(run.segment("build"))
    # buildModel's first element is the executable, and whether it comes back
    # ABSOLUTE or RELATIVE depends on how omc was reached: a plain binary in the
    # work directory returns an absolute path, a containerised or wrapped one can
    # return the bare name it wrote next to the script. Resolving it the same way
    # `modelica.simulates` resolves its result file is not defensive tidying — with
    # the two handled differently, a real omc had `simulates` passing and `compiles`
    # reporting "named X but it is not on disk" about a 61 kB executable sitting in
    # the work directory. That asymmetry was invisible while omc was absent, because
    # both gates simply skipped.
    if executable and not os.path.isabs(executable):
        executable = os.path.join(ctx.out_path("omc"), executable)
    errors = run.segment("builderr")
    dirty = M.error_is_real(errors)
    on_disk = bool(executable) and os.path.exists(executable)
    passed = bool(executable) and on_disk and not dirty

    if not executable:
        why = f"buildModel({class_name}) returned no executable"
    elif not on_disk:
        why = f"buildModel named {os.path.basename(executable)} but it is not on disk"
    elif dirty:
        why = f"built, but omc reported errors"
    else:
        why = f"built {os.path.basename(executable)}"
    return Verdict(
        gate=gid, passed=passed,
        # No `in {run.duration_s:.1f}s` (D-29, S-34): the detail is part of the bytes
        # a cache entry is written as, and a wall-clock figure made two runs on
        # identical inputs two different entries — "in 0.9s" and "in 0.8s" — an
        # add/add conflict on every branch that ran it. `duration_s` carries the time.
        detail=(why + (f"; {M.first_errors(errors)}" if dirty else "")),
        evidence=evidence)


@gate(
    id="modelica.simulates",
    title="simulate() runs to stopTime, fires no assert, and writes a result",
    claims=["simulation-runs", "simulation-result", "equation-model"],
    tier=Tier.SOLVE,
    settles="simulation runs to completion",
    requires_tools=["omc"],
    negative_control=NegativeControl(
        fixture="selftest/bad_modelica.py:assert_fires",
        note="selftest/assets/bad/AssertFires.mo — the pack's own tank with a ceiling "
             "43 K below its own steady state, so the assert fires at t=738 s of a "
             "25000 s run. It compiles, it checks balanced, it initialises, and it "
             "stops a thirtieth of the way in having written a result file full of "
             "perfectly good numbers from a run that did not happen",
    ),
)
def simulates(ctx: GateContext) -> Verdict:
    """``simulate(<class>)`` reaches stopTime, exits cleanly, and leaves a result.

    Three separate things, and a pipeline that conflates them ships numbers from
    a run that stopped. omc's ``simulate()`` returns a record; this gate reads
    ``resultFile`` and ``messages`` out of it, then reads the result file's own
    final time and reports the end time ACTUALLY reached — not the one that was
    requested, and not the one the record's options echo back.

    An assert that fires is the case worth naming. The process exits non-zero,
    the messages carry the assertion text, and a result file IS written — full of
    real numbers, ending wherever the assert caught it. Any pipeline that checks
    for the file's existence rather than its final time will read a number off it
    and be wrong in a way that survives review.

    Settings are constants and carry their provenance: ``modelica_tolerance``
    (default omc's own 1e-6), ``modelica_number_of_intervals`` (how many samples
    the result file gets — a resolution choice, not an accuracy one; the solver
    steps where it needs to and interpolates onto this grid), and
    ``modelica_simulate_timeout_s``. Change one and say why, in the model's
    provenance, exactly as for a material property.
    """
    gid = "modelica.simulates"
    setup = _omc_setup(ctx, gid)
    if isinstance(setup, Verdict):
        return setup
    class_name, sources, libraries = setup

    values, missing = _need(ctx, "modelica_stop_time_s")
    if missing:
        return _skip_missing(gid, missing, "the stopTime the run must reach")
    stop_time = _as_float(values["modelica_stop_time_s"])
    if stop_time is None:
        return _skip(gid, f"modelica_stop_time_s is "
                          f"{values['modelica_stop_time_s']!r}, not a number")
    start_time = _as_float(ctx.param("modelica_start_time_s", 0.0), 0.0) or 0.0
    intervals = int(_as_float(ctx.param("modelica_number_of_intervals", 500), 500) or 500)
    tolerance = _as_float(ctx.param("modelica_tolerance", 1e-6), 1e-6) or 1e-6
    timeout = _as_float(ctx.param("modelica_simulate_timeout_s",
                                  DEFAULT_SIMULATE_TIMEOUT_S),
                        DEFAULT_SIMULATE_TIMEOUT_S) or DEFAULT_SIMULATE_TIMEOUT_S
    tol_frac = _as_float(ctx.param("modelica_stop_time_tol_frac",
                                   DEFAULT_STOP_TIME_TOL_FRAC),
                         DEFAULT_STOP_TIME_TOL_FRAC) or DEFAULT_STOP_TIME_TOL_FRAC

    lines = M.mos_preamble(sources, libraries)
    lines.append(M.mos_mark("simulate"))
    lines.append(f"simulate({class_name}, startTime={start_time!r}, "
                 f"stopTime={stop_time!r}, numberOfIntervals={intervals}, "
                 f"tolerance={tolerance!r}, outputFormat=\"csv\");")
    lines.append(M.mos_mark("simerr"))
    lines.append('print(getErrorString() + "\\n");')
    lines.append(M.mos_mark("end"))

    run = _run_or_skip(ctx, gid, lines, "simulate", timeout)
    if isinstance(run, Verdict):
        return run
    evidence = [p for p in (run.script_path, run.log_path) if p]

    load_problem = _load_failed(run)
    if load_problem:
        return Verdict(gate=gid, passed=False,
                       detail=f"the sources did not load, so nothing was simulated: "
                              f"{load_problem}",
                       evidence=evidence)

    echoed = run.segment("simulate")
    file_match = M.RESULT_FILE_RE.search(echoed) or M.RESULT_FILE_RE.search(run.stdout)
    message_match = M.MESSAGES_RE.search(echoed) or M.MESSAGES_RE.search(run.stdout)
    messages = M.unescape(message_match.group(1)) if message_match else ""
    errors = run.segment("simerr")

    result_path = file_match.group(1) if file_match else ""
    if result_path and not os.path.isabs(result_path):
        result_path = os.path.join(ctx.out_path("omc"), result_path)

    failures: list[str] = []
    if not result_path:
        failures.append(f"simulate({class_name}) named no result file")
    elif not os.path.isfile(result_path):
        failures.append(f"simulate named {os.path.basename(result_path)} but it is "
                        f"not on disk")
    lowered = (messages + "\n" + errors).lower()
    tripped = [marker for marker in M.FAILURE_MARKERS if marker in lowered]
    if tripped:
        failures.append(f"the run reported {tripped[0]!r}: "
                        f"{M.first_errors(messages or errors)}")

    reached = None
    if result_path and os.path.isfile(result_path):
        try:
            scan = M.result_scan(result_path)
            reached = scan.last_time
            evidence.append(result_path)
        except M.ModelicaError as exc:
            failures.append(f"the result file is unreadable: {exc}")
    if reached is None:
        failures.append("no end time could be read from the result")
    else:
        span = abs(stop_time - start_time)
        if stop_time - reached > max(span * tol_frac, 0.0):
            fraction = (reached - start_time) / span if span else 0.0
            failures.append(f"stopped at t={reached:g} s, {fraction * 100:.1f}% of the "
                            f"requested {stop_time:g} s")
    if run.returncode != 0 and not failures:
        failures.append(f"omc exited {run.returncode} although the run looks complete")

    return Verdict(
        gate=gid, passed=not failures,
        measured=(None if reached is None else round(float(reached), 9)),
        limit=round(stop_time, 9), units="s",
        # No run time here either (D-29): see `compiles`.
        detail=(f"simulate({class_name}) reached t="
                f"{'?' if reached is None else f'{reached:g}'}/{stop_time:g} s"
                + (f"; " + "; ".join(failures[:2]) if failures else "; clean")),
        evidence=evidence)
