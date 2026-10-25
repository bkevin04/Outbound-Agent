"""The whole run, stage by stage, without Claude Code: `python3 -m agent run "Acme" --domain acme.dev`.

The stages are the skill's (.claude/skills/outbound-agent/SKILL.md). Claude does the judgement steps (agent/llm.py),
the Apollo and lemlist clients make the connector calls with the budget and the draft rule enforced in code, and the
deterministic modules check and shape every file in between. Each stage writes its file in the run folder and is
skipped when the file is already there, so a run that stops halfway picks up where it stopped and never pays twice."""
import glob
import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

from . import apollo as apollo_mod
from . import champions as ch
from . import clay as clay_mod
from . import rest
from . import common as c
from . import icp as icp_mod
from . import longlist as ll
from . import prompts
from . import report as rep
from . import score as sc
from . import sequence as sq

ICP_MAX_AGE_DAYS = 30
ENRICH_BATCH = 5
ENRICH_WORKERS = 6
ENRICH_SEARCHES = 25     # per batch of 5
TRIGGER_RETRIES = 5      # targeted trigger searches for NEEDS TRIGGER accounts, per run, run in parallel
FINAL_ROUNDS = 8
CHAMPION_ROUNDS = 5      # rounds of "no champion here: the next account takes the seat"
SHORT_NOTES = ("no dated trigger found for ", "only ")   # notes _pick_ten rewrites each time it runs
MIN_LONGLIST = 40
TRIGGER_TYPES = {"funding", "launch", "new_leader", "hiring", "acquisition", "partnership", "expansion", "other"}

# Files the standalone run writes on top of the skill's.
RESEARCH = "research.json"
APOLLO_SEARCH_RAW = "apollo_search_raw.json"
PEOPLE = "people_search.json"
PICKS = "champion_picks.json"
APOLLO_MATCH_RAW = "apollo_match_raw.json"
CLAY_RAW = "clay.json"
ENRICH_DIR = "enrich"
RETRY_PART = "retry.json"


STAGES = [("already_pitched", "Accounts already pitched"), ("research_and_icp", "Research and ICP"),
          ("long_list", "Long list from Apollo"), ("triage_and_shortlist", "Triage and shortlist"),
          ("enrich", "Enrich the shortlist"), ("final_ten", "Pick the accounts"), ("find_champions", "Find the champions"),
          ("write_sequence", "Write the emails"), ("load_lemlist", "Load the lemlist draft"), ("finish", "Report")]


def _keyed(obj, key="accounts"):
    """Problems when obj[key] is not an object keyed by domain (the shape every per-account answer uses)."""
    return [] if isinstance(obj.get(key), dict) else [f'"{key}" must be an object keyed by domain']


def campaign_name(company):
    return f"Outbound Agent · {company}"


def new_run(company, domain, date=None, runs=c.RUNS, companies=c.COMPANIES):
    """runs/<slug>/<date>/ with run.json, and the saved ICP copied in when it is under 30 days old.
    -> (run folder, status line, old ICP to refresh from or None)"""
    slug = c.slugify(company)
    date = date or c.today()
    run = os.path.join(runs, slug, date.isoformat())
    os.makedirs(run, exist_ok=True)
    meta_path = os.path.join(run, c.RUN)
    meta = c.load_optional(meta_path) or {"company": company, "slug": slug, "domain": c.norm_domain(domain or ""),
                                          "date": date.isoformat(), "credits": {}, "notes": []}
    c.save(meta_path, meta)
    kept, here, old = os.path.join(companies, slug, c.ICP), os.path.join(run, c.ICP), None
    if os.path.exists(here):
        status = "icp.json already in the run folder"
    elif os.path.exists(kept):
        age = (date - (c.parse_date(c.load(kept).get("updated")) or date)).days
        if age <= ICP_MAX_AGE_DAYS:
            shutil.copy(kept, here)
            status = f"reused companies/{slug}/icp.json ({age} days old)"
        else:
            old = c.load(kept)
            status = f"companies/{slug}/icp.json is {age} days old: refresh the research, start from the old file"
    else:
        status = "no saved ICP: run Stage 1 research and write icp.json"
    return run, status, old


class Pipeline:
    def __init__(self, run, claude, apollo, lemlist=None, companies=c.COMPANIES, log=print, old_icp=None, clay=None):
        self.run, self.claude, self.apollo, self.lemlist, self.clay = run, claude, apollo, lemlist, clay
        self.companies, self.log, self.old_icp = companies, log, old_icp
        self.meta = c.load(self.p(c.RUN))
        self.today = c.parse_date(self.meta["date"])
        self.company = self.meta["company"]

    # --- files ---------------------------------------------------------------------------------------------------
    def p(self, *names):
        return os.path.join(self.run, *names)

    def has(self, name):
        return os.path.exists(self.p(name))

    def load(self, name, default=None):
        return c.load_optional(self.p(name), default)

    def save(self, name, obj):
        c.save(self.p(name), obj)

    def save_meta(self):
        self.meta["claude"] = {"backend": getattr(self.claude, "backend", "api"), "model": self.claude.model,
                               **self.claude.usage, "cost_usd": self.claude.cost()}
        self.save(c.RUN, self.meta)

    def note(self, text):
        self.log(f"  note: {text}")
        if text not in self.meta["notes"]:
            self.meta["notes"].append(text)

    def stage(self, title):
        self.save_meta()
        self.log(f"\n== {title}")

    # --- the run -------------------------------------------------------------------------------------------------
    def all(self):
        """Every stage in order. run.json keeps the stage in progress and the stages done, for the dashboard."""
        done = self.meta.setdefault("stages_done", [])
        self.meta.update(error=None, run_started=time.time(), finished_at=None)
        out = None
        for key, _ in STAGES:
            self.meta.update(stage=key, stage_started=time.time())
            out = getattr(self, key)()
            if key not in done:
                done.append(key)
        self.meta.update(stage=None, finished_at=time.time())
        self.save_meta()
        return out

    def already_pitched(self):
        self.stage("Stage 0: accounts already pitched (lemlist)")
        if self.has(c.PITCHED):
            self.log("  already_pitched.json is there")
            return
        if not self.lemlist:
            self.note("lemlist not connected (no LEMLIST_API_KEY): earlier weeks' accounts are not excluded, no campaign built")
            self.save(c.PITCHED, {"domains": []})
            return
        camp = self.lemlist.find_campaign(campaign_name(self.company))
        domains = sorted(set(self.lemlist.lead_domains(camp["_id"]))) if camp else []
        self.save(c.PITCHED, {"campaign_id": camp and camp["_id"], "domains": domains})
        self.log(f"  {len(domains)} accounts already in '{campaign_name(self.company)}'" if camp else "  no campaign yet")

    def research_and_icp(self):
        self.stage("Stages 1-2: research and the ICP")
        if self.has(c.ICP):
            self.log("  icp.json is there")
            return
        if not self.has(RESEARCH):
            res = self.claude.ask_json("research", prompts.research(self.company, self.meta.get("domain")),
                                       keys=("company", "customers", "competitors", "proof_points"),
                                       searches=40, fetches=20, effort="high")
            self.save(RESEARCH, res)
        slug = self.meta["slug"]

        def check(obj):
            out = icp_mod.problems(obj)
            if "@" in json.dumps(obj, ensure_ascii=False):
                out.append("an '@' is in icp.json: no email addresses, the file goes into a public repo")
            if (obj.get("company") or {}).get("slug") != slug:
                out.append(f'company.slug must be "{slug}"')
            names = {cu.get("name") for cu in obj.get("customers") or []}
            for s in obj.get("segments") or []:
                if s.get("lookalike") and s["lookalike"] not in names:
                    out.append(f"segment {s.get('key')}: lookalike {s['lookalike']!r} is not in customers")
            return out

        icp = self.claude.ask_json("icp", prompts.icp(self.company, slug, self.load(RESEARCH), self.old_icp),
                                   keys=("company", "icp", "segments", "apollo_search"), check=check, effort="high")
        if not self.meta.get("domain"):
            self.meta["domain"] = c.norm_domain(icp["company"]["domain"])
        self.save(c.ICP, icp)
        self.save_icp(icp)

    def save_icp(self, icp):
        """Keep the ICP in companies/<slug>/icp.json for next week. Company facts only: the repo is public."""
        if "@" in json.dumps(icp, ensure_ascii=False):
            raise SystemExit("icp.json contains an '@': no email addresses go into companies/ (the repo is public)")
        kept = dict(icp, updated=self.meta["date"])
        c.save(os.path.join(self.companies, icp["company"]["slug"], c.ICP), kept)
        self.log(f"  saved companies/{icp['company']['slug']}/icp.json")

    def long_list(self):
        self.stage("Stage 3: the long list (Apollo, 1 credit)")
        icp = self.load(c.ICP)
        if not self.has(APOLLO_SEARCH_RAW):
            raw = self.apollo.search_companies(icp["apollo_search"], self.meta["credits"])
            self.save(APOLLO_SEARCH_RAW, raw)
            self.meta["credits"]["apollo_search"] = 1
            self.save_meta()
        out = ll.build([c.read_text(self.p(APOLLO_SEARCH_RAW))], icp, self.load(c.PITCHED, {}).get("domains", []))
        self.save(c.LONGLIST, out)
        self.log(f"  {out['kept']} kept of {out['searched']} searched, {len(out['dropped'])} dropped")
        if out["kept"] < MIN_LONGLIST:
            self.note(f"the Apollo search left {out['kept']} accounts (under {MIN_LONGLIST}); a second paid search "
                      "needs Kevin's yes, so the run goes on with these. Widen apollo_search in icp.json for next week")

    def triage_and_shortlist(self):
        self.stage("Stage 4: triage, pre-score and shortlist")
        icp, longlist = self.load(c.ICP), self.load(c.LONGLIST)
        if not self.has(c.TRIAGE):
            domains = [a["domain"] for a in longlist["accounts"]]
            names = {cu.get("name") for cu in icp.get("customers") or []}

            def check(obj):
                out = _keyed(obj)
                if out:
                    return out
                new = obj.get("new_segments") or []
                for s in new:
                    if not (s.get("key") and s.get("label") and s.get("keywords")):
                        out.append(f"new segment {s.get('key')!r} needs key, label and keywords")
                    if s.get("lookalike") and s["lookalike"] not in names:
                        out.append(f"new segment {s.get('key')}: lookalike {s['lookalike']!r} is not a customer")
                keys = {s["key"] for s in icp["segments"]} | {s.get("key") for s in new}
                tri = {k.lower(): v for k, v in (obj.get("accounts") or {}).items()}
                missing = [d for d in domains if d not in tri]
                if missing:
                    out.append(f"no entry for {len(missing)} domains: {', '.join(missing[:40])}")
                for d, t in tri.items():
                    if not isinstance(t, dict) or not (t.get("drop") or t.get("segment") in keys):
                        out.append(f"{d}: needs a segment key from the segments ({', '.join(sorted(keys))}) or a drop reason")
                return out

            tri = self.claude.ask_json("triage", prompts.triage(icp, longlist["accounts"]), keys=("accounts",),
                                       check=check, searches=30, effort="medium")
            new = tri.pop("new_segments", None) or []
            if new:
                icp["segments"] += new
                self.save(c.ICP, icp)
                self.save_icp(icp)
                self.log(f"  added segments: {', '.join(s['key'] for s in new)}")
            tri["accounts"] = {k.lower(): v for k, v in tri["accounts"].items()}
            self.save(c.TRIAGE, tri)
        tri = self.load(c.TRIAGE)
        applied = sc.apply_triage(longlist, tri)
        ranked = sc.rank_pre(applied, icp)
        n = c.SHORTLIST_SIZE   # the run's size wins over an older icp.json "shortlist"
        self.save(c.SHORTLIST, {"ranked": ranked, "shortlist": [r["domain"] for r in ranked[:n]],
                                "triage_dropped": applied["triage_dropped"]})
        self.log(f"  {len(applied['triage_dropped'])} dropped on the read, shortlisted {min(n, len(ranked))} of {len(ranked)}")

    def enrich(self):
        self.stage("Stage 5: enrich the shortlist (Clay when a function is set, and web research)")
        icp, sl = self.load(c.ICP), self.load(c.SHORTLIST)
        rows = {r["domain"]: r for r in sl["ranked"]}
        todo = [rows[d] for d in sl["shortlist"]]
        batches = [todo[i:i + ENRICH_BATCH] for i in range(0, len(todo), ENRICH_BATCH)]
        os.makedirs(self.p(ENRICH_DIR), exist_ok=True)
        clay = self.clay_results(todo)

        def one(i, batch):
            name = os.path.join(ENRICH_DIR, f"part-{i:02d}.json")
            if self.has(name):
                return
            want = [a["domain"] for a in batch]

            def check(obj):
                if _keyed(obj):
                    return _keyed(obj)
                out = [f"no entry for {d}" for d in want if d not in {k.lower() for k in obj["accounts"]}]
                for d, e in obj["accounts"].items():
                    for t in (e or {}).get("triggers") or []:
                        if not (t.get("url") and c.parse_date(t.get("date")) and t.get("type") in TRIGGER_TYPES):
                            out.append(f"{d}: trigger {t.get('text')!r} needs a type from the list, a date and a URL, "
                                       "or leave it out")
                return out

            facts = {d: clay[d] for d in want if d in clay}
            part = self.claude.ask_json(f"enrich {i}", prompts.enrich(icp, batch, ENRICH_SEARCHES, self.today, facts),
                                        keys=("accounts",), check=check, searches=ENRICH_SEARCHES,
                                        fetches=ENRICH_SEARCHES, effort="medium")
            part["accounts"] = {k.lower(): v for k, v in part["accounts"].items()}
            self.save(name, part)
            self.log(f"  batch {i}: {', '.join(want)}")

        with ThreadPoolExecutor(max_workers=getattr(self.claude, "workers", ENRICH_WORKERS)) as pool:
            list(pool.map(lambda x: one(*x), enumerate(batches, 1)))
        self.merge_enrichment()

    def clay_results(self, accounts):
        """CLAY_ROUTINE_ID run once over the shortlist, in Clay credits. The answer is kept in enrich/clay.json, so a
        resumed run never pays twice. -> {domain: Clay's result}; {} when Clay is not connected or no function is set."""
        path = self.p(ENRICH_DIR, CLAY_RAW)
        if not os.path.exists(path):
            try:
                routine = clay_mod.routine_from_env()
            except SystemExit as e:
                self.note(f"Clay not used: {e}")
                return {}
            if not (self.clay and routine):
                return {}
            inputs = [k.strip() for k in os.environ.get("CLAY_ROUTINE_INPUTS", "domain,name").split(",") if k.strip()]
            items = [{"id": a["domain"], "inputs": {k: a["domain"] if k == "domain" else a.get(k) or a["domain"]
                                                     for k in inputs}} for a in accounts]
            try:
                before = self.clay.balance()
                results = self.clay.run_routine(routine, items)
                after = self.clay.balance()
            except (SystemExit, rest.HTTPError, OSError, KeyError) as e:
                self.note(f"Clay function {routine} did not run ({e}): the research is web only")
                c.save(path, {"routine": routine, "error": str(e), "results": {}})
                return {}
            used = round(before - after, 2) if isinstance(before, (int, float)) and isinstance(after, (int, float)) else None
            c.save(path, {"routine": routine, "results": results, "credits_before": before, "credits_after": after})
            self.meta["clay"] = {"routine": routine, "accounts": len(results), "credits_used": used, "credits_left": after}
            self.log(f"  Clay: {len(results)} accounts through {routine}, {used if used is not None else '?'} credits")
        return {d: r for d, r in (c.load(path).get("results") or {}).items() if r and not r.get("error")}

    def merge_enrichment(self):
        parts = sorted(glob.glob(self.p(ENRICH_DIR, "part-*.json")))
        if os.path.exists(self.p(ENRICH_DIR, RETRY_PART)):
            parts.append(self.p(ENRICH_DIR, RETRY_PART))   # last, so the trigger retries win
        clay_path = self.p(ENRICH_DIR, CLAY_RAW)
        used_clay = os.path.exists(clay_path) and bool(c.load(clay_path).get("results"))
        out = sc.merge_enrichment([c.load(f) for f in parts], "clay+web" if used_clay else "web", self.meta["date"])
        self.save(c.ENRICHMENT, out)
        with_trigger = sum(1 for e in out["accounts"].values() if sc.valid_triggers(e, self.load(c.ICP)))
        self.log(f"  enrichment: {len(out['accounts'])} accounts, {with_trigger} with a dated trigger")

    def _score_final(self):
        icp = self.load(c.ICP)
        longlist = sc.apply_triage(self.load(c.LONGLIST), self.load(c.TRIAGE, {}))
        ranked = sc.rank_final(longlist, self.load(c.ENRICHMENT, {}), icp)
        self.save(c.RANKED, ranked)
        top = sc.pick_top(ranked, icp)
        old = {t["domain"]: t for t in self.load(c.TOP10, [])}
        for t in top:   # keep what was already written for an account that stays in the picks
            for k in ("why", "trigger_text", "lookalike"):
                t[k] = (old.get(t["domain"]) or {}).get(k) or t[k]
        self.save(c.TOP10, top)
        return icp, top

    def _drop(self, domain, reason):
        """Leave an account out of this run: a triage drop (the score skips it) and the run's exclude list."""
        tri = self.load(c.TRIAGE, {"accounts": {}})
        tri["accounts"][domain] = {"drop": reason}
        self.save(c.TRIAGE, tri)
        icp = self.load(c.ICP)
        ex = icp.setdefault("exclude", {"domains": [], "why": {}})
        if domain not in ex.setdefault("domains", []):
            ex["domains"].append(domain)
            ex.setdefault("why", {})[domain] = reason
        self.save(c.ICP, icp)
        self.note(f"{domain} left out: {reason}")

    def final_ten(self):
        self.stage(f"Stage 6: final score and the {c.LEADS}")
        self._pick_ten()

    def _pick_ten(self):
        """Score, research missing triggers, write why / trigger_text, until top10.json is ready. Run again after an
        account is left out, so the next one takes its seat."""
        self.meta["notes"] = [n for n in self.meta["notes"] if not n.startswith(SHORT_NOTES)]
        tried = set(self.meta.setdefault("trigger_retries", []))
        for _ in range(FINAL_ROUNDS):
            icp, top = self._score_final()
            need = [t for t in top if t["needs_trigger"] and t["domain"] not in tried]
            if need and len(tried) < TRIGGER_RETRIES:
                need = need[:TRIGGER_RETRIES - len(tried)]
                tried.update(t["domain"] for t in need)
                self.meta["trigger_retries"] = sorted(tried)
                with ThreadPoolExecutor(max_workers=getattr(self.claude, "workers", ENRICH_WORKERS)) as pool:
                    found = list(pool.map(lambda t: self._search_trigger(icp, t), need))
                for t, ans in zip(need, found):   # files are written one at a time
                    self._apply_trigger(icp, t, ans)
                self.merge_enrichment()
                continue
            pending = [t for t in top if not t["needs_trigger"] and not (t["why"] and t["trigger_text"])]
            if not pending:
                break
            if self._write(icp, pending):
                continue    # something was left out: score again, the next account moves up
        icp, top = self._score_final()
        dropped = [t for t in top if t["needs_trigger"]]
        if dropped:
            self.note(f"no dated trigger found for {', '.join(t['name'] for t in dropped)}: out of the {c.LEADS}")
            top = [dict(t, rank=i) for i, t in enumerate((t for t in top if not t["needs_trigger"]), 1)]
            self.save(c.TOP10, top)
        probs = sc.check_top10(top, icp["company"]["name"], [cu["name"] for cu in icp.get("customers") or []])
        short = [p for p in probs if p.startswith("top10 has")]
        if short:
            self.note(f"only {len(top)} accounts had a dated trigger and passed every check, not {c.LEADS}")
        rest = [p for p in probs if p not in short]
        if rest:
            raise SystemExit("top10.json:\n- " + "\n- ".join(rest))
        for t in top:
            self.log(f"  {t['rank']:>2}. {t['score']:>5} {t['name']} ({t['domain']}): {t['trigger_text']}")

    def _search_trigger(self, icp, t):
        return self.claude.ask_json(f"trigger {t['domain']}", prompts.trigger(icp, t, self.today), keys=("triggers",),
                                    searches=5, effort="medium")

    def _apply_trigger(self, icp, t, ans):
        found = sc.valid_triggers({"triggers": ans["triggers"]}, icp)
        if not found:
            self._drop(t["domain"], "no dated trigger in the last 183 days")
            return
        path = self.p(ENRICH_DIR, RETRY_PART)
        retry = c.load_optional(path, {"accounts": {}})
        base = retry["accounts"].get(t["domain"]) or (self.load(c.ENRICHMENT, {}).get("accounts") or {}).get(t["domain"]) or {}
        base = dict(base, triggers=list(base.get("triggers") or []) + ans["triggers"])
        retry["accounts"][t["domain"]] = base
        c.save(path, retry)
        self.log(f"  {t['domain']}: found {found[0]['type']} {found[0]['date']}")

    def _write(self, icp, pending):
        """why / trigger_text / lookalike for the accounts that do not have them yet. -> True when one was left out."""
        enr = (self.load(c.ENRICHMENT, {}).get("accounts")) or {}
        facts = []
        for t in pending:
            e = enr.get(t["domain"]) or {}
            facts.append({"domain": t["domain"], "name": t["name"], "segment": t["industry"], "employees": t["employees"],
                          "hq": t.get("hq"), "segment_lookalike": t["lookalike"], "best_trigger": t["trigger"],
                          "triggers": e.get("triggers"), "description": e.get("description"),
                          "ai_assistant": e.get("ai_assistant"), "ai_assistant_vendor": e.get("ai_assistant_vendor"),
                          "tech": e.get("tech"), "community": e.get("community"), "hiring": e.get("hiring"),
                          "notes": e.get("notes")})
        names = [cu["name"] for cu in icp.get("customers") or []]

        def check(obj):
            out = _keyed(obj)
            for t in pending if not out else []:
                w = (obj.get("accounts") or {}).get(t["domain"])
                if not isinstance(w, dict):
                    out.append(f"no entry for {t['domain']}")
                elif not w.get("exclude"):
                    trial = dict(t, **{k: w.get(k) or "" for k in ("why", "trigger_text", "lookalike")})
                    out += [p for p in sc.check_top10([trial], icp["company"]["name"], names) if not p.startswith("top10 has")]
            return out

        ans = self.claude.ask_json("the picks", prompts.write_top10(icp, facts), keys=("accounts",), check=check, effort="high")
        top = self.load(c.TOP10)
        left_out = False
        for t in top:
            w = ans["accounts"].get(t["domain"])
            if not w:
                continue
            if w.get("exclude"):
                self._drop(t["domain"], w["exclude"])
                left_out = True
            else:
                t.update({k: w[k] for k in ("why", "trigger_text", "lookalike")})
        self.save(c.TOP10, top)
        return left_out

    def find_champions(self):
        """A seat with no champion (nobody fits at Apollo, or the reveal has no email) goes to the next account, so
        lemlist gets every lead, not the leads minus the misses. Reveals stay within one per lead."""
        self.stage(f"Stage 7: champions (free search, then at most {c.LEADS} Apollo credits)")
        for _ in range(CHAMPION_ROUNDS):
            top = self.load(c.TOP10)
            empty = [(t, "no champion at Apollo who fits the buyer") for t in self._pick_champions(top)]
            if not empty:
                self._reveal(top)
                champs = self.load(c.CHAMPIONS, {"accounts": {}})["accounts"]
                empty = [(t, "the champion Apollo revealed has no email") for t in top
                         if not (champs.get(t["domain"]) or {}).get("email")]
                if not empty or apollo_mod.BUDGET - apollo_mod.spent(self.meta["credits"]) <= 0:
                    break
            for t, why in empty:
                self._drop(t["domain"], why)
            self.log(f"  {len(empty)} seat(s) with no champion: the next account takes each")
            self._pick_ten()
        top = self.load(c.TOP10)
        champs = self.load(c.CHAMPIONS, {"accounts": {}})["accounts"]
        for t in top:
            x = champs.get(t["domain"])
            self.log(f"  {t['name']}: " + (f"{x['first_name']} {x['last_name']} ({x['title']}) [{x['email_status']}]"
                                           if x else "no champion"))

    def _pick_champions(self, top):
        """Free people search and Claude's pick for every account that has none yet. -> the accounts with nobody."""
        icp = self.load(c.ICP)
        champ = icp.get("champion") or {}
        people = self.load(PEOPLE, {})
        for t in top:
            if t["domain"] in people:
                continue
            tiers = [("titles", champ.get("titles"), True), ("fallback_titles", champ.get("fallback_titles"), True),
                     ("unverified", champ.get("titles"), False)]
            found = {"tier": None, "people": []}
            for tier, titles, verified in tiers:
                if not titles:
                    continue
                found = {"tier": tier, "people": self.apollo.search_people(t["domain"], titles, champ.get("seniorities"),
                                                                           verified)}
                if found["people"]:
                    break
            people[t["domain"]] = found
            self.save(PEOPLE, people)
            self.log(f"  {t['name']}: {len(found['people'])} candidates ({found['tier']})")
        picks = self.load(PICKS, {"picks": {}})
        todo = [t for t in top if t["domain"] not in picks["picks"]]
        cands = [{"domain": t["domain"], "name": t["name"], "employees": t["employees"],
                  "candidates": people[t["domain"]]["people"]} for t in todo if people[t["domain"]]["people"]]
        if cands:
            ids = {d: {p["id"] for p in people[d]["people"]} for d in people}

            def check(obj):
                out = _keyed(obj, "picks")
                for d, pick in (obj["picks"] if not out else {}).items():
                    if pick and (not isinstance(pick, dict) or pick.get("id") not in ids.get(d, set())):
                        out.append(f"{d}: the pick must be {{\"id\": ...}} with one of that account's candidate ids, or null")
                return out

            ans = self.claude.ask_json("champions", prompts.champions(icp, cands), keys=("picks",), check=check,
                                       effort="medium")
            picks["picks"].update({t["domain"]: ans["picks"].get(t["domain"]) for t in todo})
        for t in todo:
            picks["picks"].setdefault(t["domain"], None)
        self.save(PICKS, picks)
        return [t for t in top if not picks["picks"].get(t["domain"])]

    def _reveal(self, top):
        """Apollo's bulk match for the picks not revealed yet (1 credit each), then champions.json."""
        people = self.load(PEOPLE, {})
        picks = {d: p for d, p in self.load(PICKS)["picks"].items() if p}
        raw = self.load(APOLLO_MATCH_RAW, {"matches": [], "credits_consumed": 0})
        done = {m.get("id") for m in raw.get("matches") or [] if m} | set(raw.get("asked") or [])
        ids = [picks[t["domain"]]["id"] for t in top if t["domain"] in picks and picks[t["domain"]]["id"] not in done]
        left = apollo_mod.BUDGET - apollo_mod.spent(self.meta["credits"])
        if len(ids) > left:
            self.note(f"{len(ids) - max(left, 0)} champion(s) not revealed: the run's {apollo_mod.BUDGET} Apollo credits "
                      "are spent, revealing more needs Kevin's yes")
            ids = ids[:max(left, 0)]
        if ids:
            new = self.apollo.bulk_match(ids, self.meta["credits"])
            paid = new.get("credits_consumed")
            raw = {"matches": (raw.get("matches") or []) + (new.get("matches") or []),
                   "asked": sorted(done | set(ids)),
                   "credits_consumed": (raw.get("credits_consumed") or 0)
                   + (paid if paid is not None else sum(1 for m in new.get("matches") or [] if m))}
            self.save(APOLLO_MATCH_RAW, raw)
        out, credits, probs = ch.build(c.read_text(self.p(APOLLO_MATCH_RAW)), top, self.load(c.CHAMPIONS))
        for d, x in out["accounts"].items():
            if (people.get(d) or {}).get("tier") == "unverified" and "no verified email" not in x["note"]:
                x["note"] = "; ".join(n for n in (x["note"], "picked from people with no verified email") if n)
        self.save(c.CHAMPIONS, out)
        if credits is None:
            credits = sum(1 for m in c.load(self.p(APOLLO_MATCH_RAW)).get("matches") or [] if m)
        self.meta["credits"]["apollo_reveal"] = credits
        for p in probs:
            self.note(p)

    def write_sequence(self):
        self.stage("Stage 8: the sequence (Kevin's 3 touches)")
        icp, top = self.load(c.ICP), self.load(c.TOP10)
        champs = {"accounts": {d: x for d, x in self.load(c.CHAMPIONS, {"accounts": {}})["accounts"].items()
                               if x.get("email")}}   # only champions with an email get the sequence
        old = self.load(c.SEQUENCE)
        if not old or any(d not in (old.get("leads") or {}) for d in champs["accounts"] if d in {t["domain"] for t in top}):
            enr = self.load(c.ENRICHMENT, {}).get("accounts") or {}
            accounts = []
            for t in top:
                x = champs["accounts"].get(t["domain"])
                if not x or (old and t["domain"] in (old.get("leads") or {})):
                    continue
                e = enr.get(t["domain"]) or {}
                accounts.append({"domain": t["domain"], "name": t["name"], "segment": t["industry"],
                                 "champion": {"first_name": x["first_name"], "title": x["title"]},
                                 "why": t["why"], "trigger_text": t["trigger_text"], "trigger": t["trigger"],
                                 "lookalike": t["lookalike"], "description": e.get("description"),
                                 "ai_assistant": e.get("ai_assistant"), "community": e.get("community"),
                                 "notes": e.get("notes")})
            if not accounts:
                raise SystemExit("no champion with an email: nothing to write a sequence for")
            want = {a["domain"] for a in accounts}
            first = [True]

            def merged(seq):
                """A run that already loaded leads keeps its templates (they are lemlist's steps) and adds leads."""
                return dict(old, leads={**old.get("leads", {}), **(seq.get("leads") or {})}) if old else seq

            def check(seq):
                seq = merged(seq)
                if len(seq.get("touches") or []) != 3:
                    return ["sequence.json needs exactly 3 touches"]
                missing = sorted(want - set(seq.get("leads") or {}))
                if missing:
                    return [f"leads has no entry for {', '.join(missing)}"]
                try:
                    rendered = sq.render(seq, [t for t in top if t["domain"] in want], champs)
                except SystemExit as e:
                    return [str(e)]
                errors, warnings = sq.lint_all(rendered, seq.get("signature", "Kevin"))
                if first[0] and warnings:   # warnings go back once, to be read again as Kevin on a call
                    errors += [f"read it again as Kevin on a call, rewrite it or keep it: {w}" for w in warnings]
                first[0] = False
                return errors

            seq = merged(self.claude.ask_json("sequence", prompts.sequence(icp, accounts, old and old["touches"]),
                                              keys=("leads",) if old else ("touches", "leads"),
                                              check=check, effort="high", tries=4))
            sq.set_days(seq)
            seq.setdefault("company", icp["company"]["name"])
            seq.setdefault("signature", "Kevin")
            self.save(c.SEQUENCE, seq)
        seq = self.load(c.SEQUENCE)
        rendered = sq.render(seq, top, champs)
        c.write_text(self.p(c.EMAILS), sq.emails_md(rendered, seq))
        errors, warnings = sq.lint_all(rendered, seq.get("signature", "Kevin"))
        if errors:
            raise SystemExit("sequence lint:\n- " + "\n- ".join(errors))
        self.log(f"  emails.md: {len(rendered)} leads x 3 emails, voice lint OK ({len(warnings)} warnings kept)")

    def load_lemlist(self):
        self.stage("Stage 9: lemlist (draft, never launched)")
        prev = self.load(c.LEMLIST_RESULT)
        seq, top = sq.set_days(self.load(c.SEQUENCE)), self.load(c.TOP10)
        steps = sq.lemlist_steps(seq)
        leads, skipped = sq.lemlist_leads(seq, top, self.load(c.CHAMPIONS, {"accounts": {}}), self.meta["date"])
        self.save(c.LEMLIST_STEPS, steps)
        self.save(c.LEMLIST_LEADS, leads)
        for s in skipped:
            self.note(f"{s['account']}: {s['reason']}, not added to lemlist")
        if prev:   # a run topped up after it loaded: only the leads lemlist does not have yet
            have = {(r.get("email") or "").lower() for r in prev.get("results") or []}
            leads = [lead for lead in leads if lead["email"].lower() not in have]
            if not leads:
                self.log("  lemlist_result.json is there, every lead is in")
                return
        if not self.lemlist:
            self.log("  no LEMLIST_API_KEY: lemlist_steps.json and lemlist_leads.json are ready to load by hand")
            return
        name = campaign_name(self.company)
        lem = self.lemlist
        camp = {"_id": prev["campaign_id"]} if prev else lem.find_campaign(name)
        if camp:
            cid = camp["_id"]
            self.log(f"  campaign '{name}' exists: its steps stay, adding the leads")
        else:
            made = lem.create_campaign(name, self.timezone())
            cid = made["_id"]
            seq_id = made.get("sequenceId") or lem.first_sequence_id(cid)
            for s in steps:
                lem.add_step(seq_id, s)
            self.log(f"  created '{name}' with {len(steps)} steps")
        me = lem.my_sender()
        if me is None:
            self.note("lemlist shows no connected mailbox for this key: add yourself as the campaign's sender in lemlist "
                      "(or set LEMLIST_SENDER_EMAIL)")
        elif not ({x.lower() for x in lem.campaign_senders(cid) if x} & set(me["emails"])):
            try:
                lem.add_sender(cid, me)
                self.log(f"  sender: {me['email']}")
            except SystemExit as e:
                self.note(f"{e}: add yourself as the sender in lemlist")
        status, senders = lem.campaign_status(cid), [x for x in lem.campaign_senders(cid) if x]
        others = [x for x in senders if not me or x.lower() not in me["emails"]]
        if status != "draft" or others:
            raise SystemExit(f"lemlist campaign '{name}' reads status {status!r} with senders {senders!r}: it has to be "
                             "a draft with no sender but you before any lead goes in. Nothing was added; check it in lemlist")
        results = [dict(lem.add_lead(cid, lead), email=lead["email"]) for lead in leads]
        status = lem.campaign_status(cid)
        self.save(c.LEMLIST_RESULT, {"campaign_id": cid, "campaign_name": name, "status": status,
                                     "sender": me and me["email"], "delays": [s["delay"] for s in steps],
                                     "results": ((prev or {}).get("results") or []) + results})
        if status != "draft":
            raise SystemExit(f"lemlist campaign '{name}' reads {status!r} after adding the leads: pause it in lemlist now")
        self.log(f"  {sum(r['outcome'] == 'added' for r in results)} of {len(results)} leads added, campaign is a draft")

    def timezone(self):
        """The accounts' HQ timezone for the campaign schedule: America/New_York for a US list."""
        geos = [g.lower() for g in (self.load(c.ICP).get("scoring") or {}).get("geo_countries") or []]
        if not geos or "united states" in geos:
            return "America/New_York"
        self.note("the list is not US-only: the campaign uses lemlist's default timezone, Europe/Paris; check it")
        return "Europe/Paris"

    def finish(self):
        self.stage("Stage 10: report")
        icp, top = self.load(c.ICP), self.load(c.TOP10)
        champs, lem = self.load(c.CHAMPIONS, {"accounts": {}}), self.load(c.LEMLIST_RESULT, {})
        table = rep.rows(top, champs, lem)
        c.write_text(self.p(c.LEADS_CSV), rep.to_csv(table))
        sl = self.load(c.SHORTLIST, {})
        balance = self.apollo.credit_balance()
        if balance:
            self.meta["apollo_balance"] = balance
        self.save_meta()
        md = rep.to_md(self.meta, icp, self.load(c.LONGLIST), sl.get("shortlist", []), self.load(c.ENRICHMENT, {}),
                       table, lem, sl.get("triage_dropped", []))
        c.write_text(self.p(c.REPORT), md)
        rep.write_workbook(self.run)
        rep.write_docs(self.run)
        return self.summary(table, lem)

    def summary(self, table, lem):
        longlist, sl = self.load(c.LONGLIST), self.load(c.SHORTLIST, {})
        enr = self.load(c.ENRICHMENT, {}).get("accounts") or {}
        credits = self.meta["credits"]
        lines = [f"# {self.company}: {len(table)} leads, {sum(1 for r in table if r['champion_email'])} with a champion email", "",
                 "| # | Account | Champion | Title | Email | Trigger |", "|---|---|---|---|---|---|"]
        for r in table:
            lines.append(f"| {r['rank']} | {r['account']} | {r['champion'] or '-'} | {r['champion_title'] or '-'} | "
                         f"{r['champion_email'] or '-'} ({r['email_status']}) | {r['trigger']} |")
        clay = self.meta.get("clay") or {}
        lines += ["", f"Funnel: {longlist['searched']} searched, {longlist['kept']} kept, "
                      f"{len(sl.get('shortlist') or [])} shortlisted, {len(enr)} enriched "
                      f"({self.load(c.ENRICHMENT, {}).get('source', 'web')}), {len(table)} picked.",
                  f"Apollo credits: {sum(v for v in credits.values() if isinstance(v, (int, float)))} this run "
                  f"({', '.join(f'{k} {v}' for k, v in credits.items())})"
                  + (f", balance {self.meta['apollo_balance']}" if self.meta.get("apollo_balance") else "") + ".",
                  ("Claude Code on your plan: " if getattr(self.claude, "backend", "") == "code" else "Claude API: ")
                  + f"{self.claude.usage['web_search_requests']} web searches, "
                  f"{self.claude.usage['input_tokens'] + self.claude.usage['cache_read_input_tokens']:,} input and "
                  f"{self.claude.usage['output_tokens']:,} output tokens"
                  + (f", about ${self.claude.cost()}" if self.claude.cost() is not None else "") + ".",
                  (f"Clay: {clay.get('credits_used', '?')} credits on {clay.get('accounts')} accounts, "
                   f"{clay.get('credits_left', '?')} left." if clay else "Clay: not used."),
                  (f"lemlist: '{lem['campaign_name']}' is a {lem['status']} with "
                   f"{sum(r['outcome'] == 'added' for r in lem['results'])} new leads, sent from {lem.get('sender') or 'nobody yet'}, "
                   "never launched." if lem else
                   "lemlist: not loaded (no LEMLIST_API_KEY)."),
                  f"Files: {self.p(c.ACCOUNTS_XLSX)} (the 100, the {len(sl.get('shortlist') or [])} researched and the {len(table)} picked, and how), "
                  f"{self.p(c.LEADS_CSV)}, {self.p(c.EMAILS_DOCX)} and {self.p(c.REPORT_DOCX)} (Word), "
                  f"{self.p(c.EMAILS)}, {self.p(c.REPORT)}"]
        if self.meta["notes"]:
            lines += ["", "To check:"] + [f"- {n}" for n in self.meta["notes"]]
        return "\n".join(lines)


def check_connections():
    """Calls that cost nothing and prove each connection works: Claude (Claude Code signed in, or the Models API),
    Apollo (the key's profile), lemlist (team credits), Clay (the workspace and its credit balance).
    -> {"claude"|"apollo"|"lemlist"|"clay": {"ok": bool, "message": str}}"""
    from . import apollo as apollo_mod, clay as clay_mod, lemlist as lemlist_mod, llm
    out = {}
    try:
        claude_env = "ANTHROPIC_API_KEY" if llm.backend() == "api" else None
    except SystemExit as e:
        return {"claude": {"ok": False, "message": str(e)},
                **{k: {"ok": False, "message": "not checked"} for k in ("apollo", "lemlist", "clay")}}
    for name, env, check in (("claude", claude_env, llm.check),
                             ("apollo", "APOLLO_API_KEY", lambda: apollo_mod.Apollo.from_env().check()),
                             ("lemlist", "LEMLIST_API_KEY", lambda: lemlist_mod.Lemlist.from_env().check()),
                             ("clay", "CLAY_API_KEY", lambda: clay_mod.Clay.from_env().check())):
        if env and not os.environ.get(env):
            out[name] = {"ok": False, "message": f"no key yet ({env})"}
            continue
        try:
            out[name] = {"ok": True, "message": check()}
        except SystemExit as e:
            out[name] = {"ok": False, "message": str(e)}
        except Exception as e:   # network down, DNS, an answer in a shape nobody expected
            out[name] = {"ok": False, "message": f"{type(e).__name__}: {str(e)[:200]}"}
    return out


def run(company, domain=None, date=None, claude=None, apollo=None, lemlist=None, log=print):
    """Build the clients from the environment (Claude Code or ANTHROPIC_API_KEY, APOLLO_API_KEY, LEMLIST_API_KEY)
    and run it all."""
    from . import apollo as apollo_mod, clay as clay_mod, lemlist as lemlist_mod, llm
    date = date or c.today()
    claude = claude or llm.make(prompts.system(date), log=log)
    apollo = apollo or apollo_mod.Apollo.from_env()
    if lemlist is None and os.environ.get("LEMLIST_API_KEY"):
        lemlist = lemlist_mod.Lemlist.from_env()
    clay = clay_mod.Clay.from_env() if os.environ.get("CLAY_API_KEY") else None
    run_dir, status, old = new_run(company, domain, date)
    log(run_dir)
    log(status)
    pipe = Pipeline(run_dir, claude, apollo, lemlist, log=log, old_icp=old, clay=clay)
    try:
        return pipe.all()
    except (SystemExit, Exception) as e:
        pipe.meta["error"] = str(e) if isinstance(e, SystemExit) else f"{type(e).__name__}: {e}"
        raise
    finally:
        pipe.save_meta()
