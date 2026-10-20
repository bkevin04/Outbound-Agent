"""Tests for the deterministic steps. Run: python3 -m unittest discover -s tests"""
import copy
import json
import os
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

os.environ["OUTBOUND_TODAY"] = "2026-10-18"

from agent import champions, common, docx, icp as icp_mod, longlist, report, score, sequence, voice, xlsx  # noqa: E402


def read_xlsx(path):
    """{sheet name: [[cell text]]}, read back with the standard library. Every part of the file has to parse."""
    ns = {"m": xlsx.MAIN}
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            ET.fromstring(z.read(name))
        names = [s.get("name") for s in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", ns)]
        out = {}
        for i, name in enumerate(names, 1):
            data = ET.fromstring(z.read(f"xl/worksheets/sheet{i}.xml")).find("m:sheetData", ns)
            out[name] = [["".join(cell.itertext()) for cell in row] for row in data]
        return out
from agent.__main__ import main  # noqa: E402

ICP = {
    "company": {"name": "Acme", "slug": "acme", "domain": "acme.dev", "one_liner": "AI answers from your docs",
                "city": "New York"},
    "icp": {
        "headline": "Acme's ICP: <em>developer platforms</em>",
        "firmographics": ["Developer platforms first: US HQ, 50 to 1,000 employees.",
                          "Second track: fintech APIs of 50 to 500 employees."],
        "pain_quote": "“We answer the same questions every day.”",
        "pain_body": "Developers do not find the answer in the docs.",
        "check": ["Mapbox confirms the support lead as the buyer.",
                  "They do not yet prove it in fintech, which is why developer platforms rank first."],
        "funnel": [{"title": f"Stage {i}", "body": "x"} for i in range(5)],
    },
    "segments": [
        {"key": "devplat", "label": "Developer platform", "keywords": ["developer platform", "api", "sdk"],
         "lookalike": "Mapbox", "max_in_top10": 2},
        {"key": "fintech", "label": "Fintech API", "keywords": ["payments", "card issuing", "fintech"],
         "lookalike": "Airwallex"},
    ],
    "apollo_search": {"organization_locations": ["United States"], "per_page": 100},
    "scoring": {"employees": {"min": 50, "max": 1000, "sweet_min": 100, "sweet_max": 500},
                "geo_countries": ["United States"],
                "keyword_weights": {"api": 2, "developer platform": 2, "sdk": 1, "payments": 1},
                "negative_keywords": {"agency": -5},
                "tech_weights": {"mintlify": 2, "zendesk": 1},
                "trigger_weights": {"funding": 3, "launch": 2, "new_leader": 3, "other": 1},
                "hiring_keywords": ["developer advocate", "support engineer"]},
    "champion": {"titles": ["head of developer relations"]},
    "customers": [{"name": "Mapbox", "domain": "mapbox.com"}],
    "competitors": [{"name": "Rival", "domain": "rival.ai"}],
    "exclude": {"domains": ["modal.com"]},
}


def org(i, name, domain, employees=200, country="United States", keywords=("api", "sdk"), funding=None):
    return {"id": f"{i:024x}", "name": name, "primary_domain": domain, "website_url": f"http://www.{domain}",
            "estimated_num_employees": employees, "country": country, "city": "New York", "state": "New York",
            "keywords": list(keywords), "industry": "computer software", "latest_funding_round_date": funding}


def raw_search(orgs, accounts=()):
    body = json.dumps({"organizations": list(orgs), "accounts": list(accounts), "pagination": {"total_entries": 9}})
    return json.dumps([{"type": "text", "text": body}])


ORGS = [org(1, "Alpha", "alpha.dev", funding="2026-09-01"), org(2, "Beta", "beta.io"), org(3, "Gamma", "gamma.com"),
        org(4, "Delta Pay", "deltapay.com", keywords=("payments", "fintech", "api")),
        org(5, "Mapbox", "mapbox.com"), org(6, "Rival", "rival.ai"), org(7, "Huge", "huge.com", employees=20000),
        org(8, "Agency Co", "agency.co", keywords=("agency", "api")), org(9, "Modal", "modal.com")]

ENRICHMENT = {"source": "web", "accounts": {
    "alpha.dev": {"triggers": [{"type": "funding", "date": "2026-10-01", "text": "Raised a Series B", "url": "https://x/a"}],
                  "tech": ["Mintlify"], "community": ["Discord"], "ai_assistant": "no", "hiring": ["Developer Advocate"]},
    "beta.io": {"triggers": [{"type": "launch", "date": "2026-08-01", "text": "Launched v2", "url": "https://x/b"}],
                "ai_assistant": "yes"},
    "gamma.com": {"triggers": [{"type": "funding", "date": "2025-01-01", "text": "Old round", "url": "https://x/g"}]},
    "deltapay.com": {"triggers": [{"type": "new_leader", "date": "2026-09-20", "text": "New VP Eng", "url": "https://x/d"},
                                  {"type": "launch", "date": "2026-10-30", "text": "future", "url": "https://x/f"}]},
}}


class Helpers(unittest.TestCase):
    def test_slug_and_domain(self):
        self.assertEqual(common.slugify("Acme.dev"), "acmedev")
        self.assertEqual(common.slugify("Tiger Data"), "tigerdata")
        self.assertEqual(common.norm_domain("https://www.Mux.com/docs?x=1"), "mux.com")

    def test_parse_date(self):
        self.assertEqual(str(common.parse_date("14 Oct 2026")), "2026-10-14")
        self.assertEqual(str(common.parse_date("2026-07")), "2026-07-01")
        self.assertIsNone(common.parse_date("soon"))

    def test_extract_json_from_wrapped_text(self):
        text = "Here is the result:\n" + raw_search(ORGS[:1])
        found = common.extract_json(text, lambda o: isinstance(o, dict) and "organizations" in o)
        self.assertEqual(found["organizations"][0]["name"], "Alpha")


class IcpTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(icp_mod.problems(ICP), [])

    def test_catches_shape_and_words(self):
        bad = copy.deepcopy(ICP)
        bad["icp"]["firmographics"] = ["only one"]
        bad["apollo_search"]["per_page"] = 25
        bad["icp"]["pain_body"] = "the customer pattern"
        probs = " ".join(icp_mod.problems(bad))
        self.assertIn("firmographics", probs)
        self.assertIn("per_page", probs)
        self.assertIn("pattern", probs)

    def test_excluded(self):
        self.assertEqual(icp_mod.excluded_domains(ICP), {"acme.dev", "mapbox.com", "rival.ai", "modal.com"})


class LonglistTests(unittest.TestCase):
    def test_build_drops_with_reasons(self):
        acct = dict(org(10, "Saved", "saved.dev"), organization_id="b" * 24, id="c" * 24)
        out = longlist.build([raw_search(ORGS, [acct])], ICP, pitched=["gamma.com"])
        reasons = {d["domain"]: d["reason"] for d in out["dropped"]}
        self.assertEqual(reasons["mapbox.com"], "already a customer")
        self.assertEqual(reasons["rival.ai"], "competitor")
        self.assertEqual(reasons["modal.com"], "on the exclude list")
        self.assertEqual(reasons["gamma.com"], "already pitched in an earlier week")
        kept = {a["domain"]: a for a in out["accounts"]}
        self.assertEqual(kept["saved.dev"]["apollo_org_id"], "b" * 24)   # accounts bucket -> organization_id
        self.assertEqual(kept["alpha.dev"]["apollo_org_id"], f"{1:024x}")
        self.assertEqual(out["searched"], 10)

    def test_growth_is_kept_as_percent(self):
        o = dict(org(11, "Grow", "grow.dev"), organization_headcount_six_month_growth=0.42,
                 organization_headcount_twelve_month_growth=-0.055)
        a = longlist.normalize(o, "organizations")
        self.assertEqual((a["headcount_growth_6m"], a["headcount_growth_12m"]), (42, -6))


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.ll = longlist.build([raw_search(ORGS)], ICP)

    def test_pre_ranks_fit_over_misfits(self):
        ranked = score.rank_pre(self.ll, ICP)
        order = [r["domain"] for r in ranked]
        self.assertLess(order.index("alpha.dev"), order.index("huge.com"))
        self.assertLess(order.index("beta.io"), order.index("agency.co"))
        self.assertEqual(ranked[0]["domain"], "alpha.dev")   # recent funding on top of the same firmographics

    def test_triggers_window_and_future(self):
        trig = score.valid_triggers(ENRICHMENT["accounts"]["deltapay.com"], ICP)
        self.assertEqual([t["type"] for t in trig], ["new_leader"])   # the future-dated launch is ignored
        self.assertEqual(score.valid_triggers(ENRICHMENT["accounts"]["gamma.com"], ICP), [])

    def test_final_and_pick(self):
        ranked = score.rank_final(self.ll, ENRICHMENT, ICP)
        top = score.pick_top(ranked, ICP, n=4)
        doms = [t["domain"] for t in top]
        self.assertEqual(doms[0], "alpha.dev")
        self.assertIn("deltapay.com", doms[:3])
        self.assertFalse(top[0]["needs_trigger"])
        # devplat is capped at 2 triggered seats; the rest are filled and flagged
        self.assertTrue(any(t["needs_trigger"] for t in top))
        self.assertEqual(top[0]["lookalike"], "Mapbox")
        beta = next(r for r in ranked if r["domain"] == "beta.io")
        self.assertTrue(any("already runs an AI assistant" in x for x in beta["reasons"]))

    def test_triage_drops_and_sets_segment(self):
        tri = {"accounts": {"agency.co": {"drop": "services firm"},
                            "beta.io": {"segment": "fintech", "description": "Payments API"}}}
        ll = score.apply_triage(self.ll, tri)
        self.assertEqual([d["domain"] for d in ll["triage_dropped"]], ["agency.co"])
        beta = next(a for a in ll["accounts"] if a["domain"] == "beta.io")
        self.assertEqual(score.segment_of(beta, ICP)["key"], "fintech")

    def test_growth_points(self):
        a = dict(self.ll["accounts"][1], headcount_growth_6m=25)
        _, reasons, _ = score.firmographic(a, ICP)
        self.assertTrue(any("+25% in 6 months (+3)" in r for r in reasons))
        a["headcount_growth_6m"] = -8
        _, reasons, _ = score.firmographic(a, ICP)
        self.assertTrue(any("-8% in 6 months (-1)" in r for r in reasons))

    def test_merge_enrichment_and_existing_customer(self):
        part = {"accounts": {"Alpha.dev": {"acme_customer": True, "community": ["Discord"]},
                             "beta.io": dict(ENRICHMENT["accounts"]["beta.io"], acme_customer=False)}}
        enr = score.merge_enrichment([part], "web", "2026-10-18")
        self.assertTrue(enr["accounts"]["alpha.dev"]["existing_customer"])
        self.assertEqual(enr["accounts"]["alpha.dev"]["community"], ["discord"])
        ranked = score.rank_final(self.ll, enr, ICP)
        self.assertNotIn("alpha.dev", [r["domain"] for r in ranked])

    def test_check_top10(self):
        ranked = score.rank_final(self.ll, ENRICHMENT, ICP)
        top = score.pick_top(ranked, ICP, n=2)
        probs = score.check_top10(top, "Acme")
        self.assertTrue(any("needs 10" in p for p in probs))
        self.assertTrue(any("why is empty" in p for p in probs))
        top[0]["why"] = "New York API company with big docs, just like Mapbox (Acme's customer)."
        top[0]["trigger_text"] = "Raised a Series B on 1 Oct 2026."
        self.assertFalse(any(p.startswith("#1 ") for p in score.check_top10(top, "Acme")))
        top[0]["lookalike"] = "Stripe"
        top[0]["why"] = "API company, just like Stripe (Acme's customer)."
        self.assertTrue(any("not in icp.json customers" in p for p in score.check_top10(top, "Acme", ["Mapbox"])))


class VoiceTests(unittest.TestCase):
    def test_errors(self):
        errors, _ = voice.lint("We leverage a seamless pattern — truly; about ~$5B.")
        text = " ".join(errors)
        for word in ("leverage", "seamless", "pattern", "dash", "semicolon", "'~'"):
            self.assertIn(word, text)

    def test_clean_kevin_line(self):
        errors, warnings = voice.lint("I started with your case studies and the results are clear and credible.")
        self.assertEqual((errors, warnings), ([], []))

    def test_warnings(self):
        _, warnings = voice.lint("The docs are big, which is why support is busy.")
        self.assertTrue(any("'which'" in w for w in warnings))


SEQ = {
    "company": "Acme", "signature": "Kevin",
    "touches": [
        {"label": "TOUCH 1: DAY 1", "day": 1, "angle": "Open with the trigger", "subject": "{{t1Subject}}",
         "paragraphs": ["Hi {{firstName}},", "{{t1Opener}}", "That is what Acme does. {{t1Proof}}",
                        "We're in New York. Would you be open for a quick 15-minute conversation?",
                        "[I would attach a 30 second video]"]},
        {"label": "TOUCH 2: DAY 4", "day": 4, "angle": "New angle", "subject": "What Would Your Engineers Say?",
         "paragraphs": ["Hi {{firstName}},", "{{t2Hook}}", "Would 15 minutes be useful for {{companyName}}?"]},
        {"label": "TOUCH 3: DAY 9", "day": 9, "angle": "Close the loop", "subject": "Before Year-End Planning",
         "paragraphs": ["Hi {{firstName}},", "I'll keep this short, and it's my last note for now."]},
    ],
    "leads": {"alpha.dev": {"t1Subject": "Series B, Then What?", "t1Opener": "Congrats on the Series B.",
                            "t1Proof": "Mapbox cut tickets by 30%.", "t2Hook": "Every question costs time."}},
}
TOP = [{"rank": 1, "name": "Alpha", "domain": "alpha.dev", "industry": "Developer platform", "score": 12.5,
        "why": "just like Mapbox", "trigger_text": "Raised a Series B on 1 Oct 2026.",
        "trigger": {"type": "funding", "date": "2026-10-01", "url": "https://x/a"}},
       {"rank": 2, "name": "Beta", "domain": "beta.io", "industry": "Developer platform", "score": 9}]
CHAMPS = {"accounts": {"alpha.dev": {"apollo_id": "a" * 24, "first_name": "Ana", "last_name": "Lee",
                                     "title": "Head of DevRel", "email": "ana@alpha.dev", "email_status": "verified",
                                     "linkedin_url": "https://www.linkedin.com/in/ana"}}}


class SequenceTests(unittest.TestCase):
    def test_render_and_missing_var(self):
        rendered = sequence.render(SEQ, TOP, CHAMPS)
        self.assertEqual(len(rendered), 1)   # Beta has no champion
        e1 = rendered[0]["emails"][0]
        self.assertEqual(e1["subject"], "Series B, Then What?")
        self.assertEqual(e1["paragraphs"][0], "Hi Ana,")
        bad = copy.deepcopy(SEQ)
        del bad["leads"]["alpha.dev"]["t2Hook"]
        with self.assertRaises(SystemExit):
            sequence.render(bad, TOP, CHAMPS)

    def test_lemlist_payloads(self):
        steps = sequence.lemlist_steps(SEQ)
        self.assertEqual([s["delay"] for s in steps], [0, 3, 5])
        self.assertIn("{{t1Opener}}", steps[0]["body"])
        self.assertNotIn("attach", steps[0]["body"])   # bracketed notes stay out of the sent email
        self.assertTrue(steps[0]["body"].endswith("<div>Kevin</div>"))
        leads, skipped = sequence.lemlist_leads(SEQ, TOP, CHAMPS, "2026-10-18")
        self.assertEqual(len(leads), 1)
        self.assertEqual(skipped[0]["account"], "Beta")
        cv = leads[0]["customVariables"]
        self.assertEqual(set(cv) >= {"t1Subject", "t1Opener", "t1Proof", "t2Hook", "fitScore", "agentWeek"}, True)
        self.assertTrue(all(isinstance(v, str) for v in cv.values()))
        self.assertEqual(leads[0]["companyDomain"], "alpha.dev")

    def test_lint_all_flags_banned(self):
        bad = copy.deepcopy(SEQ)
        bad["leads"]["alpha.dev"]["t2Hook"] = "We leverage your docs."
        errors, _ = sequence.lint_all(sequence.render(bad, TOP, CHAMPS), "Kevin")
        self.assertTrue(any("leverage" in e for e in errors))


class ChampionTests(unittest.TestCase):
    def test_build_from_bulk_match(self):
        raw = json.dumps({"status": "success", "credits_consumed": 3, "matches": [
            {"id": "a" * 24, "first_name": "Ana", "last_name": "Lee", "title": "Head of DevRel", "email": "ana@alpha.dev",
             "email_status": "verified", "linkedin_url": "http://www.linkedin.com/in/ana",
             "organization": {"name": "Alpha", "primary_domain": "alpha.dev"}},
            {"id": "b" * 24, "first_name": "Bo", "last_name": "Kim", "title": "Director, Support", "email": "bo@betasource.com",
             "email_status": "verified", "organization": {"name": "Beta", "primary_domain": "beta.io"}},
            {"id": "c" * 24, "first_name": "Cy", "title": "CTO", "email": "cy@other.com", "email_status": "verified",
             "organization": {"name": "Other", "primary_domain": "other.com"}},
            None]})
        out, credits, probs = champions.build(raw, TOP, None)
        self.assertEqual(credits, 3)
        self.assertEqual(out["accounts"]["alpha.dev"]["linkedin_url"], "https://www.linkedin.com/in/ana")
        self.assertIn("email on betasource.com", out["accounts"]["beta.io"]["note"])
        self.assertTrue(any("not one of the 10" in p for p in probs))


class ReportTests(unittest.TestCase):
    def test_rows_and_csv(self):
        lem = {"campaign_name": "Outbound Agent · Acme", "status": "draft",
               "results": [{"email": "ana@alpha.dev", "outcome": "added"}]}
        table = report.rows(TOP, CHAMPS, lem)
        self.assertEqual(table[0]["champion_email"], "ana@alpha.dev")
        self.assertEqual(table[0]["lemlist"], "added")
        self.assertEqual(table[1]["email_status"], "no champion")
        csv_text = report.to_csv(table)
        self.assertTrue(csv_text.startswith("rank,account,domain"))


class XlsxTests(unittest.TestCase):
    def test_write_and_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.xlsx")
            xlsx.write(path, [
                {"name": "Top 10", "widths": [4, 20, 40], "header": ["#", "Account", "Why <they> fit & more"],
                 "rows": [[1, xlsx.link("Alpha", "https://alpha.dev"), "An API\x07 & docs"], [2, "Beta's", 2.5]],
                 "marks": {0: "pick"}, "freeze_cols": 2},
                {"name": "How: picked?", "widths": [30], "rows": [[xlsx.Cell("Title", "title")], ["x" * 40000]]},
                {"name": "Top 10", "widths": [4], "rows": [[1]]}])
            book = read_xlsx(path)
            self.assertEqual(list(book), ["Top 10", "How picked", "Top 10 2"])   # Excel's sheet name rules
            self.assertEqual(book["Top 10"][1], ["1", "Alpha", "An API & docs"])  # control character dropped
            self.assertEqual(book["Top 10"][0][2], "Why <they> fit & more")
            self.assertEqual(len(book["How picked"][1][0]), xlsx.MAX_CELL)
            with zipfile.ZipFile(path) as z:
                rels = z.read("xl/worksheets/_rels/sheet1.xml.rels").decode()
                sheet = z.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn('Target="https://alpha.dev" TargetMode="External"', rels)
            self.assertIn('state="frozen"', sheet)
            self.assertIn('<autoFilter ref="A1:C3"/>', sheet)
            self.assertIn('<c r="C2" s="6"', sheet)    # the picked row is shaded to the last column

    def test_word_document(self):
        md = ("# Acme outbound run\n\n**2 leads.** See [the docs](https://acme.dev/docs).\n\n## The 10\n\n"
              "| # | Account |\n|---|---|\n| 1 | Alpha & Co |\n\n- one\n- two\n\n## Next\n\n[I would attach a video]\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = docx.write(os.path.join(tmp, "r.docx"), md, title="Acme", break_before=2)
            with zipfile.ZipFile(path) as z:
                parts = {n: z.read(n).decode() for n in z.namelist()}
        for text in parts.values():
            ET.fromstring(text)                                   # every part is well-formed XML
        body = parts["word/document.xml"]
        self.assertEqual(body.count("<w:tbl>"), 1)
        self.assertIn("Alpha &amp; Co", body)
        self.assertEqual(body.count('<w:numId w:val="1"/>'), 2)     # two bullets
        self.assertEqual(body.count("<w:pageBreakBefore/>"), 1)     # the second ## starts a page, the first does not
        self.assertIn("<w:i/>", body)                               # the bracketed note
        self.assertIn('Target="https://acme.dev/docs" TargetMode="External"', parts["word/_rels/document.xml.rels"])

    def test_col_name(self):
        self.assertEqual([xlsx.col_name(i) for i in (0, 25, 26, 27, 701, 702)], ["A", "Z", "AA", "AB", "ZZ", "AAA"])


class CliTests(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = os.path.join(tmp, "run")
            os.makedirs(run)
            common.save(os.path.join(run, common.ICP), ICP)
            common.save(os.path.join(run, common.RUN), {"date": "2026-10-18", "credits": {"apollo": 3}})
            raw = os.path.join(tmp, "raw.txt")
            common.write_text(raw, raw_search(ORGS))
            main(["validate", run])
            main(["longlist", run, raw])
            main(["score", run, "--stage", "pre", "--shortlist", "3"])
            common.save(os.path.join(run, common.ENRICHMENT), ENRICHMENT)
            main(["score", run, "--stage", "final"])
            top = common.load(os.path.join(run, common.TOP10))
            self.assertEqual(top[0]["domain"], "alpha.dev")
            common.save(os.path.join(run, common.CHAMPIONS), CHAMPS)
            common.save(os.path.join(run, common.SEQUENCE), SEQ)
            main(["sequence", run, "render"])
            main(["sequence", run, "lint"])
            main(["sequence", run, "lemlist"])
            main(["report", run])
            self.assertIn("ana@alpha.dev", common.read_text(os.path.join(run, common.LEADS_CSV)))
            self.assertIn("The funnel in numbers", common.read_text(os.path.join(run, common.REPORT)))
            book = read_xlsx(os.path.join(run, common.ACCOUNTS_XLSX))
            ten, short, everyone, how = book.values()
            self.assertEqual(ten[0][:7], ["#", "Account", "Type", "Why they fit", "Trigger", "Champion", "Contact"])
            self.assertEqual(ten[1][1], "Alpha")
            self.assertEqual(ten[1][5:7], ["Ana Lee (Head of DevRel)", "ana@alpha.dev"])
            self.assertEqual(len(short), 1 + 3)
            searched = common.load(os.path.join(run, common.LONGLIST))["searched"]
            self.assertEqual(len(everyone), 1 + searched)    # every company the search found, dropped ones included
            outcomes = {r[1]: r[4] for r in everyone[1:]}
            self.assertEqual(outcomes["Alpha"], "Top 10, #1")
            self.assertEqual(outcomes["Mapbox"], "Out at the long list: already a customer")
            self.assertTrue(outcomes["Gamma"].endswith("(still needs a dated trigger)"))   # its only round is old
            self.assertIn("How the score works", [r[0] for r in how if r])


if __name__ == "__main__":
    unittest.main()
