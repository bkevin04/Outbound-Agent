"""Validate icp.json, the file Stage 2 writes and every later stage reads.

The ICP keeps Kevin's structure (headline, two firmographic tracks, the buyer's
pain, the check against customers, the five funnel stages) and adds what the agent needs to run it every week:
the segments, the Apollo search, the scoring weights, the champion titles and the exclusion list."""
import re

from .common import norm_domain

FUNNEL_STAGES = 5


def problems(icp):
    """Return a list of human-readable problems; empty means the ICP is usable."""
    out = []

    def need(cond, msg):
        if not cond:
            out.append(msg)

    c = icp.get("company") or {}
    for k in ("name", "slug", "domain", "one_liner"):
        need(c.get(k), f"company.{k} is missing")
    need(c.get("city") or c.get("remote"), "company.city is missing (or set company.remote: true)")

    i = icp.get("icp") or {}
    need(i.get("headline"), "icp.headline is missing")
    firmo = i.get("firmographics") or []
    need(len(firmo) == 2, "icp.firmographics needs 2 paragraphs")
    if len(firmo) == 2:
        need(str(firmo[1]).startswith("Second track:"), 'icp.firmographics[1] must start with "Second track:"')
    check = i.get("check") or []
    need(len(check) == 2, "icp.check needs 2 paragraphs")
    if len(check) == 2:
        need(str(check[1]).startswith("They do not yet prove"), 'icp.check[1] must start with "They do not yet prove"')
    need(i.get("pain_quote") and i.get("pain_body"), "icp.pain_quote and icp.pain_body are needed")
    funnel = i.get("funnel") or []
    need(len(funnel) == FUNNEL_STAGES and all(s.get("title") and s.get("body") for s in funnel),
         f"icp.funnel needs {FUNNEL_STAGES} stages with title and body")

    segs = icp.get("segments") or []
    need(segs, "segments is empty")
    for s in segs:
        need(s.get("key") and s.get("label") and s.get("keywords"), f"segment {s.get('key')!r} needs key, label and keywords")

    a = icp.get("apollo_search") or {}
    need(a, "apollo_search is missing")
    need(a.get("per_page") == 100, "apollo_search.per_page must be 100 (one paid search returns the whole long list)")
    for bad in ("page",):
        need(bad not in a or a[bad] == 1, "apollo_search.page must be 1")

    sc = icp.get("scoring") or {}
    emp = sc.get("employees") or {}
    need(all(k in emp for k in ("min", "max")), "scoring.employees needs min and max")
    need(sc.get("keyword_weights"), "scoring.keyword_weights is empty")
    need(sc.get("trigger_weights"), "scoring.trigger_weights is empty")

    ch = icp.get("champion") or {}
    need(ch.get("titles"), "champion.titles is empty")

    for d in (icp.get("exclude") or {}).get("domains", []):
        need(d == norm_domain(d), f"exclude.domains entry {d!r} must be a bare domain ({norm_domain(d)!r})")
    for cu in icp.get("customers") or []:
        need(cu.get("name"), "every customer needs a name")

    text = repr(icp)
    need(not re.search(r"\bpatterns?\b", text, re.I), "the word 'pattern' is in icp.json: Kevin does not want it, say it plainly")
    need("—" not in text, "an em dash is in icp.json: use a comma or a colon")
    return out


def excluded_domains(icp):
    """Domains never to prospect: the company itself, its customers, its competitors and anything listed."""
    out = {norm_domain((icp.get("company") or {}).get("domain"))}
    for cu in icp.get("customers") or []:
        out.add(norm_domain(cu.get("domain")))
    for co in icp.get("competitors") or []:
        out.add(norm_domain(co.get("domain")))
    for d in (icp.get("exclude") or {}).get("domains", []):
        out.add(norm_domain(d))
    out.discard("")
    return out
