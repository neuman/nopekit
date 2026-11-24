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
        """Join the nets ``pcb_matings`` says meet. Each side is ``{board,
        ref, value}``; ``map`` is ``straight`` (pin n meets pin n),
        ``reversed`` (pin n meets pin N+1-n, N the numbered pads) or
        ``{a_pin: b_pin}``."""
        for mid, rec in sorted((recs or {}).items()):
            label = f"pcb_matings.{mid}"
            keys(rec, label, ("a", "b", "map", "source"))
            source(rec, label)
            sides = []
            for side in ("a", "b"):
                s = keys(rec[side], f"{label}.{side}", ("ref", "value"), ("board",))
                path = self.board(s, f"{label}.{side}")
                sides.append((path, self.part(path, s["ref"], s["value"], f"{label}.{side}")))
                self.mating_refs.add((path, str(s["ref"])))
            (pa, fa), (pb, fb) = sides
            mapping = rec["map"]
            numbered = sorted({p.number for p in fa.pads if p.number.isdigit()}, key=int)
            if mapping == "straight":
                pairs = [(n, n) for n in numbered]
            elif mapping == "reversed":
                total = len(numbered)
                pairs = [(n, str(total + 1 - int(n))) for n in numbered]
            elif isinstance(mapping, dict) and mapping:
                pairs = [(text(a, f"{label}.map key"), text(b, f"{label}.map[{a}]"))
                         for a, b in mapping.items()]
            else:
                raise RecordError(f"{label}.map is {mapping!r}: give straight, reversed or "
                                  f"{{a_pin: b_pin}}")
            b_numbers = {p.number for p in fb.pads}
            for a_pin, b_pin in pairs:
                if b_pin not in b_numbers:
                    raise RecordError(f"{label} is for another design: {fb.ref} ({fb.value}) "
                                      f"has no pad {b_pin} to meet {fa.ref} pad {a_pin}")
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
    outside the B pair.)"""

    def __init__(self, rec: dict, label: str):
        keys(rec, label, ("r25_ohm", "r25_tol_pct"),
             ("beta_k", "beta_pair_c", "beta_tol_pct", "rt_table_c_ohm", "ref", "value"))
        self.r25 = num(rec["r25_ohm"], f"{label}.r25_ohm", gt=0.0)
        self.r25_tol = pct(rec["r25_tol_pct"], f"{label}.r25_tol_pct")
        self.beta_tol = pct(rec.get("beta_tol_pct", 0.0), f"{label}.beta_tol_pct")
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
            if "beta_tol_pct" not in rec:
                raise RecordError(f"{label} has no beta_tol_pct: the B constant's tolerance")
            self.pair = band(rec.get("beta_pair_c"), f"{label}.beta_pair_c", positive=False)
            self.model = (f"the B equation, B{self.pair[0]:g}/{self.pair[1]:g} "
                          f"{self.beta:g} K")
        else:
            raise RecordError(f"{label} gives neither rt_table_c_ohm nor beta_k: give the "
                              f"maker's R/T table, or its B constant and the pair it is for")

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
