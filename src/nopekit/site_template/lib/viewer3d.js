// SPDX-License-Identifier: Apache-2.0
// viewer3d.js — the MODEL3D / FIELD viewer, and the verdict overlay on top of it.
//
// This module is the reason the site is worth building. A readiness line saying
// `cad.clash: 1 interfering pair — carriage / guide_roller, 0.41 mm^3` is a sentence
// somebody has to go and act on. The same verdict, carrying two locators, lights
// both parts up here at the pose where it happens, and the reader is looking at
// the problem a second later.
//
// three.js arrives through the import map declared in index.html, which points
// either at ./vendor/ or at the CDN. Nothing is bundled and nothing is built.
// Every entry point below is written so that a failure to load — no network, no
// WebGL, a corporate proxy eating the CDN — degrades to a NAMED LIST of parts
// with the same highlights on it, rather than to a blank rectangle. A debugging
// tool that goes silent in the one environment where you cannot install anything
// is not a debugging tool.

import { el, mount } from "./dom.js";

let cached = null;

/** Load three.js and the two addons, once. The specifiers are bare on purpose —
 *  `three/addons/…` resolves through the import map, which is what lets the same
 *  file work against the CDN and against a vendored copy without an edit.
 *
 *  The meshopt decoder is a third addon, imported on its own and caught on its
 *  own: a GLB written with `EXT_meshopt_compression` cannot load without it,
 *  and every other GLB does not need it. A failure there leaves `MeshoptDecoder`
 *  null and the plain GLB loads as before. (What it closes: a GLB compressed
 *  for the web failed in this viewer with the loader's own error, and inside
 *  the `Promise.all` above a decoder that failed to import would have failed
 *  every model with it.) */
export async function loadThree() {
  if (!cached) {
    cached = (async () => {
      const THREE = await import("three");
      const [{ OrbitControls }, { GLTFLoader }] = await Promise.all([
        import("three/addons/controls/OrbitControls.js"),
        import("three/addons/loaders/GLTFLoader.js"),
      ]);
      let MeshoptDecoder = null;
      try {
        ({ MeshoptDecoder } = await import("three/addons/libs/meshopt_decoder.module.js"));
      } catch (err) {
        MeshoptDecoder = null;                 // a plain GLB still loads
      }
      return { THREE, OrbitControls, GLTFLoader, MeshoptDecoder };
    })().catch((err) => { cached = null; throw err; });
  }
  return cached;
}

/** three.js rewrites glTF node names on import: whitespace becomes `_` and
 *  `. : / [ ]` are dropped entirely (PropertyBinding.sanitizeNodeName). A gate
 *  emitting a locator for `lid.boss` therefore addresses a node that, from the
 *  page's side, is called `lidboss` — the lookup misses, the pin never appears,
 *  and it looks exactly like a gate that found nothing. Every lookup goes
 *  through both spellings for that reason. */
function sanitise(name) {
  return String(name).replace(/\s/g, "_").replace(/[.:/[\]]/g, "");
}

/** ``onProgress(loaded, total)`` follows the GLB's download (``total`` is 0
 *  when the server sends no length); ``onSpinStop()`` runs when a pointer on
 *  the model stops the spin that ``setSpin`` started. Both are optional, and a
 *  caller that passes neither gets the viewer it had before (the front page's
 *  hero and the technical stage).
 *
 *  FIELD MODE (``field: true``, or a view of kind ``field``; issue #15): the
 *  colours on the mesh ARE the data, a colour scale the legend beside it
 *  explains, so the viewer shows them as the file holds them. Every mesh gets
 *  three.js's own ``MeshBasicMaterial`` with its vertex colours and no tone
 *  mapping, the scene gets no lights, and the renderer writes the colours to
 *  the canvas unconverted (``LinearSRGBColorSpace`` out, ``NoToneMapping``).
 *  Measured on 2026-10-21: ParaView 6.2's glTF exporter writes COLOR_0 as the
 *  colour map's own sRGB bytes (UNSIGNED_BYTE, normalized; Grayscale at 0.25
 *  is 64), so a pixel equals the byte only when nothing converts it;
 *  `tests/test_field_browser.py` holds a pixel to the ramp within 2. A field
 *  has no parts: tint, highlight, isolate and explode do nothing. What it
 *  closes before it could slip through: the three-point rig shades a scale's
 *  colours by the angle of each face, and a reader matches the shade against
 *  the legend (drawn lit, none of the test's four colours survived).
 *  *Rejected:* re-encoding the vertex colours into linear values ourselves (a
 *  GLB repacker is code a trusted tool already is); an unlit material with the
 *  renderer's default sRGB output (``KHR_materials_unlit`` alone): it encodes
 *  each raw byte again, and drew 64 as 137 and 128 as 188 (measured).
 *
 *  LIT FIELD MODE (``light: "scene"``, from the figure's sidecar; design P2):
 *  a solved field drawn as a scene, the way its still was drawn, so a reader
 *  reads the shape of the body and the paths of the air. Each mesh gets
 *  three.js's ``MeshStandardMaterial`` (roughness 0.6, no metal) with its
 *  vertex colours, under a rig that turns with the camera (``litRig``, not the
 *  model mode's lights, which are fixed in the world); normals are computed
 *  where the file has none (ParaView's glTF writes none, measured); the first
 *  view is the file's own camera when it has one, else the box's diagonal,
 *  and the view turns about the world axis nearest that camera's up
 *  (``orbitAbout``), through a point of its line of sight in the body it
 *  looks at (``firstLitView``).
 *  Light and shade make a colour darker or lighter than the key's, so the
 *  viewer keeps the unlit material too, and ``setFlat(true)`` (the "Flat
 *  colours" toggle) swaps it in: then each pixel is the file's byte, as in
 *  the plain field mode, and `tests/test_field_browser.py` holds it within 2.
 *  The output stays ``LinearSRGBColorSpace`` with no tone mapping in both, so
 *  the toggle changes the material and nothing else. Faces are drawn on both
 *  sides, lit by the side the camera sees. *Rejected:* ``BackSide`` alone
 *  (ParaView's front-face cull on the motorBike body): it draws nothing of a
 *  mesh whose faces wind toward the camera, and which way a file winds is
 *  the exporter's (P7 measured the motorBike's both ways in the browser: a
 *  file with every face turned over draws the same pixels, and no face is
 *  black, `test_field_browser.BothSidesOfTheHeroAreLit`); an sRGB
 *  output for the lit material (the flat toggle would then move every
 *  colour of the key). */
export function createViewer({ host, view, onSelect, onReady, onError, onProgress, onSpinStop,
  field = false, light }) {
  const isField = !!field || (view && view.kind === "field");
  const lightMode = light !== undefined ? light : ((view && view.meta && view.meta.light) || "");
  const isLit = isField && lightMode === "scene";
  const state = {
    explode: 0, isolate: null, wireframe: false,
    isolateNodes: null,          // Set of Object3D, or null: `isolateNodes`
    tints: new Map(),            // node name -> CSS colour: `tint`
    paused: false,
    nodes: new Map(),            // name (and sanitised alias) -> Object3D
    movers: new Map(),           // mover name -> { offset, nodes }
    order: [],                   // mover names, stack order
    highlights: new Map(),       // node name -> severity
    pins: [],                    // { locator, verdict, object, elem }
    missing: new Set(),          // locator targets this GLB does not contain
    flat: false,                 // a lit field drawn in its flat colours: `setFlat`
    ready: false, disposed: false,
  };

  const canvasHost = el("div", { class: "viewer-canvas" });
  const pinLayer = el("div", { class: "pin-layer" });
  mount(host, canvasHost, pinLayer);

  let THREE, renderer, scene, camera, controls, root, raycaster, pointer;
  let Orbit = null;              // three.js's OrbitControls class: `makeControls`
  let dirty = true, frameHandle = 0;
  const basePos = new Map();     // Object3D -> original local position
  const baseMat = new Map();     // Mesh -> original material, for EVERY mesh
  const clones = new Set();      // the materials this viewer cloned, disposed on restore
  const fieldMats = new Map();   // a lit field's Mesh -> { lit, flat }: `setFlat`
  let fileCamera = null;         // the file's first camera, a lit field's first view
  let home = null;               // the first framing's direction: `home()`
  let shift = 0;                 // the share of the width the model moves right: `setFrameShift`

  const invalidate = () => { dirty = true; };

  // A viewer disposed while it loads stops at the next await: it makes no
  // renderer, fetches no model and calls no ``onError``, and a renderer made
  // before the dispose is released. What slipped through (review of SPEC 40):
  // disposed while three.js loaded (a live rebuild changed the view), it went
  // on to make a WebGL renderer nothing disposed and fetch the whole GLB (6 MB
  // for a drone), one leaked context each time against a browser cap near 16;
  // and its late ``onError`` cleared the caller's NEW viewer.
  const failed = (err) => { if (!state.disposed && onError) onError(err); };

  async function start() {
    let mods;
    try {
      mods = await loadThree();
    } catch (err) {
      failed(new Error(
        "three.js did not load (" + (err && err.message ? err.message : err) + "). " +
        "The page is offline, or something blocks the CDN. The command `nopekit site vendor` " +
        "puts a copy in site/vendor/, and the import map uses that copy first."));
      return;
    }
    if (state.disposed) return;
    THREE = mods.THREE;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    } catch (err) {
      failed(new Error("this browser gave no WebGL context: " + (err && err.message ? err.message : err)));
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    if (isField) {
      renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
      renderer.toneMapping = THREE.NoToneMapping;
    }
    canvasHost.appendChild(renderer.domElement);

    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(42, 1, 0.01, 10000);
    // Three lights, no environment map: an environment would need a texture the
    // site would have to ship, and a matte three-point rig reads shape better on
    // untextured engineering geometry anyway. A field gets none (field mode,
    // above): its colours are a scale, and a light would shade them. A lit
    // field (lit field mode, above) gets a rig that turns with the camera, as
    // ParaView's light kit does, so the side the reader looks at is the lit
    // side from any first view the file gives.
    if (isLit) litRig();
    else if (!isField) {
      scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 1.5));
      const key = new THREE.DirectionalLight(0xffffff, 2.0); key.position.set(1, 2, 1.6);
      const fill = new THREE.DirectionalLight(0xffffff, 0.7); fill.position.set(-1.4, 0.4, -1);
      scene.add(key, fill);
    }

    Orbit = mods.OrbitControls;
    makeControls();

    raycaster = new THREE.Raycaster();
    pointer = new THREE.Vector2();

    try {
      root = await loadModel(mods.GLTFLoader, mods.MeshoptDecoder, view.src);
    } catch (err) {
      failed(new Error(
        `the model at ${view.src} did not load (${err && err.message ? err.message : err}). ` +
        "The command `nopekit site build` writes it. When the file is missing, its viewgen " +
        "said that it is not available."));
      return;
    }
    if (state.disposed) {
      // dispose() ran during the download: release again what it may have
      // missed, and the model that arrived after it.
      controls.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
      root.traverse((obj) => {
        if (obj.geometry) obj.geometry.dispose();
        for (const m of [].concat(obj.material || [])) m.dispose && m.dispose();
      });
      return;
    }
    if (isLit) litField(root);
    else if (isField) unlit(root);
    scene.add(root);
    indexNodes();
    readExplodeManifest();
    if (isLit && state.flat) applyFlat();      // asked for before the file loaded
    if (isLit) {
      resize();
      firstLitView();
    } else if (isField) {
      // A field is a cut, flat across its thinnest side: the first view looks
      // down on it, tilted 20 degrees so it reads as 3D, and fills the frame
      // (`fitField`). What slipped through (review of P2): the general
      // framing showed the 10 to 1 CFD strip edge-on at an angle, small in
      // its frame. Layout only: no colour and no number moves.
      resize();
      fitField();
    } else {
      frameAll();
    }
    home = camera.position.clone().sub(controls.target).normalize();
    state.ready = true;
    resize();
    loop();
    onReady && onReady(api);
  }

  function loadModel(GLTFLoader, MeshoptDecoder, src) {
    return new Promise((resolve, reject) => {
      const loader = new GLTFLoader();
      if (MeshoptDecoder) loader.setMeshoptDecoder(MeshoptDecoder);
      const progress = onProgress
        ? (ev) => onProgress(ev.loaded || 0, ev.lengthComputable ? ev.total : 0)
        : undefined;
      loader.load(src, (gltf) => {
        fileCamera = (gltf.cameras || []).find((c) => c && c.isPerspectiveCamera) || null;
        resolve(gltf.scene || gltf.scenes[0]);
      }, progress, reject);
    });
  }

  /** Field mode: each mesh drawn with three.js's MeshBasicMaterial, its vertex
   *  colours as the file holds them and no tone mapping. A mesh with no
   *  COLOR_0 is drawn white, never in a colour the file did not give. */
  function unlit(object) {
    object.traverse((obj) => {
      if (!obj.isMesh) return;
      const old = obj.material;
      obj.material = new THREE.MeshBasicMaterial({
        vertexColors: !!(obj.geometry && obj.geometry.getAttribute("color")),
        toneMapped: false, side: THREE.DoubleSide });
      for (const m of [].concat(old || [])) m && m.dispose && m.dispose();
    });
  }

  /** Lit field mode: each mesh gets a lit material and keeps an unlit one,
   *  both with its vertex colours; normals are computed where the file has
   *  none. The lit one is shown first. */
  function litField(object) {
    object.traverse((obj) => {
      if (!obj.isMesh) return;
      const geometry = obj.geometry;
      const coloured = !!(geometry && geometry.getAttribute("color"));
      if (geometry && !geometry.getAttribute("normal")) geometry.computeVertexNormals();
      const old = obj.material;
      const mats = {
        lit: new THREE.MeshStandardMaterial({ vertexColors: coloured, roughness: 0.6,
          metalness: 0, toneMapped: false, side: THREE.DoubleSide }),
        flat: new THREE.MeshBasicMaterial({ vertexColors: coloured, toneMapped: false,
          side: THREE.DoubleSide }),
      };
      fieldMats.set(obj, mats);
      obj.material = mats.lit;
      for (const m of [].concat(old || [])) m && m.dispose && m.dispose();
    });
  }

  /** A lit field's lights, children of the camera: a key light above and
   *  to the left of the reader, a weak fill from below and to the right, and
   *  an ambient floor. Three.js's lights divide by pi (its physical units),
   *  so the key and the ambient sum to about pi: a face square to the key is
   *  drawn near the key's colour, and a face in shade at about a third of it.
   *  What it closes (measured on the motorBike's glTF, design P2): the model
   *  mode's rig is fixed in the world, and from ParaView's camera, which looks
   *  from the other side, every colour drew near black. *Rejected:* a light
   *  on the camera's axis alone (a headlight flattens the shape the light is
   *  there to show). */
  function litRig() {
    scene.add(camera);
    scene.add(new THREE.AmbientLight(0xffffff, 1.0));
    const aim = new THREE.Object3D();
    aim.position.set(0, 0, -1);
    camera.add(aim);
    const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(-0.6, 1.0, 0.8);
    const fill = new THREE.DirectionalLight(0xffffff, 0.5); fill.position.set(0.8, -0.4, 0.6);
    for (const light of [key, fill]) { light.target = aim; camera.add(light); }
  }

  /** The orbit controls, built new; a second call carries the target and the
   *  spin of the old ones over and disposes of them. */
  function makeControls() {
    const old = controls;
    controls = new Orbit(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.12;
    controls.addEventListener("change", invalidate);
    if (!old) return;
    controls.target.copy(old.target);
    controls.autoRotate = old.autoRotate;
    controls.autoRotateSpeed = old.autoRotateSpeed;
    old.dispose();
  }

  /** Orbit about ``up``: the camera's up, and controls built after it.
   *  three.js's OrbitControls (r169) reads ``camera.up`` once, when it is
   *  built, and turns about that axis for its life. What slipped through
   *  review of P2: the lit field set the up of ParaView's Z-up camera on
   *  controls built about +Y, so a drag or the hero's spin turned the
   *  motorBike end over end and took the camera under the ground. */
  function orbitAbout(up) {
    if (camera.up.equals(up)) return;
    camera.up.copy(up);
    makeControls();
  }

  /** The world axis nearest ``v``, with its sign: a camera's up tilted with
   *  its line of sight (13 degrees on the motorBike's) stands for the up of
   *  the data, and an orbit about the tilted one would rock the floor. */
  function nearestAxis(v) {
    const a = [v.x, v.y, v.z];
    const k = a.map(Math.abs).indexOf(Math.max(...a.map(Math.abs)));
    return new THREE.Vector3().setComponent(k, Math.sign(a[k]) || 1);
  }

  /** A lit field's flat colours on (the unlit material) or off (the lit). */
  function applyFlat() {
    for (const [obj, mats] of fieldMats) {
      const material = state.flat ? mats.flat : mats.lit;
      material.wireframe = state.wireframe;
      obj.material = material;
      baseMat.set(obj, material);
    }
    invalidate();
  }

  /** A lit field's first view: the file's own camera, when it has one, aimed
   *  at the point of its line of sight nearest the middle of the box; else
   *  the box's diagonal, as a model is framed. The view turns about that
   *  point. The box is the whole file's, or the smallest mesh box the line of
   *  sight passes through when that one is less than half the whole box's
   *  size. What slipped through P2 (found in P7 of the motorBike design): the
   *  whole box alone, and the motorBike's stream tracks run 15 m downstream
   *  of the bike, so a drag or the spin swung the bike to a corner of the
   *  view and far off. *Rejected:* any smaller mesh box (a ground's own box,
   *  nearly the whole one, moved the turn off the ground's middle:
   *  `test_field_browser.ALitFieldTurnsAboutItsUp`); a ray cast to the first
   *  face hit (a thin track in front of the body would be the point); the
   *  file's own focal point (a glTF camera has none). */
  function firstLitView() {
    if (!fileCamera) { frameObjects(null); return; }
    const box = boundsOf(null);
    if (box.isEmpty()) return;
    fileCamera.updateWorldMatrix(true, false);
    const position = new THREE.Vector3().setFromMatrixPosition(fileCamera.matrixWorld);
    const ahead = new THREE.Vector3(0, 0, -1).transformDirection(fileCamera.matrixWorld);
    const up = new THREE.Vector3(0, 1, 0).transformDirection(fileCamera.matrixWorld);
    const sight = new THREE.Ray(position, ahead);
    const size = (b) => b.getSize(new THREE.Vector3()).length();
    let aim = box;
    root.traverse((obj) => {
      if (!obj.isMesh) return;
      const part = new THREE.Box3().setFromObject(obj);
      if (!part.isEmpty() && sight.intersectsBox(part) && size(part) < size(aim)) aim = part;
    });
    if (size(aim) >= size(box) / 2) aim = box;
    const centre = aim.getCenter(new THREE.Vector3());
    const reach = Math.max(centre.clone().sub(position).dot(ahead), 1e-3);
    const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-3);
    camera.fov = fileCamera.fov || camera.fov;
    orbitAbout(nearestAxis(up));
    camera.near = Math.max(reach / 1000, 1e-4);
    camera.far = (reach + radius) * 100;
    camera.position.copy(position);
    controls.target.copy(position).addScaledVector(ahead, reach);
    camera.updateProjectionMatrix();
    controls.update();
    invalidate();
  }

  /** Every named node by name (and its sanitised alias), and the material of
   *  EVERY mesh. The two walks are apart on purpose: a GLB mesh is often an
   *  unnamed child of the named node a locator addresses, and when one walk
   *  skipped the unnamed objects, their cloned highlight was never put back and
   *  a second colour stacked on the first (review of the two pages). */
  function indexNodes() {
    root.traverse((obj) => {
      if (obj.isMesh) baseMat.set(obj, obj.material);
      if (!obj.name) return;
      if (!state.nodes.has(obj.name)) state.nodes.set(obj.name, obj);
      const alias = sanitise(obj.name);
      if (alias !== obj.name && !state.nodes.has(alias)) state.nodes.set(alias, obj);
      basePos.set(obj, obj.position.clone());
    });
  }

  /** The explode manifest, as `nopekit site build` derives it (and as
   *  site/explode.json overrides it): `{movers: {name: {offset, nodes, rank}}}`
   *  with offsets in the GLB's own coordinate space. */
  function readExplodeManifest() {
    const manifest = (view.meta && view.meta.explode) || {};
    const movers = manifest.movers || {};
    const names = Object.keys(movers);
    names.sort((a, b) => (movers[a].rank ?? 0) - (movers[b].rank ?? 0) || a.localeCompare(b));
    for (const name of names) {
      const record = movers[name] || {};
      state.movers.set(name, {
        offset: (record.offset || [0, 0, 0]).map(Number),
        nodes: (record.nodes || [name]).map(String),
      });
    }
    state.order = names;
    if (!names.length) {
      // A model3d view with no manifest is legal — a single-part export has
      // nothing to explode. The slider is hidden by the caller rather than
      // shown doing nothing, which is the more honest of the two.
      state.order = Array.from(new Set(Array.from(state.nodes.values())
        .filter((o) => o.isMesh && o.name).map((o) => o.name)));
    }
  }

  function lookup(name) {
    return state.nodes.get(name) || state.nodes.get(sanitise(name)) || null;
  }

  /** Objects belonging to a mover — or, when there is no manifest, the node of
   *  that name on its own. */
  function objectsFor(moverName) {
    const mover = state.movers.get(moverName);
    const names = mover ? mover.nodes : [moverName];
    return names.map(lookup).filter(Boolean);
  }

  // ------------------------------------------------------------------- //
  // explode
  // ------------------------------------------------------------------- //
  /** Apply each mover's offset × t.
   *
   *  The manifest's offsets are in the GLB's coordinate space, and a node's
   *  `position` is in its PARENT's space. For a flat assembly those are the same
   *  thing, which is why an implementation that ignores the difference looks
   *  correct right up until someone exports a nested sub-assembly under a rotated
   *  parent — at which point the lid slides out sideways and the manifest gets
   *  blamed for it. Converting through the parent's world matrix costs four lines
   *  and removes the class of bug. */
  function applyExplode() {
    if (!state.ready) return;
    const t = state.explode;
    const q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
    for (const [name, mover] of state.movers) {
      const offset = new THREE.Vector3(mover.offset[0] || 0, mover.offset[1] || 0, mover.offset[2] || 0);
      for (const obj of objectsFor(name)) {
        const base = basePos.get(obj);
        if (!base) continue;
        const local = offset.clone();
        if (obj.parent) {
          obj.parent.updateWorldMatrix(true, false);
          obj.parent.matrixWorld.decompose(p, q, s);
          local.applyQuaternion(q.clone().invert());
          local.set(local.x / (s.x || 1), local.y / (s.y || 1), local.z / (s.z || 1));
        }
        obj.position.copy(base).addScaledVector(local, t);
      }
    }
    invalidate();
  }

  // ------------------------------------------------------------------- //
  // isolate / wireframe / highlight
  // ------------------------------------------------------------------- //
  function applyVisibility() {
    const keep = state.isolateNodes || (state.isolate ? new Set(objectsFor(state.isolate)) : null);
    root.traverse((obj) => {
      if (!obj.isMesh) return;
      obj.visible = !keep || inSet(obj, keep);
      obj.material && (obj.material.wireframe = state.wireframe);
    });
    invalidate();
  }

  function inSet(obj, keep) {
    for (let node = obj; node; node = node.parent) if (keep.has(node)) return true;
    return false;
  }

  /** Tint the located parts. The material is CLONED before the emissive is set:
   *  glTF exporters share one material across many nodes, and writing to the
   *  shared instance lights up every part that happens to be the same plastic —
   *  a highlight that names three parts and colours nine is worse than none,
   *  because the reader goes and inspects the six that are fine.
   *
   *  One pass paints both layers from the originals: every mesh gets its own
   *  material back and every clone of the last pass is disposed, then the
   *  presentation colours (`tint`), then the highlights. A highlight is painted
   *  last and from the ORIGINAL material, so a colour a reader chose never
   *  sits under a verdict's tint, and neither layer stacks on itself. */
  function applyHighlights() {
    for (const [obj, material] of baseMat) {
      obj.material = material;
      material.wireframe = state.wireframe;
    }
    for (const clone of clones) clone.dispose && clone.dispose();
    clones.clear();
    const painted = new Set();
    for (const [name, severity] of state.highlights) {
      for (const obj of objectsFor(name)) {
        obj.traverse((child) => {
          if (!child.isMesh) return;
          const source = baseMat.get(child) || child.material;
          const clone = source.clone();
          const colour = new THREE.Color(severity === "fail" ? 0xd7263d
            : severity === "warn" ? 0xd98a00 : 0x2f7fd1);
          if (clone.emissive) { clone.emissive = colour; clone.emissiveIntensity = 0.85; }
          else if (clone.color) clone.color = colour;
          clone.wireframe = state.wireframe;
          child.material = clone;
          clones.add(clone);
          painted.add(child);
        });
      }
    }
    for (const [name, css] of state.tints) {
      for (const obj of nodesFor([name])) {
        obj.traverse((child) => {
          if (!child.isMesh || painted.has(child)) return;
          const source = baseMat.get(child) || child.material;
          const clone = source.clone();
          if (clone.color) clone.color = new THREE.Color(css);
          clone.wireframe = state.wireframe;
          child.material = clone;
          clones.add(clone);
          painted.add(child);
        });
      }
    }
    invalidate();
  }

  /** Objects for a list of node names: the node itself when it is there, else
   *  a mover of that name. What a part row of a view's `meta.parts` names. */
  function nodesFor(names) {
    const out = [];
    for (const name of names || []) {
      const obj = lookup(name);
      if (obj) out.push(obj); else out.push(...objectsFor(name));
    }
    return out;
  }

  // ------------------------------------------------------------------- //
  // framing
  // ------------------------------------------------------------------- //
  function boundsOf(objects) {
    const box = new THREE.Box3();
    if (!objects || !objects.length) box.setFromObject(root);
    else for (const obj of objects) box.expandByObject(obj);
    return box;
  }

  function frameObjects(objects, { animate = true } = {}) {
    const box = boundsOf(objects);
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const centre = box.getCenter(new THREE.Vector3());
    const radius = Math.max(size.length() / 2, 1e-3);
    const distance = (radius / Math.sin((camera.fov * Math.PI) / 360)) * 1.25;
    const direction = camera.position.clone().sub(controls.target);
    if (direction.lengthSq() < 1e-9) direction.set(1, 0.75, 1);
    direction.normalize().multiplyScalar(distance);
    camera.near = Math.max(distance / 1000, 1e-4);
    camera.far = distance * 100;
    camera.updateProjectionMatrix();
    controls.target.copy(centre);
    camera.position.copy(centre).add(direction);
    controls.update();
    invalidate();
  }

  function frameAll() { if (isField && !isLit && state.ready) fitField(); else frameObjects(null); }

  /** A field's first view: along its box's thinnest side, tilted 20 degrees
   *  about the longest, at the distance that fits the box's two other sides
   *  in the canvas with 8% to spare. */
  function fitField() {
    const box = boundsOf(null);
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const centre = box.getCenter(new THREE.Vector3());
    const sides = [size.x, size.y, size.z];
    const thin = sides.indexOf(Math.min(...sides));
    const long = [0, 1, 2].filter((i) => i !== thin).sort((a, b) => sides[b] - sides[a]);
    const look = new THREE.Vector3().setComponent(thin, 1);
    const across = new THREE.Vector3().setComponent(long[1], 1);
    const tilt = (20 * Math.PI) / 180;
    const direction = look.clone().multiplyScalar(Math.cos(tilt))
      .add(across.clone().multiplyScalar(-Math.sin(tilt)));
    orbitAbout(across);
    const vfov = (camera.fov * Math.PI) / 180;
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * (camera.aspect || 1));
    const half = Math.max(sides[long[0]] / 2 / Math.tan(hfov / 2),
      sides[long[1]] / 2 / Math.tan(vfov / 2), 1e-3);
    const distance = half * 1.08 + sides[thin];
    camera.near = Math.max(distance / 1000, 1e-4);
    camera.far = distance * 100;
    camera.updateProjectionMatrix();
    controls.target.copy(centre);
    camera.position.copy(centre).add(direction.multiplyScalar(distance));
    controls.update();
    invalidate();
  }

  /** Back to the first framing's angle and distance. */
  function goHome() {
    if (!state.ready || !home) return;
    if (isLit) { firstLitView(); return; }
    camera.position.copy(controls.target).add(home);
    frameAll();
  }

  /** Move the model right on the canvas by ``fraction`` of its width, so copy
   *  set over the left of a full-width viewer does not cover it. A view offset
   *  on the camera, so a pick and a pin read the same projection. */
  function applyShift() {
    if (!camera) return;
    const w = canvasHost.clientWidth || 1;
    const h = canvasHost.clientHeight || 1;
    if (shift) camera.setViewOffset(w, h, -shift * w, 0, w, h);
    else camera.clearViewOffset();
    camera.updateProjectionMatrix();
  }

  // ------------------------------------------------------------------- //
  // pins
  // ------------------------------------------------------------------- //
  /** One pin per locator that resolves to a node in this GLB. A locator naming a
   *  node the model does not contain is collected in `state.missing` and handed
   *  back to the caller, which reports it instead of dropping it: that mismatch
   *  means the node-naming interface between a viewgen and its pack's gates has
   *  drifted, and it is invisible from both ends. */
  function setPins(entries) {
    for (const pin of state.pins) pin.elem.remove();
    state.pins = [];
    state.missing = new Set();
    for (const { locator, verdict, severity, label, onclick } of entries) {
      let object = null;
      const placeable = Array.isArray(locator.position) && locator.position.length >= 3;
      // A locator with neither a target nor an explicit position addresses the
      // WHOLE view. That is legitimate — "this assembly is over its mass budget"
      // — but there is no point on the model to pin it to, so it stays in the
      // rail beside the viewer and gets no pin, rather than a pin that is
      // permanently hidden and looks like a bug to whoever reads the DOM.
      if (!locator.target && !placeable) continue;
      if (locator.target) {
        object = lookup(locator.target);
        if (!object) {
          const objs = objectsFor(locator.target);
          object = objs.length ? objs[0] : null;
        }
        if (!object) { state.missing.add(locator.target); continue; }
      }
      const elem = el("button", {
        class: `pin sev-${severity}`, type: "button",
        title: label || locator.target || "",
        onclick: (ev) => { ev.stopPropagation(); onclick && onclick(locator, verdict); },
      },
        el("span", { class: "pin-shape", "aria-hidden": "true",
          text: severity === "fail" ? "✕" : severity === "warn" ? "▲" : "●" }),
        el("span", { class: "pin-text", text: label || locator.target || "here" }));
      pinLayer.appendChild(elem);
      state.pins.push({ locator, verdict, object, elem, position: locator.position });
    }
    invalidate();
    return state.missing;
  }

  const tmp = () => new THREE.Vector3();

  function updatePins() {
    if (!state.pins.length) return;
    const rect = renderer.domElement.getBoundingClientRect();
    const box = new THREE.Box3();
    for (const pin of state.pins) {
      let point;
      if (pin.object) {
        if (!pin.object.visible && state.isolate) { pin.elem.style.display = "none"; continue; }
        box.setFromObject(pin.object);
        if (box.isEmpty()) { pin.elem.style.display = "none"; continue; }
        point = box.getCenter(tmp());
      } else if (Array.isArray(pin.position) && pin.position.length >= 3) {
        point = new THREE.Vector3(...pin.position.slice(0, 3).map(Number));
      } else {
        pin.elem.style.display = "none";
        continue;
      }
      const projected = point.clone().project(camera);
      if (projected.z > 1) { pin.elem.style.display = "none"; continue; }
      pin.elem.style.display = "";
      pin.elem.style.left = `${((projected.x + 1) / 2) * rect.width}px`;
      pin.elem.style.top = `${((1 - projected.y) / 2) * rect.height}px`;
    }
  }

  // ------------------------------------------------------------------- //
  // loop and events
  // ------------------------------------------------------------------- //
  function loop() {
    if (state.disposed || state.paused) { frameHandle = 0; return; }
    frameHandle = requestAnimationFrame(loop);
    const moved = controls.update();          // damping: true while it coasts
    if (!dirty && !moved) return;
    dirty = false;
    renderer.render(scene, camera);
    updatePins();
  }

  function resize() {
    if (!renderer) return;
    const w = canvasHost.clientWidth || 1;
    const h = canvasHost.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    applyShift();
    invalidate();
  }

  const observer = new ResizeObserver(resize);
  observer.observe(canvasHost);

  // A hand on the model stops the spin: a reader who grabs a turning model
  // means to look at one side of it.
  canvasHost.addEventListener("pointerdown", () => {
    if (!controls || !controls.autoRotate) return;
    controls.autoRotate = false;
    onSpinStop && onSpinStop();
  });

  canvasHost.addEventListener("pointerdown", (ev) => {
    if (!state.ready || ev.button !== 0) return;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObject(root, true).find((h) => h.object.visible);
    onSelect && onSelect(hit ? nameOf(hit.object) : null);
  });

  /** Walk up to the nearest named ancestor. A GLB mesh is often an unnamed child
   *  of the named node a gate's locator addresses, so reporting the mesh's own
   *  (empty) name would make clicking a part select nothing. */
  function nameOf(object) {
    for (let node = object; node; node = node.parent) {
      if (node === root) break;
      if (node.name) return node.name;
    }
    return "";
  }

  const api = {
    get ready() { return state.ready; },
    get movers() { return state.order.slice(); },
    /** Whether there is anything to explode. `movers` falls back to the node
     *  list when a view ships no manifest, so the two questions are not the same
     *  one: a single-part export has parts to isolate and nothing to pull apart,
     *  and a slider that moves while nothing on screen does reads as a broken
     *  viewer rather than as a missing manifest. */
    get canExplode() { return !isField && state.movers.size > 0; },
    get nodeNames() { return Array.from(new Set(Array.from(state.nodes.keys()))); },
    get missing() { return Array.from(state.missing); },
    get field() { return isField; },
    /** Whether this is a lit field (``light: "scene"``), which has flat colours. */
    get lit() { return isLit; },
    get flat() { return state.flat; },
    /** A lit field drawn in its flat colours (each pixel the file's byte, as
     *  the key shows it), or lit again. Does nothing to any other view. */
    setFlat(on) { if (!isLit) return; state.flat = !!on; if (state.ready) applyFlat(); },
    setExplode(t) { if (isField) return; state.explode = Math.max(0, Math.min(1, Number(t) || 0)); applyExplode(); },
    setWireframe(on) { state.wireframe = !!on; applyHighlights(); applyVisibility(); },
    isolate(name) { if (isField) return; state.isolate = name || null; state.isolateNodes = null; applyVisibility(); if (name) frameObjects(objectsFor(name)); else frameAll(); },
    get isolated() { return state.isolate; },
    highlight(map) { if (isField) return; state.highlights = new Map(map || []); applyHighlights(); },
    frame(names) {
      const objects = (names || []).flatMap((n) => objectsFor(n));
      frameObjects(objects.length ? objects : null);
    },
    frameAll,
    setPins,
    /** Show only the objects of ``names`` (node names, or movers), framed;
     *  null shows every part again. A part or a group of a view's `meta` is
     *  a list of nodes, which `isolate` (one mover or one node) cannot take. */
    isolateNodes(names) {
      if (isField) return;
      const objects = names && names.length ? nodesFor(names) : [];
      state.isolateNodes = objects.length ? new Set(objects) : null;
      state.isolate = null;
      applyVisibility();
      if (objects.length) frameObjects(objects); else frameAll();
    },
    /** A presentation colour on the nodes ``names`` (a CSS colour), or null
     *  to give them their own colour back. It changes this page and nothing
     *  else; a highlight always paints over it. */
    tint(names, colour) {
      if (isField) return;
      for (const name of names || []) {
        if (colour) state.tints.set(String(name), String(colour));
        else state.tints.delete(String(name));
      }
      if (state.ready) applyHighlights();
    },
    clearTints() { state.tints = new Map(); if (state.ready) applyHighlights(); },
    /** Turn the model slowly (OrbitControls' own auto-rotate), or stop. */
    setSpin(on) {
      if (!controls) return;
      controls.autoRotate = !!on;
      controls.autoRotateSpeed = 1.1;
      invalidate();
    },
    get spinning() { return !!(controls && controls.autoRotate); },
    /** Stop drawing (a viewer out of sight, a hidden tab) or go on. */
    pause(on) {
      const was = state.paused;
      state.paused = !!on;
      if (was && !state.paused && state.ready && !state.disposed && !frameHandle) { invalidate(); loop(); }
    },
    /** The share of the width the model moves right (0 to 0.4). */
    setFrameShift(fraction) {
      shift = Math.max(0, Math.min(0.4, Number(fraction) || 0));
      applyShift();
      invalidate();
    },
    home: goHome,
    reset() {
      state.explode = 0; state.isolate = null; state.isolateNodes = null; state.wireframe = false;
      state.highlights = new Map(); state.tints = new Map();
      applyExplode(); applyHighlights(); applyVisibility(); frameAll();
    },
    resize,
    dispose() {
      state.disposed = true;
      cancelAnimationFrame(frameHandle);
      observer.disconnect();
      controls && controls.dispose();
      renderer && renderer.dispose();
      for (const clone of clones) clone.dispose && clone.dispose();
      clones.clear();
      for (const [, material] of baseMat) material && material.dispose && material.dispose();
      for (const [, mats] of fieldMats) { mats.lit.dispose(); mats.flat.dispose(); }
      renderer && renderer.domElement.remove();
    },
  };

  start();
  return api;
}
