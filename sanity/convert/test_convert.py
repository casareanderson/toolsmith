"""Parser tests for convert.py (stdlib + pytest; no network, no real run data)."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import convert as C  # noqa: E402


def vals(text):
    return [(v, u) for v, u, _ in C.numbers_in(text)]


def test_licence_ids_are_not_numbers():
    # the text validator withheld the only ADOPT-TRIAL because it read Apache-2.0 as 2.0
    line = "Verdict: ADOPT-TRIAL — Apache-2.0 licence fits standing preferences [F002][F006]"
    assert vals(line) == []
    assert C.licence_of(line) == "Apache-2.0"
    assert C.licence_of("GNU AGPL v3 [source: ...]") == "AGPL-3.0"


def test_other_identifiers_are_not_numbers():
    for text in ["CF DNS-01 module compiled in", "banned families (XTTS-v2, F5-TTS)",
                 "last release 2026-06-03 (v2.11.4)", "langfuse==4.14.4", "CT112 and CT100",
                 "bulletin sb26-208", "latest commit Sep 14, 2026", "run ts-20260914-230927-e882",
                 "image clickhouse/clickhouse-server:25.12", "Traefik v3", "[F043-F044]",
                 "disk nvme0n1 model ST1000LM024"]:
        assert vals(text) == [], text


def test_measurements_keep_units():
    assert vals("memory in use 119.1 MB, CPU 9.23%") == [(119.1, "MB"), (9.23, "%")]
    assert vals("21300 MB free of 37997 MB") == [(21300, "MB"), (37997, "MB")]
    assert vals("est. cost $0.0146") == [(0.0146, "USD")]
    assert vals("0 docker containers") == [(0, "")]                   # no unit stolen from "docker"


def test_measurement_labels_show_the_clause():
    ms = C.measurements("node-b CT100 hermes: rootfs 41% used, 21300 MB free of 37997 MB")
    assert [m["label"] for m in ms] == ["rootfs #% used", "# MB free of 37997 MB", "21300 MB free of # MB"]


def test_cited_ids_expand_ranges():
    assert C.cited_ids("x [F012][F013] y [F043-F044] z [F054]-[F055]") == \
        ["F012", "F013", "F043", "F044", "F054", "F055"]


def fact(fid, statement):
    return {"_id": f"fact-{fid}", "factId": fid, "measurements": C.measurements(statement)}


def test_numbers_used_ties_only_backed_numbers():
    facts = {"F012": fact("F012", "web, memory in use 669.2 MB, restart count 4"),
             "F027": fact("F027", "rootfs 41% used, 21300 MB free of 37997 MB")}
    claim = ("Resources: needs 4 GB RAM; web 669.2 MB [F012]\n"
             "Resources: image ~23 MB vs CT100 free 21300 MB of 37997 MB [F027]")
    used = C.numbers_used(claim, facts)
    assert (669.2, "MB", "F012") in used
    assert (21300, "MB", "F027") in used and (37997, "MB", "F027") in used
    assert not any(v == 4 for v, _, _ in used)          # "4 GB" is not the restart count of 4
    assert not any(v == 23 for v, _, _ in used)         # a web figure with no fact stays unbacked


def test_count_words_match_unitless_facts():
    facts = {"F046": fact("F046", "685 events total, 219 in the last 7 days")}
    used = C.numbers_used("only 219 events / 7 days [F046]", facts)
    assert (219, "events", "F046") in used and (7, "days", "F046") in used


def test_sanitise():
    s = C.sanitise("node c1-pn2 (192.168.8.10) and C1-G4; ZimaBlade rootfs; pop-os; "
                   "auth-1.cn1-lab.uk [source memory:homelab-langfuse.md] file /root/.hermes/x.md "
                   "recorded in homelab-langfuse.md:103 owner 1309160150593966164 CT103 infisical")
    for bad in ["192.168", "c1-pn2", "C1-G4", "ZimaBlade", "pop-os", "cn1-lab", "homelab-langfuse",
                "/root/", "1309160150593966164", "infisical"]:
        assert bad not in s, bad
    assert "192.0.2.10" in s and "node-b" in s and "node-a" in s and "nas" in s and "gpu-host" in s


def test_parse_facts_cuts_private_sections():
    pack = "\n".join([
        "generated 2026-09-17 09:31 UTC by toolsmith-gather.py (no LLM).",
        "AREA=reverse-proxy",
        "===== HOST CAPACITY (measured now) =====",
        "[F027] node-b CT100 hermes: rootfs 41% used",
        "===== RECORDED INCIDENTS / PAIN =====",
        "[F039] recorded in x.md:1: an ingress weakness",
        "===== SANDBOX HOSTS =====",
        "[F079] sandbox host CT104 docker: qualifies=True; CT MemAvailable 1665 MB",
        "[F081] never a sandbox host: 192.168.8.20:108 — house-critical",
        "===== FULL ESTATE SERVICE INVENTORY =====",
        "[F090] inventory line",
    ])
    meta, facts = C.parse_facts(pack, "20260917-1030")
    assert meta == {"startedAt": "2026-09-17T09:31:00Z", "area": "reverse-proxy"}
    assert [f["factId"] for f in facts] == ["F027", "F079"]
    assert [f["kind"] for f in facts] == ["capacity", "sandbox"]
