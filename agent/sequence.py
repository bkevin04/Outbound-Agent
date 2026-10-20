"""Stage 8 and 9: the 3-touch sequence, rendered per lead, linted, and turned into lemlist steps and leads.

sequence.json holds one template per touch (Kevin's structure: Day 1 opens with the trigger,
Day 3 tries a new angle with a proof story, Day 5 closes the loop) plus, per account domain, the lemlist custom
variables the template uses ({{t1Subject}}, {{t1Opener}}, ...). The same template becomes the lemlist steps, so
lemlist personalises every email from the lead's own variables and nothing is pasted per lead by hand."""
import html
import re

from .voice import lint

# Email 1 as soon as the campaign is launched, email 2 two days later, email 3 two days after that.
TOUCH_DAYS = (1, 3, 5)

VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
STANDARD = ("firstName", "lastName", "companyName", "jobTitle")


def set_days(seq):
    """The touches' days and labels from TOUCH_DAYS, whatever the writer put there: the timing is a rule, not copy."""
    for i, (t, day) in enumerate(zip(seq.get("touches") or [], TOUCH_DAYS), 1):
        t["day"], t["label"] = day, f"TOUCH {i}: DAY {day}"
    return seq


def is_note(p):
    """A bracketed note ('[I would attach a 30 second video ...]') is for Kevin's page, not the sent email."""
    return p.strip().startswith("[") and p.strip().endswith("]")


def lead_vars(account, champion, seq):
    v = {"firstName": champion.get("first_name") or "", "lastName": champion.get("last_name") or "",
         "companyName": account["name"], "jobTitle": champion.get("title") or ""}
    v.update((seq.get("leads") or {}).get(account["domain"]) or {})
    return v


def fill(template, values, where):
    missing = []

    def sub(m):
        k = m.group(1)
        if k not in values or values[k] in (None, ""):
            missing.append(k)
            return m.group(0)
        return str(values[k])
    out = VAR.sub(sub, template)
    if missing:
        raise SystemExit(f"{where}: no value for {', '.join(sorted(set(missing)))}")
    return out


def template_vars(seq):
    names = set()
    for t in seq["touches"]:
        for s in [t["subject"]] + t["paragraphs"]:
            names.update(VAR.findall(s))
    return names - set(STANDARD)


def render(seq, top, champions):
    """-> list of {account, champion, emails: [{label, angle, subject, paragraphs}]} for every account with a champion."""
    if len(seq.get("touches") or []) != 3:
        raise SystemExit("sequence.json needs exactly 3 touches")
    out = []
    for a in top:
        ch = (champions.get("accounts") or {}).get(a["domain"])
        if not ch:
            continue
        vals = lead_vars(a, ch, seq)
        emails = []
        for t in seq["touches"]:
            where = f"{a['name']} / {t['label']}"
            emails.append({"label": t["label"], "angle": t.get("angle", ""), "day": t["day"],
                           "subject": fill(t["subject"], vals, where),
                           "paragraphs": [fill(p, vals, where) for p in t["paragraphs"]]})
        out.append({"account": a, "champion": ch, "emails": emails})
    return out


def lint_all(rendered, signature):
    errors, warnings = [], []
    for r in rendered:
        for e in r["emails"]:
            where = f"{r['account']['name']} / {e['label']}"
            paras = [p for p in e["paragraphs"] if not p.startswith("Hi ") and p != signature]
            body = "\n".join(p for p in paras if not is_note(p))
            for text, w in ((e["subject"], where + " subject"), (body, where)):
                er, wa = lint(text, w)
                errors += er
                warnings += wa
            for note in filter(is_note, paras):   # Kevin's own bracketed notes: only the never-write list applies
                errors += lint(note, where + " note")[0]
    return errors, warnings


def emails_md(rendered, seq):
    sig = seq.get("signature", "Kevin")
    lines = [f"# {seq['company']} outbound sequence", ""]
    for r in rendered:
        a, ch = r["account"], r["champion"]
        lines += [f"## {a['rank']}. {a['name']} to {ch.get('first_name')} {ch.get('last_name')} ({ch.get('title')})", ""]
        for e in r["emails"]:
            lines += [f"### {e['label']} · {e['angle']}", "", f"**Subject:** {e['subject']}", ""]
            lines += [p + "\n" for p in e["paragraphs"]] + [sig, ""]
    return "\n".join(lines)


def _html(paragraphs, signature):
    keep = [p for p in paragraphs if not is_note(p)] + [signature]
    return "<div><br></div>".join(f"<div>{html.escape(p, quote=False)}</div>" for p in keep)


def lemlist_steps(seq):
    """The 3 email steps for lemlist, variables left in for lemlist to fill. delay = days after the previous step."""
    steps, prev = [], None
    for t in seq["touches"]:
        steps.append({"type": "email", "label": t["label"], "delay": 0 if prev is None else t["day"] - prev,
                      "subject": t["subject"], "body": _html(t["paragraphs"], seq.get("signature", "Kevin"))})
        prev = t["day"]
    return steps


def lemlist_leads(seq, top, champions, run_date):
    """add_leads_to_campaign payload: only champions with an email, every custom variable as a string."""
    need = template_vars(seq)
    leads, skipped = [], []
    for a in top:
        ch = (champions.get("accounts") or {}).get(a["domain"])
        if not ch or not ch.get("email"):
            skipped.append({"account": a["name"], "reason": "no champion email"})
            continue
        own = (seq.get("leads") or {}).get(a["domain"]) or {}
        missing = sorted(k for k in need if not own.get(k))
        if missing:
            raise SystemExit(f"{a['name']}: sequence.json leads has no {', '.join(missing)}")
        custom = {k: str(own[k]) for k in sorted(need)}
        custom.update({"fitScore": f"{a['score']:g}", "triggerLine": a.get("trigger_text") or "",
                       "whyTheyFit": a.get("why") or "", "agentWeek": run_date})
        lead = {"email": ch["email"], "firstName": ch.get("first_name") or "", "lastName": ch.get("last_name") or "",
                "jobTitle": ch.get("title") or "", "companyName": a["name"], "companyDomain": a["domain"],
                "customVariables": custom}
        if ch.get("linkedin_url"):
            lead["linkedinUrl"] = ch["linkedin_url"]
        leads.append({k: v for k, v in lead.items() if v not in ("", None)})
    return leads, skipped
