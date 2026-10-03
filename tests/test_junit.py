# SPDX-License-Identifier: Apache-2.0
"""JUnit XML is never greener than the exit code.

`atompipe check --junit` hands the run to a CI system, and a CI system renders the
XML, not the exit code: a test tab reading "12 passed, 3 skipped" beside a job that
exited 1 invites somebody to go and fix the "flaky" exit code. So every rule here
pins the XML to the same judgement the exit code is made from, in the direction
that matters — the XML may be redder than the command, never greener:

* a testcase is childless (no ``failure``, ``error`` or ``skipped``) **iff** its
  verdict's ``outcome`` is ``"pass"``. The failure this prevents is the plausible
  one: a writer that keys off ``passed`` renders a skip that also said
  ``passed=True`` as a green testcase (phase-1.md, "Failure it could introduce");
* the ``claims.critical`` suite's failures plus errors equal ``len(claims.blocking())``
  (plus one when there are no claims at all), and ``check`` exits 1 iff that count
  is positive — the two are the same judgement, printed twice;
* a pass beside a gate that skipped or never ran is red from P2.1 — the claim
  reads Skipped or Open (GLOSSARY §3), blocks, and its case is a ``<failure>``
  naming the gate; until P2.1 it was ``<skipped message="partial: …">``,
  invariant 4's PARTIAL, which GLOSSARY §3 retires;
* the renderer escapes the code points XML 1.0 forbids, because ElementTree alone
  writes them raw and the file then fails to parse — a CI step that cannot read
  the report shows no failures at all (the slice probe behind phase-1.md 1.1).

Everything here is pure: a ledger, a registry and verdicts in, a string out, parsed
back with `xml.etree.ElementTree`. The CLI edge (unlink first, one exit code, write
last) is `tests/test_junit_cli.py`'s.

Run:  PYTHONPATH=src python3 -m unittest tests.test_junit -v
"""
from __future__ import annotations

import itertools
import unittest
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from atompipe import claims as claims_mod
from atompipe import gates as gates_mod
from atompipe import report as report_mod
from atompipe.models import (
    Acceptance, Claim, ClaimKind, ClaimStatus, Comparator, GateSpec, Ledger,
    NegativeControl, PhysicalResult, ProjectMeta, Tier, Verdict,
)

#: The children that carry a testcase's outcome. `<properties>` is metadata (the
#: `cached` mark), so "childless" below means "none of these".
RESULT_TAGS = ("failure", "error", "skipped")

#: The three suites `render_junit` always writes, in this order. A fixed shape is
#: what lets a CI step (and `tests/oracle/bracket_signature.py`) find them by name.
SUITES = ("gates", "claims.critical", "claims.not-critical")

WHEN = "2026-09-27T14:02:11Z"

#: Every code point XML 1.0 forbids that a gate can plausibly emit: an ANSI
#: colour escape from a solver's log, a NUL from a C string, a form feed from a
#: pager, a lone surrogate from bytes decoded with `surrogateescape`, and the two
#: non-characters.
HOSTILE = ("\x1b", "\x00", "\x0c", "\ud800", "\ufffe", "\uffff")


def _never_called(ctx):                      # pragma: no cover - rendering runs no gate
    raise AssertionError("render_junit must never run a gate")


def _measurable(cid: str, *, critical: bool = True, statement: str = "") -> Claim:
    return Claim(id=cid, statement=statement or f"{cid} holds under load",
                 kind=ClaimKind.MEASURABLE, critical=critical,
                 acceptance=Acceptance(quantity="deflection", comparator=Comparator.LE,
                                       limit=0.5, units="mm"))


def _physical(cid: str, *, result: PhysicalResult | None = None,
              critical: bool = True) -> Claim:
    return Claim(id=cid, statement=f"{cid} survives a drop", kind=ClaimKind.PHYSICAL,
                 critical=critical, physical_result=result)


def _assumption(cid: str, *, critical: bool = True) -> Claim:
    return Claim(id=cid, statement=f"{cid} the bench is level",
                 kind=ClaimKind.ASSUMPTION, critical=critical)


def _registry(bindings: dict[str, list[str]], packs: dict[str, str] | None = None):
    """A real `gates.Registry` (never the global one): gate id -> claims it covers."""
    reg = gates_mod.Registry()
    for gid, cids in bindings.items():
        reg.register(GateSpec(id=gid, claims=list(cids), tier=Tier.INSTANT,
                              pack=(packs or {}).get(gid, ""),
                              negative_control=NegativeControl(fixture="x:y")),
                     _never_called)
    return reg


def _v(gate: str, outcome: str, claims: list[str], **kw) -> Verdict:
    base = {"pass": {"passed": True, "detail": "within limit"},
            "fail": {"passed": False, "measured": 0.7, "limit": 0.5, "units": "mm",
                     "detail": "0.700 mm at 15 N (limit 0.5 mm)"},
            "skipped": {"passed": False, "skipped": True,
                        "skip_reason": "requires openfoam (not on PATH)"},
            "error": {"passed": False, "error": "ZeroDivisionError: division by zero"},
            }[outcome]
    return Verdict(gate=gate, claims=list(claims), **{"duration_s": 0.25, **base, **kw})


@dataclass
class Scenario:
    name: str
    ledger: Ledger
    verdicts: list[Verdict]
    registry: object
    stale: bool = False
    not_run: list[tuple[str, str]] = field(default_factory=list)


def _ledger(claims: list[Claim], verdicts: list[Verdict]) -> Ledger:
    return Ledger(meta=ProjectMeta(name="t", revision="v0.1"), claims=claims,
                  verdicts=list(verdicts))


def _scenarios() -> list[Scenario]:
    """Every claim status, critical and not, with every gate outcome under them."""
    out: list[Scenario] = []

    # The bracket's shape: one failing gate, one passing, a physical claim, an
    # assumption, and a critical claim no gate covers.
    reg = _registry({"g.defl": ["C1"], "g.fit": ["C2"]}, packs={"g.fit": "fdm-print"})
    vs = [_v("g.defl", "fail", ["C1"]), _v("g.fit", "pass", ["C2"])]
    out.append(Scenario("bracket-shaped", _ledger(
        [_measurable("C1"), _measurable("C2"), _physical("C5"), _assumption("C6"),
         _measurable("C7")], vs), vs, reg))

    reg = _registry({"g.a": ["C1"], "g.b": ["C2"]})
    vs = [_v("g.a", "pass", ["C1"]), _v("g.b", "pass", ["C2"])]
    out.append(Scenario("all-green", _ledger([_measurable("C1"), _measurable("C2")], vs),
                        vs, reg))
    out.append(Scenario("all-green-but-stale",
                        _ledger([_measurable("C1"), _measurable("C2")], vs), vs, reg,
                        stale=True))

    out.append(Scenario("zero-claims", _ledger([], []), [], _registry({})))
    reg = _registry({"g.a": ["C1"]})
    vs = [_v("g.a", "pass", ["C1"])]
    out.append(Scenario("zero-claims-with-a-gate", _ledger([], vs), vs, reg))

    reg = _registry({"g.crash": ["C1"], "g.ok": ["C2"]})
    vs = [_v("g.crash", "error", ["C1"]), _v("g.ok", "pass", ["C2"])]
    out.append(Scenario("errored", _ledger([_measurable("C1"), _measurable("C2")], vs),
                        vs, reg))

    reg = _registry({"g.liar": ["C1"]})
    vs = [_v("g.liar", "skipped", ["C1"], passed=True)]
    out.append(Scenario("skip-that-said-passed", _ledger([_measurable("C1")], vs), vs,
                        reg))

    reg = _registry({"g.a": ["C1"], "g.b": ["C1"], "g.c": ["C2"]})
    vs = [_v("g.a", "pass", ["C1"]), _v("g.b", "skipped", ["C1"])]
    out.append(Scenario("partial-pass-and-pending", _ledger(
        [_measurable("C1"), _measurable("C2")], vs), vs, reg,
        not_run=[("g.c", "above the tier ceiling")]))

    reg = _registry({"g.a": ["N1", "N2"], "g.b": ["N3"], "g.c": ["C1"]})
    vs = [_v("g.a", "fail", ["N1", "N2"]), _v("g.b", "error", ["N3"]),
          _v("g.c", "pass", ["C1"])]
    out.append(Scenario("non-critical-failures", _ledger(
        [_measurable("N1", critical=False), _measurable("N2", critical=False),
         _measurable("N3", critical=False), _measurable("N4", critical=False),
         _physical("N5", critical=False), _measurable("C1")], vs), vs, reg))

    out.append(Scenario("physical-results", _ledger(
        [_physical("P1", result=PhysicalResult(passed=True, when="2026-09-01")),
         _physical("P2", result=PhysicalResult(passed=False, when="2026-09-02",
                                               detail="cracked at 0.4 m"))], []),
        [], _registry({})))

    # Every one of the 8 flag combinations under one critical claim each.
    bindings, claims, vs = {}, [], []
    for i, (passed, skipped, error) in enumerate(itertools.product(
            (False, True), (False, True), ("", "ZeroDivisionError: x"))):
        gid, cid = f"g.combo{i}", f"K{i}"
        bindings[gid] = [cid]
        claims.append(_measurable(cid))
        vs.append(Verdict(gate=gid, claims=[cid], passed=passed, skipped=skipped,
                          error=error, skip_reason="requires x" if skipped else "",
                          duration_s=0.5))
    out.append(Scenario("eight-combinations", _ledger(claims, vs), vs,
                        _registry(bindings)))
    return out


def _judge(sc: Scenario):
    """`(blockers, ready, exit_code)` exactly as `cli.cmd_check` decides them:
    blocking critical claims from `claims.blocking`, and `ready` only when there
    are claims and none blocks — zero claims is not readiness (cli.py, cmd_check)."""
    blockers = claims_mod.blocking(sc.ledger, sc.registry, stale=sc.stale)
    ready = bool(sc.ledger.claims) and not blockers
    return blockers, ready, 0 if ready else 1


def _render(sc: Scenario, *, exit_code: int | None = None, stale: bool | None = None,
            cached=frozenset()) -> tuple[ET.Element, list, int, str]:
    blockers, ready, code = _judge(sc)
    if exit_code is not None:
        code = exit_code
    xml = report_mod.render_junit(
        sc.ledger, sc.verdicts, sc.registry, tier=0, ready=ready, exit_code=code,
        when=WHEN, not_run=sc.not_run, cached=cached,
        stale=sc.stale if stale is None else stale)
    return ET.fromstring(xml), blockers, code, xml


def _suite(root: ET.Element, name: str) -> ET.Element:
    found = [s for s in root.findall("testsuite") if s.get("name") == name]
    if len(found) != 1:
        raise AssertionError(f"expected exactly one testsuite {name!r}, found {len(found)}")
    return found[0]


def _cases(suite: ET.Element) -> dict[str, ET.Element]:
    cases = suite.findall("testcase")
    names = [tc.get("name") for tc in cases]
    if len(set(names)) != len(names):
        raise AssertionError(f"duplicate testcase names in {suite.get('name')}: {names}")
    return dict(zip(names, cases))


def _result(tc: ET.Element) -> ET.Element | None:
    found = [child for child in tc if child.tag in RESULT_TAGS]
    if len(found) > 1:
        raise AssertionError(f"testcase {tc.get('name')} has {len(found)} outcomes")
    return found[0] if found else None


def _kind(tc: ET.Element) -> str:
    """`pass` for a childless testcase, else the outcome child's tag."""
    res = _result(tc)
    return "pass" if res is None else res.tag


def _red(suite: ET.Element) -> int:
    """Failures plus errors: what a CI renderer paints red."""
    return sum(1 for tc in suite.findall("testcase") if _kind(tc) in ("failure", "error"))


def _props(el: ET.Element) -> dict[str, str]:
    props = el.find("properties")
    return {} if props is None else {p.get("name"): p.get("value")
                                      for p in props.findall("property")}


class JUnitNeverGreenerThanTheExitCode(unittest.TestCase):

    def test_childless_iff_pass(self):
        """Gates: no outcome child exactly when `Verdict.outcome == "pass"` — the
        one definition, never `passed`. An uncached testcase has no children at
        all. Claims: a childless claim is PASS or VERIFIED, and nothing else."""
        for sc in _scenarios():
            root, *_ = _render(sc)
            by_gate = {v.gate: v for v in sc.verdicts}
            for name, tc in _cases(_suite(root, "gates")).items():
                with self.subTest(sc.name, gate=name):
                    verdict = by_gate.get(name)
                    ran_and_passed = verdict is not None and verdict.outcome == "pass"
                    self.assertEqual(_result(tc) is None, ran_and_passed)
                    self.assertEqual(len(list(tc)) == 0, ran_and_passed,
                                     "an uncached testcase carries nothing but its outcome")
            statuses = claims_mod.statuses(sc.ledger, stale=sc.stale, registry=sc.registry)
            for suite in ("claims.critical", "claims.not-critical"):
                for name, tc in _cases(_suite(root, suite)).items():
                    if name not in statuses:
                        continue                  # the zero-claims testcase
                    with self.subTest(sc.name, suite=suite, claim=name):
                        if _result(tc) is None:
                            self.assertIn(statuses[name],
                                          (ClaimStatus.PASS, ClaimStatus.VERIFIED))
                        if statuses[name] not in (ClaimStatus.PASS, ClaimStatus.VERIFIED):
                            self.assertIsNotNone(_result(tc))

    def test_a_skip_with_passed_true_is_never_childless(self):
        """The generous direction, planted: `passed=True` beside `skipped=True` (and
        beside an error). The gate and the claim it alone covers both stay red or
        skipped — neither reads green."""
        for flag, want in (({"skipped": True, "skip_reason": "requires x"}, "skipped"),
                           ({"error": "RuntimeError: boom"}, "error")):
            with self.subTest(want=want):
                reg = _registry({"g.liar": ["C1"]})
                vs = [Verdict(gate="g.liar", claims=["C1"], passed=True, **flag)]
                sc = Scenario("liar", _ledger([_measurable("C1")], vs), vs, reg)
                root, blockers, code, _ = _render(sc)
                gate = _cases(_suite(root, "gates"))["g.liar"]
                self.assertEqual(_kind(gate), want)
                claim = _cases(_suite(root, "claims.critical"))["C1"]
                self.assertIn(_kind(claim), ("failure", "error"))
                self.assertEqual((len(blockers), code), (1, 1))

    def test_critical_failures_plus_errors_equal_blocking(self):
        """One count, two renderings. Zero claims adds exactly one failing testcase,
        `no claims recorded`, because zero blocking claims out of zero claims is
        not readiness and `check` exits 1 on it."""
        for sc in _scenarios():
            with self.subTest(sc.name):
                root, blockers, _code, _ = _render(sc)
                critical = _suite(root, "claims.critical")
                empty = 0 if sc.ledger.claims else 1
                self.assertEqual(_red(critical), len(blockers) + empty)
                blocking_ids = {claim.id for claim, _status in blockers}
                red_ids = {name for name, tc in _cases(critical).items()
                           if _kind(tc) in ("failure", "error")}
                if empty:
                    self.assertEqual(red_ids, {"no claims recorded"})
                else:
                    self.assertEqual(red_ids, blocking_ids)
                for claim, status in blockers:
                    res = _result(_cases(critical)[claim.id])
                    if res.tag == "failure":
                        self.assertEqual(res.get("type"), str(status.value))

    def test_exit_1_iff_count_positive(self):
        for sc in _scenarios():
            with self.subTest(sc.name):
                root, _blockers, code, _ = _render(sc)
                count = _red(_suite(root, "claims.critical"))
                self.assertEqual(code == 1, count > 0)
                self.assertEqual(_props(root)["exit_code"], str(code))
                self.assertEqual(_props(root)["ready"], "true" if code == 0 else "false")

    def test_an_exit_code_nothing_explains_is_still_red(self):
        """V: a caller whose exit code and inputs disagree — it judged a stale
        project stale and handed the renderer `stale=False`, or it simply says
        exit 1 over an all-green ledger. The XML must not be the green half."""
        green = next(sc for sc in _scenarios() if sc.name == "all-green")
        root, *_ = _render(green, exit_code=1)
        self.assertGreater(_red(_suite(root, "claims.critical")), 0)

        stale = next(sc for sc in _scenarios() if sc.name == "all-green-but-stale")
        self.assertEqual(_judge(stale)[2], 1, "the fixture must block when stale")
        root, *_ = _render(stale, stale=False)
        self.assertGreater(_red(_suite(root, "claims.critical")), 0)

        # The positive half: a consistent green run carries no red testcase at all.
        root, *_ = _render(green)
        self.assertEqual(sum(_red(_suite(root, s)) for s in SUITES), 0)

    def test_illegal_characters_parse(self):
        """Every forbidden code point, in every field a gate or a human writes:
        the XML parses, encodes as UTF-8, and shows each one as visible text."""
        hostile = "".join(f"<{ch}>" for ch in HOSTILE)
        reg = _registry({"g.a": ["C1"], "g.b": ["C1"], "g.c": ["C1"]})
        vs = [_v("g.a", "fail", ["C1"], detail=f"fail {hostile}", units=f"mm{hostile}",
                 evidence=[f"out/{hostile}.log"]),
              _v("g.b", "skipped", ["C1"], skip_reason=f"skip {hostile}"),
              _v("g.c", "error", ["C1"], error=f"error {hostile}")]
        ledger = _ledger([_measurable("C1", statement=f"statement {hostile}"),
                          _measurable(f"C2{hostile}")], vs)
        xml = report_mod.render_junit(ledger, vs, reg, tier=0, ready=False, exit_code=1,
                                      when=f"{WHEN}{hostile}")
        root = ET.fromstring(xml)                     # would raise ParseError
        xml.encode("utf-8")                           # would raise UnicodeEncodeError
        text = "".join(root.itertext()) + " ".join(
            value for el in root.iter() for value in el.attrib.values())
        for ch in HOSTILE:
            with self.subTest(code_point=f"U+{ord(ch):04X}"):
                self.assertNotIn(ch, text)
                self.assertIn(report_mod.junit_safe(ch), text)

    def test_elementtree_alone_writes_them_raw(self):
        """The negative control for the test above: without the sanitiser each
        hostile code point makes ElementTree write XML that does not parse, or
        cannot be encoded. A fixture that did not break the raw writer would let
        the test above pass on a renderer that sanitises nothing."""
        for ch in HOSTILE:
            with self.subTest(code_point=f"U+{ord(ch):04X}"):
                el = ET.Element("testcase", name=f"a{ch}b")
                raw = ET.tostring(el, encoding="unicode")
                with self.assertRaises((ET.ParseError, UnicodeEncodeError)):
                    ET.fromstring(raw.encode("utf-8"))

    def test_junit_safe_is_visible_and_leaves_legal_text_alone(self):
        self.assertEqual(report_mod.junit_safe("\x1b[31mred"), "\\x1b[31mred")
        self.assertEqual(report_mod.junit_safe("a\x00b\x0cc"), "a\\x00b\\x0cc")
        self.assertEqual(report_mod.junit_safe("\ud800"), "\\ud800")
        self.assertEqual(report_mod.junit_safe("\ufffe\uffff"), "\\ufffe\\uffff")
        legal = "tab\there\nnew\rline é 😀 \x7f \x85 C:\\path"
        self.assertEqual(report_mod.junit_safe(legal), legal)
        self.assertEqual(report_mod.junit_safe(None), "")
        for cp in list(range(0x00, 0x20)) + [0xD800, 0xDBFF, 0xDC00, 0xDFFF]:
            ch = chr(cp)
            with self.subTest(code_point=f"U+{cp:04X}"):
                if ch in "\t\n\r":
                    self.assertEqual(report_mod.junit_safe(ch), ch)
                else:
                    ET.fromstring(f"<a b='{report_mod.junit_safe(ch)}'/>".encode("utf-8"))

    def test_partial_pass_is_skipped_never_childless(self):
        """A pass on one covering gate while another skipped or never ran. Until
        P2.1 the claim read PASS and CI showed it skipped, `partial: …`; under
        GLOSSARY §3's composition it reads Skipped or Open, blocks, and its case
        is red, naming the gate with its lead (R-6, stronger: red, not skipped;
        old 2.1's V, "the JUnit claims.critical suite has a <failure> for it")."""
        sc = next(s for s in _scenarios() if s.name == "partial-pass-and-pending")
        root, blockers, _code, _ = _render(sc)
        self.assertEqual(claims_mod.statuses(sc.ledger, registry=sc.registry)["C1"],
                         ClaimStatus.BLOCKED)
        res = _result(_cases(_suite(root, "claims.critical"))["C1"])
        self.assertIsNotNone(res, "a pass beside a skip rendered childless")
        self.assertEqual((res.tag, res.get("type")), ("failure", "blocked"))
        self.assertEqual(res.get("message"), "skipped: g.b : requires openfoam (not on PATH)")
        self.assertIn("C1", {c.id for c, _s in blockers})

        reg = _registry({"g.a": ["C1"], "g.never": ["C1"]})
        vs = [_v("g.a", "pass", ["C1"])]
        never = Scenario("never", _ledger([_measurable("C1")], vs), vs, reg)
        root, *_ = _render(never)
        res = _result(_cases(_suite(root, "claims.critical"))["C1"])
        self.assertEqual((res.tag, res.get("type"), res.get("message")),
                         ("failure", "pending", "unrun: g.never"))

    def test_physical_and_assumed_are_skipped_with_their_words(self):
        sc = next(s for s in _scenarios() if s.name == "bracket-shaped")
        root, *_ = _render(sc)
        cases = _cases(_suite(root, "claims.critical"))
        # P2.1 (R-6): GLOSSARY §3's words. C5 waits on an article and says
        # what it lacks; C6, an assumption nobody owns, reads Gap and blocks.
        self.assertEqual((_kind(cases["C5"]), _result(cases["C5"]).get("message")),
                         ("skipped", "pending build: needs an article; no test written down"))
        self.assertEqual((_kind(cases["C6"]), _result(cases["C6"]).get("type"),
                          _result(cases["C6"]).get("message")),
                         ("failure", "unclaimed",
                          "no owner recorded — an assumption reads Assumed only once its owner "
                          "records it; nothing can record one yet"))
        self.assertEqual(_result(cases["C1"]).get("message"),
                         "g.defl : 0.700 mm at 15 N (limit 0.5 mm)")
        self.assertEqual(_result(cases["C7"]).get("type"), "unclaimed")
        self.assertEqual(_kind(cases["C2"]), "pass")

    def test_not_critical_fail_is_red_and_the_rest_skipped(self):
        sc = next(s for s in _scenarios() if s.name == "non-critical-failures")
        root, blockers, code, _ = _render(sc)
        cases = _cases(_suite(root, "claims.not-critical"))
        self.assertEqual(_kind(cases["N1"]), "failure")
        self.assertEqual(_kind(cases["N2"]), "failure")
        self.assertEqual(_kind(cases["N3"]), "error")
        self.assertEqual(_kind(cases["N4"]), "skipped")      # unclaimed: a gap, not a defect
        self.assertEqual(_kind(cases["N5"]), "skipped")      # awaiting a real part
        self.assertNotIn("C1", cases)
        self.assertEqual((blockers, code), ([], 0))
        self.assertEqual(_red(_suite(root, "claims.critical")), 0)

    def test_not_admitted_is_error_type(self):
        """`type="not-admitted"` is keyed on the spine's mark, `Verdict.unqualified`
        (P2.1, R-6): a verdict carrying only the text — a gate that worded its
        own crash so (`g.text`) — is a crash."""
        reg = _registry({"g.na": ["C1"], "g.crash": ["C1"], "g.quoted": ["C1"],
                         "g.text": ["C1"]})
        vs = [_v("g.na", "error", ["C1"], error="not admitted: PASSED its own known-bad",
                 unqualified="PASSED its own known-bad"),
              _v("g.crash", "error", ["C1"]),
              _v("g.quoted", "error", ["C1"], error="ValueError: not admitted: x"),
              _v("g.text", "error", ["C1"], error="not admitted: worded by the gate")]
        sc = Scenario("admission", _ledger([_measurable("C1")], vs), vs, reg)
        root, *_ = _render(sc)
        cases = _cases(_suite(root, "gates"))
        self.assertEqual([_result(cases[g]).get("type")
                          for g in ("g.na", "g.crash", "g.quoted", "g.text")],
                         ["not-admitted", "error", "error", "error"])
        self.assertEqual(_result(cases["g.na"]).get("message"),
                         "not admitted: PASSED its own known-bad")

    def test_cached_testcase_carries_the_property(self):
        reg = _registry({"g.a": ["C1"], "g.b": ["C1"], "g.c": ["C2"]})
        vs = [_v("g.a", "pass", ["C1"], duration_s=1.25),
              _v("g.b", "pass", ["C1"], duration_s=1.25),
              _v("g.c", "fail", ["C2"], duration_s=2.5)]
        sc = Scenario("cache", _ledger([_measurable("C1"), _measurable("C2")], vs), vs, reg)
        root, *_ = _render(sc, cached=frozenset({"g.a", "g.c"}))
        cases = _cases(_suite(root, "gates"))
        self.assertEqual(_props(cases["g.a"]), {"cached": "true"})
        self.assertEqual(cases["g.a"].get("time"), "0", "a cached row never replays a duration")
        self.assertEqual(_kind(cases["g.a"]), "pass")
        self.assertEqual(_props(cases["g.c"]), {"cached": "true"})
        self.assertEqual(_kind(cases["g.c"]), "failure", "a cached FAIL keeps its power")
        self.assertEqual(cases["g.b"].find("properties"), None)
        self.assertEqual(cases["g.b"].get("time"), "1.25")

    def test_one_testcase_per_registered_gate_and_not_run_is_skipped(self):
        reg = _registry({"g.ran": ["C1"], "g.tier": ["C1"], "g.only": ["C1"],
                         "g.silent": ["C1"]}, packs={"g.only": "cad-solid"})
        vs = [_v("g.ran", "pass", ["C1"])]
        sc = Scenario("not-run", _ledger([_measurable("C1")], vs), vs, reg,
                      not_run=[("g.tier", "above the tier ceiling"),
                               ("g.only", "excluded by --only")])
        root, *_ = _render(sc)
        suite = _suite(root, "gates")
        self.assertEqual([tc.get("name") for tc in suite.findall("testcase")], reg.ids())
        cases = _cases(suite)
        self.assertEqual(_result(cases["g.tier"]).get("message"),
                         "not run: above the tier ceiling")
        self.assertEqual(_result(cases["g.only"]).get("message"), "not run: excluded by --only")
        self.assertEqual(_kind(cases["g.silent"]), "skipped",
                         "a registered gate with no verdict and no reason is still not a pass")
        self.assertTrue(_result(cases["g.silent"]).get("message").startswith("not run"))
        self.assertEqual(cases["g.only"].get("classname"), "pack.cad-solid")
        self.assertEqual(cases["g.ran"].get("classname"), "project")
        for gid in ("g.tier", "g.only", "g.silent"):
            self.assertEqual(cases[gid].get("time"), "0")

    def test_shape_and_counts(self):
        """Suites in a fixed order; every suite's and the root's counts equal what
        is in them — a CI summary reads the attributes, not the children."""
        for sc in _scenarios():
            with self.subTest(sc.name):
                root, *_ = _render(sc)
                self.assertEqual(root.tag, "testsuites")
                self.assertEqual([s.get("name") for s in root.findall("testsuite")],
                                 list(SUITES))
                props = _props(root)
                for key in ("spine_version", "exit_code", "tier", "ready", "when"):
                    self.assertIn(key, props)
                self.assertEqual(props["when"], WHEN)
                self.assertEqual(props["tier"], "0")
                totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
                for suite in root.findall("testsuite"):
                    kinds = [_kind(tc) for tc in suite.findall("testcase")]
                    counts = {"tests": len(kinds), "failures": kinds.count("failure"),
                              "errors": kinds.count("error"),
                              "skipped": kinds.count("skipped")}
                    self.assertEqual({k: int(suite.get(k)) for k in counts}, counts,
                                     suite.get("name"))
                    for k in totals:
                        totals[k] += counts[k]
                self.assertEqual({k: int(root.get(k)) for k in totals}, totals)

    def test_rendering_is_deterministic(self):
        for sc in _scenarios():
            with self.subTest(sc.name):
                self.assertEqual(_render(sc)[3], _render(sc)[3])

    def test_selftest_junit_counts_controls_and_baselines(self):
        """`gate selftest --junit`: one testcase per control, childless iff it
        fired; BROKEN (did not fire, or crashed) is red; a tooling skip is
        skipped. Pack mode adds `baselines`. And the command's own "nothing ran"
        exit is never an empty, green file."""
        fired = Verdict(gate="g.a#selftest", passed=True, pack="beam-analytic",
                        detail="correctly failed on selftest/bad.py", duration_s=0.5)
        passed_bad = Verdict(gate="g.b#selftest", passed=False,
                             detail="g.b PASSED its own known-bad fixture selftest/bad.py")
        crashed = Verdict(gate="g.c#selftest", passed=False,
                          error="fixture raised KeyError: 'span'", detail="Traceback ...")
        tooling = Verdict(gate="g.d#selftest", passed=False, skipped=True,
                          skip_reason="requires openfoam (not on PATH)")
        results = [fired, passed_bad, crashed, tooling]
        root = ET.fromstring(report_mod.render_selftest_junit(results, exit_code=1, when=WHEN))
        self.assertEqual([s.get("name") for s in root.findall("testsuite")], ["controls"])
        cases = _cases(_suite(root, "controls"))
        self.assertEqual(list(cases), ["g.a", "g.b", "g.c", "g.d"])
        self.assertEqual([_kind(tc) for tc in cases.values()],
                         ["pass", "failure", "error", "skipped"])
        self.assertEqual(cases["g.a"].get("classname"), "pack.beam-analytic")
        self.assertEqual(len(list(cases["g.a"])), 0)
        broken = [v for v in results if not v.ok and not v.skipped]
        self.assertEqual(_red(_suite(root, "controls")), len(broken))
        self.assertEqual(_props(root)["exit_code"], "1")

        good_base = Verdict(gate="g.a", passed=True, pack="beam-analytic")
        bad_base = Verdict(gate="g.b", passed=False, pack="beam-analytic",
                           detail="fails its own baseline: 0.9 mm (limit 0.5 mm)")
        root = ET.fromstring(report_mod.render_selftest_junit(
            [fired], exit_code=1, when=WHEN, baselines=[good_base, bad_base]))
        self.assertEqual([s.get("name") for s in root.findall("testsuite")],
                         ["controls", "baselines"])
        base = _cases(_suite(root, "baselines"))
        self.assertEqual([_kind(tc) for tc in base.values()], ["pass", "failure"])
        self.assertEqual(_red(_suite(root, "controls")), 0)

        # Zero controls ran and the command exits 1: the file says why, in red.
        root = ET.fromstring(report_mod.render_selftest_junit([], exit_code=1, when=WHEN))
        self.assertGreater(_red(_suite(root, "controls")), 0)
        # ... and `--allow-empty` (exit 0) is an empty suite, not a planted failure.
        root = ET.fromstring(report_mod.render_selftest_junit([], exit_code=0, when=WHEN))
        self.assertEqual(len(_suite(root, "controls").findall("testcase")), 0)
        # All fired and exit 0: nothing red anywhere.
        root = ET.fromstring(report_mod.render_selftest_junit([fired], exit_code=0, when=WHEN))
        self.assertEqual(_red(_suite(root, "controls")), 0)
        # Controls all fired but the command still exits 1 (a failed baseline the
        # caller did not pass): the exit code is never contradicted by a green file.
        root = ET.fromstring(report_mod.render_selftest_junit([fired], exit_code=1, when=WHEN))
        self.assertGreater(_red(_suite(root, "controls")), 0)

    def test_the_default_path_is_ignored_scratch(self):
        """`.atompipe/out/` is gate scratch, ignored by the project's own
        `.atompipe/.gitignore` — so `check --junit` never dirties the tree."""
        self.assertEqual(report_mod.JUNIT_DEFAULT, ".atompipe/out/junit.xml")


if __name__ == "__main__":
    unittest.main(verbosity=2)
