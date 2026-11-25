# The antenna's keep-out: `pcb.antenna_keepout`

The rule: no copper on any layer sits in a radio module's antenna keep-out. The
gate judges what KiCad's keep-out reports: tracks and arcs, vias, the pads of
other parts and the fill of every zone, as the board is stored and as KiCad's
next refill pours it. It does not judge a copper drawing or text (below, "What
it cannot see"), and its pass says so. KiCad decides each item: its DRC and its
zone filler. This pack decides which parts have an antenna, which area is the
keep-out, and, for a vendor drawing, where it sits on the board.

## Where the rule comes from

- Espressif's ESP32-S3 Hardware Design Guidelines, "General Principles of PCB
  Layout for Modules" (docs.espressif.com, read 2026-11-24): put the module's PCB
  antenna outside the base board, with its feed point close to the board edge; if
  it cannot extend past the edge, "cut off the base board on both sides of the
  antenna and below it ... and provide a sufficiently large clearance area for
  the PCB antenna". A board cut away holds no copper on any layer. A clearance
  area left in its place is held here to the same: no track, via, pad or pour
  on any copper layer.
  The ESP32-S3-WROOM-1 datasheet (v1.8, Figure 3-1's note) points to that section
  for the keep-out zone.
- The module's footprint: a KiCad stock module carries its keep-out as a rule
  area, drawn by KiCad's librarians from the module's datasheet.
- A module certified as a modular transmitter is granted on its integration
  instructions (FCC KDB 996369), and the keep-out is one of them.

The same guide goes on past the board: "Ensure that the PCB antenna on the base
board also has a sufficiently large clearance area inside the housing. A
clearance of at least 15 mm is recommended in all directions", and "the final end
product should be tested for throughput and communication range". Neither is a
board fact: `lenses.md`, "Metal and a hand around the antenna", and a physical
claim.

## Which parts

**Found by the gate:** every footprint from KiCad's `RF_Module` library, by its
library id (`RF_Module:...`), on `pcb_board` and on each board a `pcb_antennas`
record names. KiCad 9.0.9's `RF_Module` (surveyed with this pack's reader):

| | Footprints |
|---|---|
| in the library | 57: BLE, Wi-Fi, sub-GHz, LoRa, UWB |
| with a keep-out rule area | 31 |
| whose areas all name fewer than every copper layer | 10 (ESP32-S3-WROOM-1 and ESP32-WROOM-32: F.Cu, In1.Cu, B.Cu; ATSAMR21G18-MR210UA: every layer but B.Cu; seven on F.Cu alone) |
| whose only area allows tracks or copper pour | 3 (one forbids only footprints, one is a depanelization strip) |
| with an every-layer antenna area and a small F.Cu area beside a pad | 5 |
| with no keep-out at all | 26 (the LoRa modules among them: their antenna is on a pad or a connector) |

`pcb.drc` holds a footprint's area only on the layers it names. So on the ten,
a pour on a layer the area leaves out runs under the antenna with DRC clean.

**Not found by the gate:** a module from a project's own library, a chip antenna
(KiCad's `RF_Antenna`), a GNSS module. `RF_Antenna` and `RF_GPS` draw their areas
per layer on purpose (a chip antenna's feed keeps a track on F.Cu where the inner
layers keep none), and copied to every layer they would fail a sound board. Name
each one in a record.

## Which area is the keep-out

For a footprint's own areas (`_footprint_areas`): those that forbid tracks and
copper pour. When one of them is on every copper layer, it alone is read: the
others, on F.Cu beside a pad, keep a feature clear on their own layers, and
`pcb.drc` judges them there. KiCad saves a footprint's zones in the board's own
frame, so each polygon is used as written.

A rule area's cutouts stay cutouts. KiCad saves a zone's outline as its first
polygon and each cutout as a further polygon of the same zone, and reads them
back so (the first an outline, the rest holes). The gate adds one area per
source area, with all of its polygons in that order, so KiCad reads the added
area as it reads the source. The evidence gives each area's outline
(`polygons_board_mm`) and its cutouts (`holes_board_mm`). (What slipped through,
review of P8: each polygon became a solid area of its own, so a track in a
cutout was a finding. Measured with KiCad 9.0.9: a GND track in a rule area's
cutout, plain DRC clean, the gate 1 item.)

## The records

`pcb_antennas` is an object keyed by id, each record:

| Field | |
|---|---|
| `board` | the board, from the project root (else `pcb_board`) |
| `ref`, `value` | the part; both held to the board, so a moved or swapped part stales the record |
| `keepout` | `"footprint"`, `"none"`, `{"drawing": path}` or `{"rule_area": name}` (or `{"rule_area": {"uuid": id}}`, `{"rule_area": {"covers": ref}}`) |
| `own_nets` | optional: the antenna's own nets, a printed antenna's feed and ground arm |
| `source` | where the keep-out comes from, or why there is none |

- `"footprint"`: the part's own rule area, read as a found module's is. A part
  whose footprint has no such area is an error, naming each form that fits: a
  drawing, or a board rule area by name, id or cover.
- `"none"`: no antenna on the board (one on a cable, a u.FL or an SMA). Listed,
  not judged. A board whose every radio is `"none"` skips.
- `{"rule_area": name}`: a keep-out rule area the designer drew on the board and
  named in KiCad (Rule Area Properties, Name): a chip antenna's clearance on a
  bare SoC, from its datasheet. Its polygon is copied to every copper layer, so
  draw it around the feed line where the vendor lets the feed in, or cut the
  feed out of it (a cutout stays a cutout).
- `{"rule_area": {"uuid": id}}`: one rule area by its id, named or not.
  `{"rule_area": {"covers": ref}}`: every rule area on the board whose outline
  holds a point of that part's own copper (a pad's centre, a corner of a copper
  drawing), named or not. A printed inverted-F antenna on a bare SoC is often
  kept clear by several unnamed areas, drawn around its arms, that allow tracks
  and forbid pour; each one picked is copied to every copper layer and forbids
  tracks, vias, pads and pours there. A strip beside the feed that holds none of
  the antenna's copper is not picked. (What slipped through, a holdout run: a
  rule area was found by its name alone, so such a design's areas could not be
  named, and the `"footprint"` refusal's one remedy, a drawing, could not be
  followed for a two-pad antenna.)
- `{"drawing": path}`: the vendor's drawing, below.

`own_nets` names the antenna's own nets. A printed antenna is copper: its feed
and its ground arm are tracks, and the added area forbids every track, so
without this a sound antenna reads as findings of itself. A track of those nets
that reaches the part's own pads through tracks on the pads' layers is the
antenna's own copper, and so is a via on such a track: each is listed beside the
part's own pads and not counted. A zone's fill is never the antenna's own (a
ground pour under a printed antenna is the defect the rule is for), nor is
anything beyond a via or beyond another part's pad (the search stops there, on
every layer: a ground track that runs on from the ground arm's via, or from a
capacitor's ground pad, still counts), nor a track of those nets that touches
none of the antenna's copper (a ground track that crosses under it still
counts). What is
not judged: a two-pad drawing (two pads cannot show a drawing read mirrored, so
a drawing still registers on three or more pads off one line).

An unknown key is refused, naming the nearest known one. A record whose part is
not on its board, or holds another value, is an error ("the record is for
another design").

## A vendor drawing

The file has the shape `pcb.land_pattern` reads, so one file per module part can
serve both gates, plus the keep-out:

```json
{"part": "RADIO_MODULE", "source": "the integration guide, revision, figure",
 "view": "top", "y_axis": "up",
 "pads": [{"number": "1", "function": "GND", "x": -8.75, "y": 5.26, "w": 1.5, "h": 0.9},
          {"number": "14", "function": "IO20", "x": -8.75, "y": -11.25, "w": 1.5, "h": 0.9},
          {"number": "27", "function": "IO35", "x": 8.75, "y": -11.25, "w": 1.5, "h": 0.9}],
 "keepout": {"polygon": [[-24, 6.75], [24, 6.75], [24, 27.75], [-24, 27.75]]}}
```

The numbers above only show the shape. Transcribe every number from the
vendor's document, with its revision in `source`; never from memory.

It is registered on the footprint by its numbered pads: `_land_fit(...,
pair_by="number")`, one rigid fit with no mirror, then the same fit carries the
polygon onto the board. Registration only: a land is judged by function in
`pcb.land_pattern`. It is refused, never guessed, when:

- a pad number is not on the footprint, or the footprint repeats it (a thermal
  pad's vias: register on pads whose number is on one pad alone);
- it gives fewer than three pads, or its pads sit on one line (no pad at least
  one pad limit off the line through the two furthest apart): such a set is its
  own mirror image turned a half turn, so a `y_axis` read the wrong way would put
  the keep-out on the other side and still fit;
- a pad sits past the fit's limit, half the smaller side of the smaller pad of
  its pair, as `pcb.land_pattern` holds it.

## How KiCad judges it

First, once, before any board is staged, **the pair**: the gate asks `kicad-cli
version --format plain` and the refill's child (`_keepout_fill.py --version`)
which releases they are, and runs only on a pair a record in
`gates/pcbnew-pairs/<release>.json` names: kicad-cli 9.0.9 with pcbnew 9.0.9.
Any other pair skips, naming both releases and the pairs measured. Each release is asked
for by its record's path, found or not, so the verdict's key holds both. (What
slipped through, in the merge of the holdout fixes: kicad-cli 10.0.8 writes
"Items not allowed" for a track in the added area and names no area, where 9.0.9
writes "Items not allowed (keepout area 'nopekit_antenna_AE1')"; it names the
area for a pad, a via and a zone fill. Beside pcbnew 9.0.9, and beside pcbnew
10.0.8 in the kicad/kicad:10.0 image, a chip antenna's keep-out with a GND track
and an RF track in it read pass at 0, where the 9.0.9 pair reads fail at 2. The
stored copy, which pcbnew never touches, read the same: the cause is kicad-cli's
words. The selftest read qualified under 10.0.8: its control is a zone fill.)
An exact pair, not a series: a patch release can move the words.

Then, for each board with a keep-out to judge, on a copy staged under the gate's
out directory:

1. **The refill.** `gates/_keepout_fill.py`, in a child process under nopekit's
   own Python, loads the staged board with its project and custom rules beside
   it and refills every zone with `pcbnew.ZONE_FILLER`, as "Fill All Zones" does.
   kicad-cli 9.0 has no refill before its DRC, so the file's stored fill is all it
   sees: a pour moved under the antenna and not refilled reads clean. The refill
   runs before any area is added: an area present during it knocks the pour out,
   and DRC then sees nothing (measured with pcbnew 9.0.9).
2. **Two copies.** The board as stored and the refilled board, each with every
   keep-out rule area taken out (the board's and each footprint's) and this
   gate's area added over each keep-out: every copper layer of the board's stack,
   every kind of copper forbidden, named `nopekit_antenna_<ref>`. KiCad reports
   an item once, against the first area it lies in: with the footprint's own area
   left in, a track on F.Cu in both read "keepout area" with no name, and the
   count read 0 (measured with kicad-cli 9.0.9).
3. **The DRC.** `kicad-cli pcb drc --severity-all --exit-code-violations` on
   each copy, with the project file set to report `items_not_allowed` as an
   error and to exclude nothing, and without the custom rules: a project's
   severity, exclusion or rule cannot hide a finding. An exit code that disagrees
   with the report is an error.
4. **The count.** Each `items_not_allowed` item that names one of the gate's
   areas, once over both copies (by its id and its words), with which copy found
   it. An `items_not_allowed` item that names none of them is an error, never
   passed over: the copy holds no other keep-out, so it lies in one of the gate's
   areas, and the gate cannot say whose. An item of the area's own part ("Pad 1 [GND] of U1") is its own copper,
   listed and never counted. So is a hole with no copper: the added area forbids
   pads, so KiCad reports another part's non-plated hole ("NPTH pad of H1"), and
   the refill's child asks KiCad which of the board's non-plated pads it flashes
   on no copper layer (`PAD::FlashLayer`). The stock `MountingHole_2.2mm_M2` (pad
   no wider than its drill) flashes none, and is listed
   (`holes_no_copper_left_out`); the same hole with a 3.0 mm pad has a copper ring
   on every layer, and counts (measured with pcbnew 9.0.9). A screw in a hole is
   metal near the antenna: `lenses.md`. (What slipped through, review of P8: the
   hole counted, and a mounting hole for a plastic standoff failed a sound board.
   Rejected: counting a hole on purpose, since the rule is copper; reading the
   pad's size against its drill here, since KiCad decides copper, not this pack.)

The verdict is that count against 0. A fail counts beside a part not judged;
a part not judged beside no fail is a skip. A refill or DRC past
`pcb_drc_timeout_s` is a skip; a refill that crashes is an error, and so is a
pcbnew that cannot be imported to name its release.

## What it cannot see

- Copper text and drawings on a copper layer: KiCad's keep-out does not report
  them (measured with kicad-cli 9.0.9: a filled rectangle on B.Cu and text on
  In2.Cu in the area, no finding; a filled polygon on In2.Cu with net GND under
  the antenna, which pcbnew reads as copper on its net, passes). So the pass and
  the title name tracks, vias, other parts' pads and zone fills, never "copper",
  and the pass says "copper drawings and text not judged". (What slipped
  through, review of P8: the pass read "nothing on any copper layer" over that
  polygon.)
- What goes in a hole with no copper in the keep-out: the hole is listed, not
  counted.
- A part body over the antenna with no copper in the area: the area allows
  footprints, since the rule judges copper.
- Where the keep-out sits: at the board edge or in the middle, with the board cut
  away or not.
- The ground plane a chip antenna needs beside its clearance, and the metal
  and the hand around the antenna once it is in its case.
- A footprint whose one copper keep-out guards something other than its antenna:
  it is read as the antenna's. Give a record.
- A board newer than the reader (KiCad 10): skipped, in the guard's own words,
  as the power rules skip it.
- Any board under a pair of releases it was not measured on (kicad-cli 10.0.8
  among them, beside any pcbnew): skipped, naming both. The move: run every
  keep-out test on the pair, tie a finding kicad-cli names no area for to its
  part, and add the pair to its pcbnew record.
