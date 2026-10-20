"""Clay's Public API (https://api.clay.com/public/v0, header `clay-api-key`): the credit balance, and one routine run
over the shortlist when CLAY_ROUTINE_ID names a Clay function to run.

Clay has no "enrich this domain" endpoint: enrichment runs through routines, Clay's managed functions or your own,
identified by an id like `function:t_...` that the API cannot list (copy it in Clay: Functions, then Details). The run
hands each account's Clay result to the web research step as evidence; nothing reads Clay's output keys directly,
since they depend on the function."""
import os
import time

from . import rest

BASE = "https://api.clay.com/public/v0"
POLL_EVERY = 5      # seconds between result polls
POLL_FOR = 900      # give up on a routine run after 15 minutes


def routine_from_env():
    """CLAY_ROUTINE_ID, or "" when none is set. A Clay API key pasted there is refused: it would go into the run's
    URL, its notes and the report."""
    rid = os.environ.get("CLAY_ROUTINE_ID", "").strip()
    if rid.startswith("clay_") or (rid and rid == os.environ.get("CLAY_API_KEY", "").strip()):
        raise SystemExit("the Clay function ID holds a Clay API key: put the function's ID there (function:t_..., "
                         "from Clay, Functions, Details, with API turned on) or remove it (Edit keys, or delete the "
                         "CLAY_ROUTINE_ID line in .env)")
    return rid


class Clay:
    def __init__(self, key, base=BASE, sleep=time.sleep):
        self.headers, self.base, self.sleep = {"clay-api-key": key}, base, sleep

    @classmethod
    def from_env(cls):
        key = os.environ.get("CLAY_API_KEY")
        if not key:
            raise SystemExit("CLAY_API_KEY is not set (Clay > API & CLI > API keys)")
        return cls(key)

    def _call(self, method, path, params=None, body=None, paid=False):
        url = self.base + path + ("?" + rest.query(params) if params else "")
        return rest.request(method, url, self.headers, body=body, paid=paid)

    def balance(self):
        """Remaining data credits (rollover included), or None when Clay does not say. Free to read."""
        res = self._call("GET", "/credits/balance")
        return res.get("balance") if isinstance(res, dict) else None

    def check(self):
        """Calls that cost nothing and prove the key works, plus a look at the function ID. -> one line on the workspace."""
        try:
            routine_from_env()
            routine = ""
        except SystemExit as e:
            routine = str(e)
        try:
            me = self._call("GET", "/me")
            left = self.balance()
        except rest.HTTPError as e:
            msg = (f"Clay refused the key (HTTP {e.status}): make a new one in Clay, API and CLI, API keys, and copy "
                   "it when Clay shows it" if e.status in (401, 403) else f"Clay answered HTTP {e.status}: {e.body[:120]}")
            raise SystemExit("; ".join(m for m in (msg, routine) if m)) from None
        ws = ((me or {}).get("workspace") or {}).get("name")
        line = ("the key works" + (f" for {ws}" if ws else "")
                + (f", {left:,.0f} credits left" if isinstance(left, (int, float)) else ""))
        if routine:
            raise SystemExit(f"{line}, but {routine}")
        return line

    def run_routine(self, routine_id, items):
        """Run a routine over up to 100 items ({"id", "inputs"}) and wait for it. Paid: never sent twice.
        -> {item id: result dict or {"error": message}}"""
        run = self._call("POST", f"/routines/{routine_id}/run", body={"items": items[:100]}, paid=True)
        rid = run["routine_run_id"]
        out, cursor, waited = {}, None, 0
        while True:
            res = self._call("GET", f"/routines/run/{rid}/results", {"limit": 100, "cursor": cursor})
            if res.get("status") != "complete":
                if waited >= POLL_FOR:
                    raise SystemExit(f"Clay routine run {rid} still running after {POLL_FOR // 60} minutes")
                self.sleep(POLL_EVERY)
                waited += POLL_EVERY
                continue
            for row in res.get("data") or []:
                out[row.get("id")] = (row.get("result") or {}) if row.get("status") == "complete" else \
                    {"error": (row.get("error") or {}).get("message") or "failed"}
            cursor = res.get("cursor")
            if not cursor:
                return out
