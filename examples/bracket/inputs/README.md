# inputs/ — the evidence this design is built on

Put real files here. Hand sketches, photos of the thing you'd buy instead, CAD
you already have, datasheet PDFs, caliper readings, log files. Intake is not only
a conversation: the numbers that matter usually arrive as a photo of a napkin.

Use `nopekit ingest <path>` rather than copying by hand — it files the artifact
in the right bucket, hashes it, and registers it in the ledger so a changed input
can mark a claim STALE. Then use `nopekit extract` to record what you actually
read out of it:

    nopekit ingest inputs/sketches/hull.png --desc "midship section, dimensioned"
    nopekit extract hull-png --what "hull beam at midship reads 148 mm" \
                              --grounds beam_mm

**An artifact nobody extracted from is decoration.** A file sitting in this
directory grounds nothing, proves nothing, and will not stop anyone from
re-litigating a number two months from now. `nopekit inputs --unextracted`
lists the decorations.

## Buckets

- `sketches/`      hand drawings, napkin diagrams, whiteboard photos
- `references/`    photos of existing products, teardowns, prior art
- `cad/`           STL, STEP, 3MF, f3d, KiCad, gerbers
- `screenshots/`   a UI, a config screen, a vendor page, another tool's output
- `datasheets/`    component and material datasheets (usually PDF)
- `specs/`         written specs, briefs, RFQs, requirements — and published
                   standards (IPC, ASTM, NEC); a standard is a requirements doc
- `measurements/`  calipers, scales, meters: numbers off the real world
- `data/`          CSV, logs, test results, and anything that fits nowhere else

The directory an artifact sits in is a hint the pipeline reads, so a photo in
`references/` is treated as prior art and the same photo in `sketches/` is
treated as your own drawing. File accordingly.

Empty buckets do not survive a `git clone` (git does not track empty
directories). `nopekit ingest` recreates whichever one it needs.
