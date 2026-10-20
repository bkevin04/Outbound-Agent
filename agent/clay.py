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
        """Calls that cost nothing and prove the key works. -> one line on the workspace."""
        try:
            me = self._call("GET", "/me")
            left = self.balance()
        except rest.HTTPError as e:
            raise SystemExit(f"Clay refused the key (HTTP {e.status})" if e.status in (401, 403)
                             else f"Clay answered HTTP {e.status}: {e.body[:120]}") from None
        ws = ((me or {}).get("workspace") or {}).get("name")
        return ("the key works" + (f" for {ws}" if ws else "")
                + (f", {left:,.0f} credits left" if isinstance(left, (int, float)) else ""))

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
