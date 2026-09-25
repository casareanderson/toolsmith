"""The structured validator, on recommendation shapes exactly as REC_QUERY returns them."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.validator import loose_numbers, prose_numbers, validate  # noqa: E402

F_PREF = {"_id": "fact-r-F002", "factId": "F002", "kind": "preference",
          "statement": "Tools must be free and open source.", "measurements": []}
F_CT = {"_id": "fact-r-F027", "factId": "F027", "kind": "capacity",
        "statement": "host: rootfs 41% used, 21300 MB free", "measurements": [
            {"label": "rootfs used", "value": 41, "unit": "%"},
            {"label": "rootfs free", "value": 21300, "unit": "MB"}]}
F_LIC = {"_id": "fact-r-F049", "factId": "F049", "kind": "licence",
         "statement": "Caddy is licensed Apache-2.0", "licenceSpdx": "Apache-2.0", "measurements": []}


def caddy(**over):
    rec = {"_id": "rec-r-2", "recId": "REC 2", "title": "Caddy", "verdict": "ADOPT-TRIAL",
           "claim": "Caddy (Apache-2.0) fits the free-software rule [F002], [F049]; the host has 21300 MB "
                    "free and its rootfs is 41% used [F027].",
           "candidate": {"name": "Caddy", "licenceSpdx": "Apache-2.0", "licenceClass": "OSI"},
           "citedFacts": [F_PREF, F_CT, F_LIC],
           "numbersUsed": [{"value": 21300, "unit": "MB", "fact": F_CT},
                           {"value": 41, "unit": "%", "fact": F_CT}]}
    rec.update(over)
    return rec


def test_licence_is_an_identifier_not_the_number_2_0():
    # the text validator withheld exactly this on 2026-09-17 ("number 2.0 next to [F002]")
    assert loose_numbers(caddy()) == [21300.0, 41.0]
    v = validate(caddy())
    assert v["status"] == "shown", v["reasons"]
    assert "21300 MB ← F027 (rootfs free)" in v["checked"]


def test_a_number_the_cited_fact_does_not_hold_is_withheld():
    rec = caddy(numbersUsed=[{"value": 24000, "unit": "MB", "fact": F_CT}],
                claim="The host has 24000 MB free [F027].")
    v = validate(rec)
    assert v["status"] == "withheld" and "24000 MB is not a measurement of F027" in v["reasons"]


def test_a_loose_number_in_the_claim_is_withheld():
    rec = caddy(claim="Caddy uses about 0 MB more than NPMplus [F027].", numbersUsed=[])
    assert any("states 0" in r for r in validate(rec)["reasons"])


def test_numbers_must_come_from_a_cited_fact():
    rec = caddy(citedFacts=[F_PREF, F_LIC])          # F027 not cited any more
    assert any("does not cite" in r for r in validate(rec)["reasons"])


def test_adopt_trial_needs_an_osi_licence():
    rec = caddy(candidate={"name": "X", "licenceSpdx": "BUSL-1.1", "licenceClass": "source-available"})
    assert any("OSI licence" in r for r in validate(rec)["reasons"])


def test_units_must_match_and_tolerance_is_small():
    assert validate(caddy(numbersUsed=[{"value": 21300, "unit": "GB", "fact": F_CT},
                                       {"value": 41, "unit": "%", "fact": F_CT}]))["status"] == "withheld"
    ok = caddy(claim="21,300 MB free and 41% used [F027]", numbersUsed=[
        {"value": 21300, "unit": "MB", "fact": F_CT}, {"value": 41, "unit": "%", "fact": F_CT}])
    assert validate(ok)["status"] == "shown"


def test_answer_guard_ignores_ids_dates_and_licences():
    text = ("Run 20260917-1030 on 2026-09-17: rec-20260917-1030-2 (Caddy, Apache-2.0, DNS-01, "
            "GPL-3.0-only) cites [F049]; 21300 MB free.")
    assert prose_numbers(text) == [21300.0]


def test_missing_recommendation():
    assert validate(None)["status"] == "withheld"


def test_versions_and_image_tags_are_identifiers():
    rec = caddy(claim="cloudflared-web:2026.5.0 and v3.1.2 are fine; 21300 MB free and 41% used [F027]")
    assert loose_numbers(rec) == [21300.0, 41.0]
    assert prose_numbers("image wisdomsky/cloudflared-web:2026.5.0, Traefik v3.1.2") == []


def test_redact_numbers_keeps_run_ids():
    from agent.validator import redact_numbers
    out = redact_numbers("run-20260914-2305 found 2305 MB free", [2305.0])
    assert out == "run-20260914-2305 found [not verified] MB free"


def test_prose_dates_and_bulletin_ids_are_not_numbers():
    rec = {"claim": "repo committed Sep 14, 2026; CISA bulletin sb26-208; run 20260914 notes"}
    assert loose_numbers(rec) == []
    assert prose_numbers(rec["claim"]) == []


def test_real_quantities_still_count():
    assert loose_numbers({"claim": "needs 4 GB RAM and 2 cores"}) == [4.0, 2.0]


def test_sandbox_ids_are_not_numbers():
    assert loose_numbers({"claim": "sandbox ts-20260914-230927-e882 passed"}) == []
