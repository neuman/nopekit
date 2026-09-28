# SPDX-License-Identifier: Apache-2.0
"""atompipe.artifacts — intake of real evidence.

**Intake is not only a conversation.** This module exists because a real
project's inputs are not sentences. They are hand drawings of a layout,
photographs of a competitor's insides taken after prying the thing open, a
digitised layout whose header records exactly which drawing it came from, and a
pile of datasheet numbers.

Not one of those is volunteered. A user describing a product says "about the size
of a game controller"; the sketch says 148 mm, the teardown photo says where the
battery actually went, and the datasheet says the part is EOL. The
pipeline has to **ask, by name, for each kind** — which is what `ASK_FOR` and
`suggest_requests` are for. Everything else here is bookkeeping in service of
that: hash it, file it, and record what was read out of it.

The other half of the job is the audit trail. `Extraction` is what turns a photo
into evidence: an artifact nobody extracted from is decoration, and
`unextracted()` is the list the readiness report nags about. That one-sentence
header on a generated layout is the only reason anyone can audit it two months
later, and this module's whole purpose is to make that sentence structural
instead of lucky.

Nothing here stamps its own clock: `when` is passed in (spine rule 3).
"""
from __future__ import annotations

import os
import shutil
from urllib.parse import urlsplit

from . import store
from .models import (
    EXT_KIND_HINTS,
    ArtifactKind,
    Extraction,
    InputArtifact,
    Ledger,
    sha256_file,
    slugify,
)
from .util import (
    AtompipeError,
    ensure_dir,
    iter_suffix_unique,
    rel,
    sha256_text,
    short_hash,
)


# --------------------------------------------------------------------------- #
# what to ask a human for
# --------------------------------------------------------------------------- #
#: artifact kind -> concrete prompts, phrased as things to SAY to a person.
#:
#: These are not labels, they are interview questions, and the register is
#: deliberate: a good engineer taking a brief is specific and a little pointed,
#: because "do you have any reference material?" gets "no" from someone whose
#: phone has forty photos of the thing they are trying to replace. "A photo of
#: the closest product you'd buy instead, even if you hate it" gets the photo.
#:
#: Keys are `ArtifactKind` VALUES (plain strings) so the table round-trips
#: through JSON and through a CLI `--kind sketch` without conversion. Indexing
#: with the enum member also works (the enum mixes in `str` ahead of `Enum` in
#: its MRO, so it hashes as its value) — but go through `prompts_for()` rather
#: than betting on that.
#:
#: Rules for adding to this table: domain-neutral (nothing here may assume
#: boats, boards or brackets), answerable in one action by a non-expert, and
#: each one asks for a FILE rather than an opinion.
ASK_FOR: dict[str, list[str]] = {
    ArtifactKind.SKETCH.value: [
        "Any drawing at all — napkin, whiteboard photo, a rectangle with arrows. "
        "A bad sketch pins down intent that a paragraph cannot.",
        "Mark two or three dimensions you already believe on that drawing, even "
        "roughly, and circle the one you trust least. That circle tells me what to "
        "gate first.",
        "If there is a second view — from underneath, in section, exploded, or the "
        "thing in someone's hand — send that too. The mistakes hide in the view "
        "nobody drew.",
        "Sketch the failure as well as the object: what does it look like when this "
        "goes wrong? That drawing usually names the real constraint.",
    ],
    ArtifactKind.REFERENCE.value: [
        "A photo of the closest existing product you'd buy instead, even if you hate "
        "it. What does it get right, and what is the thing it does that you refuse to "
        "copy?",
        "If you've taken anything apart that solves part of this, photograph the "
        "insides — the board, the fasteners, how the two halves actually join.",
        "The nearest version of this that already exists in your shop, office or "
        "house: photograph it in place, with a coin or a ruler in frame for scale.",
        "Links to the two builds you keep going back to, and one sentence each on "
        "what you are taking from them.",
    ],
    ArtifactKind.CAD.value: [
        "Any CAD you already have, even if it is wrong or from another project — "
        "STEP, STL, 3MF, f3d, DXF, a KiCad board. A file I can measure beats a "
        "number you remember.",
        "The mating part you do NOT control: the enclosure it bolts into, the board "
        "it sits on, the bracket someone else owns. Vendor STEP files are usually "
        "one download away.",
        "If an earlier revision exists, send it and say what was wrong with it. A "
        "geometry that failed is provenance, and it stops me re-proposing it.",
        "If you send more than one file, say which is authoritative. Two sources of "
        "truth is zero sources of truth.",
    ],
    ArtifactKind.SCREENSHOT.value: [
        "A screenshot of wherever the real numbers live right now — the CAD tree, "
        "the spreadsheet, the calculator tab you keep reopening.",
        "The vendor page or the cart for anything you've already picked, so price, "
        "stock and the exact variant are on the record and not in your memory.",
        "If another tool is complaining — DRC errors, a solver that won't converge, "
        "a slicer warning — screenshot the error text itself, not a summary of it.",
    ],
    ArtifactKind.DATASHEET.value: [
        "For every part you've already chosen, the datasheet or the vendor part "
        "number. A part you can't order is a part you don't have.",
        "The mechanical drawing page and the absolute-maximum table specifically — "
        "that is the half of a datasheet a design physically collides with.",
        "For materials: the supplier's sheet for the exact grade, filament or alloy "
        "you will actually buy, not the generic family entry.",
        "Anything already sitting on your shelf — link the order. What you own "
        "outranks what is in the model.",
    ],
    ArtifactKind.SPEC.value: [
        "Whatever written brief exists, unedited — an email to yourself, an RFQ, "
        "three bullets in your notes. The wording you used before you talked to me "
        "is evidence.",
        "The requirements somebody ELSE imposed: a customer's spec, a client's "
        "must-haves, the rules of the competition you are entering.",
        "Anything that says what this must NOT do — safety limits, a scope you "
        "agreed to stay inside, a review that already rejected an approach.",
    ],
    ArtifactKind.MEASUREMENT.value: [
        "Calipers on anything this has to fit, mate with, or sit inside — and the "
        "tolerance you actually care about.",
        "Measure the space it has to live in, including the clearance to get a hand "
        "or a tool in there. The hole is rarely the tight part; the approach is.",
        "Put it on a scale if mass matters. One real weight settles an argument that "
        "a CAD density estimate starts.",
        "Send anything you've measured and don't trust, with the reason you don't "
        "trust it. That is a claim waiting for a gate, not a number to discard.",
    ],
    ArtifactKind.STANDARD.value: [
        "Any standard, code or regulation this has to satisfy — the number alone is "
        "enough. If you are not sure one applies, say so and I'll record it as a "
        "standing assumption instead of silently ignoring it.",
        "House rules count as standards: your shop's minimum wall, your fab's DRC "
        "file, the tolerance class your machinist insists on.",
        "If there is an inspection, certification or review this has to survive "
        "later, name it now. Those almost always cost geometry.",
    ],
    ArtifactKind.DATA.value: [
        "Any log, CSV or bench output you already have — from a prototype, from the "
        "thing this replaces, from the time it broke.",
        "The duty cycle in numbers: how often, how long, how hot, how loaded. A week "
        "of real usage beats a worst case somebody imagined.",
        "If a previous version failed, the data from the failure plus what you think "
        "it means. I'll record your interpretation separately from the numbers.",
    ],
    ArtifactKind.LINK.value: [
        "A link to the build log, thread or video you keep going back to — with the "
        "one thing in it you want copied.",
        "The product page for the thing you would buy instead, if you can't "
        "photograph one.",
    ],
}

#: The order `suggest_requests` asks in, and the reasoning behind it:
#: sketches and references SHAPE the design (they decide what is being built at
#: all), measurements and datasheets CONSTRAIN it (they decide whether it can be
#: built that way), and standards/data/links REFINE it. Asking for a standards
#: list before anyone has drawn the thing is how intake stalls.
ASK_PRIORITY: tuple[str, ...] = (
    ArtifactKind.SKETCH.value,
    ArtifactKind.REFERENCE.value,
    ArtifactKind.MEASUREMENT.value,
    ArtifactKind.DATASHEET.value,
    ArtifactKind.SPEC.value,
    ArtifactKind.CAD.value,
    ArtifactKind.SCREENSHOT.value,
    ArtifactKind.STANDARD.value,
    ArtifactKind.DATA.value,
    ArtifactKind.LINK.value,
)

#: How many of a kind before the system stops asking. Not a quota — a threshold
#: for "thin". Two references, because one reference is a thing you like and two
#: is a comparison; two measurements, because a single caliper reading has no
#: witness. One of anything else is enough to start.
ENOUGH: dict[str, int] = {
    ArtifactKind.SKETCH.value: 2,
    ArtifactKind.REFERENCE.value: 2,
    ArtifactKind.MEASUREMENT.value: 2,
    ArtifactKind.DATASHEET.value: 2,
    ArtifactKind.SPEC.value: 1,
    ArtifactKind.CAD.value: 1,
    ArtifactKind.SCREENSHOT.value: 1,
    ArtifactKind.STANDARD.value: 1,
    ArtifactKind.DATA.value: 1,
    ArtifactKind.LINK.value: 1,
}

#: Directory names that override the extension guess. `inputs/references/x.jpg`
#: is a teardown photo, NOT a sketch, even though `.jpg` hints SKETCH — the
#: person who filed it under `references/` told us more than the file extension
#: ever could. The canonical bucket names come from `store.INPUT_BUCKETS`;
#: the singulars and synonyms are here because users type what they think of.
DIR_KIND_HINTS: dict[str, ArtifactKind] = {
    "sketches": ArtifactKind.SKETCH, "sketch": ArtifactKind.SKETCH,
    "drawings": ArtifactKind.SKETCH, "napkin": ArtifactKind.SKETCH,
    "references": ArtifactKind.REFERENCE, "reference": ArtifactKind.REFERENCE,
    "refs": ArtifactKind.REFERENCE, "teardown": ArtifactKind.REFERENCE,
    "teardowns": ArtifactKind.REFERENCE, "prior-art": ArtifactKind.REFERENCE,
    "competitors": ArtifactKind.REFERENCE,
    "cad": ArtifactKind.CAD, "geometry": ArtifactKind.CAD,
    "gerbers": ArtifactKind.CAD, "step": ArtifactKind.CAD,
    "screenshots": ArtifactKind.SCREENSHOT, "screenshot": ArtifactKind.SCREENSHOT,
    "screens": ArtifactKind.SCREENSHOT, "captures": ArtifactKind.SCREENSHOT,
    "datasheets": ArtifactKind.DATASHEET, "datasheet": ArtifactKind.DATASHEET,
    "specs": ArtifactKind.SPEC, "spec": ArtifactKind.SPEC,
    "requirements": ArtifactKind.SPEC, "briefs": ArtifactKind.SPEC,
    "standards": ArtifactKind.STANDARD, "standard": ArtifactKind.STANDARD,
    "codes": ArtifactKind.STANDARD,
    "measurements": ArtifactKind.MEASUREMENT, "measurement": ArtifactKind.MEASUREMENT,
    "measured": ArtifactKind.MEASUREMENT, "calipers": ArtifactKind.MEASUREMENT,
    "data": ArtifactKind.DATA, "logs": ArtifactKind.DATA,
    "results": ArtifactKind.DATA, "tests": ArtifactKind.DATA,
}

#: How far up the path a directory hint is honoured. `inputs/references/teardown/
#: front.jpg` must still read REFERENCE (two levels), but walking all the way up
#: would let `/home/eric/data/projects/hull/sketch.png` come back DATA because of
#: a folder that has nothing to do with this project. Three is the compromise.
_DIR_HINT_DEPTH = 3

#: Tokens dropped from a `project_kind` hint before matching. Deliberately tiny
#: and domain-free: this list may never learn what a boat is.
_HINT_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "into", "its", "our",
    "project", "design", "designing", "build", "building", "make", "making",
    "new", "system", "thing", "device", "product", "kind", "some", "small",
})


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
def prompts_for(kind: ArtifactKind | str) -> list[str]:
    """The `ASK_FOR` prompts for one kind; empty list for a kind with none.

    Exists so no caller has to know whether it holds an enum member or the
    string that came off `--kind` on the command line. Returns a copy: the CLI
    sorts and slices these, and a caller that mutates the shared table would
    silently change what every later project gets asked.
    """
    return list(ASK_FOR.get(str(kind), ()))


def kind_for(path: str | os.PathLike[str]) -> ArtifactKind:
    """Best guess at what a file IS, from its directory first and extension second.

    **Directory wins.** `inputs/references/competitor_board_front.png` is a board
    teardown photograph; `EXT_KIND_HINTS` says `.png` -> SKETCH, and that would
    file a competitor's board next to the hand drawings and quietly lose the
    distinction that makes either useful. Somebody who dropped a file into
    `references/` has already classified it by hand, and a hint derived from
    three letters after a dot does not get to overrule them.

    A guess is all this is. `ingest(..., kind=...)` overrides it, and the two
    kinds that share a bucket (`STANDARD` files live in `specs/` because a
    published code IS a requirements document) can only ever come back as SPEC
    from the directory alone — pass `kind` explicitly for those.

    Unknown extension, unknown directory: OTHER. Never a guess dressed up as
    knowledge.
    """
    text = os.fspath(path).replace("\\", "/")
    parts = [p for p in os.path.normpath(text).split("/") if p not in ("", ".")]
    name = parts[-1] if parts else ""

    # Nearest directory first, bounded: see _DIR_HINT_DEPTH.
    for component in reversed(parts[:-1][-_DIR_HINT_DEPTH:]):
        hint = DIR_KIND_HINTS.get(component.strip().lower())
        if hint is not None:
            return hint

    ext = os.path.splitext(name)[1].lower()
    return EXT_KIND_HINTS.get(ext, ArtifactKind.OTHER)


def bucket_for(kind: ArtifactKind | str) -> str:
    """The `inputs/` subdirectory an artifact of this kind belongs in.

    Thin wrapper over `store.BUCKET_FOR_KIND` so the layout stays store.py's
    business alone. An unrecognised kind lands in `data/` rather than raising:
    misfiling evidence is recoverable, refusing to ingest it is how a photo ends
    up staying on someone's phone.
    """
    k = _as_kind(kind)
    return store.BUCKET_FOR_KIND.get(k, "data") or "data"


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #
def find_by_hash(ledger: Ledger, sha: str) -> InputArtifact | None:
    """The already-ingested artifact with this digest, or None.

    This function exists because of a deliberate choice about `ingest`. Ingesting
    a file that is already in the ledger returns the EXISTING record instead of
    making a second one — content-addressed, so re-running an intake script is
    idempotent and a photo sent twice does not become two pieces of evidence
    that can disagree. But then `ingest` cannot tell the caller "this was already
    here" through its return value without either a second return value or a
    `duplicate` flag bolted onto `InputArtifact` — and `InputArtifact` is the
    contract in `models.py`, which does not grow a field to carry a transient
    fact about one function call.

    So the caller asks FIRST, with this, when it cares: the CLI prints
    "already ingested as <id>" instead of "added <id>", and nothing in the
    persisted model has to know that the question was ever asked.
    """
    if not sha:
        return None
    return next((a for a in ledger.inputs if a.sha256 == sha), None)


def ingest(
    root: str,
    ledger: Ledger,
    src: str | os.PathLike[str],
    *,
    kind: ArtifactKind | str | None = None,
    description: str = "",
    when: str = "",
    copy: bool = True,
    licence: str = "",
    note: str = "",
) -> InputArtifact:
    """Take a real file into the project: copy, hash, register. Mutates `ledger`.

    The copy is the point. A record that says `/home/eric/Downloads/pump.pdf` is
    a record that stops resolving the first time someone clears their Downloads
    folder or opens the project on another machine, and the evidence for a
    decision has to outlive the browser session that produced it. So by default
    the bytes move into `inputs/<bucket>/` and the ledger stores a repo-relative
    path.

    Three behaviours worth knowing:

    * **Already inside the project** -> recorded in place, never copied, whatever
      `copy` says. The copy exists only to make a path durable, and a path that
      is already repo-relative already is; copying `docs/brief.md` to
      `inputs/specs/brief.md` would manufacture the exact second source of truth
      this whole method exists to eliminate. For a file the user filed under
      `inputs/` themselves it matters twice over: duplicating it is how
      `inputs/sketches/hull.png` and `inputs/sketches/hull-2.png` come to sit
      side by side with nobody able to say which one the model was drawn from,
      and the directory they chose has already spoken (see `kind_for`) — we do
      not move it to "correct" the bucket. The one exception is `.atompipe/`:
      that tree is gate scratch and rebuildable output, so evidence found there
      is copied out before something deletes it.
    * **Same sha256 already ingested** -> the existing record comes back
      untouched, nothing is copied, nothing is appended. See `find_by_hash` for
      why that is not signalled in the return value.
    * **Same path, new bytes** (an in-place file the user edited) -> the existing
      record's digest and size are refreshed and its id is KEPT. Minting a fresh
      id would orphan every `grounded_by` and every `Extraction` pointing at the
      old one, which is the same as deleting the provenance — and the digest
      change is exactly what `inputs_hash` needs to see so the gates go stale.

    `when` is the caller's ISO timestamp (spine rule 3: no function stamps its
    own clock). `copy=False` records an outside file where it lies, absolute
    path and all, and is for the case where the file is genuinely too large or
    genuinely belongs to someone else.

    Raises `AtompipeError` for everything the user can fix: missing file, a
    directory, an empty file, an unreadable file, a bad `--kind`.
    """
    src_path = os.path.abspath(os.fspath(src))

    if not os.path.exists(src_path):
        raise AtompipeError(f"cannot ingest {src_path}: no such file")
    if os.path.isdir(src_path):
        raise AtompipeError(
            f"cannot ingest {src_path}: it is a directory. Ingest one file at a time — "
            "a folder of sketches is N artifacts with N descriptions, and a record that "
            "points at a directory cannot be hashed or gone stale."
        )
    if not os.path.isfile(src_path):
        raise AtompipeError(f"cannot ingest {src_path}: not a regular file")

    explicit_kind = kind is not None
    resolved = _as_kind(kind) if explicit_kind else kind_for(src_path)
    if resolved is ArtifactKind.LINK:
        raise AtompipeError(
            "kind 'link' has no file to ingest — use ingest_link(root, ledger, url) instead"
        )

    try:
        size = os.path.getsize(src_path)
        sha = sha256_file(src_path)
    except PermissionError as exc:
        raise AtompipeError(f"cannot read {src_path}: permission denied") from exc
    except OSError as exc:
        raise AtompipeError(f"cannot read {src_path}: {exc.strerror or exc}") from exc

    if size == 0:
        # A 0-byte file is a failed export, an interrupted AirDrop, or a `touch`
        # somebody forgot about. Registering it would put a hash in the ledger
        # that says "evidence" and a file that says nothing.
        raise AtompipeError(
            f"cannot ingest {src_path}: the file is empty (0 bytes) — "
            "the export or transfer probably failed"
        )

    existing = find_by_hash(ledger, sha)
    if existing is not None:
        return existing

    # `.atompipe/` is excluded deliberately: it is declared rebuildable and
    # git-ignored by `store.init`, so a record pointing into it is a record
    # pointing at something a gate sweep is allowed to delete.
    #
    # The project ROOT is excluded too, and for a different reason. Recording a
    # file in place respects a filing decision the user already made — `docs/brief.md`
    # stays where they put it, and copying it to `inputs/specs/` would manufacture the
    # second source of truth this whole method exists to eliminate. But a loose file
    # sitting at the repo root is not filed; it is where things land by accident, from
    # a drag-and-drop or a browser download. Treating that as a deliberate choice
    # leaves evidence scattered across the top of the project and the `inputs/` buckets
    # permanently empty. So: anything in a real subdirectory is left alone, and
    # anything loose at the root gets filed.
    abs_root = os.path.abspath(root)
    in_project = (
        _within(src_path, abs_root)
        and not _within(src_path, os.path.abspath(store.atompipe_dir(root)))
        and os.path.dirname(src_path) != abs_root
    )

    if in_project or not copy:
        dest = src_path
    else:
        bucket_dir = ensure_dir(
            os.path.join(os.path.abspath(store.inputs_dir(root)), bucket_for(resolved))
        )
        dest, needs_copy = _dest_for(bucket_dir, os.path.basename(src_path), sha)
        if needs_copy:
            try:
                shutil.copy2(src_path, dest)
            except OSError as exc:
                raise AtompipeError(
                    f"cannot copy {src_path} to {dest}: {exc.strerror or exc}"
                ) from exc

    recorded = rel(dest, root)

    prior = next((a for a in ledger.inputs if a.path and a.path == recorded), None)
    if prior is not None:
        # Same file on disk, different bytes: the user edited their evidence.
        # Refresh in place and keep the id — see the docstring. Only fields the
        # caller actually supplied are touched: re-ingesting an edited file with
        # no `--kind` must not silently downgrade a STANDARD that someone
        # classified by hand back to the SPEC that its `specs/` directory
        # guesses, and blanking a description because this call did not repeat
        # it is the same kind of quiet loss.
        prior.sha256 = sha
        prior.bytes = size
        if explicit_kind:
            prior.kind = resolved
        if description:
            prior.description = description
        if note:
            prior.note = note
        if when:
            prior.added = when
        return prior

    stem = os.path.splitext(os.path.basename(dest))[0]
    artifact = InputArtifact(
        id=iter_suffix_unique(slugify(stem), {a.id for a in ledger.inputs}),
        path=recorded,
        kind=resolved,
        description=description,
        sha256=sha,
        bytes=size,
        added=when,
        licence=licence,
        note=note,
    )
    ledger.inputs.append(artifact)
    return artifact


def ingest_link(
    root: str,
    ledger: Ledger,
    url: str,
    *,
    description: str = "",
    kind: ArtifactKind | str = ArtifactKind.LINK,
    when: str = "",
) -> InputArtifact:
    """Register a URL as evidence. No file, no fetch. Mutates `ledger`.

    The spine never goes to the network — it is stdlib-only, it runs offline, and
    a gate sweep that silently depends on a vendor's CDN being up is not a gate
    sweep. So a link is recorded as a pointer and nothing more: a product page, a
    build thread, a datasheet someone has not downloaded yet. If the content
    matters, ask for the file (`ASK_FOR["datasheet"]` does exactly that).

    `sha256` is set to the digest of the URL STRING, not of anything fetched.
    Two consequences, both wanted: re-adding the same link is idempotent through
    the same `find_by_hash` path that deduplicates files, and re-pointing a link
    changes `inputs_hash`, which is what makes downstream results go stale. The
    field is still honestly named — it is the digest of the artifact's content,
    and for a LINK the URL *is* the content we hold.

    `root` is unused today and is in the signature because every other intake
    entry point takes it; a link that one day caches a PDF will need it, and
    changing the arity of a public function later is worse than an unused
    parameter now.
    """
    del root  # see docstring
    text = (url or "").strip()
    if not text:
        raise AtompipeError("cannot ingest an empty link")
    if "://" not in text:
        raise AtompipeError(
            f"cannot ingest link {text!r}: no scheme. Write it as https://... — "
            "a bare host is not dereferenceable two months from now."
        )

    sha = sha256_text(text)
    existing = find_by_hash(ledger, sha)
    if existing is not None:
        return existing

    split = urlsplit(text)
    tail = [seg for seg in split.path.split("/") if seg]
    stem = tail[-1] if tail else (split.netloc or "link")

    artifact = InputArtifact(
        id=iter_suffix_unique(slugify(stem), {a.id for a in ledger.inputs}),
        path="",
        url=text,
        kind=_as_kind(kind),
        description=description,
        sha256=sha,
        bytes=0,
        added=when,
    )
    ledger.inputs.append(artifact)
    return artifact


# --------------------------------------------------------------------------- #
# extraction:  what was actually read out of the evidence
# --------------------------------------------------------------------------- #
def add_extraction(ledger: Ledger, artifact_id: str, extraction: Extraction) -> InputArtifact:
    """Record what was read out of an artifact. Returns the artifact. Mutates `ledger`.

    This is the step that converts a photograph into provenance. A generated
    layout that carries the sentence *"digitised from inputs/sketches/panel.png,
    legends corrected against inputs/references/unit-front.png"* — one line,
    written once — is the only reason it can be audited two months later. Every `Extraction` here is that sentence, structured, so
    `grounding()` can answer "what is this number standing on?" without anybody
    having remembered to write prose.

    An extraction with no `what` is refused: an empty one adds a tick mark that
    makes `unextracted()` stop nagging without anybody having read anything,
    which is the intake version of a gate that cannot fail.
    """
    artifact = ledger.artifact(artifact_id)
    if artifact is None:
        known = ", ".join(a.id for a in ledger.inputs) or "none ingested yet"
        raise AtompipeError(f"no ingested artifact with id {artifact_id!r} (have: {known})")
    if not (extraction.what or "").strip():
        raise AtompipeError(
            f"extraction on {artifact_id!r} needs a `what` — "
            "an empty extraction silences the 'nobody read this' warning without reading it"
        )
    artifact.extractions.append(extraction)
    return artifact


def unextracted(ledger: Ledger) -> list[InputArtifact]:
    """Ingested artifacts nobody has read anything out of. Order preserved.

    Evidence nobody extracted from is decoration: it makes the project look
    grounded in the file listing while not one parameter traces back to it. The
    readiness report nags about this list on purpose — an unread teardown photo
    is a promise that was never kept, and it is better seen as a gap than
    counted as intake.
    """
    return [a for a in ledger.inputs if not a.extractions]


def grounding(ledger: Ledger, *, include_declared: bool = True) -> dict[str, list[str]]:
    """Invert the evidence graph: param name or claim id -> artifact ids.

    Extractions point outward (this photo grounds `hull_beam`); every consumer
    wants the other direction (what is `hull_beam` standing on?). Callers doing
    that inversion themselves is how two modules end up disagreeing about
    whether a `grounded_by` back-reference counts.

    It does count. `Param.grounded_by` and `Claim.grounded_by` in `models.py`
    hold artifact ids for the same relation recorded from the other side, and a
    report that only inverted extractions would tell a user their
    datasheet-grounded parameter is ungrounded because the edge happened to be
    written on the param instead of on the artifact. `include_declared=False`
    gets the strict inversion of extractions alone, for a caller auditing the
    extraction records themselves.

    Artifact ids are de-duplicated and keep first-seen order, so the output is
    stable enough to diff between runs.
    """
    out: dict[str, list[str]] = {}

    def link(target: str, artifact_id: str) -> None:
        if not target or not artifact_id:
            return
        bucket = out.setdefault(target, [])
        if artifact_id not in bucket:
            bucket.append(artifact_id)

    for artifact in ledger.inputs:
        for extraction in artifact.extractions:
            for target in extraction.grounds or ():
                link(target, artifact.id)

    if include_declared:
        for param in ledger.params:
            for aid in param.grounded_by or ():
                link(param.name, aid)
        for claim in ledger.claims:
            for aid in claim.grounded_by or ():
                link(claim.id, aid)

    return out


def inputs_hash(ledger: Ledger) -> str:
    """Stable digest over every ingested artifact's (id, sha256).

    A display id (`ingest --json`). It was half of the staleness test until 1.2 —
    recorded with each sweep, compared against the next — and ingesting one
    unread datasheet staled every measurable claim through it (S-33); a verdict
    is keyed by the files its own gate read now (`verdicts.freshness`). Sorted by
    id so the value does not depend on ingest order, and folding in the id as
    well as the digest so that re-filing the same bytes under a new artifact
    (which changes what the extractions hang off) still moves the hash.

    An empty project hashes to a real value rather than to `""` — "no inputs" and
    "never computed" are different facts and must not share a representation.
    """
    payload = "\n".join(
        f"{a.id}\t{a.sha256}" for a in sorted(ledger.inputs, key=lambda a: a.id)
    )
    return short_hash(payload)


# --------------------------------------------------------------------------- #
# the ask:  make intake solicit, instead of wait
# --------------------------------------------------------------------------- #
def requests_by_kind(
    ledger: Ledger, *, project_kind: str = "", limit: int = 6
) -> list[tuple[str, str]]:
    """`suggest_requests` with the kind attached: [(kind, prompt), ...].

    Same selection and ordering; the pair form is for callers that have to file
    the answer (the CLI prints the bucket the file should land in, an agent tags
    the artifact it ingests). `suggest_requests` is the plain-prose view of this.
    """
    if limit <= 0:
        return []

    counts: dict[str, int] = {}
    for artifact in ledger.inputs:
        key = str(artifact.kind)
        counts[key] = counts.get(key, 0) + 1

    hints = _hint_tokens(project_kind)

    # (0 = nothing at all of this kind, 1 = has some but thin, then rank).
    # The missing/thin split dominates: a project with no sketch is asked for a
    # sketch before anything else, whatever its project_kind says.
    ranked: list[tuple[int, int, int, str]] = []
    for position, kind in enumerate(ASK_PRIORITY):
        have = counts.get(kind, 0)
        if have >= ENOUGH.get(kind, 1):
            continue
        boost = _hint_boost(kind, hints)
        ranked.append((0 if have == 0 else 1, position - boost, position, kind))
    ranked.sort()

    ordered = [kind for _, _, _, kind in ranked]

    # Breadth before depth: one prompt from each needy kind, THEN second prompts.
    # Six questions about sketches is an interrogation; one question each about
    # six kinds is an interview, and the whole reason for the cap is that a human
    # answers five or six things and abandons twenty.
    out: list[tuple[str, str]] = []
    depth = max((len(ASK_FOR.get(k, ())) for k in ordered), default=0)
    for index in range(depth):
        for kind in ordered:
            prompts = ASK_FOR.get(kind, ())
            if index < len(prompts):
                out.append((kind, prompts[index]))
                if len(out) >= limit:
                    return out
    return out


def suggest_requests(ledger: Ledger, *, project_kind: str = "", limit: int = 6) -> list[str]:
    """What to ask this user for next, in the order worth asking. Prioritised, capped.

    This is the function that makes intake *solicit* evidence instead of waiting
    for someone to think of it. A user describing a design will describe it in
    sentences; the sketch on their desk, the competitor they bought, the caliper
    reading they took last week and the datasheet of the part they already
    ordered stay unmentioned unless something names them. Nothing names them by
    accident, so this names them on purpose.

    Ordering, in descending strength:

    1. **Kinds with nothing at all** come before thin ones. Zero sketches is a
       different problem from one sketch.
    2. **`ASK_PRIORITY`**: sketch and reference (they shape the design), then
       measurement and datasheet (they constrain it), then spec/CAD/screenshot,
       then standards and data (they refine it).
    3. **`project_kind`, softly.** It is matched by plain word overlap against
       each kind's own prompt text — no domain table, nothing here knows what a
       "PCB" or a "hull" is, and a match can move a kind at most three places.
       A hard-coded domain map would be a second source of truth about domains,
       which belongs in packs, not in the spine.

    Breadth first: one prompt per kind before any kind gets a second. `limit`
    defaults to 6 because that is roughly what a person answers in one sitting;
    ask for twenty things and you get zero.

    Returns prompt text only — no "sketch:" prefix — because these are meant to
    be said to a human, and taxonomy words are for the machine. `requests_by_kind`
    returns the same list with kinds attached.
    """
    return [prompt for _, prompt in requests_by_kind(ledger, project_kind=project_kind, limit=limit)]


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #
def _as_kind(kind: ArtifactKind | str) -> ArtifactKind:
    """Coerce a kind, turning a bad one into a user-facing error with the menu.

    A wrong `--kind` is a typo, not a bug: the CLI must print the eleven legal
    values rather than a ValueError traceback that names none of them.
    """
    if isinstance(kind, ArtifactKind):
        return kind
    try:
        return ArtifactKind(str(kind).strip().lower())
    except ValueError as exc:
        legal = ", ".join(k.value for k in ArtifactKind)
        raise AtompipeError(f"unknown artifact kind {kind!r}; expected one of: {legal}") from exc


def _within(path: str, directory: str) -> bool:
    """True if absolute `path` is inside absolute `directory`. Purely lexical.

    No filesystem access and no symlink resolution, matching `util.rel`: a user
    who symlinks `inputs/cad` at a NAS share still expects files under it to read
    as project-relative, and resolving would rewrite their paths into something
    they do not recognise.
    """
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:
        # Different drives on Windows: no common path exists, so it is not inside.
        return False


def _dest_for(directory: str, filename: str, sha: str) -> tuple[str, bool]:
    """Where to put an incoming copy: (destination path, whether to copy).

    Never overwrites. Evidence is not a build artifact — an older revision of a
    sketch or an STL is the record of what the design used to be, and clobbering
    it destroys the only copy of a decision's input. A name collision with
    DIFFERENT bytes becomes `hull-2.stl`; a collision with the SAME bytes
    (someone dragged the file into `inputs/` by hand before running ingest) is
    reused as-is, because copying a file onto itself byte-for-byte is churn.
    """
    stem, ext = os.path.splitext(filename or "artifact")
    stem = stem or "artifact"
    candidate = os.path.join(directory, f"{stem}{ext}")
    n = 2
    while os.path.exists(candidate):
        if os.path.isfile(candidate) and sha256_file(candidate) == sha:
            return candidate, False
        candidate = os.path.join(directory, f"{stem}-{n}{ext}")
        n += 1
    return candidate, True


def _hint_tokens(text: str) -> frozenset[str]:
    """Word stems from free text, minus noise. Lowercase, len >= 3, trailing -s cut.

    The plural rule is the whole reason this is not a one-line `split()`. Both
    sides of the match are written by humans in different moods: a user types
    "parts already ordered" and the datasheet prompt says "every part you've
    already chosen". Without cutting the -s those two never meet, the hint
    silently scores zero for every realistic phrase, and `project_kind` becomes
    a parameter that is accepted, documented and inert — which is the intake
    version of a gate that cannot fail. Reduce rather than expand, so one
    concept cannot be counted twice in a boost.

    "clas" from "class" is wrong and harmless: both sides stem identically, so
    a crude rule that is crude in the same way twice still matches.
    """
    word = ""
    words: list[str] = []
    for ch in (text or "").lower():
        if ch.isalnum():
            word += ch
        else:
            if word:
                words.append(word)
            word = ""
    if word:
        words.append(word)
    stems = {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words}
    return frozenset(w for w in stems if len(w) >= 3 and w not in _HINT_STOPWORDS)


#: word-set per ASK_FOR key; see _kind_vocabulary. Populated on first use so
#: importing this module stays cheap (spine rule 10: the inner loop is seconds).
_VOCAB_CACHE: dict[str, frozenset[str]] = {}


def _kind_vocabulary(kind: str) -> frozenset[str]:
    """Every word the kind's own name and prompts use. Built lazily, cached.

    The matching corpus is the kind's own name, its `DIR_KIND_HINTS` synonyms
    ("teardown", "calipers", "gerbers") and the prompt text itself. That is why
    `project_kind` can be a soft hint while this module contains no domain
    knowledge whatsoever: a project described as "pcb, parts already ordered"
    overlaps `datasheet`'s "part" and "order" because those prompts genuinely
    talk about parts and orders, not because anything here knows what a PCB is.
    Domain knowledge lives in packs; the spine only knows what evidence is.
    """
    cached = _VOCAB_CACHE.get(kind)
    if cached is None:
        synonyms = [name for name, k in DIR_KIND_HINTS.items() if str(k) == kind]
        blob = " ".join([kind, *synonyms, *ASK_FOR.get(kind, ())])
        cached = _hint_tokens(blob)
        _VOCAB_CACHE[kind] = cached
    return cached


def _hint_boost(kind: str, hints: frozenset[str]) -> int:
    """0..3 places a `project_kind` match may pull a kind forward. Soft on purpose.

    Capped at 3 so the hint can reorder neighbours but never jump the
    missing-versus-thin split, and never promote `data` over `sketch` in a fresh
    project. A hint is a nudge from a sentence somebody typed once; the fact that
    a project has no drawings is a measurement.
    """
    if not hints:
        return 0
    return min(3, len(hints & _kind_vocabulary(kind)))


__all__ = [
    "ASK_FOR", "ASK_PRIORITY", "ENOUGH", "DIR_KIND_HINTS",
    "prompts_for", "kind_for", "bucket_for",
    "find_by_hash", "ingest", "ingest_link",
    "add_extraction", "unextracted", "grounding", "inputs_hash",
    "requests_by_kind", "suggest_requests",
]
