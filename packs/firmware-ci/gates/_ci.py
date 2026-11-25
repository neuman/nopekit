# SPDX-License-Identifier: Apache-2.0
"""firmware-ci's workflow reader: the job that builds the firmware, its build step,
and the shapes that let a failed build read green. Standard library, and PyYAML
imported inside the loader (``requires_python``).

**Every FAIL rule is a documented behaviour** of GitHub (Workflow syntax for
GitHub Actions, "jobs.<job_id>.steps[*].shell" and "continue-on-error") or of the
shell it runs (bash(1), "set -e"): an unspecified shell on Linux runs ``bash -e
{0}``, or ``sh -e {0}`` where bash is absent, and neither sets ``pipefail``;
``shell: bash`` runs ``bash --noprofile --norc -eo pipefail {0}``;
``continue-on-error: true`` lets a job pass when a step fails; ``-e`` does not stop
the shell on a command before the last ``&&`` of a list. A replay cannot see any of
these: it reads green exactly where GitHub does.

**Errored, never failed:** a shape the replay cannot run (a Windows or macOS
runner, docker inside the job, a shell that is not bash or sh, a reusable workflow,
an expression only GitHub evaluates), and a job or build step this module cannot
choose. A build inside ``./build.sh`` is sound, so "no build step found" asks for
``firmware_ci_step``; it never fails the CI.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


class Refused(Exception):
    """A CI shape this pack cannot replay or choose in: errored, never failed."""


@dataclass(frozen=True)
class Fail:
    """The CI cannot build, or can read green on a failed build: a design fail."""
    reason: str


@dataclass(frozen=True)
class Workflow:
    """A workflow file as the loader read it."""
    path: str
    repo: str
    rel: str
    doc: object = None
    problem: str = ""


@dataclass(frozen=True)
class Job:
    """The job that builds, and its build step."""
    workflow: str           # repo-relative path of the workflow
    job: str
    index: int              # the build step's index in the job's steps
    name: str               # the build step's name as act reports it
    command: str            # the build command matched, or "uses: <action>"
    shell: str              # the effective shell, as GitHub runs it
    image: str              # the job's container image, or ""
    runs_on: str
    matrix: bool
    #: Each step's id as act reports it (``stepID``), by index: the step's own
    #: ``id:``, else its index. What slipped through the index alone (review of
    #: F1): act 0.2.97 logs a step that has an ``id:`` under that id, so a build
    #: step with ``id: build`` (common, for its outputs) "never ran", and the
    #: documented route of naming the step by its id in ``firmware_ci_step`` led
    #: there; a failed step before it with an id was never named.
    ids: tuple = ()
    #: Each step's ``run:`` text by index (``""`` for a ``uses:`` step): the
    #: judge reads whether a step before a failed install ran ``apt-get update``
    #: (``_replay.APT_NO_LISTS``).
    runs: tuple = ()
    #: The repository files the build step runs that this module does not read
    #: (``python3 build.py``, ``./build.sh``, ``bash scripts/x.sh``): the pass
    #: names them, so it never says more than the step's own lines show.
    unread: tuple = ()
    #: The shell scripts of the repository this module read to find the build
    #: (:func:`_search`), each judged by the masking rules: the pass names them.
    read: tuple = ()
    #: Why the build itself was not read, when it was not: the build runs inside
    #: an action, or in a script this module does not read, or behind a command
    #: it does not know. ``fw.ci_job`` then skips and says so, never passes.
    blind: str = ""

    def act_id(self, at: int) -> str:
        """The ``stepID`` act logs for step ``at``."""
        return self.ids[at] if at < len(self.ids) else str(at)


def _runs(steps: list) -> tuple:
    """Each step's ``run:`` text, ``""`` for a step with none."""
    return tuple(str(s.get("run") or "") if isinstance(s, dict) else "" for s in steps)


#: A command that runs a file of the repository through an interpreter, or by
#: a path with a ``/`` in it: ``python3 x.py``, ``bash x.sh``, ``sh x.sh``,
#: ``node x.js``, ``./x.sh``, ``scripts/x.sh`` (a bare ``idf.py`` is a tool on
#: ``PATH``, not a file of the repository). Where the build command is in the
#: step's own lines, a pass names each such file as not read. What slipped
#: through "no masking" there (holdout review, a console's upstream workflow):
#: the build step ran ``python3 <script>.py``, which runs ``docker build``
#: through ``subprocess.run(["bash", "-c", cmd])`` with no check, so a failed
#: build exits 0, and the pass said "no masking". Where the build command is not
#: in the step's own lines, the guard reads the shell scripts the step runs
#: (:func:`_search`), and a build it cannot read is a skip, never a pass (holdout
#: review round 2). *Rejected:* reading a Python, JavaScript or Perl script (a
#: program; the masking rules read the documented behaviour of a shell);
#: refusing every script (a build in a script is sound, and
#: ``firmware_ci_step`` exists for it).
_SCRIPT = re.compile(r"(?:^|(?<=[\s;&|(]))(?:(?:python[\d.]*|bash|sh|zsh|node|perl|ruby)"
                     r"(?:\s+-\S+)*\s+([\w./-]+\.(?:py|sh|bash|js|pl|rb))"
                     r"|([\w.-]*/[\w./-]*\.(?:sh|bash|py))(?=\s|$|;))")


def _scripts(run: str) -> tuple:
    """The repository files ``run`` hands to an interpreter or runs by path, in
    order, each once (:data:`_SCRIPT`)."""
    found = []
    for line in _lines(run):
        if line.startswith("#"):
            continue
        for m in _SCRIPT.finditer(_bare(line)):
            name = m.group(1) or m.group(2)
            if name and not name.startswith("/") and name not in found:
                found.append(name)
    return tuple(found)


#: A command that runs a file: sourced (``. env.sh``, ``source env.sh``: its
#: lines run in the caller's own shell, with its options), handed to an
#: interpreter with its options (``bash -e x.sh``, ``python3 x.py``), or run by
#: a path with a ``/`` in it (``./x.sh``, ``ci/x.sh``: its ``#!`` line chooses
#: the shell). The guard reads such a file when the build step's own lines hold
#: no build command (:func:`_search`).
_CALL = re.compile(
    r"(?:^|(?<=[\s;&|(]))(?:"
    r"(?:\.|source)\s+(?P<src>[\w./-]+)"
    r"|(?P<interp>python[\d.]*|bash|sh|dash|zsh|node|perl|ruby)"
    r"(?P<flags>(?:\s+(?:-o\s+\w+|[-+][\w-]+))*)\s+(?P<ifile>[\w./-]+\.(?:py|sh|bash|js|pl|rb))"
    r"|(?P<pfile>[\w.-]*/[\w./-]*\.(?:sh|bash|py))"
    r")(?=\s|$|[;&|)])")

#: The shells whose scripts the masking rules read: bash(1) and POSIX sh, as for
#: a step's own lines. *Rejected:* zsh (``setopt`` and its own ``ERR_EXIT`` rules
#: are not bash's); reading any file (a Python, JavaScript or Perl script is a
#: program, and the rules read the documented behaviour of a shell).
_READ_SHELLS = ("bash", "sh", "dash")
_LANGUAGES = {"py": "a Python script", "js": "a JavaScript file", "pl": "a Perl script",
              "rb": "a Ruby script"}


def _script_calls(text: str) -> list[tuple]:
    """``(name, line index, start, end, how, shell)`` of each file ``text`` runs
    (:data:`_CALL`), in order: ``how`` is ``source``, ``interp`` or ``path``;
    ``shell`` is the interpreter and its options for ``interp``."""
    out = []
    for i, line in enumerate(_lines(text)):
        if line.startswith("#"):
            continue
        for m in _CALL.finditer(_bare(line)):
            if m.group("src"):
                out.append((m.group("src"), i, m.start(), m.end(), "source", ""))
            elif m.group("ifile"):
                shell = (m.group("interp") + " " + " ".join(m.group("flags").split())).strip()
                out.append((m.group("ifile"), i, m.start(), m.end(), "interp", shell))
            else:
                out.append((m.group("pfile"), i, m.start(), m.end(), "path", ""))
    return out


def _script(repo: str, workdir: str, name: str, how: str, called: str,
            caller: str) -> tuple[str, str, str]:
    """``(path, shell, why not)`` of the file a call names: the shell it runs
    under, or why the guard does not read it. ``workdir`` is the step's
    ``working-directory`` (the repository when empty); a sourced file runs in
    the ``caller``'s shell; a file run by its path runs under its ``#!`` line,
    and with none, under a new shell with no options (bash(1), "COMMAND
    EXECUTION")."""
    if _EXPR in workdir:
        return "", "", "is under a working-directory only GitHub can evaluate"
    if name.startswith("/"):
        return "", "", "is outside the repository"
    root = os.path.realpath(repo)
    path = os.path.normpath(os.path.join(repo, workdir, name))
    if not (os.path.realpath(path) + os.sep).startswith(root + os.sep):
        return "", "", "is outside the repository"
    ext = name.rsplit(".", 1)[-1]
    if how == "interp":
        interp = called.split()[0]
        if interp not in _READ_SHELLS:
            what = "a zsh script" if interp == "zsh" else _LANGUAGES.get(ext, f"a {interp} file")
            return "", "", f"is {what}, which this gate does not read"
    elif how == "path" and ext in _LANGUAGES:
        return "", "", f"is {_LANGUAGES[ext]}, which this gate does not read"
    if not os.path.isfile(path):
        return "", "", "is not in the repository"
    if how == "source":
        return path, caller, ""
    if how == "interp":
        return path, called, ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        first = fh.readline().strip()
    if not first.startswith("#!"):
        return path, "bash", ""
    words = first[2:].split()
    if words and os.path.basename(words[0]) == "env":
        words = [w for w in words[1:] if w not in ("-S", "-i")]
    interp = os.path.basename(words[0]) if words else ""
    if interp not in _READ_SHELLS:
        return "", "", f"runs under {interp or 'an empty #! line'}, which this gate does not read"
    return path, " ".join([interp] + words[1:]), ""


@dataclass
class _Found:
    """What :func:`_search` found: the build command and the script that holds
    it, the scripts it read, and the calls it could not read, with why."""
    command: str = ""
    read: list = field(default_factory=list)
    unread: list = field(default_factory=list)


def _search(repo: str, workdir: str, text: str, shell: str, where: str, step: str,
            seen: set) -> _Found | Fail:
    """The build in the shell scripts ``text`` runs, each read and judged by the
    masking rules, as are the lines that run it: :class:`Fail` when one masks.

    The build step's own lines hold no build command this pack knows, so the
    guard reads the shell scripts they run (and the scripts those run), in the
    repository, under the shell each runs in: ``./build.sh`` under its ``#!``
    line, ``bash -e x.sh`` under ``bash -e``, a sourced file under the step's own
    shell. Each script is read once. A call that cannot be read is named with
    why (:func:`_script`). What slipped through a pass over such a step (holdout
    review round 2, a console's and a watch's upstream workflows): "no masking in
    the step's own lines; <script>.py is not read" was a pass over a build the
    guard never read. *Rejected:* following a ``cd`` to resolve a later call (the
    directory may be a variable; a call that does not resolve from the step's
    working directory is named, never guessed); reading a Python script or a
    local action (each is a program, not a shell's documented behaviour)."""
    found = _Found()
    for name, at, start, end, how, called in _script_calls(text):
        path, script_shell, why = _script(repo, workdir, name, how, called, shell)
        if why:
            found.unread.append((name, why))
            continue
        rel = os.path.relpath(path, repo).replace(os.sep, "/")
        if rel in seen:
            continue
        seen.add(rel)
        with open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        found.read.append(rel)
        inner = f"{rel} (run by step {step!r})"
        if _DOCKER_RUN.search(body):
            raise Refused(f"{rel}, which step {step!r} runs, runs docker inside the job; the "
                          f"replay mounts no docker socket into jobs. Run the build in a "
                          f"container job instead (references/ci-templates.md)")
        commands = _build_commands(body)
        if commands:
            problem = _masked(body, commands, script_shell, inner)
            command = f"{commands[0][0]} in {rel}"
        else:
            sub = _search(repo, workdir, body, script_shell, inner, step, seen)
            if isinstance(sub, Fail):
                return sub
            found.read += [r for r in sub.read if r not in found.read]
            found.unread += sub.unread
            if not sub.command:
                continue
            problem, command = None, sub.command
        problem = problem or _masked(text, [(name, at, start, end)], shell, where)
        if problem:
            return Fail(problem)
        found.command = command
        return found
    return found


def _act_ids(steps: list) -> tuple:
    """Each step's ``stepID`` under act 0.2.97 (``newJobExecutor``: a step with no
    ``id:`` gets its index)."""
    out = []
    for at, step in enumerate(steps):
        sid = step.get("id") if isinstance(step, dict) else None
        out.append(str(sid) if sid not in (None, "") else str(at))
    return tuple(out)


#: The closed list of build commands (a ``run:`` step that holds one is the build
#: step). Each must start a command: a line, or a segment after ``&&``, ``||``, ``;``,
#: ``|`` or a parenthesis, past any ``VAR=value``, ``sudo``, ``time``, ``env`` or
#: ``then`` and past a directory (``$HOME/bin/arduino-cli``). So ``cmake --build`` is
#: not ``make``, ``build_dir`` is not ``build``, and ``apt-get install ... make`` is
#: not a build. What slipped through a match anywhere in the line: the STM32 template's
#: toolchain step installs the package ``make``, and read as a second build step. A
#: build the list does not hold is named in ``firmware_ci_step``. *Rejected:* "the
#: last run step" (a test or an upload step is often last).
BUILD_COMMANDS = (
    ("west build", re.compile(r"(?:\S*/)?west\s+build\b")),
    ("idf.py build", re.compile(r"(?:\S*/)?idf\.py\b[^;&|\n]*?\s(?:build|all|app)(?![\w.-])")),
    ("pio run", re.compile(r"(?:\S*/)?(?:pio|platformio)\s+run\b")),
    ("arduino-cli compile", re.compile(r"(?:\S*/)?arduino-cli\s+compile\b")),
    ("qmk compile", re.compile(r"(?:\S*/)?qmk\s+compile\b")),
    ("cmake --build", re.compile(r"(?:\S*/)?cmake\s+--build\b")),
    ("make", re.compile(r"(?:\S*/)?make(?=\s|$)")),
    ("ninja", re.compile(r"(?:\S*/)?ninja(?=\s|$)")),
)

#: Where a command starts inside a line, and what may come before its name.
_SEGMENT = re.compile(r"&&|\|\||[;|()]")
_LEAD = re.compile(r"\s*(?:(?:then|do|else|sudo|time|exec|nice|env|!)\s+|[A-Za-z_]\w*=\S*\s+)*")

#: ``|| true``, ``|| :``, ``|| exit 0`` and ``|| echo`` after the build command, in
#: its own list: the list's status is then the fallback's, whatever the build did.
#: *Rejected:* every ``||`` fallback that does not fail (``|| idf.py fullclean
#: build`` is a retry, and its own failure fails the step).
_OR_MASK = re.compile(r"(?:true|:|exit\s+0|echo)(?:\s|$)")

#: A status the next command can read: ``$?`` and ``PIPESTATUS`` (``$PIPESTATUS``
#: is its element 0). After a pipeline only ``PIPESTATUS`` holds the build's.
_STATUS = re.compile(r"\$\?|\$\{\?\}|\$\{?PIPESTATUS\b")
_PIPESTATUS = re.compile(r"\$\{?PIPESTATUS\b")

#: ``rc=$?`` or ``rc=${PIPESTATUS[0]}``: the status kept for a later check.
_CAPTURE = re.compile(r"(?:(?:local|export|declare|readonly|typeset)\s+)?([A-Za-z_]\w*)=([\"']?)(\$\?|\$\{\?\}|\$\{?PIPESTATUS(?:\[\d+\])?\}?)\2$")

#: What stops the step with a failure: ``exit`` with any status but 0 (a bare
#: ``exit`` exits with the last command's), ``false``, ``return`` but ``return 0``.
_FAILS = re.compile(r"(?<![\w.$-])(?:exit(?!\s+0(?:\s|$|;))(?=\s|$|;)|false(?=\s|$|;)"
                    r"|return(?!\s+0(?:\s|$|;))(?=\s|$|;))")

#: A test command: under ``-e`` a failed test stops the step.
_TEST = re.compile(r"(?:\[\[?|test)\s")

#: ``set -e`` and its kin, and ``set -o errexit``: the last before a build decides
#: whether its failure stops the script; ``set -o pipefail`` the same for a pipe.
_ERREXIT = re.compile(r"(?:^|[;&|(\s])set\s+(?:([+-])[A-Za-z]*e[A-Za-z]*(?![\w-])"
                      r"|([+-])o\s+errexit\b)")
_PIPEFAIL = re.compile(r"(?:^|[;&|(\s])set\s+([+-])[A-Za-z]*o\s+pipefail\b")

#: What a step that runs docker inside the job looks like. The replay mounts no
#: docker socket into jobs (``--container-daemon-socket -``), so such a step cannot
#: run there. espressif/esp-idf-ci-action is a composite step that runs ``docker
#: run -v ${GITHUB_WORKSPACE}`` (its action.yml).
_DOCKER_RUN = re.compile(r"(?<![\w.-])docker\s+(?:run|build|compose|exec)\b")
_DOCKER_ACTIONS = re.compile(r"^(?:docker://|espressif/esp-idf-ci-action\b)")

_EXPR = "${{"


def locate(path: str) -> tuple[str, str]:
    """``(repo, rel)``: the repository is the directory above the workflow's
    ``<x>/workflows/`` directory's parent. GitHub reads ``.github/workflows/``; the
    pack's own control keeps its workflow at ``github/workflows/``, because nopekit
    keys a pack's controls by a walk of ``selftest/`` that skips dot-directories."""
    path = os.path.abspath(path)
    if os.path.basename(os.path.dirname(path)) != "workflows":
        raise Refused(f"{os.path.basename(path)} is not in a workflows/ directory: it is "
                      f"not a GitHub-Actions-style workflow, and the pack cannot tell "
                      f"which repository CI checks out")
    repo = os.path.dirname(os.path.dirname(os.path.dirname(path)))
    return repo, os.path.relpath(path, repo).replace(os.sep, "/")


def load(path: str) -> Workflow:
    """The workflow file, parsed; a missing or unparseable file is a Workflow with
    its ``problem`` set (the guard's FAIL). The loader of ``ctx.load_file``."""
    repo, rel = locate(path)
    import yaml  # PyYAML, inside the pack only (requires_python)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except FileNotFoundError:
        return Workflow(path, repo, rel, problem=f"no workflow at {rel}: CI cannot run "
                                                 f"this workflow")
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        return Workflow(path, repo, rel, problem=f"{rel} does not parse "
                                                 f"({type(exc).__name__}): CI cannot run "
                                                 f"this workflow")
    return Workflow(path, repo, rel, doc=doc)


def _steps(job: dict) -> list:
    steps = job.get("steps") or []
    return [s for s in steps] if isinstance(steps, list) else []


def _run_defaults(doc: dict, job: dict) -> dict:
    """``defaults.run`` as GitHub resolves it: each setting from the most specific
    level that sets it. What slipped through before: the first level with any
    ``defaults.run`` was taken whole, so a job's ``shell: bash`` hid the
    workflow's ``working-directory``."""
    merged: dict = {}
    for level in (doc, job):
        run = ((level or {}).get("defaults") or {}).get("run") or {}
        if not isinstance(run, dict):
            raise Refused(f"defaults.run is not a mapping: {run!r}")
        merged.update({k: v for k, v in run.items() if k in ("shell", "working-directory")})
    return merged


def _builds_in(line: str) -> list[tuple[str, int, int]]:
    """``(name, start, end)`` of each build command that starts a command in
    ``line``."""
    if line.lstrip().startswith("#"):
        return []
    out = []
    for at in [0] + [m.end() for m in _SEGMENT.finditer(line)]:
        lead = _LEAD.match(line, at)
        pos = lead.end() if lead else at
        for name, rx in BUILD_COMMANDS:
            m = rx.match(line, pos)
            if m:
                out.append((name, m.start(), m.end()))
                break
    return out


def _build_in(line: str) -> tuple[str, int, int] | None:
    """``(name, start, end)`` of the first build command in ``line``, or ``None``."""
    found = _builds_in(line)
    return found[0] if found else None


def _build_commands(run: str) -> list[tuple[str, int, int, int]]:
    """``(name, line index, start, end)`` of each build command in ``run``, every
    one on a line: the masking rules read each (``_masked``)."""
    return [(name, i, start, end) for i, line in enumerate(_lines(run))
            for name, start, end in _builds_in(line)]


def _step_name(step: dict, at: int) -> str:
    """The step's name as act reports it: ``name``, else ``uses``, else ``run``'s
    first line."""
    if step.get("name"):
        return str(step["name"])
    if step.get("uses"):
        return str(step["uses"])
    run = str(step.get("run") or "").strip().splitlines()
    return run[0] if run else f"step {at}"


#: How a refusal says a step is named in ``firmware_ci_step`` (:func:`_named`).
#: What slipped through naming two handles of three (holdout review rounds 1
#: and 2, a watch): an unnamed ``uses:`` step is named by its ``uses:`` value,
#: and the refusals, the key table and the baseline's notes said only "its run's
#: first line", so a person had nothing to name it by but ``_step_name``.
HANDLE = ("its name, its id, or, with neither, the name act logs for it: its `uses:` "
          "value, else its run's first line")


def _named(step: dict, at: int, wanted: str) -> bool:
    """Whether ``firmware_ci_step`` ``wanted`` names this step: its ``name:``, its
    ``id:``, or the name act logs for it (``_step_name``: for a step with no name,
    its ``uses:`` value, else its run's first line; :data:`HANDLE`). What slipped
    through (holdout review, a handheld): the run-line handle worked and no
    document said so."""
    return wanted in (str(step.get("name") or ""), str(step.get("id") or ""),
                      _step_name(step, at))


def choose(wf: Workflow, job: str | None = None, step: str | None = None):
    """The build job as a :class:`Job`, a :class:`Fail`, or :class:`Refused`."""
    if wf.problem:
        return Fail(wf.problem)
    doc, rel = wf.doc, wf.rel
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict) or not jobs:
        return Fail(f"{rel} has no jobs: CI cannot run this workflow")
    if job is not None:
        if str(job) not in {str(k) for k in jobs}:
            raise Refused(f"firmware_ci_job names {job!r}, and {rel} has no such job "
                          f"(it has {', '.join(sorted(map(str, jobs)))})")
        jid = next(k for k in jobs if str(k) == str(job))
    else:
        candidates = []
        for k, body in jobs.items():
            if not isinstance(body, dict):
                continue
            if step is not None:
                hit = any(isinstance(s, dict) and _named(s, i, step)
                          for i, s in enumerate(_steps(body)))
            else:
                hit = any(isinstance(s, dict) and isinstance(s.get("run"), str)
                          and _build_commands(s["run"]) for s in _steps(body))
            if hit:
                candidates.append(k)
        if not candidates:
            _docker_anywhere(rel, jobs)
        if len(candidates) != 1:
            said = (f"{len(candidates)} jobs build ({', '.join(map(str, candidates))})"
                    if candidates else
                    f"no job has a step named {step!r} ({HANDLE})" if step is not None else
                    "no job runs a build command this pack knows")
            raise Refused(f"{rel}: {said}; name the job in firmware_ci_job")
        jid = candidates[0]
    body = jobs[jid]
    jid = str(jid)
    if not isinstance(body, dict):
        return Fail(f"{rel}: job {jid} is not a mapping: CI cannot run it")
    if "uses" in body:
        raise Refused(f"job {jid} calls the reusable workflow {body['uses']}: its build "
                      f"step is in a file this pack does not read, and ZMK's builds "
                      f"outside its workspace; put the build in the job "
                      f"(references/ci-templates.md)")
    steps = _steps(body)
    at = _build_step(rel, jid, steps, step)
    build = steps[at]
    name = _step_name(build, at)
    for where, node in ((f"job {jid}", body), (f"step {name!r}", build)):
        for key in ("if", "continue-on-error"):
            if _EXPR in str(node.get(key, "")):
                raise Refused(f"{where} has a ${{{{ }}}} expression in `{key}:`; whether "
                              f"it runs, or counts, is GitHub's to decide")
        if node.get("continue-on-error") is True or \
                str(node.get("continue-on-error", "")).strip().lower() == "true":
            return Fail(f"{where} has `continue-on-error: true`: the job passes when the "
                        f"build fails, so a green run says nothing")
    runs_on, image = runner(jid, body)
    defaults = _run_defaults(doc, body)
    if isinstance(build.get("uses"), str):
        uses = build["uses"].strip()
        if _DOCKER_ACTIONS.match(uses):
            raise Refused(f"step {name!r} uses {uses}, which runs docker inside the job; "
                          f"the replay mounts no docker socket into jobs. Run the build "
                          f"in a container job instead (references/ci-templates.md, "
                          f"ESP-IDF)")
        return Job(rel, jid, at, name, f"uses: {uses}", "", image, runs_on,
                   _matrix(body), _act_ids(steps), _runs(steps),
                   blind=f"the build runs inside the action {uses}, and this gate does not "
                         f"read the action's own steps")
    shell = _shell(build, defaults, name)
    run = str(build.get("run") or "")
    if _EXPR in run and re.search(r"\$\{\{[^}]*\}\}", run) and not _build_commands(run):
        raise Refused(f"step {name!r} builds through a ${{{{ }}}} expression only GitHub "
                      f"can evaluate")
    if _DOCKER_RUN.search(run):
        raise Refused(f"step {name!r} runs docker inside the job; the replay mounts no "
                      f"docker socket into jobs. Run the build in a container job "
                      f"instead (references/ci-templates.md)")
    found = _build_commands(run)
    if found:
        problem = _masked(run, found, shell, f"step {name!r}")
        if problem:
            return Fail(problem)
        return Job(rel, jid, at, name, found[0][0], shell, image, runs_on, _matrix(body),
                   _act_ids(steps), _runs(steps), _scripts(run))
    # No build command in the step's own lines: the shell scripts it runs are
    # read (`_search`), and a build this gate cannot read is said (`blind`),
    # never passed. What slipped through (holdout review round 2, a console's
    # and a watch's upstream workflows): "no masking in the step's own lines;
    # <script>.py is not read", a pass over a build the guard never read.
    workdir = str(build.get("working-directory", defaults.get("working-directory")) or "")
    search = _search(wf.repo, workdir, run, shell, f"step {name!r}", name, set())
    if isinstance(search, Fail):
        return search
    unread = tuple(dict.fromkeys(n for n, _why in search.unread))
    if search.command:
        return Job(rel, jid, at, name, search.command, shell, image, runs_on, _matrix(body),
                   _act_ids(steps), _runs(steps), unread, tuple(search.read))
    said = [f"{n} {why}" for n, why in search.unread]
    said += [f"{r} runs no build command this pack knows" for r in search.read]
    first = next((ln for ln in _lines(run) if ln and not ln.startswith("#")), "")
    blind = ("the build is not in the step's own lines, and this gate cannot read where it "
             "runs: " + ("; ".join(said) if said else
                         f"{first!r} is not a build command this pack knows"))
    return Job(rel, jid, at, name, "the named step", shell, image, runs_on, _matrix(body),
               _act_ids(steps), _runs(steps), unread, tuple(search.read), blind)


def _docker_anywhere(rel: str, jobs: dict) -> None:
    """With no job that builds, a step that builds through docker is the reason:
    refused with the template that fixes it, never "name the job"."""
    for jid, body in jobs.items():
        for at, step in enumerate(_steps(body) if isinstance(body, dict) else []):
            if not isinstance(step, dict):
                continue
            uses, run = str(step.get("uses") or ""), str(step.get("run") or "")
            if _DOCKER_ACTIONS.match(uses.strip()) or _DOCKER_RUN.search(run):
                raise Refused(f"{rel}: step {_step_name(step, at)!r} of job {jid} runs "
                              f"docker inside the job; the replay mounts no docker "
                              f"socket into jobs. Run the build in a container job instead "
                              f"(references/ci-templates.md)")


def _build_step(rel: str, jid: str, steps: list, wanted: str | None) -> int:
    if wanted is not None:
        hits = [i for i, s in enumerate(steps) if isinstance(s, dict) and _named(s, i, wanted)]
        if len(hits) != 1:
            raise Refused(f"firmware_ci_step names {wanted!r}, which matches "
                          f"{len(hits)} steps of job {jid} in {rel} (a step is named by "
                          f"{HANDLE})")
        return hits[0]
    hits = [i for i, s in enumerate(steps)
            if isinstance(s, dict) and isinstance(s.get("run"), str) and _build_commands(s["run"])]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise Refused(f"job {jid} in {rel} runs no build command this pack knows (a build "
                      f"in a script is sound); name the build step in firmware_ci_step "
                      f"({HANDLE})")
    names = ", ".join(repr(_step_name(steps[i], i)) for i in hits)
    raise Refused(f"job {jid} in {rel} builds in {len(hits)} steps ({names}); name the "
                  f"build step in firmware_ci_step")


def _matrix(body: dict) -> bool:
    strategy = body.get("strategy")
    return isinstance(strategy, dict) and "matrix" in strategy


def runner(jid: str, body: dict) -> tuple[str, str]:
    """``(runs-on, container image)``. act runs Linux containers only: a Windows,
    macOS or self-hosted runner with no container is refused, and so is a runner
    only GitHub can name (an expression)."""
    runs_on = body.get("runs-on")
    container = body.get("container")
    image = ""
    if isinstance(container, str):
        image = container
    elif isinstance(container, dict):
        image = str(container.get("image") or "")
    if _EXPR in image:
        raise Refused(f"job {jid}'s container image is an expression only GitHub can "
                      f"evaluate ({image})")
    labels = runs_on if isinstance(runs_on, list) else [runs_on]
    if isinstance(runs_on, dict):
        labels = list(runs_on.get("labels") or []) + [runs_on.get("group")]
    text = " ".join(str(x) for x in labels if x is not None)
    if _EXPR in text:
        raise Refused(f"job {jid} runs on {text}, an expression only GitHub can evaluate; "
                      f"name a Linux runner")
    if not image and re.search(r"\b(?:windows|macos|self-hosted)", text, re.I):
        raise Refused(f"job {jid} runs on {text} with no container; the replay runs "
                      f"Linux containers only (references/ci-templates.md)")
    return text, image


#: Shells GitHub runs a step under: the keyword's command line (Workflow syntax,
#: "jobs.<job_id>.steps[*].shell"). Only bash and sh are replayed.
_SHELLS = {None: "bash -e {0}", "bash": "bash --noprofile --norc -eo pipefail {0}",
           "sh": "sh -e {0}"}


def _shell(step: dict, defaults: dict, name: str) -> str:
    shell = step.get("shell", defaults.get("shell"))
    if shell in _SHELLS:
        return _SHELLS[shell]
    text = str(shell).strip()
    if re.match(r"^(?:/usr)?(?:/bin/)?(?:ba)?sh(?:\s|$)", text) and "{0}" in text:
        return text
    raise Refused(f"step {name!r} runs under shell {text!r}; the pack reads bash and sh")


def _lines(run: str) -> list[str]:
    """The script's logical lines: a backslash-newline joins two."""
    return [ln.strip() for ln in run.replace("\\\n", " ").splitlines()]


#: The separators a shell command line splits at, longest first. ``|&`` is bash's
#: pipe of both streams.
_SEPS = ("&&", "||", "|&", ";", "|", "&", "\n")
_LIST_SEPS = ("&&", "||", "|", "|&")


def _pieces(text: str) -> list[tuple[str, str | None]]:
    """``text`` as ``[(command, separator after it)]``, split at the separators
    outside quotes, ``$( )``, ``( )`` and ``{ }``, with comments left out. A line
    that ends in ``&&``, ``||`` or ``|`` runs on to the next, as the shell reads it.
    Not a shell parser: a heredoc's body reads as commands."""
    out, buf, i, n, depth, quote = [], [], 0, len(text), 0, None

    def word_start(at: int) -> bool:
        return at == 0 or text[at - 1] in " \t\n;&|("

    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if quote == '"' and c == "\\" and i + 1 < n:
                buf.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf += [c, text[i + 1]]
            i += 2
            continue
        if c in "'\"":
            quote = c
        elif c == "#" and word_start(i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        elif c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        elif c == "{" and word_start(i) and text[i + 1:i + 2] in (" ", "\t", "\n"):
            depth += 1
        elif c == "}" and word_start(i):
            depth = max(0, depth - 1)
        elif depth == 0:
            sep = next((x for x in _SEPS if text.startswith(x, i)), None)
            # ``2>&1``, ``>&2`` and ``&>file`` are redirections, not ``&``.
            if sep == "&" and (text[i - 1:i] in ("<", ">") or text[i + 1:i + 2] == ">"):
                sep = None
            if sep is not None:
                out.append(("".join(buf).strip(), sep))
                buf = []
                i += len(sep)
                if sep in _LIST_SEPS:
                    while i < n and text[i] in " \t\n":
                        i += 1
                continue
        buf.append(c)
        i += 1
    out.append(("".join(buf).strip(), None))
    return [(cmd, sep) for cmd, sep in out if cmd]


def _bare(cmd: str) -> str:
    """``cmd`` with what its quotes hold blanked: ``echo "exit 1"`` exits nothing."""
    return re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", lambda m: " " * len(m.group(0)), cmd)


def _and_or(pieces: list, at: int) -> int:
    """The index of the last piece of the and-or list that starts at ``at``."""
    end = at
    while end + 1 < len(pieces) and pieces[end][1] in _LIST_SEPS:
        end += 1
    return end


def _stops(pieces: list, at: int, errexit: bool) -> bool:
    """Whether the check at ``pieces[at]`` stops the step with a failure when the
    build failed: an ``exit``, ``false`` or ``return`` in its own list (``[ $? -eq 0 ]
    || exit 1``) or in the block of the ``if`` it opens, or, under ``-e``, a test
    that ends its list (``[ ${PIPESTATUS[0]} -eq 0 ]``)."""
    end = _and_or(pieces, at)
    if any(_FAILS.search(_bare(cmd)) for cmd, _sep in pieces[at:end + 1]):
        return True
    cmd = pieces[at][0]
    if re.match(r"(?:if|elif)\s", cmd):
        depth = 0
        for body, _sep in pieces[at:]:
            if re.match(r"if\s", body):
                depth += 1
            if _FAILS.search(_bare(body)):
                return True
            if re.match(r"fi(?:\s|$)", body):
                depth -= 1
                if depth <= 0:
                    return False
        return False
    return errexit and end == at and _TEST.match(cmd) is not None


def _checked(later: list, errexit: bool, pipe: bool) -> bool:
    """Whether the first command after the build's list checks the build's status
    and fails the step on it, directly or through a variable that keeps it
    (``rc=$?`` and a later ``exit $rc``). What slipped through any mention of
    ``$?`` on any later line (review of F1): ``echo "build exited $?"`` after
    ``set +e`` counted as a check, and the step read the echo's status."""
    if not later:
        return False
    status = _PIPESTATUS if pipe else _STATUS
    first = later[0][0]
    kept = _CAPTURE.match(first)
    if kept and status.search(kept.group(3)):
        var = re.compile(r"\$\{?" + re.escape(kept.group(1)) + r"\b")
        return any(var.search(cmd) and _stops(later, at, errexit)
                   for at, (cmd, _sep) in enumerate(later) if at > 0)
    return status.search(first) is not None and _stops(later, 0, errexit)


def _masked(run: str, found: list, shell: str, where: str) -> str | None:
    """Why a failed build in this step can read green, or ``None``. Every build
    command in the step is read, each with the commands after it: those later in
    its line (after a ``;``) and on the lines below.

    What slipped through reading the first build command's line alone (review of
    F1): a second ``west build`` piped into ``tee`` on the next line, and ``idf.py
    build && echo built; echo done`` (the commands after the ``;`` were not "later"),
    both green on a failed build under GitHub's ``bash -e {0}``."""
    lines = _lines(run)
    script = "\n".join(lines)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    for _cmd, at, start, _end in found:
        problem = _masked_at(script, starts[at] + start, lines[at], shell, where)
        if problem:
            return problem
    return None


def _masked_at(script: str, offset: int, line: str, shell: str, where: str) -> str | None:
    """The masking rules for the build command at ``offset`` in ``script``;
    ``where`` names what holds it in the reason (``step 'Build'``, or a script
    and the step that runs it)."""
    pieces = _pieces(script[offset:])
    if not pieces:
        return None
    end = _and_or(pieces, 0)
    own, later = pieces[:end + 1], pieces[end + 1:]
    if any(sep == "||" and _OR_MASK.match(nxt) for (_c, sep), (nxt, _s) in zip(own, own[1:])):
        return (f"{where}: {line!r} falls back to a command that succeeds, so the "
                f"step reads green whatever the build did")
    errexit = re.search(r"(?:^|\s)-\w*e", shell) is not None
    before = script[:offset]
    set_e = [m.group(1) or m.group(2) for m in _ERREXIT.finditer(before)]
    if set_e:
        errexit = set_e[-1] == "-"
    checked = _checked(later, errexit, pipe=False) or any(
        sep == "||" and _FAILS.search(_bare(nxt))
        for (_c, sep), (nxt, _s) in zip(own, own[1:]))
    if not errexit and later and not checked:
        why = "after `set +e`" if set_e else f"under {shell!r}, which has no -e"
        return (f"{where} runs the build {why} and then {later[0][0]!r}, with no "
                f"status check that fails the step: the step's status is the last "
                f"command's, so a failed build reads green")
    # `-e` does not stop the shell on a command before the last `&&` of a list
    # (bash(1) and POSIX sh, "set -e": "any command executed in a && or || list
    # except the command following the final && or ||"). So `idf.py build && echo
    # built`, then another command, runs on after a failed build, and the step reads
    # the last command's status. Found in review of F1, beyond the design's four
    # forms: the same documented behaviour as the default shell's missing pipefail.
    if any(sep == "&&" for _c, sep in own[:-1]) and later and not checked:
        return (f"{where} runs the build before an `&&` ({line!r}) and then "
                f"{later[0][0]!r}: -e does not stop the shell on a command before the "
                f"last `&&`, so the step's status is the last command's and a failed "
                f"build reads green")
    if pieces[0][1] in ("|", "|&"):
        signs = [m.group(1) for m in _PIPEFAIL.finditer(before)]
        pipefail = signs[-1] == "-" if signs else "pipefail" in shell
        # PIPESTATUS holds the build's status only up to the next command: after
        # `| tee log && echo ok` it is echo's, and `| tee log || exit 1` reads tee's.
        piped = all(sep in ("|", "|&") for _c, sep in pieces[:end])
        if not pipefail and not (piped and _checked(later, errexit, pipe=True)):
            return (f"{where} pipes the build ({line!r}) under {shell!r}, which has "
                    f"no pipefail: the pipeline's status is its last command's, so a "
                    f"failed build reads green")
    return None
