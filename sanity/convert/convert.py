#!/usr/bin/env python3
"""Toolsmith run artefacts -> Sanity NDJSON for the public `toolsmith` dataset.

  python3 convert.py [--runs DIR] [--areas FILE] [--out FILE]

Deterministic (same input, same output, stable _ids) and stdlib only.

What goes in, per run (runs/<runId>/):
  facts.txt       -> `fact` docs. Every number becomes a typed measurement; licence ids
                     (Apache-2.0 ...) and other identifiers (DNS-01, v2.11.4, dates) are
                     NOT numbers. That distinction is the point of the dataset.
  validated.json  -> `recommendation` + `candidateTool` docs. The old text validator's
                     verdict and reasons are kept verbatim in `textValidator`.

The dataset is PUBLIC, so everything is sanitised the way the public toolsmith repo was:
RFC 5737 addresses, generic node names, owner-note sources, and the sections that
describe live weaknesses (recorded incidents, the full inventory, host roles) are cut.
See overrides.json for the hand-curated recommendation texts and why.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RUNS = "/root/.hermes/toolsmith/runs"
DEFAULT_AREAS = os.path.join(HERE, "..", "..", "data", "areas.json")
DEFAULT_OUT = os.path.join(HERE, "..", "data", "toolsmith.ndjson")
RUNS = ["20260914-2240", "20260914-2305", "20260917-1030"]

# ------------------------------------------------------------------ sanitising
SUBS = [
    (re.compile(r"\b192\.168\.8\.(\d{1,3})\b"), r"192.0.2.\1"),
    (re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.(\d{1,3})\b"), r"198.51.100.\1"),
    (re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.(\d{1,3})\b"), r"198.51.100.\1"),
    (re.compile(r"\bc1-pn2\b", re.I), "node-b"),
    (re.compile(r"\bC1-G4\b", re.I), "node-a"),
    (re.compile(r"\bpn2-data\b"), "data-hdd"),
    (re.compile(r"\bzima-backup\b", re.I), "nas-backup"),
    (re.compile(r"\bZimaBlade\b|\bZima\b", re.I), "nas"),
    (re.compile(r"\bpop-os\b", re.I), "gpu-host"),
    (re.compile(r"\bcn1-lab\.uk\b", re.I), "example.org"),
    (re.compile(r"\bCT103 infisical\b", re.I), "CT103 secrets"),        # as in the public repo's excerpt
    (re.compile(r"\[source memory:[^\]]*\]"), "[source: owner note]"),
    (re.compile(r"\[source file:[^\]]*\]"), "[source: local file]"),
    (re.compile(r"recorded in [\w.-]+\.md:\d+"), "recorded in an owner note"),
    (re.compile(r"(?<![\w/])/root/[\w./-]*"), "a local file"),
    (re.compile(r"\b\d{17,20}\b"), "<id>"),                    # Discord and similar ids
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<email>"),
    (re.compile(r"(?<![\d.])\.(149|152|120)\b"), lambda m: {"149": "nas", "152": "gpu-host", "120": "CT100"}[m.group(1)]),
]


def sanitise(text):
    for pat, rep in SUBS:
        text = pat.sub(rep, text)
    return text


# Sections of a fact pack. Kept ones are published; the rest are cut (see module doc).
SECTIONS = [
    ("GATHERER GAPS", "gap"),
    ("OWNER STANDING PREFERENCES", "preference"),
    ("AREA ROTATION", "rotation"),
    ("INCUMBENTS IN AREA", "incumbent"),
    ("HOST CAPACITY", "capacity"),
    ("AREA DEEP PROBE", "other"),
    ("Hermes' own session DB usage", "other"),
    ("Langfuse plugin coverage", "other"),
    ("Dependency versions", "other"),
    ("RECORDED INCIDENTS", "incident"),        # cut, except the allow-list below
    ("SANDBOX HOSTS", "sandbox"),
    ("PREVIOUS TOOLSMITH DECISIONS", "other"),
    ("FULL ESTATE SERVICE INVENTORY", "inventory"),   # cut
]
SOURCES = {"gap": "gatherer self-check", "preference": "owner note, re-verified this run",
           "rotation": "live inventory", "incumbent": "docker/LXC inventory, measured",
           "capacity": "Proxmox + LXC, measured", "other": "gatherer probe, measured",
           "incident": "owner note (recorded, not re-measured)", "sandbox": "sandbox host check, measured"}
# Recorded incidents that the published recommendations rely on and that describe no
# weakness: the OpenTelemetry pin conflict and the measured trace coverage. Everything
# else in that section (patching blind spots etc.) stays private.
INCIDENT_ALLOW = {"20260914-2240": {"F099", "F100"}, "20260914-2305": {"F099", "F100"}}

# ------------------------------------------------------------------ numbers vs identifiers
SPDX = re.compile(r"\b(Apache-2\.0|MIT|AGPL-3\.0(?:-only|-or-later)?|GPL-[23]\.0(?:-only|-or-later)?|"
                  r"LGPL-[23]\.[01]|MPL-2\.0|BSD-[23]-Clause|ISC|Unlicense|BUSL-1\.1|SSPL-1\.0|"
                  r"Elastic-2\.0|CC-BY-NC-[\d.]+|CC-BY-[\d.]+)\b")
LICENCE_WORDS = [(re.compile(r"GNU AGPL[ -]?v?3(?:\.0)?", re.I), "AGPL-3.0"),
                 (re.compile(r"\bApache[ -]2(?:\.0)?\b", re.I), "Apache-2.0"),
                 (re.compile(r"\bMIT\b"), "MIT")]
# Things that contain digits but are not measurements. Masked before numbers are read.
IDENTIFIERS = [
    re.compile(r"\[F\d{3}\](?:-\[F\d{3}\])?|\bF\d{3}(?:-F\d{3})?\b"),        # fact ids
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+Z?)?"),                     # dates / timestamps
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),                              # clock times
    SPDX,
    re.compile(r"\b[A-Za-z][\w]*-v?\d+(?:[.-]\d+)*\b"),                       # DNS-01, XTTS-v2, sb26-208, F5-TTS
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2},? \d{4}\b"),  # Sep 14, 2026
    re.compile(r"\b[A-Za-z]+\d+[A-Za-z\d]*\b"),                               # CT112, F5, ST1000LM024, nvme0n1, v3
    re.compile(r"[vV]?\d+(?:\.\d+){2,}(?:-[\w.]+)?"),                        # versions 2.11.4, 1.39.1
    re.compile(r"\b[vV]\d+(?:\.\d+)*\b"),                                     # v4, v3.7
    re.compile(r"[=:@]\S+"),                                                  # ==4.14.4, image:tag, @sha
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"),                      # addresses
    re.compile(r"https?://\S+"),                                              # URLs
    re.compile(r"\bts-\d{8}-\d{6}-\w+\b"),                                    # sandbox run ids
    re.compile(r"\bREC \d+\b"),
    re.compile(r"line \d+"),
]
UNIT = r"(%|MiB|MB|GiB|GB|kB|KB|TB|G|B|ms|s|USD|events|containers|sessions|API calls|tool calls|input tok|output tok|days|cores|core)"
NUMBER = re.compile(r"(?<![\w.])(\$)?(\d[\d,]*(?:\.\d+)?)(?:\s?" + UNIT + r"(?![A-Za-z]))?(?![\w.]*\d)")


def mask_identifiers(text):
    for pat in IDENTIFIERS:
        text = pat.sub(lambda m: " " * len(m.group(0)), text)
    return text


def numbers_in(text):
    """[(value, unit, start)] for every measurement-like number; identifiers excluded."""
    masked = mask_identifiers(text)
    out = []
    for m in NUMBER.finditer(masked):
        raw = m.group(2).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        unit = "USD" if m.group(1) else (m.group(3) or "")
        out.append((int(value) if value.is_integer() else value, unit, m.start(2)))
    return out


def licence_of(text):
    m = SPDX.search(text)
    if m:
        return m.group(1)
    for pat, spdx in LICENCE_WORDS:
        if pat.search(text):
            return spdx
    return None


def measurements(statement):
    """Every number as {label, value, unit}. The label is the clause the number sits in,
    with the number itself shown as #, e.g. "# MB free of 37997 MB"."""
    out = []
    for value, unit, start in numbers_in(statement):
        lo = max(statement.rfind(sep, 0, start) for sep in (",", ";", "|", "(", ":")) + 1
        hi_c = [statement.find(sep, start) for sep in (",", ";", "|", ")")]
        hi = min([h for h in hi_c if h != -1] or [len(statement)])
        raw = statement[start:start + len(str(value)) + 12]
        num_txt = re.match(r"\d[\d,]*(?:\.\d+)?", raw).group(0)
        label = (statement[lo:start] + "#" + statement[start + len(num_txt):hi]).strip(" -—=[")
        label = re.sub(r"\s+", " ", label)
        label = (label[:77] + "…") if len(label) > 78 else label
        out.append({"_key": f"m{len(out)}", "_type": "measurement", "label": label, "value": value,
                    **({"unit": unit} if unit else {})})
    return out


# ------------------------------------------------------------------ fact packs
FACT_LINE = re.compile(r"^\[(F\d{3})\]\s+(.*)$")


def parse_facts(text, run_id):
    """-> (meta, [fact dicts]) with section kinds; cut sections dropped."""
    kind, facts, meta = None, [], {}
    m = re.search(r"generated (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) UTC", text)
    if m:
        meta["startedAt"] = f"{m.group(1)}T{m.group(2)}:00Z"
    m = re.search(r"^AREA=(\S+)", text, re.M)
    if m:
        meta["area"] = m.group(1)
    for line in text.splitlines():
        if line.startswith("====="):
            kind = next((k for title, k in SECTIONS if title in line), "other")
            continue
        fm = FACT_LINE.match(line.strip())
        if not fm or kind is None:
            continue
        fid, statement = fm.groups()
        if kind == "inventory":
            continue
        if kind == "incident" and fid not in INCIDENT_ALLOW.get(run_id, set()):
            continue
        if kind == "sandbox" and statement.startswith("never a sandbox host"):
            continue                                   # host roles + addresses: private
        facts.append({"factId": fid, "kind": "other" if kind == "incident" else kind,
                      "statement": sanitise(statement), "source": SOURCES.get(kind, "gatherer")})
    return meta, facts


# ------------------------------------------------------------------ recommendations
VERDICT = re.compile(r"\b(ADOPT-TRIAL|KEEP|REJECT|WATCH)\b")
FIELD_ORDER = ["Incumbent", "Candidate", "Licence", "Health", "Resources", "Sandbox",
               "Migration effort", "Verdict"]
CITE = re.compile(r"\[(F\d{3})\](?:\s*-\s*\[(F\d{3})\])?|\[(F\d{3})-(F\d{3})\]")


def cited_ids(text):
    """[F012] [F043-F044] [F054]-[F055] -> ordered unique ids (ranges expanded)."""
    ids = []
    for m in CITE.finditer(text):
        a, b = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        lo, hi = int(a[1:]), int((b or a)[1:])
        for n in range(lo, hi + 1):
            fid = f"F{n:03d}"
            if fid not in ids:
                ids.append(fid)
    return ids


PHYSICAL = {"%", "MiB", "MB", "GiB", "GB", "kB", "KB", "TB", "G", "B", "ms", "s", "USD"}


def units_agree(claim_unit, fact_unit):
    """A physical unit must match exactly ("4 GB" is never backed by a restart count of 4).
    A count word ("219 events") may match a fact that states the count without one."""
    if claim_unit in PHYSICAL or fact_unit in PHYSICAL:
        return claim_unit == fact_unit
    return True


def numbers_used(claim, facts_by_id):
    """Numbers in the claim that sit on a line citing a fact that HOLDS that value.
    Numbers that can't be tied to a cited fact are left out on purpose: the claim still
    contains them, so a structured validator will withhold it."""
    used, seen = [], set()
    for line in claim.split("\n"):
        cites = [f for f in cited_ids(line) if f in facts_by_id]
        for value, unit, _ in numbers_in(line):
            for fid in cites:
                if any(m["value"] == value and units_agree(unit, m.get("unit", ""))
                       for m in facts_by_id[fid]["measurements"]):
                    key = (value, unit, fid)
                    if key not in seen:
                        seen.add(key)
                        used.append((value, unit, fid))
                    break
    return used


def claim_from_fields(fields):
    return "\n".join(f"{k}: {sanitise(fields[k])}" for k in FIELD_ORDER if fields.get(k))


def candidate_of(fields, title):
    cand = fields.get("Candidate") or ""
    url = re.search(r"source: (https?://\S+)", cand)
    lic = fields.get("Licence") or ""
    cls = re.search(r"class: (OSI|source-available|non-commercial|unknown)", lic)
    name = re.split(r"[,(—]| - ", title)[0].strip()
    return {"name": name, "homepage": url.group(1).rstrip(".;,") if url else None,
            "licenceSpdx": licence_of(lic), "licenceClass": cls.group(1) if cls else None}


# ------------------------------------------------------------------ assembly
def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def build(runs_dir, areas_file, overrides):
    docs = []
    areas = json.load(open(areas_file))
    areas = areas.get("areas", areas) if isinstance(areas, dict) else areas
    for a in areas:
        docs.append({"_id": f"area-{a['id']}", "_type": "toolArea", "areaId": {"_type": "slug", "current": a["id"]},
                     "title": a["title"], "keywords": a.get("keywords", [])})
    candidates = {}
    for run_id in RUNS:
        rdir = os.path.join(runs_dir, run_id)
        meta, facts = parse_facts(open(os.path.join(rdir, "facts.txt")).read(), run_id)
        area_ref = {"_type": "reference", "_ref": f"area-{meta['area']}"}
        run_doc = {"_id": f"run-{run_id}", "_type": "run", "runId": run_id, "area": area_ref,
                   "gaps": [f["statement"] for f in facts if f["kind"] == "gap"]}
        if meta.get("startedAt"):
            run_doc["startedAt"] = meta["startedAt"]
        docs.append(run_doc)
        by_id = {}
        for f in facts:
            doc = {"_id": f"fact-{run_id}-{f['factId']}", "_type": "fact", "factId": f["factId"],
                   "run": {"_type": "reference", "_ref": f"run-{run_id}"}, "kind": f["kind"],
                   "statement": f["statement"], "measurements": measurements(f["statement"]),
                   "source": f["source"]}
            lic = licence_of(f["statement"])
            if lic:
                doc["licenceSpdx"] = lic
            by_id[f["factId"]] = doc
            docs.append(doc)
        vpath = os.path.join(rdir, "validated.json")
        if not os.path.exists(vpath):
            continue                                   # 20260914-2240 never produced a report
        v = json.load(open(vpath))
        recs = [(r, "withheld") for r in v["withheld"]] + [(r, "published") for r in v["passed"]]
        for r, status in sorted(recs, key=lambda x: int(x[0]["n"])):
            rid = f"rec-{run_id}-{r['n']}"
            ov = overrides.get(rid, {})
            fields = r["fields"]
            claim = ov.get("claim") or claim_from_fields(fields)
            title = sanitise(ov.get("title") or r["title"])
            verdict_m = VERDICT.search(fields.get("Verdict") or r.get("verdict") or "")
            cand = candidate_of(fields, ov.get("candidateName") or r["title"])
            cand_ref = None
            if not ov.get("noCandidate"):
                cid = f"tool-{slug(cand['name'])}"
                if cid not in candidates:
                    candidates[cid] = {"_id": cid, "_type": "candidateTool", "name": cand["name"],
                                       "area": area_ref,
                                       **{k: cand[k] for k in ("homepage", "licenceSpdx", "licenceClass") if cand[k]}}
                cand_ref = {"_type": "reference", "_ref": cid}
            cites = [f for f in cited_ids(claim) if f in by_id]
            used = numbers_used(claim, by_id)
            doc = {"_id": rid, "_type": "recommendation", "recId": f"REC {r['n']}",
                   "run": {"_type": "reference", "_ref": f"run-{run_id}"}, "title": title,
                   "verdict": verdict_m.group(1) if verdict_m else "WATCH", "claim": claim,
                   "citedFacts": [{"_key": f, "_type": "reference", "_ref": by_id[f]["_id"]} for f in cites],
                   "numbersUsed": [{"_key": f"n{i}", "_type": "usedNumber", "value": val,
                                    **({"unit": unit} if unit else {}),
                                    "fact": {"_type": "reference", "_ref": by_id[fid]["_id"]}}
                                   for i, (val, unit, fid) in enumerate(used)],
                   "textValidator": {"status": status, "reasons": [sanitise(x) for x in r.get("reasons") or []]}}
            if cand_ref:
                doc["candidate"] = cand_ref
            docs.append(doc)
    docs.extend(candidates[k] for k in sorted(candidates))
    return docs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=DEFAULT_RUNS)
    ap.add_argument("--areas", default=DEFAULT_AREAS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    overrides = json.load(open(os.path.join(HERE, "overrides.json")))
    overrides = {k: v for k, v in overrides.items() if not k.startswith("_")}
    docs = build(a.runs, a.areas, overrides)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n")
    counts = {}
    for d in docs:
        counts[d["_type"]] = counts.get(d["_type"], 0) + 1
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
