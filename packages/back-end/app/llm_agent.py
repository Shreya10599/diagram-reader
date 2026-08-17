import base64
import json
import logging
import re
from typing import Optional

from anthropic import Anthropic
from fastapi import HTTPException

from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from .models import (
    AnalyzeChartResponse,
    ChartTask,
    SeriesCorrection,
    SeriesPoint,
    StructuredData,
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
- If the extracted table includes an extra point that does not actually appear anywhere in \
the original chart (e.g. a hallucinated bar past the last real one, or an off-by-one that \
invented a trailing data point), report a correction for that label with "value": null to \
mean "remove this point entirely" — do not guess a plausible-looking value for a point that \
shouldn't exist at all.
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
  "corrections": [ { "label": "<series label>", "value": <number> | null }, ... ],
  "notes": "<short summary of what matched or what was corrected, or empty string>"
}

"value" must be a plain number for a value correction, or null to mean "this point should be \
removed — it doesn't appear in the original chart." Labels in "corrections" must exactly \
match the labels in the provided table (case-sensitive) — if you don't recognize a label, \
leave it out rather than guessing."""


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
    per-request, so it's built here instead.

    full_extraction with an instruction is a distinct case from
    summary/lookup: those ask for a derived figure computed FROM the
    extraction (LIHEAP average usage, stock high/low, etc.), so their
    instruction is framed as "compute this and report it as
    computedAnswer". full_extraction's instruction instead guides HOW to
    read/label the series itself — e.g. income_pdf.py's grouped-bar-chart
    case, where bars need compound "<member> — <period>" labels that no
    generic chart-reading procedure would produce on its own. Needed once
    a caller had a real reason to customize extraction-time behavior
    without asking for any computed figure; previously nothing did, so
    full_extraction + instruction was just silently ignored."""
    base = "Extract this chart's data following the procedure in your instructions."
    if task and task.instruction:
        if task.type == "full_extraction":
            base += f" EXTRACTION NOTE: {task.instruction}"
        else:
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
    case it wraps the response in ```json anyway, and fall back to slicing out the \
    outermost {...} object in case it prefaces the JSON with a stray sentence despite \
    being told not to (seen in practice from the verification pass: "The values match \
    closely enough given chart precision.\\n{...}") — every node (extraction, \
    verification, re-extract, validate_mapping) funnels through this one function, so \
    this fix covers all of them."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(json)?", "", text).rstrip("`").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Slice out the first balanced {...} object rather than str.rfind("}"),
    # so a stray brace in trailing prose can't truncate the object early.
    start = text.find("{")
    if start != -1:
        depth = 0
        end = None
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is not None:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as exc:
                logger.error(
                    "Failed to parse extracted JSON candidate: %s\nCandidate: %s", exc, candidate
                )
                raise HTTPException(
                    status_code=502,
                    detail="Claude returned a response that couldn't be parsed as JSON.",
                )

    logger.error("Failed to parse Claude response as JSON (no JSON object found)\nRaw: %s", raw_text)
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

    print("=== RAW FIRST-PASS EXTRACTION RESPONSE ===")
    print(response.model_dump_json(indent=2))

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


def _apply_corrections(
    steps: StepLogger,
    structured: StructuredData,
    corrections: list[SeriesCorrection],
    round_no: int,
) -> StructuredData:
    """Merge Claude's corrections into the extracted table by label
    (case-insensitive); labels the model invented are ignored. A
    correction with value=None means "this point doesn't actually appear
    in the original chart" — remove it entirely rather than trying to
    update it to some value.

    A removal is a bigger, harder-to-verify claim than a value correction
    (the verification pass is asserting a point doesn't exist at all, not
    just that its value looks off) — and it's just as fallible as any
    other model judgment: real testing found a case where a removal was
    later confirmed WRONG (the point did exist). So a removal always
    downgrades confidence to "low" and gets logged in uncertainValues
    rather than silently trusted — don't remove that downgrade without
    something that actually double-checks the removal first."""
    corrected = {c.label.strip().lower(): c.value for c in corrections}
    removed_labels = {c.label.strip().lower() for c in corrections if c.value is None}

    changes = []
    removed = []
    updated_series = []
    for point in structured.series:
        key = point.label.strip().lower()
        if key in removed_labels:
            removed.append(point.label)
            continue
        new_value = corrected.get(key)
        if new_value is not None and new_value != point.value:
            changes.append(f"{point.label}: {point.value} -> {new_value}")
        updated_series.append(
            SeriesPoint(label=point.label, value=new_value if new_value is not None else point.value)
        )

    if changes or removed:
        summary_parts = []
        if changes:
            summary_parts.append("; ".join(changes))
        if removed:
            summary_parts.append(f"removed: {', '.join(removed)}")
        steps.log(f"Applying corrections (round {round_no})", " | ".join(summary_parts))
    else:
        steps.log(f"Applying corrections (round {round_no})", "NO CHANGES — values already match")

    update: dict = {"series": updated_series}
    if removed:
        update["confidence"] = "low"
        update["uncertainValues"] = list(structured.uncertainValues or []) + [
            f"{label} (removed by verification pass — unconfirmed, double check the original chart)"
            for label in removed
        ]
    return structured.model_copy(update=update)

