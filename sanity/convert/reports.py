#!/usr/bin/env python3
"""Toolsmith report prose -> `report` docs, the source for the Knowledge Base.

  python3 reports.py [--examples DIR] [--out FILE]

The structured docs (convert.py) are what the validator checks. These are the other half:
the full weekly report and the validator's own "withheld" file for each run, kept as prose
so a Context Knowledge Base can index them. They answer "why was it withheld?" and "what
did the run say about X?"; they never back a number (only a shown recommendation does).

Input is the public repo's examples/, already sanitised; sanitise() runs again anyway.
"""
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from convert import sanitise  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EXAMPLES = os.path.join(HERE, "..", "..", "examples")
DEFAULT_OUT = os.path.join(HERE, "..", "data", "reports.ndjson")
RUN_IN_TITLE = re.compile(r"\brun (\d{8}-\d{4})\b")
# The repo's PII scrubber once read "Grafana" as a person's name; undo it here so the
# Knowledge Base doesn't index a tool called "GNU [PERSON_NAME] OSS".
FIXUPS = [(re.compile(r"GNU \[PERSON_NAME\] OSS"), "Grafana OSS")]


def report_doc(path, kind):
    text = open(path, encoding="utf-8").read()
    for pat, rep in FIXUPS:
        text = pat.sub(rep, text)
    text = sanitise(text)
    first = text.splitlines()[0].lstrip("# ").strip()
    m = RUN_IN_TITLE.search(first)
    if not m:
        raise ValueError(f"{path}: no 'run <id>' in the title line")
    run_id = m.group(1)
    return {"_id": f"{kind}-{run_id}", "_type": "report", "kind": kind, "title": first,
            "run": {"_type": "reference", "_ref": f"run-{run_id}"},
            "sourceFile": os.path.relpath(path, os.path.join(HERE, "..", "..")),
            "body": text}


def build(examples):
    docs = [report_doc(p, "report") for p in sorted(glob.glob(os.path.join(examples, "reports", "*.md")))]
    docs += [report_doc(p, "withheld") for p in sorted(glob.glob(os.path.join(examples, "quarantine", "*.md")))]
    return docs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--examples", default=DEFAULT_EXAMPLES)
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    docs = build(a.examples)
    with open(a.out, "w") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({d["_id"]: len(d["body"]) for d in docs}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
