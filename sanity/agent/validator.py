"""The structured validator: a recommendation is shown only if its numbers are backed.

The 2026-09 text validator pulled digits out of prose and looked for them in the cited
fact lines. It worked, but it could not tell a number from an identifier: it withheld
the only ADOPT-TRIAL it ever produced because "Apache-2.0" contains 2.0, and "DNS-01"
contains 01. Here nothing is guessed from prose:

  1. every entry in `numbersUsed` must be held by the fact it points at (value and unit,
     from that fact's typed `measurements`), and that fact must be one the
     recommendation cites;
  2. no number may be left in the claim text that `numbersUsed` doesn't account for.
     Identifiers are known from their FIELDS (licence SPDX ids of the cited facts and
     the candidate, fact ids, dates) and are removed first, so "Apache-2.0" is a licence;
  3. an ADOPT-TRIAL needs a candidate whose licence class is OSI, the owner's standing
     rule that tools must be free and open source.

GROQ below is the one query the agent's `present_recommendation` tool sends through
Context MCP: the recommendation with its cited facts and number sources joined in.
"""
import re

REC_QUERY = """*[_type == "recommendation" && _id == $id][0]{
  _id, recId, title, verdict, claim,
  "run": run->{runId, "area": area->title},
  "candidate": candidate->{name, homepage, licenceSpdx, licenceClass},
  "citedFacts": citedFacts[]->{_id, factId, kind, statement, licenceSpdx,
                               "measurements": measurements[]{label, value, unit}},
  "numbersUsed": numbersUsed[]{value, unit, "fact": fact->{_id, factId,
                               "measurements": measurements[]{label, value, unit}}},
  textValidator
}"""

NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?![\w])")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?\b")
FACT_ID = re.compile(r"\[?F\d{3}\]?")
REC_ID = re.compile(r"\bREC\s*\d+\b", re.I)
URL = re.compile(r"https?://\S+")
RUN_ID = re.compile(r"\b(?:rec-|fact-|run-|ts-)?\d{8}-\d{4,6}(?:-[A-Za-z0-9]+)?\b")
# Versions and image tags: 2026.9.1, v3.1.2, :2026.5.0
VERSION = re.compile(r"(?:\bv|:)?\b\d+(?:\.\d+){2,}\b")
# Word-hyphen-number identifiers: licences (Apache-2.0, GPL-3.0-only), DNS-01, ISO-8601 ...
IDENT = re.compile(r"\b[A-Za-z][A-Za-z0-9+]*(?:-[A-Za-z]+)*-\d+(?:\.\d+)*(?:-[A-Za-z]+)*\b")
# Dates as prose writes them ("Sep 14, 2026", "14 September 2026") and bare run stamps (20260914)
PROSE_DATE = re.compile(r"\b(?:\d{1,2}\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
                        r"(?:\s+\d{1,2})?,?\s+\d{4}\b")
STAMP = re.compile(r"\b20\d{6}\b")
TOL = 0.005          # 0.5 %: "30.8 MB" in a claim vs 30.8 in the fact


def _num(s):
    return float(s.replace(",", ""))


def _close(a, b):
    return abs(a - b) <= max(abs(b) * TOL, 1e-9)


def _unit_ok(u_used, u_fact):
    return not u_used or not u_fact or u_used.strip().lower() == u_fact.strip().lower()


def identifiers(rec):
    """Strings that look numeric but are identifiers, known from structured fields."""
    ids = set()
    for f in rec.get("citedFacts") or []:
        if f and f.get("licenceSpdx"):
            ids.add(f["licenceSpdx"])
    cand = rec.get("candidate") or {}
    if cand.get("licenceSpdx"):
        ids.add(cand["licenceSpdx"])
    return sorted(ids, key=len, reverse=True)


def loose_numbers(rec):
    """Numbers left in the claim once identifiers, fact ids, dates and URLs are removed."""
    text = rec.get("claim") or ""
    for ident in identifiers(rec):
        text = re.sub(re.escape(ident), " ", text, flags=re.I)
    for pat in (URL, DATE, PROSE_DATE, RUN_ID, STAMP, FACT_ID, REC_ID, VERSION, IDENT):
        text = pat.sub(" ", text)
    return [_num(m.group(0)) for m in NUM.finditer(text)]


def prose_numbers(text):
    """Numbers in free text (an agent's answer) once ids, dates, URLs and word-hyphen-number
    identifiers are removed. Used by the answer guard, where no structured fields exist."""
    for pat in (URL, DATE, PROSE_DATE, RUN_ID, STAMP, FACT_ID, REC_ID, VERSION, IDENT):
        text = pat.sub(" ", text)
    return [_num(m.group(0)) for m in NUM.finditer(text)]


PROTECTED = (URL, DATE, PROSE_DATE, RUN_ID, STAMP, FACT_ID, REC_ID, VERSION, IDENT)


def redact_numbers(text, bad):
    """Replace each unbacked number with [not verified], leaving ids, dates, URLs and versions
    intact -- the same spans prose_numbers() skips, so a run id never loses its suffix."""
    if not bad:
        return text
    spans = sorted({m.span() for pat in PROTECTED for m in pat.finditer(text)})
    keep = []
    for s, e in spans:
        if keep and s < keep[-1][1]:
            keep[-1] = (keep[-1][0], max(e, keep[-1][1]))
        else:
            keep.append((s, e))

    # Match whole numbers the way the detector reads them, then compare by value, so an
    # unbacked 2.0 is redacted as "2.0" and never leaves "[not verified].0" behind.
    def scrub(chunk):
        return NUM.sub(lambda m: "[not verified]" if any(_close(_num(m.group(0)), b) for b in bad)
                       else m.group(0), chunk)

    out, pos = [], 0
    for s, e in keep:
        out += [scrub(text[pos:s]), text[s:e]]
        pos = e
    out.append(scrub(text[pos:]))
    return "".join(out)


def validate(rec):
    """-> {"status": "shown"|"withheld", "reasons": [...], "checked": [...]}"""
    if not rec:
        return {"status": "withheld", "reasons": ["no such recommendation"], "checked": []}
    reasons, checked = [], []
    cited = {f["_id"] for f in rec.get("citedFacts") or [] if f}
    used = rec.get("numbersUsed") or []

    # 1. each used number is held by a cited fact
    for u in used:
        fact = u.get("fact") or {}
        fid = fact.get("factId", "?")
        if fact.get("_id") not in cited:
            reasons.append(f"{u.get('value')} relies on {fid}, which the recommendation does not cite")
            continue
        hit = next((m for m in fact.get("measurements") or []
                    if _close(u["value"], m["value"]) and _unit_ok(u.get("unit"), m.get("unit"))), None)
        if hit:
            checked.append(f"{u['value']}{(' ' + u['unit']) if u.get('unit') else ''} ← {fid} ({hit['label']})")
        else:
            reasons.append(f"{u.get('value')} {u.get('unit') or ''}".strip() + f" is not a measurement of {fid}")

    # 2. nothing numeric left unaccounted for in the claim
    backed = [u["value"] for u in used if u.get("value") is not None]
    for n in loose_numbers(rec):
        if not any(_close(n, b) for b in backed):
            reasons.append(f"the claim states {n:g} but no cited fact backs it")

    # 3. adopting a tool needs an open-source licence
    if rec.get("verdict") == "ADOPT-TRIAL":
        cand = rec.get("candidate") or {}
        if cand.get("licenceClass") != "OSI":
            reasons.append(f"ADOPT-TRIAL needs an OSI licence; candidate is "
                           f"{cand.get('licenceSpdx') or 'unknown'} ({cand.get('licenceClass') or 'unclassified'})")

    return {"status": "withheld" if reasons else "shown", "reasons": reasons, "checked": checked}
