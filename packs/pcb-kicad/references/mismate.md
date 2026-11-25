# `pcb.mismate`: the wrong ways a connector can meet

A ribbon one size too narrow seats in a wider housing. A two-pin cell lead is
wired the other way by another vendor. A header is plugged one row off. DRC, ERC
and parity judge one board's copper, never a mating, so each of these passes
them. A bench test of a wrong fit destroys the unit. This rule finds the wrong
fit on paper, from the records and the boards, before anyone orders a board.

It is model-only. No outside source says which wrong fits count: the person
states them, and the rule is this pack's own arithmetic on the boards' pad nets.

## What it reads

- `pcb_matings`: each connector, what meets it, the nominal `map` and the
  `modes` (the wrong ways it can meet). A mating with no `modes` is an error.
  `modes: []` is a statement: nothing else meets it. Say why in `source` (a
  keyed housing, Type-C's two correct orientations, a soldered wire). A
  connector that cannot mate the wrong way takes `[]`, and the rule then
  enumerates nothing for it.
- `pcb_batteries`: each battery's terminals and taps, on its connector.
- `pcb_protectors`: each `trip` (the net beyond a fuse, PTC, eFuse or switch is
  a protected node) and each `reverse_block`.
- `pcb_usbc`: each receptacle's VBUS and GND pins. A receptacle no record names
  is read too, by its pads (Type-C's names A4, A9, B4, B9 and A1, A12, B1, B12)
  or its pin names (VBUS, GND).
- `pcb_rails`: each net a regulator or an outside supply drives, its return and
  its nominal voltage.
- The boards these records name, with the reader's checks: a board the reader
  cannot vouch for is an error, and a newer one skips.

It reads no mechanical model. A housing's key, a latch and a cable's width are
facts the person writes in `modes`.

## A mating's far side

| `b` | What it is | Modes it takes |
|---|---|---|
| `{board, ref, value}` | a second connector on a board this pack reads | all |
| `{battery: id}` | the battery's pigtail; `a` must be the battery's own connector | `reversed` |
| `{external: text}` | something outside every board here: a charger's plug, a soldered lead | `bridges` |

Only two connectors on boards join nets (`map`). A battery's pigtail meets the
connector its record already names. An external side has no pads to join.

## The modes

Each mode is a physical event. The rule enumerates every placement it allows.

| Mode | The event | The arithmetic |
|---|---|---|
| `reversed` | the plug turned end for end | each pad meets the pad its centre turns onto, 180 degrees about the middle of a's numbered pads |
| `rotate` | a two-row header turned 180 degrees | the same; the count must be even |
| `flipped_type` | the other FFC contact type (same side for opposite side) | b's pads turned the same way: b's order reversed |
| `{narrower: N}` | an N-way cable in the n-way housings | every pair of end offsets in 0..n-N |
| `{offset: k}` | a's unshrouded header off by up to k positions | shifts of -k..k but 0; `rows: 2` shifts by columns, on KiCad's zigzag numbering only |
| `{slide: [pins]}` | an audio plug on its way in | a's contacts, the deepest first: each plug contact passes the shallower ones |
| `{bridges: [[p, q]]}` | a wrong plug that shorts two of a's contacts | p and q joined, beside the nominal map |
| `cell_reversed` | one loose cell of a holder turned (a battery mating only) | counted as the cell's hazard ("A reversed cell") |

The modes that act on one housing act on `a`. When `b` can be off or slid too,
state the mating a second time with its ends swapped. `slide` and `bridges`
take any pad names. The positional modes need the housing's pads numbered 1..n
with none left out: a gap (1, 2, 3 and 5) is refused, naming it, because the
left-out position is not on the board and a plug turned or moved across it
cannot be placed. If the gap keys the housing against that fit, the mating
takes `modes: []`. A map of `reversed` needs the same, at both ends, and the
same count. `straight` meets each pad with the pad of its own number, so a gap
there meets nothing, as it does.

The turned modes (`reversed`, `rotate`, `flipped_type`) find each pad's
partner on the pad centres, never from its number: the point reflection
through the middle of the numbered pads. So KiCad's zigzag numbering, a
top-bottom one and a DIP's counter-clockwise one (where a turned header meets
1 with n/2+1, not with n) all read as the copper does. A pad whose turned
centre lands on no numbered pad (within 0.01 mm) is refused, naming it.
`offset` with `rows: 2` shifts by two numbers, so it is refused on a footprint
whose pad centres are not on KiCad's zigzag numbering. `narrower` and `offset`
count positions in pad-number order along the row.

## Sources, hazards and protection

A **source node** is a battery's terminal or tap, a net beyond a fault element,
a Type-C VBUS, or a rail. A **return** is a battery's negative, a Type-C GND, a
rail's return, or the net beyond a low-side switch.

A mating's wrong way joins nets. A **hazard** is a pair of nodes it puts on one
net:

- two nodes of one source (a cell's positive on its negative, VBUS on its GND,
  a tap on the next);
- a source's node on another source's node, or on another source's return.

Two returns that meet are no hazard. A source that meets a signal is listed in
the evidence, and not judged.

The loop of the source that drives a hazard decides whether it is **raw**.
When the other node carries a terminal of that same source, the source's own
loop closes there, and that pair decides: a cell's positive on its own return
beyond a low-side switch is protected, whatever else (a receptacle's GND, a
rail's return) shares that return; VBUS on its own receptacle's GND is raw.
Only when the other node carries none of the source's own terminals does the
source meet another source; then the hazard is raw unless either node lies
beyond a fault element. The pair of nodes is raw when any source's loop is. A
protected hazard is named in the detail and does not fail: `pcb.fault_current`
judges the element. The count is of raw hazards, each pair of nodes once,
however many wrong ways make it.

## The nominal map

The map itself is judged first, with no mode: a regenerated board whose pin
order no longer matches the cable is caught on the correct cable. On the
nominal map two sources at one voltage are one rail: two receptacles' VBUS, or a
VBUS (5 V, USB's default bus voltage) and a rail stated at 5 V. A battery is
never one rail with another source. In a wrong way, any two sources that meet
are a hazard.

A net a cell drives is the battery's. Do not state it again as a rail: the rule
would read the rail and the cell as two sources and fail on the nominal map.

## Coverage

Every fitted connector with a pad on a source's net must be named by a mating.
A connector is a footprint from KiCad's `Connector*` or `Battery` libraries, or
one whose ref is J, P, CN or BT and a number (a JP solder jumper is not one). An
unnamed one is an error, naming it. Coverage forces a statement for each
connector, not the completeness of its modes.

## A reversed cell

A battery mating with `reversed` asks what stops a cell whose pigtail meets its
connector the other way. A reversed cell joins no nets: it drives the board
backwards, through every diode and body diode on it.

- **Blocked**: a `reverse_block` in series counts (a diode or an ideal-diode
  FET: on the board, fitted, one path pin on the terminal's net, and no part on
  that net ahead of it that its `ahead` list does not name with why). No hazard.
  A block behind series parts (the cell holder, then an on/off switch, then a
  Schottky diode) names them in `through`, in order from the terminal: `[{ref,
  value, path: [p, q], why}]`, each held to the copper, its `p` on the net
  before it and its `q` on the next, and the block's path pin on the chain's
  last net. A part in the chain passes the reversed cell on and is never
  credited with blocking it; any other part on a net of the chain sees the
  reversed cell first, and is refused until `ahead` names it with why.
- **Limited**: a fuse or PTC at the terminal, with nothing ahead of it, opens on
  the reversed current. A protected hazard, named.
- **Open**: anything else. A raw hazard.

An element in the pack is neither: it sits before the pigtail that reverses.
Protector FETs do not count (their controller runs from the cell, now
backwards). An eFuse that blocks reverse current is stated as a `reverse_block`.

**One loose cell turned.** A holder of loose cells, two or more in series, can
take one of them the wrong way round. The mode is `cell_reversed`, on a battery
mating only. The board then sees the string the right way round, at two cells'
voltage fewer (none, for two cells), so nothing on the board is driven
backwards; the turned cell is, by the others (IEC 60086-5, the incorrect
installation of a primary cell). No element on a board blocks that, so the rule
counts it as a raw hazard and says so: a holder that keys each cell is the
remedy, and a record for such a holder states the mode no more. A one-cell
battery is refused: `reversed` turns it whole.

## The baseline, worked

`selftest/assets/power_board.kicad_pcb` carries J10 and J11, two 8-position FFC
housings on one board, joined by the `flex` mating: straight, and a 6-way cable
also fits (`{narrower: 6}`). Both carry GND on 1, SIG_A on 2, SIG_B on 4, VBUS on
5, SIG_C on 7 and SIG_D on 8; 3 and 6 are open. The cable at end offsets (0, 1),
(0, 2), (1, 0), (1, 2), (2, 0) and (2, 1) is 6 wrong ways. The two housings make
a loop through their shared nets, and the rule follows it as the copper does: an
open position breaks each chain from 1 to 5, so VBUS meets SIG_B and SIG_C (listed)
and never GND. 0 hazards.

`ground_beside_vbus` moves GND to 7 on both housings, as a generator's rotation
flip reorders them. The straight map still meets GND with GND. At (0, 2) J10's
VBUS meets J11's GND, and at (2, 0) the same pair the other way: 1 hazard.

The cell meets BT1 in a keyed housing (`modes: []`), J1 meets a charger's plug
(Type-C: both orientations are correct, `[]`), and J6's lead is soldered (`[]`).

## What it cannot see

- a wrong fit nobody stated;
- a keyed housing forced past its key, and a cable from another product;
- a source on a GPIO or a data pin (listed, not judged), and what it damages;
- a source that meets another source's return only through a part (a fuse, a
  FET): the rule reads them as two nets;
- two copies of one board file (the halves of a split keyboard drawn once): it
  reads them as one board;
- a housing wider than its footprint's pads (the last positions drawn with no
  pad): it reads the pads as the housing's positions;
- `narrower` or `offset` on a footprint whose pad numbers do not run in order
  along its row (a shield tab given the next number);
- a USB Power Delivery contract above 5 V;
- the arc as a plug goes in, and hot-plug damage.

## Sources

- Digi-Key, FFC/FPC connector and cable compatibility (same-side and
  opposite-side contact types):
  https://www.digikey.com/en/blog/ffc-fpc-connector-and-cable-compatibility-basics
- SparkFun, single-cell LiPo care (vendors wire two-pin cell leads both ways):
  https://learn.sparkfun.com/tutorials/single-cell-lipo-battery-care
- QMK, split keyboards (a TRRS link carries VCC and is not hot-pluggable):
  https://develop-docs.qmk.fm/features/split_keyboard
- splitkb.com, one half not working (a plug's contacts slide over the jack's as
  it goes in): https://docs.splitkb.com/troubleshooting/one-half
