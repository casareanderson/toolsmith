#!/usr/bin/env python3
"""toolsmith_approvals.py — owner approval of toolsmith ADOPT-TRIAL recommendations by Discord reaction.

The gateway does not turn reactions into approvals, so this is a small REST poller.

  post --validated V.json --run RUN --area AREA --report PATH
        one message per validated ADOPT-TRIAL rec in the toolsmith channel, bot pre-adds ✅ ❌,
        record stored as pending. Skips a candidate that is pending/approved already, or
        rejected and still inside its cooldown.
  post-test     a clearly labelled TEST recommendation (for proving the loop)
  poll          (toolsmith-approvals.timer, every 10 min) for each pending record:
                  ✅ by the OWNER  -> approved, kanban card for TOOLSMITH_KANBAN_ASSIGNEE on
                                      board TOOLSMITH_KANBAN_BOARD, reply
                  ❌ by the OWNER  -> rejected, cooldown recorded, reply
                  no owner reaction after TOOLSMITH_APPROVAL_EXPIRY_DAYS -> expired, reply
                reactions from anyone else — including this bot's own pre-added ✅/❌ — are IGNORED.
  list          print the state

State: $TOOLSMITH_HOME/approvals.json (flock'd). Log: stdout (the systemd unit appends it to a log file).

Approval never makes toolsmith change production. It creates a card for the ops
agent, whose own authority rules still apply: RED stays RED.
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import toolsmith_config as cfg  # noqa: E402
import toolsmith_discord as td  # noqa: E402

STATE = os.path.join(cfg.TS_ROOT, "approvals.json")
HERMES = cfg.HERMES_BIN
BOARD = cfg.get("TOOLSMITH_KANBAN_BOARD", "it")
ASSIGNEE = cfg.get("TOOLSMITH_KANBAN_ASSIGNEE", "coder")


def now():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"[{now().strftime('%Y-%m-%d %H:%M:%S')}Z] {msg}", flush=True)


class State:
    def __enter__(self):
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        self.fh = open(STATE + ".lock", "w")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        try:
            self.data = json.load(open(STATE))
        except FileNotFoundError:
            self.data = {"records": []}
        return self.data

    def __exit__(self, *exc):
        tmp = STATE + ".tmp"
        json.dump(self.data, open(tmp, "w"), indent=2)
        os.replace(tmp, STATE)
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60]


def strip_md(s, n):
    s = re.sub(r"\[F\d{3}\]", "", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def new_id(data):
    day = now().strftime("%Y%m%d")
    n = 1 + sum(1 for r in data["records"] if r["approval_id"].startswith(f"TS-{day}-"))
    return f"TS-{day}-{n}"


def message_for(r):
    head = "🧪 **TEST — toolsmith approval loop check (not a real recommendation)**\n" if r.get("test") else ""
    return (f"{head}🛠️ **Toolsmith ADOPT-TRIAL `{r['approval_id']}`** — area *{r['area']}*\n"
            f"**{strip_md(r['incumbent_name'], 80)} → {strip_md(r['candidate'], 80)}.** "
            f"Why: {strip_md(r['why'], 330)} "
            f"Licence: {strip_md(r['licence'], 160)}. "
            f"Sandbox: {strip_md(r['sandbox'], 200)}. "
            f"Migration: {strip_md(r['migration'], 180)}. "
            f"Rollback: {strip_md(r['rollback'], 180)}.\n"
            f"React ✅ to approve a staged trial (creates a card for {ASSIGNEE}), ❌ to reject "
            f"(not re-proposed for {td.conf().get('TOOLSMITH_REJECT_COOLDOWN_WEEKS', '12')} weeks). "
            f"Only the owner's reaction counts. Full report: `{r.get('report_path', '')}`")


def post_record(data, r):
    mid = td.post(message_for(r))
    r["message_id"] = mid
    r["channel_id"] = td.channel_id()
    for e in (td.APPROVE, td.REJECT):
        td.react(mid, e)
    data["records"].append(r)
    log(f"posted {r['approval_id']} message {mid} ({r['candidate']})")


def cmd_post(a):
    v = json.load(open(a.validated))
    c = td.conf()
    with State() as data:
        for p in v.get("passed", []):
            if p.get("verdict") != "ADOPT-TRIAL":
                continue
            f = p["fields"]
            cand = re.split(r"\s+—\s+|\s+-\s+source:", f.get("Candidate") or p["title"])[0]
            inc = f.get("Incumbent") or ""
            inc_name, _, why = inc.partition("why:")
            s = slug(cand)
            today = now().date().isoformat()
            dup = [r for r in data["records"] if r.get("slug") == s and r.get("area") == a.area and not r.get("test")
                   and (r["status"] in ("pending", "approved")
                        or (r["status"] == "rejected" and (r.get("cooldown_until") or "") > today))]
            if dup:
                log(f"skip {cand}: already {dup[0]['status']} as {dup[0]['approval_id']}")
                continue
            r = {"approval_id": new_id(data), "run": a.run, "area": a.area, "slug": s, "test": False,
                 "candidate": cand.strip(), "incumbent_name": inc_name.strip(" —-") or "incumbent",
                 "incumbent": inc, "why": why.strip() or inc, "licence": f.get("Licence") or "",
                 "sandbox": f.get("Sandbox") or "", "migration": f.get("Migration effort") or "",
                 "rollback": f.get("Rollback") or "", "resources": f.get("Resources") or "",
                 "health": f.get("Health") or "", "report_path": a.report, "rec_body": p.get("body", ""),
                 "created_at": iso(now()), "status": "pending"}
            post_record(data, r)
    return 0


def cmd_post_test(_a):
    with State() as data:
        r = {"approval_id": new_id(data) + "-TEST", "run": "test", "area": "test", "slug": "test-approval-loop",
             "test": True, "candidate": "TEST candidate (no such change)", "incumbent_name": "TEST incumbent",
             "incumbent": "TEST", "why": "this message exists only to prove the reaction-approval loop end to end.",
             "licence": "n/a (test)", "sandbox": "n/a (test)", "migration": "none", "rollback": "none",
             "resources": "", "health": "", "report_path": "(test)", "rec_body": "TEST",
             "created_at": iso(now()), "status": "pending"}
        post_record(data, r)
        print(json.dumps(r, indent=2))
    return 0


def kanban(*args, timeout=90):
    p = subprocess.run([HERMES, "kanban", "--board", BOARD, *args], capture_output=True, text=True,
                       timeout=timeout, cwd=os.path.expanduser("~"), env={**os.environ, "HERMES_HOME": cfg.HERMES_HOME})
    return p.returncode, p.stdout + p.stderr


def access_note():
    """Optional $TOOLSMITH_HOME/card-access.txt: how the ops agent reaches your hosts
    (e.g. `ssh root@<node> 'pct exec <vmid> -- <cmd>'`). Pasted into every approved card."""
    try:
        return open(os.path.join(cfg.TS_ROOT, "card-access.txt")).read().strip()
    except OSError:
        return "Access paths: see your own runbook (no card-access.txt configured)."


def card_body(r):
    return f"""Owner APPROVED toolsmith recommendation {r['approval_id']} by reacting ✅ in the toolsmith channel.
This approves a STAGED, REVERSIBLE TRIAL — not a cut-over, and not blanket permission.

Incumbent: {r['incumbent']}
Candidate: {r['candidate']}
Licence: {r['licence']}
Project health: {r['health']}
Resources: {r['resources']}
Sandbox evidence: {r['sandbox']}
Migration effort: {r['migration']}
Rollback: {r['rollback']}
Full toolsmith report: {r['report_path']}

Staged rollout (stop at the first failed gate):
1. Pre-flight (GREEN): re-verify the target host's free RAM/disk and the incumbent's current state with commands you run now; re-read the candidate's licence and latest release from its own repo. Record the commands and outputs in a card comment.
2. Plan (GREEN): write the exact deploy commands, ports, data paths, and the rollback commands. Nothing outside localhost/LAN, no secrets printed.
3. Trial deploy — RED under your authority rules (new containers on a production host, ports, DNS, stopping or reconfiguring anything): do NOT execute. End with kanban_block quoting the exact commands for the owner to approve in words.
4. Once the owner has approved step 3 in words: deploy ALONGSIDE the incumbent (never replace it), capped resources, verify health endpoint + resource use for 7 days, compare with the incumbent on the pain named above.
5. Decision: report measured results; retiring the incumbent is a separate RED decision for the owner.

Verification: every claim must come from a command you actually ran; state the command beside the finding. "unknown" is a valid finding, a plausible guess is not. Verify the toolsmith findings yourself rather than taking them from this brief.
Rollback at any stage: {r['rollback']}

{access_note()}

Secrets: never print a secret value into this card, a comment or a log."""


def test_card_body(r):
    return (f"TEST card created by the toolsmith approval poller for {r['approval_id']} after the owner reacted ✅ "
            "to the TEST message in the toolsmith channel. It proves the loop works. There is NO work to do: do not act on it. "
            f"Owner: archive it with `hermes kanban --board {BOARD} archive <id>`.")


def approve(r):
    title = (f"TEST toolsmith approval — {r['approval_id']}" if r.get("test")
             else f"Toolsmith-approved trial: {r['candidate'][:60]} (replacing {r['incumbent_name'][:40]}) [{r['approval_id']}]")
    args = ["create", title, "--assignee", ASSIGNEE, "--body", test_card_body(r) if r.get("test") else card_body(r),
            "--idempotency-key", f"toolsmith-{r['approval_id']}", "--created-by", "toolsmith", "--json"]
    if r.get("test"):
        args += ["--max-retries", "1", "--initial-status", "blocked"]
    rc, out = kanban(*args)
    m = re.search(r"\bt_[0-9a-f]{8}\b", out)
    if rc != 0 or not m:
        raise RuntimeError(f"kanban create failed rc={rc}: {out.strip()[-200:]}")
    tid = m.group(0)
    if r.get("test"):
        # ⚠️ `--initial-status blocked` is known to store READY; block explicitly, reason BEFORE --kind.
        rc2, out2 = kanban("block", tid, "TEST card from the toolsmith approval loop - no work, archive it", "--kind", "needs_input")
        rc3, out3 = kanban("show", tid)
        r["test_card_blocked"] = "blocked" in out3.lower()
    return tid


def cmd_poll(a):
    c = td.conf()
    owner = c.get("TOOLSMITH_OWNER_DISCORD_ID")
    if not owner:
        log("TOOLSMITH_OWNER_DISCORD_ID not set — refusing to poll (nobody's reaction may count)")
        return 2
    expiry_days = int(c.get("TOOLSMITH_APPROVAL_EXPIRY_DAYS", "14"))
    cooldown_w = int(c.get("TOOLSMITH_REJECT_COOLDOWN_WEEKS", "12"))
    bot = None
    try:
        bot = td.bot_user_id()
    except Exception as e:
        log(f"could not read bot id: {e}")
    with State() as data:
        pend = [r for r in data["records"] if r["status"] == "pending"]
        log(f"poll: {len(pend)} pending")
        for r in pend:
            ch, mid = r.get("channel_id"), r.get("message_id")
            try:
                yes = td.reactors(mid, td.APPROVE, ch)
                no = td.reactors(mid, td.REJECT, ch)
            except Exception as e:
                log(f"{r['approval_id']}: reaction read failed ({e}) — left pending")
                continue
            if yes is None or no is None:
                r["status"], r["decided_at"] = "gone", iso(now())
                log(f"{r['approval_id']}: message deleted — status gone")
                continue
            ignored = sorted({u for u in yes + no if u != owner})
            r["last_poll"] = {"at": iso(now()), "approve_reactors": len(yes), "reject_reactors": len(no),
                              "ignored_non_owner": len(ignored), "ignored_includes_bot": bool(bot and bot in ignored)}
            if ignored:
                log(f"{r['approval_id']}: ignoring {len(ignored)} non-owner reaction(s)"
                    + (" (incl. this bot's own)" if bot and bot in ignored else ""))
            oy, on = owner in yes, owner in no
            if oy and on:
                log(f"{r['approval_id']}: owner reacted both ✅ and ❌ — ambiguous, left pending")
                continue
            if oy:
                try:
                    tid = approve(r)
                except Exception as e:
                    log(f"{r['approval_id']}: APPROVED but card creation failed: {e} — left pending, will retry")
                    continue
                r.update(status="approved", decided_at=iso(now()), card=tid)
                td.post(f"✅ `{r['approval_id']}` approved → card `{tid}` (board `{BOARD}`, assignee {ASSIGNEE})"
                        + (" — TEST card, parked blocked" if r.get("test") else ""), ch, reply_to=mid)
                log(f"{r['approval_id']}: approved -> {tid}")
            elif on:
                until = (now() + dt.timedelta(weeks=cooldown_w)).date().isoformat()
                r.update(status="rejected", decided_at=iso(now()), cooldown_until=until)
                td.post(f"❌ `{r['approval_id']}` rejected — not re-proposed before {until}.", ch, reply_to=mid)
                log(f"{r['approval_id']}: rejected until {until}")
            else:
                created = dt.datetime.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
                if now() - created > dt.timedelta(days=expiry_days):
                    r.update(status="expired", decided_at=iso(now()))
                    td.post(f"⌛ `{r['approval_id']}` expired after {expiry_days} days with no owner reaction.", ch, reply_to=mid)
                    log(f"{r['approval_id']}: expired")
    return 0


def cmd_list(_a):
    try:
        print(json.dumps(json.load(open(STATE)), indent=2))
    except FileNotFoundError:
        print('{"records": []}')
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("post")
    p.add_argument("--validated", required=True); p.add_argument("--run", required=True)
    p.add_argument("--area", required=True); p.add_argument("--report", required=True)
    sub.add_parser("post-test"); sub.add_parser("poll"); sub.add_parser("list")
    a = ap.parse_args()
    sys.exit({"post": cmd_post, "post-test": cmd_post_test, "poll": cmd_poll, "list": cmd_list}[a.cmd](a))


if __name__ == "__main__":
    main()
