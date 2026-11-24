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
from dataclasses import dataclass


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

#: ``|| true``, ``|| :``, ``|| exit 0`` and ``|| echo`` after the build command:
#: the line's status is then the fallback's, whatever the build did.
_OR_MASK = re.compile(r"\|\|\s*(?:true\b|:(?=\s|$|;)|exit\s+0\b|echo\b)")

#: A pipe after the build command (one ``|``, not ``||``): under a shell with no
#: pipefail, the pipeline's status is its last command's.
_PIPE_AFTER = re.compile(r"(?<!\|)\|(?!\|)")

#: A status check after the build: ``$?``, ``PIPESTATUS``, ``exit $?``.
_STATUS_CHECK = re.compile(r"\$\?|\$\{\?\}|PIPESTATUS")

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


def _build_in(line: str) -> tuple[str, int, int] | None:
    """``(name, start, end)`` of the first build command that starts a command in
    ``line``, or ``None``."""
    if line.lstrip().startswith("#"):
        return None
    bounds = [0] + [m.end() for m in _SEGMENT.finditer(line)]
    for at in bounds:
        lead = _LEAD.match(line, at)
        pos = lead.end() if lead else at
        for name, rx in BUILD_COMMANDS:
            m = rx.match(line, pos)
            if m:
                return name, m.start(), m.end()
    return None


def _build_commands(run: str) -> list[tuple[str, int, int, int]]:
    """``(name, line index, start, end)`` of each line of ``run`` that builds."""
    found = []
    for i, line in enumerate(_lines(run)):
        hit = _build_in(line)
        if hit:
            found.append((hit[0], i, hit[1], hit[2]))
    return found


def _step_name(step: dict, at: int) -> str:
    """The step's name as act reports it: ``name``, else ``uses``, else ``run``'s
    first line."""
    if step.get("name"):
        return str(step["name"])
    if step.get("uses"):
        return str(step["uses"])
    run = str(step.get("run") or "").strip().splitlines()
    return run[0] if run else f"step {at}"


def _named(step: dict, at: int, wanted: str) -> bool:
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
                    f"no job has a step named {step!r}" if step is not None else
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
                   _matrix(body))
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
    command = found[0][0] if found else "the named step"
    problem = _masked(run, found, shell, name)
    if problem:
        return Fail(problem)
    return Job(rel, jid, at, name, command, shell, image, runs_on, _matrix(body))


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
                          f"{len(hits)} steps of job {jid} in {rel}")
        return hits[0]
    hits = [i for i, s in enumerate(steps)
            if isinstance(s, dict) and isinstance(s.get("run"), str) and _build_commands(s["run"])]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise Refused(f"job {jid} in {rel} runs no build command this pack knows (a build "
                      f"in a script is sound); name the build step in firmware_ci_step")
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


def _masked(run: str, found: list, shell: str, name: str) -> str | None:
    """Why a failed build in this step can read green, or ``None``."""
    if not found:
        return None
    lines = _lines(run)
    _cmd, at, _start, end = found[0]
    line = lines[at]
    tail = line[end:]
    if _OR_MASK.search(tail):
        return (f"step {name!r}: {line!r} falls back to a command that succeeds, so the "
                f"step reads green whatever the build did")
    later = [ln for ln in lines[at + 1:] if ln and not ln.startswith("#")]
    errexit = re.search(r"(?:^|\s)-\w*e", shell) is not None
    before = "\n".join(lines[:at])
    # `set -e`, `set +e`, `set -eo pipefail`, `set -euo pipefail`: the last one before
    # the build decides whether a failed build stops the script.
    set_e = [m.group(1) for m in re.finditer(r"(?:^|[;&\s])set\s+([+-])[A-Za-z]*e", before)]
    if set_e:
        errexit = set_e[-1] == "-"
    checked = any(_STATUS_CHECK.search(ln) for ln in later) or \
        re.search(r"\|\|\s*(?:exit\s+[1-9$]|false\b|return\b)", tail)
    if not errexit and later and not checked:
        why = "after `set +e`" if set_e else f"under {shell!r}, which has no -e"
        return (f"step {name!r} runs the build {why} and then {later[0]!r}, with no "
                f"status check: the step's status is the last command's, so a failed "
                f"build reads green")
    # `-e` does not stop the shell on a command before the last `&&` of a list
    # (bash(1) and POSIX sh, "set -e": "any command executed in a && or || list
    # except the command following the final && or ||"). So `idf.py build && echo
    # built`, then another line, runs on after a failed build, and the step reads
    # the last line's status. Found in review of F1, beyond the design's four forms:
    # the same documented behaviour as the default shell's missing pipefail.
    in_list = re.split(r";|(?<!\|)\|(?!\|)", tail)[0]
    if "&&" in in_list and later and not checked:
        return (f"step {name!r} runs the build before an `&&` ({line!r}) and then "
                f"{later[0]!r}: -e does not stop the shell on a command before the "
                f"last `&&`, so the step's status is the last command's and a failed "
                f"build reads green")
    pipe = _PIPE_AFTER.search(tail)
    if pipe:
        pipefail = "pipefail" in shell or re.search(r"set\s+-\w*o\s+pipefail|set\s+-o\s+pipefail",
                                                    before)
        pipestatus = any("PIPESTATUS" in ln for ln in [line] + later)
        if not pipefail and not pipestatus:
            return (f"step {name!r} pipes the build ({line!r}) under {shell!r}, which has "
                    f"no pipefail: the pipeline's status is its last command's, so a "
                    f"failed build reads green")
    return None
