"""Tests for the standalone run (agent/pipeline.py) and its clients, with Claude, Apollo and lemlist faked.
Standard library only, like the rest: python3 -m unittest discover -s tests"""
import copy
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ["OUTBOUND_TODAY"] = "2026-10-18"

from agent import apollo, clay, common, lemlist, llm, pipeline, rest  # noqa: E402
from test_agent import ENRICHMENT, ICP, ORGS, SEQ, read_xlsx  # noqa: E402

FULL_ICP = copy.deepcopy(ICP)
FULL_ICP["customers"].append({"name": "Airwallex", "domain": "airwallex.com"})
FULL_ICP["company"].update({"ceo_first_name": "Dana"})


def _accounts(prompt):
    """The JSON account lines a prompt lists, as the fake reads them back."""
    out = []
    for line in prompt.splitlines():
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if "domain" in obj:
                out.append(obj)
    return out


class FakeClaude:
    model = "fake"

    def __init__(self):
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0, "web_search_requests": 0}
        self.steps, self.prompts = [], {}

    def cost(self):
        return None

    def ask_json(self, step, prompt, keys, check=None, **kw):
        self.steps.append(step)
        self.prompts[step] = prompt
        ans = self.answer(step, prompt)
        problems = check(ans) if check else []
        if problems:
            raise AssertionError(f"{step}: the fake answer fails the check: {problems}")
        return ans

    def answer(self, step, prompt):
        if step == "research":
            return {"company": {"name": "Acme"}, "customers": FULL_ICP["customers"], "competitors": [], "proof_points": []}
        if step == "icp":
            return copy.deepcopy(FULL_ICP)
        if step == "triage":
            acc = {a["domain"]: {"segment": "devplat", "description": "an API"} for a in _accounts(prompt)}
            acc["deltapay.com"] = {"segment": "fintech", "description": "card issuing API"}
            acc["agency.co"] = {"drop": "an agency"}
            return {"accounts": acc, "new_segments": []}
        if step.startswith("enrich"):
            return {"accounts": {a["domain"]: ENRICHMENT["accounts"].get(a["domain"], {"triggers": []})
                                 for a in _accounts(prompt)}}
        if step.startswith("trigger"):
            return {"triggers": []}
        if step == "the 10":
            out = {}
            for a in _accounts(prompt):
                if a["domain"] == "beta.io":
                    out[a["domain"]] = {"exclude": "tied to a competitor"}
                    continue
                look = a["segment_lookalike"]
                out[a["domain"]] = {"lookalike": look, "why": f"{a['name']} runs an API, just like {look} (Acme's customer).",
                                    "trigger_text": f"{a['best_trigger']['text']} on {a['best_trigger']['date']}."}
            return {"accounts": out}
        if step == "champions":
            return {"picks": {a["domain"]: {"id": a["candidates"][0]["id"], "reason": "head of DevRel"}
                              for a in _accounts(prompt)}}
        if step == "sequence":
            seq = copy.deepcopy(SEQ)
            one = seq["leads"].pop("alpha.dev")
            seq["leads"] = {a["domain"]: dict(one) for a in _accounts(prompt)}
            return seq
        raise AssertionError(f"unexpected step {step}")


class FakeApollo:
    def __init__(self):
        self.paid = []

    def search_companies(self, params, credits):
        self.paid.append(("search", 1))
        return {"organizations": ORGS, "accounts": []}

    def search_people(self, domain, titles, seniorities=None, verified=True, per_page=10):
        return [{"id": f"p-{domain}", "first_name": "Ana", "last_name": "L***e", "title": "Head of DevRel",
                 "organization": domain, "has_email": True}]

    def bulk_match(self, ids, credits):
        self.paid.append(("reveal", len(ids)))
        return {"credits_consumed": len(ids), "matches": [
            {"id": i, "first_name": "Ana", "last_name": "Lee", "title": "Head of DevRel", "email": f"ana@{i[2:]}",
             "email_status": "verified", "organization": {"primary_domain": i[2:]}} for i in ids]}

    def credit_balance(self):
        return {"num_credits_remaining": 90}


class FakeLemlist:
    def __init__(self):
        self.steps, self.leads, self.status, self.senders = [], [], "draft", []

    def find_campaign(self, name):
        return None

    def lead_domains(self, cid):
        return []

    def create_campaign(self, name, timezone):
        self.name, self.tz = name, timezone
        return {"_id": "cam_1", "sequenceId": "seq_1"}

    def add_step(self, seq_id, step):
        self.steps.append(step)

    def campaign_status(self, cid):
        return self.status

    def campaign_senders(self, cid):
        return list(self.senders)

    def my_sender(self):
        return {"userId": "usr_1", "mailboxId": "usm_1", "email": "kevin@kevinbui.dev", "emails": ["kevin@kevinbui.dev"]}

    def add_sender(self, cid, sender):
        self.senders.append(sender["email"])

    def add_lead(self, cid, lead):
        self.leads.append(lead)
        return {"outcome": "added", "leadId": f"lea_{len(self.leads)}"}


class FakeClay:
    def __init__(self):
        self.balances, self.runs = [90, 80], []

    def balance(self):
        return self.balances.pop(0)

    def run_routine(self, routine, items):
        self.runs.append((routine, items))
        return {it["id"]: {"latest_funding": "Series B"} for it in items}


class PipelineTests(unittest.TestCase):
    def run_all(self, tmp, lem):
        run, status, _ = pipeline.new_run("Acme", "acme.dev", runs=os.path.join(tmp, "runs"),
                                          companies=os.path.join(tmp, "companies"))
        self.assertIn("no saved ICP", status)
        claude, ap = FakeClaude(), FakeApollo()
        pipe = pipeline.Pipeline(run, claude, ap, lem, companies=os.path.join(tmp, "companies"), log=lambda *a: None)
        return run, pipe, claude, ap, pipe.all()

    def test_full_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            lem = FakeLemlist()
            run, pipe, claude, ap, summary = self.run_all(tmp, lem)
            top = common.load(os.path.join(run, common.TOP10))
            self.assertEqual([t["domain"] for t in top], ["alpha.dev", "deltapay.com"])   # beta.io left out
            self.assertTrue(os.path.exists(os.path.join(tmp, "companies", "acme", common.ICP)))
            self.assertEqual(common.load(os.path.join(run, common.TRIAGE))["accounts"]["beta.io"]["drop"],
                             "tied to a competitor")
            self.assertEqual(ap.paid, [("search", 1), ("reveal", 2)])
            self.assertEqual(lem.tz, "America/New_York")
            self.assertEqual([s["delay"] for s in lem.steps], [0, 2, 2])
            self.assertEqual(lem.senders, ["kevin@kevinbui.dev"])   # Kevin is the sender, and nothing launched it
            res = common.load(os.path.join(run, common.LEMLIST_RESULT))
            self.assertEqual((res["status"], res["sender"], res["delays"]), ("draft", "kevin@kevinbui.dev", [0, 2, 2]))
            self.assertEqual({lead["email"] for lead in lem.leads}, {"ana@alpha.dev", "ana@deltapay.com"})
            csv_text = common.read_text(os.path.join(run, common.LEADS_CSV))
            self.assertIn("ana@deltapay.com", csv_text)
            book = read_xlsx(os.path.join(run, common.ACCOUNTS_XLSX))
            self.assertEqual(list(book), ["Top 2", "Top 5", "All 9", "How they were picked"])
            outcomes = {r[1]: r[4] for r in book["Top 5"][1:]}
            self.assertEqual(outcomes["Beta"], "Out after research: tied to a competitor")
            self.assertEqual(outcomes["Delta Pay"], "Top 10, #2")
            self.assertIn("accounts.xlsx", summary)
            meta = common.load(os.path.join(run, common.RUN))
            self.assertEqual(meta["credits"], {"apollo_search": 1, "apollo_reveal": 2})
            self.assertIn("only 2 accounts", summary)
            self.assertIn("is a draft with 2 new leads", summary)

            # A second pass over the same folder redoes nothing paid and asks Claude nothing.
            before = len(claude.steps)
            again = pipeline.Pipeline(run, claude, ap, lem, companies=os.path.join(tmp, "companies"), log=lambda *a: None)
            again.all()
            self.assertEqual(ap.paid, [("search", 1), ("reveal", 2)])
            self.assertEqual(len(claude.steps), before)
            self.assertEqual(len(lem.leads), 2)

    def test_clay_feeds_the_research(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CLAY_ROUTINE_ID": "function:t_1"}):
            run, _, _ = pipeline.new_run("Acme", "acme.dev", runs=os.path.join(tmp, "runs"),
                                         companies=os.path.join(tmp, "companies"))
            claude, cl = FakeClaude(), FakeClay()
            pipeline.Pipeline(run, claude, FakeApollo(), None, companies=os.path.join(tmp, "companies"),
                              log=lambda *a: None, clay=cl).all()
            [(routine, items)] = cl.runs
            self.assertEqual(routine, "function:t_1")
            self.assertEqual(items[0]["inputs"].keys(), {"domain", "name"})
            self.assertIn("Clay already ran", claude.prompts["enrich 1"])
            self.assertIn("Series B", claude.prompts["enrich 1"])
            self.assertEqual(common.load(os.path.join(run, common.ENRICHMENT))["source"], "clay+web")
            self.assertEqual(common.load(os.path.join(run, common.RUN))["clay"]["credits_used"], 10)

    def test_api_key_in_the_function_id_is_refused(self):
        env = {"CLAY_ROUTINE_ID": "clay_secret_123", "CLAY_API_KEY": "clay_secret_123"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env):
            run, _, _ = pipeline.new_run("Acme", "acme.dev", runs=os.path.join(tmp, "runs"),
                                         companies=os.path.join(tmp, "companies"))
            cl = FakeClay()
            pipeline.Pipeline(run, FakeClaude(), FakeApollo(), None, companies=os.path.join(tmp, "companies"),
                              log=lambda *a: None, clay=cl).all()
            self.assertEqual(cl.runs, [])
            meta = common.read_text(os.path.join(run, common.RUN))
            self.assertIn("function ID holds a Clay API key", meta)
            self.assertNotIn("clay_secret", meta + common.read_text(os.path.join(run, common.REPORT)))

    def test_stops_when_someone_else_sends(self):
        with tempfile.TemporaryDirectory() as tmp:
            lem = FakeLemlist()
            lem.senders = ["someone@else.com"]
            with self.assertRaises(SystemExit) as e:
                self.run_all(tmp, lem)
            self.assertIn("no sender but you", str(e.exception))
            self.assertEqual(lem.leads, [])

    def test_stops_when_campaign_is_not_a_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            lem = FakeLemlist()
            lem.status = "running"
            with self.assertRaises(SystemExit) as e:
                self.run_all(tmp, lem)
            self.assertIn("has to be a draft", str(e.exception))
            self.assertEqual(lem.leads, [])

    def test_runs_without_lemlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, _, _, _, summary = self.run_all(tmp, None)
            self.assertIn("not loaded", summary)
            self.assertEqual(len(common.load(os.path.join(run, common.LEMLIST_LEADS))), 2)


def _msg(text, stop="end_turn"):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                            server_tool_use=SimpleNamespace(web_search_requests=1))
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop, usage=usage,
                           stop_details=None)


class FakeAnthropic:
    """client.beta.messages.stream(...) handing back canned messages in order."""

    def __init__(self, messages):
        self.queue, self.calls = list(messages), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **params):
        self.calls.append(params)
        msg = self.queue.pop(0)

        class Ctx:
            def __enter__(self):
                return SimpleNamespace(get_final_message=lambda: msg)

            def __exit__(self, *a):
                return False
        return Ctx()


class ClaudeTests(unittest.TestCase):
    def test_pause_turn_then_fix_loop(self):
        fake = FakeAnthropic([_msg("searching...", "pause_turn"), _msg('Here: ```json\n{"a": 1}\n```'),
                              _msg('```json\n{"a": 2}\n```')])
        cl = llm.Claude("system", client=fake, model="claude-opus-5-5", log=lambda *a: None)
        out = cl.ask_json("step", "go", keys=("a",), check=lambda o: [] if o["a"] == 2 else ["a must be 2"], searches=3)
        self.assertEqual(out, {"a": 2})
        self.assertEqual(len(fake.calls), 3)
        last = fake.calls[-1]
        self.assertEqual(last["tools"][0]["type"], "web_search_20260209")
        self.assertEqual(last["fallbacks"], "default")
        roles = [m["role"] for m in last["messages"]]   # the same list, appended to after the call
        self.assertEqual(roles, ["user", "assistant", "assistant", "user", "assistant"])
        self.assertIn("a must be 2", last["messages"][3]["content"])
        self.assertEqual(cl.usage["web_search_requests"], 3)
        self.assertIsNotNone(cl.cost())

    def test_gives_up(self):
        fake = FakeAnthropic([_msg("no json")] * 2)
        cl = llm.Claude("system", client=fake, log=lambda *a: None)
        with self.assertRaises(SystemExit):
            cl.ask_json("step", "go", keys=("a",), tries=2)


def _cli(answers):
    """A stand-in for `claude -p --output-format stream-json`: for each answer, one assistant event per search
    ("_searches") and the result event, one JSON event a line. Records every call."""
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        res = dict(answers.pop(0), type="result")
        search = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebSearch", "input": {}}]}}
        events = [{"type": "system"}] + [search] * res.pop("_searches", 0) + [res]
        return SimpleNamespace(stdout="\n".join(json.dumps(e) for e in events), stderr="", returncode=0)
    return run, calls


class ClaudeCodeTests(unittest.TestCase):
    def test_session_fix_loop_on_the_plan(self):
        run, calls = _cli([
            {"result": "```json\n{\"a\": 1}\n```", "session_id": "s1", "is_error": False, "_searches": 4,
             "usage": {"input_tokens": 5, "output_tokens": 2}},
            {"result": '{"a": 2}', "session_id": "s1", "is_error": False, "usage": {}}])
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret"}):
            cc = llm.ClaudeCode("sys", log=lambda *a: None, cli="claude", runner=run)
            out = cc.ask_json("step", "go", keys=("a",), check=lambda o: [] if o["a"] == 2 else ["a must be 2"],
                              searches=3, effort="medium")
        self.assertEqual(out, {"a": 2})
        (first, kw1), (second, kw2) = calls
        self.assertNotIn("ANTHROPIC_API_KEY", kw1["env"])   # always the plan, never an API key
        self.assertEqual(kw1["input"], "go")
        self.assertIn("--safe-mode", first)
        self.assertEqual(first[first.index("--tools") + 1], "WebSearch,WebFetch")
        self.assertEqual(first[first.index("--effort") + 1], "medium")
        self.assertNotIn("--resume", first)
        self.assertEqual(second[second.index("--resume") + 1], "s1")
        self.assertIn("a must be 2", kw2["input"])
        self.assertEqual(cc.usage["web_search_requests"], 4)
        self.assertIsNone(cc.cost())

    def test_no_tools_without_search_and_errors_stop(self):
        run, calls = _cli([{"result": "boom", "is_error": True, "subtype": "error_during_execution"}])
        cc = llm.ClaudeCode("sys", log=lambda *a: None, cli="claude", runner=run)
        with self.assertRaises(SystemExit):
            cc.ask_json("step", "go", keys=("a",))
        cmd = calls[0][0]
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")

    def test_signed_in_check(self):
        def status(body):
            return lambda cmd, **kw: SimpleNamespace(stdout=json.dumps(body), stderr="", returncode=0)
        with mock.patch.object(llm, "find_cli", return_value="claude"):
            self.assertIn("max plan", llm.check_code(runner=status({"loggedIn": True, "subscriptionType": "max"})))
            with self.assertRaises(SystemExit):
                llm.check_code(runner=status({"loggedIn": False}))

    def test_backend_choice(self):
        with mock.patch.dict(os.environ, {"OUTBOUND_CLAUDE": "nope"}):
            with self.assertRaises(SystemExit):
                llm.backend()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(llm.backend(), "code")


class ClientTests(unittest.TestCase):
    def test_query_encoding(self):
        q = rest.query({"organization_locations": ["United States"], "total_funding_range": {"min": 25000000},
                        "per_page": 100, "skip": None, "flag": False})
        self.assertEqual(q, "organization_locations%5B%5D=United+States&total_funding_range%5Bmin%5D=25000000"
                            "&per_page=100&flag=false")

    def test_apollo_budget(self):
        ap = apollo.Apollo("k")
        with mock.patch.object(rest, "request", return_value={"matches": []}) as req:
            ap.bulk_match(["x"] * 10, {"apollo_search": 1})
            self.assertEqual(json.dumps(req.call_args.kwargs["body"]).count('"id"'), 10)
            self.assertTrue(req.call_args.kwargs["paid"])
            with self.assertRaises(SystemExit):
                ap.bulk_match(["x"] * 10, {"apollo_search": 1, "apollo_reveal": 1})
            with self.assertRaises(SystemExit):
                ap.search_companies({}, {"apollo_search": 1, "apollo_reveal": 10})

    def test_connection_checks(self):
        with mock.patch.dict(os.environ, {"OUTBOUND_CLAUDE": "api"}, clear=True):
            res = pipeline.check_connections()
        self.assertEqual({k: v["ok"] for k, v in res.items()},
                         {"claude": False, "apollo": False, "lemlist": False, "clay": False})
        self.assertIn("no key yet", res["apollo"]["message"])
        refused = rest.HTTPError(401, "{}", "u")
        with mock.patch.object(rest, "request", side_effect=refused):
            with self.assertRaises(SystemExit) as e:
                apollo.Apollo("k").check()
            self.assertIn("refused the key", str(e.exception))
            with self.assertRaises(SystemExit):
                lemlist.Lemlist("k").check()
        with mock.patch.object(rest, "request", return_value={"num_credits_remaining": 1200, "credits": 50}) as req:
            self.assertIn("1,200 credits left", apollo.Apollo("k").check())
            self.assertFalse(req.call_args.kwargs.get("paid"))
            self.assertIn("50 lemlist credits", lemlist.Lemlist("k").check())
        env = {"OUTBOUND_CLAUDE": "api", "ANTHROPIC_API_KEY": "a", "APOLLO_API_KEY": "b", "LEMLIST_API_KEY": "c",
               "CLAY_API_KEY": "d"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(llm, "check", return_value="ready"), \
                mock.patch.object(rest, "request", return_value={}):
            res = pipeline.check_connections()
        self.assertTrue(all(v["ok"] for v in res.values()))

    def test_clay_balance_and_routine(self):
        cl = clay.Clay("k", sleep=lambda s: None)
        answers = [{"user": {"id": 1}, "workspace": {"name": "Kevin's workspace"}}, {"balance": 87}]
        with mock.patch.object(rest, "request", side_effect=answers) as req:
            self.assertEqual(cl.check(), "the key works for Kevin's workspace, 87 credits left")
        self.assertEqual(req.call_args.args[2], {"clay-api-key": "k"})
        with mock.patch.dict(os.environ, {"CLAY_ROUTINE_ID": "clay_abc"}):
            with mock.patch.object(rest, "request", side_effect=rest.HTTPError(401, "{}", "u")), \
                    self.assertRaises(SystemExit) as e:
                cl.check()
            self.assertIn("make a new one", str(e.exception))
            self.assertIn("function ID holds a Clay API key", str(e.exception))
            with mock.patch.object(rest, "request", side_effect=[{}, {"balance": 5}]), self.assertRaises(SystemExit) as e:
                cl.check()
            self.assertTrue(str(e.exception).startswith("the key works, 5 credits left, but"))
        answers = [{"routine_run_id": "r1", "status": "in_progress"},
                   {"routine_run_id": "r1", "status": "in_progress", "total": 2, "finished": 1},
                   {"routine_run_id": "r1", "status": "complete", "cursor": "c2",
                    "data": [{"id": "a.dev", "status": "complete", "result": {"funding": "Series A"}}]},
                   {"routine_run_id": "r1", "status": "complete",
                    "data": [{"id": "b.io", "status": "failed", "error": {"message": "no match"}}]}]
        with mock.patch.object(rest, "request", side_effect=answers) as req:
            out = cl.run_routine("function:t_1", [{"id": "a.dev", "inputs": {"domain": "a.dev"}},
                                                  {"id": "b.io", "inputs": {"domain": "b.io"}}])
        self.assertEqual(out, {"a.dev": {"funding": "Series A"}, "b.io": {"error": "no match"}})
        self.assertTrue(req.call_args_list[0].kwargs["paid"])                     # the run is never sent twice
        self.assertIn("cursor=c2", req.call_args_list[3].args[1])

    def test_lemlist_sender(self):
        lem = lemlist.Lemlist("k")
        senders = {"data": {"senders": [
            {"userId": "usr_1", "email": "pat@team.com", "channels": [{"type": "email", "id": "usm_1", "label": "pat@team.com"}]},
            {"userId": "usr_2", "email": "kevin@team.com", "channels": [{"type": "email", "id": "usm_2", "label": "kevin@team.com"}]}]}}
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(rest, "request", side_effect=[{"email": {"accounts": [{"id": "usm_2"}]}}, senders]):
            me = lem.my_sender()
        self.assertEqual((me["userId"], me["mailboxId"], me["email"]), ("usr_2", "usm_2", "kevin@team.com"))
        with mock.patch.dict(os.environ, {"LEMLIST_SENDER_EMAIL": "Pat@team.com"}), \
                mock.patch.object(rest, "request", side_effect=[senders]):
            self.assertEqual(lem.my_sender()["userId"], "usr_1")
        no_plan = rest.HTTPError(402, "{}", "u")
        with mock.patch.object(rest, "request", side_effect=[no_plan, {}]) as req:
            lem.add_sender("cam_1", me)
        self.assertEqual([c.args[0] for c in req.call_args_list], ["PATCH", "POST"])
        self.assertEqual(req.call_args_list[1].kwargs["body"]["senders"][0]["channels"][0]["id"], "usm_2")

    def test_lemlist_lead_is_flat_and_never_enriched(self):
        lem = lemlist.Lemlist("k")
        lead = {"email": "a@b.co", "firstName": "A", "customVariables": {"t1Opener": "Hi"}}
        with mock.patch.object(rest, "request", return_value={"_id": "lea_1"}) as req:
            self.assertEqual(lem.add_lead("cam_1", lead), {"outcome": "added", "leadId": "lea_1"})
        url = req.call_args.args[1]
        self.assertIn("deduplicate=true", url)
        for flag in ("findEmail", "verifyEmail", "linkedinEnrichment", "findPhone"):
            self.assertIn(f"{flag}=false", url)
        self.assertEqual(req.call_args.kwargs["body"], {"email": "a@b.co", "firstName": "A", "t1Opener": "Hi"})
        err = rest.HTTPError(400, json.dumps({"code": "LEAD_ALREADY_IN_CAMPAIGN"}), "u")
        with mock.patch.object(rest, "request", side_effect=err):
            self.assertEqual(lem.add_lead("cam_1", lead)["outcome"], "already in this campaign")
        self.assertFalse(any(hasattr(lem, m) for m in ("start", "launch", "start_campaign", "review_lead", "pause")))


if __name__ == "__main__":
    unittest.main()
