"""app/graph.py — the extraction + verification pipeline expressed as a
LangGraph StateGraph instead of hand-rolled Python loops.

Graph shape:

    extract ──(empty)──────────> finalize
        │
        └──(series)──> render ──> verify_pass  (nested subgraph)
                                    │
        route_after_verify_pass:    │
            match ─────────────────┤
            retry ──────────────> correct ──(rounds left)──> render
                                   └─(rounds exhausted)─────> finalize
            exhausted (verdict None) ─────────────────────> finalize

Where each node owns one responsibility:

  extract       first-pass Claude Vision extraction -> extracted_series
  render        re-render extracted_series as a chart image
  verify_pass   nested StateGraph: the verification agent loop
                (ask Claude -> it may call zoom_tool / re_extract_points ->
                feed the results back -> repeat until verdict or the tool
                budget runs out). Tool history lives in state["messages"].
  correct       apply the verdict's corrections to extracted_series
                (round_no is advanced here, as a state field)
  finalize      pick MATCH / CORRECTED / EXHAUSTED / EMPTY, merge the final
                table back into the extraction response

FORCE_VERIFY_ROUNDS lives in state as forced_rounds and is consulted by the
routing between verify and correct, so the debug override is just another
declarative branch rather than a Python if-statement. The verification loop's
continuation decision is likewise driven by state (verdict present, round_no,
tool_calls_used), never by an in-code for/while counter.
"""

import functools
import logging
from typing import Literal, Optional, TypedDict

from anthropic import Anthropic
from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph

from . import llm_agent
from .chart_render import render_chart_image
from .config import (
    FORCE_VERIFY_ROUNDS,
    MAX_TOOL_ROUNDS,
    MAX_VERIFICATION_ROUNDS,
)
from .llm_agent import (
    VERIFICATION_SYSTEM_PROMPT,
    VERIFICATION_TOOLS,
    StepLogger,
    get_client,
)
from .models import (
    AnalyzeChartRequest,
    AnalyzeChartResponse,
    ChartTask,
    SeriesCorrection,
    StructuredData,
    VerificationResult,
)

logger = logging.getLogger("diagram_reader")


class AgentState(TypedDict, total=False):
    """One state object drives the whole pipeline. Every channel below is
    updated via the node that produces it; routing reads them to decide
    whether (and where) the graph continues, so nothing about the loop is
    hard-coded into a for/while counter.

    status values: MATCH (verified clean on the first pass), CORRECTED
    (corrections were applied and then confirmed), EXHAUSTED (ran out of
    rounds or tool budget without a confirmed match), EMPTY (the chart was
    unreadable — no series to verify)."""

    # --- inputs ---
    image_data: dict  # {"media_type": str, "base64": str}
    task: Optional[ChartTask]
    forced_rounds: int  # FORCE_VERIFY_ROUNDS debug override (0 = off)

    # --- per-request plumbing (never mutated by nodes) ---
    steps: StepLogger
    client: Anthropic

    # --- progress ---
    round_no: int  # current verification round (starts at 1 after extract)
    tool_calls_used: int  # tools dispatched so far within this round
    corrections_applied: int  # cumulative count across all rounds
    messages: list  # verification agent conversation (Anthropic message dicts)
    pending_tool: object  # one tool_use block awaiting dispatch (verify -> tool_dispatch)

    # --- data ---
    result: AnalyzeChartResponse  # first-pass extraction response (unchanged)
    extracted_series: StructuredData  # current table, mutated by corrections
    rendered_image: Optional[str]  # base64 of the re-rendered chart, or None
    verification_result: Optional[VerificationResult]  # latest verdict (or None if exhausted)
    status: Optional[Literal["MATCH", "CORRECTED", "EXHAUSTED", "EMPTY"]]
    final_response: AnalyzeChartResponse  # set by finalize, returned to the caller


def log_transition(node_name: str):
    """before_node / after_node hooks for a single graph node, implemented as
    a decorator (this langgraph version has no built-in node middleware).
    Every node transition is logged in the same 'Step N: <action> : <status>'
    format as the rest of the pipeline, so the graph's control flow is fully
    traceable from the logs. Nodes inside the verification subgraph use the
    'parent:child' form (verify_pass:verify) to mirror the graph structure."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(state):
            steps = state.get("steps")
            if steps is not None:
                steps.log(f"Node {node_name}", "START")
            try:
                result = fn(state)
            except Exception:
                if steps is not None:
                    steps.log(f"Node {node_name}", "FAILED")
                raise
            if steps is not None:
                steps.log(f"Node {node_name}", "DONE")
            return result

        return wrapper

    return decorator


# ---- Nodes ------------------------------------------------------------------


@log_transition("extract")
def extract_node(state: AgentState) -> dict:
    """First pass of the pipeline: a single Claude Vision extraction call.
    The full response is kept in state['result'] (description, computedAnswer)
    while state['extracted_series'] becomes the working table the verification
    loop corrects in place. An empty series short-circuits straight to
    finalize with status EMPTY."""
    steps = state["steps"]
    image_data = state["image_data"]
    result = llm_agent._run_extraction(
        steps, state["client"], image_data["media_type"], image_data["base64"], state.get("task")
    )
    series = result.structuredData.series

    update = {
        "result": result,
        "extracted_series": result.structuredData,
        "round_no": 1,
        "tool_calls_used": 0,
        "corrections_applied": 0,
        "messages": [],
        "pending_tool": None,
        "verification_result": None,
        "rendered_image": None,
    }
    if not series:
        steps.log(
            "Validating extracted series",
            "EMPTY — chart unreadable, skipping verification",
        )
        update["status"] = "EMPTY"
    else:
        steps.log(
            "Validating extracted series",
            f"OK ({len(series)} point(s), starting verification)",
        )
    return update


def route_after_extract(state: AgentState) -> str:
    """Early-exit edge: an empty series skips verification entirely."""
    if state.get("status") == "EMPTY":
        return "empty"
    return "render"


@log_transition("render")
def render_node(state: AgentState) -> dict:
    """Re-render the current extracted table as a chart so the verification
    pass can compare it against the original image. Also resets the
    round-local channels (conversation, tool budget, pending tool, verdict) —
    each round gets a fresh conversation that ends with a user message, and a
    fresh tool budget, exactly like the original per-round verification call.
    A render failure skips straight to finalize (status EXHAUSTED — nothing to
    verify against)."""
    steps = state["steps"]
    round_no = state["round_no"]
    try:
        rendered_url = render_chart_image(state["extracted_series"])
        _, rendered_b64 = llm_agent._parse_image_data_url(rendered_url)
    except Exception as exc:
        steps.log(f"Rendering extracted chart (round {round_no})", f"SKIPPED ({exc})")
        return {"rendered_image": None}
    steps.log(f"Rendering extracted chart (round {round_no})", "OK")
    return {
        "rendered_image": rendered_b64,
        "messages": [],
        "tool_calls_used": 0,
        "pending_tool": None,
        "verification_result": None,
    }


def route_after_render(state: AgentState) -> str:
    if state.get("rendered_image") is None:
        return "finalize"
    return "verify_pass"


@log_transition("verify_pass:verify")
def verify_node(state: AgentState) -> dict:
    """The verification agent loop's ask-Claude step. Builds (once) the
    [original, re-render, table] prompt, calls the model with the running
    conversation, appends the assistant response to state['messages'], then:
      - if Claude requested a tool -> stash it as pending_tool and let the
        subgraph route it to tool_dispatch;
      - otherwise -> parse the JSON verdict into state['verification_result']."""
    steps = state["steps"]
    image_data = state["image_data"]
    round_no = state["round_no"]
    action = f"Verification pass (round {round_no})"
    messages = list(state.get("messages") or [])

    if not messages:
        table = "\n".join(f"{p.label}: {p.value}" for p in state["extracted_series"].series)
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
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": state["rendered_image"],
                        },
                    },
                    {"type": "text", "text": f"Current extracted table:\n{table}"},
                ],
            }
        ]

    try:
        response = state["client"].messages.create(
            model=llm_agent.CLAUDE_MODEL,
            max_tokens=2000,
            thinking={"type": "adaptive"},
            system=VERIFICATION_SYSTEM_PROMPT,
            tools=VERIFICATION_TOOLS,
            messages=messages,
            output_config={"effort": "low"},
        )
    except Exception as exc:
        steps.log(action, f"FAILED (Claude API call: {exc})")
        logger.exception("Verification Claude call failed")
        raise HTTPException(status_code=502, detail=f"Verification call failed: {exc}")

    if response.stop_reason == "max_tokens":
        steps.log(action, "FAILED (response cut off at max_tokens)")
        logger.error("Verification response hit max_tokens: %s", response)
        raise HTTPException(
            status_code=502,
            detail="Verification response was cut off (hit max_tokens) before finishing.",
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
        raise HTTPException(
            status_code=502,
            detail="Verification produced no final JSON text.",
        )
    try:
        parsed = llm_agent._extract_json(raw_text)
    except Exception as exc:
        steps.log(action, "FAILED (response not valid JSON)")
        raise

    try:
        verdict = VerificationResult(**parsed)
    except Exception as exc:
        steps.log(action, f"FAILED (schema mismatch: {exc})")
        logger.error("Verification JSON didn't match expected schema: %s\nParsed: %s", exc, parsed)
        raise HTTPException(
            status_code=502,
            detail=f"Verification response didn't match the expected schema: {exc}",
        )

    if not verdict.corrections:
        steps.log(action, "MATCH — charts align")
    else:
        steps.log(action, f"MISMATCH — {len(verdict.corrections)} correction(s) reported")
    return {"messages": messages, "verification_result": verdict}


def route_verify_loop(state: AgentState) -> str:
    """Subgraph edge from verify_node: a pending tool call goes to
    tool_dispatch; a verdict exits the subgraph back to the parent graph."""
    if state.get("verification_result") is not None:
        return "done"
    return "tool"


@log_transition("verify_pass:tool_dispatch")
def tool_dispatch_node(state: AgentState) -> dict:
    """Executes one requested tool (zoom_tool / re_extract_points), feeds the
    result back into the running conversation as a tool_result message, and
    bumps the tool budget counter. The subgraph then either calls verify again
    or exits when the budget is exhausted."""
    steps = state["steps"]
    image_data = state["image_data"]
    call = state["pending_tool"]
    messages = list(state["messages"])
    action = f"Verification pass (round {state['round_no']})"

    steps.log(action, f"CALLED tool {call.name}({call.input})")
    try:
        content_blocks, summary = llm_agent._execute_verification_tool(
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
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": content_blocks,
                }
            ],
        }
    ]
    return {
        "messages": messages,
        "tool_calls_used": state["tool_calls_used"] + 1,
        "pending_tool": None,
    }


def route_after_tool(state: AgentState) -> str:
    """Subgraph edge from tool_dispatch: keep verifying while the tool budget
    lasts; exit the subgraph (verdict still None) once it's exhausted."""
    if state["tool_calls_used"] >= MAX_TOOL_ROUNDS:
        steps = state.get("steps")
        if steps is not None:
            steps.log(
                f"Verification pass (round {state.get('round_no', 1)})",
                f"EXHAUSTED tool budget ({MAX_TOOL_ROUNDS} calls) without a verdict",
            )
        return "done"
    return "verify"


@log_transition("correct")
def correct_node(state: AgentState) -> dict:
    """Apply the latest verdict's corrections to extracted_series and advance
    round_no. This is where FORCE_VERIFY_ROUNDS synthesizes a mismatch when
    state['forced_rounds'] says to — so the debug override is a state-driven
    branch, exactly like every other routing decision in the graph."""
    steps = state["steps"]
    round_no = state["round_no"]
    verdict = state["verification_result"]

    if state.get("forced_rounds", 0) > 0 and round_no < state.get("forced_rounds", 0):
        forced_label = state["extracted_series"].series[0].label
        forced_value = round(state["extracted_series"].series[0].value * 1.1, 2)
        steps.log(
            f"Verification pass (round {round_no})",
            f"FORCED mismatch (debug FORCE_VERIFY_ROUNDS={state['forced_rounds']})",
        )
        verdict = VerificationResult(
            match=False,
            corrections=[SeriesCorrection(label=forced_label, value=forced_value)],
            notes="forced by FORCE_VERIFY_ROUNDS debug flag",
        )

    corrections = verdict.corrections if verdict else []
    if corrections:
        structured = llm_agent._apply_corrections(steps, state["extracted_series"], corrections, round_no)
    else:
        structured = state["extracted_series"]

    return {
        "extracted_series": structured,
        "verification_result": verdict,
        "round_no": round_no + 1,
        "corrections_applied": state.get("corrections_applied", 0) + len(corrections),
    }


def route_after_verify_pass(state: AgentState) -> str:
    """Outer-loop continuation decision, driven entirely by state:
      - verdict is None (tool budget exhausted mid-verification) -> exhausted;
      - FORCE_VERIFY_ROUNDS still demands a forced round -> retry;
      - the verdict reports corrections -> retry (another round);
      - otherwise the charts match -> finalize as MATCH/CORRECTED."""
    verdict = state.get("verification_result")
    if verdict is None:
        return "exhausted"
    forced = state.get("forced_rounds", 0)
    if forced > 0 and state["round_no"] < forced:
        return "retry"
    if verdict.corrections:
        return "retry"
    return "match"


def route_after_correct(state: AgentState) -> str:
    """After a correction round, keep looping while rounds remain."""
    if state["round_no"] > MAX_VERIFICATION_ROUNDS:
        return "exhausted"
    return "render"


@log_transition("finalize")
def finalize_node(state: AgentState) -> dict:
    """Terminal node: derive the final status from state alone and merge the
    corrected table back into the first-pass extraction response (keeping
    description/computedAnswer from the original pass, exactly as the old
    loop did)."""
    steps = state["steps"]
    result = state["result"]
    structured = state["extracted_series"]

    if state.get("status") == "EMPTY":
        status = "EMPTY"
    elif state.get("rendered_image") is None:
        status = "EXHAUSTED"
        reason = "chart re-render failed, nothing to verify against"
    elif state.get("verification_result") is None:
        status = "EXHAUSTED"
        reason = f"verification used its full tool budget ({MAX_TOOL_ROUNDS} calls) without a verdict"
    elif state["verification_result"].corrections:
        status = "EXHAUSTED"
        reason = f"{MAX_VERIFICATION_ROUNDS} round(s) ran without a confirmed match"
    elif state.get("corrections_applied", 0) > 0:
        status = "CORRECTED"
    else:
        status = "MATCH"

    if status == "EXHAUSTED":
        steps.log("Verification loop", f"EXHAUSTED — {reason}")
    steps.log("Returning final result", f"OK ({len(structured.series)} data point(s))")
    return {
        "status": status,
        "final_response": result.model_copy(update={"structuredData": structured}),
    }


# ---- Graph construction ------------------------------------------------------


def _build_verify_subgraph():
    """The verification pass is itself a StateGraph: verify_node may request a
    tool, tool_dispatch runs it and feeds the result back, and conditional
    edges loop verify -> tool_dispatch -> verify until Claude delivers a
    verdict or the tool budget is exhausted (at which point the subgraph
    exits with verification_result still None)."""
    builder = StateGraph(AgentState)
    builder.add_node("verify", verify_node)
    builder.add_node("tool_dispatch", tool_dispatch_node)
    builder.add_edge(START, "verify")
    builder.add_conditional_edges(
        "verify",
        route_verify_loop,
        {"tool": "tool_dispatch", "done": END},
    )
    builder.add_conditional_edges(
        "tool_dispatch",
        route_after_tool,
        {"verify": "verify", "done": END},
    )
    return builder.compile()


def _build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("extract", extract_node)
    builder.add_node("render", render_node)
    builder.add_node("verify_pass", _build_verify_subgraph())
    builder.add_node("correct", correct_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "extract")
    builder.add_conditional_edges(
        "extract",
        route_after_extract,
        {"empty": "finalize", "render": "render"},
    )
    builder.add_conditional_edges(
        "render",
        route_after_render,
        {"finalize": "finalize", "verify_pass": "verify_pass"},
    )
    builder.add_conditional_edges(
        "verify_pass",
        route_after_verify_pass,
        {"match": "finalize", "retry": "correct", "exhausted": "finalize"},
    )
    builder.add_conditional_edges(
        "correct",
        route_after_correct,
        {"render": "render", "exhausted": "finalize"},
    )
    builder.add_edge("finalize", END)
    return builder.compile()


graph = _build_graph()


# ---- Entry point --------------------------------------------------------------


def analyze_chart(payload: AnalyzeChartRequest) -> AnalyzeChartResponse:
    """Runs the LangGraph pipeline for a chart request and returns the final
    extraction response. The graph is invoked with the raw input in state;
    the caller unpacks state['final_response'] into the response model.

    The verification loop's continuation (and the FORCE_VERIFY_ROUNDS debug
    override) is entirely state-driven inside the graph — nothing here tells
    it when to stop."""
    steps = StepLogger()

    try:
        media_type, base64_data = llm_agent._parse_image_data_url(payload.image)
    except Exception as exc:
        steps.log("Parsing image data URL", f"FAILED ({exc})")
        raise
    steps.log("Parsing image data URL", "OK")

    try:
        client = get_client()
    except Exception as exc:
        steps.log("Initializing Claude client", f"FAILED ({exc})")
        raise
    steps.log("Initializing Claude client", "OK")

    initial_state: AgentState = {
        "image_data": {"media_type": media_type, "base64": base64_data},
        "task": payload.task,
        "forced_rounds": FORCE_VERIFY_ROUNDS,
        "round_no": 0,
        "tool_calls_used": 0,
        "corrections_applied": 0,
        "messages": [],
        "steps": steps,
        "client": client,
    }

    try:
        final_state = graph.invoke(initial_state)
    except Exception as exc:
        steps.log("LangGraph pipeline", f"FAILED ({exc})")
        logger.exception("LangGraph pipeline failed")
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Extraction pipeline failed: {exc}")

    return final_state["final_response"]
