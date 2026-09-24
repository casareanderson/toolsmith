#!/usr/bin/env python3
"""toolsmith_sandbox.py — the ONLY way the toolsmith profile runs a candidate tool.

Invoked through toolsmith_sandbox.sh (the one path the guard hook allows). Runs on
the Hermes box and drives a docker host container through the Proxmox hypervisor:
    ssh root@<node> 'pct exec <ct> -- sh -s'   (script on stdin, no nested quoting)
Sandbox hosts come from TOOLSMITH_SANDBOX_HOSTS in toolsmith.conf.

Subcommands
  hosts                         measure every candidate host, print verdicts (JSON)
  run --spec FILE [--host N:CT] run one candidate, write sandbox/<run_id>.json
  sweep                         remove ONLY objects labelled io.hermes.toolsmith=1
  refuse-test                   prove the refusal path (asks for an impossible cap)

Hard limits (not configurable from a spec):
  * docker run --rm-equivalent: every object carries io.hermes.toolsmith=1 and a
    per-run label, and is removed by LABEL in a finally block, by a systemd-run
    reaper inside the CT if this process dies, and by `sweep`
  * never --privileged, never host network/pid/ipc, never -v / --mount bind,
    never devices; only tmpfs mounts inside the container
  * --memory/--memory-swap, --cpus, --pids-limit always set; no-new-privileges
  * default network is an --internal bridge (no egress, no LAN reach); with
    egress=true the port is published on 127.0.0.1 only
  * env values must not be secrets: secret-looking names need a
    'toolsmith-dummy-' value, and no value may equal any value in a Hermes .env
  * host must MEASURE healthy: rootfs <= 80% used, CT and node MemAvailable
    >= 2 x cap, disk free >= 2 x max image size, no other toolsmith run live

⚠️ Never prune by ID pattern or by `docker ps --format {{.Image}}` — that is the
image-ID trap that nearly deleted a live container on the author's docker host
(2026-08-27). Every removal here is by label, or by the exact image reference
THIS run pulled.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import secrets as pysecrets
import shlex
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import toolsmith_config as cfg  # noqa: E402

TS_ROOT = cfg.TS_ROOT
OUT_DIR = TS_ROOT + "/sandbox"
LABEL = "io.hermes.toolsmith"
# Order = preference. Pick them by measurement (see README "Choosing sandbox hosts").
# [(node, ct, name, note)]
CANDIDATE_HOSTS = cfg.sandbox_hosts()
# Deliberately NOT sandbox hosts (recorded so the refusal is explicit, not silent):
# {"node:ct" or "host": reason}
EXCLUDED_HOSTS = cfg.excluded_hosts()
SECRETISH = re.compile(r"(KEY|SECRET|TOKEN|PASS|CRED|AUTH|PRIVATE|SALT|COOKIE)", re.I)
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]{1,128})?(@sha256:[a-f0-9]{64})?$")
MAX_ROOTFS_PCT = 80


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def remote(node, ct, script, timeout=120):
    """Run a POSIX sh script inside a CT. Returns (rc, stdout, stderr)."""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", f"root@{node}"]
    cmd += [f"pct exec {int(ct)} -- sh -s"] if ct else ["sh -s"]
    try:
        p = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


def measure_host(node, ct, cap_mb, max_image_mb):
    probe = r"""
command -v docker >/dev/null 2>&1 && echo "docker=$(docker version --format '{{.Server.Version}}' 2>/dev/null)" || echo docker=
df -P / | awk 'NR==2{gsub("%","",$5); print "rootfs_pct="$5; print "disk_free_mb="int($4/1024)}'
awk '/^MemAvailable:/{print "ct_mem_avail_mb="int($2/1024)} /^MemTotal:/{print "ct_mem_total_mb="int($2/1024)}' /proc/meminfo
echo "toolsmith_live=$(docker ps -q --filter label=io.hermes.toolsmith=1 2>/dev/null | wc -l)"
command -v curl >/dev/null && echo curl=yes || echo curl=no
command -v systemd-run >/dev/null && echo systemd_run=yes || echo systemd_run=no
"""
    rc, out, err = remote(node, ct, probe, timeout=40)
    facts = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    rc2, out2, _ = remote(node, None, "awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo", 20)
    facts["node_mem_avail_mb"] = out2.strip() if rc2 == 0 else ""
    v = {"node": node, "ct": ct, "measured_at": now_iso(), "facts": facts, "reasons": []}
    if rc != 0:
        v["reasons"].append(f"unreachable via pct exec (rc={rc}: {err.strip()[:120]})")
    else:
        def num(k):
            try:
                return int(facts.get(k, ""))
            except ValueError:
                return None
        if not facts.get("docker"):
            v["reasons"].append("no docker")
        if num("rootfs_pct") is None or num("rootfs_pct") > MAX_ROOTFS_PCT:
            v["reasons"].append(f"rootfs {facts.get('rootfs_pct')}% used > {MAX_ROOTFS_PCT}%")
        if num("ct_mem_avail_mb") is None or num("ct_mem_avail_mb") < 2 * cap_mb:
            v["reasons"].append(f"CT MemAvailable {facts.get('ct_mem_avail_mb')}MB < 2x cap ({2*cap_mb}MB)")
        if num("node_mem_avail_mb") is None or num("node_mem_avail_mb") < 2 * cap_mb:
            v["reasons"].append(f"node MemAvailable {facts.get('node_mem_avail_mb')}MB < 2x cap ({2*cap_mb}MB)")
        if num("disk_free_mb") is None or num("disk_free_mb") < 2 * max_image_mb:
            v["reasons"].append(f"disk free {facts.get('disk_free_mb')}MB < 2x max image ({2*max_image_mb}MB)")
        if num("toolsmith_live") not in (0,):
            v["reasons"].append(f"another toolsmith container is live ({facts.get('toolsmith_live')})")
        if facts.get("curl") != "yes" or facts.get("systemd_run") != "yes":
            v["reasons"].append("curl or systemd-run missing (health probe / reaper impossible)")
    v["qualifies"] = not v["reasons"]
    return v


def hermes_env_values():
    vals = set()
    files = [os.path.join(cfg.HERMES_HOME, ".env")] + glob.glob(os.path.join(cfg.HERMES_HOME, "profiles", "*", ".env"))
    files += [f for f in cfg.get("TOOLSMITH_EXTRA_ENV_FILES").split() if f]
    for f in files:
        try:
            for line in open(f, errors="ignore"):
                if "=" in line and not line.lstrip().startswith("#"):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    if len(val) >= 8:
                        vals.add(val)
        except OSError:
            pass
    return vals


def load_spec(path):
    spec = json.load(open(path))
    errs = []
    s = {
        "candidate": str(spec.get("candidate", "")).strip()[:80],
        "image": str(spec.get("image", "")).strip(),
        "port": spec.get("port"),
        "health_path": str(spec.get("health_path", "/")),
        "env": spec.get("env") or {},
        "command": spec.get("command") or [],
        "mem_mb": int(spec.get("mem_mb", 512)),
        "cpus": float(spec.get("cpus", 1)),
        "pids": int(spec.get("pids", 256)),
        "timeout_s": int(spec.get("timeout_s", 300)),
        "egress": bool(spec.get("egress", False)),
        "max_image_mb": int(spec.get("max_image_mb", 3000)),
        "tmpfs": spec.get("tmpfs") or [],
        "source_url": str(spec.get("source_url", "")),
    }
    for k in spec:
        if k not in s:
            errs.append(f"unknown spec key '{k}' (volumes/privileged/network are not settable)")
    if not s["candidate"]:
        errs.append("candidate is required")
    if not IMAGE_RE.match(s["image"]):
        errs.append("image must be a plain registry reference")
    if s["port"] is not None and not (isinstance(s["port"], int) and 1 <= s["port"] <= 65535):
        errs.append("port must be an integer 1-65535")
    if not s["health_path"].startswith("/") or re.search(r"[\s'\"`$;&|<>\\]", s["health_path"]):
        errs.append("health_path must be a plain absolute URL path")
    if not 64 <= s["mem_mb"] <= 2048:
        errs.append("mem_mb must be 64-2048")
    if not 0.1 <= s["cpus"] <= 2:
        errs.append("cpus must be 0.1-2")
    if not 32 <= s["pids"] <= 1024:
        errs.append("pids must be 32-1024")
    if not 60 <= s["timeout_s"] <= 900:
        errs.append("timeout_s must be 60-900")
    if not 50 <= s["max_image_mb"] <= 6000:
        errs.append("max_image_mb must be 50-6000")
    if not isinstance(s["command"], list) or not all(isinstance(c, str) for c in s["command"]):
        errs.append("command must be a list of strings")
    if not isinstance(s["tmpfs"], list) or not all(isinstance(t, str) and re.match(r"^/[A-Za-z0-9._/-]+$", t) for t in s["tmpfs"]):
        errs.append("tmpfs must be a list of absolute container paths")
    if not isinstance(s["env"], dict):
        errs.append("env must be an object")
    else:
        known = hermes_env_values()
        for k, v in s["env"].items():
            v = str(v)
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                errs.append(f"env name '{k}' invalid")
            if SECRETISH.search(k) and not v.startswith("toolsmith-dummy-"):
                errs.append(f"env '{k}' looks secret-bearing: its value must start with 'toolsmith-dummy-'")
            if v in known:
                errs.append(f"env '{k}' value equals a value from a Hermes .env — refusing (value not shown)")
    return s, errs


def run(args):
    s, errs = load_spec(args.spec)
    run_id = "ts-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + pysecrets.token_hex(2)
    rec = {"run_id": run_id, "started_at": now_iso(), "spec": s, "status": "refused",
           "refusal_reasons": [], "host": None, "host_measurements": [],
           "measurements": {}, "cleanup": {}, "notes": []}
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = f"{OUT_DIR}/{run_id}.json"

    def finish(code):
        rec["finished_at"] = now_iso()
        json.dump(rec, open(out_path, "w"), indent=2)
        print(json.dumps(rec, indent=2))
        print(f"\nSANDBOX_JSON={out_path}")
        sys.exit(code)

    if errs:
        rec["refusal_reasons"] = errs
        finish(2)

    hosts = CANDIDATE_HOSTS
    if not hosts:
        rec["refusal_reasons"] = ["no sandbox hosts configured (TOOLSMITH_SANDBOX_HOSTS in toolsmith.conf)"]
        finish(3)
    if args.host:
        node, _, ct = args.host.partition(":")
        key = f"{node}:{ct}"
        if key in EXCLUDED_HOSTS or node in EXCLUDED_HOSTS:
            rec["refusal_reasons"] = [f"{key} is excluded: {EXCLUDED_HOSTS.get(key) or EXCLUDED_HOSTS.get(node)}"]
            finish(2)
        hosts = [h for h in CANDIDATE_HOSTS if h[0] == node and str(h[1]) == ct]
        if not hosts:
            rec["refusal_reasons"] = [f"{key} is not a configured sandbox host"]
            finish(2)
    chosen = None
    for node, ct, name, note in hosts:
        m = measure_host(node, ct, s["mem_mb"], s["max_image_mb"])
        m["name"] = name
        rec["host_measurements"].append(m)
        if m["qualifies"]:
            chosen = (node, ct, name)
            break
    if not chosen:
        rec["refusal_reasons"] = ["no sandbox host qualifies: " + "; ".join(
            f"CT{m['ct']} {m['name']}: {', '.join(m['reasons'])}" for m in rec["host_measurements"])]
        finish(3)
    node, ct, name = chosen
    rec["host"] = {"node": node, "ct": ct, "name": name}

    cname, net, wimg = f"toolsmith-{run_id}", f"toolsmith-net-{run_id}", f"toolsmith-img-{run_id}:run"
    lab = f"--label {LABEL}=1 --label {LABEL}.run={run_id}"
    ref = s["image"]
    q = shlex.quote

    # Snapshot of everything that is NOT ours, to prove cleanup never touched it.
    snap = ("docker ps -aq --no-trunc | sort > /tmp/{0}.before.c; "
            "docker images -q --no-trunc | sort -u > /tmp/{0}.before.i; "
            "docker volume ls -q | sort > /tmp/{0}.before.v; "
            "docker image inspect {1} >/dev/null 2>&1 && echo PRESENT || echo ABSENT").format(run_id, q(ref))
    rc, out, err = remote(node, ct, snap, 60)
    pre_present = out.strip().endswith("PRESENT")
    rec["notes"].append(f"base image {'already present before this run (will NOT be removed)' if pre_present else 'absent before this run (this run pulls it and untags it afterwards)'}")

    cleanup_script = (
        f"docker ps -aq --filter label={LABEL}.run={run_id} | xargs -r docker rm -f >/dev/null 2>&1; "
        f"docker network ls -q --filter label={LABEL}.run={run_id} | xargs -r docker network rm >/dev/null 2>&1; "
        f"docker volume ls -q --filter label={LABEL}.run={run_id} | xargs -r docker volume rm >/dev/null 2>&1; "
        f"docker images -q --filter label={LABEL}.run={run_id} | sort -u | xargs -r docker rmi -f >/dev/null 2>&1; "
        + ("" if pre_present else f"docker rmi {q(ref)} >/dev/null 2>&1; ")
        + "true")
    # Reaper: if this process is SIGKILLed or the ssh drops, the CT cleans itself.
    reap_after = s["timeout_s"] + 900
    rc, out, err = remote(node, ct,
        f"systemd-run --quiet --unit=toolsmith-reap-{run_id} --on-active={reap_after} /bin/sh -c {q(cleanup_script)}", 30)
    rec["cleanup"]["reaper_armed"] = rc == 0
    if rc != 0:
        rec["refusal_reasons"] = [f"could not arm the in-CT reaper (rc={rc}: {err.strip()[:120]}) — refusing to start without it"]
        finish(4)

    def on_term(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGTERM, on_term)
    deadline = time.time() + s["timeout_s"]

    def body():
        rec["status"] = "failed"
        t0 = time.time()
        rc, out, err = remote(node, ct, f"timeout {min(600, s['timeout_s'])} docker pull -q {q(ref)}", min(600, s["timeout_s"]) + 30)
        rec["measurements"]["pull_s"] = round(time.time() - t0, 1)
        if rc != 0:
            rec["notes"].append(f"pull failed rc={rc}: {(err or out).strip()[-300:]}")
            return
        rc, out, err = remote(node, ct, f"docker image inspect --format '{{{{.Size}}}}' {q(ref)}", 30)
        try:
            size_mb = round(int(out.strip()) / 1048576, 1)
        except ValueError:
            size_mb = None
        rec["measurements"]["image_size_mb"] = size_mb
        if size_mb is None or size_mb > s["max_image_mb"]:
            rec["notes"].append(f"image size {size_mb}MB exceeds max_image_mb {s['max_image_mb']} — not started")
            return
        # A labelled wrapper image, so the image itself is removable by label.
        build = (f"printf 'FROM %s\\nLABEL {LABEL}=1 {LABEL}.run={run_id}\\n' {q(ref)} | "
                 f"timeout 180 docker build -q --pull=false -t {wimg} - ")
        rc, out, err = remote(node, ct, build, 240)
        run_image = wimg if rc == 0 else ref
        rec["measurements"]["image_labelled"] = rc == 0
        if rc != 0:
            rec["notes"].append(f"labelled wrapper build failed ({(err or out).strip()[-160:]}); ran base image, container still labelled")
        netflag = "" if s["egress"] else "--internal"
        rc, out, err = remote(node, ct, f"docker network create {netflag} {lab} {net} >/dev/null", 30)
        if rc != 0:
            rec["notes"].append(f"network create failed: {err.strip()[:160]}")
            return
        parts = ["docker run -d", f"--name {cname}", lab, f"--network {net}",
                 f"--memory {s['mem_mb']}m", f"--memory-swap {s['mem_mb']}m",
                 f"--cpus {s['cpus']}", f"--pids-limit {s['pids']}",
                 "--security-opt no-new-privileges:true",
                 "--cap-drop NET_RAW --cap-drop MKNOD --cap-drop AUDIT_WRITE --cap-drop SYS_CHROOT",
                 "--log-driver json-file --log-opt max-size=5m",
                 "--restart no"]
        for t in s["tmpfs"]:
            parts.append(f"--tmpfs {q(t)}:rw,size=256m")
        for k, v in s["env"].items():
            parts.append(f"-e {q(k + '=' + str(v))}")
        if s["egress"] and s["port"]:
            parts.append(f"-p 127.0.0.1::{s['port']}")
        parts.append(q(run_image))
        parts += [q(c) for c in s["command"]]
        t_start = time.time()
        rc, out, err = remote(node, ct, " ".join(parts), 60)
        if rc != 0:
            rec["notes"].append(f"docker run failed: {err.strip()[-300:]}")
            return
        rec["measurements"]["started"] = True
        ip = ""
        healthy_code = None
        last_code = "000"
        while time.time() < deadline - 15:
            rc, out, _ = remote(node, ct,
                f"docker inspect -f '{{{{.State.Status}}}} {{{{.State.OOMKilled}}}} {{{{.State.ExitCode}}}} {{{{range .NetworkSettings.Networks}}}}{{{{.IPAddress}}}}{{{{end}}}}' {cname}", 20)
            fields = out.split()
            state = fields[0] if fields else "unknown"
            if len(fields) >= 4:
                ip = fields[3]
            if state != "running":
                rec["measurements"]["state"] = state
                rec["measurements"]["oom_killed"] = fields[1] if len(fields) > 1 else None
                rec["measurements"]["exit_code"] = fields[2] if len(fields) > 2 else None
                break
            if not s["port"]:
                healthy_code = "no-port"
                time.sleep(min(20, max(0, deadline - time.time() - 20)))  # let a port-less candidate log
                break
            if ip:
                rc, out, _ = remote(node, ct,
                    f"curl -s -o /dev/null -m 3 -w '%{{http_code}}' http://{ip}:{int(s['port'])}{s['health_path']}", 15)
                last_code = out.strip() or "000"
                if last_code[:1] in ("2", "3"):
                    healthy_code = last_code
                    break
            time.sleep(2)
        rec["measurements"]["health_url"] = f"http://<container-ip>:{s['port']}{s['health_path']}" if s["port"] else None
        rec["measurements"]["last_http_code"] = last_code
        rec["measurements"]["healthy"] = healthy_code is not None and healthy_code != "no-port"
        if healthy_code:
            rec["measurements"]["startup_s"] = round(time.time() - t_start, 1)
            time.sleep(10)   # settle before sampling memory
            rc, out, _ = remote(node, ct,
                f"docker stats --no-stream --format '{{{{.MemUsage}}}}|{{{{.CPUPerc}}}}|{{{{.PIDs}}}}' {cname}", 40)
            rec["measurements"]["docker_stats_raw"] = out.strip()
            m = re.match(r"\s*([\d.]+)\s*([KMG]i?B)", out)
            if m:
                mult = {"KiB": 1 / 1024, "KB": 1 / 1024, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024}[m.group(2)]
                rec["measurements"]["mem_usage_mb"] = round(float(m.group(1)) * mult, 1)
            rec["measurements"]["mem_note"] = "docker stats MemUsage (cgroup usage minus inactive file cache), sampled 10s after first healthy response"
        rc, out, _ = remote(node, ct, f"docker logs --tail 25 {cname} 2>&1 | cut -c1-240", 30)
        rec["measurements"]["log_tail"] = out.splitlines()[-25:]
        rec["status"] = "healthy" if rec["measurements"].get("healthy") else (
            "started-no-port" if healthy_code == "no-port" else "unhealthy")

    try:
        body()
    except KeyboardInterrupt as e:
        rec["notes"].append(f"interrupted: {e}")
    finally:
        rc, out, err = remote(node, ct, cleanup_script + (
            f"; systemctl stop toolsmith-reap-{run_id}.timer >/dev/null 2>&1; "
            f"echo left_containers=$(docker ps -aq --filter label={LABEL}.run={run_id} | wc -l); "
            f"echo left_networks=$(docker network ls -q --filter label={LABEL}.run={run_id} | wc -l); "
            f"echo left_images=$(docker images -q --filter label={LABEL}.run={run_id} | wc -l); "
            f"docker ps -aq --no-trunc | sort > /tmp/{run_id}.after.c; "
            f"docker images -q --no-trunc | sort -u > /tmp/{run_id}.after.i; "
            f"docker volume ls -q | sort > /tmp/{run_id}.after.v; "
            f"echo foreign_containers_changed=$(diff /tmp/{run_id}.before.c /tmp/{run_id}.after.c | grep -c '^[<>]'); "
            f"echo foreign_images_removed=$(comm -23 /tmp/{run_id}.before.i /tmp/{run_id}.after.i | wc -l); "
            f"echo images_added_and_left=$(comm -13 /tmp/{run_id}.before.i /tmp/{run_id}.after.i | wc -l); "
            f"echo foreign_volumes_changed=$(diff /tmp/{run_id}.before.v /tmp/{run_id}.after.v | grep -c '^[<>]'); "
            f"rm -f /tmp/{run_id}.before.* /tmp/{run_id}.after.*"), 180)
        kv = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
        rec["cleanup"].update({k: kv.get(k) for k in (
            "left_containers", "left_networks", "left_images", "foreign_containers_changed",
            "foreign_images_removed", "images_added_and_left", "foreign_volumes_changed")})
        rec["cleanup"]["verified_clean"] = (
            kv.get("left_containers") == "0" and kv.get("left_networks") == "0"
            and kv.get("left_images") == "0" and kv.get("foreign_containers_changed") == "0"
            and kv.get("foreign_volumes_changed") == "0" and kv.get("foreign_images_removed") == "0")
        if kv.get("images_added_and_left", "0") != "0":
            rec["notes"].append("image layers added by this run remain (shared base layers or a base "
                                "image docker refused to untag because something else uses it)")
    finish(0 if rec["status"] in ("healthy", "started-no-port") else 1)


def sweep(_args):
    report = []
    for node, ct, name, _ in CANDIDATE_HOSTS:
        script = (
            f"echo containers=$(docker ps -aq --filter label={LABEL}=1 | wc -l); "
            f"docker ps -aq --filter label={LABEL}=1 | xargs -r docker rm -f >/dev/null 2>&1; "
            f"docker network ls -q --filter label={LABEL}=1 | xargs -r docker network rm >/dev/null 2>&1; "
            f"docker volume ls -q --filter label={LABEL}=1 | xargs -r docker volume rm >/dev/null 2>&1; "
            f"docker images -q --filter label={LABEL}=1 | sort -u | xargs -r docker rmi -f >/dev/null 2>&1; "
            f"echo remaining=$(( $(docker ps -aq --filter label={LABEL}=1 | wc -l) + "
            f"$(docker network ls -q --filter label={LABEL}=1 | wc -l) + "
            f"$(docker images -q --filter label={LABEL}=1 | wc -l) ))")
        rc, out, err = remote(node, ct, script, 120)
        report.append({"host": f"{node}:CT{ct} {name}", "rc": rc, "out": out.strip()})
    print(json.dumps(report, indent=2))


def hosts(args):
    print(json.dumps([dict(measure_host(n, c, args.cap_mb, args.max_image_mb), name=nm, note=nt)
                      for n, c, nm, nt in CANDIDATE_HOSTS] + [{"excluded": EXCLUDED_HOSTS}], indent=2))


def refuse_test(_args):
    path = f"{TS_ROOT}/workspace/.refuse-test-spec.json"
    json.dump({"candidate": "refusal-path self-test", "image": "traefik/whoami:v1.10",
               "port": 80, "mem_mb": 2048, "max_image_mb": 6000}, open(path, "w"))
    # 2048MB cap needs 4096MB MemAvailable on the CT AND the node, and 12GB disk.
    run(argparse.Namespace(spec=path, host=None))


def main():
    ap = argparse.ArgumentParser(prog="toolsmith_sandbox.sh")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hosts"); h.add_argument("--cap-mb", type=int, default=512); h.add_argument("--max-image-mb", type=int, default=3000)
    r = sub.add_parser("run"); r.add_argument("--spec", required=True); r.add_argument("--host")
    sub.add_parser("sweep")
    sub.add_parser("refuse-test")
    a = ap.parse_args()
    if a.cmd == "run" and not os.path.realpath(a.spec).startswith(TS_ROOT + "/"):
        print(f"refused: spec must live under {TS_ROOT}", file=sys.stderr)
        sys.exit(2)
    {"hosts": hosts, "run": run, "sweep": sweep, "refuse-test": refuse_test}[a.cmd](a)


if __name__ == "__main__":
    main()
