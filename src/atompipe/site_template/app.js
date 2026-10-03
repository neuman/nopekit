// SPDX-License-Identifier: Apache-2.0
// app.js — the renderer. It reads site/data/state.json and nothing else.
//
// The page has two jobs and the second is the reason it is worth building:
//
//   EXPLAIN  someone who did not build this reads it and learns what the thing
//            is, what is proven, and what is not.
//   DEBUG    the owner opens it to find out WHY something is failing — gate
//            verdicts anchored to the geometry they are about.
//
// It never computes truth. Every status, count and sentence on this page was
// resolved by atompipe.claims and atompipe.report and written into state.json;
// this file chooses layout and wording for judgements it did not make. If a
// number here is wrong, the ledger is wrong (method rule 1: generated files are
// outputs, not sources). The one thing the page does decide is EMPHASIS — what
// goes above the fold — and it decides it the same way every time: the verdict
// first, unsoftened, before any picture.
//
// No build step, no framework, no bundler. Plain ES modules, loaded by the
// browser in the order they are needed, so the whole renderer can be read with
// `view-source` and audited by whoever has to trust it.

import { el, mount, $ } from "./lib/dom.js";
import { code, stamp, age, isAged, useWords } from "./lib/format.js";
import {
  headline, staleBanner, locatorProblems, claimsPanel, verdictsPanel,
  paramsPanel, evidencePanel, gapsPanel, decisionsPanel, aboutPanel, cssId,
} from "./lib/panels.js";
import { createStage, KEYS } from "./lib/stage.js";

const STATE_URL = "data/state.json";

const app = {
  state: null,
  index: { verdictByGate: new Map(), viewById: new Map(), claimById: new Map() },
  hash: parseHash(),
  stage: null,
  setHash,
  reveal,
  focusLocator,
};

boot();

async function boot() {
  const root = $("#app") || document.body;
  mount(root, el("p", { class: "booting", text: "Reading the ledger…" }));

  let state;
  try {
    const res = await fetch(STATE_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    state = await res.json();
  } catch (err) {
    mount(root, noState(err));
    return;
  }

  // ANNOTATIONS are authored, not generated: site/annotations.json is written by
  // whoever designed the thing, and it is the explaining half of the page — the
  // difference between a model that spins and a model that teaches. It is read
  // here rather than required in state.json so that editing it does not mean
  // rebuilding the site, and a project without one simply has no cards. A 404 is
  // the normal case and must stay silent; anything noisier trains readers to
  // ignore the page's warnings.
  if (!state.annotations) {
    try {
      const res = await fetch("annotations.json", { cache: "no-store" });
      if (res.ok) state.annotations = await res.json();
    } catch (err) {
      /* no annotations file: the common case, and not a problem */
    }
  }

  app.state = state;
  useWords(state.words, state.outcome_words);
  for (const v of state.verdicts || []) app.index.verdictByGate.set(v.gate, v);
  for (const v of state.views || []) app.index.viewById.set(v.id, v);
  for (const c of state.claims || []) app.index.claimById.set(c.id, c);

  document.documentElement.toggleAttribute("data-stale", !!(state.meta || {}).stale);
  const name = (state.meta || {}).name || "atompipe project";
  document.title = `${name} — ${(state.readiness || {}).all_required_checked ? "ready" : "not ready"}`;

  render(root, state);
}

function render(root, state) {
  app.stage = createStage(state, app);

  mount(root,
    topbar(state),
    headline(state, app),
    staleBanner(state),
    mixedAgeBanner(state),
    sectionNav(state),
    el("main", { class: "sheet" },
      app.stage.element,
      claimsPanel(state, app),
      verdictsPanel(state, app),
      locatorProblems(state),
      paramsPanel(state, app),
      evidencePanel(state, app),
      gapsPanel(state, app),
      decisionsPanel(state, app),
      aboutPanel(state)),
    keyDialog());

  wireKeys();
  applyHash();
}

// --------------------------------------------------------------------------- //
// chrome
// --------------------------------------------------------------------------- //
function topbar(state) {
  const meta = state.meta || {};
  return el("header", { class: "topbar" },
    el("div", { class: "ident" },
      el("h1", {}, meta.name || "untitled project",
        meta.revision ? el("span", { class: "rev", text: meta.revision }) : null),
      meta.summary ? el("p", { class: "summary", text: meta.summary }) : null),
    el("div", { class: "built" },
      meta.stale ? el("span", { class: "stale-flag", title: meta.stale_reason || "", text: "≈ STALE" }) : null,
      el("span", { class: "muted", text: meta.built ? `built ${stamp(meta.built)}` : "built (unstamped)" }),
      el("button", { class: "btn btn-ghost", type: "button", text: "Keys",
        onclick: () => document.getElementById("key-dialog").showModal() })));
}

function sectionNav(state) {
  const items = [
    ["views", "Views", (state.views || []).length],
    ["claims", "Claims", (state.claims || []).length],
    ["verdicts", "Gates", (state.verdicts || []).length],
    ["params", "Parameters", (state.params || []).length],
    ["evidence", "Evidence", (state.inputs || []).length],
    ["gaps", "Gaps", (state.gaps || []).length],
    ["decisions", "Decisions", (state.decisions || []).length],
    // Claims and gates are always listed, even at zero. An empty claims section
    // is a statement about the project ("nothing has been claimed") and quietly
    // dropping the link would read as the page not having that section at all.
  ].filter(([id, , n]) => n > 0 || id === "claims" || id === "verdicts");
  return el("nav", { class: "section-nav" }, ...items.map(([id, label, n]) =>
    el("a", { href: `#${id}`, class: "section-link" }, label,
      el("span", { class: "section-count", text: String(n) }))));
}

/** Verdicts of visibly different ages under one "built" timestamp.
 *
 *  `site build` deliberately does not re-run gates, and a `--only` sweep leaves
 *  the gates it skipped at their previous results — so a page can legitimately
 *  show a tier-0 number from a minute ago beside a tier-2 number from last week.
 *  That is honest, but only if it is SAID: a single build stamp at the top of the
 *  page otherwise reads as one timestamp over everything below it. */
function mixedAgeBanner(state) {
  const ages = (state.verdicts || []).map((v) => v.age_s).filter((a) => a !== null && a !== undefined);
  if (ages.length < 2) return null;
  const newest = Math.min(...ages), oldest = Math.max(...ages);
  if (!(isAged(oldest) && oldest > newest * 4)) return null;
  const stale = (state.verdicts || []).filter((v) => isAged(v.age_s));
  return el("aside", { class: "banner banner-age", role: "status" },
    el("span", { class: "banner-glyph", "aria-hidden": "true", text: "≈" }),
    el("div", {},
      el("b", { text: "These results are not all the same age. " }),
      `The oldest is ${age(oldest)}, the newest ${age(newest)}. `,
      el("span", { class: "muted", text:
        "Building the site does not re-run gates — a sweep that skipped the expensive ones " +
        "leaves them at their previous answer, and one timestamp over all of it would hide that." }),
      el("p", { class: "small" }, "Aged: ",
        ...stale.map((v) => el("button", { class: "linkish mono", type: "button", text: v.gate,
          onclick: () => reveal(`gate-${cssId(v.gate)}`) })))));
}

/** The page with no data, which is a page someone WILL hit — `site init` writes
 *  the shell and `site build` writes the JSON, and between those two commands
 *  this is what the site is. It has to say what to run, not go blank. */
function noState(err) {
  const isFile = location.protocol === "file:";
  return el("div", { class: "no-state" },
    el("h1", { text: "No ledger data on this page yet." }),
    el("p", { class: "mono small", text: `${STATE_URL} — ${err && err.message ? err.message : err}` }),
    isFile
      ? el("div", {},
          el("p", {}, el("b", { text: "This page was opened from the filesystem." }),
            " A browser will not let a module fetch a neighbouring file over ", code("file://"), "."),
          el("pre", { class: "cmd" }, code("atompipe site serve")),
          el("p", { class: "small muted" }, "which is ", code("python3 -m http.server"), " and nothing else."))
      : el("div", {},
          el("p", { text: "Build it from the project root:" }),
          el("pre", { class: "cmd" }, code("atompipe site build")),
          el("p", { class: "small muted" },
            "That runs the viewgens and writes ", code("site/data/state.json"),
            " from the ledger. It does not run gates — if a verdict is invalidated, ",
            code("atompipe check"), " first.")),
    el("p", { class: "small muted", text:
      "The site is an output. Nothing under site/data/ is hand-edited, and nothing here is a " +
      "source of truth about the project." }));
}

function keyDialog() {
  const groups = new Map();
  for (const k of KEYS) {
    if (!groups.has(k.scope)) groups.set(k.scope, []);
    groups.get(k.scope).push(k);
  }
  return el("dialog", { class: "key-dialog", id: "key-dialog" },
    el("form", { method: "dialog" },
      el("h2", { text: "Keyboard" }),
      ...Array.from(groups, ([scope, keys]) => el("section", {},
        el("h3", { text: scope }),
        el("dl", { class: "keys" }, ...keys.flatMap((k) => [
          el("dt", {}, ...k.keys.map((key) => el("kbd", { text: key }))),
          el("dd", { text: k.label }),
        ])))),
      el("button", { class: "btn", text: "Close" })));
}

// --------------------------------------------------------------------------- //
// wiring
// --------------------------------------------------------------------------- //
function wireKeys() {
  document.addEventListener("keydown", (ev) => {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const target = ev.target;
    if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA"
      || target.tagName === "SELECT" || target.isContentEditable)) return;
    if (ev.key === "?") { document.getElementById("key-dialog").showModal(); ev.preventDefault(); return; }
    if (app.stage && app.stage.handleKey(ev)) ev.preventDefault();
  });
  window.addEventListener("hashchange", () => { app.hash = parseHash(); applyHash(); });
}

/** Deep links, because a debugging session is something you send to somebody.
 *  `#view=assembly&gate=cad.clash` opens the page already looking at the problem
 *  rather than at the top of a long document with instructions to scroll. */
function parseHash() {
  const out = {};
  const raw = (location.hash || "").replace(/^#/, "");
  if (!raw) return out;
  if (!raw.includes("=")) return { anchor: raw };
  for (const part of raw.split("&")) {
    const [key, value] = part.split("=");
    if (key) out[decodeURIComponent(key)] = decodeURIComponent(value || "");
  }
  return out;
}

let hashWriting = false;
function setHash(patch) {
  const next = { ...app.hash, ...patch };
  delete next.anchor;
  const text = Object.entries(next).filter(([, v]) => v)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
  app.hash = next;
  hashWriting = true;
  history.replaceState(null, "", text ? `#${text}` : location.pathname + location.search);
  hashWriting = false;
}

function applyHash() {
  if (hashWriting) return;
  const h = app.hash;
  if (h.anchor) { reveal(h.anchor); return; }
  if (h.view && app.stage) app.stage.showView(h.view);
  if (h.gate) reveal(`gate-${cssId(h.gate)}`);
  else if (h.claim) reveal(`claim-${h.claim}`);
  else if (h.param) reveal(`param-${cssId(h.param)}`);
}

/** Scroll something into view and OPEN IT. Every provenance panel is a
 *  `<details>`; linking to a collapsed one lands the reader on a closed row with
 *  no sign that the thing they clicked for is inside it. */
function reveal(id) {
  const node = document.getElementById(id);
  if (!node) return false;
  for (let parent = node; parent; parent = parent.parentElement) {
    if (parent.tagName === "DETAILS") parent.open = true;
  }
  const own = node.querySelector(":scope > details");
  if (own) own.open = true;
  node.scrollIntoView({ behavior: "smooth", block: "center" });
  node.classList.add("flash");
  setTimeout(() => node.classList.remove("flash"), 1600);
  return true;
}

function focusLocator(locator, verdict) {
  if (app.stage) app.stage.focusLocator(locator, verdict);
}
