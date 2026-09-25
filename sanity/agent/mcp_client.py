"""A minimal MCP client for Sanity Context (Streamable HTTP transport), stdlib only.

Sanity Context MCP lives at
    https://api.sanity.io/v1/context/organizations/<orgId>/mcp/<endpointName>
and needs an ORGANIZATION token with Context Viewer permissions (project tokens are
rejected). JSON-RPC 2.0 goes over POST; a reply is either JSON or a short
text/event-stream. The server may hand out an Mcp-Session-Id, which is sent back.
"""
import json
import os
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"


class MCPError(Exception):
    pass


class MCPClient:
    def __init__(self, url, token, timeout=60):
        self.url, self.token, self.timeout = url, token, timeout
        self.session = None
        self._id = 0
        self.tools = []

    @classmethod
    def from_env(cls, url_env="SANITY_CONTEXT_MCP_URL", token_env="SANITY_CONTEXT_TOKEN"):
        url, token = os.environ.get(url_env), os.environ.get(token_env)
        if not url or not token:
            raise SystemExit(f"set {url_env} and {token_env} (an ORGANIZATION token with Context Viewer)")
        return cls(url, token)

    # ------------------------------------------------------------ transport
    def _post(self, payload, expect_reply=True):
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "Authorization": f"Bearer {self.token}",
                   "MCP-Protocol-Version": PROTOCOL}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, json.dumps(payload).encode(), headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                self.session = r.headers.get("Mcp-Session-Id") or self.session
                body = r.read().decode("utf-8", "replace")
                ctype = r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            hint = " (a PROJECT token is rejected: use an organization token)" if e.code in (401, 403) else ""
            raise MCPError(f"HTTP {e.code} from Context MCP{hint}: {detail}") from None
        if not expect_reply:
            return None
        return self._decode(body, ctype, payload.get("id"))

    @staticmethod
    def _decode(body, ctype, want_id):
        msgs = []
        if "text/event-stream" in ctype:
            for block in body.split("\n\n"):
                data = "".join(line[5:].strip() for line in block.splitlines() if line.startswith("data:"))
                if data:
                    msgs.append(json.loads(data))
        elif body.strip():
            parsed = json.loads(body)
            msgs = parsed if isinstance(parsed, list) else [parsed]
        for m in msgs:
            if m.get("id") == want_id:
                if "error" in m:
                    raise MCPError(f"MCP error {m['error'].get('code')}: {m['error'].get('message')}")
                return m.get("result")
        raise MCPError("no reply to the request in the MCP response")

    def _call(self, method, params=None):
        self._id += 1
        return self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})

    # ------------------------------------------------------------ MCP
    def connect(self):
        info = self._call("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                         "clientInfo": {"name": "toolsmith-kb-agent", "version": "1.0"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_reply=False)
        self.tools = self._call("tools/list").get("tools", [])
        return info

    def call_tool(self, name, arguments=None):
        """-> the tool's text output (content blocks joined). Tool errors raise MCPError."""
        res = self._call("tools/call", {"name": name, "arguments": arguments or {}})
        text = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        if res.get("isError"):
            raise MCPError(f"{name}: {text[:300]}")
        return text

    def groq(self, query):
        """groq_query, parsed. Context wraps results with a `meta` block; return the rows."""
        text = self.call_tool("groq_query", {"query": query})
        try:
            data = json.loads(text)
        except ValueError:
            raise MCPError(f"groq_query did not return JSON: {text[:200]}") from None
        return data.get("result", data) if isinstance(data, dict) else data


class PublicGroq:
    """The same GROQ, straight from the PUBLIC dataset API: used by the tests and by
    `--offline`, never by the demo (which goes through Context MCP)."""

    def __init__(self, project="en9phc0q", dataset="toolsmith", api="v2025-02-19"):
        self.base = f"https://{project}.apicdn.sanity.io/{api}/data/query/{dataset}"

    SCHEMA = (
        "Offline stand-in for Context MCP's initial_context. Types: toolArea{areaId.current,title,keywords[]}; "
        "run{runId,area->toolArea,startedAt,gaps[]}; fact{factId,run->run,kind,statement,"
        "measurements[]{label,value,unit},licenceSpdx,source}; candidateTool{name,area->,homepage,"
        "licenceSpdx,licenceClass}; recommendation{recId,run->run,title,verdict,candidate->candidateTool,"
        "claim,citedFacts[]->fact,numbersUsed[]{value,unit,fact->fact},textValidator{status,reasons[]}}. "
        "Recommendation _id = rec-<runId>-<n>. Areas: run.area->areaId.current, e.g. 'reverse-proxy', "
        "'observability'. Example: *[_type=='recommendation' && run->area->areaId.current=='reverse-proxy']"
        "{_id,title,verdict}")

    def groq(self, query):
        import urllib.parse
        try:
            with urllib.request.urlopen(f"{self.base}?query={urllib.parse.quote(query)}", timeout=30) as r:
                return json.load(r)["result"]
        except urllib.error.HTTPError as e:
            raise MCPError(f"GROQ HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}") from None
