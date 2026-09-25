import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reports  # noqa: E402

EX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "examples")


def test_every_report_links_a_run_and_is_clean():
    docs = reports.build(EX)
    assert {d["kind"] for d in docs} == {"report", "withheld"}
    for d in docs:
        assert d["run"]["_ref"].startswith("run-2026")
        assert "[PERSON_NAME]" not in d["body"]
        assert "192.168." not in d["body"] and "/root/" not in d["body"]


def test_ids_are_stable():
    assert [d["_id"] for d in reports.build(EX)] == [d["_id"] for d in reports.build(EX)]
