# SPDX-License-Identifier: Apache-2.0
"""An owner written into a claim file by hand — or by an agent — never counts.

GLOSSARY §3: *Assumed* is "accepted provisionally with a named reason and owner",
and with no owner the claim reads Gap. PLAN-v0.14 §1.4: the owner is set only
through the signing channel (D-12, D-13); "an owner written any other way — a
hand or agent edit of the claim file — reads unattributed and the claim stays
Gap". P2.1 adds `Claim.owner` (a nominee) and reads it only against `owners`,
the attributions the channel produces — and nothing in P2.1 produces one. So an
owner typed into `claims/C6.json` names who should record it, and changes
nothing else, on any channel.

* **AnOwnerWrittenByHandNeverCounts** (planned invariant 11, `test_meta`'s
  `PLANNED_INVARIANT_CLASSES[11]`: "a human or physical terminal is satisfied only
  by a result entered through a channel the proposer cannot author") — D8's rows
  in process, then end to end on a bracket copy whose `claims/C6.json` carries
  `"owner": "Sam"`, written as an agent's Edit would write it: C6 reads Gap with
  the unattributed reason naming Sam on `check` (exit 1, C6 listed), its JUnit
  report (C6 red), `status` and its JSON, the report and its JSON, `claim
  list`/`show`, `why` and `state.json` — and never the word *assumed*. No command
  writes the field, and the strict reader accepts `owner` and refuses `ownr`,
  naming it. It moves to `INVARIANT_CLASSES[11]` with CLAUDE.md's 11, when the
  signing channel lands.
* **ARecordedResultNeverOutranksTheEvaluators** — the same copy's C5 is tagged
  so the bracket's failing deflection evaluator covers it: a physical pass typed
  with `claim physical` leaves C5 Failing, and the command's own printed row
  says so (review of the P2.1 design: it printed `[ok   ]` from the result alone,
  a second status producer, while `status` read Failing).

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_owner.py -v
"""
from __future__ import annotations

import ast
import json
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from typing import Any, NamedTuple
from unittest import mock

import _env
import _projects
from nopekit import claims as claims_mod
from nopekit import report as report_mod
from nopekit import store as store_mod
from nopekit.models import ClaimKind, ClaimStatus, Ledger, ProjectMeta
from nopekit.util import NopekitError

#: The name written into C6's file, as an agent's Edit would.
NOMINEE = "Sam"

#: The reason the unattributed claim must carry (P2.1-D15), typed here — and,
#: from P2.5a, the act that records it (R-6 in words: until then it said "no
#: command can record it yet", because none could).
UNATTRIBUTED = (f"owner {NOMINEE} is named in claims/C6.json and has not recorded it — "
                f"{NOMINEE} records it in their own shell: nopekit claim physical C6 assume")

_RUNS = (("check", ["check", "--junit"]), ("status", ["status"]),
         ("status.json", ["status", "--json"]), ("report", ["report"]),
         ("report.json", ["report", "--json"]), ("claim.list", ["claim", "list"]),
         ("claim.list.json", ["claim", "list", "--json"]),
         ("claim.show", ["claim", "show", "C6"]), ("why", ["why", "C6"]),
         ("site.init", ["site", "init"]), ("site.build", ["site", "build"]),
         # Last: it records a result, and the runs above read C5 without one —
         # from a pipe, with the evidence and the written test a pass needs
         # (P2.5a-D9), so it is recorded and counts for nothing.
         ("claim.physical", ["claim", "physical", "C5", "--pass", "--detail", "looked fine",
                             "--evidence", "photos/c5.jpg"]),
         ("claim.physical.json", ["status", "--json"]))


class _Owned(NamedTuple):
    root: str
    out: dict[str, Any]
    junit: str
    state: dict
    c6_bytes: bytes


_OWNED: list[_Owned] = []


def _owned_project() -> _Owned:
    """The bracket, migrated, with C6 naming an owner by hand and C5 tagged so
    the failing deflection evaluator covers it; every command run once."""
    if _OWNED:
        return _OWNED[0]
    tmp = tempfile.mkdtemp(prefix="nopekit-owner-")
    unittest.addModuleCleanup(_env._rmtree, tmp)
    root = _projects.bracket_copy(os.path.join(tmp, "bracket"), migrated=True)
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    path = os.path.join(root, "claims", "C6.json")
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    record["owner"] = NOMINEE
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    with open(path, "rb") as fh:
        c6_bytes = fh.read()
    c5 = os.path.join(root, "claims", "C5.json")
    with open(c5, encoding="utf-8") as fh:
        record = json.load(fh)
    record["tags"] = ["stiffness"]
    record["note"] = "outdoor rack, two winters, look for crazing at the root"
    with open(c5, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    os.makedirs(os.path.join(root, "photos"))
    with open(os.path.join(root, "photos", "c5.jpg"), "wb") as fh:
        fh.write(b"a photo")
    out = {key: _env.nopekit(argv, cwd=root, home=home, identity=True)
           for key, argv in _RUNS}
    for key, proc in out.items():
        want = 1 if key == "check" else 0
        if proc.returncode != want:
            raise AssertionError(f"`nopekit {key}` exited {proc.returncode}, not {want}:\n"
                                 f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    with open(os.path.join(root, ".nopekit", "out", "junit.xml"), encoding="utf-8") as fh:
        junit = fh.read()
    with open(os.path.join(root, "site", "data", "state.json"), encoding="utf-8") as fh:
        state = json.load(fh)
    run = _Owned(root, out, junit, state, c6_bytes)
    _OWNED.append(run)
    return run


def _c6_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if re.match(r"^\[.{5}\] C6\b", ln)]


def owner_problems(run: _Owned) -> list[str]:
    """Every channel where C6 — an owner in its file, no attribution — reads
    anything but Gap with the unattributed reason, or says *assumed*."""
    out: list[str] = []
    check = run.out["check"].stdout
    rows = _c6_lines(check)
    if not rows or not rows[0].startswith("[gap  ] C6") or not rows[0].endswith(UNATTRIBUTED):
        out.append(f"check: C6 {rows}")
    case = ET.fromstring(run.junit).find("testsuite[@name='claims.critical']/testcase[@name='C6']")
    kids = [(k.tag, k.get("type"), k.get("message")) for k in case] if case is not None else []
    if kids != [("failure", "unclaimed", UNATTRIBUTED)]:
        out.append(f"check.junit: C6 {kids}")
    for key in ("status.json", "report.json"):
        doc = json.loads(run.out[key].stdout)
        view = (doc.get("statuses") or {}).get("C6") or {}
        if (doc["claims"].get("C6"), view.get("word"), view.get("cause"), view.get("reason")) \
                != ("unclaimed", "gap", "owner-unattributed", UNATTRIBUTED):
            out.append(f"{key}: C6 {doc['claims'].get('C6')} {view}")
        if "C6" not in doc["summary"].get("blocking_ids", []):
            out.append(f"{key}: C6 is not blocking")
    status = _c6_lines(run.out["status"].stdout)
    if not status or not status[0].startswith("[gap  ] C6") or UNATTRIBUTED not in status[0]:
        out.append(f"status: C6 {status}")
    report = run.out["report"].stdout
    gaps = report.split("## Gaps", 1)[-1].split("\n## ", 1)[0]
    if not re.search(rf"^- \*\*C6\*\* .* — {re.escape(UNATTRIBUTED)}$", gaps, re.M):
        out.append("report: C6 is not under Gaps with its reason")
    assumed = report.split("## Assumed", 1)[-1].split("\n## ", 1)[0]
    if re.search(r"\bC6\b", assumed):
        out.append("report: C6 is under Assumed")
    listed = _c6_lines(run.out["claim.list"].stdout)
    if not listed or not listed[0].startswith("[gap  ] C6"):
        out.append(f"claim.list: C6 {listed}")
    rows = {r["id"]: r for r in json.loads(run.out["claim.list.json"].stdout)["claims"]}
    if (rows["C6"].get("status"), rows["C6"].get("cause")) != ("unclaimed", "owner-unattributed"):
        out.append(f"claim.list.json: C6 {rows['C6'].get('status')}/{rows['C6'].get('cause')}")
    if not run.out["claim.show"].stdout.startswith("[gap  ] C6"):
        out.append(f"claim.show: {run.out['claim.show'].stdout.splitlines()[:1]}")
    why = run.out["why"].stdout
    if UNATTRIBUTED not in " ".join(why.split()):
        out.append("why: C6's line does not name the unattributed owner")
    row = next((r for r in run.state.get("claims") or () if r.get("id") == "C6"), {})
    if (row.get("status"), row.get("cause"), row.get("reason")) != (
            "unclaimed", "owner-unattributed", UNATTRIBUTED):
        out.append(f"site: C6 {row.get('status')}/{row.get('cause')}")
    for key in ("check", "status", "report", "claim.list", "claim.show", "why"):
        for line in run.out[key].stdout.splitlines():
            if re.search(r"\bC6\b", line) and re.search(r"\bassumed\b", line, re.I):
                out.append(f"{key}: says assumed for C6: {line[:80]}")
    return out


class AnOwnerWrittenByHandNeverCounts(_env.EnvCase):
    """(planned 11) An owner named in a claim file is a nominee, never an
    attribution: the assumption reads Gap until the signing channel records it."""

    # -- in process: D8's rows -------------------------------------------- #
    def _claim(self, **kw):
        from nopekit.models import Claim
        fields = dict(id="C6", statement="the load is static", kind=ClaimKind.ASSUMPTION,
                      rationale="no fatigue term", owner=NOMINEE)
        fields.update(kw)
        return Claim(**fields)

    def test_an_owner_counts_only_as_recorded_and_only_as_it_still_reads(self):
        signed = {"C6": claims_mod.Attribution(NOMINEE, "no fatigue term")}
        cases = {
            "an owner in the file only": (self._claim(), None, "owner-unattributed"),
            "no owner named": (self._claim(owner=""), signed, "no-owner"),
            "no reason": (self._claim(rationale=""), signed, "no-reason"),
            "recorded under another name": (self._claim(owner="Alex"), signed,
                                            "owner-unattributed"),
            "the rationale edited after": (self._claim(rationale="it is fine"), signed,
                                           "owner-unattributed"),
            "recorded, as it reads": (self._claim(), signed, "owned"),
        }
        for name, (claim, owners, cause) in cases.items():
            with self.subTest(name):
                found = claims_mod.compose(claim, [], owners=owners)
                self.assertEqual(found.cause.value, cause)
                self.assertEqual(found.status, ClaimStatus.ASSERTED if cause == "owned"
                                 else ClaimStatus.UNCLAIMED)

    def test_a_compose_that_trusts_the_file_is_caught(self):
        """Planted: a composition that reads `claim.owner` as attributed — C6
        Assumed with nothing recorded — and a report that lists it so."""
        real = claims_mod.compose

        def trusts(claim, verdicts, **kw):
            if claim.owner:
                kw["owners"] = {claim.id: claims_mod.Attribution(claim.owner, claim.rationale)}
            return real(claim, verdicts, **kw)

        ledger = Ledger(meta=ProjectMeta(name="t", revision="v0.1"), claims=[self._claim()])
        with mock.patch.object(claims_mod, "compose", trusts):
            md = report_mod.render_markdown(ledger, None)
            status = claims_mod.resolve_status(ledger.claims[0], [])
        self.assertEqual(status, ClaimStatus.ASSERTED)
        assumed = md.split("## Assumed", 1)[1].split("\n## ", 1)[0]
        self.assertIn("**C6**", assumed, "the planted compose did not reach the report")

    # -- end to end ---------------------------------------------------------- #
    def test_every_channel_reads_gap_with_the_unattributed_reason(self):
        self.assertEqual(owner_problems(_owned_project()), [])

    def test_the_channel_checks_refuse_what_they_forbid(self):
        """Planted: the outputs a trusting spine would print — C6 `[assum]` on
        `status`, Assumed in JSON, a skipped JUnit case saying "assumed"."""
        run = _owned_project()
        status = run.out["status"].stdout.replace("[gap  ] C6", "[assum] C6")
        doc = json.loads(run.out["status.json"].stdout)
        doc["claims"]["C6"] = "asserted"
        junit = re.sub(r'(<testcase[^>]*name="C6"[^>]*>).*?(</testcase>)',
                       r'\1<skipped message="assumed" />\2', run.junit, count=1, flags=re.S)

        class _Out(NamedTuple):
            stdout: str

        planted = run._replace(junit=junit, out=dict(run.out, status=_Out(status),
                                                     **{"status.json": _Out(json.dumps(doc))}))
        found = owner_problems(planted)
        for want in ("status: C6", "status.json: C6", "check.junit: C6"):
            with self.subTest(want):
                self.assertTrue(any(p.startswith(want) for p in found), found)

    def test_no_command_writes_the_owner(self):
        """Every command above left C6's file as the hand edit wrote it, and no
        code under `src/nopekit` sets `owner` on a claim: the field's only
        producer is a file edit, and the edit counts for nothing (the signing
        channel, when it lands, writes attributions, not this field)."""
        run = _owned_project()
        with open(os.path.join(run.root, "claims", "C6.json"), "rb") as fh:
            self.assertEqual(fh.read(), run.c6_bytes)
        hits = []
        src = os.path.join(_env.REPO, "src", "nopekit")
        for name in sorted(os.listdir(src)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(src, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                # A record built or copied with an owner: `Claim(owner=…)`,
                # `replace(claim, owner=…)`. (`str.format(owner=…)` is a word.)
                if isinstance(node, ast.Call) and any(k.arg == "owner" for k in node.keywords):
                    func = node.func
                    called = func.attr if isinstance(func, ast.Attribute) else getattr(
                        func, "id", "")
                    if called in ("Claim", "replace"):
                        hits.append(f"{name}:{node.lineno}")
                if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Attribute) and target.attr == "owner" \
                                and name != "models.py":
                            hits.append(f"{name}:{target.lineno}")
        self.assertEqual(hits, [], "a spine path sets Claim.owner")

    def test_the_strict_reader_accepts_owner_and_names_a_misspelling(self):
        root = self.tmp()
        good = os.path.join(root, "C6.json")
        with open(good, "w", encoding="utf-8") as fh:
            json.dump({"statement": "s", "kind": "assumption", "owner": NOMINEE}, fh)
        self.assertEqual(store_mod.read_record(good, "claims").owner, NOMINEE)
        bad = os.path.join(root, "C7.json")
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump({"statement": "s", "kind": "assumption", "ownr": NOMINEE}, fh)
        with self.assertRaises(NopekitError) as caught:
            store_mod.read_record(bad, "claims")
        self.assertIn("ownr", str(caught.exception))
        self.assertIn("owner", str(caught.exception))


def physical_row_problems(stdout: str) -> list[str]:
    """`claim physical C5 --pass`'s printed row, beside the failing deflection
    evaluator that covers C5: Failing, citing that evaluator."""
    rows = [ln for ln in stdout.splitlines() if re.match(r"^\[.{5}\] C5 ", ln)]
    if len(rows) != 1:
        return [f"{len(rows)} rows for C5"]
    if not rows[0].startswith("[FAIL ] C5 "):
        return [f"C5 tagged {rows[0][:7]}"]
    if "bracket.deflection : 0.700 mm" not in rows[0]:
        return ["C5's reason does not cite the failing evaluator"]
    return []


class ARecordedResultNeverOutranksTheEvaluators(_env.EnvCase):
    """A physical pass typed with `claim physical` beside a covering evaluator
    that fails leaves the claim Failing (S-49, R-3), and the command's own row
    says so — `claim physical` prints the status every reader composes, never
    one built from the result alone (R-5)."""

    def test_the_command_prints_the_composed_status(self):
        run = _owned_project()
        self.assertEqual(physical_row_problems(run.out["claim.physical"].stdout), [])
        after = json.loads(run.out["claim.physical.json"].stdout)
        self.assertEqual(after["claims"]["C5"], "fail")

    def test_the_row_says_what_was_recorded_once(self):
        """The row names who recorded the result in GLOSSARY's words — `recorded,
        unattributed` with no `--who`, never "recorded by unattributed" — and says
        the detail once. What slipped through (review of P2.1): the reason's
        template filled "unattributed" in as a name, and the command appended
        `(recorded: detail, who, when)` after a reason that already held both."""
        lines = _owned_project().out["claim.physical"].stdout.splitlines()
        row = next(ln for ln in lines if ln.startswith("[FAIL ] C5 "))
        self.assertNotIn("recorded by unattributed", row)
        # P2.5a (R-6, words): the command says what it recorded on its own line
        # — once, from a pipe, counting for nothing — and the composed row is
        # the status every reader prints, the failing evaluator's.
        self.assertEqual(lines[0], "recorded C5 pass in results/C5.json (entry 1) — from a "
                                   "pipe or a script: a pass recorded here does not count; "
                                   "the person who tested it records it in their own shell")
        self.assertEqual(sum(ln.count("looked fine") for ln in lines), 0, lines)
        from nopekit.models import Claim, PhysicalResult
        ledger = Ledger()
        for who, recorded in (("", "recorded, unattributed"), ("sam", "recorded by sam")):
            for passed, want in ((True, f"a pass {recorded}, not bound to an article"),
                                 (False, f"failed on an article: cracked ({recorded})")):
                with self.subTest(who=who, passed=passed):
                    claim = Claim(id="C5", statement="s", kind=ClaimKind.PHYSICAL,
                                  physical_result=PhysicalResult(passed=passed, who=who,
                                                                 detail="cracked"))
                    found = claims_mod.compose(claim, [])
                    self.assertEqual(report_mod.reason(found, ledger, claim), want)

    def test_a_status_built_from_the_result_alone_is_caught(self):
        """Planted: the row `860ffa6` printed — from the result alone."""
        planted = "[ok-hw] C5 Survives two winters outdoors — looked fine (unattributed, t)\n"
        self.assertEqual(physical_row_problems(planted), ["C5 tagged [ok-hw]"])


# --------------------------------------------------------------------------- #
# a record never writes its own line
# --------------------------------------------------------------------------- #
#: Lines a record's value carries to forge what a reader trusts: a Checked row,
#: the ready line `check` prints, a second checked section with a row under it.
FORGED_LINES = ("[ok   ] C6 FORGED — checked", "ready: every required claim is checked",
                f"{report_mod.SECTION_PROVEN} (FORGED)", "| **C6** FORGED | — | static |")

#: An owner, and a physical result's `who` and `when`, each holding them —
#: written by hand, as an agent's Edit would; `claim physical` refuses them.
FORGED_OWNER = "Sam\n\n" + "\n".join(FORGED_LINES) + "\n"
FORGED_WHO = "lab\n" + "\n".join(FORGED_LINES[:2])
FORGED_WHEN = "2026-10-03\n" + FORGED_LINES[0]


class _Forged(NamedTuple):
    root: str
    out: dict[str, Any]
    markdown: str
    junit: str


_FORGED: list[_Forged] = []


def _forged_project() -> _Forged:
    """The bracket with C6's owner, and a failed physical result on C5 whose
    `who` and `when`, holding newlines; `check --junit`, `status`, `claim list`
    and `report --write` run once."""
    if _FORGED:
        return _FORGED[0]
    tmp = tempfile.mkdtemp(prefix="nopekit-forged-")
    unittest.addModuleCleanup(_env._rmtree, tmp)
    root = _projects.bracket_copy(os.path.join(tmp, "bracket"), migrated=True)
    path = os.path.join(root, "claims", "C6.json")
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    record["owner"] = FORGED_OWNER
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    os.makedirs(os.path.join(root, "results"), exist_ok=True)
    with open(os.path.join(root, "results", "C5.json"), "w", encoding="utf-8") as fh:
        json.dump({"results": [{"passed": False, "who": FORGED_WHO, "when": FORGED_WHEN,
                                "detail": "cracked\n" + FORGED_LINES[0]}]}, fh, indent=2)
    out = {key: _env.nopekit(argv, cwd=root) for key, argv in (
        ("check", ["check", "--junit"]), ("status", ["status"]),
        ("claim.list", ["claim", "list"]), ("report", ["report", "--write"]))}
    for key, proc in out.items():
        if proc.returncode != (1 if key == "check" else 0):
            raise AssertionError(f"`nopekit {key}` exited {proc.returncode}:\n"
                                 f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    # P2.5b-D17: `report --write` renders REPORT.md at the root (R-6: the same
    # rendering, read where it is written now).
    with open(os.path.join(root, "REPORT.md"), encoding="utf-8") as fh:
        markdown = fh.read()
    with open(os.path.join(root, ".nopekit", "out", "junit.xml"), encoding="utf-8") as fh:
        junit = fh.read()
    run = _Forged(root, {k: p.stdout for k, p in out.items()}, markdown, junit)
    _FORGED.append(run)
    return run


def forged_line_problems(channels: dict[str, str], junit: str) -> list[str]:
    """Every line of a channel that begins as a forged line does, a second
    checked section, and a JUnit message holding a line break or naming a
    claim the ledger lacks."""
    out: list[str] = []
    for channel, text in channels.items():
        for line in text.splitlines():
            if line.startswith(FORGED_LINES) or line.startswith(("[ok   ] C6", "ready:")):
                out.append(f"{channel}: a forged line: {line[:60]}")
        heads = [ln for ln in text.splitlines() if ln.startswith(report_mod.SECTION_PROVEN)]
        if len(heads) > 1:
            out.append(f"{channel}: {len(heads)} checked sections")
    root = ET.fromstring(junit)
    for case in root.iter("testcase"):
        for kid in case:
            message = kid.get("message") or ""
            if "\n" in message or "\r" in message:
                out.append(f"junit: {case.get('name')}'s message breaks its line")
    return out


class ARecordNeverWritesItsOwnLine(_env.EnvCase):
    """A value a record supplies — an owner, a physical result's `who` and
    `when` — is one line on every channel. What slipped through (review of
    P2.1): the owner and `who` went into the claim's reason raw, so a record
    holding a newline wrote a second `## What is PROVEN` with a forged row into
    the report, and an `[ok   ] C5 … — checked` row and a `ready:` line under
    the real `[FAIL ]` in `status` and `check`. The exit code held; the human
    and agent channels read Checked and ready."""

    def test_no_channel_prints_a_forged_line(self):
        run = _forged_project()
        channels = dict(run.out, markdown=run.markdown)
        self.assertEqual(forged_line_problems(channels, run.junit), [])
        self.assertEqual(run.markdown.count(f"\n{report_mod.SECTION_PROVEN}"), 1)
        # The values are there, on one line each — said, not dropped.
        rows = run.out["status"].splitlines()
        self.assertTrue(any(ln.startswith("[gap  ] C6 ") and "owner Sam " in ln
                            and "FORGED" in ln for ln in rows), run.out["status"])
        self.assertTrue(any(ln.startswith("[FAIL ] C5 ") and "recorded by lab " in ln
                            for ln in rows), run.out["status"])

    def test_a_reason_that_takes_the_value_raw_is_caught(self):
        """Planted: `report._one` as P2.1 had it in effect — the value raw."""
        run = _forged_project()
        ledger = store_mod.load(run.root)
        with mock.patch.object(report_mod, "_one", lambda text: str(text or "")):
            channels = {"render_terminal": report_mod.render_terminal(ledger, None),
                        "render_markdown": report_mod.render_markdown(ledger, None)}
        found = forged_line_problems(channels, "<testsuites/>")
        self.assertTrue(any(p.startswith("render_terminal: a forged line") for p in found),
                        found)
        self.assertTrue(any(re.fullmatch(r"render_markdown: [2-9] checked sections", p)
                            for p in found), found)

    def test_an_escape_never_reaches_a_terminal(self):
        """(review of P2.5a) A statement holding ``\\x1b[1G\\x1b[2K`` erased itself
        on `claim physical`'s prompt and printed another claim's words in its
        place — the one line a person reads before typing the id they settle —
        and the pass was sealed to the hidden sentence. Every control character
        reaches a terminal as visible text. Planted: `report._trunc` collapsing
        whitespace alone, as P2.5a had it."""
        import _physical as P
        root = P.project(os.path.join(self.tmp(), "b"), planted=("C9",))
        hidden = ("Holds 40 kg above a cot for ten years\x1b[1G\x1b[2KC9 The printed "
                  "hook's colour matches the sample card")
        P.edit_claim(root, "C9", statement=hidden)
        proc = P.tty(root, "claim", "physical", "C9", "pass", "--detail", "fine\x1b[2K",
                     "--evidence", P.EVIDENCE, answer="C9", code=0)
        self.assertNotIn("\x1b", proc.stderr + proc.stdout)
        self.assertIn("ten years\\x1b[1G\\x1b[2KC9 The printed", proc.stderr)
        status = P.run(root, "status", code=0).stdout
        self.assertNotIn("\x1b", status)
        ledger = store_mod.load(root)
        with mock.patch.object(report_mod, "_trunc", lambda text, limit: " ".join(
                str(text or "").split())):
            planted = report_mod.render_terminal(ledger, None)
        self.assertIn("\x1b", planted, "a whitespace-only line was not caught")

    def test_claim_physical_refuses_a_value_that_breaks_its_line(self):
        run = _forged_project()
        path = os.path.join(run.root, "results", "C5.json")
        with open(path, "rb") as fh:
            before = fh.read()
        # From P2.5a every value is refused, before anything is read (R-6: only
        # a multi-line one was, while --who and --when were still taken).
        for flag, value in (("--who", FORGED_WHO), ("--when", FORGED_WHEN),
                            ("--who", "a tester"), ("--when", "2026-10-03")):
            with self.subTest(flag=flag, value=value):
                proc = _env.nopekit(["claim", "physical", "C5", "--pass", flag, value],
                                     cwd=run.root, identity=True)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn(f"{flag} is not accepted", proc.stderr)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before, "a refused result was written")


if __name__ == "__main__":
    unittest.main(verbosity=2)
