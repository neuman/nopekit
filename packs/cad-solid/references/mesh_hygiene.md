# Mesh hygiene

Tier 3. Read this when a mesh gate has failed, when you are choosing export
settings, or when you are about to "just repair it".

---

## 1. What a solid actually requires

A triangle mesh is a valid solid when all of these hold:

1. **Closed.** Every edge is shared by exactly two triangles. Edges used once are
   holes; edges used three or more times are doubled or non-manifold surfaces.
2. **Consistently wound.** Adjacent triangles agree on which side is outside, so
   every interior directed edge appears exactly once.
3. **Positively oriented.** The signed volume is positive. A negative one means the
   whole surface is inside-out — usually a mirrored transform with a negative
   determinant applied without flipping the faces.
4. **Non-self-intersecting.** No triangle passes through another. This is the
   expensive one and the one `cad.is_volume` does not test.

Conditions 1–3 are cheap and are what `cad.watertight` and `cad.is_volume` cover
between them. They are separate gates because they fail separately: a mesh with one
facet flipped is perfectly watertight, and a watertightness check will wave it
through to a boolean that cannot use it.

## 2. Why `process=True` is a trap

Most mesh libraries run a repair pipeline on load by default — merge vertices, drop
degenerate faces, sometimes fix winding. It is convenient and it is the single
easiest way to make a validation pass meaningless:

- the gate measures the **repaired in-memory mesh**, not the file on disk;
- every other consumer of that file — the slicer, the vendor's quoting tool, the
  mesher — does its own repair, differently, and gets a different mesh;
- the defect is reported by nobody, and shows up as a mystery at the far end.

Every load in this pack is `process=False` for that reason. Validate the bytes you
are going to send. Repair, if you repair, is a separate deliberate step that writes
a new file with a new name.

## 3. The repair order that works

Duplicate vertices and zero-area triangles are the same defect seen from two sides,
and the order you address them in decides whether you find it:

```
repeat:
    weld vertices closer than 1e-4 mm
    drop triangles with area below 1e-8 mm^2
until neither step changed anything
```

**Weld first.** A sliver straddling a duplicated vertex pair has a small but
genuinely non-zero area — a few times 1e-6 mm² is typical from a tessellator working
on a rounded rim. An area test alone does not see it. After the weld its two
endpoints are one vertex and its area is exactly zero.

**Then loop.** Dropping a face can leave a vertex referenced only by its twin, which
the next weld pass then merges, which can zero another face. Once through is not
enough, and in practice it converges in two or three passes.

**Do not pick the weld tolerance by taste.** It must be far below the smallest real
feature (or you weld a genuine seam shut and report a clean part) and far above the
float noise of your export (or every float32 round trip looks defective). Between
1e-4 and 1e-3 mm is the working band for millimetre-scale mechanical parts; this
pack uses 1e-4 and says why in the constant's own comment.

## 4. Where slivers come from

Rounded rims, fillet runouts, and any surface where two curvatures blend. The
tessellator walks two curves that converge and emits triangles that get narrower
until they are needles. They are geometrically almost-degenerate but numerically
fine, which is why they survive every naive check and then destroy the first
algorithm that needs a face normal or an adjacency walk.

Nothing in this pack catches a sliver with non-zero area. If a mesher is failing on
a mesh that passes `cad.degenerate_faces`, that is where to look, and the tool for
it is an aspect-ratio or minimum-angle histogram over the face list.

## 5. Export settings are design decisions

Tessellation settings change every number this pack measures. Record them with the
same discipline as any other constant:

- **Chord height / deviation** — the maximum distance between the true surface and
  the chord that replaces it. This is the dominant term in clash noise on curved
  mating faces, and it is why a curved pair gets a larger tolerance than a flat one.
- **Angular deviation** — the maximum turn per facet. Governs how many facets a
  small fillet gets, and therefore how many slivers you receive.
- **Minimum facet size** — where it exists, this is the setting that suppresses
  slivers at the source. Prefer it over repairing them afterwards.
- **Units and scale.** State them. A mesh exported in metres passes a wall-thickness
  check at 0.001 against a limit of 1.2 and fails nothing else.

If the settings change, every prior verdict is stale. That is what STALE means in
the readiness report, and it is a real status, not a formality.

## 6. Measuring wall thickness honestly

Three methods, in increasing cost and confidence:

1. **Ray casting from facet centroids** (what `cad.wall_thickness` does). Cheap,
   deterministic, and a *lower-bound sampler*: it only ever measures thickness
   normal to a facet that exists. It misses a thin region whose thinnest direction
   is normal to nothing it sampled, and UNDER-reports on a coarse tessellation of
   a curve: the chord sits inside the true surface, so a ray leaving one chord and
   landing on the other crosses `t*cos(pi/N)` rather than `t` — a shortfall of
   `t*(1 - cos(pi/N))` for a wall of thickness `t` across an N-sided tessellation
   (1.9% of the wall at N=16, 7.6% at N=8). The error is conservative: it raises
   false alarms, it does not hide thin walls. Do not confuse it with facet-normal
   OBLIQUITY on an irregular tessellation, which is the one mechanism here that can
   over-report — a facet whose normal does not point at the opposing surface sends
   its ray on a longer path than the wall is thick. That effect is unsigned and
   mesh-specific; the chord effect is systematic and always one way.
2. **Sphere inscription / medial sampling.** Fit the largest sphere that fits
   locally; the thickness is twice its radius. Direction-independent, so it finds
   cases method 1 misses. Costs a spatial index and considerably more time. Its
   local reach includes sharp corners: trimesh's `proximity.thickness` with
   `method="max_sphere"` reads 0.244 mm on the subdivided 10 mm acute-edge prism
   used by the gate's test. It cannot replace this gate's wall measure while
   keeping that ordinary corner exempt. The gate instead uses trimesh's coplanar
   groups and edge adjacency to separate corners from nonadjacent tapered flanks.
3. **A medial-axis transform.** The real answer, and the expensive one. Reach for it
   when the thin region is load-bearing and the design has stopped moving.

Whichever you use, a pass means *nothing was found*, never *nothing is there*. Say
it that way in the report.

## 7. Booleans on a mesh that is not a solid

This is the failure that justifies gating watertightness before anything else.

Boolean kernels do not uniformly refuse invalid input. Depending on the engine and
the defect, an intersection against a leaking mesh can return an **empty result with
no exception raised**. Downstream that reads as "these parts share no material" —
a green verdict produced by a corrupt part. The same input class can also produce a
large **negative** signed volume, which any code comparing against a small positive
tolerance will classify as comfortably clear.

So: treat a raise, a negative volume, a non-finite volume, and a non-volume input as
a **clash**, not as a pass. The clean-looking answer is the one the bad input
produces, and a validator that cannot tell those apart is a logger.
