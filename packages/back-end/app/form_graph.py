"""app/form_graph.py — the form-filling pipeline, as its own LangGraph.

Deliberately a separate graph from app/graph.py's extraction/verification
pipeline, not a continuation of it — a caller may want a chart described
with no form involved at all, so this only runs when a formType is given.
Reuses graph.py's log_transition decorator and StepLogger so both
pipelines produce the same traceable step log.

Graph shape:

    select_form_schema -> map_to_schema -> validate_pass (nested subgraph,
    only entered if there's at least one computedAnswer-sourced field) ->
    record_field_result --(fields remain)--> validate_pass
                         --(done)------------> finalize

Where each node owns one responsibility:

  select_form_schema  deterministic: look up the hardcoded schema for
                       request.formType (see form_schemas.py — the mapping
                       is hardcoded on purpose, not agent-inferred)
  map_to_schema        deterministic: "manual" fields go straight into
                       field_results as manual_required (nothing for an
                       agent to validate there); "computedAnswer" fields
                       are queued in pending_fields for validate_mapping
  validate_pass         nested StateGraph: the ONE agentic step in this
                       pipeline — Claude decides whether a mapped value is
                       trustworthy, optionally calling recheck_source_value
                       first (see form_agent.py for why this is the only
                       genuinely agentic part; everything else here is
                       fixed-path workflow)
  record_field_result  deterministic: turns the verdict into a
                       WorksheetFieldResult, advances to the next pending
                       field
  finalize             deterministic: assembles the final worksheet +
                       summary from field_results

Nothing about how many fields get validated is hard-coded into a for-loop —
route_after_record reads pending_fields, exactly like graph.py's rounds are
state-driven rather than counted in Python.
"""

import functools
import logging
from typing import Optional, TypedDict

from anthropic import Anthropic
from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph

from .config import CLAUDE_MODEL, MAX_TOOL_ROUNDS
from .form_agent import (
    VALIDATE_MAPPING_SYSTEM_PROMPT,
    VALIDATE_MAPPING_TOOLS,
    _build_validate_mapping_messages,
    execute_validate_mapping_tool,
)
from .form_schemas import FormField, get_schema
from .graph import log_transition
from .llm_agent import (
    StepLogger,
    _extract_json,
    _parse_image_data_url,
    get_client,
)
from .models import (
    ComputedAnswer,
    FieldValidationVerdict,
    FormFillRequest,
    FormFillResponse,
    WorksheetFieldResult,
)

logger = logging.getLogger("diagram_reader")


class FormAgentState(TypedDict, total=False):
    # --- inputs ---
    request: FormFillRequest
    image_data: dict  # {"media_type": str, "base64": str}

    # --- per-request plumbing ---
    steps: StepLogger
    client: Anthropic

    # --- progress ---
    schema: list[FormField]
    # request.computedAnswers indexed by field_id — built once in
    # map_to_schema_node. LIHEAP/stock_basis only ever populate one entry;
    # vera_summary populates three (min/max/average). validate_node and
    # record_field_result_node look up the CURRENT field's answer here
    # instead of assuming there's exactly one computedAnswer for the whole
    # request the way the original single-field design did.
    computed_by_field: dict[str, ComputedAnswer]
    field_results: list[WorksheetFieldResult]  # accumulates as fields finish
    pending_fields: list[FormField]  # computedAnswer-sourced fields still to validate
    current_field: Optional[FormField]
    messages: list  # validate_mapping's conversation for the CURRENT field only
    tool_calls_used: int
    pending_tool: object
    verdict: Optional[FieldValidationVerdict]

    # --- output ---
    final_response: FormFillResponse


# ---- Nodes -------------------------------------------------------------------


@log_transition("select_form_schema")
def select_form_schema_node(state: FormAgentState) -> dict:
    """Deterministic lookup for the three hardcoded formTypes — see
    form_schemas.py for why that isn't an agent decision. For
    formType == "custom", the schema was already extracted from the
    user's uploaded form by form_schema_extraction.py before this graph
    ever ran; this just reads it off the request rather than looking it
    up, still no agent call here. Fails loudly rather than guessing a
    schema either way."""
    steps = state["steps"]
    request = state["request"]
    form_type = request.formType

    if form_type == "custom":
        if not request.customSchema:
            steps.log("Selecting form schema", "FAILED (formType='custom' but customSchema is empty)")
            raise HTTPException(
                status_code=400, detail="formType 'custom' requires a non-empty customSchema."
            )
        schema = request.customSchema
        steps.log("Selecting form schema", f"OK (custom, {len(schema)} field(s))")
        return {"schema": schema}

    try:
        schema = get_schema(form_type)
    except ValueError as exc:
        steps.log("Selecting form schema", f"FAILED ({exc})")
        raise HTTPException(status_code=400, detail=str(exc))
    steps.log("Selecting form schema", f"OK ({form_type}, {len(schema)} field(s))")
    return {"schema": schema}


@log_transition("map_to_schema")
def map_to_schema_node(state: FormAgentState) -> dict:
    """Deterministic mapping: manual fields need no agent involvement at
    all (there's nothing to validate — the chart pipeline simply cannot
    answer them), so they go straight into field_results. computedAnswer
    fields are matched to the request's computedAnswers by field_id and
    queued for validate_mapping, one at a time — a computedAnswer field
    with no matching entry (caller forgot it, or field_id was left null)
    is recorded as needs_review immediately rather than crashing, same
    'flagging is the safe default' rule validate_mapping itself follows."""
    steps = state["steps"]
    schema = state["schema"]
    computed_by_field = {
        ans.field_id: ans for ans in state["request"].computedAnswers if ans.field_id
    }

    field_results: list[WorksheetFieldResult] = []
    pending_fields: list[FormField] = []

    for field in schema:
        if field.source == "manual":
            field_results.append(
                WorksheetFieldResult(
                    field_id=field.field_id,
                    label=field.label,
                    unit=field.unit,
                    status="manual_required",
                    reason="This value can't be read from a chart — fill it in yourself.",
                )
            )
        elif field.field_id in computed_by_field:
            pending_fields.append(field)
        else:
            field_results.append(
                WorksheetFieldResult(
                    field_id=field.field_id,
                    label=field.label,
                    unit=field.unit,
                    status="needs_review",
                    reason="No computed value was provided for this field.",
                )
            )

    steps.log(
        "Mapping computedAnswers to schema",
        f"OK ({len(pending_fields)} field(s) to validate, "
        f"{len(field_results)} field(s) resolved without the agent) — "
        f"received {len(computed_by_field)} computed value(s): "
        f"{list(computed_by_field)}",
    )
    return {
        "computed_by_field": computed_by_field,
        "field_results": field_results,
        "pending_fields": pending_fields,
    }


def route_after_map(state: FormAgentState) -> str:
    """Skip the agent entirely if this schema has no computedAnswer field
    to validate — no reason to invoke Claude for a form that's all manual
    fields."""
    if not state.get("pending_fields"):
        return "finalize"
    return "next_field"


@log_transition("prepare_next_field")
def prepare_next_field_node(state: FormAgentState) -> dict:
    """Pops the next pending field into current_field and resets the
    per-field channels (conversation, tool budget, verdict) — same pattern
    as graph.py's render_node resetting per-round state before each
    verification pass."""
    pending = list(state["pending_fields"])
    current = pending.pop(0)
    return {
        "current_field": current,
        "pending_fields": pending,
        "messages": [],
        "tool_calls_used": 0,
        "pending_tool": None,
        "verdict": None,
    }


@log_transition("validate_pass:validate")
def validate_node(state: FormAgentState) -> dict:
    """validate_mapping's ask-Claude step. Builds (once, per field) the
    field + computedAnswer + series-table prompt, calls the model with the
    running conversation, then either stashes a requested tool call or
    parses the final verdict — identical shape to graph.py's verify_node,
    on purpose."""
    steps = state["steps"]
    client = state["client"]
    field = state["current_field"]
    computed_answer = state["computed_by_field"][field.field_id]
    image_data = state["image_data"]
    action = f"Validating field {field.field_id!r}"
    messages = list(state.get("messages") or [])

    if not messages:
        series_text = "\n".join(
            f"{p.label}: {p.value}" for p in state["request"].extractedSeries.series
        )
        prompt_text = _build_validate_mapping_messages(
            field.label, field.unit or "", computed_answer.model_dump(), series_text
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image_data["media_type"],
                            "data": image_data["base64"],
                        },
                    },
                    {"type": "text", "text": prompt_text},
                ],
            }
        ]

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            thinking={"type": "adaptive"},
            system=VALIDATE_MAPPING_SYSTEM_PROMPT,
            tools=VALIDATE_MAPPING_TOOLS,
            messages=messages,
            output_config={"effort": "low"},
        )
    except Exception as exc:
        steps.log(action, f"FAILED (Claude API call: {exc})")
        logger.exception("validate_mapping Claude call failed")
        raise HTTPException(status_code=502, detail=f"validate_mapping call failed: {exc}")

    if response.stop_reason == "max_tokens":
        steps.log(action, "FAILED (response cut off at max_tokens)")
        raise HTTPException(
            status_code=502,
            detail="validate_mapping response was cut off (hit max_tokens) before finishing.",
        )

    messages = messages + [{"role": "assistant", "content": list(response.content)}]

    tool_calls = [
        block for block in response.content if getattr(block, "type", None) == "tool_use"
    ]
    if tool_calls:
        return {"messages": messages, "pending_tool": tool_calls[0]}

    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    if not raw_text.strip():
        steps.log(action, "FAILED (no final JSON text in response)")
        raise HTTPException(status_code=502, detail="validate_mapping produced no final JSON.")

    parsed = _extract_json(raw_text)
    try:
        verdict = FieldValidationVerdict(**parsed)
    except Exception as exc:
        steps.log(action, f"FAILED (schema mismatch: {exc})")
        raise HTTPException(
            status_code=502,
            detail=f"validate_mapping response didn't match the expected schema: {exc}",
        )

    steps.log(action, f"{verdict.status.upper()} — {verdict.reason}")
    return {"messages": messages, "verdict": verdict}


def route_validate_loop(state: FormAgentState) -> str:
    if state.get("verdict") is not None:
        return "done"
    return "tool"


@log_transition("validate_pass:tool_dispatch")
def tool_dispatch_node(state: FormAgentState) -> dict:
    """Executes recheck_source_value and feeds the result back — identical
    shape to graph.py's tool_dispatch_node."""
    steps = state["steps"]
    image_data = state["image_data"]
    call = state["pending_tool"]
    messages = list(state["messages"])
    field = state["current_field"]
    action = f"Validating field {field.field_id!r}"

    steps.log(action, f"CALLED tool {call.name}({call.input})")
    try:
        content_blocks, summary = execute_validate_mapping_tool(
            state["client"], image_data["media_type"], image_data["base64"], call
        )
    except Exception as exc:
        steps.log(action, f"TOOL {call.name} FAILED ({exc})")
        content_blocks, summary = (
            [{"type": "text", "text": f"Tool {call.name} failed: {exc}"}],
            f"tool {call.name} FAILED",
        )
    steps.log(action, f"TOOL result {summary}")

    messages = messages + [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": call.id, "content": content_blocks}
            ],
        }
    ]
    return {
        "messages": messages,
        "tool_calls_used": state["tool_calls_used"] + 1,
        "pending_tool": None,
    }


def route_after_tool(state: FormAgentState) -> str:
    if state["tool_calls_used"] >= MAX_TOOL_ROUNDS:
        steps = state.get("steps")
        field = state.get("current_field")
        if steps is not None and field is not None:
            steps.log(
                f"Validating field {field.field_id!r}",
                f"EXHAUSTED tool budget ({MAX_TOOL_ROUNDS} calls) without a verdict",
            )
        return "done"
    return "validate"


@log_transition("record_field_result")
def record_field_result_node(state: FormAgentState) -> dict:
    """Turns the current field's verdict (or its absence, if the tool
    budget ran out) into a WorksheetFieldResult and appends it. A missing
    verdict is treated the same as an explicit flag — silence is not
    treated as acceptance, matching the 'flagging is the safe default'
    rule in the validate_mapping prompt."""
    steps = state["steps"]
    field = state["current_field"]
    verdict = state.get("verdict")
    computed_answer = state["computed_by_field"][field.field_id]

    if verdict is None:
        result = WorksheetFieldResult(
            field_id=field.field_id,
            label=field.label,
            unit=field.unit,
            status="needs_review",
            reason="Validation used its full tool budget without reaching a verdict.",
            sourcePoints=computed_answer.sourcePoints,
        )
    elif verdict.status == "accepted":
        result = WorksheetFieldResult(
            field_id=field.field_id,
            label=field.label,
            value=verdict.value,
            unit=field.unit,
            status="filled",
            reason=verdict.reason,
            sourcePoints=computed_answer.sourcePoints,
        )
    else:
        result = WorksheetFieldResult(
            field_id=field.field_id,
            label=field.label,
            value=verdict.value,
            unit=field.unit,
            status="needs_review",
            reason=verdict.reason,
            sourcePoints=computed_answer.sourcePoints,
        )

    steps.log(f"Recording result for {field.field_id!r}", result.status.upper())
    return {"field_results": state["field_results"] + [result]}


def route_after_record(state: FormAgentState) -> str:
    if state.get("pending_fields"):
        return "next_field"
    return "finalize"


@log_transition("finalize")
def finalize_node(state: FormAgentState) -> dict:
    """Terminal node: assemble the worksheet response and a one-line
    summary counting filled / needs-review / manual fields."""
    steps = state["steps"]
    results = state["field_results"]
    filled = sum(1 for r in results if r.status == "filled")
    needs_review = sum(1 for r in results if r.status == "needs_review")
    manual = sum(1 for r in results if r.status == "manual_required")

    summary = (
        f"{filled} field(s) filled, {needs_review} need your review, "
        f"{manual} require manual entry."
    )
    steps.log("Returning worksheet", summary)
    return {
        "final_response": FormFillResponse(
            formType=state["request"].formType,
            fields=results,
            summary=summary,
        )
    }


# ---- Graph construction --------------------------------------------------------


def _build_validate_subgraph():
    builder = StateGraph(FormAgentState)
    builder.add_node("validate", validate_node)
    builder.add_node("tool_dispatch", tool_dispatch_node)
    builder.add_edge(START, "validate")
    builder.add_conditional_edges(
        "validate", route_validate_loop, {"tool": "tool_dispatch", "done": END}
    )
    builder.add_conditional_edges(
        "tool_dispatch", route_after_tool, {"validate": "validate", "done": END}
    )
    return builder.compile()


def _build_form_graph():
    builder = StateGraph(FormAgentState)
    builder.add_node("select_form_schema", select_form_schema_node)
    builder.add_node("map_to_schema", map_to_schema_node)
    builder.add_node("prepare_next_field", prepare_next_field_node)
    builder.add_node("validate_pass", _build_validate_subgraph())
    builder.add_node("record_field_result", record_field_result_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "select_form_schema")
    builder.add_edge("select_form_schema", "map_to_schema")
    builder.add_conditional_edges(
        "map_to_schema", route_after_map, {"next_field": "prepare_next_field", "finalize": "finalize"}
    )
    builder.add_edge("prepare_next_field", "validate_pass")
    builder.add_edge("validate_pass", "record_field_result")
    builder.add_conditional_edges(
        "record_field_result",
        route_after_record,
        {"next_field": "prepare_next_field", "finalize": "finalize"},
    )
    builder.add_edge("finalize", END)
    return builder.compile()


form_graph = _build_form_graph()


# ---- Entry point --------------------------------------------------------------


def fill_form(payload: FormFillRequest) -> FormFillResponse:
    """Runs the form-filling graph for a request and returns the worksheet.
    Separate entry point from graph.py's analyze_chart — this is meant to
    be called with the computedAnswer/extractedSeries a prior /analyze-chart
    call already produced, not to re-run extraction itself."""
    steps = StepLogger()

    try:
        media_type, base64_data = _parse_image_data_url(payload.image)
    except Exception:
        steps.log("Parsing image data URL", "FAILED")
        raise
    steps.log("Parsing image data URL", "OK")

    try:
        client = get_client()
    except Exception:
        steps.log("Initializing Claude client", "FAILED")
        raise
    steps.log("Initializing Claude client", "OK")

    initial_state: FormAgentState = {
        "request": payload,
        "image_data": {"media_type": media_type, "base64": base64_data},
        "steps": steps,
        "client": client,
        "field_results": [],
        "pending_fields": [],
    }

    try:
        final_state = form_graph.invoke(initial_state)
    except Exception as exc:
        steps.log("Form-filling pipeline", f"FAILED ({exc})")
        logger.exception("Form-filling pipeline failed")
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Form-filling pipeline failed: {exc}")

    return final_state["final_response"]
