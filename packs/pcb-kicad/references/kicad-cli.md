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
symbols and lists, under `schematic_parity` (the types KiCad 9.0.9 and 10.0.8
report, read off both builds):

| Type | What differs | `pcb.parity` |
|---|---|---|
| `missing_footprint` | a part in the schematic has no footprint | counted |
| `extra_footprint` | a footprint has no part in the schematic | counted, unless board-only drawing |
| `duplicate_footprints` | two footprints for one part | counted, unless board-only drawing |
| `footprint_symbol_mismatch` | a value, the footprint (a board whose footprints carry no library name, as `pcbnew.FootprintLoad` leaves them, fails here) or a fitting attribute ("Exclude from bill of materials" settings differ) | counted |
| `net_conflict` | a pad on a net the schematic does not give it | counted, unless an open pin on a pad with no net |
| `footprint_symbol_field_mismatch` | a symbol field, "Missing symbol field 'manf#' in footprint" (KiCad 10 only) | said apart |
| `footprint_filters_mismatch` | a footprint outside its symbol's footprint filters | said apart |

Board-only drawing is a footprint with no numbered pad that no bill of materials
takes (no reference, an unannotated one such as `REF**` or `G***`, or
`exclude_from_bom`): a mounting hole, a logo, an outline not marked "Not in
schematic". A footprint with a numbered pad, one a BOM would take, and one the
report names by an id the board does not keep, all count. A type not in the table
is named; with nothing counted beside it the gate errors, since it cannot say
whether a new check of a later KiCad is a difference in what is built. The gate
quotes KiCad's own words for the first three counted items, as they are: KiCad
9.0.9's for a value reads "Value (C7) doesn't match symbol value (TBD)", where the
first parentheses hold the reference and the second the board's value, and the
schematic's value is not printed (seen in the holdout run). (What
slipped through, in the holdout run: under KiCad 10.0.8, 32 of a board's 33 parity
items were BOM fields, and on another board all 16 were mounting holes, each
counted as a difference.) They are warnings, so the run is at every severity. The
gate counts `schematic_parity` only: the same run's copper findings are
`pcb.drc`'s. `--exit-code-violations` counts parity items too (exit 5).

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
