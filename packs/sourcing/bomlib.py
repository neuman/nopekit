# SPDX-License-Identifier: Apache-2.0
"""Reading a project's bill of materials, and rolling it up. Stdlib only.

This module is the one place that knows the BOM document shape, so the gates and
the negative-control fixtures agree about it by construction rather than by
coincidence (rule 2: derive, never duplicate). It computes nothing about physics
and calls nothing over a network — a sourcing gate that phones a distributor is
not a gate, it is a weather report.

Document shape (``selftest/example_bom.json`` is the worked example)::

    {
      "schema": "nopekit.bom/1",
      "currency": "USD",
      "build_quantity": 250,
      "budget_per_unit": 46.0,
      "lead_time_budget_weeks": 16,
      "design": { "<attribute>": <value>, ... },
      "process_rules": [ ... ],
      "order_minimums": { "<vendor>": 100.0 },
      "charges": [ {"id": "...", "vendor": "...", "amount": 850.0, "description": "..."} ],
      "lines": [ {
          "ref": "ENC-01", "description": "machined enclosure",
          "qty_per_unit": 1, "spares_fraction": 0.0,
          "vendor": "...", "vendor_pn": "...", "manufacturer": "...", "mpn": "...",
          "manufacturers": ["...", "..."],
          "unit_price": 14.8, "price_currency": "USD",
          "moq": 50, "order_multiple": 25,
          "stock": null, "lead_time_weeks": 6.0,
          "lifecycle": "active|nrnd|eol|allocation|unknown",
          "sources": ["vendor a", "vendor b"], "alternate_qualified": true,
          "single_source_accepted": false, "acceptance_note": "",
          "attributes": { "process": "cnc-milling", "tolerance_mm": 0.15 }
      } ]
    }

Everything except ``lines`` is optional; a gate that needs a missing key SKIPs and
names the key rather than inventing a default. ``unit_price: null`` is an UNKNOWN
price, never a free part — that distinction is the whole reason ``bom.complete``
exists.

Two conventions worth stating at the top, because both are silent when wrong:

* ``fx_rates[CODE]`` multiplies FROM the quoted currency TO ``currency`` — units of
  ``currency`` per one unit of ``CODE``. A USD document with EUR lines writes
  ``1.08``, not ``0.926``.
* ``manufacturer`` / ``manufacturers`` is what a SOURCE is counted from. ``sources``
  is who will sell it to you, which is a different question and a weaker one: two
  distributors of one factory's part is one source with better logistics.

CSV is accepted as a lines-only spelling: one header row using the same field
names, ``sources`` semicolon-separated, empty cell meaning null. A CSV carries no
``design`` block and no ``process_rules``, so ``bom.process_rules`` will SKIP on
one; that is honest rather than convenient.
"""
from __future__ import annotations

import csv
import json
import math
import os
from typing import Any

#: Bumped only when the shape changes incompatibly. A document with no `schema`
#: key is read anyway — refusing it would be pedantry, not safety.
SCHEMA = "nopekit.bom/1"

PACK_DIR = os.path.dirname(os.path.abspath(__file__))

#: A complete, passing BOM shipped with the pack, as the documentation example.
#: The negative controls do NOT build on it: they build on `selftest/baseline.json`,
#: which is the projection CI asserts every gate passes (see `selftest/bad_boms.py`
#: and the SEALED FIXTURES section of docs/PACK_FORMAT.md).
EXAMPLE_BOM = os.path.join(PACK_DIR, "selftest", "example_bom.json")

#: Lifecycle states that mean "this part has a countdown on it".
DEAD = ("eol", "obsolete", "discontinued")
WARN = ("nrnd", "allocation", "last-time-buy", "ltb")

#: Buying more than ~3x what the build needs stops being batch rounding and starts
#: being inventory you are financing for a vendor. Below 3x, reel sizes and sheet
#: counts make some overbuy unavoidable and flagging it is noise.
DEFAULT_MOQ_RATIO_LIMIT = 3.0

#: ...and a ratio only matters attached to money. 4x on a 4-cent label is 30 dollars
#: of spare labels; 4x on a 9-dollar sensor is a second build's cash in a drawer.
#: This is the per-line limit APPLIED TO LINES PAST THE RATIO, and it is only half
#: of the MOQ test: a limit in absolute money cannot be the whole verdict, because
#: 50 is nothing against a 25,000 buy and everything against a 300 one.
DEFAULT_MOQ_EXCESS_LIMIT = 50.0

#: The other half, and the one that cannot be dodged by staying under the ratio:
#: money tied up in pieces THIS run will not consume, summed over every line, as a
#: share of what the run's parts cost. Below roughly a tenth of the parts spend the
#: excess is reel, tray and sheet granularity that no amount of negotiating removes
#: at this quantity; above it the order is financing a second build's stock.
#:
#: Rejected: a flat currency figure as the only limit. It is not scale-free — the
#: same 50 that is a reasonable wake-up on a 300 buy fails every honest 500-off BOM
#: in existence, and a gate that fails every honest BOM is switched off inside a
#: week. Rejected: the purchase/need RATIO as the verdict. A 4x overbuy of four-cent
#: labels is 30 of anybody's money and waking someone for it is the same mistake
#: wearing the opposite coat. The ratio survives as a filter and a reported column;
#: money is the verdict, and it is measured over every line, not only the flagged ones.
DEFAULT_MOQ_IDLE_FRACTION = 0.10

#: A spares allowance buys pieces for attrition, setup scrap and destructive test.
#: Above 1.0 the line is not carrying spares, it is building a second run under
#: another name, and the arithmetic below it (cost, MOQ, lead time) stops describing
#: the build that was asked for. Zero is the floor for a reason that is not pedantry:
#: a NEGATIVE fraction is a plausible sign typo that silently buys fewer pieces than
#: the build consumes, and every number downstream of it moves in the direction that
#: gets the build approved.
#:
#: Rejected: no ceiling at all (the sign typo this catches arrives with a magnitude
#: attached, and 5.0 would sail through). Rejected: a tight 0.25 "typical" ceiling —
#: pilot builds, destructive test and first-article genuinely buy 50% extra, and a
#: validity guard that fails honest documents teaches people to delete the guard.
MAX_SPARES_FRACTION = 1.0

#: Single-sourced lines with no qualified alternate and no WRITTEN acceptance.
#: Zero, because the fix is not "find another vendor" — it is that somebody decides,
#: in writing, that the risk is being carried and what re-qualifying would cost.
DEFAULT_SINGLE_SOURCE_LIMIT = 0

#: The view id ``views/bom.py`` registers, and therefore the string every
#: ``Locator.view`` in this pack carries. It lives here, beside :func:`ref` — which
#: is the ROW id a locator targets — because the pair is an interface between the
#: gates and the view, and an interface in two copies drifts. A gate whose locator
#: names a view that is not built is reported by ``site build`` as a dangling
#: anchor; one whose target is not a row in the table is reported the same way, and
#: both look from the inside exactly like a gate that found nothing.
SITE_VIEW_ID = "bom"

_NUM_FIELDS = ("qty_per_unit", "spares_fraction", "unit_price", "moq",
               "order_multiple", "stock", "lead_time_weeks")
_BOOL_FIELDS = ("alternate_qualified", "single_source_accepted", "critical")


class BomUnavailable(Exception):
    """No usable BOM here. Gates turn this into a SKIP that names the missing key.

    Raised rather than returning an empty document on purpose: an empty BOM would
    sail through every gate in this pack and report a project with nothing to buy
    as fully sourced.
    """


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def num(value: Any) -> Any:
    """A number from whatever the document actually carried, or ``None``.

    ``None`` means UNKNOWN, never zero. Vendors write lead times as ``8``, as
    ``"8"``, and as ``"ARO 8 weeks"``; a stock cell arrives as ``""`` or as
    ``"call"``. The first two are numbers, the last two are not, and the caller
    must be able to tell the difference without exploding.

    No text is parsed out of a string that is not wholly a number. Reading 8 out
    of ``"ARO 8 weeks"`` looks helpful and is a guess: ARO starts the clock at the
    purchase order, which may be a month from now, so the 8 is not the figure the
    schedule needs. Unknown-and-named beats confidently-wrong.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    try:
        text = str(value).strip().replace(",", "")
        return float(text) if ("." in text or "e" in text.lower()) else int(text)
    except ValueError:
        return None


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def read_csv_lines(path: str) -> dict[str, Any]:
    """A lines-only CSV as a BOM document. Header names are the JSON field names.

    A CSV has nowhere to put a document-level ``currency`` or an ``fx_rates``
    table. :func:`document_currency` covers the honest half of that: one
    ``price_currency`` code throughout IS the document's currency. Two different
    codes in a CSV leaves nothing to convert into, so those lines read as UNPRICED
    and ``bom.complete`` and ``bom.currency`` say so. A CSV that needs mixed
    currencies needs the JSON format; that is a real limitation, and it is better
    than a total that silently adds yen to euros.
    """
    lines: list[dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            line = {(k or "").strip(): (v.strip() if isinstance(v, str) else v)
                    for k, v in row.items() if k}
            for field in _NUM_FIELDS:
                if field in line:
                    line[field] = num(line[field])
            for field in _BOOL_FIELDS:
                if field in line:
                    line[field] = _flag(line[field])
            if "sources" in line and isinstance(line["sources"], str):
                line["sources"] = [s.strip() for s in line["sources"].split(";") if s.strip()]
            lines.append(line)
    return {"schema": SCHEMA, "lines": lines, "_format": "csv"}


def read_bom(path: str) -> dict[str, Any]:
    """Read a BOM from ``path`` (.json or .csv). Raises BomUnavailable on failure."""
    if not os.path.isfile(path):
        raise BomUnavailable(f"BOM file not found: {path}")
    try:
        if path.lower().endswith(".csv"):
            doc = read_csv_lines(path)
        else:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BomUnavailable(f"cannot read BOM {path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(doc, dict):
        raise BomUnavailable(f"{path}: a BOM document is a JSON object, not {type(doc).__name__}")
    if not isinstance(doc.get("lines"), list):
        raise BomUnavailable(f"{path}: no `lines` array — nothing to source")
    doc.setdefault("_source", path)
    return doc


def resolve(ctx: Any) -> dict[str, Any]:
    """The BOM this context points at, looked up DEFENSIVELY (gate rule 7).

    In order: an already-parsed ``ctx.extra["bom"]`` (which is how the negative
    controls hand a gate their mutated copy), ``ctx.extra["bom_path"]``, then the
    model projection's ``bom`` / ``bom_path``. Relative paths resolve against
    ``ctx.root``.

    Raises :class:`BomUnavailable` naming the keys it looked for, so the gate can
    SKIP visibly instead of passing a project that never supplied a BOM.
    """
    extra = getattr(ctx, "extra", None) or {}
    inline = extra.get("bom")
    if isinstance(inline, dict):
        doc = dict(inline)
        doc.setdefault("_source", "ctx.extra['bom']")
        if not isinstance(doc.get("lines"), list):
            raise BomUnavailable("ctx.extra['bom'] has no `lines` array")
        return doc

    path = extra.get("bom_path")
    if not path and hasattr(ctx, "param"):
        model_inline = ctx.param("bom")
        if isinstance(model_inline, dict):
            doc = dict(model_inline)
            doc.setdefault("_source", "params['bom']")
            if not isinstance(doc.get("lines"), list):
                raise BomUnavailable("params['bom'] has no `lines` array")
            return doc
        path = ctx.param("bom_path")

    if not path:
        raise BomUnavailable(
            "no BOM: the model projection defines neither 'bom_path' nor 'bom', and "
            "ctx.extra carries neither 'bom_path' nor 'bom'"
        )
    path = str(path)
    if not os.path.isabs(path):
        path = os.path.join(getattr(ctx, "root", "") or os.curdir, path)
    return read_bom(path)


# --------------------------------------------------------------------------- #
# field access
# --------------------------------------------------------------------------- #
def setting(doc: dict[str, Any], ctx: Any, key: str, default: Any = None) -> Any:
    """A threshold, from the BOM document first, then the model projection.

    The BOM wins because it is the document a buyer edits; the projection is the
    fallback for a project that keeps its budget in the model. Neither is invented.
    """
    if key in doc and doc[key] is not None:
        return doc[key]
    if hasattr(ctx, "param"):
        got = ctx.param(key)
        if got is not None:
            return got
    return default


def lines(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [ln for ln in (doc.get("lines") or []) if isinstance(ln, dict)]


def ref(line: dict[str, Any], index: int = 0) -> str:
    return str(line.get("ref") or line.get("vendor_pn") or line.get("mpn")
               or line.get("description") or f"line#{index + 1}")


def _distinct(names: list[str]) -> list[str]:
    """Case- and whitespace-insensitively distinct, first spelling wins."""
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name is None:
            continue
        text = str(name).strip()
        if not text:
            continue
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            out.append(text)
    return out


def sources_of(line: dict[str, Any]) -> list[str]:
    """Distinct named SUPPLIERS for a line, vendor included.

    Suppliers, not sources. Who will send you a box is a logistics question; who
    makes the part is the supply-risk question, and they are different numbers.
    See :func:`manufacturers_of` and :func:`source_breadth`.
    """
    return _distinct([*(line.get("sources") or []), line.get("vendor")])


def manufacturers_of(line: dict[str, Any]) -> list[str]:
    """Distinct MANUFACTURERS recorded for a line. Empty when none is recorded.

    Reads ``manufacturers`` (a list, for a line genuinely qualified against two
    factories' parts) and ``manufacturer`` (the single spelling). ``mpn`` is
    deliberately NOT read as a second name: a part number without a maker beside
    it names a part, not a company, and two MPNs from one factory are one source.
    """
    return _distinct([*(line.get("manufacturers") or []), line.get("manufacturer")])


def source_breadth(line: dict[str, Any]) -> tuple[int, str, list[str]]:
    """``(independent sources, what they were counted from, their names)``.

    **Two distributors is not two sources.** Two distributors shipping the same
    factory's part is one source with better logistics: useful against a warehouse
    fire, useless against an EOL notice, a price rise or an allocation. So when a
    line records who MAKES it, that is what gets counted, however many companies
    will sell it to you; the supplier list is breadth of *supply* and is reported
    beside the count rather than inside it.

    A line that records no manufacturer is counted by supplier, because that is
    all it stated — and the verdict names the basis, because "we did not write it
    down" is not the same evidence as "two factories" and must not read like it.
    """
    makers = manufacturers_of(line)
    if makers:
        return len(makers), "manufacturer", makers
    named = sources_of(line)
    return len(named), "supplier", named


def document_currency(doc: dict[str, Any]) -> tuple[str, str]:
    """``(the currency every total is reported in, where it came from)``.

    ``doc["currency"]`` when the document states one. When it does not — and a CSV
    has nowhere to put it — but every priced line names the SAME ``price_currency``,
    that code is the document's currency: it is being read out of the document, not
    invented, and one code throughout is not a mixed-currency document.

    Two or more distinct codes with no stated base returns ``("", ...)`` and every
    line that names a currency then counts as UNPRICED, because there is a
    conversion to do and nothing to convert into. That case used to short-circuit
    the whole check and add yen to euros to dollars at face value.
    """
    stated = str(doc.get("currency") or "").strip().upper()
    if stated:
        return stated, "the document's 'currency'"
    quoted = {str(ln.get("price_currency") or "").strip().upper() for ln in lines(doc)
              if ln.get("unit_price") not in (None, "")}
    quoted.discard("")
    if len(quoted) == 1:
        return quoted.pop(), "every priced line's price_currency (one code throughout)"
    return "", ("no 'currency' and no single price_currency" if quoted
                else "no 'currency' declared and no line names one")


def price_conversion(line: dict[str, Any], doc: dict[str, Any]) -> tuple[float, str]:
    """``(multiplier to reach the document currency, why there isn't one)``.

    The ONE place the currency question is answered, so ``bom.complete`` (which
    calls it through :func:`unit_price`) and ``bom.currency`` (which calls it
    directly) can never disagree about which lines are convertible — the earlier
    arrangement had the currency gate matching on the text of another function's
    error message, which agreed by coincidence and stopped agreeing the moment a
    new message was added.

    Direction, stated once and repeated wherever it can be got wrong:
    ``fx_rates[CODE]`` is units of the document's currency per ONE unit of CODE. A
    USD document holding EUR lines writes ``{"EUR": 1.08}``, never ``0.926``.
    """
    base, _from = document_currency(doc)
    quoted = str(line.get("price_currency") or "").strip().upper()
    if not quoted or (base and quoted == base):
        return 1.0, ""
    if not base:
        return 0.0, (f"priced in {quoted} and this document has no base currency to "
                     f"convert it into: it declares no 'currency' and its lines are "
                     f"quoted in more than one")
    rates = doc.get("fx_rates") or {}
    if quoted not in rates:
        return 0.0, f"priced in {quoted} with no fx_rates['{quoted}'] to reach {base}"
    rate = num(rates[quoted])
    if rate is None or isinstance(rate, bool) or float(rate) <= 0:
        return 0.0, (f"priced in {quoted} and fx_rates['{quoted}'] is {rates[quoted]!r}, "
                     f"which is not a usable rate to reach {base}")
    return float(rate), ""


def unit_price(line: dict[str, Any], doc: dict[str, Any]) -> tuple[float | None, str]:
    """``(price in the document's currency, reason it is unusable)``.

    A price quoted in another currency with no rate in ``doc["fx_rates"]`` is an
    UNKNOWN, not a number to add up. Silently summing mixed currencies is the
    arithmetic version of the lie this whole tool exists to prevent.

    **The direction of an fx rate**, stated here because a reversed one is the one
    convention error that silently halves or doubles a total and no other number in
    the document contradicts it: ``fx_rates[CODE]`` is the multiplier FROM the
    quoted currency TO ``doc["currency"]`` — units of the document's currency per
    one unit of the quoted one. A USD document holding EUR-quoted lines writes
    ``{"EUR": 1.08}``, never ``0.926``.

    A line that names a ``price_currency`` in a document that declares no top-level
    ``currency`` is UNPRICED, and that is the third hole this function closes. The
    document currency is optional, which is fine while every price is in one
    unnamed currency; the moment a line says which currency it was quoted in, there
    is a conversion to do and nothing to convert to. The old behaviour was to take
    every price at face value, so a JPY line, a EUR line and a USD line added up to
    a number with no unit and no meaning, while the completeness gate reported that
    every line had a usable price.
    """
    raw = line.get("unit_price")
    if raw is None or raw == "":
        return None, "no unit_price"
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"unit_price {raw!r} is not a number"
    if value < 0:
        return None, f"unit_price {value} is negative"
    rate, problem = price_conversion(line, doc)
    if problem:
        return None, problem
    return value * rate, ""


def build_quantity(doc: dict[str, Any], ctx: Any) -> int:
    q = setting(doc, ctx, "build_quantity")
    if q is None:
        raise BomUnavailable(
            "no 'build_quantity': cost, MOQ and stock cover all depend on how many "
            "units are being built, and guessing one would invent the answer"
        )
    q = num(q)
    if q is None or isinstance(q, bool):
        raise BomUnavailable(
            f"build_quantity is {setting(doc, ctx, 'build_quantity')!r}, which is not a "
            f"number of units")
    q = int(q)
    if q <= 0:
        raise BomUnavailable(f"build_quantity is {q}; a run of zero units sources nothing")
    return q


def spares_fraction(line: dict[str, Any]) -> tuple[float, str]:
    """``(usable spares fraction, why the recorded one is not usable)``.

    Out of range is treated as ZERO here and named for ``bom.complete`` to refuse,
    rather than being used. That choice is deliberate and it is the conservative
    one: a ``-0.5`` typo used as written buys half the pieces the build consumes
    and every downstream number — cost, MOQ overbuy, vendor minimums — moves in the
    direction that gets the build approved. Falling back to zero can only make the
    order too small by the spares, never by the build, and the validity gate stops
    the run either way.
    """
    raw = line.get("spares_fraction")
    if raw is None or raw == "":
        return 0.0, ""
    value = num(raw)
    if value is None or isinstance(value, bool):
        return 0.0, f"spares_fraction {raw!r} is not a number"
    value = float(value)
    if value < 0:
        return 0.0, (f"spares_fraction {value:g} is negative — a spares allowance cannot "
                     f"buy fewer pieces than the build consumes")
    if value > MAX_SPARES_FRACTION:
        return 0.0, (f"spares_fraction {value:g} is above {MAX_SPARES_FRACTION:g}: that is "
                     f"not an attrition allowance, it is a second run under another name")
    return value, ""


def quantity_problems(line: dict[str, Any]) -> list[str]:
    """Why ``line``'s quantity cannot be read as written — ``[]`` when it can.

    ``qty_per_unit`` missing or not positive, a spares fraction
    :func:`spares_fraction` refuses, and an ``moq`` or ``order_multiple`` that
    is present and not a quantity. Each is read downstream as something
    smaller — no need, no spares, "no minimum", "no rounding" — which quietly
    makes the order SMALLER and the run CHEAPER than the vendor will actually
    sell it, and a stock figure look like it covers the build: the permissive
    direction every BOM error fails in, and just as invisible.

    One copy for the two readers that refuse it: ``bom.complete`` (as part of
    "not orderable") and ``bom.availability`` (a line whose quantity cannot be
    read is never covered by stock). What slipped through (review of P2.2):
    ``bom.availability`` had relied on ``bom.complete`` as its prerequisite for
    this, and that guard also fails on an unpriced line, which says nothing
    about a ship date.
    """
    out: list[str] = []
    per = num(line.get("qty_per_unit"))
    if per is None or isinstance(per, bool):
        out.append("no qty_per_unit")
    elif float(per) <= 0:
        out.append(f"qty_per_unit {float(per):g}")
    _spares, spares_problem = spares_fraction(line)
    if spares_problem:
        out.append(spares_problem)
    for field in ("moq", "order_multiple"):
        raw = line.get(field)
        if raw is None or raw == "":
            continue
        value = num(raw)
        if value is None or isinstance(value, bool) or float(value) < 0:
            out.append(f"{field} {raw!r} is not a quantity")
    return out


def needed_qty(line: dict[str, Any], qty: int) -> float:
    """Pieces the build consumes, spares included. Not yet rounded to a purchase."""
    per = num(line.get("qty_per_unit"))
    per = float(per) if per is not None and not isinstance(per, bool) else 0.0
    spares, _problem = spares_fraction(line)
    return per * qty * (1.0 + spares)


def purchase_qty(line: dict[str, Any], qty: int) -> float:
    """Pieces actually bought: the need, lifted to the MOQ and the order multiple.

    This is where a $3 part becomes $300 and nobody notices, because the BOM says
    $3 and the invoice says the MOQ.
    """
    need = needed_qty(line, qty)
    if need <= 0:
        return 0.0
    # Both of these arrive as "1,000" and as "1000" from real documents; num()
    # reads either and returns None for anything that is not a number, which is
    # treated as "no minimum stated" rather than crashing the roll-up.
    moq = num(line.get("moq"))
    moq = float(moq) if moq is not None and not isinstance(moq, bool) and moq > 0 else 0.0
    buy = max(need, moq)
    multiple = num(line.get("order_multiple"))
    multiple = float(multiple) if multiple is not None and not isinstance(multiple, bool) else 0.0
    if multiple > 0:
        buy = math.ceil(buy / multiple - 1e-9) * multiple
    return buy


def money(value: float, currency: str = "") -> str:
    return f"{value:,.2f}{(' ' + currency) if currency else ''}"


def lead_weeks(line: dict[str, Any]) -> tuple[float | None, str]:
    """``(quoted lead time in weeks, why it is unknown)``.

    A lead time arrives as ``8``, as ``"8"``, and as ``"ARO 8 weeks"``. The last
    one is not a number, and the gate that called ``float()`` on it used to crash —
    an error settles nothing, and the pack's own documentation names that exact
    spelling as one a vendor will send you. Uncoercible is UNKNOWN, named with the
    text that arrived, and an unknown ship date is a FAIL, not a zero.
    """
    raw = line.get("lead_time_weeks")
    if raw is None or raw == "":
        return None, "no lead_time_weeks"
    weeks = num(raw)
    if weeks is None or isinstance(weeks, bool):
        return None, f"lead_time_weeks {raw!r} is not a number of weeks"
    weeks = float(weeks)
    if weeks < 0:
        return None, f"lead_time_weeks {weeks:g} is negative"
    return weeks, ""


def stock_qty(line: dict[str, Any]) -> tuple[float | None, str]:
    """``(pieces on the shelf, why it is unknown)``. Same rules as :func:`lead_weeks`."""
    raw = line.get("stock")
    if raw is None or raw == "":
        return None, ""
    pieces = num(raw)
    if pieces is None or isinstance(pieces, bool):
        return None, f"stock {raw!r} is not a number of pieces"
    pieces = float(pieces)
    if pieces < 0:
        return None, f"stock {pieces:g} is negative"
    return pieces, ""


def idle_capital_limit(doc: dict[str, Any], ctx: Any,
                       parts_total: float) -> tuple[float, str]:
    """``(limit on BOM-wide idle capital, where that limit came from)``.

    Derived from the run's own parts spend by default, because money has no
    absolute scale: the same figure that is a sensible wake-up on a 300 buy fails
    every honest 500-off BOM in existence. A project that knows its own tolerance
    states ``moq_excess_total_limit`` in money and that wins.
    """
    stated = setting(doc, ctx, "moq_excess_total_limit")
    value = num(stated)
    if value is not None and not isinstance(value, bool):
        return float(value), "moq_excess_total_limit"
    fraction = num(setting(doc, ctx, "moq_idle_limit_fraction", DEFAULT_MOQ_IDLE_FRACTION))
    if fraction is None or isinstance(fraction, bool) or float(fraction) < 0:
        fraction = DEFAULT_MOQ_IDLE_FRACTION
    fraction = float(fraction)
    return fraction * float(parts_total), f"{fraction:.0%} of the {money(parts_total)} parts spend"


# --------------------------------------------------------------------------- #
# the roll-up
# --------------------------------------------------------------------------- #
def rollup(doc: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Cost of one build run, and the per-unit number that comes out of it.

    Three things that a naive `sum(qty * price)` misses, and all three are the
    ones that turn up on the invoice:

    * **purchase quantity, not need** — MOQ and order multiples are bought, not
      needed (see :func:`purchase_qty`).
    * **per-order minimums** — a vendor with a $250 minimum charges $250 for a
      $40 order. The shortfall is real money and it belongs in the roll-up. Vendor
      names are matched case- and whitespace-insensitively, and a minimum naming a
      vendor no line buys from is REPORTED (``order_minimum_unmatched``) rather
      than dropped: it is either a typo, in which case a real minimum has gone
      missing from the total, or a vendor somebody forgot to order from. Both need
      a human, and both used to vanish in the cheap direction.
    * **non-recurring charges** — tooling, setup, programming, a test fixture.
      They are one-time for the RUN, so they are amortised over the run, which is
      why the per-unit cost falls as the quantity rises and why a 25-off price
      quoted from a 1000-off roll-up is fiction.

    Unpriced lines do not contribute zero. They are listed in ``unpriced`` and the
    caller must refuse to report a total as if they were free.
    """
    qty = build_quantity(doc, ctx)
    rows: list[dict[str, Any]] = []
    unpriced: list[dict[str, str]] = []
    by_vendor: dict[str, float] = {}
    #: casefolded vendor name -> the spelling the BOM used, for every vendor that
    #: has a LINE (priced or not). An order minimum is matched against this, not
    #: against the spend, so a vendor whose lines are all unpriced is not mistaken
    #: for a vendor nobody buys from.
    vendor_keys: dict[str, str] = {}

    for i, line in enumerate(lines(doc)):
        price, problem = unit_price(line, doc)
        need = needed_qty(line, qty)
        buy = purchase_qty(line, qty)
        vendor = str(line.get("vendor") or "").strip() or "(no vendor)"
        vendor_keys.setdefault(vendor.casefold(), vendor)
        row = {
            "ref": ref(line, i), "vendor": vendor,
            "vendor_pn": line.get("vendor_pn") or "",
            "qty_per_unit": line.get("qty_per_unit"),
            "needed": round(need, 4), "purchased": round(buy, 4),
            "unit_price": price, "extended": None,
        }
        if price is None:
            unpriced.append({"ref": row["ref"], "why": problem})
        else:
            extended = round(buy * price, 6)
            row["extended"] = extended
            by_vendor[vendor] = by_vendor.get(vendor, 0.0) + extended
        rows.append(row)

    parts_total = sum(r["extended"] or 0.0 for r in rows)

    minimums = doc.get("order_minimums") or {}
    spend_by_key = {name.casefold(): total for name, total in by_vendor.items()}
    shortfalls: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for vendor, minimum in minimums.items():
        floor = num(minimum)
        if floor is None or isinstance(floor, bool):
            unmatched.append({"vendor": vendor, "minimum": minimum,
                              "why": "the minimum is not a number"})
            continue
        floor = float(floor)
        key = str(vendor).strip().casefold()
        if key not in vendor_keys:
            unmatched.append({"vendor": vendor, "minimum": floor,
                              "why": "no line in this BOM names that vendor, so the "
                                     "minimum is either a misspelling of one that does "
                                     "or an order nobody placed"})
            continue
        spent = spend_by_key.get(key, 0.0)
        if spent > 0 and spent < floor:
            shortfalls.append({"vendor": vendor_keys[key], "spent": round(spent, 2),
                               "minimum": floor, "shortfall": round(floor - spent, 2)})
    minimum_total = sum(s["shortfall"] for s in shortfalls)

    charges = []
    for c in (doc.get("charges") or []):
        if not isinstance(c, dict):
            continue
        try:
            amount = float(c.get("amount"))
        except (TypeError, ValueError):
            continue
        charges.append({"id": c.get("id") or c.get("description") or "charge",
                        "vendor": c.get("vendor") or "", "amount": amount,
                        "description": c.get("description") or ""})
    charge_total = sum(c["amount"] for c in charges)

    run_total = parts_total + minimum_total + charge_total
    return {
        "build_quantity": qty,
        "currency": document_currency(doc)[0],
        "lines": rows,
        "unpriced": unpriced,
        "parts_total": round(parts_total, 2),
        "vendor_subtotals": {k: round(v, 2) for k, v in sorted(by_vendor.items())},
        "order_minimum_shortfalls": shortfalls,
        "order_minimum_unmatched": unmatched,
        "order_minimum_total": round(minimum_total, 2),
        "charges": charges,
        "charge_total": round(charge_total, 2),
        "nre_per_unit": round(charge_total / qty, 4),
        "run_total": round(run_total, 2),
        "per_unit": round(run_total / qty, 4),
    }


def write_evidence(ctx: Any, name: str, payload: Any) -> list[str]:
    """Dump the bulk output next to the verdict and return it as ``evidence``.

    One dense line goes in the verdict; the forty-row table goes here (rule 5 of
    the pack contract). Never fails a gate over a filesystem problem — the
    measurement is the product, the file is the receipt.
    """
    try:
        path = ctx.out_path(name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=False, default=str)
        return [path]
    except Exception:                            # noqa: BLE001 - evidence is best effort
        return []


# --------------------------------------------------------------------------- #
# process rules:  the vendor's capability set, supplied by the project
# --------------------------------------------------------------------------- #
#
# The pack does NOT ship a rule list. Vendor capability is local, dated and
# negotiable — a hard-coded "minimum 0.15 mm" would be wrong for half the shops
# in the world and, worse, would be believed. The project supplies the rules it
# was quoted; this pack only checks the design against them, and says which rule
# and which attribute when it does not hold.
#
#   {"id": "...", "scope": "design"|"line", "when": {attr: value|{"in":[...]}},
#    "require": {attr: {"in"|"not_in"|"equals"|"not_equals"|"min"|"max": ...}},
#    "note": "why the vendor says so"}
#
# A `require` on an attribute the design never declares is a VIOLATION, not a
# pass. "We did not say" is exactly the state that gets discovered at the quote.

_OPS = ("in", "not_in", "equals", "not_equals", "min", "max")


def attrs_for_line(line: dict[str, Any]) -> dict[str, Any]:
    """A line's attributes for rule matching: its own keys, `attributes` winning."""
    merged = {k: v for k, v in line.items() if not isinstance(v, (dict, list))}
    merged.update(line.get("attributes") or {})
    return merged


def _matches(expected: Any, value: Any) -> bool:
    """`when` matching: a bare value means equality, a dict reuses the operators."""
    if isinstance(expected, dict):
        return not _requirement_problem(expected, value)
    if isinstance(expected, list):
        return _same(value, expected)
    return _eq(value, expected)


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return a == b


def _same(value: Any, allowed: Any) -> bool:
    return any(_eq(value, a) for a in (allowed or []))


def _requirement_problem(spec: Any, value: Any) -> str:
    """Why ``value`` fails ``spec``, or "" when it holds. Missing is not passing."""
    if not isinstance(spec, dict):
        spec = {"equals": spec}
    unknown = [k for k in spec if k not in _OPS]
    if unknown:
        return f"rule uses unknown operator(s) {', '.join(sorted(unknown))}"
    if value is None:
        return "the design does not declare it"
    for op, want in spec.items():
        if op == "in" and not _same(value, want):
            return f"{value!r} is not one of {want}"
        if op == "not_in" and _same(value, want):
            return f"{value!r} is excluded ({want})"
        if op == "equals" and not _eq(value, want):
            return f"{value!r} != {want!r}"
        if op == "not_equals" and _eq(value, want):
            return f"{value!r} is not allowed"
        if op in ("min", "max"):
            try:
                numeric = float(value)
                bound = float(want)
            except (TypeError, ValueError):
                return f"{value!r} is not numeric, so {op} {want!r} cannot be checked"
            if op == "min" and numeric < bound:
                return f"{numeric:g} < min {bound:g}"
            if op == "max" and numeric > bound:
                return f"{numeric:g} > max {bound:g}"
    return ""


def rule_applies(rule: dict[str, Any], attrs: dict[str, Any]) -> bool:
    """Does this rule's ``when`` match these attributes? Empty ``when`` means always."""
    return all(_matches(exp, attrs.get(key)) for key, exp in (rule.get("when") or {}).items())


def evaluate_rules(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every violation of the project's process rules, as dicts. Empty = clean."""
    rules = doc.get("process_rules") or []
    design = doc.get("design") or {}
    out: list[dict[str, Any]] = []

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rid = str(rule.get("id") or "rule")
        require = rule.get("require") or {}
        scope = str(rule.get("scope") or "design").strip().lower()
        targets: list[tuple[str, dict[str, Any]]]
        if scope == "line":
            targets = [(ref(ln, i), attrs_for_line(ln)) for i, ln in enumerate(lines(doc))]
        else:
            targets = [("design", dict(design))]

        for where, attrs in targets:
            if not rule_applies(rule, attrs):
                continue
            for key, spec in require.items():
                problem = _requirement_problem(spec, attrs.get(key))
                if problem:
                    out.append({
                        "rule": rid, "scope": scope, "where": where,
                        "attribute": key, "problem": problem,
                        "note": str(rule.get("note") or ""),
                    })
    return out


def applicable_design_rules(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Design-scoped rules whose ``when`` currently matches. Used by the fixture."""
    design = doc.get("design") or {}
    out = []
    for rule in (doc.get("process_rules") or []):
        if not isinstance(rule, dict) or not (rule.get("require") or {}):
            continue
        if str(rule.get("scope") or "design").strip().lower() != "design":
            continue
        if rule_applies(rule, design):
            out.append(rule)
    return out


def value_outside(spec: Any, current: Any) -> Any:
    """A plausible value that BREAKS ``spec`` — the fixture's one meaningful change.

    Not a sentinel and not garbage: for an allowed-set rule it picks a real
    alternative the vendor did not list, for a bound it moves an order of
    magnitude past the bound. The known-bad input has to be a design somebody
    could plausibly have specified, or the control proves only that the gate
    rejects nonsense.
    """
    if not isinstance(spec, dict):
        spec = {"equals": spec}
    if "min" in spec:
        return float(spec["min"]) / 10.0
    if "max" in spec:
        return float(spec["max"]) * 10.0
    if "not_in" in spec and spec["not_in"]:
        return spec["not_in"][0]
    if "not_equals" in spec:
        return spec["not_equals"]
    allowed = spec.get("in") or ([spec["equals"]] if "equals" in spec else [])
    for candidate in ("levelled-solder", "bare", "standard", "commercial", "unqualified"):
        if not _same(candidate, allowed) and not _eq(candidate, current):
            return candidate
    return "unqualified"
