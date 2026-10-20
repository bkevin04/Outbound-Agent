"""What Claude is asked at each judgement step of the run. The rules are the skill's (SKILL.md), and the shapes and
Kevin's voice are read from the skill's own reference files, so the skill and the standalone agent never drift apart."""
import datetime
import json
import os

from .common import ROOT

REFS = os.path.join(ROOT, ".claude", "skills", "outbound-agent", "references")


def ref(name):
    with open(os.path.join(REFS, name), encoding="utf-8") as f:
        return f.read()


def _j(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def system(today):
    return f"""You are Kevin's outbound agent. For a company, you find the accounts that company should sell to and the champion at each one, and you write the outbound sequence as that company's BDR. A program runs the stages, calls Apollo and lemlist itself and checks every answer; you do the research, the reading and the writing, one step at a time.

Today is {today.isoformat()}.

Rules for every step:
- Facts only from a source. Never invent a customer, a number, a date, a person or a URL. A fact you cannot verify stays out: leave the field empty or "unknown".
- Dates are YYYY-MM-DD. A trigger counts only when it is dated in the last 183 days (on or after {(today - datetime.timedelta(days=183)).isoformat()}) and has the URL you found it at.
- No email addresses or phone numbers in anything you write. The program handles contact data.
- Every sentence a person reads (the ICP, why they fit, the trigger line, the emails) is in Kevin's voice, below.
- Answer with one JSON object in a ```json block once you are done searching. Nothing after it.

{ref("voice.md")}"""


def research(company, domain):
    return f"""Step: research {company} ({domain or "find the domain"}).

Search the web and read the company's own pages, case studies and customer pages. Facts with a URL on every claim, no copy. Find:
- The company: what the product does in one line, how it works in two plain sentences, named customers and case studies with numbers and quotes, pricing, founders (the CEO's first name), funding, the office city (for "We're in {{city}}", or remote), competitors with domains.
- ICP evidence: the segments they sell to, size and stage, geography, the titles that buy or champion it, tech that signals fit, the buyer's pains in the buyer's own words (a quote and its URL).
- The full public customer list with domains: they are left out of the prospects and are the "just like X" lookalikes.
- 3 to 6 proof points: a customer, who they are in a few words, a result with a number, the URL.
- Suggested Apollo company search filters: keyword tags, employee ranges (Apollo's "51,200" form), HQ locations, a total funding floor when it fits.

JSON keys: "company" (name in their own spelling, domain, one_liner, how_it_works, city, remote, ceo_first_name, founders, funding, pricing, sources), "customers" ([{{name, domain, segment, result, url}}]), "competitors" ([{{name, domain}}]), "proof_points" ([{{customer, who, result, url}}]), "icp_evidence" (segments, size, stage, geography, buyer_titles, tech_signals, pains), "apollo_hints"."""


def icp(company, slug, research_json, old_icp=None):
    old = ("\n\nThe last saved ICP is older than 30 days. Start from it and keep what the new research still supports:\n"
           + _j(old_icp)) if old_icp else ""
    return f"""Step: write icp.json for {company}.

The shape, with an example for a made-up company, Acme (keep the shape, write {company}'s own content):

{ref("icp.md").split("## enrichment.json")[0]}

The program checks:
- company: name (their own spelling), slug "{slug}", domain, one_liner, city (or "remote": true), ceo_first_name, sources.
- icp: headline (rich text, <em> and <mark> only, in the template's shape); firmographics: exactly 2 paragraphs, the second starts with "Second track:"; pain_quote and pain_body; check: exactly 2 paragraphs, the second starts with "They do not yet prove"; funnel: exactly 5 stages, each with title and body.
- segments: each with key, label, keywords (lowercase, the words company descriptions use), lookalike (a name from customers) and max_in_top10. Mix them the way the ICP does.
- apollo_search: the exact parameters of Apollo's company search, per_page 100 and page 1. One search has to bring back about 100 good lookalikes, so keep the keyword tags broad enough and let the scoring do the rest.
- scoring: as in the shape. has_ai_assistant / no_ai_assistant score whether an account already runs a product that does what {company} sells.
- champion: titles (the buyer the ICP names first), seniorities (Apollo values: owner, founder, c_suite, partner, vp, head, director, manager, senior, entry), fallback_titles.
- customers (name, domain, segment, result, url), competitors (name, domain), exclude ({{"domains": [], "why": {{}}}}), proof_points (customer, who, result, url).
- Never the word "pattern", no em dashes, no "@" anywhere: this file goes into a public repo.{old}

The research:
{_j(research_json)}"""


def triage(icp, accounts):
    c = icp["company"]["name"]
    rows = "\n".join(_j({k: a.get(k) for k in ("domain", "name", "industry", "employees", "country",
                                                 "short_description", "keywords", "headcount_growth_6m", "revenue")
                         if a.get(k) not in (None, "", [])}) for a in accounts)
    return f"""Step: triage the long list for {c}.

Apollo gives each account's name, domain, industry, size and growth, and little else. Read every account: use what you know, and search the web when you do not know what a company sells. For each one write either
- {{"segment": "<a segment key>", "description": "one line on what they sell"}}, or
- {{"drop": "<reason>"}} for a misfit: media, investors, recruiters, agencies, consumer products, anything {c}'s buyer would not run, a company that is now part of a bigger one ("part of IBM since 2025"), a close competitor of {c}, or an existing customer of {c} under another name.

When the list shows a segment the ICP needs and the segments lack, add it to "new_segments" (key, label, keywords, lookalike from the customers, max_in_top10) and use its key. Descriptions only rank the list; nobody reads them.

Every one of the {len(accounts)} domains gets an entry. JSON: {{"accounts": {{"<domain>": {{...}}}}, "new_segments": []}}

The ICP: {icp["icp"]["headline"]}
Segments: {_j([{k: s.get(k) for k in ("key", "label", "keywords", "lookalike")} for s in icp["segments"]])}
Customers: {", ".join(cu["name"] for cu in icp.get("customers") or [])}
Competitors: {", ".join(f'{co["name"]} ({co.get("domain")})' for co in icp.get("competitors") or [])}

The accounts:
{rows}"""


ENRICH_SHAPE = ref("icp.md").split("## enrichment.json")[1]


def enrich(icp, accounts, searches, today, clay=None):
    c = icp["company"]["name"]
    cutoff = (today - datetime.timedelta(days=183)).isoformat()
    hiring = ", ".join((icp.get("scoring") or {}).get("hiring_keywords") or [])
    rows = "\n".join(_j({"domain": a["domain"], "name": a["name"], "segment": a.get("segment")}) for a in accounts)
    clay_part = ("\n\nClay already ran on these accounts. Its data is below, keyed by domain: use it, check it, and keep "
                 "every rule above (a trigger still needs its date and a URL; a Clay fact without a source stays out of "
                 "anything a person reads). Search the web for what Clay left out.\n" + _j(clay)) if clay else ""
    return f"""Step: enrich {len(accounts)} accounts for {c}'s outbound (web research).

For each account, in this order:
1. triggers: events dated on or after {cutoff}. Each has type (funding, launch, new_leader, hiring, acquisition, partnership, expansion, other), date (YYYY-MM-DD, as published), text (one plain sentence) and url. No date or no URL: leave it out.
2. ai_assistant: "yes" when they already run a product that does what {c} sells ({icp["company"].get("one_liner", "")}), "no" when you checked and there is none, "unknown" otherwise; ai_assistant_vendor names it, ai_assistant_url is where you checked. existing_customer is true when that vendor is {c} itself.
3. description (what they sell, one line) and description_url; tech (docs platform, support and community tools, lowercase); community (discord, slack, forum, github discussions, discourse); hiring (open roles that matter: {hiring}) and hiring_url; hq_city; notes (short and factual, including anything that ties them to a competitor of {c}).

At most {searches} searches for all {len(accounts)} accounts: triggers first, then the assistant check, then the rest. A thin field only costs points, a missing trigger costs the account its seat. When a page will not load, work from the search results.

The shape:
{ENRICH_SHAPE}
JSON: {{"accounts": {{"<domain>": {{...}}}}}} with an entry for every domain below (source and enriched_on are filled in by the program).

{rows}{clay_part}"""


def trigger(icp, account, today):
    cutoff = (today - datetime.timedelta(days=183)).isoformat()
    return f"""Step: find one dated trigger for {account["name"]} ({account["domain"]}).

It is in {icp["company"]["name"]}'s top 10 but has no trigger yet. Search for news on or after {cutoff}: funding, a launch, a new leader, hiring, an acquisition, a partnership, an expansion. Each trigger needs type, date (YYYY-MM-DD), text (one plain sentence) and url. None in the window: answer an empty list.

JSON: {{"triggers": []}}"""


def write_top10(icp, accounts):
    c = icp["company"]["name"]
    customers = [{k: cu.get(k) for k in ("name", "segment", "result")} for cu in icp.get("customers") or []]
    return f"""Step: the 10 accounts for {c}. For each account below, either
- leave it out: {{"exclude": "<reason>"}} when its notes tie it to a competitor of {c} (the same team runs a competitor, say), it is already a customer, or it is not the ICP's buyer after all; or
- write:
  - "lookalike": the closest real customer of {c} from the list (a fintech API gets the fintech customer), exactly as the list spells it.
  - "why": one short sentence, under 30 words, that says what they are and names the lookalike: "New York card issuing API with detailed docs and no AI assistant, just like Northwind ({c}'s customer)."
  - "trigger_text": one short sentence about the trigger with its date: "Raised a $50M Series B led by Peak XV on 12 May 2026."

Use only the facts below. JSON: {{"accounts": {{"<domain>": {{...}}}}}}

Customers: {_j(customers)}
Competitors: {", ".join(co["name"] for co in icp.get("competitors") or [])}

The accounts:
{chr(10).join(_j(a) for a in accounts)}"""


def champions(icp, accounts):
    ch = icp.get("champion") or {}
    return f"""Step: pick the champion at each account from Apollo's people search.

The buyer: {", ".join(ch.get("titles") or [])}. Pick the person whose title is closest to the buyer the ICP names (the earlier title in that list wins a tie). A founder or C-level only at a company under about 80 people. A DevRel or developer advocate IC is a fine champion when there is no head of the function, and better than a CTO at a company of 100+. Someone at a different organisation is the wrong person. Nobody fits: null.

JSON: {{"picks": {{"<domain>": {{"id": "<apollo person id>", "reason": "a few words"}} or null}}}}

{chr(10).join(_j(a) for a in accounts)}"""


def sequence(icp, accounts):
    co = icp["company"]
    return f"""Step: write sequence.json, Kevin's 3-touch sequence for {co["name"]}, sent as {co["name"]}'s BDR to the champion at each account.

{ref("sequence.md")}

Write it for {co["name"]}: the company-level sentences (what {co["name"]} does and how it works, "We're in {co.get("city") or "..."}", {co.get("ceo_first_name") or "the CEO"} as the CEO in touch 3) go in the templates; everything that changes per account goes in that account's variables. Pick the proof for each account from the proof points, the customer closest to that account. Then read three of the emails out loud as Kevin: anything that sounds like a press release, rewrite it plainer.

The program renders all 30 emails, lints them for Kevin's voice and sends back what fails.

The company: {_j({k: co.get(k) for k in ("name", "one_liner", "city", "remote", "ceo_first_name")})}
Proof points: {_j(icp.get("proof_points") or [])}

The accounts (leads keys are these domains):
{chr(10).join(_j(a) for a in accounts)}"""
