# Driving kicad-cli: what the tier-1 gates run

`pcb.drc` and `pcb.unconnected` run, on a copy of the board staged under the gate's
out directory:

    kicad-cli pcb drc <--severity-all|--severity-error|--severity-warning> \
        --exit-code-violations --format json --units mm -o drc.json <board>.kicad_pcb

## Why each flag

- `--exit-code-violations`: without it kicad-cli exits 0 whatever it found, and a
  script that forgets to read the report passes everything. With it: **0** nothing
  found, **5** something found. Any other code is kicad-cli failing to run (a file
  it cannot load, a crash): the gate errors, never passes.
- `--severity-all` (the default for `pcb.drc`): a run at error severity cannot see
  a warning. Silk off the board edge, a reference over a pad, text too small for
  the fab's printer: all warnings, all printed.
- `--format json`: the report the gate reads for the count and the kinds. It must be
  the report this run wrote: the gate deletes any old one first.
- `--units mm`: positions in the report are in the board's own unit.

`pcb.unconnected` runs at `--severity-error`: an unconnected item is always an error,
and a warning elsewhere on the board is not its business.

## The report

    {"violations": [...], "unconnected_items": [...], "schematic_parity": [...],
     "kicad_version": "9.0.9", "source": "...", "date": "...", ...}

Each item: `type` (`clearance`, `shorting_items`, `silk_edge_clearance`,
`courtyards_overlap`, `unconnected_items`, ...), `severity`, `description`, and the
`items` it names, each with a position and a description such as `Track [DM] on
F.Cu`. An item a person excluded in KiCad also carries `"excluded": true` and the
exclusion's `comment` (below). `schematic_parity` is filled only with
`--schematic-parity` (`pcb.parity`).

The verdict's line gives each severity's count, then the kinds, errors first and
the most numerous first within one severity, five kinds and how many more:
`334 finding(s), 16 error, 318 warning: 5x shorting_items (error), ...`. (Before
the holdout run it named the four most numerous kinds, and 16 errors under 318
warnings were in no word of it.) It ends with the release `kicad_version` names
(below).

## ERC: what `pcb.erc` runs

    kicad-cli sch erc --severity-all --exit-code-violations --format json \
        --units mm -o erc.json <project>.kicad_sch

On a staged copy of the root schematic, every sheet it names (followed through
each sheet's `Sheetfile` property, which must stay inside the schematic's
directory), the `.kicad_pro` and the project's library tables. Exit codes as for
DRC: 0 nothing, 5 something, anything else an error. The report:

    {"sheets": [{"path": "/", "uuid_path": "/...", "violations": [...]}], ...}

Each violation has `type` (`pin_not_connected`, `global_label_dangling`,
`power_pin_not_driven`, `pin_to_pin`, `lib_symbol_issues`, ...), `severity`,
`description` and `items`. `lib_symbol_issues` ("the current configuration does not
include the symbol library") depends on the machine unless the project carries its
own `sym-lib-table`: keep one. With the libraries found, ERC also compares each
symbol's copy in the schematic with the library's (`lib_symbol_mismatch`), and DRC
each footprint's (`lib_footprint_mismatch`): against the host's own library when
the table names it through `${KICADn_...}`, so keep the libraries a design uses in
the project, as the baseline does (`selftest/assets/lib/`, its tables naming
`${KIPRJMOD}/lib/...`).

## The libraries a table names

The stage takes each library a table names through `${KIPRJMOD}` that is inside
the project, at its place relative to the board (a library one directory up puts
the staged board one directory down), so `${KIPRJMOD}/library/...` and
`${KIPRJMOD}/../kicad-library/...` resolve in the stage as in the project. A run
takes the libraries its findings read: the footprint table's for `pcb.drc` and
`pcb.fab_rules`, both tables' for `pcb.erc`. An entry with nothing at its path, or
outside the project, is named in the verdict ("not staged with the board"), and
KiCad then reports it not found. (What slipped through, in the holdout run: the
stage copied the tables alone, and ERC called a design's own libraries "not
found".)

## Parity: what `pcb.parity` runs

    kicad-cli pcb drc --schematic-parity --severity-all --exit-code-violations ...

KiCad loads the root schematic of the board's project, the board's name with
`.kicad_sch`, beside it; there is no flag to name another. It matches footprints to
symbols and lists, under `schematic_parity`, the types KiCad 9.0.9 and 10.0.8
report (read off both builds). Every rule `pcb.parity` applies to them is in this
one table:

| Item | What differs | `pcb.parity` |
|---|---|---|
| `missing_footprint` | a part in the schematic has no footprint | counted |
| `extra_footprint` | a footprint has no part in the schematic | counted |
| `duplicate_footprints` | two footprints for one part | counted |
| `footprint_symbol_mismatch` | a value, the footprint (a board whose footprints carry no library name, as `pcbnew.FootprintLoad` leaves them, fails here) or a fitting attribute ("Exclude from bill of materials" settings differ) | counted |
| `net_conflict` | a pad on a net the schematic does not give it | counted |
| `extra_footprint` or `duplicate_footprints` on board-only drawing | a mounting hole, a logo or an outline not marked "Not in schematic" (below) | counted, and said apart: "k of n: board-only footprint(s) not marked 'Not in schematic' (...), mark them so in KiCad", with "no part, value, footprint or pad net differs" when it is every difference |
| `net_conflict` on an open pin | "Pad missing net given by schematic (unconnected-(...))" on a pad the board gives no net (below) | agreement, not counted, said ("open pin(s) sit on pads with no net") |
| `footprint_symbol_field_mismatch` | a symbol field, "Missing symbol field 'manf#' in footprint" (KiCad 10 only): BOM data | said apart, not counted |
| `footprint_filters_mismatch` | a footprint outside its symbol's footprint filters: a library rule | said apart, not counted |
| any item with `"excluded": true` | a person excluded it in KiCad ("Waivers" below) | said apart with the first comment, not counted |
| a type not in this table | a check a later KiCad added | named, not counted; with nothing counted beside it the gate errors, since it cannot say whether it is a difference in what is built |

Board-only drawing is a footprint with no numbered pad that no bill of materials
takes (no reference, an unannotated one such as `REF**` or `G***`, or
`exclude_from_bom`): a mounting hole, a logo, an outline not marked "Not in
schematic". A footprint with a numbered pad, one a BOM would take, and one the
report names by an id the board does not keep, are no drawing and count as any
other. KiCad's remedy is the footprint's "Not in schematic" attribute
(`board_only`), one click; without it, KiCad's own "Update PCB from Schematic"
with "delete extra footprints" removes the footprint. (What slipped through, in
the holdout run: under KiCad 10.0.8, 32 of a board's 33 parity items were BOM
fields, and on another board all 16 were mounting holes, each counted as a
difference in words that did not show no part differed. The fix that followed
left the mounting holes out of the count, and round 2 of the holdout read that
as a regression: round 1 had confirmed each such footprint a real, minor defect,
three refuters of three on two boards, and asked only for the split line. BOM
fields stay out of the count on round 1's own confirmed finding: they move no
part, value, footprint or net.)

The gate quotes KiCad's own words for the first three counted differences in
what is built, board-only drawing after them, as they are: KiCad 9.0.9's for a
value reads "Value (C7) doesn't match symbol value (TBD)", where the first
parentheses hold the reference and the second the board's value, and the
schematic's value is not printed (seen in the holdout run). They are warnings,
so the run is at every severity. The gate counts `schematic_parity` only: the
same run's copper findings are `pcb.drc`'s. `--exit-code-violations` counts
parity items too (exit 5).

## A stored zone fill, and KiCad's refill

kicad-cli's DRC judges a zone, a teardrop among them, by the fill the file
stores; a person's DRC run in KiCad refills first. On a board with a zone (a pour
or a teardrop, on the board or in a footprint; a rule area fills nothing),
`pcb.drc` and `pcb.fab_rules` run their DRC twice on the same stage: on the board
as stored, and on a copy refilled under the project's own `.kicad_pro` and
`.kicad_dru` (in `nopekit-refill/` of the stage), never under the fab's rules
`pcb.fab_rules` writes. The copy is refilled by:

- kicad-cli 10: `kicad-cli pcb drc --refill-zones --save-board` on the copy (it
  saves the copy in KiCad 10's format, which kicad-cli 10 then reads);
- kicad-cli 9.0.9, which has no refill: KiCad's `pcbnew` in a child process
  (`gates/_keepout_fill.py`, `ZONE_FILLER`, as the keep-out's refill);
- neither (kicad-cli 9 and no `pcbnew` in nopekit's Python): no refill; the
  stored fill alone is judged, and the line says so.

A refill side that skips keeps the stored run's findings: the refill or its run
past `pcb_drc_timeout_s`, a kicad-cli that does not launch, or a refilled copy
saved in a format the kicad-cli on PATH does not read (a `pcbnew` newer than
kicad-cli 9). They fail, and the line says "the stored zone fill only: the
refill side skipped (why), so what a refill pours is not judged". The skip
stands only where the stored run counts nothing; an error stands always. (What
slipped through, in review of holdout round 3: the skip took the gate's place,
so a short the stored run counted read Skipped, "nothing was shown".)

A finding is the same in both runs when its type and the ids of its items are:
a refill keeps every id the file holds once (measured: the 12 findings a refill
left on a holdout board kept theirs), and the words move (the actual gap). An
item with no id, a bad one or one the file holds twice takes a new random id at
each load, and the refilled copy keeps the one KiCad saved; KiCad 10.0.8 writes
every teardrop with no id. A finding that names one is matched by its type and
its other items' ids alone (loosely), counted as often as the run that lists it
more, and said apart ("k of n name an item KiCad gives a new id at each load"):
the extra findings one run lists are at least that many the other does not
show, so a count said apart is the least it can be, never more. (What slipped
through, in review of holdout round 3: on the baseline with no pad ids, an
unfilled pour and a via on a pad, kicad-cli 9.0.9 with pcbnew 9.0.9, the 3
findings read 5, 2 of them "only on the stored zone fill" and 2 "only after
KiCad's refill". Rejected: such a finding never called either copy's own, which
took the remedy from the 20 stale teardrops below.) Each finding counts once
over both. The line says apart how many only the stored fill shows ("k of
n only on the stored zone fill (18x clearance, 2x hole_clearance), which KiCad's
refill clears: refill the zones and save the board, since a fab plots the
stored fill") and how many only the refill shows (the stored fill is out of
date there). Measured:

| Board | stored | refilled | counted |
|---|---|---|---|
| a KiCad 10 holdout board, kicad-cli 10.0.8 | 32, 2 of them excluded in the project: 30 counted (21 errors) | 12, the same 2 excluded: 10 counted (1 error) | 30: 20 only on the stored fill, every one a teardrop KiCad 10 writes with no id, so matched loosely |
| the baseline with a GND fill stored over three pads and two tracks (tests/test_pcb_kicad.py), 9.0.9 with pcbnew 9.0.9, and 10.0.8 | 9 | 0 | 9, all only on the stored fill |
| the same with a thermal-relief pad connection | 9 | 1 (`starved_thermal`) | 10: 1 only after the refill |

Why both count: the stored fill is what a fab plots. `kicad-cli pcb export
gerbers` 9.0.9 has no refill, and 10.0.8 refills only with `--check-zones` (on
the holdout board the two plots of B.Cu differ); one of that board's stored
teardrops sat on another net's pad at 0.0000 mm. The remedy is KiCad's "Fill All
Zones" and a save, after which the two runs agree. (What slipped through,
holdout round 2: the stored run alone counted 20 of pcb.drc's 21 errors and 13
of pcb.fab_rules' findings from stored teardrops a refill clears, and the line
sent a person to fix each clearance; and a pour moved under a track, not
refilled, read clean.) Rejected: the refilled run alone (the short a fab would
plot passes); the stored-only findings said apart and not counted (the same);
refilling under the stage's rules (the fab's clearance would pour less copper
than the person's refill and hide a gap). A board with no zone runs once.

## Waivers

KiCad stores DRC exclusions in the `.kicad_pro` (`board.design_settings.drc_exclusions`)
and ERC exclusions beside them (`erc.erc_exclusions`), each a pair of a key and a
comment. A DRC key is `<type>|<x>|<y>|<item uuid>|<other item uuid>`, the
position the marker's own, in nanometres (an item with no other names
`00000000-0000-0000-0000-000000000000`). That makes a waiver a reviewable line in
a tracked file. kicad-cli 9.0.9 and 10.0.8 both still list an excluded finding,
with `"excluded": true` and the comment, and both exit 5 when it is the only one
(measured: a dangling via on the baseline, excluded); a DRC exclusion that matches
nothing is dropped. kicad-cli 9.0.9's ERC is not the same: an ERC exclusion whose
key matches no finding is listed as an excluded item of its own, beside the live
finding it was meant for (measured with a key in the DRC form on a dangling
label), so a stale ERC waiver reads as one more excluded finding, and the finding
still counts. So the gates count what no exclusion waives, say the excluded
apart, by kind, with the first comment, and hold the exit code to the report with
the excluded findings in it. (What slipped through, in the holdout run: they said
exclusions are honoured and counted every item, so a design's two waived
courtyard overlaps would have failed it.) `pcb.fab_rules` counts a fab finding the
project excludes: the fab's minimum is not the project's to waive. The gates copy
the `.kicad_pro`, any `.kicad_dru`, the library tables and the project libraries
beside the board into their stage, so the rules, exclusions and libraries they ran
under are the project's.

A `.kicad_dru` rule can also relax a constraint. Read its `condition` before you
trust it: a rule written for one connector's shield pads with the condition
`A.NetName == 'GND'` relaxes every ground pad on the board.

## Custom rules: the probes `pcb.drc` adds

KiCad 9.0.9 evaluates a rule it cannot read in silence: a misspelled property
(`A.NetNmae`) selects nothing, `!(A.Pin_Nmae == 'zz')` selects everything, and one
syntax error (an unknown constraint, a missing paren) drops the whole file. Each
reads as a clean DRC. So when the board has a `.kicad_dru`, `pcb.drc` stages it
again (`pcb_drc_probe/`) with probes appended to the project's own text:

    (rule nopekit_probe_<i> [its layer] [its condition] (constraint assertion "false"))
    (rule nopekit_token_<j> (condition "A.<Prop> == A.<Prop>") (constraint assertion "false"))

An assertion fires on every item its condition selects, and KiCad prints the rule
name in the finding (`Assertion failure (rule 'nopekit_probe_0')`), untranslated.
A probe at 0 in the combined run is run again alone, because several assertion
rules on one item mask each other; when no probe fires at all, a canary that
selects everything tells a dropped file from dead rules. KiCad evaluates a
one-item assertion with no B, so a `B.` property is probed as `A.`, and a rule
whose condition reads B is probed on its A side: the top-level `&&` terms that
read A alone, which every pair it selects must meet. `A.NetName == 'VBUSS' &&
B.NetName == 'GND'` is probed as `A.NetName == 'VBUSS'`, and is dead. A rule with
no such term is never called dead, only "not shown live". Rejected:
`disallow` probes (a later one masked an earlier one fully, 12 against 0) and
probes that restate the rule's constraint at an impossible bound (11 s, and an
item in the board's middle was missed).

## A schematic written from a board

`pcb.schematic_from_board` (PACK.md) writes the schematic `pcb.erc` and
`pcb.parity` need on a board a script generated. Two things KiCad 9.0.9 does with
one, measured on a 4-layer board:

- The Footprint field must be the board's footprint name exactly: a library
  nickname added, or the field emptied, gave a `footprint_symbol_mismatch` for
  every part.
  A footprint name with no nickname reads as `footprint_link_issues` in ERC (a
  warning): the generator writes nicknames, or a person waives that type in the
  `.kicad_pro`.
- Every open schematic pin's net is named `unconnected-(<ref>-<pin>-Pad<n>)`, even
  under a no-connect flag, and parity reports the netless pad under it as
  `net_conflict`, "Pad missing net given by schematic (unconnected-(...))".
  `pcb.parity` counts that as agreement when every item is a pad the board gives
  no net (matched by id) and the words hold `(unconnected-(`.

## Cost and opacity

About 0.7 s for the baseline board, about 2.3 s for a 4-layer board of about a
hundred footprints.
The subprocess reads files the trace cannot see, so the gates are opaque: never
served from the cache, re-run on every check where kicad-cli exists, skipped where
it does not.

## Versions

Measured on kicad-cli 9.0.9 and 10.0.8 (the kicad/kicad:10.0 image): one record of
each in `gates/kicad-cli/<release>.json`, with the newest board and schematic
format it reads and what was shown on it. KiCad 8 has the same `pcb drc` flags.
KiCad 7's kicad-cli has no `pcb drc` at all, so these gates error there rather
than skip: upgrade.

- **The release is part of what a gate read.** After each run the gate asks for
  the record of the release its report names (`kicad_version`), by that file's
  path, found or not, and the spine keys a path a gate asked about. A control run
  under 9.0.9 and one under 10.0.8 are two qualifications, never "two outcomes
  for identical inputs". (What slipped through, in the holdout run: they were
  one, and `pcb.drc` read unqualified on every KiCad 10 host.) The verdict's line
  ends with the release, and a release with no record is said: `kicad-cli 10.0.9
  (no record of it in this pack: measured 9.0.9, 10.0.8)`. It still runs: the
  exit code is KiCad's verdict, and its report must agree with it.
- **A file newer than the release reads.** kicad-cli 9.0.9 prints "Failed to load
  board" (or "schematic") and exits 3 on a file KiCad 10 saved. After such a
  failure the gate asks `kicad-cli version --format plain`, compares the file's
  `(version N)` with what that release (or the newest record of its series) reads,
  and skips, naming the release: a tool limit, not a defect, as a missing tool is.
  Any other failure stays an error.
- **The keep-out runs on a measured pair.** `pcb.antenna_keepout` hands one board
  to pcbnew (the refill) and to kicad-cli (the DRC), and ties each finding to its
  part by the name kicad-cli writes for the area. It asks both releases before it
  stages a board, and runs only on a pair a record in
  `gates/pcbnew-pairs/<release>.json` names (`kicad_cli`): kicad-cli 9.0.9 with
  pcbnew 9.0.9. Any other pair skips, naming both. (What slipped through, in the
  merge of the holdout fixes: kicad-cli 10.0.8 writes "Items not allowed" for a track in a keep-out and names no area,
  so beside pcbnew 9.0.9, and beside pcbnew 10.0.8 in the image, a board with two
  tracks in the keep-out read pass at 0. The selftest read qualified: its control
  is a zone fill, whose words name the area.) An exact pair, not a series: a patch
  release can move the words, and the words are what the gate reads.
