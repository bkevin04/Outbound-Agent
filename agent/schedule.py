"""Weekly runs on this machine: a company's run starts again every week on the day and at the time you pick.

macOS: a LaunchAgent (~/Library/LaunchAgents/dev.outbound-agent.<slug>.plist) loaded with launchctl. A Mac that is
asleep at that time runs it when it wakes up; one that is off or logged out skips that week. Linux: a crontab line.
The job runs `python -m agent scheduled <company>`, which starts the run the way the dashboard does (its log in
run.log in the run folder), so the dashboard shows it like any other. What is scheduled is kept in
runs/<slug>/schedule.json (git-ignored with the rest of runs/). Each run spends what a run spends: at most 11 Apollo
credits, and the lemlist campaign stays a draft."""
import os
import platform
import plistlib
import re
import shlex
import subprocess

from . import common as c

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
LABEL = "dev.outbound-agent."
MARK = "# outbound-agent:"
INFO = "schedule.json"
LOG = "schedule.log"


class Scheduler:
    def __init__(self, runs_dir=c.RUNS, py=None, system=None, agents_dir=None, run=subprocess.run):
        self.dir, self.py, self.run = runs_dir, py, run
        self.system = system or {"Darwin": "launchd", "Linux": "cron"}.get(platform.system())
        self.agents = agents_dir or os.path.expanduser("~/Library/LaunchAgents")

    def supported(self):
        return self.system in ("launchd", "cron")

    def _info_path(self, slug):
        return os.path.join(self.dir, slug, INFO)

    def all(self):
        """{slug: {"company", "domain", "day", "time"}} for every company scheduled on this machine."""
        out = {}
        if not os.path.isdir(self.dir):
            return out
        for slug in sorted(os.listdir(self.dir)):
            info = c.load_optional(self._info_path(slug))
            if info and self._installed(slug):
                out[slug] = info
        return out

    def set(self, company, domain=None, day="Monday", time="09:00"):
        if not self.supported():
            raise ValueError("weekly runs need macOS or Linux on this machine")
        if day not in DAYS:
            raise ValueError(f"pick a day: {', '.join(DAYS)}")
        m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", time or "")
        if not m:
            raise ValueError("pick a time like 09:00")
        slug = c.slugify(company)
        hour, minute = int(m.group(1)), int(m.group(2))
        cmd = [self.py or "python3", "-m", "agent", "scheduled", company] + (["--domain", domain] if domain else [])
        log = os.path.join(self.dir, slug, LOG)
        os.makedirs(os.path.dirname(log), exist_ok=True)
        if self.system == "launchd":
            self._launchd_set(slug, cmd, log, DAYS.index(day) + 1, hour, minute)   # launchd: 1 is Monday, 7 Sunday
        else:
            self._cron_set(slug, cmd, log, (DAYS.index(day) + 1) % 7, hour, minute)  # cron: 0 is Sunday
        info = {"company": company, "domain": domain or None, "day": day, "time": f"{hour:02d}:{minute:02d}"}
        c.save(self._info_path(slug), info)
        return info

    def remove(self, slug):
        if self.system == "launchd":
            label = LABEL + slug
            self.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
            path = os.path.join(self.agents, label + ".plist")
            if os.path.exists(path):
                os.remove(path)
        elif self.system == "cron":
            self._cron_write([ln for ln in self._cron_lines() if not ln.endswith(MARK + slug)])
        if os.path.exists(self._info_path(slug)):
            os.remove(self._info_path(slug))

    # launchd
    def _launchd_set(self, slug, cmd, log, weekday, hour, minute):
        label = LABEL + slug
        os.makedirs(self.agents, exist_ok=True)
        path = os.path.join(self.agents, label + ".plist")
        job = {"Label": label, "ProgramArguments": cmd, "WorkingDirectory": c.ROOT,
               "StartCalendarInterval": {"Weekday": weekday, "Hour": hour, "Minute": minute},
               # the PATH this was set from, so the job finds `claude` the way your terminal does
               "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")},
               "StandardOutPath": log, "StandardErrorPath": log, "RunAtLoad": False}
        domain = f"gui/{os.getuid()}"
        self.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)   # replace an older one
        with open(path, "wb") as f:
            plistlib.dump(job, f)
        res = self.run(["launchctl", "bootstrap", domain, path], capture_output=True, text=True)
        if res.returncode != 0:
            os.remove(path)
            raise ValueError(f"launchctl did not take the schedule: {(res.stderr or res.stdout or '').strip()[:200]}")

    def _installed(self, slug):
        if self.system == "launchd":
            return os.path.exists(os.path.join(self.agents, LABEL + slug + ".plist"))
        if self.system == "cron":
            return any(ln.endswith(MARK + slug) for ln in self._cron_lines())
        return False

    # cron
    def _cron_lines(self):
        res = self.run(["crontab", "-l"], capture_output=True, text=True)
        return (res.stdout or "").splitlines() if res.returncode == 0 else []

    def _cron_write(self, lines):
        res = self.run(["crontab", "-"], input="\n".join(lines) + "\n", capture_output=True, text=True)
        if res.returncode != 0:
            raise ValueError(f"crontab did not take the schedule: {(res.stderr or '').strip()[:200]}")

    def _cron_set(self, slug, cmd, log, dow, hour, minute):
        path = os.environ.get("PATH", "/usr/bin:/bin")
        line = (f"{minute} {hour} * * {dow} cd {shlex.quote(c.ROOT)} && PATH={shlex.quote(path)} "
                f"{' '.join(shlex.quote(x) for x in cmd)} >> {shlex.quote(log)} 2>&1").replace("%", "\\%")
        line += f" {MARK}{slug}"     # cron turns a bare % into a new line; the marker is a shell comment
        self._cron_write([ln for ln in self._cron_lines() if not ln.endswith(MARK + slug)] + [line])

