"""Tests for the local dashboard (agent/dashboard.py): run listing and detail, the token and host checks, the keys file,
deleting runs, weekly schedules (agent/schedule.py, against a temp folder and a fake launchctl / crontab) and the Excel
and Word downloads. Standard library only: python3 -m unittest discover -s tests"""
import json
import os
import plistlib
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

os.environ["OUTBOUND_TODAY"] = "2026-10-18"

from unittest import mock  # noqa: E402

from agent import common, dashboard, pipeline, projects, schedule  # noqa: E402
from test_pipeline import FakeApollo, FakeClaude, FakeLemlist  # noqa: E402


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        runs, companies = os.path.join(cls.tmp.name, "runs"), os.path.join(cls.tmp.name, "companies")
        run, _, _ = pipeline.new_run("Acme", "acme.dev", runs=runs, companies=companies)
        pipeline.Pipeline(run, FakeClaude(), FakeApollo(), FakeLemlist(), companies=companies, log=lambda *a: None).all()
        cls.runs = dashboard.Runs(runs_dir=runs, py="python3")
        cls.calls = []
        cls.sched = schedule.Scheduler(runs, py="python3", system="launchd", agents_dir=os.path.join(cls.tmp.name, "agents"),
                                       run=lambda cmd, **kw: cls.calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.make_handler(cls.runs, "tok", "1.15.0", cls.sched))
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
        self.assertEqual(d["files"][0], "accounts.xlsx")

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"X-Token": "tok", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_word_and_excel_downloads(self):
        folder = self.runs.path("acme/2026-10-18")
        d = self.runs.detail("acme/2026-10-18")
        self.assertEqual(d["files"][:4], ["accounts.xlsx", "leads.csv", "emails.docx", "report.docx"])
        os.remove(os.path.join(folder, "report.docx"))     # a run from before Word downloads
        self.assertIn("report.docx", self.runs.detail("acme/2026-10-18")["files"])
        with urllib.request.urlopen(self.base + "/api/file?id=acme/2026-10-18&name=report.docx&t=tok") as r:
            self.assertEqual(r.headers["Content-Type"], dashboard.TYPES[".docx"])
            self.assertTrue(r.read().startswith(b"PK"))
        self.assertTrue(os.path.exists(os.path.join(folder, "report.docx")))   # built on the way

    def test_delete_run(self):
        src = self.runs.path("acme/2026-10-18")
        shutil.copytree(src, self.runs.path("acme/2026-10-11"))
        self.assertEqual(len(self.runs.list()), 2)
        with mock.patch.object(dashboard, "pid_alive", return_value=True):     # still going: not deleted
            code, body = self.post("/api/delete", {"id": "acme/2026-10-11"})
        self.assertEqual(code, 400)
        self.assertIn("still going", body["error"])
        code, body = self.post("/api/delete", {"id": "acme/2026-10-11"})
        self.assertEqual(code, 200)
        self.assertEqual([r["id"] for r in body["runs"]], ["acme/2026-10-18"])
        self.assertFalse(os.path.exists(self.runs.path("acme/2026-10-11")))
        self.assertEqual(self.post("/api/delete", {"id": "acme/2026-10-11"})[0], 400)
        self.assertEqual(self.post("/api/delete", {"id": "../x/2026-10-18"})[0], 400)

    def test_schedule_endpoints(self):
        code, body = self.post("/api/schedule", {"company": "Acme", "domain": "acme.dev", "day": "Monday", "time": "8:30"})
        self.assertEqual(code, 200)
        self.assertEqual(body["schedules"]["acme"], {"company": "Acme", "domain": "acme.dev", "day": "Monday", "time": "08:30"})
        code, state = self.get("/api/state", {"X-Token": "tok"})
        self.assertTrue(json.loads(state)["can_schedule"])
        self.assertEqual(self.post("/api/schedule", {"company": "Acme", "day": "Someday"})[0], 400)
        code, body = self.post("/api/schedule", {"company": "Acme", "off": True})
        self.assertEqual(body["schedules"], {})

    def test_project_endpoints(self):
        code, body = self.post("/api/projects", {"action": "create", "name": "Fintech", "run": "acme/2026-10-18"})
        self.assertEqual(code, 200)
        pid = body["projects"][0]["id"]
        self.assertEqual(body["runs"][0]["project"], pid)
        self.assertEqual(self.post("/api/projects", {"action": "create", "name": "fintech"})[0], 400)   # taken
        self.assertEqual(self.post("/api/projects", {"action": "move", "run": "../x/2026-10-18", "id": pid})[0], 400)
        code, body = self.post("/api/projects", {"action": "move", "run": "acme/2026-10-18", "id": ""})
        self.assertIsNone(body["runs"][0]["project"])
        code, body = self.post("/api/projects", {"action": "delete", "id": pid})
        self.assertEqual(body["projects"], [])
        code, state = self.get("/api/state", {"X-Token": "tok"})
        self.assertEqual(json.loads(state)["projects"], [])

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
        with urllib.request.urlopen(self.base + "/api/file?id=acme/2026-10-18&name=accounts.xlsx&t=tok") as r:
            self.assertEqual(r.headers["Content-Type"], dashboard.TYPES[".xlsx"])
            self.assertTrue(r.read().startswith(b"PK"))   # a zip, like every .xlsx

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
                with self.assertRaises(ValueError):    # an API key pasted into the function ID box
                    dashboard.save_keys({"clay_routine": "clay_abc"}, path)
                dashboard.save_keys({"clay_routine": "function:t_1"}, path)
                self.assertIn("CLAY_ROUTINE_ID=function:t_1", common.read_text(path))
                dashboard.save_keys({"clear_clay_routine": "1"}, path)
                self.assertNotIn("CLAY_ROUTINE_ID", common.read_text(path))
                self.assertNotIn("CLAY_ROUTINE_ID", os.environ)
                self.assertIn("APOLLO_API_KEY=new", common.read_text(path))
            finally:
                os.environ.pop("CLAY_ROUTINE_ID", None)
                if old is None:
                    os.environ.pop("APOLLO_API_KEY", None)
                else:
                    os.environ["APOLLO_API_KEY"] = old


class ProjectTests(unittest.TestCase):
    def test_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            pr = projects.Projects(tmp)
            a = pr.create("  Fintech   apps ")
            self.assertEqual(a["name"], "Fintech apps")
            b = pr.create("Dev tools")
            with self.assertRaises(ValueError):
                pr.create("")
            with self.assertRaises(ValueError):
                pr.rename(b["id"], "FINTECH APPS")
            pr.rename(b["id"], "Developer tools")
            pr.move("acme/2026-10-12", a["id"])
            pr.move("zeta/2026-10-12", b["id"])
            with self.assertRaises(ValueError):
                pr.move("acme/2026-10-12", "nope")
            where = pr.of(["acme/2026-10-12", "acme/2026-10-19", "zeta/2026-10-12", "new/2026-10-19"])
            self.assertEqual(where, {"acme/2026-10-12": a["id"], "acme/2026-10-19": a["id"],   # next week follows
                                     "zeta/2026-10-12": b["id"], "new/2026-10-19": None})
            pr.move("acme/2026-10-19", "")            # taken out on purpose: it stays out
            self.assertIsNone(pr.of(["acme/2026-10-19"])["acme/2026-10-19"])
            pr.delete(a["id"])
            self.assertEqual([p["name"] for p in pr.all()], ["Developer tools"])
            self.assertIsNone(pr.of(["acme/2026-10-12"])["acme/2026-10-12"])   # its runs stay, in none
            pr.forget("zeta/2026-10-12")
            self.assertIsNone(pr.of(["zeta/2026-10-12"])["zeta/2026-10-12"])


class ScheduleTests(unittest.TestCase):
    def fake(self, crontab=None):
        calls, tab = [], crontab if crontab is not None else []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[:2] == ["crontab", "-l"]:
                return subprocess.CompletedProcess(cmd, 0 if tab else 1, "\n".join(tab), "")
            if cmd[:2] == ["crontab", "-"]:
                tab[:] = [ln for ln in kw["input"].splitlines() if ln]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return calls, tab, run

    def test_launchd(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls, _, run = self.fake()
            s = schedule.Scheduler(os.path.join(tmp, "runs"), py="/venv/python", system="launchd",
                                   agents_dir=os.path.join(tmp, "agents"), run=run)
            info = s.set("Tiger Data", "tigerdata.com", "Monday", "09:00")
            self.assertEqual(info["time"], "09:00")
            path = os.path.join(tmp, "agents", "dev.outbound-agent.tigerdata.plist")
            with open(path, "rb") as f:
                job = plistlib.load(f)
            self.assertEqual(job["ProgramArguments"], ["/venv/python", "-m", "agent", "scheduled", "Tiger Data",
                                                       "--domain", "tigerdata.com"])
            self.assertEqual(job["StartCalendarInterval"], {"Weekday": 1, "Hour": 9, "Minute": 0})   # launchd: 1 is Monday
            self.assertEqual(job["WorkingDirectory"], common.ROOT)
            self.assertFalse(job["RunAtLoad"])                     # saving a schedule starts nothing
            self.assertEqual(calls[-1][:2], ["launchctl", "bootstrap"])
            self.assertEqual(list(s.all()), ["tigerdata"])
            s.set("Tiger Data", None, "Sunday", "18:45")
            with open(path, "rb") as f:
                self.assertEqual(plistlib.load(f)["StartCalendarInterval"], {"Weekday": 7, "Hour": 18, "Minute": 45})
            s.remove("tigerdata")
            self.assertFalse(os.path.exists(path))
            self.assertEqual(s.all(), {})
            with self.assertRaises(ValueError):
                s.set("Acme", None, "Monday", "25:00")

    def test_cron(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, tab, run = self.fake(["0 7 * * * backup.sh"])
            s = schedule.Scheduler(os.path.join(tmp, "runs"), py="/venv/python", system="cron", run=run)
            s.set("Acme 100%", "acme.dev", "Monday", "09:30")
            self.assertEqual(tab[0], "0 7 * * * backup.sh")       # the other jobs stay
            self.assertTrue(tab[1].startswith("30 9 * * 1 cd "))   # cron: 1 is Monday
            self.assertIn("scheduled 'Acme 100\\%'", tab[1])     # cron reads a bare % as a new line
            self.assertTrue(tab[1].endswith("# outbound-agent:acme100"))
            self.assertEqual(list(s.all()), ["acme100"])
            s.remove("acme100")
            self.assertEqual(tab, ["0 7 * * * backup.sh"])

    def test_unsupported(self):
        s = schedule.Scheduler("/nowhere", system=None)
        s.system = None
        self.assertFalse(s.supported())
        with self.assertRaises(ValueError):
            s.set("Acme")


if __name__ == "__main__":
    unittest.main()
