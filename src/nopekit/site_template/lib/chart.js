// SPDX-License-Identifier: Apache-2.0
// chart.js — a CHART view: the series, the acceptance limit as a line, and where
// the current design sits on it.
//
// The limit line is the whole reason this kind exists. "0.42 mm against a 0.5 mm
// limit" is a number a reader has to do arithmetic on; the same pair drawn with
// the design's point sitting just under a dashed line is a margin you can see,
// and the SHAPE of the curve as it approaches the limit is what says whether the
// design is robust or merely lucky. That distinction never survives a table.
//
// Hand-built SVG, no plotting library: the site has no build step and the one
// artifact whose job is to be trusted when a gate says something is wrong should
// not pull a megabyte of third-party code to draw six hundred points.
//
// PAYLOAD SHAPE. No pack emitted a chart before this renderer existed, so this
// comment is the contract until PACK.md files start quoting it. Everything is
// optional and every spelling below is accepted, because a viewgen author should
// not have to guess between `points` and `x`/`y`:
//
//   view.data = {
//     series: { "tip": [[x, y], ...] }
//           | [ { key|name: "tip", label?, points?: [[x,y],...],
//                 x?: [...], y?: [...], style?: "line"|"scatter"|"area" } ],
//     limit:  0.5
//           | { value, label?, comparator?: "<="|">="|"<"|">", axis?: "y"|"x" }
//           | [ ...the above... ],
//     current: { x?, y, label? } | [ ... ],       // where this design sits
//     x: { label?, units?, ticks?: n }, y: { ...same... },
//   }
//
// `view.meta` is read for `x_label` / `y_label` / `x_units` / `y_units` as well,
// since a viewgen that put axis metadata in meta rather than data is making a
// reasonable choice and should not get an unlabelled chart for it.
//
// Locators address a chart by SERIES KEY (`target`) or by an x value
// (`position: [x]`). A located series is drawn heavier with its points marked; a
// located x gets a vertical rule. Same rule as everywhere else: shape and label
// first, colour second.

import { el, svg } from "./dom.js";
import { num } from "./format.js";
import { severityOf } from "./panels.js";

const W = 840, H = 460;
const PAD = { l: 74, r: 26, t: 26, b: 56 };

export function renderChart(view, locators = []) {
  const data = view.data || {};
  const meta = view.meta || {};
  const series = normaliseSeries(data.series);
  const axes = {
    x: { label: pick(data.x && data.x.label, meta.x_label, ""), units: pick(data.x && data.x.units, meta.x_units, "") },
    y: { label: pick(data.y && data.y.label, meta.y_label, ""), units: pick(data.y && data.y.units, meta.y_units, "") },
  };
  const limits = normaliseLimits(data.limit ?? data.limits ?? meta.limit);
  const currents = normaliseCurrent(data.current ?? data.marker ?? data.now);

  if (!series.length && !currents.length) {
    return el("div", { class: "chart-empty" },
      el("p", { text: "This chart view carries no series." }),
      el("p", { class: "small muted", text:
        "A viewgen returning an empty chart rather than None is the one case the site cannot " +
        "draw its way out of — there is nothing to plot." }));
  }

  // Domain from every point, both limit lines and the current marker: a limit
  // drawn off the edge of the plot is a limit nobody can read a margin against,
  // which defeats the point of drawing it at all.
  const xs = [], ys = [];
  for (const s of series) for (const [x, y] of s.points) { if (isNum(x)) xs.push(x); if (isNum(y)) ys.push(y); }
  for (const l of limits) (l.axis === "x" ? xs : ys).push(l.value);
  for (const c of currents) { if (isNum(c.x)) xs.push(c.x); if (isNum(c.y)) ys.push(c.y); }
  for (const loc of locators) {
    const p = loc.position;
    if (Array.isArray(p) && isNum(p[0])) xs.push(p[0]);
    if (Array.isArray(p) && isNum(p[1])) ys.push(p[1]);
  }

  const xd = domain(xs), yd = domain(ys, { padFraction: 0.08 });
  const sx = (v) => PAD.l + ((v - xd[0]) / (xd[1] - xd[0] || 1)) * (W - PAD.l - PAD.r);
  const sy = (v) => H - PAD.b - ((v - yd[0]) / (yd[1] - yd[0] || 1)) * (H - PAD.t - PAD.b);

  const locSeries = new Set(locators.map((l) => l.target).filter(Boolean));
  const kids = [];

  // grid + axes
  for (const t of ticks(yd)) {
    kids.push(svg("line", { class: "grid", x1: PAD.l, x2: W - PAD.r, y1: sy(t), y2: sy(t) }));
    kids.push(svg("text", { class: "tick tick-y", x: PAD.l - 10, y: sy(t) + 4, "text-anchor": "end", text: num(t) }));
  }
  for (const t of ticks(xd)) {
    kids.push(svg("line", { class: "grid grid-v", x1: sx(t), x2: sx(t), y1: PAD.t, y2: H - PAD.b }));
    kids.push(svg("text", { class: "tick tick-x", x: sx(t), y: H - PAD.b + 22, "text-anchor": "middle", text: num(t) }));
  }
  kids.push(svg("line", { class: "axis", x1: PAD.l, x2: W - PAD.r, y1: H - PAD.b, y2: H - PAD.b }));
  kids.push(svg("line", { class: "axis", x1: PAD.l, x2: PAD.l, y1: PAD.t, y2: H - PAD.b }));
  if (axisLabel(axes.x)) {
    kids.push(svg("text", { class: "axis-label", x: (PAD.l + W - PAD.r) / 2, y: H - 12,
      "text-anchor": "middle", text: axisLabel(axes.x) }));
  }
  if (axisLabel(axes.y)) {
    kids.push(svg("text", { class: "axis-label", x: 16, y: (PAD.t + H - PAD.b) / 2,
      "text-anchor": "middle", transform: `rotate(-90 16 ${(PAD.t + H - PAD.b) / 2})`, text: axisLabel(axes.y) }));
  }

  // The forbidden side of each limit, shaded. Faint on purpose: it says which way
  // is bad without turning the plot into a warning poster.
  for (const limit of limits) {
    if (limit.axis === "y") {
      const y = sy(limit.value);
      const above = limit.comparator.startsWith("<");
      kids.push(svg("rect", {
        class: "limit-bad", x: PAD.l, width: W - PAD.l - PAD.r,
        y: above ? PAD.t : y, height: Math.max(0, above ? y - PAD.t : (H - PAD.b) - y),
      }));
      kids.push(svg("line", { class: "limit", x1: PAD.l, x2: W - PAD.r, y1: y, y2: y }));
      kids.push(svg("text", { class: "limit-label", x: W - PAD.r - 6, y: y - 8, "text-anchor": "end",
        text: limit.label || `limit ${limit.comparator} ${num(limit.value)}${axes.y.units ? " " + axes.y.units : ""}` }));
    } else {
      const x = sx(limit.value);
      kids.push(svg("line", { class: "limit", x1: x, x2: x, y1: PAD.t, y2: H - PAD.b }));
      kids.push(svg("text", { class: "limit-label", x: x + 6, y: PAD.t + 14,
        text: limit.label || `limit ${limit.comparator} ${num(limit.value)}` }));
    }
  }

  // locator rules on the x axis
  for (const loc of locators) {
    const p = loc.position;
    if (!Array.isArray(p) || !isNum(p[0]) || loc.target) continue;
    const x = sx(p[0]);
    kids.push(svg("line", { class: `loc-rule sev-${severityOf(loc, null)}`, x1: x, x2: x, y1: PAD.t, y2: H - PAD.b }));
    if (loc.label) {
      kids.push(svg("text", { class: "loc-rule-label", x: x + 5, y: H - PAD.b - 8, text: loc.label }));
    }
  }

  // series
  series.forEach((s, i) => {
    const located = locSeries.has(s.key);
    const pts = s.points.filter(([x, y]) => isNum(x) && isNum(y));
    if (!pts.length) return;
    const d = pts.map(([x, y], j) => `${j ? "L" : "M"}${sx(x).toFixed(2)},${sy(y).toFixed(2)}`).join(" ");
    if (s.style !== "scatter") {
      kids.push(svg("path", { class: `series s${i % 6}${located ? " located" : ""}`, d, fill: "none" }));
    }
    if (s.style === "scatter" || located || pts.length <= 24) {
      for (const [x, y] of pts) {
        kids.push(svg("circle", { class: `point s${i % 6}${located ? " located" : ""}`, cx: sx(x), cy: sy(y), r: located ? 4 : 2.5 }));
      }
    }
    const [lx, ly] = pts[pts.length - 1];
    kids.push(svg("text", { class: `series-label s${i % 6}${located ? " located" : ""}`,
      x: Math.min(sx(lx) + 8, W - PAD.r), y: sy(ly) - 8, "text-anchor": "end", text: s.label }));
  });

  // where this design sits, with the distance to the limit spelled out
  for (const c of currents) {
    const x = isNum(c.x) ? sx(c.x) : (W - PAD.r + PAD.l) / 2;
    const y = sy(c.y);
    kids.push(svg("line", { class: "current-stem", x1: x, x2: x, y1: y, y2: H - PAD.b }));
    kids.push(svg("circle", { class: "current-halo", cx: x, cy: y, r: 9 }));
    kids.push(svg("circle", { class: "current", cx: x, cy: y, r: 4.5 }));
    const gap = marginText(c, limits, axes);
    kids.push(svg("text", { class: "current-label", x: x + 12, y: y - 12,
      text: c.label || "as designed" }));
    if (gap) kids.push(svg("text", { class: "current-margin", x: x + 12, y: y + 4, text: gap }));
  }

  const figure = el("figure", { class: "chart" },
    svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", preserveAspectRatio: "xMidYMid meet",
      "aria-label": view.title || "chart" }, ...kids),
    el("figcaption", { class: "chart-caption" },
      series.length
        ? el("ul", { class: "chart-legend" }, ...series.map((s, i) => el("li", { class: `s${i % 6}${locSeries.has(s.key) ? " located" : ""}` },
            el("span", { class: "swatch", "aria-hidden": "true" }), s.label)))
        : null,
      limits.length ? el("span", { class: "legend-limit" }, el("span", { class: "swatch swatch-limit", "aria-hidden": "true" }), "acceptance limit") : null,
      currents.length ? el("span", { class: "legend-current" }, el("span", { class: "swatch swatch-current", "aria-hidden": "true" }), "this design") : null));
  return figure;
}

/** The distance from the design point to the limit, in the axis's own units.
 *
 *  This is a SUBTRACTION OF TWO NUMBERS THE LEDGER ALREADY PUBLISHED, and it is
 *  phrased as a distance ("0.20 mm under the limit") rather than as a judgement.
 *  The page does not get to decide whether that margin is a pass: the verdict
 *  does, it is printed beside this chart, and if the two ever disagreed the
 *  ledger would be the one telling the truth. */
function marginText(current, limits, axes) {
  const limit = limits.find((l) => l.axis === "y");
  if (!limit || !isNum(current.y)) return "";
  const delta = current.y - limit.value;
  const under = limit.comparator.startsWith("<") ? delta < 0 : delta > 0;
  const units = axes.y.units ? ` ${axes.y.units}` : "";
  return `${num(Math.abs(delta))}${units} ${under ? "inside" : "outside"} the limit`;
}

// --------------------------------------------------------------------------- //
// payload normalisation — liberal in what it accepts
// --------------------------------------------------------------------------- //
function normaliseSeries(raw) {
  const out = [];
  if (!raw) return out;
  if (Array.isArray(raw)) {
    raw.forEach((s, i) => {
      if (Array.isArray(s)) { out.push({ key: `s${i}`, label: `series ${i + 1}`, points: pairs(s), style: "line" }); return; }
      if (!s || typeof s !== "object") return;
      const key = String(s.key || s.name || `s${i}`);
      out.push({ key, label: String(s.label || s.name || key), style: s.style || "line", points: pointsOf(s) });
    });
  } else if (typeof raw === "object") {
    for (const [key, value] of Object.entries(raw)) {
      out.push({ key, label: key, style: "line", points: Array.isArray(value) ? pairs(value) : pointsOf(value) });
    }
  }
  return out.filter((s) => s.points.length);
}

function pointsOf(s) {
  if (!s) return [];
  if (Array.isArray(s.points)) return pairs(s.points);
  if (Array.isArray(s.y)) {
    const xs = Array.isArray(s.x) ? s.x : s.y.map((_, i) => i);
    return s.y.map((y, i) => [Number(xs[i]), Number(y)]);
  }
  return [];
}

function pairs(list) {
  return list.map((p, i) => (Array.isArray(p) ? [Number(p[0]), Number(p[1])]
    : (p && typeof p === "object") ? [Number(p.x ?? i), Number(p.y)]
    : [i, Number(p)]));
}

function normaliseLimits(raw) {
  const list = raw === null || raw === undefined ? [] : (Array.isArray(raw) ? raw : [raw]);
  return list.map((l) => (typeof l === "object" && l !== null
    ? { value: Number(l.value ?? l.limit), label: l.label || "", comparator: String(l.comparator || "<="), axis: l.axis === "x" ? "x" : "y" }
    : { value: Number(l), label: "", comparator: "<=", axis: "y" }))
    .filter((l) => isNum(l.value));
}

function normaliseCurrent(raw) {
  const list = raw === null || raw === undefined ? [] : (Array.isArray(raw) ? raw : [raw]);
  return list.map((c) => (typeof c === "object" && c !== null
    ? { x: Number(c.x), y: Number(c.y ?? c.value), label: c.label || "" }
    : { x: NaN, y: Number(c), label: "" }))
    .filter((c) => isNum(c.y));
}

const isNum = (v) => typeof v === "number" ? Number.isFinite(v) : Number.isFinite(Number(v));
const pick = (...values) => values.find((v) => v !== undefined && v !== null && v !== "") || "";
const axisLabel = (a) => [a.label, a.units && `(${a.units})`].filter(Boolean).join(" ");

function domain(values, { padFraction = 0.04 } = {}) {
  const nums = values.map(Number).filter(Number.isFinite);
  if (!nums.length) return [0, 1];
  let lo = Math.min(...nums), hi = Math.max(...nums);
  if (lo === hi) { const d = Math.abs(lo) * 0.1 || 1; lo -= d; hi += d; }
  const pad = (hi - lo) * padFraction;
  // Zero is kept in frame when the data is all one sign: a deflection curve that
  // starts at the origin and is drawn from 0.31 to 0.72 hides how much of the
  // budget is already spent.
  if (lo > 0 && lo < (hi - lo)) lo = 0; else lo -= pad;
  return [lo, hi + pad];
}

function ticks([lo, hi], count = 5) {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag * 10;
  const out = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + 1e-9; t += step) out.push(Number(t.toPrecision(12)));
  return out;
}
