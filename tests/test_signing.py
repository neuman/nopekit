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
    ("an entry point", False, {"CLAUDE_CODE_ENTRYPOINT": "cli"}, None, "agent-session unknown"),
    # R-6 (review of P2.5a): "a future CLAUDE_CODE_ marker" asserted the prefix
    # rule this review moved — Claude Code's user-set configuration lives under
    # that prefix — and each observed marker is a row of its own above. A
    # person's own exported setting is a person's shell.
    ("a terminal, a person's own CLAUDE_CODE_USE_BEDROCK", True,
     {"CLAUDE_CODE_USE_BEDROCK": "1"}, "C5", "interactive"),
    ("a terminal, a person's own CLAUDE_CODE_MAX_OUTPUT_TOKENS", True,
     {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "32000"}, "C5", "interactive"),
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

        def the_whole_prefix(tty, environ, answer, claim_id):
            # P2.5a's rule: every CLAUDE_CODE_ name a marker (review of P2.5a).
            if any(k.startswith("CLAUDE_CODE_") for k in environ):
                return "agent-session unknown"
            return real(tty, environ, answer, claim_id)

        planted = {"reads ATOMPIPE_CHANNEL": reads_a_variable, "trusts isatty": trusts_the_tty,
                   "markers only": reads_markers_only, "accepts any line": any_line,
                   "the whole CLAUDE_CODE_ prefix": the_whole_prefix}
        for name, fn in planted.items():
            with self.subTest(name):
                self.assertNotEqual(channel_problems(fn), [], f"{name} was not caught")

    def test_the_marker_is_named(self):
        """(review of P2.5a) A shell read as an agent's says which variable made it
        one — the first of ``AGENT_MARKERS`` it sets — in the recorded line and in
        the refusal of ``assume``; a person's own setting is no marker."""
        marker = _need(cli, "_agent_marker")
        self.assertEqual(marker({"CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDECODE": "1"}),
                         "CLAUDECODE")
        self.assertEqual(marker({"CLAUDE_CODE_USE_BEDROCK": "1",
                                 "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "1"}), "")
        said = __import__("atompipe.report", fromlist=["report"]).HUMAN["signing"]
        self.assertIn("{marker}", said["agent_pass"])
        self.assertIn("{why}", said["assume_channel"])

    def test_the_marker_is_named_end_to_end(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        proc = P.run(root, "claim", "physical", "C5", "fail", "--detail", "cracked",
                     agent=True, code=0)
        self.assertIn("(CLAUDECODE is set in this shell", proc.stdout)
        P.edit_claim(root, "C6", owner=P.NAME)
        proc = P.run(root, "claim", "physical", "C6", "assume", agent=True, code=2)
        self.assertIn("(CLAUDECODE is set in this shell", proc.stderr)
        proc = P.run(root, "claim", "physical", "C6", "assume", code=2)
        self.assertIn("(stdin is not a terminal)", proc.stderr)
        # A person's own Claude Code setting leaves their shell theirs.
        proc = P.tty(root, "claim", "physical", "C6", "assume", answer="C6",
                     env={"CLAUDE_CODE_USE_BEDROCK": "1"}, code=0)
        self.assertEqual(P.results(root, "C6")["attributions"][-1]["channel"], "interactive")

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
        # (review of P2.5a) The flags' help from `HUMAN`, and never "(what the
        # report prints)" — the report prints the positional `pass|fail`.
        self.assertNotIn("what the report prints", proc.stdout)
        self.assertIn("same as the positional `pass`", " ".join(proc.stdout.split()))
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

    def test_the_act_and_its_flag_never_disagree(self):
        """(review of P2.5a) ``fail --pass`` recorded and counted a PASS, ``pass
        --fail`` a fail nothing can supersede, ``assume --fail`` an attribution:
        the flag won, silently. Each disagreement is refused, nothing written.
        Planted: P2.5a's reading, the flag first."""
        self.assertEqual(act_problems(_need(cli, "_act")), [])

        def flag_wins(result, passed, measured, claim_id="C9"):
            typed = passed if passed is not None else (
                (result == "pass") if result in ("pass", "fail") else None)
            return (result or (("pass" if typed else "fail") if typed is not None else None),
                    typed if result != "assume" else None)

        self.assertNotEqual(act_problems(flag_wins), [], "the flag winning was not caught")

    def test_the_act_and_its_flag_end_to_end(self):
        root = P.project(os.path.join(self.tmp(), "b"), planted=("C9",))
        before = _tree(root)
        for args in (("C9", "fail", "--pass", "--evidence", P.EVIDENCE),
                     ("C9", "pass", "--fail"), ("C6", "assume", "--fail"),
                     ("C6", "assume", "--measured", "3")):
            with self.subTest(args=args):
                proc = P.run(root, "claim", "physical", *args, "--detail", "creep", code=2)
                self.assertIn("Nothing was written", proc.stderr)
                self.assertEqual(_tree(root), before)
        doc = json.loads(P.run(root, "claim", "physical", "C1", "--measured", "0.62",
                               "--detail", "ruler", "--json", code=0).stdout)
        self.assertEqual(doc["recorded"]["act"], "fail")

    def test_a_value_that_is_not_text_is_refused(self):
        """(review of P2.5a) An argument that is not UTF-8 reached the writer and
        printed a traceback, exit 1. It is refused, naming its flag, exit 2; and
        the writer itself never raises past an AtompipeError."""
        root = P.project(os.path.join(self.tmp(), "b"))
        before = _tree(root)
        for flag in ("--detail", "--evidence", "--authority"):
            err = io.StringIO()
            with self.subTest(flag=flag), mock.patch.dict(os.environ, _env.IDENTITY), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = cli.main(["-C", root, "claim", "physical", "C2", "fail",
                                 flag, "bad\udcffbyte"])
                self.assertEqual(code, 2, err.getvalue())
                self.assertIn(f"{flag} holds bytes that are not text", err.getvalue())
                self.assertEqual(_tree(root), before)
        with self.assertRaises(AtompipeError) as caught:
            store._dumps({"detail": "bad\udcffbyte"}, "results/C2.json")
        self.assertIn("results/C2.json", str(caught.exception))

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


#: (positional act, --pass/--fail, --measured, (act, typed) or None for refused).
ACT_ROWS: tuple = (
    ("pass", None, None, ("pass", True)), ("fail", None, None, ("fail", False)),
    (None, True, None, ("pass", True)), (None, False, None, ("fail", False)),
    ("pass", True, None, ("pass", True)), ("fail", False, None, ("fail", False)),
    ("fail", True, None, None), ("pass", False, None, None),
    ("assume", None, None, ("assume", None)), ("assume", False, None, None),
    ("assume", True, None, None), ("assume", None, 3.0, None),
    (None, None, 0.62, (None, None)), ("fail", None, 0.62, ("fail", False)),
)


def act_problems(act: Callable[..., Any]) -> list[str]:
    """Every ``ACT_ROWS`` row ``act`` gets wrong."""
    out: list[str] = []
    for result, passed, measured, want in ACT_ROWS:
        try:
            got = act(result, passed, measured, "C9")
        except AtompipeError:
            got = None
        if got != want:
            out.append(f"{result} --pass={passed} --measured={measured}: {got!r}, not "
                       f"{want!r}")
    return out


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

    def test_every_entry_a_restore_discards_is_named(self):
        """(review of P2.5a) In process, against a real git history: every entry
        the restore's version does not hold is named — a flipped fail as
        unverifiable, never skipped as a pass; a removed one said; a sealed fail
        with the command that records it again, carrying its measured value and
        evidence and never an edited detail; and a committed hand edit pointed at
        the newest commit that verifies, never at a checkout that changes
        nothing."""
        self.assertEqual(advice_problems(self.tmp), [])

    def test_planted_advice_is_caught(self):
        """Planted: P2.5a's advice — the fails by their `passed` flag, and HEAD
        alone as what a restore brings back."""
        real = _need(store, "_discarded")

        def by_passed(claim_id, data, source, holder):
            held = {store.canonical_json(e) for e in (source or {}).get("results") or ()}
            fails = [e for e in data.get("results") or () if e.get("passed") is not True
                     and store.canonical_json(e) not in held]
            return [f"discards {len(fails)} fail(s)"] if fails else []

        with mock.patch.object(store, "_discarded", by_passed):
            found = advice_problems(self.tmp)
        self.assertTrue(any(p.startswith("an uncommitted fail flipped to a pass") for p in found),
                        found)
        self.assertTrue(callable(real))
        with mock.patch.object(store, "_restore_source",
                               lambda claim_id, rel: ("head", None)):
            found = advice_problems(self.tmp)
        self.assertTrue(any(p.startswith("a committed hand edit") for p in found), found)

    def test_a_committed_hand_edit_is_restored_by_the_advice(self):
        """(review of P2.5a) A fail flipped to a pass and COMMITTED: the advice
        names the commit before the edit, and following it brings the fail back."""
        root = P.project(os.path.join(self.tmp(), "b"), git=True)
        P.run(root, "claim", "physical", "C2", "fail", "--detail", "cracked at the bolt",
              code=0)
        _commit(root, "the fail")
        path = os.path.join(root, "results", "C2.json")
        data = json.load(open(path, encoding="utf-8"))
        data["results"][0]["passed"] = True
        _write(path, data)
        _commit(root, "tidied")
        proc = P.run(root, "status", code=2)
        self.assertIn("the last commit holds results/C2.json broken too", proc.stderr)
        found = re.search(r"git checkout ([0-9a-f]{12}) -- results/C2\.json", proc.stderr)
        self.assertIsNotNone(found, proc.stderr)
        _env.git(["checkout", found.group(1), "--", "results/C2.json"], cwd=root)
        doc = json.loads(P.run(root, "status", "--json", code=0).stdout)
        self.assertEqual(doc["claims"]["C2"], "refuted")

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


def _commit(root: str, message: str) -> str:
    """``git add -A`` and a commit in ``root`` (initialised if it is not a
    repository yet), with the test identity; the new commit's id."""
    if not os.path.isdir(os.path.join(root, ".git")):
        _env.git(["-c", "init.defaultBranch=main", "init", "-q"], cwd=root)
    for argv, identity in ((["add", "-A"], False), (["commit", "-q", "-m", message], True)):
        proc = _env.git(argv, cwd=root, identity=identity)
        if proc.returncode != 0:
            raise AssertionError(f"git {argv} failed: {proc.stderr}")
    return _env.git(["rev-parse", "HEAD"], cwd=root).stdout.strip()


def _result(passed: bool, detail: str, **changes: Any) -> dict:
    entry = {"passed": passed, "when": "2026-10-04T10:00:00Z", "who": P.WHO,
             "detail": detail, "evidence": [], "channel": "interactive", "authority": "",
             "measured": None, "units": "", "article": {}, "claim_digest": "c" * 64,
             "rho": "", "evidence_sha256": {}, "contradicts": [], "contradiction_check": ""}
    entry.update(changes)
    return entry


def _advised(path: str) -> str:
    try:
        store.read_record(path, "results")
    except AtompipeError as exc:
        return str(exc)
    return ""


def advice_problems(tmp: Callable[[], str]) -> list[str]:
    """Each row of the restore advice that does not say what it must, or says
    what it must not (review of P2.5a). Every row: a pass committed, then two
    fails recorded and not committed — the first with a measured value and
    evidence — then one tampering."""
    def base() -> tuple[str, str, str]:
        root = tmp()
        os.makedirs(os.path.join(root, "results"), exist_ok=True)
        path = os.path.join(root, "results", "C5.json")
        store.append_signed(root, "C5", "results", _result(True, "fine after two winters"))
        first = _commit(root, "the pass")
        store.append_signed(root, "C5", "results", _result(
            False, "cracked at the root", measured=0.9, units="mm",
            evidence=["photos/a.jpg"]))
        store.append_signed(root, "C5", "results", _result(False, "cracked again"))
        return root, path, first

    def edited(d: dict, root: str) -> None:
        d["results"][2]["detail"] = "tidied"

    def flipped(d: dict, root: str) -> None:
        d["results"][1]["passed"] = True

    def removed(d: dict, root: str) -> None:
        del d["results"][1]

    rows: dict[str, tuple] = {
        "an uncommitted fail's detail edited": (edited, False, (
            "git checkout -- results/C5.json", "results[1], a fail recorded",
            "--measured 0.9", "--evidence photos/a.jpg", "'cracked at the root'",
            "results[2], whose seal does not hold"), ("--detail tidied",)),
        "an uncommitted fail flipped to a pass": (flipped, False, (
            "results[1], whose seal does not hold", "it reads as a pass now",
            "results[2], a fail recorded"), ()),
        "an uncommitted fail removed": (removed, False, (
            "results[1], a fail recorded", "cracked again",
            "The entries before results[1] are not as they were recorded"), ()),
        "a committed hand edit": (flipped, True, (
            "the last commit holds results/C5.json broken too",
            "results[1], whose seal does not hold"), ("git checkout -- results/C5.json,",)),
    }
    out: list[str] = []
    for name, (edit, commit_first, wanted, unwanted) in rows.items():
        root, path, first = base()
        before = _commit(root, "the fails") if commit_first else first
        data = json.load(open(path, encoding="utf-8"))
        edit(data, root)
        _write(path, data)
        if commit_first:
            _commit(root, "tidied")
            wanted = (*wanted, f"git checkout {before[:12]} -- results/C5.json")
        text = _advised(path)
        out += [f"{name}: does not say {w!r}" for w in wanted if w not in text]
        out += [f"{name}: says {w!r}" for w in unwanted if w in text]
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

    def test_a_name_with_spaces_around_it_is_the_name(self):
        """(review of P2.5a) ``"owner": "Dana Reviewer "``: the writers stripped
        it and the readers did not, so Dana's own ``assume`` never counted. One
        normaliser (``claims.name_of``) on every side; planted: a ``name_of``
        that keeps the spaces."""
        from atompipe.models import AttributionRecord, Claim, ClaimKind
        record = AttributionRecord(role="owner", name=P.NAME, reason="r", who=P.WHO,
                                   channel="interactive")
        claim = Claim(id="C6", statement="s", kind=ClaimKind.ASSUMPTION, rationale="r",
                      owner=P.NAME + " ", attributions=(record,))
        self.assertEqual(claims.compose(claim, []).cause.value, "owned")
        _need(claims, "name_of")
        with mock.patch.object(claims, "name_of", lambda v: str(v if v is not None else "")):
            self.assertNotEqual(claims.compose(claim, []).cause.value, "owned",
                                "a reader comparing the raw name was not caught")

    def test_a_name_with_spaces_around_it_end_to_end(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        P.edit_claim(root, "C6", owner=P.NAME + " ")
        P.tty(root, "claim", "physical", "C6", "assume", answer="C6", code=0)
        found = json.loads(P.run(root, "status", "--json", code=0).stdout)
        self.assertEqual(found["statuses"]["C6"]["cause"], "owned")

    def test_an_owner_changed_back_reads_assumed_again(self):
        """(review of P2.5a) Dana records C6, the owner moves to Pat, who records
        it, and back to Dana: Dana's attribution, for this owner and this reason,
        still counts — as a reverted article reads Checked again. Planted: the
        newest attribution alone."""
        from atompipe.models import AttributionRecord, Claim, ClaimKind
        dana = AttributionRecord(role="owner", name=P.NAME, reason="r", who=P.WHO,
                                 channel="interactive")
        pat = AttributionRecord(role="owner", name="Pat Other", reason="r",
                                who="Pat Other <pat@example.invalid>", channel="interactive")
        claim = Claim(id="C6", statement="s", kind=ClaimKind.ASSUMPTION, rationale="r",
                      owner=P.NAME, attributions=(pat, dana))
        self.assertEqual(claims.compose(claim, []).cause.value, "owned")

        def newest(claim_):
            found = claims._attributed(claim_, "owner")
            return {claim_.id: claims.Attribution(found[0].name, found[0].reason)} if found \
                else {}

        with mock.patch.object(claims, "_owners_of", newest):
            self.assertEqual(claims.compose(claim, []).cause.value, "owner-unattributed",
                             "the newest-only reading was not caught")

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


# --------------------------------------------------------------------------- #
# C-1 (P2.5b) — the results seal is unmoved by the export record's
# --------------------------------------------------------------------------- #
#: A results file exactly as P2.5a's ``append_signed`` wrote it (ecaad99): one
#: sealed fail on a design article, one owner attribution. Values, not a
#: recording: the seals below are what ecaad99's writer computed for them.
_FROZEN_RESULTS = {
    "results": [{
        "passed": False, "when": "2026-10-04T10:00:00Z", "who": "Dana <dana@example.invalid>",
        "detail": "sagged at the tip", "evidence": [], "channel": "interactive",
        "authority": "", "measured": 0.62, "units": "mm",
        "article": {"source": "design", "hash": "a" * 64,
                    "built_from": {"params": [[["config", "thickness"], "b" * 64, 8.0]],
                                   "model": {"model/bracket.py": "c" * 64}, "files": {}},
                    "revision": "", "dirty": False},
        "claim_digest": "d" * 64, "rho": "e" * 64, "evidence_sha256": {},
        "contradicts": [{"gate": "bracket.deflection", "code": "f" * 64, "rho": "1" * 64,
                         "value": 0.469, "units": "mm", "inside": True}],
        "contradiction_check": "", "prev": "",
        "digest": "2b2db1470fd1dc857fc71a23f70d6aa642c90544bea06696fa421be56743b12d"}],
    "attributions": [{
        "role": "owner", "name": "Dana", "reason": "carried", "claim_digest": "2" * 64,
        "who": "Dana <dana@example.invalid>", "when": "2026-10-04T10:01:00Z",
        "channel": "interactive", "prev": "",
        "digest": "d86e51b11117ae91f1fcc9f1b004a0157a5d1695c6f127fc35f7a44e1ed34031"}],
}

#: The seal ecaad99's writer gives the pass appended after it.
_FROZEN_NEXT = "1d4082341e286b51397d3a78703b1de00c89a6d047b3ad91eaf9f3964d5775a1"


class TheResultsSealIsUnmoved(_env.EnvCase):
    """(C-1, P2.5b) P2.5b generalises the one sealed writer to the export record
    (``store.append_sealed``): a results file P2.5a wrote still verifies, and a
    result appended to it is sealed byte for byte as P2.5a sealed it. What this
    pins against: a seal form that gained the export record's ``kind`` would
    move every result's digest, and every results file a person committed would
    refuse every command."""

    def test_a_p25a_results_file_verifies_and_extends_as_it_did(self):
        root = self.tmp()
        P.write_json(os.path.join(root, "results", "C1.json"), _FROZEN_RESULTS)
        found = store.read_record(os.path.join(root, "results", "C1.json"), "results")
        self.assertEqual([r.passed for r in found], [False])
        self.assertEqual([a.name for a in found.attributions], ["Dana"])
        entry = {k: v for k, v in _FROZEN_RESULTS["results"][0].items()
                 if k not in ("prev", "digest")}
        entry.update(passed=True, detail="held", measured=None, units="", contradicts=[],
                     when="2026-10-05T10:00:00Z")
        stored = store.append_signed(root, "C1", "results", entry)
        self.assertEqual(stored["prev"], _FROZEN_RESULTS["results"][0]["digest"])
        self.assertEqual(stored["digest"], _FROZEN_NEXT)
        again = store.read_record(os.path.join(root, "results", "C1.json"), "results")
        self.assertEqual([r.passed for r in again], [False, True])


if __name__ == "__main__":
    unittest.main(verbosity=2)
