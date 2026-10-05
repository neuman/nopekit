# SPDX-License-Identifier: Apache-2.0
"""Test projects for P2.5a's physical path: the bracket, with what a person would
add before recording a physical result — and the one reader every channel check
goes through.

* :func:`project` — a migrated bracket copy whose C5 has a written test (a
  ``note``) and an evidence file (``photos/c5-root.jpg``), optionally with the
  planted claims the tests name: C8 (an expert-judgment claim, its authority the
  test identity), C9 (a second physical claim with a note), C10-C14 as V-14
  places them. Neutral names only (``tests/test_packs.py`` FORBIDDEN).
* :func:`channels` — one claim's status token and cause as every channel reads it:
  ``status --json``, ``check --json`` (its BLOCKING list and exit code),
  ``report --json``, ``claim show --json``, ``claim list --json``, the JUnit
  file, ``state.json`` and ``last_check.json`` — so "every channel agrees" is one
  dictionary compared with one expected pair.
* :func:`resolved` — the same view in process: the CLI's own ``_resolved`` over
  the loaded model, so an in-process row reads exactly as the commands do.

What slipped through without one reader: each P2.1-P2.4 test that crossed the
channels kept its own copy of how to read each one, and the copy that read
``status`` alone passed while ``state.json`` disagreed.
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Mapping

import _env
import _projects

#: The name and the whole identity ``_env.run(identity=True)`` gives a child — the
#: git identity every recorded entry's ``who`` is read from (P2.5a-D5).
NAME = _env.IDENTITY["GIT_AUTHOR_NAME"]
WHO = f"{NAME} <{_env.IDENTITY['GIT_AUTHOR_EMAIL']}>"

#: A second person, for the rows where someone other than the named owner or
#: authority types the command.
OTHER = {"GIT_AUTHOR_NAME": "Pat Other", "GIT_AUTHOR_EMAIL": "pat@example.invalid",
         "GIT_COMMITTER_NAME": "Pat Other", "GIT_COMMITTER_EMAIL": "pat@example.invalid"}

#: The agent's shell, as observed in this harness on 2026-10-04 (P2.5a §1):
#: ``CLAUDECODE`` and a session id. Each marker alone is tested in V-1.
AGENT = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s1"}

C5_NOTE = "outdoor rack, two winters, visual check of the root for crazing"
EVIDENCE = "photos/c5-root.jpg"

#: The planted claims. C8's authority is the test identity's name, so the run
#: that records its judgment is the authority's own (critique 10 of the P2.5a
#: design: only the named authority settles it).
PLANTED: dict[str, dict[str, Any]] = {
    "C8": {"statement": "Safe to mount above a sleeping area", "kind": "assumption",
           "terminal": "human", "authority": NAME,
           "rationale": "a falling shelf is a safety call no evaluator here makes"},
    "C9": {"statement": "The printed hook holds a coat for a month", "kind": "physical",
           "note": "a coat on the hook for thirty days; look for creep at the root"},
}


def write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def edit_claim(root: str, cid: str, **changes: Any) -> None:
    """Edit ``claims/<cid>.json`` as a hand or an agent would; ``None`` deletes a key."""
    path = os.path.join(root, "claims", f"{cid}.json")
    record = read_json(path)
    for key, value in changes.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    write_json(path, record)


def project(dest: str, *, planted: tuple[str, ...] = (), thickness: float | None = None,
            git: bool = False) -> str:
    """The migrated bracket at ``dest`` with C5's note and evidence, and ``planted``."""
    root = _projects.bracket_copy(dest, migrated=True, thickness=thickness)
    edit_claim(root, "C5", note=C5_NOTE)
    os.makedirs(os.path.join(root, "photos"), exist_ok=True)
    with open(os.path.join(root, EVIDENCE), "wb") as fh:
        fh.write(b"\xff\xd8 a photo of the root after two winters \xff\xd9")
    for cid in planted:
        write_json(os.path.join(root, "claims", f"{cid}.json"), PLANTED[cid])
    if git:
        _projects._commit_all(root, "a copy of examples/bracket with a test written for C5")
    return root


def run(root: str, *args: Any, code: int | None = None, agent: bool = False,
        env: Mapping[str, Any] | None = None, identity: bool = True) -> Any:
    """``atompipe <args>`` in ``root`` with the test identity; ``agent`` sets the
    agent's markers. ``code`` asserts the exit code, naming the output."""
    extra = dict(AGENT if agent else {})
    extra.update(env or {})
    proc = _env.atompipe(list(args), cwd=root, identity=identity, env=extra)
    if code is not None and proc.returncode != code:
        raise AssertionError(f"`atompipe {' '.join(map(str, args))}` exited "
                             f"{proc.returncode}, not {code}:\n{proc.stdout[-3000:]}\n"
                             f"{proc.stderr[-3000:]}")
    return proc


def tty(root: str, *args: Any, answer: str | None, code: int | None = None,
        env: Mapping[str, Any] | None = None) -> Any:
    """``atompipe <args>`` in a person's own shell (a pty), typing ``answer``."""
    proc = _env.run_tty(list(args), cwd=root, answer=answer, env=env)
    if code is not None and proc.returncode != code:
        raise AssertionError(f"`atompipe {' '.join(map(str, args))}` (a terminal) exited "
                             f"{proc.returncode}, not {code}:\n{proc.stdout[-3000:]}\n"
                             f"{proc.stderr[-3000:]}")
    return proc


def results(root: str, cid: str) -> dict:
    path = os.path.join(root, "results", f"{cid}.json")
    return read_json(path) if os.path.isfile(path) else {}


def milestone(root: str, name: str, requires: list[str], generator: str = "",
              description: str = "") -> str:
    """Write ``milestones/<name>.json`` as a person declares a spend (P2.5b-D1);
    its path."""
    record: dict[str, Any] = {"description": description or f"the {name} spend",
                              "requires": list(requires)}
    if generator:
        record["generator"] = generator
    path = os.path.join(root, "milestones", f"{name}.json")
    write_json(path, record)
    return path


def exports(root: str, name: str) -> dict:
    """``exports/<name>.json`` as `export` sealed it, parsed (``{}`` with none)."""
    path = os.path.join(root, "exports", f"{name}.json")
    return read_json(path) if os.path.isfile(path) else {}


def file_bytes(root: str, rel: str) -> bytes | None:
    path = os.path.join(root, *rel.split("/"))
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def channels(root: str, cid: str, *, site: bool = True) -> dict[str, Any]:
    """``{channel: (status value, cause)}`` for claim ``cid``, plus ``check.exit``,
    ``check.blocking`` (whether ``cid`` is in its BLOCKING list), ``junit`` (the
    claim's testcase children as ``(tag, type)``), ``ready`` and the human rows.

    Runs ``check --junit --json`` (which writes ``last_check.json``), then the
    readers; ``site=False`` skips ``site init``/``build`` where a test needs
    none."""
    out: dict[str, Any] = {}
    check = run(root, "check", "--junit", "--json")
    out["check.exit"] = check.returncode
    doc = json.loads(check.stdout)
    rows = {row["claim"]: row for row in doc.get("blocking") or ()}
    out["check.blocking"] = cid in rows
    out["ready"] = doc.get("all_required_checked")
    junit = os.path.join(root, ".atompipe", "out", "junit.xml")
    tree = ET.parse(junit).getroot()
    case = next((c for c in tree.iter("testcase") if c.get("name") == cid
                 and (c.get("classname") or "").startswith("claims")), None)
    out["junit"] = [(k.tag, k.get("type")) for k in case] if case is not None else None
    out["junit.message"] = " ".join(k.get("message") or "" for k in case) if case is not None \
        else ""
    status = json.loads(run(root, "status", "--json", code=0).stdout)
    out["status.json"] = (status["claims"].get(cid),
                          (status["statuses"].get(cid) or {}).get("cause"))
    out["status.reason"] = (status["statuses"].get(cid) or {}).get("reason", "")
    out["summary"] = status["summary"]
    out["rebuild"] = status.get("rebuild")
    report = json.loads(run(root, "report", "--json", code=0).stdout)
    out["report.json"] = (report["claims"].get(cid),
                          (report["statuses"].get(cid) or {}).get("cause"))
    show = json.loads(run(root, "claim", "show", cid, "--json", code=0).stdout)
    out["claim.show"] = (show.get("status"), show.get("cause"))
    listed = {r["id"]: r for r in json.loads(run(root, "claim", "list", "--json",
                                                code=0).stdout)["claims"]}
    out["claim.list"] = (listed[cid].get("status"), listed[cid].get("cause"))
    last = read_json(os.path.join(root, ".atompipe", "cache", "last_check.json"))
    out["last_check"] = last.get("statuses", {}).get(cid)
    text = run(root, "status", code=0).stdout
    out["status.row"] = next((ln for ln in text.splitlines()
                              if re.match(rf"^\[.{{5}}\] {re.escape(cid)} ", ln)), "")
    out["status.text"] = text
    out["report.text"] = run(root, "report", code=0).stdout
    if site:
        if not os.path.isdir(os.path.join(root, "site")):
            run(root, "site", "init", code=0)
        run(root, "site", "build", code=0)
        state = read_json(os.path.join(root, "site", "data", "state.json"))
        row = next((r for r in state.get("claims") or () if r.get("id") == cid), {})
        out["state.json"] = (row.get("status"), row.get("cause"))
        out["state.row"] = row
        out["state"] = state
    return out


#: The channels whose ``(status, cause)`` pair ``agree`` compares.
PAIRED = ("status.json", "report.json", "claim.show", "claim.list", "state.json")


def disagreements(found: Mapping[str, Any], status: str, cause: str) -> list[str]:
    """Every channel in ``found`` whose pair is not ``(status, cause)``, and
    ``last_check.json``'s status when it is not ``status``."""
    out = [f"{name}: {found[name]}" for name in PAIRED
           if name in found and tuple(found[name]) != (status, cause)]
    if found.get("last_check") != status:
        out.append(f"last_check: {found.get('last_check')}")
    return out


def resolved(root: str) -> tuple[Any, Any]:
    """``(view, resolution)`` for ``root`` in process, as every command builds it."""
    from atompipe import cli, store
    ledger = store.load(root)
    registry, _problems = cli._registry(root, ledger, strict=False)
    model, projection, model_error = cli._projection_safe(root, ledger)
    return cli._resolved(root, ledger, registry, projection, model_error,
                         now="2026-10-04T10:00:00Z", model=model)


def composed(root: str, cid: str) -> Any:
    """``compose`` over the view for ``cid`` — what every channel renders."""
    from atompipe import claims
    view, resolution = resolved(root)
    return claims.compose(view.claim(cid), view.verdicts,
                          stale_gates=resolution.stale_gates)


# --------------------------------------------------------------------------- #
# one transcript of the physical path, for the word and shape scans (V-15, V-16)
# --------------------------------------------------------------------------- #
_TRANSCRIPT: list[dict[str, Any]] = []


def transcript() -> dict[str, Any]:
    """Every human line the physical path prints, run once per process: a pass
    from an agent session and one typed in a person's own shell (its prompt on
    stderr), the refusals (`--who`, no identity, a mistyped id, `assume` from an
    agent session), the design moved (`status`, `check`, `claim list`, `why`,
    `report`), and — on a copy at thickness 8.0 — a contradiction recorded in a
    person's own shell, `gate show` and `why` of the evaluator and claim."""
    if _TRANSCRIPT:
        return _TRANSCRIPT[0]
    import atexit
    import tempfile
    tmp = tempfile.mkdtemp(prefix="atompipe-physical-words-")
    atexit.register(_env._rmtree, tmp)
    root = project(os.path.join(tmp, "b"), planted=("C8",))
    edit_claim(root, "C6", owner=NAME)
    out: dict[str, Any] = {"root": root}
    out["agent.pass"] = run(root, "claim", "physical", "C5", "pass", "--detail", "no cracking",
                            "--evidence", EVIDENCE, agent=True, code=0)
    out["agent.fail"] = run(root, "claim", "physical", "C8", "fail", "--detail", "too heavy",
                            agent=True, code=0)
    out["tty.pass"] = tty(root, "claim", "physical", "C5", "pass", "--detail",
                          "no cracking after two winters", "--evidence", EVIDENCE,
                          answer="C5", code=0)
    out["assume"] = tty(root, "claim", "physical", "C6", "assume", answer="C6", code=0)
    out["refuse.who"] = run(root, "claim", "physical", "C5", "fail", "--who", "Sam", code=2)
    out["refuse.when"] = run(root, "claim", "physical", "C5", "fail", "--when", "2020-01-01",
                             code=2)
    out["refuse.identity"] = run(root, "claim", "physical", "C5", "fail", "--detail", "x",
                                 identity=False, code=2)
    out["refuse.typed"] = tty(root, "claim", "physical", "C5", "fail", "--detail", "x",
                              answer="C4", code=2)
    out["refuse.assume"] = run(root, "claim", "physical", "C6", "assume", agent=True, code=2)
    # An authority's own acts (review of P2.5a: neither prompt was pinned, R-11).
    out["assume.authority"] = tty(root, "claim", "physical", "C8", "assume", "--authority",
                                  NAME, answer="C8", code=0)
    out["judgment"] = tty(root, "claim", "physical", "C8", "pass", "--authority", NAME,
                          "--detail", "safe above a bed", answer="C8", code=0)
    out["why.C8"] = run(root, "why", "C8", code=0)
    _projects.set_thickness(root, 7.5)
    out["status"] = run(root, "status", code=0)
    out["check"] = run(root, "check")
    out["claim.list"] = run(root, "claim", "list", code=0)
    out["why.C5"] = run(root, "why", "C5", code=0)
    out["report"] = run(root, "report", code=0)
    eight = project(os.path.join(tmp, "eight"), thickness=8.0)
    run(eight, "check")
    out["contradiction"] = tty(eight, "claim", "physical", "C1", "--measured", "0.62",
                               "--detail", "ruler at the tip, 15 N for an hour", answer="C1",
                               code=0)
    out["gate.show"] = run(eight, "gate", "show", "bracket.deflection", code=0)
    out["why.C1"] = run(eight, "why", "C1", code=0)
    out["help"] = run(eight, "claim", "physical", "--help", code=0)
    _TRANSCRIPT.append(out)
    return out


def human_lines(found: Mapping[str, Any]) -> dict[str, str]:
    """``{run: stdout + stderr}`` of every run in a transcript."""
    return {key: (proc.stdout or "") + (proc.stderr or "") for key, proc in found.items()
            if hasattr(proc, "stdout")}
