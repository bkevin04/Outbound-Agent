# Outbound Agent

Give it a company name. Every week it finds 10 new accounts that company should sell to, the champion at each one with a verified email, and builds the 3-touch outbound sequence in lemlist.

```mermaid
flowchart LR
  A[Company name] --> B[Research + ICP<br/>Claude]
  B --> C[100 lookalike accounts<br/>Apollo, 1 credit]
  C --> D[Triage + score<br/>top 30]
  D --> E[Enrich: triggers, tech,<br/>community, hiring<br/>Clay]
  E --> F[Final score<br/>10 accounts]
  F --> G[Champion per account<br/>Apollo search + reveal]
  G --> H[3-touch sequence<br/>in Kevin's voice]
  H --> I[lemlist campaign<br/>draft]
  I --> J[leads.csv:<br/>10 leads + emails]
```

## How it works

| Stage | What happens | Tool | Cost |
|---|---|---|---|
| 0 | Run folder, connectors, accounts already pitched in earlier weeks | lemlist | 0 |
| 1–2 | Research the company, write the ICP (headline, two firmographic tracks, pain, check against customers, 5 funnel stages) plus segments, Apollo filters, scoring weights, champion titles | Claude | 0 |
| 3 | One company search for 100 lookalikes, minus the company's customers, competitors and anything already pitched | Apollo | 1 credit |
| 4 | Read every account (segment, what they sell, or why it is out), score on firmographics and headcount growth, shortlist 30 | Claude + `agent/score.py` | 0 |
| 5 | Enrich the shortlist: a dated trigger from the last 6 months, docs and support tech, community channels, whether they already run an AI assistant, relevant hiring, fit score | Clay (web research when Clay is not connected) | Clay credits |
| 6 | Final score, pick 10 with a dated trigger, mix segments; write "why they fit" and the trigger line | `agent/score.py` + Claude | 0 |
| 7 | Find the champion at each account (free search), reveal the 10 emails | Apollo | ≤10 credits |
| 8 | Write Touch 1 (Day 1, the trigger), Touch 2 (Day 3, a proof story), Touch 3 (Day 5, close the loop) per champion, lint the voice | Claude + `agent/voice.py` | 0 |
| 9 | Build the lemlist campaign, steps driven by per-lead variables, add the 10 leads | lemlist | 0 |
| 10 | `leads.csv` + `report.md` | `agent/report.py` | 0 |

At most 11 Apollo credits a run. The lemlist campaign is built and loaded but never launched: the emails are written as the company's BDR, and I don't work there yet.

## Run it

### The dashboard

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m agent dashboard
```

It opens http://127.0.0.1:8765. Add the Apollo and lemlist keys with **Edit keys** (they go into `.env`, which git ignores), click **Check connections**, enter a company and start a run. Claude needs no key: the agent uses Claude Code, signed in on your Claude plan. While it runs you see each stage and the live log; when it is done, the funnel, the 10 leads (open one for why it fits, the trigger's source and its 3 emails), what to check, Apollo credits and Claude cost, and whether the lemlist campaign is a draft. A run that stopped on an error shows the error and a **Resume run** button. The dashboard listens on your machine only and needs the token it hands its own page, since the run folders hold real people's emails.

### On its own, from the terminal (no Claude Code)

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export APOLLO_API_KEY=...      # company search, people search, email reveal (or put both keys in .env)
export LEMLIST_API_KEY=...     # optional: without it the lemlist payloads are written but not loaded
.venv/bin/python -m agent check-keys      # free calls: is Claude Code signed in, do the keys work
.venv/bin/python -m agent run "Acme" --domain acme.dev
```

[`agent/pipeline.py`](agent/pipeline.py) runs stages 0 to 10 end to end. Claude does the judgement steps through Claude Code (`claude -p`) on your Claude plan: no API key, no per-call bill, and a run counts against the plan's usage limits. Each step is its own Claude Code session in an empty folder with your skills, plugins, hooks and CLAUDE.md switched off and no tools but web search and web fetch. To use the Claude API instead, set `OUTBOUND_CLAUDE=api` and `ANTHROPIC_API_KEY` (`OUTBOUND_MODEL` picks the model; the API mode reports its cost). Either way it uses web search for the research and the enrichment, and every answer goes through the same checks as the skill's (ICP shape, triage coverage, dated triggers with URLs, the top-10 rules, the voice lint); problems go back to Claude for a fix. Apollo and lemlist are called through their REST APIs ([`agent/apollo.py`](agent/apollo.py), [`agent/lemlist.py`](agent/lemlist.py)), and the hard rules are in the code, not the prompt: the Apollo client refuses any paid call past 11 credits and has no phone, waterfall or enrich method; the lemlist client has no start, launch or lead-review method and adds every lead with lemlist enrichment off; the run stops before adding a lead unless the campaign reads draft with no senders.

Every stage writes its file in the run folder and is skipped when the file is there, so a run that stops halfway is picked up by running the same command again, without paying Apollo twice. Enrichment is web research, plus Clay when you give it a Clay API key and the ID of a Clay function to run (Clay's API has no plain "enrich this domain" call: its functions, such as "Get Funding and Expansion Events", run by ID, and the ID is copied from Functions, then Details in Clay). The function runs once on the 30 shortlisted accounts, its results go to the research step as evidence, and the run reports the Clay credits it used; with only a key, Clay shows its credit balance and is not used. In lemlist the campaign is built with you as the sender (the mailbox that owns the key, or `LEMLIST_SENDER_EMAIL`), email 1 right away, email 2 two days later, email 3 two days after that, and it is never launched. The final message prints the 10 leads, the funnel, Apollo credits and balance, and Claude's searches and tokens (and its cost in API mode).

### In Claude Code

From this repo:

```
/outbound-agent Acme acme.dev
```

The skill is in [`.claude/skills/outbound-agent/SKILL.md`](.claude/skills/outbound-agent/SKILL.md). It needs the Apollo and lemlist connectors, and Clay for the enrichment step.

The Python in `agent/` does the steps that should not depend on judgement: parsing the Apollo result, dropping customers and repeats, scoring, checking the 10, rendering and linting the emails, building the lemlist payloads and the report.

```
python3 -m agent new "Acme" --domain acme.dev
python3 -m agent validate <run>
python3 -m agent longlist <run> <apollo-result-file>
python3 -m agent score <run> --stage pre
python3 -m agent enrich-merge <run> <run>/enrich/*.json --source clay
python3 -m agent score <run> --stage final
python3 -m agent check <run>
python3 -m agent champions <run> <apollo-match-file>
python3 -m agent sequence <run> render|lint|lemlist
python3 -m agent report <run>
```

Tests: `python3 -m unittest discover -s tests` (standard library only).

## What is in the repo and what is not

- `companies/<slug>/icp.json`: the ICP, reused week to week. Git ignores it, so it stays on your machine.
- `runs/`: every run's files, including the champions' emails. Git ignores it, and nothing with a person's email goes anywhere else.
