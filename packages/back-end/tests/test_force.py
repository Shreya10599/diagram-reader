"""test_force.py — port of the original FORCE_VERIFY_ROUNDS debug-flag tests
to the LangGraph pipeline.

FORCE_VERIFY_ROUNDS is now a state field (forced_rounds) consulted by the
routing between verify_pass and correct, so these scenarios prove the override
still drives the multi-round loop deterministically — the graph advances
round_no as state, not as a Python for-loop variable.

Run from packages/back-end:
    .venv/bin/python tests/test_force.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers import (
    FakeClient,
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


def test_force_three_rounds():
    print("FORCE_VERIFY_ROUNDS=3 — rounds 1-2 forced to mismatch, round 3 real")
    client = FakeClient(
        [
            extraction_response(("A", 10.0), ("B", 20.0)),
            verdict_response(match=True),
            verdict_response(match=True),
            verdict_response(match=True),
        ]
    )
    final = run_graph(client, IMAGE, forced_rounds=3)
    check("4 Claude calls (extract + 3 verifications)",
          client.messages.calls == 4, f"got {client.messages.calls}")
    check("round_no ended at the last round (3)", final["round_no"] == 3,
          f"got {final['round_no']}")
    check("A forced 10 -> 11 -> 12.1", final["extracted_series"].series[0].value == 12.1,
          f"got {final['extracted_series'].series[0].value}")
    check("B untouched", final["extracted_series"].series[1].value == 20.0)
    check("corrections_applied == 2", final["corrections_applied"] == 2,
          f"got {final['corrections_applied']}")
    check("status CORRECTED (last round matched)", final["status"] == "CORRECTED",
          f"got {final['status']}")


def test_force_off():
    print("FORCE_VERIFY_ROUNDS=0 (off) — normal first-round match")
    client = FakeClient(
        [
            extraction_response(("A", 10.0)),
            verdict_response(match=True),
        ]
    )
    final = run_graph(client, IMAGE, forced_rounds=0)
    check("2 Claude calls", client.messages.calls == 2, f"got {client.messages.calls}")
    check("status MATCH", final["status"] == "MATCH", f"got {final['status']}")
    check("A unchanged", final["extracted_series"].series[0].value == 10.0)


if __name__ == "__main__":
    print("=== test_force ===")
    test_force_three_rounds()
    test_force_off()
    print(f"--- {0 if not FAILED else FAILED} failure(s) ---")
    sys.exit(1 if FAILED else 0)
