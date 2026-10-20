"""Claude for the judgement steps: research, the ICP, triage, enrichment, the 10, the champions and the emails.

Two ways to reach Claude, picked with OUTBOUND_CLAUDE:
- "code" (the default): Claude Code (`claude -p`) on your Claude plan, signed in the way Claude Code already is.
  No API key and no per-call bill; a run counts against the plan's usage limits.
- "api": the Claude API with ANTHROPIC_API_KEY, billed per token (the `anthropic` package).

Every step asks for one JSON answer, checks it with the same rules the scripts enforce and, when something is wrong,
sends the problems back in the same conversation for a fix. The deterministic steps stay standard library only."""
import json
import os
import shutil
import subprocess
import tempfile
import threading

from .common import extract_json

MODEL = os.environ.get("OUTBOUND_MODEL", "claude-opus-5-5")
MAX_CONTINUATIONS = 5          # pause_turn resumes per answer (web search runs a server-side loop)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
CODE_TIMEOUT = 1800            # seconds for one Claude Code answer (the research step searches for minutes)

# $ per million tokens, and per web search, for the cost line in the report. Unknown models report tokens only.
PRICES = {"claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.20, "cache_write": 5.0},
          "claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_read": 0.20, "cache_write": 2.5}}
WEB_SEARCH_PRICE = 0.01


def backend():
    b = os.environ.get("OUTBOUND_CLAUDE", "code").strip().lower()
    if b not in ("code", "api"):
        raise SystemExit(f"OUTBOUND_CLAUDE={b!r}: use 'code' (Claude Code on your plan) or 'api' (ANTHROPIC_API_KEY)")
    return b


def make(system, log=print):
    """The Claude client the environment asks for."""
    return ClaudeCode(system, log=log) if backend() == "code" else Claude(system, log=log)


def check():
    """A call that costs nothing and proves Claude can be reached the way the run will reach it."""
    return check_code() if backend() == "code" else check_api()


def sdk():
    """The anthropic package, imported only when the API is called so everything else runs without it."""
    try:
        import anthropic
    except ImportError:
        raise SystemExit("the anthropic package is missing: pip install -r requirements.txt") from None
    if int(anthropic.__version__.split(".")[0]) < 1:
        raise SystemExit(f"anthropic {anthropic.__version__} is too old: pip install -r requirements.txt")
    return anthropic


def check_api(model=MODEL):
    """The Models API: proves the key works and the model is open to it."""
    anthropic = sdk()
    try:
        m = anthropic.Anthropic().models.retrieve(model)
    except anthropic.AuthenticationError:
        raise SystemExit("Anthropic refused the key: check it in the Claude Console") from None
    except anthropic.NotFoundError:
        raise SystemExit(f"{model} is not open to this key") from None
    return f"{m.display_name} is ready (Claude API)"


def find_cli():
    cli = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not os.path.exists(cli):
        raise SystemExit("Claude Code is not installed (no `claude` command): install it, or set OUTBOUND_CLAUDE=api")
    return cli


def plan_env():
    """The environment for Claude Code without any API key, so it always runs on the plan it is signed in with."""
    return {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}


def check_code(runner=subprocess.run):
    """`claude auth status`: proves Claude Code is signed in, without using the plan."""
    out = runner([find_cli(), "auth", "status", "--json"], env=plan_env(), capture_output=True, text=True, timeout=30)
    try:
        st = json.loads(out.stdout)
    except ValueError:
        raise SystemExit(f"Claude Code did not say whether it is signed in: {(out.stderr or out.stdout)[:200]}") from None
    if not st.get("loggedIn"):
        raise SystemExit("Claude Code is not signed in: run `claude` in a terminal and sign in")
    plan = st.get("subscriptionType")
    return f"Claude Code is signed in{f' ({plan} plan)' if plan else ''}"


def web_tools(searches, fetches=None):
    """The server-side web tools, capped per answer. They run on Anthropic's side: nothing to implement here."""
    return [{"type": "web_search_20260209", "name": "web_search", "max_uses": searches},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": fetches if fetches is not None else searches}]


class _Asker:
    """ask_json on top of a conversation: subclasses give `_conversation(...)` -> say(message) -> answer text."""
    workers = 6     # enrichment batches at a time

    def _new_usage(self):
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0, "web_search_requests": 0}
        self._lock = threading.Lock()

    def ask_json(self, step, prompt, keys, check=None, effort="high", searches=0, fetches=None,
                 max_tokens=64000, tries=3):
        """Ask for one JSON object that has every key in `keys`. check(obj) -> list of problems; while it finds any,
        they go back to Claude in the same conversation, at most `tries` answers in all."""
        say = self._conversation(step, effort, searches, fetches, max_tokens)
        message, problems = prompt, []
        for attempt in range(1, tries + 1):
            text = say(message)
            obj = extract_json(text, lambda o: isinstance(o, dict) and all(k in o for k in keys))
            problems = ([f"no JSON object with the keys {', '.join(keys)} in the answer"] if obj is None
                        else list(check(obj) if check else []))
            if not problems:
                return obj
            self.log(f"  {step}: answer {attempt} has {len(problems)} problem(s), asking for a fix")
            message = ("Fix these problems and send the whole JSON object again, nothing else changed:\n- "
                       + "\n- ".join(problems[:40]))
        raise SystemExit(f"{step}: still {len(problems)} problem(s) after {tries} answers:\n- " + "\n- ".join(problems[:20]))


class Claude(_Asker):
    """The Claude API. One client for the run; thread-safe, so the enrichment batches can run in parallel."""
    backend = "api"

    def __init__(self, system, client=None, model=MODEL, log=print):
        if client is None:
            client = sdk().Anthropic()
        self.client, self.model, self.system, self.log = client, model, system, log
        self._new_usage()

    def _count(self, msg):
        u = msg.usage
        with self._lock:
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                self.usage[k] += getattr(u, k, 0) or 0
            stu = getattr(u, "server_tool_use", None)
            self.usage["web_search_requests"] += getattr(stu, "web_search_requests", 0) or 0

    def cost(self):
        p = PRICES.get(self.model)
        if not p:
            return None
        u = self.usage
        return round((u["input_tokens"] * p["in"] + u["output_tokens"] * p["out"]
                      + u["cache_read_input_tokens"] * p["cache_read"]
                      + u["cache_creation_input_tokens"] * p["cache_write"]) / 1e6
                     + u["web_search_requests"] * WEB_SEARCH_PRICE, 2)

    def _answer(self, messages, step, effort, tools, max_tokens):
        """One assistant answer, resumed through pause_turn. Appends every assistant turn to messages."""
        texts = []
        for _ in range(MAX_CONTINUATIONS + 1):
            params = dict(model=self.model, max_tokens=max_tokens, system=self.system, messages=messages,
                          cache_control={"type": "ephemeral"}, output_config={"effort": effort},
                          betas=[FALLBACK_BETA], fallbacks="default")
            if tools:
                params["tools"] = tools
            with self.client.beta.messages.stream(**params) as stream:
                msg = stream.get_final_message()
            self._count(msg)
            messages.append({"role": "assistant", "content": msg.content})
            texts += [b.text for b in msg.content if b.type == "text"]
            if msg.stop_reason == "pause_turn":
                continue
            if msg.stop_reason == "refusal":
                raise SystemExit(f"{step}: Claude declined the request ({getattr(msg.stop_details, 'category', None)})")
            if msg.stop_reason == "max_tokens":
                raise SystemExit(f"{step}: the answer hit max_tokens ({max_tokens}); raise it for this step")
            return "".join(texts)
        raise SystemExit(f"{step}: still paused after {MAX_CONTINUATIONS} continuations")

    def _conversation(self, step, effort, searches, fetches, max_tokens):
        messages, tools = [], web_tools(searches, fetches) if searches else None

        def say(text):
            messages.append({"role": "user", "content": text})
            return self._answer(messages, step, effort, tools, max_tokens)
        return say


class ClaudeCode(_Asker):
    """Claude Code (`claude -p`) on the plan it is signed in with. Each step is one Claude Code session in an empty
    folder, with every customization off (--safe-mode: no CLAUDE.md, skills, plugins, hooks or MCP servers), this
    agent's system prompt, and no tools but WebSearch and WebFetch, and those only when the step searches. A fix goes
    back with --resume into the same session. The search caps are in the prompts: Claude Code has no per-call cap."""
    backend = "code"
    workers = 3     # Claude Code sessions at a time, kinder to the plan's rate limits

    def __init__(self, system, model=None, log=print, cli=None, runner=subprocess.run):
        self.cli = cli or find_cli()
        self._model_arg = model or os.environ.get("OUTBOUND_MODEL")
        self.model = self._model_arg or "Claude Code"
        self.system, self.log, self.run = system, log, runner
        self.workdir = tempfile.mkdtemp(prefix="outbound-claude-")
        self._new_usage()

    def cost(self):
        return None     # on the plan: no per-call bill

    def _count(self, res, searches):
        u = res.get("usage") or {}
        with self._lock:
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                self.usage[k] += u.get(k) or 0
            self.usage["web_search_requests"] += searches

    @staticmethod
    def _read(stdout):
        """Claude Code's stream (one JSON event a line) -> (the result event, how many web searches it ran).
        The result's usage leaves out Claude Code's own WebSearch calls, so they are counted from the stream."""
        res, searches = None, 0
        for line in (stdout or "").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                searches += sum(1 for b in (ev.get("message") or {}).get("content") or []
                                if b.get("type") == "tool_use" and b.get("name") == "WebSearch")
            elif ev.get("type") == "result":
                res = ev
        return res, searches

    def _call(self, step, message, effort, web, session):
        cmd = [self.cli, "-p", "--output-format", "stream-json", "--verbose", "--safe-mode", "--permission-prompts", "none",
               "--system-prompt", self.system, "--effort", effort, "--tools", "WebSearch,WebFetch" if web else ""]
        if web:
            cmd += ["--allowedTools", "WebSearch,WebFetch"]
        if self._model_arg:
            cmd += ["--model", self._model_arg]
        if session:
            cmd += ["--resume", session]
        try:
            out = self.run(cmd, input=message, cwd=self.workdir, env=plan_env(), capture_output=True, text=True,
                           timeout=CODE_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise SystemExit(f"{step}: Claude Code took longer than {CODE_TIMEOUT // 60} minutes") from None
        res, searches = self._read(out.stdout)
        if not res:
            raise SystemExit(f"{step}: Claude Code gave no answer (exit {out.returncode}): "
                             f"{(out.stderr or out.stdout or '').strip()[-300:]}")
        self._count(res, searches)
        if res.get("is_error"):
            raise SystemExit(f"{step}: Claude Code stopped ({res.get('subtype')}): {str(res.get('result') or '')[:300]}")
        return res.get("result") or "", res.get("session_id")

    def _conversation(self, step, effort, searches, fetches, max_tokens):
        state = {"session": None}

        def say(text):
            answer, state["session"] = self._call(step, text, effort, bool(searches), state["session"])
            return answer
        return say
