---
name: outbound-agent
description: "Kevin's weekly outbound agent. Give it a company name and it defines that company's ICP, pulls 100 lookalike accounts from Apollo, enriches them with Clay (web research when Clay is not connected), scores them and picks 10 accounts with dated triggers, finds each champion and reveals their email with Apollo, writes Kevin's 3-touch sequence, and loads it into a lemlist campaign that stays in draft. Output: the 10 leads with champion emails. Use for 'run the outbound agent for X', 'weekly lead list for X', '/outbound-agent X'."
---

# Outbound agent

This agent does a BDR's prospecting for a company, for real and every week: plug in the company name, get 10 new accounts, the champion at each with a verified email, and the sequence built in lemlist.

**Input:** `$ARGUMENTS` = company name, optionally its domain (`Acme`, `Acme acme.dev`). Nothing else is asked.
**Output:** `runs/<slug>/<date>/accounts.xlsx` (the 100, the 30 and the 10, with how each was scored and how far it got), `leads.csv` (10 leads with champion emails) and `report.md`, the lemlist campaign "Outbound Agent · {Company}" with the leads added (draft), and the final message.

## Hard rules

- **The repo is public.** `runs/` is git-ignored and is the only place a person's email, phone or LinkedIn goes. Never write one into `companies/`, the skill, the README or a commit. `python3 -m agent save-icp` refuses an `@`.
- **Apollo budget: at most 11 credits a run** (1 for the 100-company search, at most 10 for the champion reveals). Kevin approved this on 18 Oct 2026 ("Yes, up to 11 per run", about 44 a month weekly). Free calls: people search. Paid: `apollo_mixed_companies_search` (1 credit per request with results), `apollo_people_bulk_match` (1 per matched person), `apollo_organizations_enrich` / `bulk_enrich` and job postings (1 each: do not use them). Never reveal phones and never run waterfall. Anything beyond 11: stop and ask.
- **lemlist stays in draft.** The emails speak as {Company}'s BDR ("We're in {city}", "our CEO") and Kevin does not work there yet, so launching would email real people on the company's behalf. Never call `launch_campaign` or `set_campaign_state`, and never turn on lemlist enrichment (`findEmail`, `verifyEmail`, `linkedinEnrichment`, `findPhone` all cost lemlist credits).
- **Facts only from a source.** Every trigger has a date in the last 183 days and a URL. Every "why they fit" names a real customer of {Company}. Nothing invented; an unverified fact stays out.
- **Kevin's voice** for every sentence a person reads (the ICP, why / trigger, the emails): load the `kevin-voice` skill if it is available, and follow `references/voice.md` either way. `python3 -m agent sequence <run> lint` must pass.

All commands run from the repo root. `<run>` is the folder `python3 -m agent new` prints.

## Stage 0: Set up

1. `python3 -m agent new "<Company>" --domain <domain>` → prints the run folder and whether `companies/<slug>/icp.json` was reused (it is, when under 30 days old).
2. Load the tools in one ToolSearch: `select:mcp__Apollo_io__apollo_mixed_companies_search,mcp__Apollo_io__apollo_mixed_people_api_search,mcp__Apollo_io__apollo_people_bulk_match,mcp__Apollo_io__apollo_users_api_profile,mcp__lemlist__get_campaigns,mcp__lemlist__search_campaign_leads,mcp__lemlist__create_campaign_with_sequence,mcp__lemlist__add_sequence_step,mcp__lemlist__get_campaign_sequences,mcp__lemlist__get_campaign_details,mcp__lemlist__add_leads_to_campaign,mcp__lemlist__preview_email`. Then ToolSearch `+clay` to see whether the Clay connector is connected (its tools are named like `find-and-enrich-company`, `add-company-data-points`, `get-credits-available`). Note the answer in `run.json` (`"clay": "connected"` or `"not connected"`).
3. Already pitched: `get_campaigns` with `search: "Outbound Agent · {Company}"`. If the campaign exists, `search_campaign_leads` with its id (page through with `offset`) and write every lead's company domain to `<run>/already_pitched.json` as `{"domains": [...]}`, so this week's 10 are new. Add any accounts Kevin has already shown the company's founders to `icp.json` `exclude.domains`.
4. One `_conversation_ref` for every Apollo call in the run.

## Stage 1: Research (skip when icp.json was reused)

One research subagent (general-purpose, WebSearch "extended" where thin, WebFetch), told today's date, asked for facts with a URL on every claim and no copy:
- The company: what the product does, named customers and case studies with numbers and quotes, pricing, founders (CEO first name for touch 3), funding, office city (for "We're in {city}"), competitors with domains.
- ICP evidence: segments they sell to, size and stage, geography, the titles that buy or champion it, tech that signals fit, the pains in the buyer's own words.
- The full public customer list with domains (exclusions and "just like X" lookalikes).
- 3 to 6 proof points (customer + number + URL).
- Suggested Apollo filters: keyword tags, employee ranges, HQ locations, technology uids.

## Stage 2: The ICP (icp.json)

Write `<run>/icp.json` in the shape `references/icp.md` gives: the ICP (headline, two firmographic tracks, pain quote and body, check against customers, the 5 funnel stages), then what the agent runs on: `segments` (key, label, keywords, the lookalike customer, `max_in_top10`), `apollo_search` (the exact search parameters, `per_page: 100`), `scoring` (weights), `champion` (titles, seniorities), `customers`, `competitors`, `exclude`, `proof_points`. Then `python3 -m agent validate <run>` until it says OK, and `python3 -m agent save-icp <run>` so next week reuses it.

## Stage 3: The long list (Apollo, 1 credit)

1. One `apollo_mixed_companies_search` with `icp.apollo_search` as it is (`per_page: 100`, `page: 1`). This is the run's 1 search credit.
2. The result is large and is usually saved to a file automatically; pass that file to the next step. If it came back inline, write it to `<run>/apollo_search_raw.json` exactly as returned.
3. `python3 -m agent longlist <run> <raw file>` → `longlist.json`: both result buckets flattened, and the company itself, customers, competitors, excluded and already-pitched domains dropped with reasons. The search answer is thin (name, domain, LinkedIn, revenue, NAICS/SIC, headcount growth; no description, size or keywords), because the filters already did that work: every result is in the size range, the HQ country and the keyword tags.
4. Fewer than 40 left: say so in the report and go on with what is there. A second paid search needs Kevin's yes (it breaks the 11-credit budget once the reveals run), so in a scheduled run do not make one.

## Stage 4: Triage, pre-score and shortlist

1. **Triage** (the "understand everything using Claude" step): read every account on the long list and write `<run>/triage.json`, `{"accounts": {"<domain>": {"segment": "<segment key>", "description": "one line on what they sell"}}}`, or `{"drop": "<reason>"}` for a misfit: media, investors, recruiters, consumer or non-developer products, a company that is now part of a bigger one ("part of IBM since 2025"), a close competitor, or an existing customer under another name. Every domain on the long list gets an entry. When the ICP needs a segment the list shows and `segments` lacks (web3 platforms, low-code app platforms), add it to `icp.json` with its lookalike customer. Triage descriptions only rank; no sentence a person reads is written from them.
2. `python3 -m agent score <run> --stage pre --shortlist 30` ranks what is left on firmographics (segment and keywords from the triage, headcount growth over 6 months, HQ, size and funding when Apollo has them) and writes `shortlist.json`, the top 30 to enrich (25 is enough when Clay enriches; web research loses a few to missing triggers and hidden customers).

## Stage 5: Enrich (Clay, else web research)

What every shortlisted account needs (shape in `references/icp.md`): `triggers` (type, date, one plain sentence, URL; types `funding`, `launch`, `new_leader`, `hiring`, `acquisition`, `partnership`, `expansion`, `other`), `description` with its URL, `tech` (docs platform, support and community tools), `community` (discord, slack, forum ...), `ai_assistant` (`yes` when their docs already run an AI assistant, `no` when checked and none, `unknown`) and the vendor, `existing_customer: true` when that vendor is {Company} itself, `hiring` (open roles that matter to the ICP), `hq_city`, and `fit_score` 0–100 when Clay gives one.

- **Clay connected:** `get-credits-available` first and say the number in the report. Then `find-and-enrich-company` per shortlisted domain and `add-company-data-points` for tech stack, recent news and funding, open jobs, and a fit score against the ICP headline. Write Clay's answers in the shape above as `<run>/enrich/clay.json`; every trigger still needs a date and a URL (Clay's news items carry them).
- **Clay not connected:** 6 research subagents in parallel (general-purpose), 5 shortlisted accounts each, today's date and the 6-month window in the prompt, web only (no Apollo, no lemlist), each writing `<run>/enrich/part-N.json` in the shape above with a URL for every trigger and for the `ai_assistant` answer. WebFetch can fail in a cloud container (DNS): tell them to work from WebSearch results when it does. WebSearch has a budget of about 200 searches per turn shared by every agent (Stage 1 research included): give each agent at most 25 searches, triggers first, then the AI assistant check, then the rest, and on a turn that already ran Stage 1 expect the thin fields to stay thin (18 Oct 2026: research used about 115, the 6 agents ran dry at about 15 each). Thin fields only cost points, a missing trigger costs the account its seat.

Then `python3 -m agent enrich-merge <run> <run>/enrich/*.json --source clay|web|clay+web` → `enrichment.json`. It lists each account's best trigger and flags existing customers, which the final score leaves out.

## Stage 6: Final score and the 10

1. `python3 -m agent score <run> --stage final` → `ranked.json` (all accounts, score breakdown) and `top10.json`: the 10 best with a dated trigger, at most `max_in_top10` per segment. An account flagged `NEEDS TRIGGER` gets one more targeted search for a dated trigger; if none, add it to `exclude.domains` for this run, and rerun the score. Read the enrichment notes of the 10 once: an account tied to a competitor (the same team runs a competitor, say) goes into `exclude.domains` with the reason, then rerun.
2. For each of the 10 write into `top10.json`:
   - `lookalike`: starts as the segment's; set the closest real customer for this account (a fintech API gets the fintech customer). `check` refuses a name that is not in `icp.json` `customers`.
   - `why`: one short sentence that says what they are and names the lookalike customer: "New York card issuing API with detailed docs and no AI assistant, just like Northwind (Acme's customer)."
   - `trigger_text`: one short sentence with the date: "Raised a $50M Series B led by Peak XV on 12 May 2026."
3. `python3 -m agent check <run>` until OK.

## Stage 7: Champions (free search, then at most 10 credits)

For each of the 10, one `apollo_mixed_people_api_search` (free; the calls can go in parallel): `q_organization_domains_list: [domain]`, `person_titles: icp.champion.titles`, `person_seniorities: icp.champion.seniorities`, `contact_email_status: ["verified"]`, `per_page: 10`. Pick the person whose title is closest to the buyer the ICP names (the first title in the list wins a tie; a founder or C-level only for a company under ~80 people). Nobody: the same search with `icp.champion.fallback_titles`. Still nobody: drop `contact_email_status` and take the best title, marked `email_status: "unverified"` (it is revealed like the others and the report says so). A result at a different organisation is the wrong person.

A DevRel or developer-advocate IC is a fine champion when there is no head of the function (they answer the questions themselves); prefer them over a CTO at a company of 100+.

Then one `apollo_people_bulk_match` with the 10 ids (`details: [{"id": ...}]`, nothing else turned on; Apollo reports waterfall as enabled, but Kevin's rule is the standard match): at most 10 credits, 1 per match. The answer is large and is saved to a file: `python3 -m agent champions <run> <that file>` writes `champions.json` (`{"accounts": {"<domain>": {"apollo_id", "first_name", "last_name", "title", "email", "email_status", "linkedin_url", "swapped", "note"}}}`), ties each person to an account by Apollo's organization domain, notes an email on another company domain (Gravitee writes from graviteesource.com), leaves out anyone at another company, and records `credits_consumed` in `run.json`. Set `"apollo_search": 1` in `run.json` `credits` yourself.

## Stage 8: The sequence (Kevin's 3 touches)

Write `<run>/sequence.json` in the shape `references/sequence.md` gives: one template per touch (Day 1 opens with the trigger, Day 3 a new angle with a proof story, Day 5 closes the loop) and, per account domain, the lemlist variables the templates use (`t1Subject`, `t1Opener`, `t1Proof`, `t2Subject`, `t2Hook`, `t2Proof`). The company-level sentences (what the product does, "We're in {city}", the CEO's name in touch 3) sit in the template; everything that changes per account sits in the variables. Then:
1. `python3 -m agent sequence <run> render` → `emails.md` (all 30 emails, for Kevin to read).
2. `python3 -m agent sequence <run> lint` → fix every ERROR, read every warning again as Kevin on a call.
3. Read three of the rendered emails out loud as Kevin. Anything that sounds like a press release: rewrite it plainer.

## Stage 9: lemlist (draft, never launched)

1. `python3 -m agent sequence <run> lemlist` → `lemlist_steps.json` (3 email steps with delays 0, 2, 2: email 1 as soon as the campaign is launched, then 2 days, then 2 more) and `lemlist_leads.json`.
2. Campaign: `get_campaigns` `search: "Outbound Agent · {Company}"`. None yet: `create_campaign_with_sequence` with `name: "Outbound Agent · {Company}"`, a timezone for the accounts' HQs (`America/New_York` for a US list), and step 1's `subject` and `body`; its answer carries the sequence id, then `add_sequence_step` twice (type `email`, `delay` and `subject` / `message` from steps 2 and 3, `userConfirmed: true`; Kevin approved building the campaign). Once the steps are in, put Kevin on the campaign as its sender (his own mailbox; Kevin asked for this on 20 Oct 2026, a sender alone sends nothing while the campaign is never launched). The create answer can say `"status": "running"` for a brand-new campaign: before adding any lead, `get_campaign_details` must read `draft` and `get_campaign_senders` show no one but Kevin. Anything else: stop and tell Kevin. The campaign already exists: leave its steps alone unless the template changed, and only add the leads.
3. `add_leads_to_campaign` with `lemlist_leads.json` as `leads`, `deduplicate: true`, no enrichment options. Write `<run>/lemlist_result.json`: `{"campaign_id", "campaign_name", "status", "results": [{"email", "outcome", "leadId"}]}` from the answer.
4. `preview_email` for the first lead and the first step: the subject and body must show no `{{` and the lead's own opener. `get_campaign_details`: the status must be draft. Put the status in `lemlist_result.json`.

## Stage 10: Report

1. `python3 -m agent report <run>` → `accounts.xlsx`, `leads.csv`, `report.md`, and `emails.docx` and `report.docx` for Word. The workbook is built from the run folder's files: the 10 in the accounts table (#, Account, Type, Why they fit, Trigger, Champion, Contact, then the detail: email status, trigger source, LinkedIn, score and how it scored), the 30 researched with each one's result (picked, or why not), every company the Apollo search found with how far it got and why, and a tab on how they were picked (the funnel step by step, the scoring rules with this ICP's weights, the segments, the ICP).
2. `apollo_users_api_profile` with `include_credit_usage: true` for the balance; put it in the final message, with the credits this run used.
3. `SendUserFile` `accounts.xlsx`, `leads.csv`, `emails.docx` and `report.docx` (display `attach`).
4. The final message, short: the 10 leads (account, champion, title, email, the trigger in a few words) as a table, the funnel in numbers (100 searched → kept → shortlisted → enriched → 10), Clay or web enrichment, Apollo credits used and the balance, the lemlist campaign name and that it is in draft with N leads, that `accounts.xlsx` has the 100, the 30 and the 10 with the reasoning, and anything to check (unverified emails, swapped champions, accounts that needed a second look).

## When it runs on a schedule

On Kevin's Mac the dashboard schedules the standalone run instead (⋯ → Run every week: a LaunchAgent running `python -m agent scheduled "<Company>"`), with `companies/` and `.env` already there. A cloud scheduled task starts a fresh session that clones this repo and runs this skill for the same company. Git ignores `companies/`, so a fresh clone writes the ICP again (Stages 1 and 2); on Kevin's machine `companies/<slug>/icp.json` carries it from week to week. The lemlist campaign carries the history (already-pitched accounts). Nobody is there to answer a question, so: no second paid search, no launch, and anything that would need Kevin's yes goes into the final message instead.
