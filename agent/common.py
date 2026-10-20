"""Shared helpers: paths, slugs, domains, dates and JSON I/O for the outbound agent."""
import datetime
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, "runs")
COMPANIES = os.path.join(ROOT, "companies")

# Run-folder file names, in the order the stages write them.
RUN = "run.json"
ICP = "icp.json"
PITCHED = "already_pitched.json"
LONGLIST = "longlist.json"
TRIAGE = "triage.json"
SHORTLIST = "shortlist.json"
ENRICHMENT = "enrichment.json"
RANKED = "ranked.json"
TOP10 = "top10.json"
CHAMPIONS = "champions.json"
SEQUENCE = "sequence.json"
EMAILS = "emails.md"
LEMLIST_STEPS = "lemlist_steps.json"
LEMLIST_LEADS = "lemlist_leads.json"
LEMLIST_RESULT = "lemlist_result.json"
LEADS_CSV = "leads.csv"
REPORT = "report.md"


def slugify(name):
    """Company name -> slug: Acme.dev -> acmedev, Northwind Labs -> northwindlabs."""
    s = re.sub(r"[\s.]+", "", name.strip().lower())
    s = re.sub(r"[^a-z0-9-]", "", s)
    if not s:
        raise SystemExit(f"cannot make a slug from {name!r}")
    return s


def norm_domain(value):
    """URL or domain -> bare lowercase domain: https://www.Example.com/docs -> example.com."""
    if not value:
        return ""
    d = str(value).strip().lower()
    d = re.sub(r"^[a-z]+://", "", d)
    d = d.split("/")[0].split("?")[0].split("#")[0]
    d = d.split(":")[0]
    if d.startswith("www."):
        d = d[4:]
    return d


ENV = os.path.join(ROOT, ".env")


def load_env(path=ENV):
    """KEY=VALUE lines from .env (git-ignored) into the environment, without overriding what is already set."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            k, sep, v = line.strip().partition("=")
            if sep and k and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def today():
    override = os.environ.get("OUTBOUND_TODAY")
    return datetime.date.fromisoformat(override) if override else datetime.date.today()


def parse_date(value):
    """'2026-10-14', '2026-10-14T09:00:00Z', '2026-10' or '14 Oct 2026' -> date (first of month when no day)."""
    if not value:
        return None
    if isinstance(value, datetime.date):
        return value
    s = str(value).strip()
    m = re.match(r"(\d{4})-(\d{2})(?:-(\d{2}))?", s)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    for fmt in ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %Y", "%B %Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def run_dir(slug, date=None):
    return os.path.join(RUNS, slug, (date or today()).isoformat())


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def load_optional(path, default=None):
    return load(path) if os.path.exists(path) else default


def extract_json(text, want):
    """Find the first JSON object in text (a raw MCP tool result, possibly wrapped) for which want(obj) is true.

    Tool results come back as plain JSON, as JSON inside a {"type": "text", "text": "..."} wrapper,
    or with a line of prose in front. This walks every '{' / '[' and tries to decode from there."""
    dec = json.JSONDecoder()

    def search(obj):
        if want(obj):
            return obj
        if isinstance(obj, dict):
            if isinstance(obj.get("text"), str):
                found = extract_json(obj["text"], want)
                if found is not None:
                    return found
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    found = search(v)
                    if found is not None:
                        return found
        elif isinstance(obj, list):
            for v in obj:
                found = search(v)
                if found is not None:
                    return found
        return None

    i = 0
    while i < len(text):
        if text[i] in "{[":
            try:
                obj, end = dec.raw_decode(text, i)
            except ValueError:
                i += 1
                continue
            found = search(obj)
            if found is not None:
                return found
            i = end
        else:
            i += 1
    return None
