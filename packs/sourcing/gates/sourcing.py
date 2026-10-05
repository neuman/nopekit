# SPDX-License-Identifier: Apache-2.0
"""Gates for the sourcing pack. Arithmetic over a project-supplied BOM, tier 0.

Nothing here touches a network. A gate that asks a distributor for today's stock
is not repeatable, not offline, and not a gate — it is a quote with a timestamp.
These gates read a document the project maintains and say whether it adds up.

Every gate reads its numbers from the BOM the project supplied (via
``ctx.params['bom_path']`` or ``ctx.extra``) and its thresholds from that document
or the model projection. Where a needed key is absent the gate SKIPs and NAMES the
key: a missing budget is an unknown, and an unknown reported as a pass is the
failure this whole system exists to prevent.

**Bind each gate to the quantity it measures.** Narrow tags, so a lead-time
problem does not read as a cost problem. The one exception is ``bom.complete``,
which is this pack's validity gate: when the BOM has holes in it, every other
number below is arithmetic on fiction, so it binds to the whole domain on purpose.

Fixtures live in ``../selftest/bad_boms.py``; each changes ONE commercially
meaningful thing, in the direction that gate cares about, on a copy of this pack's
own ``selftest/baseline.json`` — sealed, so the control is the same control in
every project — and changes it far enough past the threshold, read out of that same
baseline, to survive somebody moving the threshold.
"""
from __future__ import annotations

import os
import sys

from nopekit.gates import gate, GateContext
from nopekit.models import Locator, NegativeControl, Tier, Verdict

_PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACK_DIR not in sys.path:                    # also true under load_gates; harmless
    sys.path.insert(0, _PACK_DIR)

import bomlib  # noqa: E402

# Domain default thresholds live in bomlib beside the reader, so the gates and the
# negative controls reason about the same numbers (rule 2). Every one of them is
# overridable per project from the BOM document or the model projection, and every
# one states, at its definition, why that value and what was rejected.

#: How many offending lines get a pin before a gate stops drawing them. A BOM is a
#: table and a table is read row by row: past a dozen highlights the eye stops
#: picking them out and starts reading the table again, which is what the pins were
#: for. The counts in `measured` and `detail` are always the real ones — the cap
#: trims the drawing, never the measurement.
_MAX_LOCATORS = 12


def _skip(gate_id: str, reason: str) -> Verdict:
    """A visible non-answer. Never a pass — the claim goes BLOCKED, on purpose."""
    return Verdict(gate=gate_id, passed=False, skipped=True, skip_reason=reason)


def _row(ref: str, label: str, value: float | None = None, severity: str = "") -> Locator:
    """One locator onto a ROW of the BOM table.

    A BOM line has no geometry and no position, and inventing one would be the
    failure the contract names: a confident highlight somewhere nobody measured.
    What it has is an identity — the `ref` the buyer types on the purchase order —
    and that is what the table publishes as its row id and what these gates target.
    """
    return Locator(view=bomlib.SITE_VIEW_ID, target=str(ref), kind="row",
                   label=label, severity=severity,
                   value=(float(value) if isinstance(value, (int, float))
                          and not isinstance(value, bool) else None))


def _load(ctx: GateContext, gate_id: str):
    """``(doc, None)`` or ``(None, skip verdict)``. Rule 7, in two lines."""
    try:
        return bomlib.resolve(ctx), None
    except bomlib.BomUnavailable as exc:
        return None, _skip(gate_id, str(exc))


# --------------------------------------------------------------------------- #
@gate(
    id="bom.complete",
    title="Every BOM line can actually be ordered",
    # DELIBERATELY BROAD, and the only gate here that is. It does not measure a
    # commercial quantity; it decides whether the other five mean anything. A BOM
    # with an unpriced line makes the cost gate a work of fiction, so when this
    # trips it should drag the whole domain with it.
    claims=["sourcing", "procurement", "bom", "cost", "availability", "supply-chain"],
    tier=Tier.INSTANT,
    settles="bill of materials completeness",
    negative_control=NegativeControl(
        fixture="selftest/bad_boms.py:unpriced_line",
        # the known-good control on the channel this fixture uses
        # (ctx.extra): selftest/good_boms.py says why (P2.3-D5)
        good="selftest/good_boms.py:baseline_bom",
        note="one line's unit_price blanked out — the single most common real BOM "
             "defect. Nothing else about the part changes, and the roll-up that "
             "silently treats it as free is exactly what this gate refuses",
    ),
)
def complete(ctx: GateContext) -> Verdict:
    """Vendor part number, quantity and a usable price on every line.

    An unpriced line is an UNKNOWN, not a zero. A roll-up that adds it as zero
    reports a cheaper product than the one that exists, and the error is always in
    the same direction — the direction that gets the build approved.

    A price quoted in a currency the document cannot convert counts as unpriced
    too: summing mixed currencies produces a number, and the number is wrong.

    It is also this pack's input-validity guard, so it refuses the fields every
    other gate's arithmetic consumes: a line nobody can be ordered FROM, and a
    spares fraction outside [0, 1]. A negative spares fraction is a one-character
    typo that silently halves a line's requirement, and it fails in the direction
    every error in a BOM fails in — the one that gets the build approved.
    """
    doc, skipped = _load(ctx, "bom.complete")
    if skipped:
        return skipped

    rows = bomlib.lines(doc)
    if not rows:
        return Verdict(gate="bom.complete", passed=False, measured=0.0, limit=0.0, comparator="<=",
                       units="lines", detail="the BOM has no lines — nothing to buy "
                                             "and nothing proven")

    problems: list[dict[str, str]] = []
    for i, line in enumerate(rows):
        ref = bomlib.ref(line, i)
        missing: list[str] = []
        if not str(line.get("vendor_pn") or line.get("mpn") or "").strip():
            missing.append("no vendor_pn/mpn")
        # A part number with nobody to buy it from is the definition of not
        # orderable, which is this gate's title. It is not cosmetic either: the
        # roll-up files such a line under "(no vendor)", where it can never match
        # an order minimum, and bom.single_source reports its supplier as "(none)".
        if not bomlib.sources_of(line):
            missing.append("no vendor and no sources")
        missing.extend(bomlib.quantity_problems(line))
        price, why = bomlib.unit_price(line, doc)
        if price is None:
            missing.append(why)
        if missing:
            problems.append({"ref": ref, "vendor": str(line.get("vendor") or ""),
                             "problems": "; ".join(missing)})

    evidence = bomlib.write_evidence(
        ctx, "bom_complete.json",
        {"lines": len(rows), "incomplete": problems, "source": doc.get("_source", "")})
    worst = ", ".join(p["ref"] for p in problems[:4]) + ("..." if len(problems) > 4 else "")
    return Verdict(
        gate="bom.complete", passed=not problems,
        measured=float(len(problems)), limit=0.0, comparator="<=", units="lines",
        detail=f"{len(problems)} of {len(rows)} lines not orderable (limit 0)"
               + (f": {worst}" if problems else " — every line has a part number, "
                                                "a quantity and a usable price"),
        evidence=evidence,
    )


# --------------------------------------------------------------------------- #
@gate(
    id="bom.cost",
    title="Rolled-up build cost within budget",
    claims=["cost", "build-cost", "budget", "unit-cost"],
    tier=Tier.INSTANT,
    settles="build cost per unit",
    # Prerequisite bom.complete (P2.2-D12): it is the input-validity guard —
    #    an unpriced or unorderable line, or a spares fraction outside [0, 1], makes
    #    this gate's arithmetic over the BOM a work of fiction.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["bom.complete"],
    negative_control=NegativeControl(
        fixture="selftest/bad_boms.py:price_shock",
        # the known-good control on the channel this fixture uses
        # (ctx.extra): selftest/good_boms.py says why (P2.3-D5)
        good="selftest/good_boms.py:baseline_bom",
        note="one line repriced so that part alone costs ten times the whole per-unit "
             "budget — the re-quote that arrives the week you order. One number "
             "moves; quantities, vendors and charges are untouched",
    ),
)
def cost(ctx: GateContext) -> Verdict:
    """Cost of the run at the chosen build quantity, per unit, against the budget.

    Counts what the invoice will count: purchase quantities rather than needs
    (MOQ and order multiples are bought, not needed), per-order minimums, and
    one-time tooling/setup charges amortised across the run. That last term is why
    a per-unit price quoted at one quantity is meaningless at another, and why the
    gate names the quantity it used in the verdict.

    Refuses to answer at all while any line is unpriced: a total that treats an
    unknown as zero is not a cheaper build, it is a wrong number.

    Fails, separately from the budget, when an ``order_minimums`` key names a
    vendor no line buys from. Vendor names match case- and space-insensitively, so
    what is left is either a misspelling — in which case a real minimum is missing
    from this total — or a vendor somebody meant to order from and did not. Neither
    is a rounding error and both used to disappear silently, in the cheap direction.
    """
    doc, skipped = _load(ctx, "bom.cost")
    if skipped:
        return skipped
    try:
        roll = bomlib.rollup(doc, ctx)
    except bomlib.BomUnavailable as exc:
        return _skip("bom.cost", str(exc))

    if roll["unpriced"]:
        names = ", ".join(u["ref"] for u in roll["unpriced"][:5])
        return _skip("bom.cost",
                     f"{len(roll['unpriced'])} line(s) have no usable price ({names}) — "
                     f"the total would be a guess; see bom.complete")

    budget_unit = bomlib.setting(doc, ctx, "budget_per_unit")
    budget_total = bomlib.setting(doc, ctx, "budget_total")
    if budget_unit is None and budget_total is None:
        return _skip("bom.cost",
                     "no 'budget_per_unit' and no 'budget_total' in the BOM or the model "
                     "projection — cost without a budget is a number, not a verdict")

    cur = roll["currency"]
    qty = roll["build_quantity"]
    unmatched = roll.get("order_minimum_unmatched") or []
    if budget_unit is not None:
        measured, limit, units = roll["per_unit"], float(budget_unit), f"{cur}/unit".strip("/")
    else:
        measured, limit, units = roll["run_total"], float(budget_total), f"{cur}/run".strip("/")

    evidence = bomlib.write_evidence(ctx, "bom_cost.json", roll)
    against = (f"{bomlib.money(roll['per_unit'], cur)}/unit at qty {qty} vs "
               f"{bomlib.money(limit, cur)} budget"
               if budget_unit is not None else
               f"{bomlib.money(roll['run_total'], cur)} for the run of {qty} vs "
               f"{bomlib.money(limit, cur)} budget")
    detail = (f"{against}; parts {bomlib.money(roll['parts_total'], cur)} + "
              f"minimums {bomlib.money(roll['order_minimum_total'], cur)} + "
              f"one-time {bomlib.money(roll['charge_total'], cur)} "
              f"({bomlib.money(roll['nre_per_unit'], cur)}/unit amortised) = "
              f"{bomlib.money(roll['run_total'], cur)} run")
    if unmatched:
        named = ", ".join(
            f"{u['vendor']!r} ("
            + (bomlib.money(float(u["minimum"]), cur)
               if isinstance(u.get("minimum"), (int, float)) else f"{u['minimum']!r}")
            + f": {u['why']})"
            for u in unmatched[:3])
        detail += (f"; {len(unmatched)} order_minimums entry(s) placed on nothing: {named}")
    return Verdict(
        gate="bom.cost", passed=measured <= limit and not unmatched,
        measured=round(measured, 2), limit=round(limit, 2), comparator="<=", units=units,
        detail=detail, evidence=evidence,
    )


# --------------------------------------------------------------------------- #
@gate(
    id="bom.availability",
    title="Every line has a known ship date, and nothing is dead",
    claims=["availability", "lead-time", "ship-date", "stock"],
    tier=Tier.INSTANT,
    settles="longest lead time",
    # No edge to bom.complete (review of P2.2: P2.2-D12's first test, "every
    #    fail of the prerequisite means the number does not apply", does not
    #    hold): a lead time, a lifecycle and a stock figure do not depend on
    #    another line's price, and bom.complete fails on any unpriced line — so
    #    with the edge one blank price cell turned a real end-of-life FAIL into
    #    a skip naming the wrong root, in every channel, until the unrelated
    #    price was filled in. The one input this gate shares with the guard is
    #    a line's quantity (stock covers a need), and the body refuses that
    #    itself: a line whose quantity cannot be read is never covered by stock.
    #    The edge can land once bom.complete's quantity half is its own guard.
    negative_control=NegativeControl(
        fixture="selftest/bad_boms.py:end_of_life",
        # the known-good control on the channel this fixture uses
        # (ctx.extra): selftest/good_boms.py says why (P2.3-D5)
        good="selftest/good_boms.py:baseline_bom",
        note="one line's lifecycle moved to 'eol' — the notice that arrives by email "
             "and gets filed. Price, quantity and vendor are unchanged; only the "
             "part's future is",
    ),
)
def availability(ctx: GateContext) -> Verdict:
    """The LONGEST lead time, because that is the ship date. Plus what is dying.

    Three separate ways a line has no known ship date, and all three fail:
    no stock figure and no lead time (nobody knows), stock below what the order
    needs with no lead time for the rest, and a lifecycle that says the part is
    ending. NRND, allocation and last-time-buy are reported rather than failed —
    they are buy-able today and gone later, which is a schedule problem, not a
    stop — but they are counted in the verdict so nobody has to feel them.

    Lead time is reported in weeks and a line covered by stock counts as zero:
    a 26-week factory lead on a part sitting in a distributor's warehouse is not
    your lead time until someone else buys it first.

    Without a ``lead_time_budget_weeks`` this gate cannot say whether the longest
    lead fits, so it does not pretend to: the two halves separate. A line with no
    ship date or a dead lifecycle FAILS with or without a budget — those are
    absolute, nothing needs comparing. Everything else, with no budget, SKIPs with
    the key named, exactly as ``bom.cost`` does for a missing budget. The gate used
    to PASS a 52-week lead on a project that had simply never written a schedule
    down, which is a readiness report starting to lie.
    """
    doc, skipped = _load(ctx, "bom.availability")
    if skipped:
        return skipped
    try:
        qty = bomlib.build_quantity(doc, ctx)
    except bomlib.BomUnavailable as exc:
        return _skip("bom.availability", str(exc))

    unknown: list[str] = []
    # The same lines again as (ref, why) pairs, for the locators. Kept beside
    # `unknown` rather than parsed back out of its formatted strings: a part number
    # with a bracket in it would turn a locator into a target that matches no row,
    # and a locator that matches nothing is silent by construction.
    unknown_rows: list[tuple[str, str]] = []
    dead: list[str] = []
    warned: list[str] = []
    longest: float | None = None
    longest_ref = "-"
    rows = []

    for i, line in enumerate(bomlib.lines(doc)):
        ref = bomlib.ref(line, i)
        buy = bomlib.purchase_qty(line, qty)
        # Both of these arrive as text from real documents ("ARO 8 weeks", "call").
        # Uncoercible is UNKNOWN and named, never a crash and never a zero.
        stock, stock_problem = bomlib.stock_qty(line)
        lead, lead_problem = bomlib.lead_weeks(line)
        lifecycle = str(line.get("lifecycle") or "unknown").strip().lower()
        # A line whose quantity cannot be read is never covered by stock: with
        # no usable need, purchase_qty reads 0 and any shelf "covers" it. This
        # was bom.complete's to refuse while it was this gate's prerequisite;
        # the edge is gone (see the decorator), so the gate refuses it itself,
        # in the direction that leaves a ship date unknown, never known.
        qty_problems = bomlib.quantity_problems(line)
        covered = not qty_problems and stock is not None and stock >= buy
        if qty_problems and stock is not None and not stock_problem:
            stock_problem = (f"stock {stock:g} against an unknown quantity "
                             f"({'; '.join(qty_problems)})")

        if lifecycle in bomlib.DEAD:
            dead.append(ref)
        elif lifecycle in bomlib.WARN:
            warned.append(f"{ref}:{lifecycle}")

        if covered:
            effective = 0.0
        elif lead is None:
            effective = None
            why = lead_problem or "no lead_time_weeks"
            if stock_problem:
                why = f"{stock_problem}, {why}"
            elif stock is not None:
                why = f"stock {stock:g} < {buy:g}, {why}"
            unknown.append(f"{ref}({why})")
            unknown_rows.append((ref, why))
        else:
            effective = lead

        if effective is not None and (longest is None or effective > longest):
            longest, longest_ref = effective, ref
        rows.append({"ref": ref, "purchase_qty": buy, "stock": line.get("stock"),
                     "lead_time_weeks": line.get("lead_time_weeks"),
                     "effective_weeks": effective, "lifecycle": lifecycle})

    budget = bomlib.num(bomlib.setting(doc, ctx, "lead_time_budget_weeks"))
    if isinstance(budget, bool):
        budget = None
    over = budget is not None and longest is not None and longest > float(budget)
    evidence = bomlib.write_evidence(
        ctx, "bom_availability.json",
        {"lines": rows, "unknown": unknown, "eol": dead, "flagged": warned,
         "longest_weeks": longest, "longest_ref": longest_ref,
         "lead_time_budget_weeks": budget})

    # A missing budget cannot turn a dead part into a good one, so the absolute
    # half of this gate answers first and the comparative half skips after it.
    if not unknown and not dead and budget is None:
        return _skip("bom.availability",
                     "no 'lead_time_budget_weeks' in the BOM or the model projection — "
                     f"every line has a ship date and nothing is EOL, but the longest lead "
                     f"({'unknown' if longest is None else f'{longest:g} wk'}) fits a "
                     f"schedule nobody has written down, so there is nothing to compare it "
                     f"against and this gate settles nothing")

    # Pin the rows that are actually the problem, in the order somebody has to deal
    # with them: dead parts first (a redesign, not a phone call), then the lines
    # with no ship date, then — only when the schedule is actually blown — the one
    # line that sets the longest lead. That last one is the ONLY line worth pinning
    # for a lead-time overrun: every other line ships sooner and moving any of them
    # changes nothing, so highlighting them would be twenty pins pointing at parts
    # nobody needs to chase.
    locators: list[Locator] = []
    by_ref = {r["ref"]: r for r in rows}
    for ref in dead:
        row = by_ref.get(ref, {})
        locators.append(_row(ref, f"lifecycle {row.get('lifecycle', 'eol')} — this part "
                                  f"has a last order date, not a lead time"))
    for ref, why in unknown_rows:
        locators.append(_row(ref, f"no ship date: {why}"))
    if over and longest is not None:
        locators.append(_row(longest_ref,
                             f"{longest:g} wk lead sets the ship date, vs a "
                             f"{float(budget):g} wk budget",
                             value=longest))

    bits = [f"longest lead {'unknown' if longest is None else f'{longest:g}'} wk ({longest_ref})"]
    bits.append(f"vs {float(budget):g} wk budget" if budget is not None
                else "no lead-time budget set (nothing to compare against)")
    bits.append(f"{len(unknown)} unknown, {len(dead)} EOL, {len(warned)} flagged")
    if dead:
        bits.append("EOL: " + ", ".join(dead[:4]))
    if unknown:
        bits.append("no ship date: " + ", ".join(unknown[:4]))
    if warned:
        bits.append("watch: " + ", ".join(warned[:4]))

    return Verdict(
        gate="bom.availability",
        passed=not unknown and not dead and not over,
        # None, not 0.0: every line's lead being unknown is not "no wait".
        measured=(None if longest is None else round(longest, 2)),
        limit=(round(float(budget), 2) if budget is not None else None),
        # The lead-time reading's side; a fail for an EOL or undated line is
        # another fact, and its margin then says the side does not explain it
        # (`disagrees`: the verdict wins, D-17).
        comparator="<=",
        units="weeks", detail="; ".join(bits), evidence=evidence,
        locators=locators[:_MAX_LOCATORS],
    )


# --------------------------------------------------------------------------- #
@gate(
    id="bom.moq",
    title="Minimum order quantities not buying a second build",
    claims=["moq", "minimum-order", "overbuy", "inventory"],
    tier=Tier.INSTANT,
    settles="minimum order quantity overbuy",
    # Prerequisite bom.complete (P2.2-D12): it is the input-validity guard —
    #    an unpriced or unorderable line, or a spares fraction outside [0, 1], makes
    #    this gate's arithmetic over the BOM a work of fiction.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["bom.complete"],
    negative_control=NegativeControl(
        fixture="selftest/bad_boms.py:brutal_moq",
        # the known-good control on the channel this fixture uses
        # (ctx.extra): selftest/good_boms.py says why (P2.3-D5)
        good="selftest/good_boms.py:baseline_bom",
        note="one line's MOQ raised far past what the build needs, on the line whose "
             "price makes the overbuy real money. The part, the price, the order "
             "multiple and the design are unchanged — only the smallest quantity the "
             "vendor will sell. The raise is computed from the limits it has to beat, "
             "read out of the same document the gate reads them from, so it goes on "
             "tripping when a project moves a threshold",
    ),
)
def moq(ctx: GateContext) -> Verdict:
    """Money tied up in pieces this run will not consume. Every line, not some.

    The measured number is IDLE CAPITAL: for each line, (bought - needed) x price,
    summed over the whole BOM. That is the figure a buyer recognises — what the
    order spends on stock the build does not eat — and it is the figure this gate
    used to hide. It reported the worst single line *past a 3x purchase/need
    filter*, so money below the filter was invisible however large: on a document
    whose lines idled 1,800 it answered 30, and on a 2.5x overbuy of a six-dollar
    part it answered "worst excess 0.00" and passed. A filter applied before the
    money makes the error unbounded and always permissive.

    The limit is derived from the run's own parts spend (a tenth of it by default)
    because money has no absolute scale: the same currency figure is a reasonable
    wake-up on a small buy and an insult on a large one. A project that knows its
    own tolerance states ``moq_excess_total_limit`` and that wins.

    The ratio survives, in its proper place. ``moq_ratio_limit`` no longer decides
    what is measured; it selects the lines worth naming, and a line past it whose
    idle money also clears ``moq_excess_limit`` fails on its own — a single reel
    that ties up real money is a problem even on a BOM big enough to absorb it.
    Four times what you need of a four-cent label is thirty dollars and still
    nobody should be woken for it.

    Unpriced lines make this unanswerable rather than free, exactly as in
    ``bom.cost``: a 100x overbuy on a line with no price is not zero idle capital.
    """
    doc, skipped = _load(ctx, "bom.moq")
    if skipped:
        return skipped
    try:
        qty = bomlib.build_quantity(doc, ctx)
    except bomlib.BomUnavailable as exc:
        return _skip("bom.moq", str(exc))

    ratio_limit = float(bomlib.setting(doc, ctx, "moq_ratio_limit", bomlib.DEFAULT_MOQ_RATIO_LIMIT))
    excess_limit = float(bomlib.setting(doc, ctx, "moq_excess_limit", bomlib.DEFAULT_MOQ_EXCESS_LIMIT))

    rows: list[dict] = []
    unpriced_any: list[str] = []
    unpriced_overbuy: list[str] = []
    parts_total = 0.0
    total_idle = 0.0
    for i, line in enumerate(bomlib.lines(doc)):
        need = bomlib.needed_qty(line, qty)
        buy = bomlib.purchase_qty(line, qty)
        if need <= 0 or buy <= 0:
            continue
        ref = bomlib.ref(line, i)
        price, _why = bomlib.unit_price(line, doc)
        if price is None:
            unpriced_any.append(ref)
            if buy > need:
                unpriced_overbuy.append(f"{ref}({buy:g} bought for {need:g} needed)")
            continue
        parts_total += buy * price
        idle = (buy - need) * price
        total_idle += idle
        rows.append({"ref": ref, "needed": round(need, 2), "purchased": round(buy, 2),
                     "ratio": round(buy / need, 2), "unit_price": price,
                     "excess_pieces": round(buy - need, 2),
                     "excess_spend": round(idle, 2),
                     "past_ratio_filter": (buy / need) > ratio_limit,
                     "moq": line.get("moq"), "order_multiple": line.get("order_multiple")})

    limit, limit_from = bomlib.idle_capital_limit(doc, ctx, parts_total)
    if unpriced_overbuy:
        return _skip("bom.moq",
                     f"{len(unpriced_overbuy)} line(s) buy more than the build needs and "
                     f"carry no usable price ({', '.join(unpriced_overbuy[:4])}) — idle "
                     f"capital cannot be measured without a price, and answering 0.00 "
                     f"would be the same lie bom.cost refuses; see bom.complete")
    if unpriced_any and limit_from != "moq_excess_total_limit":
        return _skip("bom.moq",
                     f"{len(unpriced_any)} line(s) have no usable price "
                     f"({', '.join(unpriced_any[:4])}), so the parts spend this gate's "
                     f"limit is derived from is incomplete — set 'moq_excess_total_limit' "
                     f"to state the limit in money instead, or price the lines")

    cur = bomlib.document_currency(doc)[0]
    worst = max(rows, key=lambda r: r["excess_spend"], default=None)
    flagged = [r for r in rows if r["past_ratio_filter"]]
    worst_flagged = max(flagged, key=lambda r: r["excess_spend"], default=None)
    over_line = worst_flagged is not None and worst_flagged["excess_spend"] > excess_limit

    evidence = bomlib.write_evidence(
        ctx, "bom_moq.json",
        {"ratio_limit": ratio_limit, "excess_limit": excess_limit,
         "idle_capital_limit": round(limit, 2), "idle_capital_limit_from": limit_from,
         "parts_total": round(parts_total, 2), "idle_capital_total": round(total_idle, 2),
         "lines": sorted(rows, key=lambda r: -r["excess_spend"])})

    bits = [f"idle capital {bomlib.money(total_idle, cur)} vs "
            f"{bomlib.money(limit, cur)} ({limit_from})"]
    if worst is None or worst["excess_spend"] <= 0:
        bits.append("no line buys more than the build consumes")
    else:
        bits.append(f"worst {worst['ref']}: {worst['purchased']:g} bought for "
                    f"{worst['needed']:g} needed ({worst['ratio']:g}x) = "
                    f"{bomlib.money(float(worst['excess_spend']), cur)}")
    bits.append(f"{len(flagged)} line(s) past {ratio_limit:g}x")
    if worst_flagged is not None:
        bits.append(f"worst past the filter {worst_flagged['ref']} "
                    f"{bomlib.money(float(worst_flagged['excess_spend']), cur)} vs "
                    f"{bomlib.money(excess_limit, cur)} per-line limit"
                    + (" — OVER" if over_line else ""))
    return Verdict(
        gate="bom.moq", passed=total_idle <= limit and not over_line,
        measured=round(total_idle, 2), limit=round(limit, 2), comparator="<=",
        units=cur or "currency", detail="; ".join(bits), evidence=evidence,
    )


# --------------------------------------------------------------------------- #
@gate(
    id="bom.process_rules",
    title="Design inside the vendor's stated capability set",
    claims=["process-rules", "vendor-capability", "manufacturability", "dfm"],
    tier=Tier.INSTANT,
    settles="vendor process capability",
    # No edge to bom.complete (review of P2.2: P2.2-D12's first test does not
    #    hold): a vendor's capability set is checked against the design's
    #    declared attributes, none of which a price, a quantity or a part
    #    number moves — with the edge, one blank price cell hid a real
    #    capability violation behind a skip naming the wrong root.
    negative_control=NegativeControl(
        fixture="selftest/bad_boms.py:outside_capability",
        # the known-good control on the channel this fixture uses
        # (ctx.extra): selftest/good_boms.py says why (P2.3-D5)
        good="selftest/good_boms.py:baseline_bom",
        note="one declared process attribute moved outside the vendor's stated set — "
             "a finish the quoted line does not offer, or a tolerance ten times "
             "tighter than the process holds — written everywhere the document "
             "records that one fact, so a design-scoped rule and a line-scoped rule "
             "both trip from the single change and neither half of the rule engine "
             "goes unexercised. The BOM is otherwise untouched",
    ),
)
def process_rules(ctx: GateContext) -> Verdict:
    """The design checked against the capability rules the PROJECT supplied.

    This pack ships no rule list. Vendor capability is local, dated and
    negotiable — a hard-coded limit would be wrong for half the shops in the world
    and, far worse, would be believed. The project records what it was quoted; the
    gate checks the design against it and names the rule, the attribute and the
    reason when it does not hold.

    An attribute a rule requires and the design never declares counts as a
    violation. "We did not say" is precisely the state that gets discovered at the
    quote, by someone else, in a week you did not budget.
    """
    doc, skipped = _load(ctx, "bom.process_rules")
    if skipped:
        return skipped

    rules = doc.get("process_rules") or []
    if not rules:
        return _skip("bom.process_rules",
                     "no 'process_rules' in the BOM document — the vendor's capability set "
                     "is project data (ask for it in writing with the quote); nothing to "
                     "check the design against")

    violations = bomlib.evaluate_rules(doc)
    evidence = bomlib.write_evidence(
        ctx, "bom_process_rules.json",
        {"rules": len(rules), "violations": violations})
    first = "; ".join(f"{v['rule']}@{v['where']}.{v['attribute']} {v['problem']}"
                      for v in violations[:3])
    return Verdict(
        gate="bom.process_rules", passed=not violations,
        measured=float(len(violations)), limit=0.0, comparator="<=", units="violations",
        detail=f"{len(violations)} violation(s) of {len(rules)} project-supplied rule(s) "
               f"(limit 0)" + (f": {first}" if violations else " — design is inside every "
                                                              "stated capability"),
        evidence=evidence,
    )


# --------------------------------------------------------------------------- #
@gate(
    id="bom.single_source",
    title="Single-source risk counted, not felt",
    claims=["single-source", "supply-risk", "second-source", "supply-chain-risk"],
    tier=Tier.INSTANT,
    settles="single source count",
    # No edge to bom.complete (review of P2.2: P2.2-D12's first test does not
    #    hold): the count reads who makes and who sells each line, and an
    #    unpriced line, a missing quantity or a spares typo moves none of it —
    #    with the edge, one blank price cell hid an unrecorded single source
    #    behind a skip naming the wrong root. A line with no source at all is
    #    counted here as no second source (the conservative direction).
    negative_control=NegativeControl(
        fixture="selftest/bad_boms.py:lost_second_source",
        # the known-good control on the channel this fixture uses
        # (ctx.extra): selftest/good_boms.py says why (P2.3-D5)
        good="selftest/good_boms.py:baseline_bom",
        note="the second source goes away, with nothing written down — expressed on "
             "each line in the terms that line records: the lines that name a "
             "MANUFACTURER keep both distributors and lose the qualified alternate "
             "(two distributors of one factory's part is the case this gate exists "
             "for and the case a distributor count cannot see), the lines that name "
             "only suppliers drop to one. Prices, quantities and vendors are "
             "untouched. Enough lines to clear whatever single_source_limit the "
             "project set, so the control does not stop tripping when somebody "
             "raises it",
    ),
)
def single_source(ctx: GateContext) -> Verdict:
    """Lines behind ONE independent source, with no alternate and no acceptance.

    One *source*, counted from who MAKES the part wherever the line records it.
    Two distributors shipping the same factory's part is one source with better
    logistics: good against a warehouse fire, worthless against an EOL notice, a
    price move or an allocation. This gate used to count distributors, so a BOM
    with two parts from one semiconductor house behind two distributors each read
    as fully dual-sourced and passed — while the pack's own prose said, correctly,
    that a second distributor is not a second source. It counts manufacturers now,
    falls back to suppliers only on a line that records no manufacturer, and NAMES
    which basis it used, because "we did not write down who makes it" is not the
    same evidence as "two factories" and must not read like it.

    The number is not "how many single-source parts" — plenty of good designs have
    several and ship fine. It is how many are single-source and nobody has written
    down that they know. A line with ``single_source_accepted: true`` and an
    ``acceptance_note`` saying what the re-qualification would cost is a decision;
    the same line without one is a surprise with a date on it. ``alternate_qualified``
    is the other way to clear it: a second manufacturer's part actually qualified,
    which is what a second source means.

    Default limit is zero for that reason. Clearing this gate does not require
    finding another vendor; it requires somebody deciding, in writing.
    """
    doc, skipped = _load(ctx, "bom.single_source")
    if skipped:
        return skipped

    limit = float(bomlib.setting(doc, ctx, "single_source_limit", bomlib.DEFAULT_SINGLE_SOURCE_LIMIT))
    sole: list[dict] = []
    accepted: list[str] = []
    qualified: list[str] = []
    unrecorded: list[dict] = []
    unstated = 0
    for i, line in enumerate(bomlib.lines(doc)):
        count, basis, names = bomlib.source_breadth(line)
        if basis == "supplier":
            unstated += 1
        if count > 1:
            continue
        ref = bomlib.ref(line, i)
        suppliers = bomlib.sources_of(line)
        row = {"ref": ref, "source": names[0] if names else "(none)", "basis": basis,
               "suppliers": suppliers}
        sole.append(row)
        if bool(line.get("alternate_qualified")):
            qualified.append(ref)
            continue
        if bool(line.get("single_source_accepted")) and str(line.get("acceptance_note") or "").strip():
            accepted.append(ref)
            continue
        unrecorded.append({**row,
                           "why": "no qualified alternate and no written acceptance"
                                  if not line.get("single_source_accepted")
                                  else "accepted but no acceptance_note saying what it costs"})

    evidence = bomlib.write_evidence(
        ctx, "bom_single_source.json",
        {"single_source": sole, "accepted_in_writing": accepted,
         "covered_by_qualified_alternate": qualified, "unrecorded": unrecorded,
         "lines_with_no_manufacturer_recorded": unstated, "limit": limit})
    named = ", ".join(f"{u['ref']} ({u['basis']} {u['source']}"
                      + (f", stocked by {len(u['suppliers'])}" if len(u["suppliers"]) > 1 else "")
                      + ")" for u in unrecorded[:4])
    detail = (f"{len(unrecorded)} unrecorded single-source line(s) vs limit {limit:g} "
              f"({len(sole)} behind one source in total, {len(qualified)} with a qualified "
              f"alternate, {len(accepted)} accepted in writing)")
    if unrecorded:
        detail += f": {named}"
    if unstated:
        detail += (f"; {unstated} line(s) record no manufacturer, so their sources are "
                   f"counted by supplier — two distributors of one factory's part would "
                   f"read as two there")
    # The UNRECORDED lines only. A line behind one source with a qualified
    # alternate, or with a written acceptance, is a decision somebody made — it is
    # in the evidence file and it is not a finding. Pinning it would tell a reader
    # that the thing they already did is still outstanding, and the second time
    # that happens they stop reading the highlights.
    locators = [
        _row(u["ref"], f"single source ({u['basis']} {u['source']}) — {u['why']}")
        for u in unrecorded[:_MAX_LOCATORS]
    ]

    return Verdict(
        gate="bom.single_source", passed=float(len(unrecorded)) <= limit,
        measured=float(len(unrecorded)), limit=limit, comparator="<=", units="lines",
        detail=detail, evidence=evidence, locators=locators,
    )


# --------------------------------------------------------------------------- #
@gate(
    id="bom.currency",
    title="Every price is in a currency the roll-up can convert",
    claims=["currency", "fx", "price-currency", "cost"],
    tier=Tier.INSTANT,
    settles="price currency coherence",
    # No edge to bom.complete: this gate's own control fails it (1 of 10 lines
    # not orderable), so the guard would pre-empt the control.
    negative_control=NegativeControl(
        fixture="selftest/bad_boms.py:foreign_quote",
        # the known-good control on the channel this fixture uses
        # (ctx.extra): selftest/good_boms.py says why (P2.3-D5)
        good="selftest/good_boms.py:baseline_bom",
        note="one line re-quoted in a currency the document holds no fx rate for — "
             "the vendor who sends the second quote on their own price list. The "
             "part, the quantity, the vendor and the number itself are unchanged; "
             "only the currency beside the number is, which is precisely the change "
             "a roll-up cannot see and must refuse",
    ),
)
def currency(ctx: GateContext) -> Verdict:
    """Every quoted price reaches the document's currency, or it is not a price.

    Adding 950 JPY to 0.31 EUR to 14.80 USD produces a number. The number is
    wrong, it is wrong by two orders of magnitude, and nothing downstream of it
    looks odd — which is why this is a gate and not a comment. Three ways a
    document loses the ability to convert, and all three fail here:

    * a line quoted in a currency with no ``fx_rates`` entry,
    * an ``fx_rates`` entry that is not a usable positive number,
    * a line that names its ``price_currency`` in a document that declares no
      top-level ``currency`` at all — there is a conversion to do and nothing to
      convert into. That combination used to disable the check entirely and take
      every price at face value, in whatever currency it happened to be.

    ``fx_rates[CODE]`` is the multiplier FROM the quoted currency TO the document
    currency: units of the document's currency per one unit of the quoted one. A
    USD document holding EUR lines writes ``{"EUR": 1.08}``, never ``0.926``. This
    gate cannot catch a reversed rate — no other number in the document
    contradicts it — which is exactly why the direction is written here, in
    ``bomlib.unit_price``, in ``PACK.md`` and in the baseline's notes.
    """
    doc, skipped = _load(ctx, "bom.currency")
    if skipped:
        return skipped

    rows = bomlib.lines(doc)
    base, base_from = bomlib.document_currency(doc)
    quoted = {str(ln.get("price_currency") or "").strip().upper() for ln in rows}
    quoted.discard("")

    if not base and not quoted:
        return _skip("bom.currency",
                     "the document declares no 'currency' and no line names a "
                     "price_currency — every total this pack produces would be a number "
                     "with no unit on it, and guessing which one is how a cost report "
                     "starts lying")

    rates = doc.get("fx_rates") or {}
    bad: list[dict[str, str]] = []
    converted: list[str] = []
    for i, line in enumerate(rows):
        if line.get("unit_price") in (None, ""):
            continue                     # unpriced is bom.complete's business, not this
        code = str(line.get("price_currency") or "").strip().upper()
        rate, why = bomlib.price_conversion(line, doc)
        if why:
            bad.append({"ref": bomlib.ref(line, i), "why": why})
        elif code and base and code != base:
            converted.append(f"{bomlib.ref(line, i)}:{code} x {rate:g}")

    evidence = bomlib.write_evidence(
        ctx, "bom_currency.json",
        {"currency": base or None, "currency_from": base_from, "fx_rates": rates, "quoted_currencies": sorted(quoted),
         "unconvertible": bad, "converted": converted,
         "fx_direction": "fx_rates[CODE] multiplies a price quoted in CODE to reach "
                         "the document currency"})
    detail = (f"{len(bad)} of {len(rows)} line(s) priced in a currency this document "
              f"cannot convert (limit 0)")
    if bad:
        detail += ": " + "; ".join(f"{b['ref']} {b['why']}" for b in bad[:3])
    else:
        detail += (f" — base {base or '(none declared)'} ({base_from}), "
                   + (f"{len(converted)} line(s) converted at fx_rates "
                      f"({', '.join(converted[:4])})" if converted
                      else "every line quoted in the base currency"))
    return Verdict(
        gate="bom.currency", passed=not bad,
        measured=float(len(bad)), limit=0.0, comparator="<=", units="lines",
        detail=detail, evidence=evidence,
    )
