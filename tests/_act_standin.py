# SPDX-License-Identifier: Apache-2.0
"""A stand-in for act and docker, for tests/test_firmware_ci.py.

Installed twice into a test's own bin directory, as ``act`` and as ``docker``, so the
whole replay path runs where neither tool is: the copy, its git commit, act's argv
and environment, the JSON log, the files the job wrote, the read-back and the
removal. None of its output is act's own words except the JSON log a test hands it,
which is a committed excerpt of a real replay (tests/fw_logs/).

What it does is set by ``mode.json`` beside it, which the test writes:

- ``version``: what ``act --version`` prints (default 0.2.97); ``version_crash``:
  ``act --version`` writes a loader error and exits 127.
- ``log``: a JSONL file printed as act's stdout, with ``copy_in_log`` (the copy
  path the excerpt was recorded under) replaced by the real copy; ``stderr``,
  ``exit`` and ``sleep`` (seconds before anything is written).
- ``write``: ``{rel: {"size": n, "head": "<hex>"}}``, files the "job" writes into the
  copy, ``head`` first and zero bytes after.

Each call is logged to ``calls`` beside it as one JSON object: the tool, its argv,
the environment the replay set, and, for ``act push``, what the copy held (its
files and whether git had a commit there).

Not a test module: its name starts with ``_``, as tests/_env.py's does.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "mode.json"), encoding="utf-8") as fh:
    MODE = json.load(fh)
TOOL = os.path.basename(sys.argv[0])
ARGS = sys.argv[1:]


def log(entry):
    with open(os.path.join(HERE, "calls"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def opt(name):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else None


if TOOL == "docker":
    log({"tool": "docker", "argv": ARGS})
    if ARGS[:2] == ["image", "inspect"]:
        print("sha256:" + "0" * 64)
    sys.exit(0)

if "--version" in ARGS:
    log({"tool": "act", "argv": ARGS})
    if MODE.get("version_crash"):
        sys.stderr.write("act: error while loading shared libraries: stand-in.so\n")
        sys.exit(127)
    print(f"act version {MODE.get('version', '0.2.97')}")
    sys.exit(0)

copy = opt("-C")
files = sorted(os.path.relpath(os.path.join(d, f), copy).replace(os.sep, "/")
               for d, _dirs, names in os.walk(copy) if ".git" not in d.split(os.sep)
               for f in names)
head = subprocess.run(["git", "-C", copy, "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                      stderr=subprocess.DEVNULL, text=True)
log({"tool": "act", "argv": ARGS, "files": files, "commit": head.returncode == 0,
     "env": {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME",
                                            "ACT_DISABLE_VERSION_CHECK")}})
time.sleep(MODE.get("sleep", 0))
for rel, spec in (MODE.get("write") or {}).items():
    path = os.path.join(copy, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    first = bytes.fromhex(spec.get("head", ""))
    with open(path, "wb") as fh:
        fh.write(first + b"\x00" * max(0, spec.get("size", len(first)) - len(first)))
if MODE.get("log"):
    with open(MODE["log"], encoding="utf-8") as fh:
        text = fh.read()
    if MODE.get("copy_in_log"):
        text = text.replace(MODE["copy_in_log"], copy)
    sys.stdout.write(text)
sys.stderr.write(MODE.get("stderr", ""))
sys.exit(int(MODE.get("exit", 0)))
