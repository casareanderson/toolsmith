#!/bin/bash
# toolsmith-review.sh — the weekly toolsmith run (toolsmith-review.timer).
#
#   1. toolsmith-gather.py    deterministic fact pack for this week's area (no LLM)
#   2. phase 1 (model)        research candidates on the web, propose <=2 sandbox test specs
#   3. toolsmith_sandbox.sh   the SCRIPT runs the specs (the model never runs them here)
#   4. phase 2 (model)        write the recommendation report from facts + notes + sandbox JSON
#   5. toolsmith_validate.py  withhold anything unsupported (quarantine + reasons)
#   6. deliver                full report -> $TOOLSMITH_HOME/reports/, concise summary ->
#                             Discord (TOOLSMITH_DISCORD_CHANNEL_ID), ADOPT-TRIAL recs ->
#                             one approval message each (toolsmith_approvals.py post)
#
# Usage: toolsmith-review.sh [--area ID] [--no-post]
set -uo pipefail
export HOME=${HOME:-/root}
export HERMES_HOME=${HERMES_HOME:-$HOME/.hermes}
export TOOLSMITH_HOME=${TOOLSMITH_HOME:-$HERMES_HOME/toolsmith}
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

HERMES=${HERMES_BIN:-/usr/local/bin/hermes}
PY=${HERMES_VENV:-/usr/local/lib/hermes-agent/venv}/bin/python
S=$(dirname "$(readlink -f "$0")")
TS=$TOOLSMITH_HOME
PROFILE=${TOOLSMITH_PROFILE:-toolsmith}
HOST=$(hostname)
AREA_ARG=(); POST=1
while [ $# -gt 0 ]; do
  case "$1" in
    --area) AREA_ARG=(--area "$2"); shift 2 ;;
    --no-post) POST=0; shift ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done

RUN=$(date +%Y%m%d-%H%M)
START=$(date +%s)
D=$TS/runs/$RUN
mkdir -p "$D" "$TS/reports" "$TS/quarantine" "$TS/workspace"
log() { echo "[$(date '+%F %T')] $*"; }
say() {  # message to the toolsmith Discord channel
  [ "$POST" = 1 ] || { log "(no-post) would send: $1"; return 0; }
  printf '%s' "$1" | $PY $S/toolsmith_discord.py - >/dev/null || log "discord send failed"
}
ask() {  # ask <prompt-file> <out-file> <timeout>
  ( cd $TS/workspace && timeout "$3" $HERMES --accept-hooks --profile "$PROFILE" chat -Q --oneshot --query-file "$1" ) > "$2" 2> "$2.stderr"
}

log "run $RUN starting"
$PY $S/toolsmith-gather.py "${AREA_ARG[@]}" > "$D/facts.txt" 2> "$D/gather.stderr"
lines=$(grep -c '^\[F[0-9]' "$D/facts.txt")
AREA=$(sed -n 's/^AREA=//p' "$D/facts.txt")
if [ "$lines" -lt 40 ] || [ -z "$AREA" ]; then
  log "ABORT: gatherer produced $lines facts"
  say "🛠️ **Toolsmith run FAILED at the gather step** — only ${lines} facts, so no research was done. This is NOT a 'nothing to replace' result. Log: \`/var/log/toolsmith-review.log\`, run dir \`$D\` on $HOST."
  exit 1
fi
cp "$D/facts.txt" "$TS/facts/$RUN-$AREA.txt" 2>/dev/null || { mkdir -p $TS/facts; cp "$D/facts.txt" "$TS/facts/$RUN-$AREA.txt"; }
log "area=$AREA facts=$lines"

# ------------------------------------------------------------------ phase 1
cat > "$D/phase1.prompt" <<EOF
TOOLSMITH WEEKLY REVIEW — PHASE 1 of 2: RESEARCH + TEST PLAN. Area: $AREA.

Your SOUL rules apply in full. This phase does research only; you do not write the final report yet.

1. From the fact pack below, identify the incumbent(s) in area '$AREA' and the MEASURED pains (cite [F###]).
2. Research 3-5 realistic replacement candidates that fit the owner's preferences in the fact pack, PLUS the
   option of keeping / slimming the incumbent (including using data the estate already has, when the fact pack
   shows it). Use web_search and web_extract. For EACH candidate fetch, this run:
     - the project's own repository or official site
     - its LICENSE (the file or the SPDX id on the repo) — watch for source-available / enterprise-directory traps
     - its latest release (GitHub releases page or API) with the date
     - its documented self-hosting resource needs and dependencies (databases etc.)
   Record the exact URLs you fetched.
3. Choose AT MOST 2 candidates that are worth a sandbox test and have an official container image with an HTTP
   endpoint that starts WITHOUT internet access and without external databases (the sandbox has no egress and a
   512 MB-2048 MB cap; see the SANDBOX HOSTS facts). If none qualify, propose none and say why.

Output format (plain markdown, no preamble):
## Research notes
One subsection per candidate with bullet facts, each with its source URL.
## Test plan
A single fenced json block containing a list (possibly empty) of sandbox specs, e.g.
\`\`\`json
[{"candidate": "Name", "image": "org/image:tag", "port": 1234, "health_path": "/", "mem_mb": 1024,
  "cpus": 1, "timeout_s": 420, "max_image_mb": 3000, "env": {}, "tmpfs": [], "source_url": "https://..."}]
\`\`\`
Allowed keys only (candidate, image, port, health_path, env, command, mem_mb, cpus, pids, timeout_s, egress,
max_image_mb, tmpfs, source_url). Secret-looking env names need values starting with toolsmith-dummy-.
Take the image name, tag and port from a source you fetched. Do NOT run the sandbox yourself.

===== FACT PACK =====
$(cat "$D/facts.txt")
EOF
log "phase 1 (research) starting"
ask "$D/phase1.prompt" "$D/phase1.md" 2400; rc=$?
if [ $rc -ne 0 ] || [ ! -s "$D/phase1.md" ]; then
  log "phase 1 failed rc=$rc"
  say "🛠️ **Toolsmith run failed in research (phase 1, rc=$rc)** for area *$AREA*. Facts gathered fine (${lines}); nothing was recommended. Run dir \`$D\`."
  exit 1
fi
log "phase 1 done ($(wc -c < "$D/phase1.md") bytes)"

# ------------------------------------------------------------------ sandbox (script-run)
$PY - "$D/phase1.md" "$D" <<'PYEOF'
import json, re, sys, os
text = open(sys.argv[1]).read(); d = sys.argv[2]
blocks = re.findall(r"```json\s*(.*?)```", text, re.S)
specs = []
for b in blocks[::-1]:
    try:
        v = json.loads(b)
        specs = v if isinstance(v, list) else [v]
        break
    except Exception:
        continue
specs = [s for s in specs if isinstance(s, dict)][:2]
ws = os.path.join(os.environ["TOOLSMITH_HOME"], "workspace")
paths = []
for i, s in enumerate(specs, 1):
    p = f"{ws}/{os.path.basename(d)}-spec{i}.json"
    json.dump(s, open(p, "w"), indent=2); paths.append(p)
open(f"{d}/specs.txt", "w").write("\n".join(paths) + ("\n" if paths else ""))
print(f"{len(paths)} spec(s)")
PYEOF
: > "$D/sandbox-results.txt"
while read -r spec; do
  [ -n "$spec" ] || continue
  log "sandbox: $spec"
  timeout 1500 $S/toolsmith_sandbox.sh run --spec "$spec" > "$D/sandbox-$(basename "$spec" .json).out" 2>&1
  j=$(sed -n 's/^SANDBOX_JSON=//p' "$D/sandbox-$(basename "$spec" .json).out")
  if [ -n "$j" ] && [ -f "$j" ]; then
    echo "$j" >> "$D/sandbox-results.txt"
    log "sandbox result $(basename "$j"): $($PY -c "import json,sys; d=json.load(open('$j')); print(d['status'], d.get('host'), d['cleanup'].get('verified_clean'))")"
  else
    log "sandbox produced no JSON for $spec"
  fi
done < "$D/specs.txt"

SANDBOX_BLOCK=$($PY - "$D/sandbox-results.txt" <<'PYEOF'
import json, sys
out = []
for p in open(sys.argv[1]).read().split():
    d = json.load(open(p))
    m = dict(d.get("measurements", {})); m["log_tail"] = m.get("log_tail", [])[-8:]
    out.append(json.dumps({"run_id": d["run_id"], "candidate": d["spec"]["candidate"], "image": d["spec"]["image"],
        "status": d["status"], "refusal_reasons": d.get("refusal_reasons"), "host": d.get("host"),
        "caps": {k: d["spec"][k] for k in ("mem_mb", "cpus", "timeout_s", "egress")},
        "measurements": m, "cleanup_verified_clean": d.get("cleanup", {}).get("verified_clean"),
        "notes": d.get("notes")}, indent=1))
print("\n".join(out) if out else "(no sandbox tests were run this week)")
PYEOF
)

# ------------------------------------------------------------------ phase 2
cat > "$D/phase2.prompt" <<EOF
TOOLSMITH WEEKLY REVIEW — PHASE 2 of 2: THE REPORT. Area: $AREA. Date: $(date +%F).

Write the final recommendation report using ONLY: the fact pack (estate facts, cite [F###]), your phase-1
research notes (candidate facts with their URLs — you may re-fetch a page to confirm, but every URL you cite
must have been fetched in this run), and the sandbox JSON below (quote run_id + its numbers exactly).

Required shape (the validator parses it; anything malformed is withheld):
- 3-5 line verdict summary first (every estate number carries its [F###]).
- One block per candidate, INCLUDING a "keep the incumbent" / "use data we already have" option where live:
### REC <n>: <candidate name>
- Incumbent: <name> — why: <measured pain with [F###] beside each number>
- Candidate: <name, one line> — source: <url>
- Licence: <SPDX or exact name> — class: <OSI | source-available | non-commercial | unknown> — source: <url>
- Health: last release <YYYY-MM-DD> (<version>) — source: <url>
- Resources: needs <per source> — source: <url> vs target <host> free <numbers with [F###]>
- Sandbox: <run_id: status, numbers from the JSON> | not tested (<why>)
- Migration effort: <S/M/L + what moves>
- Rollback: <how>
- Verdict: <ADOPT-TRIAL | WATCH | REJECT> — <one sentence>
- Finish with: ## What I could not establish  (bullets)

Rules: no estate number without its [F###]; never compute sums/differences of estate numbers (quote them);
no sandbox claim without a run_id; licence from the project's own repo; "keep what we have" is a valid verdict
(express it as REJECT for the candidates it beats, or ADOPT-TRIAL only for a genuinely bounded trial the owner
would approve). No preamble, no code fences around the report.

===== SANDBOX RESULTS (from toolsmith_sandbox.sh, this run) =====
$SANDBOX_BLOCK

===== YOUR PHASE-1 RESEARCH NOTES =====
$(cat "$D/phase1.md")

===== FACT PACK =====
$(cat "$D/facts.txt")
EOF
log "phase 2 (report) starting"
ask "$D/phase2.prompt" "$D/report.raw.md" 1800; rc=$?
if [ $rc -ne 0 ] || [ ! -s "$D/report.raw.md" ]; then
  log "phase 2 failed rc=$rc"
  say "🛠️ **Toolsmith run failed writing the report (phase 2, rc=$rc)** for area *$AREA*. Research notes and sandbox results are kept in \`$D\`."
  exit 1
fi

# ------------------------------------------------------------------ validate
$PY $S/toolsmith_validate.py --facts "$D/facts.txt" --report "$D/report.raw.md" --since "$START" \
    --out "$D/validated.json" > "$D/validate.out" 2>&1
vrc=$?
log "validator rc=$vrc: $(tr -d '\n' < "$D/validate.out" | cut -c1-600)"
REPORT=$TS/reports/$(date +%F)-$AREA.md
$PY - "$D/validated.json" "$REPORT" "$AREA" "$RUN" "$D" <<'PYEOF'
import json, sys, os
v = json.load(open(sys.argv[1])); rep, area, run, d = sys.argv[2:6]
with open(rep, "w") as f:
    f.write(f"# Toolsmith report — {area} — run {run}\n\n")
    f.write("_Validated by toolsmith_validate.py: estate facts checked against the fact pack, URLs against this run's tool calls, sandbox claims against sandbox JSON._\n\n")
    f.write(v.get("report_md", ""))
    f.write(f"\n\n---\nFact pack: `{d}/facts.txt` · raw model report: `{d}/report.raw.md` · validator: `{d}/validated.json`\n")
if v.get("withheld") or not v.get("summary_ok", True):
    q = os.path.join(os.environ["TOOLSMITH_HOME"], "quarantine", f"{run}-{area}-withheld.md")
    with open(q, "w") as f:
        f.write(f"# Withheld by toolsmith_validate.py — run {run}\n\n")
        if not v.get("summary_ok", True):
            f.write("## Summary withheld\n" + "\n".join("- " + p for p in v["summary_problems"]) + "\n\n")
        for w in v.get("withheld", []):
            f.write(f"## REC {w['n']}: {w['title']}\nReasons:\n" + "\n".join("- " + r for r in w["reasons"]) +
                    "\n\n```\n" + w["body"] + "\n```\n\n")
print(rep)
PYEOF

# ------------------------------------------------------------------ deliver
MSG=$($PY - "$D/validated.json" "$REPORT" "$AREA" "$D/sandbox-results.txt" <<'PYEOF'
import json, sys, re, os
v = json.load(open(sys.argv[1])); rep, area, sbf = sys.argv[2:5]
lines = [f"🛠️ **Toolsmith — weekly review: {area}**"]
if v.get("fatal"):
    lines.append(f"⚠️ Report WITHHELD entirely: {v['fatal']}.")
for p in v.get("passed", []):
    why = (p["fields"].get("Verdict") or "").split("—", 1)[-1].strip()
    why = re.sub(r"\[F\d{3}\]", "", why)[:160]
    lines.append(f"• **{p['verdict']}** — {p['title']}: {why}")
sb = []
for path in open(sbf).read().split():
    d = json.load(open(path)); m = d.get("measurements", {})
    bits = [d["status"]] + [f"{k}={m[k]}" for k in ("image_size_mb", "startup_s", "mem_usage_mb") if k in m]
    sb.append(f"`{d['run_id']}` {d['spec']['candidate']}: " + ", ".join(map(str, bits)) +
              f", cleanup {'verified' if d.get('cleanup', {}).get('verified_clean') else 'NOT verified'}")
lines.append("🧪 Sandbox: " + ("; ".join(sb) if sb else "no candidate tested this week"))
if v.get("withheld"):
    lines.append(f"🚫 Withheld by validator ({len(v['withheld'])}): " +
                 "; ".join(f"{w['title']} — {w['reasons'][0][:90]}" for w in v["withheld"]))
if not v.get("summary_ok", True):
    lines.append("🚫 The model's summary was withheld (unsupported numbers); verdicts above are from validated RECs only.")
n_adopt = sum(1 for p in v.get("passed", []) if p["verdict"] == "ADOPT-TRIAL")
if n_adopt:
    lines.append(f"✅/❌ {n_adopt} ADOPT-TRIAL item(s) follow as separate messages — react to approve or reject.")
lines.append(f"Full report: `{rep}` on {os.uname().nodename}")
print("\n".join(lines))
PYEOF
)
say "$MSG"
if [ "$POST" = 1 ] && [ "$vrc" -eq 0 ]; then
  $PY $S/toolsmith_approvals.py post --validated "$D/validated.json" --run "$RUN" --area "$AREA" --report "$REPORT" || log "approval posting failed"
fi

# rotation state: record the area only after a delivered run
$PY - "$AREA" "$RUN" "$vrc" <<'PYEOF'
import json, sys, datetime, os
p = os.path.join(os.environ["TOOLSMITH_HOME"], "rotation.json")
try: s = json.load(open(p))
except Exception: s = {"history": []}
s["history"].append({"area": sys.argv[1], "run": sys.argv[2], "validator_rc": int(sys.argv[3]),
                     "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
json.dump(s, open(p, "w"), indent=2)
PYEOF
log "run $RUN done in $(( $(date +%s) - START ))s — report $REPORT"
