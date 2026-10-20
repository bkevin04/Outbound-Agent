"""Stage 3: turn the raw Apollo company search into the long list (longlist.json).

The search returns two buckets, `organizations` (net-new companies, `id` is the organization id) and `accounts`
(companies the team already saved, the organization id is `organization_id`). Both are flattened into one list
with the fields the scorer reads, then the company itself, its customers, its competitors, the excluded domains
and every account already pitched in an earlier week are dropped, each with its reason."""
from .common import extract_json, norm_domain
from .icp import excluded_domains


def _is_search_result(obj):
    return isinstance(obj, dict) and ("organizations" in obj or "accounts" in obj)


def parse_raw(text):
    found = extract_json(text, _is_search_result)
    if found is None:
        raise SystemExit("no Apollo company search result (organizations / accounts) found in the file")
    return found


def _first(*values):
    for v in values:
        if v not in (None, "", [], {}):
            return v
    return None


def _pct(v):
    """Apollo gives growth as a fraction (0.42); keep it as a whole percent (42)."""
    return None if v is None else round(float(v) * 100)


def normalize(row, bucket):
    org = row.get("organization") if isinstance(row.get("organization"), dict) else {}
    org_id = row.get("id") if bucket == "organizations" else _first(row.get("organization_id"), org.get("id"))
    domain = norm_domain(_first(row.get("primary_domain"), row.get("domain"), org.get("primary_domain"),
                                row.get("website_url"), org.get("website_url")))
    techs = _first(row.get("technology_names"), org.get("technology_names"),
                   [t.get("name") for t in (row.get("current_technologies") or []) if isinstance(t, dict)]) or []
    keywords = _first(row.get("keywords"), org.get("keywords")) or []
    return {
        "apollo_org_id": org_id,
        "name": _first(row.get("name"), org.get("name")),
        "domain": domain,
        "website_url": _first(row.get("website_url"), org.get("website_url")),
        "linkedin_url": _first(row.get("linkedin_url"), org.get("linkedin_url")),
        "industry": _first(row.get("industry"), org.get("industry")),
        "employees": _first(row.get("estimated_num_employees"), org.get("estimated_num_employees")),
        "city": _first(row.get("city"), row.get("organization_city"), org.get("city")),
        "state": _first(row.get("state"), row.get("organization_state"), org.get("state")),
        "country": _first(row.get("country"), row.get("organization_country"), org.get("country")),
        "founded_year": _first(row.get("founded_year"), org.get("founded_year")),
        "short_description": _first(row.get("short_description"), org.get("short_description"), row.get("seo_description")),
        "keywords": [str(k).lower() for k in keywords][:40],
        "technologies": [str(t) for t in techs][:60],
        "latest_funding_stage": _first(row.get("latest_funding_stage"), org.get("latest_funding_stage")),
        "latest_funding_date": _first(row.get("latest_funding_round_date"), org.get("latest_funding_round_date")),
        "total_funding": _first(row.get("total_funding"), org.get("total_funding")),
        "publicly_traded": bool(_first(row.get("publicly_traded_symbol"), org.get("publicly_traded_symbol"))),
        "headcount_growth_6m": _pct(_first(row.get("organization_headcount_six_month_growth"), org.get("organization_headcount_six_month_growth"))),
        "headcount_growth_12m": _pct(_first(row.get("organization_headcount_twelve_month_growth"), org.get("organization_headcount_twelve_month_growth"))),
        "revenue": _first(row.get("organization_revenue_printed"), org.get("organization_revenue_printed")),
        "naics": (_first(row.get("naics_codes"), org.get("naics_codes")) or [None])[0],
        "apollo_bucket": bucket,
    }


def build(raw_texts, icp, pitched=None):
    """raw_texts: one or more raw search results (pages). pitched: domains already pitched in earlier weeks."""
    excluded = excluded_domains(icp)
    customers = {norm_domain(c.get("domain")) for c in icp.get("customers") or []}
    competitors = {norm_domain(c.get("domain")) for c in icp.get("competitors") or []}
    pitched = {norm_domain(d) for d in (pitched or [])}
    own = norm_domain((icp.get("company") or {}).get("domain"))

    rows, total = [], 0
    for text in raw_texts:
        res = parse_raw(text)
        for bucket in ("accounts", "organizations"):
            for row in res.get(bucket) or []:
                total += 1
                rows.append(normalize(row, bucket))

    kept, dropped, seen = [], [], set()
    for a in rows:
        d = a["domain"]
        if not d:
            reason = "no domain"
        elif d in seen:
            reason = "duplicate"
        elif d == own:
            reason = "the company itself"
        elif d in customers:
            reason = "already a customer"
        elif d in competitors:
            reason = "competitor"
        elif d in pitched:
            reason = "already pitched in an earlier week"
        elif d in excluded:
            reason = "on the exclude list"
        else:
            reason = None
        if reason:
            dropped.append({"name": a["name"], "domain": d, "reason": reason})
        else:
            kept.append(a)
        if d:
            seen.add(d)
    return {"searched": total, "kept": len(kept), "dropped": dropped, "accounts": kept}
