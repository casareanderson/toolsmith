"""Toolsmith KB agent: ask what to do about a tool area; get only recommendations whose
numbers are backed by the facts they cite.

    python -m agent "Should we replace our reverse proxy?"            # via Sanity Context MCP
    python -m agent --trace "What did the observability runs find?"
    python -m agent --offline "..."                                  # public GROQ API (tests/dev only)

Environment:
    SANITY_CONTEXT_MCP_URL   https://api.sanity.io/v1/context/organizations/<org>/mcp/<name>  (GROQ mode)
    SANITY_CONTEXT_KB_URL    optional second endpoint serving the Knowledge Base (KB mode)
    SANITY_CONTEXT_TOKEN     organization token, Context Viewer
    OPENROUTER_API_KEY       or LLM_API_KEY + LLM_BASE_URL for any OpenAI-compatible endpoint
    LLM_MODEL                default qwen/qwen3.7-flash

The model explores the knowledge base freely (initial_context, schema_explorer,
groq_query, knowledge_base_read), but it can only PRESENT a recommendation through
`present_recommendation`, which fetches the recommendation with its cited facts joined in
through Context MCP and runs validator.validate(). A final answer may only contain numbers
that a shown recommendation or the question itself contains; anything else is sent back
once, then replaced with [not verified].
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from .mcp_client import MCPClient, MCPError, PublicGroq
from .validator import REC_QUERY, prose_numbers, redact_numbers, validate

SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")

SYSTEM = """You are Toolsmith's knowledge-base assistant for a self-hosted homelab. The knowledge
base (Sanity, reached through Context MCP) holds research runs, gathered facts with typed
measurements, candidate tools and recommendations.

How to work:
1. Call initial_context first to learn the schema and how to query.
2. Use groq_query (and schema_explorer when unsure) to find the runs, facts and
   recommendations relevant to the question. Recommendation document ids look like
   "rec-<runId>-<n>".
3. To PRESENT any recommendation, call present_recommendation with its _id. Only its output
   may be repeated as a recommendation. If it says WITHHELD, say it was withheld and give
   the validator's reasons in plain words; never restate a withheld claim's numbers.
4. If a Knowledge Base tool (knowledge_base_read) is available, use it to explain how the
   research or the validator works.
Be short and concrete. Every number you write must come from a present_recommendation
result or from the user's question."""

PRESENT_TOOL = {
    "type": "function",
    "function": {
        "name": "present_recommendation",
        "description": ("Fetch ONE recommendation with its cited facts joined in, run the structured "
                        "validator, and return it if its numbers are backed (status shown) or the "
                        "reasons it is WITHHELD. The only way to present a recommendation."),
        "parameters": {"type": "object", "properties": {"id": {"type": "string",
                       "description": "the recommendation document _id, e.g. rec-20260917-1030-2"}},
                       "required": ["id"]},
    },
}


# ---------------------------------------------------------------- LLM (OpenAI-compatible)
class LLM:
    def __init__(self):
        self.key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        self.base = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        self.model = os.environ.get("LLM_MODEL", "qwen/qwen3.7-flash")
        if not self.key:
            raise SystemExit("set OPENROUTER_API_KEY (or LLM_API_KEY + LLM_BASE_URL)")

    def chat(self, messages, tools):
        body = json.dumps({"model": self.model, "messages": messages, "tools": tools,
                           "max_tokens": 2500, "temperature": 0.2}).encode()
        for attempt, wait in enumerate((2, 6, 15, None)):
            req = urllib.request.Request(f"{self.base}/chat/completions", body, {
                "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.load(r)["choices"][0]["message"]
            except urllib.error.HTTPError as e:
                if e.code not in (408, 429, 500, 502, 503, 504) or wait is None:
                    raise SystemExit(f"LLM HTTP {e.code}: {e.read()[:200]!r}") from None
            time.sleep(wait)


# ---------------------------------------------------------------- the agent
class Agent:
    def __init__(self, groq_backend, kb=None, llm=None, trace=False):
        self.mcp = groq_backend          # MCPClient (Context) or PublicGroq (offline)
        self.kb = kb                     # optional MCPClient on a Knowledge Base endpoint
        self.llm = llm or LLM()
        self.trace = trace
        self.shown_numbers = set()

    def log(self, *a):
        if self.trace:
            print("  ·", *a, file=sys.stderr)

    def tools(self):
        out = [PRESENT_TOOL]
        for client, prefix in ((self.mcp, ""), (self.kb, "kb_")):
            for t in getattr(client, "tools", []) or []:
                if client is self.kb and t["name"] != "knowledge_base_read" and t["name"] != "initial_context":
                    continue
                out.append({"type": "function", "function": {
                    "name": prefix + t["name"], "description": t.get("description", "")[:1000],
                    "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}})
        if isinstance(self.mcp, PublicGroq):
            out += [{"type": "function", "function": {
                "name": "initial_context", "description": "Schema overview and how to query. Call first.",
                "parameters": {"type": "object", "properties": {}}}},
                {"type": "function", "function": {
                "name": "groq_query", "description": "Run a GROQ query against the dataset.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                               "required": ["query"]}}}]
        return out

    def present(self, rec_id):
        if not SAFE_ID.match(rec_id or ""):
            return {"status": "withheld", "reasons": ["not a valid document id"]}
        try:
            rec = self.mcp.groq(REC_QUERY.replace("$id", json.dumps(rec_id)))
        except MCPError as e:
            return {"status": "withheld", "reasons": [f"could not fetch it: {e}"]}
        v = validate(rec)
        out = {"id": rec_id, "status": v["status"]}
        if rec:
            out.update(title=rec.get("title"), verdict=rec.get("verdict"),
                       run=(rec.get("run") or {}).get("runId"), area=(rec.get("run") or {}).get("area"))
        if v["status"] == "shown":
            out.update(claim=rec["claim"], backed_by=v["checked"])
            self.shown_numbers |= set(prose_numbers(rec["claim"]))
        else:
            out["WITHHELD_because"] = v["reasons"]
        return out

    def run_tool(self, name, args):
        if name == "present_recommendation":
            return json.dumps(self.present(args.get("id")))
        client = self.kb if name.startswith("kb_") else self.mcp
        name = name[3:] if name.startswith("kb_") else name
        try:
            if isinstance(client, PublicGroq):
                if name == "initial_context":
                    return client.SCHEMA
                return json.dumps(client.groq(args.get("query", "")))[:12000]
            return client.call_tool(name, args)[:12000]
        except MCPError as e:
            return f"error: {e}"

    def answer(self, question, max_rounds=16):
        if isinstance(self.mcp, MCPClient):
            self.mcp.connect()
        if isinstance(self.kb, MCPClient):
            self.kb.connect()
        tools = self.tools()
        self.log("tools:", ", ".join(t["function"]["name"] for t in tools))
        allowed = set(prose_numbers(question))
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
        nudged = False
        for _ in range(max_rounds):
            m = self.llm.chat(msgs, tools)
            calls = m.get("tool_calls") or []
            msgs.append({"role": "assistant", "content": m.get("content"), "tool_calls": calls or None})
            if not calls:
                text = m.get("content") or ""
                bad = [n for n in prose_numbers(text) if n not in self.shown_numbers and n not in allowed]
                if bad and not nudged:
                    nudged = True
                    self.log("guard: unbacked numbers", bad)
                    msgs.append({"role": "user", "content": (
                        "[check] Your answer states numbers that no shown recommendation contains: "
                        f"{', '.join(f'{b:g}' for b in bad)}. Remove them or present the recommendation "
                        "that holds them.")})
                    continue
                return redact_numbers(text, bad)
            for c in calls:
                fn = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except ValueError:
                    args = {}
                self.log(fn, json.dumps(args)[:160])
                out = self.run_tool(fn, args)
                if fn == "present_recommendation":
                    self.log("  →", json.loads(out)["status"])
                msgs.append({"role": "tool", "tool_call_id": c["id"], "content": out})
        return "I ran out of steps before finishing; try a narrower question."


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m agent")
    ap.add_argument("question")
    ap.add_argument("--trace", action="store_true", help="print every tool call to stderr")
    ap.add_argument("--offline", action="store_true", help="public GROQ API instead of Context MCP (dev only)")
    a = ap.parse_args(argv)
    if a.offline:
        backend, kb = PublicGroq(), None
    else:
        backend = MCPClient.from_env()
        kb_url = os.environ.get("SANITY_CONTEXT_KB_URL")
        kb = MCPClient(kb_url, os.environ["SANITY_CONTEXT_TOKEN"]) if kb_url else None
    print(Agent(backend, kb, trace=a.trace).answer(a.question))


if __name__ == "__main__":
    main()
