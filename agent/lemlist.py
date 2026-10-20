"""lemlist's REST API for the run: find or create the campaign, add the 3 steps, put Kevin on it as the sender, add
the leads.

There is no method that starts a campaign, reviews (launches) a lead or turns on lemlist enrichment. The emails speak
as the company's BDR and Kevin does not work there yet, so the campaign stays a draft and nothing is ever sent; every
lead goes in with findEmail, verifyEmail, linkedinEnrichment and findPhone off, which also keeps lemlist credits
untouched."""
import base64
import os

from . import rest
from .common import norm_domain

BASE = "https://api.lemlist.com/api"
NO_ENRICHMENT = {"deduplicate": True, "findEmail": False, "verifyEmail": False, "linkedinEnrichment": False,
                 "findPhone": False}


def _rows(res, *keys):
    """A list answer, or the list inside a wrapped one ({"campaigns": [...]}, {"data": [...]})."""
    if isinstance(res, list):
        return res
    for k in keys + ("data", "items", "results"):
        if isinstance((res or {}).get(k), list):
            return res[k]
    return []


class Lemlist:
    def __init__(self, key, base=BASE):
        token = base64.b64encode(f":{key}".encode()).decode()
        self.headers, self.base = {"Authorization": f"Basic {token}"}, base

    @classmethod
    def from_env(cls):
        key = os.environ.get("LEMLIST_API_KEY")
        if not key:
            raise SystemExit("LEMLIST_API_KEY is not set (lemlist > Settings > Integrations > API)")
        return cls(key)

    def _call(self, method, path, params=None, body=None):
        url = self.base + path + ("?" + rest.query(params) if params else "")
        return rest.request(method, url, self.headers, body=body)

    def check(self):
        """A free call that proves the key works. -> one line on the account."""
        try:
            res = self._call("GET", "/team/credits")
        except rest.HTTPError as e:
            raise SystemExit(f"lemlist refused the key (HTTP {e.status})" if e.status in (401, 403)
                             else f"lemlist answered HTTP {e.status}: {e.code() or e.body[:120]}") from None
        n = res.get("credits") if isinstance(res, dict) else None
        return "the key works" + (f", {n:,} lemlist credits (the agent never spends them)" if isinstance(n, (int, float)) else "")

    def find_campaign(self, name):
        offset = 0
        while True:
            page = _rows(self._call("GET", "/campaigns", {"version": "v2", "limit": 100, "offset": offset}), "campaigns")
            for camp in page:
                if camp.get("name") == name:
                    return camp
            if len(page) < 100:
                return None
            offset += 100

    def lead_domains(self, campaign_id):
        """Every lead's company domain (the email's domain when lemlist has none): the accounts already pitched."""
        res = self._call("GET", f"/v2/campaigns/{campaign_id}/export/leads", {"state": "all", "format": "json"})
        out = []
        for lead in _rows(res, "leads"):
            d = norm_domain(lead.get("companyDomain")) or (lead.get("email") or "").split("@")[-1].lower()
            if d:
                out.append(d)
        return out

    def create_campaign(self, name, timezone):
        """-> {_id, sequenceId, ...}. lemlist adds an empty sequence and a default schedule."""
        return self._call("POST", "/campaigns", body={"name": name, "timezone": timezone})

    def first_sequence_id(self, campaign_id):
        seqs = self._call("GET", f"/campaigns/{campaign_id}/sequences")
        main = [s for s in (seqs.values() if isinstance(seqs, dict) else seqs) if not s.get("level")]
        return main[0]["_id"]

    def add_step(self, sequence_id, step):
        body = {"type": "email", "subject": step["subject"], "message": step["body"], "delay": step["delay"]}
        return self._call("POST", f"/sequences/{sequence_id}/steps", body=body)

    def campaign(self, campaign_id):
        return self._call("GET", f"/campaigns/{campaign_id}")

    def campaign_status(self, campaign_id):
        return self.campaign(campaign_id).get("status")

    def campaign_senders(self, campaign_id):
        return [s.get("email") or s.get("id") for s in self.campaign(campaign_id).get("senders") or []]

    def my_sender(self):
        """The sender to put on the campaign: the mailbox LEMLIST_SENDER_EMAIL names, else the API key owner's first
        connected email account. -> {"userId", "mailboxId", "email", "emails"} or None when lemlist shows none."""
        want = (os.environ.get("LEMLIST_SENDER_EMAIL") or "").strip().lower()
        owned = set()
        if not want:
            mine = self._call("GET", "/user/channels")
            owned = {a.get("id") for a in ((mine or {}).get("email") or {}).get("accounts") or []}
        senders = ((self._call("GET", "/senders") or {}).get("data") or {}).get("senders") or []
        for s in senders:
            for ch in s.get("channels") or []:
                if ch.get("type") != "email":
                    continue
                emails = {e.lower() for e in [s.get("email"), ch.get("label")] + list(ch.get("aliases") or []) if e}
                if (want and want in emails) or (not want and ch.get("id") in owned):
                    return {"userId": s.get("userId"), "mailboxId": ch.get("id"),
                            "email": ch.get("label") or s.get("email"), "emails": sorted(emails)}
        return None

    def add_sender(self, campaign_id, sender):
        """Make the sender the campaign's sender pool: PATCH sendUserIds (no plan named for it), else the senders route
        (lemlist's Email Pro plan). Run after the email steps are in: lemlist refuses a sender for a channel the
        campaign does not use yet. Adding a sender sends nothing; only launching does, and this module cannot."""
        try:
            self._call("PATCH", f"/campaigns/{campaign_id}", body={"sendUserIds": [sender["userId"]]})
            return
        except rest.HTTPError:
            pass
        body = {"senders": [{"userId": sender["userId"], "channels": [{"type": "email", "id": sender["mailboxId"]}]}]}
        try:
            self._call("POST", f"/campaigns/{campaign_id}/senders", body=body)
        except rest.HTTPError as e:
            raise SystemExit(f"lemlist would not add {sender['email']} as the sender (HTTP {e.status}: "
                             f"{e.code() or e.body[:120]})") from None

    def add_lead(self, campaign_id, lead):
        """One lead, its custom variables as top-level fields. -> {outcome, leadId}."""
        body = {k: v for k, v in lead.items() if k != "customVariables"}
        body.update(lead.get("customVariables") or {})
        try:
            res = self._call("POST", f"/campaigns/{campaign_id}/leads/", NO_ENRICHMENT, body=body)
        except rest.HTTPError as e:
            code = e.code()
            if "ALREADY_IN_CAMPAIGN" in code and "OTHER" not in code:
                return {"outcome": "already in this campaign", "leadId": None}
            if "ALREADY_IN_OTHER_CAMPAIGN" in code:
                return {"outcome": "already in another campaign", "leadId": None}
            return {"outcome": f"error {e.status}: {code or e.body[:120]}", "leadId": None}
        return {"outcome": "added", "leadId": res.get("_id")}
