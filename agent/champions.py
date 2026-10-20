"""Stage 7: turn Apollo's bulk match (the 10 revealed champions) into champions.json.

Each match is tied to its account by the organization's domain, falling back to the email's domain. A match whose
organization is not one of the 10 is the wrong person and is left out. An email on another domain than the account's
(Gravitee's people write from graviteesource.com) is kept when Apollo ties the person to the account, with a note."""
from .common import extract_json, norm_domain


def _is_match_result(obj):
    return isinstance(obj, dict) and "matches" in obj


def parse_raw(text):
    found = extract_json(text, _is_match_result)
    if found is None:
        raise SystemExit("no Apollo bulk match result (matches) found in the file")
    return found


def _https(url):
    return url.replace("http://", "https://", 1) if url else url


def build(text, top, old=None):
    """-> (champions dict, credits consumed, problems). old: an existing champions.json whose notes are kept."""
    res = parse_raw(text)
    domains = {a["domain"] for a in top}
    old_acc = (old or {}).get("accounts") or {}
    accounts, problems = dict(old_acc), []
    for m in res.get("matches") or []:
        if not m:
            continue
        org = m.get("organization") or {}
        email = (m.get("email") or "").strip()
        email_dom = email.split("@")[-1].lower() if "@" in email else ""
        dom = norm_domain(org.get("primary_domain") or org.get("website_url"))
        if dom not in domains:
            dom = email_dom if email_dom in domains else dom
        if dom not in domains:
            problems.append(f"{m.get('name')} ({m.get('title')}) works at {org.get('name')}, not one of the 10: left out")
            continue
        notes = []
        if email and email_dom != dom:
            notes.append(f"email on {email_dom}, Apollo ties the person to {dom}")
        if m.get("email_status") != "verified":
            notes.append(f"email status {m.get('email_status') or 'unknown'}")
        prev = old_acc.get(dom) or {}
        accounts[dom] = {
            "apollo_id": m.get("id"), "first_name": m.get("first_name") or "", "last_name": m.get("last_name") or "",
            "title": m.get("title") or "", "email": email, "email_status": m.get("email_status") or "",
            "linkedin_url": _https(m.get("linkedin_url")), "swapped": bool(prev.get("swapped")),
            "note": "; ".join(x for x in [prev.get("note")] + notes if x),
        }
    for a in top:
        if a["domain"] not in accounts:
            problems.append(f"{a['name']}: no champion matched")
    return {"accounts": accounts}, res.get("credits_consumed"), problems
