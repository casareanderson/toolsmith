#!/usr/bin/env python3
"""toolsmith_guard.py — pre_tool_call shell hook for the `toolsmith` profile.

Wired in the toolsmith profile's config.yaml with matcher ".*" and
fail_closed: true, so a crash, a timeout or garbage output BLOCKS the call.

Why this exists: the SOUL says toolsmith is research + sandbox only. A prompt
rule that forbids an action is not a control in this estate (the writer ignored
"never state a port you have not read" and invented a runbook). The Hermes box
holds root SSH keys to the Proxmox nodes, so an unguarded `terminal` tool on this
profile could reach every container. This hook makes the mandate mechanical:

  * tools are ALLOW-LISTED by name; anything else is blocked
  * `terminal` may only run the sandbox script, a few read-only commands on the
    toolsmith tree, or a plain HTTPS GET to a public host — no shell syntax
  * file reads are confined to the toolsmith tree + the profile's skills
  * file writes are confined to the toolsmith workspace

Protocol: JSON on stdin; print {"decision":"block","reason":...} to block,
{} to allow.
"""
import ipaddress
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
try:
    import toolsmith_config as cfg
except Exception:          # a broken config must not open the guard; main() blocks on error
    cfg = None

TS_ROOT = cfg.TS_ROOT if cfg else "/nonexistent"
WORKSPACE = TS_ROOT + "/workspace"
READ_ROOTS = (TS_ROOT, (cfg.PROFILE_DIR if cfg else "/nonexistent") + "/skills")
SANDBOX = os.path.join(cfg.SCRIPTS if cfg else "/nonexistent", "toolsmith_sandbox.sh")
# Your own LAN / internal domains, space-separated in TOOLSMITH_PRIVATE_DOMAINS
# (e.g. "home.example.org"). curl may never reach them or any subdomain.
PRIVATE_DOMAINS = tuple(d.lower().lstrip(".") for d in (cfg.get("TOOLSMITH_PRIVATE_DOMAINS").split() if cfg else []))

ALLOWED_TOOLS = {
    "web_search", "web_extract",
    "read_file", "search_files", "write_file", "patch",
    "terminal",
    "skills_list", "skill_view",
    "todo_list", "todo_write", "todo",
}
# Kanban lifecycle tools are injected for board workers; they only touch the board.
ALLOWED_PREFIXES = ("kanban_",)

READONLY_CMDS = {"cat", "head", "tail", "ls", "wc", "grep", "jq", "sha256sum", "date", "stat"}
CURL_OK_FLAGS = {"-s", "-S", "-sS", "-Ss", "-L", "-sL", "-sSL", "-I", "--silent",
                 "--show-error", "--location", "--head", "--compressed", "-f", "--fail"}
CURL_OK_WITH_ARG = {"-m", "--max-time", "-A", "--user-agent"}
SHELL_META = re.compile(r"[;&|`$<>(){}\\\n\r*?!~]")


def block(reason):
    print(json.dumps({"decision": "block",
                      "reason": "toolsmith guard: " + reason +
                      " (toolsmith is research + sandbox only; see SOUL.md)"}))
    sys.exit(0)


def allow():
    print("{}")
    sys.exit(0)


def under(path, roots):
    if not isinstance(path, str) or not path:
        return False
    real = os.path.realpath(path if os.path.isabs(path) else os.path.join(WORKSPACE, path))
    return any(real == r or real.startswith(r.rstrip("/") + "/") for r in roots)


def public_https(url):
    m = re.match(r"^https://([A-Za-z0-9.-]+)(:\d+)?(/[^\s]*)?$", url)
    if not m:
        return False
    host = m.group(1).lower()
    if host in ("localhost",) or host.endswith((".lan", ".local", ".lab", ".internal", ".home.arpa")):
        return False
    if any(host == d or host.endswith("." + d) for d in PRIVATE_DOMAINS):
        return False
    try:
        ipaddress.ip_address(host)
        return False            # bare IPs are never allowed, public or not
    except ValueError:
        pass
    return "." in host


def check_terminal(cmd):
    if not isinstance(cmd, str) or not cmd.strip():
        block("empty terminal command")
    if SHELL_META.search(cmd):
        block("shell syntax (pipes, redirects, substitution, globs, chaining) is not allowed")
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        block(f"unparseable command: {e}")
    prog = argv[0]
    if prog in (SANDBOX, "bash " + SANDBOX) or (prog == "bash" and len(argv) > 1 and argv[1] == SANDBOX):
        allow()
    base = os.path.basename(prog)
    if base in READONLY_CMDS and prog in (base, "/usr/bin/" + base, "/bin/" + base):
        for a in argv[1:]:
            if a.startswith("-"):
                continue
            if base in ("grep", "jq") and not a.startswith("/"):
                continue            # the pattern / filter argument
            if base == "date":
                continue
            if not under(a, READ_ROOTS):
                block(f"{base} may only read inside {TS_ROOT}")
        allow()
    if base == "curl" and prog in ("curl", "/usr/bin/curl"):
        urls, i = [], 1
        while i < len(argv):
            a = argv[i]
            if a in CURL_OK_FLAGS:
                i += 1
                continue
            if a in CURL_OK_WITH_ARG:
                i += 2
                continue
            if a.startswith("-"):
                block(f"curl flag {a} is not allowed (GET to a public https URL only)")
            urls.append(a)
            i += 1
        if len(urls) != 1 or not public_https(urls[0]):
            block("curl may only GET one public https:// URL (no IPs, no LAN names)")
        allow()
    block(f"command '{base}' is not allowed; allowed: {SANDBOX}, read-only "
          f"{sorted(READONLY_CMDS)} inside {TS_ROOT}, curl GET to public https")


def main():
    if cfg is None:
        block("toolsmith_config could not be loaded")
    try:
        payload = json.load(sys.stdin)
    except Exception:
        block("could not parse hook payload")
    tool = payload.get("tool_name") or ""
    args = payload.get("tool_input") or {}
    if not isinstance(args, dict):
        args = {}
    if tool.startswith(ALLOWED_PREFIXES):
        allow()
    if tool not in ALLOWED_TOOLS:
        block(f"tool '{tool}' is not on the toolsmith allow-list")
    if tool == "terminal":
        if args.get("background") in (True, "true"):
            block("background terminal processes are not allowed")
        check_terminal(args.get("command"))
    if tool in ("write_file", "patch"):
        if not under(args.get("path"), (WORKSPACE,)):
            block(f"writes are confined to {WORKSPACE}")
    if tool == "read_file":
        if not under(args.get("path"), READ_ROOTS):
            block(f"reads are confined to {TS_ROOT} and the toolsmith skills tree")
    if tool == "search_files":
        p = args.get("path") or WORKSPACE
        if not under(p, READ_ROOTS):
            block(f"search is confined to {TS_ROOT}")
    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:           # any bug here must fail CLOSED, never open
        block(f"guard error {type(e).__name__}")
