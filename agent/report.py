"""Stage 10: the deliverable. leads.csv (the 10 leads with champion emails) and report.md (the funnel in numbers,
the 10 accounts, credits, campaign state). Both stay in the run folder, which git ignores: they hold real emails."""
import csv
import io
from collections import Counter

FIELDS = ["rank", "account", "domain", "industry", "employees", "hq", "fit_score", "why_they_fit", "trigger",
          "trigger_date", "trigger_url", "champion", "champion_title", "champion_email", "email_status",
          "champion_linkedin", "champion_swapped", "lemlist"]


def rows(top, champions, lemlist_result):
    outcome = {r.get("email", "").lower(): r.get("outcome") for r in (lemlist_result or {}).get("results") or []}
    out = []
    for a in top:
        ch = (champions.get("accounts") or {}).get(a["domain"]) or {}
        t = a.get("trigger") or {}
        email = ch.get("email") or ""
        out.append({
            "rank": a["rank"], "account": a["name"], "domain": a["domain"], "industry": a["industry"],
            "employees": a.get("employees") or "", "hq": a.get("hq") or "", "fit_score": a["score"],
            "why_they_fit": a.get("why", ""), "trigger": a.get("trigger_text", ""), "trigger_date": t.get("date", ""),
            "trigger_url": t.get("url", ""),
            "champion": " ".join(x for x in (ch.get("first_name"), ch.get("last_name")) if x),
            "champion_title": ch.get("title", ""), "champion_email": email,
            "email_status": ch.get("email_status") or ("not found" if ch else "no champion"),
            "champion_linkedin": ch.get("linkedin_url", ""), "champion_swapped": "yes" if ch.get("swapped") else "",
            "lemlist": outcome.get(email.lower(), "not added" if email else ""),
        })
    return out


def to_csv(table):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS)
    w.writeheader()
    w.writerows(table)
    return buf.getvalue()


def _cell(s):
    return str(s).replace("|", "/").replace("\n", " ")


def to_md(run, icp, longlist, shortlist, enrichment, table, lemlist_result, triage_dropped=()):
    company = icp["company"]["name"]
    drops = Counter(d["reason"] for d in longlist.get("dropped") or [])
    enr = (enrichment or {}).get("accounts") or {}
    with_trigger = sum(1 for e in enr.values() if e.get("triggers"))
    emails = sum(1 for r in table if r["champion_email"])
    credits = run.get("credits") or {}
    lines = [
        f"# {company} outbound run, {run.get('date')}", "",
        f"**10 leads, {emails} with a champion email.** Campaign: {(lemlist_result or {}).get('campaign_name', 'not built yet')}"
        f" ({(lemlist_result or {}).get('status', '—')}).", "",
        "## The funnel in numbers", "",
        f"- Apollo search: {longlist.get('searched')} companies",
        f"- Dropped: {sum(drops.values())} ({', '.join(f'{n} {r}' for r, n in drops.most_common()) or 'none'})",
        f"- Long list kept: {longlist.get('kept')}",
        f"- Dropped on a read of each account: {len(triage_dropped)}"
        + (f" ({', '.join(d['name'] for d in triage_dropped)})" if triage_dropped else ""),
        f"- Shortlisted on firmographics: {len(shortlist or [])}",
        f"- Enriched with {(enrichment or {}).get('source', '—')}: {len(enr)}, {with_trigger} with a dated trigger",
        "- Top 10 by fit score, at most a few per segment", "",
        "## The 10 accounts", "",
        "| # | Account | Industry | Why they fit | Trigger | Champion | Email | lemlist |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in table:
        champ = f"{r['champion']} ({r['champion_title']})" if r["champion"] else "—"
        lines.append(f"| {r['rank']} | {_cell(r['account'])} | {_cell(r['industry'])} | {_cell(r['why_they_fit'])} | "
                     f"{_cell(r['trigger'])} | {_cell(champ)} | {r['champion_email'] or '—'} ({r['email_status']}) | {r['lemlist'] or '—'} |")
    lines += ["", "## Credits", ""]
    for k, v in credits.items():
        lines.append(f"- {k}: {v}")
    if run.get("notes"):
        lines += ["", "## Notes", ""] + [f"- {n}" for n in run["notes"]]
    return "\n".join(lines) + "\n"
