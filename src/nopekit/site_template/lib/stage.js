// SPDX-License-Identifier: Apache-2.0
// stage.js — the view stage and the verdict overlay.
//
// One host for all six view kinds, because the site has no idea what domain it
// is looking at and that is what lets the same page serve a bracket, a boat and
// a chemical process. A pack emits a view in a kind the renderer already knows
// and gets visualisation for free; nothing here loads pack JavaScript.
//
// The overlay is the part that matters. Verdicts are grouped by the view their
// locators address, failing ones first, and selecting one isolates and frames
// its targets. Every kind implements the same two verbs — highlight these
// targets, frame these targets — so "Show me" behaves identically whether the
// answer is a part in an assembly, a row in a bill of materials, a series on a
// curve or a hotspot on a photograph.

import { el, mount, append } from "./dom.js";
import { tag, chip, verdictStatus, num, quantity, plural, code } from "./format.js";
import { panelHead, shapeFor, severityOf, cssId, annotationCard } from "./panels.js";
import { renderChart } from "./chart.js";
import { createViewer } from "./viewer3d.js";

const VIEWER_KINDS = new Set(["model3d", "field"]);

/** Keyboard shortcuts, declared ONCE and rendered into the legend from this same
 *  table. Shortcuts that live only in a keydown switch are folklore: the two
 *  people who know them use them and nobody else finds out they exist. */
export const KEYS = [
  { keys: ["←", "→"], label: "explode out / in", scope: "3D" },
  { keys: ["0"], label: "collapse the explode", scope: "3D" },
  { keys: ["9"], label: "fully exploded", scope: "3D" },
  { keys: ["W"], label: "wireframe", scope: "3D" },
  { keys: ["I"], label: "isolate the selected part", scope: "3D" },
  { keys: ["F"], label: "frame everything", scope: "3D" },
  { keys: ["R"], label: "reset the view", scope: "3D" },
  { keys: ["Esc"], label: "clear isolate and selection", scope: "3D" },
  { keys: ["N", "P"], label: "next / previous view", scope: "any view" },
  { keys: ["J", "K"], label: "next / previous anchored verdict", scope: "any view" },
  { keys: ["?"], label: "show these keys", scope: "anywhere" },
];

export function createStage(state, app) {
  const views = (state.views || []).slice();
  const host = el("div", { class: "stage-canvas" });
  const side = el("aside", { class: "stage-side" });
  const controls = el("div", { class: "stage-controls" });
  const tabs = el("nav", { class: "view-tabs", role: "tablist" });
  const note = el("div", { class: "stage-note" });

  let active = null;         // the view object
  let viewer = null;         // live viewer3d api, when the active view is 3D
  let selected = null;       // selected node name (3D) / row id / series key
  let focusIndex = -1;       // position in the active view's anchored verdict list

  const element = el("section", { class: "panel panel-stage", id: "views" },
    panelHead("Views", views.length ? plural(views.length, "view") : "",
      "Gate results, anchored to the thing they are about."),
    views.length ? tabs : null,
    el("div", { class: "stage-grid" },
      el("div", { class: "stage-main" }, host, note, controls),
      side));

  if (!views.length) {
    mount(host, el("div", { class: "stage-empty" },
      el("p", { text: "This project has no views." }),
      el("p", { class: "small muted", text:
        "That is a normal state, not a missing feature: a project with no geometry — a " +
        "process, a supply chain, a design that has not been exported yet — still has " +
        "claims, verdicts, evidence and a readiness sentence, and they are all below. " +
        "A pack that emits a view gets a picture here for free." })));
    mount(side, unanchoredList(state, app));
  }

  // ------------------------------------------------------------------- //
  // view selection
  // ------------------------------------------------------------------- //
  function paintTabs() {
    mount(tabs, ...views.map((v) => {
      const anchored = anchoredVerdicts(v.id);
      const failing = anchored.filter((x) => !x.verdict.ok).length;
      return el("button", {
        class: `view-tab${active && active.id === v.id ? " is-active" : ""}`,
        type: "button", role: "tab", "aria-selected": String(!!active && active.id === v.id),
        onclick: () => showView(v.id),
      },
        el("span", { class: "view-tab-kind", text: v.kind }),
        el("span", { class: "view-tab-title", text: v.title || v.id }),
        failing ? el("span", { class: "view-tab-bad", title: `${failing} failing here`, text: `✕${failing}` }) : null);
    }));
  }

  async function showView(id, { scroll = false } = {}) {
    const next = views.find((v) => v.id === id) || views[0];
    if (!next) return;
    if (viewer) { viewer.dispose(); viewer = null; }
    active = next;
    selected = null;
    focusIndex = -1;
    paintTabs();
    mount(note);
    mount(controls);
    mount(host, el("p", { class: "stage-loading", text: `Loading ${next.title || next.id}…` }));
    app.setHash({ view: next.id });

    // A payload too big to inline was split into data/views/<id>.json by
    // `site build`. Fetched on demand so a forty-row bill of materials does not
    // sit in the one file the page reads on first paint.
    if (next.data_url && !(next.data && Object.keys(next.data).length)) {
      try {
        const res = await fetch(next.data_url, { cache: "no-store" });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        next.data = await res.json();
      } catch (err) {
        mount(note, problem(`${next.data_url} did not load (${err.message}). Re-run \`nopekit site build\`.`));
      }
    }
    if (active !== next) return;                 // a faster click won
    renderActive();
    paintSide();
    if (scroll) element.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderActive() {
    const kind = String(active.kind || "image");
    mount(host);
    if (VIEWER_KINDS.has(kind)) return renderModel();
    if (kind === "chart") return mount(host, renderChart(active, locatorsFor(active.id)));
    if (kind === "table") return mount(host, renderTable(active, locatorsFor(active.id), app));
    if (kind === "diagram") return renderDiagram(active, host, note);
    if (kind === "image") return mount(host, renderImage(active, locatorsFor(active.id), app));
    // The renderer knows six kinds and nothing else, by design — extensibility
    // lives in the data. A seventh kind is a pack talking to a newer site than
    // this one, and saying so beats rendering a blank panel.
    return mount(host, el("div", { class: "stage-empty" },
      el("p", {}, "This view is of kind ", code(kind), ", which this renderer does not know."),
      el("p", { class: "small muted", text:
        "The site renders model3d, image, chart, table, field and diagram. A pack emitting " +
        "anything else is ahead of the page: upgrade the site template, or re-emit the view " +
        "in a kind the renderer implements." })));
  }

  // ------------------------------------------------------------------- //
  // model3d / field
  // ------------------------------------------------------------------- //
  function renderModel() {
    const frame = el("div", { class: "viewer" });
    mount(host, frame);
    if (!active.src) {
      mount(host, problem("This 3D view carries no asset. The viewgen produced a record but no GLB."));
      return;
    }
    viewer = createViewer({
      host: frame,
      view: active,
      onSelect: (name) => { selected = name; paintSide(); paintControls(); },
      onReady: () => { applyOverlay(); paintControls(); paintSide(); },
      onError: (err) => {
        // The degraded path, and it is a real path rather than an apology. The
        // parts and the verdicts anchored to them are exactly what a reader
        // needs; the spinning picture is the part that is optional.
        viewer = null;
        mount(host, el("div", { class: "viewer-fallback" },
          el("p", { class: "tone-bad" }, el("b", { text: "No 3D viewer. " }), err.message),
          el("p", { class: "small muted", text:
            "Everything the viewer would have shown is still addressable: the parts this " +
            "view declares are listed here, and the verdicts anchored to them are beside it." }),
          nodeList(active, app)));
        paintControls();
        paintSide();
      },
    });
  }

  /** Annotations addressed at one view. Authored, never derived — see
   *  annotationCard in panels.js. An annotation with no `view` belongs to the
   *  only view there is, which is the common single-assembly case and saves an
   *  author restating it on every entry. */
  function annotationsFor(viewId) {
    return (state.annotations || []).filter((a) =>
      (a.view || (views.length === 1 ? viewId : "")) === viewId);
  }

  function applyOverlay() {
    if (!viewer || !viewer.ready) return;
    const entries = [];
    const highlights = new Map();
    for (const { locator, verdict } of anchoredVerdicts(active.id)) {
      const severity = severityOf(locator, verdict);
      if (locator.target) highlights.set(locator.target, severity);
      entries.push({
        locator, verdict, severity,
        label: locator.label || `${verdict.gate}${locator.value !== null && locator.value !== undefined ? " " + num(locator.value) : ""}`,
        onclick: (loc, v) => selectLocator(loc, v),
      });
    }
    // Annotation pins ride in the same overlay as the verdict pins, at info
    // severity. They are the EXPLAIN half: a reader who did not build this needs
    // "the arm clamps here and why" as much as the owner needs "this clashes".
    for (const annot of annotationsFor(active.id)) {
      for (const anchor of annot.anchors || []) {
        entries.push({
          locator: { view: active.id, target: anchor, severity: "info" },
          verdict: null, severity: "info", label: annot.title || annot.id,
          onclick: () => { viewer.frame(annot.anchors || [anchor]); openAnnotation(annot); },
        });
      }
    }
    viewer.highlight(highlights);
    const missing = viewer.setPins(entries);
    // A locator naming a node this GLB does not contain is REPORTED, never
    // dropped. `nopekit site build` catches the same class of mismatch against
    // the view's declared node list; this catches the case where the declaration
    // and the actual export disagree, which nothing upstream can see.
    if (missing.length) {
      mount(note, problem(
        `${plural(missing.length, "locator")} could not be placed: this model has no node named ` +
        `${missing.map((m) => `'${m}'`).join(", ")}. The node names a viewgen exports are the ` +
        `interface its pack's gates address — one side of it has moved.`));
    }
  }

  function paintControls() {
    if (!active || !VIEWER_KINDS.has(String(active.kind))) return mount(controls);
    const disabled = !viewer || !viewer.ready;
    const movers = disabled ? [] : viewer.movers;
    const canExplode = !disabled && viewer.canExplode;
    const slider = el("input", {
      type: "range", min: "0", max: "100", value: "0", class: "explode",
      "aria-label": "explode", disabled: !canExplode,
      oninput: (ev) => { viewer && viewer.setExplode(Number(ev.target.value) / 100); },
    });
    const isolateSelect = el("select", {
      class: "isolate-select", disabled, "aria-label": "isolate a part",
      onchange: (ev) => { viewer && viewer.isolate(ev.target.value || null); paintSide(); },
    },
      el("option", { value: "", text: "All parts" }),
      ...movers.map((m) => el("option", { value: m, text: m, selected: viewer && viewer.isolated === m })));

    mount(controls,
      el("div", { class: "control" },
        el("label", { class: "control-label", text: "Explode" }),
        slider,
        el("span", { class: "key-hint", text: "← →" })),
      el("div", { class: "control" },
        el("label", { class: "control-label", text: "Isolate" }),
        isolateSelect,
        el("span", { class: "key-hint", text: "I" })),
      el("div", { class: "control control-buttons" },
        el("button", { class: "btn", type: "button", disabled, text: "Wireframe",
          onclick: (ev) => { const on = ev.currentTarget.getAttribute("aria-pressed") !== "true";
            ev.currentTarget.setAttribute("aria-pressed", String(on)); viewer && viewer.setWireframe(on); },
          "aria-pressed": "false" }),
        el("span", { class: "key-hint", text: "W" }),
        el("button", { class: "btn", type: "button", disabled, text: "Frame",
          onclick: () => viewer && viewer.frameAll() }),
        el("span", { class: "key-hint", text: "F" }),
        el("button", { class: "btn", type: "button", disabled, text: "Reset",
          onclick: () => { viewer && viewer.reset(); applyOverlay(); paintControls(); paintSide(); } }),
        el("span", { class: "key-hint", text: "R" })),
      keyLegend());
  }

  // ------------------------------------------------------------------- //
  // the overlay rail
  // ------------------------------------------------------------------- //
  function openAnnotation(note) {
    const card = document.getElementById(`annot-${cssId(note.id || note.title || "note")}`);
    if (card) { card.classList.add("is-open"); card.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
  }

  function paintSide() {
    if (!active) return;
    const annotations = annotationsFor(active.id);
    const anchored = anchoredVerdicts(active.id);
    const failing = anchored.filter((a) => !a.verdict.ok);
    mount(side,
      el("h3", { class: "side-head" }, "Anchored here",
        tag(String(anchored.length), { tone: failing.length ? "bad" : "muted" })),
      anchored.length
        ? el("ul", { class: "anchor-list" }, ...anchored.map((a, i) => anchorRow(a, i)))
        : el("p", { class: "small muted", text:
            "No verdict points at this view. Either nothing here is in question, or the gates " +
            "that cover it could not say where the problem was — an unlocatable failure carries " +
            "no anchor rather than guessing at one." }),
      active.description ? el("p", { class: "view-desc", text: active.description }) : null,
      annotations.length
        ? el("div", { class: "annots" },
            el("h3", { class: "side-head" }, "What this is", tag(String(annotations.length))),
            ...annotations.map((a) => annotationCard(a, app, {
              onFocus: (note) => {
                if (viewer && viewer.ready) viewer.frame(note.anchors || []);
                openAnnotation(note);
              },
            })))
        : null,
      VIEWER_KINDS.has(String(active.kind)) && viewer && viewer.ready ? partsList() : null,
      unanchoredList(state, app));
  }

  function anchorRow({ locator, verdict }, index) {
    const severity = severityOf(locator, verdict);
    return el("li", { class: `anchor sev-${severity}${index === focusIndex ? " is-focused" : ""}` },
      el("button", { class: "anchor-btn", type: "button", onclick: () => selectLocator(locator, verdict, index) },
        el("span", { class: "anchor-shape", "aria-hidden": "true", text: shapeFor(locator, verdict) }),
        el("span", { class: "anchor-body" },
          el("span", { class: "anchor-gate mono", text: verdict.gate }),
          el("span", { class: "anchor-target mono", text: locator.target || "whole view" }),
          el("span", { class: "anchor-label", text: locator.label || verdict.detail || "" }),
          locator.value !== null && locator.value !== undefined
            ? el("span", { class: "anchor-value mono", text: quantity(locator.value, verdict.units) })
            : null)),
      el("button", { class: "linkish small", type: "button", text: "verdict →",
        onclick: () => app.reveal(`gate-${cssId(verdict.gate)}`) }));
  }

  function partsList() {
    const isolated = viewer.isolated;
    return el("details", { class: "parts", open: false },
      el("summary", {}, "Parts ", tag(String(viewer.movers.length))),
      el("ul", { class: "part-list" }, ...viewer.movers.map((name) => el("li", {
        class: `${isolated === name ? "is-isolated" : ""}${selected === name ? " is-selected" : ""}`,
      },
        el("button", { class: "linkish mono", type: "button", text: name,
          onclick: () => { viewer.isolate(isolated === name ? null : name); paintSide(); paintControls(); } })))));
  }

  /** Failures that point nowhere. Shown beside the stage on purpose: a reader
   *  looking at a clean picture must not conclude that everything is fine
   *  because the only failing gate could not say where it was. */
  function unanchoredList(state, app) {
    const orphans = (state.verdicts || []).filter((v) => v.unanchored && v.status !== "skipped");
    if (!orphans.length) return null;
    return el("div", { class: "orphans" },
      el("h3", { class: "side-head" }, "Not anchored", tag(String(orphans.length), { tone: "warn" })),
      el("p", { class: "small muted", text: "These verdicts know something is wrong, not where." }),
      el("ul", { class: "orphan-list" }, ...orphans.map((v) => el("li", {},
        chip(verdictStatus(v.status), { small: true }),
        el("button", { class: "linkish mono", type: "button", text: v.gate,
          onclick: () => app.reveal(`gate-${cssId(v.gate)}`) })))));
  }

  // ------------------------------------------------------------------- //
  // focus: the "show me" verb, per kind
  // ------------------------------------------------------------------- //
  function selectLocator(locator, verdict, index) {
    const anchored = anchoredVerdicts(active ? active.id : "");
    focusIndex = index !== undefined ? index
      : anchored.findIndex((a) => a.locator === locator && a.verdict === verdict);
    const kind = String(active.kind || "");
    if (VIEWER_KINDS.has(kind) && viewer && viewer.ready) {
      const targets = siblingTargets(verdict, active.id);
      if (locator.target) viewer.isolate(null);
      viewer.frame(targets.length ? targets : [locator.target].filter(Boolean));
      applyOverlay();
    } else if (kind === "table" || kind === "chart" || kind === "image" || kind === "diagram") {
      markFlat(locator);
    }
    paintSide();
    app.setHash({ view: active.id, gate: verdict.gate });
  }

  /** Every target of the same verdict, not only the one clicked. A clash is a
   *  RELATIONSHIP: framing just `back_left` shows a part with nothing obviously
   *  wrong with it, and framing both parts shows the overlap. */
  function siblingTargets(verdict, viewId) {
    return (verdict.locators || [])
      .filter((l) => l.view === viewId && l.target)
      .map((l) => l.target);
  }

  function markFlat(locator) {
    for (const node of host.querySelectorAll(".is-located")) node.classList.remove("is-located");
    const hit = locator.target && host.querySelector(`[data-target="${CSS.escape(locator.target)}"]`);
    if (hit) {
      hit.classList.add("is-located");
      hit.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  // ------------------------------------------------------------------- //
  // helpers over the ledger
  // ------------------------------------------------------------------- //
  /** Locators addressing one view, worst first. Failing verdicts lead because
   *  they are the reason anyone opened the page in debug mode. */
  function anchoredVerdicts(viewId) {
    const out = [];
    for (const verdict of state.verdicts || []) {
      for (const locator of verdict.locators || []) {
        if (locator.view === viewId) out.push({ locator, verdict });
      }
    }
    const rank = (a) => (a.verdict.error ? 0 : !a.verdict.passed ? 1 : a.verdict.skipped ? 2 : 3);
    return out.sort((a, b) => rank(a) - rank(b) || String(a.verdict.gate).localeCompare(String(b.verdict.gate)));
  }

  const locatorsFor = (viewId) => anchoredVerdicts(viewId).map((a) => a.locator);

  // ------------------------------------------------------------------- //
  // keyboard
  // ------------------------------------------------------------------- //
  function handleKey(ev) {
    if (!active) return false;
    const is3d = VIEWER_KINDS.has(String(active.kind)) && viewer && viewer.ready;
    const step = (delta) => {
      const slider = controls.querySelector(".explode");
      if (!slider || slider.disabled) return;
      slider.value = String(Math.max(0, Math.min(100, Number(slider.value) + delta)));
      viewer.setExplode(Number(slider.value) / 100);
    };
    switch (ev.key) {
      case "ArrowRight": if (is3d) { step(+8); return true; } return false;
      case "ArrowLeft": if (is3d) { step(-8); return true; } return false;
      case "0": if (is3d) { step(-100); return true; } return false;
      case "9": if (is3d) { step(+100); return true; } return false;
      case "w": case "W": if (is3d) {
        const btn = Array.from(controls.querySelectorAll(".btn")).find((b) => b.textContent === "Wireframe");
        btn && btn.click(); return true;
      } return false;
      case "i": case "I": if (is3d && selected) { viewer.isolate(viewer.isolated ? null : selected); paintSide(); paintControls(); return true; } return false;
      case "f": case "F": if (is3d) { viewer.frameAll(); return true; } return false;
      case "r": case "R": if (is3d) { viewer.reset(); applyOverlay(); paintControls(); paintSide(); return true; } return false;
      case "Escape": if (is3d) { selected = null; viewer.isolate(null); paintSide(); paintControls(); return true; } return false;
      case "n": case "N": cycleView(+1); return true;
      case "p": case "P": cycleView(-1); return true;
      case "j": case "J": cycleAnchor(+1); return true;
      case "k": case "K": cycleAnchor(-1); return true;
      default: return false;
    }
  }

  function cycleView(delta) {
    if (views.length < 2) return;
    const i = views.findIndex((v) => active && v.id === active.id);
    showView(views[(i + delta + views.length) % views.length].id);
  }

  function cycleAnchor(delta) {
    const anchored = anchoredVerdicts(active.id);
    if (!anchored.length) return;
    const next = (focusIndex + delta + anchored.length) % anchored.length;
    selectLocator(anchored[next].locator, anchored[next].verdict, next);
  }

  if (views.length) showView((app.hash.view && views.some((v) => v.id === app.hash.view)) ? app.hash.view : views[0].id);

  return {
    element,
    showView,
    handleKey,
    get active() { return active; },
    focusLocator(locator, verdict) {
      const go = () => selectLocator(locator, verdict);
      if (active && active.id === locator.view) { go(); element.scrollIntoView({ behavior: "smooth", block: "start" }); }
      else showView(locator.view, { scroll: true }).then(go);
    },
  };
}

// --------------------------------------------------------------------------- //
// the flat kinds
// --------------------------------------------------------------------------- //

/** TABLE. Columns may be strings or records; rows may be objects keyed by column
 *  or arrays in column order. Sorting is client-side and presentational — it
 *  reorders what the ledger said, it never recomputes it. */
export function renderTable(view, locators, app) {
  const data = view.data || {};
  const rows = Array.isArray(data.rows) ? data.rows : [];
  if (!rows.length) return el("p", { class: "stage-empty", text: "This table view carries no rows." });

  const columns = normaliseColumns(data.columns, rows);
  const located = new Map(locators.filter((l) => l.target).map((l) => [l.target, l]));
  let sortKey = null, sortDir = 1;

  const table = el("table", { class: "data-table" });
  const paint = () => {
    const body = rows.slice();
    if (sortKey) {
      body.sort((a, b) => {
        const x = cellValue(a, sortKey), y = cellValue(b, sortKey);
        const nx = Number(x), ny = Number(y);
        const cmp = Number.isFinite(nx) && Number.isFinite(ny)
          ? nx - ny : String(x).localeCompare(String(y));
        return cmp * sortDir;
      });
    }
    mount(table,
      el("thead", {}, el("tr", {}, ...columns.map((c) => el("th", {
        class: `${c.align === "right" ? "right" : ""}${sortKey === c.key ? " sorted" : ""}`,
        scope: "col",
        onclick: () => { if (sortKey === c.key) sortDir = -sortDir; else { sortKey = c.key; sortDir = 1; } paint(); },
      }, c.label, c.units ? el("span", { class: "col-units", text: c.units }) : null,
         sortKey === c.key ? el("span", { class: "sort-arrow", text: sortDir > 0 ? "▲" : "▼" }) : null)))),
      el("tbody", {}, ...body.map((row) => {
        const id = String(row.id ?? "");
        const loc = located.get(id);
        return el("tr", {
          class: loc ? `located sev-${severityOf(loc, null)}` : "",
          dataset: loc ? { target: id } : {},
        },
          ...columns.map((c, i) => el(i === 0 ? "th" : "td", {
            class: c.align === "right" ? "right mono" : (typeof cellValue(row, c.key) === "number" ? "mono" : ""),
            scope: i === 0 ? "row" : null,
          },
            i === 0 && loc ? el("span", { class: "row-flag", "aria-hidden": "true", text: shapeFor(loc, null) }) : null,
            formatCell(cellValue(row, c.key)),
            i === 0 && loc && loc.label ? el("span", { class: "row-note", text: loc.label }) : null)));
      })));
  };
  paint();
  return el("div", { class: "table-wrap" }, table);
}

function normaliseColumns(columns, rows) {
  if (Array.isArray(columns) && columns.length) {
    return columns.map((c) => (typeof c === "string"
      ? { key: c, label: c, units: "", align: "" }
      : { key: String(c.key || c.name), label: String(c.label || c.key || c.name), units: c.units || "", align: c.align || "" }));
  }
  const keys = [];
  for (const row of rows) for (const key of Object.keys(row || {})) if (!keys.includes(key)) keys.push(key);
  return keys.map((k) => ({ key: k, label: k, units: "", align: "" }));
}

function cellValue(row, key) {
  if (!row) return "";
  if (Array.isArray(row)) return row[Number(key)] ?? "";
  if (row.cells && key in row.cells) return row.cells[key];
  return row[key] ?? "";
}

function formatCell(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return num(value, { digits: 6 });
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** IMAGE. Hotspots are in normalised 0..1 coordinates so the overlay survives a
 *  re-render at another resolution — an absolute pixel hotspot is correct for
 *  exactly one export of one render. */
export function renderImage(view, locators, app) {
  const data = view.data || {};
  const spots = new Map((data.hotspots || []).map((h) => [String(h.id), h]));
  const wrap = el("div", { class: "image-wrap" });
  if (!view.src) return el("p", { class: "stage-empty", text: "This image view carries no asset." });
  const img = el("img", { class: "view-image", src: view.src, alt: view.title || view.id,
    onerror: () => mount(wrap, problem(`${view.src} did not load. \`nopekit site build\` writes it.`)) });
  const layer = el("div", { class: "hotspot-layer" });
  for (const loc of locators) {
    const spot = loc.target ? spots.get(String(loc.target)) : null;
    const xy = spot ? [spot.x, spot.y] : (Array.isArray(loc.position) ? loc.position : null);
    if (!xy || !Number.isFinite(Number(xy[0]))) continue;
    layer.appendChild(el("button", {
      class: `pin sev-${severityOf(loc, null)}`, type: "button",
      dataset: { target: loc.target || "" },
      style: { left: `${Number(xy[0]) * 100}%`, top: `${Number(xy[1]) * 100}%` },
      title: loc.label || loc.target || "",
    },
      el("span", { class: "pin-shape", "aria-hidden": "true", text: shapeFor(loc, null) }),
      el("span", { class: "pin-text", text: loc.label || (spot && spot.label) || loc.target || "" })));
  }
  return append(wrap, [img, layer]);
}

/** DIAGRAM. An SVG is inlined so its element ids stay addressable by a locator;
 *  anything else is shown as the source it is.
 *
 *  The inlined markup is a GENERATED PROJECT ASSET from this site's own
 *  `assets/` directory — the same trust level as the GLB next to it — and
 *  `<script>` elements are stripped anyway, because "it came from our own build"
 *  is a sentence that stays true only until someone ingests a third-party SVG
 *  and a viewgen copies it through. */
export async function renderDiagram(view, host, note) {
  const data = view.data || {};
  if (data.mermaid) {
    mount(host, el("figure", { class: "diagram-source" },
      el("figcaption", { text: "mermaid source" }),
      el("pre", {}, code(String(data.mermaid)))));
    mount(note, el("p", { class: "small muted", text:
      "Rendered as source: the site ships no mermaid renderer, because the one artifact " +
      "whose job is to be trusted should not pull a second library to draw a box. A viewgen " +
      "that emits SVG gets a drawn diagram and addressable element ids." }));
    return;
  }
  const markup = data.svg || "";
  if (!markup && !view.src) {
    mount(host, el("p", { class: "stage-empty", text: "This diagram view carries neither an asset nor inline SVG." }));
    return;
  }
  if (!markup && !/\.svg($|\?)/i.test(view.src)) {
    mount(host, el("div", { class: "image-wrap" }, el("img", { class: "view-image", src: view.src, alt: view.title || view.id })));
    return;
  }
  let text = markup;
  if (!text) {
    try {
      const res = await fetch(view.src, { cache: "no-store" });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      text = await res.text();
    } catch (err) {
      mount(host, problem(`${view.src} did not load (${err.message}).`));
      return;
    }
  }
  const doc = new DOMParser().parseFromString(text, "image/svg+xml");
  const svgRoot = doc.documentElement;
  if (!svgRoot || svgRoot.nodeName === "parsererror") {
    mount(host, problem("that SVG did not parse."));
    return;
  }
  svgRoot.querySelectorAll("script, foreignObject").forEach((n) => n.remove());
  svgRoot.setAttribute("class", "view-diagram");
  mount(host, el("div", { class: "diagram-wrap" }, document.importNode(svgRoot, true)));
}

/** The declared parts of a 3D view, without the 3D. Used when three.js is not
 *  available at all, which is the environment vendoring exists for. */
export function nodeList(view, app) {
  const meta = view.meta || {};
  const movers = Object.keys((meta.explode && meta.explode.movers) || {});
  const nodes = (meta.nodes || []).map(String);
  const names = movers.length ? movers : nodes;
  if (!names.length) return el("p", { class: "small muted", text: "This view declares no node names." });
  return el("ul", { class: "part-list flat" }, ...names.map((n) => el("li", {}, code(n))));
}

function problem(text) {
  return el("p", { class: "stage-problem" },
    el("span", { class: "banner-glyph", "aria-hidden": "true", text: "!" }), text);
}

function keyLegend() {
  return el("details", { class: "key-legend" },
    el("summary", {}, "Keys"),
    el("dl", { class: "keys" }, ...KEYS.flatMap((k) => [
      el("dt", {}, ...k.keys.map((key) => el("kbd", { text: key }))),
      el("dd", {}, k.label, el("span", { class: "muted small", text: ` (${k.scope})` })),
    ])));
}
