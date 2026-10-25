"""Projects: folders on the dashboard that group runs (by client, by month, by anything). Kept in runs/projects.json,
git-ignored with the rest of runs/. A run is in at most one project. A run with nothing set follows the newest run of
the same company that is in one, so next week's run, scheduled or not, lands in the same project."""
import os
import secrets
import threading

from . import common as c

FILE = "projects.json"


class Projects:
    def __init__(self, runs_dir=c.RUNS):
        self.path, self.lock = os.path.join(runs_dir, FILE), threading.Lock()

    def _load(self):
        d = c.load_optional(self.path, {}) or {}
        return {"projects": list(d.get("projects") or []), "runs": dict(d.get("runs") or {})}

    def all(self):
        return self._load()["projects"]

    @staticmethod
    def _name(name, d, skip=None):
        name = " ".join(str(name or "").split())
        if not name or len(name) > 60:
            raise ValueError("give the project a name, up to 60 characters")
        if any(p["name"].lower() == name.lower() and p["id"] != skip for p in d["projects"]):
            raise ValueError(f"there is already a project called {name}")
        return name

    def create(self, name):
        with self.lock:
            d = self._load()
            p = {"id": secrets.token_hex(4), "name": self._name(name, d)}
            d["projects"].append(p)
            c.save(self.path, d)
            return p

    def rename(self, pid, name):
        with self.lock:
            d = self._load()
            p = self._get(d, pid)
            p["name"] = self._name(name, d, skip=pid)
            c.save(self.path, d)
            return p

    def delete(self, pid):
        """The project goes; its runs stay, outside any project."""
        with self.lock:
            d = self._load()
            self._get(d, pid)
            d["projects"] = [p for p in d["projects"] if p["id"] != pid]
            d["runs"] = {r: ("" if v == pid else v) for r, v in d["runs"].items()}
            c.save(self.path, d)

    def move(self, rid, pid):
        """Put a run in a project, or in none (pid empty)."""
        with self.lock:
            d = self._load()
            if pid:
                self._get(d, pid)
            d["runs"][rid] = pid or ""
            c.save(self.path, d)

    def forget(self, rid):
        with self.lock:
            d = self._load()
            if d["runs"].pop(rid, None) is not None:
                c.save(self.path, d)

    def of(self, rids):
        """{run id: project id or None}. A run with nothing set takes the project of its company's newest run in one."""
        d = self._load()
        ids = {p["id"] for p in d["projects"]}
        explicit = {r: v for r, v in d["runs"].items() if v == "" or v in ids}
        newest = {}
        for r in sorted(explicit, key=lambda r: r.split("/")[-1]):   # oldest first, so the newest wins
            if explicit[r]:
                newest[r.split("/")[0]] = explicit[r]
        return {r: (explicit[r] or None) if r in explicit else newest.get(r.split("/")[0]) for r in rids}

    @staticmethod
    def _get(d, pid):
        for p in d["projects"]:
            if p["id"] == pid:
                return p
        raise ValueError("no such project")
