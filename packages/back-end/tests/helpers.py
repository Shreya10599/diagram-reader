"""Shared helpers for the graph tests: a fake Anthropic client that returns
scripted responses, block builders, and a tiny in-memory PNG so tools like
zoom_tool have a real image to crop.

The fake also counts create() calls — since every graph node that talks to
Claude does so through exactly one messages.create() call, the call count is
the node-execution count we assert on.
"""

import base64
import io
import json
from types import SimpleNamespace

from PIL import Image

from app.llm_agent import StepLogger


def text_block(payload: dict | str) -> SimpleNamespace:
    return SimpleNamespace(
        type="text",
        text=payload if isinstance(payload, str) else json.dumps(payload),
    )


def tool_use_block(name: str, tool_input: dict, ident: str = "toolu_01") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, id=ident, input=tool_input)


def response(blocks: list) -> SimpleNamespace:
    return SimpleNamespace(stop_reason="end_turn", content=blocks)


def tool_use_response(name: str, tool_input: dict, ident: str = "toolu_01") -> SimpleNamespace:
    """A full fake response whose only content block is a tool_use request."""
    return response([tool_use_block(name, tool_input, ident)])


class FakeMessages:
    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if not self.responses:
            raise AssertionError("FakeClient out of scripted responses")
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses: list):
        self.messages = FakeMessages(responses)
        self.requests = []  # (system, kwargs) captured for optional assertions

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def make_image_png(width: int = 100, height: int = 80) -> str:
    img = Image.new("RGB", (width, height), (255, 255, 255))
    for x in range(width):
        for y in range(height):
            if (x + y) % 3 == 0:
                img.putpixel((x, y), (70, 111, 209))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def run_graph(client: FakeClient, image_b64: str, forced_rounds: int = 0) -> dict:
    """Invoke the compiled graph directly with a fake client in state (no HTTP
    API key needed) and return the final AgentState."""
    from app.graph import graph

    initial = {
        "image_data": {"media_type": "image/png", "base64": image_b64},
        "task": None,
        "forced_rounds": forced_rounds,
        "round_no": 0,
        "tool_calls_used": 0,
        "corrections_applied": 0,
        "messages": [],
        "steps": StepLogger(),
        "client": client,
    }
    return graph.invoke(initial)


def extraction_response(*points: tuple[str, float]) -> SimpleNamespace:
    """A successful first-pass extraction of the given (label, value) points."""
    return response(
        [
            text_block(
                {
                    "description": "test chart",
                    "shortDescription": "test",
                    "structuredData": {
                        "chartType": "bar",
                        "title": "test",
                        "series": [{"label": l, "value": v} for l, v in points],
                    },
                    "computedAnswer": None,
                }
            )
        ]
    )


def empty_extraction_response() -> SimpleNamespace:
    return response(
        [
            text_block(
                {
                    "description": "cannot read the chart",
                    "shortDescription": "unreadable",
                    "structuredData": {
                        "chartType": "other",
                        "title": "unreadable",
                        "series": [],
                    },
                    "computedAnswer": None,
                }
            )
        ]
    )


def verdict_response(
    match: bool, corrections: list[dict] | None = None, notes: str = ""
) -> SimpleNamespace:
    return response(
        [
            text_block(
                {
                    "match": match,
                    "corrections": corrections or [],
                    "notes": notes,
                }
            )
        ]
    )


def reextract_response(points: list[dict]) -> SimpleNamespace:
    return response([text_block({"points": points, "notes": ""})])
