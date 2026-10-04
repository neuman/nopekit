// SPDX-License-Identifier: Apache-2.0
// panels.js — everything on the page that is not the view stage.
//
// The headline, the claims, the gate verdicts, the parameters with their
// rejected alternatives, the ingested evidence, the capability gaps and the
// decision log. This is the EXPLAIN half of the site, and it is also the half
// that has to survive a project with no geometry at all: a chemical process or a
// supply chain builds a site with zero views, and every panel below still has
// its full content. 3D is one view kind among six, not the point.
//
// Two rules run through all of it:
//
//  * Nothing is softened. The headline sentence comes from the ledger's own
//    readiness logic and is printed as written, above the fold, before any
//    picture. A project page that leads with a render while a claim is failing
//    is marketing.
//  * Proven, physical and assumed never share a list. They are three different
//    kinds of knowledge and the credibility of the first one comes entirely from
//    the other two being visible next to it (method rule 9).

import { el, mount, field } from "./dom.js";
import {
  chip, tag, claimStatus, verdictStatus, claimKind,
  num, quantity, age, isAged, stamp, plural, code, phrase, needWord,
} from "./format.js";

/** `invalidated` -> `Invalidated`, for a field label. */
function capital(text) {
  const s = String(text || "");
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

// --------------------------------------------------------------------------- //
// headline
// --------------------------------------------------------------------------- //

/** The verdict band. First thing under the project name, and unsoftened.
 *
 *  `readiness.verdict` is written by atompipe.report — the same sentence the
 *  readiness document leads with. It is rendered verbatim rather than
 *  paraphrased, so the page and the document cannot drift into two different
 *  opinions about the same ledger, and the one more people read is not the one
 *  that quietly got friendlier. */
export function headline(state, app) {
  const r = state.readiness || {};
  const meta = state.meta || {};
  // READY only when every required claim reads Checked (GLOSSARY §4) —
  // `all_required_checked`, never `ready`, which keeps "nothing stops check"
  // for older readers. What slipped through: READY over a project whose
  // claim waited for an article.
  const ready = !!r.all_required_checked;
  const tally = r.tally || [];
  const blocking = r.blocking || [];

  return el("section", { class: `headline ${ready ? "is-ready" : "is-not-ready"}`, id: "verdict" },
    el("div", { class: "headline-mark", "aria-hidden": "true", text: ready ? "✓" : "✕" }),
    el("div", { class: "headline-body" },
      el("p", { class: "headline-status" },
        el("span", { class: "headline-word", text: ready ? "READY" : "NOT READY" }),
        meta.revision ? el("span", { class: "headline-rev", text: meta.revision }) : null),
      el("p", { class: "headline-verdict", text: r.verdict || "No readiness sentence in the ledger." }),
      blocking.length
        ? el("p", { class: "headline-blocking" },
            el("span", { class: "muted", text: "blocking: " }),
            ...blocking.map((id) => el("button", {
              class: "linkish mono", type: "button", text: id,
              onclick: () => app.reveal(`claim-${id}`),
            })))
        : null,
      // The strip is `readiness.tally`: report.count_bits' items, each label
      // already in HUMAN's words with a crash counted apart ("7 skipped (6
      // errored)"), toned by its status (a crash's in Failing's).
      el("ul", { class: "counts" },
        countItem(r.n_claims, r.n_claims === 1 ? "claim" : "claims"),
        ...tally.map((t) => labelItem(t.label,
          claimStatus(t.status, { errored: t.errored > 0 }).tone)),
        countItem(r.n_gates, r.n_gates === 1 ? "gate" : "gates"))));
}

function countItem(n, noun, tone = "muted") {
  if (n === null || n === undefined) return null;
  return el("li", { class: `count tone-${tone}` },
    el("b", { class: "mono", text: String(n) }), " ", noun);
}

function labelItem(label, tone) {
  const [n, ...rest] = String(label).split(" ");
  return el("li", { class: `count tone-${tone}` },
    el("b", { class: "mono", text: n }), " ", rest.join(" "));
}

/** The staleness band. Shown ONLY when the resolver says a verdict is not
 *  current — `meta.stale` and `meta.stale_reason` are written by atompipe.site
 *  from `verdicts.resolve`, per gate (an input moved, the code moved, a control
 *  never shown to fail at this version), never guessed here from a clock. The
 *  banner is loud on purpose and the whole page gets `data-stale`, which
 *  desaturates the stage and hatches the header: a stale result has to LOOK
 *  stale, because the failure mode is a reader trusting a green page that
 *  describes last week's geometry. */
export function staleBanner(state) {
  const meta = state.meta || {};
  if (!meta.stale) return null;
  return el("aside", { class: "banner banner-stale", role: "status" },
    el("span", { class: "banner-glyph", "aria-hidden": "true", text: "≈" }),
    el("div", {},
      el("b", { text: "Some verdicts are invalidated." }),
      " ",
      el("span", { text: meta.stale_reason || "a verdict's inputs have moved since it was measured" }),
      el("p", { class: "banner-fix" }, "Re-run ", code("atompipe check"),
        " and then ", code("atompipe site build"), " to settle them against what the model says now.")));
}

/** Locator problems: a gate that believes it is drawing and is not.
 *
 *  This banner exists because that failure is silent on both ends — the pack
 *  author sees a clean import and the reader sees a verdict with no highlight,
 *  which is indistinguishable from a verdict that never had one. atompipe.site
 *  refuses to drop a bad locator for exactly this reason; the page's job is to
 *  make the report impossible to walk past. */
export function locatorProblems(state) {
  const problems = state.locator_problems || [];
  if (!problems.length) return null;
  return el("section", { class: "panel", id: "locator-problems" },
    panelHead("Locators that cannot be drawn", `${problems.length}`,
      "A gate attached a highlight to something this site cannot find. The verdict is " +
      "still true; it just has no anchor, and a gate that thinks it is drawing and is " +
      "not looks exactly like a gate that found nothing."),
    el("ul", { class: "problem-list" },
      ...problems.map((p) => el("li", {},
        code(p.gate), " → ", code(`${p.view || "(no view)"}${p.target ? "/" + p.target : ""}`),
        el("span", { class: "problem-why", text: p.problem })))));
}

// --------------------------------------------------------------------------- //
// claims
// --------------------------------------------------------------------------- //

/** Every claim, grouped by kind, each with the gate and measured value that
 *  settled it — or the reason nothing did.
 *
 *  `row.unproven` is the load-bearing detail: each covering gate that produced
 *  no pass that counts, with its reason led by the fact (errored, skipped,
 *  unqualified, unrun), straight from the readiness report's own coverage
 *  logic. Under GLOSSARY §3's composition a Checked claim has none, so PARTIAL
 *  went (P2.1); a claim the resolver calls Checked while one exists is a
 *  contradiction, `row.disagree`, painted loud. The claims arrive in severity
 *  order (`state.json`), and the page keeps it. */
export function claimsPanel(state, app) {
  const claims = state.claims || [];
  const groups = ["measurable", "physical", "assumption"];
  const seen = new Set();
  const sections = [];

  for (const kind of groups) {
    const rows = claims.filter((c) => (c.kind || "measurable") === kind);
    rows.forEach((c) => seen.add(c.id));
    if (!rows.length) continue;
    const info = claimKind(kind);
    sections.push(el("div", { class: `claim-group group-${kind}` },
      el("h3", { class: "group-head" },
        el("span", { text: info.title }),
        tag(String(rows.length))),
      el("p", { class: "group-blurb", text: info.blurb }),
      el("ul", { class: "claim-list" }, ...rows.map((c) => claimRow(c, state, app)))));
  }
  // Anything with an unexpected kind still gets rendered. A claim silently
  // missing from the page is the one failure this panel must not have.
  const rest = claims.filter((c) => !seen.has(c.id));
  if (rest.length) {
    sections.push(el("div", { class: "claim-group" },
      el("h3", { class: "group-head" }, el("span", { text: "Other" }), tag(String(rest.length))),
      el("ul", { class: "claim-list" }, ...rest.map((c) => claimRow(c, state, app)))));
  }

  return el("section", { class: "panel", id: "claims" },
    panelHead("Claims", plural(claims.length, "claim"),
      "What has to be true for this design to work, and what settled it."),
    ...(sections.length ? sections : [emptyNote("This project has recorded no claims yet. " +
      "A claim is a file, `claims/<id>.json`, and it is where a project starts: a design with " +
      "no claims has nothing to prove.")]));
}

function claimRow(claim, state, app) {
  const status = claimStatus(claim.status, { errored: !!claim.errored });
  const verdicts = (claim.verdicts || []).map((g) => app.index.verdictByGate.get(g)).filter(Boolean);
  // The claim's own value: the first verdict state.json says is compared with
  // its acceptance condition (`claim.compared`, P2.4) — never one this page
  // picks. What slipped through: the first passing verdict was the headline,
  // so failing C1 showed its guard's passing L/h. None compared, none shown.
  const compared = (claim.compared || []).map((g) => app.index.verdictByGate.get(g)).filter(Boolean);
  const headline = compared.length ? compared[0] : null;

  const body = el("div", { class: "claim-detail" },
    el("dl", { class: "kv" },
      ...(claim.acceptance_render ? field("Acceptance", code(claim.acceptance_render)) : []),
      ...(claim.rationale ? field("Why it matters", claim.rationale) : []),
      ...(claim.source ? field("Source", claim.source) : []),
      ...(claim.note ? field("Note", claim.note) : []),
      ...(claim.tags || []).length ? field("Tags", ...claim.tags.map((t) => tag(t))) : [],
      ...(claim.grounded_by || []).length
        ? field("Grounded by", ...claim.grounded_by.map((id) => el("button", {
            class: "linkish mono", type: "button", text: id,
            onclick: () => app.reveal(`input-${id}`),
          })))
        : []),
    claim.physical_result ? physicalResult(claim.physical_result) : null,
    verdicts.length
      ? el("div", { class: "claim-verdicts" },
          el("h4", { text: "Gates" }),
          el("ul", { class: "mini-verdicts" },
            ...verdicts.map((v) => miniVerdict(v, app))))
      : null,
    // Why the claim reads what it reads: state.json's `reason`, the ledger's
    // words (no evaluator, unrun, no owner recorded, needs an article…) — none
    // here. What slipped through (review of P2.1): with no verdict this panel
    // said "No gate covers this claim" itself, the old NO GATE meaning, for an
    // unowned assumption and a claim waiting on an article alike; and a
    // Checked claim's reason, `—`, rendered as a lone dash.
    claim.reason && claim.cause !== "checked"
      ? el("p", { class: "claim-reason", text: claim.reason }) : null,
    (claim.unproven || []).length
      ? el("div", { class: claim.disagree ? "partial-note tone-bad" : "partial-note" },
          claim.disagree ? el("b", { text: "Status and evidence disagree — " }) : null,
          "a gate covering this claim produced no verdict that counts:",
          el("ul", {}, ...claim.unproven.map((u) =>
            el("li", {}, code(u.gate), " — ", u.why))))
      : null,
    (claim.evidence || []).length
      ? el("div", { class: "evidence-row" },
          el("h4", { text: "Evidence" }),
          el("ul", { class: "file-list" }, ...claim.evidence.map((e) => fileRef(e))))
      : null);

  const summary = el("summary", { class: "claim-summary" },
    chip(status),
    el("span", { class: "claim-id mono", text: claim.id }),
    el("span", { class: "claim-statement", text: claim.statement || "(no statement)" }),
    claim.disagree ? tag("status and evidence disagree", { tone: "bad",
      title: "the resolver and the verdicts contradict each other — a defect to report" }) : null,
    claim.critical === false ? tag("not required", { tone: "muted" }) : null,
    // Beside it, the CLAIM's limit (`claim.limit_text`, written by site.state),
    // never the verdict's: a compared pair is the one kind whose two limits can
    // part. What slipped through (review of P2.4): Failing C3 summarised as
    // `0.195 MPa / 15 MPa` — its evaluator's own limit — against `<= 0.1 MPa`.
    headline && headline.measured !== null && headline.measured !== undefined
      ? el("span", { class: "claim-measure mono", title: `measured by ${headline.gate}` },
          quantity(headline.measured, headline.units || claim.acceptance?.units || ""),
          claim.limit_text
            ? el("span", { class: "muted", text: ` / ${claim.limit_text}` })
            : null)
      : null);

  return el("li", { class: `claim tone-${claim.disagree ? "bad" : status.tone}`, id: `claim-${claim.id}` },
    el("details", { class: "disclosure" }, summary, body));
}

function physicalResult(result) {
  const ok = !!result.passed;
  return el("div", { class: `physical-result ${ok ? "tone-ok" : "tone-bad"}` },
    el("b", { text: ok ? "A physical result was recorded: pass. " : "A physical result was recorded: fail. " }),
    el("span", { text: result.detail || "" }),
    el("p", { class: "muted small" },
      [result.who, stamp(result.when)].filter(Boolean).join(" · ")),
    (result.evidence || []).length
      ? el("ul", { class: "file-list" }, ...result.evidence.map((e) => fileRef(e)))
      : null);
}

// --------------------------------------------------------------------------- //
// verdicts
// --------------------------------------------------------------------------- //

/** The gate results, newest judgement first in reading order: what failed, what
 *  never ran, then what passed.
 *
 *  Sorted that way deliberately. A list in alphabetical order buries the one
 *  failing gate among fourteen passing ones, and the reason anybody opens this
 *  page in DEBUG mode is the failing one. */
const VERDICT_ORDER = { errored: 0, fail: 1, skipped: 2, pass: 3 };

export function verdictsPanel(state, app) {
  const all = state.verdicts || [];
  const host = el("ul", { class: "verdict-list" });
  const filters = ["problems", "all"];
  let mode = all.some((v) => v.status !== "pass") ? "problems" : "all";

  const paint = () => {
    const rows = (mode === "all" ? all : all.filter((v) => v.status !== "pass"))
      .slice()
      .sort((a, b) => (VERDICT_ORDER[a.status] ?? 9) - (VERDICT_ORDER[b.status] ?? 9)
        || String(a.gate).localeCompare(String(b.gate)));
    mount(host, rows.length
      ? rows.map((v) => verdictRow(v, app))
      : el("li", { class: "muted", text: "Every gate that ran, passed." }));
  };

  const switcher = el("div", { class: "seg", role: "group", "aria-label": "filter verdicts" },
    ...filters.map((f) => el("button", {
      class: "seg-btn", type: "button", "aria-pressed": String(f === mode),
      text: f === "problems" ? "Problems only" : "All gates",
      onclick: (ev) => {
        mode = f;
        ev.currentTarget.parentElement.querySelectorAll(".seg-btn")
          .forEach((b) => b.setAttribute("aria-pressed", String(b === ev.currentTarget)));
        paint();
      },
    })));

  paint();
  return el("section", { class: "panel", id: "verdicts" },
    panelHead("Gate results", plural(all.length, "verdict"),
      "One line per gate. A skip is not a pass and a crash is not a pass; both are " +
      "listed as what they are.", switcher),
    host);
}

function verdictRow(v, app) {
  const status = verdictStatus(v.status);
  const locators = v.locators || [];
  const anchored = locators.filter((loc) => app.index.viewById.has(loc.view));

  // An unqualified evaluator's row says what state.json words for it, never
  // `error` — R-2's fallback, which carries the spine's token (review of P2.3).
  const unqualified = v.qualification && v.qualification.text;
  const detail = unqualified || v.error || v.detail || v.skip_reason || "";
  const measured = v.measured !== null && v.measured !== undefined
    ? el("span", { class: "verdict-measure mono" },
        quantity(v.measured, v.units),
        v.limit !== null && v.limit !== undefined
          ? el("span", { class: "muted", text: ` (limit ${quantity(v.limit, v.units)})` })
          : null)
    : null;

  const body = el("div", { class: "verdict-detail" },
    detail ? el("p", { class: `verdict-text${v.error && !unqualified ? " mono" : ""}`,
                       text: detail }) : null,
    el("dl", { class: "kv" },
      ...(v.pack ? field("Pack", code(v.pack)) : []),
      ...field("Tier", `${v.tier} — ${["instant", "build", "solve", "external"][v.tier] || "?"}`),
      ...(v.duration_s ? field("Ran in", `${num(v.duration_s)} s`) : []),
      // `when` is the site's, from the resolver: the run that last wrote or hit
      // this result, else the commit that brought it. Neither known: said so.
      ...field("Recorded", v.when ? `${stamp(v.when)} · ${age(v.age_s)}` : "no date recorded for this result"),
      ...(v.stale_reason ? field(capital(phrase("invalidated")), v.stale_reason) : []),
      ...(v.claims || []).length
        ? field("Settles", ...v.claims.map((c) => el("button", {
            class: "linkish mono", type: "button", text: c,
            onclick: () => app.reveal(`claim-${c}`),
          })))
        : [],
      ...(v.evidence || []).length ? field("Evidence", el("ul", { class: "file-list" },
        ...v.evidence.map((e) => fileRef(e)))) : []),
    // An unlocatable failure is a NORMAL outcome, said out loud. A gate attaches
    // a locator only when it genuinely knows the position, because a confident
    // highlight on the wrong part sends someone to inspect a part that is fine
    // and they stop trusting the overlay afterwards.
    v.unanchored
      ? el("p", { class: "muted small", text:
          "This verdict carries no locator, so there is nothing to highlight. The gate " +
          "knows that something is wrong, not where." })
      : null,
    anchored.length
      ? el("div", { class: "locator-row" },
          el("h4", { text: "Anchored to" }),
          el("ul", { class: "locator-list" }, ...anchored.map((loc) =>
            el("li", {}, el("button", {
              class: "locator-btn", type: "button",
              onclick: () => app.focusLocator(loc, v),
            },
              el("span", { class: "loc-shape", "aria-hidden": "true", text: shapeFor(loc, v) }),
              code(loc.target || "(whole view)"),
              el("span", { class: "muted", text: ` in ${loc.view}` }),
              loc.label ? el("span", { class: "loc-label", text: loc.label }) : null))))
        )
      : null);

  return el("li", { class: `verdict tone-${status.tone}`, id: `gate-${cssId(v.gate)}` },
    el("details", { class: "disclosure" },
      el("summary", { class: "verdict-summary" },
        chip(status),
        code(v.gate, "verdict-gate"),
        measured,
        el("span", { class: "verdict-line", text: detail }),
        v.stale_reason
          ? el("span", { class: "stale-flag", title: v.stale_reason,
              text: `≈ ${phrase("invalidated")}` })
          : null,
        anchored.length
          ? el("span", { class: "pin-count", title: "highlights the geometry this is about" },
              `⌖ ${anchored.length}`)
          : null,
        el("span", { class: `verdict-age${isAged(v.age_s) ? " aged" : ""}`, text: age(v.age_s) })),
      body),
    anchored.length
      ? el("button", {
          class: "show-me", type: "button", title: "isolate and frame this in the viewer",
          text: "Show me",
          onclick: () => app.focusLocator(anchored[0], v),
        })
      : null);
}

/** Pin shape per severity. The shape is the primary signal and the colour is the
 *  reinforcement, not the other way round — see the note at the top of
 *  format.js. `severity` defaults to the verdict's own outcome, exactly as the
 *  Locator docstring says. */
export function shapeFor(loc, verdict) {
  const sev = (loc.severity || (verdict && verdict.ok ? "info" : "fail")).toLowerCase();
  if (sev === "fail" || sev === "error" || sev === "critical") return "✕";
  if (sev === "warn" || sev === "warning") return "▲";
  return "●";
}

export function severityOf(loc, verdict) {
  const sev = (loc.severity || (verdict && verdict.ok ? "info" : "fail")).toLowerCase();
  if (sev === "fail" || sev === "error" || sev === "critical") return "fail";
  if (sev === "warn" || sev === "warning") return "warn";
  return "info";
}

function miniVerdict(v, app) {
  const status = verdictStatus(v.status);
  return el("li", {},
    chip(status, { small: true }),
    el("button", { class: "linkish mono", type: "button", text: v.gate,
      onclick: () => app.reveal(`gate-${cssId(v.gate)}`) }),
    v.measured !== null && v.measured !== undefined
      ? el("span", { class: "mono", text: ` ${quantity(v.measured, v.units)}` })
      : null,
    v.limit !== null && v.limit !== undefined
      ? el("span", { class: "muted mono", text: ` / ${quantity(v.limit, v.units)}` })
      : null,
    el("span", { class: "muted", text: ` ${age(v.age_s)}` }));
}

// --------------------------------------------------------------------------- //
// parameters — provenance on demand
// --------------------------------------------------------------------------- //

/** Every parameter, and behind each one the thing that actually pays: what was
 *  tried and rejected, and why it lost.
 *
 *  A number with no rationale is flagged `undefended` rather than left looking
 *  the same as a defended one. That flag comes from the spine (`row.defended`,
 *  the list `doctor` and `status` print), and it matters because an undefended
 *  constant is the one the next agent changes — then the one after that
 *  changes it back. `defended` is `null` where the model holds no number (it
 *  does not load, or the record outlived its field): nothing to defend, so the
 *  row says why there is no value instead. Only `=== false` is undefended —
 *  what slipped through (review, checkpoint 1.3): `!p.defended` would paint a
 *  broken model's every parameter "undefended", a claim about text nobody read. */
export function paramsPanel(state, app) {
  const params = state.params || [];
  const undefended = params.filter((p) => p.defended === false).length;

  return el("section", { class: "panel", id: "params" },
    panelHead("Parameters", plural(params.length, "parameter"),
      "Click one for why it has this value and what was tried instead.",
      undefended ? tag(`${undefended} undefended`, { tone: "warn",
        title: "no rationale recorded — the next agent will change it" }) : null),
    params.length
      ? el("ul", { class: "param-list" }, ...params.map((p) => paramRow(p, app)))
      : emptyNote("No parameters in the ledger yet."));
}

function paramRow(p, app) {
  const rejected = p.rejected || [];
  return el("li", { class: "param", id: `param-${cssId(p.name)}` },
    el("details", { class: "disclosure" },
      el("summary", { class: "param-summary" },
        code(p.name, "param-name"),
        el("span", { class: "param-value mono", text: formatValue(p.value) }),
        p.units ? el("span", { class: "param-units", text: p.units }) : null,
        p.derived ? tag("derived", { tone: "ok", title: `from ${(p.derived_from || []).join(", ")}` }) : null,
        p.defended === false ? tag("undefended", { tone: "warn", title: "no rationale recorded" }) : null,
        p.model_error ? tag("no value", { tone: "warn", title: `model does not load: ${p.model_error}` })
          : !p.home ? tag("not in the model", { tone: "muted", title: `only ${p.record || "a record"} holds it` })
          : null,
        rejected.length ? tag(`${rejected.length} rejected`, { tone: "muted" }) : null),
      el("div", { class: "param-detail" },
        el("dl", { class: "kv" },
          ...(p.model_error ? field("Value", el("span", { class: "tone-warn",
                text: `model does not load: ${p.model_error}` })) : []),
          ...(p.home ? field("Lives in", code(p.home)) : []),
          ...(p.rationale
            ? field("Why this value", p.rationale)
            : p.defended === false
              ? field("Why this value", el("span", { class: "tone-warn",
                  text: "Not recorded. A number nobody can defend is a number the next agent changes." }))
              : []),
          ...(p.source ? field("Source", p.source) : []),
          ...(p.derived_from || []).length
            ? field("Derived from", ...p.derived_from.map((d) => el("button", {
                class: "linkish mono", type: "button", text: d,
                onclick: () => app.reveal(`param-${cssId(d)}`) })))
            : [],
          ...(p.gates || []).length
            ? field("Protected by", ...p.gates.map((g) => el("button", {
                class: "linkish mono", type: "button", text: g,
                onclick: () => app.reveal(`gate-${cssId(g)}`) })))
            : [],
          ...(p.grounded_by || []).length
            ? field("Grounded by", ...p.grounded_by.map((id) => el("button", {
                class: "linkish mono", type: "button", text: id,
                onclick: () => app.reveal(`input-${id}`) })))
            : [],
          ...(p.changed_in ? field("Last moved in", code(p.changed_in)) : [])),
        rejected.length ? rejectedList(rejected) : null)));
}

/** What lost, and why. Rendered as a first-class block rather than a footnote:
 *  this is the field that stops every fresh context window re-litigating every
 *  settled number, and a site that hid it would be throwing away the most
 *  expensive thing in the ledger. */
export function rejectedList(rejected) {
  return el("div", { class: "rejected" },
    el("h4", {}, "Rejected ", el("span", { class: "muted", text: "— tried, and why it lost" })),
    el("ul", {}, ...rejected.map((r) => el("li", {},
      el("span", { class: "rejected-value mono", text: r.value }),
      el("span", { class: "rejected-why", text: r.why }),
      r.evidence ? fileRef(r.evidence) : null))));
}

function formatValue(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return num(value, { digits: 6 });
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return `[${value.map(formatValue).join(", ")}]`;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// --------------------------------------------------------------------------- //
// evidence, gaps, decisions
// --------------------------------------------------------------------------- //

/** Ingested artifacts and what was actually read out of each one.
 *
 *  An artifact with no extractions is called out, not listed quietly beside the
 *  others: a sketch nobody read is decoration, and the whole point of intake is
 *  that the design is grounded in something other than the conversation. */
export function evidencePanel(state, app) {
  const inputs = state.inputs || [];
  const unread = inputs.filter((a) => !a.extracted).length;
  return el("section", { class: "panel", id: "evidence" },
    panelHead("Evidence", plural(inputs.length, "artifact"),
      "What was supplied, and what was read out of it.",
      unread ? tag(`${unread} never read`, { tone: "warn" }) : null),
    inputs.length
      ? el("ul", { class: "input-list" }, ...inputs.map((a) => inputRow(a)))
      : emptyNote("Nothing has been ingested. A design conversation is the thinnest input a " +
          "project has — sketches, teardown photos, calipers and datasheets are the real ones."));
}

function inputRow(a) {
  const extractions = a.extractions || [];
  return el("li", { class: "input", id: `input-${a.id}` },
    el("details", { class: "disclosure" },
      el("summary", {},
        tag(a.kind, { tone: "muted" }),
        code(a.id),
        el("span", { class: "input-desc", text: a.description || a.path || a.url || "" }),
        extractions.length
          ? tag(plural(extractions.length, "extraction"), { tone: "ok" })
          : tag("never read", { tone: "warn", title: "an artifact nobody extracted from is decoration" })),
      el("div", { class: "input-detail" },
        el("dl", { class: "kv" },
          ...(a.path ? field("Path", code(a.path)) : []),
          ...(a.url ? field("URL", el("a", { href: a.url, rel: "noreferrer noopener", text: a.url })) : []),
          ...(a.added ? field("Added", stamp(a.added)) : []),
          ...(a.bytes ? field("Size", `${(a.bytes / 1024).toFixed(1)} kB`) : []),
          ...(a.licence ? field("Licence", a.licence) : []),
          ...(a.sha256 ? field("sha256", code(a.sha256.slice(0, 16) + "…")) : []),
          ...(a.note ? field("Note", a.note) : [])),
        extractions.length
          ? el("ul", { class: "extractions" }, ...extractions.map((e) => el("li", {},
              el("span", { class: "extract-what", text: e.what }),
              tag(e.confidence || "stated", { tone: e.confidence === "measured" ? "ok" : "muted" }),
              (e.grounds || []).length
                ? el("span", { class: "muted", text: ` grounds ${e.grounds.join(", ")}` })
                : null,
              e.note ? el("p", { class: "small muted", text: e.note }) : null)))
          : null)));
}

/** Capability gaps. Not a blocker and not an apology — a claim with no gate is
 *  the system's growth mechanism, and naming the unvalidated quantity is step
 *  one of closing it. */
export function gapsPanel(state, app) {
  const gaps = state.gaps || [];
  if (!gaps.length) return null;
  return el("section", { class: "panel", id: "gaps" },
    panelHead("Gap records", plural(gaps.length, "record"),
      "Claims no evaluator here can settle yet. Each record names the physical " +
      "quantity nothing measures, which is where the extension protocol starts."),
    el("ul", { class: "gap-list" }, ...gaps.map((g) => el("li", { class: "gap", id: `gap-${cssId(g.id)}` },
      el("details", { class: "disclosure" },
        el("summary", {},
          // A record's state, never a claim status word: `open` is Open's alone
          // (GLOSSARY §6), so a record nobody acted on is *identified* — the
          // ledger's word, from state.json's `phrases` (review of P2.1: this
          // held its own copy of `HUMAN["need"]`).
          tag(needWord(g.status), { tone: g.status === "satisfied" ? "ok" : "warn" }),
          code(g.id),
          el("span", { text: g.quantity || "" }),
          (g.claim_ids || []).length ? el("span", { class: "muted mono", text: g.claim_ids.join(" ") }) : null),
        el("div", { class: "gap-detail" },
          el("dl", { class: "kv" },
            ...(g.claim_class ? field("Class", g.claim_class) : []),
            ...(g.chosen ? field("Chosen", code(g.chosen)) : []),
            ...(g.note ? field("Note", g.note) : []),
            ...(g.claim_ids || []).length
              ? field("Claims", ...g.claim_ids.map((c) => el("button", {
                  class: "linkish mono", type: "button", text: c,
                  onclick: () => app.reveal(`claim-${c}`) })))
              : []),
          (g.candidates || []).length
            ? el("ul", { class: "candidates" }, ...g.candidates.map((c) => el("li", {},
                el("b", { text: c.name }), " ", tag(c.kind || "tool", { tone: "muted" }),
                c.why ? el("p", { text: c.why }) : null,
                c.cost ? el("p", { class: "small muted", text: `cost: ${c.cost}` }) : null,
                c.install ? el("pre", { class: "install" }, code(c.install)) : null)))
            : el("p", { class: "muted small", text: "No tool options proposed yet — `atompipe gap --propose`." })))))));
}

/** The decision log, newest first, each entry naming what lost. */
export function decisionsPanel(state, app) {
  const decisions = state.decisions || [];
  if (!decisions.length) return null;
  return el("section", { class: "panel", id: "decisions" },
    panelHead("Decisions", plural(decisions.length, "decision"),
      "What changed, when, and what was rejected on the way."),
    el("ol", { class: "decision-list" }, ...decisions.map((d) =>
      el("li", { class: "decision", id: `decision-${cssId(d.id)}` },
        el("details", { class: "disclosure" },
          el("summary", {},
            code(d.id),
            el("span", { class: "decision-title", text: d.title || "" }),
            d.when ? el("span", { class: "muted", text: stamp(d.when) }) : null),
          el("div", { class: "decision-detail" },
            d.summary ? el("p", { text: d.summary }) : null,
            d.body ? el("pre", { class: "prose-block", text: d.body }) : null,
            el("dl", { class: "kv" },
              ...(d.params_changed || []).length
                ? field("Parameters moved", ...d.params_changed.map((p) => el("button", {
                    class: "linkish mono", type: "button", text: p,
                    onclick: () => app.reveal(`param-${cssId(p)}`) })))
                : [],
              ...(d.claims_changed || []).length
                ? field("Claims touched", ...d.claims_changed.map((c) => el("button", {
                    class: "linkish mono", type: "button", text: c,
                    onclick: () => app.reveal(`claim-${c}`) })))
                : [],
              ...(d.evidence || []).length
                ? field("Evidence", el("ul", { class: "file-list" }, ...d.evidence.map(fileRef)))
                : []),
            (d.rejected || []).length ? rejectedList(d.rejected) : null))))));
}

/** Project identity and the provenance of the page itself. */
export function aboutPanel(state) {
  const meta = state.meta || {};
  return el("section", { class: "panel", id: "about" },
    panelHead("This page", "", "An output of the ledger, not a source."),
    el("dl", { class: "kv" },
      ...(meta.model_entry ? field("Model", code(meta.model_entry)) : []),
      ...(meta.created ? field("Project created", stamp(meta.created)) : []),
      ...(meta.built ? field("Site built", `${stamp(meta.built)}`) : field("Site built", "unstamped")),
      // No "last gate sweep" and no model hash: there is no sweep record any
      // more, and one hash for the whole project said THAT something moved,
      // never which result. Each verdict carries its own date and, when it is
      // not current, its own reason.
      ...((meta.packs || []).length ? field("Packs", ...meta.packs.map((p) => tag(p))) : []),
      ...(meta.spine_version ? field("atompipe", meta.spine_version) : [])),
    el("p", { class: "small muted", text: meta.generated ||
      "Generated by `atompipe site build` from the ledger. If a number here is wrong, the ledger is wrong." }));
}

// --------------------------------------------------------------------------- //
// shared bits
// --------------------------------------------------------------------------- //

/** An authored annotation: what this part IS and why it is like that.
 *
 *  Locators answer *where is the problem*; annotations answer *what am I looking
 *  at*, and nothing derives them — every field is a judgement about what a
 *  non-specialist needs to hear at the moment they are looking straight at the
 *  part. They are read from `site/annotations.json`, which is AUTHORED and not
 *  generated, and a project whose annotations are thin has a site that spins
 *  prettily and says nothing.
 *
 *  `claims` and `params` are the hooks back into the ledger, so the card that
 *  explains a part also reaches the claim that governs it and the parameter's
 *  rejected alternatives. */
export function annotationCard(note, app, { onFocus } = {}) {
  return el("article", { class: "annot", id: `annot-${cssId(note.id || note.title || "note")}` },
    el("h4", { class: "annot-title" },
      onFocus
        ? el("button", { class: "linkish", type: "button", text: note.title || note.id || "note",
            onclick: () => onFocus(note) })
        : el("span", { text: note.title || note.id || "note" })),
    note.oneLiner ? el("p", { class: "annot-one", text: note.oneLiner }) : null,
    note.blurb ? el("p", { class: "annot-blurb", text: note.blurb }) : null,
    el("p", { class: "annot-links" },
      ...(note.claims || []).map((c) => el("button", { class: "linkish mono small", type: "button",
        text: c, title: "the claim that governs this", onclick: () => app.reveal(`claim-${c}`) })),
      ...(note.params || []).map((p) => el("button", { class: "linkish mono small", type: "button",
        text: p, title: "why this value, and what lost", onclick: () => app.reveal(`param-${cssId(p)}`) })),
      note.href ? el("a", { class: "small", href: note.href, text: note.linkLabel || note.href }) : null));
}

export function panelHead(title, count, blurb, ...extra) {
  return el("header", { class: "panel-head" },
    el("h2", {}, el("span", { text: title }), count ? el("span", { class: "panel-count", text: count }) : null),
    blurb ? el("p", { class: "panel-blurb", text: blurb }) : null,
    extra.filter(Boolean).length ? el("div", { class: "panel-extra" }, ...extra) : null);
}

export function emptyNote(text) {
  return el("p", { class: "empty-note", text });
}

/** An evidence path. Linked only when it is under `assets/`, which is the one
 *  directory the site actually serves — everything else is a repo path, and a
 *  dead link to `runs/2026-09/deflection.json` is worse than the plain string,
 *  because it looks like the page is offering the file and it is not. */
export function fileRef(path) {
  const text = String(path);
  if (text.startsWith("assets/") || text.startsWith("./assets/")) {
    return el("li", {}, el("a", { class: "mono", href: text, text }));
  }
  return el("li", {}, code(text));
}

/** Ids in the ledger contain dots (`bracket.deflection`) and are used as DOM ids
 *  and as hash fragments. Dots are legal in an id attribute but not in a CSS
 *  selector without escaping, and `document.querySelector("#gate-a.b")` silently
 *  matches nothing at all rather than erroring — which is how a "Show me" button
 *  ends up doing nothing on exactly the gates that have a pack prefix. */
export function cssId(text) {
  return String(text).replace(/[^A-Za-z0-9_-]/g, "_");
}
