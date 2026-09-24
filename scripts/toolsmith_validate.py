#!/usr/bin/env python3
"""toolsmith_validate.py — mechanical fact check of a toolsmith report.

A prompt rule is not a control, so the report is checked against evidence and
anything unsupported is WITHHELD (quarantined with the reason), never sent with
a caveat.

A recommendation (### REC n: ...) is withheld when it:
  * cites a fact id [F###] that is not in the fact pack
  * quotes a number next to a fact id that does not appear in the cited fact line(s)
  * names a CT id or private (RFC 1918) IP that appears nowhere in the fact pack
  * gives no measured incumbent pain with a fact id
  * lacks a candidate source URL, or cites any URL not seen in this run's tool calls
  * lacks a licence line (SPDX/name + class + source URL)
  * claims a sandbox result without a sandbox run_id, cites a run_id with no
    sandbox JSON, or quotes sandbox numbers the JSON does not contain
  * has no valid verdict (ADOPT-TRIAL | WATCH | REJECT)

Usage:
  toolsmith_validate.py --facts F --report R --since EPOCH --out OUT.json [--sandbox-dir D] [--statedb DB]
Exit 0 = at least one REC passed; 1 = nothing publishable.
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import toolsmith_config as cfg  # noqa: E402

URL_RE = re.compile(r"https?://[^\s<>\"'`\])]+")
FID_RE = re.compile(r"\[F(\d{3})\]")
RUNID_RE = re.compile(r"\bts-\d{8}-\d{6}-[0-9a-f]{4}\b")
NUM_RE = re.compile(r"(?<![A-Za-z0-9.])\d[\d,]*(?:\.\d+)?")
CT_RE = re.compile(r"\bCT\s?(\d{3})\b")
IP_RE = re.compile(r"\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b")
FIELDS = ["Incumbent", "Candidate", "Licence", "Health", "Resources", "Sandbox",
          "Migration effort", "Rollback", "Verdict"]
CLASSES = ("osi", "source-available", "non-commercial", "unknown")


def norm_url(u):
    u = u.strip().rstrip(".,;:)*_")
    u = u.split("#", 1)[0]
    m = re.match(r"^(https?)://([^/]+)(.*)$", u, re.I)
    if not m:
        return u.lower()
    path = m.group(3).rstrip("/")
    host = m.group(2).lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}{path}"


def fetched_urls(statedb, since):
    """Every URL that appeared in a tool call or tool result of this run's sessions."""
    seen, extracted = set(), set()
    con = sqlite3.connect(f"file:{statedb}?mode=ro", uri=True, timeout=10)
    sids = [r[0] for r in con.execute("SELECT id FROM sessions WHERE started_at >= ?", (since - 5,))]
    for sid in sids:
        for role, content, tool_calls, tool_name in con.execute(
                "SELECT role, content, tool_calls, tool_name FROM messages WHERE session_id = ?", (sid,)):
            if role == "assistant" and tool_calls:
                for u in URL_RE.findall(tool_calls):
                    seen.add(norm_url(u))
                    if "web_extract" in tool_calls:
                        extracted.add(norm_url(u))
            if role == "tool" and content:
                for u in URL_RE.findall(content):
                    seen.add(norm_url(u))
    con.close()
    return seen, extracted, len(sids)


def num_tokens(text):
    out = set()
    for n in NUM_RE.findall(text):
        n = n.replace(",", "")
        out.add(n)
    return out


def numbers_supported(clause_nums, fact_text):
    have = num_tokens(fact_text)
    have_f = set()
    for h in have:
        try:
            have_f.add(float(h))
        except ValueError:
            pass
    bad = []
    for n in clause_nums:
        try:
            v = float(n)
        except ValueError:
            continue
        if n in have or v in have_f or any(abs(round(h) - v) < 1e-9 and v == int(v) for h in have_f):
            continue
        bad.append(n)
    return bad


def check_citations(text, facts):
    """Clause-level: numbers quoted in a clause carrying [F###] must be in those facts."""
    problems = []
    for line in text.splitlines():
        ids = FID_RE.findall(line)
        if not ids:
            continue
        for i in ids:
            if i not in facts:
                problems.append(f"cites [F{i}], which is not in the fact pack")
        # clauses: split on ; and ' vs ' and ' — ' ; each clause judged by its own citations
        for clause in re.split(r";|\bvs\.?\b| — | - (?=[A-Z])|\||(?<=[a-z\])])\.\s+(?=[A-Z])", line):
            cids = FID_RE.findall(clause)
            if not cids:
                continue
            ftext = " ".join(facts.get(c, "") for c in cids)
            stripped = FID_RE.sub(" ", clause)
            stripped = URL_RE.sub(" ", stripped)
            stripped = RUNID_RE.sub(" ", stripped)
            stripped = re.sub(r"\b(?:CT\s?\d{3}|F\d{3}|REC\s*\d+|v?\d+\.\d+\.\d+\S*)\b", " ", stripped)
            bad = numbers_supported(num_tokens(stripped), ftext)
            if bad:
                problems.append(f"number(s) {', '.join(sorted(set(bad))[:4])} next to "
                                f"{', '.join('[F' + c + ']' for c in cids)} are not in those fact lines")
    return problems


def uncited_measurements(text):
    """Estate-looking measurements (MB/GB/%/restarts/events) on a line with no fact id and no URL."""
    probs = []
    for line in text.splitlines():
        if FID_RE.search(line) or URL_RE.search(line) or RUNID_RE.search(line):
            continue
        m = re.search(r"(?i)\b\d[\d,]*(?:\.\d+)?\s*(MB|MiB|GB|GiB|%|restarts?|events|API calls|sessions)\b", line)
        if m:
            probs.append(f"uncited measurement '{m.group(0)}' (no [F###], URL or run_id on that line)")
    return probs


def check_identifiers(text, facts_blob):
    problems = []
    for ct in set(CT_RE.findall(text)):
        if f"CT{ct}" not in facts_blob:
            problems.append(f"names CT{ct}, which is not in the fact pack")
    for ip in set(IP_RE.findall(text)):
        if ip not in facts_blob:
            problems.append(f"names {ip}, which is not in the fact pack")
    return problems


def flatten(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            flatten(v, out)
    elif isinstance(obj, list):
        for v in obj:
            flatten(v, out)
    else:
        out.append(str(obj))
    return out


def parse_recs(report):
    parts = re.split(r"(?m)^###\s*REC\s*(\d+)\s*:\s*(.*)$", report)
    head = parts[0]
    recs = []
    for i in range(1, len(parts) - 2, 3):
        num, title, body = parts[i], parts[i + 1].strip(), parts[i + 2]
        # cut the body at the next top-level heading
        body = re.split(r"(?m)^##\s", body)[0]
        fields = {}
        for f in FIELDS:
            m = re.search(rf"(?mi)^\s*[-*]\s*\**{re.escape(f)}\**\s*:\s*(.+)$", body)
            fields[f] = m.group(1).strip() if m else None
        recs.append({"n": num, "title": title, "body": body.strip(), "fields": fields})
    tail = ""
    m = re.search(r"(?ims)^##+\s*What I could not establish.*", report)
    if m:
        tail = m.group(0)
        # don't let the tail be swallowed into the last REC body
        if recs:
            recs[-1]["body"] = recs[-1]["body"].split(m.group(0).strip()[:40])[0].strip()
    return head.strip(), recs, tail.strip()


def validate(args):
    facts_text = open(args.facts).read()
    facts = {m.group(1): m.group(2) for m in re.finditer(r"(?m)^\[F(\d{3})\]\s*(.*)$", facts_text)}
    report = open(args.report).read()
    seen, extracted, nsess = fetched_urls(args.statedb, args.since)
    sandbox = {}
    for p in glob.glob(os.path.join(args.sandbox_dir, "ts-*.json")):
        try:
            d = json.load(open(p))
            sandbox[d["run_id"]] = d
        except Exception:
            pass

    head, recs, tail = parse_recs(report)
    result = {"sessions_checked": nsess, "urls_seen_this_run": len(seen), "passed": [], "withheld": [],
              "summary_ok": True, "summary_problems": []}

    sp = check_citations(head, facts) + check_identifiers(head, facts_text) + uncited_measurements(head)
    if sp:
        result["summary_ok"], result["summary_problems"] = False, sp

    for r in recs:
        f, reasons = r["fields"], []
        body = r["body"]
        reasons += check_citations(body, facts)
        reasons += check_identifiers(body, facts_text)
        inc = f.get("Incumbent")
        if not inc:
            reasons.append("no Incumbent line")
        elif not FID_RE.search(inc):
            reasons.append("incumbent pain carries no [F###] fact id")
        else:
            for seg in re.split(r"[;,]", inc):
                reasons += uncited_measurements(seg)
        cand = f.get("Candidate")
        if not cand or not URL_RE.search(cand):
            reasons.append("no candidate source URL")
        lic = f.get("Licence")
        if not lic:
            reasons.append("no Licence line")
        else:
            if not URL_RE.search(lic):
                reasons.append("licence has no source URL")
            m = re.search(r"class:\s*([A-Za-z-]+)", lic, re.I)
            if not m or m.group(1).lower() not in CLASSES:
                reasons.append("licence has no valid class (OSI | source-available | non-commercial | unknown)")
        for u in URL_RE.findall(body):
            if norm_url(u) not in seen:
                reasons.append(f"URL not fetched this run: {u[:90]}")
        sb = f.get("Sandbox") or ""
        ids = set(RUNID_RE.findall(body))
        for rid in ids:
            if rid not in sandbox:
                reasons.append(f"sandbox run_id {rid} has no sandbox JSON")
        if sb and not sb.lower().startswith("not tested"):
            sb_ids = RUNID_RE.findall(sb)
            if not sb_ids:
                reasons.append("sandbox result claimed without a run_id")
            else:
                vals = " ".join(" ".join(flatten(sandbox[i], [])) for i in sb_ids if i in sandbox)
                bad = numbers_supported(num_tokens(RUNID_RE.sub(" ", URL_RE.sub(" ", sb))), vals)
                if bad:
                    reasons.append(f"sandbox number(s) {', '.join(bad[:4])} are not in the sandbox JSON")
                for i in sb_ids:
                    if i in sandbox and re.search(r"\bhealthy\b", sb, re.I) and not re.search(r"\bunhealthy\b", sb, re.I) \
                            and sandbox[i].get("status") != "healthy":
                        reasons.append(f"says healthy but sandbox {i} status is {sandbox[i].get('status')}")
        elif not sb:
            reasons.append("no Sandbox line")
        others = body.replace(sb, "")
        if re.search(r"(?i)\bsandbox\w*\b[^\n]{0,80}\b\d+(\.\d+)?\s*(MB|MiB|GB|s|sec|seconds|ms)\b", others) and not ids:
            reasons.append("sandbox measurement stated outside the Sandbox line without a run_id")
        v = (f.get("Verdict") or "").upper()
        vm = re.match(r"\**\s*(ADOPT-TRIAL|WATCH|REJECT)\b", v)
        if not vm:
            reasons.append("no valid verdict (ADOPT-TRIAL | WATCH | REJECT)")
        rec = {"n": r["n"], "title": r["title"], "fields": f, "body": body,
               "verdict": vm.group(1) if vm else None}
        if reasons:
            rec["reasons"] = sorted(set(reasons))
            result["withheld"].append(rec)
        else:
            rec["extracted_sources"] = sorted({u for u in URL_RE.findall(body) if norm_url(u) in extracted})
            result["passed"].append(rec)

    # Publishable markdown: summary (or a deterministic replacement), passed RECs, withheld notes.
    out = []
    if result["summary_ok"] and head:
        out.append(head)
    else:
        out.append("**Summary withheld by the validator** (" + "; ".join(result["summary_problems"][:3]) +
                   "). Deterministic summary of the recommendations that passed:")
        for p in result["passed"]:
            out.append(f"- {p['verdict']}: {p['title']}")
    for p in result["passed"]:
        out.append(f"\n### REC {p['n']}: {p['title']}\n{p['body']}")
    if result["withheld"]:
        out.append("\n## Withheld by the validator")
        for w in result["withheld"]:
            out.append(f"- REC {w['n']} ({w['title']}): " + "; ".join(w["reasons"][:5]))
    if tail:
        tp = check_identifiers(tail, facts_text) + [p for p in check_citations(tail, facts)]
        out.append("\n" + tail if not tp else "\n## What I could not establish\n(withheld: " + "; ".join(tp[:3]) + ")")
    result["report_md"] = "\n".join(out).strip() + "\n"
    if not recs:
        result["fatal"] = "no '### REC n:' blocks could be parsed — nothing is publishable"
    json.dump(result, open(args.out, "w"), indent=2)
    print(json.dumps({k: result[k] for k in ("sessions_checked", "urls_seen_this_run", "summary_ok")} |
                     {"passed": [p["title"] for p in result["passed"]],
                      "withheld": [(w["title"], w["reasons"]) for w in result["withheld"]],
                      "fatal": result.get("fatal")}, indent=2))
    return 0 if result["passed"] else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--since", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sandbox-dir", default=os.path.join(cfg.TS_ROOT, "sandbox"))
    ap.add_argument("--statedb", default=os.path.join(cfg.PROFILE_DIR, "state.db"))
    sys.exit(validate(ap.parse_args()))


if __name__ == "__main__":
    main()
