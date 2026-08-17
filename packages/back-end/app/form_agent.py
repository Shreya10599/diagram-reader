"""app/form_agent.py — prompt, tool, and tool-executor for validate_mapping,
the one agentic step in the form-filling graph.

Mirrors llm_agent.py's verification pass on purpose: same shape (a bounded
tool-calling loop that ends in a JSON verdict), same underlying re-read
mechanism (re_extract_points), same reasoning for why it's agentic at all —
this is the one place in form_graph.py where the "right" answer isn't
knowable in advance (is this mapped value actually trustworthy?), so it's
the one place a model's judgment, not fixed code, decides what happens next.

Everything else in the form-filling pipeline (which schema to use, which
field a computedAnswer maps to) is deterministic on purpose — see
form_schemas.py.
"""

import json
import logging
from typing import Optional

from anthropic import Anthropic
from fastapi import HTTPException

from .config import CLAUDE_MODEL
from .llm_agent import _extract_json, _re_extract_points
from .models import FieldValidationVerdict

logger = logging.getLogger("diagram_reader")


VALIDATE_MAPPING_SYSTEM_PROMPT = """You are the validation step of a chart-to-form-field \
mapping pipeline, for a tool that helps people fill out real forms (like a LIHEAP energy \
assistance application, or an inherited-stock tax basis worksheet) using data read from a \
chart. The mapping from chart data to a computed figure has already happened — your job is \
to decide whether that computed figure is trustworthy enough to hand to the user as a filled \
worksheet value, or whether it needs to be flagged for the user to double-check themselves.

This is a real form a person may submit to a government agency or the IRS — do not accept a \
mapping you are not actually confident in. Flagging something for human review is always the \
safe default; silently shipping a wrong number is not.

You are given:
- The target form field: its label and expected unit.
- The computed answer that was mapped to it: its label, value, unit, the formula that \
produced it, which chart series labels fed into it (sourcePoints), and its own confidence \
from the extraction pass.
- The full extracted series table, for context.

Checks to run, in order:
1. Unit compatibility: does the computed answer's unit actually match what this field \
expects? A mismatch (e.g. the field wants therms but the computed value is in kWh) is a real \
problem — flag it. Do not silently convert units unless you are certain of the conversion.
2. Confidence: if the computed answer's own confidence was "low", lean toward flagging \
unless a recheck resolves your doubt.
3. Plausibility: does the value make sense given its sourcePoints and formula? Recompute the \
formula yourself from the source values shown and check it matches.

If you want to double-check a specific value before deciding, you may call \
recheck_source_value(labels) to re-read those points directly from the original chart image \
— this is optional, use it only when a recheck would actually change your answer.

Respond with ONLY a single JSON object (no markdown fences, no commentary outside the JSON), \
matching exactly this shape:

{
  "status": "accepted" | "flagged",
  "value": <number>,
  "reason": "<short explanation — why accepted, or specifically what's wrong if flagged>"
}

"value" should be the computed answer's original value if you're accepting it as-is, or a \
corrected value if a recheck changed it. Always include a reason, even when accepting — a \
one-line justification, not a restatement of the checks above."""


VALIDATE_MAPPING_TOOLS = [
    {
        "name": "recheck_source_value",
        "description": (
            "Re-read specific series labels directly from the original chart image before "
            "deciding whether a mapped value is trustworthy. Use when a source point's value "
            "seems off or you want to confirm it before accepting or flagging the field."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Series labels to re-read from the original chart.",
                }
            },
            "required": ["labels"],
        },
    }
]


def _build_validate_mapping_messages(
    field_label: str,
    field_unit: str,
    computed_answer: dict,
    extracted_series_text: str,
) -> str:
    return (
        f"Target form field: {field_label!r} (expected unit: {field_unit or 'none'})\n\n"
        f"Computed answer mapped to it:\n"
        f"  label: {computed_answer.get('label')}\n"
        f"  value: {computed_answer.get('value')}\n"
        f"  unit: {computed_answer.get('unit') or 'none'}\n"
        f"  formula: {computed_answer.get('formula')}\n"
        f"  sourcePoints: {computed_answer.get('sourcePoints')}\n"
        f"  confidence: {computed_answer.get('confidence')}\n\n"
        f"Full extracted series (for context):\n{extracted_series_text}\n\n"
        f"Decide whether to accept or flag this field, per your instructions."
    )


def execute_validate_mapping_tool(
    client: Anthropic,
    media_type: str,
    original_b64: str,
    call,
) -> tuple[list[dict], str]:
    """Runs the one tool validate_mapping can call. Same shape as
    llm_agent._execute_verification_tool — returns (tool_result_content,
    human_summary) so the caller can feed the result back and log it."""
    if call.name == "recheck_source_value":
        labels = [str(label) for label in call.input.get("labels", [])]
        points = _re_extract_points(client, media_type, original_b64, labels)
        summary = f"recheck_source_value({labels}) -> {len(points)} re-read point(s)"
        return (
            [{"type": "text", "text": json.dumps({"points": points})}],
            summary,
        )

    raise HTTPException(
        status_code=502, detail=f"Unknown tool requested by validate_mapping: {call.name}"
    )
