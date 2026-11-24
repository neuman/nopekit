# SPDX-License-Identifier: Apache-2.0
"""What the four battery rules in ``power.py`` share: the records, read strictly
and bound to the boards they name; nets joined across boards; the checks of a
part's place on the copper; the board value parser; and the thermistor
arithmetic. Not a gate file: it registers nothing.

**The records** are params, each an object keyed by its id, never a list:
``pcb_batteries``, ``pcb_protectors``, ``pcb_conductors`` and ``pcb_matings``.
A design with two cells (one on each of two boards), or one protector IC
that does four jobs, is stated once and read by every rule. (What slipped
through the branch's first cut: ``pcb_cell`` was one record, so a second cell
was never read; and the same protector was stated twice, in two records that
could disagree.) Keyed by id, not a list: the mutation walk reaches the leaves
of an object and treats a list as one opaque leaf, so a list of records would
leave every number in them unwalked (thermal-analytic's ``heat_sources`` found
this first). Every record is read strictly: an unknown key is refused with the
nearest known one, a number is held to its kind, and a ``source`` must say
something.

**Bound to the design.** A record names the board it is for (``board``, else
``pcb_board``) and the ref, value and pins it describes. A ref the board lacks,
another value on it, or a pin with no pad raises :class:`RecordError`: the
record is for another design. A board the reader cannot vouch for
(``_kicad.board_problems``) is an error, and one newer than the reader was
checked through (``_kicad.reader_ceiling``) raises :class:`ReaderLimit`, which
each rule turns into a skip with the guard's own words. A part marked do not
populate or left out of the BOM (``_kicad.NOT_FITTED``) is not on the built
board, and no rule counts it.

**Nets across boards.** A net is ``(board, name)``. ``pcb_matings`` says which
connector meets which, pin by pin, and :class:`Design` joins the two pads'
nets into one. So a cutoff on a daughterboard that senses the cell through a
flex counts, where the branch read it as absent.

**A mating is read whole by every rule.** :class:`Mating` reads one
``pcb_matings`` record: its two sides (a connector on a board; the far side a
connector, a battery's pigtail, or something outside every board this pack
reads), its nominal ``map``, and the ``modes`` in which it can meet the wrong
way. Only ``pcb.mismate`` enumerates the modes, but every rule refuses a
record it could not read, so no two rules read one record two ways.

**USB-C receptacles** (:class:`UsbC`, :func:`typec_receptacle`): the pins by
the Type-C specification's names, a ``pcb_usbc`` record's binding to its
footprint, and the pins it maps when the footprint numbers its pads otherwise.
``pcb.usbc_receptacle`` judges them; ``pcb.mismate`` reads their VBUS as a
source.
"""
from __future__ import annotations

import difflib
import math
import os
import re

from nopekit.modelio import load_path as _load_path

_HERE = os.path.dirname(os.path.abspath(__file__))
K = _load_path(os.path.join(_HERE, "_kicad.py"))


class RecordError(ValueError):
    """A record the gate cannot read as one for this design."""


class ReaderLimit(RecordError):
    """The board a record names is newer than any format the reader was checked
    through: the rule skips with the guard's own words. (What slipped through:
    a KiCad 10 board read as an error, a crash, beside the guard's skip.)"""


# --------------------------------------------------------------------------- #
# reading a record strictly
# --------------------------------------------------------------------------- #

def obj(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise RecordError(f"{label} is not a record (a JSON object)")
    return value


def keys(rec: dict, label: str, required: tuple = (), optional: tuple = ()) -> dict:
    """``rec`` held to its keys: every required one present, nothing unknown. An
    unknown key names the nearest known one: a misspelled ``tol_pct`` would
    otherwise read as no tolerance at all."""
    obj(rec, label)
    known = (*required, *optional)
    for key in rec:
        if key not in known:
            near = difflib.get_close_matches(str(key), known, n=1)
            hint = f" (did you mean {near[0]!r}?)" if near else ""
            raise RecordError(f"{label} has an unknown key {key!r}{hint}; it takes "
                              f"{', '.join(known)}")
    missing = [k for k in required if k not in rec]
    if missing:
        raise RecordError(f"{label} has no {', '.join(missing)}")
    return rec


def source(rec: dict, label: str) -> str:
    """A record's ``source``, which must say something: a datasheet fact with
    no source is a number nobody can check."""
    text = rec.get("source")
    if not isinstance(text, str) or not text.strip():
        raise RecordError(f"{label}'s source is empty: say where each number comes from")
    return text


def num(value, label: str, *, gt: float | None = None, ge: float | None = None) -> float:
    """A JSON number held to its bound. A flag is not a number (``true`` reads
    as 1 in Python)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RecordError(f"{label} is {value!r}: give a number")
    if gt is not None and not value > gt:
        raise RecordError(f"{label} is {value!r}: it must be above {gt:g}")
    if ge is not None and not value >= ge:
        raise RecordError(f"{label} is {value!r}: it must be at least {ge:g}")
    return float(value)


def count(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RecordError(f"{label} is {value!r}: give a whole number, 1 or more")
    return value


def flag(value, label: str) -> bool:
    if not isinstance(value, bool):
        raise RecordError(f"{label} is {value!r}: give true or false (a string such as "
                          f"\"false\" reads as true)")
    return value


def word(value, label: str, choices: tuple) -> str:
    if value not in choices:
        raise RecordError(f"{label} is {value!r}: give one of {', '.join(choices)}")
    return value


def text(value, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip():
        raise RecordError(f"{label} is {value!r}: give a name")
    return str(value)


def pins(value, label: str, n: int | None = None) -> list[str]:
    if not isinstance(value, list) or not value or (n is not None and len(value) != n):
        want = f"{n} pins" if n is not None else "a list of pins"
        raise RecordError(f"{label} is {value!r}: give {want}")
    return [text(p, f"{label}[{i}]") for i, p in enumerate(value)]


#: The words a datasheet range is refused with when only one number is given:
#: thermal-analytic's design for ``charge_window``, kept when that gate was
#: folded into ``pcb.charge_temperature``. A typical value read as [typ, typ]
#: makes every tolerance corner the typical one, and the corners are the point.
TYPICAL_ONLY = ("give the datasheet [min, max]; for a value the datasheet gives only as "
                "typical, state [typ-x, typ+x] and say in source that x is your "
                "assumption")


def band(value, label: str, *, positive: bool = True) -> tuple[float, float]:
    """A datasheet ``[min, max]``. One number is refused (:data:`TYPICAL_ONLY`),
    and so is a min above its max."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        raise RecordError(f"{label} is one number, {value!r}: {TYPICAL_ONLY}")
    if not isinstance(value, list) or len(value) != 2:
        raise RecordError(f"{label} is {value!r}: {TYPICAL_ONLY}")
    lo, hi = (num(v, f"{label}[{i}]", gt=0.0 if positive else None)
              for i, v in enumerate(value))
    if lo > hi:
        raise RecordError(f"{label} is {value!r}: its min is above its max")
    return lo, hi


def pct(value, label: str) -> float:
    """A tolerance in percent, as a fraction."""
    v = num(value, label, ge=0.0)
    if v >= 100.0:
        raise RecordError(f"{label} is {value!r}: a tolerance in percent, below 100")
    return v / 100.0


def records(ctx, key: str) -> dict | None:
    """The records under ``key``, ``{id: record}``; None when the project states
    none. A list is refused, naming why: a list's records are one opaque leaf to
    the mutation walk, and ids are what other records point at."""
    value = ctx.param(key)
    if value is None:
        return None
    if isinstance(value, list):
        raise RecordError(f"{key} is a list: give an object keyed by each record's id "
                          f"({{\"main\": {{...}}}})")
    obj(value, key)
    return value


# --------------------------------------------------------------------------- #
# board values
# --------------------------------------------------------------------------- #

#: SI prefixes a board's Value field uses for a resistance. ``M`` is mega and
#: ``m`` milli, as a resistor's value is written; ``k`` and ``K`` both kilo.
#: Rejected: ``pcb.py``'s ``spice_number``, which reads as ngspice reads, so
#: ``1M`` is a milliohm there and a megohm on every resistor's label.
_PREFIX = {"": 1.0, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3,
           "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
_DECIMAL = re.compile(r"^(\d+(?:\.\d*)?|\.\d+)\s*([pnuµmkKMG]?)\s*"
                      r"(?:R|Ω|ohms?|Ohms?)?$")
#: RKM code (IEC 60062): the prefix letter stands for the decimal point, and
#: ``R`` for ohms (``4k7`` is 4700, ``4R7`` 4.7, ``1M5`` 1.5 MOhm).
_RKM = re.compile(r"^(\d+)([RkKMG])(\d+)$")


def resistance(value: str) -> float | None:
    """A resistor's board Value in ohms, or None when it is not a bare value
    (``10k 1%``, a part number): the gate then refuses it rather than guess."""
    v = str(value).strip()
    m = _RKM.match(v)
    if m:
        scale = 1.0 if m.group(2) == "R" else _PREFIX[m.group(2)]
        return float(f"{m.group(1)}.{m.group(3)}") * scale
    m = _DECIMAL.match(v)
    if m:
        return float(m.group(1)) * _PREFIX[m.group(2)]
    return None


# --------------------------------------------------------------------------- #
# the design: boards, parts and nets
# --------------------------------------------------------------------------- #

def _not_fitted(fp) -> str:
    return f"{fp.ref} ({fp.value}) is marked not fitted ({', '.join(fp.attrs)})"


not_fitted = _not_fitted


class Design:
    """Every board the gate's records name, read once, and the nets matings
    join. A net is the root of ``(board path, net name)`` in a union-find."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.boards: dict[str, object] = {}
        self._parent: dict[tuple, tuple] = {}
        self.mating_refs: set[tuple[str, str]] = set()
        self.mates: list[Mating] = []

    # -- boards ---------------------------------------------------------- #
    def board(self, rec: dict, label: str) -> str:
        """The path of the board ``rec`` names (``board``, else ``pcb_board``),
        read through ``ctx.load_file`` so the file is keyed, and refused as
        ``pcb.board_readable`` would fail it (RecordError) or skip it
        (ReaderLimit)."""
        rel = rec.get("board") or self.ctx.param("pcb_board")
        if not rel:
            raise RecordError(f"{label} names no board, and there is no pcb_board")
        path = K.project_path(self.ctx, rel)
        if path in self.boards:
            return path
        if not os.path.isfile(path):
            raise RecordError(f"{label}: no board at {path}")
        try:
            version, problems = K.board_problems(self.ctx.load_file(path, loader=K.load_tree))
        except (K.KicadFormatError, UnicodeDecodeError) as exc:
            version, problems = None, [f"it does not parse: {exc}"]
        if K.reader_ceiling(version):
            raise ReaderLimit(f"the board {os.path.basename(path)}: {K.reader_ceiling(version)}")
        if problems:
            raise RecordError(f"the board {os.path.basename(path)} is not one this reader can "
                              f"vouch for: {'; '.join(problems)}")
        self.boards[path] = self.ctx.load_file(path, loader=K.load_board)
        return path

    def part(self, path: str, ref, value, label: str, need=()) -> object:
        """The footprint ``ref`` on the board at ``path``, held to ``value``
        (None: not held) and to having a pad for each of ``need``."""
        board = self.boards[path]
        ref = str(ref)
        fp = next((f for f in board.footprints if f.ref == ref), None)
        if fp is None:
            raise RecordError(f"{label} is for another design: "
                              f"{os.path.basename(path)} has no {ref}")
        if value is not None and fp.value != str(value):
            raise RecordError(f"{label} is for another design: {os.path.basename(path)}'s "
                              f"{ref} is {fp.value}, the record's {value}")
        numbers = {p.number for p in fp.pads}
        for pin in need:
            if str(pin) not in numbers:
                raise RecordError(f"{label} is for another design: {ref} ({fp.value}) has no "
                                  f"pad {pin}")
        return fp

    # -- nets ------------------------------------------------------------ #
    def _find(self, node: tuple) -> tuple:
        self._parent.setdefault(node, node)
        while self._parent[node] != node:
            self._parent[node] = self._parent[self._parent[node]]
            node = self._parent[node]
        return node

    def join(self, a: tuple, b: tuple) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)

    def net(self, path: str, fp, pin) -> tuple | None:
        """The global net of ``fp``'s pad ``pin`` on the board at ``path``; None
        when the pad is on no net."""
        name = next((p.net for p in fp.pads if p.number == str(pin)), "")
        return self._find((path, name)) if name else None

    @staticmethod
    def pad_node(path: str, fp, pin) -> tuple | None:
        """The board's own net of ``fp``'s pad ``pin``, ``(board path, name)``,
        before any mating joins it; None when the pad is on no net.
        ``pcb.mismate`` builds each wrong mating's nets from these, never from
        the nominal joins."""
        name = next((p.net for p in fp.pads if p.number == str(pin)), "")
        return (path, name) if name else None

    def node(self, path: str, name: str) -> tuple:
        """The global net of the net ``name`` on the board at ``path``."""
        return self._find((path, name))

    def same(self, a: tuple | None, b: tuple | None) -> bool:
        return a is not None and b is not None and self._find(a) == self._find(b)

    def pads_on(self, net: tuple | None, *, fitted_only: bool = True) -> list[tuple]:
        """``(board path, ref, pad)`` of every pad on the global ``net``."""
        if net is None:
            return []
        root = self._find(net)
        out = []
        for path, board in sorted(self.boards.items()):
            for fp in board.footprints:
                if fitted_only and not fp.fitted:
                    continue
                for p in fp.pads:
                    if p.net and self._find((path, p.net)) == root:
                        out.append((path, fp.ref, p.number))
        return out

    def label(self, net: tuple | None) -> str:
        """A net's name for a detail line: its first board's name, and the
        board when the design has more than one."""
        if net is None:
            return "no net"
        path, name = self._find(net)
        if len(self.boards) > 1:
            return f"{name} ({os.path.basename(path)})"
        return name

    def matings(self, recs: dict | None) -> None:
        """Join the nets ``pcb_matings`` says meet, each record read whole
        (:class:`Mating`). Only a mating between two connectors on boards joins
        nets: a battery's pigtail meets its own connector, whose pads are the
        battery's terminals already, and an external side has no pad here. The
        two connectors of a board-to-board mating are conductors, never parts
        that tap a net (``mating_refs``); an external connector is not one of
        them, since what lies beyond it may tap the net."""
        for mid, rec in sorted((recs or {}).items()):
            m = Mating(self, mid, rec)
            self.mates.append(m)
            if m.kind != "board":
                continue
            (pa, fa), (pb, fb) = m.a, m.b
            self.mating_refs.add((pa, fa.ref))
            self.mating_refs.add((pb, fb.ref))
            for a_pin, b_pin in m.pairs:
                na, nb = self.net(pa, fa, a_pin), self.net(pb, fb, b_pin)
                if na is not None and nb is not None:
                    self.join(na, nb)

    # -- a part's place on the copper ----------------------------------- #
    def chain(self, path: str, start: tuple | None, refs: list[str], end: tuple | None,
              avoid: set[str]) -> str:
        """Why the fitted two-pad parts ``refs``, in order, are not a series
        chain from the net ``start`` to the net ``end`` on the board at
        ``path``, or "" when they are: each part one pad on the net before it
        and one on the next. A part in ``avoid`` (the battery's own connector)
        never counts. (What slipped through the branch: one series part, so a
        protector sensing the cell through a fuse and then its RC filter read
        as sensing nothing.)"""
        here = start
        for ref in refs:
            fp = self.part(path, ref, None, f"the chain part {ref}")
            if fp.ref in avoid:
                return f"{fp.ref} is the battery's own connector"
            if not fp.fitted:
                return _not_fitted(fp)
            numbered = [p for p in fp.pads if p.number]
            nets = [self.net(path, fp, p.number) for p in numbered]
            if len(numbered) != 2:
                return f"{fp.ref} has {len(numbered)} numbered pads, not two"
            if self.same(nets[0], here) and not self.same(nets[1], here):
                here = nets[1]
            elif self.same(nets[1], here) and not self.same(nets[0], here):
                here = nets[0]
            else:
                return (f"{fp.ref}'s pads sit on {self.label(nets[0])} and "
                        f"{self.label(nets[1])}, not one on {self.label(here)}")
        if not self.same(here, end):
            return (f"the chain ends on {self.label(here)}, not on the sense pin's "
                    f"{self.label(end)}")
        return ""

    def reached_by_another(self, net: tuple | None, refs: set[tuple[str, str]]) -> bool:
        """Whether a fitted part other than ``refs`` ``(path, ref)`` has a pad on
        ``net``: a net only one part reaches carries nothing anywhere."""
        return any((path, ref) not in refs for path, ref, _pad in self.pads_on(net))


# --------------------------------------------------------------------------- #
# a mating, and the ways it can meet wrong
# --------------------------------------------------------------------------- #

#: The modes a mating takes as one word: ``reversed`` (the plug turned end for
#: end in its housing: position i meets the one n+1-i would), ``rotate`` (a
#: two-row header turned 180 degrees: on KiCad's zigzag numbering, odd pins on
#: one row and even on the other, position i lands on n+1-i too, and the rule
#: holds the count even) and ``flipped_type`` (the other FFC contact type, same
#: side for opposite side: the order reverses at b's end only). The first two
#: name different events with one arithmetic; both are kept so a record says
#: which it means.
MODE_WORDS = ("reversed", "rotate", "flipped_type")

#: The modes a mating takes as ``{mode: value}``: ``narrower`` (an N-way cable
#: in the n-way housings, at every pair of end offsets), ``offset`` (an
#: unshrouded header at ``a`` off by up to k positions, or k columns with
#: ``rows: 2``), ``slide`` (an audio plug passing ``a``'s contacts on its way
#: in: the list is a's pins, the deepest first) and ``bridges`` (a wrong plug
#: shorting pairs of a's contacts, as a TRS plug's sleeve spans a TRRS jack's
#: ring 2 and sleeve). The modes that act on one housing act on ``a``: a
#: mating whose b end can be off or slid too is stated again with its ends
#: swapped. Rejected: a mode with a free map (a person who can write the wrong
#: map can write the hazard; the modes are the events a connector allows).
MODE_KEYS = ("narrower", "offset", "slide", "bridges")

#: The modes each kind of far side can be judged in. A battery's pigtail can be
#: reversed in its housing (vendors wire two-pin cell leads both ways), and the
#: rule judges that by what blocks the reversed cell, never by joining nets: a
#: reversed cell joins nothing, it drives the board backwards. An external side
#: has no pads this pack reads, so only a plug that bridges a's own contacts can
#: be judged there.
MODES_FOR = {"board": MODE_WORDS + MODE_KEYS, "battery": ("reversed",),
             "external": ("bridges",)}


def positions(fp) -> list[str]:
    """``fp``'s pads numbered 1..n, in number order: the positions a cable's
    conductors sit at. A mounting pad (``MP``, unnumbered) is not one."""
    return sorted({p.number for p in fp.pads if p.number.isdigit()}, key=int)


class Mating:
    """One ``pcb_matings`` record, read strictly and bound to its connectors.

    ``a`` is ``(board path, footprint)``. ``kind`` is ``board`` (``b`` is a
    second connector, ``(board path, footprint)``), ``battery`` (``b`` is a
    ``pcb_batteries`` id: its pigtail meets ``a``, the battery's own
    connector) or ``external`` (``b`` says what lies outside every board: a
    host's plug, a soldered wire). ``pairs`` is the nominal map, ``[(a_pin,
    b_pin)]``, for a board mating only: a battery's record states its
    terminals, and an external side has no pins. ``modes`` is None when the
    record states none, else the parsed list; ``[]`` is a statement (nothing
    else meets it, and ``source`` says why: a keyed housing, Type-C's
    reversible plug, a soldered wire).

    What slipped through the branch's reader: a dict map's a pins were never
    held to a's pads, so a pin a regenerated board had renumbered joined
    nothing and no rule said so; and a straight map on a footprint whose pads
    are not numbered 1..n (USB-C's A1..B12) joined nothing at all."""

    def __init__(self, d, mid: str, rec: dict):
        label = f"pcb_matings.{mid}"
        keys(rec, label, ("a", "b", "source"), ("map", "modes"))
        self.source = source(rec, label)
        self.id, self.label = mid, label
        sa = keys(rec["a"], f"{label}.a", ("ref", "value"), ("board",))
        pa = d.board(sa, f"{label}.a")
        self.a = (pa, d.part(pa, sa["ref"], sa["value"], f"{label}.a"))
        b = obj(rec["b"], f"{label}.b")
        if "battery" in b:
            keys(b, f"{label}.b", ("battery",))
            self.kind, self.b = "battery", text(b["battery"], f"{label}.b.battery")
        elif "external" in b:
            keys(b, f"{label}.b", ("external",))
            self.kind, self.b = "external", text(b["external"], f"{label}.b.external")
        else:
            sb = keys(b, f"{label}.b", ("ref", "value"), ("board",))
            pb = d.board(sb, f"{label}.b")
            self.kind, self.b = "board", (pb, d.part(pb, sb["ref"], sb["value"], f"{label}.b"))
        self.pairs: list[tuple[str, str]] = []
        if self.kind == "board":
            if "map" not in rec:
                raise RecordError(f"{label} has no map: give straight, reversed or "
                                  f"{{a_pin: b_pin}}")
            self.pairs = self._map(rec["map"])
        elif "map" in rec:
            why = ("the battery's terminals are its map" if self.kind == "battery"
                   else "the far side has no pads this pack reads")
            raise RecordError(f"{label}.map: a mating whose far side is {self.kind} takes no "
                              f"map ({why})")
        self.modes = None if "modes" not in rec else self._modes(rec["modes"])

    @property
    def name(self) -> str:
        """``a``'s ref, and what it meets, for a detail line."""
        far = (self.b[1].ref if self.kind == "board" else
               f"battery {self.b}" if self.kind == "battery" else self.b)
        return f"{self.id} ({self.a[1].ref} to {far})"

    def _map(self, mapping) -> list[tuple[str, str]]:
        (_pa, fa), (_pb, fb) = self.a, self.b
        label = self.label
        numbered = positions(fa)
        if mapping in ("straight", "reversed"):
            if not numbered:
                raise RecordError(f"{label}.map is {mapping}, and {fa.ref} ({fa.value}) has no "
                                  f"pads numbered 1..n: give {{a_pin: b_pin}}")
            pairs = list(zip(numbered, numbered if mapping == "straight" else numbered[::-1]))
        elif isinstance(mapping, dict) and mapping:
            pairs = [(text(a, f"{label}.map key"), text(b, f"{label}.map[{a}]"))
                     for a, b in mapping.items()]
        else:
            raise RecordError(f"{label}.map is {mapping!r}: give straight, reversed or "
                              f"{{a_pin: b_pin}}")
        a_numbers = {p.number for p in fa.pads}
        b_numbers = {p.number for p in fb.pads}
        for a_pin, b_pin in pairs:
            if a_pin not in a_numbers:
                raise RecordError(f"{label} is for another design: {fa.ref} ({fa.value}) "
                                  f"has no pad {a_pin}")
            if b_pin not in b_numbers:
                raise RecordError(f"{label} is for another design: {fb.ref} ({fb.value}) "
                                  f"has no pad {b_pin} to meet {fa.ref} pad {a_pin}")
        return pairs

    def _modes(self, modes) -> list[tuple]:
        label = f"{self.label}.modes"
        if not isinstance(modes, list):
            raise RecordError(f"{label} is {modes!r}: give a list of the ways it can meet "
                              f"wrong, [] when nothing else meets it")
        out = []
        for i, mode in enumerate(modes):
            where = f"{label}[{i}]"
            if isinstance(mode, str):
                if mode not in MODE_WORDS:
                    near = difflib.get_close_matches(mode, MODE_WORDS + MODE_KEYS, n=1)
                    hint = f" (did you mean {near[0]!r}?)" if near else ""
                    raise RecordError(f"{where} is {mode!r}{hint}: give one of "
                                      f"{', '.join(MODE_WORDS)}, or {{key: value}} with key "
                                      f"{', '.join(MODE_KEYS)}")
                name, arg = mode, None
            elif isinstance(mode, dict) and mode:
                name = next((k for k in MODE_KEYS if k in mode), None)
                if name is None:
                    raise RecordError(f"{where} is {mode!r}: give one of {', '.join(MODE_KEYS)} "
                                      f"as its key")
                keys(mode, where, (name,), ("rows",) if name == "offset" else ())
                arg = mode[name]
            else:
                raise RecordError(f"{where} is {mode!r}: give a word or {{key: value}}")
            if name not in MODES_FOR[self.kind]:
                raise RecordError(f"{where}: {name} is not judged on a mating whose far side is "
                                  f"{self.kind}; it takes {', '.join(MODES_FOR[self.kind])}")
            out.append(self._mode(name, arg, mode, where))
        return out

    def _mode(self, name: str, arg, mode, where: str) -> tuple:
        if self.kind == "battery":
            return (name,)
        fa = self.a[1]
        pads = {p.number for p in fa.pads}
        n = len(positions(fa))
        if name in ("slide", "bridges"):
            if name == "slide":
                order = pins(arg, f"{where}.slide")
                if len(order) < 2 or len(set(order)) != len(order):
                    raise RecordError(f"{where}.slide: give two or more of {fa.ref}'s contacts, "
                                      f"each once, the deepest first")
                groups = [order]
            else:
                if not isinstance(arg, list) or not arg:
                    raise RecordError(f"{where}.bridges: give [[pin, pin], ...]")
                groups = [pins(pair, f"{where}.bridges[{j}]", 2) for j, pair in enumerate(arg)]
                if any(p == q for p, q in groups):
                    raise RecordError(f"{where}.bridges: a pair names one pin twice")
            for pin in (p for g in groups for p in g):
                if pin not in pads:
                    raise RecordError(f"{where} is for another design: {fa.ref} ({fa.value}) "
                                      f"has no pad {pin}")
            return (name, tuple(groups[0]) if name == "slide" else tuple(map(tuple, groups)))
        # The positional modes move conductors along a's numbered pads.
        if not [c for c, _ in self.pairs if c in set(positions(fa))]:
            raise RecordError(f"{where}: {name} moves the plug along {fa.ref}'s pads numbered "
                              f"1..n, and the map puts no conductor on one")
        if name == "rotate" and n % 2:
            raise RecordError(f"{where}: rotate is a two-row header turned 180 degrees, and "
                              f"{fa.ref} has {n} numbered pads, an odd count")
        if name == "flipped_type":
            q = set(positions(self.b[1]))
            if not all(bp in q for _c, bp in self.pairs):
                raise RecordError(f"{where}: flipped_type reverses b's numbered pads, and the "
                                  f"map meets a pad of {self.b[1].ref} that is not numbered")
        if name == "narrower":
            width = count(arg, f"{where}.narrower")
            if width >= n:
                raise RecordError(f"{where}.narrower is {width}: a narrower cable has fewer "
                                  f"ways than {fa.ref}'s {n}")
            return (name, width)
        if name == "offset":
            k = count(arg, f"{where}.offset")
            rows = mode.get("rows", 1)
            if rows not in (1, 2) or isinstance(rows, bool):
                raise RecordError(f"{where}.rows is {rows!r}: give 1 or 2")
            if k * rows >= n:
                raise RecordError(f"{where}.offset is {k}: past {fa.ref}'s {n} positions")
            return (name, k, rows)
        return (name,)

    def instances(self, mode: tuple) -> list[tuple[str, list[list[tuple[str, str]]]]]:
        """Each way ``mode`` can meet: ``(what, groups)``, each group the
        contacts one conductor touches, ``("a" | "b", pin)``. A battery's
        reversal yields none here: it joins no nets."""
        name = mode[0]
        if self.kind == "battery":
            return []
        fa = self.a[1]
        p = positions(fa)
        idx = {pin: i for i, pin in enumerate(p)}
        n = len(p)
        nominal = [[("a", c), ("b", bp)] for c, bp in self.pairs]
        if name in ("reversed", "rotate"):
            groups = [[("a", p[n - 1 - idx[c]]), ("b", bp)] if c in idx else [("a", c), ("b", bp)]
                      for c, bp in self.pairs]
            return [("turned 180 degrees" if name == "rotate" else "reversed", groups)]
        if name == "flipped_type":
            q = positions(self.b[1])
            jdx = {pin: i for i, pin in enumerate(q)}
            groups = [[("a", c), ("b", q[len(q) - 1 - jdx[bp]])] for c, bp in self.pairs]
            return [("with the other FFC contact type", groups)]
        if name == "narrower":
            width, m = mode[1], dict(self.pairs)
            out = []
            for oa in range(n - width + 1):
                for ob in range(n - width + 1):
                    if oa == ob:
                        continue                # the nominal map on fewer ways
                    groups = []
                    for j in range(width):
                        g = [("a", p[oa + j])]
                        if p[ob + j] in m:
                            g.append(("b", m[p[ob + j]]))
                        groups.append(g)
                    out.append((f"narrower {width} at ({oa}, {ob})", groups))
            return out
        if name == "offset":
            k, rows = mode[1], mode[2]
            unit = "column" if rows == 2 else "position"
            out = []
            for s in [*range(-k, 0), *range(1, k + 1)]:
                groups = []
                for c, bp in self.pairs:
                    if c not in idx:
                        groups.append([("a", c), ("b", bp)])
                        continue
                    t = idx[c] + s * rows
                    groups.append([("a", p[t]), ("b", bp)] if 0 <= t < n else [("b", bp)])
                out.append((f"{fa.ref} off by {s:+d} {unit}{'s' if abs(s) > 1 else ''}", groups))
            return out
        if name == "slide":
            order = list(mode[1])
            out = []
            for short in range(1, len(order)):
                groups = []
                for c, bp in self.pairs:
                    if c not in order:
                        groups.append([("a", c), ("b", bp)])
                        continue
                    j = order.index(c) + short
                    groups.append([("a", order[j]), ("b", bp)] if j < len(order) else [("b", bp)])
                out.append((f"its plug {short} contact{'s' if short > 1 else ''} short of home "
                            f"in {fa.ref}", groups))
            return out
        # bridges
        return [(f"a plug that bridges {', '.join(f'{a}-{b}' for a, b in mode[1])} on {fa.ref}",
                 nominal + [[("a", a), ("a", b)] for a, b in mode[1]])]


def describe(mode: tuple) -> str:
    """A mode as the record wrote it, for the evidence."""
    if len(mode) == 1:
        return mode[0]
    if mode[0] == "offset":
        return f"offset {mode[1]}" + (" (rows 2)" if mode[2] == 2 else "")
    if mode[0] == "slide":
        return f"slide {'/'.join(mode[1])}"
    if mode[0] == "bridges":
        return "bridges " + ", ".join(f"{a}-{b}" for a, b in mode[1])
    return f"{mode[0]} {mode[1]}"


# --------------------------------------------------------------------------- #
# USB-C receptacles
# --------------------------------------------------------------------------- #

#: The Type-C receptacle's pins by signal, by the specification's names: USB
#: Type-C Cable and Connector Specification, Release 2.0 (August 2019, USB-IF),
#: Table 3-4 (full-featured) and Table 3-5 (USB 2.0 only), read 2026-11-24. All
#: 22 of KiCad 9.0.9's stock USB-C receptacle footprints number their pads by
#: these names (the 6-pin ones carry A5, A9, A12, B5, B9 and B12 only).
TYPEC_GND = ("A1", "A12", "B1", "B12")
TYPEC_VBUS = ("A4", "A9", "B4", "B9")
TYPEC_DP = ("A6", "B6")
TYPEC_DM = ("A7", "B7")
TYPEC_PINS = tuple(f"{row}{n}" for row in "AB" for n in range(1, 13))

_TYPEC_NAME = re.compile(r"USB.?C|TYPE.?C", re.IGNORECASE)


def typec_receptacle(fp) -> bool:
    """Whether ``fp`` is a USB Type-C receptacle: two of its pads carry the pin
    names CC1 and CC2 (KiCad's receptacle symbols name them so; its plug
    symbol names them CC and VCONN), or its footprint's name says USB-C or
    Type-C, not Plug, and it has pads A5 and B5. Rejected: pads A5 and B5
    alone (a ball grid numbers its balls so, and every BGA would read as a
    receptacle)."""
    names = {p.pinfunction.strip().upper() for p in fp.pads}
    if {"CC1", "CC2"} <= names:
        return True
    lib = fp.lib_id.split(":")[-1]
    numbers = {p.number for p in fp.pads}
    return (bool(_TYPEC_NAME.search(lib)) and "PLUG" not in lib.upper()
            and {"A5", "B5"} <= numbers)


class UsbC:
    """One ``pcb_usbc`` record bound to its receptacle. ``pad[spec pin]`` is the
    footprint's pad for each Type-C pin it has: the record's ``pins`` first,
    else the pad numbered by the pin's name. The role's own keys are judged by
    ``pcb.usbc_receptacle``; this reads what every reader shares, so
    ``pcb.mismate`` finds the VBUS and GND a record names."""

    ROLES = ("sink", "source", "drp")
    OPTIONAL = ("board", "pins", "cc_by", "reads_rp", "cc_resistor_tol_pct", "rp",
                "vbus_switch", "others")

    def __init__(self, d, uid: str, rec: dict):
        label = f"pcb_usbc.{uid}"
        keys(rec, label, ("ref", "value", "role", "usb2", "source"), self.OPTIONAL)
        source(rec, label)
        self.id, self.label, self.rec, self.d = uid, label, rec, d
        self.role = word(rec["role"], f"{label}.role", self.ROLES)
        self.usb2 = flag(rec["usb2"], f"{label}.usb2")
        self.path = d.board(rec, label)
        self.fp = d.part(self.path, rec["ref"], rec["value"], label)
        numbers = {p.number for p in self.fp.pads}
        given = rec.get("pins")
        if given is not None:
            keys(given, f"{label}.pins", (), TYPEC_PINS)
        self.pad: dict[str, str] = {}
        for spec in TYPEC_PINS:
            if given and spec in given:
                pad = text(given[spec], f"{label}.pins.{spec}")
                if pad not in numbers:
                    raise RecordError(f"{label}.pins is for another design: {self.fp.ref} "
                                      f"({self.fp.value}) has no pad {pad} (for {spec})")
                self.pad[spec] = pad
            elif spec in numbers:
                self.pad[spec] = spec
        need = [("A5", "CC1"), ("B5", "CC2")]
        if not self.specs(TYPEC_VBUS):
            need.append(("A9", "VBUS"))
        if not self.specs(TYPEC_GND):
            need.append(("A12", "GND"))
        for spec, what in need:
            if spec not in self.pad:
                raise RecordError(f"{label}: {self.fp.ref} ({self.fp.value}) has no pad {spec} "
                                  f"({what}): name its pad in pins")
        if self.usb2:
            missing = [s for s in TYPEC_DP + TYPEC_DM if s not in self.pad]
            if missing:
                raise RecordError(f"{label}.usb2 is true, and {self.fp.ref} has no pad "
                                  f"{', '.join(missing)}: a receptacle with no data pins (a "
                                  f"6-pin one) carries no USB 2.0 data; state usb2 false")

    def specs(self, group) -> list[str]:
        """The pins of ``group`` this receptacle has."""
        return [s for s in group if s in self.pad]

    def net(self, spec: str):
        """The global net of pin ``spec``; None when it is open or absent."""
        return self.d.net(self.path, self.fp, self.pad[spec]) if spec in self.pad else None

    def node(self, spec: str):
        """The board's own net of pin ``spec``; None when it is open or absent."""
        return Design.pad_node(self.path, self.fp, self.pad[spec]) if spec in self.pad else None


# --------------------------------------------------------------------------- #
# thermistor arithmetic
# --------------------------------------------------------------------------- #

KELVIN = 273.15

#: The reference temperature of an NTC's R25 and of its B constant's first
#: point: 25 C. Every B constant on a datasheet is a pair (B25/50, B25/85).
T25_K = 298.15

#: Where the bisection looks for a trip with the B model, C: the range of
#: every commercial NTC's datasheet and of every handheld cell's storage
#: rating. A trip outside it is a trip that never happens. Rejected: the cell
#: window widened by a margin (a trip just past it would read as never).
SEARCH_C = (-40.0, 150.0)

#: The bisection's resolution, K: a hundredth of the 0.01 K the verdict is
#: rounded to, so the rounding decides the last digit, never the search.
RESOLUTION_K = 1e-4


class OutOfTable(RecordError):
    """A temperature the NTC's R/T table does not reach."""


class NTC:
    """One NTC's resistance at a temperature, at one tolerance corner.

    From its maker's R/T table when the record gives one (``ln R`` interpolated
    against ``1/T`` between the two bracketing rows), else from the B equation
    ``R = R25 exp(B (1/T - 1/T25))``. The corners: R25 at +-``r25_tol``, and B
    at +-``beta_tol``. With a table, the B tolerance scales the table's own
    exponent, ``R = R25' (R_table / R25)^(1 +- beta_tol)``, which is the B
    equation's corner when the table is one. (A B constant fits two points:
    away from them the B equation drifts, and below its pair it reads a cold
    trip warm, so the table comes first, and the detail says when a trip lies
    outside the B pair.)

    ``beta_tol_pct`` is required with a table too: a maker's table spreads away
    from 25 C as its B does. A person who means an exact table states 0 and
    says why in ``source``. (What slipped through this rule's first cut: a
    table with no ``beta_tol_pct`` read as a B tolerance of 0, where the B
    equation refused the same omission; a B4250 table at R25 8 % passed at
    +0.14 K, and failed at -0.52 K once its B 3 % was stated.)"""

    def __init__(self, rec: dict, label: str):
        keys(rec, label, ("r25_ohm", "r25_tol_pct"),
             ("beta_k", "beta_pair_c", "beta_tol_pct", "rt_table_c_ohm", "ref", "value"))
        self.r25 = num(rec["r25_ohm"], f"{label}.r25_ohm", gt=0.0)
        self.r25_tol = pct(rec["r25_tol_pct"], f"{label}.r25_tol_pct")
        self.table = None
        if rec.get("rt_table_c_ohm") is not None:
            rows = rec["rt_table_c_ohm"]
            if not isinstance(rows, list) or len(rows) < 2:
                raise RecordError(f"{label}.rt_table_c_ohm: give the maker's rows, "
                                  f"[[C, ohm], ...], two or more")
            table = []
            for i, row in enumerate(rows):
                if not isinstance(row, list) or len(row) != 2:
                    raise RecordError(f"{label}.rt_table_c_ohm[{i}] is {row!r}: give [C, ohm]")
                table.append((num(row[0], f"{label}.rt_table_c_ohm[{i}][0]"),
                              num(row[1], f"{label}.rt_table_c_ohm[{i}][1]", gt=0.0)))
            if any(b[0] <= a[0] or b[1] >= a[1] for a, b in zip(table, table[1:])):
                raise RecordError(f"{label}.rt_table_c_ohm: the rows must rise in "
                                  f"temperature and fall in resistance (an NTC)")
            self.table = table
            self.model = "the maker's R/T table"
        elif rec.get("beta_k") is not None:
            self.beta = num(rec["beta_k"], f"{label}.beta_k", gt=0.0)
            self.pair = band(rec.get("beta_pair_c"), f"{label}.beta_pair_c", positive=False)
            self.model = (f"the B equation, B{self.pair[0]:g}/{self.pair[1]:g} "
                          f"{self.beta:g} K")
        else:
            raise RecordError(f"{label} gives neither rt_table_c_ohm nor beta_k: give the "
                              f"maker's R/T table, or its B constant and the pair it is for")
        if "beta_tol_pct" not in rec:
            raise RecordError(f"{label} has no beta_tol_pct: the B constant's tolerance, "
                              f"which spreads the curve away from 25 C, a table's too (state "
                              f"0 only for an exact table, and say why in source)")
        self.beta_tol = pct(rec["beta_tol_pct"], f"{label}.beta_tol_pct")

    @property
    def span_c(self) -> tuple[float, float]:
        return (self.table[0][0], self.table[-1][0]) if self.table else SEARCH_C

    def _table_r(self, t_c: float) -> float:
        rows = self.table
        if not rows[0][0] <= t_c <= rows[-1][0]:
            raise OutOfTable(f"{t_c:.2f} C is outside the R/T table's "
                             f"{rows[0][0]:g}..{rows[-1][0]:g} C")
        for (t0, r0), (t1, r1) in zip(rows, rows[1:]):
            if t0 <= t_c <= t1:
                x0, x1, x = 1 / (t0 + KELVIN), 1 / (t1 + KELVIN), 1 / (t_c + KELVIN)
                return math.exp(math.log(r0) + (math.log(r1) - math.log(r0))
                                * (x - x0) / (x1 - x0))
        raise OutOfTable(f"{t_c:.2f} C")                     # unreachable

    def r(self, t_c: float, r_sign: int, b_sign: int) -> float:
        r25 = self.r25 * (1 + r_sign * self.r25_tol)
        b = 1 + b_sign * self.beta_tol
        if self.table is not None:
            return r25 * (self._table_r(t_c) / self.r25) ** b
        return r25 * math.exp(self.beta * b * (1 / (t_c + KELVIN) - 1 / T25_K))

    def extrapolated(self, t_c: float) -> bool:
        """Whether the B equation is read outside the pair it was fitted on."""
        return self.table is None and not self.pair[0] <= t_c <= self.pair[1]


def network(r_ntc: float, r_par: float | None, r_ser: float | None) -> float:
    """What the sense pin sees: ``(R_ntc || R_par) + R_ser``."""
    r = r_ntc if r_par is None else r_ntc * r_par / (r_ntc + r_par)
    return r + (r_ser or 0.0)


def crossing(f, target: float, lo: float, hi: float) -> float | None:
    """The temperature in ``[lo, hi]`` at which the falling ``f(T)`` equals
    ``target``, by bisection to :data:`RESOLUTION_K`; None when ``f`` stays on
    one side of it across the whole range (the trip never happens there)."""
    f_lo, f_hi = f(lo), f(hi)
    if not (f_hi <= target <= f_lo):
        return None
    while hi - lo > RESOLUTION_K:
        mid = (lo + hi) / 2
        if f(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
