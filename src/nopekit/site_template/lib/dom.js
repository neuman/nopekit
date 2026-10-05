// SPDX-License-Identifier: Apache-2.0
// dom.js — the only DOM helpers the renderer uses.
//
// Hyperscript rather than template strings, and that is a safety decision rather
// than a taste one. Every string on this page comes out of the ledger: a gate's
// `detail`, a parameter's rationale, a rejected alternative's `why`. Those are
// written by packs and by agents, and a `detail` containing `<b>` would be
// injected verbatim by an innerHTML renderer. `el()` only ever sets textContent,
// so the page cannot be made to execute what a gate said. There is exactly one
// place that parses markup — `svgFromText` in stage.js, for a DIAGRAM view whose
// whole payload is an SVG the project itself generated — and it says so there.

/** Create an element. `spec` accepts class, dataset, on<event> handlers and plain
 *  attributes; children may be nodes, strings, numbers or nested arrays, and
 *  null/undefined/false are dropped so callers can write `cond && el(...)`
 *  inline. */
export function el(tag, spec, ...children) {
  const node = document.createElement(tag);
  if (spec && (typeof spec !== "object" || spec instanceof Node || Array.isArray(spec))) {
    children.unshift(spec);
    spec = null;
  }
  for (const [key, value] of Object.entries(spec || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class" || key === "className") node.className = String(value);
    else if (key === "text") node.textContent = String(value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  append(node, children);
  return node;
}

export function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === "") continue;
    parent.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function svg(tag, spec, ...children) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(spec || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") { node.textContent = String(value); continue; }
    if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
      continue;
    }
    node.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function clear(node) {
  while (node && node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** Replace a host's contents in one shot. Used for every panel repaint: the page
 *  is small enough that rebuilding a section beats diffing one, and a renderer
 *  with no incremental update path has no stale-node class of bug at all. */
export function mount(host, ...children) {
  if (!host) return host;
  clear(host);
  return append(host, children);
}

/** A definition row: `<dt>` term, `<dd>` value. The provenance panels are all
 *  this shape, and a real <dl> keeps them readable to a screen reader and to
 *  anyone who prints the page. */
export function field(term, ...value) {
  return [el("dt", { text: term }), append(el("dd"), value)];
}
