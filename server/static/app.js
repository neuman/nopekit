// SPDX-License-Identifier: Apache-2.0
// Eddy's screens: the family tree, a candidate's engineering and marketing views, its files, and
// the packs. One page, drawn from the server's data routes.
//
// The rule this file keeps: it computes no status. Every token, word, count and readiness
// sentence on a candidate's views is read from the baked files that candidate's own commit holds
// (site/data/page.json and map.json, written by `nopekit site build`). The page draws them and
// formats numbers; it never takes a worst-of, compares a value with a limit, or sorts by status.
// A bar's colour is its claim's baked token, never the result of a comparison made here.
"use strict";

// ---------------------------------------------------------------- helpers
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const enc = p => String(p).split("/").map(encodeURIComponent).join("/");
const TONE = { checked: "ok", failing: "bad", errored: "bad", gap: "warn", pending_build: "phys", assumed: "assum", open: "open", skipped: "skip", stale: "stale" };
const GLYPH = { checked: "✓", failing: "✕", errored: "✕", gap: "!", pending_build: "◷", assumed: "≈", open: "○", skipped: "–", stale: "↻" };
const tone = t => TONE[t] || "open";
const glyph = t => GLYPH[t] || "○";
const fmtSize = n => n == null ? "" : n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KiB" : (n / 1048576).toFixed(1) + " MiB";
const fmtNum = n => { const a = Math.abs(n); return a >= 1000 ? n.toFixed(0) : a >= 100 ? n.toFixed(1) : a >= 1 ? String(+n.toFixed(2)) : String(+n.toPrecision(3)); };
const fmtDate = t => t ? new Date(t * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";
const pretty = id => { const tail = String(id).split(".").pop().replace(/_/g, " "); return tail.charAt(0).toUpperCase() + tail.slice(1); };
function toast(html) { const t = document.createElement("div"); t.className = "toast"; t.innerHTML = html; document.body.append(t); setTimeout(() => t.remove(), 5000); }
async function api(path) {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) { let said = r.status; try { said = (await r.json()).error || said; } catch (e) { /* not JSON */ } throw new Error(said); }
  return r.json();
}
const rawUrl = (thing, sha, path) => `/r/${enc(thing)}/raw/${sha}/${enc(path)}`;

// ---------------------------------------------------------------- data
const FAMILY = {};          // thing -> family (dropped when a push lands)
const CAND = {};            // head sha -> {page, map, state, boxes}: a commit never changes
async function family(thing) { return FAMILY[thing] || (FAMILY[thing] = await api(`/r/${enc(thing)}/family`)); }
async function candData(thing, c) {
  if (CAND[c.head]) return CAND[c.head];
  const get = p => api(rawUrl(thing, c.head, p));
  const [page, map, state, boxes] = await Promise.all([get("site/data/page.json"), get("site/data/map.json"), get("site/data/state.json").catch(() => null), api(`/r/${enc(thing)}/boxes/${c.head}`).catch(() => ({ boxes: [], problems: [] }))]);
  return (CAND[c.head] = { page, map, state, boxes });
}
const MARKWORD = { selected: "selected", proposal: "proposal", "not-selected": "not selected", "": "not marked" };
const markClass = m => m === "not-selected" ? "not" : m || "none";
// The candidate a project opens on: the one the agent marked selected; with none, the newest.
const landing = fam => fam.candidates.find(c => c.mark === "selected") || fam.candidates[0] || null;
const byId = (fam, id) => fam.candidates.find(c => c.sha.startsWith(id)) || null;

// ---------------------------------------------------------------- routes
// #/                              home
// #/p/<thing>/<view>/<cand>/<path>   view: eng | tree | marketing | files; cand: 12 hex of its commit
// #/pack/<name>
const S = { view: "home", thing: "", cand: "", path: "", ftab: "code", pack: "", ppath: "", sel: null };
const LIVE = [];            // teardown for the current view: WebGL contexts, observers
let renderId = 0, stream = null, streamThing = "";
const href = (view, cand, path) => `#/p/${enc(S.thing)}/${view}${cand ? "/" + cand.slice(0, 12) : ""}${path ? "/" + enc(path) : ""}`;
function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  if (parts[0] === "p" && parts[1]) { S.thing = parts[1]; S.view = ["eng", "tree", "marketing", "files"].includes(parts[2]) ? parts[2] : "eng"; S.cand = parts[3] || ""; S.path = parts.slice(4).join("/"); }
  else if (parts[0] === "pack" && parts[1]) { S.view = "pack"; S.pack = parts[1]; S.ppath = parts.slice(2).join("/"); S.thing = ""; }
  else { S.view = "home"; S.thing = ""; }
  render();
}
window.addEventListener("hashchange", route);
document.addEventListener("click", e => {
  const d = e.target.closest("[data-do]");
  if (d) { e.preventDefault(); ACT[d.dataset.do](d.dataset.arg, d); }
});
const ACT = {
  theme() { const r = document.documentElement, dark = getComputedStyle(r).colorScheme.includes("dark"); r.dataset.theme = dark ? "light" : "dark"; try { localStorage.setItem("eddy-theme", r.dataset.theme); } catch (e) { /* private window */ } },
  ftab(t) { S.ftab = t; render(); },
  claim(id) { const row = document.getElementById("claim-" + id); if (!row) return; row.open = true; row.scrollIntoView({ block: "center", behavior: "smooth" }); row.classList.remove("flash"); void row.offsetWidth; row.classList.add("flash"); },
  copy(_a, el) { const text = el.closest(".panel").querySelector(".prompt").textContent; navigator.clipboard.writeText(text).then(() => toast("Copied"), () => toast("Select the text and copy it")); },
};
try { const t = localStorage.getItem("eddy-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) { /* private window */ }

// A push that lands refreshes what is on screen (the server's event stream).
function listen(thing) {
  if (streamThing === thing) return;
  if (stream) stream.close();
  streamThing = thing; stream = null;
  if (!thing || !window.EventSource) return;
  stream = new EventSource(`/r/${enc(thing)}/events`);
  stream.onmessage = ev => {
    let row = {}; try { row = JSON.parse(ev.data); } catch (e) { return; }
    delete FAMILY[thing];
    const name = String(row.ref || "").replace("refs/heads/", "");
    if (S.thing !== thing) return;
    if (S.view === "tree" || !S.cand) render();
    toast(`${esc(name)} landed. <a href="${href("tree", "")}">Open the tree</a>`);
  };
}

// ---------------------------------------------------------------- prose, as the site bakes it
function runs(list) { return (list || []).map(r => r.claim ? `<a href="#" data-do="claim" data-arg="${esc(r.claim)}" class="mono">${esc(r.t)}</a>` : r.num != null ? `<span class="num">${esc(r.t)}</span>` : esc(r.t)).join(""); }
function prose(p) {
  if (!p || !p.blocks || !p.blocks.length) return "";
  const out = p.blocks.map(b => b.type === "ul" ? `<ul>${(b.items || []).map(i => `<li>${runs(i.runs || i)}</li>`).join("")}</ul>` : b.type === "h3" || b.type === "h2" ? `<h3>${runs(b.runs)}</h3>` : `<p>${runs(b.runs)}</p>`).join("");
  return `<div class="prose">${p.notice ? `<p class="muted small">${esc(p.notice)}</p>` : ""}${out}</div>`;
}

// ---------------------------------------------------------------- boxes the agent supplies
const ybox = (tag, body, caption, cls = "") => `<figure class="ybox"><div class="ytag">${tag}</div><div class="ybody ${cls}">${body}</div>${caption ? `<figcaption>${caption}</figcaption>` : ""}</figure>`;
function boxHtml(b) {
  const cap = [b.title ? `<b>${esc(b.title)}</b>` : "", esc(b.caption)].filter(Boolean).join(" ");
  const ratio = `aspect-ratio:${esc(b.aspect.replace("/", " / "))}`;
  if (b.kind === "image") return ybox("image", `<img src="${esc(b.src)}" alt="${esc(b.title || b.caption)}" data-fallback="${esc(b.thumbnail)}">`, cap, "flush");
  if (b.kind === "video") return ybox("video", `<video controls preload="metadata" poster="${esc(b.thumbnail)}" src="${esc(b.src)}"></video>`, cap, "flush");
  const theme = getComputedStyle(document.documentElement).colorScheme.includes("dark") ? "dark" : "light";
  return ybox("custom · runs live", `<div class="boxframe" style="${ratio}"><img class="thumb" src="${esc(b.thumbnail)}" alt="${esc(b.title || "Static thumbnail")}"><iframe sandbox="allow-scripts" loading="lazy" title="${esc(b.title || b.id)}" src="${esc(b.src)}#${theme}"></iframe><div class="vnote"></div></div>`, cap, "flush");
}
// A custom box says it has drawn by posting {eddy: "ready"} to its parent. Until then, and if it
// never does, its static thumbnail stays in front.
window.addEventListener("message", e => {
  if (!e.data || e.data.eddy !== "ready") return;
  const f = $$(".boxframe iframe").find(x => x.contentWindow === e.source);
  if (f) { f.parentElement.classList.add("ready"); f.parentElement.querySelector(".vnote").textContent = ""; }
});
function mountBoxes() {
  $$(".boxframe").forEach(f => { const t = setTimeout(() => { if (!f.classList.contains("ready")) f.querySelector(".vnote").textContent = "Static thumbnail: this box has not started."; }, 10000); LIVE.push(() => clearTimeout(t)); });
  $$("img[data-fallback]").forEach(i => i.addEventListener("error", () => { if (i.src !== i.dataset.fallback) i.src = i.dataset.fallback; }, { once: true }));
}
const boxesFor = (cd, view, section) => cd.boxes.boxes.filter(b => b.section === section && (b.view === "both" || b.view === view));
const boxAbout = (list, ...keys) => list.filter(b => b.about && keys.includes(b.about));
const boxRest = (list, used) => list.filter(b => !used.has(b.id));

// ---------------------------------------------------------------- the 3D representation
function viewer(host, url, opts = {}) {
  const note = host.querySelector(".vnote");
  const api2 = { setExplode() {}, dispose() {} };
  let dead = false; api2.dispose = () => { dead = true; };
  const fail = () => { if (note) note.textContent = "Static thumbnail: the live model did not load."; };
  if (!window.THREE || !THREE.GLTFLoader) { fail(); return api2; }
  new THREE.GLTFLoader().load(url, gltf => {
    if (dead) return;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2)); renderer.outputEncoding = THREE.sRGBEncoding;
    const scene = new THREE.Scene(), root = gltf.scene;
    root.rotation.x = -Math.PI / 2;                       // nopekit's models are CAD models: z is up
    const holder = new THREE.Group(); holder.add(root); scene.add(holder);
    const box = new THREE.Box3().setFromObject(holder), c = box.getCenter(new THREE.Vector3()), r = box.getSize(new THREE.Vector3()).length() / 2 || 1;
    holder.position.sub(c);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x555566, 1.05));
    const sun = new THREE.DirectionalLight(0xffffff, 0.9); sun.position.set(r, 2 * r, 1.5 * r); scene.add(sun);
    const cam = new THREE.PerspectiveCamera(32, 1, r / 100, r * 100);
    let az = 0.7, el = 0.62, ex = 0, dirty = true, raf = 0, dragging = false, lx = 0, ly = 0, zoom = 1;
    const movers = Object.values((opts.explode || {}).movers || {}).map(m => ({ off: m.offset || [0, 0, 0], nodes: (m.nodes || []).map(n => root.getObjectByName(n) || root.getObjectByName(String(n).replace(/\s/g, "_").replace(/[.:/[\]]/g, ""))).filter(Boolean).map(o => ({ o, p: o.position.clone() })) })).filter(m => m.nodes.length);
    const place = () => { const d = r * 3.6 * zoom * (1 + 0.5 * ex); cam.position.set(d * Math.cos(el) * Math.sin(az), d * Math.sin(el), d * Math.cos(el) * Math.cos(az)); cam.lookAt(0, 0, 0); };
    const size = () => { const w = host.clientWidth, h = host.clientHeight; if (!w || !h) return; renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix(); zoom = w / h < 1.2 ? 1.2 : 1; dirty = true; };
    const frame = () => { if (dead) return; if (opts.spin && !dragging) { az += 0.004; dirty = true; } if (dirty) { place(); renderer.render(scene, cam); dirty = false; } raf = requestAnimationFrame(frame); };
    host.addEventListener("pointerdown", e => { dragging = true; lx = e.clientX; ly = e.clientY; host.setPointerCapture(e.pointerId); });
    host.addEventListener("pointermove", e => { if (!dragging) return; az -= (e.clientX - lx) * 0.01; el = Math.max(-0.2, Math.min(1.45, el + (e.clientY - ly) * 0.008)); lx = e.clientX; ly = e.clientY; dirty = true; });
    const up = () => { dragging = false; }; host.addEventListener("pointerup", up); host.addEventListener("pointercancel", up);
    api2.setExplode = t => { ex = t; movers.forEach(m => m.nodes.forEach(({ o, p }) => o.position.set(p.x + m.off[0] * t, p.y + m.off[1] * t, p.z + m.off[2] * t))); dirty = true; };
    host.append(renderer.domElement);
    const th = host.querySelector(".thumb, .thph"); if (th) th.hidden = true;
    if (note) note.textContent = "Drag to turn.";
    const ro = new ResizeObserver(size); ro.observe(host); size(); frame();
    api2.dispose = () => { dead = true; cancelAnimationFrame(raf); ro.disconnect(); renderer.dispose(); renderer.forceContextLoss(); renderer.domElement.remove(); };
    if (opts.ready) opts.ready({ canExplode: movers.length > 0 });
  }, undefined, fail);
  LIVE.push(() => api2.dispose());
  return api2;
}
const thumbOf = c => c.thumb ? `<img class="thumb" src="${rawUrl(S.thing, c.head, c.thumb)}" alt="Picture of ${esc(c.name)}">` : `<div class="thph">no thumbnail</div>`;
function heroBox(c, cd) {
  const rep = cd.page.representation || {}, view = rep.view && cd.state ? (cd.state.views || []).find(v => v.id === rep.view) : null;
  const live = view && rep.kind === "model3d" && view.src;
  if (!live && !c.thumb) return `<div class="panel muted small">${esc(rep.notice || "This candidate has no model or picture to show.")}</div>`;
  const stage = `<div class="v3" id="hero3d" ${live ? `data-src="${rawUrl(S.thing, c.head, "site/" + view.src)}"` : ""}>${thumbOf(c)}<div class="vnote">${live ? "Loading the model…" : ""}</div></div>`;
  const ctl = live ? `<div class="vctl" id="herectl" hidden><label for="explode">Assembled</label><input id="explode" type="range" min="0" max="1" step="0.01" value="0" aria-label="Explode the assembly"><span>Exploded</span></div>` : "";
  return ybox(live ? "model · runs live" : "static thumbnail", stage + ctl, esc(rep.title || ""), "flush");
}
function mountHero(cd, spin) {
  const host = $("#hero3d"); if (!host || !host.dataset.src) return;
  const rep = cd.page.representation, view = (cd.state.views || []).find(v => v.id === rep.view);
  const v = viewer(host, host.dataset.src, { spin, explode: (view.meta || {}).explode, ready: a => { const ctl = $("#herectl"); if (ctl && a.canExplode && !spin) ctl.hidden = false; } });
  const s = $("#explode"); if (s) s.addEventListener("input", () => v.setExplode(+s.value));
}

// ---------------------------------------------------------------- the nopekit diagram
// Three fixed columns: choices, implications, claims. Drawn from map.json and page.json alone.
const MW = 1440, COL = { a: 16, aw: 300, s: 420, sw: 190, v: 700, vw: 210, c: 1010, cw: 414 };
function wrap(text, n, lines) { const words = String(text).split(/\s+/), out = [""]; for (const w of words) { if ((out[out.length - 1] + " " + w).trim().length <= n) out[out.length - 1] = (out[out.length - 1] + " " + w).trim(); else if (out.length < lines) out.push(w); else { out[out.length - 1] = out[out.length - 1].replace(/.{0,2}$/, "…"); break; } } return out; }
const nodesOf = (map, kind) => map.nodes.filter(n => n.kind === kind).sort((a, b) => a.order - b.order);
const bindingValue = claim => (claim.values || []).find(v => v.binding && v.measured != null && v.limit != null) || null;
const cmpSign = c => ({ "<=": "≤", ">=": "≥", "<": "<", ">": ">", "==": "=" }[c] || c || "");
function buildMap(cd) {
  const { map, page } = cd, words = (page.words || {}).tokens || {}, word = t => (words[t] || {}).word || t;
  const P = {}, g = [];
  let y = 62;
  g.push(`<text class="colhead" x="${COL.a}" y="24">CHOICES</text><text class="colhead" x="${COL.s}" y="24">IMPLICATIONS</text><text class="colhead" x="${COL.c}" y="24">CLAIMS</text>`);
  g.push(`<text class="subhead" x="${COL.a}" y="52">WHAT WAS ASKED</text><text class="subhead" x="${COL.s}" y="52">SHARED VALUES</text><text class="subhead" x="${COL.v}" y="52">WHAT IT COMES TO</text>`);
  for (const r of nodesOf(map, "requirement")) {
    const tx = wrap(r.title, 42, 2);
    P[r.id] = { x: COL.a + COL.aw, y: y + 23 };
    g.push(`<g class="card" data-n="${esc(r.id)}" tabindex="0" role="button" aria-label="${esc(r.key + ": " + r.title)}"><rect class="bg" x="${COL.a}" y="${y}" width="${COL.aw}" height="46" rx="6"/><rect class="bar tone-${tone(r.token)}" x="${COL.a}" y="${y}" width="5" height="46" rx="2"/><text x="${COL.a + 14}" y="${y + 19}" class="t12"><tspan class="id">${esc(r.key)}</tspan> ${esc(tx[0])}</text>${tx[1] ? `<text x="${COL.a + 14}" y="${y + 35}" class="t12">${esc(tx[1])}</text>` : ""}</g>`);
    y += 52;
  }
  y += 22; g.push(`<text class="subhead" x="${COL.a}" y="${y}">WHAT WAS CHOSEN</text>`); y += 10;
  for (const z of nodesOf(map, "zone")) {
    P[z.id] = { x: COL.a + COL.aw, y: y + 20 };
    g.push(`<g class="zone" data-n="${esc(z.id)}" tabindex="0" role="button" aria-label="${esc(z.title)}"><rect class="bg" x="${COL.a}" y="${y}" width="${COL.aw}" height="40" rx="6"/><text x="${COL.a + 14}" y="${y + 25}" class="name">${esc(wrap(z.title, 28, 1)[0])}</text><text x="${COL.a + COL.aw - 12}" y="${y + 25}" text-anchor="end" class="t11">${z.count} value${z.count === 1 ? "" : "s"}</text></g>`);
    y += 46;
  }
  const leftH = y, claimNode = Object.fromEntries(nodesOf(map, "claim").map(n => [n.key, n])), cy = {};
  y = 40;
  for (const b of nodesOf(map, "board")) {
    const bt = tone(b.token);
    g.push(`<g class="bhead tone-${bt}"><rect x="${COL.c}" y="${y}" width="${COL.cw}" height="28" rx="6"/><text x="${COL.c + 12}" y="${y + 18.5}">${esc(wrap(b.title.toUpperCase(), 30, 1)[0])}</text><text x="${COL.c + COL.cw - 12}" y="${y + 18.5}" text-anchor="end">${glyph(b.token)} ${esc(String(b.word || word(b.token)).toUpperCase())}</text></g>`);
    y += 34;
    for (const id of b.claims) {
      const n = claimNode[id]; if (!n) continue;
      const t = tone(n.token), vt = n.value_text ? n.value_text.split(",")[0] : "";
      cy[id] = y + 19; P[n.id] = { x: COL.c, y: y + 19 };
      g.push(`<g class="claim" data-n="${esc(n.id)}" tabindex="0" role="button" aria-label="${esc(id + " " + (n.word || word(n.token)) + ": " + n.title)}"><rect class="bg" x="${COL.c}" y="${y}" width="${COL.cw}" height="38" rx="6"/><circle class="dot tone-${t}" cx="${COL.c + 12}" cy="${y + 19}" r="5"/><text x="${COL.c + 26}" y="${y + 16}" class="t12"><tspan class="id">${esc(id)}</tspan> ${esc(wrap(n.title, 52, 1)[0])}</text><text x="${COL.c + 26}" y="${y + 31}" class="t11"><tspan class="w tone-${t}" font-weight="700">${glyph(n.token)} ${esc(n.word || word(n.token))}</tspan>${vt ? " · " + esc(wrap(vt, 44, 1)[0]) : ""}</text></g>`);
      y += 40;
    }
    y += 14;
  }
  // implications: the values several claims share, then what each claim's value comes to
  const stations = nodesOf(map, "station").map(s => ({ s, y: (s.claims || []).filter(c => cy[c]).reduce((a, c, _i, l) => a + cy[c] / l.length, 0) || 80 })).sort((a, b) => a.y - b.y);
  for (let k = 0; k < stations.length; k++) { if (k && stations[k].y - stations[k - 1].y < 50) stations[k].y = stations[k - 1].y + 50; if (stations[k].y < 84) stations[k].y = 84; }
  for (const { s, y: sy } of stations) {
    P[s.id] = { x: COL.s, y: sy, x2: COL.s + COL.sw };
    g.push(`<g class="station" data-n="${esc(s.id)}" tabindex="0" role="button" aria-label="${esc(s.title + " " + (s.value_text || ""))}"><rect class="bg" x="${COL.s}" y="${sy - 21}" width="${COL.sw}" height="42" rx="21"/><text x="${COL.s + 18}" y="${sy - 3}" class="name">${esc(wrap(s.title, 22, 1)[0])}</text><text x="${COL.s + 18}" y="${sy + 13}" class="t11">${esc(wrap(s.value_text || "", 28, 1)[0])}</text></g>`);
  }
  for (const id of Object.keys(cy)) {
    const v = bindingValue(page.claims[id] || {}); if (!v) continue;
    const vy = cy[id], nid = "value:" + id;
    P[nid] = { x: COL.v, y: vy, x2: COL.v + COL.vw };
    g.push(`<g class="value" data-n="${esc(nid)}" tabindex="0" role="button" aria-label="${esc(pretty(v.gate))}"><rect class="bg" x="${COL.v}" y="${vy - 17}" width="${COL.vw}" height="34" rx="17"/><text x="${COL.v + 16}" y="${vy - 2}" class="t12" font-weight="700">${esc(wrap(pretty(v.gate), 26, 1)[0])}</text><text x="${COL.v + 16}" y="${vy + 11}" class="t11">${esc(`${fmtNum(v.measured)} ${v.units} ${cmpSign(v.comparator)} ${fmtNum(v.limit)} ${v.units}`)}</text></g>`);
  }
  const tracks = [], curve = (a, b) => { const dx = Math.max(30, (b.x - a.x) * 0.5); return `M${a.x} ${a.y} C${a.x + dx} ${a.y} ${b.x - dx} ${b.y} ${b.x} ${b.y}`; };
  for (const ln of map.lines) {
    const end = P["claim:" + ln.claim]; if (!end) continue;
    const t = tone(ln.token), way = (ln.stops || []).filter(s => s.startsWith("station:") && P[s]).map(s => P[s]);
    if (P["value:" + ln.claim]) way.push(P["value:" + ln.claim]);
    way.push(end);
    const srcs = [...(ln.serves || []).map(r => "requirement:" + r), ...(ln.fed_by || [])].filter(s => P[s]);
    for (const s of srcs) tracks.push(`<path class="track tone-${t}" data-c="${esc(ln.claim)}" data-s="${esc(s)}" d="${curve(P[s], way[0])}"/>`);
    for (let k = 1; k < way.length; k++) tracks.push(`<path class="track tone-${t}" data-c="${esc(ln.claim)}" d="${curve({ x: way[k - 1].x2, y: way[k - 1].y }, way[k])}"/>`);
  }
  const H = Math.max(leftH, y) + 10;
  return { H, svg: `<svg class="nk" width="${MW}" height="${H}" viewBox="0 0 ${MW} ${H}" role="group" aria-label="The nopekit diagram: choices, implications and claims">${tracks.join("")}${g.join("")}</svg>` };
}
function relClaims(cd, n) {
  if (n.startsWith("claim:") || n.startsWith("value:")) return [n.split(":")[1]];
  const node = cd.map.nodes.find(x => x.id === n);
  return node ? node.claims || [] : [];
}
function selInfo(cd, n) {
  if (!n) return `<span class="muted">Select anything in the diagram to see what it connects to.</span>`;
  const cl = relClaims(cd, n), links = cl.length ? cl.map(c => `<a href="#" data-do="claim" data-arg="${esc(c)}">${esc(c)}</a>`).join(", ") : "no claim yet";
  const kind = n.split(":")[0], key = n.slice(kind.length + 1), node = cd.map.nodes.find(x => x.id === n);
  if (kind === "claim") return `<div>${tok(cd, node.token)} <b class="mono">${esc(key)}</b> ${esc(node.title)}</div><div class="muted small">${esc(node.value_text || (cd.page.claims[key] || {}).cause_text || "")}</div><div><a href="#" data-do="claim" data-arg="${esc(key)}">Open this claim below</a></div>`;
  if (kind === "value") { const v = bindingValue(cd.page.claims[key]); return `<div><b>${esc(pretty(v.gate))}</b> · ${esc(v.value_text || "")}</div><div class="muted small">What the design comes to, as <span class="mono">${esc(v.gate)}</span> computes it. Judged by ${links}.</div>`; }
  if (kind === "requirement") return `<div><b class="mono">${esc(key)}</b> ${esc(node.title)}</div><div class="muted small">What was asked. Served by ${links}.</div>`;
  if (kind === "zone") return `<div><b>${esc(node.title)}</b> · ${node.count} values chosen</div><div class="muted small">${(node.members || []).slice(0, 6).map(esc).join(", ")}${(node.members || []).length > 6 ? "…" : ""}. Read by ${links}.</div>`;
  return `<div><b>${esc(node.title)}</b>${node.value_text ? " · " + esc(node.value_text) : ""}</div><div class="muted small">${esc(node.line || "A value more than one claim reads")}. Read by ${links}.</div>`;
}
function mountMap(cd) {
  const frame = $("#mapframe"); if (!frame) return;
  const { H, svg: html } = buildMap(cd);
  frame.innerHTML = html;
  const svg = frame.firstElementChild, info = $("#mapsel"), bar = $("#fullbar");
  let k0 = 1, k = 1, tx = 0, ty = 0, full = false;
  const box = () => ({ w: frame.clientWidth, h: frame.clientHeight });
  const apply = () => {
    const { w, h } = box();
    tx = MW * k <= w ? (w - MW * k) / 2 : Math.min(0, Math.max(w - MW * k, tx));
    ty = H * k <= h ? (full ? (h - H * k) / 2 : 0) : Math.min(0, Math.max(h - H * k, ty));
    svg.style.transform = `translate(${tx}px,${ty}px) scale(${k})`;
    frame.classList.toggle("zoomed", k > k0 * 1.01 || full);
  };
  const fit = () => {                                     // the whole diagram, scaled to the width
    if (full) { const { w, h } = box(); k0 = Math.min(w / MW, h / H); }
    else { k0 = frame.clientWidth / MW; frame.style.height = Math.ceil(H * k0) + "px"; }
    k = k0; tx = ty = 0; apply();
  };
  const zoomAt = (cx, cy, f) => { const nk = Math.min(3, Math.max(k0, k * f)); tx = cx - (cx - tx) * (nk / k); ty = cy - (cy - ty) * (nk / k); k = nk; apply(); };
  const local = e => { const r = frame.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  const pts = new Map(); let moved = 0, pinch = 0;
  frame.addEventListener("pointerdown", e => { pts.set(e.pointerId, local(e)); moved = 0; if (pts.size === 2) { const [a, b] = [...pts.values()]; pinch = Math.hypot(a.x - b.x, a.y - b.y); } if (frame.classList.contains("zoomed") || pts.size === 2) frame.setPointerCapture(e.pointerId); });
  frame.addEventListener("pointermove", e => {
    if (!pts.has(e.pointerId)) return;
    const p = local(e), o = pts.get(e.pointerId); pts.set(e.pointerId, p);
    if (pts.size === 2) { const [a, b] = [...pts.values()], d = Math.hypot(a.x - b.x, a.y - b.y); if (pinch) zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, d / pinch); pinch = d; moved = 99; }
    else if (frame.classList.contains("zoomed")) { tx += p.x - o.x; ty += p.y - o.y; moved += Math.abs(p.x - o.x) + Math.abs(p.y - o.y); apply(); }
  });
  const end = e => { pts.delete(e.pointerId); pinch = 0; };
  frame.addEventListener("pointerup", end); frame.addEventListener("pointercancel", end);
  frame.addEventListener("wheel", e => { if (!(e.ctrlKey || e.metaKey) && !full) return; e.preventDefault(); const p = local(e); zoomAt(p.x, p.y, Math.exp(-e.deltaY * 0.01)); }, { passive: false });
  frame.addEventListener("dblclick", e => { const p = local(e); zoomAt(p.x, p.y, 1.7); });
  const select = n => {
    S.sel = S.sel === n ? null : n;
    svg.classList.toggle("has-sel", !!S.sel);
    const cl = S.sel ? relClaims(cd, S.sel) : [], source = S.sel && (S.sel.startsWith("requirement:") || S.sel.startsWith("zone:"));
    $$("[data-n]", svg).forEach(el => { const m = el.dataset.n; el.classList.toggle("hl", m === S.sel); el.classList.toggle("rel", !!S.sel && (m.startsWith("claim:") || m.startsWith("value:")) && cl.includes(m.split(":")[1])); });
    $$("path.track", svg).forEach(p => p.classList.toggle("hl", cl.includes(p.dataset.c) && (!p.dataset.s || !source || p.dataset.s === S.sel)));
    info.innerHTML = selInfo(cd, S.sel);
  };
  frame.addEventListener("click", e => { if (moved > 6) return; const n = e.target.closest("[data-n]"); if (n) select(n.dataset.n); });
  frame.addEventListener("keydown", e => { if (e.key !== "Enter" && e.key !== " ") return; const n = e.target.closest("[data-n]"); if (n) { e.preventDefault(); select(n.dataset.n); } });
  const setFull = on => { full = on; frame.classList.toggle("full", on); bar.classList.toggle("on", on); if (!on) frame.style.height = ""; fit(); };
  $("#map-in").onclick = () => { const { w, h } = box(); zoomAt(w / 2, h / 2, 1.4); };
  $("#map-out").onclick = () => { const { w, h } = box(); zoomAt(w / 2, h / 2, 1 / 1.4); };
  $("#map-fit").onclick = fit; $("#map-full").onclick = () => setFull(true);
  $("#full-close").onclick = () => setFull(false); $("#full-fit").onclick = fit;
  let lastW = 0; const ro = new ResizeObserver(() => { const w = frame.clientWidth; if (w !== lastW) { lastW = w; fit(); } });
  ro.observe(frame); LIVE.push(() => { ro.disconnect(); if (full) { frame.classList.remove("full"); bar.classList.remove("on"); } });
  S.sel = null; info.innerHTML = selInfo(cd, null);
}

// ---------------------------------------------------------------- views
const tok = (cd, t) => `<span class="tok tone-${tone(t)}"><span aria-hidden="true">${glyph(t)}</span>${esc((((cd.page.words || {}).tokens || {})[t] || {}).word || t)}</span>`;
const tally = list => `<div class="pills">${(list || []).map(x => `<span class="tok tone-${tone(x.key)}"><span aria-hidden="true">${glyph(x.key)}</span>${esc(x.label)}</span>`).join("")}</div>`;
function banner(fam, c) {
  const sel = fam.candidates.find(x => x.mark === "selected");
  if (!sel || sel.sha === c.sha) return "";
  return `<div class="banner"><span>You are viewing <b class="mono">${esc(c.name)}</b>, ${c.mark ? "marked <b>" + MARKWORD[c.mark] + "</b>" : "which is not marked"}.</span><a href="${href(S.view, sel.sha)}">Go to the selected candidate, ${esc(sel.name)}</a></div>`;
}
const noSite = c => `<div class="panel"><b>${esc(c.name)} carries no baked page data.</b><div class="small">Eddy draws this view from <span class="mono">site/data/page.json</span> and <span class="mono">map.json</span> in the candidate's own commit. Ask the agent to run <span class="mono">nopekit site init</span> once, then <span class="mono">nopekit round</span>, and to commit <span class="mono">site/data/</span> with the next candidate. Its files are under Files.</div></div>`;
const problems = list => list && list.length ? `<div class="problems">${list.map(p => `<div>${esc(p)}</div>`).join("")}</div>` : "";

function viewEng(fam, c, cd) {
  const { page, map, state } = cd, params = Object.fromEntries(((state || {}).params || []).map(p => [p.name, p]));
  const bx = { choices: boxesFor(cd, "engineering", "choices"), implications: boxesFor(cd, "engineering", "implications"), claims: boxesFor(cd, "engineering", "claims") }, used = new Set();
  const place = (list, ...keys) => boxAbout(list, ...keys).map(b => { used.add(b.id); return boxHtml(b); }).join("");
  const zones = nodesOf(map, "zone").map(z => {
    const ms = (z.members || []).map(n => params[n] || { name: n });
    const shown = ms.slice(0, 6).map(m => `<span class="val">${esc(m.name)}${m.value != null && typeof m.value !== "object" ? ` <b>${esc(m.value)}${m.units ? " " + esc(m.units) : ""}</b>` : ""}</span>`).join("");
    const why = ms.filter(m => m.rationale).map(m => `<div><b>${esc(m.name)}</b> ${esc(m.rationale)}${(m.rejected || []).slice(0, 3).map(l => `<div class="lost">Lost: ${esc(l.value)}. ${esc(l.why || l.reason || "")}</div>`).join("")}</div>`).join("");
    return `<div class="zone"><div class="zh"><b>${esc(z.title)}</b><span class="muted small">${z.count} value${z.count === 1 ? "" : "s"}${(z.claims || []).length ? " · read by " + z.claims.map(esc).join(", ") : ""}</span></div><div class="vals">${shown}${ms.length > 6 ? `<span class="val">+${ms.length - 6} more</span>` : ""}</div>${place(bx.choices, z.key, z.title)}${why ? `<details><summary>Why these values, and what lost</summary><div class="why">${why}</div></details>` : ""}</div>`;
  }).join("");
  const stations = nodesOf(map, "station").map(s => `<div class="impl"><div class="nm"><b>${esc(s.title)}</b><span>${esc(s.line || "a value more than one claim reads")}</span></div><div class="small muted">${(s.claims || []).length ? "read by " : ""}${(s.claims || []).map(x => `<a href="#" data-do="claim" data-arg="${esc(x)}">${esc(x)}</a>`).join(", ")}</div><div class="num">${esc(s.value_text || "")}</div></div>`).join("");
  const values = nodesOf(map, "claim").map(n => {
    const v = bindingValue(page.claims[n.key] || {}); if (!v) return "";
    const max = Math.max(Math.abs(v.measured), Math.abs(v.limit)) * 1.25 || 1;
    return `<div class="impl"><div class="nm"><b>${esc(pretty(v.gate))}</b><span><a href="#" data-do="claim" data-arg="${esc(n.key)}">${esc(n.key)}</a> ${esc(wrap(n.title, 60, 1)[0])}</span></div><div class="meter" role="img" aria-label="${esc(v.value_text || "")}"><div class="fill tone-${tone(n.token)}" style="width:${Math.max(2, Math.abs(v.measured) / max * 100)}%"></div><div class="lim" style="left:${Math.abs(v.limit) / max * 100}%"></div></div><div class="num">${fmtNum(v.measured)} ${esc(v.units)} <span class="muted">${cmpSign(v.comparator)} ${fmtNum(v.limit)}</span></div></div>`;
  }).join("");
  const boards = (page.sections || []).map(b => {
    const rows = (b.claims || []).map(id => { const cl = page.claims[id]; if (!cl) return ""; const vt = (bindingValue(cl) || {}).value_text || ""; return `<details class="claimrow" id="claim-${esc(id)}"><summary>${tok(cd, cl.token)}<span class="st"><span class="cid">${esc(id)}</span>${esc(cl.statement)}</span>${vt ? `<span class="vt tnum">${esc(vt)}</span>` : ""}</summary><div class="cbody"><div>${esc(cl.cause_text || "")} ${esc((cl.settle || {}).text || "")}</div>${(cl.mechanism || {}).how ? `<div><b>How it is settled.</b> ${esc(cl.mechanism.how)}</div>` : ""}${(cl.evaluators || []).length ? `<div class="evs">${cl.evaluators.map(e => `<div><b>${esc(e.id)}</b> ${esc(e.outcome_word || e.outcome || "")}${e.detail ? ": " + esc(e.detail) : ""}</div>`).join("")}</div>` : ""}<div class="muted small">Serves ${(cl.requirements || []).map(esc).join(", ") || "no requirement"} · ${esc(cl.kind_word || "")}</div>${place(bx.claims, id)}</div></details>`; }).join("");
    return `<div class="board"><div class="bh"><b>${esc(b.title)}</b>${tally(b.tally)}</div>${b.line ? `<div class="small" style="padding:10px 14px 0;color:var(--ink2)">${esc(b.line)}</div>` : ""}${b.prose ? `<div style="padding:10px 14px">${prose(b.prose)}</div>` : ""}${place(bx.claims, b.board, b.title)}${rows}</div>`;
  }).join("");
  const nx = page.next || {}, next = (nx.milestones || []).map(ms => `<div class="ms${ms.next ? " nextup" : ""}"><div class="mh"><b>${esc(ms.id)}</b>${ms.next ? `<span class="mark" style="color:var(--accent)">next</span>` : ""}<span>${esc(ms.description)}</span></div><div class="small muted">${esc(ms.sentence || "")}</div>${(ms.steps || []).length ? `<ul>${ms.steps.map(s => `<li>${tok(cd, s.token)}<span><a href="#" data-do="claim" data-arg="${esc(s.claim)}" class="mono">${esc(s.claim)}</a> ${esc(s.statement)}</span><span class="how">${esc(s.text || s.how || "")}</span></li>`).join("")}</ul>` : ""}</div>`).join("");
  const v = page.verdict || {}, sentence = v.headline && (v.sentence || "").startsWith(v.headline) ? `<b>${esc(v.headline)}</b>${esc(v.sentence.slice(v.headline.length))}` : esc(v.sentence || "");
  return `${banner(fam, c)}${problems(cd.boxes.problems)}
  <section class="hero"><div class="herotext"><h1>${esc((page.meta || {}).name || S.thing)}<span class="vchip">${esc(c.name)}</span></h1><p class="lede">${esc((page.description || {}).summary || "")}</p><p class="verdict">${sentence}</p>${tally(v.tally)}${v.notice ? `<p class="small muted">${esc(v.notice)}</p>` : ""}
    <div class="legendline"><span class="swatch"></span><span>A yellow outline is a box the agent supplies: a model, an image, a video or a custom visualization. Everything else is Eddy's standard renderer reading this commit's files.</span></div></div>
    ${heroBox(c, cd)}</section>
  ${page.intro ? `<section class="sec">${prose(page.intro)}</section>` : ""}
  <section class="sec"><div class="sech"><h2>The nopekit diagram</h2><p>Each line is one claim. It runs from what was asked and what was chosen, through the values that follow, to the claim that judges them. The three columns are the same for every project.</p></div>
    <div class="mapwrap"><div class="maptools"><span class="hint">Opens fitted to the width. Pinch, double-tap or use the buttons to zoom; drag to move once zoomed in.</span><button type="button" id="map-out" aria-label="Zoom out">−</button><button type="button" id="map-in" aria-label="Zoom in">+</button><button type="button" id="map-fit">Fit</button><button type="button" id="map-full">Full screen</button></div>
    <div class="mapframe" id="mapframe"></div><div class="mapsel" id="mapsel" aria-live="polite"></div></div></section>
  <section class="sec"><div class="sech"><h2>Choices</h2><p>The values the design sets, in the groups the project keeps them in.</p></div><div class="grid2">${zones || `<div class="panel muted">No choice is grouped yet.</div>`}</div>${boxRest(bx.choices, used).map(boxHtml).join("")}</section>
  <section class="sec"><div class="sech"><h2>Implications</h2><p>What follows from the choices: the values several claims share, then what each claim's value comes to. A bar is the design's value; the mark is the limit its claim holds it to.</p></div>${stations || values ? `<div class="impls">${stations}${values}</div>` : `<div class="panel muted">No value is computed yet.</div>`}${boxRest(bx.implications, used).map(boxHtml).join("")}</section>
  <section class="sec"><div class="sech"><h2>Claims</h2><p>What must be true for the design to work, and where each one stands. ${esc(v.limits || "")}</p></div>${boards}${boxRest(bx.claims, used).map(boxHtml).join("")}</section>
  <section class="sec"><div class="sech"><h2>Next steps</h2></div>${prose(nx.prose)}<div class="next">${next || `<div class="panel muted">The project names no milestone yet.</div>`}</div></section>`;
}
function viewMarketing(fam, c, cd) {
  const { page, map } = cd, v = page.verdict || {};
  const story = boxesFor(cd, "marketing", "story"), specs = boxesFor(cd, "marketing", "specs");
  const tiles = nodesOf(map, "station").filter(s => s.value_text).map(s => `<div class="tile"><b>${esc(s.value_text || "")}</b><span>${esc(s.title)}</span></div>`).concat(nodesOf(map, "claim").map(n => { const b = bindingValue(page.claims[n.key] || {}); return b ? `<div class="tile"><b>${fmtNum(b.measured)} ${esc(b.units)}</b><span>${esc(pretty(b.gate))} · ${tok(cd, n.token)}</span></div>` : ""; })).join("");
  const asked = (page.requirements || []).map(r => `<li style="display:grid;grid-template-columns:auto minmax(0,1fr);gap:10px;align-items:start;padding:8px 0;border-top:1px solid var(--line2)">${tok(cd, r.token)}<span>${esc(r.statement)}</span></li>`).join("");
  return `${banner(fam, c)}<div class="mk">
  <div class="mhead"><h1>${esc((page.meta || {}).name || S.thing)}</h1><p class="tag">${esc((page.description || {}).summary || "")}</p></div>
  ${heroBox(c, cd)}
  ${page.story || story.length ? `<div class="story"><h2>The story</h2>${prose(page.story)}${story.map(boxHtml).join("")}</div>` : ""}
  ${tiles || specs.length ? `<div class="sec"><h2>Specs</h2><div class="specs">${tiles}</div>${specs.length ? `<div class="grid2">${specs.map(boxHtml).join("")}</div>` : ""}</div>` : ""}
  ${asked ? `<div class="sec"><h2>What it must do</h2><ul>${asked}</ul></div>` : ""}
  <p class="verdict">${esc(v.sentence || "")} <a href="${href("eng", c.sha)}">The engineering view has every claim.</a></p>
  <div class="made"><img src="/static/made-with-nopekit.png" alt="Made with nopekit"><div><b>Made with nopekit</b><p class="muted small">nopekit is the check tool for this design. It runs the checks, records the evidence and bakes the data this page is drawn from.</p></div></div></div>`;
}
function viewTree(fam) {
  if (!fam.candidates.length) return `<section class="sec"><div class="sech"><h2>Family tree</h2></div>${empty()}</section>`;
  const sel = landing(fam), trunk = sel.line, sides = {}; let n = 0;
  for (const l of fam.lines) if (l.line !== trunk) sides[l.line] = n++ % 2 ? 0 : 2;
  const col = c => c.line === trunk ? 1 : sides[c.line];
  const nodes = fam.candidates.map((c, row) => `<a class="tnode ${markClass(c.mark)}${S.cand && c.sha.startsWith(S.cand) ? " viewing" : ""}" href="${href("eng", c.sha)}" data-id="${c.sha}" data-col="${col(c)}" style="grid-row:${row + 1};grid-column:${col(c) + 1}"><div class="th">${c.thumb ? `<img src="${rawUrl(S.thing, c.head, c.thumb)}" alt="Picture of ${esc(c.name)}" loading="lazy">` : `<div class="thph">no thumbnail</div>`}</div><b>${esc(c.name)}</b><span class="why2">${esc(c.subject)}</span><span class="ln">cand/${esc(c.line)} · ${esc(fmtDate(c.time))}${c.site ? "" : " · no page data"}</span></a>`).join("");
  return `<section class="sec"><div class="sech"><h2>Family tree</h2><p>Every candidate of ${esc(S.thing)}, newest on top. Each one is a commit; a tick on a line is a working commit between two candidates. Select a candidate to open its engineering view.</p></div>
    <div class="treelegend"><span><i style="border-color:var(--ok)"></i>selected</span><span><i style="border-color:var(--box)"></i>proposal</span><span><i style="border-color:var(--bad)"></i>not selected</span><span><i style="border-color:var(--line)"></i>not marked</span><span class="muted">The agent records these marks in the repo (<span class="mono">eddy/marks.json</span> on main). Eddy only shows them.</span></div>
    ${problems(fam.problems)}<div class="tree" id="tree"><svg class="links" aria-hidden="true"></svg>${nodes}</div></section>`;
}
function mountTree(fam) {
  const tree = $("#tree"); if (!tree) return;
  const draw = () => {
    const svg = tree.querySelector("svg.links"), o = tree.getBoundingClientRect(), out = [];
    const el = id => tree.querySelector(`[data-id="${id}"]`);
    const at = e => { const r = e.getBoundingClientRect(); return { l: r.left - o.left, r: r.right - o.left, t: r.top - o.top, b: r.bottom - o.top, cx: (r.left + r.right) / 2 - o.left, cy: (r.top + r.bottom) / 2 - o.top }; };
    for (const c of fam.candidates) {
      const pe = c.parent && el(c.parent), ce = el(c.sha); if (!pe || !ce) continue;
      const a = at(ce), p = at(pe), same = ce.dataset.col === pe.dataset.col;
      let x = a.cx, y1 = a.b, y2;
      if (same) { y2 = p.t; out.push(`<path d="M${x} ${y1} V${y2}"/>`); }
      else { const px = +ce.dataset.col < +pe.dataset.col ? p.l : p.r; y2 = p.cy; out.push(`<path d="M${x} ${y1} V${y2 - 8} Q${x} ${y2} ${x + (px > x ? 8 : -8)} ${y2} H${px}"/>`); y2 -= 8; }
      const n = Math.min(c.work, 6), span = y2 - y1;
      for (let i = 1; i <= n; i++) { const y = y1 + span * i / (n + 1); out.push(`<line x1="${x - 5}" y1="${y}" x2="${x + 5}" y2="${y}"/>`); }
    }
    svg.innerHTML = out.join("");
  };
  const ro = new ResizeObserver(draw); ro.observe(tree); LIVE.push(() => ro.disconnect()); draw();
  tree.querySelectorAll("img").forEach(i => i.addEventListener("load", draw));
}

// files: a GitHub-like browser over one commit's tree
async function viewFiles(fam, c) {
  const base = `/r/${enc(S.thing)}`, parent = fam.candidates.find(x => x.sha === c.parent);
  const tabs = `<div class="subtabs">${[["code", "Files"], ["commit", "Commit"], ["diff", "Diff from its parent"]].map(([k, l]) => `<button type="button" data-do="ftab" data-arg="${k}" aria-pressed="${S.ftab === k}">${l}</button>`).join("")}</div>`;
  let body;
  if (S.ftab === "commit") {
    const d = await api(`${base}/commit/${c.sha}`);
    body = `<div class="panel"><pre class="prompt">${esc(d.message)}</pre><dl class="kv"><dt>commit</dt><dd>${esc(c.sha)}</dd><dt>line</dt><dd>cand/${esc(c.line)}</dd><dt>parent</dt><dd>${parent ? `<a href="${href("files", parent.sha)}">${esc(parent.name)}</a>` : "none: the first candidate on its line"}</dd><dt>pushed</dt><dd>${esc(new Date(c.time * 1000).toLocaleString())}</dd><dt>changed</dt><dd>${d.changed} paths from its first parent</dd></dl></div>
      <div class="panel"><h3>Trailers, as git reads them</h3><dl class="kv">${d.trailers.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl></div>
      <div class="panel"><h3>Between ${parent ? esc(parent.name) : "the start"} and ${esc(c.name)}</h3><div class="small">${c.work} working commit${c.work === 1 ? "" : "s"}: the agent's own checkpoints, kept and never judged. ${c.evidence} evidence commit${c.evidence === 1 ? "" : "s"} since.</div></div>
      <div class="panel"><h3>Mark</h3><div><span class="mark ${markClass(c.mark)}">${MARKWORD[c.mark]}</span></div></div>`;
  } else if (S.ftab === "diff") {
    if (!parent) body = `<div class="panel muted">The first candidate has no parent to compare with.</div>`;
    else {
      const d = await api(`${base}/diff/${parent.head}..${c.head}`), spec = d.files.filter(f => f.spec), rest = d.files.filter(f => !f.spec);
      const file = f => `<div class="panel"><div class="small"><b>${{ A: "added", D: "deleted", M: "changed", T: "type changed" }[f.status] || esc(f.status)}</b> <a class="mono" href="${href("files", f.status === "D" ? parent.sha : c.sha, f.path)}">${esc(f.path)}</a></div>${f.diff ? `<div class="codebox"><pre>${f.diff.slice(2).map(l => `<span class="${l[0] === "+" ? "add" : l[0] === "-" ? "del" : l.startsWith("@@") ? "hunk" : "ctx"}">${esc(l) || " "}</span>`).join("")}</pre></div>` : `<div class="muted small">changed (${fmtSize(f.changed.old_size) || "—"} → ${fmtSize(f.changed.new_size) || "—"})</div>`}</div>`;
      body = `<div class="panel"><h3>Claims, requirements and milestones</h3>${spec.length ? "" : `<div class="small">No change. The conditions ${esc(c.name)} is judged by are the ones ${esc(parent.name)} was judged by.</div>`}</div>${spec.map(file).join("")}
        ${d.parts_changed.length ? `<div class="panel"><h3>Parts changed</h3>${d.parts_changed.map(p => `<div class="small mono">${esc(p.path)}</div>`).join("")}</div>` : ""}
        <h3>Other files (${rest.length})</h3>${rest.slice(0, 60).map(file).join("")}${rest.length > 60 ? `<div class="muted small">${rest.length - 60} more files changed.</div>` : ""}`;
    }
  } else {
    const crumbs = [`<a href="${href("files", c.sha)}">root</a>`]; let acc = "";
    for (const part of S.path ? S.path.split("/") : []) { acc = acc ? acc + "/" + part : part; crumbs.push(`<a href="${href("files", c.sha, acc)}">${esc(part)}</a>`); }
    const head = `<div class="fpath">${crumbs.join(" / ")}</div>`;
    let d = await api(`${base}/tree/${c.head}/${enc(S.path)}`).catch(() => null);
    if (d && d.view === "blob" || !d) d = await api(`${base}/blob/${c.head}/${enc(S.path)}`);
    if (d.view === "tree") {
      const pre = S.path ? S.path + "/" : "";
      body = head + `<div class="flist">${d.entries.map(e => `<a href="${href("files", c.sha, pre + e.name)}"><span aria-hidden="true">${e.type === "tree" ? "▸" : " "}</span><span class="nm">${esc(e.name)}${e.type === "tree" ? "/" : ""}</span><span class="sz">${fmtSize(e.size)}</span></a>`).join("")}</div>`;
    } else {
      const meta = `<div class="muted small mono">${fmtSize(d.size)} · <a href="${esc(d.raw)}">download</a></div>`;
      const show = d.kind === "text" ? `<div class="codebox"><pre>${esc(d.text)}</pre></div>` : d.kind === "image" ? `<div class="codebox"><img src="${esc(d.raw)}" alt="${esc(d.path)}" style="display:block;max-width:100%"></div>` : d.kind === "audio" ? `<audio controls src="${esc(d.raw)}"></audio>` : d.kind === "video" ? `<video controls src="${esc(d.raw)}" style="max-width:100%"></video>` : d.kind === "model" && d.path.toLowerCase().endsWith(".glb") ? `<div class="v3" id="blob3d" data-src="${esc(d.raw)}"><div class="vnote">Loading the model…</div></div>` : `<div class="panel muted">${d.kind === "symlink" ? "A symbolic link to " + esc(d.target) : d.kind === "too-large" ? "Too large to show here." : "This file is not text. Download it to open it."}</div>`;
      body = head + meta + show;
    }
  }
  return `${banner(fam, c)}<section class="sec"><div class="sech"><h2>Files of <span class="mono">${esc(c.name)}</span></h2><p>The repository as this candidate's commit holds it.</p></div>${tabs}${body}</section>`;
}
const empty = () => `<div class="panel"><b>Nothing has landed in ${esc(S.thing)} yet.</b><div class="small">A candidate appears here when your coding agent pushes one. Give the agent this:</div><pre class="prompt">Read ${esc(location.origin)}/agent.md and push this project to ${esc(location.origin)}/${esc(S.thing)}.git as its first candidate.</pre><div class="rowbtns"><button type="button" data-do="copy">Copy</button></div></div>`;

async function viewHome() {
  const [th, pk] = await Promise.all([api("/api/things"), api("/api/packs")]);
  const cards = await Promise.all(th.things.map(async t => {
    let fam = null, page = null, c = null;
    try { fam = await family(t.thing); c = landing(fam); if (c && c.site) page = await api(rawUrl(t.thing, c.head, "site/data/page.json")); } catch (e) { /* drawn without it */ }
    const pic = c && c.thumb ? `<img src="${rawUrl(t.thing, c.head, c.thumb)}" alt="Picture of ${esc(t.thing)}">` : `<div class="thph">no picture</div>`;
    return `<a class="pcard" href="#/p/${enc(t.thing)}"><div class="th">${pic}</div><div class="tx"><b>${esc(page ? page.meta.name : t.thing)}</b><span class="small" style="color:var(--ink2)">${esc(page ? page.description.summary : c ? c.subject : "Nothing has landed yet.")}</span><div class="pills">${c ? `<span class="mark ${markClass(c.mark)}">${esc(c.name)} ${MARKWORD[c.mark]}</span>` : ""}${page ? (page.verdict.tally || []).map(x => `<span class="tok tone-${tone(x.key)}">${glyph(x.key)} ${esc(x.label)}</span>`).join("") : ""}</div><span class="small muted mono">${esc(t.thing)} · ${fam ? fam.candidates.length : 0} candidate${fam && fam.candidates.length === 1 ? "" : "s"} on ${t.lines || 0} line${t.lines === 1 ? "" : "s"}${t.last_push ? " · last push " + esc(new Date(t.last_push).toLocaleString()) : ""}</span></div></a>`;
  }));
  return `<section class="sec"><div class="sech"><h1>Eddy</h1><p>A git server for things you design with a coding agent. The agent pushes; Eddy keeps only what follows the nopekit structure, and shows any candidate of any project.</p></div></section>
  <section class="sec"><h3>Projects</h3><div class="cards">${cards.join("") || `<div class="panel muted">No project yet.</div>`}</div>
    <div class="panel"><h3>Start a new project</h3><div class="small">Projects begin in your coding agent, not here. Open the agent in an empty folder with the nopekit plugin and give it this, with your idea in place of the dots:</div><pre class="prompt">Read ${esc(location.origin)}/agent.md first. Then use nopekit to design …. Interview me about it one question at a time, and push each candidate to Eddy as that guide says.</pre><div class="rowbtns"><button type="button" data-do="copy">Copy</button></div></div></section>
  <section class="sec"><h3>Packs</h3><div class="cards two">${pk.packs.map(p => `<a class="kcard" href="#/pack/${enc(p.name)}"><b>${esc(p.name)}</b><span class="small" style="color:var(--ink2)">${esc(p.description)}</span><span class="small muted">${p.gates.length} gate${p.gates.length === 1 ? "" : "s"}${p.proposers.length ? " · " + p.proposers.length + " proposer" : ""}</span></a>`).join("")}</div></section>`;
}
async function viewPack() {
  const p = await api(`/api/packs/${enc(S.pack)}`), tier = { instant: "instant", build: "needs a build", solver: "needs a solver", physical: "physical" };
  const gates = p.gates.map(g => `<div class="grow"><div class="gh1"><b>${esc(g.id)}</b><span>${esc(g.title)}</span></div><div class="chips"><span class="chip">${esc(tier[g.tier] || g.tier)}</span>${(g.needs || []).map(n => `<span class="chip">after ${esc(n)}</span>`).join("")}</div>${g.note ? `<div class="ctl"><span>Shown to fail on:</span> ${esc(g.note)} <span class="mono">(${esc(g.fixture)})</span></div>` : ""}</div>`).join("");
  let files;
  if (S.ppath && p.files.some(f => f[0] === S.ppath)) { const f = await api(`/api/packs/${enc(S.pack)}/file?path=${encodeURIComponent(S.ppath)}`); files = `<div class="fpath"><a href="#/pack/${enc(S.pack)}">root</a> / ${esc(S.ppath)}</div>` + (f.text != null ? `<div class="codebox"><pre>${esc(f.text)}</pre></div>` : `<div class="panel muted">Not a text file (${fmtSize(f.size)}).</div>`); }
  else files = `<div class="flist">${p.files.map(([n, sz]) => `<a href="#/pack/${enc(S.pack)}/${enc(n)}"><span aria-hidden="true"> </span><span class="nm">${esc(n)}</span><span class="sz">${fmtSize(sz)}</span></a>`).join("")}</div>`;
  return `<section class="sec"><div class="sech"><h1 class="mono" style="font-size:30px">${esc(p.name)}<span class="vchip">${esc(p.version)}</span></h1><p>${esc(p.description)}</p></div><div class="chips">${p.settles.slice(0, 14).map(s => `<span class="chip">${esc(s)}</span>`).join("")}${p.settles.length > 14 ? `<span class="chip">+${p.settles.length - 14} more</span>` : ""}</div></section>
  <section class="sec"><div class="sech"><h2>Gates</h2><p>Each gate answers one question about a design. A gate is admitted only with an input it is known to fail, so a pass means it could have said no.</p></div><div class="gtable">${gates || `<div class="grow muted">This pack declares no gate Eddy could read.</div>`}</div></section>
  <section class="sec"><div class="sech"><h2>Files</h2><p>The pack as this Eddy's nopekit bundles it.</p></div>${files}</section>`;
}

async function render() {
  const id = ++renderId;
  LIVE.splice(0).forEach(f => f());
  const inProject = !!S.thing, page = $("#page"), tabs = $("#tabs");
  listen(S.thing);
  const bar = cand => { $("#bar").innerHTML = `<a class="brand" href="#/">Eddy</a>${inProject ? `<span class="sepc">/</span><a class="crumb" href="#/p/${enc(S.thing)}">${esc(S.thing)}</a>${cand ? `<a class="cand ghchip" href="${href("tree", cand.sha)}" aria-label="Candidate ${esc(cand.name)}, ${MARKWORD[cand.mark]}. Open the family tree"><span class="mono">${esc(cand.name)}</span><span class="mark ${markClass(cand.mark)}">${MARKWORD[cand.mark]}</span></a>` : ""}` : S.view === "pack" ? `<span class="sepc">/</span><span class="crumb">packs</span>` : ""}<span class="sp"></span><button type="button" id="theme" data-do="theme" aria-label="Switch between dark and light">Dark / light</button>`; };
  bar(null); tabs.hidden = !inProject;
  $("#foot").textContent = "Every status on a candidate's views was computed by the agent's own nopekit run and committed with the candidate. Eddy draws it and executes nothing.";
  try {
    let html, after = () => {};
    if (S.view === "home") html = await viewHome();
    else if (S.view === "pack") html = await viewPack();
    else {
      const fam = await family(S.thing), c = (S.cand && byId(fam, S.cand)) || landing(fam);
      if (id !== renderId) return;
      bar(c);
      tabs.innerHTML = [["eng", "Engineering"], ["tree", "Tree"], ["marketing", "Marketing"], ["files", "Files"]].map(([v, l]) => `<a href="${href(v, c ? c.sha : "")}" ${S.view === v ? 'aria-current="page"' : ""}>${l}</a>`).join("");
      if (!c) html = `<section class="sec"><div class="sech"><h1>${esc(S.thing)}</h1></div>${problems(fam.problems)}${empty()}</section>`;
      else if (S.view === "tree") { html = viewTree(fam); after = () => mountTree(fam); }
      else if (S.view === "files") { html = await viewFiles(fam, c); after = () => { const b = $("#blob3d"); if (b) viewer(b, b.dataset.src); }; }
      else if (!c.site) html = banner(fam, c) + noSite(c);
      else {
        const cd = await candData(S.thing, c);
        if (S.view === "marketing") { html = viewMarketing(fam, c, cd); after = () => { mountHero(cd, true); mountBoxes(); }; }
        else { html = viewEng(fam, c, cd); after = () => { mountHero(cd, false); mountMap(cd); mountBoxes(); }; }
      }
    }
    if (id !== renderId) return;
    page.innerHTML = html; after();
  } catch (err) {
    if (id !== renderId) return;
    page.innerHTML = `<section class="sec"><div class="panel"><b>This page could not be drawn.</b><div class="small">${esc(err.message || err)}</div></div></section>`;
  }
  const key = S.view + S.thing + S.cand + S.pack;
  if (render.last !== key) window.scrollTo(0, 0);
  render.last = key;
}
route();
