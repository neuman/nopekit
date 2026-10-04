# SPDX-License-Identifier: Apache-2.0
"""Invariant 11, the channel half: a measurement, a judgment, an owner or an
authority counts only when a person enters it in their own shell (P2.5a).

GLOSSARY §1 *recorded by*: who entered a physical result is derived from the git
identity, never typed (D-12). PLAN-v0.14 §1.4: an owner counts only when recorded
through the signing channel. What slipped through before this file (S-48):
`claim physical C5 --pass --who Sam` took a name the agent typed and defaulted to
nobody, so C5 read "verified" in the same second it was claimed; and an owner was
whatever a claim file said.

* **HumanChannelOnly** (V-1) — the channel is derived (`cli._channel`): from a
  TTY with no agent marker, once the claim's id is typed, `interactive`;
  anything else from a TTY writes nothing; an agent marker makes it
  `agent-session`, a pipe `non-interactive`, and neither makes a pass count.
* **WhoAndWhenAreNeverTyped** (V-2) — `--who` and `--when` are refused before
  the project is read; `who` is git's identity, and with none nothing is written.
* **TheResultsFileIsSealedAndChained** (V-3) — every entry sealed (its claim id
  and list inside the seal) and chained; a broken seal refuses the file, naming
  it, the list, the entry and the fix — and, where the fix would discard an
  uncommitted fail, that fail (critique 6 of the P2.5a design).
* **AnOwnerOnlyThroughTheChannel** (V-4) — `claim physical <id> assume`, typed
  by the owner in their own shell, is the only way an owner counts.

Each row carries a planted violator the check must catch (R-12: a checker that
has never refused anything is a logger).

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_signing.py -v
"""
from __future__ import annotations

import ast
import contextlib
import copy
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from typing import Any, Callable
from unittest import mock

import _env
import _physical as P
from atompipe import cli, claims, store
from atompipe.util import AtompipeError


def _need(module: Any, name: str) -> Any:
    """``module.name``, or an assertion naming what is missing — so a test written
    before the code it holds is red for its reason, never an ImportError."""
    found = getattr(module, name, None)
    if found is None:
        raise AssertionError(f"{module.__name__}.{name} does not exist yet")
    return found


def _tree(root: str) -> dict[str, bytes]:
    """Every file under ``root`` except the generated index and caches, for a
    nothing-was-written comparison."""
    out: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "cache", "obs", "out")]
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel in (".atompipe/ledger.json", ".atompipe/build.lock"):
                continue
            with open(path, "rb") as fh:
                out[rel] = fh.read()
    return out


# --------------------------------------------------------------------------- #
# V-1 — the channel
# --------------------------------------------------------------------------- #
#: (row, stdin is a TTY, environment, typed answer, what the channel must be —
#: a string, or None for "refused, nothing written").
CHANNEL_ROWS: tuple = (
    ("CLAUDECODE alone", False, {"CLAUDECODE": "1"}, None, "agent-session unknown"),
    ("AI_AGENT alone", False, {"AI_AGENT": "claude"}, None, "agent-session unknown"),
    ("a session id", False, {"CLAUDE_CODE_SESSION_ID": "s1"}, None, "agent-session s1"),
    ("a child session", False, {"CLAUDE_CODE_CHILD_SESSION": "1"}, None,
     "agent-session unknown"),
    ("a future CLAUDE_CODE_ marker", False, {"CLAUDE_CODE_X": "1"}, None,
     "agent-session unknown"),
    ("a pipe, no marker", False, {}, None, "non-interactive"),
    ("a marker beside an ATOMPIPE_CHANNEL a generator set", False,
     {"CLAUDECODE": "1", "ATOMPIPE_CHANNEL": "interactive"}, None, "agent-session unknown"),
    ("a terminal, the id typed", True, {}, "C5", "interactive"),
    ("a terminal, the id typed with spaces", True, {}, "  C5 ", "interactive"),
    ("a terminal with a marker", True, {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s2"},
     "C5", "agent-session s2"),
    ("a terminal, a person's own CLAUDE_API_KEY", True, {"CLAUDE_API_KEY": "k"}, "C5",
     "interactive"),
    ("a terminal, another id typed", True, {}, "C4", None),
    ("a terminal, an empty line", True, {}, "", None),
    ("a terminal, end of input", True, {}, None, None),
)


def channel_problems(channel: Callable[..., str]) -> list[str]:
    """Every CHANNEL_ROWS row ``channel`` gets wrong."""
    out: list[str] = []
    for row, tty, environ, answer, want in CHANNEL_ROWS:
        try:
            got = channel(tty, environ, answer, "C5")
        except AtompipeError:
            got = None
        if got != want:
            out.append(f"{row}: {got!r}, not {want!r}")
    return out


class HumanChannelOnly(_env.EnvCase):
    """(V-1, invariant 11) Only `interactive` makes a pass count or an
    attribution exist, and only a person typing the claim's id in a terminal
    with no agent marker is `interactive`."""

    def test_the_channel_table(self):
        self.assertEqual(channel_problems(_need(cli, "_channel")), [])

    def test_planted_channels_are_caught(self):
        real = _need(cli, "_channel")

        def reads_a_variable(tty, environ, answer, claim_id):
            return environ.get("ATOMPIPE_CHANNEL") or real(tty, environ, answer, claim_id)

        def trusts_the_tty(tty, environ, answer, claim_id):
            return "interactive" if tty else "non-interactive"

        def reads_markers_only(tty, environ, answer, claim_id):
            if any(k == "CLAUDECODE" or k == "AI_AGENT" or k.startswith("CLAUDE_CODE_")
                   for k in environ):
                return real(tty, environ, answer, claim_id)
            return "interactive"

        def any_line(tty, environ, answer, claim_id):
            return real(tty, environ, claim_id if tty else answer, claim_id)

        planted = {"reads ATOMPIPE_CHANNEL": reads_a_variable, "trusts isatty": trusts_the_tty,
                   "markers only": reads_markers_only, "accepts any line": any_line}
        for name, fn in planted.items():
            with self.subTest(name):
                self.assertNotEqual(channel_problems(fn), [], f"{name} was not caught")

    def test_no_flag_sets_the_channel(self):
        parser = cli.build_parser()
        for flag in ("--channel", "--via", "--interactive", "--signed", "--yes"):
            with self.subTest(flag), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args(["claim", "physical", "C5", "pass", flag, "x"])

    def test_an_agents_pass_is_recorded_and_settles_nothing(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        proc = P.run(root, "claim", "physical", "C5", "pass", "--detail", "no cracking",
                     "--evidence", P.EVIDENCE, agent=True, code=0)
        entry = P.results(root, "C5")["results"][-1]
        self.assertEqual(entry.get("channel"), "agent-session s1")
        self.assertIn("from an agent session", proc.stdout)
        self.assertFalse(any(ln.startswith("[ok   ] C5") for ln in proc.stdout.splitlines()),
                         proc.stdout)
        found = P.channels(root, "C5")
        self.assertEqual(P.disagreements(found, "unverified", "physical-pass"), [])
        self.assertFalse(found["ready"])
        self.assertIn("from an agent session", found["status.reason"])
        self.assertTrue(found["status.row"].startswith("[build] C5"), found["status.row"])

    def test_a_pass_typed_in_a_persons_shell_counts(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        proc = P.tty(root, "claim", "physical", "C5", "pass", "--detail", "no cracking",
                     "--evidence", P.EVIDENCE, answer="C5", code=0)
        entry = P.results(root, "C5")["results"][-1]
        self.assertEqual(entry.get("channel"), "interactive")
        self.assertEqual(entry.get("who"), P.WHO)
        prompt = [ln for ln in proc.stderr.splitlines() if ln.strip()]
        self.assertRegex(prompt[0], r"^C5 Survives two winters outdoors")
        self.assertRegex(prompt[1], r"^  measurement · required · reads now: pending build$")
        self.assertRegex(prompt[2], r"^  you record: pass — no cracking \[photos/c5-root\.jpg\]$")
        self.assertRegex(prompt[3], r"^  on article [0-9a-f]{12}: the design as the model "
                                    r"holds it now \(")
        self.assertEqual(prompt[4], f"  recorded by: {P.WHO}")
        self.assertTrue(prompt[5].startswith("type C5 to record it"), prompt)
        self.assertTrue(any(ln.startswith("[ok   ] C5 ") for ln in proc.stdout.splitlines()),
                        proc.stdout)
        found = P.channels(root, "C5")
        self.assertEqual(P.disagreements(found, "verified", "on-article"), [])
        self.assertEqual(found["junit"], [])

    def test_a_mistyped_id_or_no_answer_writes_nothing(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        before = _tree(root)
        for answer in ("C4", "", None):
            with self.subTest(answer=answer):
                proc = P.tty(root, "claim", "physical", "C5", "pass", "--detail", "x",
                             "--evidence", P.EVIDENCE, answer=answer, code=2)
                self.assertIn("nothing was recorded", proc.stderr.lower())
                self.assertEqual(_tree(root), before)

    def test_assume_off_the_terminal_is_refused(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        P.edit_claim(root, "C6", owner=P.NAME)
        before = _tree(root)
        for agent in (True, False):
            with self.subTest(agent=agent):
                proc = P.run(root, "claim", "physical", "C6", "assume", agent=agent, code=2)
                self.assertIn("in their own shell", proc.stderr)
                self.assertIn("atompipe claim physical C6 assume", proc.stderr)
                self.assertEqual(_tree(root), before)


# --------------------------------------------------------------------------- #
# V-2 — who and when are never typed
# --------------------------------------------------------------------------- #
class WhoAndWhenAreNeverTyped(_env.EnvCase):
    """(V-2, invariant 11; S-48) `--who` and `--when` are parsed only to be
    refused, before the project is read; `who` is git's identity."""

    def _broken_project(self) -> str:
        root = P.project(os.path.join(self.tmp(), "b"))
        with open(os.path.join(root, "claims", "C3.json"), "w", encoding="utf-8") as fh:
            fh.write('{"statement": "s", "kindd": "measurable"}\n')
        return root

    def test_every_value_is_refused_before_the_project_is_read(self):
        root = self._broken_project()
        before = _tree(root)
        for flag, value, words in (("--who", "Sam", "git"), ("--who", "", "git"),
                                   ("--who", "Sam\n[ok   ] C5", "git"),
                                   ("--when", "2020-01-01", "--detail")):
            with self.subTest(flag=flag, value=value):
                proc = P.run(root, "claim", "physical", "C5", "fail", flag, value, code=2)
                self.assertIn(f"{flag} is not accepted", proc.stderr)
                self.assertIn(words, proc.stderr)
                self.assertNotIn("kindd", proc.stderr, "the reader ran before the refusal")
                self.assertEqual(_tree(root), before)

    def test_help_lists_neither_and_one_list_names_both(self):
        proc = _env.atompipe(["claim", "physical", "--help"], cwd=self.tmp())
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("--who", proc.stdout)
        self.assertNotIn("--when", proc.stdout)
        self.assertNotIn("real-world", proc.stdout)
        refused = _need(cli, "REFUSED_FLAGS")
        self.assertIn("--who", refused)
        self.assertIn("--when", refused)

    def test_no_identity_records_nothing(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        P.edit_claim(root, "C6", owner=P.NAME)
        before = _tree(root)
        for args, agent in ((("C5", "fail", "--detail", "x"), True),
                            (("C5", "fail", "--detail", "x"), False),
                            (("C5", "pass", "--detail", "x", "--evidence", P.EVIDENCE), False)):
            with self.subTest(args=args, agent=agent):
                proc = P.run(root, "claim", "physical", *args, agent=agent, identity=False,
                             code=2)
                self.assertIn("no git identity here", proc.stderr)
                self.assertEqual(_tree(root), before)
        proc = _env.run_tty(["claim", "physical", "C6", "assume"], cwd=root, answer="C6",
                            identity=False)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("no git identity here", proc.stderr)
        self.assertEqual(_tree(root), before)

    def test_who_is_the_git_identity(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        P.run(root, "claim", "physical", "C5", "fail", "--detail", "cracked", code=0)
        self.assertEqual(P.results(root, "C5")["results"][-1]["who"], P.WHO)

    def test_a_parser_that_accepts_who_is_caught(self):
        """Planted: the refused-flag list emptied — `--who` parsed and ignored. A
        result is written, and the nothing-written check sees it."""
        root = P.project(os.path.join(self.tmp(), "b"))
        before = _tree(root)
        with mock.patch.object(cli, "REFUSED_FLAGS", {}), \
                mock.patch.dict(os.environ, _env.IDENTITY), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            for key in [k for k in os.environ if k == "CLAUDECODE" or k == "AI_AGENT"
                        or k.startswith("CLAUDE_CODE_")]:
                os.environ.pop(key)
            code = cli.main(["-C", root, "claim", "physical", "C5", "fail", "--detail", "x",
                             "--who", "Sam"])
        self.assertEqual(code, 0)
        self.assertNotEqual(_tree(root), before, "the planted parser wrote nothing")


# --------------------------------------------------------------------------- #
# V-3 — the results file is sealed and chained
# --------------------------------------------------------------------------- #
def _write(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def _sealed_file(root: str, cid: str = "C5") -> str:
    """``results/<cid>.json`` with a legacy fail, then three sealed entries
    written by the real ``store.append_signed``: a fail, a pass, a pass."""
    append = _need(store, "append_signed")
    os.makedirs(os.path.join(root, "results"), exist_ok=True)
    path = os.path.join(root, "results", f"{cid}.json")
    _write(path, {"results": [{"passed": False, "when": "2026-01-01T00:00:00Z",
                               "who": "lab", "detail": "legacy no"}]})
    for passed, detail in ((False, "hairline cracks"), (True, "fine after a fix"),
                           (True, "fine again")):
        append(root, cid, "results", {
            "passed": passed, "when": "2026-10-04T10:00:00Z", "who": P.WHO,
            "detail": detail, "evidence": [], "channel": "interactive", "authority": "",
            "measured": None, "units": "", "article": {}, "claim_digest": "c" * 64,
            "rho": "", "evidence_sha256": {}, "contradicts": [], "contradiction_check": ""})
    return path


class TheResultsFileIsSealedAndChained(_env.EnvCase):
    """(V-3, invariant 11) A results file whose seal or chain is broken is
    refused, naming the file, the list, the entry and the fix."""

    def _root(self) -> str:
        root = self.tmp()
        os.makedirs(os.path.join(root, "results"))
        return root

    def _refused(self, path: str, *where: str) -> str:
        with self.assertRaises(AtompipeError) as caught:
            store.read_record(path, "results")
        text = str(caught.exception)
        self.assertIn(os.path.basename(path), text)
        for bit in where:
            self.assertIn(bit, text)
        return text

    def test_a_legacy_prefix_and_a_sealed_chain_read(self):
        root = self._root()
        path = _sealed_file(root)
        found = store.read_record(path, "results")
        self.assertEqual([r.passed for r in found], [False, False, True, True])
        raw = json.load(open(path, encoding="utf-8"))
        self.assertNotIn("digest", raw["results"][0])
        self.assertTrue(all(len(e["digest"]) == 64 for e in raw["results"][1:]))
        self.assertEqual(raw["results"][2]["prev"], raw["results"][1]["digest"])

    def test_every_tampering_is_refused(self):
        def edit_detail(d):
            d["results"][2]["detail"] = "it was fine"

        def channel_up(d):
            d["results"][3]["channel"] = "interactive"
            d["results"][3]["detail"] = "x"

        def flip_fail(d):
            d["results"][1]["passed"] = True

        def drop_middle(d):
            del d["results"][1]

        def swap(d):
            d["results"][2], d["results"][3] = d["results"][3], d["results"][2]

        def unsealed_after(d):
            d["results"].append({"passed": True, "who": "x", "detail": "typed"})

        def edit_prefix(d):
            d["results"][0]["passed"] = True

        def into_attributions(d):
            d["attributions"] = [d["results"].pop(3)]

        rows = {"a sealed detail edited": (edit_detail, "results[2]"),
                "a sealed fail's passed flipped": (flip_fail, "results[1]"),
                "a middle fail removed": (drop_middle, "results[1]"),
                "two entries swapped": (swap, "results[2]"),
                "an unsealed entry after a sealed one": (unsealed_after, "results[4]"),
                "the legacy prefix edited after a sealed entry": (edit_prefix, "results[1]"),
                "a result moved into attributions": (into_attributions, "attributions[0]")}
        for name, (edit, where) in rows.items():
            with self.subTest(name):
                root = self._root()
                path = _sealed_file(root)
                data = json.load(open(path, encoding="utf-8"))
                edit(data)
                _write(path, data)
                text = self._refused(path, where)
                if name != "a result moved into attributions":
                    self.assertRegex(text, r"until it is restored: (git checkout -- "
                                           r"results/C5\.json|restore results/C5\.json)")

    def test_an_entry_copied_to_another_claim_does_not_verify(self):
        root = self._root()
        path = _sealed_file(root, "C5")
        data = json.load(open(path, encoding="utf-8"))
        _write(os.path.join(root, "results", "C1.json"), data)
        self._refused(os.path.join(root, "results", "C1.json"), "results[1]")

    def test_a_hand_written_attribution_is_refused(self):
        root = self._root()
        path = os.path.join(root, "results", "C6.json")
        _write(path, {"results": [], "attributions": [
            {"role": "owner", "name": "Sam", "reason": "r", "who": "Sam <s@x>",
             "when": "2026-10-04T10:00:00Z", "channel": "interactive"}]})
        self._refused(path, "attributions[0]")

    def test_the_strict_reader_closes_every_key(self):
        root = self._root()
        path = os.path.join(root, "results", "C5.json")
        rows = {
            "an unknown entry key": ({"results": [{"passed": True, "chanel": "x"}]}, "channel"),
            "a role outside the two": ({"results": [], "attributions": [{"role": "boss",
                                                                          "name": "x"}]},
                                       "owner"),
            "passed not a bool": ({"results": [{"passed": "yes"}]}, "true or false"),
            "an open-keyed contradicts item": ({"results": [{"passed": False, "contradicts": [
                {"gate": "g", "extra": 1}]}]}, "contradicts"),
            "a non-finite measured": ('{"results": [{"passed": false, "measured": NaN}]}',
                                      "measured"),
        }
        for name, (data, word) in rows.items():
            with self.subTest(name):
                if isinstance(data, str):
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(data)
                else:
                    _write(path, data)
                with self.assertRaises(AtompipeError) as caught:
                    store.read_record(path, "results")
                self.assertIn(word, str(caught.exception))

    def test_append_keeps_every_earlier_entry_and_refuses_a_broken_file(self):
        root = self._root()
        path = _sealed_file(root)
        with open(path, "rb") as fh:
            before = json.loads(fh.read())
        _need(store, "append_signed")(root, "C5", "results", {
            "passed": False, "when": "2026-10-05T00:00:00Z", "who": P.WHO, "detail": "again",
            "evidence": [], "channel": "non-interactive", "authority": "", "measured": None,
            "units": "", "article": {}, "claim_digest": "", "rho": "", "evidence_sha256": {},
            "contradicts": [], "contradiction_check": ""})
        after = json.load(open(path, encoding="utf-8"))
        self.assertEqual(after["results"][:4], before["results"])
        data = copy.deepcopy(after)
        data["results"][2]["detail"] = "edited"
        _write(path, data)
        with self.assertRaises(AtompipeError):
            store.append_signed(root, "C5", "results", dict(after["results"][-1], detail="x"))

    def test_save_never_appends_an_unsealed_entry_after_a_sealed_one(self):
        from atompipe.models import PhysicalResult
        root = P.project(os.path.join(self.tmp(), "b"))
        _sealed_file(root)
        ledger = store.load(root)
        ledger.claim("C5").physical_result = PhysicalResult(passed=False, detail="saved")
        with self.assertRaises(AtompipeError):
            store.save(root, ledger)

    def test_doctor_names_a_refused_file_and_answers(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        path = _sealed_file(root)
        data = json.load(open(path, encoding="utf-8"))
        data["results"][2]["detail"] = "edited"
        _write(path, data)
        proc = P.run(root, "status", code=2)
        self.assertIn("results/C5.json", proc.stderr)
        proc = P.run(root, "doctor")
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("results/C5.json", proc.stdout)

    def test_a_fix_that_would_discard_an_uncommitted_fail_names_it(self):
        """(critique 6 of the P2.5a design) The refusal compares the file with
        what git holds, and names every fail the checkout would drop."""
        root = P.project(os.path.join(self.tmp(), "b"), git=True)
        P.run(root, "claim", "physical", "C1", "fail", "--detail", "sagged 0.9 mm", code=0)
        P.run(root, "claim", "physical", "C1", "fail", "--detail", "sagged again", code=0)
        path = os.path.join(root, "results", "C1.json")
        data = json.load(open(path, encoding="utf-8"))
        data["results"][1]["detail"] = "tidied"
        _write(path, data)
        proc = P.run(root, "status", code=2)
        self.assertIn("results/C1.json", proc.stderr)
        self.assertIn("sagged 0.9 mm", proc.stderr)
        self.assertIn("atompipe claim physical C1 fail", proc.stderr)

    def test_planted_readers_are_caught(self):
        """Planted: a reader that skips verification (every tampering reads),
        and a seal that leaves out the claim id (a copied entry verifies)."""
        self.assertEqual(tamper_problems(self.tmp), [])
        _need(store, "_verify_chain")
        with mock.patch.object(store, "_verify_chain", lambda *a, **k: None):
            self.assertNotEqual(tamper_problems(self.tmp), [], "a reader that skips the "
                                                              "seal was not caught")
        real_seal, real_virtual = store._seal_form, store._virtual_digest
        with mock.patch.object(store, "_seal_form",
                               lambda claim_id, list_name, entry:
                               real_seal("", list_name, entry)), \
                mock.patch.object(store, "_virtual_digest",
                                  lambda claim_id, list_name, entry, prev:
                                  real_virtual("", list_name, entry, prev)):
            found = tamper_problems(self.tmp)
        self.assertIn("an entry copied to another claim", found)


def tamper_problems(tmp: Callable[[], str]) -> list[str]:
    """Each tampering of a sealed file that the strict reader does NOT refuse."""
    def edit_detail(d):
        d["results"][2]["detail"] = "it was fine"

    def flip_fail(d):
        d["results"][1]["passed"] = True

    def drop_middle(d):
        del d["results"][1]

    rows = {"a sealed detail edited": edit_detail, "a sealed fail's passed flipped": flip_fail,
            "a middle fail removed": drop_middle}
    out = []
    for name, edit in rows.items():
        root = tmp()
        os.makedirs(os.path.join(root, "results"), exist_ok=True)
        path = _sealed_file(root)
        data = json.load(open(path, encoding="utf-8"))
        edit(data)
        _write(path, data)
        try:
            store.read_record(path, "results")
            out.append(name)
        except AtompipeError:
            pass
    root = tmp()
    os.makedirs(os.path.join(root, "results"), exist_ok=True)
    path = _sealed_file(root, "C5")
    copied = os.path.join(root, "results", "C1.json")
    shutil.copy(path, copied)
    try:
        store.read_record(copied, "results")
        out.append("an entry copied to another claim")
    except AtompipeError:
        pass
    return out


# --------------------------------------------------------------------------- #
# V-4 — an owner only through the channel
# --------------------------------------------------------------------------- #
class AnOwnerOnlyThroughTheChannel(_env.EnvCase):
    """(V-4, invariant 11) An owner counts only when the owner typed `claim
    physical <id> assume` in their own shell; any edit after un-attributes it."""

    def _owned(self) -> str:
        root = P.project(os.path.join(self.tmp(), "b"))
        P.edit_claim(root, "C6", owner=P.NAME)
        proc = P.tty(root, "claim", "physical", "C6", "assume", answer="C6", code=0)
        self.assertIn("recorded C6", proc.stdout)
        return root

    def test_the_owner_records_it_and_every_channel_reads_assumed(self):
        root = self._owned()
        found = P.channels(root, "C6")
        self.assertEqual(P.disagreements(found, "asserted", "owned"), [])
        self.assertFalse(found["check.blocking"])
        self.assertIn(f"assumed by {P.NAME}", found["status.reason"])
        raw = store.load(root).claim("C6")
        self.assertEqual(claims.compose(raw, []).cause.value, "owned",
                         "a raw store.load reader disagrees with the view")

    def test_every_edit_after_unattributes_it(self):
        for edit, cause in (({"owner": None}, "no-owner"),
                            ({"rationale": "it is fine"}, "owner-unattributed"),
                            ({"owner": "Alex Other"}, "owner-unattributed")):
            with self.subTest(edit=edit):
                root = self._owned()
                P.edit_claim(root, "C6", **edit)
                found = json.loads(P.run(root, "status", "--json", code=0).stdout)
                self.assertEqual(found["statuses"]["C6"]["cause"], cause)

    def test_someone_else_cannot_record_the_owner(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        P.edit_claim(root, "C6", owner=P.NAME)
        before = _tree(root)
        proc = P.tty(root, "claim", "physical", "C6", "assume", answer="C6", env=P.OTHER,
                     code=2)
        self.assertIn(P.NAME, proc.stderr)
        self.assertIn("Pat Other", proc.stderr)
        self.assertEqual(_tree(root), before)

    def test_a_fallback_owner_reads_assumed_outside_the_context(self):
        """P2.4-D18's hand-off: an owned fallback carries a measurable claim
        outside its evaluator's context, once the owner records it."""
        root = P.project(os.path.join(self.tmp(), "b"))
        P.edit_claim(root, "C1", owner=P.NAME,
                     fallback="the sag is linear well past the range it was qualified on")
        proc = P.tty(root, "claim", "physical", "C1", "assume", answer="C1", code=0)
        self.assertIn("recorded C1", proc.stdout)
        attributions = P.results(root, "C1").get("attributions") or []
        self.assertEqual([a.get("role") for a in attributions], ["owner"])
        self.assertEqual(attributions[0].get("reason"),
                         "the sag is linear well past the range it was qualified on")

    def test_no_spine_module_passes_owners(self):
        self.assertEqual(owners_callers(_spine_sources()), [])

    def test_planted_owner_paths_are_caught(self):
        planted = {"cli_planted.py": "from atompipe import claims\n"
                                     "def f(view):\n"
                                     "    return claims.compositions(view, owners={'C6': 1})\n"}
        self.assertNotEqual(owners_callers(planted), [])
        # A store that assembles a non-interactive attribution: C6 Assumed with no act.
        from atompipe.models import AttributionRecord, Claim, ClaimKind
        claim = Claim(id="C6", statement="s", kind=ClaimKind.ASSUMPTION, rationale="r",
                      owner=P.NAME)
        record = AttributionRecord(role="owner", name=P.NAME, reason="r", who=P.WHO,
                                   channel="non-interactive")
        planted_claim = __import__('dataclasses').replace(claim, attributions=(record,))
        found = claims.compose(planted_claim, [])
        self.assertNotEqual(found.cause.value, "owned",
                            "an attribution from a pipe counted for an owner")


def _spine_sources() -> dict[str, str]:
    src = os.path.join(_env.REPO, "src", "atompipe")
    out = {}
    for name in sorted(os.listdir(src)):
        if name.endswith(".py"):
            with open(os.path.join(src, name), encoding="utf-8") as fh:
                out[name] = fh.read()
    return out


def owners_callers(sources: dict[str, str]) -> list[str]:
    """Every call that passes ``owners=`` to a composition entry point, outside
    ``claims.py``'s own forwarding — the seam stays a test seam (P2.5a-D11)."""
    wanted = {"compose", "compositions", "statuses", "blocking", "summarise",
              "resolve_status"}
    hits = []
    for name, text in sources.items():
        if name == "claims.py":
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and any(k.arg == "owners" for k in node.keywords):
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if called in wanted:
                    hits.append(f"{name}:{node.lineno}")
    return hits


if __name__ == "__main__":
    unittest.main(verbosity=2)
