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
F.Cu`. `schematic_parity` is filled only with `--schematic-parity` (`pcb.parity`).

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
symbol's copy in the schematic with the library's (`lib_symbol_mismatch`).

## Parity: what `pcb.parity` runs

    kicad-cli pcb drc --schematic-parity --severity-all --exit-code-violations ...

KiCad loads the root schematic of the board's project, the board's name with
`.kicad_sch`, beside it; there is no flag to name another. It matches footprints to
symbols and lists, under `schematic_parity`: `missing_footprint`, `extra_footprint`,
`footprint_symbol_mismatch` (the footprint the symbol names is not the one on the
board; a board whose footprints carry no library name, as `pcbnew.FootprintLoad`
leaves them, fails here), `net_conflict` (a pad on a net the schematic does not
give it), and value differences. They are warnings, so the run is at every
severity. The gate counts `schematic_parity` only: the same run's copper findings
are `pcb.drc`'s. `--exit-code-violations` counts parity items too (exit 5).

## Waivers

KiCad stores DRC exclusions in the `.kicad_pro` (`board.design_settings.drc_exclusions`)
and ERC exclusions beside them (`erc.erc_exclusions`), each naming the violation and
the items. `kicad-cli` honours them unless `--severity-exclusions` is given. That
makes a waiver a reviewable line in a tracked file. The gates copy the `.kicad_pro`,
any `.kicad_dru` and the library tables beside the board into their stage, so the
rules, exclusions and libraries they ran under are the project's.

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
  nickname added, or the field emptied, gave 114 `footprint_symbol_mismatch`.
  A footprint name with no nickname reads as `footprint_link_issues` in ERC (a
  warning): the generator writes nicknames, or a person waives that type in the
  `.kicad_pro`.
- Every open schematic pin's net is named `unconnected-(<ref>-<pin>-Pad<n>)`, even
  under a no-connect flag, and parity reports the netless pad under it as
  `net_conflict`, "Pad missing net given by schematic (unconnected-(...))".
  `pcb.parity` counts that as agreement when every item is a pad the board gives
  no net (matched by id) and the words hold `(unconnected-(`.

## Cost and opacity

About 0.7 s for the baseline board, about 2.3 s for a 4-layer 115-footprint board.
The subprocess reads files the trace cannot see, so the gates are opaque: never
served from the cache, re-run on every check where kicad-cli exists, skipped where
it does not.

## Versions

Written against kicad-cli 9.0.9. KiCad 8 has the same `pcb drc` flags. KiCad 7's
kicad-cli has no `pcb drc` at all, so these gates error there rather than skip:
upgrade.
