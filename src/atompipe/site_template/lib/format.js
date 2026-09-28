// SPDX-License-Identifier: Apache-2.0
// format.js — how the ledger's vocabulary is spoken on the page.
//
// Nothing here decides anything. Every status on this page arrives already
// resolved in state.json (atompipe.claims resolves claims, atompipe.report
// writes the readiness sentence); this module only chooses the word, the glyph
// and the class name for a status the ledger already reached. The moment a
// function in here starts inferring a status from a measurement, the page has a
// second opinion about the project and the reader has no way to tell which of
// the two is the real one.
//
// EVERY STATUS CARRIES A GLYPH AND A WORD, never a colour alone. Colour is the
// third channel, not the first: a reader with a colour vision deficiency, a
// greyscale print of the readiness page, and a photograph of a laptop screen in
// a workshop all have to survive the distinction between "failing" and "proven",
// and two shades of a traffic light do not survive any of them.

import { el } from "./dom.js";

/** Claim statuses, as ClaimStatus in atompipe.models. Unknown values fall back
 *  to a neutral chip that prints the raw string — a newer spine inventing a
 *  status must not blank out a claim row on an older page. */
const CLAIM_STATUS = {
  pass:       { label: "PROVEN",     glyph: "✓", tone: "ok",    hint: "every covering gate ran and passed" },
  fail:       { label: "FAILING",    glyph: "✕", tone: "bad",   hint: "a covering gate failed" },
  stale:      { label: "STALE",      glyph: "≈", tone: "warn",  hint: "it passed, but the inputs have moved since — nothing is proven now" },
  unclaimed:  { label: "NO GATE",    glyph: "?", tone: "warn",  hint: "nothing covers this claim — a capability gap" },
  blocked:    { label: "BLOCKED",    glyph: "⊘", tone: "warn",  hint: "a gate covers it but its tooling is missing, so nothing was proven" },
  pending:    { label: "NOT RUN",    glyph: "◌", tone: "warn",  hint: "gates exist and have never run" },
  unverified: { label: "UNVERIFIED", glyph: "◻", tone: "phys",  hint: "physical: awaiting a result from a real object" },
  verified:   { label: "VERIFIED",   glyph: "✓", tone: "ok",    hint: "physical: a human recorded a real-world pass" },
  refuted:    { label: "REFUTED",    glyph: "✕", tone: "bad",   hint: "physical: a human recorded a real-world failure" },
  asserted:   { label: "ASSUMED",    glyph: "≡", tone: "assum", hint: "standing assumption, carried in the open and unevidenced" },
};

/** Verdict statuses, as written by site.state(): pass | fail | skipped | errored.
 *  `skipped` and `errored` are deliberately NOT neutral greys — a gate whose
 *  solver is missing proved nothing, and an errored gate reads louder still,
 *  because a crash is a defect in the check itself. */
const VERDICT_STATUS = {
  pass:    { label: "PASS",    glyph: "✓", tone: "ok",   hint: "the gate ran and the measurement holds" },
  fail:    { label: "FAIL",    glyph: "✕", tone: "bad",  hint: "the gate ran and refused" },
  skipped: { label: "SKIPPED", glyph: "⊘", tone: "warn", hint: "the gate did not run — nothing was proven" },
  errored: { label: "ERRORED", glyph: "!", tone: "bad",  hint: "the gate crashed — nothing was proven, and the check itself is broken" },
};

const CLAIM_KIND = {
  measurable: {
    title: "Measurable",
    blurb: "A gate settles these from the model. This is the only kind a machine proves.",
  },
  physical: {
    title: "Physical",
    blurb: "Only a real object settles these. The pipeline never proves them; it carries them, " +
           "visibly, until a human records a result.",
  },
  assumption: {
    title: "Assumptions",
    blurb: "Taken on faith and written down so they stay visible. An assumption nobody recorded " +
           "is the one that sinks the build.",
  },
};

export function claimStatus(key) {
  return CLAIM_STATUS[key] || { label: String(key || "unknown").toUpperCase(), glyph: "·", tone: "muted", hint: "" };
}

export function verdictStatus(key) {
  return VERDICT_STATUS[key] || { label: String(key || "unknown").toUpperCase(), glyph: "·", tone: "muted", hint: "" };
}

export function claimKind(key) {
  return CLAIM_KIND[key] || { title: String(key || "other"), blurb: "" };
}

/** The status chip. Glyph, then word, then colour — in that order of importance. */
export function chip(status, { small = false, title = "" } = {}) {
  return el("span", { class: `chip tone-${status.tone}${small ? " chip-sm" : ""}`, title: title || status.hint },
    el("span", { class: "chip-glyph", "aria-hidden": "true", text: status.glyph }),
    el("span", { class: "chip-label", text: status.label }));
}

/** A plain labelled tag with no status semantics (tier, pack, kind, count). */
export function tag(text, { tone = "muted", title = "" } = {}) {
  return el("span", { class: `tag tone-${tone}`, title, text });
}

// --------------------------------------------------------------------------- //
// numbers and time
// --------------------------------------------------------------------------- //

/** Four significant figures, trailing zeros stripped, never exponent soup for
 *  ordinary engineering magnitudes. A deflection of 0.7000000000000001 mm is the
 *  same measurement as 0.7 mm and one of the two spellings makes a reader
 *  distrust the page. */
export function num(value, { digits = 4 } = {}) {
  if (value === null || value === undefined || value === "") return "";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (n === 0) return "0";
  const magnitude = Math.abs(n);
  if (magnitude >= 1e6 || magnitude < 1e-4) return n.toExponential(2).replace("e", "e");
  return String(Number(n.toPrecision(digits)));
}

export function quantity(value, units) {
  const text = num(value);
  if (!text) return "";
  return units ? `${text} ${units}` : text;
}

/** Human age from seconds. `null` is NOT zero — site.state() dates a verdict
 *  from the run that last wrote or hit its entry, else the commit that brought
 *  it, and writes a null age when it knows neither; a zero renders as "just
 *  now", which is the exact lie a staleness display exists to prevent. */
export function age(seconds) {
  if (seconds === null || seconds === undefined) return "age unknown";
  const s = Math.max(0, Number(seconds) || 0);
  if (s < 90) return `${Math.round(s)}s ago`;
  const m = s / 60;
  if (m < 90) return `${Math.round(m)}m ago`;
  const h = m / 60;
  if (h < 36) return `${Math.round(h)}h ago`;
  const d = h / 24;
  if (d < 14) return `${Math.round(d)}d ago`;
  const w = d / 7;
  if (w < 9) return `${Math.round(w)}w ago`;
  return `${Math.round(d / 30)} months ago`;
}

/** Anything older than this reads as aged on the page. It is a DISPLAY
 *  threshold: it marks a row so a reader looks, and it never changes a status.
 *  Whether a result is actually invalidated is the resolver's judgement, per
 *  gate (each row's stale_reason, and meta.stale), not a clock's: an old result
 *  whose inputs have not moved is current, and a fresh one whose inputs moved
 *  a second later is not. */
export const AGED_SECONDS = 24 * 3600;

export function isAged(seconds) {
  return seconds !== null && seconds !== undefined && Number(seconds) >= AGED_SECONDS;
}

/** ISO timestamp -> a local, readable stamp with the raw value on hover.
 *  Unparseable input is printed verbatim rather than swallowed: a malformed
 *  timestamp in the ledger is a thing to see, not to hide. */
export function stamp(iso) {
  const raw = String(iso || "").trim();
  if (!raw) return "";
  const date = new Date(raw.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return raw;
  return date.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : (many || one + "s")}`;
}

/** A gate id, a node name, a parameter: rendered monospace so an identifier is
 *  never mistaken for prose, and copyable as one word. */
export function code(text, extra = "") {
  return el("code", { class: `mono ${extra}`.trim(), text: String(text) });
}
