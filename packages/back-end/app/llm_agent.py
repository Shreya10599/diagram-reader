import base64
import json
import logging
import re
from typing import Optional

from anthropic import Anthropic
from fastapi import HTTPException

from .chart_render import render_chart_image
from .config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    FORCE_VERIFY_ROUNDS,
    MAX_TOOL_ROUNDS,
    MAX_VERIFICATION_ROUNDS,
)
from .models import (
    AnalyzeChartRequest,
    AnalyzeChartResponse,
    ChartTask,
    SeriesCorrection,
    SeriesPoint,
    StructuredData,
    VerificationResult,
)
from .tools import zoom_image

logger = logging.getLogger("diagram_reader")

_client: Optional[Anthropic] = None


class StepLogger:
    """Per-request, monotonic step counter so every backend action is logged
    in a consistent format — 'Step N: <action> : <status>' — that both a
    human and a screen reader can follow as the pipeline runs."""

    def __init__(self) -> None:
        self._step = 0

    def log(self, action: str, status: str) -> None:
        self._step += 1
        logger.info("Step %d: %s : %s", self._step, action, status)


# ---- Extraction prompt -----------------------------------------------------------
# Encodes the axis-calibration + self-check procedure discussed in planning:
# anchor to labeled gridlines before reading values, rather than estimating
# proportionally from bar/slice size by eye. This is the FIRST pass of the
# loop; the verification pass below re-checks its output against the image.


EXTRACTION_SYSTEM_PROMPT = """You are a precise chart-reading assistant. You extract exact \
numerical data from an image of a chart (bar, line, pie, or scatter) for a blind or \
low-vision user, so accuracy matters more than a rich narrative description.

Follow this procedure before answering:
1. Identify the chart type (bar, line, pie, or scatter).
2. Identify the axis reference points: for the value axis, find the numeric label at the \
zero/baseline gridline and the numeric label at the max gridline you can see. Use these two \
reference points to calibrate the scale before reading any individual value — do not \
estimate values by eye without first anchoring to labeled gridlines or tick marks.
3. Check the x-axis the same way: count how many labeled ticks there are versus how many \
actual data points (bars/markers) are drawn. If there are FEWER labeled ticks than data \
points — e.g. a tick every 4 days but a bar for every single day — do not guess an \
unlabeled point's position by eye. Instead count sequentially from the nearest labeled tick \
in both directions to assign every point's exact label: if "Aug 4" and "Aug 8" are both \
labeled with 3 unlabeled bars between them, those bars must be Aug 5, Aug 6, Aug 7 in order. \
Apply this counting to the START and END of the data too — do not assume the data stops at \
or before the last labeled tick; count the actual bars present, including any that extend \
past it.
4. For each data point/bar/slice/marker, read its value against that calibrated scale, not \
by guessing proportionally from its visual size alone.
5. Re-check every value against the image once more before finalizing: does the relative \
height/length/angle of each item match the number you assigned it? If a value looks \
inconsistent with its neighbors given the scale, re-measure it.
6. If a pie chart's slice values are given as percentages, they should sum to ~100 — check \
this and note any mismatch by lowering your confidence.
7. Set structuredData.confidence to "low" for any chart where gridlines/axis labels were \
hard to read, and list the specific series labels you're unsure about in uncertainValues.
8. Before finalizing, check that "description" is fully consistent with \
"structuredData.series" — the date range, number of points, and highest/lowest values \
mentioned in the description must exactly match the actual series entries you extracted. \
If they don't match, that's a sign one of them is wrong — recheck against the image and fix \
whichever one is actually incorrect rather than leaving a contradiction between them.
9. TASK (only if one is given in the user's message below): after extracting the full \
series as above, compute the specific derived figure the task asks for, using only the \
values you just extracted. Do this arithmetic carefully and show your work in the \
"formula" field (e.g. "Mean of 12 monthly values: (40+65+30+...)/12 = 62.4"), and list \
which series labels fed into it in "sourcePoints". If the task asks about a specific x-axis \
point (e.g. a date) that isn't exactly on a labeled gridline, use the nearest point you can \
identify, note that it's an approximation, and lower confidence accordingly. If no TASK is \
given, omit "computedAnswer" entirely (set it to null) — do not invent a computation nobody \
asked for.

Respond with ONLY a single JSON object (no markdown fences, no commentary outside the JSON), \
matching exactly this shape:

{
  "description": "<2-4 sentence natural-language description of the whole chart, suitable to \
read aloud, including the title, axes/units if applicable, the overall trend, and the \
highest/lowest notable points>",
  "shortDescription": "<1 sentence summary>",
  "structuredData": {
    "chartType": "bar" | "line" | "pie" | "scatter" | "other",
    "title": "<chart title, or a short generated title if none is visible>",
    "xLabel": "<x-axis label, or null>",
    "yLabel": "<y-axis label, or null>",
    "unit": "<unit string, e.g. 'mm', '%', '$', or empty string if none>",
    "series": [ { "label": "<category/x-value>", "value": <number> }, ... ],
    "confidence": "high" | "medium" | "low",
    "uncertainValues": ["<labels of any values you were not confident about>"]
  },
  "computedAnswer": null | {
    "label": "<short name for the computed figure, e.g. 'Average Monthly Usage'>",
    "value": <number>,
    "unit": "<unit, or empty string>",
    "formula": "<short human-readable arithmetic showing exactly how this was derived>",
    "sourcePoints": ["<series labels that fed into this computation>"],
    "confidence": "high" | "medium" | "low"
  }
}

"value" must always be a plain number (no units, no percent signs, no commas). If you \
cannot read the chart at all, set structuredData.series to an empty list and explain why in \
"description"."""


# ---- Verification prompt ----------------------------------------------------------
# The branch point of the loop: Claude sees the original image AND a chart
# re-rendered from the extracted table, and decides (a) whether they match and
# (b) which specific values to correct if they don't. Nothing about whether to
# continue is hard-coded — it follows from the corrections Claude reports.


VERIFICATION_SYSTEM_PROMPT = """You are the verification pass of a chart-data extraction \
pipeline.

You are shown TWO images:
1. The ORIGINAL chart the user photographed or uploaded.
2. A RE-RENDERED chart drawn from the values a previous pass extracted from the original.

Compare them carefully, point by point. The re-rendered chart is drawn directly from a table \
of (label, value) pairs, so if that table was accurate the two charts should look the same. \
You are also given that table as text.

Your job is to decide whether the re-rendered chart faithfully reproduces the original, and \
if it does not, to correct the specific values that are wrong.

Rules:
- Compare every data point (bar height / line position / slice angle) against the axis scale \
visible in the original. A scale mismatch (e.g. the original maxes out at 100 but the \
re-render maxes at 40) usually means the extracted scale was wrong — correct every value you \
can read in that case.
- Only report corrections for points you are confident are wrong. Do not guess, and do not \
"improve" values that actually match.
- If the two charts match, return "match": true with an empty "corrections" array.
- If some values differ, return "match": false and list the corrected value for each \
mismatched point only.
- If any part of the chart is hard to read — cut-off axis labels, ambiguous tick marks, \
crowded data points — you MAY call the provided tools before answering: zoom_tool(region) \
crops and upscales a region of the original for a closer look, and re_extract_points(labels) \
re-reads specific points from the original and returns their true values. Using a tool is \
always optional — YOU decide whether a closer look would change your answer, and how many \
times to look before finalizing.

Respond with ONLY a single JSON object (no markdown fences, no commentary outside the JSON), \
matching exactly this shape:

{
  "match": true | false,
  "corrections": [ { "label": "<series label>", "value": <number> }, ... ],
  "notes": "<short summary of what matched or what was corrected, or empty string>"
}

"value" must always be a plain number. Labels in "corrections" must exactly match the labels \
in the provided table (case-sensitive) — if you don't recognize a label, leave it out rather \
than guessing."""


VERIFICATION_TOOLS = [
    {
        "name": "zoom_tool",
        "description": (
            "Crop and upscale a region of the original chart image when axis labels or data "
            "points are ambiguous, cut off, or hard to read. Use before giving your final "
            "answer if a closer look would help."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": (
                        "Region to zoom into, e.g. 'left half', 'right half', 'top', 'bottom', "
                        "'center', or a pixel box like 'x:200-400, y:100-300'. Defaults to the "
                        "whole chart upscaled if not recognized."
                    ),
                }
            },
            "required": ["region"],
        },
    },
    {
        "name": "re_extract_points",
        "description": (
            "Re-read specific data points from the original chart image and return their true "
            "values. Use when particular series labels were read incorrectly in the first "
            "pass. Returns corrected values for the requested labels only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Series labels whose values should be re-read from the original chart."
                    ),
                }
            },
            "required": ["labels"],
        },
    },
]


REEXTRACT_SYSTEM_PROMPT = """You are a focused chart re-reader. You are shown a chart image \
and given a list of series labels whose values need re-checking.

Re-read ONLY those specific points from the image. Follow the calibration procedure: anchor \
to the labeled gridlines before reading any value, and re-measure each requested point \
against that scale. If you cannot identify a requested point at all, skip it — do not guess \
its label or value.

Respond with ONLY a single JSON object (no markdown fences, no commentary outside the JSON), \
matching exactly this shape:

{
  "points": [ { "label": "<series label>", "value": <number> }, ... ],
  "notes": "<short summary, or empty string>"
}

"value" must always be a plain number. Only include labels you were asked to re-read."""


def get_client() -> Anthropic:
    """Lazy singleton so /health works even with no API key set yet."""
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise HTTPException(
                status_code=500,
                detail="ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.",
            )
        _client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _build_user_instruction(task: Optional[ChartTask]) -> str:
    """The system prompt describes the computedAnswer mechanism once and
    stays static (keeps it cache-friendly); the actual task text is
    per-request, so it's built here instead."""
    base = "Extract this chart's data following the procedure in your instructions."
    if task and task.type != "full_extraction" and task.instruction:
        target_note = f" The specific point of interest is: {task.target}." if task.target else ""
        base += (
            f" TASK: {task.instruction}.{target_note} Compute this using the values you "
            f"extract and report it as the computedAnswer object described in your instructions."
        )
    base += " Respond with the JSON object only."
    return base


def _parse_image_data_url(data_url: str) -> tuple[str, str]:
    """Split a `data:image/png;base64,....` string into (media_type, base64_payload)."""
    match = re.match(r"^data:(image/[a-zA-Z+]+);base64,(.+)$", data_url, re.DOTALL)
    if not match:
        raise HTTPException(
            status_code=400,
            detail="image must be a data URL like 'data:image/png;base64,...'",
        )
    media_type, payload = match.group(1), match.group(2)
    try:
        base64.b64decode(payload, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64 image data: {exc}")
    return media_type, payload


def _extract_json(raw_text: str) -> dict:
    """Claude is instructed to return raw JSON; strip markdown fences defensively in \
    case it wraps the response in ```json anyway."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(json)?", "", text).rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Claude response as JSON: %s\nRaw: %s", exc, raw_text)
        raise HTTPException(
            status_code=502,
            detail="Claude returned a response that couldn't be parsed as JSON.",
        )


def _run_extraction(
    steps: StepLogger,
    client: Anthropic,
    media_type: str,
    base64_data: str,
    task: Optional[ChartTask],
) -> AnalyzeChartResponse:
    """First pass of the loop: the single Claude Vision extraction call."""
    action = "First-pass extraction (Claude Vision)"
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            # temperature is deprecated on this model generation (400s on any
            # non-default value, even with thinking off). Use adaptive
            # thinking + effort instead — controls reasoning depth, not
            # sampling randomness, which is actually the more relevant knob
            # for a task that's supposed to follow a fixed calibration
            # procedure rather than write varied prose. max_tokens raised
            # further (4000 -> 8000): effort="max" spends a lot more of the
            # token budget on thinking before writing the final JSON, and
            # 4000 wasn't leaving enough room, truncating the JSON mid-output.
            max_tokens=4000,
            thinking={"type": "adaptive"},
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": _build_user_instruction(task),
                        },
                    ],
                }
            ],
            # Bumped from "medium" to "max": real testing against an actual
            # PG&E chart showed persistent off-by-one date-counting errors
            # at the boundary (past the last labeled tick) even after the
            # prompt was updated to explicitly walk through that counting —
            # worth spending the extra reasoning depth on getting the count
            # right before concluding prompting alone can't fix it.
            output_config={
                "effort": "medium"
            }
        )
    except Exception as exc:
        steps.log(action, f"FAILED (Claude API call: {exc})")
        logger.exception("Claude API call failed")
        raise HTTPException(status_code=502, detail=f"Claude API call failed: {exc}")

    if response.stop_reason == "max_tokens":
        # Diagnose truncation explicitly instead of letting it surface as a
        # confusing "couldn't be parsed as JSON" error further down — this
        # is exactly what happened when effort="max" started spending more
        # of the budget on thinking, leaving the JSON cut off mid-output.
        steps.log(action, "FAILED (response cut off at max_tokens)")
        logger.error("Response hit max_tokens (likely truncated mid-JSON): %s", response)
        raise HTTPException(
            status_code=502,
            detail="Claude's response was cut off (hit max_tokens) before finishing — "
            "usually means max_tokens needs to be raised for the current effort level.",
        )

    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    try:
        parsed = _extract_json(raw_text)
    except HTTPException as exc:
        steps.log(action, "FAILED (response not valid JSON)")
        raise

    try:
        result = AnalyzeChartResponse(**parsed)
    except Exception as exc:
        steps.log(action, f"FAILED (schema mismatch: {exc})")
        logger.error("Claude JSON didn't match expected schema: %s\nParsed: %s", exc, parsed)
        raise HTTPException(
            status_code=502,
            detail=f"Claude's response didn't match the expected schema: {exc}",
        )

    steps.log(action, f"OK ({len(result.structuredData.series)} data point(s))")
    return result


def _re_extract_points(
    client: Anthropic,
    media_type: str,
    original_b64: str,
    labels: list[str],
) -> list[dict]:
    """Executes the re_extract_points tool: a focused Claude call that
    re-reads only the requested labels from the original image and returns
    their true values."""
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            thinking={"type": "adaptive"},
            system=REEXTRACT_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": original_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": f"Re-read ONLY these points and report their values: {labels}",
                        },
                    ],
                }
            ],
            output_config={"effort": "low"},
        )
    except Exception as exc:
        logger.exception("re_extract_points Claude call failed")
        raise HTTPException(status_code=502, detail=f"re_extract_points call failed: {exc}")

    if response.stop_reason == "max_tokens":
        logger.error("re_extract_points response hit max_tokens: %s", response)
        raise HTTPException(
            status_code=502,
            detail="re_extract_points response was cut off (hit max_tokens) before finishing.",
        )

    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    parsed = _extract_json(raw_text)

    points = parsed.get("points")
    if not isinstance(points, list):
        logger.error(
            "re_extract_points response missing 'points' list: %s\nParsed: %s", raw_text, parsed
        )
        raise HTTPException(
            status_code=502,
            detail="re_extract_points returned an unexpected shape (missing 'points').",
        )
    return points


def _execute_verification_tool(
    client: Anthropic,
    media_type: str,
    original_b64: str,
    call,
) -> tuple[list[dict], str]:
    """Runs one tool call the verification agent requested and returns
    (tool_result_content_blocks, human_summary). The summary is logged; the
    content blocks are fed back to Claude so it can act on the result."""
    if call.name == "zoom_tool":
        region = str(call.input.get("region", ""))
        crop_url = zoom_image(original_b64, region)
        _, crop_b64 = _parse_image_data_url(crop_url)
        summary = f"zoom_tool({region!r}) -> 2x PNG crop"
        return (
            [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": crop_b64},
                },
                {
                    "type": "text",
                    "text": "Above is a zoomed/upscaled view of the original chart.",
                },
            ],
            summary,
        )

    if call.name == "re_extract_points":
        labels = [str(label) for label in call.input.get("labels", [])]
        points = _re_extract_points(client, media_type, original_b64, labels)
        summary = f"re_extract_points({labels}) -> {len(points)} re-read point(s)"
        return (
            [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"points": points},
                    ),
                }
            ],
            summary,
        )

    raise HTTPException(status_code=502, detail=f"Unknown tool requested by Claude: {call.name}")


def _run_verification(
    steps: StepLogger,
    client: Anthropic,
    media_type: str,
    original_b64: str,
    rendered_b64: str,
    structured: StructuredData,
    round_no: int,
) -> VerificationResult:
    """Verification pass as an agent loop: Claude sees the original image
    alongside a chart re-rendered from the extracted table, and is given
    tools (zoom_tool, re_extract_points) so IT decides whether and how to
    investigate a mismatch before delivering the verdict. The verdict's
    corrections then drive the outer extraction loop.

    Loop shape:
      ask Claude -> if it requests a tool, run it, feed the result back, repeat
      -> stop when Claude answers with the final JSON verdict (or the tool
      budget runs out). Whether to keep investigating — and how — is Claude's
      decision, not this code's."""
    action = f"Verification pass (round {round_no})"
    table = "\n".join(f"{p.label}: {p.value}" for p in structured.series)
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": original_b64,
                    },
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": rendered_b64,
                    },
                },
                {
                    "type": "text",
                    "text": f"Current extracted table:\n{table}",
                },
            ],
        }
    ]

    response = None
    for tool_round in range(1, MAX_TOOL_ROUNDS + 1):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
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

        tool_calls = [
            block for block in response.content if getattr(block, "type", None) == "tool_use"
        ]
        if not tool_calls:
            break

        for call in tool_calls:
            steps.log(action, f"CALLED tool {call.name}({call.input})")
            try:
                content_blocks, summary = _execute_verification_tool(
                    client, media_type, original_b64, call
                )
            except Exception as exc:
                steps.log(action, f"TOOL {call.name} FAILED ({exc})")
                content_blocks, summary = (
                    [{"type": "text", "text": f"Tool {call.name} failed: {exc}"}],
                    f"tool {call.name} FAILED",
                )
            steps.log(action, f"TOOL result {summary}")
            messages.append(
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
            )
    else:
        steps.log(action, f"EXHAUSTED tool budget ({MAX_TOOL_ROUNDS} calls) without a verdict")

    if response is None:
        raise HTTPException(status_code=502, detail="Verification produced no response.")

    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    if not raw_text.strip():
        steps.log(action, "FAILED (no final JSON text in response)")
        raise HTTPException(
            status_code=502,
            detail="Verification used its tool budget without producing a final answer.",
        )
    try:
        parsed = _extract_json(raw_text)
    except HTTPException as exc:
        steps.log(action, "FAILED (response not valid JSON)")
        raise

    try:
        verdict = VerificationResult(**parsed)
    except Exception as exc:
        steps.log(action, f"FAILED (schema mismatch: {exc})")
        logger.error(
            "Verification JSON didn't match expected schema: %s\nParsed: %s", exc, parsed
        )
        raise HTTPException(
            status_code=502,
            detail=f"Verification response didn't match the expected schema: {exc}",
        )

    if not verdict.corrections:
        steps.log(action, "MATCH — charts align")
    else:
        steps.log(action, f"MISMATCH — {len(verdict.corrections)} correction(s) reported")
    return verdict


def _apply_corrections(
    steps: StepLogger,
    structured: StructuredData,
    corrections: list[SeriesCorrection],
    round_no: int,
) -> StructuredData:
    """Merge Claude's corrections into the extracted table by label
    (case-insensitive); labels the model invented are ignored."""
    corrected = {c.label.strip().lower(): c.value for c in corrections}
    changes = []
    for point in structured.series:
        new_value = corrected.get(point.label.strip().lower())
        if new_value is not None and new_value != point.value:
            changes.append(f"{point.label}: {point.value} -> {new_value}")
    updated_series = [
        SeriesPoint(label=p.label, value=corrected.get(p.label.strip().lower(), p.value))
        for p in structured.series
    ]

    if changes:
        steps.log(
            f"Applying corrections (round {round_no})",
            "; ".join(changes),
        )
    else:
        steps.log(f"Applying corrections (round {round_no})", "NO CHANGES — values already match")
    return structured.model_copy(update={"series": updated_series})


def analyze_chart(payload: AnalyzeChartRequest) -> AnalyzeChartResponse:
    """Runs the extraction loop for a chart request:

    1. First pass: extract the full table from the image.
    2. Re-render that table as a chart, show [original, re-render] to Claude,
       and ask whether they match.
    3. If Claude reports corrections, apply them and repeat step 2.
    4. Stop when Claude reports a match, or after MAX_VERIFICATION_ROUNDS.

    Whether to continue — and what to fix — is decided by Claude's output,
    not by this code.
    """
    steps = StepLogger()

    try:
        media_type, base64_data = _parse_image_data_url(payload.image)
    except HTTPException as exc:
        steps.log("Parsing image data URL", f"FAILED ({exc.detail})")
        raise
    steps.log("Parsing image data URL", "OK")

    try:
        client = get_client()
    except HTTPException as exc:
        steps.log("Initializing Claude client", f"FAILED ({exc.detail})")
        raise
    steps.log("Initializing Claude client", "OK")

    result = _run_extraction(steps, client, media_type, base64_data, payload.task)

    if not result.structuredData.series:
        steps.log("Validating extracted series", "EMPTY — chart unreadable, skipping verification")
        return result
    steps.log(
        "Validating extracted series",
        f"OK ({len(result.structuredData.series)} point(s), starting verification)",
    )

    structured = result.structuredData
    for round_no in range(1, MAX_VERIFICATION_ROUNDS + 1):
        try:
            rendered_url = render_chart_image(structured)
            _, rendered_b64 = _parse_image_data_url(rendered_url)
        except Exception as exc:
            steps.log(
                f"Rendering extracted chart (round {round_no})",
                f"SKIPPED ({exc})",
            )
            break
        steps.log(f"Rendering extracted chart (round {round_no})", "OK")

        verdict = _run_verification(
            steps, client, media_type, base64_data, rendered_b64, structured, round_no
        )

        # DEBUG override: FORCE_VERIFY_ROUNDS makes early rounds report a
        # mismatch so the multi-round loop is observable on any chart. Only
        # active when the env flag is set (default 0 = off).
        if FORCE_VERIFY_ROUNDS > 0 and round_no < FORCE_VERIFY_ROUNDS:
            forced_label = structured.series[0].label
            forced_value = round(structured.series[0].value * 1.1, 2)
            steps.log(
                f"Verification pass (round {round_no})",
                f"FORCED mismatch (debug FORCE_VERIFY_ROUNDS={FORCE_VERIFY_ROUNDS})",
            )
            verdict = VerificationResult(
                match=False,
                corrections=[SeriesCorrection(label=forced_label, value=forced_value)],
                notes="forced by FORCE_VERIFY_ROUNDS debug flag",
            )

        if not verdict.corrections:
            break

        structured = _apply_corrections(steps, structured, verdict.corrections, round_no)
    else:
        steps.log(
            "Verification loop",
            f"EXHAUSTED {MAX_VERIFICATION_ROUNDS} round(s) without a confirmed match",
        )

    steps.log(
        "Returning final result",
        f"OK ({len(structured.series)} data point(s))",
    )
    return result.model_copy(update={"structuredData": structured})
