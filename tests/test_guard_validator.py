"""Stdlib-only checks of the two mechanical controls: the guard hook and the validator.

    python3 -m unittest discover -s tests
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {**os.environ, "HERMES_HOME": self.tmp,
                    "TOOLSMITH_HOME": os.path.join(self.tmp, "toolsmith"),
                    "TOOLSMITH_CONF": os.path.join(self.tmp, "none.conf"),
                    "TOOLSMITH_PRIVATE_DOMAINS": "home.example.org"}
        os.makedirs(os.path.join(self.tmp, "toolsmith", "workspace"))
        os.makedirs(os.path.join(self.tmp, "toolsmith", "sandbox"))


class GuardTest(Base):
    def guard(self, tool, **args):
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "toolsmith_guard.py")],
                           input=json.dumps({"tool_name": tool, "tool_input": args}),
                           capture_output=True, text=True, env=self.env, timeout=15)
        return json.loads(p.stdout)

    def blocked(self, tool, **args):
        return self.guard(tool, **args).get("decision") == "block"

    def test_unknown_tool_blocked(self):
        self.assertTrue(self.blocked("ssh_exec", command="uptime"))

    def test_shell_syntax_blocked(self):
        self.assertTrue(self.blocked("terminal", command="cat /etc/passwd | head"))
        self.assertTrue(self.blocked("terminal", command="ls; rm -rf /"))

    def test_sandbox_allowed(self):
        sb = os.path.join(SCRIPTS, "toolsmith_sandbox.sh")
        self.assertFalse(self.blocked("terminal", command=f"{sb} hosts"))

    def test_reads_confined(self):
        self.assertTrue(self.blocked("read_file", path="/etc/shadow"))
        self.assertFalse(self.blocked("read_file", path=os.path.join(self.tmp, "toolsmith", "workspace", "x.json")))
        self.assertTrue(self.blocked("terminal", command="cat /etc/hosts"))

    def test_writes_confined(self):
        self.assertTrue(self.blocked("write_file", path=os.path.join(self.tmp, "toolsmith", "toolsmith.conf")))
        self.assertFalse(self.blocked("write_file", path=os.path.join(self.tmp, "toolsmith", "workspace", "s.json")))

    def test_curl_public_https_only(self):
        self.assertFalse(self.blocked("terminal", command="curl -sL https://github.com/caddyserver/caddy/releases"))
        self.assertTrue(self.blocked("terminal", command="curl -s http://github.com/"))
        self.assertTrue(self.blocked("terminal", command="curl -s https://10.0.0.5/"))
        self.assertTrue(self.blocked("terminal", command="curl -s https://nas.lan/"))
        self.assertTrue(self.blocked("terminal", command="curl -s https://auth.home.example.org/"))
        self.assertTrue(self.blocked("terminal", command="curl -X POST https://example.com/"))

    def test_background_blocked(self):
        sb = os.path.join(SCRIPTS, "toolsmith_sandbox.sh")
        self.assertTrue(self.blocked("terminal", command=f"{sb} hosts", background=True))


FACTS = """AREA=demo
[F001] docker 'proxy', memory in use 119.1 MB, restart count 0
[F002] docker 'tunnel-ui', memory in use 10.9 MB
[F003] docker 'tunnel-ui-2', memory in use 13.3 MB
[F004] Tools must be free and open source.
"""

GOOD = """Summary line with no numbers.

### REC 1: Caddy
- Incumbent: proxy — why: uses 119.1 MB [F001]
- Candidate: Caddy — source: https://github.com/caddyserver/caddy
- Licence: Apache License 2 — class: OSI — source: https://github.com/caddyserver/caddy/blob/master/LICENSE
- Health: last release 2026-06-03 (v2.11.4) — source: https://github.com/caddyserver/caddy/releases
- Resources: needs little — source: https://github.com/caddyserver/caddy
- Sandbox: not tested (no run)
- Migration effort: M
- Rollback: restart the old proxy
- Verdict: WATCH — fine.
"""


class ValidatorTest(Base):
    def run_validator(self, report):
        facts = os.path.join(self.tmp, "facts.txt")
        open(facts, "w").write(FACTS)
        rep = os.path.join(self.tmp, "report.md")
        open(rep, "w").write(report)
        db = os.path.join(self.tmp, "state.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT, started_at REAL)")
        con.execute("CREATE TABLE IF NOT EXISTS messages (session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, tool_name TEXT)")
        con.execute("INSERT INTO sessions VALUES ('s1', ?)", (time.time(),))
        for u in ("https://github.com/caddyserver/caddy", "https://github.com/caddyserver/caddy/blob/master/LICENSE",
                  "https://github.com/caddyserver/caddy/releases"):
            con.execute("INSERT INTO messages VALUES ('s1', 'assistant', '', ?, 'web_extract')",
                        (json.dumps({"name": "web_extract", "url": u}),))
        con.commit(); con.close()
        out = os.path.join(self.tmp, "v.json")
        subprocess.run([sys.executable, os.path.join(SCRIPTS, "toolsmith_validate.py"), "--facts", facts,
                        "--report", rep, "--since", str(time.time() - 60), "--out", out, "--statedb", db,
                        "--sandbox-dir", os.path.join(self.tmp, "toolsmith", "sandbox")],
                       capture_output=True, text=True, env=self.env, timeout=30)
        return json.load(open(out))

    def test_supported_rec_passes(self):
        v = self.run_validator(GOOD)
        self.assertEqual([p["title"] for p in v["passed"]], ["Caddy"])

    def test_computed_sum_is_withheld(self):
        # 10.9 + 13.3 = 24.2: a sum is not in either fact line, so it is withheld
        v = self.run_validator(GOOD.replace("uses 119.1 MB [F001]", "two dead UIs use ~24 MB [F002][F003]"))
        self.assertEqual(v["passed"], [])
        self.assertIn("24", " ".join(v["withheld"][0]["reasons"]))

    def test_unknown_fact_id_withheld(self):
        v = self.run_validator(GOOD.replace("[F001]", "[F099]"))
        self.assertTrue(any("F099" in r for r in v["withheld"][0]["reasons"]))

    def test_unfetched_url_withheld(self):
        v = self.run_validator(GOOD.replace("source: https://github.com/caddyserver/caddy/releases",
                                            "source: https://example.com/never-fetched"))
        self.assertTrue(any("not fetched" in r for r in v["withheld"][0]["reasons"]))

    def test_sandbox_claim_without_run_id_withheld(self):
        v = self.run_validator(GOOD.replace("Sandbox: not tested (no run)", "Sandbox: healthy, 12 MB"))
        self.assertTrue(any("without a run_id" in r for r in v["withheld"][0]["reasons"]))

    def test_known_false_positive_apache_2_0(self):
        # Documented limitation: "Apache-2.0" in a clause that cites [F004] reads as the number 2.0.
        v = self.run_validator(GOOD.replace("- Verdict: WATCH — fine.",
                                            "- Verdict: WATCH — Apache-2.0 fits preferences [F004]."))
        self.assertEqual(v["passed"], [])


if __name__ == "__main__":
    unittest.main()
