# SPDX-License-Identifier: Apache-2.0
"""nopekit.receive — what Eddy's pre-receive hook says of a push (milestone 0).

A thing under Eddy is one git repository, and the push is the one place a
change can be seen before it lands. This module judges a push by what git
holds — refs, commits, trailers, trees, blobs, the objects new to the push —
and says, rule by rule (`RULES`, E01–E44), why a line may not grow the way it
was asked to, or notes (`NOTES`, N01–N04) what will read Stale or wait when it
does. `nopekit validate` prints the same report the hook does, so the agent
runs it before pushing; `server/pre-receive` runs it inside the hook with the
quarantine handed over (the carve-out in `vcs`'s docstring).

What it does not do, on purpose: it executes nothing from the tree (a pushed
tree deciding its own push is the forged-cache class with a shell); it starts
no git process itself (every question is a `vcs` call); it reads no clock and
no environment variable (the CLI edge and the hook script hand everything in);
it writes only under a `tempfile.mkdtemp(prefix="nopekit-validate-")` outside
the repository, removed in a `finally`; and it never says *checked* — its
vocabulary is well-formed, self-consistent, the tree's own. A verdict entry is
loaded for its shape, its digest and its rho only (invariant 12), which is why
the forged PASS at the honest rho is named in the limits rather than refused
here. It fails closed: any `None` from `vcs` is E07 and exit 2, and the hook
declines on any non-zero exit, its own crash included.

What slipped through in the design, each a rule or a walk below:

* a validator that walked ``old..new`` from the main object store saw no pushed
  object inside the hook (they sit in the quarantine) and would have refused
  everything — or, walking nothing, accepted everything; so every read takes
  the hook's ``objects`` explicitly;
* one that diffed each claiming commit against its PARENT let a working commit
  between two claiming commits smuggle any change — the diff is taken against
  the previous claiming commit's tree, found by walking the line below the
  push (`PREVIOUS_CLAIMING_LIMIT`), so a change split across commits lands in
  the next claiming commit's diff whatever commit carried it;
* one that used ``merge-base --is-ancestor`` to decide a fast-forward let a
  merge whose FIRST parent was a foreign chain, with the old tip under its
  second parent, rewrite a line's history as a fast-forward; a line is its
  first-parent chain, and ``old`` must be on it (E04);
* a scan that named blobs by ``rev-list``'s paths missed an old blob placed at
  ``.aws/credentials`` by a new tree (the blob is not new; the tree is), so the
  name scan reads every new tree's entries instead (E10).
"""
from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from typing import Any, Iterable, Mapping, NamedTuple, Sequence
from urllib.parse import urlsplit

from . import derivation, modelio, packs, rounds, site, store, vcs, verdicts
from .util import NopekitError, printable

__all__ = [
    "SCHEMA", "KINDS", "AGENTS", "ACTS", "TRAILER_KEYS", "ACT_ONLY_KEYS", "REQUIRED_KEYS",
    "MAX_VALUE_CHARS", "NOPE_LINE",
    "CAND_PREFIX", "DRAFT_PREFIX", "MAIN", "SPEC", "PUBLISHED", "LINE_NAME", "TRIGGER",
    "STATE_REL", "PROJECT_REL", "VERDICTS_PREFIX", "TRACE_PREFIX", "PACKS_REL", "SPEC_DIR",
    "SPEC_ENTRIES", "NOPEKIT_KEPT", "SEALED_DIRS", "TRACE_NAME", "EVIDENCE_ADDS",
    "ATTRIBUTES_REFUSED", "OUTPUT_PATTERNS",
    "MAX_BLOB_BYTES", "MAX_PUSH_BYTES", "MATERIALISE_MAX_FILES", "MATERIALISE_MAX_BYTES",
    "WALK_LIMIT", "PREVIOUS_CLAIMING_LIMIT", "BATCH", "MAX_JSON_DEPTH", "EMPTY_TREE",
    "Rule", "RULES", "NOTES", "Refusal", "Note", "Commit", "RefReport", "Report",
    "validate_push", "classify_ref", "is_zero", "message_problem", "trailer_problems",
    "tree_id", "spec_tree_id", "packs_problems", "nesting_problem", "tree_problems",
    "diff_problems", "materialise", "record_problems", "sealed_problems", "entry_problems",
    "state_file_problems", "secret_name", "object_problems", "format_line",
]

# --------------------------------------------------------------------------- #
# the trailer schema
# --------------------------------------------------------------------------- #
#: The ``Nope-Schema`` this nopekit reads. Schema-1 rules are frozen: a stricter
#: rule is ``Nope-Schema: 2``, because a new line re-validates its whole chain
#: and a rule that moved would refuse history that landed honestly.
SCHEMA = 1
#: The kinds a ``Nope-Kind`` may name. ``candidate`` and ``evidence`` are the
#: claiming commits a push carries; ``act`` is known so that an act arriving by
#: push is refused by name (E19), never read as an unknown kind.
KINDS = ("candidate", "evidence", "act")
#: Who a claiming commit says made it (``Nope-Agent``): the two roles the
#: architecture gives an agent. *Rejected:* a free string (the UI groups by it).
AGENTS = ("designer", "reviewer")
#: The acts milestone 1 records (``Nope-Act``). Read by no rule in milestone 0:
#: E19 refuses every act before its ``Nope-Act`` is read. Kept here because the
#: act path's writer (``update-ref`` inside the bare repository, with these
#: trailer rules under ``from_push=False``) is the one other reader of the
#: schema, and the schema is one list.
ACTS = ("accept-gates", "approve-print", "measure", "prefer", "publish")
#: The closed schema: every ``Nope-*`` key a commit may carry, spelled exactly.
#: A key not here is E13 (with a suggestion), one spelled in another case E14.
TRAILER_KEYS = ("Nope-Schema", "Nope-Kind", "Nope-Name", "Nope-Trigger", "Nope-Agent",
                "Nope-Model", "Nope-Act", "Nope-By", "Nope-Role", "Nope-Candidate",
                "Nope-Spec")
#: The keys only an act carries; on a candidate or an evidence commit each is
#: E21 (``belongs to an act; drop it``).
ACT_ONLY_KEYS = ("Nope-Act", "Nope-By", "Nope-Role", "Nope-Candidate", "Nope-Spec")
#: What each claiming kind must carry (E20): a candidate is named, an evidence
#: commit inherits its candidate's name (E23) and need not repeat it.
REQUIRED_KEYS = {"candidate": ("Nope-Schema", "Nope-Kind", "Nope-Name", "Nope-Agent",
                               "Nope-Model"),
                 "evidence": ("Nope-Schema", "Nope-Kind", "Nope-Agent", "Nope-Model")}
#: The longest trailer value. Provenance: a model id or a name, never prose — the
#: longest model id seen in a session marker is under 60 characters, and a line
#: name is 64 at most. *Rejected:* no cap (a trailer value reaches the pusher's
#: terminal and the UI's commit page, and a megabyte there is a denial of
#: service on both); 64 (a pinned model id with a date and a variant is longer).
MAX_VALUE_CHARS = 200
#: The raw-message scan behind E16: a line that LOOKS like a Nope- trailer,
#: wherever it sits. Case-insensitive on ``Nope`` and loose on the rest, so a
#: misspelt or mis-cased key is seen here too and never read as prose.
NOPE_LINE = re.compile(rb"^[Nn][Oo][Pp][Ee]-[A-Za-z0-9-]*[ \t]*:", re.M)
#: ``Nope-Schema``'s digits (E17): ASCII only, where ``str.isdigit`` is not.
_ASCII_DIGITS = re.compile(r"^[0-9]+$")

# --------------------------------------------------------------------------- #
# the ref layout
# --------------------------------------------------------------------------- #
CAND_PREFIX = "refs/heads/cand/"
DRAFT_PREFIX = "refs/heads/draft/"
MAIN = "refs/heads/main"
SPEC = "refs/heads/spec"
PUBLISHED = "refs/heads/published"
#: A line's name: `claims._PLAIN_ID`'s alphabet at `store.MILESTONE_NAME`'s
#: length — a name is a ref component, a trailer value and a directory name.
LINE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
#: ``Nope-Trigger``: ``<gate id>/<rho16>-<out8>``, a verdict entry's address.
TRIGGER = re.compile(r"^[^/\s]+/[0-9a-f]{16}-[0-9a-f]{8}$")

# --------------------------------------------------------------------------- #
# the tree layout
# --------------------------------------------------------------------------- #
#: The state file: the round record, unchanged in format (the hook holds half
#: of it; the renderer, milestone 1, regenerates the whole body).
STATE_REL = rounds.ROUND_REL
#: Every ``.nopekit/`` path below is spelled from its home (`store.NOPEKIT_DIR`,
#: `store.PROJECT_NAME`, `store.VERDICTS_NAME`, `derivation.TRACE_DIR`,
#: `rounds.ROUND_NAME`), never as a literal: a review of milestone 0 found the
#: same five paths spelled here by hand, with nothing holding them to the
#: modules that write them. A pure test holds `EVIDENCE_ADDS` equal to the
#: two prefixes, so the scan's exempt subtrees and the diff's evidence set are
#: one list (`rounds.ROUND_NAMED_BY_NOPEKIT`).
_NOPEKIT_PREFIX = f"{store.NOPEKIT_DIR}/"
PROJECT_REL = f"{store.NOPEKIT_DIR}/{store.PROJECT_NAME}"
VERDICTS_PREFIX = f"{store.NOPEKIT_DIR}/{store.VERDICTS_NAME}/"
TRACE_PREFIX = f"{store.NOPEKIT_DIR}/{derivation.TRACE_DIR}/"
PACKS_REL = "spec/packs.json"
SPEC_DIR = "spec/"
#: The spec tree's entries beside ``packs.json``: the record directories that
#: are the contract. ``gates/`` and ``selftest/`` are NOT in it in milestone 0
#: (the limits say so): folding them in is a one-constant change.
SPEC_ENTRIES = ("claims", "requirements", "milestones")
#: The closed list under ``.nopekit/``. ``.nopekit/packs/`` is not in it on
#: purpose: a pack committed there would shadow a bundled pack by name, and the
#: spec pins packs by repository and commit instead.
NOPEKIT_KEPT = (store.PROJECT_NAME, ".gitignore", rounds.ROUND_NAME,
                f"{store.VERDICTS_NAME}/", f"{derivation.TRACE_DIR}/")
#: The record kinds E33 holds append-only: `store.SEALED_KINDS` (the kinds
#: `store.append_sealed` alone writes) and ``results``, which `store` keeps out
#: of that tuple because `write_record` refuses it on a branch of its own (a
#: results file may hold unsealed entries beside sealed ones, P2.5a-D6). One
#: spelling: a kind added to `store.SEALED_KINDS` is held here the same day.
SEALED_DIRS = ("results",) + tuple(store.SEALED_KINDS)
#: A trace file's name as `derivation` writes and reads it: ``<key16>.json``
#: (`derivation.read_trace`: ``f"{key[:16]}.json"``) and
#: ``<key16>.amber.<self16>.json`` (`derivation.read_amber`). Mirrored here so
#: E29 refuses a stray name before the reader is asked; the reader's own name
#: check (``mismatch``) still runs on every added file (E38).
TRACE_NAME = re.compile(r"^[0-9a-f]{16}(\.amber\.[0-9a-f]{16})?\.json$")
#: What an evidence commit may ADD: `rounds.ROUND_NAMED_BY_NOPEKIT`, the two
#: subtrees nopekit names every path in. The trace is there deliberately:
#: `round` starts it in the background and its files are tracked, write-once,
#: clock-free and carry no status; without this every evidence push after a
#: traced round would be refused. Declining it means dropping the trace from
#: that tuple's readers together — this set and the scan's exemption are one.
EVIDENCE_ADDS = tuple(rounds.ROUND_NAMED_BY_NOPEKIT)
#: Attribute tokens that make a checkout differ from the tree (E27), plus any
#: token starting ``filter=`` or ``working-tree-encoding=``. nopekit's own
#: ``* text=auto eol=lf`` block is none of them.
ATTRIBUTES_REFUSED = ("export-ignore", "export-subst", "ident", "eol=crlf")


def _ignore_lines(block: str) -> tuple[str, ...]:
    """Every pattern line of an ignore block: no comment, no marker, no blank."""
    return tuple(line.strip() for line in block.splitlines()
                 if line.strip() and not line.lstrip().startswith("#"))


#: ``(root, pattern)`` for every line of the three ignore blocks nopekit writes,
#: each pattern read relative to its root with gitignore's meaning (a slash
#: anchors; none matches a name at any depth; a trailing slash names a
#: directory).
_OUTPUT_RULES: tuple[tuple[str, str], ...] = (
    tuple((_NOPEKIT_PREFIX, line) for line in _ignore_lines(store._NOPEKIT_IGNORE_BLOCK))
    + tuple(("", line) for line in _ignore_lines(store._ROOT_IGNORE_BLOCK))
    + tuple(("site/", line) for line in _ignore_lines(site._BUILD_IGNORE)))
#: Derived at import from `store._NOPEKIT_IGNORE_BLOCK`, `store._ROOT_IGNORE_BLOCK`
#: and `site._BUILD_IGNORE`: every non-comment, non-marker line, rooted at the
#: directory its ignore file sits in. A path matching one is an output nopekit
#: ignores (E28): committed, it would be read as truth on every clone.
OUTPUT_PATTERNS: tuple[str, ...] = tuple(root + line for root, line in _OUTPUT_RULES)

# --------------------------------------------------------------------------- #
# bounds
# --------------------------------------------------------------------------- #
#: One number with the round's commit (`rounds.ROUND_MAX_BYTES`, 50 MiB, with
#: its provenance there), so a round the Stop hook commits is a push the hook
#: accepts. Added here: on a line nothing is ever deleted, so GitHub's warning
#: threshold is the right cap, never its 100 MB hard limit.
MAX_BLOB_BYTES = rounds.ROUND_MAX_BYTES
#: Ten files at the cap with room. *Rejected:* no total (ten 49 MiB blobs pass
#: the per-file cap); GitHub's 2 GiB pack limit (a clone already pays it before
#: anyone notices). It bounds one push's cost, not the repository's growth.
MAX_PUSH_BYTES = 512 * 1024 * 1024
#: The most files one tree may ask the hook to materialise (the records, the
#: verdict cache and the trace): the bracket asks for ~45, a large project for a
#: few thousand. *Rejected:* no cap (a tree of a million empty entries is a
#: million writes inside a hook).
MATERIALISE_MAX_FILES = 20_000
#: The most bytes one tree may ask the hook to materialise, in all: the same
#: number as the per-blob cap on purpose. What the hook reads of a tree is the
#: records and the cache — the bracket's come to ~200 KiB, a large project's to
#: a few MiB — so one file at the blob cap is already more than any honest
#: tree asks; a second number would be a second thing to explain and to drift.
#: *Rejected:* no total (20 000 files near the blob cap is a terabyte written
#: inside a hook); a larger total (every byte is written, hashed and parsed
#: under `vcs.VCS_TIMEOUT_S`-sized steps, and the records of a project that
#: needs more are not records nopekit's strict reader can hold).
MATERIALISE_MAX_BYTES = rounds.ROUND_MAX_BYTES
#: First-parent commits one push may ask to validate; a longer line starts
#: again from a candidate nearer its tip (E06). Why 10 000: the walk reads
#: every new commit's object and trailer block in `BATCH`-sized ``cat-file``
#: and ``log`` processes, five of each at this bound, and a line under Eddy
#: grows by a candidate or an evidence commit per push, so 10 000 is years of
#: a line's life, not a push's. *Rejected:* unbounded (a pushed history of a
#: million commits is minutes of git inside a hook that holds the push);
#: ``--max-count`` alone with no check that the chain was cut (it would
#: truncate silently and judge a line's tail as its whole — `vcs.first_parents`
#: answers ``limit + 1`` rows so the cut is seen).
WALK_LIMIT = 10_000
#: First-parent commits walked below the base for the previous claiming commit:
#: one ``rev-list`` and `BATCH`-sized ``log`` reads. By the tip rule's induction
#: it is the base itself on every line created under the hook (one row read);
#: the walk exists for the general case — a line that predates the hook, whose
#: tip may be a working commit — and is bounded with `WALK_LIMIT`'s number for
#: `WALK_LIMIT`'s reasons. *Rejected:* reading the base alone (a working tip
#: below the push would then be diffed against the empty tree and refuse every
#: file as an addition).
PREVIOUS_CLAIMING_LIMIT = 10_000
#: Object ids per ``cat-file`` process, so each stays under `vcs.VCS_TIMEOUT_S`:
#: measured 2026-11-09, 2 000 blobs of the bracket's size answer in ~0.1 s.
#: *Rejected:* one process for everything (a tree of 20 000 entries at the
#: blob cap is a gigabyte on one pipe, past the timeout); 100 (a large
#: project's cache is hundreds of processes per claiming commit, each a fork).
BATCH = 2_000
#: The deepest a materialised JSON file may nest before any reader opens it.
#: The deepest file the hook reads is an amber file at 8 levels; a record is 4
#: or 5. Why a bound of its own: ``json.loads`` recurses once per level and
#: raises ``RecursionError`` at the interpreter's limit, which is 1 000 minus
#: whatever stack is in use when the reader runs — so a file nested 950 deep
#: would be refused in one process and read in another, and the error is not
#: a ``ValueError``, so every reader crashed through it (review of milestone 0:
#: ``[`` × 100 000 in ``spec/packs.json``, a claim, an entry or the state file
#: read ``nopekit validate crashed (RecursionError …)`` in the hook and a
#: traceback from the CLI). *Rejected:* catching ``RecursionError`` at every
#: reader (eleven sites, the depth still the interpreter's); 1 000 (the limit
#: itself, nondeterministic as above); 16 (twice the deepest honest file is
#: too close for a trace that gains a level).
MAX_JSON_DEPTH = 32
#: hashlib over ``b"tree 0\\0"`` for each hash; a test recomputes both and
#: compares them with `vcs.empty_tree`.
EMPTY_TREE = {"sha1": "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
              "sha256": "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321"}

_MIB = 1024 * 1024


# --------------------------------------------------------------------------- #
# the rule table
# --------------------------------------------------------------------------- #
class Rule(NamedTuple):
    """One row of the contract's table: ``id`` (``E01``…``E44``, ``N01``…``N04``),
    what it ``applies`` to, what it ``refuses``, and its ``message`` — the
    line's template, alternatives separated by `` / `` and a continuation
    abbreviated with ``…``. `docs/EDDY_CONTRACT.md` carries the same rows; a
    test holds them equal."""

    id: str
    applies: str
    refuses: str
    message: str


_KEPT = ", ".join(NOPEKIT_KEPT)

RULES: tuple[Rule, ...] = (
    Rule("E01", "ref", "a ref outside the layout",
         "E01 {ref}: not a ref Eddy keeps; push to cand/<name> (a line), draft/<name> "
         "(scratch) or main"),
    Rule("E02", "ref", "a push to Eddy's refs",
         "E02 spec: written only by Eddy when the gates are accepted; push a candidate to "
         "cand/<name> and accept it there / E02 published: moved only by a publish act; "
         "push a candidate to cand/<name>"),
    Rule("E03", "ref", "deleting a line or main",
         "E03 cand/{name}: a line is never deleted; leave it and push a new line / "
         "E03 main: never deleted; it carries the README and the Pages workflow"),
    Rule("E04", "ref", "old not on new's first-parent chain",
         "E04 cand/{name}: {new12} is not a fast-forward of {old12} along the first parent; "
         "a line only grows — rebase onto cand/{name} or push a new cand/<name>"),
    Rule("E05", "ref", "a line name out of shape, or a case twin",
         "E05 cand/{name}: a line's name is [A-Za-z0-9][A-Za-z0-9._-]{0,63}; rename the ref "
         "/ E05 cand/{name}: differs only in case from cand/{other}; pick another name"),
    Rule("E06", "ref", "more than WALK_LIMIT commits to validate",
         "E06 cand/{name}: more than 10000 first-parent commits to validate; start the line "
         "from a candidate commit nearer its tip"),
    Rule("E07", "push", "git did not answer (exit 2)",
         "E07 push: git did not answer ({call}: {first stderr line}); nothing landed — push "
         "again, and if it repeats the server's keeper reads the hook's log"),
    Rule("E08", "object", "a blob over MAX_BLOB_BYTES",
         "E08 push: {path} ({oid12}) is {size} MiB, over the 50 MiB a pushed file may be; "
         "nothing pushed is ever removed, so keep it out of the push or shrink it"),
    Rule("E09", "push", "new blobs over MAX_PUSH_BYTES",
         "E09 push: {size} MiB of new files, over the 512 MiB one push may carry; push in "
         "smaller steps"),
    Rule("E10", "object", "a secret-looking name in any new tree",
         "E10 push: {path} looks like a secret ({pattern}); nothing pushed is ever removed, "
         "so rename it or keep it out of the push"),
    Rule("E11", "object", "an object git cannot find",
         "E11 push: object {oid12} is in neither the push nor the repository; push again"),
    Rule("E12", "commit", "a control byte, or a byte that is not UTF-8, in the message",
         "E12 {sha12}: the commit message holds a control byte (0x{nn}); rewrite the message / "
         "E12 {sha12}: the commit message is not UTF-8 (byte 0x{nn} at offset {n}); commit it "
         "as UTF-8"),
    Rule("E13", "commit", "an unknown Nope-* key",
         "E13 {sha12}: {key} is not a trailer the schema knows (did you mean {suggestion}?); "
         "the keys are Nope-Schema, Nope-Kind, Nope-Name, Nope-Trigger, Nope-Agent, "
         "Nope-Model"),
    Rule("E14", "commit", "a key spelled in another case",
         "E14 {sha12}: {key} is spelled {Key}; trailer keys are spelled exactly"),
    Rule("E15", "commit", "a key given twice",
         "E15 {sha12}: {key} is given twice; give each trailer once"),
    Rule("E16", "commit", "a Nope- line git does not read as a trailer",
         "E16 {sha12}: \"{line}\" sits outside the trailer block, where git reads it as "
         "prose; put every Nope- line in the last paragraph with nothing else in it"),
    Rule("E17", "commit", "Nope-Schema missing, not 1, or newer",
         "E17 {sha12}: Nope-Schema is missing; a commit that claims a kind carries "
         "Nope-Schema: 1 / E17 {sha12}: Nope-Schema: {n} is newer than this server reads "
         "(1); push with Nope-Schema: 1 / E17 {sha12}: Nope-Schema: \"{value}\" is not 1; a "
         "commit that claims a kind carries Nope-Schema: 1"),
    Rule("E18", "commit", "Nope-Kind missing beside Nope- keys, or not a kind",
         "E18 {sha12}: {key} without Nope-Kind; a commit that claims nothing carries no "
         "Nope- trailer, one that claims carries Nope-Kind: candidate or evidence / "
         "E18 {sha12}: Nope-Kind: \"{value}\" is not one of candidate, evidence, act"),
    Rule("E19", "commit", "an act in a push",
         "E19 {sha12}: Nope-Kind: act is never pushed; an act is recorded by Eddy on a "
         "signed-in person's click"),
    Rule("E20", "claiming", "a required key missing",
         "E20 {sha12} (candidate): {key} is missing; a candidate commit carries Nope-Schema, "
         "Nope-Kind, Nope-Name, Nope-Agent, Nope-Model / E20 {sha12} (evidence): {key} is "
         "missing; an evidence commit carries Nope-Schema, Nope-Kind, Nope-Agent, Nope-Model"),
    Rule("E21", "claiming", "a value out of shape, or an act key",
         "E21 {sha12} ({kind} {name}): Nope-Agent: \"{value}\" is not one of designer, "
         "reviewer / … Nope-Name: \"{value}\" is not [A-Za-z0-9][A-Za-z0-9._-]{0,63} / … "
         "Nope-Model is empty or over 200 characters / … Nope-Trigger: \"{value}\" is not "
         "<gate id>/<16 hex>-<8 hex> / … {key} belongs to an act; drop it"),
    Rule("E22", "claiming", "a trigger naming no entry in this tree",
         "E22 {sha12} ({kind} {name}): Nope-Trigger names {value}, which this tree does not "
         "hold under .nopekit/verdicts/; name an entry the tree holds"),
    Rule("E23", "claiming", "a name reused on the line; an evidence name not its candidate's",
         "E23 {sha12} (candidate {name}): Nope-Name {name} is already the name of {sha12b} on "
         "this line; a name is given once per line / E23 {sha12} (evidence): Nope-Name "
         "{name} is not its candidate's ({cname}); drop it or match it"),
    Rule("E24", "tip", "a tip that claims no kind, or is not a commit",
         "E24 cand/{name}: the tip {sha12} is a working commit; a push ends on a candidate "
         "or an evidence commit / E24 cand/{name}: the tip {sha12} is a {type}, not a commit; "
         "a push ends on a candidate or an evidence commit"),
    Rule("E25", "claiming tree", "a symlink or gitlink",
         "E25 {sha12} ({kind} {name}): {path} is a symbolic link; a tree Eddy reads holds "
         "files and directories only / … {path} is a gitlink (a nested repository); vendor "
         "its files or leave them out"),
    Rule("E26", "claiming tree", "a collision or a forbidden path part",
         "E26 {sha12} ({kind} {name}): {a} and {b} differ only in case; one file on a "
         "case-insensitive checkout — rename one / … differ only in Unicode normalisation; "
         "rename one / … {path} has a path part that is .git, ., .. or empty, or a control "
         "byte; rename it"),
    Rule("E27", "claiming tree", "an attribute that makes a checkout differ",
         "E27 {sha12} ({kind} {name}): {file} sets {attr} on {pattern}; a checkout must be "
         "the tree — export-ignore, export-subst, filter, ident, eol=crlf and "
         "working-tree-encoding are refused"),
    Rule("E28", "claiming tree",
         "an ignored output, or a path under .nopekit/ nopekit does not keep",
         "E28 {sha12} ({kind} {name}): {path} is an output nopekit ignores; committed, it "
         "would be read as truth on every clone — take it out of the commit / … {path} is "
         f"not a path nopekit keeps under .nopekit/ ({_KEPT}); pin a pack in spec/packs.json "
         "instead of committing it"),
    Rule("E29", "claiming tree", "a path under verdicts/ or trace/ that is not an entry name",
         "E29 {sha12} ({kind} {name}): {path} is not a verdict entry name "
         "(<gate>/<16 hex>-<8 hex>.json or <gate>/control-<16 hex>-<8 hex>.json); take it "
         "out / … is not a trace file name (<16 hex>.json or <16 hex>.amber.<16 hex>.json); "
         "take it out / … {gate}: {verdicts._check_gate_id's words}"),
    Rule("E30", "claiming tree", "more than the hook may read",
         "E30 {sha12} ({kind} {name}): {n} files under the records, .nopekit/verdicts/ and "
         ".nopekit/trace/, over the 20000 one tree may ask the hook to read; start a new "
         "line from a slimmer candidate / … {size} MiB …, over the 50 MiB …"),
    Rule("E31", "claiming tree", "not a nopekit project",
         "E31 {sha12} ({kind} {name}): not a nopekit project (.nopekit/project.json is "
         "missing); run nopekit init and commit what it writes"),
    Rule("E32", "claiming tree",
         "a record the strict reader refuses; a non-record under a spec directory",
         "E32 {sha12} ({kind} {name}): {store's message verbatim} / … {path} is not a "
         "record; claims/, requirements/ and milestones/ hold <id>.json files only — move "
         "it"),
    Rule("E33", "claiming diff", "a sealed record shortened, rewritten or deleted",
         "E33 {sha12} ({kind} {name}): {path} drops or rewrites entry {n}; a sealed record "
         "is appended, never shortened — restore it from the previous candidate and append "
         "/ … {path} is deleted; a sealed record is never removed"),
    Rule("E34", "candidate", "the spec out of shape",
         "E34 {sha12} (candidate {name}): spec/packs.json is missing; the spec pins its "
         "packs — commit {\"schema\": 1, \"packs\": {}} when there are none / … spec/{path} "
         "is not spec/packs.json; claims/, requirements/ and milestones/ live at the root "
         "and spec/ holds packs.json alone / … spec/packs.json: {reader's words, e.g. "
         "packs.mesh.repo must be an https:// URL with a host name}"),
    Rule("E35", "candidate", "pins and the live list disagree",
         "E35 {sha12} (candidate {name}): spec/packs.json pins {pack}, which "
         ".nopekit/project.json's packs does not list; add it with nopekit packs add or "
         "drop the pin / … .nopekit/project.json lists {pack}, which is neither bundled nor "
         "pinned in spec/packs.json; pin it"),
    Rule("E36", "evidence diff", "a path outside the evidence set",
         "E36 {sha12} (evidence): changes {path} ({status}); an evidence commit adds under "
         f".nopekit/verdicts/ and .nopekit/trace/ and rewrites {STATE_REL}, nothing else — "
         "put the change in a candidate commit"),
    Rule("E37", "claiming diff", "an entry or trace file modified, retyped or deleted",
         "E37 {sha12} ({kind} {name}): {path} is modified; a verdict entry is written once "
         "— file a new entry at its new rho / … {path} is deleted; evidence is added, never "
         "withdrawn"),
    Rule("E38", "claiming diff", "an added entry or trace file its reader refuses",
         "E38 {sha12} ({kind} {name}): {path}: {loader's words, e.g. hand-edited entry: its "
         "digest does not match its content}; file the entry nopekit check writes"),
    Rule("E39", "claiming diff", "an entry whose rho or static does not follow",
         "E39 {sha12} ({kind} {name}): {path}: rho does not follow from its own gate, spine, "
         "code and reads; file the entry nopekit check writes / … static does not follow "
         "from its own static_parts …"),
    Rule("E40", "claiming tree", "no state file",
         f"E40 {{sha12}} ({{kind}} {{name}}): {STATE_REL} is missing; run nopekit round and "
         "commit it"),
    Rule("E41", "claiming tree", "a state file edited, unreadable or newer",
         f"E41 {{sha12}} ({{kind}} {{name}}): {STATE_REL} reads edited (its self-digest does "
         "not match its content); run nopekit round and commit what it writes / … reads "
         "unreadable ({why}); … / … was written by a newer nopekit (schema {n}); this "
         "server reads 1"),
    Rule("E42", "claiming tree", "a state file computed from other records or verdicts",
         f"E42 {{sha12}} ({{kind}} {{name}}): {STATE_REL} was computed from other records "
         "(computed_from.records {a12}, this tree's records digest {b12}); run nopekit round "
         "and commit it / … from another verdict cache (computed_from.verdicts …)"),
    Rule("E43", "claiming tree", "a state file not in rendered form",
         f"E43 {{sha12}} ({{kind}} {{name}}): {STATE_REL} is not the bytes nopekit round "
         "renders (indent, key order or newline differ); run nopekit round and commit what "
         "it writes"),
    Rule("E44", "claiming diff", "a round counter not above the previous claiming commit's",
         f"E44 {{sha12}} ({{kind}} {{name}}): {STATE_REL} says round {{n}}, not above the "
         "previous candidate's {m}; run nopekit round and commit what it writes"),
)

NOTES: tuple[Rule, ...] = (
    Rule("N01", "candidate", "",
         "N01 {sha12} (candidate {name}): awaits acceptance; its gates (spec tree {id12}) "
         "are on no commit of spec"),
    Rule("N02", "claiming diff", "",
         "N02 {sha12} ({kind} {name}): {n} added entries were written under another spine "
         "({spine12}); they read Stale under this nopekit"),
    Rule("N03", "claiming diff", "",
         "N03 {sha12} ({kind} {name}): {path} read {what} at {a12}; this tree holds {b12}, "
         "so it arrives Stale"),
    Rule("N04", "claiming diff", "",
         "N04 {sha12} ({kind} {name}): .nopekit/verdicts/{gate}/ holds two outcomes at rho "
         "{rho12}; the resolver reads that as an error and the page shows the "
         "contradiction, not either outcome"),
)


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #
class Refusal(NamedTuple):
    """One refused rule: ``rule`` its id, ``where`` the ref, ``push`` or the
    commit (``<sha12>``, then ``<sha12> (<kind> <Nope-Name>)`` once known),
    ``message`` the words after ``<id> <where>: ``."""

    rule: str
    where: str
    message: str


class Note(NamedTuple):
    """One note (N01–N04): printed, never a refusal, the exit code unmoved."""

    rule: str
    where: str
    message: str


class Commit(NamedTuple):
    """A new commit of the push as the walk read it: ``kind`` ``candidate``,
    ``evidence``, ``act`` or ``""`` for a working commit; ``name`` its
    ``Nope-Name`` or ``""``; ``trailers`` the exact-spelled ``Nope-*`` pairs."""

    sha: str
    kind: str
    name: str
    trailers: tuple[tuple[str, str], ...]
    tree: str
    parents: tuple[str, ...]


class RefReport(NamedTuple):
    """One ref of the push: its refusals in evaluation order, its notes, its
    new commits oldest first, and the spec tree of the tip's nearest candidate
    with whether a commit of ``spec`` holds it (``None`` when none was read)."""

    ref: str
    old: str
    new: str
    refusals: tuple[Refusal, ...]
    notes: tuple[Note, ...]
    commits: tuple[Commit, ...]
    spec_tree: str
    accepted_spec: bool | None


class Report(NamedTuple):
    """The push: every ref in the order git gave them; ``unanswered`` is E07's
    line when git did not answer (the walk stopped there: exit 2); ``pusher``
    is what the hook was told (``NOPE_PUSHER``), recorded and judged by no rule
    in milestone 0. Accepted when ``unanswered`` is empty and no ref holds a
    refusal."""

    refs: tuple[RefReport, ...]
    unanswered: str
    pusher: str

    @property
    def accepted(self) -> bool:
        return not self.unanswered and not any(r.refusals for r in self.refs)


def format_line(item: Refusal | Note) -> str:
    """``<id> <where>: <message>``, every part through `util.printable`: a
    tree-entry name may hold a newline and a trailer value an escape sequence,
    and the line reaches the pusher's terminal."""
    return f"{printable(item.rule)} {printable(item.where)}: {printable(item.message)}"


class _Unanswered(NopekitError):
    """Raised inside the walk when a `vcs` read answered ``None``: E07, carried
    as its full line. `validate_push` catches it and stops the walk (fail
    closed); a helper called on its own raises it as a `NopekitError`."""

    def __init__(self, line: str) -> None:
        super().__init__(line)
        self.line = line


# --------------------------------------------------------------------------- #
# small pure pieces
# --------------------------------------------------------------------------- #
def is_zero(sha: str) -> bool:
    """The all-zero id git hands a hook for a ref being created or deleted."""
    return isinstance(sha, str) and len(sha) in (40, 64) and set(sha) == {"0"}


def classify_ref(ref: str) -> tuple[str, str]:
    """``("cand"|"draft"|"main"|"spec"|"published"|"other", name)``."""
    if ref.startswith(CAND_PREFIX):
        return "cand", ref[len(CAND_PREFIX):]
    if ref.startswith(DRAFT_PREFIX):
        return "draft", ref[len(DRAFT_PREFIX):]
    if ref == MAIN:
        return "main", "main"
    if ref == SPEC:
        return "spec", "spec"
    if ref == PUBLISHED:
        return "published", "published"
    return "other", ref


def _short(sha: str) -> str:
    return sha[:12]


def _mib(n: int) -> str:
    return f"{n / _MIB:.1f} MiB"


def _size_words(n: int) -> str:
    return _mib(n) if n >= _MIB else f"{n} bytes"


def _cap_words(cap: int) -> str:
    if cap >= _MIB:
        return f"{cap // _MIB} MiB" if cap % _MIB == 0 else _mib(cap)
    return f"{cap}-byte limit"


def _chunks(items: Sequence[Any], size: int = BATCH) -> Iterable[Sequence[Any]]:
    for at in range(0, len(items), size):
        yield items[at:at + size]


def _parts_ok(path: str) -> bool:
    """A path safe to join under a scratch directory: no empty, ``.`` or ``..``
    part, no backslash, no control character (E26's rule, re-checked where a
    byte is written)."""
    return bool(path) and not path.startswith("/") and all(
        part not in ("", ".", "..") and "\\" not in part
        and not any(ord(c) < 0x20 or ord(c) == 0x7f for c in part)
        for part in path.split("/"))


def _where(sha: str, kind: str, name: str) -> str:
    """``<sha12> (<kind> <name>)``; ``(<kind>)`` with no name."""
    return f"{_short(sha)} ({kind} {name})" if name else f"{_short(sha)} ({kind})"


_JSON_STRINGS = re.compile(rb'"(?:[^"\\]|\\.)*"')
_JSON_BRACKETS = re.compile(rb"[\[{\]}]")


def nesting_problem(raw: bytes) -> str | None:
    """``not strict JSON (nested deeper than N levels)`` when ``raw`` opens
    more than `MAX_JSON_DEPTH` arrays or objects without closing them, else
    ``None`` — a scan of the brackets outside string literals, run on every
    materialised JSON file BEFORE any reader opens it, so a reader never
    recurses to the interpreter's limit (where it raises ``RecursionError``,
    not the ``ValueError`` every reader catches). Linear in the bytes, bounded
    by `MATERIALISE_MAX_BYTES`; a file that is not JSON at all is left to its
    reader's words — this says only what no reader could."""
    depth = 0
    for hit in _JSON_BRACKETS.finditer(_JSON_STRINGS.sub(b"", raw)):
        if hit.group() in (b"[", b"{"):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                return f"not strict JSON (nested deeper than {MAX_JSON_DEPTH} levels)"
        elif depth:
            depth -= 1
    return None


def _deep_file_rule(path: str, why: str) -> str:
    """The rule that owns a materialised JSON file nested too deep, in that
    rule's own words: the state file E41, the spec's E34, an entry or a trace
    file E38, a record E32."""
    if path == STATE_REL:
        return f"E41 {STATE_REL} reads unreadable ({why}); run nopekit round and commit what it writes"
    if path == PACKS_REL:
        return f"E34 {PACKS_REL}: {why}"
    if path.startswith(EVIDENCE_ADDS):
        return f"E38 {path}: {why}; file the entry nopekit check writes"
    return f"E32 {path}: {why}"


def _split(problem: str) -> tuple[str, str]:
    """A helper's ``"<id> <tail>"`` string into its two halves."""
    rule, _sep, tail = problem.partition(" ")
    return rule, tail


def _refusals(where: str, problems: Iterable[str]) -> list[Refusal]:
    return [Refusal(rule, where, tail) for rule, tail in map(_split, problems)]


def _notes(where: str, problems: Iterable[str]) -> list[Note]:
    return [Note(rule, where, tail) for rule, tail in map(_split, problems)]


# --------------------------------------------------------------------------- #
# the tree id (git's tree format, pure)
# --------------------------------------------------------------------------- #
def tree_id(entries: Iterable[tuple[str, str, str]], algo: str) -> str:
    """git's id of the tree holding ``entries`` (``(mode, name, oid)``), pure:
    modes as git stores them (``40000`` for a tree, with or without `ls-tree`'s
    leading zero), entries sorted as git sorts them (a tree's name as if it ended
    in ``/``), ``tree <len>\\0`` before the body, hashed with ``algo`` (``sha1``
    or ``sha256``). Oracle: ``git mktree``."""
    rows: list[tuple[bytes, str, str, str]] = []
    for mode, name, oid in entries:
        mode = str(mode).lstrip("0") or "0"
        raw = os.fsencode(name)
        rows.append((raw + (b"/" if mode == "40000" else b""), mode, name, oid))
    rows.sort(key=lambda row: row[0])
    body = b"".join(f"{mode} ".encode("ascii") + os.fsencode(name) + b"\0" + bytes.fromhex(oid)
                    for _key, mode, name, oid in rows)
    digest = hashlib.sha256() if algo == "sha256" else hashlib.sha1()
    digest.update(b"tree %d\0" % len(body))
    digest.update(body)
    return digest.hexdigest()


def _spec_tree(top: Sequence[vcs.TreeEntry], listing: Sequence[vcs.TreeEntry],
               algo: str) -> tuple[str | None, list[str]]:
    """The spec tree's id from a commit's top-level entries and its listing:
    ``claims``, ``requirements``, ``milestones`` (each a tree, omitted when
    absent) and ``spec/packs.json``'s blob as ``packs.json``; ``(None, [E34
    tails])`` when ``spec/`` is out of shape."""
    problems: list[str] = []
    entries: list[tuple[str, str, str]] = []
    for entry in top:
        if entry.path in SPEC_ENTRIES and entry.type == "tree":
            entries.append((entry.mode, entry.path, entry.oid))
    packs_blob = None
    for entry in listing:
        if not entry.path.startswith(SPEC_DIR):
            continue
        if entry.path == PACKS_REL and entry.type == "blob" and entry.mode != "120000":
            packs_blob = entry
        else:
            problems.append(f"spec/{entry.path[len(SPEC_DIR):]} is not spec/packs.json; "
                            f"claims/, requirements/ and milestones/ live at the root and "
                            f"spec/ holds packs.json alone")
    if packs_blob is None:
        problems.insert(0, "spec/packs.json is missing; the spec pins its packs — commit "
                           "{\"schema\": 1, \"packs\": {}} when there are none")
        return None, problems
    entries.append((packs_blob.mode, "packs.json", packs_blob.oid))
    return tree_id(entries, algo), problems


def spec_tree_id(repo: str, sha: str, *,
                 objects: vcs.ObjectDirs | None = None) -> tuple[str | None, list[str]]:
    """The spec tree id of commit ``sha`` (`tree_id` over its ``claims``,
    ``requirements`` and ``milestones`` trees and its ``spec/packs.json`` blob)
    with E34's words when ``spec/`` is out of shape; ``(None, [...])`` then.
    Raises `NopekitError` (E07's line) when git does not answer."""
    git = _Git(repo, objects)
    algo = git.ask("object_format", vcs.object_format, repo, objects=objects)
    tree = git.ask("tree_of", vcs.tree_of, repo, sha, objects=objects)
    top = git.ask("tree_entries", vcs.tree_entries, repo, tree, objects=objects)
    listing = git.ask("tree_listing", vcs.tree_listing, repo, tree, objects=objects)
    return _spec_tree(top, listing, algo)


# --------------------------------------------------------------------------- #
# the spec's one real file
# --------------------------------------------------------------------------- #
_PACKS_KEYS = ("schema", "packs")
_PIN_KEYS = ("repo", "commit")
_HOST_LABEL = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$")
_HEX_COMMIT = re.compile(r"^([0-9a-f]{40}|[0-9a-f]{64})$")


def _strict_pairs(pairs: list) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def _unknown_key(where: str, key: str, known: Sequence[str]) -> str:
    """``unknown key "x"`` at the top, ``packs.mesh: unknown key "x"`` in a
    pin (``where`` is the pin's label, or empty)."""
    close = difflib.get_close_matches(key, list(known), n=1, cutoff=0.6)
    guess = f' (did you mean "{close[0]}"?)' if close else ""
    return f'{where}{": " if where else ""}unknown key "{key}"{guess}'


def _repo_problem(where: str, value: Any) -> str | None:
    shape = f"{where} must be an https:// URL with a host name"
    if not isinstance(value, str) or not value:
        return shape
    try:
        url = urlsplit(value)
    except ValueError:
        return shape
    if url.scheme != "https" or not url.hostname:
        return shape
    if url.username is not None or url.password is not None:
        return f"{where} must not carry a user name or a password"
    host = url.hostname
    labels = host.split(".")
    if (host == "localhost" or host.startswith("[") or all(l.isdigit() for l in labels)
            or not all(_HOST_LABEL.match(l) for l in labels)):
        return f"{where} must name a DNS host, not localhost or an IP address"
    if any(part == ".." for part in url.path.split("/")):
        return f"{where} has a .. path part"
    return None


def packs_problems(raw: bytes) -> tuple[list[str], dict[str, dict[str, str]]]:
    """The strict reader for ``spec/packs.json``: ``(problems, pins)``.

    ``{"schema": 1, "packs": {"<name>": {"repo": "<https URL>", "commit": "<40
    or 64 hex>"}}}``, nothing else — an unknown key is refused with a suggestion
    (as `store.read_record` refuses one), a duplicate key and NaN too; a name
    is `LINE_NAME`; ``repo`` is an ``https://`` URL with a DNS-name host, no
    userinfo, no ``..`` path part, never ``file:``, ``ssh:``, ``git:``, an IP
    literal or ``localhost`` (a pin names where a release is published, and a
    local or a bare-address pin is one nobody else can fetch)."""
    problems: list[str] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"not UTF-8 text ({exc.reason})"], {}
    if not text.strip():
        return ["the file is empty"], {}
    deep = nesting_problem(raw)
    if deep:
        return [deep], {}
    try:
        data = json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_no_constant)
    except (ValueError, RecursionError) as exc:
        return [f"not strict JSON ({' '.join(str(exc).split())})"], {}
    if not isinstance(data, dict):
        return [f"expected a JSON object, found {type(data).__name__}"], {}
    for key in data:
        if key not in _PACKS_KEYS:
            problems.append(_unknown_key("", key, _PACKS_KEYS))
    for key in _PACKS_KEYS:
        if key not in data:
            problems.append(f'"{key}" is missing; the file is {{"schema": 1, "packs": {{}}}} '
                            f'with a pin per pack')
    schema = data.get("schema")
    if "schema" in data and (isinstance(schema, bool) or schema != SCHEMA):
        problems.append(f"schema must be {SCHEMA}, not {schema!r}")
    found = data.get("packs")
    pins: dict[str, dict[str, str]] = {}
    if "packs" in data and not isinstance(found, dict):
        problems.append("packs must be an object of pins by pack name")
        found = {}
    for name, pin in (found or {}).items():
        where = f"packs.{name}"
        if not LINE_NAME.match(name):
            problems.append(f"{where}: not a pack name ([A-Za-z0-9][A-Za-z0-9._-]{{0,63}})")
        if not isinstance(pin, dict):
            problems.append(f"{where} must be an object with keys repo and commit")
            continue
        ok = True
        for key in pin:
            if key not in _PIN_KEYS:
                problems.append(_unknown_key(where, key, _PIN_KEYS))
                ok = False
        for key in _PIN_KEYS:
            if key not in pin:
                problems.append(f"{where}.{key} is missing")
                ok = False
        if "repo" in pin:
            why = _repo_problem(f"{where}.repo", pin["repo"])
            if why:
                problems.append(why)
                ok = False
        if "commit" in pin and not (isinstance(pin["commit"], str)
                                    and _HEX_COMMIT.match(pin["commit"])):
            problems.append(f"{where}.commit must be 40 or 64 hex characters")
            ok = False
        if ok:
            pins[name] = {"repo": pin["repo"], "commit": pin["commit"]}
    return problems, pins


# --------------------------------------------------------------------------- #
# the trailers (E12..E21)
# --------------------------------------------------------------------------- #
def _nope_lines(message: bytes) -> list[bytes]:
    """Every raw line of ``message`` that `NOPE_LINE` matches, as bytes."""
    found: list[bytes] = []
    for match in NOPE_LINE.finditer(message):
        end = message.find(b"\n", match.start())
        found.append(message[match.start():end if end >= 0 else len(message)])
    return found


def _raw_pair(line: bytes) -> tuple[str, str]:
    """A raw ``Nope-`` line's ``(key, value)``; the message is UTF-8 by the
    time this runs (`message_problem` refused it otherwise)."""
    key, _colon, value = line.partition(b":")
    return key.decode("utf-8").strip(), value.decode("utf-8").strip()


def message_problem(message: bytes) -> str | None:
    """E12's tail for a raw commit message the validator will not read, or
    ``None``: a control byte (anything below 0x20 but ``\\n`` and ``\\t``, or
    0x7f), then a byte that is not UTF-8. Checked on the raw bytes BEFORE
    git's reading of the trailer block is asked for or trusted, so the
    ``\\x01``/``\\x02`` separators `vcs.trailer_lines` frames with are safe.
    What slipped through (review of milestone 0): a ``\\x02`` in a trailer
    value broke that framing first, and the commit read E07 "git did not
    answer … push again" — a deterministic refusal presented as transient;
    and a Latin-1 commit (an ``encoding`` header, ``\\xe9`` in a value) read
    E16, because git re-encodes a trailer to UTF-8 and the raw-line scan
    compared bytes, with advice that could not fix it."""
    for offset, byte in enumerate(message):
        if (byte < 0x20 and byte not in (0x0A, 0x09)) or byte == 0x7F:
            return f"the commit message holds a control byte (0x{byte:02x}); rewrite the message"
    try:
        message.decode("utf-8")
    except UnicodeDecodeError as exc:
        return (f"the commit message is not UTF-8 (byte 0x{exc.object[exc.start]:02x} at "
                f"offset {exc.start}); commit it as UTF-8")
    return None


def _article(kind: str) -> str:
    """``an evidence commit``, ``a candidate commit`` (E20; the table spells
    both out — the first draft substituted ``a {kind}`` and printed "a evidence
    commit")."""
    return f"an {kind}" if kind[:1] in ("a", "e", "i", "o", "u") else f"a {kind}"


def trailer_problems(sha: str, message: bytes,
                     parsed: Sequence[tuple[str, str]]) -> tuple[str, dict[str, str], list[Refusal]]:
    """``(kind, trailers, refusals)`` for one commit: E12..E21 over its raw
    ``message`` and git's own reading of its trailer block (``parsed``, every
    trailer line, the non-``Nope-`` ones ignored). ``kind`` is ``""`` for a
    working commit — git returned no ``Nope-*`` trailer AND `NOPE_LINE` matches
    nowhere in the raw message; the two-readers ambiguity is closed both ways,
    so a commit git reads as trailer-less that holds a ``Nope-`` line is E16,
    never a working commit. The commit is still classified by its ``Nope-Kind``
    when one parsed, so later commits are judged against the right previous
    tree even when this one is refused. A message `message_problem` refuses
    (E12) is read no further: nothing of it is trusted, and it is not
    classified (the tip rule reads a refused commit, never a working one)."""
    sha12 = _short(sha)
    refusals: list[Refusal] = []
    unreadable = message_problem(message)
    if unreadable:
        return "", {}, [Refusal("E12", sha12, unreadable)]
    nope = [(key, value) for key, value in parsed if key.casefold().startswith("nope-")]
    raw_lines = _nope_lines(message)
    if not nope and not raw_lines:
        return "", {}, refusals

    # E16: a Nope- line git did not return as a trailer — outside the last
    # paragraph, or in a last paragraph git dropped for holding prose.
    unmatched = list(nope)
    for line in raw_lines:
        key, value = _raw_pair(line)
        hit = next((pair for pair in unmatched if pair == (key, value)), None)
        if hit is None:                 # a folded value: git joined its continuation
            hit = next((pair for pair in unmatched
                        if pair[0] == key and pair[1].startswith(value + " ")), None)
        if hit is None:
            refusals.append(Refusal("E16", sha12, f"\"{line.decode('utf-8')}\" sits outside "
                                                  f"the trailer block, where git reads it as "
                                                  f"prose; put every Nope- line in the last "
                                                  f"paragraph with nothing else in it"))
        else:
            unmatched.remove(hit)
    if not nope:
        # Git read no trailer and the raw scan found a Nope- line: E16 said
        # it. Not a working commit, and not classified — the tip rule reads
        # a refused commit, never a working one (E24 holds its tongue).
        return "", {}, refusals

    # E13, E14, E15: the closed schema, spelled exactly, each key once.
    by_fold = {key.casefold(): key for key in TRAILER_KEYS}
    variants = {key.casefold() for key, _value in nope}
    trailers: dict[str, str] = {}
    for key, value in nope:
        if key in TRAILER_KEYS:
            if key in trailers:
                refusals.append(Refusal("E15", sha12, f"{key} is given twice; give each "
                                                      f"trailer once"))
            else:
                trailers[key] = value
        elif key.casefold() in by_fold:
            refusals.append(Refusal("E14", sha12, f"{key} is spelled {by_fold[key.casefold()]}; "
                                                  f"trailer keys are spelled exactly"))
        else:
            close = difflib.get_close_matches(key, TRAILER_KEYS, n=1, cutoff=0.6)
            guess = f" (did you mean {close[0]}?)" if close else ""
            refusals.append(Refusal("E13", sha12, f"{key} is not a trailer the schema knows"
                                                  f"{guess}; the keys are Nope-Schema, "
                                                  f"Nope-Kind, Nope-Name, Nope-Trigger, "
                                                  f"Nope-Agent, Nope-Model"))

    # E17: the schema (a key given in another case was E14's; it is not
    # repeated as missing).
    if "Nope-Schema" not in trailers:
        if "nope-schema" not in variants:
            refusals.append(Refusal("E17", sha12, "Nope-Schema is missing; a commit that "
                                                  "claims a kind carries Nope-Schema: 1"))
    else:
        said = trailers["Nope-Schema"]
        # ASCII digits only, then the exact spelling: ``str.isdigit`` is true of
        # ``²`` (``int`` raises) and of ``١`` (``int`` reads 1) — review of
        # milestone 0, one crash and one acceptance from the same test.
        newer = _ASCII_DIGITS.match(said) is not None and int(said) > SCHEMA
        if newer:
            refusals.append(Refusal("E17", sha12, f"Nope-Schema: {said} is newer than this "
                                                  f"server reads ({SCHEMA}); push with "
                                                  f"Nope-Schema: {SCHEMA}"))
        elif said != str(SCHEMA):
            refusals.append(Refusal("E17", sha12, f"Nope-Schema: \"{said}\" is not {SCHEMA}; a "
                                                  f"commit that claims a kind carries "
                                                  f"Nope-Schema: {SCHEMA}"))

    # E18, E19: the kind.
    kind = ""
    if "Nope-Kind" not in trailers:
        if "nope-kind" not in variants:
            other = next((key for key, _value in nope if key.casefold() != "nope-kind"),
                         "Nope-Kind")
            refusals.append(Refusal("E18", sha12, f"{other} without Nope-Kind; a commit that "
                                                  f"claims nothing carries no Nope- trailer, "
                                                  f"one that claims carries Nope-Kind: "
                                                  f"candidate or evidence"))
        return "", trailers, refusals
    kind = trailers["Nope-Kind"]
    if kind not in KINDS:
        refusals.append(Refusal("E18", sha12, f"Nope-Kind: \"{kind}\" is not one of "
                                              f"{', '.join(KINDS)}"))
        return "", trailers, refusals
    if kind == "act":
        refusals.append(Refusal("E19", sha12, "Nope-Kind: act is never pushed; an act is "
                                              "recorded by Eddy on a signed-in person's click"))
        return kind, trailers, refusals

    # E20: the required keys (Nope-Schema and Nope-Kind have their own rules).
    required = REQUIRED_KEYS[kind]
    for key in required:
        if key not in trailers and key not in ("Nope-Schema", "Nope-Kind"):
            refusals.append(Refusal("E20", f"{sha12} ({kind})",
                                    f"{key} is missing; {_article(kind)} commit carries "
                                    f"{', '.join(required)}"))

    # E21: each value's shape, and an act's keys on a non-act.
    name = trailers.get("Nope-Name", "")
    where = _where(sha, kind, name if LINE_NAME.match(name) else "")
    if "Nope-Agent" in trailers and trailers["Nope-Agent"] not in AGENTS:
        refusals.append(Refusal("E21", where, f"Nope-Agent: \"{trailers['Nope-Agent']}\" is "
                                              f"not one of {', '.join(AGENTS)}"))
    if "Nope-Name" in trailers and not LINE_NAME.match(name):
        refusals.append(Refusal("E21", where, f"Nope-Name: \"{name}\" is not "
                                              f"[A-Za-z0-9][A-Za-z0-9._-]{{0,63}}"))
    if "Nope-Model" in trailers and not (0 < len(trailers["Nope-Model"]) <= MAX_VALUE_CHARS):
        refusals.append(Refusal("E21", where, f"Nope-Model is empty or over {MAX_VALUE_CHARS} "
                                              f"characters"))
    if "Nope-Trigger" in trailers and not TRIGGER.match(trailers["Nope-Trigger"]):
        refusals.append(Refusal("E21", where, f"Nope-Trigger: \"{trailers['Nope-Trigger']}\" "
                                              f"is not <gate id>/<16 hex>-<8 hex>"))
    for key in ACT_ONLY_KEYS:
        if key in trailers:
            refusals.append(Refusal("E21", where, f"{key} belongs to an act; drop it"))
    return kind, trailers, refusals


# --------------------------------------------------------------------------- #
# the tree (E25..E29)
# --------------------------------------------------------------------------- #
def _is_output(path: str) -> bool:
    """Does ``path`` match one of the ignore blocks nopekit writes, read with
    gitignore's meaning relative to the block's own directory?"""
    for root, pattern in _OUTPUT_RULES:
        if root and not path.startswith(root):
            continue
        rel = path[len(root):]
        parts = rel.split("/")
        is_dir = pattern.endswith("/")
        name = pattern.rstrip("/")
        anchored = name.startswith("/") or "/" in name
        name = name.lstrip("/")
        if is_dir:
            if anchored:
                if rel.startswith(name + "/"):
                    return True
            elif any(fnmatch.fnmatchcase(part, name) for part in parts[:-1]):
                return True
        elif anchored:
            if fnmatch.fnmatchcase(rel, name):
                return True
        elif any(fnmatch.fnmatchcase(part, name) for part in parts):
            return True
    return False


def _attribute_problems(file: str, raw: bytes) -> list[str]:
    """E27 tails for one ``.gitattributes`` blob: each token after the pattern
    judged against `ATTRIBUTES_REFUSED`, ``filter=`` and
    ``working-tree-encoding=``."""
    out: list[str] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        tokens = text.split()
        pattern, attrs = tokens[0], tokens[1:]
        for attr in attrs:
            if (attr in ATTRIBUTES_REFUSED or attr.startswith("filter=")
                    or attr.startswith("working-tree-encoding=")):
                out.append(f"E27 {file} sets {attr} on {pattern}; a checkout must be the tree "
                           f"— export-ignore, export-subst, filter, ident, eol=crlf and "
                           f"working-tree-encoding are refused")
    return out


def tree_problems(listing: Sequence[vcs.TreeEntry], top: Sequence[vcs.TreeEntry],
                  attributes: Mapping[str, bytes]) -> list[str]:
    """E25..E29 over a claiming commit's full ``listing`` (every blob, link and
    gitlink), its ``top``-level entries and the bytes of every
    ``.gitattributes`` in it (``{path: bytes}``), as ``"<id> <tail>"`` strings
    in rule order."""
    out: list[str] = []
    # E25: a tree Eddy reads holds files and directories only.
    for entry in listing:
        if entry.mode == "120000":
            out.append(f"E25 {entry.path} is a symbolic link; a tree Eddy reads holds files "
                       f"and directories only")
        elif entry.mode == "160000" or entry.type == "commit":
            out.append(f"E25 {entry.path} is a gitlink (a nested repository); vendor its "
                       f"files or leave them out")
    # E26: a collision on a case-insensitive or a normalising checkout, or a
    # path part no checkout should write. Directories count: `A/x` beside `a/y`
    # is two directories that are one.
    names: list[str] = []
    seen_paths: set[str] = set()
    for entry in listing:
        parts = entry.path.split("/")
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            if prefix not in seen_paths:
                seen_paths.add(prefix)
                names.append(prefix)
    reported: set[frozenset[str]] = set()
    for fold, words in (((lambda p: p.casefold()), "case; one file on a case-insensitive "
                                                   "checkout — rename one"),
                        ((lambda p: unicodedata.normalize("NFC", p).casefold()),
                         "Unicode normalisation; rename one")):
        groups: dict[str, list[str]] = {}
        for path in names:
            groups.setdefault(fold(path), []).append(path)
        for group in groups.values():
            if len(group) > 1 and frozenset(group) not in reported:
                reported.add(frozenset(group))
                out.append(f"E26 {group[0]} and {group[1]} differ only in {words}")
    for entry in listing:
        if not _parts_ok(entry.path) or any(part.casefold() == ".git"
                                            for part in entry.path.split("/")):
            out.append(f"E26 {entry.path} has a path part that is .git, ., .. or empty, or "
                       f"a control byte; rename it")
    # E27: every .gitattributes, materialised, scanned token by token.
    for file in sorted(attributes):
        out += _attribute_problems(file, attributes[file])
    # E28: an ignored output, or a path under .nopekit/ nopekit does not keep.
    for entry in listing:
        path = entry.path
        if _is_output(path):
            out.append(f"E28 {path} is an output nopekit ignores; committed, it would be read "
                       f"as truth on every clone — take it out of the commit")
        elif path.startswith(_NOPEKIT_PREFIX):
            rest = path[len(_NOPEKIT_PREFIX):]
            first, slash, _more = rest.partition("/")
            kept = ((not slash and first in NOPEKIT_KEPT)
                    or (slash and first + "/" in NOPEKIT_KEPT))
            if not kept:
                out.append(f"E28 {path} is not a path nopekit keeps under .nopekit/ ({_KEPT}); "
                           f"pin a pack in spec/packs.json instead of committing it")
    # E29: every path under verdicts/ is exactly <gate>/<entry name>, every one
    # under trace/ exactly one trace file name.
    for entry in listing:
        path = entry.path
        if path.startswith(VERDICTS_PREFIX):
            parts = path[len(VERDICTS_PREFIX):].split("/")
            if len(parts) != 2 or not (verdicts._ENTRY_NAME.match(parts[1])
                                        or verdicts._CONTROL_NAME.match(parts[1])):
                out.append(f"E29 {path} is not a verdict entry name (<gate>/<16 hex>-<8 "
                           f"hex>.json or <gate>/control-<16 hex>-<8 hex>.json); take it out")
                continue
            try:
                verdicts._check_gate_id(parts[0])
            except NopekitError as exc:
                out.append(f"E29 {parts[0]}: {exc}")
        elif path.startswith(TRACE_PREFIX):
            if not TRACE_NAME.match(path[len(TRACE_PREFIX):]):
                out.append(f"E29 {path} is not a trace file name (<16 hex>.json or <16 "
                           f"hex>.amber.<16 hex>.json); take it out")
    return out


# --------------------------------------------------------------------------- #
# the diff (E36, E37)
# --------------------------------------------------------------------------- #
def diff_problems(kind: str, changes: Sequence[vcs.Change]) -> list[str]:
    """E36 (an evidence commit changes only the evidence set) and E37 (an entry
    or a trace file is never modified, retyped or deleted), as ``"<id> <tail>"``
    strings."""
    out: list[str] = []
    for change in changes:
        path, status = change.path, change.status
        under_cache = path.startswith(EVIDENCE_ADDS)
        if kind == "evidence" and not under_cache:
            if not (path == STATE_REL and status in ("A", "M")):
                out.append(f"E36 changes {path} ({status}); an evidence commit adds under "
                           f".nopekit/verdicts/ and .nopekit/trace/ and rewrites {STATE_REL}, "
                           f"nothing else — put the change in a candidate commit")
    for change in changes:
        if change.path.startswith(EVIDENCE_ADDS) and change.status != "A":
            if change.status == "D":
                out.append(f"E37 {change.path} is deleted; evidence is added, never withdrawn")
            else:
                out.append(f"E37 {change.path} is modified; a verdict entry is written once — "
                           f"file a new entry at its new rho")
    return out


# --------------------------------------------------------------------------- #
# the materialised copy (E30), and what runs on it (E31..E33, E38..E44)
# --------------------------------------------------------------------------- #
def _materialise_set(listing: Sequence[vcs.TreeEntry]) -> list[vcs.TreeEntry]:
    """What the hook reads of a tree: the project file, the state file, every
    blob under the verdict cache and the trace, every top-level
    ``<kind>/<stem>.json`` record, every ``.gitattributes`` and the spec's
    ``packs.json`` — blobs only (a link or a gitlink there is E25's)."""
    chosen: list[vcs.TreeEntry] = []
    for entry in listing:
        if entry.type != "blob" or entry.mode == "120000":
            continue
        path = entry.path
        parts = path.split("/")
        if path in (PROJECT_REL, STATE_REL, PACKS_REL) or parts[-1] == ".gitattributes":
            chosen.append(entry)
        elif path.startswith((VERDICTS_PREFIX, TRACE_PREFIX)):
            chosen.append(entry)
        elif (len(parts) == 2 and parts[0] in store.RECORD_DIRS and parts[1].endswith(".json")
              and not parts[1].startswith(".")):
            chosen.append(entry)
    return chosen


def materialise(repo: str, listing: Sequence[vcs.TreeEntry], *,
                objects: vcs.ObjectDirs | None = None) -> tuple[str, list[str]]:
    """Write the blobs the hook reads (`_materialise_set`) under a fresh
    ``tempfile.mkdtemp(prefix="nopekit-validate-")`` — ``(tmpdir, problems)``,
    the caller removes the directory. Count and size are checked against
    `MATERIALISE_MAX_FILES` and `MATERIALISE_MAX_BYTES` BEFORE any blob is
    asked for (E30, the one problem it reports; nothing is written then).
    Never follows a link: every path is joined under the directory from its
    checked parts, and a link in the set was E25 already. Raises `NopekitError`
    (E07's line) when git does not answer."""
    chosen = _materialise_set(listing)
    total = sum(entry.size or 0 for entry in chosen)
    tmp = tempfile.mkdtemp(prefix="nopekit-validate-")
    if len(chosen) > MATERIALISE_MAX_FILES:
        return tmp, [f"E30 {len(chosen)} files under the records, .nopekit/verdicts/ and "
                     f".nopekit/trace/, over the {MATERIALISE_MAX_FILES} one tree may ask the "
                     f"hook to read; start a new line from a slimmer candidate"]
    if total > MATERIALISE_MAX_BYTES:
        return tmp, [f"E30 {_size_words(total)} under the records, .nopekit/verdicts/ and "
                     f".nopekit/trace/, over the {_cap_words(MATERIALISE_MAX_BYTES)} one tree "
                     f"may ask the hook to read; start a new line from a slimmer candidate"]
    git = _Git(repo, objects)
    try:
        for chunk in _chunks([entry for entry in chosen if _parts_ok(entry.path)]):
            oids = sorted({entry.oid for entry in chunk})
            found = git.ask("blobs", vcs.blobs, repo, oids, objects=objects)
            for entry in chunk:
                full = os.path.join(tmp, *entry.path.split("/"))
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "wb") as fh:
                    fh.write(found[entry.oid])
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tmp, []


def record_problems(tmp: str, *, listing: Sequence[vcs.TreeEntry] = ()) -> list[str]:
    """E32: `store.load` over the materialised copy, a `NopekitError`'s message
    verbatim (an unknown key with its suggestion, a duplicate key, NaN, a stem
    that is not its id, case variants, a forbidden key, a broken sealed chain);
    and, from ``listing``, every blob under ``claims/``, ``requirements/`` or
    ``milestones/`` that is not a top-level ``<stem>.json`` — a stray file could
    otherwise move the spec tree without moving a claim."""
    out: list[str] = []
    try:
        store.load(tmp, model_prose=modelio.static_param_prose)
    except NopekitError as exc:
        out.append(f"E32 {exc}")
    for entry in listing:
        parts = entry.path.split("/")
        if parts[0] in SPEC_ENTRIES and not (len(parts) == 2 and parts[1].endswith(".json")
                                             and not parts[1].startswith(".")):
            out.append(f"E32 {entry.path} is not a record; claims/, requirements/ and "
                       f"milestones/ hold <id>.json files only — move it")
    return out


def _sealed_lists(found: Any) -> dict[str, list]:
    raw = getattr(found, "raw", None)
    if isinstance(raw, dict):
        return {name: list(items or ()) for name, items in raw.items()}
    if isinstance(raw, list):
        return {"": list(raw)}
    return {}


def sealed_problems(kind: str, before: Any, after: Any) -> list[str]:
    """E33 for one sealed record (``results``, ``exports``, ``preferences``):
    ``before`` and ``after`` as `store.read_record` returns them (the previous
    tree's and this tree's), ``after`` ``None`` when the file was deleted. The
    previous file's stored entries must be a prefix of the new's, list by list.
    ``kind`` here is the record's label, ``results/C1.json``."""
    if after is None:
        return [f"E33 {kind} is deleted; a sealed record is never removed"]
    old, new = _sealed_lists(before), _sealed_lists(after)
    for name, items in old.items():
        now = new.get(name, [])
        for index, entry in enumerate(items):
            if index >= len(now) or now[index] != entry:
                return [f"E33 {kind} drops or rewrites entry {index + 1}; a sealed record is "
                        f"appended, never shortened — restore it from the previous candidate "
                        f"and append"]
    return []


def _blob_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def entry_problems(tmp: str, added: Sequence[str], *, ledger: Any = None,
                   listing: Sequence[vcs.TreeEntry] = (), hashes: Mapping[str, str] | None = None
                   ) -> tuple[list[str], list[str]]:
    """``(refusals, notes)`` for the entries and trace files a claiming commit
    ``added`` (root-relative paths, materialised under ``tmp``): E38 when the
    entry's own reader refuses it (`verdicts._load_entry`, `_load_control`,
    `derivation.read_trace`, `read_amber` — their words verbatim), E39 when its
    rho or its static does not follow from what it says it read; N02 for entries
    written under another spine, N03 for a tree-visible read (a file's sha, a
    claim's or an acceptance's digest in ``reads.ledger``) that disagrees with
    this tree (``hashes``: ``{path: sha256}`` of the tree's blobs that were
    hashed; ``ledger``: `store.load` of the copy), N04 for two outcomes at one
    rho under a gate — notes, never refusals: an agent that ran `check` twice at
    two limits between commits commits two honest entries, one of them Stale on
    arrival, and a forger who can compute rho can compute those digests too."""
    refusals: list[str] = []
    notes: list[str] = []
    other_spine: list[str] = []
    spine_now = verdicts.spine_digest()
    hashes = hashes or {}
    names_by_gate: dict[str, set[str]] = {}
    for entry in listing:
        if entry.path.startswith(VERDICTS_PREFIX):
            parts = entry.path[len(VERDICTS_PREFIX):].split("/")
            if len(parts) == 2:
                names_by_gate.setdefault(parts[0], set()).add(parts[1])
    noted_rho: set[tuple[str, str]] = set()
    for path in added:
        full = os.path.join(tmp, *path.split("/"))
        if path.startswith(TRACE_PREFIX):
            name = path[len(TRACE_PREFIX):]
            reader = derivation.read_amber if ".amber." in name else derivation.read_trace
            state, _file, line = reader(full)
            if state != "ok":
                refusals.append(f"E38 {path}: {line or state}; file the entry nopekit check "
                                f"writes")
            continue
        if not path.startswith(VERDICTS_PREFIX):
            continue
        parts = path[len(VERDICTS_PREFIX):].split("/")
        if len(parts) != 2:
            continue                                      # E29's
        gate, name = parts
        reads: dict = {}
        file_reads: list[Mapping[str, Any]] = []
        if verdicts._CONTROL_NAME.match(name):
            control, why = verdicts._load_control(full, gate)
            if control is None:
                refusals.append(f"E38 {path}: {why}; file the entry nopekit check writes")
                continue
            static_now = verdicts._digest_of({"schema": verdicts.SCHEMA, **control.static_parts})
            if control.static != static_now:
                refusals.append(f"E39 {path}: static does not follow from its own "
                                f"static_parts; file the entry nopekit check writes")
            elif verdicts.rho_control(control.gate, control.static, control.reads,
                                      control.good["reads"] if control.good else None) \
                    != control.rho:
                refusals.append(f"E39 {path}: rho does not follow from its own gate, spine, "
                                f"code and reads; file the entry nopekit check writes")
            spine = control.static_parts.get("spine")
            if isinstance(spine, str) and spine != spine_now:
                other_spine.append(spine)
            reads = control.reads
            file_reads = [reads.get("files") or {}, control.fixture.get("files") or {},
                          (control.static_parts.get("selftest") or {}).get("files") or {}]
        else:
            entry, why = verdicts._load_entry(full, gate)
            if entry is None:
                refusals.append(f"E38 {path}: {why}; file the entry nopekit check writes")
                continue
            if verdicts.rho(entry.gate, entry.spine, entry.code["digest"], entry.reads) \
                    != entry.rho:
                refusals.append(f"E39 {path}: rho does not follow from its own gate, spine, "
                                f"code and reads; file the entry nopekit check writes")
            if entry.spine != spine_now:
                other_spine.append(entry.spine)
            reads = entry.reads
            file_reads = [reads.get("files") or {}]
            rho16 = name[:verdicts.RHO_CHARS]
            twins = {n for n in names_by_gate.get(gate, ()) if n.startswith(rho16 + "-")
                     and verdicts._ENTRY_NAME.match(n)}
            if len(twins) > 1 and (gate, rho16) not in noted_rho:
                noted_rho.add((gate, rho16))
                notes.append(f"N04 {VERDICTS_PREFIX}{gate}/ holds two outcomes at rho "
                             f"{rho16[:12]}; the resolver reads that as an error and the page "
                             f"shows the contradiction, not either outcome")
        for table in file_reads:
            if not isinstance(table, Mapping):
                continue
            for rel, digest in sorted(table.items()):
                if isinstance(digest, str) and rel in hashes and hashes[rel] != digest:
                    notes.append(f"N03 {path} read {rel} at {digest[:12]}; this tree holds "
                                 f"{hashes[rel][:12]}, so it arrives Stale")
        if ledger is not None and isinstance(reads.get("ledger"), Mapping):
            for key, digest in sorted(reads["ledger"].items()):
                now = verdicts._ledger_now(ledger, key)
                if isinstance(digest, str) and now is not None and now != digest:
                    notes.append(f"N03 {path} read {key} at {digest[:12]}; this tree holds "
                                 f"{now[:12]}, so it arrives Stale")
    if other_spine:
        notes.insert(0, f"N02 {len(other_spine)} added entries were written under another "
                        f"spine ({other_spine[0][:12]}); they read Stale under this nopekit")
    return refusals, notes


def state_file_problems(tmp: str, previous_round: int | None, state_changed: bool) -> list[str]:
    """E40..E44 over the materialised copy's state file: present; `rounds.read`
    says ``ok``; computed from this tree's records and verdict cache (the two
    digests that are pure functions of file bytes); the bytes
    `rounds.render(rounds.sealed(doc, round))` would write; and, when the blob
    moved from the previous claiming commit's (``state_changed``), a round
    above that commit's (``previous_round``, when known). The question
    `cli._round_state` asks for `status` and `doctor` — whether the record is
    the tree's own — and never a status."""
    found = rounds.read(tmp)
    if found.state == "absent":
        return [f"E40 {STATE_REL} is missing; run nopekit round and commit it"]
    if found.state == "edited":
        return [f"E41 {STATE_REL} reads edited (its self-digest does not match its content); "
                f"run nopekit round and commit what it writes"]
    if found.state == "unreadable":
        return [f"E41 {STATE_REL} reads unreadable ({found.why}); run nopekit round and commit "
                f"what it writes"]
    if found.state == "newer":
        return [f"E41 {STATE_REL} was written by a newer nopekit (schema {found.schema}); this "
                f"server reads {rounds.SCHEMA}"]
    out: list[str] = []
    doc = found.doc or {}
    held = doc.get("computed_from") if isinstance(doc.get("computed_from"), dict) else {}
    try:
        records_now: str | None = store.records_digest(tmp)
    except NopekitError:
        # Two record stems differing only in case: the digest cannot be taken
        # over files the strict reader refuses (E32 said why), and the record
        # could not have been computed from them.
        records_now = None
    verdicts_now = rounds.verdicts_digest(tmp)
    if records_now is not None and held.get("records") != records_now:
        out.append(f"E42 {STATE_REL} was computed from other records (computed_from.records "
                   f"{str(held.get('records') or '')[:12]}, this tree's records digest "
                   f"{records_now[:12]}); run nopekit round and commit it")
    if held.get("verdicts") != verdicts_now:
        out.append(f"E42 {STATE_REL} was computed from another verdict cache "
                   f"(computed_from.verdicts {str(held.get('verdicts') or '')[:12]}, this "
                   f"tree's verdicts digest {verdicts_now[:12]}); run nopekit round and commit "
                   f"it")
    if found.round is None:
        # `rounds.read` says ``ok`` of a record whose ``round`` is not an
        # integer >= 1 (``Previous.round`` is None then), and nothing else
        # below would hold it: ``sealed(doc, 0)`` renders the same bytes (E43
        # holds), and E44 compares nothing against a round it never learned.
        # What slipped through (review of milestone 0): a line started with
        # ``round: 0`` landed, then an evidence commit on it saying ``round:
        # -7`` landed too, because `rounds.last_committed_round` carries no
        # number below 1 and E44's guard waits for one.
        value = repr(doc.get("round"))
        return [f"E41 {STATE_REL} reads unreadable (round {value[:40]} is not an integer >= 1); "
                f"run nopekit round and commit what it writes"]
    number = found.round
    try:
        with open(os.path.join(tmp, *STATE_REL.split("/")), "rb") as fh:
            raw = fh.read()
        rendered = rounds.render(rounds.sealed(doc, number))
    except (OSError, TypeError, ValueError, KeyError):
        rendered = None
    if rendered != raw:
        out.append(f"E43 {STATE_REL} is not the bytes nopekit round renders (indent, key order "
                   f"or newline differ); run nopekit round and commit what it writes")
    if state_changed and previous_round is not None and not (
            isinstance(number, int) and not isinstance(number, bool) and number > previous_round):
        out.append(f"E44 {STATE_REL} says round {number}, not above the previous candidate's "
                   f"{previous_round}; run nopekit round and commit what it writes")
    return out


# --------------------------------------------------------------------------- #
# the git questions, each read as E07 when unanswered
# --------------------------------------------------------------------------- #
class _Git:
    """Every `vcs` read the walk makes, through one method: a ``None`` answer
    is E07, with the call's name and git's first stderr line from a diagnostic
    ``rev-parse`` (the reads themselves swallow stderr by `vcs`'s contract)."""

    def __init__(self, repo: str, objects: vcs.ObjectDirs | None) -> None:
        self.repo = repo
        self.objects = objects

    def ask(self, call: str, fn: Any, *args: Any, **kw: Any) -> Any:
        result = fn(*args, **kw)
        if result is None:
            raise _Unanswered(self.e07(call))
        return result

    def e07(self, call: str) -> str:
        code, _out, err = vcs._ask(self.repo, ["rev-parse", "--git-dir"], objects=self.objects)
        first = ""
        if err:
            first = err.decode("utf-8", "replace").splitlines()[0].strip()
        if not first:
            first = "no stderr" if code is not None else "git did not start"
        return format_line(Refusal("E07", "push", f"git did not answer ({call}: {first}); "
                                                  f"nothing landed — push again, and if it "
                                                  f"repeats the server's keeper reads the "
                                                  f"hook's log"))


# --------------------------------------------------------------------------- #
# the object scan (E08..E11)
# --------------------------------------------------------------------------- #
def _cache_shaped(prefix: str, rest: str, *, is_tree: bool) -> bool:
    """Is ``rest`` (a path under ``prefix``, one of `EVIDENCE_ADDS`) a path
    nopekit itself writes there? Under the verdict cache: a gate's directory
    (one part, a tree, a name `verdicts._check_gate_id` takes) or an entry in
    one (two parts, a blob named ``<rho16>-<out8>.json`` or
    ``control-…``); under the trace: one blob named as `TRACE_NAME`."""
    parts = rest.split("/")
    if prefix == TRACE_PREFIX:
        return len(parts) == 1 and not is_tree and TRACE_NAME.match(parts[0]) is not None
    if len(parts) > 2 or not parts[0]:
        return False
    try:
        verdicts._check_gate_id(parts[0])
    except NopekitError:
        return False
    if len(parts) == 1:
        return is_tree
    return not is_tree and (verdicts._ENTRY_NAME.match(parts[1]) is not None
                            or verdicts._CONTROL_NAME.match(parts[1]) is not None)


def secret_name(path: str, *, is_tree: bool = False) -> str | None:
    """`rounds.secret_name` for a new tree's entry (E10), with the exemption
    of `rounds.ROUND_NAMED_BY_NOPEKIT` narrowed to the paths nopekit writes
    there (`_cache_shaped`): an evaluator called ``cad.key`` keeps its
    verdicts, and ``.nopekit/verdicts/aws/credentials`` or
    ``.nopekit/trace/id_rsa`` is named like any other path. What slipped
    through (review of milestone 0): both landed on ``draft/*`` and ``main``,
    where no tree rule runs (E29 holds the shape only on a claiming commit),
    and nothing pushed is ever removed."""
    pattern = rounds.secret_name(path)
    if pattern is None:
        for prefix in EVIDENCE_ADDS:
            if path.startswith(prefix) and not _cache_shaped(prefix, path[len(prefix):],
                                                              is_tree=is_tree):
                return rounds.secret_name(path[len(prefix):])
    return pattern


def object_problems(repo: str, new: str, *, exclude: Sequence[str],
                    objects: vcs.ObjectDirs | None = None) -> list[Refusal]:
    """The scan of every object reachable from ``new`` and from none of
    ``exclude`` (the pre-push ref tips, and ``old``): sizes through
    ``batch-check`` (no blob is read), names from every new tree's entries.
    E11 an object git cannot find; E08 a blob (or a commit, or a tree) over
    `MAX_BLOB_BYTES`, named by rev-list's label; E09 new blobs over
    `MAX_PUSH_BYTES` in all; E10 a secret-looking name (`rounds.secret_name`)
    anywhere in a new tree — a blob the repository already held, placed at
    ``.aws/credentials`` by a new tree, is seen here and nowhere else. Raises
    `NopekitError` (E07's line) when git does not answer."""
    git = _Git(repo, objects)
    found = git.ask("new_objects", vcs.new_objects, repo, new, exclude=list(exclude),
                    objects=objects)
    sizes: dict[str, tuple[str, int]] = {}
    oids = list(dict.fromkeys(oid for oid, _path in found))
    for chunk in _chunks(oids):
        sizes.update(git.ask("object_sizes", vcs.object_sizes, repo, list(chunk), objects=objects))
    out: list[Refusal] = []
    for oid in oids:
        if oid not in sizes:
            out.append(Refusal("E11", "push", f"object {_short(oid)} is in neither the push "
                                              f"nor the repository; push again"))
    total = 0
    trees: dict[str, str] = {}
    for oid, path in found:
        if oid not in sizes:
            continue
        kind, size = sizes[oid]
        if kind == "blob":
            total += size
        elif kind == "tree" and oid not in trees:
            trees[oid] = path
        if size > MAX_BLOB_BYTES:
            label = path or kind
            out.append(Refusal("E08", "push", f"{label} ({_short(oid)}) is {_size_words(size)}, "
                                              f"over the {_cap_words(MAX_BLOB_BYTES)} a pushed "
                                              f"file may be; nothing pushed is ever removed, "
                                              f"so keep it out of the push or shrink it"))
    if total > MAX_PUSH_BYTES:
        out.append(Refusal("E09", "push", f"{_mib(total)} of new files, over the "
                                          f"{_mib(MAX_PUSH_BYTES)} one push may carry; push in "
                                          f"smaller steps"))
    named: set[str] = set()
    tree_oids = list(trees)
    for chunk in _chunks(tree_oids):
        entries = git.ask("tree_objects", vcs.tree_objects, repo, list(chunk), objects=objects)
        for oid in chunk:
            base = trees[oid]
            for mode, name, _child in entries.get(oid, ()):
                path = f"{base}/{name}" if base else name
                pattern = secret_name(path, is_tree=str(mode).lstrip("0") == "40000")
                if pattern and path not in named:
                    named.add(path)
                    out.append(Refusal("E10", "push", f"{path} looks like a secret ({pattern}); "
                                                      f"nothing pushed is ever removed, so "
                                                      f"rename it or keep it out of the push"))
    return out


# --------------------------------------------------------------------------- #
# the push walk
# --------------------------------------------------------------------------- #
class _Line:
    """What the walk carries along a line, oldest commit first: the previous
    claiming commit's tree and round, the candidate names given so far (E23),
    the nearest candidate below (an evidence commit's), and the spec tree the
    report says."""

    def __init__(self, prev_tree: str, prev_round: int | None, names: dict[str, str],
                 candidate: tuple[str, str] | None) -> None:
        self.prev_tree = prev_tree
        self.prev_round = prev_round
        self.names = names                     # Nope-Name -> sha of the candidate that holds it
        self.candidate = candidate             # (sha, name) of the nearest candidate below
        self.spec_tree = ""
        self.accepted: bool | None = None


class _Push:
    """One push against one repository: the git questions, the refs' tips read
    once, the spec branch's trees read once."""

    def __init__(self, repo: str, objects: vcs.ObjectDirs | None) -> None:
        self.repo = repo
        self.objects = objects
        self.git = _Git(repo, objects)
        self.algo = self.git.ask("object_format", vcs.object_format, repo, objects=objects)
        self.tips: dict[str, str] = self.git.ask("ref_tips", vcs.ref_tips, repo, "refs/",
                                                 objects=objects)
        self._spec_trees: set[str] | None = None

    def ask(self, call: str, fn: Any, *args: Any, **kw: Any) -> Any:
        return self.git.ask(call, fn, *args, objects=self.objects, **kw)

    def spec_trees(self) -> set[str]:
        if self._spec_trees is None:
            self._spec_trees = set(self.ask("ref_trees", vcs.ref_trees, self.repo, SPEC,
                                            limit=100_000))
        return self._spec_trees

    def commits(self, shas: Sequence[str]) -> tuple[dict[str, vcs.CommitObject],
                                                    dict[str, list[tuple[str, str]]]]:
        """`vcs.commit_objects` and `vcs.trailer_lines` over ``shas`` in
        `BATCH`-sized processes."""
        objects: dict[str, vcs.CommitObject] = {}
        trailers: dict[str, list[tuple[str, str]]] = {}
        for chunk in _chunks(list(shas)):
            objects.update(self.ask("commit_objects", vcs.commit_objects, self.repo, list(chunk)))
            # A message E12 refuses is not handed to git's trailer reader: a
            # \x01 or \x02 in it breaks `vcs.trailer_lines`'s framing, which
            # answers None, and that read E07 (review of milestone 0).
            readable = [sha for sha in chunk
                        if sha in objects and message_problem(objects[sha].message) is None]
            for sha in chunk:
                if sha not in readable:
                    trailers[sha] = []
            if readable:
                trailers.update(self.ask("trailer_lines", vcs.trailer_lines, self.repo, readable))
        for sha in shas:
            if sha not in objects or sha not in trailers:
                raise _Unanswered(self.git.e07("commit_objects"))
        return objects, trailers

    def lenient_kind(self, parsed: Sequence[tuple[str, str]]) -> tuple[str, str]:
        """``(kind, name)`` as a commit that landed was judged: read leniently
        (it was judged when it landed), ``("", "")`` for a working commit."""
        kind = name = ""
        for key, value in parsed:
            fold = key.casefold()
            if fold == "nope-kind" and not kind:
                kind = value if value in ("candidate", "evidence") else kind
            elif fold == "nope-name" and not name:
                name = value
        return kind, name

    def line_below(self, base: str | None) -> _Line:
        """Step 7: the previous claiming commit P0 at or below ``base`` — its
        tree, and the candidate names on the line below the push."""
        if base is None:
            empty = self.git.ask("empty_tree", vcs.empty_tree, self.repo)
            if empty != EMPTY_TREE.get(self.algo):
                raise _Unanswered(self.git.e07("empty_tree"))
            return _Line(empty, None, {}, None)
        below = self.ask("first_parents", vcs.first_parents, self.repo, base,
                         limit=PREVIOUS_CLAIMING_LIMIT)
        prev_tree: str | None = None
        names: dict[str, str] = {}
        candidate: tuple[str, str] | None = None
        for chunk in _chunks(below):
            trailers = self.ask("trailer_lines", vcs.trailer_lines, self.repo, list(chunk))
            for sha in chunk:
                kind, name = self.lenient_kind(trailers.get(sha, ()))
                if not kind:
                    continue
                if prev_tree is None:
                    prev_tree = self.ask("tree_of", vcs.tree_of, self.repo, sha)
                if kind == "candidate":
                    if candidate is None:
                        candidate = (sha, name)
                    names.setdefault(name, sha)
        if prev_tree is None:
            prev_tree = self.git.ask("empty_tree", vcs.empty_tree, self.repo)
        return _Line(prev_tree, None, names, candidate)


def _ref_label(ref: str) -> str:
    kind, name = classify_ref(ref)
    if kind in ("cand", "draft"):
        return f"{kind}/{name}"
    return name


def _ref_problems(push: _Push, old: str, new: str, ref: str,
                  earlier: Sequence[str] = ()) -> RefReport:
    """Steps 1–10 for one ref of the push; ``earlier`` the refs before it in
    the same push that are not deletions (E05 reads them beside the
    repository's tips)."""
    kind, name = classify_ref(ref)
    label = _ref_label(ref)
    refusals: list[Refusal] = []
    notes: list[Note] = []
    commits: list[Commit] = []
    done = lambda spec_tree="", accepted=None: RefReport(  # noqa: E731
        ref, old, new, tuple(refusals), tuple(notes), tuple(commits), spec_tree, accepted)

    # Step 1: the layout.
    if kind == "other":
        refusals.append(Refusal("E01", ref, "not a ref Eddy keeps; push to cand/<name> (a "
                                            "line), draft/<name> (scratch) or main"))
        return done()
    if kind == "spec":
        refusals.append(Refusal("E02", "spec", "written only by Eddy when the gates are "
                                               "accepted; push a candidate to cand/<name> and "
                                               "accept it there"))
        return done()
    if kind == "published":
        refusals.append(Refusal("E02", "published", "moved only by a publish act; push a "
                                                    "candidate to cand/<name>"))
        return done()
    if kind == "cand":
        if not LINE_NAME.match(name):
            refusals.append(Refusal("E05", label, "a line's name is "
                                                  "[A-Za-z0-9][A-Za-z0-9._-]{0,63}; rename "
                                                  "the ref"))
            return done()
        # The repository's lines, then the lines this same push creates before
        # this one: `push.tips` is the ref state BEFORE the push, so two case
        # twins pushed together saw neither (review of milestone 0: both
        # landed, and the honest line could then never grow).
        for other in list(push.tips) + list(earlier):
            if other.startswith(CAND_PREFIX) and other != ref \
                    and other[len(CAND_PREFIX):].casefold() == name.casefold():
                refusals.append(Refusal("E05", label, f"differs only in case from "
                                                      f"{_ref_label(other)}; pick another name"))
                return done()

    # Step 2: deletion.
    if is_zero(new):
        if kind == "cand":
            refusals.append(Refusal("E03", label, "a line is never deleted; leave it and push "
                                                  "a new line"))
        elif kind == "main":
            refusals.append(Refusal("E03", "main", "never deleted; it carries the README and "
                                                   "the Pages workflow"))
        return done()

    # Step 3: existence. `new` that is there but is not a commit (a tag, a
    # tree) is said in the validator's words — on a line E24, since the tip
    # claims no kind; on draft/* and main the scan runs and git itself then
    # refuses the ref write ("trying to write non-commit object"). Review of
    # milestone 0: it read E07 "push again", a deterministic refusal presented
    # as transient. `old`, when git does not hold it as a commit, is E07 still:
    # this is not the repository the hook was asked about.
    if vcs.commit_exists(push.repo, new, objects=push.objects) is not True:
        held = push.ask("object_sizes", vcs.object_sizes, push.repo, [new]).get(new)
        if held is None or held[0] == "commit":
            raise _Unanswered(push.git.e07("commit_exists"))
        if kind == "cand":
            refusals.append(Refusal("E24", label, f"the tip {_short(new)} is a {held[0]}, not a "
                                                  f"commit; a push ends on a candidate or an "
                                                  f"evidence commit"))
            return done()
    if not is_zero(old) and push.ask("commit_exists", vcs.commit_exists, push.repo, old) \
            is not True:
        raise _Unanswered(push.git.e07("commit_exists"))

    # Step 4: the object scan, before any blob is read.
    exclude = [tip for other, tip in push.tips.items() if other != ref]
    if not is_zero(old):
        exclude.append(old)
    refusals += object_problems(push.repo, new, exclude=exclude, objects=push.objects)
    if kind != "cand":
        return done()

    # Step 5: the chain.
    chain = push.ask("first_parents", vcs.first_parents, push.repo, new, limit=WALK_LIMIT)
    over = (f"more than {WALK_LIMIT} first-parent commits to validate; start the line from a "
            f"candidate commit nearer its tip")
    if is_zero(old):
        others = [tip for other, tip in push.tips.items()
                  if other.startswith(CAND_PREFIX) and other != ref]
        lines = set(push.ask("line_commits", vcs.line_commits, push.repo, others,
                             limit=200_000)) if others else set()
        k = next((i for i, sha in enumerate(chain) if sha in lines), len(chain))
    else:
        try:
            k = chain.index(old)
        except ValueError:
            if len(chain) > WALK_LIMIT:
                refusals.append(Refusal("E06", label, over))
            else:
                refusals.append(Refusal("E04", label, f"{_short(new)} is not a fast-forward of "
                                                      f"{_short(old)} along the first parent; a "
                                                      f"line only grows — rebase onto {label} "
                                                      f"or push a new cand/<name>"))
            return done()
    if k > WALK_LIMIT:
        refusals.append(Refusal("E06", label, over))
        return done()
    new_shas = chain[:k]
    base = chain[k] if k < len(chain) else None

    # Step 6: the new commits, read once; the tip too when nothing is new.
    read = new_shas or chain[:1]
    objects, parsed = push.commits(read)
    judged: dict[str, tuple[str, dict[str, str], bool]] = {}
    for sha in reversed(read):
        kind_, trailers, found = trailer_problems(sha, objects[sha].message, parsed[sha])
        refusals += found
        judged[sha] = (kind_, trailers, bool(found))
    for sha in reversed(new_shas):
        kind_, trailers, _refused = judged[sha]
        commits.append(Commit(sha, kind_, trailers.get("Nope-Name", "") if kind_ else "",
                              tuple(sorted(trailers.items())), objects[sha].tree,
                              objects[sha].parents))

    # Step 8: the tip — a working commit, not one already refused for what
    # its trailers say.
    tip_kind, _tip_trailers, tip_refused = judged[chain[0]]
    if tip_kind == "" and not tip_refused:
        refusals.append(Refusal("E24", label, f"the tip {_short(chain[0])} is a working commit; "
                                              f"a push ends on a candidate or an evidence "
                                              f"commit"))

    # Step 7: the previous claiming commit, and the names below.
    line = push.line_below(base)

    # Step 9: each new claiming commit, oldest first.
    for commit in commits:
        if commit.kind not in ("candidate", "evidence"):
            continue
        found, noted = _judge_claiming(push, commit, dict(commit.trailers), line)
        refusals += found
        notes += noted

    # Step 10: the tip's nearest candidate.
    spec_tree, accepted = line.spec_tree, line.accepted
    if not spec_tree and line.candidate is not None and not refusals:
        spec_id, _problems = spec_tree_id(push.repo, line.candidate[0], objects=push.objects)
        if spec_id:
            spec_tree, accepted = spec_id, spec_id in push.spec_trees()
    return done(spec_tree, accepted)


def _judge_claiming(push: _Push, commit: Commit, trailers: dict[str, str],
                    line: _Line) -> tuple[list[Refusal], list[Note]]:
    """Step 9 for one candidate or evidence commit: the tree rules, the
    materialised copy and everything that runs on it, the spec, the diff
    against the previous claiming commit's tree, the entries, the state file.
    Refusals come back in rule order."""
    sha, kind = commit.sha, commit.kind
    where = _where(sha, kind, commit.name)
    problems: list[str] = []
    noted: list[str] = []
    tree = commit.tree
    top = push.ask("tree_entries", vcs.tree_entries, push.repo, tree)
    listing = push.ask("tree_listing", vcs.tree_listing, push.repo, tree)
    changes = push.ask("tree_diff", vcs.tree_diff, push.repo, line.prev_tree, tree)
    paths = {entry.path: entry for entry in listing}

    # E22, E23: the trigger and the name, against this tree and this line.
    trigger = trailers.get("Nope-Trigger")
    if trigger and TRIGGER.match(trigger) and f"{VERDICTS_PREFIX}{trigger}.json" not in paths:
        problems.append(f"E22 Nope-Trigger names {trigger}, which this tree does not hold under "
                        f"{VERDICTS_PREFIX}; name an entry the tree holds")
    name = commit.name
    if kind == "candidate":
        if name and name in line.names:
            problems.append(f"E23 Nope-Name {name} is already the name of "
                            f"{_short(line.names[name])} on this line; a name is given once "
                            f"per line")
        elif name:
            line.names[name] = sha
        line.candidate = (sha, name)
    elif kind == "evidence" and name:
        cname = line.candidate[1] if line.candidate else "none"
        if cname != name:
            problems.append(f"E23 Nope-Name {name} is not its candidate's ({cname}); drop it "
                            f"or match it")

    # E30 first (the bound on what is read), then the copy.
    tmp, found = materialise(push.repo, listing, objects=push.objects)
    try:
        problems += found
        attributes: dict[str, bytes] = {}
        if not found:
            for entry in listing:
                if entry.path.split("/")[-1] == ".gitattributes" and _parts_ok(entry.path) \
                        and entry.type == "blob" and entry.mode != "120000":
                    with open(os.path.join(tmp, *entry.path.split("/")), "rb") as fh:
                        attributes[entry.path] = fh.read()
        problems += tree_problems(listing, top, attributes)
        for entry in top:
            if entry.type == "blob" and entry.path in store.RECORD_DIRS:
                problems.append(f"E32 {entry.path}: expected a directory of {entry.path} "
                                f"records, found a file")
        # Every materialised JSON file's nesting, before any reader opens one
        # (`nesting_problem`): a file too deep is named under its own rule and,
        # as under E30, the copy is read no further — the readers would recurse
        # to the interpreter's limit on it.
        deep: list[str] = []
        if not found:
            for entry in _materialise_set(listing):
                if entry.path.endswith(".json") and _parts_ok(entry.path):
                    with open(os.path.join(tmp, *entry.path.split("/")), "rb") as fh:
                        why = nesting_problem(fh.read())
                    if why:
                        deep.append(_deep_file_rule(entry.path, why))
        problems += deep
        if found or deep:
            line.prev_tree = tree
            return _refusals(where, _in_rule_order(problems)), _notes(where, noted)
        if PROJECT_REL not in paths:
            problems.append(f"E31 not a nopekit project ({PROJECT_REL} is missing); run nopekit "
                            f"init and commit what it writes")
        problems += record_problems(tmp, listing=listing)
        ledger = None
        try:
            ledger = store.load(tmp, model_prose=modelio.static_param_prose)
        except NopekitError:
            ledger = None

        # E33: the sealed kinds, against the previous tree's blobs.
        old_blobs: dict[str, bytes] = {}
        wanted = [c.old_oid for c in changes if c.status == "M" and (
            c.path == STATE_REL or _sealed_path(c.path))]
        for chunk in _chunks(sorted(set(wanted))):
            old_blobs.update(push.ask("blobs", vcs.blobs, push.repo, list(chunk)))
        for change in changes:
            record_kind = _sealed_path(change.path)
            if not record_kind or change.status not in ("M", "D"):
                continue
            label = change.path
            if change.status == "D":
                problems += sealed_problems(label, None, None)
                continue
            prev_dir = os.path.join(tmp, "prev")
            prev_path = os.path.join(prev_dir, *change.path.split("/"))
            os.makedirs(os.path.dirname(prev_path), exist_ok=True)
            with open(prev_path, "wb") as fh:
                fh.write(old_blobs[change.old_oid])
            try:
                before = store.read_record(prev_path, record_kind)
                after = store.read_record(os.path.join(tmp, *change.path.split("/")), record_kind)
            except (NopekitError, RecursionError):
                continue   # E32 said why; the previous blob landed under no depth scan
            problems += sealed_problems(label, before, after)

        # E34, E35, N01: the spec (a candidate's).
        if kind == "candidate":
            spec_id, spec_words = _spec_tree(top, listing, push.algo)
            problems += [f"E34 {words}" for words in spec_words]
            if spec_id and PACKS_REL in paths:
                with open(os.path.join(tmp, *PACKS_REL.split("/")), "rb") as fh:
                    pin_words, pins = packs_problems(fh.read())
                problems += [f"E34 {PACKS_REL}: {words}" for words in pin_words]
                if ledger is not None and not pin_words:
                    listed = list(ledger.meta.packs or [])
                    for pack in pins:
                        if pack not in listed:
                            problems.append(f"E35 {PACKS_REL} pins {pack}, which "
                                            f"{PROJECT_REL}'s packs does not list; add it with "
                                            f"nopekit packs add or drop the pin")
                    for pack in listed:
                        if pack not in pins and not os.path.isdir(
                                os.path.join(packs.BUNDLED_PACKS, pack)):
                            problems.append(f"E35 {PROJECT_REL} lists {pack}, which is neither "
                                            f"bundled nor pinned in {PACKS_REL}; pin it")
            if spec_id:
                line.spec_tree = spec_id
                line.accepted = spec_id in push.spec_trees()
                if not line.accepted:
                    noted.append(f"N01 awaits acceptance; its gates (spec tree {spec_id[:12]}) "
                                 f"are on no commit of spec")

        # E36, E37: the diff against the previous claiming commit.
        problems += diff_problems(kind, changes)

        # E38, E39, N02..N04: every added entry and trace file.
        added = [c.path for c in changes if c.status == "A" and c.path.startswith(EVIDENCE_ADDS)]
        hashes = _hashes_for(push, listing, tmp, added)
        found_entries, entry_notes = entry_problems(tmp, added, ledger=ledger, listing=listing,
                                                    hashes=hashes)
        problems += found_entries
        noted += entry_notes

        # E40..E44: the state file.
        state_change = next((c for c in changes if c.path == STATE_REL), None)
        if line.prev_round is None and state_change is not None \
                and state_change.status == "M":
            try:
                line.prev_round = rounds.last_committed_round(old_blobs.get(state_change.old_oid))
            except RecursionError:          # a previous blob that landed under no depth scan
                line.prev_round = None
        problems += state_file_problems(tmp, line.prev_round, state_change is not None)
        if STATE_REL in paths:
            # The round this tree carries forward, read as `rounds.last_committed_round`
            # reads HEAD's copy for the commit's note: a number, never a status.
            with open(os.path.join(tmp, *STATE_REL.split("/")), "rb") as fh:
                carried = rounds.last_committed_round(fh.read())
            if carried is not None:
                line.prev_round = carried
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    line.prev_tree = tree
    return _refusals(where, _in_rule_order(problems)), _notes(where, noted)


def _sealed_path(path: str) -> str:
    """The record kind when ``path`` is a top-level sealed record file, else ``""``."""
    parts = path.split("/")
    if len(parts) == 2 and parts[0] in SEALED_DIRS and parts[1].endswith(".json") \
            and not parts[1].startswith("."):
        return parts[0]
    return ""


def _in_rule_order(problems: list[str]) -> list[str]:
    return sorted(problems, key=lambda p: p.partition(" ")[0])


def _hashes_for(push: _Push, listing: Sequence[vcs.TreeEntry], tmp: str,
                added: Sequence[str]) -> dict[str, str]:
    """sha256 of the tree's blob at every root-relative path an added entry
    says it read (N03's comparison), fetched in batches and bounded by
    `MATERIALISE_MAX_BYTES` in all — a note's cost, never a refusal's."""
    wanted: set[str] = set()
    for path in added:
        if not path.startswith(VERDICTS_PREFIX):
            continue
        full = os.path.join(tmp, *path.split("/"))
        try:
            with open(full, "rb") as fh:
                doc = json.loads(fh.read().decode("utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        if not isinstance(doc, dict):
            continue
        tables = [(doc.get("reads") or {}).get("files") if isinstance(doc.get("reads"), dict)
                  else None,
                  (doc.get("fixture") or {}).get("files") if isinstance(doc.get("fixture"), dict)
                  else None,
                  ((doc.get("static_parts") or {}).get("selftest") or {}).get("files")
                  if isinstance(doc.get("static_parts"), dict)
                  and isinstance((doc.get("static_parts") or {}).get("selftest"), dict)
                  else None]
        for table in tables:
            if isinstance(table, dict):
                wanted.update(k for k, v in table.items() if isinstance(k, str)
                              and isinstance(v, str))
    by_path = {entry.path: entry for entry in listing if entry.type == "blob"
               and entry.mode != "120000"}
    chosen: list[vcs.TreeEntry] = []
    total = 0
    for rel in sorted(wanted):
        entry = by_path.get(rel)
        if entry is None:
            continue
        total += entry.size or 0
        if total > MATERIALISE_MAX_BYTES:
            break
        chosen.append(entry)
    hashes: dict[str, str] = {}
    for chunk in _chunks(chosen):
        found = push.ask("blobs", vcs.blobs, push.repo, sorted({e.oid for e in chunk}))
        for entry in chunk:
            hashes[entry.path] = _blob_sha256(found[entry.oid])
    return hashes


def validate_push(repo: str, updates: Sequence[tuple[str, str, str]], *,
                  objects: vcs.ObjectDirs | None = None, pusher: str = "") -> Report:
    """Judge a push: ``updates`` are ``(old, new, ref)`` as pre-receive hands
    them, in git's order; ``objects`` the quarantine and the main store inside
    a hook (``None`` outside one); ``pusher`` what the hook was told, recorded.
    Every line's refusals are reported; a pre-receive hook refuses the whole
    push when any line is refused. Raises `NopekitError` only for a malformed
    argument — a sha that is not 40/64 hex or all zeros, a ref not under
    ``refs/`` (git never hands a hook a malformed line, so this is the
    caller's bug) — and never on content: any ``None`` from `vcs` ends the walk
    with ``Report.unanswered`` set (E07, exit 2). A line whose two ids are both
    zero IS one git hands a hook — the deletion of a ref that does not exist
    (``git push origin :refs/heads/draft/x``, verified on 2.34.1, which warns
    "Deleting a non-existent ref" and runs the hook with no quarantine) — and
    is judged as a deletion: accepted on ``draft/*``, E03 on a line or
    ``main``, E01/E02 elsewhere. Review of milestone 0: it was refused as the
    caller's bug, so an honest no-op push declined with exit 2."""
    checked: list[tuple[str, str, str]] = []
    for update in updates:
        try:
            old, new, ref = (str(x) for x in update)
        except (TypeError, ValueError):
            raise NopekitError(f"not a pre-receive line: {update!r}") from None
        for sha in (old, new):
            # `vcs._HEX_OID` and `vcs._ref_arg` are the edge's own checks,
            # reached here as privates; milestone 1 promotes them with the
            # four `verdicts` and two `store` names `receive` also reaches.
            if not (vcs._HEX_OID.match(sha) or is_zero(sha)):
                raise NopekitError(f"{sha!r} is not an object id (40 or 64 hex, or all zeros)")
        if len(old) != len(new):
            raise NopekitError(f"{ref}: {old!r} and {new!r} are ids of different hashes")
        if not ref.startswith("refs/") or not vcs._ref_arg(ref):
            raise NopekitError(f"{ref!r} is not a full ref (refs/heads/cand/<name>)")
        checked.append((old, new, ref))
    refs: list[RefReport] = []
    try:
        push = _Push(repo, objects)
        for at, (old, new, ref) in enumerate(checked):
            earlier = [r for _o, n, r in checked[:at] if not is_zero(n)]
            refs.append(_ref_problems(push, old, new, ref, earlier))
    except _Unanswered as exc:
        return Report(tuple(refs), exc.line, pusher)
    return Report(tuple(refs), "", pusher)
