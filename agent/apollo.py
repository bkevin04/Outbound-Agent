"""Apollo's REST API for the three calls a run makes, with Kevin's budget enforced in code before every paid call:
at most 6 credits a run (1 company search + at most 5 reveals, one per lead; Kevin approved up to 11 on 18 Oct 2026
and cut the run to 5 leads on 20 Oct). There is no method for a
phone reveal, a waterfall, an organization enrich or job postings, so the run cannot spend credits on them."""
import os

from . import common as c
from . import rest

BASE = "https://api.apollo.io/api/v1"
BUDGET = 1 + c.LEADS


def spent(credits):
    return sum(v for k, v in (credits or {}).items() if k.startswith("apollo") and isinstance(v, (int, float)))


class Apollo:
    def __init__(self, key, base=BASE):
        self.headers, self.base = {"x-api-key": key, "Cache-Control": "no-cache"}, base

    @classmethod
    def from_env(cls):
        key = os.environ.get("APOLLO_API_KEY")
        if not key:
            raise SystemExit("APOLLO_API_KEY is not set (Apollo > Settings > Integrations > API)")
        return cls(key)

    def _call(self, method, path, params=None, body=None, paid=False):
        url = self.base + path + ("?" + rest.query(params) if params else "")
        return rest.request(method, url, self.headers, body=body, paid=paid)

    def _need(self, credits, n, what):
        if spent(credits) + n > BUDGET:
            raise SystemExit(f"{what} needs {n} Apollo credit(s) and this run has spent {spent(credits)} of {BUDGET}: "
                             "stop and ask Kevin")

    def search_companies(self, params, credits):
        """The run's one paid search: 1 credit for up to 100 companies. -> Apollo's answer as it came."""
        self._need(credits, 1, "the company search")
        return self._call("POST", "/mixed_companies/search", dict(params, page=1, per_page=100), paid=True)

    def search_people(self, domain, titles, seniorities=None, verified=True, per_page=10):
        """Free people search at one account (Apollo returns no domain per person, so one domain per call).
        -> [{id, first_name, last_name, title, organization, has_email}]; last names come back obfuscated."""
        q = {"q_organization_domains_list": [domain], "person_titles": titles, "person_seniorities": seniorities,
             "contact_email_status": ["verified"] if verified else None, "per_page": per_page, "page": 1}
        res = self._call("POST", "/mixed_people/api_search", q)
        return [{"id": p.get("id"), "first_name": p.get("first_name"),
                 "last_name": p.get("last_name") or p.get("last_name_obfuscated"), "title": p.get("title"),
                 "organization": (p.get("organization") or {}).get("name"), "has_email": p.get("has_email")}
                for p in res.get("people") or [] if p.get("id")]

    def bulk_match(self, ids, credits):
        """Reveal the champions' work emails: 1 credit per match, at most one per lead. Every reveal flag stays at its default
        (off): no personal emails, no phones, no waterfall."""
        ids = list(ids)[:c.LEADS]
        self._need(credits, len(ids), f"revealing {len(ids)} champions")
        return self._call("POST", "/people/bulk_match", body={"details": [{"id": i} for i in ids]}, paid=True)

    def check(self):
        """A free call that proves the key works. -> one line on the account."""
        try:
            res = self._call("GET", "/users/api_profile", {"include_credit_usage": True})
        except rest.HTTPError as e:
            raise SystemExit(f"Apollo refused the key (HTTP {e.status})" if e.status in (401, 403)
                             else f"Apollo answered HTTP {e.status}: {e.code() or e.body[:120]}") from None
        left = res.get("num_credits_remaining")
        return "the key works" + (f", {left:,} credits left on the account" if isinstance(left, (int, float)) else "")

    def credit_balance(self):
        """{num_credits_remaining, ...} from the key's profile (free), or None when Apollo does not say."""
        try:
            res = self._call("GET", "/users/api_profile", {"include_credit_usage": True})
        except (rest.HTTPError, OSError):
            return None
        keep = ("num_credits_remaining", "effective_num_lead_credits", "num_lead_credits_used")
        return {k: res[k] for k in keep if res.get(k) is not None} or None
