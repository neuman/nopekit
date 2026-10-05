"""Every groundspace opens its own site from the editor: `.vscode/launch.json`.

What slipped through: a groundspace built end to end had a site and no way to open it
short of knowing `atompipe site serve` existed. These tests hold four things:

* `init`, `site init` and `site build` each leave a launch entry behind when there is
  none, so a new groundspace has one and an older one gains it on its next site build;
* a launch.json the person already has is never touched (they are often JSON with
  comments, which no writer here can round-trip);
* the pattern the editor waits for matches the line `site serve` prints once it is
  listening — the two live side by side in `site.py`, and this is what stops one moving
  without the other;
* the configured command, run the way the editor runs it, builds and serves the site
  where `atompipe` is NOT on PATH (the fallback through PYTHONPATH), which is how a
  checkout is used before anything is installed.

Every child goes through `_env` (test_meta.NoSubprocessOutsideRun); the ready line is
read in process, with the real server bound and only `serve_forever` stopped.
"""
from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import _env
from atompipe import cli, site

#: PATH with no atompipe on it, so the fallback is what gets exercised.
BARE_PATH = os.pathsep.join(p for p in ("/usr/local/bin", "/usr/bin", "/bin") if os.path.isdir(p))


def _cli(cwd: str, *args: str):
    return _env.atompipe(args, cwd=cwd, env={"PATH": BARE_PATH})


class LaunchEntryIsWritten(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="launch-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _launch(self) -> dict:
        path = os.path.join(self.dir, site.LAUNCH_PATH)
        self.assertTrue(os.path.isfile(path), "no .vscode/launch.json")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_init_writes_one_that_builds_and_serves_the_site(self):
        r = _cli(self.dir, "init", "--name", "rig")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(".vscode/launch.json", r.stdout)
        doc = self._launch()
        names = [c["name"] for c in doc["configurations"]]
        self.assertEqual(names, ["rig site — Chrome", "rig site — Edge"])
        for c in doc["configurations"]:
            self.assertIn("site build;", c["command"])
            self.assertIn("site serve -p 0 --no-browser", c["command"])
            self.assertEqual(c["serverReadyAction"]["pattern"], site.SERVE_READY_RE)
            self.assertTrue(os.path.isdir(c["env"]["PYTHONPATH"]), c["env"])

    def test_site_build_gives_an_older_groundspace_one(self):
        self.assertEqual(_cli(self.dir, "init").returncode, 0)
        os.remove(os.path.join(self.dir, site.LAUNCH_PATH))      # as built before this existed
        self.assertEqual(_cli(self.dir, "site", "init").returncode, 0)
        self._launch()
        os.remove(os.path.join(self.dir, site.LAUNCH_PATH))
        r = _cli(self.dir, "site", "build")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wrote .vscode/launch.json", r.stdout)
        self._launch()

    def test_an_existing_one_is_never_touched(self):
        # V: the person's own file, with comments json cannot read, must survive every
        # command that writes one when absent.
        mine = '{\n  // mine\n  "version": "0.2.0", "configurations": []\n}\n'
        os.makedirs(os.path.join(self.dir, ".vscode"))
        path = os.path.join(self.dir, site.LAUNCH_PATH)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(mine)
        for args in (("init",), ("site", "init"), ("site", "build")):
            r = _cli(self.dir, *args)
            self.assertEqual(r.returncode, 0, (args, r.stderr))
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), mine, args)
            self.assertNotIn("wrote .vscode/launch.json", r.stdout, args)


class TheEditorSeesTheServerReady(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="launch-ready-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        for args in (("init", "--name", "ready"), ("site", "init"), ("site", "build")):
            r = _cli(self.dir, *args)
            self.assertEqual(r.returncode, 0, (args, r.stderr))

    def test_the_pattern_refuses_what_is_not_the_ready_line(self):
        # The control for the test below: a pattern that matched anything would pass it.
        for line in ("serving site", "listening on http://127.0.0.1:8000/", "scaffolded site/"):
            self.assertIsNone(re.search(site.SERVE_READY_RE, line), line)

    def test_the_pattern_matches_what_the_real_server_prints(self):
        # The real command binds a real port and prints its line; only the loop that
        # would run forever is stopped, as Ctrl-C stops it.
        out = io.StringIO()
        with mock.patch.object(http.server.ThreadingHTTPServer, "serve_forever",
                               side_effect=KeyboardInterrupt), \
                contextlib.redirect_stdout(out):
            rc = cli.main(["site", "serve", "-C", self.dir, "-p", "0", "--no-browser"])
        self.assertEqual(rc, 0, out.getvalue())
        first = out.getvalue().splitlines()[0]
        m = re.search(site.SERVE_READY_RE, first)
        self.assertIsNotNone(m, first)
        self.assertRegex(m.group(1), r"^http://127\.0\.0\.1:[1-9]\d*/$")

    def test_the_configured_command_works_without_atompipe_on_path(self):
        with open(os.path.join(self.dir, site.LAUNCH_PATH), encoding="utf-8") as fh:
            cfg = json.load(fh)["configurations"][0]
        self.assertIsNone(shutil.which("atompipe", path=BARE_PATH))
        # The editor runs `command` in a shell with the entry's env, waits for the
        # ready line, then opens its URL. The same, in one shell: run it in the
        # background, wait for the line, fetch the page, stop it. SIGTERM, not
        # SIGINT: what slipped through in the first draft — a non-interactive shell
        # starts a background job with SIGINT ignored, so the server never saw the
        # Ctrl-C and the test hung with a live server behind it. The trap stops the
        # group on any exit.
        fetch = ("import sys, urllib.request; "
                 "print('HTTP', urllib.request.urlopen(sys.argv[1], timeout=10).status)")
        script = f"""
set -u
out=$(mktemp)
setsid bash -c {shlex.quote(cfg["command"])} > "$out" 2>&1 &
pid=$!
trap 'kill -TERM -- -$pid 2>/dev/null' EXIT
for i in $(seq 1 600); do
  url=$(grep -oE {shlex.quote(cfg["serverReadyAction"]["pattern"])} "$out" | grep -oE 'https?://[^ ]+' | head -1)
  [ -n "$url" ] && break
  sleep 0.2
done
echo "READY ${{url:-none}}"
[ -n "$url" ] && {shlex.quote(sys.executable)} -c {shlex.quote(fetch)} "$url"
kill -TERM -- -$pid 2>/dev/null; wait $pid 2>/dev/null
cat "$out"; rm -f "$out"
"""
        env = {"PATH": BARE_PATH, **cfg["env"]}
        r = _env.run(["bash", "-c", script], cwd=self.dir, env=env, timeout=240)
        self.assertRegex(r.stdout, r"READY https?://127\.0\.0\.1:\d+/", r.stdout + r.stderr)
        self.assertIn("HTTP 200", r.stdout, r.stdout + r.stderr)


class TheReferenceGroundspaceHasOne(unittest.TestCase):
    def test_the_bracket_ships_the_entry_the_helper_writes(self):
        # Committed (the repo ignores .vscode/ except here), so it may not hold a
        # machine's path: its module path is the checkout's src/, relative.
        path = os.path.join(_env.REPO, "examples", "bracket", site.LAUNCH_PATH)
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
        rel_src = "${workspaceFolder}/../../src"
        self.assertEqual(got, site.launch_config("wall-bracket", src_root=rel_src))
        workspace = os.path.dirname(os.path.dirname(path))
        resolved = os.path.normpath(rel_src.replace("${workspaceFolder}", workspace))
        self.assertEqual(resolved, os.path.normpath(os.path.join(_env.REPO, "src")))


if __name__ == "__main__":
    unittest.main()
