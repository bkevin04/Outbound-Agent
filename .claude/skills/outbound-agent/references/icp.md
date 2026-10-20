# icp.json and enrichment.json

## icp.json

The first half is the ICP in Kevin's structure. The second half is what the agent needs to run it every week.

```jsonc
{
  "updated": "2026-10-18",                       // set by save-icp; reused for 30 days
  "company": {
    "name": "Acme",                              // the company's own spelling
    "slug": "acme",                              // lowercase, spaces and dots dropped
    "domain": "acme.dev",
    "one_liner": "AI assistant that answers technical questions from a company's docs, code and community",
    "city": "New York",                          // office city for "We're in {city}"; or "remote": true
    "ceo_first_name": "Dana"                     // touch 3: "15 minutes with Dana, our CEO"
  },
  "icp": {
    // rich text: <em> and <mark> only
    "headline": "{Company}'s ICP: <em>{segment with examples in brackets}</em>, that {is|are} {size word} (<mark>{size range}</mark>), living in <mark>{tech stack}</mark> with a <mark>{person or setup}</mark>, where {one-line situation} and going through a change that {makes|raises} <em>{the cost or risk}</em>.",
    "firmographics": ["{Segment A} first: {geo}, {size}, {revenue or stage}, {qualifier}.",
                      "Second track: {Segment B} of {size} whose {thing} is {qualifier}."],
    "pain_quote": "“The buyer's words, one line.”",
    "pain_body": "Two or three plain sentences on the day-to-day without the product.",
    "check": ["{Customer 1}, {Customer 2} and {Customer 3} confirm the {buyer}: {what the case studies prove}.",
              "They do not yet prove {what is missing}, which is why {segment} rank first."],
    "funnel": [                                   // exactly 5
      {"title": "Market, size & stage", "body": "..."},
      {"title": "Tech stack & channels", "body": "..."},
      {"title": "Pain point: {name}", "body": "..."},
      {"title": "Trigger in the last 6 months", "body": "..."},
      {"title": "10 specific accounts", "body": "A {champion} who already has the problem on their desk!"}
    ]
  },
  "segments": [                                   // mix them the way the ICP does
    {"key": "ai_infra", "label": "AI infrastructure", "keywords": ["inference", "llm", "gpu cloud", "model serving"],
     "lookalike": "Northwind", "max_in_top10": 4}
  ],
  "apollo_search": {                              // passed to apollo_mixed_companies_search as it is
    "organization_locations": ["United States"],
    "organization_num_employees_ranges": ["51,200", "201,500", "501,1000"],
    "q_organization_keyword_tags": ["developer tools", "api", "developer platform"],
    "per_page": 100, "page": 1
  },
  "scoring": {
    "employees": {"min": 50, "max": 1000, "sweet_min": 80, "sweet_max": 500},
    "geo_countries": ["United States"],
    "keyword_weights": {"api": 2, "sdk": 2, "developer platform": 2, "open source": 1},
    "negative_keywords": {"agency": -5, "consulting": -4, "staffing": -5},
    "tech_weights": {"mintlify": 2, "readme": 2, "gitbook": 2, "docusaurus": 1, "zendesk": 1, "intercom": 1},
    "trigger_weights": {"funding": 3, "new_leader": 3, "launch": 2, "hiring": 2, "acquisition": 2, "partnership": 1, "expansion": 1, "other": 1},
    "trigger_max_age_days": 183,
    "hiring_keywords": ["developer advocate", "developer relations", "support engineer", "technical writer"],
    "growth_6m_points": [[20, 3], [10, 2], [3, 1]], "growth_negative": -1,
    "has_ai_assistant": -3, "no_ai_assistant": 2, "public_company": -2, "keyword_cap": 8, "shortlist": 30
  },
  "champion": {
    "titles": ["head of developer relations", "head of developer experience", "head of support"],
    "seniorities": ["head", "director", "vp", "manager"],
    "fallback_titles": ["vp engineering", "head of community", "cto"]
  },
  "customers": [{"name": "Northwind", "domain": "northwind.dev", "segment": "api", "result": "30% fewer monthly tickets", "url": "..."}],
  "competitors": [{"name": "Rival", "domain": "rival.ai"}],
  "exclude": {"domains": ["hooli.dev"], "why": {"hooli.dev": "already shown to the founders"}},
  "proof_points": [{"customer": "Globex", "who": "a CI/CD platform", "result": "saves 500+ support hours a month", "url": "..."}]
}
```

Scoring, in plain words (`agent/score.py`):
- Firmographics (all ~100, after the triage): 6-month headcount growth ≥20% +3, ≥10% +2, ≥3% +1, shrinking -1; in the size range +2 and in the sweet spot +1 more, out of range -4; HQ in a target country +1, elsewhere -2; public company -2; keyword hits by weight (capped); negative keywords; no segment matched -1; a funding round in the last 183 days +2 (last year +1).
- Signals (the enriched shortlist): the best dated trigger by type weight and recency (≤45 days ×1, ≤90 ×0.8, ≤183 ×0.6) plus 0.5 per extra trigger (max +1); tech by weight (capped); community channels +1 each (max 2); AI assistant already on the docs -3, checked and none +2; relevant hiring +1 each (max 2); Clay fit score / 25.
- The 10: best total with a dated trigger, at most `max_in_top10` per segment.

## enrichment.json

```jsonc
{
  "source": "clay",                  // clay | web | clay+web
  "enriched_on": "2026-10-18",
  "accounts": {
    "initech.io": {
      "triggers": [{"type": "launch", "date": "2026-10-01", "text": "Launched the Initech video playground", "url": "https://..."}],
      "description": "Video API for developers", "description_url": "https://...",
      "segment": "devtools",         // optional: overrides the triage segment
      "tech": ["mintlify", "zendesk"],
      "community": ["discord", "slack"],
      "ai_assistant": "no",          // yes | no | unknown
      "ai_assistant_vendor": "",     // the vendor's name, or own
      "ai_assistant_url": "https://docs.initech.io",
      "existing_customer": false,    // true when the assistant is the client's own product: dropped from the 10
      "hiring": ["Developer Advocate"], "hiring_url": "https://...",
      "hq_city": "San Francisco",
      "fit_score": 82,               // Clay only, 0-100
      "notes": "short, factual"
    }
  }
}
```
