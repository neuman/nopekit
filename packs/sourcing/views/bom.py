# SPDX-License-Identifier: Apache-2.0
"""The bill of materials as a table, with the gates' findings landing on its rows.

A sourcing verdict names a part — ``2 unrecorded single-source line(s): U1
(manufacturer Wexmoor Semiconductor), J2 (...)`` — and a reader then goes looking
for U1 in a JSON file to find out what it costs, when it ships and who else makes
it. This view is that lookup, published once: every line with its money, its
schedule and its supply risk beside it, and ``bom.availability`` and
``bom.single_source`` pointing straight at the rows that are the problem.

Three things it does not do, and each is the same rule:

* **It does not price anything the gates did not price.** The roll-up comes from
  ``bomlib.rollup`` — the same function ``bom.cost`` reports — so the per-unit
  number on the page is the per-unit number in the verdict, and cannot drift from
  it. The site renders the ledger; it never computes a second opinion about it.
* **It does not fill a blank.** An unpriced line shows as unpriced, and its
  extended cost is ``null`` rather than zero. A zero would add up, look like money
  and make the total smaller — the direction every error in a BOM goes.
* **It does not sort.** Rows come out in the document's own order, because that is
  the order the buyer maintains the file in and the order the refs will be read
  back in.

The row id is ``bomlib.ref`` — the ref a buyer types on a purchase order — and it
is the same function the gates' locators target. That pairing is the interface;
see ``bomlib.SITE_VIEW_ID``.
"""
from __future__ import annotations

import os
import sys
from typing import Any

from nopekit.models import View, ViewKind
from nopekit.site import ViewContext, viewgen

_PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACK_DIR not in sys.path:
    sys.path.insert(0, _PACK_DIR)

import bomlib  # noqa: E402

#: The columns, in reading order: what it is, what it costs, when it lands, what
#: could go wrong with it. Money in the middle because that is what a BOM is for;
#: risk last because it is the column somebody has to go and do something about.
COLUMNS = [
    {"key": "ref", "label": "Ref", "align": "left"},
    {"key": "description", "label": "Description", "align": "left"},
    {"key": "vendor", "label": "Vendor", "align": "left"},
    {"key": "vendor_pn", "label": "Vendor P/N", "align": "left"},
    {"key": "purchased", "label": "Buy", "align": "right",
     "note": "pieces ordered for the run — MOQ and order multiples included"},
    {"key": "unit_price", "label": "Unit", "align": "right", "kind": "money"},
    {"key": "extended", "label": "Extended", "align": "right", "kind": "money",
     "note": "null where the line is unpriced. Not zero: an unknown that adds up "
             "is an unknown that makes the total smaller"},
    {"key": "lead_time_weeks", "label": "Lead", "align": "right", "units": "wk",
     "note": "effective: zero where distributor stock already covers the order"},
    {"key": "lifecycle", "label": "Lifecycle", "align": "left"},
    {"key": "sources", "label": "Sources", "align": "right",
     "note": "independent sources, counted from who MAKES the part where the line "
             "records it. Two distributors of one factory's part is one source"},
    {"key": "risk", "label": "Risk", "align": "left"},
]


def _risk(line: dict, count: int, basis: str, lifecycle: str,
          lead: float | None, stock: float | None, buy: float) -> str:
    """One short phrase per row: what a buyer would flag, in their own words.

    Deliberately a summary and deliberately not a verdict. The gates decide what
    passes; this column exists so that a reader scanning the table can see why a
    row is highlighted without clicking it, and so that the rows that are FINE
    still say what is carrying them.
    """
    bits = []
    if lifecycle in bomlib.DEAD:
        bits.append(f"{lifecycle.upper()}: last order date, not a lead time")
    elif lifecycle in bomlib.WARN:
        bits.append(f"{lifecycle}: buyable now, gone later")
    if count <= 1:
        bits.append("single source"
                    + (" (counted by supplier — no manufacturer recorded)"
                       if basis == "supplier" else ""))
    if lead is None and not (stock is not None and stock >= buy):
        bits.append("no ship date")
    elif stock is not None and stock >= buy:
        bits.append("covered from stock")
    return "; ".join(bits)


@viewgen(
    id=bomlib.SITE_VIEW_ID,
    kind=ViewKind.TABLE,
    title="Bill of materials",
    description="Every line with its purchase quantity, cost, lead time and supply "
                "risk, and the roll-up the cost gate reports.",
    order=30,
    gates=["bom.availability", "bom.single_source", "bom.cost", "bom.complete",
           "bom.moq", "bom.currency", "bom.process_rules"],
)
def bom(ctx: ViewContext) -> View | None:
    """The BOM as rows a locator can address, with the run roll-up in ``meta``."""
    try:
        doc = bomlib.resolve(ctx)
    except bomlib.BomUnavailable:
        # A project with no BOM. Normal — most projects reach a bill of materials
        # late — and not an error.
        return None

    lines = bomlib.lines(doc)
    if not lines:
        # A BOM document with no lines is a document, not a table. Drawing an empty
        # one would read as "nothing to buy", which is the same lie the gates
        # refuse: bom.complete FAILS on this, and the failing verdict is the thing
        # that should be on the page.
        return None

    try:
        roll = bomlib.rollup(doc, ctx)
    except bomlib.BomUnavailable:
        roll = None
    priced = {row["ref"]: row for row in (roll or {}).get("lines", [])}
    qty = (roll or {}).get("build_quantity") or bomlib.build_quantity(doc, ctx)
    currency, _basis = bomlib.document_currency(doc)

    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        ref = bomlib.ref(line, index)
        money = priced.get(ref, {})
        buy = bomlib.purchase_qty(line, qty)
        stock, _sp = bomlib.stock_qty(line)
        lead, _lp = bomlib.lead_weeks(line)
        lifecycle = str(line.get("lifecycle") or "unknown").strip().lower()
        count, basis, names = bomlib.source_breadth(line)
        # The lead time the schedule actually runs on, which is the one the
        # availability gate measures: a 22-week factory lead on a part already
        # sitting in a distributor's warehouse is not your wait.
        effective = 0.0 if (stock is not None and stock >= buy) else lead
        rows.append({
            # `id` is what a Locator targets. Same string as `ref` on purpose — the
            # table shows the buyer the identity the gate pinned.
            "id": ref,
            "ref": ref,
            "description": str(line.get("description") or ""),
            "vendor": str(line.get("vendor") or "(no vendor)"),
            "vendor_pn": str(line.get("vendor_pn") or line.get("mpn") or ""),
            "purchased": round(float(buy), 4),
            "needed": round(float(bomlib.needed_qty(line, qty)), 4),
            "unit_price": money.get("unit_price"),
            "extended": money.get("extended"),
            "lead_time_weeks": effective,
            "quoted_lead_weeks": lead,
            "stock": stock,
            "lifecycle": lifecycle,
            "sources": count,
            "source_basis": basis,
            "source_names": names,
            "risk": _risk(line, count, basis, lifecycle, lead, stock, buy),
        })

    meta: dict[str, Any] = {
        "currency": currency,
        "build_quantity": qty,
        "product": str(doc.get("product") or ""),
        "source": str(doc.get("_source") or "bom_path"),
        "gate": "bom.cost",
        "row_id": "the BOM line's `ref` — the string a buyer types on a purchase "
                  "order, and what every locator in this pack targets",
    }
    if roll is not None:
        # The roll-up as the cost gate computes it, including the two things a
        # naive sum of the Extended column misses — per-order minimums and
        # amortised one-time charges — so a reader who adds the column up and gets
        # a smaller number can see exactly where the difference went.
        meta["totals"] = {
            "parts_total": roll["parts_total"],
            "order_minimum_total": roll["order_minimum_total"],
            "charge_total": roll["charge_total"],
            "nre_per_unit": roll["nre_per_unit"],
            "run_total": roll["run_total"],
            "per_unit": roll["per_unit"],
            "unpriced_lines": [u["ref"] for u in roll["unpriced"]],
        }
        meta["totals_note"] = (
            "run_total is parts + per-order minimum shortfalls + one-time charges, "
            "so it is larger than the Extended column adds to. per_unit amortises "
            "the charges over the run, which is why a per-unit price quoted from "
            "one build quantity is fiction at another"
            + (". Lines with no price contribute NOTHING to these totals — they are "
               "listed in unpriced_lines and the totals are understated by whatever "
               "they cost" if roll["unpriced"] else ""))

    return View(
        id=bomlib.SITE_VIEW_ID,
        kind=ViewKind.TABLE,
        title="Bill of materials",
        data={"columns": COLUMNS, "rows": rows},
        meta=meta,
    )
