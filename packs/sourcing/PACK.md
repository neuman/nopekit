# sourcing — can you actually buy and build this?

A part you cannot order is a part you do not have. This pack settles the claims that
are not physics and sink projects anyway: a line nobody priced, a cost that only
looks like the budget until the tooling charge lands, a sixteen-week part on a
six-week schedule, a $3 component with a 5,000-piece minimum, a finish the quoted
line does not offer, and a part exactly one company in the world makes.

Everything here is tier 0, pure arithmetic over a document the project maintains,
and **offline**. No gate calls a distributor. A check that depends on today's stock
page is a quote with a timestamp, not a gate: it cannot be reproduced, it fails in
CI, and it disagrees with itself twice a day.

---

## 1. What this pack settles

| Claim, in plain language | Gate |
|---|---|
| every part has a vendor, a part number, a quantity and a real price | `bom.complete` |
| the run fits the budget, including minimums and amortised tooling | `bom.cost` |
| every line has a known ship date and nothing on it is dying | `bom.availability` |
| no minimum order quantity is buying a second build's worth of stock | `bom.moq` |
| the design is inside the capability set the vendor actually quoted | `bom.process_rules` |
| single-source risk — counted by *manufacturer* — is a number somebody has signed for | `bom.single_source` |
| every price is in a currency the roll-up can actually convert | `bom.currency` |

## 2. What this pack CANNOT settle

Read this section twice; it prevents more damage than the one above.

- **It cannot tell you a price is real.** It checks the arithmetic on the numbers
  in your document. A stale quote, a price scraped at qty 1 and used at qty 1,000,
  or a number somebody remembered will pass every gate here.
- **It cannot see stock.** `stock` is whatever the BOM says. Availability is a
  moving target and this pack deliberately reads a snapshot you own rather than a
  page you do not. Re-record before you commit money; the ledger will mark the
  claims STALE when the document changes, which is the honest outcome.
- **It does not know your vendors' rules.** `bom.process_rules` checks the design
  against rules *the project supplies*. Shipping a rule list would be inventing
  facts about somebody else's shop.
- **It says nothing about whether a part is the right part.** Electrical fit,
  mechanical fit, thermal headroom, footprint agreement, firmware support: other
  packs, other claims. A fully sourced BOM of wrong parts passes every gate here.
- **It does not model cash flow, currency risk over time, tariffs, duty codes,
  export control, or compliance** (RoHS/REACH/conflict minerals). It will convert
  currency only if you give it a rate, and it refuses to add mixed currencies
  without one.
- **It cannot price assembly labour or yield.** Add them as lines or charges if
  they matter; the gate arithmetic will then include them, but the numbers are
  still yours.
- **It cannot tell you who really makes a part.** `bom.single_source` counts
  distinct `manufacturer` values where a line records one, which is the right
  question — two distributors shipping one factory's part is one source. But a line
  that records no manufacturer is counted by *supplier*, so two distributors of the
  same factory read there as two sources. The verdict says how many lines are in
  that state. Record `manufacturer` and `mpn` on anything you would be sad to lose.
- **It cannot tell you whether an overbuy was avoidable.** `bom.moq` measures the
  money — pieces bought minus pieces consumed, times price, over every line.
  Whether another vendor would break a reel is a decision it cannot make for you.
- **The longest lead time is not the ship date.** `bom.availability` reports when
  the last PART lands. Assembly, test, rework and transit after that are somebody
  else's weeks and are not in this document at all — add them to the schedule
  yourself, or as a line with its own lead time.
- **It cannot catch a reversed exchange rate.** `fx_rates` is believed. Write it in
  the direction stated in section 5; nothing else in the document contradicts a
  rate that is upside down, so a reversed one understates every foreign line by a
  plausible-looking amount and every gate here stays green.
- **It is not a quote.** It is the thing you run *before* asking for one, and again
  against the one that comes back.

## 3. The BOM document

One JSON file, read from `ctx.params["bom_path"]` (or `ctx.extra["bom_path"]`, or
an already-parsed `bom` in either). Paths are relative to the project root.
`selftest/example_bom.json` is the complete worked example and passes every gate —
copy it and edit. The shape, in full:

```json
{
  "schema": "atompipe.bom/1",
  "currency": "USD",
  "build_quantity": 250,
  "budget_per_unit": 52.0,
  "lead_time_budget_weeks": 16,
  "moq_ratio_limit": 3.0, "moq_excess_limit": 50.0,
  "moq_idle_limit_fraction": 0.10, "single_source_limit": 0,
  "design":        { "surface_finish": "ENIG", "contact_type": "pressed", "...": "..." },
  "process_rules": [ { "id": "...", "scope": "design|line", "when": {...},
                       "require": {"attr": {"in": [...]}}, "note": "why the vendor says so" } ],
  "order_minimums": { "Meridian Labelworks": 75.0 },
  "charges":       [ { "id": "test-fixture", "amount": 1200.0, "description": "one time" } ],
  "fx_rates":      { "EUR": 1.09 },
  "lines": [ {
      "ref": "U2", "description": "sensing element",
      "qty_per_unit": 1, "spares_fraction": 0.02,
      "vendor": "Ardent Supply", "vendor_pn": "AS-SEN-2200",
      "manufacturer": "Calder Sensing", "mpn": "CS-2200-B",
      "manufacturers": ["Calder Sensing"],
      "unit_price": 9.10, "price_currency": "USD",
      "moq": 250, "order_multiple": 25,
      "stock": null, "lead_time_weeks": 14,
      "lifecycle": "active|nrnd|allocation|eol|unknown",
      "sources": ["Ardent Supply"], "alternate_qualified": false,
      "single_source_accepted": true, "acceptance_note": "why the risk is carried",
      "attributes": { "process": "cnc-milling", "tolerance_mm": 0.15 }
  } ]
}
```

Only `lines` is required. Anything else a gate needs and cannot find makes that
gate **SKIP with the key named** — the claim goes BLOCKED and stays visible. A
missing number is never a zero and never a default.

`unit_price: null` means *unknown*, not free. That one distinction is why
`bom.complete` exists and why `bom.cost` refuses to total a BOM with a hole in it.

**CSV** is accepted as a lines-only spelling: one header row using the same field
names, `sources` semicolon-separated, blank cell meaning null. A CSV carries no
`design` block and no `process_rules`, so `bom.process_rules` SKIPs on one, and the
run-level numbers (`build_quantity`, budget, minimums, charges) have to come from
the model projection. Convenient for a first pass; the JSON is the real format.

## 4. Gates

| id | tier | measures | limit from | known-bad fixture |
|---|---|---|---|---|
| `bom.complete` | 0 | lines not orderable | 0, always | one line's price cell blanked |
| `bom.cost` | 0 | cost per unit (or per run) | `budget_per_unit` / `budget_total` | one line re-quoted to 10 budgets |
| `bom.availability` | 0 | longest effective lead time, weeks | `lead_time_budget_weeks` | one line marked `eol` |
| `bom.moq` | 0 | idle capital across the BOM | `moq_idle_limit_fraction` x parts spend (default 10%), or `moq_excess_total_limit` | one MOQ raised past the build |
| `bom.process_rules` | 0 | rule violations | 0, always | one attribute outside the vendor's set |
| `bom.single_source` | 0 | unrecorded single-*manufacturer* lines | `single_source_limit` (default 0) | the second source disappears |
| `bom.currency` | 0 | prices that cannot reach the base currency | 0, always | one line re-quoted in a currency with no rate |

Three gates fail on more than their headline number, and say so in the verdict:
`bom.cost` also fails when an `order_minimums` key names a vendor no line buys
from; `bom.moq` also fails when a single line past `moq_ratio_limit` idles more
than `moq_excess_limit`; `bom.availability` fails on any unknown ship date or dead
lifecycle whether or not a budget exists, and SKIPs the lead-vs-budget comparison
when `lead_time_budget_weeks` is absent rather than passing an unbounded schedule.

`bom.complete` binds to the whole domain (`sourcing`, `cost`, `availability`, …)
on purpose: it is this pack's validity gate. Every other number below it is
arithmetic on the document, so when the document has holes, the other verdicts are
fiction and should fall with it. The other five bind narrowly, so a lead-time
problem never reads as a cost problem.

Each fixture changes exactly ONE commercially meaningful thing, in the direction
that gate cares about, on a copy of the pack's own `selftest/baseline.json` — never
on the host project's BOM. That sealing is deliberate: a control is a test of the
*instrument*, and one whose severity depends on the installing project's numbers
fires in one repository and passes in another. No fixture hardcodes a magnitude
either; each reads the limit it has to beat out of the baseline the gate reads it
from. Run them: `atompipe gate selftest sourcing`.

### Prerequisites

`bom.complete` is the prerequisite (`needs`) of `bom.cost`, `bom.availability`,
`bom.moq`, `bom.process_rules` and `bom.single_source`: an unpriced or unorderable
line, or a spares fraction outside [0, 1], makes their arithmetic over the BOM a work of
fiction. Below it failing they are not run, and a claim tagged only `moq`,
`single-source`, `process-rules` or `lead-time` reads Skipped, `prerequisite failed:
bom.complete`. `bom.currency` needs nothing: its own control fails `bom.complete` (one
line not orderable), which would pre-empt it.

## 4b. The view, and the rows locators land on

| View | Kind | Payload | Addressed by |
|---|---|---|---|
| `bom` | `table` | every line with purchase quantity, unit and extended cost, effective lead time, lifecycle, source count and a risk phrase; the run roll-up in `meta.totals` | **row id = the line's `ref`** |

**The row id is `bomlib.ref(line, i)`** — the string a buyer types on a purchase
order, which is also what every locator in this pack targets. That pairing is the
interface, and it lives in `bomlib` (`SITE_VIEW_ID` beside `ref`) so the gates and
the view cannot end up spelling it two ways.

The table is the lookup a sourcing verdict currently forces on its reader: `2
unrecorded single-source line(s): U1, REG-01` sends somebody into a JSON file to
find out what U1 costs, when it ships and who else makes it. Here it is one row,
highlighted.

Three things it does not do:

- **It does not price anything the gates did not.** The roll-up is
  `bomlib.rollup`, the same function `bom.cost` reports, so the per-unit number on
  the page is the per-unit number in the verdict.
- **It does not fill a blank.** An unpriced line's extended cost is `null`, never
  0. A zero adds up, looks like money, and makes the total smaller — the direction
  every error in a BOM goes. `meta.totals.unpriced_lines` names them and the note
  says the totals are understated by whatever they cost.
- **It does not sort.** Document order, because that is the order the buyer
  maintains the file in.

`meta.totals` carries the two things summing the Extended column misses —
per-order minimum shortfalls and amortised one-time charges — so a reader who adds
the column up and gets a smaller number can see exactly where the difference went.

### What each gate pins

| Gate | Locator | Why that and not more |
|---|---|---|
| `bom.availability` | the EOL lines first, then the lines with no ship date, then — only when the schedule is actually blown — the single line that sets the longest lead | every other line ships sooner, so moving any of them changes nothing. Pinning them would be twenty highlights on parts nobody needs to chase |
| `bom.single_source` | the **unrecorded** single-source lines only | a line with a qualified alternate or a written acceptance is a decision somebody made. Telling a reader that the thing they already did is still outstanding is how they learn to ignore the highlights |
| `bom.cost`, `bom.moq`, `bom.currency`, `bom.complete`, `bom.process_rules` | none yet | each of these already names its offending lines in `detail` and its evidence file; they are candidates for the same treatment and simply have not been given it |

At most twelve pins per verdict — a table is read row by row, and past a dozen
highlights the eye stops picking them out. The counts in `measured` and `detail`
are always the real ones: the cap trims the drawing, never the measurement.

A BOM line has no geometry and no position, so no locator here carries one.
Inventing a `position` for a purchase-order line would be the failure the site
contract names: a confident highlight somewhere nobody measured.

## 5. Units, currency and frames

- **Money** is in the document's `currency`, one code for the whole document. A
  line priced in another currency needs `fx_rates[<code>]` or it counts as
  **unpriced** — adding mixed currencies produces a number, and the number is wrong.
- **`fx_rates[CODE]` is the multiplier FROM the quoted currency TO the document's
  currency**: units of `currency` per ONE unit of `CODE`. A USD document holding
  EUR-quoted lines writes `{"EUR": 1.08}`, never `0.926`. This is the one
  convention in the pack that can silently halve or double a total, because no
  other number in the document contradicts a rate written backwards.
- **A missing `currency` is only allowed while nothing needs converting.** If the
  document declares none and every priced line names the same `price_currency`,
  that code IS the document's currency (it was read out of the file, not invented).
  Two different codes with no stated base leaves nothing to convert into, so those
  lines count as unpriced and `bom.currency` fails. This is the hole that let a
  document add 950 JPY to 0.31 EUR to 14.80 USD and report every line as priced.
- **Time** is in **weeks**, everywhere. Vendors quote in weeks, days and "ARO"
  (after receipt of order); convert on the way in and write down which.
- **Quantity** is pieces. `qty_per_unit` may be fractional (0.4 m of gasket cord);
  purchase quantities round up to the order multiple.
- **Lead time is effective lead time.** A line covered by stock counts as 0 weeks
  even if the factory lead is 26 — that stock is not yours until you buy it, which
  is the whole argument for reserving the riskiest line first.
- Thresholds resolve **BOM document first, model projection second, pack default
  last**. Five have defaults — `moq_ratio_limit`, `moq_excess_limit`,
  `moq_idle_limit_fraction`, `single_source_limit` and the `spares_fraction`
  ceiling — and each states its reason, and what was rejected, in `bomlib.py`.

## 6. The arithmetic in one paragraph

For each line the build *needs* `qty_per_unit x build_quantity x (1 + spares)`, and
*buys* that lifted to the MOQ and rounded up to the order multiple — the gap
between those two numbers is where a cheap part quietly becomes an expensive one.
Extended cost is purchase quantity x unit price, never need x price. Vendor
subtotals below a stated order minimum get the shortfall added. One-time charges
(tooling, setup, programming, a test fixture) are added to the run and amortised
across it, which is why per-unit cost falls with quantity and why a per-unit price
quoted at one quantity means nothing at another. Ship date is the longest effective
lead time in the BOM, because the build cannot ship before the last part arrives.
**Idle capital** is that same gap in money: `(purchased - needed) x price`, summed
over every line — what the order spends on pieces this run will not consume — and
it is bounded by a share of the parts spend rather than by a flat sum, because the
same figure is a sensible alarm on a small buy and an insult on a large one.

A sanity check on any result: if the per-unit cost moves sharply when you change
`build_quantity`, the run is dominated by one-time charges; if it barely moves, it
is dominated by parts, and negotiating tooling is wasted effort.

## 7. Common failure modes, and what they look like

- **`bom.cost` SKIPs with "N lines have no usable price".** Correct behaviour and
  the whole point: fix `bom.complete` first. A cost gate that treats unknowns as
  zero always errs in the direction that gets the build approved.
- **`bom.availability` FAILs with "no ship date".** Either no stock figure and no
  lead time, or stock below what the order needs with no lead time for the rest.
  Nobody knows when this ships, which is different from it being slow.
- **`bom.moq` passes with a 4x overbuy in the detail line.** Intended: 4x on a
  four-cent label is thirty dollars. The ratio names lines, it no longer decides
  what is measured — every line's idle money is counted whether it is past 3x or
  not, and a single line past 3x that idles more than `moq_excess_limit` fails on
  its own.
- **`bom.moq` SKIPs on an unpriced overbuy.** A 100x overbuy on a line with no
  price is not zero idle capital. Price it, or state `moq_excess_total_limit`.
- **`bom.availability` SKIPs with "no `lead_time_budget_weeks`".** Nothing is dead
  and every line has a ship date, but no schedule was ever written down to compare
  the longest lead against, so the claim goes BLOCKED rather than green.
- **`bom.availability` FAILs with "not a number of weeks".** A vendor wrote `ARO 8
  weeks` or `TBD` in the cell. ARO starts the clock at the purchase order, so the 8
  is not your schedule's number; convert on the way in. Unknown and named — never a
  crash, never a zero.
- **`bom.cost` FAILs while the per-unit figure is under budget.** An
  `order_minimums` key names a vendor no line buys from: a misspelling (a real
  minimum is then missing from that total) or an order nobody placed. Names match
  ignoring case and space, so what is left is a genuine mismatch.
- **`bom.currency` FAILs on a line you did price.** No `fx_rates` entry, an
  unusable rate, or no base currency. `bom.cost` SKIPs alongside it, on purpose.
- **`bom.single_source` FAILs on a part you already know about.** The fix is not a
  second vendor; it is `single_source_accepted: true` plus an `acceptance_note`
  saying what re-qualifying would cost. Write it down and the gate goes green,
  which is exactly the trade this pack wants you to make consciously.
- **`bom.process_rules` FAILs with "the design does not declare it".** A rule
  requires an attribute your `design` block never states. Unstated is not
  compliant; it is the thing that gets discovered at the quote, by someone else.
- **Everything SKIPs.** No `bom_path` in the projection. The claims are BLOCKED,
  not passed, and the readiness report will say so.

## 8. Where to look next

- `references/quote_checklist.md` — reading a vendor quote: what is excluded,
  what "price" means, what to ask before you accept it.
- `references/lead_time_reality.md` — what a lead time actually is, why it moves,
  and how to schedule against one.
- `references/stock_volatility.md` — why stock matters most for the part with the
  highest qualification cost, and the habit of reserving the riskiest line first.
- `sourcing.md` — the constraints that are not physics, condensed.
- `lenses.md` — the five questions to attack a supply chain with before you buy.
