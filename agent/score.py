"""Stages 4 and 6: score the long list, so "why these leads" has an answer you can show.

Two passes, both from the weights in icp.json["scoring"]:
- pre:   firmographics only (size, HQ, keywords, segment, funding, headcount growth from Apollo, plus the
         agent's triage of every account: its segment and one line on what it does) for all ~100 accounts,
         to pick the shortlist that gets enriched (Clay, or web research when Clay is not connected).
- final: firmographics + the enrichment signals (a dated trigger, docs/support tech, community channels,
         whether they already run an AI assistant, relevant hiring, Clay's fit score).
The top accounts (common.LEADS) need a trigger from the last `trigger_max_age_days` days with a source URL, and no segment may take
more than its share of the seats (`max_in_top10`, scaled to the run's size), so the picks mix segments the way the
ICP does."""
import math
import re

from . import common as c
from .common import parse_date, today

DEFAULTS = {
    "keyword_cap": 8,
    "tech_cap": 4,
    "public_company": -2,
    "community_channels": ["discord", "slack", "forum", "github discussions", "discourse"],
    "has_ai_assistant": -3,
    "no_ai_assistant": 2,
    "hiring_keywords": [],
    "trigger_max_age_days": 183,
    "segment_cap": 4,
    # 6-month headcount growth in percent -> points, first threshold met wins; shrinking scores growth_negative
    "growth_6m_points": [[20, 3], [10, 2], [3, 1]],
    "growth_negative": -1,
}


def _cfg(icp, key):
    return (icp.get("scoring") or {}).get(key, DEFAULTS.get(key))


def _hits(text, terms):
    """terms that occur in text as whole words or phrases (case-insensitive)."""
    return [t for t in terms if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(t.lower()), text)]


def _text(a):
    parts = [a.get("name"), a.get("industry"), a.get("short_description")] + list(a.get("keywords") or [])
    return " ".join(str(p) for p in parts if p).lower()


def apply_triage(longlist, triage):
    """The agent's read of every account (triage.json: segment, one line on what it does, or a reason to drop it)
    laid over the long list. Descriptions only help the ranking; copy is written from sourced enrichment."""
    tri = (triage or {}).get("accounts") or {}
    kept, dropped = [], []
    for a in longlist["accounts"]:
        t = tri.get(a["domain"]) or {}
        if t.get("drop"):
            dropped.append({"name": a["name"], "domain": a["domain"], "reason": f"triage: {t['drop']}"})
            continue
        a = dict(a)
        if t.get("description") and not a.get("short_description"):
            a["short_description"] = t["description"]
        if "segment" in t:
            a["segment_key"] = t["segment"]
        kept.append(a)
    return dict(longlist, accounts=kept, triage_dropped=dropped)


def _with_enrichment(a, enr):
    """Enrichment (sourced) wins over triage for the description and the segment."""
    if not enr:
        return a
    a = dict(a)
    if enr.get("description"):
        a["short_description"] = enr["description"]
    if enr.get("segment"):
        a["segment_key"] = enr["segment"]
    return a


def segment_of(a, icp):
    """The segment the triage or enrichment set, else the best match by keyword hits, else None."""
    if "segment_key" in a:
        return next((s for s in icp.get("segments") or [] if s["key"] == a["segment_key"]), None)
    text, best, best_n = _text(a), None, 0
    for s in icp.get("segments") or []:
        n = len(_hits(text, s.get("keywords") or []))
        if n > best_n:
            best, best_n = s, n
    return best


def firmographic(a, icp):
    sc, reasons = 0.0, []
    emp = (icp.get("scoring") or {}).get("employees") or {}
    n = a.get("employees")
    if n is None:
        reasons.append("size unknown (0)")
    elif emp.get("min", 0) <= n <= emp.get("max", 10 ** 9):
        sc += 2
        reasons.append(f"{n} employees in range (+2)")
        if emp.get("sweet_min", emp.get("min", 0)) <= n <= emp.get("sweet_max", emp.get("max", 10 ** 9)):
            sc += 1
            reasons.append("in the sweet spot (+1)")
    else:
        sc -= 4
        reasons.append(f"{n} employees out of range (-4)")

    geos = [g.lower() for g in (icp.get("scoring") or {}).get("geo_countries") or []]
    country = (a.get("country") or "").lower()
    if geos and country:
        if country in geos:
            sc += 1
            reasons.append(f"HQ in {a.get('country')} (+1)")
        else:
            sc -= 2
            reasons.append(f"HQ in {a.get('country')} (-2)")

    if a.get("publicly_traded"):
        sc += _cfg(icp, "public_company")
        reasons.append(f"public company ({_cfg(icp, 'public_company'):+})")

    text = _text(a)
    kw = (icp.get("scoring") or {}).get("keyword_weights") or {}
    hit = _hits(text, list(kw))
    kws = min(sum(kw[h] for h in hit), _cfg(icp, "keyword_cap"))
    if hit:
        sc += kws
        reasons.append(f"keywords {', '.join(hit[:5])} (+{kws:g})")
    neg = (icp.get("scoring") or {}).get("negative_keywords") or {}
    for h in _hits(text, list(neg)):
        sc += neg[h]
        reasons.append(f"'{h}' ({neg[h]:+g})")

    seg = segment_of(a, icp)
    if seg is None:
        sc -= 1
        reasons.append("no ICP segment matched (-1)")

    g = a.get("headcount_growth_6m")
    if g is not None:
        pts = next((p for t, p in _cfg(icp, "growth_6m_points") if g >= t), 0)
        if g < 0:
            pts = _cfg(icp, "growth_negative")
        if pts:
            sc += pts
            reasons.append(f"headcount {g:+d}% in 6 months ({pts:+g})")

    d = parse_date(a.get("latest_funding_date"))
    if d:
        age = (today() - d).days
        if 0 <= age <= _cfg(icp, "trigger_max_age_days"):
            sc += 2
            reasons.append(f"funding round {d.isoformat()} (+2)")
        elif 0 <= age <= 365:
            sc += 1
            reasons.append(f"funding round {d.isoformat()} (+1)")
    return sc, reasons, seg


def valid_triggers(enr, icp):
    """Triggers with a source URL, dated in the window and not in the future, newest first."""
    out = []
    for t in (enr or {}).get("triggers") or []:
        d = parse_date(t.get("date"))
        if not d or not t.get("url"):
            continue
        age = (today() - d).days
        if 0 <= age <= _cfg(icp, "trigger_max_age_days"):
            out.append(dict(t, date=d.isoformat(), age_days=age))
    return sorted(out, key=lambda t: t["age_days"])


def _recency(age):
    return 1.0 if age <= 45 else 0.8 if age <= 90 else 0.6


def signals(a, enr, icp):
    sc, reasons = 0.0, []
    enr = enr or {}
    tw = (icp.get("scoring") or {}).get("trigger_weights") or {}
    trig = valid_triggers(enr, icp)
    best = None
    if trig:
        best = max(trig, key=lambda t: tw.get(t.get("type"), tw.get("other", 1)) * _recency(t["age_days"]))
        pts = tw.get(best.get("type"), tw.get("other", 1)) * _recency(best["age_days"])
        extra = min(0.5 * (len(trig) - 1), 1.0)
        sc += pts + extra
        reasons.append(f"trigger: {best.get('type')} {best['date']} (+{pts + extra:g})")
    else:
        reasons.append("no dated trigger in the window")

    tech = {t.lower() for t in (a.get("technologies") or []) + (enr.get("tech") or [])}
    techw = (icp.get("scoring") or {}).get("tech_weights") or {}
    th = [t for t in techw if t.lower() in tech]
    if th:
        pts = min(sum(techw[t] for t in th), _cfg(icp, "tech_cap"))
        sc += pts
        reasons.append(f"tech {', '.join(th[:4])} (+{pts:g})")

    comm = [c for c in _cfg(icp, "community_channels") if c in {x.lower() for x in enr.get("community") or []}]
    if comm:
        pts = min(len(comm), 2)
        sc += pts
        reasons.append(f"community on {', '.join(comm)} (+{pts})")

    ai = (enr.get("ai_assistant") or "unknown").lower()
    if ai == "yes":
        sc += _cfg(icp, "has_ai_assistant")
        reasons.append(f"already runs an AI assistant ({_cfg(icp, 'has_ai_assistant'):+})")
    elif ai == "no":
        sc += _cfg(icp, "no_ai_assistant")
        reasons.append(f"no AI assistant on the docs ({_cfg(icp, 'no_ai_assistant'):+})")

    hk = [h.lower() for h in _cfg(icp, "hiring_keywords")]
    roles = [r for r in enr.get("hiring") or [] if any(k in r.lower() for k in hk)]
    if roles:
        pts = min(len(roles), 2)
        sc += pts
        reasons.append(f"hiring {', '.join(roles[:2])} (+{pts})")

    fs = enr.get("fit_score")
    if isinstance(fs, (int, float)):
        sc += fs / 25
        reasons.append(f"Clay fit score {fs:g} (+{fs / 25:g})")
    return sc, reasons, best


def merge_enrichment(parts, source, enriched_on):
    """Enrichment batches (one file per research agent or Clay export) -> one enrichment.json.
    A batch may name the existing-customer flag after the client (acme_customer); it becomes existing_customer."""
    accounts = {}
    for part in parts:
        for dom, e in ((part or {}).get("accounts") or {}).items():
            e = dict(e)
            for k in [k for k in e if k.endswith("_customer") and k != "existing_customer"]:
                e["existing_customer"] = bool(e.pop(k)) or bool(e.get("existing_customer"))
            e["community"] = [str(x).lower() for x in e.get("community") or []]
            e["tech"] = [str(x).lower() for x in e.get("tech") or []]
            accounts[dom.lower()] = e
    return {"source": source, "enriched_on": enriched_on, "accounts": accounts}


def _industry_label(a, seg):
    if seg:
        return seg["label"]
    return (a.get("industry") or "—").title()


def rank_pre(longlist, icp):
    rows = []
    for a in longlist["accounts"]:
        sc, reasons, seg = firmographic(a, icp)
        rows.append({"name": a["name"], "domain": a["domain"], "segment": seg["key"] if seg else None,
                     "industry": _industry_label(a, seg), "employees": a.get("employees"),
                     "score": round(sc, 2), "reasons": reasons})
    rows.sort(key=lambda r: (-r["score"], r["name"] or ""))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def rank_final(longlist, enrichment, icp):
    enr_all = (enrichment or {}).get("accounts") or {}
    rows = []
    for a in longlist["accounts"]:
        enr = enr_all.get(a["domain"])
        if (enr or {}).get("existing_customer"):
            continue   # their docs already run the product: a customer, not a prospect
        a = _with_enrichment(a, enr)
        f_sc, f_reasons, seg = firmographic(a, icp)
        s_sc, s_reasons, trig = signals(a, enr, icp)
        rows.append({"name": a["name"], "domain": a["domain"], "segment": seg["key"] if seg else None,
                     "industry": _industry_label(a, seg), "employees": a.get("employees"),
                     "hq": ", ".join(x for x in (a.get("city"), a.get("state") or a.get("country")) if x),
                     "linkedin_url": a.get("linkedin_url"), "apollo_org_id": a.get("apollo_org_id"),
                     "enriched": enr is not None, "score": round(f_sc + s_sc, 2),
                     "firmographic_score": round(f_sc, 2), "signal_score": round(s_sc, 2),
                     "reasons": f_reasons + s_reasons, "trigger": trig,
                     "ai_assistant": (enr or {}).get("ai_assistant", "unknown")})
    rows.sort(key=lambda r: (-r["score"], r["name"] or ""))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def cap(seg_cap, n):
    """A segment's seats when n accounts are picked: `max_in_top10` is written for 10, so 4 of 10 is 2 of 5."""
    return max(1, math.ceil(seg_cap * n / 10))


def pick_top(ranked, icp, n=c.LEADS):
    """Best n with a dated trigger, at most max_in_top10 per segment. Short of n: the next best with a trigger take
    the seats over their segment's cap (a full list beats the mix), then the best untriggered accounts, flagged
    needs_trigger, so the agent researches them or swaps them before going on."""
    caps = {s["key"]: s.get("max_in_top10", _cfg(icp, "segment_cap")) for s in icp.get("segments") or []}
    ready = [r for r in ranked if r.get("trigger") and r.get("enriched") and r.get("segment") is not None]
    used, top, picked = {}, [], set()
    for r in ready:
        if len(top) == n:
            break
        seg = r["segment"]
        if used.get(seg, 0) >= cap(caps.get(seg, _cfg(icp, "segment_cap")), n):
            continue
        used[seg] = used.get(seg, 0) + 1
        top.append(r)
        picked.add(r["domain"])
    for r in ready:
        if len(top) == n:
            break
        if r["domain"] not in picked:
            top.append(r)
            picked.add(r["domain"])
    order = {r["domain"]: i for i, r in enumerate(ranked)}
    top.sort(key=lambda r: order[r["domain"]])
    for r in ranked:
        if len(top) == n:
            break
        if r["domain"] not in picked and r.get("segment") is not None:
            top.append(dict(r, needs_trigger=True))
    lookalike = {s["key"]: s.get("lookalike") for s in icp.get("segments") or []}
    out = []
    for i, r in enumerate(top, 1):
        out.append({"rank": i, "name": r["name"], "domain": r["domain"], "industry": r["industry"],
                    "segment": r["segment"], "lookalike": lookalike.get(r["segment"]), "employees": r["employees"],
                    "hq": r.get("hq"), "linkedin_url": r.get("linkedin_url"), "apollo_org_id": r.get("apollo_org_id"),
                    "score": r["score"], "reasons": r["reasons"], "trigger": r.get("trigger"),
                    "needs_trigger": bool(r.get("needs_trigger")),
                    "why": "", "trigger_text": ""})
    return out


def check_top10(top, company_name, customer_names=None):
    """Problems with the written top10.json (why / trigger_text / lookalike), empty when it is ready for the champions
    stage. The lookalike starts as the segment's and may be set per account, but must be a real customer."""
    out = []
    customers = {n.lower() for n in customer_names or []}
    if len(top) != c.LEADS:
        out.append(f"top10 has {len(top)} accounts, needs {c.LEADS}")
    for a in top:
        tag = f"#{a.get('rank')} {a.get('name')}"
        if a.get("needs_trigger") or not a.get("trigger"):
            out.append(f"{tag}: no dated trigger with a URL (research one or swap the account)")
        why, trig = (a.get("why") or "").strip(), (a.get("trigger_text") or "").strip()
        if not why:
            out.append(f"{tag}: why is empty")
        elif len(why.split()) > 30:
            out.append(f"{tag}: why is {len(why.split())} words, keep it to one short sentence")
        if customers and a.get("lookalike") and a["lookalike"].lower() not in customers:
            out.append(f"{tag}: lookalike {a['lookalike']!r} is not in icp.json customers")
        if a.get("lookalike") and why and a["lookalike"] not in why:
            out.append(f"{tag}: why should name the lookalike customer, e.g. 'just like {a['lookalike']} ({company_name}'s customer)'")
        if not trig:
            out.append(f"{tag}: trigger_text is empty")
        elif not re.search(r"\b20\d\d\b", trig):
            out.append(f"{tag}: trigger_text needs its date, e.g. 'on 14 Oct 2026'")
        if re.search(r"\bpatterns?\b|—|~\$", why + " " + trig, re.I):
            out.append(f"{tag}: why/trigger_text uses 'pattern', an em dash or '~$'")
    return out
