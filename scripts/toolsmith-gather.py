#!/usr/bin/env python3
"""toolsmith-gather.py — deterministic, READ-ONLY fact pack for the toolsmith profile.

NO LLM. Every estate fact the toolsmith report may state comes from here, each
on its own numbered line ([F001] ...). The validator later checks that every
fact the model cites exists and that the numbers it quotes appear in the cited
lines. This is the estate rule (AUTHORITY.md): scripts gather facts, models
never do. A section that could not be measured prints NOT MEASURED + why —
silence would read as "nothing wrong".

Usage:
  toolsmith-gather.py [--area ID]      print the fact pack for ID (default: next in rotation)
  toolsmith-gather.py --list-areas     print the derived rotation and exit

Reads (never writes): every Proxmox node in TOOLSMITH_PROXMOX_NODES + every
running CT on it via `pct exec`, the extra hosts in TOOLSMITH_SSH_HOSTS over
their own SSH paths, Hermes state.db files (sqlite read-only URI), profile
configs, an optional lessons file, the operator's notes directory (local or
over SSH), toolsmith's own approvals/rotation state. All of it is configured in
toolsmith.conf; see toolsmith.conf.example.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import toolsmith_config as cfg  # noqa: E402

TS = cfg.TS_ROOT
SCRIPTS = cfg.SCRIPTS
HH = cfg.HERMES_HOME
# Operator notes (markdown) used as the "recorded incidents" source. Optional.
MEMDIR = cfg.get("TOOLSMITH_NOTES_DIR")
MEMSSH = cfg.get("TOOLSMITH_NOTES_SSH")          # e.g. root@notes-host; empty = local
# [(name, ip)] Proxmox nodes reached as root@ip
NODES = cfg.pairs("TOOLSMITH_PROXMOX_NODES")
# [(label, user@host)] non-Proxmox docker hosts (a NAS, a GPU box...)
SSH_HOSTS = cfg.pairs("TOOLSMITH_SSH_HOSTS")
OLLAMA_HOSTS = set(cfg.get("TOOLSMITH_OLLAMA_HOSTS").split())   # labels from SSH_HOSTS
HOST_UNITS = cfg.get("TOOLSMITH_HOST_UNITS", "ollama comfyui").split()

# ---------------------------------------------------------------------------
out_lines = []
_n = 0


def fact(text):
    global _n
    _n += 1
    out_lines.append(f"[F{_n:03d}] {text}")


def section(title):
    out_lines.append("")
    out_lines.append(f"===== {title} =====")


def note(text):
    out_lines.append(f"  ({text})")


def sh(cmd, inp=None, timeout=90):
    try:
        p = subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:  # noqa
        return 1, "", str(e)


def ssh(target, script, timeout=120):
    if not target:                       # local
        return sh(["sh", "-s"], inp=script, timeout=timeout)
    return sh(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", target, "sh -s"],
              inp=script, timeout=timeout)


# Inner probe run inside every CT / physical host. Plain POSIX sh via stdin.
INNER = r"""
df -P / | awk 'NR==2{gsub("%","",$5); print "DF|"$5"|"int($4/1024)"|"int($2/1024)}'
awk '/^MemAvailable:/{a=int($2/1024)} /^MemTotal:/{t=int($2/1024)} END{print "MEM|"t"|"a}' /proc/meminfo
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  for c in $(docker ps -q); do
    docker inspect -f 'R|{{.Name}}|{{.Config.Image}}|{{.RestartCount}}|{{.State.StartedAt}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$c"
  done
  docker stats --no-stream --format 'S|{{.Name}}|{{.MemUsage}}|{{.CPUPerc}}'
fi
"""

NODE_SCRIPT = r"""
T=$(mktemp)
cat > "$T" <<'INNEREOF'
__INNER__
INNEREOF
awk '/^MemAvailable:/{a=int($2/1024)} /^MemTotal:/{t=int($2/1024)} END{print "NODEMEM|"t"|"a}' /proc/meminfo
pvesm status 2>/dev/null | awk 'NR>1{printf "POOL|%s|%s|%s\n",$1,$3,$7}'
lsblk -d -n -o NAME,ROTA,SIZE,MODEL 2>/dev/null | awk '{printf "DISK|%s|%s|%s|",$1,$2,$3; for(i=4;i<=NF;i++) printf "%s ",$i; print ""}'
for ct in $(pct list | awk 'NR>1 && $2=="running"{print $1}'); do
  name=$(pct config "$ct" | awk '/^hostname:/{print $2}')
  mem=$(pct config "$ct" | awk '/^memory:/{print $2}')
  store=$(pct config "$ct" | awk -F'[ :,]+' '/^rootfs:/{print $2}')
  echo "CT|$ct|$name|$mem|$store"
  timeout 60 pct exec "$ct" -- sh -s < "$T" 2>/dev/null
  echo "ENDCT|$ct"
done
rm -f "$T"
""".replace("__INNER__", INNER)


def mem_mb(s):
    m = re.match(r"\s*([\d.]+)\s*([KMG]i?B|B)", s or "")
    if not m:
        return None
    mult = {"B": 1 / 1048576, "KiB": 1 / 1024, "KB": 1 / 1024, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024}[m.group(2)]
    return round(float(m.group(1)) * mult, 1)


def parse_inner(lines, host, ct=None, ctname=None, services=None, hostinfo=None):
    stats, rows = {}, []
    for l in lines:
        p = l.split("|")
        if p[0] == "DF" and len(p) >= 4:
            hostinfo.update(rootfs_pct=p[1], disk_free_mb=p[2], disk_total_mb=p[3])
        elif p[0] == "MEM" and len(p) >= 3:
            hostinfo.update(mem_total_mb=p[1], mem_avail_mb=p[2])
        elif p[0] == "R" and len(p) >= 6:
            rows.append(p)
        elif p[0] == "S" and len(p) >= 4:
            stats[p[1]] = (mem_mb(p[2]), p[3])
    for p in rows:
        name = p[1].lstrip("/")
        m, cpu = stats.get(name, (None, None))
        services.append(dict(host=host, ct=ct, ctname=ctname, kind="docker", name=name, image=p[2],
                             restarts=p[3], started=p[4][:19], health=p[5], mem_mb=m, cpu=cpu))
    hostinfo["docker_containers"] = len(rows)


def inventory():
    services, hosts, nodes, errors = [], [], [], []
    if not NODES:
        errors.append("no Proxmox nodes configured (TOOLSMITH_PROXMOX_NODES) - the inventory covers this box only")
    for nname, ip in NODES:
        rc, out, err = ssh(f"root@{ip}", NODE_SCRIPT, timeout=400)
        if rc != 0 and not out:
            errors.append(f"node {nname} ({ip}) unreachable: rc={rc} {err.strip()[:100]}")
            continue
        node = dict(name=nname, ip=ip, pools=[], disks=[])
        cur, buf = None, []
        for l in out.splitlines():
            p = l.split("|")
            if p[0] == "NODEMEM":
                node.update(mem_total_mb=p[1], mem_avail_mb=p[2])
            elif p[0] == "POOL":
                node["pools"].append(p[1:])
            elif p[0] == "DISK":
                node["disks"].append(p[1:])
            elif p[0] == "CT":
                cur = dict(node=nname, ct=p[1], name=p[2], cap_mb=p[3], storage=p[4]); buf = []
            elif p[0] == "ENDCT" and cur:
                parse_inner(buf, nname, cur["ct"], cur["name"], services, cur)
                if not cur.get("docker_containers"):
                    used = None
                    try:
                        used = int(cur["mem_total_mb"]) - int(cur["mem_avail_mb"])
                    except (KeyError, ValueError):
                        pass
                    services.append(dict(host=nname, ct=cur["ct"], ctname=cur["name"], kind="ct",
                                         name=cur["name"], image="(LXC, no docker)", restarts="",
                                         started="", health="", mem_mb=used, cpu=None))
                hosts.append(cur); cur = None
            elif cur is not None:
                buf.append(l)
        nodes.append(node)
    for label, target in SSH_HOSTS:
        extra = ""
        if label in OLLAMA_HOSTS:
            extra = (f"\nfor u in {' '.join(HOST_UNITS)}; do echo \"UNIT|$u|$(systemctl is-active $u 2>/dev/null)\"; done\n"
                     "curl -s -m 5 http://127.0.0.1:11434/api/ps 2>/dev/null | tr -d '\\n' | sed 's/^/OLLAMAPS|/'; echo\n")
        rc, out, err = ssh(target, INNER + extra, timeout=90)
        if rc != 0 and not out:
            errors.append(f"{label} unreachable via {target}: rc={rc} {err.strip()[:100]}")
            continue
        h = dict(node="physical", ct="", name=label, cap_mb="", storage="")
        parse_inner(out.splitlines(), label, None, label, services, h)
        for l in out.splitlines():
            p = l.split("|", 2)
            if p[0] == "UNIT" and len(p) == 3:
                services.append(dict(host=label, ct=None, ctname=label, kind="systemd", name=p[1],
                                     image="(host systemd unit)", restarts="", started="", health=p[2],
                                     mem_mb=None, cpu=None))
            elif p[0] == "OLLAMAPS" and len(p) >= 2:
                try:
                    h["ollama_loaded"] = [m.get("name") for m in json.loads(l.split("|", 1)[1]).get("models", [])]
                except Exception:
                    h["ollama_loaded"] = "unparsed"
        hosts.append(h)
    # The Hermes box itself: its systemd timers are its "services"
    me = socket.gethostname()
    rc, out, _ = sh(["systemctl", "list-timers", "--all", "--no-legend", "--no-pager"])
    for l in out.splitlines():
        m = re.search(r"(\S+)\.timer\s+(\S+)\.service", l)
        if m:
            services.append(dict(host=cfg.get("TOOLSMITH_SELF_NODE", me), ct=cfg.get("TOOLSMITH_SELF_CT") or None,
                                 ctname=me, kind="timer", name=m.group(1),
                                 image=f"(systemd timer on {me})", restarts="", started="", health="",
                                 mem_mb=None, cpu=None))
    return services, hosts, nodes, errors


def classify(services, areas):
    for s in services:
        text = f"{s['name']} {s['image']} {s['ctname'] if s['kind'] == 'ct' else ''}".lower()
        s["area"] = next((a["id"] for a in areas if any(k in text for k in a["keywords"])), None)


# ---------------------------------------------------------------------------
def memory_notes(keywords, limit=40, primary=()):
    """Dated lines from the operator's notes that mention the area's keywords."""
    if not MEMDIR:
        return None, "no operator notes directory configured (TOOLSMITH_NOTES_DIR)"
    pat = "|".join(k.replace(".", "\\.") for k in keywords)
    script = (f"cd {MEMDIR} 2>/dev/null || exit 3\n"
              f"for f in *.md; do d=$(date -r \"$f\" +%F); grep -n -i -w -E {json.dumps(pat)} \"$f\" | "
              f"awk -v f=\"$f\" -v d=\"$d\" '{{print f\"|\"d\"|\"$0}}'; done\n")
    rc, out, err = ssh(MEMSSH, script, timeout=60)
    if rc != 0 and not out:
        return None, f"operator notes unreadable (rc={rc})"
    rows = []
    for l in out.splitlines():
        f, d, rest = l.split("|", 2)
        ln, _, text = rest.partition(":")
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 25 or f == "MEMORY.md":
            continue
        low = text.lower()
        score = ((4 if any(k in low for k in primary) else 0) + (2 if re.search(r"\d", text) else 0)
                 + (2 if "⚠" in text else 0) + (1 if "✅" in text else 0))
        rows.append((score, f, d, ln, text[:280]))
    rows.sort(key=lambda r: (-r[0], r[1], int(r[3])))
    per_file, picked = {}, []
    for r in rows:
        if per_file.get(r[1], 0) >= 6:
            continue
        per_file[r[1]] = per_file.get(r[1], 0) + 1
        picked.append(r)
        if len(picked) >= limit:
            break
    return picked, None


def verify_preferences():
    prefs = json.load(open(f"{TS}/preferences.json"))["entries"]
    mem_needed = sorted({p["source"].split(":", 1)[1] for p in prefs if p["source"].startswith("memory:")})
    mem_text = {}
    if mem_needed and MEMDIR:
        script = "".join(f"echo '@@@{f}'; cat {MEMDIR}/{f} 2>/dev/null\n" for f in mem_needed)
        rc, out, _ = ssh(MEMSSH, script, timeout=40)
        cur = None
        for l in out.splitlines():
            if l.startswith("@@@"):
                cur = l[3:]; mem_text[cur] = []
            elif cur:
                mem_text[cur].append(l)
    res = []
    for p in prefs:
        kind, src = p["source"].split(":", 1)
        if kind == "memory":
            body = "\n".join(mem_text.get(src, []))
        else:
            try:
                body = open(src).read()
            except OSError:
                body = ""
        res.append((p, p["quote"] in body))
    return res


def statedb_usage():
    dbs = [("default", f"{HH}/state.db")] + sorted(
        (os.path.basename(os.path.dirname(p)), p) for p in glob.glob(f"{HH}/profiles/*/state.db"))
    rows, now = [], time.time()
    for prof, path in dbs:
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            r = {}
            for days in (7, 30):
                q = ("SELECT count(*), coalesce(sum(api_call_count),0), coalesce(sum(input_tokens),0), "
                     "coalesce(sum(output_tokens),0), round(coalesce(sum(coalesce(actual_cost_usd, estimated_cost_usd)),0),4), "
                     "coalesce(sum(tool_call_count),0) FROM sessions WHERE started_at > ?")
                r[days] = con.execute(q, (now - days * 86400,)).fetchone()
            t = con.execute("SELECT count(*) FROM messages WHERE role='tool' AND timestamp > ?", (now - 7 * 86400,)).fetchone()[0]
            con.close()
            rows.append((prof, r, t))
        except Exception as e:
            rows.append((prof, None, f"unreadable: {type(e).__name__}"))
    return rows


def langfuse_coverage():
    import yaml
    res = []
    cfgs = [("default", f"{HH}/config.yaml", f"{HH}/.env")] + sorted(
        (os.path.basename(os.path.dirname(p)), p, os.path.join(os.path.dirname(p), ".env"))
        for p in glob.glob(f"{HH}/profiles/*/config.yaml"))
    for prof, cfg, env in cfgs:
        try:
            c = yaml.safe_load(open(cfg)) or {}
            enabled = "observability/langfuse" in ((c.get("plugins") or {}).get("enabled") or [])
        except Exception:
            enabled = "unparsed"
        try:
            names = [l.split("=", 1)[0].strip() for l in open(env) if "=" in l and not l.startswith("#")]
            keys = "HERMES_LANGFUSE_SECRET_KEY" in names and "HERMES_LANGFUSE_ENV" in names
        except OSError:
            keys = False
        res.append((prof, enabled, keys))
    return res


OBS_LANGFUSE = r"""
CH=$(docker ps --format '{{.Names}}' | grep -m1 clickhouse)
if [ -n "$CH" ]; then
  docker exec "$CH" sh -c 'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSV -q "SELECT count(), countIf(start_time > now() - INTERVAL 7 DAY), countIf(type = '"'"'GENERATION'"'"' AND start_time > now() - INTERVAL 7 DAY), toString(min(start_time)), toString(max(start_time)) FROM default.events_core"' 2>/dev/null | sed 's/^/EV|/'
  docker exec "$CH" sh -c 'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSV -q "SELECT environment, count() FROM default.events_core WHERE start_time > now() - INTERVAL 7 DAY GROUP BY environment ORDER BY 2 DESC"' 2>/dev/null | sed 's/^/ENV|/'
  docker exec "$CH" sh -c 'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSV -q "SELECT database, formatReadableSize(sum(bytes_on_disk)) FROM system.parts WHERE active GROUP BY database"' 2>/dev/null | sed 's/^/CHDISK|/'
fi
docker system df --format '{{.Type}}|{{.Size}}' 2>/dev/null | sed 's/^/DDF|/'
curl -s -o /dev/null -m 5 -w 'WEB|%{http_code}\n' http://127.0.0.1:3000/api/public/health
"""


def observability_probe():
    # Example area probe: a self-hosted Langfuse (docker compose inside a Proxmox CT)
    # vs the usage data Hermes already keeps in its own state.db.
    lf_node, lf_ct = cfg.get("TOOLSMITH_LANGFUSE_NODE"), cfg.get("TOOLSMITH_LANGFUSE_CT")
    ctl = f"CT{lf_ct}" if lf_ct else "CT?"
    section(f"AREA DEEP PROBE: observability (Langfuse on {ctl} vs Hermes' own state.db)")
    if not (lf_node and lf_ct.isdigit()):
        rc, out = 1, ""
        fact("Langfuse deep probe NOT MEASURED (TOOLSMITH_LANGFUSE_NODE / TOOLSMITH_LANGFUSE_CT not configured)")
    else:
        rc, out, err = ssh(f"root@{lf_node}", f"pct exec {int(lf_ct)} -- sh -s <<'EOF'\n" + OBS_LANGFUSE + "\nEOF\n"
                           f"pct config {int(lf_ct)} | awk '/^(memory|swap|cores|rootfs|onboot):/'\n", timeout=120)
        if rc != 0 and not out:
            fact(f"{ctl} langfuse deep probe NOT MEASURED (rc={rc})")
    if out:
        for l in out.splitlines():
            p = l.split("|")
            if p[0] == "EV" and len(p) >= 2:
                f = p[1].split("\t")
                if len(f) >= 5:
                    fact(f"Langfuse ClickHouse events_core: {f[0]} events total, {f[1]} in the last 7 days, "
                         f"{f[2]} GENERATION events in the last 7 days; first event {f[3]}, latest {f[4]} (UTC)")
            elif p[0] == "ENV" and len(p) >= 2:
                f = p[1].split("\t")
                if len(f) >= 2:
                    fact(f"Langfuse events last 7 days in environment '{f[0] or '(blank)'}': {f[1]}")
            elif p[0] == "CHDISK" and len(p) >= 2:
                f = p[1].split("\t")
                if len(f) >= 2:
                    fact(f"ClickHouse on {ctl}: database '{f[0]}' active parts use {f[1]} on disk")
            elif p[0] == "DDF" and len(p) >= 3:
                fact(f"{ctl} docker system df: {p[1]} {p[2]}")
            elif p[0] == "WEB":
                fact(f"Langfuse web /api/public/health from inside {ctl} returned HTTP {p[1]}")
            elif re.match(r"^(memory|swap|cores|rootfs|onboot):", l):
                fact(f"{ctl} langfuse config {l.strip()}")
    section("Hermes' own session DB usage (state.db per profile; sqlite read-only)")
    note("columns: sessions, model API calls, input tokens, output tokens, est. cost USD, tool calls")
    for prof, r, t in statedb_usage():
        if r is None:
            fact(f"state.db for profile {prof}: {t}")
            continue
        a, b = r[7], r[30]
        fact(f"state.db profile {prof}: last 7d {a[0]} sessions, {a[1]} API calls, {a[2]} input tok, {a[3]} output tok, "
             f"${a[4]} est cost, {a[5]} tool calls, {t} tool-result messages with timestamps | "
             f"last 30d {b[0]} sessions, {b[1]} API calls, ${b[4]} est cost")
    cols = []
    try:
        con = sqlite3.connect(f"file:{HH}/state.db?mode=ro", uri=True)
        cols = [r[1] for r in con.execute("PRAGMA table_info(sessions)")]
        con.close()
    except Exception:
        pass
    keep = [c for c in cols if c in ("model", "billing_provider", "input_tokens", "output_tokens", "reasoning_tokens",
                                     "cache_read_tokens", "estimated_cost_usd", "actual_cost_usd", "api_call_count",
                                     "tool_call_count", "started_at", "ended_at", "end_reason", "profile_name", "tool_names")]
    if keep:
        fact("state.db sessions table already records per session: " + ", ".join(keep) +
             "; messages table records role, tool_name, tool_calls and a timestamp per message")
    section("Langfuse plugin coverage per Hermes profile (config + .env key NAMES only)")
    for prof, en, keys in langfuse_coverage():
        fact(f"profile {prof}: observability/langfuse plugin enabled={en}; HERMES_LANGFUSE_* keys present in its .env={keys}")
    section("Dependency versions in the Hermes venv (the OpenTelemetry question)")
    rc, out, _ = sh([f"{cfg.HERMES_VENV}/bin/pip", "list", "--format=freeze"], timeout=60)
    for l in out.splitlines():
        if re.match(r"^(langfuse|opentelemetry-(api|sdk|exporter-otlp-proto-http))==", l, re.I):
            fact(f"Hermes venv has {l.strip()}")
    try:
        for i, l in enumerate(open(f"{cfg.HERMES_SRC}/tools/lazy_deps.py"), 1):
            if "opentelemetry" in l:
                fact(f"Hermes tools/lazy_deps.py line {i} pins {l.strip().strip(',')}")
    except OSError:
        fact("Hermes tools/lazy_deps.py NOT READABLE")


AREA_PROBES = {"observability": observability_probe}


# ---------------------------------------------------------------------------
def load_state():
    try:
        return json.load(open(f"{TS}/rotation.json"))
    except Exception:
        return {"history": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area")
    ap.add_argument("--list-areas", action="store_true")
    a = ap.parse_args()

    areas = json.load(open(f"{TS}/areas.json"))["areas"]
    services, hosts, nodes, errors = inventory()
    classify(services, areas)
    eligible = [x for x in areas if any(s["area"] == x["id"] and s["kind"] != "timer" or
                                        (s["area"] == x["id"] and x["id"] == "backups") for s in services)]
    state = load_state()
    last = state["history"][-1]["area"] if state.get("history") else None
    ids = [x["id"] for x in eligible]
    if a.area:
        area_id = a.area
    elif last in ids:
        area_id = ids[(ids.index(last) + 1) % len(ids)]
    else:
        area_id = ids[0] if ids else None
    if a.list_areas:
        for x in eligible:
            print(x["id"], "->", sorted({s["name"] for s in services if s["area"] == x["id"]}))
        print("NEXT:", area_id)
        return
    area = next((x for x in areas if x["id"] == area_id), None)
    if not area:
        print(f"ABORT: unknown or ineligible area {area_id}", file=sys.stderr)
        sys.exit(2)

    gen = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out_lines.append(f"# TOOLSMITH FACT PACK — area: {area['id']} ({area['title']})")
    out_lines.append(f"generated {gen} by toolsmith-gather.py (no LLM). Cite estate facts as [F###].")
    out_lines.append(f"AREA={area['id']}")

    section("GATHERER GAPS (things this run could not look at — NOT an all-clear)")
    if errors:
        for e in errors:
            fact(f"GAP: {e}")
    else:
        fact("GAP: none — every configured Proxmox node and SSH host answered")

    section("OWNER STANDING PREFERENCES (verified against their recorded source this run)")
    try:
        for p, ok in verify_preferences():
            fact(("" if ok else "UNVERIFIED (quote no longer found in source) — ") +
                 f"{p['pref']} [source {p['source']}]")
    except Exception as e:
        fact(f"preferences NOT MEASURED ({type(e).__name__})")

    section(f"AREA ROTATION (derived from the live inventory; this run = {area['id']})")
    fact("areas with >=1 live incumbent, in rotation order: " + ", ".join(ids))
    fact(f"previous toolsmith run area: {last or 'none (first run)'}")

    section(f"INCUMBENTS IN AREA {area['id']} (measured now)")
    inc = [s for s in services if s["area"] == area["id"]]
    if not inc:
        fact(f"no live incumbent matched keywords {area['keywords']}")
    for s in inc:
        where = f"{s['host']} CT{s['ct']} ({s['ctname']})" if s["ct"] else s["host"]
        bits = [f"{s['kind']} '{s['name']}'", f"image {s['image']}", f"on {where}"]
        if s["mem_mb"] is not None:
            bits.append(f"memory in use {s['mem_mb']} MB" + (" (whole CT)" if s["kind"] == "ct" else ""))
        if s["cpu"]:
            bits.append(f"CPU {s['cpu']}")
        if s["restarts"] not in ("", None):
            bits.append(f"restart count {s['restarts']}")
        if s["started"]:
            bits.append(f"running since {s['started']}Z")
        if s["health"] and s["health"] != "none":
            bits.append(f"health {s['health']}")
        fact(", ".join(bits))
    by_ct = {}
    for s in inc:
        if s["kind"] == "docker" and s["mem_mb"] is not None and s["ct"]:
            by_ct.setdefault((s["host"], s["ct"], s["ctname"]), []).append(s["mem_mb"])
    for (h, ct, nm), v in by_ct.items():
        fact(f"sum of docker memory in use for area {area['id']} on {h} CT{ct} ({nm}): {round(sum(v), 1)} MB across {len(v)} containers")

    section("HOST CAPACITY (measured now; LXC view of each CT, node view of each hypervisor)")
    for nd in nodes:
        fact(f"node {nd['name']} ({nd['ip']}): MemTotal {nd.get('mem_total_mb')} MB, MemAvailable {nd.get('mem_avail_mb')} MB")
        for pl in nd["pools"]:
            fact(f"node {nd['name']} storage pool {pl[0]}: {pl[2]} used")
        for dk in nd["disks"]:
            fact(f"node {nd['name']} disk {dk[0]} rotational={dk[1]} size {dk[2]} model {dk[3].strip()}")
    for h in hosts:
        label = f"{h['node']} CT{h['ct']} {h['name']}" if h["ct"] else h["name"]
        fact(f"{label}: rootfs {h.get('rootfs_pct', '?')}% used, {h.get('disk_free_mb', '?')} MB free of {h.get('disk_total_mb', '?')} MB; "
             f"RAM {h.get('mem_avail_mb', '?')} MB available of {h.get('mem_total_mb', '?')} MB; "
             f"{h.get('docker_containers', 0)} docker containers" + (f"; storage {h['storage']}" if h.get("storage") else ""))

    if area["id"] in AREA_PROBES:
        try:
            AREA_PROBES[area["id"]]()
        except Exception as e:
            fact(f"area deep probe crashed: {type(e).__name__}: {str(e)[:120]} — NOT MEASURED")

    section("RECORDED INCIDENTS / PAIN (operator notes; dated by file mtime; RECORDED, not re-measured)")
    picked, err = memory_notes(area["memory_keywords"], primary=area["keywords"])
    if err:
        fact(f"NOT MEASURED: {err}")
    else:
        for _, f, d, ln, text in picked:
            fact(f"recorded in {f}:{ln} (file last modified {d}): {text}")
    lessons = cfg.get("TOOLSMITH_KB_LESSONS")
    if lessons:
        try:
            for i, l in enumerate(open(lessons), 1):
                if any(k in l.lower() for k in area["memory_keywords"]):
                    fact(f"KB lesson {os.path.basename(lessons)}:{i}: {l.strip()[:240]}")
        except OSError:
            pass

    section("SANDBOX HOSTS (measured now for a 512 MB cap; toolsmith_sandbox.sh applies the same test at run time)")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("tsb", f"{SCRIPTS}/toolsmith_sandbox.py")
        tsb = importlib.util.module_from_spec(spec); spec.loader.exec_module(tsb)
        for node, ct, name, noteText in tsb.CANDIDATE_HOSTS:
            m = tsb.measure_host(node, ct, 512, 3000)
            fx = m["facts"]
            fact(f"sandbox host CT{ct} {name} ({noteText}): qualifies={m['qualifies']}; rootfs {fx.get('rootfs_pct')}%, "
                 f"CT MemAvailable {fx.get('ct_mem_avail_mb')} MB, node MemAvailable {fx.get('node_mem_avail_mb')} MB, "
                 f"disk free {fx.get('disk_free_mb')} MB" + (f"; refused because {', '.join(m['reasons'])}" if m["reasons"] else ""))
        for k, v in tsb.EXCLUDED_HOSTS.items():
            fact(f"never a sandbox host: {k} — {v}")
    except Exception as e:
        fact(f"sandbox host measurement NOT MEASURED ({type(e).__name__})")

    section("PREVIOUS TOOLSMITH DECISIONS (approvals state)")
    try:
        ap_state = json.load(open(f"{TS}/approvals.json"))
        recs = [r for r in ap_state.get("records", []) if not r.get("test")]
        if not recs:
            fact("no previous recommendations have been posted for approval")
        for r in recs:
            extra = f", do not re-propose before {r['cooldown_until']}" if r.get("cooldown_until") else ""
            fact(f"approval {r['approval_id']}: {r.get('incumbent')} -> {r.get('candidate')} status {r['status']}{extra}")
    except FileNotFoundError:
        fact("no previous recommendations have been posted for approval")
    except Exception as e:
        fact(f"approvals state unreadable ({type(e).__name__})")

    section("FULL ESTATE SERVICE INVENTORY (compact, for cross-area context)")
    for x in areas:
        names = sorted({f"{s['name']}@{('CT' + s['ct']) if s['ct'] else s['host']}" for s in services if s["area"] == x["id"]})
        if names:
            fact(f"area {x['id']}: " + ", ".join(names))
    other = sorted({f"{s['name']}@{('CT' + s['ct']) if s['ct'] else s['host']}" for s in services
                    if s["area"] is None and s["kind"] != "timer"})
    fact("unclassified running services: " + (", ".join(other) if other else "none"))

    print("\n".join(out_lines))


if __name__ == "__main__":
    main()
