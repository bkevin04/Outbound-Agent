"""A local dashboard for the agent: `python3 -m agent dashboard` opens it at http://127.0.0.1:8765.

Start a run for a company, watch the stages and the log as it goes, and read the 10 leads and their emails. Delete a
run, or have a company's run start again every week (agent/schedule.py). A run is the same `python -m agent run` in a
subprocess, its output in run.log and its process id in run.pid in the run folder, so a run started here, by a
schedule or from the terminal through `agent scheduled` is a run like any other. The server listens on 127.0.0.1 only and every API call needs the token handed to the page,
because the run folders hold real people's emails and a run spends Apollo credits."""
import glob
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import common as c
from . import llm
from . import pipeline as pl
from . import report as rep
from . import schedule as sch
from . import sequence as sq

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = "run.log"
KEYS = {"anthropic": "ANTHROPIC_API_KEY", "apollo": "APOLLO_API_KEY", "lemlist": "LEMLIST_API_KEY",
        "lemlist_sender": "LEMLIST_SENDER_EMAIL", "clay": "CLAY_API_KEY", "clay_routine": "CLAY_ROUTINE_ID"}
FILES = (c.ACCOUNTS_XLSX, c.LEADS_CSV, c.EMAILS_DOCX, c.REPORT_DOCX, c.EMAILS, c.REPORT)
TYPES = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ".csv": "text/csv; charset=utf-8", ".md": "text/markdown; charset=utf-8"}
# Built from the run's files when a run made before they existed is downloaded: the file -> what it needs.
DERIVED = {c.ACCOUNTS_XLSX: c.TOP10, c.EMAILS_DOCX: c.EMAILS, c.REPORT_DOCX: c.REPORT}


def pid_alive(path):
    """The process in run.pid is still an agent run (a stale pid that now belongs to something else is not)."""
    try:
        pid = int(c.read_text(path).strip())
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    try:
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return True
    return "agent" in cmd


def available(folder):
    """The downloads a run has: its files, and the ones that can be built from them."""
    return [f for f in FILES if os.path.exists(os.path.join(folder, f))
            or (f in DERIVED and os.path.exists(os.path.join(folder, DERIVED[f])))]


def build(folder, name):
    """Make a derived file that is not there yet (a run from before Excel and Word downloads)."""
    if name == c.ACCOUNTS_XLSX:
        rep.write_workbook(folder)
    elif name in (c.EMAILS_DOCX, c.REPORT_DOCX):
        rep.write_docs(folder)


def python():
    """The project's .venv Python when there is one (it has the anthropic SDK), else this one."""
    venv = os.path.join(c.ROOT, ".venv", "bin", "python")
    return venv if os.path.exists(venv) else sys.executable


def sdk_version(py):
    try:
        out = subprocess.run([py, "-c", "import anthropic; print(anthropic.__version__)"], capture_output=True,
                             text=True, timeout=20)
        return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


class Runs:
    """The runs on disk and the ones this dashboard started."""

    def __init__(self, runs_dir=c.RUNS, py=None):
        self.dir, self.py = runs_dir, py or python()
        self.procs, self.lock = {}, threading.Lock()

    def path(self, rid):
        if not re.fullmatch(r"[a-z0-9-]+/\d{4}-\d{2}-\d{2}", rid or ""):
            raise ValueError(f"bad run id {rid!r}")
        return os.path.join(self.dir, *rid.split("/"))

    def running(self, rid):
        p = self.procs.get(rid)
        if p is not None:
            return p.poll() is None
        return pid_alive(os.path.join(self.path(rid), c.PID))

    def state(self, rid, meta):
        if self.running(rid):
            return "running"
        if meta.get("error"):
            return "failed"
        if "finish" in (meta.get("stages_done") or []) and not meta.get("stage"):
            return "done"
        return "stopped"

    def list(self):
        out = []
        if not os.path.isdir(self.dir):
            return out
        for slug in os.listdir(self.dir):
            for date in os.listdir(os.path.join(self.dir, slug)) if os.path.isdir(os.path.join(self.dir, slug)) else []:
                meta = c.load_optional(os.path.join(self.dir, slug, date, c.RUN))
                if not meta:
                    continue
                rid = f"{slug}/{date}"
                top = c.load_optional(os.path.join(self.dir, slug, date, c.TOP10), [])
                out.append({"id": rid, "company": meta.get("company"), "domain": meta.get("domain"), "date": date,
                            "state": self.state(rid, meta),
                            "stages_done": len(meta.get("stages_done") or []), "leads": len(top)})
        return sorted(out, key=lambda r: (r["date"], r["company"] or ""), reverse=True)

    def start(self, company, domain=None, date=None, note="started from the dashboard"):
        slug = c.slugify(company)
        date = date or c.today().isoformat()
        rid = f"{slug}/{date}"
        with self.lock:
            if self.running(rid):
                return rid
            folder = self.path(rid)
            os.makedirs(folder, exist_ok=True)
            cmd = [self.py, "-u", "-m", "agent", "run", company, "--date", date]
            cmd += ["--domain", domain] if domain else []
            log = open(os.path.join(folder, LOG), "a", encoding="utf-8")
            log.write(f"\n--- {note}: {company} ---\n")
            log.flush()
            self.procs[rid] = subprocess.Popen(cmd, cwd=c.ROOT, stdout=log, stderr=subprocess.STDOUT,
                                               env=os.environ.copy())
            log.close()
            c.write_text(os.path.join(folder, c.PID), str(self.procs[rid].pid))
        return rid

    def delete(self, rid):
        """Remove a run's folder from this machine. Not while it runs. The lemlist campaign and the company's saved
        ICP stay, so a later run still skips the accounts this one pitched."""
        folder, root = os.path.realpath(self.path(rid)), os.path.realpath(self.dir)
        if not folder.startswith(root + os.sep) or not os.path.isdir(folder):
            raise ValueError("no such run")
        if self.running(rid):
            raise ValueError("this run is still going: delete it once it has finished")
        with self.lock:
            shutil.rmtree(folder)
            self.procs.pop(rid, None)
            parent = os.path.dirname(folder)
            if not os.listdir(parent):
                os.rmdir(parent)

    def log(self, rid, offset=0):
        path = os.path.join(self.path(rid), LOG)
        if not os.path.exists(path):
            return {"text": "", "offset": 0}
        with open(path, encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            text = f.read()
            return {"text": text, "offset": f.tell()}

    def activity(self, rid):
        """The last line the run printed: what it is doing right now."""
        path = os.path.join(self.path(rid), LOG)
        if not os.path.exists(path):
            return ""
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - 4096))
            lines = f.read().decode("utf-8", "replace").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if line and not line.startswith(("/", "---", "==")):
                return line[:220]
        return ""

    @staticmethod
    def progress(folder, stage, sl, top):
        """How far the stage in progress is, where the files can tell: enrichment batches, champion searches."""
        if stage == "enrich":
            total = -(-len(sl.get("shortlist") or []) // pl.ENRICH_BATCH)
            done = len(glob.glob(os.path.join(folder, pl.ENRICH_DIR, "part-*.json")))
            return {"done": min(done, total), "total": total, "unit": "batches of 5 accounts researched"} if total else None
        if stage == "find_champions" and top:
            people = c.load_optional(os.path.join(folder, pl.PEOPLE), {})
            return {"done": len(people), "total": len(top), "unit": "accounts searched for a champion"}
        return None

    def detail(self, rid):
        folder = self.path(rid)

        def load(name, default=None):
            return c.load_optional(os.path.join(folder, name), default)

        meta = load(c.RUN, {})
        icp, longlist, sl = load(c.ICP, {}), load(c.LONGLIST, {}), load(c.SHORTLIST, {})
        enr = (load(c.ENRICHMENT, {}) or {}).get("accounts") or {}
        top, champs = load(c.TOP10, []), load(c.CHAMPIONS, {"accounts": {}})
        lem = load(c.LEMLIST_RESULT, {})
        state = self.state(rid, meta)
        done, current = meta.get("stages_done") or [], meta.get("stage")
        stages = []
        for key, title in pl.STAGES:
            st = "done" if key in done else "pending"
            if key == current and st != "done":
                st = {"running": "running", "failed": "failed"}.get(state, "stopped")
            stages.append({"key": key, "title": title, "state": st})
        emails = {}
        seq = load(c.SEQUENCE)
        if seq and top:
            mail = {"accounts": {d: x for d, x in champs["accounts"].items() if x.get("email")}}
            try:
                for r in sq.render(seq, top, mail):
                    emails[r["account"]["domain"]] = r["emails"]
            except SystemExit:
                pass
        leads = []
        for row, t in zip(rep.rows(top, champs, lem), top):
            trig = t.get("trigger") or {}
            leads.append(dict(row, trigger_type=trig.get("type", ""), emails=emails.get(t["domain"], []),
                              description=(enr.get(t["domain"]) or {}).get("description", ""),
                              written=bool(t.get("why") and t.get("trigger_text"))))
        co = icp.get("company") or {}
        return {
            "id": rid, "state": state, "company": meta.get("company"), "date": meta.get("date"),
            "domain": meta.get("domain"),
            "error": meta.get("error"), "notes": meta.get("notes") or [], "stages": stages,
            "now": {"stage": current, "started": meta.get("stage_started"), "run_started": meta.get("run_started"),
                    "finished": meta.get("finished_at"), "clock": time.time(), "activity": self.activity(rid),
                    "progress": self.progress(folder, current, sl, top)},
            "credits": meta.get("credits") or {}, "apollo_balance": meta.get("apollo_balance"),
            "claude": meta.get("claude"), "clay": meta.get("clay"),
            "clay_on": bool(os.environ.get("CLAY_API_KEY") and os.environ.get("CLAY_ROUTINE_ID")),
            "icp": {"headline": (icp.get("icp") or {}).get("headline"), "one_liner": co.get("one_liner")},
            "funnel": [n for n in (
                {"label": "Found on Apollo", "n": longlist.get("searched")},
                {"label": "Kept after customers, competitors and repeats", "n": longlist.get("kept")},
                {"label": "Kept after reading each one", "n": len(sl.get("ranked") or []) or None},
                {"label": "Shortlisted and researched", "n": len(enr) or None},
                {"label": "Picked", "n": len(top) or None}) if n["n"] is not None],
            "leads": leads,
            "lemlist": {"campaign": lem.get("campaign_name"), "status": lem.get("status"), "sender": lem.get("sender"),
                        "delays": lem.get("delays"),
                        "added": sum(1 for r in lem.get("results") or [] if r.get("outcome") == "added")} if lem else None,
            "files": available(folder),
        }


def check_keys(py):
    """`python -m agent check-keys --json` with the project's Python (the one that has the anthropic SDK)."""
    try:
        out = subprocess.run([py, "-m", "agent", "check-keys", "--json"], cwd=c.ROOT, env=os.environ.copy(),
                             capture_output=True, text=True, timeout=90)
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return {k: {"ok": False, "message": "the check did not finish: run python -m agent check-keys in a terminal"}
                for k in ("claude", "apollo", "lemlist", "clay")}


def claude_backend():
    try:
        return llm.backend()
    except SystemExit:
        return "unknown"


def keys_state():
    return {k: bool(os.environ.get(v)) for k, v in KEYS.items()}


def save_keys(values, path=c.ENV):
    """Write the keys given into .env (git-ignored, readable by you only) and into this process's environment.
    clear_<field> removes that key instead (an empty field keeps the one you have)."""
    if (values.get("clay_routine") or "").strip().startswith("clay_"):
        raise ValueError("that is a Clay API key, not a function ID: copy the ID (function:t_...) from Clay, "
                         "Functions, Details")
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    for k, env in KEYS.items():
        if values.get(f"clear_{k}"):
            lines = [ln for ln in lines if not ln.startswith(env + "=")]
            os.environ.pop(env, None)
            continue
        v = (values.get(k) or "").strip()
        if not v:
            continue
        if any(ch in v for ch in "\r\n="):
            raise ValueError(f"the {k} key has a character a key never has")
        lines = [ln for ln in lines if not ln.startswith(env + "=")] + [f"{env}={v}"]
        os.environ[env] = v
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def make_handler(runs, token, sdk, scheduler=None):
    scheduler = scheduler or sch.Scheduler(runs.dir, runs.py)
    page = open(os.path.join(HERE, "dashboard.html"), encoding="utf-8").read().replace("__TOKEN__", token)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _ok_host(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _authed(self, q):
            return secrets.compare_digest(self.headers.get("X-Token") or (q.get("t") or [""])[0], token)

        def _route(self, method):
            url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(url.query)
            if not self._ok_host():
                return self._send(403, {"error": "open the dashboard at 127.0.0.1"})
            if method == "GET" and url.path == "/":
                return self._send(200, page, "text/html; charset=utf-8")
            if not url.path.startswith("/api/") or not self._authed(q):
                return self._send(404 if not url.path.startswith("/api/") else 403, {"error": "not found"})
            try:
                return self._api(method, url.path[5:], q)
            except ValueError as e:
                return self._send(400, {"error": str(e)})

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _api(self, method, path, q):
            rid = (q.get("id") or [""])[0]
            if method == "GET" and path == "state":
                return self._send(200, {"keys": keys_state(), "sdk": sdk, "claude": claude_backend(),
                                        "runs": runs.list(), "schedules": scheduler.all(),
                                        "can_schedule": scheduler.supported()})
            if method == "GET" and path == "run":
                return self._send(200, runs.detail(rid))
            if method == "GET" and path == "log":
                return self._send(200, dict(runs.log(rid, int((q.get("offset") or ["0"])[0])), running=runs.running(rid)))
            if method == "GET" and path == "file":
                name = (q.get("name") or [""])[0]
                if name not in FILES:
                    raise ValueError("unknown file")
                folder = runs.path(rid)
                if not os.path.exists(os.path.join(folder, name)) and name in available(folder):
                    build(folder, name)
                with open(os.path.join(folder, name), "rb") as f:
                    data = f.read()
                return self._send(200, data, TYPES[os.path.splitext(name)[1]],
                                  {"Content-Disposition": f'attachment; filename="{rid.replace("/", "-")}-{name}"'})
            if method == "POST" and path == "run":
                b = self._body()
                if not (b.get("company") or "").strip():
                    raise ValueError("enter a company name")
                api = claude_backend() == "api"
                missing = [k for k in (("anthropic", "apollo") if api else ("apollo",)) if not keys_state()[k]]
                if missing:
                    raise ValueError(f"add your {' and '.join(m.title() for m in missing)} key first")
                if api and (not sdk or int(sdk.split(".")[0]) < 1):
                    raise ValueError("the anthropic package is missing or too old: run pip install -r requirements.txt")
                date = b.get("date") if b.get("resume") else None
                rid = runs.start(b["company"].strip(), (b.get("domain") or "").strip() or None, date)
                return self._send(200, {"id": rid})
            if method == "POST" and path == "check":
                return self._send(200, {"checks": check_keys(runs.py)})
            if method == "POST" and path == "delete":
                runs.delete(self._body().get("id"))
                return self._send(200, {"runs": runs.list()})
            if method == "POST" and path == "schedule":
                b = self._body()
                if not (b.get("company") or "").strip():
                    raise ValueError("which company?")
                if b.get("off"):
                    scheduler.remove(c.slugify(b["company"]))
                else:
                    scheduler.set(b["company"].strip(), (b.get("domain") or "").strip() or None,
                                  b.get("day") or "Monday", b.get("time") or "09:00")
                return self._send(200, {"schedules": scheduler.all()})
            if method == "POST" and path == "keys":
                save_keys(self._body())
                return self._send(200, {"keys": keys_state()})
            return self._send(404, {"error": "not found"})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

    return Handler


def serve(port=8765, open_browser=True):
    c.load_env()
    runs = Runs()
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(runs, token, sdk_version(runs.py)))
    url = f"http://127.0.0.1:{port}/"
    print(f"Outbound Agent dashboard: {url}  (Ctrl+C to stop; runs started here keep going)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
