# SPDX-License-Identifier: Apache-2.0
"""Eddy's server (milestone 1): git over HTTP through the hook, tokens, and the
data the screens read.

``server/eddy.py`` is run here in process, on a loopback port the OS picks, and
real ``git push``, ``git clone`` and HTTP requests go to it. What each class
holds:

* `ThePushReachesTheHook` — a push over HTTP is judged by ``server/pre-receive``
  exactly as a local push is: a ref outside the layout is refused in ``remote:``
  lines and nothing lands; a candidate lands, is cloned back, and leaves one
  event row; a thing is created with section 10's configuration and both shims.
* `OnlyATokenGetsIn` — no token reads nothing and pushes nothing; the login a
  push is recorded under is the token's, never the name typed beside it;
  ``--public-read`` opens reads and never a push; a token is kept as a digest.
* `TheScreensReadTheCommit` — the family tree, marks, boxes and files come
  from the candidate's own commit; a tree's HTML is served as text everywhere
  but inside a box, where it is sandboxed; a box or a marks file out of shape
  is named, never guessed at; no route walks out of a tree.
* `ShapesAreClosed` — pure: thing names, box manifests, content types.

What these were written against: a server that set ``REMOTE_USER`` from the
basic-auth user name would have recorded any name a pusher typed; one that
served a tree's ``.html`` with its own type would run a pushed page in Eddy's
origin; one that created repositories without the shims would host a line no
hook judged.

Every child process goes through ``_env`` (`test_meta.NoSubprocessOutsideRun`);
the server's own ``git http-backend`` is started by the server, in the clean
environment it builds. Not an invariant class: ``skipTest`` where git is absent
is allowed, as in `test_receive`.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_eddy_server.py'
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import stat
import threading
import unittest
import urllib.error
import urllib.request
from typing import Any

import _env
import _projects

SERVER = os.path.join(_env.REPO, "server", "eddy.py")
NO_PINS = '{"schema": 1, "packs": {}}\n'
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
TRAILERS = ("Nope-Schema: 1\nNope-Kind: candidate\nNope-Name: {name}\n"
            "Nope-Agent: designer\nNope-Model: test-model\n")


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("eddy_server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


eddy = _load()


class EddyCase(_env.EnvCase):
    """A server on a loopback port over a fresh data directory, one thing
    (``owl``) and one token (login ``maker``)."""

    public_read = False

    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not on PATH")
        self.root = os.path.join(self.tmp(), "eddy")
        self.token = eddy.add_token(self.root, "maker")
        self.bare = eddy.create_thing(self.root, "owl")
        self.app = eddy.Eddy(self.root, public_read=self.public_read, quiet=True)
        self.httpd = eddy.make_server(self.app, "127.0.0.1", 0)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    # -- helpers ------------------------------------------------------------ #
    def get(self, path: str, *, token: str | None = "", headers: dict | None = None) -> tuple:
        """``(status, headers, body)``; ``token=""`` sends this case's token,
        ``None`` sends none."""
        request = urllib.request.Request(self.url + path, headers=headers or {})
        sent = self.token if token == "" else token
        if sent:
            request.add_header("Authorization", f"Bearer {sent}")
        try:
            with urllib.request.urlopen(request, timeout=30) as reply:
                return reply.status, reply.headers, reply.read()
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read()

    def json(self, path: str, **kw: Any) -> Any:
        status, _headers, body = self.get(path, **kw)
        self.assertEqual(status, 200, body[:300])
        return json.loads(body)

    def git(self, args: list, cwd: str, *, token: str | None = "", **kw: Any) -> Any:
        sent = self.token if token == "" else token
        auth = ["-c", f"http.extraHeader=Authorization: Bearer {sent}"] if sent else []
        return _env.git([*auth, *args], cwd=cwd, env={"GIT_TERMINAL_PROMPT": "0"}, **kw)

    def scratch(self) -> str:
        """A repository with one plain commit: enough for a draft."""
        work = os.path.join(self.tmp(), "scratch")
        os.makedirs(work)
        self.assertEqual(_env.git(["init", "-q", "-b", "main", "."], cwd=work).returncode, 0)
        with open(os.path.join(work, "notes.txt"), "w", encoding="utf-8") as fh:
            fh.write("a first note\n")
        _env.git(["add", "-A"], cwd=work)
        done = _env.git(["commit", "-q", "-m", "a note"], cwd=work, identity=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return work

    def candidate(self, name: str = "v0.1", files: dict | None = None) -> str:
        """The bracket as a clone holds it, with ``files`` added and the
        candidate trailers on its one commit."""
        work = _projects.bracket_copy(os.path.join(self.tmp(), "work"), git=True, migrated=True)
        launch = os.path.join(work, ".vscode", "launch.json")
        if os.path.exists(launch):
            _env.git(["rm", "-q", "--cached", "--ignore-unmatch", "--", ".vscode/launch.json"],
                     cwd=work)
            os.remove(launch)
        for rel, content in {"spec/packs.json": NO_PINS, **(files or {})}.items():
            full = os.path.join(work, *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as fh:
                fh.write(content if isinstance(content, bytes) else content.encode("utf-8"))
            _env.git(["add", "-f", "--", rel], cwd=work)
        _env.git(["add", "-A"], cwd=work)
        done = _env.git(["commit", "-q", "--amend", "-m", f"the bracket, as {name}", "-m",
                         TRAILERS.format(name=name)], cwd=work, identity=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return work

    def head(self, work: str) -> str:
        return _env.git(["rev-parse", "HEAD"], cwd=work).stdout.strip()

    def refs(self) -> dict:
        out = _env.git(["for-each-ref", "--format=%(refname) %(objectname)"], cwd=self.bare).stdout
        return dict(line.split(" ", 1) for line in out.splitlines())

    def events(self) -> list:
        return eddy.read_events(self.root, "owl")


class ThePushReachesTheHook(EddyCase):
    def test_a_stray_ref_is_refused_in_remote_lines_and_a_draft_lands(self):
        work = self.scratch()
        refused = self.git(["push", f"{self.url}/owl.git", "HEAD:refs/heads/feature"], work)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("remote: E01 refs/heads/feature: not a ref Eddy keeps", refused.stderr)
        self.assertIn("(pre-receive hook declined)", refused.stderr)
        self.assertEqual(self.refs(), {})
        self.assertEqual(self.events(), [], "a refused push leaves no event")
        landed = self.git(["push", f"{self.url}/owl.git", "HEAD:refs/heads/draft/first"], work)
        self.assertEqual(landed.returncode, 0, landed.stderr)
        self.assertEqual(self.refs(), {"refs/heads/draft/first": self.head(work)})

    def test_a_working_commit_on_a_line_is_refused_by_the_hooks_own_rule(self):
        work = self.scratch()
        refused = self.git(["push", f"{self.url}/owl.git", "HEAD:refs/heads/cand/a"], work)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("remote: E24 cand/a", refused.stderr)
        self.assertEqual(self.refs(), {})

    def test_a_candidate_lands_is_cloned_back_and_leaves_one_event(self):
        work = self.candidate()
        pushed = self.git(["push", f"{self.url}/owl.git", "HEAD:refs/heads/cand/bracket"], work)
        self.assertEqual(pushed.returncode, 0, pushed.stderr)
        self.assertIn("remote: N01", pushed.stderr)
        self.assertIn(f"remote: eddy: cand/bracket landed at {self.head(work)[:12]}", pushed.stderr)
        self.assertEqual(self.refs(), {"refs/heads/cand/bracket": self.head(work)})
        clone = os.path.join(self.tmp(), "clone")
        got = self.git(["clone", "-q", "-b", "cand/bracket", f"{self.url}/owl.git", clone],
                       self.tmp())
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(self.head(clone), self.head(work))
        rows = self.events()
        self.assertEqual([(r["ref"], r["new"], r["by"]) for r in rows],
                         [("refs/heads/cand/bracket", self.head(work), "maker")])

    def test_a_thing_is_created_under_the_hook(self):
        config = _env.git(["config", "--list", "--local"], cwd=self.bare).stdout
        for line in ("receive.fsckobjects=true", "transfer.fsckobjects=true",
                     "http.receivepack=true"):
            self.assertIn(line, config)
        self.assertNotIn("denydeletes", config.lower())
        self.assertNotIn("denynonfastforwards", config.lower())
        hooks = os.path.join(self.bare, "hooks")
        self.assertEqual(sorted(os.listdir(hooks)), ["post-receive", "pre-receive"])
        for name in ("pre-receive", "post-receive"):
            path = os.path.join(hooks, name)
            self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR, f"{name} is not executable")
            with open(path, encoding="utf-8") as fh:
                self.assertIn(os.path.join(_env.REPO, "server", name), fh.read())
        self.assertEqual(_env.git(["symbolic-ref", "HEAD"], cwd=self.bare).stdout.strip(),
                         "refs/heads/main")
        with self.assertRaises(eddy.EddyError):
            eddy.create_thing(self.root, "owl")

    def test_dumb_http_is_refused(self):
        status, _headers, body = self.get("/owl.git/info/refs")
        self.assertEqual(status, 403)
        self.assertIn(b"smart HTTP only", body)
        self.assertEqual(self.get("/owl.git/HEAD")[0], 404)


class OnlyATokenGetsIn(EddyCase):
    def test_no_token_reads_nothing_and_pushes_nothing(self):
        for path in ("/", "/api/things", "/r/owl/family", "/owl.git/info/refs?service=git-upload-pack"):
            for token in (None, "eddy_not-a-token"):
                status, headers, _body = self.get(path, token=token)
                self.assertEqual(status, 401, f"{path} with {token!r}")
                self.assertIn("Basic", headers.get("WWW-Authenticate", ""))
        work = self.scratch()
        pushed = self.git(["push", f"{self.url}/owl.git", "HEAD:refs/heads/draft/x"], work,
                          token=None)
        self.assertNotEqual(pushed.returncode, 0)
        self.assertEqual(self.refs(), {})
        self.assertEqual(self.get("/healthz", token=None)[0], 200)
        self.assertEqual(self.get("/agent.md", token=None)[0], 200)

    def test_the_pusher_is_the_tokens_login_not_the_name_typed(self):
        work = self.scratch()
        url = self.url.replace("http://", f"http://mallory:{self.token}@")
        pushed = _env.git(["push", f"{url}/owl.git", "HEAD:refs/heads/draft/x"], cwd=work,
                          env={"GIT_TERMINAL_PROMPT": "0"})
        self.assertEqual(pushed.returncode, 0, pushed.stderr)
        self.assertEqual([r["by"] for r in self.events()], ["maker"])

    def test_a_token_is_kept_as_a_digest_and_revoked_by_its_id(self):
        with open(os.path.join(self.root, "tokens.json"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn(self.token, text)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.root, "tokens.json")).st_mode), 0o600)
        self.assertEqual(eddy.login_for(self.root, self.token), "maker")
        entry = eddy.read_tokens(self.root)[0]
        self.assertEqual(eddy.revoke_token(self.root, entry["id"]), 1)
        self.assertIsNone(eddy.login_for(self.root, self.token))
        self.assertEqual(self.get("/api/things")[0], 401)
        for login in ("", "two words", "a/b", "x" * 65):
            with self.assertRaises(eddy.EddyError):
                eddy.add_token(self.root, login)

    def test_a_thing_is_made_over_http_only_with_a_token(self):
        def post(token: str | None) -> int:
            request = urllib.request.Request(self.url + "/api/things", method="POST",
                                             data=b'{"name": "kite"}')
            if token:
                request.add_header("Authorization", f"Bearer {token}")
            try:
                with urllib.request.urlopen(request, timeout=30) as reply:
                    return reply.status
            except urllib.error.HTTPError as err:
                return err.code
        self.assertEqual(post(None), 401)
        self.assertEqual(eddy.things(self.root), ["owl"])
        self.assertEqual(post(self.token), 201)
        self.assertEqual(eddy.things(self.root), ["kite", "owl"])
        self.assertEqual(post(self.token), 400, "a thing that exists is not made twice")


class PublicReadNeverPushes(EddyCase):
    public_read = True

    def test_reads_open_and_a_push_still_needs_a_token(self):
        self.assertEqual(self.get("/api/things", token=None)[0], 200)
        self.assertEqual(self.get("/r/owl/family", token=None)[0], 200)
        work = self.scratch()
        pushed = self.git(["push", f"{self.url}/owl.git", "HEAD:refs/heads/draft/x"], work,
                          token=None)
        self.assertNotEqual(pushed.returncode, 0)
        self.assertEqual(self.refs(), {})
        status, _h, _b = self.get("/api/things", token=None, headers={"X-Method": "x"})
        self.assertEqual(status, 200)


class TheScreensReadTheCommit(EddyCase):
    BOX = {"schema": 1, "kind": "custom", "title": "Sag", "caption": "sag against thickness",
           "src": "index.html", "thumbnail": "thumb.png", "view": "both",
           "section": "implications"}

    def land(self, files: dict | None = None) -> str:
        files = {"eddy/marks.json": '{"schema": 1, "marks": {"v0.1": "selected"}}\n',
                 "site/thumb.png": PNG,
                 "site/boxes/sag/box.json": json.dumps(self.BOX),
                 "site/boxes/sag/index.html": "<!doctype html><script>parent.postMessage({eddy:'ready'},'*')</script>",
                 "site/boxes/sag/thumb.png": PNG, **(files or {})}
        work = self.candidate(files=files)
        pushed = self.git(["push", f"{self.url}/owl.git", "HEAD:refs/heads/cand/bracket",
                           "HEAD:refs/heads/main"], work)
        self.assertEqual(pushed.returncode, 0, pushed.stderr)
        return self.head(work)

    def test_the_family_tree_is_the_lines_candidates_with_their_marks(self):
        sha = self.land()
        fam = self.json("/r/owl/family")
        self.assertEqual([(c["name"], c["sha"], c["head"], c["line"], c["mark"], c["thumb"],
                           c["parent"], c["work"]) for c in fam["candidates"]],
                         [("v0.1", sha, sha, "bracket", "selected", "site/thumb.png", "", 0)])
        self.assertEqual(fam["problems"], [])
        self.assertFalse(fam["candidates"][0]["site"], "the bracket commits no site/data")

    def test_a_marks_file_out_of_shape_is_named_and_marks_nothing(self):
        self.land({"eddy/marks.json": '{"schema": 1, "marks": {"v0.1": "winner"}}\n'})
        fam = self.json("/r/owl/family")
        self.assertEqual(fam["candidates"][0]["mark"], "")
        self.assertEqual(len(fam["problems"]), 1)
        self.assertIn("'winner'", fam["problems"][0])

    def test_a_trees_html_is_text_everywhere_but_inside_a_sandboxed_box(self):
        sha = self.land()
        status, headers, body = self.get(f"/r/owl/raw/{sha}/site/boxes/sag/index.html")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertIn(b"postMessage", body)
        status, headers, _body = self.get(f"/r/owl/box/{sha}/sag/index.html")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        policy = headers["Content-Security-Policy"]
        self.assertTrue(policy.startswith("sandbox allow-scripts;"), policy)
        self.assertNotIn("allow-same-origin", policy)
        self.assertIn("default-src 'none'", policy)
        blob = self.json(f"/r/owl/blob/{sha}/site/boxes/sag/index.html")
        self.assertEqual((blob["kind"], blob["markup"]), ("text", True))

    def test_a_box_is_read_strictly_and_one_out_of_shape_is_named_not_shown(self):
        bad = dict(self.BOX, thumbnail="missing.png")
        sha = self.land({"site/boxes/bad/box.json": json.dumps(bad),
                         "site/boxes/bad/index.html": "<p>x</p>"})
        boxes = self.json(f"/r/owl/boxes/{sha}")
        self.assertEqual([b["id"] for b in boxes["boxes"]], ["sag"])
        self.assertEqual(boxes["boxes"][0]["src"], f"/r/owl/box/{sha}/sag/index.html")
        self.assertEqual(boxes["boxes"][0]["thumbnail"], f"/r/owl/raw/{sha}/site/boxes/sag/thumb.png")
        self.assertEqual(len(boxes["problems"]), 1)
        self.assertIn("site/boxes/bad/box.json", boxes["problems"][0])
        self.assertIn("missing.png", boxes["problems"][0])

    def test_no_route_walks_out_of_the_tree_or_the_box(self):
        sha = self.land()
        for path in (f"/r/owl/raw/{sha}/../../tokens.json", f"/r/owl/raw/{sha}/claims/../../x",
                     f"/r/owl/box/{sha}/sag/..%2F..%2Fthumb.png", f"/r/owl/box/{sha}/sag/box.py",
                     f"/r/owl/box/{sha}/../sag/index.html", f"/r/owl/tree/{'0' * 40}",
                     "/r/nothing/family", "/r/owl/nosuchview", "/static/../eddy.py",
                     "/api/packs/sourcing/file?path=../../README.md"):
            self.assertEqual(self.get(path)[0], 404, path)

    def test_the_files_are_the_commits(self):
        sha = self.land()
        tree = self.json(f"/r/owl/tree/{sha}")
        names = [e["name"] for e in tree["entries"]]
        self.assertIn("claims", names)
        self.assertEqual(tree["commit"]["kind"], "candidate")
        self.assertEqual(tree["commit"]["name"], "v0.1")
        blob = self.json(f"/r/owl/blob/{sha}/claims/C1.json")
        self.assertEqual(blob["kind"], "text")
        self.assertIn("statement", blob["text"])
        commit = self.json(f"/r/owl/commit/{sha}")
        self.assertIn(["Nope-Kind", "candidate"], commit["trailers"])
        self.assertNotIn("validate", commit)
        judged = self.json(f"/r/owl/commit/{sha}?validate=1")["validate"]
        self.assertEqual(judged["refusals"], [])
        self.assertTrue(any(line.startswith("N01 ") for line in judged["notes"]), judged)
        lines = self.json("/r/owl/lines")
        self.assertEqual(lines["lines"][0]["state"], "not yet rendered")

    def test_the_bundled_packs_are_read_without_running_one(self):
        packs = self.json("/api/packs")["packs"]
        self.assertIn("fdm-print", [p["name"] for p in packs])
        pack = self.json("/api/packs/fdm-print")
        gate = next(g for g in pack["gates"] if g["id"] == "fdm.bed_fit")
        self.assertTrue(gate["fixture"] and gate["note"], "a gate with no control was listed")
        text = self.json("/api/packs/fdm-print/file?path=pack.json")["text"]
        self.assertEqual(json.loads(text)["name"], "fdm-print")


class ShapesAreClosed(unittest.TestCase):
    def test_a_things_name(self):
        for good in ("owl", "petgasus-2", "a"):
            self.assertTrue(eddy.THING_NAME.match(good), good)
        for bad in ("", "Owl", "owl.git", "-owl", "a b", "../x", "x" * 65, "owl/x"):
            self.assertFalse(eddy.THING_NAME.match(bad), bad)

    def test_a_box_manifest(self):
        files = {"site/boxes/a/pic.png": 1, "site/boxes/a/index.html": 1, "site/boxes/a/t.png": 1}
        good = {"schema": 1, "kind": "image", "src": "pic.png", "thumbnail": "t.png",
                "section": "claims"}

        def why(**change: Any) -> Any:
            doc = {k: v for k, v in {**good, **change}.items() if v is not None}
            return eddy._box_problem("a", json.dumps(doc).encode(), "site/boxes/a/", files)
        self.assertIsInstance(why(), dict)
        self.assertIsInstance(why(kind="custom", src="index.html"), dict)
        for change in ({"schema": 2}, {"kind": "applet"}, {"section": "hero"}, {"view": "all"},
                       {"src": "../x.png"}, {"src": "gone.png"}, {"thumbnail": None},
                       {"thumbnail": "index.html"}, {"kind": "custom"}, {"aspect": "wide"},
                       {"colour": "red"}, {"title": "x" * 301}):
            self.assertIsInstance(why(**change), str, change)
        self.assertIsInstance(eddy._box_problem("Bad Id", b"{}", "site/boxes/x/", files), str)
        self.assertIsInstance(eddy._box_problem("a", b"not json", "site/boxes/a/", files), str)

    def test_markup_is_never_served_as_markup_by_the_raw_route(self):
        for name in ("page.html", "PAGE.HTM", "drawing.svg", "feed.xml"):
            self.assertEqual(eddy._content_type(name, b"<script>alert(1)</script>"),
                             "text/plain; charset=utf-8", name)
        self.assertEqual(eddy._content_type("part.stl", b"\0solid"), "model/stl")
        self.assertEqual(eddy._content_type("blob.bin", b"\0\1\2"), "application/octet-stream")


if __name__ == "__main__":
    unittest.main()
