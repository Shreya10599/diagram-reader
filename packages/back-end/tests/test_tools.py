"""test_tools.py — port of the original verification-tool tests to the
LangGraph pipeline.

The verification agent loop now lives in the nested verify_pass subgraph
(verify node <-> tool_dispatch node), so these scenarios assert on the whole
graph's final state rather than on _run_verification directly: API-call count
(= node executions), final status, and that tool results fed back through the
conversation actually influenced the verdict.

Run from packages/back-end:
    .venv/bin/python tests/test_tools.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import MAX_TOOL_ROUNDS

from helpers import (
    FakeClient,
    extraction_response,
    make_image_png,
    reextract_response,
    run_graph,
    tool_use_response,
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


def test_verdict_without_tools():
    print("clean verification — direct verdict, no tools called")
    client = FakeClient(
        [
            extraction_response(("A", 10.0)),
            verdict_response(match=True),
        ]
    )
    final = run_graph(client, IMAGE)
    check("2 Claude calls", client.messages.calls == 2, f"got {client.messages.calls}")
    check("tool_calls_used == 0", final["tool_calls_used"] == 0, f"got {final['tool_calls_used']}")
    check("status MATCH", final["status"] == "MATCH", f"got {final['status']}")


def test_zoom_tool_then_match():
    print("zoom_tool — verify asks for a closer look, then matches")
    client = FakeClient(
        [
            extraction_response(("A", 10.0)),
            tool_use_response("zoom_tool", {"region": "center"}),
            verdict_response(match=True),
        ]
    )
    final = run_graph(client, IMAGE)
    check("3 Claude calls (extract + zoom ask + final verdict)",
          client.messages.calls == 3, f"got {client.messages.calls}")
    check("tool_calls_used == 1", final["tool_calls_used"] == 1, f"got {final['tool_calls_used']}")
    check("status MATCH", final["status"] == "MATCH", f"got {final['status']}")
    check("verification_result has no corrections", not final["verification_result"].corrections)


def test_reextract_then_verify_then_match():
    print("re_extract_points — re-read, verify mismatch, then confirm on next round")
    client = FakeClient(
        [
            extraction_response(("A", 10.0)),
            tool_use_response("re_extract_points", {"labels": ["A"]}),
            reextract_response([{"label": "A", "value": 15.0}]),
            verdict_response(match=False, corrections=[{"label": "A", "value": 15.0}]),
            verdict_response(match=True),
        ]
    )
    final = run_graph(client, IMAGE)
    check("5 Claude calls (extract + re-extract + 3 verifications)",
          client.messages.calls == 5, f"got {client.messages.calls}")
    check("status CORRECTED", final["status"] == "CORRECTED", f"got {final['status']}")
    check("final value 15.0", final["extracted_series"].series[0].value == 15.0)


def test_tool_budget_exhaustion():
    print(f"verification keeps calling tools until the budget ({MAX_TOOL_ROUNDS}) runs out")
    client = FakeClient(
        [
            extraction_response(("A", 10.0)),
            *[tool_use_response("zoom_tool", {"region": "center"}) for _ in range(MAX_TOOL_ROUNDS)],
        ]
    )
    final = run_graph(client, IMAGE)
    check("1 + MAX_TOOL_ROUNDS Claude calls",
          client.messages.calls == 1 + MAX_TOOL_ROUNDS, f"got {client.messages.calls}")
    check("tool_calls_used == MAX_TOOL_ROUNDS",
          final["tool_calls_used"] == MAX_TOOL_ROUNDS, f"got {final['tool_calls_used']}")
    check("status EXHAUSTED (no verdict within budget)",
          final["status"] == "EXHAUSTED", f"got {final['status']}")
    check("verification_result is None", final["verification_result"] is None)


if __name__ == "__main__":
    print("=== test_tools ===")
    test_verdict_without_tools()
    test_zoom_tool_then_match()
    test_reextract_then_verify_then_match()
    test_tool_budget_exhaustion()
    print(f"--- {0 if not FAILED else FAILED} failure(s) ---")
    sys.exit(1 if FAILED else 0)
