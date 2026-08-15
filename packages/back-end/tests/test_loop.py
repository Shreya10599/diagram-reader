"""test_loop.py — port of the original loop tests to the LangGraph pipeline.

Each scenario invokes the compiled graph directly (graph.invoke) with a fake
Claude client and asserts on the final AgentState: the number of API calls
(each = one extraction/verification node execution), the final status, and
the corrected series.

Run from packages/back-end:
    .venv/bin/python tests/test_loop.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers import (
    FakeClient,
    empty_extraction_response,
    extraction_response,
    make_image_png,
    run_graph,
    verdict_response,
)

IMAGE = make_image_png()
FAILED = 0


def check(name: str, cond: bool, detail: str = ""):
    global FAILED
    status = "PASS" if cond else "FAIL"
    if not cond:
        FAILED += 1
    print(f"  [{status}] {name}{'  ' + detail if detail and not cond else ''}")


def test_match_on_first_round():
    print("match on first round")
    client = FakeClient(
        [
            extraction_response(("A", 10.0), ("B", 20.0)),
            verdict_response(match=True),
        ]
    )
    final = run_graph(client, IMAGE)
    check("2 Claude calls (extract + 1 verify)", client.messages.calls == 2, f"got {client.messages.calls}")
    check("status MATCH", final["status"] == "MATCH", f"got {final['status']}")
    check("series unchanged", [p.value for p in final["extracted_series"].series] == [10.0, 20.0])


def test_correct_then_match():
    print("correct-then-match (one correction, then confirmed)")
    client = FakeClient(
        [
            extraction_response(("A", 10.0)),
            verdict_response(match=False, corrections=[{"label": "A", "value": 12.0}]),
            verdict_response(match=True),
        ]
    )
    final = run_graph(client, IMAGE)
    check("3 Claude calls", client.messages.calls == 3, f"got {client.messages.calls}")
    check("status CORRECTED", final["status"] == "CORRECTED", f"got {final['status']}")
    check("value corrected to 12.0", final["extracted_series"].series[0].value == 12.0)
    check("corrections_applied == 1", final["corrections_applied"] == 1)


def test_exhaust_all_rounds():
    print("exhaust all MAX_VERIFICATION_ROUNDS (3) without a match")
    mismatches = [verdict_response(match=False, corrections=[{"label": "A", "value": v}]) for v in (11.0, 12.0, 13.0)]
    client = FakeClient([extraction_response(("A", 10.0)), *mismatches])
    final = run_graph(client, IMAGE)
    check("4 Claude calls", client.messages.calls == 4, f"got {client.messages.calls}")
    check("status EXHAUSTED", final["status"] == "EXHAUSTED", f"got {final['status']}")
    check("final value kept the last correction (13.0)", final["extracted_series"].series[0].value == 13.0)


def test_empty_series_skips_verification():
    print("unreadable chart (empty series) — early exit, no verification")
    client = FakeClient([empty_extraction_response()])
    final = run_graph(client, IMAGE)
    check("exactly 1 Claude call", client.messages.calls == 1, f"got {client.messages.calls}")
    check("status EMPTY", final["status"] == "EMPTY", f"got {final['status']}")
    check("verification_result is None", final["verification_result"] is None)


def test_case_insensitive_correction_merge():
    print("correction label 'a' merges into series label 'A' (case-insensitive)")
    client = FakeClient(
        [
            extraction_response(("A", 10.0)),
            verdict_response(match=False, corrections=[{"label": "a", "value": 14.0}]),
            verdict_response(match=True),
        ]
    )
    final = run_graph(client, IMAGE)
    check("status CORRECTED", final["status"] == "CORRECTED", f"got {final['status']}")
    check("value corrected to 14.0", final["extracted_series"].series[0].value == 14.0)


if __name__ == "__main__":
    print("=== test_loop ===")
    test_match_on_first_round()
    test_correct_then_match()
    test_exhaust_all_rounds()
    test_empty_series_skips_verification()
    test_case_insensitive_correction_merge()
    print(f"--- {0 if not FAILED else FAILED} failure(s) ---")
    sys.exit(1 if FAILED else 0)
