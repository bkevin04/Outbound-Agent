"""Tests for the local dashboard (agent/dashboard.py): run listing and detail, the token and host checks, the keys file.
Standard library only: python3 -m unittest discover -s tests"""
import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

os.environ["OUTBOUND_TODAY"] = "2026-10-18"

from agent import common, dashboard, pipeline  # noqa: E402
from test_pipeline import FakeApollo, FakeClaude, FakeLemlist  # noqa: E402


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        runs, companies = os.path.join(cls.tmp.name, "runs"), os.path.join(cls.tmp.name, "companies")
        run, _, _ = pipeline.new_run("Acme", "acme.dev", runs=runs, companies=companies)
        pipeline.Pipeline(run, FakeClaude(), FakeApollo(), FakeLemlist(), companies=companies, log=lambda *a: None).all()
        cls.runs = dashboard.Runs(runs_dir=runs, py="python3")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.make_handler(cls.runs, "tok", "1.15.0"))
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_list_and_detail(self):
        [row] = self.runs.list()
        self.assertEqual((row["id"], row["state"], row["leads"]), ("acme/2026-10-18", "done", 2))
        d = self.runs.detail("acme/2026-10-18")
        self.assertTrue(all(s["state"] == "done" for s in d["stages"]))
        self.assertEqual(d["funnel"][0]["n"], 9)
        self.assertEqual(len(d["leads"][0]["emails"]), 3)
        self.assertEqual(d["lemlist"]["status"], "draft")
        self.assertIn("leads.csv", d["files"])

    def test_bad_run_ids(self):
        for rid in ("../etc/2026-10-18", "acme", "acme/2026-10-18/x", "acme/../../x"):
            with self.assertRaises(ValueError):
                self.runs.path(rid)

    def test_token_and_host(self):
        self.assertEqual(self.get("/")[0], 200)
        self.assertIn('content="tok"', self.get("/")[1])
        self.assertEqual(self.get("/api/state")[0], 403)
        self.assertEqual(self.get("/api/state", {"X-Token": "nope"})[0], 403)
        code, body = self.get("/api/state", {"X-Token": "tok"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["runs"][0]["company"], "Acme")
        self.assertEqual(self.get("/", {"Host": "evil.example"})[0], 403)
        self.assertEqual(self.get("/api/file?id=acme/2026-10-18&name=run.json&t=tok")[0], 400)
        code, body = self.get("/api/file?id=acme/2026-10-18&name=leads.csv&t=tok")
        self.assertEqual(code, 200)
        self.assertIn("ana@alpha.dev", body)

    def test_save_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            common.write_text(path, "OTHER=1\nAPOLLO_API_KEY=old\n")
            old = os.environ.get("APOLLO_API_KEY")
            try:
                dashboard.save_keys({"apollo": "new", "anthropic": ""}, path)
                text = common.read_text(path)
                self.assertIn("OTHER=1", text)
                self.assertIn("APOLLO_API_KEY=new", text)
                self.assertNotIn("old", text)
                self.assertNotIn("ANTHROPIC_API_KEY", text)
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
                with self.assertRaises(ValueError):
                    dashboard.save_keys({"apollo": "a\nB=c"}, path)
            finally:
                if old is None:
                    os.environ.pop("APOLLO_API_KEY", None)
                else:
                    os.environ["APOLLO_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
