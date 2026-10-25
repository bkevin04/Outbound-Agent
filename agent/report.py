"""Stage 10: the deliverable. leads.csv (the leads with champion emails), report.md (the funnel in numbers,
the accounts picked, credits, campaign state) and accounts.xlsx (the picks, the shortlist researched and all 100 from Apollo, with
how each one was scored and how far it got), plus emails.docx and report.docx for Word. All of them stay in the run
folder, which git ignores: they hold real emails."""
import csv
import io
import os
import re
from collections import Counter

from . import common as c, docx, score as sc, xlsx
from .common import norm_domain

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
        f"**{len(table)} leads, {emails} with a champion email.** Campaign: {(lemlist_result or {}).get('campaign_name', 'not built yet')}"
        f" ({(lemlist_result or {}).get('status', '—')}).", "",
        "## The funnel in numbers", "",
        f"- Apollo search: {longlist.get('searched')} companies",
        f"- Dropped: {sum(drops.values())} ({', '.join(f'{n} {r}' for r, n in drops.most_common()) or 'none'})",
        f"- Long list kept: {longlist.get('kept')}",
        f"- Dropped on a read of each account: {len(triage_dropped)}"
        + (f" ({', '.join(d['name'] for d in triage_dropped)})" if triage_dropped else ""),
        f"- Shortlisted on firmographics: {len(shortlist or [])}",
        f"- Enriched with {(enrichment or {}).get('source', '—')}: {len(enr)}, {with_trigger} with a dated trigger",
        f"- Top {len(table)} by fit score, at most a few per segment", "",
        f"## The {len(table)} accounts", "",
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


SOURCES = {"clay": "Clay", "web": "Web research", "clay+web": "Clay and web research"}


def _plain(html):
    return re.sub(r"<[^>]+>", "", html or "")


def _hq(a):
    return ", ".join(x for x in (a.get("city"), a.get("state") or a.get("country")) if x)


def _source(url):
    return xlsx.link(norm_domain(url), url) if url else ""


def _site(domain):
    return xlsx.link(domain, f"https://{domain}") if domain else ""


def _join(values):
    return ", ".join(str(v) for v in values or [])


def _uses(e):
    ai, vendor = (e or {}).get("ai_assistant") or "", (e or {}).get("ai_assistant_vendor")
    return f"{ai} ({vendor})" if ai == "yes" and vendor else ai


def workbook(folder):
    """The run as a workbook people can read and filter: the picks in the accounts table (#, Account, Type, Why they fit,
    Trigger, Champion, Contact, then the detail), the shortlist researched, every company Apollo found with how far it got
    and why, and how they were picked. Read from the run folder's files. -> the sheets for xlsx.write"""
    def load(name, default):
        return c.load_optional(os.path.join(folder, name), default) or default

    meta, icp, longlist, sl = load(c.RUN, {}), load(c.ICP, {}), load(c.LONGLIST, {}), load(c.SHORTLIST, {})
    enrichment, ranked, top = load(c.ENRICHMENT, {}), load(c.RANKED, []), load(c.TOP10, [])
    champions, lem = load(c.CHAMPIONS, {"accounts": {}}), load(c.LEMLIST_RESULT, {})
    tri, enr = load(c.TRIAGE, {}).get("accounts") or {}, enrichment.get("accounts") or {}
    company = (icp.get("company") or {}).get("name", "the company")
    accounts = {a["domain"]: a for a in longlist.get("accounts") or []}
    pre = {r["domain"]: r for r in sl.get("ranked") or []}
    short = list(sl.get("shortlist") or [])
    final = {r["domain"]: r for r in ranked}
    picked = {t["domain"]: t for t in top}
    segments = icp.get("segments") or []
    caps = {s["key"]: sc.cap(s.get("max_in_top10", sc._cfg(icp, "segment_cap")), c.LEADS) for s in segments}
    labels = {s["key"]: s.get("label") or s["key"] for s in segments}
    worst = max((final[d]["rank"] for d in picked if d in final), default=None)

    def outcome(d):
        """How far an account got and why it stopped there. -> (text, row mark)"""
        if d in picked:
            return (f"Picked, #{picked[d]['rank']}"
                    + (" (still needs a dated trigger)" if picked[d].get("needs_trigger") else "")), "pick"
        drop = (tri.get(d) or {}).get("drop")
        if drop:
            return (f"Out after research: {drop}" if d in short else f"Out on the read: {drop}"), "out"
        if d not in short:
            p = pre.get(d)
            return (f"Not shortlisted: #{p['rank']} of {len(pre)} on firmographics" if p else "Not shortlisted"), None
        if d not in enr:
            return "Shortlisted, not researched", None
        if enr[d].get("existing_customer"):
            return f"Out after research: already uses {company}", "out"
        r = final.get(d) or {}
        if not r.get("trigger"):
            return "Researched: no dated trigger in the last 6 months", None
        if r.get("segment") is None:
            return "Researched: fits no ICP segment", None
        if worst is not None and r["rank"] < worst:
            seg = r["segment"]
            return f"Researched: {labels.get(seg, seg)} already had its {caps.get(seg)} seats in the {c.LEADS}", None
        return f"Researched: scored below the {c.LEADS} (#{r['rank']} overall)", None

    def describe(d):
        return ((enr.get(d) or {}).get("description") or (tri.get(d) or {}).get("description")
                or (accounts.get(d) or {}).get("short_description") or "")

    # The picks: the accounts table, then the detail behind each line.
    ten, table = [], rows(top, champions, lem)
    for r, t in zip(table, top):
        champ = f"{r['champion']} ({r['champion_title']})" if r["champion"] and r["champion_title"] else r["champion"]
        ten.append([r["rank"], r["account"], r["industry"], r["why_they_fit"], r["trigger"],
                    champ or "no champion found", r["champion_email"], r["email_status"], r["trigger_date"],
                    _source(r["trigger_url"]), xlsx.link("LinkedIn", r["champion_linkedin"]) if r["champion_linkedin"] else "",
                    _site(r["domain"]), r["employees"], r["hq"], t.get("lookalike") or "", r["fit_score"],
                    "; ".join(t.get("reasons") or []), r["lemlist"]])

    # The shortlist, in the order of the final score; the ones left out after research go last.
    thirty, thirty_marks = [], {}
    for i, d in enumerate(sorted(short, key=lambda d: (0, final[d]["rank"]) if d in final
                                 else (1, (pre.get(d) or {}).get("rank", 10 ** 6))), 1):
        a, e, r, p = accounts.get(d) or {}, enr.get(d) or {}, final.get(d) or {}, pre.get(d) or {}
        trig = r.get("trigger") or next(iter(sc.valid_triggers(e, icp)), None) or {}
        text, mark = outcome(d)
        if mark:
            thirty_marks[i - 1] = mark
        thirty.append([i, a.get("name") or p.get("name") or d, r.get("industry") or p.get("industry") or "",
                       describe(d), text, trig.get("text") or "", trig.get("date") or "", _source(trig.get("url")),
                       _uses(e), _join(e.get("tech")), _join(e.get("community")), _join(e.get("hiring")),
                       r.get("firmographic_score", p.get("score")), r.get("signal_score"), r.get("score"),
                       "; ".join(r.get("reasons") or p.get("reasons") or []), _site(d)])

    # Every company the search found, from the picks down to the ones left out before any scoring.
    def stage(d):
        if d in picked:
            return 0, picked[d]["rank"]
        if d in short:
            return 1, short.index(d)
        if d in pre:
            return 2, pre[d]["rank"]
        return 3, 0

    hundred, hundred_marks = [], {}
    for d in sorted(accounts, key=lambda d: (stage(d), accounts[d].get("name") or d)):
        a, p = accounts[d], pre.get(d) or {}
        text, mark = outcome(d)
        if mark:
            hundred_marks[len(hundred)] = mark
        g = a.get("headcount_growth_6m")
        hundred.append([len(hundred) + 1, a.get("name") or d, p.get("industry") or (a.get("industry") or "").title(),
                        describe(d), text, a.get("employees"), _hq(a), f"{g:+d}%" if isinstance(g, int) else "",
                        a.get("industry") or "", p.get("score"), "; ".join(p.get("reasons") or []), _site(d),
                        xlsx.link("LinkedIn", a.get("linkedin_url")) if a.get("linkedin_url") else ""])
    for x in longlist.get("dropped") or []:
        hundred_marks[len(hundred)] = "out"
        hundred.append([len(hundred) + 1, x.get("name") or x.get("domain"), "", "",
                        f"Out at the long list: {x.get('reason')}", "", "", "", "", "", "", _site(x.get("domain"))])

    searched = longlist.get("searched") or len(hundred)
    sheets = [
        {"name": f"Top {len(top)}", "freeze_cols": 2, "rows": ten,
         "header": ["#", "Account", "Type", "Why they fit", "Trigger", "Champion", "Contact", "Email status",
                    "Trigger date", "Trigger source", "Champion's LinkedIn", "Website", "Employees", "HQ",
                    "Closest customer", "Score", "How it scored", "lemlist"],
         "widths": [4, 18, 18, 46, 40, 30, 28, 12, 11, 18, 12, 18, 10, 18, 16, 7, 60, 10]},
        {"name": f"Top {len(short)}", "freeze_cols": 2, "rows": thirty, "marks": thirty_marks,
         "header": ["#", "Account", "Type", "What they do", "Result", "Best trigger", "Trigger date", "Trigger source",
                    f"Already uses a tool like {company}", "Tech", "Community", "Hiring", "Firmographic score",
                    "Signal score", "Total score", "How it scored", "Website"],
         "widths": [4, 18, 18, 36, 34, 40, 11, 18, 16, 22, 16, 24, 12, 9, 9, 60, 18]},
        {"name": f"All {searched}", "freeze_cols": 2, "rows": hundred, "marks": hundred_marks,
         "header": ["#", "Account", "Type", "What they do", "How far it got", "Employees", "HQ", "Headcount, 6 months",
                    "Apollo industry", "Firmographic score", "How it scored", "Website", "LinkedIn"],
         "widths": [4, 20, 18, 40, 40, 10, 18, 11, 18, 12, 60, 18, 10]},
        {"name": "How they were picked", "widths": [30, 12, 100], "rows": _how(meta, icp, longlist, sl, enrichment,
                                                                                top, table, short, caps, company)},
    ]
    return sheets


def _how(meta, icp, longlist, sl, enrichment, top, table, short, caps, company):
    """The thought process: the funnel step by step, the scoring rules with this ICP's weights, the segments, the ICP."""
    Cell = xlsx.Cell
    cfg = lambda k: sc._cfg(icp, k)   # noqa: E731
    scoring = icp.get("scoring") or {}
    enr = enrichment.get("accounts") or {}
    tri_dropped = sl.get("triage_dropped") or []
    dropped = Counter(d["reason"] for d in longlist.get("dropped") or [])
    search = icp.get("apollo_search") or {}
    filters = [f"HQ in {_join(search['organization_locations'])}" if search.get("organization_locations") else "",
               "employees " + _join(r.replace(",", " to ") for r in search["organization_num_employees_ranges"])
               if search.get("organization_num_employees_ranges") else "",
               f"keywords {_join(search['q_organization_keyword_tags'])}" if search.get("q_organization_keyword_tags") else ""]
    filters = "; ".join(f for f in filters if f)
    with_trigger = sum(1 for e in enr.values() if sc.valid_triggers(e, icp))
    customers = sum(1 for e in enr.values() if e.get("existing_customer"))
    credits = meta.get("credits") or {}
    titles = ((icp.get("champion") or {}).get("titles") or [])[:3]
    funnel = [
        ["1. Apollo company search", longlist.get("searched"),
         f"One search with the ICP's filters ({filters or 'see icp.json'}) brought back lookalike companies."],
        ["2. Long list", longlist.get("kept"),
         "Left out " + (_join(f"{n} {r}" for r, n in dropped.most_common()) or "nothing") + "."],
        ["3. Read every account", len(sl.get("ranked") or []),
         f"Claude read what each company sells. {len(tri_dropped)} did not fit"
         + (": " + "; ".join(f"{d['name']} ({d['reason'].removeprefix('triage: ')})" for d in tri_dropped[:12])
            + ("; ..." if len(tri_dropped) > 12 else "") if tri_dropped else "")
         + ". Every other account got an ICP segment."],
        [f"4. Shortlist (the Top {len(short)} tab)", len(short),
         "Ranked on firmographics: size in the ICP's range, HQ, keywords, segment, headcount growth over 6 months and "
         "recent funding. The best went on to research."],
        ["5. Research", len(enr),
         f"{SOURCES.get(enrichment.get('source'), 'Research')} on each: a trigger from the last 6 months with its date "
         f"and source, tech, community, whether they already use a tool like {company}, open roles. "
         f"{with_trigger} had a dated trigger" + (f", {customers} turned out to use {company} already" if customers else "")
         + "."],
        [f"6. The final {len(top)} (the Top {len(top)} tab)", len(top),
         f"The best total score with a dated trigger, at most {max(caps.values(), default=cfg('segment_cap'))} per "
         f"segment, so the list mixes segments. Then Claude wrote why each one fits, naming the closest {company} "
         "customer, and the trigger line."],
        ["7. Champions", sum(1 for r in table if r["champion_email"]),
         f"Apollo people search at each account for the buyer ({_join(titles)}), then one email reveal per champion. "
         f"Apollo credits this run: {sum(v for v in credits.values() if isinstance(v, (int, float))) or 'see run.json'}."],
    ]
    emp = scoring.get("employees") or {}
    kw, neg = scoring.get("keyword_weights") or {}, scoring.get("negative_keywords") or {}
    tw, techw = scoring.get("trigger_weights") or {}, scoring.get("tech_weights") or {}
    age = cfg("trigger_max_age_days")
    rules = [
        ["Size", "", f"{emp.get('min')} to {emp.get('max')} employees +2, {emp.get('sweet_min', emp.get('min'))} to "
                     f"{emp.get('sweet_max', emp.get('max'))} +1 more, out of range -4."],
        ["HQ", "", f"In {_join(scoring.get('geo_countries')) or 'the target countries'} +1, elsewhere -2. A public company {cfg('public_company'):+g}."],
        ["Keywords", "", (_join(f"{k} +{w:g}" for k, w in kw.items()) + f", at most +{cfg('keyword_cap')} in all."
                          if kw else "none set") + (" Against: " + _join(f"{k} {w:+g}" for k, w in neg.items()) + "." if neg else "")],
        ["Headcount growth", "", "Over 6 months: " + _join(f"{t}% or more +{p}" for t, p in cfg("growth_6m_points"))
                                 + f", shrinking {cfg('growth_negative'):+g}."],
        ["Funding", "", f"A round in the last {age} days +2, in the last year +1."],
        ["Trigger", "", f"The best dated trigger by type ({_join(f'{k} {v:g}' for k, v in tw.items())}) times how recent "
                        f"it is (45 days or less x1, 90 days x0.8, {age} days x0.6), plus 0.5 per extra trigger, at most "
                        f"+1. No dated trigger with a source, no seat in the {c.LEADS}."],
        ["Tech", "", (_join(f"{k} +{w:g}" for k, w in techw.items()) + f", at most +{cfg('tech_cap')}.") if techw else "none set"],
        ["Community", "", f"+1 per channel ({_join(cfg('community_channels'))}), at most +2."],
        [f"Already uses a tool like {company}", "", f"Yes {cfg('has_ai_assistant'):+g}, checked and no {cfg('no_ai_assistant'):+g}."],
        ["Hiring", "", f"+1 per open role that matters ({_join(cfg('hiring_keywords')) or 'none set'}), at most +2."],
        ["Clay fit score", "", "Clay's fit score divided by 25, when Clay gives one."],
    ]
    out = [[Cell(f"{company}: how the {len(top)} accounts were picked", "title")],
           [Cell(f"Run of {meta.get('date', '')}. Each tab is one step back: the Top {len(top)}, the {len(short)} "
                 f"researched, then all {longlist.get('searched')} from Apollo with how far each one got.", "note")],
           [], [Cell("The funnel", "bold")],
           [Cell("Step", "header"), Cell("Accounts", "header"), Cell("What happened", "header")]] + funnel
    out += [[], [Cell("How the score works", "bold")],
            [Cell("Part", "header"), Cell("", "header"), Cell("The rule, with this ICP's weights", "header")]] + rules
    out += [[], [Cell("The segments", "bold")],
            [Cell("Segment", "header"), Cell(f"Most in the {c.LEADS}", "header"), Cell("Keywords and closest customer", "header")]]
    out += [[s.get("label") or s["key"], caps.get(s["key"]),
             f"{_join(s.get('keywords'))}. Closest customer: {s.get('lookalike') or 'none'}."]
            for s in icp.get("segments") or []]
    i = icp.get("icp") or {}
    out += [[], [Cell("The ICP", "bold")], ["Who", "", _plain(i.get("headline"))]]
    out += [["Firmographics", "", f] for f in i.get("firmographics") or []]
    if i.get("pain_quote") or i.get("pain_body"):
        out.append(["The pain", "", " ".join(x for x in (i.get("pain_quote"), i.get("pain_body")) if x)])
    if meta.get("notes"):
        out += [[], [Cell("To check", "bold")]] + [["", "", n] for n in meta["notes"]]
    return out


def write_workbook(folder):
    path = os.path.join(folder, c.ACCOUNTS_XLSX)
    xlsx.write(path, workbook(folder))
    return path


def write_docs(folder):
    """emails.docx (one account per page) and report.docx from their Markdown, for Word. -> the paths written"""
    out = []
    for md, doc, page in ((c.EMAILS, c.EMAILS_DOCX, 2), (c.REPORT, c.REPORT_DOCX, None)):
        src = os.path.join(folder, md)
        if os.path.exists(src):
            text = c.read_text(src)
            title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("# ")), md)
            out.append(docx.write(os.path.join(folder, doc), text, title=title, break_before=page))
    return out
