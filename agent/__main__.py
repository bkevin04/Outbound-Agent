"""Command line for the outbound agent. `run` does the whole thing on its own (agent/pipeline.py: Claude through the
API, Apollo and lemlist through their REST APIs). The other commands are the deterministic steps the skill
(.claude/skills/outbound-agent/SKILL.md) runs between its research, connector calls and writing.

  python3 -m agent run "Acme" --domain acme.dev           the whole run, stages 0 to 10
  python3 -m agent dashboard                              the local dashboard: start, watch, schedule, delete runs
  python3 -m agent scheduled "Acme" --domain acme.dev    what a weekly schedule runs (today's run, logged for the dashboard)
  python3 -m agent check-keys                             test the Claude, Apollo and lemlist keys (free calls)
  python3 -m agent new "Acme" --domain acme.dev          make runs/<slug>/<date>/ (reuses companies/<slug>/icp.json)
  python3 -m agent validate <run>                         check icp.json
  python3 -m agent longlist <run> <raw-apollo-file>...    Apollo company search -> longlist.json
  python3 -m agent score <run> --stage pre                firmographic ranking -> shortlist.json
  python3 -m agent enrich-merge <run> <part>... --source web   enrichment batches -> enrichment.json
  python3 -m agent score <run> --stage final              + enrichment -> ranked.json, top10.json
  python3 -m agent check <run>                            top10.json ready for the champions stage?
  python3 -m agent champions <run> <raw-bulk-match-file>  Apollo reveal -> champions.json
  python3 -m agent sequence <run> render|lint|lemlist     emails.md / voice lint / lemlist steps + leads
  python3 -m agent report <run>                           leads.csv, report.md, accounts.xlsx, the .docx files
  python3 -m agent save-icp <run>                         keep the ICP in companies/<slug>/icp.json for next week
"""
import argparse
import json
import os
import sys

from . import champions as ch
from . import common as c
from . import icp as icp_mod
from . import longlist as ll
from . import pipeline as pl
from . import report as rep
from . import score as sc
from . import sequence as sq


def _p(run, name):
    return os.path.join(run, name)


def _run_meta(run):
    return c.load_optional(_p(run, c.RUN), {})


def cmd_new(a):
    run, status, _ = pl.new_run(a.company, a.domain, date=c.parse_date(a.date) if a.date else None)
    print(run)
    print(status)


def cmd_run(a):
    c.load_env()
    print("\n" + pl.run(a.company, a.domain, c.parse_date(a.date) if a.date else None))


def cmd_scheduled(a):
    """What a weekly schedule starts (agent/schedule.py): today's run for the company, started the way the dashboard
    starts one, so the dashboard shows it, and waited for."""
    from . import dashboard
    c.load_env()
    runs = dashboard.Runs()
    rid = runs.start(a.company, a.domain, note="started by the weekly schedule")
    proc = runs.procs.get(rid)
    if proc is None:
        print(f"{rid} is already running")
        return
    print(f"{rid}: started, log in {os.path.join(runs.path(rid), dashboard.LOG)}", flush=True)
    sys.exit(proc.wait())


def cmd_check_keys(a):
    c.load_env()
    res = pl.check_connections()
    if a.json:
        print(json.dumps(res))
    else:
        for name, r in res.items():
            print(f"{name:<8} {'OK    ' if r['ok'] else 'NOT OK'}  {r['message']}")
    if not (res["claude"]["ok"] and res["apollo"]["ok"]):
        sys.exit(1)


def cmd_dashboard(a):
    from . import dashboard
    dashboard.serve(a.port, open_browser=not a.no_open)


def cmd_validate(a):
    probs = icp_mod.problems(c.load(_p(a.run, c.ICP)))
    for p in probs:
        print("ICP:", p)
    if probs:
        sys.exit(1)
    print("icp.json OK")


def cmd_longlist(a):
    icp = c.load(_p(a.run, c.ICP))
    pitched = c.load_optional(_p(a.run, c.PITCHED), {}).get("domains", [])
    texts = [c.read_text(f) for f in a.raw]
    out = ll.build(texts, icp, pitched)
    c.save(_p(a.run, c.LONGLIST), out)
    print(f"long list: {out['kept']} kept of {out['searched']} searched, {len(out['dropped'])} dropped")
    for d in out["dropped"]:
        print(f"  dropped {d['name']} ({d['domain']}): {d['reason']}")


def cmd_score(a):
    icp = c.load(_p(a.run, c.ICP))
    longlist = sc.apply_triage(c.load(_p(a.run, c.LONGLIST)), c.load_optional(_p(a.run, c.TRIAGE), {}))
    if a.stage == "pre":
        ranked = sc.rank_pre(longlist, icp)
        n = a.shortlist or c.SHORTLIST_SIZE
        c.save(_p(a.run, c.SHORTLIST), {"ranked": ranked, "shortlist": [r["domain"] for r in ranked[:n]],
                                        "triage_dropped": longlist["triage_dropped"]})
        for d in longlist["triage_dropped"]:
            print(f"  dropped {d['name']} ({d['domain']}): {d['reason']}")
        for r in ranked[:n]:
            print(f"{r['rank']:>3}. {r['score']:>5} {r['name']} ({r['domain']}) [{r['segment']}]")
        return
    enrichment = c.load_optional(_p(a.run, c.ENRICHMENT), {})
    ranked = sc.rank_final(longlist, enrichment, icp)
    c.save(_p(a.run, c.RANKED), ranked)
    top = sc.pick_top(ranked, icp)
    old = {t["domain"]: t for t in c.load_optional(_p(a.run, c.TOP10), [])}
    for t in top:   # keep what was already written for an account that stays in the top 10
        for k in ("why", "trigger_text", "lookalike"):
            t[k] = (old.get(t["domain"]) or {}).get(k) or t[k]
    c.save(_p(a.run, c.TOP10), top)
    for t in top:
        trig = t["trigger"] or {}
        flag = "  NEEDS TRIGGER" if t["needs_trigger"] else ""
        print(f"{t['rank']:>2}. {t['score']:>5} {t['name']} ({t['domain']}) [{t['segment']}] "
              f"{trig.get('type', '')} {trig.get('date', '')}{flag}")


def cmd_enrich_merge(a):
    parts = [c.load(f) for f in a.parts]
    meta = _run_meta(a.run)
    out = sc.merge_enrichment(parts, a.source, meta.get("date") or c.today().isoformat())
    shortlist = set(c.load_optional(_p(a.run, c.SHORTLIST), {}).get("shortlist", []))
    c.save(_p(a.run, c.ENRICHMENT), out)
    accts = out["accounts"]
    icp = c.load(_p(a.run, c.ICP))
    print(f"enrichment: {len(accts)} accounts from {len(parts)} batches ({a.source})")
    for d in sorted(shortlist - set(accts)):
        print(f"  missing from the batches: {d}")
    for d, e in sorted(accts.items()):
        trig = sc.valid_triggers(e, icp)
        flag = "  EXISTING CUSTOMER (dropped)" if e.get("existing_customer") else ""
        best = f"{trig[0]['type']} {trig[0]['date']}" if trig else "no trigger in the window"
        print(f"  {d}: {best}; ai_assistant {e.get('ai_assistant', 'unknown')}{flag}")


def cmd_check(a):
    icp = c.load(_p(a.run, c.ICP))
    probs = sc.check_top10(c.load(_p(a.run, c.TOP10)), icp["company"]["name"],
                           [cu["name"] for cu in icp.get("customers") or []])
    for p in probs:
        print("TOP10:", p)
    if probs:
        sys.exit(1)
    print("top10.json OK")


def cmd_champions(a):
    top = c.load(_p(a.run, c.TOP10))
    out, credits, probs = ch.build(c.read_text(a.raw), top, c.load_optional(_p(a.run, c.CHAMPIONS)))
    c.save(_p(a.run, c.CHAMPIONS), out)
    meta = _run_meta(a.run)
    if credits is not None:
        meta.setdefault("credits", {})["apollo_reveal"] = credits
        c.save(_p(a.run, c.RUN), meta)
    for t in top:
        x = out["accounts"].get(t["domain"])
        if x:
            print(f"{t['rank']:>2}. {t['name']}: {x['first_name']} {x['last_name']} ({x['title']}) "
                  f"{x['email'] or 'NO EMAIL'} [{x['email_status']}]{'  ' + x['note'] if x['note'] else ''}")
    for p in probs:
        print("CHAMPIONS:", p)
    print(f"Apollo credits for the reveal: {credits}")


def cmd_sequence(a):
    seq = c.load(_p(a.run, c.SEQUENCE))
    top = c.load(_p(a.run, c.TOP10))
    champions = c.load_optional(_p(a.run, c.CHAMPIONS), {"accounts": {}})
    rendered = sq.render(seq, top, champions)
    if a.action == "render":
        c.write_text(_p(a.run, c.EMAILS), sq.emails_md(rendered, seq))
        print(f"wrote {_p(a.run, c.EMAILS)}: {len(rendered)} leads x 3 emails")
    elif a.action == "lint":
        errors, warnings = sq.lint_all(rendered, seq.get("signature", "Kevin"))
        for w in warnings:
            print("warn:", w)
        for e in errors:
            print("ERROR:", e)
        if errors:
            sys.exit(1)
        print(f"voice lint OK ({len(warnings)} warnings to read again)")
    elif a.action == "lemlist":
        meta = _run_meta(a.run)
        steps = sq.lemlist_steps(seq)
        leads, skipped = sq.lemlist_leads(seq, top, champions, meta.get("date") or c.today().isoformat())
        c.save(_p(a.run, c.LEMLIST_STEPS), steps)
        c.save(_p(a.run, c.LEMLIST_LEADS), leads)
        print(f"lemlist: {len(steps)} steps, {len(leads)} leads; skipped: {skipped or 'none'}")


def cmd_report(a):
    icp = c.load(_p(a.run, c.ICP))
    top = c.load(_p(a.run, c.TOP10))
    champions = c.load_optional(_p(a.run, c.CHAMPIONS), {"accounts": {}})
    lem = c.load_optional(_p(a.run, c.LEMLIST_RESULT), {})
    table = rep.rows(top, champions, lem)
    c.write_text(_p(a.run, c.LEADS_CSV), rep.to_csv(table))
    sl = c.load_optional(_p(a.run, c.SHORTLIST), {})
    md = rep.to_md(_run_meta(a.run), icp, c.load(_p(a.run, c.LONGLIST)), sl.get("shortlist", []),
                   c.load_optional(_p(a.run, c.ENRICHMENT), {}), table, lem, sl.get("triage_dropped", []))
    c.write_text(_p(a.run, c.REPORT), md)
    print(f"wrote {_p(a.run, c.LEADS_CSV)}, {_p(a.run, c.REPORT)}, {rep.write_workbook(a.run)} and "
          f"{', '.join(rep.write_docs(a.run))}")


def cmd_save_icp(a):
    icp = c.load(_p(a.run, c.ICP))
    text = c.read_text(_p(a.run, c.ICP))
    if "@" in text:
        sys.exit("icp.json contains an '@': no email addresses go into companies/ (the repo is public)")
    icp["updated"] = _run_meta(a.run).get("date") or c.today().isoformat()
    dest = os.path.join(c.COMPANIES, icp["company"]["slug"], c.ICP)
    c.save(dest, icp)
    print(f"saved {dest}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m agent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    n = s.add_parser("new")
    n.add_argument("company")
    n.add_argument("--domain")
    n.add_argument("--date")
    n.set_defaults(f=cmd_new)
    r = s.add_parser("run")
    r.add_argument("company")
    r.add_argument("--domain")
    r.add_argument("--date")
    r.set_defaults(f=cmd_run)
    x = s.add_parser("scheduled", help="what a weekly schedule runs: today's run, logged for the dashboard")
    x.add_argument("company")
    x.add_argument("--domain")
    x.set_defaults(f=cmd_scheduled)
    k = s.add_parser("check-keys")
    k.add_argument("--json", action="store_true")
    k.set_defaults(f=cmd_check_keys)
    d = s.add_parser("dashboard")
    d.add_argument("--port", type=int, default=8765)
    d.add_argument("--no-open", action="store_true", help="do not open the browser")
    d.set_defaults(f=cmd_dashboard)
    for name, f in (("validate", cmd_validate), ("check", cmd_check), ("report", cmd_report), ("save-icp", cmd_save_icp)):
        x = s.add_parser(name)
        x.add_argument("run")
        x.set_defaults(f=f)
    x = s.add_parser("longlist")
    x.add_argument("run")
    x.add_argument("raw", nargs="+")
    x.set_defaults(f=cmd_longlist)
    x = s.add_parser("score")
    x.add_argument("run")
    x.add_argument("--stage", choices=("pre", "final"), required=True)
    x.add_argument("--shortlist", type=int)
    x.set_defaults(f=cmd_score)
    x = s.add_parser("champions")
    x.add_argument("run")
    x.add_argument("raw")
    x.set_defaults(f=cmd_champions)
    x = s.add_parser("enrich-merge")
    x.add_argument("run")
    x.add_argument("parts", nargs="+")
    x.add_argument("--source", required=True, choices=("clay", "web", "clay+web"))
    x.set_defaults(f=cmd_enrich_merge)
    x = s.add_parser("sequence")
    x.add_argument("run")
    x.add_argument("action", choices=("render", "lint", "lemlist"))
    x.set_defaults(f=cmd_sequence)
    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
